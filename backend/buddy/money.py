"""Money agent: what the user spends, the bills coming up, and spending limits.

Charges, bills, payments and refunds are read from bank and card emails by the
email manager (the same cheap sorting call, no extra cost) and can be added by
voice. Bills due within BILL_WARN_DAYS get a reminder once a day until paid;
a payment email for the same biller marks the bill paid. Monthly limits per
category warn at 80% and again at 100%. Amounts stay in their own currency
(US dollars, soles, reais, rupees...); nothing is converted or guessed.
"""

import json
from datetime import date, datetime, timedelta, timezone

from .memory import Memory
from .tools import ToolError, local_now

BILL_WARN_DAYS = 3
CATEGORIES = ("food", "groceries", "transport", "travel", "shopping", "bills", "health",
              "entertainment", "subscriptions", "cash", "other")

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS transactions (
    id INTEGER PRIMARY KEY,
    day TEXT NOT NULL,                 -- local date YYYY-MM-DD
    amount REAL NOT NULL,              -- positive = spent, negative = refund
    currency TEXT NOT NULL,
    merchant TEXT NOT NULL,
    category TEXT NOT NULL,
    card TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL UNIQUE,       -- email:<record id>, or manual:<n>
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS bills (
    id INTEGER PRIMARY KEY,
    biller TEXT NOT NULL,
    amount REAL NOT NULL,
    currency TEXT NOT NULL,
    due_date TEXT NOT NULL,            -- YYYY-MM-DD
    card TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'open',   -- open | paid
    reminded_on TEXT,
    source TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS spending_limits (
    category TEXT PRIMARY KEY,
    amount REAL NOT NULL,
    currency TEXT NOT NULL
);
"""

# What the email manager extracts from each email (added to its sorting schema).
MONEY_SCHEMA = {
    "type": "object",
    "properties": {
        "kind": {"type": "string", "enum": ["none", "charge", "bill", "payment", "refund"]},
        "amount": {"type": "number"},
        "currency": {"type": "string"},
        "merchant": {"type": "string"},
        "category": {"type": "string", "enum": list(CATEGORIES)},
        "card": {"type": "string"},
        "due_date": {"type": "string"},
    },
    "required": ["kind", "amount", "currency", "merchant", "category", "card", "due_date"],
    "additionalProperties": False,
}

MONEY_PROMPT = """
Also fill "money" for every email. kind: "charge" for a purchase or card transaction \
alert, "bill" for a statement or invoice with an amount due (amount = total due, \
due_date = YYYY-MM-DD), "payment" for a confirmation that a bill was paid, "refund" \
for money back, otherwise "none" (amount 0, empty strings, category other). currency \
is the ISO code (USD, PEN, BRL, INR, EUR...). merchant is the shop or biller in a few \
words. card is the card's last four digits or name if shown. Promotions and offers \
are "none", even with prices in them.
"""


def _month_bounds(which: str) -> tuple[str, str]:
    today = local_now().date()
    if which == "today":
        return today.isoformat(), today.isoformat()
    if which == "this_week":
        start = today - timedelta(days=today.weekday())
        return start.isoformat(), today.isoformat()
    first = today.replace(day=1)
    if which == "last_month":
        end = first - timedelta(days=1)
        return end.replace(day=1).isoformat(), end.isoformat()
    return first.isoformat(), today.isoformat()  # this_month


class Money:
    def __init__(self, memory: Memory):
        self.memory = memory
        memory.db.executescript(SCHEMA_SQL)

    # --- recording -----------------------------------------------------------
    def add_transaction(self, amount: float, currency: str, merchant: str, category: str,
                        day: str | None = None, card: str = "", source: str | None = None) -> list[str]:
        """Store one charge (or a refund as a negative amount). Returns limit warnings."""
        category = category if category in CATEGORIES else "other"
        currency = (currency or "USD").upper()[:3]
        day = day or local_now().date().isoformat()
        if source is None:
            n = self.memory.db.execute("SELECT COUNT(*) AS n FROM transactions").fetchone()["n"]
            source = f"manual:{n + 1}:{datetime.now(timezone.utc).timestamp()}"
        cur = self.memory.db.execute(
            "INSERT OR IGNORE INTO transactions (day, amount, currency, merchant, category, card, source, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (day, round(amount, 2), currency, merchant.strip()[:80] or "unknown", category, card[:20], source,
             datetime.now(timezone.utc).isoformat()),
        )
        self.memory.db.commit()
        return self._limit_warnings(category, currency) if cur.rowcount and amount > 0 else []

    def add_bill(self, biller: str, amount: float, currency: str, due_date: str, card: str = "",
                 source: str | None = None) -> None:
        try:
            due = date.fromisoformat(due_date[:10]).isoformat()
        except ValueError:
            return  # no readable due date: nothing to remind about
        source = source or f"manual:{biller}:{due}"
        self.memory.db.execute(
            "INSERT OR IGNORE INTO bills (biller, amount, currency, due_date, card, source, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (biller.strip()[:80], round(amount, 2), (currency or "USD").upper()[:3], due, card[:20], source,
             datetime.now(timezone.utc).isoformat()),
        )
        self.memory.db.commit()

    def mark_paid(self, what: str) -> dict | None:
        """The open bill matching an id, a biller name or a card; None if none matches."""
        bills = self.open_bills(days=3650)
        what = str(what).strip().lower()
        match = next((b for b in bills if str(b["id"]) == what), None) or next(
            (b for b in bills if what and (what in b["biller"].lower() or b["biller"].lower() in what
                                          or (b["card"] and b["card"].lower() == what))), None)
        if match:
            self.memory.db.execute("UPDATE bills SET status = 'paid' WHERE id = ?", (match["id"],))
            self.memory.db.commit()
        return match

    def from_email(self, record_id: int, money: dict, received: str | None) -> list[str]:
        """Take what the email manager read from one email. Returns things to tell the user."""
        kind = money.get("kind", "none")
        amount = float(money.get("amount") or 0)
        if kind == "none" or (amount <= 0 and kind != "payment"):
            return []
        source = f"email:{record_id}"
        day = (received or "")[:10] or None
        merchant, currency, card = money.get("merchant", ""), money.get("currency", "USD"), money.get("card", "")
        if kind == "charge":
            return self.add_transaction(amount, currency, merchant, money.get("category", "other"), day, card, source)
        if kind == "refund":
            self.add_transaction(-amount, currency, merchant, money.get("category", "other"), day, card, source)
        elif kind == "bill":
            self.add_bill(merchant, amount, currency, money.get("due_date", ""), card, source)
        elif kind == "payment":
            self.mark_paid(merchant) or (card and self.mark_paid(card))
        return []

    # --- limits ----------------------------------------------------------------
    def set_limit(self, category: str, amount: float, currency: str = "USD") -> None:
        if category not in CATEGORIES and category != "total":
            raise ToolError(f"Pick a category: {', '.join(CATEGORIES)}, or total.")
        if amount <= 0:
            self.memory.db.execute("DELETE FROM spending_limits WHERE category = ?", (category,))
        else:
            self.memory.db.execute("INSERT OR REPLACE INTO spending_limits VALUES (?, ?, ?)",
                                   (category, amount, currency.upper()[:3]))
        self.memory.db.commit()

    def limits(self) -> list[dict]:
        out = []
        start, end = _month_bounds("this_month")
        for row in self.memory.db.execute("SELECT * FROM spending_limits ORDER BY category"):
            spent = self._spent(start, end, row["currency"], None if row["category"] == "total" else row["category"])
            out.append({**dict(row), "spent": round(spent, 2), "left": round(row["amount"] - spent, 2)})
        return out

    def _spent(self, start: str, end: str, currency: str, category: str | None) -> float:
        sql = "SELECT COALESCE(SUM(amount), 0) AS s FROM transactions WHERE day BETWEEN ? AND ? AND currency = ?"
        args = [start, end, currency]
        if category:
            sql += " AND category = ?"
            args.append(category)
        return float(self.memory.db.execute(sql, args).fetchone()["s"])

    def _limit_warnings(self, category: str, currency: str) -> list[str]:
        month = local_now().strftime("%Y-%m")
        warnings = []
        for limit in self.limits():
            if limit["category"] not in (category, "total") or limit["currency"] != currency:
                continue
            share = limit["spent"] / limit["amount"]
            for level in (100, 80):
                key = f"limit:{limit['category']}:{month}:{level}"
                if share * 100 >= level and not self.memory.get(key):
                    self.memory.set(key, "1")
                    name = "total spending" if limit["category"] == "total" else limit["category"]
                    warnings.append(
                        f"Heads up: {name} is at {limit['spent']:.0f} of your {limit['amount']:.0f} {currency} "
                        f"limit this month" + (" — you're over." if level == 100 else "."))
                    break
        return warnings

    # --- reading -----------------------------------------------------------
    def summary(self, period: str = "this_month", category: str = "") -> dict:
        start, end = _month_bounds(period)
        where, args = "day BETWEEN ? AND ?", [start, end]
        if category:
            where += " AND category = ?"
            args.append(category)
        totals = {}
        for r in self.memory.db.execute(
                f"SELECT currency, category, SUM(amount) AS s FROM transactions WHERE {where} "
                "GROUP BY currency, category ORDER BY s DESC", args):
            cur = totals.setdefault(r["currency"], {"total": 0.0, "by_category": {}})
            cur["by_category"][r["category"]] = round(r["s"], 2)
            cur["total"] = round(cur["total"] + r["s"], 2)
        top = [dict(r) for r in self.memory.db.execute(
            f"SELECT merchant, currency, ROUND(SUM(amount), 2) AS spent, COUNT(*) AS times FROM transactions "
            f"WHERE {where} GROUP BY merchant, currency ORDER BY spent DESC LIMIT 5", args)]
        return {"period": period, "from": start, "to": end, "by_currency": totals, "top_merchants": top}

    def recent(self, limit: int = 15) -> list[dict]:
        return [dict(r) for r in self.memory.db.execute(
            "SELECT id, day, amount, currency, merchant, category, card FROM transactions "
            "ORDER BY day DESC, id DESC LIMIT ?", (limit,))]

    def open_bills(self, days: int = 14) -> list[dict]:
        until = (local_now().date() + timedelta(days=days)).isoformat()
        return [dict(r) for r in self.memory.db.execute(
            "SELECT id, biller, amount, currency, due_date, card FROM bills WHERE status = 'open' "
            "AND due_date <= ? ORDER BY due_date", (until,))]

    def bills_to_remind(self) -> list[dict]:
        """Open bills due within BILL_WARN_DAYS (or overdue) not yet reminded today."""
        today = local_now().date().isoformat()
        return [b for b in self.open_bills(BILL_WARN_DAYS)
                if self.memory.db.execute("SELECT reminded_on FROM bills WHERE id = ?",
                                          (b["id"],)).fetchone()["reminded_on"] != today]

    def reminded(self, bill_id: int) -> None:
        self.memory.db.execute("UPDATE bills SET reminded_on = ? WHERE id = ?",
                               (local_now().date().isoformat(), bill_id))
        self.memory.db.commit()

    def morning_line(self) -> str:
        bills = self.open_bills(BILL_WARN_DAYS)
        if not bills:
            return "no bills due in the next few days"
        return "; ".join(f"{b['biller']} {b['amount']:.2f} {b['currency']} due {b['due_date']}" for b in bills)

    def today_line(self) -> str:
        s = self.summary("today")["by_currency"]
        return ", ".join(f"{v['total']:.2f} {c}" for c, v in s.items()) or "nothing recorded"


def bill_reminder_text(bill: dict) -> str:
    when = date.fromisoformat(bill["due_date"])
    days = (when - local_now().date()).days
    day = "today" if days == 0 else "tomorrow" if days == 1 else (
        f"on {when.strftime('%A')}" if days > 1 else f"— it was due {when.strftime('%A %d %B')}")
    return f"Your {bill['biller']} bill of {bill['amount']:.2f} {bill['currency']} is due {day}."


MONEY_TOOLS = [
    {
        "name": "spending",
        "description": "What the user spent, from bank and card emails plus expenses added by voice: totals "
        "per currency and category, top merchants. period: today, this_week, this_month, last_month. "
        "With recent=true, the latest transactions instead.",
        "input_schema": {
            "type": "object",
            "properties": {
                "period": {"type": "string", "enum": ["today", "this_week", "this_month", "last_month"]},
                "category": {"type": "string", "enum": list(CATEGORIES)},
                "recent": {"type": "boolean"},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "add_expense",
        "description": "Record something the user paid for (cash, or anything the bank emails won't show). "
        "A refund is a negative amount. date: YYYY-MM-DD, default today.",
        "input_schema": {
            "type": "object",
            "properties": {
                "amount": {"type": "number"},
                "currency": {"type": "string", "description": "ISO code; default USD"},
                "what": {"type": "string", "description": "Shop or what it was for"},
                "category": {"type": "string", "enum": list(CATEGORIES)},
                "date": {"type": "string"},
            },
            "required": ["amount", "what", "category"],
            "additionalProperties": False,
        },
    },
    {
        "name": "bills",
        "description": "Bills coming up (from statement emails, or added here), oldest due first. "
        "To add one: biller, amount, due_date. To mark one paid: paid = its id or biller.",
        "input_schema": {
            "type": "object",
            "properties": {
                "days": {"type": "integer", "description": "How far ahead, default 14"},
                "biller": {"type": "string"},
                "amount": {"type": "number"},
                "currency": {"type": "string"},
                "due_date": {"type": "string"},
                "paid": {"type": "string"},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "spending_limit",
        "description": "Monthly spending limits per category (or 'total'): list them with what's spent and "
        "left, or set one (amount 0 removes it). Buddy warns at 80% and 100%.",
        "input_schema": {
            "type": "object",
            "properties": {
                "category": {"type": "string", "enum": list(CATEGORIES) + ["total"]},
                "amount": {"type": "number"},
                "currency": {"type": "string"},
            },
            "additionalProperties": False,
        },
    },
]
MONEY_TOOL_NAMES = {t["name"] for t in MONEY_TOOLS}


def run_money_tool(money: Money, name: str, args: dict) -> str:
    if name == "spending":
        if args.get("recent"):
            return json.dumps(money.recent()) if money.recent() else "No transactions yet."
        return json.dumps(money.summary(args.get("period", "this_month"), args.get("category", "")))
    if name == "add_expense":
        day = args.get("date") or None
        warnings = money.add_transaction(float(args["amount"]), args.get("currency", "USD"), args["what"],
                                         args["category"], day)
        return " ".join(["Recorded."] + warnings)
    if name == "bills":
        if args.get("paid"):
            bill = money.mark_paid(args["paid"])
            return f"Marked paid: {bill['biller']}." if bill else "No open bill matches that."
        if args.get("biller") and args.get("amount") and args.get("due_date"):
            money.add_bill(args["biller"], float(args["amount"]), args.get("currency", "USD"), args["due_date"])
            return "Bill added; I'll remind you before it's due."
        bills = money.open_bills(int(args.get("days") or 14))
        return json.dumps(bills) if bills else "No open bills coming up."
    if name == "spending_limit":
        if args.get("category") and args.get("amount") is not None:
            money.set_limit(args["category"], float(args["amount"]), args.get("currency", "USD"))
            return "Limit removed." if float(args["amount"]) <= 0 else "Limit set."
        return json.dumps(money.limits()) if money.limits() else "No limits set."
    raise ToolError(f"Unknown tool {name}")
