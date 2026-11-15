"""The email organizer: marketing, newsletters and app notifications are moved out of the inbox into their own
folders, so what is left in the inbox is real email: from people, and the automated mail that matters.

How a message is judged (from its headers only: the body is never read, and no AI is involved):
  stays in the inbox, always
    - anything that is not bulk mail: no unsubscribe header and not marked bulk. A person's email has neither,
      so a personal email can never be moved, whatever its subject says
    - bulk mail that matters, by its subject: security and sign-in alerts, codes, bills, payments, statements,
      receipts, orders, deliveries, bookings, tickets, travel changes, appointments, taxes, job offers
    - any sender the owner said to keep
  is moved, when it is bulk mail and none of the above
    Buddy Promotions      sales, offers, coupons, deals
    Buddy Newsletters     newsletters, digests, roundups, blog and product updates
    Buddy Notifications   everything else automated: social networks, app activity, build and system notices

Nothing is ever deleted and nothing is marked read: a moved message sits in its folder exactly as it arrived.
Every move is written down, so "undo" puts a run's messages back in the inbox. Moving a message back yourself
(or saying "keep emails from X") teaches Buddy to leave that sender alone.

The owner is always shown a preview first (how many would move, where, with examples by sender), and Buddy moves
mail only after their yes. With "organize my email automatically" on, new mail is organized each night after the
deals reader has taken what it needs from the marketing mail. Works with any account Buddy can read over IMAP
(Gmail, Yahoo, iCloud and others); in Gmail the folders appear as labels and the mail leaves the inbox.
"""

import email
import email.policy
import imaplib
import json
import logging
import re
from datetime import date, datetime, timedelta, timezone
from email.utils import parseaddr

from .memory import Memory
from .tools import ToolError, local_now

log = logging.getLogger(__name__)

FOLDERS = {"promotions": "Buddy Promotions", "newsletters": "Buddy Newsletters",
           "notifications": "Buddy Notifications"}
MAX_PER_RUN, BATCH = 2000, 200
AUTO_KEY, DAY_KEY, KEEP_KEY, PREVIEW_KEY, STATUS_KEY = ("mailorg:auto", "mailorg:day", "mailorg:senders",
                                                        "mailorg:previewed", "mailorg:status")
HEADERS = "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT MESSAGE-ID LIST-UNSUBSCRIBE PRECEDENCE DATE)])"
IMPORTANT = re.compile(
    r"\b(security (alert|code|notice|warning|update)|new sign[- ]?in|sign[- ]?in (attempt|alert|code)|log[- ]?in (attempt|alert|code)|password|verif\w+|one[- ]time|\bcode\b|2fa|fraud|suspicious|unusual"
    r"|bill\b|invoice|payment|paid\b|statement|receipt|refund|charge[ds]?\b|past due|overdue|due (today|tomorrow|on)"
    r"|order\b|shipped|shipping|deliver\w+|tracking|out for delivery|return\b"
    r"|booking|reservation|itinerary|ticket|boarding|check[- ]?in|flight|gate change|schedule change|cancel+ed"
    r"|appointment|reminder: |tax\b|1099|w-?2\b|interview|offer letter|application|lease|renewal|expir\w+"
    r"|action required|final notice|your account)\b", re.I)
PROMO = re.compile(r"(\d{1,2}\s?% off|\bsale\b|\bdeals?\b|\boffers?\b|coupon|promo|save \$|\bfree\b|discount|clearance"
                   r"|black friday|cyber monday|limited time|last chance|ends (today|tonight|soon)|exclusive|\bbogo\b"
                   r"|new arrivals|shop now|just for you|earn \d|points|cash ?back|upgrade now|\$\d+ off)", re.I)
NEWS = re.compile(r"(newsletter|digest|roundup|weekly|monthly|this week|edition|issue #?\d|what's new|top stories"
                  r"|product update|release notes|blog|webinar|podcast|recommended for you|you might like)", re.I)
MARKETING_SENDER = re.compile(r"(^|[.@_-])(marketing|promo(tions)?|offers?|deals?|sales?|shop|store|news(letter)?|"
                              r"email|e-?mail|mailer|info|hello|team|updates?)([.@_-]|$)", re.I)
SCHEMA = """
CREATE TABLE IF NOT EXISTS mail_moves (
    id INTEGER PRIMARY KEY AUTOINCREMENT, run TEXT NOT NULL, at TEXT NOT NULL, account TEXT NOT NULL,
    message_id TEXT NOT NULL, sender TEXT NOT NULL, subject TEXT NOT NULL, folder TEXT NOT NULL,
    undone INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS mail_moves_run ON mail_moves (run);
"""


def _ready(memory: Memory) -> None:
    memory.db.executescript(SCHEMA)


# --- judging one message, from its headers --------------------------------------------------------------------
def senders(memory: Memory) -> dict:
    """{address or domain: "keep" | folder key}: what the owner taught."""
    try:
        return json.loads(memory.get(KEEP_KEY) or "{}")
    except ValueError:
        return {}


def teach(memory: Memory, who: str, what: str) -> str:
    who = (parseaddr(who)[1] or who).strip().lower().lstrip("@")
    if not re.fullmatch(r"[\w.+-]*@?[\w-]+(\.[\w-]+)+", who):
        raise ToolError("Which sender? Give the email address or the domain, like news@store.com or store.com.")
    if what not in ("keep", *FOLDERS, "forget"):
        raise ToolError(f"Say keep, or one of: {', '.join(FOLDERS)}.")
    saved = senders(memory)
    if what == "forget":
        saved.pop(who, None)
    else:
        saved[who] = what
    memory.set(KEEP_KEY, json.dumps(saved))
    return (f"I'll forget what you said about {who}." if what == "forget" else
            f"Email from {who} always stays in your inbox." if what == "keep" else
            f"Email from {who} always goes to {FOLDERS[what]}.")


def judge(headers, taught: dict | None = None) -> tuple[str, str]:
    """(where, why): where is "inbox" or a folder key. `headers`: an email.message with the header fields."""
    name, address = parseaddr(str(headers.get("From", "")))
    address, subject = address.lower(), " ".join(str(headers.get("Subject", "")).split())
    domain = address.rsplit("@", 1)[-1]
    taught = taught or {}
    said = taught.get(address) or taught.get(domain) or next(
        (v for k, v in taught.items() if "@" not in k and domain.endswith("." + k)), None)
    bulk = bool(headers.get("List-Unsubscribe")) or str(headers.get("Precedence", "")).strip().lower() in (
        "bulk", "list", "junk")
    if not bulk:
        return "inbox", "not bulk mail (a person, or a direct message)"
    if said == "keep":
        return "inbox", "you said to keep this sender"
    if said in FOLDERS:
        return said, "you said where this sender goes"
    if IMPORTANT.search(subject):
        return "inbox", "automated, but its subject says it matters"
    if PROMO.search(subject) or (MARKETING_SENDER.search(address.split("@")[0]) and not NEWS.search(subject)
                                 and PROMO.search(name + " " + subject)):
        return "promotions", "a sale or offer"
    if NEWS.search(subject) or NEWS.search(name):
        return "newsletters", "a newsletter or digest"
    return "notifications", "an automated notice"


# --- the mailbox ----------------------------------------------------------------------------------------------
def _connect(account):
    imap = imaplib.IMAP4_SSL(account.imap_host, timeout=60)
    imap.login(account.address, account.password)
    return imap


def _accounts(env=None, which: str = "") -> list:
    import os  # noqa: PLC0415

    from .email_accounts import email_accounts  # noqa: PLC0415
    all_ = email_accounts(os.environ if env is None else env)
    if which:
        all_ = [a for a in all_ if which.lower() in a.address.lower()]
    if not all_:
        raise ToolError("No email account is connected for that." if which else
                        "No email account is connected: sudo bash /opt/buddy/deploy/set.sh BUDDY_EMAIL_ADDRESS "
                        "and BUDDY_EMAIL_APP_PASSWORD")
    return all_


def scan(imap, account, days: int, taught: dict, today: date | None = None) -> list[dict]:
    """The inbox's messages of the last `days`, each judged: [{uid, message_id, sender, subject, where, why}]."""
    imap.select(account.folder, readonly=True)
    since = (today or local_now().date()) - timedelta(days=days)
    _, found = imap.uid("search", None, f"(SINCE {since:%d-%b-%Y})")
    uids = [u.decode() for u in (found[0] or b"").split()][-MAX_PER_RUN:]
    out = []
    for i in range(0, len(uids), BATCH):
        _, parts = imap.uid("fetch", ",".join(uids[i:i + BATCH]), HEADERS)
        for p in parts:
            if not isinstance(p, tuple):
                continue
            m = re.search(rb"UID (\d+)", p[0])
            head = email.message_from_bytes(p[1], policy=email.policy.default)
            where, why = judge(head, taught)
            name, address = parseaddr(str(head.get("From", "")))
            out.append({"uid": m.group(1).decode() if m else "", "message_id": str(head.get("Message-ID", "")).strip(),
                        "sender": address.lower(), "name": name or address,
                        "subject": " ".join(str(head.get("Subject", "")).split())[:160], "where": where, "why": why})
    return [o for o in out if o["uid"]]


def summary(rows: list[dict]) -> dict:
    """What a scan found, for the owner to read before saying yes."""
    out = {"looked_at": len(rows), "stay_in_inbox": sum(r["where"] == "inbox" for r in rows), "would_move": {}}
    for key, folder in FOLDERS.items():
        mine = [r for r in rows if r["where"] == key]
        by: dict[str, int] = {}
        for r in mine:
            by[r["name"]] = by.get(r["name"], 0) + 1
        out["would_move"][folder] = {"emails": len(mine),
                                     "top_senders": [f"{n} ({c})" for n, c in sorted(by.items(), key=lambda kv: -kv[1])[:6]],
                                     "examples": [f"{r['name']}: {r['subject']}" for r in mine[:3]]}
    kept = [r for r in rows if r["where"] == "inbox" and "bulk" not in r["why"]]
    out["kept_automated_because_they_matter"] = [f"{r['name']}: {r['subject']}" for r in kept[:5]]
    return out


def preview(memory: Memory, env=None, which: str = "", days: int = 30, connect=None) -> dict:
    """What organizing would do, per account. Changes nothing."""
    taught, out = senders(memory), {}
    for a in _accounts(env, which):
        imap = (connect or _connect)(a)
        try:
            out[a.address] = summary(scan(imap, a, days, taught))
        finally:
            _logout(imap)
    memory.set(PREVIEW_KEY, json.dumps({"at": datetime.now(timezone.utc).isoformat(), "which": which, "days": days}))
    return {"days": days, "accounts": out,
            "note": "Nothing was changed. Moved mail is never deleted or marked read; it goes to folders named "
                    "Buddy Promotions, Buddy Newsletters and Buddy Notifications, and 'undo' puts it back."}


def _logout(imap) -> None:
    try:
        imap.logout()
    except Exception:
        pass


def _move(imap, uids: list[str], folder: str) -> None:
    """Move messages to a folder (made if needed): MOVE where the server has it, else copy then remove."""
    imap.create(f'"{folder}"')  # an existing folder just says so
    for i in range(0, len(uids), BATCH):
        some = ",".join(uids[i:i + BATCH])
        typ, _ = imap.uid("MOVE", some, f'"{folder}"')
        if typ != "OK":
            typ, _ = imap.uid("COPY", some, f'"{folder}"')
            if typ != "OK":
                raise ToolError(f"The mail server wouldn't copy to {folder}.")
            imap.uid("STORE", some, "+FLAGS.SILENT", "(\\Deleted)")
            imap.expunge()


def organize(memory: Memory, env=None, which: str = "", days: int = 30, connect=None, run: str = "") -> dict:
    """Move the marketing, newsletters and notices of the last `days` out of each inbox. Returns what moved."""
    _ready(memory)
    run = run or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    taught, out = senders(memory), {}
    try:  # the deals reader takes what it needs from the marketing mail before it leaves the inbox
        from . import dealmail  # noqa: PLC0415
        if connect is None:
            dealmail.run_all(memory, env)
    except Exception:
        log.info("mail organizer: the deals reader failed first", exc_info=True)
    for a in _accounts(env, which):
        imap = (connect or _connect)(a)
        try:
            rows = [r for r in scan(imap, a, days, taught) if r["where"] != "inbox"]
            imap.select(a.folder, readonly=False)
            moved = {}
            for key, folder in FOLDERS.items():
                mine = [r for r in rows if r["where"] == key]
                if not mine:
                    continue
                _move(imap, [r["uid"] for r in mine], folder)
                moved[folder] = len(mine)
                now = datetime.now(timezone.utc).isoformat(timespec="seconds")
                memory.db.executemany(
                    "INSERT INTO mail_moves (run, at, account, message_id, sender, subject, folder) VALUES (?, ?, ?, "
                    "?, ?, ?, ?)", [(run, now, a.address, r["message_id"], r["sender"], r["subject"], folder)
                                    for r in mine])
                memory.db.commit()
            out[a.address] = moved or "nothing to move"
        except (imaplib.IMAP4.error, OSError) as e:
            out[a.address] = f"couldn't be organized: {str(e)[:80]}"
        finally:
            _logout(imap)
    result = {"run": run, "moved": out, "note": "Nothing was deleted or marked read. Say 'undo the email "
                                                "organizing' to put this run back."}
    memory.set(STATUS_KEY, json.dumps({"at": local_now().isoformat(timespec="minutes"), **result}))
    return result


def undo(memory: Memory, env=None, run: str = "", connect=None) -> dict:
    """Put a run's messages back in the inbox (the latest run by default), found again by their message ids."""
    _ready(memory)
    if not run:
        row = memory.db.execute("SELECT run FROM mail_moves WHERE undone = 0 ORDER BY id DESC LIMIT 1").fetchone()
        if not row:
            raise ToolError("There's nothing to undo: I haven't moved any email.")
        run = row["run"]
    rows = [dict(r) for r in memory.db.execute("SELECT * FROM mail_moves WHERE run = ? AND undone = 0", (run,))]
    if not rows:
        raise ToolError("That run was already undone.")
    by_account = {a.address: a for a in _accounts(env)}
    back, missing = 0, 0
    for address in sorted({r["account"] for r in rows}):
        a = by_account.get(address)
        if a is None:
            missing += sum(r["account"] == address for r in rows)
            continue
        imap = (connect or _connect)(a)
        try:
            for folder in sorted({r["folder"] for r in rows if r["account"] == address}):
                imap.select(f'"{folder}"', readonly=False)
                uids, done = [], []
                for r in rows:
                    if r["account"] != address or r["folder"] != folder or not r["message_id"]:
                        continue
                    _, found = imap.uid("search", None, "HEADER", "Message-ID", f'"{r["message_id"]}"')
                    got = (found[0] or b"").split()
                    if got:
                        uids.append(got[0].decode())
                        done.append(r["id"])
                    else:
                        missing += 1
                if uids:
                    _move(imap, uids, a.folder)
                    memory.db.executemany("UPDATE mail_moves SET undone = 1 WHERE id = ?", [(i,) for i in done])
                    memory.db.commit()
                    back += len(uids)
        finally:
            _logout(imap)
    return {"run": run, "put_back_in_the_inbox": back,
            **({"not_found": missing, "why": "they were moved again or deleted since"} if missing else {})}


# --- every night, once it's switched on -----------------------------------------------------------------------
def auto(memory: Memory) -> bool:
    return memory.get(AUTO_KEY) == "1"


def due(memory: Memory, now: datetime | None = None) -> bool:
    now = now or local_now()
    return auto(memory) and 1 <= now.hour < 6 and memory.get(DAY_KEY) != now.date().isoformat()


def nightly(memory: Memory, env=None, connect=None) -> dict:
    """New mail of the last two days, organized (run only when the owner turned automatic organizing on)."""
    return organize(memory, env, days=2, connect=connect)


def status(memory: Memory) -> dict:
    _ready(memory)
    counts = {r["folder"]: r["n"] for r in memory.db.execute(
        "SELECT folder, COUNT(*) AS n FROM mail_moves WHERE undone = 0 GROUP BY folder")}
    return {"automatic": auto(memory), "moved_so_far": counts, "what_you_taught_me": senders(memory),
            "last_run": json.loads(memory.get(STATUS_KEY) or "null"), "folders": list(FOLDERS.values())}


TOOL = {
    "name": "email_organizer",
    "description": (
        "Keeps the inbox for real email by moving marketing, newsletters and app notifications into their own "
        "folders (Buddy Promotions, Buddy Newsletters, Buddy Notifications), in every connected email account. "
        "Judged from headers only by code: mail that isn't bulk mail (anything from a person) never moves, and "
        "bulk mail that matters (security alerts, bills, receipts, orders, deliveries, bookings) stays too. "
        "Nothing is deleted or marked read. preview (days?, account?): what would move, with counts, top senders "
        "and examples; changes nothing. ALWAYS preview first and read the owner the counts. organize (days?, "
        "account?): move them, after the owner's yes to the preview. undo: put the last run back in the inbox. "
        "keep (sender): this address or domain always stays in the inbox. always (sender, folder: promotions, "
        "newsletters or notifications): where a sender always goes. forget (sender). auto (on): organize new "
        "mail every night. status. Use it for 'organize my email', 'clean my inbox', 'too many promotions'."),
    "input_schema": {"type": "object", "properties": {
        "action": {"type": "string", "enum": ["preview", "organize", "undo", "keep", "always", "forget", "auto",
                                              "status"]},
        "days": {"type": "integer"}, "account": {"type": "string"}, "sender": {"type": "string"},
        "folder": {"type": "string", "enum": list(FOLDERS)}, "on": {"type": "boolean"}},
        "required": ["action"], "additionalProperties": False},
}


def _recent_preview(memory: Memory) -> bool:
    try:
        at = datetime.fromisoformat(json.loads(memory.get(PREVIEW_KEY) or "{}").get("at", ""))
    except ValueError:
        return False
    return datetime.now(timezone.utc) - at <= timedelta(minutes=30)


async def run_tool(memory: Memory, args: dict, thrift=None) -> str:
    import asyncio  # noqa: PLC0415
    a = args.get("action")
    days = max(1, min(int(args.get("days") or 30), 365))
    which = args.get("account") or ""
    if a == "preview":
        return json.dumps(await asyncio.to_thread(preview, memory, None, which, days))
    if a == "status":
        return json.dumps(status(memory))
    if a in ("keep", "always", "forget"):
        return teach(memory, args.get("sender", ""), "keep" if a == "keep" else "forget" if a == "forget"
                     else args.get("folder") or "")
    if a == "organize":
        if not _recent_preview(memory):  # checked in code: the owner has to have seen what will move
            raise ToolError("Preview first: show the owner what would move, then organize after their yes.")

        async def go() -> str:
            return json.dumps(await asyncio.to_thread(organize, memory, None, which, days))
        if thrift is not None:
            return thrift.ask_action(f"Shall I move the marketing, newsletters and notices of the last {days} days "
                                     "out of your inbox into their own folders? Nothing is deleted, and you can "
                                     "undo it.", go, group="email_organizer", item=f"last {days} days",
                                     plural="inbox clean-ups")
        return await go()
    if a == "undo":
        return json.dumps(await asyncio.to_thread(undo, memory))
    if a == "auto":
        on = bool(args.get("on"))
        _ready(memory)
        if on and not memory.db.execute("SELECT 1 FROM mail_moves LIMIT 1").fetchone():
            raise ToolError("Organize once by hand first (preview, then organize), so the owner has seen how it "
                            "sorts their mail before it runs by itself.")
        memory.set(AUTO_KEY, "1" if on else "0")
        return ("New email will be organized every night." if on else "I'll only organize your email when you ask.")
    raise ToolError("Use preview, organize, undo, keep, always, forget, auto or status.")
