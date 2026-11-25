"""The email map: what kinds of email arrive, from whom, and what Buddy does with each kind, as the owner decides.

  The scan     the last 90 days of every regular account (not the deals inbox), read-only and headers only: who
               sent each message, its subject and its date, and whether it is bulk mail. No message is opened,
               nothing in the mailbox changes, and no AI is involved: rules on this server put each sender in a
               category. The owner starts it from the Email page; after that, each night adds the new mail's senders.
  What is kept per sender (mail_senders, in the encrypted database): address, name, account, category, how many
               emails, how many were bulk, the last date, and up to three recent subjects, for bulk mail only and
               never for health or people: a person's email and health mail keep no subject.
  Categories   security and sign-in, card offers, payments to people, bank and card alerts, bills, orders and
               deliveries, travel, health, deals and promotions, newsletters, social and app notices, people, not sure.
  The choices  (mail_choices) for a category, or for one sender in it, which wins over its category:
                 importance  tell me now, normal, low, or ignore (low, and never told). The email manager uses it
                             for that sender's mail in place of its own judgment; a rule the owner wrote still wins.
                 uses        what Buddy takes from the mail, switched per category or sender: card offers, deals and
                             prices, payments to people (paymail.py), keep in the inbox (the organizer leaves it).
                 category    a sender put in another category by the owner.
"""

import email
import email.policy
import imaplib
import json
import logging
import re
from datetime import date, datetime, timedelta, timezone
from email.utils import parseaddr, parsedate_to_datetime

from .memory import Memory
from .tools import ToolError, local_now

log = logging.getLogger(__name__)

DAYS, MAX_SCAN, BATCH, SUBJECTS = 90, 6000, 200, 3
SCANNED_KEY, STATUS_KEY, LAST_KEY, DAY_KEY = "mailmap:scanned_at", "mailmap:status", "mailmap:last_uid", "mailmap:day"
IMPORTANCE = {"urgent": "Tell me now", "normal": "Normal", "low": "Low", "ignore": "Ignore"}
USES = {"offers": "Card offers onto the Money page", "deals": "Deals and prices onto the Deals page",
        "payments": "Payments to people onto the Money page", "keep": "Always keep in the inbox"}
# What each use is when nobody has said: on for the readers that already run, off for "keep".
USE_DEFAULT = {"offers": True, "deals": True, "payments": True, "keep": False}

# (key, title, what Buddy does with it today, the uses that can be switched)
CATEGORIES = [
    ("security", "Security and sign-in", "Kept in the inbox; never moved by the organizer.", ()),
    ("card_offers", "Card offers", "The offers are read into Offers on your cards, on the Money page.", ("offers", "keep")),
    ("payments", "Payments to people", "Venmo, Zelle, PayPal and Cash App payments are listed on the Money page.",
     ("payments", "keep")),
    ("bank", "Bank and card alerts", "The email manager reads due dates, bills and big charges from them.", ("keep",)),
    ("bills", "Bills", "Bills become reminders until a payment email marks them paid.", ("keep",)),
    ("orders", "Orders and deliveries", "Used for your purchases and delivery notices where Buddy recognises them.",
     ("keep",)),
    ("travel", "Travel", "Bookings and flight messages feed your trips and flight alerts.", ("keep",)),
    ("health", "Health", "Appointments only (who, when, where). No AI reads health email.", ("keep",)),
    ("deals", "Deals and promotions", "Deals go into the price book and the Deals page.", ("deals", "keep")),
    ("newsletters", "Newsletters", "Nothing is taken from them. The organizer can move them to their own folder.",
     ("keep",)),
    ("social", "Social and app notices", "Nothing is taken from them. The organizer can move them to their own folder.",
     ("keep",)),
    ("people", "People", "Remembered as life memory (who, when, what). Never moved.", ()),
    ("other", "Not sure", "Buddy does nothing special with these.", ("deals", "keep")),
]
TITLES = {k: t for k, t, *_ in CATEGORIES}
NO_SUBJECTS = ("health", "people")

SCHEMA = """
CREATE TABLE IF NOT EXISTS mail_senders (
    address TEXT PRIMARY KEY, name TEXT NOT NULL DEFAULT '', domain TEXT NOT NULL DEFAULT '',
    account TEXT NOT NULL DEFAULT '', category TEXT NOT NULL DEFAULT 'other', emails INTEGER NOT NULL DEFAULT 0,
    bulk INTEGER NOT NULL DEFAULT 0, last_at TEXT NOT NULL DEFAULT '', subjects TEXT NOT NULL DEFAULT '[]',
    first_seen TEXT NOT NULL DEFAULT '', is_new INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS mail_choices (
    target TEXT PRIMARY KEY, importance TEXT NOT NULL DEFAULT '', uses TEXT NOT NULL DEFAULT '{}',
    category TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL DEFAULT '');
"""

# --- the rules that put a sender in a category (no AI) -------------------------------------------------------------
SECURITY = re.compile(r"\b(security (alert|code|notice|warning)|sign[- ]?in|log[- ]?in (attempt|alert|code)|"
                      r"verification code|verify your|one[- ]time (pass)?code|passcode|password (reset|changed|change)|"
                      r"new device|2-step|two-factor|suspicious)\b", re.I)
PAY_DOMAINS = {"venmo.com": "Venmo", "paypal.com": "PayPal", "cash.app": "Cash App", "square.com": "Cash App",
               "zellepay.com": "Zelle"}
ZELLE = re.compile(r"\bzelle\b", re.I)
BANKS = re.compile(r"(americanexpress|aexp|\bamex\b|chase|citi(bank|cards)?\b|capitalone|discover|bankofamerica|"
                   r"wellsfargo|usbank|barclays|\bally\b|synchrony|sofi|schwab|fidelity|vanguard|robinhood|marcus|"
                   r"\bpnc\b|tdbank|santander|navyfederal|apple ?card|goldman)", re.I)
OFFER = re.compile(r"\boffers?\b", re.I)
HEALTH = re.compile(r"(cvs|walgreens|rite ?aid|mychart|labcorp|quest ?diagnostics|zocdoc|one ?medical|kaiser|aetna|"
                    r"cigna|unitedhealth|uhc|humana|bcbs|blue ?cross|anthem|pharmacy|dental|dentist|clinic|hospital|"
                    r"medical|patient|doctor|\bdr\.? |health ?care|healthcare)", re.I)
TRAVEL = re.compile(r"(delta|united|\baa\.com|americanairlines|jetblue|southwest|alaskaair|spirit|frontier|"
                    r"aircanada|lufthansa|emirates|qatar|marriott|hilton|hyatt|\bihg\b|wyndham|expedia|booking\.com|"
                    r"airbnb|vrbo|kayak|hopper|priceline|tripit|amtrak|hotels\.com|agoda|trip\.com)", re.I)
TRAVEL_SUBJECT = re.compile(r"\b(itinerary|boarding pass|your flight|flight (status|update|change)|check[- ]in|"
                            r"your trip|reservation (confirmed|confirmation)|booking confirm\w*)\b", re.I)
ORDERS = re.compile(r"(amazon|\bups\b|fedex|usps|\bdhl\b|ebay|instacart|doordash|ubereats|grubhub)", re.I)
ORDER_SUBJECT = re.compile(r"\b(your order|order (confirmed|confirmation|#|number)|has shipped|shipped|delivered|"
                           r"out for delivery|tracking|your package|return (started|received|label))\b", re.I)
BILLERS = re.compile(r"(verizon|\bat&?t\b|\batt\.com|t-?mobile|comcast|xfinity|spectrum|optimum|pseg|coned|"
                     r"national ?grid|duke ?energy|geico|progressive|state ?farm|allstate|liberty ?mutual|"
                     r"water|electric|utility|insurance)", re.I)
BILL_SUBJECT = re.compile(r"\b(your (bill|statement)|bill is (ready|available|due)|statement is (ready|available)|"
                          r"payment (due|reminder|received|scheduled)|autopay|amount due|invoice)\b", re.I)
SOCIAL = re.compile(r"(facebook|facebookmail|instagram|linkedin|twitter|\bx\.com|tiktok|nextdoor|reddit|quora|"
                    r"youtube|pinterest|whatsapp|snapchat|discord|meetup|strava)", re.I)
CONSUMER = re.compile(r"(^|\.)(gmail|googlemail|yahoo|ymail|outlook|hotmail|live|msn|icloud|me|mac|aol|proton|"
                      r"protonmail|pm|zoho|gmx|mail|rediffmail)\.\w+$", re.I)
ROBOT = re.compile(r"(no-?reply|do-?not-?reply|notifications?|alerts?|mailer|support|service|info|team|news|"
                   r"updates?|hello|account|billing|orders?|receipts?)", re.I)


def category_of(address: str, name: str, subject: str, bulk: bool) -> str:
    """Which category a message puts its sender in, by rules on the sender and the subject."""
    from . import mailorganizer  # noqa: PLC0415
    address, name, subject = (address or "").lower(), name or "", subject or ""
    domain = address.rsplit("@", 1)[-1]
    who = f"{name} {address}"
    if any(domain == d or domain.endswith("." + d) for d in PAY_DOMAINS) or (BANKS.search(who) and ZELLE.search(subject)):
        return "payments"
    if SECURITY.search(subject):
        return "security"
    if BANKS.search(who):
        return "card_offers" if bulk and (OFFER.search(subject) or OFFER.search(name)) else "bank"
    if HEALTH.search(who):
        return "health"
    if TRAVEL.search(who) or TRAVEL_SUBJECT.search(subject):
        return "travel"
    if ORDERS.search(who) or ORDER_SUBJECT.search(subject):
        return "orders"
    if BILLERS.search(who) or BILL_SUBJECT.search(subject):
        return "bills"
    if SOCIAL.search(who):
        return "social"
    if bulk:
        headers = email.message.EmailMessage()
        headers["From"], headers["Subject"], headers["List-Unsubscribe"] = f"{name} <{address}>", subject, "<x>"
        where = mailorganizer.judge(headers)[0]
        return {"promotions": "deals", "newsletters": "newsletters", "notifications": "social"}.get(where, "other")
    if CONSUMER.search(domain) or not ROBOT.search(address.split("@")[0]):
        return "people"
    return "other"


# --- the store --------------------------------------------------------------------------------------------------
def _ready(memory: Memory) -> None:
    memory.db.executescript(SCHEMA)


def _address(sender: str) -> str:
    return (parseaddr(str(sender or ""))[1] or str(sender or "")).strip().lower()


def choices(memory: Memory) -> dict:
    _ready(memory)
    out = {}
    for r in memory.db.execute("SELECT target, importance, uses, category FROM mail_choices"):
        try:
            uses = json.loads(r["uses"] or "{}")
        except ValueError:
            uses = {}
        out[r["target"]] = {"importance": r["importance"], "uses": uses, "category": r["category"]}
    return out


def _sender_row(memory: Memory, address: str):
    _ready(memory)
    return memory.db.execute("SELECT * FROM mail_senders WHERE address = ?", (address,)).fetchone()


def effective(memory: Memory, sender: str, mine: dict | None = None) -> dict:
    """What holds for one sender: its category, importance and uses, the sender's own choice over its category's."""
    address = _address(sender)
    mine = choices(memory) if mine is None else mine
    own = mine.get(f"sender:{address}", {})
    row = _sender_row(memory, address)
    category = own.get("category") or (row["category"] if row else "")
    group = mine.get(f"category:{category}", {}) if category else {}
    uses = {k: v for k, v in {**group.get("uses", {}), **own.get("uses", {})}.items() if k in USES}
    return {"address": address, "category": category, "importance": own.get("importance") or group.get("importance") or "",
            "uses": uses}


def importance(memory: Memory, sender: str) -> str:
    """'' (Buddy decides), or 'urgent', 'normal', 'low', 'ignore': what the owner chose for mail from this sender."""
    try:
        return effective(memory, sender)["importance"]
    except Exception:
        return ""


def use_on(memory: Memory, sender: str, use: str) -> bool:
    """Is this use on for mail from this sender? The default holds until the owner switches it."""
    try:
        return bool(effective(memory, sender)["uses"].get(use, USE_DEFAULT.get(use, False)))
    except Exception:
        return USE_DEFAULT.get(use, False)


def choose(memory: Memory, target: str, importance: str | None = None, use: str | None = None,
           on: bool | None = None, category: str | None = None) -> dict:
    """The owner's choice for 'category:<key>' or 'sender:<address>'. Returns what now holds for that target."""
    _ready(memory)
    kind, _, key = str(target or "").partition(":")
    key = key.strip().lower()
    if kind == "category" and key not in TITLES:
        raise ToolError("Which category? " + ", ".join(TITLES))
    if kind == "sender" and not re.fullmatch(r"[^@\s]+@[\w.-]+\.\w+", key):
        raise ToolError("Which sender? Give the email address.")
    if kind not in ("category", "sender"):
        raise ToolError("Choose for a category or a sender.")
    if importance is not None and importance not in ("", *IMPORTANCE):
        raise ToolError("Importance is one of: " + ", ".join(IMPORTANCE) + " (or nothing, to let Buddy decide).")
    if use is not None and use not in USES:
        raise ToolError("Use is one of: " + ", ".join(USES))
    if category is not None and category not in ("", *TITLES):
        raise ToolError("Which category? " + ", ".join(TITLES))
    target = f"{kind}:{key}"
    now = choices(memory).get(target, {"importance": "", "uses": {}, "category": ""})
    if importance is not None:
        now["importance"] = importance
    if use is not None and on is not None:
        now["uses"][use] = bool(on)
    if category is not None and kind == "sender":
        now["category"] = category
    memory.db.execute("INSERT OR REPLACE INTO mail_choices (target, importance, uses, category, updated_at) "
                      "VALUES (?, ?, ?, ?, ?)", (target, now["importance"], json.dumps(now["uses"]), now["category"],
                                                 datetime.now(timezone.utc).isoformat()))
    memory.db.commit()
    if use == "keep":
        _teach_keep(memory, kind, key, bool(on))
    if kind == "sender":
        memory.db.execute("UPDATE mail_senders SET is_new = 0 WHERE address = ?", (key,))
        memory.db.commit()
    return {"target": target, **now}


def _teach_keep(memory: Memory, kind: str, key: str, on: bool) -> None:
    """Keep in the inbox: the organizer's own list of senders it leaves alone."""
    from . import mailorganizer  # noqa: PLC0415
    addresses = [key] if kind == "sender" else [r["address"] for r in memory.db.execute(
        "SELECT address FROM mail_senders WHERE category = ?", (key,))]
    for address in addresses:
        try:
            mailorganizer.teach(memory, address, "keep" if on else "forget")
        except ToolError:
            continue


# --- the scan ---------------------------------------------------------------------------------------------------
def accounts(env=None) -> list:
    """The owner's regular accounts (the deals inbox is not part of the map)."""
    import os  # noqa: PLC0415

    from .email_accounts import email_accounts  # noqa: PLC0415
    env = os.environ if env is None else env
    deals = str(env.get("BUDDY_DEALS_EMAIL_ADDRESS") or "").strip().lower()
    return [a for a in email_accounts(env) if a.address.lower() != deals]


def _headers(imap, uids: list[str]) -> list:
    from .mailorganizer import HEADERS  # noqa: PLC0415
    out = []
    for i in range(0, len(uids), BATCH):
        _, parts = imap.uid("fetch", ",".join(uids[i:i + BATCH]), HEADERS)
        for p in parts:
            if isinstance(p, tuple):
                out.append(email.message_from_bytes(p[1], policy=email.policy.default))
    return out


def _when(headers) -> str:
    try:
        return parsedate_to_datetime(str(headers.get("Date", ""))).astimezone(timezone.utc).isoformat()
    except (TypeError, ValueError, IndexError):
        return ""


def _tally(found: dict, headers, account: str) -> None:
    name, address = parseaddr(str(headers.get("From", "")))
    address = address.strip().lower()
    if "@" not in address:
        return
    subject = " ".join(str(headers.get("Subject", "")).split())[:140]
    bulk = bool(headers.get("List-Unsubscribe")) or str(headers.get("Precedence", "")).strip().lower() in (
        "bulk", "list", "junk")
    s = found.setdefault(address, {"name": "", "account": account, "emails": 0, "bulk": 0, "last_at": "",
                                   "mail": []})
    s["emails"] += 1
    s["bulk"] += bulk
    s["name"] = s["name"] or " ".join(name.split())[:80]
    when = _when(headers)
    s["last_at"] = max(s["last_at"], when)
    s["mail"].append((when, subject, bulk))


def _save(memory: Memory, found: dict, full: bool) -> int:
    """Into mail_senders. A full scan replaces the counts; a nightly one adds to them. Returns how many are new."""
    _ready(memory)
    today = local_now().date().isoformat()
    had_scan = bool(memory.get(SCANNED_KEY))
    new = 0
    for address, s in found.items():
        mail = sorted(s["mail"], reverse=True)
        bulk = s["bulk"] * 2 >= s["emails"]
        latest = mail[0][1] if mail else ""
        category = category_of(address, s["name"], latest, bulk)
        row = memory.db.execute("SELECT * FROM mail_senders WHERE address = ?", (address,)).fetchone()
        keep = [] if category in NO_SUBJECTS else [sub for _, sub, b in mail if b and sub][:SUBJECTS]
        if row and not full:
            old = json.loads(row["subjects"] or "[]")
            keep = list(dict.fromkeys(keep + old))[:SUBJECTS] if category not in NO_SUBJECTS else []
            memory.db.execute(
                "UPDATE mail_senders SET emails = emails + ?, bulk = bulk + ?, last_at = MAX(last_at, ?), subjects = ?, "
                "name = CASE WHEN name = '' THEN ? ELSE name END WHERE address = ?",
                (s["emails"], s["bulk"], s["last_at"], json.dumps(keep), s["name"], address))
            continue
        is_new = int(not row and had_scan and not full)
        new += is_new
        memory.db.execute(
            "INSERT OR REPLACE INTO mail_senders (address, name, domain, account, category, emails, bulk, last_at, "
            "subjects, first_seen, is_new) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (address, s["name"], address.rsplit("@", 1)[-1], s["account"], category, s["emails"], s["bulk"],
             s["last_at"], json.dumps(keep), row["first_seen"] if row else today, row["is_new"] if row else is_new))
    memory.db.commit()
    return new


def scan(memory: Memory, env=None, connect=None, days: int = DAYS, today: date | None = None) -> dict:
    """The full scan of the last `days` days, headers only and read-only. Returns {"senders", "emails", "accounts",
    "failed"}."""
    from . import mailorganizer  # noqa: PLC0415
    mine = accounts(env)
    if not mine:
        raise ToolError("No email account is connected: sudo bash /opt/buddy/deploy/set.sh BUDDY_EMAIL_ADDRESS")
    memory.set(STATUS_KEY, json.dumps({"state": "scanning", "at": datetime.now(timezone.utc).isoformat()}))
    since = (today or local_now().date()) - timedelta(days=days)
    found, looked, failed = {}, 0, []
    for account in mine:
        try:
            imap = (connect or mailorganizer._connect)(account)
        except (imaplib.IMAP4.error, OSError) as e:
            failed.append(f"{account.address}: {str(e)[:80]}")
            continue
        try:
            imap.select(account.folder, readonly=True)
            _, got = imap.uid("search", None, f"(SINCE {since:%d-%b-%Y})")
            uids = [u.decode() for u in (got[0] or b"").split()][-MAX_SCAN:]
            for headers in _headers(imap, uids):
                _tally(found, headers, account.address)
            looked += len(uids)
            if uids:
                memory.set(f"{LAST_KEY}:{account.address.lower()}", str(max(int(u) for u in uids)))
        except (imaplib.IMAP4.error, OSError) as e:
            failed.append(f"{account.address}: {str(e)[:80]}")
        finally:
            mailorganizer._logout(imap)
    _save(memory, found, full=True)
    memory.set(SCANNED_KEY, datetime.now(timezone.utc).isoformat())
    out = {"senders": len(found), "emails": looked, "accounts": len(mine) - len(failed), "failed": failed}
    memory.set(STATUS_KEY, json.dumps({"state": "done", "at": datetime.now(timezone.utc).isoformat(), **out}))
    return out


def due(memory: Memory, now: datetime | None = None) -> bool:
    """Once a night (1 to 6 in the morning), and only after the owner ran the first scan."""
    now = now or local_now()
    return bool(memory.get(SCANNED_KEY)) and 1 <= now.hour < 6 and memory.get(DAY_KEY) != now.date().isoformat()


def nightly(memory: Memory, env=None, connect=None) -> str:
    """The new mail since the last look, headers only: counts updated, senders not seen before added as new.
    Nothing happens until the owner has run the first scan."""
    from . import mailorganizer  # noqa: PLC0415
    if not memory.get(SCANNED_KEY):
        return ""
    found, looked = {}, 0
    for account in accounts(env):
        key = f"{LAST_KEY}:{account.address.lower()}"
        try:
            imap = (connect or mailorganizer._connect)(account)
        except (imaplib.IMAP4.error, OSError):
            continue
        try:
            imap.select(account.folder, readonly=True)
            last = int(memory.get(key) or 0)
            _, got = imap.uid("search", None, f"UID {last + 1}:*")
            uids = [u.decode() for u in (got[0] or b"").split() if int(u) > last][-MAX_SCAN:]
            for headers in _headers(imap, uids):
                _tally(found, headers, account.address)
            looked += len(uids)
            if uids:
                memory.set(key, str(max(int(u) for u in uids)))
        except (imaplib.IMAP4.error, OSError):
            continue
        finally:
            mailorganizer._logout(imap)
    new = _save(memory, found, full=False)
    return f"email map: {looked} new emails, {new} new senders" if looked else ""


# --- the page ---------------------------------------------------------------------------------------------------
def view(memory: Memory, senders_each: int = 40) -> dict:
    """The Email page: each category with its senders, the owner's choices and what Buddy does with it."""
    _ready(memory)
    mine = choices(memory)
    rows = [dict(r) for r in memory.db.execute("SELECT * FROM mail_senders ORDER BY emails DESC, address")]
    groups: dict[str, list] = {}
    for r in rows:
        own = mine.get(f"sender:{r['address']}", {})
        r["category"] = own.get("category") or r["category"]
        r["subjects"] = json.loads(r["subjects"] or "[]")
        r["importance"] = own.get("importance", "")
        r["is_new"] = bool(r["is_new"])
        groups.setdefault(r["category"], []).append(r)
    out = []
    for key, title, does, uses in CATEGORIES:
        senders = groups.get(key, [])
        if not senders:
            continue
        group = mine.get(f"category:{key}", {})
        out.append({"key": key, "title": title, "does": does, "importance": group.get("importance", ""),
                    "uses": [{"key": u, "title": USES[u], "on": bool(group.get("uses", {}).get(u, USE_DEFAULT[u]))}
                             for u in uses],
                    "senders_count": len(senders), "emails": sum(s["emails"] for s in senders),
                    "senders": [{k: s[k] for k in ("address", "name", "emails", "last_at", "subjects", "importance",
                                                   "is_new")} for s in senders[:senders_each]]})
    try:
        status = json.loads(memory.get(STATUS_KEY) or "{}")
    except ValueError:
        status = {}
    return {"scanned_at": memory.get(SCANNED_KEY) or "", "days": DAYS, "status": status, "categories": out,
            "new": [{"address": r["address"], "name": r["name"], "category": r["category"],
                     "title": TITLES.get(r["category"], "")} for r in rows if r["is_new"]][:30],
            "importance": IMPORTANCE, "category_titles": TITLES}


def forget(memory: Memory) -> str:
    """Delete the map and the choices (the email itself is untouched)."""
    _ready(memory)
    memory.db.execute("DELETE FROM mail_senders")
    memory.db.execute("DELETE FROM mail_choices")
    memory.db.commit()
    for key in (SCANNED_KEY, STATUS_KEY):
        memory.set(key, "")
    return "The email map and your choices are deleted. Your email itself is unchanged."
