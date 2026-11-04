"""Cancelling subscriptions, made easy. Buddy helps with every step; the user taps the final button.

1. Guided cancel: the cancel page opens on the phone and Buddy reads it through the form helper
   (fill_form read), saying what to tap and pointing out the traps (offers, "pause instead",
   "are you sure?"). Buddy never presses the final Cancel or Confirm: the user does.
2. A discount first: before cancelling, a short script to ask for a retention offer.
3. By email: for services without an online cancel, Buddy writes the cancellation email; it is saved
   as a Gmail draft or sent from the user's Gmail after their yes (the Gmail agent asks first).
4. Proof: a "your cancellation is confirmed" email is saved against the subscription, with its date.
5. If they keep charging: a dispute letter for the card issuer with the cancellation date and the
   proof, and where to file it in the issuer's app.
6. Free trials: a signup email that says when the trial ends is noted, and the user is reminded two
   days before it starts charging.
"""

import json
import re
from datetime import date, datetime, timedelta, timezone

from .memory import Memory
from .tools import ToolError, local_now

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS fin_cancel_proofs (
    id INTEGER PRIMARY KEY,
    subscription_id INTEGER,
    merchant TEXT NOT NULL,
    day TEXT NOT NULL,                 -- when the cancellation was confirmed
    record_id INTEGER,                 -- the confirmation email (records.id)
    subject TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS fin_trials (
    id INTEGER PRIMARY KEY,
    merchant TEXT NOT NULL,
    ends TEXT NOT NULL,                -- YYYY-MM-DD: the first charge
    amount REAL,
    currency TEXT NOT NULL DEFAULT 'USD',
    record_id INTEGER UNIQUE,
    status TEXT NOT NULL DEFAULT 'open',  -- open | kept | cancelled
    told TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
"""

TRAPS = ("an offer to stay (a discount or free month): skip it unless you want it",
         "'pause instead': only if you want to come back",
         "a survey: optional, you can skip it",
         "a final 'Are you sure?' / 'Finish cancellation' button: that's the one that actually cancels",
         "a confirmation screen or email: keep it, it's your proof")
RETENTION_SCRIPT = ("Hi, I'd like to cancel my {merchant} subscription. Before I do: is there a discount or a cheaper "
                    "plan you can offer to keep me? If not, please cancel it today and email me a confirmation.")
CANCEL_CONFIRMED = re.compile(
    r"(?i)(?:subscription|membership|plan|account|trial)?[^.\n]{0,40}\b(?:has been|was|is now|successfully)?\s*"
    r"(?:cancel+ed|cancel+ation (?:confirmed|complete|is confirmed|request received))|"
    r"we(?:'|’)?re sorry to see you go|your cancel+ation")
TRIAL = re.compile(r"(?i)free trial|trial (?:period|ends|will end)|start(?:s|ing)? your (?:free )?trial")
MONTHS = {m: i for i, m in enumerate(("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov",
                                      "dec"), 1)}
DATE_NEAR = re.compile(r"(?i)(?:ends?|end(?:ing)? on|until|through|will be charged(?: on)?|billing (?:starts|begins)(?: on)?|"
                       r"first (?:charge|payment|bill)(?: on| date)?|renews? on|convert(?:s)? on)(?:\s+on)?[:\s]+"
                       r"((?:[A-Z][a-z]{2,8}\.? \d{1,2}(?:st|nd|rd|th)?,? \d{4})|(?:\d{1,2} [A-Z][a-z]{2,8},? \d{4})|"
                       r"(?:\d{1,2}/\d{1,2}/\d{2,4})|(?:\d{4}-\d{2}-\d{2})|(?:[A-Z][a-z]{2,8}\.? \d{1,2}(?:st|nd|rd|th)?))")
AMOUNT = re.compile(r"(?:\$|USD\s?)(\d{1,4}(?:\.\d{2})?)")
ISSUER_DISPUTE = {
    "american express": "In the Amex app: Statements & Activity, tap the charge, then 'Dispute charge'. Or "
                        "americanexpress.com, Statements & Activity, the charge, Dispute.",
    "chase": "In the Chase app: tap the card, tap the charge, then 'Dispute transaction'. Or chase.com, the charge, "
             "'Dispute this transaction'.",
    "citi": "In the Citi app or citi.com: the card's activity, the charge, 'Dispute'.",
    "capital one": "In the Capital One app: the charge, 'Report a problem' / 'Dispute transaction'.",
    "discover": "In the Discover app or discover.com: Activity, the charge, 'Dispute'.",
    "bank of america": "In the Bank of America app: the card, the charge, 'Dispute this transaction'.",
    "wells fargo": "In the Wells Fargo app: the account, the charge, 'Dispute a transaction'.",
}


def _parse_date(text: str, received: date) -> date | None:
    t = text.strip().replace(".", "").replace(",", "")
    t = re.sub(r"(\d)(st|nd|rd|th)\b", r"\1", t)
    m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", t)
    try:
        if m:
            return date(int(m[1]), int(m[2]), int(m[3]))
        m = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{2,4})", t)
        if m:
            y = int(m[3]) + (2000 if len(m[3]) == 2 else 0)
            return date(y, int(m[1]), int(m[2]))
        m = re.fullmatch(r"([A-Za-z]{3,9}) (\d{1,2})(?: (\d{4}))?", t) or re.fullmatch(r"(\d{1,2}) ([A-Za-z]{3,9}) (\d{4})", t)
        if m:
            a, b = m[1], m[2]
            month, day = (MONTHS.get(a[:3].lower()), int(b)) if a.isalpha() else (MONTHS.get(b[:3].lower()), int(a))
            year = int(m[3]) if m.lastindex and m.lastindex >= 3 and m[3] else received.year
            if not month:
                return None
            d = date(year, month, day)
            if not (m.lastindex and m.lastindex >= 3 and m[3]) and d < received:
                d = date(year + 1, month, day)  # "ends Jan 3" said in December
            return d
    except ValueError:
        return None
    return None


def _merchant_from(sender: str, subject: str) -> str:
    """'Netflix <info@account.netflix.com>' -> 'Netflix'."""
    name = re.sub(r"<[^>]*>|\"", "", sender or "").strip()
    if not name or "@" in name:
        dom = re.search(r"@([a-z0-9-]+)\.", (sender or "").lower())
        name = dom.group(1) if dom else ""
        name = re.sub(r"^(mail|email|info|accounts?|billing|noreply|no-reply|news)$", "", name)
    name = re.sub(r"(?i)\b(team|support|billing|no-?reply|accounts?|customer care)\b", "", name).strip(" -|")
    return (name or subject.split(":")[0])[:60].strip().title() if name.islower() else (name or subject)[:60].strip()


class Cancelling:
    def __init__(self, memory: Memory):
        self.memory = memory
        memory.db.executescript(SCHEMA_SQL)

    def _fin(self):
        from .finance import Finance
        return Finance(self.memory)

    # --- 1 and 2: the guided cancel, a discount first ------------------------------------------------
    def guide(self, what: str) -> dict:
        fin = self._fin()
        page = fin.cancel_page(what)
        fin.decide(what, "cancel")
        return {**page, "retention_script": RETENTION_SCRIPT.format(merchant=page["merchant"]),
                "traps": list(TRAPS),
                "next": ("Open the cancel page (it opens on the phone), then use fill_form read to see the screen and "
                         "say which button to tap at each step; scroll when needed. Point out offers and 'pause' "
                         "buttons. Never press the final Cancel/Confirm yourself: the user taps it. If there is no "
                         "page, search how to cancel it; if it can only be done by email, use cancel_email.")
                if page["url"] else
                ("No known cancel page: search the web for how to cancel it (the official help page). If it's by "
                 "email, offer cancel_email; by phone, give the number and the retention script.")}

    # --- 3: cancel by email -------------------------------------------------------------------------
    def cancel_email(self, what: str, to: str = "", account_email: str = "", name: str = "") -> dict:
        from .config import settings

        sub = self._fin()._find(what)
        who = name or settings.user_name
        account = f" The account is under {account_email}." if account_email else ""
        body = (f"Hello,\n\nPlease cancel my {sub['merchant']} subscription, effective immediately, and stop all "
                f"future charges.{account} My last payment was {sub['amount']:.2f} {sub['currency']} on "
                f"{sub['last_charge']}.\n\nPlease reply to confirm the cancellation and the date it takes effect.\n\n"
                f"Thank you,\n{who}")
        self._fin().decide(what, "cancel")
        return {"to": to, "subject": f"Cancel my {sub['merchant']} subscription", "body": body,
                "next": "Show it to the user. Save it with the Gmail agent's create_draft, or send_email after their "
                        "yes (it asks itself). If 'to' is empty, find the official cancellation address first."}

    # --- 4: proof -----------------------------------------------------------------------------------
    def scan_email(self, record: dict, sender: str = "") -> list[str]:
        """One incoming email: a cancellation confirmation (saved as proof) or a free-trial signup (reminder)."""
        subject, body = record.get("title") or "", record.get("body") or ""
        text = f"{subject}\n{body[:3000]}"
        received = (record.get("start_at") or local_now().isoformat())[:10]
        try:
            got = date.fromisoformat(received)
        except ValueError:
            got = local_now().date()
        notes = []
        if CANCEL_CONFIRMED.search(subject) or (CANCEL_CONFIRMED.search(body[:600]) and "cancel" in subject.lower()):
            note = self._proof(record, sender, subject, got)
            if note:
                notes.append(note)
        elif TRIAL.search(text):
            note = self._trial(record, sender, subject, text, got)
            if note:
                notes.append(note)
        return notes

    def _match(self, sender: str, subject: str):
        from .finance import merchant_key
        key = merchant_key(_merchant_from(sender, subject))
        words = [w for w in re.findall(r"[a-z]{3,}", f"{sender} {subject}".lower())]
        for s in self.memory.db.execute("SELECT * FROM fin_subscriptions").fetchall():
            k = s["key"]
            if k and (k == key or any(w == k.split()[0] for w in words)):
                return s
        return None

    def _proof(self, record: dict, sender: str, subject: str, got: date) -> str:
        if self.memory.db.execute("SELECT 1 FROM fin_cancel_proofs WHERE record_id = ?", (record.get("id"),)).fetchone():
            return ""
        sub = self._match(sender, subject)
        merchant = sub["merchant"] if sub else _merchant_from(sender, subject)
        self.memory.db.execute("INSERT INTO fin_cancel_proofs (subscription_id, merchant, day, record_id, subject, "
                               "created_at) VALUES (?, ?, ?, ?, ?, ?)",
                               (sub["id"] if sub else None, merchant, got.isoformat(), record.get("id"), subject[:200],
                                datetime.now(timezone.utc).isoformat()))
        if sub:
            self.memory.db.execute("UPDATE fin_subscriptions SET status = 'cancel', decided_at = ? WHERE id = ? AND "
                                   "status != 'cancelled'", (got.isoformat(), sub["id"]))
        self.memory.db.execute("UPDATE fin_trials SET status = 'cancelled' WHERE lower(merchant) = lower(?)", (merchant,))
        self.memory.db.commit()
        return (f"Saved {merchant}'s cancellation confirmation ({got:%b %d}) as proof. I'll check the charges stop; if "
                "they don't, I'll help you dispute it.")

    # --- 6: free trials -----------------------------------------------------------------------------
    def _trial(self, record: dict, sender: str, subject: str, text: str, got: date) -> str:
        found = DATE_NEAR.search(text)
        ends = _parse_date(found.group(1), got) if found else None
        if not ends or ends < got or ends > got + timedelta(days=400):
            return ""
        merchant = _merchant_from(sender, subject)
        amount = AMOUNT.search(text)
        try:
            self.memory.db.execute(
                "INSERT INTO fin_trials (merchant, ends, amount, record_id, created_at) VALUES (?, ?, ?, ?, ?)",
                (merchant, ends.isoformat(), float(amount.group(1)) if amount else None, record.get("id"),
                 datetime.now(timezone.utc).isoformat()))
        except Exception:  # this email was seen already
            return ""
        self.memory.db.commit()
        cost = f" (then ${float(amount.group(1)):.2f})" if amount else ""
        return (f"Noted your {merchant} free trial: it starts charging {ends:%b %d}{cost}. I'll remind you two days "
                "before, so you can cancel in time if you don't want it.")

    def trials(self) -> list[dict]:
        return [dict(r) for r in self.memory.db.execute("SELECT * FROM fin_trials WHERE status = 'open' ORDER BY ends")]

    def trial_alerts(self, today: date | None = None) -> list[dict]:
        today = today or local_now().date()
        out = []
        for t in self.trials():
            days = (date.fromisoformat(t["ends"]) - today).days
            step = "2" if 1 <= days <= 2 else "0" if days == 0 else ""
            if not step or step in t["told"]:
                continue
            when = "today" if days == 0 else f"in {days} day{'s' if days != 1 else ''} ({date.fromisoformat(t['ends']):%b %d})"
            cost = f", ${t['amount']:.2f}" if t["amount"] else ""
            out.append({"id": t["id"], "step": step,
                        "text": f"Your {t['merchant']} free trial starts charging {when}{cost}. Keep it, or shall I open "
                                "the cancel page and walk you through it?"})
        return out

    def told_trial(self, alert: dict) -> None:
        self.memory.db.execute("UPDATE fin_trials SET told = told || ? WHERE id = ?", (alert["step"], alert["id"]))
        self.memory.db.commit()

    def keep_trial(self, what: str) -> str:
        n = self.memory.db.execute("UPDATE fin_trials SET status = 'kept' WHERE status = 'open' AND lower(merchant) LIKE ?",
                                   (f"%{str(what).lower()}%",)).rowcount
        self.memory.db.commit()
        return "Okay, no more reminders for it." if n else "I don't have a trial like that."

    # --- 5: dispute help ----------------------------------------------------------------------------
    def dispute(self, what: str) -> dict:
        from .config import settings
        from .finance import merchant_key

        sub = self._fin()._find(what)
        proof = self.memory.db.execute(
            "SELECT * FROM fin_cancel_proofs WHERE subscription_id = ? OR lower(merchant) = lower(?) ORDER BY day DESC "
            "LIMIT 1", (sub["id"], sub["merchant"])).fetchone()
        cancelled = (proof["day"] if proof else (sub.get("decided_at") or ""))[:10]
        if not cancelled:
            raise ToolError("When did they cancel it? Tell me the date (and forward the confirmation email if there is one).")
        key = merchant_key(sub["merchant"])
        charges = [dict(r) for r in self.memory.db.execute(
            "SELECT day, amount, currency, card, merchant FROM transactions WHERE day > ? AND amount > 0 ORDER BY day",
            (cancelled,)) if merchant_key(r["merchant"]) == key]
        if not charges:
            return {"merchant": sub["merchant"], "charges_after_cancel": [],
                    "note": f"No {sub['merchant']} charge after {cancelled} in the transactions I have."}
        card = charges[-1]["card"]
        issuer = ""
        try:
            row = self.memory.db.execute(
                "SELECT i.institution FROM plaid_accounts a JOIN plaid_items i ON i.item_id = a.item_id WHERE a.mask = ?",
                (card,)).fetchone() or self.memory.db.execute("SELECT issuer AS institution FROM fin_cards WHERE last4 = ?",
                                                             (card,)).fetchone()
            issuer = (row["institution"] if row else "") or ""
        except Exception:
            pass
        how = next((v for k, v in ISSUER_DISPUTE.items() if k in issuer.lower()), "In your card's app: tap the charge, "
                   "then 'Dispute'.")
        lines = "; ".join(f"{c['amount']:.2f} {c['currency']} on {c['day']}" for c in charges)
        letter = (f"I am disputing {len(charges)} charge{'s' if len(charges) != 1 else ''} from {sub['merchant']} on my card "
                  f"ending {card}: {lines}. I cancelled this subscription on {cancelled}"
                  + (f" and received a written confirmation that day (\"{proof['subject']}\")" if proof else "")
                  + ". The merchant continued to charge me after the cancellation. Please reverse these charges.\n\n"
                  + settings.user_name)
        return {"merchant": sub["merchant"], "charges_after_cancel": charges, "cancelled_on": cancelled,
                "proof": dict(proof) if proof else None, "issuer": issuer, "where_to_file": how, "letter": letter,
                "next": "Show the letter and where to file it. Attach or forward the confirmation email if the issuer "
                        "asks. Buddy never files the dispute itself."}

    def proofs(self) -> list[dict]:
        return [dict(r) for r in self.memory.db.execute("SELECT * FROM fin_cancel_proofs ORDER BY day DESC LIMIT 30")]


def run_tool(memory: Memory, args: dict) -> str:
    c = Cancelling(memory)
    a = args.get("action")
    what = str(args.get("what") or "")
    if a == "cancel_email":
        out = c.cancel_email(what, str(args.get("to") or ""), str(args.get("account_email") or ""))
    elif a == "dispute_help":
        out = c.dispute(what)
    elif a == "trials":
        out = {"trials": c.trials(), "proofs": c.proofs()}
    elif a == "keep_trial":
        return c.keep_trial(what)
    else:
        out = c.guide(what)
    return json.dumps(out, ensure_ascii=False, default=str)
