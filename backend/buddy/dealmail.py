"""The deals inbox: a separate email account the owner subscribes to deal alerts and store, airline and hotel
newsletters. Every night Buddy reads the new emails there (code only, no AI), pulls each deal out
(product, flight or hotel; price, regular price, store, link, when it ends) and keeps it in the price book, so the
deals feed the Deals page (web/deals.html) and the nightly pattern learning.

Once read, newsletters older than two days are moved to Trash so the inbox stays clean (clean(): bulk mail only,
never a personal email; BUDDY_DEALS_EMAIL_CLEAN_DAYS, 0 to keep everything).

Why a separate inbox: newsletters don't bury real mail or go through the AI email sorting (which costs money per
email), and nothing personal is in it. Setup: a new free email account (2-step verification on, an app password),
then sudo bash /opt/buddy/deploy/set.sh BUDDY_DEALS_EMAIL_ADDRESS and ... BUDDY_DEALS_EMAIL_APP_PASSWORD.

All the owner's email, for deals only (on unless BUDDY_DEALS_FROM_ALL_EMAIL=0): each night Buddy also looks through
the owner's regular email accounts for marketing email, and only that. It first reads just the headers of each new
message; a message is opened only if it is bulk mail (it carries an unsubscribe header, or says it is bulk), so a
personal email's body is never downloaded for this. From the bulk mail it takes the same deals into the price
book, and from card issuers' emails (American Express, Chase, Citi, Capital One...) the card offers: "spend $250
or more, get $50 back at Best Buy, by 12/31". Card offers are about the owner's own cards, so they are kept in
Buddy's encrypted database, not the price book. Regular accounts are only ever read: nothing is marked read, moved
or deleted there (the tidy-up is for the deals inbox alone). All of it is code on the owner's server: no AI reads
these emails, and nothing is sent anywhere.

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
# "$1,299.99 After $300 OFF" (a warehouse club's way of writing a sale): the price is the one after the saving, so
# the regular price is the two added up.
AFTER_OFF = re.compile(r"\bafter\s+\$\s?(\d[\d,]*(?:\.\d{2})?)\s+(?:off|(?:instant\s+|manufacturer'?s?\s+)?savings)\b", re.I)
ENDS = re.compile(r"(?:ends|expires|through|thru|until|valid through|book by)\s*:?\s*"
                  r"((?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+\d{1,2}(?:,\s*\d{4})?|"
                  r"\d{1,2}/\d{1,2}(?:/\d{2,4})?)", re.I)
SAVINGS = re.compile(r"(?:sav(?:e|ed|ings of)|extra|additional)\s+(?:up to\s+)?\$\s?(\d[\d,]*(?:\.\d{2})?)|"
                     r"\$\s?(\d[\d,]*(?:\.\d{2})?)\s+(?:off|savings|cash ?back|back|credit)", re.I)  # not prices
FLIGHT = re.compile(r"\b(fares?|flights?|airfare|round[- ]?trip|one[- ]?way|nonstop|basic economy|"
                    r"economy (?:class|fares?|seats?)|first class)\b", re.I)  # not "the economy": that is news
BIG = re.compile(r"(?:\.\d+)?\s*(?:trillion|billion|million|bn\b|[kmb]\b)", re.I)  # "$10 trillion" is not a price
# Who sends fares: an airline, or a service that is about flights or travel. Two three-letter codes in a title
# ("PAC-MAN", "USB to VGA") are a route only in an email from one of these, or where flights are spoken of.
TRAVEL_WORDS = (r"air(?:lines?|ways)?|flights?|fly\w*|travel\w*|trips?|vacations?|jet\w*|delta|united|southwest|alaska|"
                r"spirit|frontier|hawaiian|latam|avianca|copa|volaris|aerom[eé]xico|lufthansa|emirates|qatar|etihad|"
                r"klm|iberia|qantas|virgin|kayak|skyscanner|expedia|hopper|going|priceline|orbitz")
TRAVEL = re.compile(rf"\b(?:{TRAVEL_WORDS})\b", re.I)
# What prices are usually like ("flights to San Juan usually cost between $155-295"): information, not an offer.
NOT_AN_OFFER = re.compile(r"\b(?:usually|typically|normally) (?:costs?|books?)\b|\bprices? (?:are|is) (?:currently )?"
                          r"(?:low|typical|high)\b|\bprice insights?\b", re.I)
HOTEL = re.compile(r"\b(hotels?|resorts?|per night|/\s?night|nightly|suites?|stays?|inn)\b", re.I)
ROUTE = re.compile(r"\b([A-Z]{3})\s*(?:to|-|–|→|>)\s*([A-Z]{3})\b")
CITIES = re.compile(r"\b(?:from\s+)?([A-Z][a-zA-Z .]{2,24}?)\s+to\s+([A-Z][a-zA-Z .]{2,24}?)(?:\s+(?:from|for|starting|"
                    r"round|one|at|\$)|[,.:]|$)")
PLACE = re.compile(r"\bin\s+([A-Z][a-zA-Z .]{2,30}?)(?:[,.:!]|\s+(?:from|for|starting|at|\$)|$)")
SKIP_LINK = re.compile(r"unsubscribe|preferences|privacy|view (?:this|in) (?:email|browser)|manage|mailto:|"
                       r"facebook|twitter|instagram|tiktok|youtube|pinterest|app store|google play", re.I)
BLOCKS = {"td", "tr", "table", "div", "p", "li", "h1", "h2", "h3", "h4", "section", "article"}  # <br> stays inline
# Words a picture's label adds that say nothing about what it shows ("Dell logo" is Dell).
PICTURE_NOISE = re.compile(r"(?i)\b(?:logo|image|img|icon|banner|photo|picture|graphic|spacer|pixel)s?\b")


def picture_words(alt: str) -> str:
    """What a picture's label (its alt text) says, for a store or product shown only as a picture: '' for none."""
    words = " ".join(PICTURE_NOISE.sub(" ", str(alt or "")).split()).strip(" -–|:,.")
    return words if 2 <= len(words) <= 80 and re.search(r"[A-Za-z]", words) else ""


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
        elif tag == "img" and not self.skip:  # a store's logo, a product's photo: its label is its name
            words = picture_words(dict(attrs).get("alt") or "")
            if words:
                self.text.append(f" {words} ")
                if self.href is not None:
                    self.anchor.append(f" {words} ")
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


def _prices(text: str) -> list[float]:
    """The prices in a line: dollar amounts that are not savings ("$20 off") and not sums ("$10 trillion")."""
    text = SAVINGS.sub(" ", AFTER_OFF.sub(" ", text))  # "after $100 manufacturer's savings" is a saving too
    return [n for n in (_num(m.group(1)) for m in PRICE.finditer(text) if not BIG.match(text, m.end()))
            if 0 < n < 100000]


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
    out, seen, named_by_subject = [], set(), False
    for i, cell in enumerate(cells):
        prices = _prices(cell["text"])
        if not prices or NOT_AN_OFFER.search(cell["text"]):
            continue  # no price, or a sentence about what prices are usually like: not a deal
        before = cells[i - 1] if i else None
        after = cells[i + 1] if i + 1 < len(cells) else None
        if not _link(cell) and before and before["links"] and not PRICE.search(before["text"]):
            # the price on its own line under the product's name and link: one deal
            cell = {"text": before["text"] + " " + cell["text"], "links": before["links"]}
        context = " ".join(c["text"] for c in cells[max(0, i - 1): i + 2])
        title = _title(cell)
        if len(title) < 6 or PRICE.fullmatch(title.strip()):
            prev = cells[i - 1]["text"] if i else subject
            if PRICE.search(prev) or len(prev.strip()) < 6:
                # A price with no name of its own. The email's subject can name one deal, not every loose price
                # in the email: an alert digest would otherwise list its subject again and again at other prices.
                if named_by_subject:
                    continue
                named_by_subject = True
                title = re.sub(r"^Buddy:\s+", "", subject)[:160]
                prices = _prices(subject) or prices  # the price the subject itself gives, when it gives one
            else:
                title = prev[:160]
        reg = REGULAR.search(cell["text"])
        price = min(prices)
        less = None if reg else AFTER_OFF.search(cell["text"])
        regular = _num(reg.group(1)) if reg else round(price + _num(less.group(1)), 2) if less else \
            (max(prices) if len(prices) > 1 else None)
        if regular is not None and regular <= price:
            regular = None
        where = f"{title} {subject} {context}"
        domain = "flight" if FLIGHT.search(where) or (ROUTE.search(title) and TRAVEL.search(sender)) else \
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
        # The link: this line's own, else the line before or after it when that line carries no price of its own.
        url = _link(cell) or next((_link(c) for c in (before, after) if c and not _prices(c["text"]) and _link(c)), "")
        out.append({"title": title, "domain": domain, "item": item, "price": price, "regular": regular,
                    "store": sender[:60], "url": url, "ends": ends.group(1) if ends else "",
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


# --- the owner's regular accounts: marketing mail only, read-only ---------------------------------------------
ISSUERS = {"American Express": r"american ?express|\bamex\b", "Chase": r"\bchase\b", "Citi": r"\bciti(?:bank)?\b",
           "Capital One": r"capital ?one", "Discover": r"\bdiscover\b", "Bank of America": r"bank ?of ?america",
           "Wells Fargo": r"wells ?fargo", "U.S. Bank": r"\bu\.?s\.? ?bank", "Barclays": r"barclays"}
SPEND_GET = re.compile(r"spend\s+\$\s?(\d[\d,]*)(?:\.\d{2})?(?:\s+or more)?[^$]{0,90}?(?:get|earn|receive)\s+"
                       r"(?:a\s+)?\$\s?(\d[\d,]*)(?:\.\d{2})?\s+(?:back|statement credit|credit|cash ?back)", re.I)
PCT_BACK = re.compile(r"(?:get|earn|receive)\s+(\d{1,2})%\s+(?:cash ?)?back", re.I)
# The reward first: "earn a $10 statement credit on a purchase of $50 or more".
EARN_ON = re.compile(r"(?:get|earn|receive)\s+(?:a\s+)?\$\s?(\d[\d,]*)(?:\.\d{2})?\s+(?:back|statement credit|credit|"
                     r"cash ?back)\b[^$]{0,60}?\b(?:purchases?|spend(?:ing)?|orders?)\s+(?:of\s+)?\$\s?(\d[\d,]*)", re.I)
# Points for every dollar: "earn +3 Membership Rewards points per eligible dollar spent".
PER_DOLLAR = re.compile(r"(?:get|earn|receive)\s+(?:an?\s+)?(?:additional\s+|extra\s+)?\+?\s?(\d{1,2})\s+"
                        r"(?:[\w®]+\s+){0,3}?(?:points|miles)\s+(?:per|for every|on every)\s+(?:eligible\s+)?dollar", re.I)
POINTS = re.compile(r"(?:get|earn|receive)\s+(?:an additional\s+|\+\s?)?(\d{1,3}(?:,\d{3})+|\d{3,6})\s+(?:[\w® ]{0,30}\s)?"
                    r"(?:points|miles)[^$]{0,90}?spend(?:ing)?\s+\$\s?(\d[\d,]*)", re.I)
AT = re.compile(r"\b(?:at|with|from|on)\s+((?:[A-Z][\w&'-]*(?:\.\w+)*)(?:\s+(?:[A-Z&][\w&'-]*(?:\.\w+)*)){0,3})")
OFFER_ENDS = re.compile(r"(?:ends|expires|through|thru|until|valid through|by|before)\s*:?\s*"
                        r"((?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+\d{1,2}(?:,\s*\d{4})?|"
                        r"\d{1,2}/\d{1,2}(?:/\d{2,4})?)", re.I)
OFFERS_SCHEMA = """
CREATE TABLE IF NOT EXISTS card_offers (
    id INTEGER PRIMARY KEY AUTOINCREMENT, issuer TEXT NOT NULL, merchant TEXT NOT NULL, spend REAL, reward REAL NOT NULL,
    reward_kind TEXT NOT NULL, ends TEXT DEFAULT '', seen_on TEXT NOT NULL, subject TEXT DEFAULT '',
    UNIQUE (issuer, merchant, spend, reward, reward_kind)
);
"""


def all_mail_on(env=None) -> bool:
    return (gateway._secret(env, "BUDDY_DEALS_FROM_ALL_EMAIL") or "1") not in ("0", "off", "no")


def accounts(env=None) -> list:
    """The owner's regular email accounts (not the deals inbox), when reading them for deals is on."""
    from .email_accounts import email_accounts  # noqa: PLC0415
    if not all_mail_on(env):
        return []
    import os  # noqa: PLC0415
    deals = (gateway._secret(env, "BUDDY_DEALS_EMAIL_ADDRESS") or "").lower()
    return [a for a in email_accounts(os.environ if env is None else env) if a.address.lower() != deals]


def is_bulk(message) -> bool:
    """Marketing or list mail, by its own headers. A personal email has neither."""
    return bool(message.get("List-Unsubscribe")) or str(message.get("Precedence", "")).strip().lower() in ("bulk", "list")


def _imap_account(account):
    imap = imaplib.IMAP4_SSL(account.imap_host, timeout=60)
    imap.login(account.address, account.password)
    return imap


def fetch_bulk(memory: Memory, account, connect=None) -> tuple[list, int]:
    """(the new bulk messages in one regular account, how many new messages were looked at). Read-only; the body
    of a message that isn't bulk mail is never fetched."""
    imap = (connect or _imap_account)(account)
    key = f"{LAST_KEY}:{account.address.lower()}"
    try:
        imap.select(account.folder, readonly=True)
        last = int(memory.get(key) or 0)
        if last:
            _, found = imap.uid("search", None, f"UID {last + 1}:*")
        else:  # the first run: the last two weeks
            _, found = imap.uid("search", None, f"(SINCE {date.fromordinal(local_now().date().toordinal() - 14):%d-%b-%Y})")
        uids = [int(u) for u in (found[0] or b"").split() if int(u) > last][-MAX_EMAILS:]
        out = []
        for uid in uids:
            _, parts = imap.uid("fetch", str(uid), "(BODY.PEEK[HEADER.FIELDS (LIST-UNSUBSCRIBE PRECEDENCE FROM)])")
            head = next((p[1] for p in parts if isinstance(p, tuple)), None)
            if not head or not is_bulk(email.message_from_bytes(head, policy=email.policy.default)):
                continue
            _, parts = imap.uid("fetch", str(uid), "(BODY.PEEK[])")
            raw = next((p[1] for p in parts if isinstance(p, tuple)), None)
            if raw:
                out.append(email.message_from_bytes(raw, policy=email.policy.default))
        if uids:
            memory.set(key, str(max(uids)))
        return out, len(uids)
    finally:
        try:
            imap.logout()
        except Exception:
            pass


def issuer_of(message) -> str:
    sender = str(message.get("From", ""))
    return next((name for name, rx in ISSUERS.items() if re.search(rx, sender, re.I)), "")


def offers_in(message) -> list[dict]:
    """Card offers in a card issuer's email: [{issuer, merchant, spend, reward, reward_kind, ends, subject}]."""
    issuer = issuer_of(message)
    if not issuer:
        return []
    subject = str(message.get("Subject", ""))
    cells = _cells(message)
    out, seen = [], set()
    for i, cell in enumerate(cells):
        text = " ".join(cell["text"].split())
        if m := SPEND_GET.search(text):
            spend, reward, kind = _num(m.group(1)), _num(m.group(2)), "cash"
        elif m := EARN_ON.search(text):
            spend, reward, kind = _num(m.group(2)), _num(m.group(1)), "cash"
        elif m := POINTS.search(text):
            spend, reward, kind = _num(m.group(2)), _num(m.group(1)), "points"
        elif m := PER_DOLLAR.search(text):
            spend, reward, kind = None, float(m.group(1)), "points_per_dollar"
        elif m := PCT_BACK.search(text):
            spend, reward, kind = None, float(m.group(1)), "percent"
        else:
            continue
        where = AT.search(text[m.end():]) or AT.search(text)
        merchant = re.sub(r"\s+(?:by|through|until|before|and|or|when|with)\b.*$", "", where.group(1)).strip(" .,") \
            if where else ""
        lead = text[:m.start()].strip(" -–:|,.")  # "Dell  Spend $500 or more...": the logo's label in the same cell
        if not merchant and 2 < len(lead) <= 40 and not re.search(r"[\d$%]", lead):
            merchant = lead
        before = " ".join(cells[i - 1]["text"].split()) if i else ""
        if not merchant and 2 < len(before) <= 40 and not re.search(r"[\d$%]", before):
            merchant = before  # the store's name on its own line above the offer; never a neighbouring offer's
        if not merchant or re.fullmatch(r"(?i)(your|the|this|eligible|select|participating).*", merchant):
            continue
        ends = OFFER_ENDS.search(text)  # this offer's own date only: a neighbour's would be wrong
        after = " ".join(cells[i + 1]["text"].split()) if i + 1 < len(cells) else ""
        if not ends and len(after) <= 40 and not re.search(r"\$|%|points|miles", after, re.I):
            ends = OFFER_ENDS.search(after)  # "Expires 12/31/2026" in the cell under the offer, on its own
        key = (merchant.lower(), spend, reward, kind)
        if key not in seen:
            seen.add(key)
            out.append({"issuer": issuer, "merchant": merchant[:60], "spend": spend, "reward": reward,
                        "reward_kind": kind, "ends": ends.group(1) if ends else "", "subject": subject[:160]})
    return out[:40]


def keep_offers(memory: Memory, offers: list[dict], today: date | None = None) -> int:
    """New card offers into Buddy's own (encrypted) database; one seen again just has its date refreshed."""
    memory.db.executescript(OFFERS_SCHEMA)
    day = (today or local_now().date()).isoformat()
    added = 0
    for o in offers:
        cur = memory.db.execute(
            "INSERT OR IGNORE INTO card_offers (issuer, merchant, spend, reward, reward_kind, ends, seen_on, subject) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (o["issuer"], o["merchant"], o["spend"] or 0, o["reward"], o["reward_kind"],
                                                _ends_iso(o["ends"], today), day, o["subject"]))
        added += cur.rowcount
        if not cur.rowcount:
            memory.db.execute("UPDATE card_offers SET seen_on = ? WHERE issuer = ? AND merchant = ? AND reward = ? AND "
                              "reward_kind = ?", (day, o["issuer"], o["merchant"], o["reward"], o["reward_kind"]))
    memory.db.commit()
    return added


def _ends_iso(text: str, today: date | None = None) -> str:
    """'12/31', '12/31/26' or 'Dec 31, 2026' as YYYY-MM-DD ('' when it can't be read): the next such date."""
    today = today or local_now().date()
    text = (text or "").strip().rstrip(".")
    if not text:
        return ""
    try:
        if "/" in text:
            parts = [int(p) for p in text.split("/")]
            year = parts[2] + (2000 if parts[2] < 100 else 0) if len(parts) > 2 else today.year
            got = date(year, parts[0], parts[1])
        else:
            m = re.match(r"([A-Za-z]{3})[a-z]*\.?\s+(\d{1,2})(?:,\s*(\d{4}))?", text)
            month = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"].index(
                m.group(1).lower()) + 1
            got = date(int(m.group(3) or today.year), month, int(m.group(2)))
        if got < today and "/" in text and len(text.split("/")) == 2 or (got < today and not re.search(r"\d{4}", text)):
            got = got.replace(year=got.year + 1)
        return got.isoformat()
    except (ValueError, AttributeError, IndexError):
        return ""


def offers(memory: Memory, merchant: str = "", today: date | None = None) -> list[dict]:
    """The card offers still running (not past their end date, seen in the last 90 days), a merchant's when given."""
    memory.db.executescript(OFFERS_SCHEMA)
    today = today or local_now().date()
    since = date.fromordinal(today.toordinal() - 90).isoformat()
    rows = [dict(r) for r in memory.db.execute(
        "SELECT issuer, merchant, spend, reward, reward_kind, ends, seen_on FROM card_offers WHERE seen_on >= ? AND "
        "(ends = '' OR ends >= ?) ORDER BY merchant", (since, today.isoformat()))]
    words = {w for w in re.findall(r"[a-z0-9]{3,}", (merchant or "").lower())} - {"the", "and", "com", "inc"}
    if merchant:
        rows = [r for r in rows if words & set(re.findall(r"[a-z0-9]{3,}", r["merchant"].lower()))]
    for r in rows:
        r["offer"] = offer_text(r)
    return rows


def offer_short(o: dict) -> str:
    """The reward in a few words, for a list: '$50 back on $250+', '5,000 points on $500+', '10% back',
    '3 extra points per $'."""
    kind, reward, spend = o["reward_kind"], o["reward"], o.get("spend") or 0
    what = (f"${reward:,.0f} back" if kind == "cash" else f"{reward:g}% back" if kind == "percent"
            else f"{reward:g} extra points per $" if kind == "points_per_dollar" else f"{reward:,.0f} points")
    return what + (f" on ${spend:,.0f}+" if spend else "")


def offer_text(o: dict) -> str:
    what = (f"${o['reward']:,.0f} back" if o["reward_kind"] == "cash" else f"{o['reward']:g}% back"
            if o["reward_kind"] == "percent" else f"{o['reward']:g} extra points per dollar"
            if o["reward_kind"] == "points_per_dollar" else f"{o['reward']:,.0f} points")
    return (f"{o['issuer']}: {what} at {o['merchant']}" + (f" when you spend ${o['spend']:,.0f} or more" if o["spend"]
                                                             else "") + (f", until {o['ends']}" if o["ends"] else ""))


def run_all(memory: Memory, env=None, connect=None) -> str:
    """The owner's regular accounts: bulk mail only. Returns a status line ('' when there is nothing to read)."""
    from . import dealforecast, mailmap  # noqa: PLC0415
    lines = []
    for account in accounts(env):
        try:
            messages, looked = fetch_bulk(memory, account, connect)
        except (imaplib.IMAP4.error, OSError) as e:
            lines.append(f"{account.address}: couldn't be read ({str(e)[:60]})")
            continue
        found = kept = new_offers = 0
        for m in messages:
            try:  # what the owner switched off for this sender on the Email page is not taken
                got = offers_in(m) if mailmap.use_on(memory, str(m.get("From", "")), "offers") else []
                deals = [] if issuer_of(m) or not mailmap.use_on(memory, str(m.get("From", "")), "deals") \
                    else deals_in(m)  # a card issuer's email holds offers, not prices
            except Exception:
                log.warning("deal mail: couldn't read a marketing email", exc_info=True)
                continue
            found += len(deals)
            kept += keep(memory, deals, source="your email")
            new_offers += keep_offers(memory, got)
            dealforecast.check(memory, [{**d, "source": f"email: {d['store']}"} for d in deals])
        lines.append(f"{account.address}: {len(messages)} marketing emails of {looked} new, {found} deals "
                     f"({kept} new), {new_offers} new card offers")
    return "; ".join(lines)


def keep(memory: Memory, deals: list[dict], when: datetime | None = None, source: str = "") -> int:
    """Into the price book: each deal (once) and its price as history."""
    from . import dealforecast, pricebook  # noqa: PLC0415
    pricebook.conn(memory)
    added = 0
    day = (when or datetime.now(timezone.utc)).date().isoformat()
    for d in deals:
        d.setdefault("reason", dealforecast.reason_of(d["title"], d.get("subject", ""), kind=_kind(d)))
    for d in deals:
        url = d["url"] or "email:" + hashlib.sha1(
            f"{d['store']}|{d['title']}|{d['price']}|{day}".encode(), usedforsecurity=False).hexdigest()
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
                              seen_at=when, title=d["title"])
    if added:  # filed under their products here and now, so that no page has to do it before it can answer
        pricebook.identify(memory)
    return added


# Words in a card issuer's sender address. A message is opened only if its headers also say it is bulk mail from an
# issuer (issuer_of), so a personal email from someone called Chase is never opened.
ISSUER_FROM = ("americanexpress", "aexp", "chase", "citi", "capitalone", "discover", "bankofamerica", "wellsfargo",
               "usbank", "barclays")
REREAD_KEY, REREAD_DAYS, REREAD_MAX = "dealmail:offers_reread_v1", 30, 200


def reread_offers(memory: Memory, env=None, connect=None, days: int = REREAD_DAYS) -> str:
    """Once: the card issuers' emails of the last `days` days in the regular accounts, read again for offers. The
    nightly read only looks at mail it has not seen, so offers an older version of this reader missed (a store shown
    only as a logo) would otherwise stay missed. Read-only; headers first, and only an issuer's bulk mail is opened.
    Returns a status line ('' when it has already been done)."""
    from . import mailmap  # noqa: PLC0415
    mine = accounts(env)
    if memory.get(REREAD_KEY) or not mine:
        return ""
    since = date.fromordinal(local_now().date().toordinal() - days).strftime("%d-%b-%Y")
    added, failed = 0, False
    for account in mine:
        try:
            imap = (connect or _imap_account)(account)
        except (imaplib.IMAP4.error, OSError):
            failed = True
            continue
        try:
            imap.select(account.folder, readonly=True)
            uids: set[int] = set()
            for word in ISSUER_FROM:
                _, found = imap.uid("search", None, f'(SINCE {since} FROM "{word}")')
                uids |= {int(u) for u in (found[0] or b"").split()}
            for uid in sorted(uids)[-REREAD_MAX:]:
                _, parts = imap.uid("fetch", str(uid), "(BODY.PEEK[HEADER.FIELDS (LIST-UNSUBSCRIBE PRECEDENCE FROM)])")
                head = next((p[1] for p in parts if isinstance(p, tuple)), None)
                if not head:
                    continue
                headers = email.message_from_bytes(head, policy=email.policy.default)
                if not (is_bulk(headers) and issuer_of(headers)) or not mailmap.use_on(
                        memory, str(headers.get("From", "")), "offers"):
                    continue
                _, parts = imap.uid("fetch", str(uid), "(BODY.PEEK[])")
                raw = next((p[1] for p in parts if isinstance(p, tuple)), None)
                if raw:
                    added += keep_offers(memory, offers_in(email.message_from_bytes(raw, policy=email.policy.default)))
        except (imaplib.IMAP4.error, OSError):
            failed = True
        finally:
            try:
                imap.logout()
            except Exception:
                pass
    if failed:
        return ""  # tried again the next night
    memory.set(REREAD_KEY, local_now().date().isoformat())
    return f"card issuers' emails of the last {days} days read again: {added} card offers found that were missed"


def run(memory: Memory, env=None, connect=None, connect_account=None) -> str:
    """The nightly read: the deals inbox, then the marketing mail in the owner's regular accounts."""
    try:
        rest = run_all(memory, env, connect_account)
    except Exception:
        log.warning("deal mail: reading the regular accounts failed", exc_info=True)
        rest = ""
    try:
        again = reread_offers(memory, env, connect_account)
        rest = "; ".join(x for x in (rest, again) if x)
    except Exception:
        log.warning("deal mail: reading the card issuers' emails again failed", exc_info=True)
    first = _run_inbox(memory, env, connect)
    if rest and not configured(env) and connect is None:
        first = "no separate deals inbox"
    return _status(memory, first + (f". Your email accounts (marketing mail only, nothing changed there): {rest}"
                                    if rest else ""))


def _run_inbox(memory: Memory, env=None, connect=None) -> str:
    try:
        messages = fetch_new(memory, env, connect)
    except ToolError as e:
        return str(e)
    except (imaplib.IMAP4.error, OSError) as e:
        return f"couldn't read the deals inbox: {e}"
    try:
        gone = tidy(memory)
        if any(gone.values()):
            log.info("deals inbox: removed %s misread rows", gone)
    except Exception:
        log.warning("deals inbox: tidying failed", exc_info=True)
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
    return (f"read {len(messages)} email{'s' if len(messages) != 1 else ''}: {found} deals, "
            f"{kept} new kept in the price book"
            + (f"; {cleaned} read newsletters older than {clean_days(env)} days moved to Trash" if cleaned else ""))


def tidy(memory: Memory) -> dict:
    """Remove what an earlier reading of the emails got wrong, and nothing else: one email's subject kept many
    times at different prices on the same day, sums such as "$10 trillion" read as a price, a sentence about what
    flights usually cost kept as a fare, a product kept as a flight because its name looks like a route
    ("PAC-MAN"), and a fare filed under its sender as if that were an airline. Safe to run again."""
    from . import pricebook  # noqa: PLC0415
    pricebook.conn(memory)
    out = {}
    out["repeated"] = len(pricebook.run(memory, """DELETE FROM deals WHERE url LIKE 'email:%' AND (source, title, seen_on) IN
        (SELECT source, title, seen_on FROM deals WHERE url LIKE 'email:%' GROUP BY source, title, seen_on
         HAVING COUNT(DISTINCT price) > 1) RETURNING id"""))
    out["sums"] = len(pricebook.run(memory, "DELETE FROM deals WHERE source LIKE 'email:%' AND regexp_matches(title, "
                                            "'\\$\\s?[0-9][0-9,.]*\\s*(trillion|billion|million)', 'i') RETURNING id"))
    out["prices"] = len(pricebook.run(memory, """DELETE FROM observations WHERE source = 'deal email' AND
        (item, seller, CAST(seen_at AS DATE)) IN (SELECT item, seller, CAST(seen_at AS DATE) FROM observations
         WHERE source = 'deal email' GROUP BY 1, 2, 3 HAVING COUNT(DISTINCT price) > 2) RETURNING id"""))
    # Not fares at all. The price each one left in the history goes with it, so a route's history holds only fares.
    usual = pricebook.run(memory, "DELETE FROM deals WHERE source LIKE 'email:%' AND regexp_matches(title, "
                                  "'(usually|typically|normally) (costs?|books?)', 'i') RETURNING item, price, store")
    wrong = pricebook.run(memory, "DELETE FROM deals WHERE source LIKE 'email:%' AND domain = 'flight' AND "
                                  "regexp_matches(item, '^[a-z]{3}-[a-z]{3}$') AND NOT regexp_matches("
                                  f"title || ' ' || store, '\\b({TRAVEL_WORDS}|fares?|airfare|round[- ]?trip|one[- ]?way|"
                                  "nonstop|economy)\\b', 'i') RETURNING item, price, store")
    for r in usual + wrong:
        pricebook.run(memory, "DELETE FROM observations WHERE source = 'deal email' AND domain = 'flight' AND item = ? "
                              "AND price = ? AND seller = ?", (r["item"], r["price"], r["store"]))
    out["not_offers"], out["not_flights"] = len(usual), len(wrong)
    # Fares filed under their sender as if it were an airline ("going", "deal site"): filed under no airline.
    from . import productid  # noqa: PLC0415
    out["not_airlines"] = 0
    for table in ("deals", "observations"):
        for r in pricebook.run(memory, f"SELECT DISTINCT brand FROM {table} WHERE domain = 'flight' AND brand <> ''"):
            if not productid.is_airline(r["brand"]):
                out["not_airlines"] += len(pricebook.run(
                    memory, f"UPDATE {table} SET brand = '' WHERE domain = 'flight' AND brand = ? RETURNING id", (r["brand"],)))
    return out


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
