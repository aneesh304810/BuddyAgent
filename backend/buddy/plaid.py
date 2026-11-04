"""Finance, phase D: banks and cards through Plaid, read-only (Transactions + Liabilities).

  linking   Buddy makes a Plaid Link token and a one-time page on this server
            (https://<your server>/plaid/link?state=...). The user opens it on the phone, picks the bank
            and signs in inside Plaid's own window: Buddy never sees bank passwords. Plaid hands back a
            public token, swapped here for an access token kept in the encrypted database.
  every 6 h transactions (/transactions/sync, with a cursor: only what changed) -> spending, card
            payments and transfers between the user's own accounts left out; balances -> bank_accounts
  every day liabilities (/liabilities/get): for each credit card the statement date and balance, the
            due date, the minimum, the purchase APR and the limit -> the card calendar and a bill to remind
  unlinking /item/remove revokes the token at Plaid, then the bank's accounts, transactions and cards
            are deleted here

Settings (hidden prompts: sudo bash /opt/buddy/deploy/set.sh NAME): BUDDY_PLAID_CLIENT_ID,
BUDDY_PLAID_SECRET, BUDDY_PLAID_ENV (sandbox | production), and for banks that sign in through their
own site (Chase, Capital One...) BUDDY_PLAID_REDIRECT_URI = https://<your server>/plaid/oauth, the
same address added under Allowed redirect URIs in the Plaid dashboard.
"""

import asyncio
import html
import json
import logging
import os
import secrets
import time
import urllib.request
from datetime import date, datetime, timedelta, timezone

from .bankfeed import ACCOUNTS_SQL
from .finance import Finance
from .memory import Memory
from .money import Money
from .tools import ToolError, require_https

log = logging.getLogger(__name__)

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS plaid_items (
    item_id TEXT PRIMARY KEY,
    access_token TEXT NOT NULL,       -- in the encrypted database only; never logged or shown
    institution TEXT NOT NULL DEFAULT '',
    cursor TEXT NOT NULL DEFAULT '',
    last_sync TEXT,
    last_liabilities TEXT,
    error TEXT NOT NULL DEFAULT '',
    added_at TEXT NOT NULL,
    env TEXT NOT NULL DEFAULT ''      -- sandbox | production, as linked (sandbox = Plaid's test data)
);
CREATE TABLE IF NOT EXISTS fin_income (
    source TEXT PRIMARY KEY,          -- plaid:<item>:<transaction id>
    day TEXT NOT NULL,
    amount REAL NOT NULL,             -- money in, positive
    currency TEXT NOT NULL,
    name TEXT NOT NULL,               -- "ACME CORP PAYROLL"
    kind TEXT NOT NULL DEFAULT ''     -- Wages, Interest earned, Tax refund...
);
CREATE TABLE IF NOT EXISTS plaid_accounts (
    account_id TEXT PRIMARY KEY,
    item_id TEXT NOT NULL,
    name TEXT NOT NULL,
    mask TEXT NOT NULL DEFAULT '',
    type TEXT NOT NULL,               -- depository | credit | loan | investment
    subtype TEXT NOT NULL DEFAULT ''
);
"""
SYNC_HOURS = 6
SOON_MINUTES = 30   # after a card email: sync again once the last sync is this old (Plaid needs a little time)
LINK_MINUTES = 30
# Plaid's categories -> Buddy's spending categories. None = not spending (payments, transfers, income).
CATEGORY = {"FOOD_AND_DRINK": "food", "TRANSPORTATION": "transport", "TRAVEL": "travel",
            "GENERAL_MERCHANDISE": "shopping", "HOME_IMPROVEMENT": "shopping", "RENT_AND_UTILITIES": "bills",
            "MEDICAL": "health", "PERSONAL_CARE": "health", "ENTERTAINMENT": "entertainment",
            "GENERAL_SERVICES": "bills", "GOVERNMENT_AND_NON_PROFIT": "bills", "BANK_FEES": "fees",
            "LOAN_PAYMENTS": None, "TRANSFER_IN": None, "TRANSFER_OUT": None, "INCOME": None}
DETAILED = {"FOOD_AND_DRINK_GROCERIES": "groceries", "GENERAL_SERVICES_SUBSCRIPTION": "subscriptions",
            "ENTERTAINMENT_TV_AND_MOVIES": "subscriptions", "ENTERTAINMENT_MUSIC_AND_AUDIO": "subscriptions",
            "BANK_FEES_INTEREST_CHARGE": "fees", "TRANSFER_OUT_WITHDRAWAL": "cash"}


def detail_label(primary: str, detailed: str) -> str:
    """'FOOD_AND_DRINK' + 'FOOD_AND_DRINK_RESTAURANT' -> 'Restaurant'; 'TRANSPORTATION_TAXIS_AND_RIDE_SHARES' -> 'Taxis and ride shares'."""
    rest = detailed[len(primary) + 1:] if primary and detailed.startswith(primary + "_") else detailed
    return rest.replace("_", " ").strip().capitalize()[:40]


class Plaid:
    def __init__(self, memory: Memory, env=None, http=None):
        self.memory = memory
        self.env = os.environ if env is None else env
        self._http = http or _post
        memory.db.executescript(SCHEMA_SQL + ACCOUNTS_SQL)
        self.money = Money(memory)
        cols = {r[1] for r in memory.db.execute("PRAGMA table_info(plaid_items)")}
        if "env" not in cols:  # older databases: banks linked before this column were real (production)
            memory.db.execute("ALTER TABLE plaid_items ADD COLUMN env TEXT NOT NULL DEFAULT 'production'")
            memory.db.commit()

    def setting(self, key: str) -> str:
        return (self.env.get(key) or "").strip()

    def configured(self) -> bool:
        return bool(self.setting("BUDDY_PLAID_CLIENT_ID") and self.setting("BUDDY_PLAID_SECRET"))

    @property
    def host(self) -> str:
        env = self.setting("BUDDY_PLAID_ENV") or "sandbox"
        if env not in ("sandbox", "production"):
            raise ToolError("BUDDY_PLAID_ENV must be sandbox or production.")
        return f"https://{env}.plaid.com"

    def call(self, path: str, body: dict) -> dict:
        if not self.configured():
            raise ToolError("Plaid isn't set up: add BUDDY_PLAID_CLIENT_ID and BUDDY_PLAID_SECRET on the server "
                            "(sudo bash /opt/buddy/deploy/set.sh BUDDY_PLAID_CLIENT_ID).")
        payload = {"client_id": self.setting("BUDDY_PLAID_CLIENT_ID"), "secret": self.setting("BUDDY_PLAID_SECRET"),
                   **body}
        data = self._http(self.host + path, payload)
        if "error_code" in data and data.get("error_code"):
            raise PlaidError(data.get("error_code", ""), data.get("display_message") or data.get("error_message") or "")
        return data

    # --- linking -----------------------------------------------------------------------------------
    def start_link(self, base_url: str) -> dict:
        """A Link token and the one-time page that opens Plaid Link with it."""
        body = {"client_name": "Buddy", "language": "en", "country_codes": ["US"],
                "user": {"client_user_id": "buddy-owner"}, "products": ["transactions"],
                "required_if_supported_products": ["liabilities"], "transactions": {"days_requested": 730}}
        redirect = self.setting("BUDDY_PLAID_REDIRECT_URI")
        if redirect:
            body["redirect_uri"] = require_https(redirect)
        token = self.call("/link/token/create", body)["link_token"]
        state = secrets.token_urlsafe(24)
        self.memory.set("plaid:link", json.dumps({"state": state, "token": token,
                                                  "until": time.time() + LINK_MINUTES * 60}))
        return {"url": f"{base_url.rstrip('/')}/plaid/link?state={state}", "minutes": LINK_MINUTES}

    def pending(self, state: str | None = None) -> dict | None:
        """The link in progress, if it's still valid (and matches the state, when one is given)."""
        try:
            link = json.loads(self.memory.get("plaid:link") or "null")
        except json.JSONDecodeError:
            return None
        if not link or link["until"] < time.time():
            return None
        if state is not None and not secrets.compare_digest(str(state), link["state"]):
            return None
        return link

    def finish_link(self, state: str, public_token: str, institution: str = "") -> dict:
        if not self.pending(state):
            raise ToolError("That link expired: ask Buddy to connect the bank again.")
        got = self.call("/item/public_token/exchange", {"public_token": public_token})
        self.memory.db.execute(
            "INSERT OR REPLACE INTO plaid_items (item_id, access_token, institution, added_at, env) VALUES (?, ?, ?, ?, ?)",
            (got["item_id"], got["access_token"], institution[:80], datetime.now(timezone.utc).isoformat(),
             self.setting("BUDDY_PLAID_ENV") or "sandbox"))
        self.memory.db.commit()
        self.memory.set("plaid:link", "")
        return {"item_id": got["item_id"], "institution": institution}

    # --- syncing -----------------------------------------------------------------------------------
    def items(self) -> list[dict]:
        return [dict(r) for r in self.memory.db.execute("SELECT * FROM plaid_items ORDER BY added_at")]

    def status(self) -> list[dict]:
        out = []
        for i in self.items():
            accounts = [dict(a) for a in self.memory.db.execute(
                "SELECT name, mask, type, subtype FROM plaid_accounts WHERE item_id = ?", (i["item_id"],))]
            out.append({"item_id": i["item_id"], "institution": i["institution"], "accounts": accounts,
                        "last_sync": i["last_sync"] or "", "problem": i["error"] or "",
                        "test_data": i.get("env") == "sandbox"})
        return out

    def sync_item(self, item: dict) -> dict:
        db = self.memory.db
        token = item["access_token"]
        accounts = self._accounts(item)
        cursor, added, removed, more = item["cursor"], 0, 0, True
        while more:
            page = self.call("/transactions/sync", {"access_token": token, "cursor": cursor, "count": 500})
            for tx in page.get("added", []) + page.get("modified", []):
                if self._transaction(item["item_id"], tx, accounts, real=item.get("env") != "sandbox"):
                    added += 1
            for tx in page.get("removed", []):
                removed += db.execute("DELETE FROM transactions WHERE source = ?",
                                      (f"plaid:{item['item_id']}:{tx.get('transaction_id')}",)).rowcount
                db.execute("DELETE FROM fin_income WHERE source = ?", (f"plaid:{item['item_id']}:{tx.get('transaction_id')}",))
            cursor, more = page.get("next_cursor", cursor), bool(page.get("has_more"))
        db.execute("UPDATE plaid_items SET cursor = ?, last_sync = ?, error = '' WHERE item_id = ?",
                   (cursor, datetime.now(timezone.utc).isoformat(), item["item_id"]))
        db.commit()
        if item.get("env") != "sandbox":  # test data never replaces the real charges read from emails
            self.memory.set("money:bank_feed", "1")  # charges now come from the bank; emails keep only bills
            self._take_over_cards(item["item_id"])
        cards = 0
        if not item["last_liabilities"] or datetime.fromisoformat(item["last_liabilities"]) < \
                datetime.now(timezone.utc) - timedelta(hours=20):
            cards = self.liabilities(item, accounts)
        return {"added": added, "removed": removed, "cards": cards}

    def _accounts(self, item: dict) -> dict:
        data = self.call("/accounts/get", {"access_token": item["access_token"]})
        out = {}
        now = datetime.now(timezone.utc).isoformat()
        institution = item["institution"]
        for a in data.get("accounts", []):
            out[a["account_id"]] = a
            name = a.get("official_name") or a.get("name") or "Account"
            mask = a.get("mask") or ""
            self.memory.db.execute("INSERT OR REPLACE INTO plaid_accounts (account_id, item_id, name, mask, type, subtype) "
                                   "VALUES (?, ?, ?, ?, ?, ?)", (a["account_id"], item["item_id"], name[:80], mask[-4:],
                                                                 a.get("type", ""), a.get("subtype") or ""))
            bal = a.get("balances") or {}
            current = bal.get("current")
            if current is not None and a.get("type") in ("credit", "loan"):
                current = -abs(current)  # what's owed shows as negative, like the bank feed
            self.memory.db.execute(
                "INSERT OR REPLACE INTO bank_accounts (id, name, institution, currency, balance, available, balance_date, "
                "synced_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (f"plaid:{a['account_id']}", f"{name[:60]} ••{mask[-4:]}" if mask else name[:60], institution,
                 (bal.get("iso_currency_code") or "USD")[:3], current, bal.get("available"), now[:10], now))
        self.memory.db.commit()
        return out

    def _transaction(self, item_id: str, tx: dict, accounts: dict, real: bool = True) -> bool:
        if tx.get("pending"):
            return False  # it posts later with its final amount
        pfc = tx.get("personal_finance_category") or {}
        primary, detailed = pfc.get("primary", ""), pfc.get("detailed", "")
        if primary == "INCOME" and float(tx.get("amount") or 0) < 0:  # Plaid: negative = money in
            self.memory.db.execute(
                "INSERT OR REPLACE INTO fin_income (source, day, amount, currency, name, kind) VALUES (?, ?, ?, ?, ?, ?)",
                (f"plaid:{item_id}:{tx['transaction_id']}", str(tx.get("date") or tx.get("authorized_date"))[:10],
                 round(-float(tx["amount"]), 2), tx.get("iso_currency_code") or "USD",
                 (tx.get("merchant_name") or tx.get("name") or "Income")[:80], detail_label(primary, detailed)))
            return False
        category = DETAILED.get(detailed, CATEGORY.get(primary, "other"))
        if category is None or not tx.get("amount"):
            return False  # card payments, transfers between the user's own accounts, pay coming in
        account = accounts.get(tx.get("account_id"), {})
        source = f"plaid:{item_id}:{tx['transaction_id']}"
        self.memory.db.execute("DELETE FROM transactions WHERE source = ?", (source,))  # modified: replace it
        day = str(tx.get("date") or tx.get("authorized_date"))[:10]
        currency = tx.get("iso_currency_code") or "USD"
        if real:  # the same purchase counted earlier from an email or SimpleFIN: the bank's record replaces it
            self._replace_earlier_copy(float(tx["amount"]), currency, day)
        self.money.add_transaction(float(tx["amount"]),  # Plaid: positive = money out, same as Buddy
                                   currency, tx.get("merchant_name") or tx.get("name") or "unknown", category, day,
                                   (account.get("mask") or "")[-4:] or account.get("name", ""), source,
                                   detail_label(primary, detailed))
        return True

    def _take_over_cards(self, item_id: str) -> int:
        """Plaid is the record for every account it covers: charges counted earlier from emails or SimpleFIN for
        that card or account, from the start of Plaid's history for it, are removed (amounts that differ, like a
        tip added later, would otherwise count twice). Other cards and hand-entered cash are untouched."""
        db, removed = self.memory.db, 0
        for acct in db.execute("SELECT name, mask FROM plaid_accounts WHERE item_id = ?", (item_id,)).fetchall():
            mask, name = (acct["mask"] or "")[-4:], (acct["name"] or "").strip().lower()
            first = db.execute("SELECT MIN(day) FROM transactions WHERE source LIKE ? AND card = ?",
                               (f"plaid:{item_id}:%", mask or acct["name"])).fetchone()[0]
            if not first or not (mask or name):
                continue
            removed += db.execute(
                "DELETE FROM transactions WHERE (source LIKE 'email:%' OR source LIKE 'simplefin:%') AND day >= ? "
                "AND ((? != '' AND card LIKE ?) OR (? != '' AND LOWER(TRIM(card)) = ?))",
                (first, mask, f"%{mask}%", name, name)).rowcount
        db.commit()
        return removed

    def _replace_earlier_copy(self, amount: float, currency: str, day: str) -> None:
        """Remove ONE charge counted before from an email or SimpleFIN with the same amount and currency
        within 3 days. Cash entered by hand (manual:) is never touched."""
        try:
            d = date.fromisoformat(day)
        except ValueError:
            return
        self.memory.db.execute(
            "DELETE FROM transactions WHERE id = (SELECT id FROM transactions WHERE "
            "(source LIKE 'email:%' OR source LIKE 'simplefin:%') AND currency = ? AND ABS(amount - ?) < 0.005 "
            "AND day BETWEEN ? AND ? ORDER BY ABS(julianday(day) - julianday(?)) LIMIT 1)",
            (currency.upper()[:3], round(amount, 2), (d - timedelta(days=3)).isoformat(),
             (d + timedelta(days=3)).isoformat(), day))

    def liabilities(self, item: dict, accounts: dict) -> int:
        """Each credit card's statement, due date, minimum, APR and limit -> the card calendar."""
        try:
            data = self.call("/liabilities/get", {"access_token": item["access_token"]})
        except PlaidError as e:
            if e.code in ("PRODUCTS_NOT_SUPPORTED", "NO_LIABILITY_ACCOUNTS", "PRODUCT_NOT_READY"):
                return 0  # this bank has no cards Plaid can describe (or not yet)
            raise
        fin = Finance(self.memory)
        accts = {a["account_id"]: a for a in data.get("accounts", [])} or accounts
        n = 0
        for c in (data.get("liabilities") or {}).get("credit") or []:
            a = accts.get(c.get("account_id"), {})
            bal = a.get("balances") or {}
            apr = next((x.get("apr_percentage") for x in c.get("aprs") or [] if x.get("apr_type") == "purchase_apr"),
                       None)
            stmt, due = c.get("last_statement_issue_date"), c.get("next_payment_due_date")
            name = a.get("official_name") or a.get("name") or "Card"
            card = fin.card(name=name, issuer=item["institution"], last4=a.get("mask") or "",
                            statement_date=stmt, due_date=due, closing_day=_dom(stmt), due_day=_dom(due),
                            statement_balance=c.get("last_statement_balance"), minimum_due=c.get("minimum_payment_amount"),
                            credit_limit=bal.get("limit"), apr=apr, currency=(bal.get("iso_currency_code") or "USD")[:3],
                            source=f"plaid:{item['item_id']}")
            if due and c.get("last_statement_balance"):
                paid = c.get("last_payment_date") and stmt and c["last_payment_date"] >= stmt and \
                    (c.get("last_payment_amount") or 0) >= c["last_statement_balance"]
                self.money.add_bill(card["name"], float(c["last_statement_balance"]), card["currency"], due,
                                    card["last4"], f"plaid:{c.get('account_id')}:{due}")
                if paid:
                    self.money.mark_paid(card["last4"] or card["name"])
            n += 1
        self.memory.db.execute("UPDATE plaid_items SET last_liabilities = ? WHERE item_id = ?",
                               (datetime.now(timezone.utc).isoformat(), item["item_id"]))
        self.memory.db.commit()
        return n

    def sync(self, force: bool = False) -> dict:
        totals = {"added": 0, "removed": 0, "cards": 0, "problems": []}
        soon = self.memory.get("plaid:sync_soon") or ""  # a card email just arrived: the bank likely has it too
        now = datetime.now(timezone.utc)
        for item in self.items():
            last = datetime.fromisoformat(item["last_sync"]) if item["last_sync"] else None
            due = force or last is None or last < now - timedelta(hours=SYNC_HOURS) or \
                (soon > item["last_sync"] and last < now - timedelta(minutes=SOON_MINUTES))
            if not due:
                continue
            try:
                got = self.sync_item(item)
                for k in ("added", "removed", "cards"):
                    totals[k] += got[k]
            except PlaidError as e:
                problem = ("the bank needs you to sign in again (ask Buddy to reconnect it)"
                           if e.code == "ITEM_LOGIN_REQUIRED" else f"{e.code}: {e.message}"[:200])
                self.memory.db.execute("UPDATE plaid_items SET error = ? WHERE item_id = ?", (problem, item["item_id"]))
                self.memory.db.commit()
                totals["problems"].append(f"{item['institution'] or 'A bank'}: {problem}")
        if totals["added"] or totals["cards"]:
            Finance(self.memory).find_subscriptions()
        return totals

    # --- unlinking -----------------------------------------------------------------------------------
    def unlink(self, what: str) -> str:
        what = str(what).strip().lower()
        item = next((i for i in self.items() if what and (what == i["item_id"].lower() or what in i["institution"].lower())),
                    None)
        if item is None:
            raise ToolError("No linked bank like that.")
        try:
            self.call("/item/remove", {"access_token": item["access_token"]})  # revokes the token at Plaid
        except PlaidError as e:
            if e.code not in ("ITEM_NOT_FOUND", "INVALID_ACCESS_TOKEN"):
                raise
        db = self.memory.db
        accounts = [r["account_id"] for r in db.execute("SELECT account_id FROM plaid_accounts WHERE item_id = ?",
                                                        (item["item_id"],))]
        for account in accounts:
            db.execute("DELETE FROM bank_accounts WHERE id = ?", (f"plaid:{account}",))
            db.execute("DELETE FROM bills WHERE source LIKE ?", (f"plaid:{account}:%",))
        db.execute("DELETE FROM transactions WHERE source LIKE ?", (f"plaid:{item['item_id']}:%",))
        db.execute("DELETE FROM fin_income WHERE source LIKE ?", (f"plaid:{item['item_id']}:%",))
        db.execute("DELETE FROM fin_cards WHERE source = ?", (f"plaid:{item['item_id']}",))
        db.execute("DELETE FROM plaid_accounts WHERE item_id = ?", (item["item_id"],))
        db.execute("DELETE FROM plaid_items WHERE item_id = ?", (item["item_id"],))
        db.commit()
        self._forget_derived()
        if not any(i.get("env") != "sandbox" for i in self.items()):
            self.memory.set("money:bank_feed", "")  # back to reading charges from emails
        return f"Unlinked {item['institution'] or 'the bank'}: access revoked at Plaid and its data deleted here."

    def _forget_derived(self) -> None:
        """After a bank's data is deleted: drop subscriptions only its charges showed, and reward rates of
        cards that are gone, so nothing found in (for example) sandbox test data lingers."""
        from .finance import merchant_key
        db = self.memory.db
        since = (date.today() - timedelta(days=760)).isoformat()
        keys = {merchant_key(r["merchant"]) for r in db.execute(
            "SELECT merchant FROM transactions WHERE day >= ? AND amount > 0", (since,))}
        try:
            for sub in db.execute("SELECT id, key, last_charge FROM fin_subscriptions").fetchall():
                if sub["last_charge"] >= since and sub["key"] not in keys:
                    db.execute("DELETE FROM fin_subscriptions WHERE id = ?", (sub["id"],))
        except Exception:
            pass  # no subscriptions table yet
        try:
            db.execute("DELETE FROM fin_rewards WHERE card_id NOT IN (SELECT id FROM fin_cards)")
        except Exception:
            pass
        db.commit()

    async def run(self, notify=None) -> None:
        """Every 15 minutes: sync whatever is due (each bank every 6 hours)."""
        await asyncio.sleep(60)
        while True:
            if self.configured() and self.items():
                try:
                    got = await asyncio.to_thread(self.sync)
                    if got["problems"] and notify and self.memory.get("plaid:told") != date.today().isoformat():
                        self.memory.set("plaid:told", date.today().isoformat())
                        await notify("money", "Bank connection: " + "; ".join(got["problems"]))
                except Exception:
                    log.exception("plaid sync failed")
            await asyncio.sleep(15 * 60)


class PlaidError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code, self.message = code, message


def _dom(value) -> int | None:
    try:
        return date.fromisoformat(str(value)[:10]).day
    except ValueError:
        return None


def _post(url: str, payload: dict) -> dict:
    request = urllib.request.Request(require_https(url), data=json.dumps(payload).encode(),
                                     headers={"Content-Type": "application/json", "Plaid-Version": "2020-09-14"})
    try:
        with urllib.request.urlopen(request, timeout=60) as response:  # https only  # nosec B310
            return json.loads(response.read(20_000_000))
    except urllib.error.HTTPError as e:  # Plaid explains errors in the body
        try:
            return json.loads(e.read(100_000))
        except Exception:
            return {"error_code": f"HTTP_{e.code}", "error_message": str(e)}


def link_page(link_token: str, state: str, oauth: bool = False) -> str:
    """The one-time page that opens Plaid Link. Only Plaid's script runs on it."""
    token, st = json.dumps(link_token), json.dumps(state)
    redirect = "receivedRedirectUri: window.location.href," if oauth else ""
    return f"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'unsafe-inline' https://cdn.plaid.com; frame-src https://cdn.plaid.com; connect-src 'self' https://*.plaid.com; style-src 'unsafe-inline'">
<title>Connect a bank to Buddy</title>
<style>body{{font-family:system-ui,sans-serif;background:#0b1020;color:#fff;padding:24px;line-height:1.5}}
button{{font-size:18px;padding:12px 20px;border-radius:10px;border:0;background:#fff;color:#000}}</style></head>
<body><h2>Connect a bank to Buddy</h2>
<p id="msg">Plaid opens in a moment. You sign in to your bank inside Plaid's window; Buddy never sees your password and
can only read, never move money.</p><button id="go">Open Plaid</button>
<script src="https://cdn.plaid.com/link/v2/stable/link-initialize.js"></script>
<script>
const msg = document.getElementById('msg');
const handler = Plaid.create({{
  token: {token}, {redirect}
  onSuccess: async (public_token, metadata) => {{
    msg.textContent = 'Connecting…';
    const r = await fetch('/plaid/exchange', {{method: 'POST', headers: {{'Content-Type': 'application/json'}},
      body: JSON.stringify({{state: {st}, public_token, institution: (metadata.institution || {{}}).name || ''}})}});
    msg.textContent = r.ok ? 'Done. You can close this page; Buddy will tell you what it found.'
                           : 'Something went wrong. Ask Buddy to connect the bank again.';
  }},
  onExit: (err) => {{ if (err) msg.textContent = 'Plaid closed: ' + (err.display_message || err.error_code || '') +
                      '. Tap the button to try again.'; }},
}});
document.getElementById('go').onclick = () => handler.open();
handler.open();
</script></body></html>"""


def error_page(text: str) -> str:
    return (f"<!doctype html><meta charset='utf-8'><meta name='viewport' content='width=device-width'>"
            f"<body style='font-family:system-ui;background:#0b1020;color:#fff;padding:24px'>{html.escape(text)}</body>")


BANK_TOOL = {
    "name": "bank_link",
    "description": "The user's banks and cards connected through Plaid (read-only: Buddy can never move money). "
    "link: opens a page on the phone where they pick the bank and sign in inside Plaid's own window (Buddy never "
    "sees the password); a card's due date, minimum, APR, limit and every transaction then come in by themselves. "
    "status: linked banks, their accounts (last four digits) and any problem. sync: fetch now. unlink (what = the "
    "bank): revokes access at Plaid and deletes that bank's data here (asks the user first).",
    "input_schema": {"type": "object", "properties": {
        "action": {"type": "string", "enum": ["link", "status", "sync", "unlink"]},
        "what": {"type": "string"}},
        "required": ["action"], "additionalProperties": False},
}


async def run_tool(plaid: Plaid, args: dict, base_url: str = "", open_links=None, thrift=None) -> str:
    action = args.get("action")
    if action == "status":
        found = plaid.status()
        if not plaid.configured():
            return ("Plaid isn't set up on the server yet: once Plaid approves the app, add the keys with "
                    "sudo bash /opt/buddy/deploy/set.sh BUDDY_PLAID_CLIENT_ID (and BUDDY_PLAID_SECRET, BUDDY_PLAID_ENV).")
        return json.dumps(found) if found else "No bank linked yet: say 'connect my bank'."
    if action == "sync":
        got = await asyncio.to_thread(plaid.sync, True)
        return json.dumps(got)
    if action == "link":
        if not base_url:
            raise ToolError("I don't know the server's address yet: open the Money screen and tap Connect a bank.")
        link = await asyncio.to_thread(plaid.start_link, base_url)
        if open_links is not None:
            await open_links([{"app": "", "url": link["url"], "label": "Connect a bank"}])
            return (f"Opened the Plaid page on the phone (valid {link['minutes']} minutes). They pick the bank and sign "
                    "in inside Plaid's window; Buddy says what it found once it's connected.")
        return f"Open this on the phone within {link['minutes']} minutes: {link['url']}"
    if action == "unlink":
        what = str(args.get("what") or "")
        if thrift is None:
            return plaid.unlink(what)

        async def go() -> str:
            return await asyncio.to_thread(plaid.unlink, what)
        return thrift.ask_action(f"Unlink {what or 'the bank'}: revoke Buddy's access at Plaid and delete its data?", go)
    raise ToolError("Unknown bank_link action.")
