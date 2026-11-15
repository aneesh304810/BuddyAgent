"""The subscription cancel service: Buddy finds what you pay for, you say which to cancel, and Buddy does the
cancelling and then checks it really stopped.

  1. Review    once a month (and on request): every subscription found in the card and bank charges, what it
               costs a month and a year, and what changed (new, price went up, charged on two cards, marked to
               cancel but still charging). Buddy can't see whether you use a service, so it doesn't guess.
  2. One yes   each cancellation is asked for on its own: the service, the price, the route and exactly what
               will be sent or said. Nothing is cancelled without that yes.
  3. Cancel    by the route that works for that service:
                 email    Buddy sends the cancellation email from your own address (the account's SMTP)
                 phone    Buddy's phone agent calls the service as your assistant, asks to cancel, turns down
                          offers to stay unless you set one you'd accept, and asks for a confirmation number
                          and the date it ends. It is honest about being an AI, gives only your name, callback
                          number and the account's email, never a card number, password or SSN, and never agrees
                          to a fee. Many services will only cancel for the account holder: then it says so and
                          you get the number to call.
                 guided   services that cancel only inside your signed-in account: the cancel page opens on the
                          phone and Buddy walks you through it; you tap the last button. Buddy never signs in
                          to your accounts and never holds their passwords.
  4. Check     the confirmation email is saved as proof (cancelling.py); the call's outcome is kept; then the
               charges are watched: if the next one doesn't come, it's reported as stopped; if a charge does
               come after the cancellation, Buddy says so and offers the dispute letter for the card issuer.

The phone number or email address to cancel with comes from the service's own official help page (looked up at
the time), never from memory. Everything here stays in the encrypted database.
"""

import json
import logging
import re
import smtplib
import ssl
from datetime import date, datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import formatdate, make_msgid

from .memory import Memory
from .tools import ToolError, local_now

log = logging.getLogger(__name__)

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS fin_cancel_jobs (
    id INTEGER PRIMARY KEY,
    subscription_id INTEGER NOT NULL,
    merchant TEXT NOT NULL,
    route TEXT NOT NULL,                 -- email | phone | guided
    target TEXT NOT NULL DEFAULT '',     -- the address written to, or the number called
    status TEXT NOT NULL DEFAULT 'waiting',  -- waiting | confirmed | stopped | still_charging | failed
    started TEXT NOT NULL,               -- YYYY-MM-DD
    call_id INTEGER,
    note TEXT NOT NULL DEFAULT '',       -- what happened, in words
    told TEXT NOT NULL DEFAULT '',       -- updates already given, so each is said once
    updated_at TEXT NOT NULL
);
"""
ROUTES = ("email", "phone", "guided")
OPEN = ("waiting", "confirmed")
REVIEW_KEY = "subcancel:review_month"
ADDRESS = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")
NUDGE_DAYS, GRACE_DAYS = 7, 2


def _ready(memory: Memory) -> None:
    from . import cancelling  # noqa: PLC0415  the proofs table is read here
    memory.db.executescript(cancelling.SCHEMA_SQL + SCHEMA_SQL)


def _fin(memory: Memory):
    from .finance import Finance  # noqa: PLC0415
    return Finance(memory)


def _money(amount: float, currency: str = "USD") -> str:
    return f"${amount:,.2f}" if currency == "USD" else f"{amount:,.2f} {currency}"


# --- 1: the review ------------------------------------------------------------------------------------------
def review(memory: Memory) -> dict:
    """Every active subscription with its cost and what changed, the ones worth a look first."""
    _ready(memory)
    found = _fin(memory).subscriptions()
    jobs = {j["subscription_id"]: j for j in memory.db.execute(
        "SELECT * FROM fin_cancel_jobs ORDER BY id").fetchall()}
    rows = []
    for s in found["subscriptions"]:
        if not s["active"] and s["status"] != "cancel":
            continue
        look = [f for f in s["flags"] if f.startswith(("new", "price up", "charged on more", "cancel pending"))]
        job = jobs.get(s["id"])
        rows.append({**s, "monthly_cost": round(s["yearly_cost"] / 12, 2), "look_at": look,
                     "cancelling": ({"route": job["route"], "status": job["status"], "started": job["started"],
                                     "note": job["note"]} if job else None)})
    rows.sort(key=lambda r: (not r["look_at"], -r["yearly_cost"]))
    return {"subscriptions": rows, "totals": found["totals"],
            "note": "Found from repeating charges on your cards and bank. I can't see whether you use a service, "
                    "so tell me which to cancel: each one is asked for on its own before anything is sent."}


def review_message(memory: Memory) -> str:
    """The monthly check-in, spoken || shown. '' when there are no subscriptions."""
    got = review(memory)
    rows = [r for r in got["subscriptions"] if r["status"] != "cancelled"]
    if not rows:
        return ""
    total = sum(t["monthly"] for t in got["totals"].values())
    lines = [f"**Subscription check: {len(rows)} subscriptions, about {_money(total)} a month**", ""]
    look = [r for r in rows if r["look_at"]]
    if look:
        lines.append("Worth a look:")
        lines += [f"- {r['merchant']}: {_money(r['amount'], r['currency'])} {r['cadence']} ({'; '.join(r['look_at'])})"
                  for r in look[:8]]
        lines.append("")
    lines.append("All of them:")
    lines += [f"- {r['merchant']}: {_money(r['amount'], r['currency'])} {r['cadence']}, "
              f"{_money(r['yearly_cost'], r['currency'])} a year" for r in rows[:25]]
    lines += ["", "Say \"cancel\" and the name, and I'll do it after your yes."]
    spoken = (f"Your monthly subscription check: {len(rows)} subscriptions, about {_money(total)} a month"
              + (f", and {len(look)} worth a look" if look else "") + ". The list is on screen.")
    return f"{spoken} || " + "\n".join(lines)


def review_due(memory: Memory, now: datetime | None = None) -> bool:
    now = now or local_now()
    return now.hour >= 9 and memory.get(REVIEW_KEY) != now.strftime("%Y-%m")


def mark_reviewed(memory: Memory, now: datetime | None = None) -> None:
    memory.set(REVIEW_KEY, (now or local_now()).strftime("%Y-%m"))


# --- 3: the routes ------------------------------------------------------------------------------------------
def email_text(sub: dict, name: str, account_email: str = "") -> tuple[str, str]:
    account = f" The account is under {account_email}." if account_email else ""
    body = (f"Hello,\n\nPlease cancel my {sub['merchant']} subscription, effective immediately, and stop all "
            f"future charges.{account} My last payment was {sub['amount']:.2f} {sub['currency']} on "
            f"{sub['last_charge']}.\n\nPlease reply to confirm the cancellation and the date it takes effect.\n\n"
            f"Thank you,\n{name}")
    return f"Cancel my {sub['merchant']} subscription", body


SMTP_TIMEOUT = 12


def build_email(account, to: str, subject: str, body: str) -> EmailMessage:
    message = EmailMessage()
    message["From"], message["To"], message["Subject"] = account.address, to, subject
    message["Date"], message["Message-ID"] = formatdate(localtime=True), make_msgid()
    message.set_content(body)
    return message


def _connect_smtp(host: str, port: int):
    if port == 465:
        return smtplib.SMTP_SSL(host, port, timeout=SMTP_TIMEOUT, context=ssl.create_default_context())
    server = smtplib.SMTP(host, port, timeout=SMTP_TIMEOUT)
    server.starttls(context=ssl.create_default_context())
    return server


def send_email(account, to: str, subject: str, body: str, smtp=None) -> EmailMessage:
    """Send one plain email from the owner's own account, trying the account's sending port and then the other
    standard one (many cloud hosts block one or both). Raises the last error. `smtp`: a factory for tests."""
    message = build_email(account, to, subject, body)
    last: Exception | None = None
    for port in dict.fromkeys((account.smtp_port, 465, 587)):
        try:
            server = (smtp or _connect_smtp)(account.smtp_host, port)
        except OSError as e:  # refused, unreachable or timed out: this port is closed from here
            last = e
            log.info("smtp %s:%s unreachable: %s", account.smtp_host, port, type(e).__name__)
            continue
        try:
            server.login(account.address, account.password)
            server.send_message(message)
            return message
        finally:
            try:
                server.quit()
            except Exception:  # the message has gone (or failed) already; a bad goodbye changes nothing
                log.debug("smtp quit failed", exc_info=True)
    raise last or OSError("no sending port could be reached")


def save_draft(account, message: EmailMessage, connect=None) -> str:
    """Put the message in the account's Drafts folder over IMAP (the reading connection, which works where
    sending ports are blocked). Returns the folder's name."""
    import imaplib  # noqa: PLC0415
    import time  # noqa: PLC0415
    imap = connect(account) if connect else imaplib.IMAP4_SSL(account.imap_host, timeout=30)
    try:
        if not connect:
            imap.login(account.address, account.password)
        folders = ['"[Gmail]/Drafts"', "Drafts"] if getattr(account, "is_gmail", False) else ["Drafts", '"Draft"']
        for folder in folders:
            ok, _ = imap.append(folder, "(\\Draft)", imaplib.Time2Internaldate(time.time()), message.as_bytes())
            if ok == "OK":
                return folder.strip('"')
        raise OSError("the Drafts folder wasn't found")
    finally:
        try:
            imap.logout()
        except Exception:
            log.debug("imap logout failed", exc_info=True)


def call_goal(sub: dict, account_email: str = "", accept: str = "") -> str:
    """The phone agent's goal for one cancellation (kept short: it is read into the call's instructions)."""
    goal = (f"Cancel the {sub['merchant']} subscription ({sub['amount']:.2f} {sub['currency']} {sub['cadence']}), "
            "effective immediately, with no further charges.")
    if account_email:
        goal += f" The account's email address is {account_email}."
    goal += (f" An offer to stay may be accepted only if: {accept[:120]}." if accept else
             " Turn down every offer to stay, pause or downgrade.")
    return goal


def _job(memory: Memory, sub: dict, route: str, target: str, note: str, call_id: int | None = None,
         today: date | None = None) -> int:
    _ready(memory)
    cur = memory.db.execute(
        "INSERT INTO fin_cancel_jobs (subscription_id, merchant, route, target, started, call_id, note, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (sub["id"], sub["merchant"], route, target, (today or local_now().date()).isoformat(), call_id, note,
         datetime.now(timezone.utc).isoformat()))
    memory.db.commit()
    return cur.lastrowid


def _set(memory: Memory, job_id: int, **fields) -> None:
    fields["updated_at"] = datetime.now(timezone.utc).isoformat()
    memory.db.execute(f"UPDATE fin_cancel_jobs SET {', '.join(k + ' = ?' for k in fields)} WHERE id = ?",  # nosec B608: fixed column names
                      (*fields.values(), job_id))
    memory.db.commit()


def running(memory: Memory, sub_id: int) -> dict | None:
    _ready(memory)
    row = memory.db.execute("SELECT * FROM fin_cancel_jobs WHERE subscription_id = ? AND status = 'waiting' "
                            "ORDER BY id DESC LIMIT 1", (sub_id,)).fetchone()
    return dict(row) if row else None


async def start(memory: Memory, thrift, args: dict, open_links=None, env=None, smtp=None, draft=None) -> str:
    """Begin one cancellation: pick the route, ask the owner, and on their yes do it."""
    import asyncio  # noqa: PLC0415

    from .config import settings  # noqa: PLC0415
    _ready(memory)
    fin = _fin(memory)
    sub = fin._find(args.get("what") or "")
    to, number = str(args.get("to") or "").strip(), str(args.get("number") or "").strip()
    account_email = str(args.get("account_email") or "").strip()
    route = str(args.get("route") or "") or ("phone" if number else "email" if to else "guided")
    if route not in ROUTES:
        raise ToolError("The route is email, phone or guided.")
    price = f"{_money(sub['amount'], sub['currency'])} {sub['cadence']}"
    already = running(memory, sub["id"])
    if already and (local_now().date() - date.fromisoformat(already["started"])).days < NUDGE_DAYS and \
            not args.get("again"):
        return (f"I already asked to cancel {sub['merchant']} by {already['route']} on {already['started']} and am "
                "waiting for it to be confirmed. Say so if you want another route tried now (again: true).")

    if route == "guided":
        from .cancelling import Cancelling  # noqa: PLC0415
        guide = Cancelling(memory).guide(sub["id"])
        if guide["url"] and open_links is not None:
            await open_links([{"app": "", "url": guide["url"], "label": f"Cancel {guide['merchant']}"}])
            guide["opened"] = "the cancel page is open on the phone"
        _job(memory, sub, "guided", guide["url"], "walked through on the phone; the owner taps the last button")
        return json.dumps({**guide, "watching": "I'll watch for the confirmation email and check the charges stop."},
                          ensure_ascii=False)
    if thrift is None:
        return "I need your yes before cancelling anything, and I can't ask right now."

    if route == "email":
        from .email_accounts import email_accounts, pick_account  # noqa: PLC0415
        if not ADDRESS.match(to):
            raise ToolError("I need the service's own cancellation or support address (to). Look it up on its "
                            "official help page first; don't guess one.")
        try:
            account = pick_account(email_accounts(env), str(args.get("from_account") or ""))
        except LookupError as e:
            raise ToolError(f"{e} Without one I can write the email for you to send (finance cancel_email).") from e
        subject, body = email_text(sub, settings.user_name, account_email or account.address)

        async def send() -> str:
            try:
                await asyncio.to_thread(send_email, account, to, subject, body, smtp)
            except smtplib.SMTPAuthenticationError:
                return (f"{account.address} refused the sign-in for sending, so nothing was sent or cancelled. The "
                        "app password may have changed: sudo bash /opt/buddy/deploy/email.sh")
            except Exception as e:
                blocked = isinstance(e, OSError) and not isinstance(e, smtplib.SMTPException)
                log.warning("cancellation email failed: %s: %s", type(e).__name__, str(e)[:120])
                why = ("this server can't reach the mail-sending ports (cloud hosts such as DigitalOcean block "
                       "them by default)" if blocked else f"the mail server answered with an error ({type(e).__name__})")
                try:
                    folder = await asyncio.to_thread(save_draft, account, build_email(account, to, subject, body),
                                                     draft)
                except Exception as e2:
                    log.warning("saving the cancellation draft failed: %s", type(e2).__name__)
                    return (f"The email to {to} was NOT sent: {why}. I couldn't save it as a draft either. "
                            "Nothing was cancelled. I can cancel by phone instead, or read you the email to send "
                            "yourself.")
                fin.decide(sub["id"], "cancel")
                _job(memory, sub, "email", to, f"saved as a draft in {account.address} for the owner to send")
                return (f"The email to {to} was NOT sent: {why}. I saved it in {folder} in {account.address}, "
                        "ready to go: open it and tap Send, and I'll watch for their confirmation and check the "
                        "charges stop. Or say the word and I'll cancel by phone instead."
                        + (" To let me send by myself, ask DigitalOcean support to unblock SMTP for this server."
                           if blocked else ""))
            fin.decide(sub["id"], "cancel")
            _job(memory, sub, "email", to, f"cancellation email sent from {account.address}")
            return (f"Sent the cancellation email for {sub['merchant']} to {to} from {account.address}. I'll save "
                    "their confirmation when it arrives and check that the charges stop.")
        return thrift.ask_action(f"Cancel {sub['merchant']} ({price}) by email? I'll send this to {to} from "
                                 f"{account.address}:\n\n{body}", send)

    from . import calls  # noqa: PLC0415  the phone route
    missing = calls.ready(env)
    if missing:
        return missing
    if not number:
        raise ToolError("I need the service's customer-service phone number (number). Look it up on its official "
                        "help page first; don't guess one.")
    accept = str(args.get("accept_offer") or "").strip()
    call = calls.plan(memory, sub["merchant"], number, "cancellation", call_goal(sub, account_email, accept))

    async def ring() -> str:
        await asyncio.to_thread(calls.dial, memory, call)
        fin.decide(sub["id"], "cancel")
        _job(memory, sub, "phone", call["number"], "calling to cancel", call["id"])
        return (f"Calling {sub['merchant']} now to cancel. I'll tell you what they said, including the "
                "confirmation number if they give one, and then check that the charges stop.")
    shares = "your name, your callback number" + (f" and the account's email ({account_email})" if account_email else "")
    return thrift.ask_action(
        f"Call {sub['merchant']} at {call['number']} as your assistant and cancel the subscription ({price})? "
        f"I'll {('accept an offer only if: ' + accept) if accept else 'turn down offers to stay'}, ask for a "
        f"confirmation number, and may share {shares}; no card numbers, no passwords, no fees agreed, and the call "
        "isn't recorded. If they'll only cancel for you in person, I'll say so.", ring)


# --- 4: checking it stopped ---------------------------------------------------------------------------------
def _charges_after(memory: Memory, merchant: str, day: str) -> list[dict]:
    from .finance import merchant_key  # noqa: PLC0415
    key = merchant_key(merchant)
    return [dict(r) for r in memory.db.execute(
        "SELECT day, amount, currency, merchant FROM transactions WHERE day > ? AND amount > 0 ORDER BY day", (day,))
        if merchant_key(r["merchant"]) == key]


def check(memory: Memory, today: date | None = None) -> list[str]:
    """Look at every cancellation in progress; returns the updates to tell the owner (each said once)."""
    _ready(memory)
    today = today or local_now().date()
    out = []
    for j in [dict(r) for r in memory.db.execute(
            "SELECT * FROM fin_cancel_jobs WHERE status IN ('waiting', 'confirmed') ORDER BY id")]:
        started = date.fromisoformat(j["started"])
        told = j["told"]
        sub = memory.db.execute("SELECT * FROM fin_subscriptions WHERE id = ?", (j["subscription_id"],)).fetchone()
        if j["call_id"] and "call" not in told:
            call = memory.db.execute("SELECT status, outcome FROM phone_calls WHERE id = ?", (j["call_id"],)).fetchone()
            if call and call["status"] in ("done", "failed"):
                told += " call"  # the call's own report was already given when it ended: only kept here
                _set(memory, j["id"], told=told, note=(call["outcome"] or "the call didn't get through")[:500],
                     **({"status": "failed"} if call["status"] == "failed" else {}))
                if call["status"] == "failed":
                    out.append(f"The call to cancel {j['merchant']} didn't get through. Shall I try again, or "
                               "another route?")
                    continue
        proof = memory.db.execute(
            "SELECT day, subject FROM fin_cancel_proofs WHERE (subscription_id = ? OR lower(merchant) = lower(?)) AND "
            "day >= ? ORDER BY day DESC LIMIT 1", (j["subscription_id"], j["merchant"], j["started"])).fetchone()
        if proof and j["status"] == "waiting":
            j["status"] = "confirmed"
            _set(memory, j["id"], status="confirmed", note=f"confirmed in writing on {proof['day']}")
            # the proof itself was announced when its email arrived (cancelling.py)
        charges = _charges_after(memory, j["merchant"], (started + timedelta(days=GRACE_DAYS)).isoformat())
        if charges:
            last = charges[-1]
            _set(memory, j["id"], status="still_charging",
                 note=f"charged {last['amount']:.2f} {last['currency']} on {last['day']}, after the cancellation")
            out.append(f"{j['merchant']} charged you {_money(last['amount'], last['currency'])} on {last['day']}, "
                       f"after I cancelled it on {j['started']}. Shall I write the dispute letter for your card "
                       "issuer, or cancel again another way?")
            continue
        due = date.fromisoformat(sub["next_charge"]) if sub else None
        if due and due <= started:  # the charge was already overdue when it was cancelled: give it a full month
            due = started + timedelta(days=35)
        if due and due + timedelta(days=5) < today:
            _set(memory, j["id"], status="stopped", note="the next charge didn't come")
            out.append(f"{j['merchant']} is cancelled for good: its next charge was due {due.isoformat()} and "
                       "didn't come.")
            continue
        if j["status"] == "waiting" and "nudge" not in told and (today - started).days >= NUDGE_DAYS:
            _set(memory, j["id"], told=told + " nudge")
            out.append(f"No confirmation from {j['merchant']} yet, {(today - started).days} days after I asked to "
                       f"cancel by {j['route']}. Shall I follow up, or try {'a call' if j['route'] != 'phone' else 'email'}?")
    return out


def status(memory: Memory) -> dict:
    _ready(memory)
    rows = [dict(r) for r in memory.db.execute("SELECT id, merchant, route, target, status, started, note FROM "
                                               "fin_cancel_jobs ORDER BY id DESC LIMIT 30")]
    return {"cancellations": rows,
            "meaning": {"waiting": "asked, not confirmed yet", "confirmed": "confirmed in writing; watching the charges",
                        "stopped": "the next charge didn't come: done", "still_charging": "charged after cancelling",
                        "failed": "the call or email didn't get through"}}


TOOL = {
    "name": "cancel_subscription",
    "description":
        "The subscription cancel service. review: every subscription with its cost and what changed (new, price "
        "up, on two cards, cancel pending), for the monthly check or 'what am I paying for'. start (what; route "
        "email | phone | guided; to = the service's cancellation address for email; number = its customer-service "
        "number for phone; account_email = the address the account is under; accept_offer = an offer the owner "
        "would stay for, else offers are turned down; from_account = which of the owner's email accounts sends): "
        "cancels ONE subscription after the owner's yes (the tool asks; never say it's done before it is). Before "
        "start, find the service's official cancellation address or phone number on its own help page with a web "
        "search; never guess one. Prefer email when the service accepts it, phone when it requires a call, guided "
        "when it can only be cancelled inside the signed-in account (the page opens and the owner taps the last "
        "button). To cancel several, call start once per subscription. status: cancellations in progress and how "
        "each ended. check: look now for confirmations and charges after cancelling. Buddy never signs in to the "
        "owner's accounts and never gives card numbers or passwords.",
    "input_schema": {"type": "object", "properties": {
        "action": {"type": "string", "enum": ["review", "start", "status", "check"]},
        "what": {"type": "string", "description": "the subscription: its name or id"},
        "route": {"type": "string", "enum": list(ROUTES)},
        "to": {"type": "string"}, "number": {"type": "string"}, "account_email": {"type": "string"},
        "accept_offer": {"type": "string"}, "from_account": {"type": "string"},
        "again": {"type": "boolean", "description": "try again although one is already waiting"}},
        "required": ["action"], "additionalProperties": False},
}


async def run_tool(memory: Memory, args: dict, thrift=None, open_links=None) -> str:
    import asyncio  # noqa: PLC0415
    action = args.get("action")
    if action == "start":
        return await start(memory, thrift, args, open_links)
    if action == "status":
        return json.dumps(status(memory), ensure_ascii=False)
    if action == "check":
        return json.dumps({"updates": await asyncio.to_thread(check, memory), **status(memory)}, ensure_ascii=False)
    await asyncio.to_thread(_fin(memory).find_subscriptions)
    got = review(memory)
    if not got["subscriptions"]:
        return ("No subscriptions found yet. I find them in card and bank charges: connect a bank or share a few "
                "months of card statements.")
    return json.dumps(got, ensure_ascii=False)
