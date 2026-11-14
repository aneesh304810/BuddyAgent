"""The deals inbox: a separate email account the owner subscribes to deal alerts and store, airline and hotel
newsletters. Every night Buddy reads the new emails there (code only, no AI), pulls each deal out
(product, flight or hotel; price, regular price, store, link, when it ends) and keeps it in the price book, so the
deals feed the Deals page (web/deals.html) and the nightly pattern learning.

Once read, newsletters older than two days are moved to Trash so the inbox stays clean (clean(): bulk mail only,
never a personal email; BUDDY_DEALS_EMAIL_CLEAN_DAYS, 0 to keep everything).

Why a separate inbox: newsletters don't bury real mail or go through the AI email sorting (which costs money per
email), and nothing personal is in it. Setup: a new free email account (2-step verification on, an app password),
then sudo bash /opt/buddy/deploy/set.sh BUDDY_DEALS_EMAIL_ADDRESS and ... BUDDY_DEALS_EMAIL_APP_PASSWORD.

The price is taken from the email itself; store pages are never opened by Buddy (their terms forbid automated
visits). The owner opens a deal's link in their own browser from the Deals page.
"""

import email
import email.policy
import hashlib
import imaplib
import json
import logging
import re
from datetime import date, datetime, timezone
from email.utils import parseaddr
from html.parser import HTMLParser

from . import gateway
from .memory import Memory
from .tools import ToolError, local_now

log = logging.getLogger(__name__)

MAX_EMAILS, MAX_DEALS_PER_EMAIL = 300, 60
LAST_KEY, DAY_KEY, STATUS_KEY = "dealmail:last_uid", "dealmail:day", "dealmail:status"
HOUR = 1  # 01:00, before the pattern learning at 02:30

PRICE = re.compile(r"(?<![\w.])\$\s?(\d{1,3}(?:,\d{3})*(?:\.\d{2})?|\d+(?:\.\d{2})?)(?!\d)")
REGULAR = re.compile(r"(?:reg(?:ular)?\.?|was|list(?:\s+price)?|orig(?:inal)?\.?|compare(?:\s+at)?|msrp)\s*:?\s*"
                     r"\$\s?(\d[\d,]*(?:\.\d{2})?)", re.I)
ENDS = re.compile(r"(?:ends|expires|through|thru|until|valid through|book by)\s*:?\s*"
                  r"((?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+\d{1,2}(?:,\s*\d{4})?|"
                  r"\d{1,2}/\d{1,2}(?:/\d{2,4})?)", re.I)
SAVINGS = re.compile(r"(?:sav(?:e|ed|ings of)|extra|additional)\s+(?:up to\s+)?\$\s?(\d[\d,]*(?:\.\d{2})?)|"
                     r"\$\s?(\d[\d,]*(?:\.\d{2})?)\s+(?:off|savings|cash ?back|back|credit)", re.I)  # not prices
FLIGHT = re.compile(r"\b(fares?|flights?|airfare|round[- ]?trip|one[- ]?way|nonstop|economy|first class)\b", re.I)
HOTEL = re.compile(r"\b(hotels?|resorts?|per night|/\s?night|nightly|suites?|stays?|inn)\b", re.I)
ROUTE = re.compile(r"\b([A-Z]{3})\s*(?:to|-|–|→|>)\s*([A-Z]{3})\b")
CITIES = re.compile(r"\b(?:from\s+)?([A-Z][a-zA-Z .]{2,24}?)\s+to\s+([A-Z][a-zA-Z .]{2,24}?)(?:\s+(?:from|for|starting|"
                    r"round|one|at|\$)|[,.:]|$)")
PLACE = re.compile(r"\bin\s+([A-Z][a-zA-Z .]{2,30}?)(?:[,.:!]|\s+(?:from|for|starting|at|\$)|$)")
SKIP_LINK = re.compile(r"unsubscribe|preferences|privacy|view (?:this|in) (?:email|browser)|manage|mailto:|"
                       r"facebook|twitter|instagram|tiktok|youtube|pinterest|app store|google play", re.I)
BLOCKS = {"td", "tr", "table", "div", "p", "li", "h1", "h2", "h3", "h4", "section", "article"}  # <br> stays inline


class _Cells(HTMLParser):
    """An HTML email as cells (table cells, paragraphs, list items): each with its text and its links."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.cells, self.text, self.links, self.href, self.anchor = [], [], [], None, []
        self.skip = 0

    def _flush(self):
        text = " ".join(" ".join(self.text).split())
        if text:
            self.cells.append({"text": text, "links": self.links})
        self.text, self.links = [], []

    def handle_starttag(self, tag, attrs):
        if tag in ("style", "script", "head"):
            self.skip += 1
        elif tag == "a":
            self.href, self.anchor = dict(attrs).get("href", ""), []
        elif tag == "br":
            self.text.append(" ")
        elif tag in BLOCKS and tag not in ("td",):
            self._flush()

    def handle_endtag(self, tag):
        if tag in ("style", "script", "head"):
            self.skip = max(0, self.skip - 1)
        elif tag == "a":
            text = " ".join(" ".join(self.anchor).split())
            if self.href:
                self.links.append({"href": self.href, "text": text})
            self.href = None
        elif tag in BLOCKS:
            self._flush()

    def handle_data(self, data):
        if self.skip:
            return
        self.text.append(data)
        if self.href is not None:
            self.anchor.append(data)


def _cells(message) -> list[dict]:
    html = plain = ""
    for part in message.walk():
        kind = part.get_content_type()
        try:
            if kind == "text/html" and not html:
                html = part.get_content()
            elif kind == "text/plain" and not plain:
                plain = part.get_content()
        except (LookupError, ValueError):
            continue
    if html:
        p = _Cells()
        p.feed(html)
        p._flush()
        return p.cells
    cells = []
    for line in plain.splitlines():
        urls = re.findall(r"https://\S+", line)
        text = re.sub(r"https://\S+", "", line).strip()
        if text or urls:
            if not text and cells:
                cells[-1]["links"] += [{"href": u, "text": ""} for u in urls]
            else:
                cells.append({"text": text, "links": [{"href": u, "text": ""} for u in urls]})
    return cells


def _num(text: str) -> float:
    return float(text.replace(",", ""))


def _link(cell: dict) -> str:
    for l in cell["links"]:
        if l["href"].startswith("https://") and not SKIP_LINK.search(l["href"] + " " + l["text"]):
            return l["href"][:500]
    return ""


def _title(cell: dict) -> str:
    named = [l["text"] for l in cell["links"] if l["text"] and not PRICE.fullmatch(l["text"].strip())
             and not SKIP_LINK.search(l["text"]) and len(l["text"]) >= 6]
    text = max(named, key=len) if named else cell["text"]
    text = re.sub(r"\s*(?:shop now|buy now|book now|see deal|get deal|learn more)\b.*$", "", text, flags=re.I)
    return text.strip(" -–|:")[:160]


def deals_in(message) -> list[dict]:
    """The deals in one email: title, domain (product, flight, hotel), item key, price, regular price, store, link,
    ends. Cells without a price, or with only a link to unsubscribe, are skipped."""
    sender = parseaddr(str(message.get("From", "")))[0] or parseaddr(str(message.get("From", "")))[1]
    subject = str(message.get("Subject", ""))
    cells = _cells(message)
    out, seen = [], set()
    for i, cell in enumerate(cells):
        prices = [p for p in PRICE.findall(SAVINGS.sub(" ", cell["text"])) if 0 < _num(p) < 100000]
        if not prices:
            continue
        before = cells[i - 1] if i else None
        if not _link(cell) and before and before["links"] and not PRICE.search(before["text"]):
            # the price on its own line under the product's name and link: one deal
            cell = {"text": before["text"] + " " + cell["text"], "links": before["links"]}
        context = " ".join(c["text"] for c in cells[max(0, i - 1): i + 2])
        title = _title(cell)
        if len(title) < 6 or PRICE.fullmatch(title.strip()):
            prev = cells[i - 1]["text"] if i else subject
            title = (prev if not PRICE.search(prev) else subject)[:160]
        reg = REGULAR.search(cell["text"])
        price = min(_num(p) for p in prices)
        regular = _num(reg.group(1)) if reg else (max(_num(p) for p in prices) if len(prices) > 1 else None)
        if regular is not None and regular <= price:
            regular = None
        where = f"{title} {subject} {context}"
        domain = "flight" if FLIGHT.search(where) or ROUTE.search(title) else \
            "hotel" if HOTEL.search(where) else "product"
        item = title
        if domain == "flight":  # the route in this deal's own words first, the lines around it only if missing
            r = ROUTE.search(title)
            c = None if r else CITIES.search(title)
            if not (r or c):
                r = ROUTE.search(context)
                c = None if r else CITIES.search(context)
            item = f"{r.group(1)}-{r.group(2)}" if r else \
                f"{c.group(1).strip()}-{c.group(2).strip()}".lower() if c else title
        elif domain == "hotel":
            p = PLACE.search(title) or PLACE.search(cell["text"]) or PLACE.search(context)
            item = f"hotels:{p.group(1).strip().lower()}" if p else f"hotels:{title.lower()}"
        ends = ENDS.search(cell["text"]) or ENDS.search(subject)
        key = (item.lower(), price)
        if key in seen:
            continue
        seen.add(key)
        out.append({"title": title, "domain": domain, "item": item, "price": price, "regular": regular,
                    "store": sender[:60], "url": _link(cell), "ends": ends.group(1) if ends else "",
                    "subject": subject[:160]})
        if len(out) >= MAX_DEALS_PER_EMAIL:
            break
    return out


def configured(env=None) -> bool:
    return bool(gateway._secret(env, "BUDDY_DEALS_EMAIL_ADDRESS") and gateway._secret(env, "BUDDY_DEALS_EMAIL_APP_PASSWORD"))


def _imap(env=None):
    address = gateway._secret(env, "BUDDY_DEALS_EMAIL_ADDRESS")
    password = gateway._secret(env, "BUDDY_DEALS_EMAIL_APP_PASSWORD").replace(" ", "")
    if not (address and password):
        raise ToolError("The deals inbox isn't set up: make a separate email for deal newsletters, then "
                        "sudo bash /opt/buddy/deploy/set.sh BUDDY_DEALS_EMAIL_ADDRESS and BUDDY_DEALS_EMAIL_APP_PASSWORD")
    from .email_accounts import GMAIL, PROVIDERS  # noqa: PLC0415
    host = gateway._secret(env, "BUDDY_DEALS_EMAIL_IMAP_HOST") or \
        PROVIDERS.get(address.rsplit("@", 1)[-1].lower(), GMAIL)[0]
    imap = imaplib.IMAP4_SSL(host, timeout=60)
    imap.login(address, password)
    return imap


def fetch_new(memory: Memory, env=None, connect=None) -> list:
    """New messages in the deals inbox since the last run (read-only: nothing is marked read or moved)."""
    imap = (connect or _imap)(env)
    try:
        imap.select("INBOX", readonly=True)
        last = int(memory.get(LAST_KEY) or 0)
        if last:
            _, found = imap.uid("search", None, f"UID {last + 1}:*")
        else:  # the first run: the last two weeks
            since = (local_now().date().toordinal() - 14)
            _, found = imap.uid("search", None, f"(SINCE {date.fromordinal(since):%d-%b-%Y})")
        uids = [int(u) for u in (found[0] or b"").split() if int(u) > last][-MAX_EMAILS:]
        out = []
        for uid in uids:
            _, parts = imap.uid("fetch", str(uid), "(BODY.PEEK[])")
            raw = next((p[1] for p in parts if isinstance(p, tuple)), None)
            if raw:
                out.append(email.message_from_bytes(raw, policy=email.policy.default))
        if uids:
            memory.set(LAST_KEY, str(max(uids)))
        return out
    finally:
        try:
            imap.logout()
        except Exception:
            pass


CLEAN_MAX = 500


def clean_days(env=None) -> int:
    """After how many days read newsletters are moved to Trash (BUDDY_DEALS_EMAIL_CLEAN_DAYS; 2 by default, 0 = never)."""
    try:
        return max(0, int(gateway._secret(env, "BUDDY_DEALS_EMAIL_CLEAN_DAYS") or 2))
    except ValueError:
        return 2


def clean(memory: Memory, env=None, connect=None) -> int:
    """Tidy the deals inbox: emails older than clean_days that Buddy has already read into the price book are moved
    to Trash (the mail service empties Trash by itself, usually after 30 days, so a mistake can be undone). Only
    bulk mail is touched: an email must carry the List-Unsubscribe header every newsletter has, so a personal email
    that lands in this inbox is never removed. Returns how many were moved."""
    days = clean_days(env)
    last = int(memory.get(LAST_KEY) or 0)
    if not days or not last:
        return 0
    imap = (connect or _imap)(env)
    try:
        imap.select("INBOX")
        before = date.fromordinal(local_now().date().toordinal() - days)
        _, found = imap.uid("search", None, f"(UID 1:{last} BEFORE {before:%d-%b-%Y})")
        uids = [u for u in (found[0] or b"").split() if int(u) <= last][:CLEAN_MAX]
        bulk = []
        for uid in uids:
            _, parts = imap.uid("fetch", uid.decode(), "(BODY.PEEK[HEADER.FIELDS (LIST-UNSUBSCRIBE)])")
            header = next((p[1] for p in parts if isinstance(p, tuple)), b"") or b""
            if b"list-unsubscribe" in header.lower():
                bulk.append(uid.decode())
        gmail = "gmail" in str(getattr(imap, "host", "")).lower()
        for uid in bulk:
            if gmail:  # Gmail: the Trash label moves it out of the inbox and into Trash
                imap.uid("store", uid, "+X-GM-LABELS", "\\Trash")
            else:
                imap.uid("store", uid, "+FLAGS", "(\\Deleted)")
        if bulk and not gmail:
            imap.expunge()
        return len(bulk)
    finally:
        try:
            imap.logout()
        except Exception:
            pass


def keep(memory: Memory, deals: list[dict], when: datetime | None = None, source: str = "") -> int:
    """Into the price book: each deal (once) and its price as history."""
    from . import dealforecast, pricebook  # noqa: PLC0415
    pricebook.conn(memory)
    added = 0
    day = (when or datetime.now(timezone.utc)).date().isoformat()
    for d in deals:
        d.setdefault("reason", dealforecast.reason_of(d["title"], d.get("subject", ""), kind=_kind(d)))
    for d in deals:
        url = d["url"] or "email:" + hashlib.sha1(f"{d['store']}|{d['title']}|{d['price']}|{day}".encode()).hexdigest()
        got = pricebook.run(memory, "INSERT OR IGNORE INTO deals (seen_on, source, title, url, price, store, kind, posted, "
                                    "domain, item, regular_price, ends, reason, model) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, "
                                    "?, ?, ?, ?, ?) RETURNING id",
                            (day, source or f"email: {d['store']}", d["title"], url, d["price"], d["store"],
                             _kind(d), day, d["domain"], d["item"].lower(), d["regular"], d["ends"], d["reason"],
                             dealforecast.model_key(d["title"] if d["domain"] == "product" else d["item"])))
        if got:
            added += 1
            pricebook.observe(memory, d["domain"], d["item"], d["price"], seller=d["store"], url=d["url"],
                              regular_price=d["regular"], on_sale=bool(d["regular"]),
                              source="deal notification" if source.startswith("app:") else "deal email",
                              seen_at=when)
    return added


def run(memory: Memory, env=None, connect=None) -> str:
    try:
        messages = fetch_new(memory, env, connect)
    except ToolError as e:
        return _status(memory, str(e))
    except (imaplib.IMAP4.error, OSError) as e:
        return _status(memory, f"couldn't read the deals inbox: {e}")
    found = kept = 0
    for m in messages:
        try:
            deals = deals_in(m)
        except Exception:
            log.warning("deals inbox: couldn't read an email", exc_info=True)
            continue
        found += len(deals)
        kept += keep(memory, deals)
        from . import dealforecast  # noqa: PLC0415  watched-for deals: told in the morning
        dealforecast.check(memory, [{**d, "source": f"email: {d['store']}"} for d in deals])
    cleaned = 0
    try:
        cleaned = clean(memory, env, connect)
    except (imaplib.IMAP4.error, OSError, ToolError) as e:
        log.warning("deals inbox: cleaning failed: %s", e)
    return _status(memory, f"read {len(messages)} email{'s' if len(messages) != 1 else ''}: {found} deals, "
                           f"{kept} new kept in the price book"
                           + (f"; {cleaned} read newsletters older than {clean_days(env)} days moved to Trash"
                              if cleaned else ""))


def _kind(d: dict) -> str:
    if d["domain"] != "product":
        return d["domain"]
    from .shopper import kind_of  # noqa: PLC0415
    return kind_of(d["title"], d.get("store", ""))


def _status(memory: Memory, text: str) -> str:
    memory.set(STATUS_KEY, json.dumps({"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "status": text}))
    return text


def due(memory: Memory, now: datetime | None = None) -> bool:
    now = now or local_now()
    return HOUR <= now.hour < HOUR + 4 and memory.get(DAY_KEY) != now.date().isoformat()


def check(memory=None, env=None, fetch=None) -> None:
    """Buddy Connect's Test: sign in to the deals inbox."""
    _imap(env).logout()
