"""Money back: every refund and statement credit on the owner's cards, refunds they're owed (Buddy watches for them and
says when one posts, or reminds them when it's late), and which cards Buddy can't see refunds for, so it never
guesses about one.

  credits(memory, days, card)   -> refunds and statement credits Buddy has (the bank feed, emails, statements, added
                                   by the owner), newest first, with where each came from
  coverage(memory)              -> which cards' refunds Buddy can see (linked through Plaid) and which it can't
  expect(memory, merchant, ...) -> a refund the owner is owed ("Marriott is refunding $100 to my Amex"); a refund
                                   email from a merchant adds one by itself when a bank feed is on (the card's own
                                   record then confirms it)
  match(memory) / reminders(memory) -> what to tell the owner in the app: one posted, or one is late
  owed(memory), received(memory, id), cancel(memory, id)

Worked out by code from the owner's own data; nothing here is sent anywhere.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone

from .memory import Memory
from .tools import ToolError, local_now

SCHEMA = """
CREATE TABLE IF NOT EXISTS refunds_owed (      -- a refund the owner is waiting for
    id INTEGER PRIMARY KEY, merchant TEXT NOT NULL, amount REAL, currency TEXT NOT NULL DEFAULT 'USD',
    card TEXT NOT NULL DEFAULT '', expected_by TEXT NOT NULL, note TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT 'you',        -- you | email:<record id>
    created_at TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'waiting',   -- waiting | received | cancelled
    received_day TEXT NOT NULL DEFAULT '', received_amount REAL, received_card TEXT NOT NULL DEFAULT '',
    reminded INTEGER NOT NULL DEFAULT 0
);
"""
WAIT_DAYS = 14      # a card refund usually posts within 5 to 10 business days
LOOK_BACK = 7       # a credit up to this many days before the owner mentioned it still counts
STOP = {"the", "inc", "llc", "co", "corp", "com", "www", "and", "of", "store", "online", "usa", "us", "payment",
        "refund", "credit", "return", "purchase"}


def _ready(memory: Memory) -> None:
    memory.db.executescript(SCHEMA)


def _money(amount: float, currency: str = "USD") -> str:
    return f"${amount:,.2f}" if (currency or "USD").upper() == "USD" else f"{amount:,.2f} {currency.upper()}"


def _words(name: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", (name or "").lower()) if len(w) > 2 and w not in STOP}


def _same_merchant(a: str, b: str) -> bool:
    wa, wb = _words(a), _words(b)
    return bool(wa and wb and (wa & wb))


def _last4(card: str) -> str:
    digits = re.sub(r"\D", "", card or "")
    return digits[-4:] if len(digits) >= 4 else ""


def _from(source: str) -> str:
    s = source or ""
    return ("your bank feed" if s.startswith(("plaid:", "simplefin:")) else "an email" if s.startswith("email:")
            else "a statement" if s.startswith(("statement:", "import:")) else "you")


# --- what came back --------------------------------------------------------------------------------------------
def credits(memory: Memory, days: int = 90, card: str = "", today: date | None = None) -> list[dict]:
    """Refunds and statement credits (negative transactions), newest first."""
    from .money import Money  # noqa: PLC0415  (its tables)
    Money(memory)
    since = ((today or local_now().date()) - timedelta(days=max(1, min(int(days or 90), 730)))).isoformat()
    rows = [dict(r) for r in memory.db.execute(
        "SELECT id, day, amount, currency, merchant, card, source, detail FROM transactions WHERE amount < 0 AND "
        "day >= ? ORDER BY day DESC, id DESC", (since,))]
    four = _last4(card)
    out = []
    for r in rows:
        if card and not (four and four == _last4(r["card"])) and card.lower() not in (r["card"] or "").lower():
            continue
        out.append({"id": r["id"], "day": r["day"], "amount": round(-r["amount"], 2), "currency": r["currency"],
                    "merchant": r["merchant"], "card": r["card"],
                    "kind": "Statement credit" if r["detail"] == "Statement credit" else "Refund",
                    "from": _from(r["source"])})
    return out


def coverage(memory: Memory) -> dict:
    """Which cards' refunds Buddy sees (linked through Plaid: every charge and credit) and which it knows only from
    bills or emails (a refund there shows only if an email says so)."""
    linked, known = [], []
    try:
        linked = [dict(r) for r in memory.db.execute(
            "SELECT name, mask FROM plaid_accounts WHERE type = 'credit' ORDER BY name")]
    except Exception:  # noqa: BLE001 - Plaid not set up
        linked = []
    masks = {(r["mask"] or "")[-4:] for r in linked}
    try:
        known = [dict(r) for r in memory.db.execute("SELECT name, issuer, last4 FROM fin_cards ORDER BY name")]
    except Exception:  # noqa: BLE001
        known = []
    unseen = [c for c in known if not (c["last4"] and c["last4"][-4:] in masks)]
    return {"linked": [f"{r['name']} ••{(r['mask'] or '')[-4:]}" if r["mask"] else r["name"] for r in linked],
            "unseen": [f"{c['name']}{' ••' + c['last4'][-4:] if c['last4'] else ''}" for c in unseen],
            "note": ("Refunds show for cards linked in Connections (Plaid, read-only). For a card that isn't linked, "
                     "Buddy sees a refund only if an email says so, or a statement you add lists it.")}


# --- refunds the owner is owed ----------------------------------------------------------------------------------
def expect(memory: Memory, merchant: str, amount=None, currency: str = "USD", card: str = "", by: str = "",
           note: str = "", source: str = "you", today: date | None = None) -> dict:
    merchant = " ".join(str(merchant or "").split())[:80]
    if not merchant:
        raise ToolError("Who is the refund from?")
    try:
        amount = None if amount in (None, "") else round(abs(float(amount)), 2)
    except (TypeError, ValueError):
        raise ToolError("How much, as a number?") from None
    today = today or local_now().date()
    try:
        until = date.fromisoformat(str(by)[:10]) if by else today + timedelta(days=WAIT_DAYS)
    except ValueError:
        raise ToolError("By when? Like 2026-12-10.") from None
    _ready(memory)
    for r in memory.db.execute("SELECT * FROM refunds_owed WHERE status = 'waiting'"):
        if _same_merchant(r["merchant"], merchant) and (amount is None or r["amount"] is None
                                                        or abs((r["amount"] or 0) - amount) < 0.01):
            return dict(r) | {"already": True}  # the same refund said twice (or an email and the owner)
    cur = memory.db.execute(
        "INSERT INTO refunds_owed (merchant, amount, currency, card, expected_by, note, source, created_at) VALUES "
        "(?, ?, ?, ?, ?, ?, ?, ?)", (merchant, amount, (currency or "USD").upper()[:3], " ".join(str(card).split())[:40],
                                     until.isoformat(), " ".join(str(note).split())[:200], source[:40],
                                     datetime.now(timezone.utc).isoformat()))
    memory.db.commit()
    return dict(memory.db.execute("SELECT * FROM refunds_owed WHERE id = ?", (cur.lastrowid,)).fetchone())


def notice(memory: Memory, merchant: str, amount: float, currency: str, card: str, record_id) -> None:
    """A refund email ("your refund has been processed") while a bank feed is on: watched for, so the owner hears
    when it posts to the card, or that it's late."""
    if merchant and amount:
        expect(memory, merchant, amount, currency, card, source=f"email:{record_id}")


def owed(memory: Memory) -> list[dict]:
    _ready(memory)
    return [dict(r) for r in memory.db.execute("SELECT * FROM refunds_owed WHERE status = 'waiting' ORDER BY expected_by")]


def _set(memory: Memory, refund_id: int, status: str, **got) -> dict:
    _ready(memory)
    row = memory.db.execute("SELECT * FROM refunds_owed WHERE id = ?", (int(refund_id or 0),)).fetchone()
    if not row:
        raise ToolError("No refund with that number: list them with owed.")
    memory.db.execute("UPDATE refunds_owed SET status = ?, received_day = ?, received_amount = ?, received_card = ? "
                      "WHERE id = ?", (status, got.get("day", ""), got.get("amount"), got.get("card", ""), row["id"]))
    memory.db.commit()
    return dict(row)


def received(memory: Memory, refund_id: int) -> dict:
    """The owner says it came (a check, a bank account Buddy can't see)."""
    return _set(memory, refund_id, "received", day=local_now().date().isoformat())


def cancel(memory: Memory, refund_id: int) -> dict:
    return _set(memory, refund_id, "cancelled")


def match(memory: Memory) -> list[str]:
    """Each refund owed that has now posted to a card: marked received, and a line for the owner."""
    _ready(memory)
    notes = []
    for r in owed(memory):
        since = (datetime.fromisoformat(r["created_at"]).date() - timedelta(days=LOOK_BACK)).isoformat()
        taken = {x["received_day"] + str(x["received_amount"]) for x in memory.db.execute(
            "SELECT received_day, received_amount FROM refunds_owed WHERE status = 'received'")}
        for c in reversed(credits(memory, 730)):
            if c["day"] < since or not _same_merchant(c["merchant"], r["merchant"]):
                continue
            if r["amount"] is not None and abs(c["amount"] - r["amount"]) > max(1.0, r["amount"] * 0.02):
                continue
            if r["card"] and _last4(r["card"]) and _last4(r["card"]) != _last4(c["card"]):
                continue
            if c["day"] + str(c["amount"]) in taken:
                continue
            _set(memory, r["id"], "received", day=c["day"], amount=c["amount"], card=c["card"])
            notes.append(f"✅ Your refund from {r['merchant']} posted: {_money(c['amount'], c['currency'])}"
                         + (f" on card {c['card']}" if c["card"] else "") + f" on {c['day']}.")
            break
    return notes


def reminders(memory: Memory, today: date | None = None) -> list[str]:
    """Refunds owed past the day they were expected: said once."""
    _ready(memory)
    today = today or local_now().date()
    notes = []
    for r in owed(memory):
        if r["reminded"] or r["expected_by"] >= today.isoformat():
            continue
        memory.db.execute("UPDATE refunds_owed SET reminded = 1 WHERE id = ?", (r["id"],))
        what = (_money(r["amount"], r["currency"]) + " ") if r["amount"] is not None else ""
        notes.append(f"⏳ Your {what}refund from {r['merchant']} hasn't shown on a card Buddy can see (expected by "
                     f"{r['expected_by']}). Check with them, or tell Buddy if it came another way.")
    memory.db.commit()
    return notes


# --- the tool ----------------------------------------------------------------------------------------------------
TOOL = {
    "name": "refunds",
    "description": "Money back on the user's cards. list (days, default 90; card: a name or last 4): every refund and "
    "statement credit Buddy has, with where it came from (bank feed, email, statement, added by them), plus which "
    "cards Buddy can't see refunds for. Say only what this returns: when a refund isn't in it, say Buddy doesn't see "
    "it and why (the card isn't linked, or it hasn't posted). expect (merchant, amount, card, by YYYY-MM-DD, note): a "
    "refund they're owed ('Marriott is refunding $100 to my Amex'); Buddy watches for it, tells them in the app when "
    "it posts, and reminds them if it's late (two weeks unless they say). owed: what they're still waiting for. "
    "received (id): it came another way. cancel (id): not expected any more.",
    "input_schema": {"type": "object", "properties": {
        "action": {"type": "string", "enum": ["list", "expect", "owed", "received", "cancel"]},
        "days": {"type": "integer"}, "card": {"type": "string"}, "merchant": {"type": "string"},
        "amount": {"type": "number"}, "currency": {"type": "string"}, "by": {"type": "string"},
        "note": {"type": "string"}, "id": {"type": "integer"}},
        "required": ["action"], "additionalProperties": False},
}


def _coverage_line(memory: Memory) -> str:
    c = coverage(memory)
    parts = []
    if c["linked"]:
        parts.append("Cards Buddy sees every refund on (linked): " + ", ".join(c["linked"]) + ".")
    if c["unseen"]:
        parts.append("Cards Buddy can't see refunds on unless an email or a statement shows them: "
                     + ", ".join(c["unseen"]) + " (link them in Profile > Connections).")
    if not c["linked"]:
        parts.append("No card is linked yet, so Buddy sees refunds only from emails, statements and what you add.")
    return " ".join(parts)


def run_tool(memory: Memory, args: dict) -> str:
    import json  # noqa: PLC0415
    act = args.get("action")
    if act == "list":
        got = credits(memory, int(args.get("days") or 90), str(args.get("card") or ""))
        head = (json.dumps(got[:40]) if got else f"No refunds or statement credits in the last "
                f"{int(args.get('days') or 90)} days{' on that card' if args.get('card') else ''}.")
        waiting = owed(memory)
        return head + ("\nStill waiting for: " + json.dumps([{k: r[k] for k in ("id", "merchant", "amount", "card",
                                                                                 "expected_by")} for r in waiting])
                       if waiting else "") + "\n" + _coverage_line(memory)
    if act == "expect":
        r = expect(memory, str(args.get("merchant") or ""), args.get("amount"), str(args.get("currency") or "USD"),
                   str(args.get("card") or ""), str(args.get("by") or ""), str(args.get("note") or ""))
        if r.get("already"):
            return f"Already watching for that refund from {r['merchant']} (number {r['id']}, by {r['expected_by']})."
        now = match(memory)
        return (f"Watching for the refund from {r['merchant']}"
                + (f" of {_money(r['amount'], r['currency'])}" if r["amount"] is not None else "")
                + f" (number {r['id']}). I'll tell you in the app when it posts, or by {r['expected_by']} if it hasn't. "
                + (" ".join(now) if now else "") + " " + _coverage_line(memory)).strip()
    if act == "owed":
        waiting = owed(memory)
        return json.dumps(waiting) if waiting else "No refunds you're waiting for."
    if act == "received":
        r = received(memory, args.get("id"))
        return f"Marked the refund from {r['merchant']} as received."
    if act == "cancel":
        r = cancel(memory, args.get("id"))
        return f"Stopped watching for the refund from {r['merchant']}."
    raise ToolError("Use list, expect, owed, received or cancel.")
