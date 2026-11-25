"""Payments to and from people, from the payment apps' own emails: Venmo, Zelle (sent by your bank), PayPal, Cash App.

Read in code from the subject (and, for Venmo, the note under the amount), with no AI, from the email Buddy already
fetched. Runs with the email manager, so it keeps working while background AI work is paused.

  "You paid Peiji Shi $38.00"      -> to Peiji Shi, $38.00, Venmo, note "Lokal"
  "Neha paid you $20.00"           -> from Neha, $20.00
  "You sent $500.00 to NEHA S"     -> to Neha S, $500.00, Zelle (a bank's email that says Zelle)

Kept in their own table (p2p_payments) and shown on the Money page, and not added to what you spent: when a payment
is paid from a bank or card, the bank feed or the card's email already counts that money, so adding it again would
count it twice. The owner can switch it off per sender on the Email page ("payments to people"). Kept two years.
"""

import json
import logging
import re
from datetime import datetime, timedelta, timezone

from .memory import Memory

log = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS p2p_payments (
    id INTEGER PRIMARY KEY AUTOINCREMENT, record_id INTEGER, day TEXT NOT NULL, app TEXT NOT NULL,
    direction TEXT NOT NULL, person TEXT NOT NULL, amount REAL NOT NULL, note TEXT NOT NULL DEFAULT '',
    UNIQUE (app, direction, person, amount, day)
);
"""
BACKFILL_KEY, KEEP_DAYS = "paymail:backfilled_v1", 730  # kept two years, then deleted
APPS = {"venmo.com": "Venmo", "paypal.com": "PayPal", "cash.app": "Cash App", "square.com": "Cash App",
        "zellepay.com": "Zelle"}
AMOUNT = r"\$\s?(?P<amt>\d[\d,]*(?:\.\d{2})?)(?:\s?USD)?"
SUBJECTS = [
    ("out", re.compile(rf"^you paid (?P<who>.+?) {AMOUNT}", re.I)),
    ("in", re.compile(rf"^(?P<who>.+?) paid you {AMOUNT}", re.I)),
    ("out", re.compile(rf"^you completed (?P<who>.+?)'s {AMOUNT}", re.I)),
    ("out", re.compile(rf"^you(?:'ve)? sent (?:a )?{AMOUNT}(?: payment)? to (?P<who>.+?)\s*$", re.I)),
    ("in", re.compile(rf"^(?:you(?:'ve)? received|you got) (?:a )?{AMOUNT}(?: payment)? from (?P<who>.+?)\s*$", re.I)),
    ("in", re.compile(rf"^(?P<who>.+?) sent you {AMOUNT}", re.I)),
]
NOT_A_NOTE = re.compile(r"(see transaction|transaction details|payment id|like|comment|venmo|\$|^you |paid you|"
                        r"^to |^from |privacy|help|download)", re.I)


def _ready(memory: Memory) -> None:
    memory.db.executescript(SCHEMA)


def _app(sender: str, subject: str, body: str) -> str:
    from email.utils import parseaddr

    from .mailmap import BANKS  # noqa: PLC0415
    address = (parseaddr(sender)[1] or sender).lower()
    domain = address.rsplit("@", 1)[-1]
    app = next((name for d, name in APPS.items() if domain == d or domain.endswith("." + d)), "")
    if not app and BANKS.search(f"{sender}") and re.search(r"\bzelle\b", f"{subject} {body[:600]}", re.I):
        app = "Zelle"
    return app


def _note(body: str, amount: str) -> str:
    """Venmo puts the payment's note on its own line under the amount."""
    lines = [" ".join(line.split()) for line in str(body or "").splitlines() if line.strip()]
    digits = amount.replace(",", "").split(".")[0]
    for i, line in enumerate(lines):
        if digits and digits in line.replace(",", "") and "$" in line:
            for nxt in lines[i + 1:i + 3]:
                if 1 <= len(nxt) <= 60 and not NOT_A_NOTE.search(nxt) and not re.fullmatch(r"[\d.\s]+", nxt):
                    return nxt
            break
    return ""


def payment_in(sender: str, subject: str, body: str = "") -> dict | None:
    """{app, direction, person, amount, note} when this email is a payment app's own message about a payment."""
    app = _app(sender, subject, body)
    if not app:
        return None
    subject = " ".join(str(subject or "").split())
    for direction, rx in SUBJECTS:
        m = rx.search(subject)
        if not m:
            continue
        who = re.sub(r"\s+(?:with|via|using|on) (?:zelle|venmo|paypal|cash app)\b.*$", "", m.group("who"), flags=re.I)
        who = who.strip(" .,:;!-–")[:60]
        if not who or re.search(r"\$|\d{3,}", who):
            continue
        amount = float(m.group("amt").replace(",", ""))
        if amount <= 0:
            continue
        note = _note(body, m.group("amt")) if app == "Venmo" else ""
        return {"app": app, "direction": direction, "person": who.title() if who.isupper() else who,
                "amount": amount, "note": note}
    return None


def take(memory: Memory, emails: list[dict]) -> int:
    """Payments in these emails (rows of the email records: id, title, body, data, start_at). Returns how many new."""
    from . import mailmap  # noqa: PLC0415
    _ready(memory)
    added = 0
    for e in emails:
        try:
            data = json.loads(e.get("data") or "{}")
        except ValueError:
            data = {}
        sender = str(data.get("from", ""))
        got = payment_in(sender, e.get("title") or "", e.get("body") or "")
        if not got or not mailmap.use_on(memory, sender, "payments"):
            continue
        day = str(e.get("start_at") or "")[:10] or datetime.now(timezone.utc).date().isoformat()
        cur = memory.db.execute(
            "INSERT OR IGNORE INTO p2p_payments (record_id, day, app, direction, person, amount, note) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)", (e.get("id"), day, got["app"], got["direction"], got["person"],
                                              got["amount"], got["note"]))
        added += cur.rowcount
    memory.db.execute("DELETE FROM p2p_payments WHERE day < ?",
                      ((datetime.now(timezone.utc) - timedelta(days=KEEP_DAYS)).date().isoformat(),))
    memory.db.commit()
    return added


def backfill(memory: Memory) -> int:
    """Once: the payment emails Buddy already holds, from before this reader existed."""
    if memory.get(BACKFILL_KEY):
        return 0
    try:
        rows = [dict(r) for r in memory.db.execute(
            "SELECT id, title, body, data, start_at FROM records WHERE source = 'email' AND "
            "(lower(data) LIKE '%venmo%' OR lower(data) LIKE '%paypal%' OR lower(data) LIKE '%cash.app%' OR "
            "lower(data) LIKE '%square.com%' OR lower(title) LIKE '%zelle%' OR lower(body) LIKE '%zelle%') "
            "ORDER BY id")]
    except Exception:  # no email records yet
        return 0
    added = take(memory, rows)
    memory.set(BACKFILL_KEY, datetime.now(timezone.utc).isoformat())
    return added


def payments(memory: Memory, days: int = 90) -> dict:
    """The payments of the last `days` days, newest first, with the totals per person."""
    _ready(memory)
    since = (datetime.now(timezone.utc) - timedelta(days=days)).date().isoformat()
    rows = [dict(r) for r in memory.db.execute(
        "SELECT day, app, direction, person, amount, note FROM p2p_payments WHERE day >= ? ORDER BY day DESC, id DESC",
        (since,))]
    people: dict[str, dict] = {}
    for r in rows:
        p = people.setdefault(r["person"], {"person": r["person"], "paid": 0.0, "received": 0.0, "count": 0})
        p["paid" if r["direction"] == "out" else "received"] += r["amount"]
        p["count"] += 1
    return {"days": days, "payments": rows,
            "people": sorted(people.values(), key=lambda p: -(p["paid"] + p["received"])),
            "paid": round(sum(r["amount"] for r in rows if r["direction"] == "out"), 2),
            "received": round(sum(r["amount"] for r in rows if r["direction"] == "in"), 2)}
