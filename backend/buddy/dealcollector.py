"""The daily deals collector: every morning Buddy reads the deal sites and checks the prices of what the owner
wants, keeps every price it sees (so real price history builds up), and tells them about the deals that fit them.

  1. Deal feeds (RSS / Atom, published by the sites for readers): Slickdeals front page and popular deals,
     DealNews, and deal subreddits (r/buildapcsales, r/deals, r/frugalmalefashion). The owner can add any other
     deal site's feed ("add the feed https://..."). Each site's robots.txt is checked first and a feed it disallows
     is skipped (and shown as skipped). Read in the morning run and again every few hours through the day
     (READ_EVERY_HOURS): a feed holds only its newest items, and in a sale week those turn over many times a day,
     so one read a day sees a small part of them. The later reads only keep the deals and their prices; the price
     checks below stay once a day. A feed is a product feed or a travel feed:
     from a travel feed only fares and hotel rates priced in dollars are kept, filed as flights (by route and
     airline) and hotels (by place and chain). A feed added from the Deals page is read once first, and added
     only if it answers with a feed (try_feed); SUGGESTED_FEEDS are offered there and never added by themselves.
     A third kind is a news feed (which stores' ads are out, when a sale starts): its headlines are kept apart in
     Buddy's own database and shown as sale news. No price is taken from a headline ("the best TVs under $500" is
     not a price), and nothing from it goes into the price book.
  2. The owner's wanted things: what they said they're thinking of buying (shopping_profile), their price watches
     and their wish list get a daily price check from official sources: Google Shopping through Serper (every
     store), the Best Buy Products API (free key: the regular price and whether it's on sale) and the eBay Browse
     API (free developer account; new items only). Every price goes into Buddy's price history.
  3. What fits the owner: a deal that names something they want is a match (told the same morning, up to three a
     day, outside quiet hours); deals in the kinds and brands they like (their shopping profile, never brands they
     avoid) go into the day's "Deals for you" report. A wanted thing at its lowest price yet, or at the discount
     they wait for, is told too.

Store pages themselves are never scraped (Amazon, Walmart, Target, Costco and Best Buy's own site forbid it).
Only feed addresses and product names go out; the owner's profile never does. Buddy never buys.
"""

import json
import logging
import os
import re
import urllib.parse
import urllib.robotparser
import xml.etree.ElementTree as ET  # for ParseError only: feeds are parsed with defusedxml
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

from . import gateway
from .memory import Memory
from .tools import ToolError, local_now

log = logging.getLogger(__name__)

AGENT = "Buddy personal deal reader (a few visits a day)"
START_HOUR = 9
# Between the morning runs the feeds are read again this often, between these hours of the owner's day. 0, or 24
# and more, means the morning read only (BUDDY_DEAL_FEEDS_EVERY_HOURS).
READ_FROM_HOUR, READ_UNTIL_HOUR = 7, 23
LAST_READ_KEY = "dealfeed:last_read"


def read_every_hours(env=None) -> float:
    try:
        return float((os.environ if env is None else env).get("BUDDY_DEAL_FEEDS_EVERY_HOURS", "3") or 0)
    except ValueError:
        return 3.0
MAX_ALERTS = 3
DONE_KEY, OFF_KEY, FEEDS_KEY, STATUS_KEY = "dealfeed:done", "dealfeed:off", "dealfeed:feeds", "dealfeed:status"

DEFAULT_FEEDS = [
    {"name": "Slickdeals front page", "service": "slickdeals",
     "url": "https://slickdeals.net/newsearch.php?mode=frontpage&searcharea=deals&searchin=first&rss=1"},
    {"name": "Slickdeals popular", "service": "slickdeals",
     "url": "https://slickdeals.net/newsearch.php?mode=popdeals&searcharea=deals&searchin=first&rss=1"},
    {"name": "DealNews", "service": "dealnews", "url": "https://www.dealnews.com/?rss=1"},
    {"name": "r/buildapcsales", "service": "reddit", "url": "https://www.reddit.com/r/buildapcsales/new/.rss"},
    {"name": "r/deals", "service": "reddit", "url": "https://www.reddit.com/r/deals/new/.rss"},
    {"name": "r/frugalmalefashion", "service": "reddit", "url": "https://www.reddit.com/r/frugalmalefashion/new/.rss"},
    {"name": "r/frugalfemalefashion", "service": "reddit",
     "url": "https://www.reddit.com/r/frugalfemalefashion/new/.rss"},
    {"name": "r/GameDeals", "service": "reddit", "url": "https://www.reddit.com/r/GameDeals/new/.rss"},
    {"name": "r/ToolDeals", "service": "reddit", "url": "https://www.reddit.com/r/ToolDeals/new/.rss"},
    {"name": "r/BuyItForLife", "service": "reddit", "url": "https://www.reddit.com/r/BuyItForLife/new/.rss"},
    # Addresses not confirmed when this was written: paused by themselves after 3 days of failing (see status).
    {"name": "Ben's Bargains", "service": "deal_feed", "url": "https://bensbargains.com/rss/"},
    {"name": "TechBargains", "service": "deal_feed", "url": "https://www.techbargains.com/rss.xml"},
    {"name": "Brad's Deals", "service": "deal_feed", "url": "https://www.bradsdeals.com/deals/feed"},
    {"name": "DealCatcher", "service": "deal_feed", "url": "https://www.dealcatcher.com/rss"},
    {"name": "Woot", "service": "deal_feed", "url": "https://www.woot.com/blog/feed.rss"},
]
# Offered on the Deals page under Sources, never read until the owner adds one. The addresses were not confirmed
# when this was written (the machine this was built on cannot reach them); the first two are listed in a public
# directory of feeds. That is why a feed is read once before it is added: an address that does not answer is refused.
SUGGESTED_FEEDS = [
    {"name": "The Flight Deal", "kind": "travel", "url": "https://www.theflightdeal.com/feed/",
     "about": "Fares from big US airports, each with its airline and price."},
    {"name": "Fly4free", "kind": "travel", "url": "https://www.fly4free.com/feed/",
     "about": "Flight and hotel deals worldwide. Only those priced in dollars are kept."},
    {"name": "Secret Flying", "kind": "travel", "url": "https://www.secretflying.com/feed/",
     "about": "Cheap fares and error fares."},
    {"name": "Slickdeals: Hot Deals forum", "kind": "product",
     "url": "https://slickdeals.net/newsearch.php?searchin=first&forumchoice%5B%5D=9&rss=1",
     "about": "Every deal the Slickdeals community posts, before it is voted onto the front page: many more deals, "
              "and more that are not worth it."},
    {"name": "DealNews: Black Friday", "kind": "product", "url": "https://www.dealnews.com/f1676/Black-Friday/?rss=1",
     "about": "DealNews' Black Friday section."},
    {"name": "DealNews: Electronics", "kind": "product", "url": "https://www.dealnews.com/c142/Electronics/?rss=1",
     "about": "DealNews' electronics deals: more of them than its main feed has room for."},
    {"name": "DealNews: Computers", "kind": "product", "url": "https://www.dealnews.com/c39/Computers/?rss=1",
     "about": "DealNews' computer deals."},
    {"name": "9to5Toys", "kind": "product", "url": "https://9to5toys.com/feed/",
     "about": "Electronics and home deals through the day."},
    {"name": "Slickdeals: Black Friday news", "kind": "news", "url": "https://blackfriday.slickdeals.net/feed",
     "about": "Which stores' Black Friday ads are out and when their sales start. Shown as sale news; no prices are "
              "taken from it."},
    {"name": "BlackFriday.com news", "kind": "news", "url": "https://blackfriday.com/news/feed",
     "about": "Black Friday ad alerts and sale dates. Shown as sale news; no prices are taken from it."},
    {"name": "Frugal Hotspot (Costco)", "kind": "news", "url": "https://www.frugalhotspot.com/feed/",
     "about": "A fan site, not Costco: which Costco coupon books, hot buys and sale ads are out. Shown as sale "
              "news; no prices are taken from it."},
]
KINDS = ("product", "travel", "news")
NEWS_SQL = """CREATE TABLE IF NOT EXISTS deal_news (
    url TEXT PRIMARY KEY, day TEXT NOT NULL, source TEXT NOT NULL, title TEXT NOT NULL, posted TEXT NOT NULL DEFAULT '')"""
NEWS_DAYS = 45  # how long a headline is kept


def keep_news(memory: Memory, items: list[dict], source: str) -> int:
    """A news feed's headlines into Buddy's own database (never the price book). Returns how many are new."""
    memory.db.execute(NEWS_SQL)
    today, added = local_now().date(), 0
    for it in items:
        posted = it.get("posted", "")
        try:
            posted = parsedate_to_datetime(posted).date().isoformat()
        except (TypeError, ValueError, IndexError):
            posted = posted[:10]
        added += memory.db.execute("INSERT OR IGNORE INTO deal_news (url, day, source, title, posted) VALUES (?, ?, ?, ?, ?)",
                                   (it["url"][:500], today.isoformat(), source, " ".join(it["title"].split())[:300],
                                    posted)).rowcount
    memory.db.execute("DELETE FROM deal_news WHERE day < ?", ((today - timedelta(days=NEWS_DAYS)).isoformat(),))
    memory.db.commit()
    return added


def news(memory: Memory, days: int = 21, limit: int = 25) -> list[dict]:
    """The newest sale news headlines, for the Deals page."""
    memory.db.execute(NEWS_SQL)
    since = (local_now().date() - timedelta(days=days)).isoformat()
    return [{"title": r["title"], "url": r["url"] if str(r["url"]).startswith("https://") else "", "source": r["source"],
             "day": r["posted"] or r["day"]}
            for r in memory.db.execute("SELECT * FROM deal_news WHERE day >= ? ORDER BY COALESCE(NULLIF(posted, ''), day) DESC, "
                                       "day DESC, rowid LIMIT ?", (since, int(limit)))]
FAIL_DAYS = 3  # a feed that fails this many days running is paused (shown in status; "add" it again to retry)
RETRY_EVERY = 7  # and tried again by itself every this many days, so a site that was down for a while comes back



PRICE = re.compile(r"\$\s?(\d[\d,]*(?:\.\d{2})?)")
STORE = re.compile(r"(?:\bat|@|\bfrom)\s+([A-Z][\w&.'+-]*(?:\s[A-Z][\w&.'+-]*){0,3})\s*(?:\(|$|[-–,;|])")
PAREN_STORE = re.compile(r"\(([A-Z][\w&.' -]{1,30})\)\s*$")


def _ready(memory: Memory) -> None:
    from . import pricebook  # noqa: PLC0415  deals live in the price book
    pricebook.conn(memory)


# --- feeds ----------------------------------------------------------------------------------------------------
def feeds(memory: Memory) -> list[dict]:
    try:
        saved = json.loads(memory.get(FEEDS_KEY) or "null")
    except ValueError:
        saved = None
    return saved if isinstance(saved, list) else list(DEFAULT_FEEDS)


def _service_for(url: str) -> str:
    host = urllib.parse.urlparse(url).netloc.lower()
    for name, entry in gateway.CATALOG.items():
        if any(urllib.parse.urlparse(h).netloc.lower() == host for h in entry.get("hosts", [])):
            return name
    return "deal_feed"


def add_feed(memory: Memory, url: str, name: str = "", kind: str = "product") -> str:
    url = (url or "").strip()
    if not re.match(r"https://[^\s/]+\.[^\s]+", url):
        raise ToolError("Give the feed's full https address (the site's RSS or Atom link).")
    kind = kind if kind in KINDS else "product"
    have = feeds(memory)
    if any(f["url"] == url for f in have):
        fails = _failures(memory)
        for f in have:
            if f["url"] == url:
                fails.pop(f["name"], None)
        memory.set("dealfeed:failures", json.dumps(fails))
        return "That feed is already on the list; I'll try it again tomorrow morning."
    have.append({"name": name or urllib.parse.urlparse(url).netloc, "service": _service_for(url), "url": url,
                 **({"kind": kind} if kind != "product" else {})})
    memory.set(FEEDS_KEY, json.dumps(have))
    return f"Added {have[-1]['name']}: I'll read it every morning (if its robots rules allow)."


def try_feed(memory: Memory, url: str, kind: str = "product", fetch=None) -> dict:
    """Read a feed once, before it is added: its robots rules, that it answers, that it is a feed, and how many of
    its items Buddy could keep as deals. Raises ToolError, in words, when it should not be added."""
    url = (url or "").strip()
    if not re.match(r"https://[^\s/]+\.[^\s]+", url):
        raise ToolError("Give the feed's full https address (the site's RSS or Atom link).")
    service = _service_for(url)
    try:
        if not allowed(memory, service, url, fetch):
            raise ToolError(f"Not added: {robots_said(memory, url)}.")
        items = parse_feed(_get(memory, service, url, fetch) or b"")
    except (gateway.ConnectError, ET.ParseError, ValueError) as e:  # ValueError: XML defusedxml refused
        raise ToolError(f"I could not read that feed ({str(e)[:120]}), so I did not add it.") from e
    if not items:
        raise ToolError("That address answered, but not with a feed that has items in it, so I did not add it.")
    if kind == "news":  # headlines, shown as they are: none of them is a deal with a price
        return {"items": len(items), "deals": 0, "priced": 0, "news": len(items),
                "examples": [" ".join(it["title"].split())[:120] for it in items[:3]]}
    deals = [d for d in (deal_from(it, "test", kind) for it in items) if d]
    return {"items": len(items), "deals": len(deals), "priced": sum(1 for d in deals if d["price"]),
            "examples": [d["title"][:120] for d in deals[:3]]}


def sources(memory: Memory, env=None) -> dict:
    """Where deals come from, for the Deals page: every feed with what it said the last morning, the feeds on
    offer that are not added, and the other ways deals arrive with whether each is set up."""
    from . import dealmail  # noqa: PLC0415
    status = page_status(memory)
    said = {line.partition(": ")[0]: line.partition(": ")[2] for line in status["feeds"]}
    have = feeds(memory)
    urls = {f["url"] for f in have}
    return {"last_run": status["last_run"], "off": status["off"], "reads": status["reads"],
            "last_read": status["last_read"], "every_hours": status["every_hours"],
            "feeds": [{"name": f["name"], "url": f["url"], "kind": f.get("kind", "product"),
                       "said": said.get(f["name"], "")} for f in have],
            "suggested": [s for s in SUGGESTED_FEEDS if s["url"] not in urls],
            "other": [
                {"name": "Deals inbox", "on": dealmail.configured(env),
                 "about": "Newsletters sent to the address you gave Buddy for deals. Stores, airlines and hotel "
                          "chains send their Black Friday and holiday offers this way. Read every night."},
                {"name": "Marketing mail in your email accounts", "on": dealmail.all_mail_on(env),
                 "about": "Offers already arriving in your own inboxes. Read every night."},
                {"name": "Best Buy", "on": bool(gateway._secret(env, "BUDDY_BESTBUY_KEY")),
                 "about": "Daily prices for what you want, with the regular price. Needs a free Best Buy key."},
                {"name": "eBay", "on": bool(gateway._secret(env, "BUDDY_EBAY_CLIENT_ID") and gateway._secret(env, "BUDDY_EBAY_CLIENT_SECRET")),
                 "about": "Daily prices for what you want. Needs a free eBay developer account."},
                {"name": "Google Shopping", "on": bool(gateway._secret(env, "BUDDY_SERPER_KEY")),
                 "about": "Daily prices for what you want, at every store. Uses your Serper credits."}]}


def remove_feed(memory: Memory, which: str) -> str:
    if not (which or "").strip():  # every feed's name holds the empty word: that would remove them all
        raise ToolError("Say which feed to remove: its name or its address.")
    have = feeds(memory)
    keep = [f for f in have if which.lower() not in (f["name"] + " " + f["url"]).lower()]
    if len(keep) == len(have):
        raise ToolError("No feed by that name.")
    memory.set(FEEDS_KEY, json.dumps(keep))
    return f"Removed {len(have) - len(keep)} feed{'s' if len(have) - len(keep) != 1 else ''}."


def _get(memory: Memory, service: str, url: str, fetch=None) -> bytes:
    return gateway.call_url(memory, service, [url], headers={"User-Agent": AGENT}, raw=True, fetch=fetch,
                            purpose="deals")


def allowed(memory: Memory, service: str, url: str, fetch=None) -> bool:
    """The site's robots.txt allows reading this address (kept for the day). No robots.txt: allowed."""
    parts = urllib.parse.urlparse(url)
    key = f"dealfeed:robots:{parts.netloc}:{local_now().date().isoformat()}"
    rules = memory.get(key)
    if rules is None:
        try:
            rules = (_get(memory, service, f"{parts.scheme}://{parts.netloc}/robots.txt", fetch) or b"").decode(
                "utf-8", "replace")
        except gateway.ConnectError as e:
            # No robots.txt: allowed. Any other answer (refused, down): its rules are unknown, so nothing is read.
            rules = "" if getattr(e, "status", 0) in (404, 410) else UNREAD + "\nUser-agent: *\nDisallow: /"
        memory.set(key, rules)
    rp = urllib.robotparser.RobotFileParser()
    rp.parse(rules.splitlines())
    return rp.can_fetch(AGENT, url)


UNREAD = "# robots.txt could not be read"


def robots_said(memory: Memory, url: str) -> str:
    """Why a feed was not read for its site's robots rules: they forbid it, or they could not be read at all (the
    site refused or did not answer, and Buddy reads nothing it has not been told it may)."""
    parts = urllib.parse.urlparse(url)
    rules = memory.get(f"dealfeed:robots:{parts.netloc}:{local_now().date().isoformat()}") or ""
    return "the site would not show its robots rules, so Buddy did not read it" if rules.startswith(UNREAD) \
        else "the site's robots rules don't allow it"


def _text(el, *names) -> str:
    for n in names:
        found = el.find(n)
        if found is not None:
            if found.text and found.text.strip():
                return found.text.strip()
            if found.get("href"):
                return found.get("href")
    return ""


def parse_feed(raw: bytes) -> list[dict]:
    """RSS 2.0 or Atom: title, link, posted, summary."""
    from defusedxml import ElementTree as SafeET  # noqa: PLC0415  feeds are untrusted XML
    root = SafeET.fromstring(raw)
    atom = "{http://www.w3.org/2005/Atom}"
    out = []
    for it in root.iter("item"):
        out.append({"title": _text(it, "title"), "url": _text(it, "link"), "posted": _text(it, "pubDate"),
                    "summary": re.sub(r"<[^>]+>", " ", _text(it, "description"))[:400]})
    for it in root.iter(f"{atom}entry"):
        out.append({"title": _text(it, f"{atom}title"), "url": _text(it, f"{atom}link"),
                    "posted": _text(it, f"{atom}updated", f"{atom}published"),
                    "summary": re.sub(r"<[^>]+>", " ", _text(it, f"{atom}content", f"{atom}summary"))[:400]})
    return [o for o in out if o["title"] and o["url"]]


# A place name as headlines write it: up to four capitalised words, with the small words some names carry
# ("Rio de Janeiro"). "Cheap flights from Boston to Dublin" gives Boston and Dublin, not "Cheap flights from Boston".
_PLACE = r"(?:[A-Z][\w.'’]*(?:-[A-Z][\w.'’]*)*(?:\s(?:de|del|da|do|la|el|las|los))?\s?){1,4}"
FROM_TO = re.compile(rf"({_PLACE})\s+to\s+({_PLACE})")
DASHED = re.compile(rf"({_PLACE})\s*[–—]\s*({_PLACE})")  # "Delta: New York – Barcelona, Spain. $386"


def _travel(title: str, summary: str = "") -> dict | None:
    """A travel feed's item as a flight or a hotel deal: {"domain", "item", "airline"}, or None when it is neither
    (a travel site's feed also carries articles). The same words decide as in the deals inbox (dealmail)."""
    from . import dealmail, productid  # noqa: PLC0415
    if dealmail.NOT_AN_OFFER.search(title):
        return None
    codes = dealmail.ROUTE.search(title)
    cities = None if codes else FROM_TO.search(title) or DASHED.search(title)
    if dealmail.FLIGHT.search(f"{title} {summary}") or codes or cities:
        if not (codes or cities):
            return None  # it speaks of fares but names no route: nothing to file it under
        item = f"{codes.group(1)}-{codes.group(2)}" if codes else f"{cities.group(1).strip()}-{cities.group(2).strip()}"
        return {"domain": "flight", "item": item.lower(), "airline": productid._named(productid.AIRLINES, title)}
    if dealmail.HOTEL.search(title):
        place = dealmail.PLACE.search(title)
        name = re.split(r"\s+(?:for|from|starting at|at|now)\s+(?:only\s+|just\s+)?\$", title)[0]  # without its price
        return {"domain": "hotel", "item": f"hotels:{place.group(1).strip().lower()}" if place else f"hotels:{name.lower()}"[:200],
                "airline": ""}
    return None


def deal_from(item: dict, source: str, kind: str = "product") -> dict | None:
    """A feed item as a deal. From a travel feed (kind "travel") only a fare or a hotel rate priced in dollars is a
    deal: anything else gives None."""
    from . import dealmail  # noqa: PLC0415
    from .shopper import kind_of  # noqa: PLC0415
    title = " ".join(item["title"].split())
    summary = item.get("summary", "")
    price = PRICE.search(title) or PRICE.search(summary)
    store = STORE.search(title) or PAREN_STORE.search(title)
    posted = item.get("posted", "")
    try:
        posted = parsedate_to_datetime(posted).date().isoformat()
    except (TypeError, ValueError, IndexError):
        posted = posted[:10]
    amount = float(price.group(1).replace(",", "")) if price else None
    was = dealmail.REGULAR.search(title) or dealmail.REGULAR.search(summary)
    regular = float(was.group(1).replace(",", "")) if was else None
    out = {"source": source, "title": title[:300], "url": item["url"][:500], "price": amount,
           "store": (store.group(1).strip() if store else "")[:60], "kind": kind_of(title), "posted": posted,
           "domain": "product", "item": "", "regular": regular if regular and amount and regular > amount else None}
    if kind == "travel":
        found = _travel(title, summary)
        if not found or not amount:
            return None
        out.update({"domain": found["domain"], "item": found["item"], "store": found["airline"], "kind": "travel"})
    return out


def _failures(memory: Memory) -> dict:
    try:
        return json.loads(memory.get("dealfeed:failures") or "{}")
    except ValueError:
        return {}


def _note_failures(memory: Memory, status: list[str]) -> None:
    """Count days in a row each feed failed; a feed failing FAIL_DAYS days running is paused."""
    fails = _failures(memory)
    for line in status:
        name, _, said = line.partition(": ")
        if said.startswith(("couldn't read it", "paused")):   # a paused day counts too: that is how its turn comes
            fails[name] = fails.get(name, 0) + 1
        elif said.endswith(" new"):
            fails.pop(name, None)
    memory.set("dealfeed:failures", json.dumps(fails))


def read_feeds(memory: Memory, fetch=None, later: bool = False) -> tuple[list[dict], list[str]]:
    """The new deals from every feed since it was last read, and a status line per feed. later: one of the day's
    reads after the morning run; it does not count towards pausing a feed (FAIL_DAYS counts mornings)."""
    _ready(memory)
    memory.set(LAST_READ_KEY, local_now().isoformat(timespec="seconds"))
    today = local_now().date().isoformat()
    new, status = [], []
    fails = _failures(memory)
    for f in feeds(memory):
        failed = fails.get(f["name"], 0)
        if failed >= FAIL_DAYS and (failed - FAIL_DAYS) % RETRY_EVERY != RETRY_EVERY - 1:
            status.append(f"{f['name']}: paused after failing {FAIL_DAYS} days in a row (check its feed address); "
                          f"Buddy tries it again every {RETRY_EVERY} days")
            continue
        try:
            if not allowed(memory, f["service"], f["url"], fetch):
                status.append(f"{f['name']}: skipped, {robots_said(memory, f['url'])}")
                continue
            items = parse_feed(_get(memory, f["service"], f["url"], fetch) or b"")
        except (gateway.ConnectError, ET.ParseError, ValueError) as e:  # ValueError: XML defusedxml refused
            status.append(f"{f['name']}: couldn't read it ({str(e)[:80]})")
            continue
        if f.get("kind") == "news":  # sale news: kept apart, never in the price book
            status.append(f"{f['name']}: {keep_news(memory, items, f['name'])} new")
            continue
        added = 0
        from . import pricebook  # noqa: PLC0415
        from . import dealforecast  # noqa: PLC0415
        # Most of a feed's items were kept on an earlier read: ask once which, not once for each of them.
        have = {r["url"] for r in pricebook.run(memory, "SELECT url FROM deals WHERE url IN (SELECT UNNEST(CAST(? AS "
                                                        "VARCHAR[])))", ([it["url"][:500] for it in items],))} if items else set()
        for it in items:
            if it["url"][:500] in have:
                continue
            d = deal_from(it, f["name"], f.get("kind", "product"))
            if d is None:  # a travel feed's article, or a fare not priced in dollars: not a deal
                continue
            travel = d["domain"] != "product"
            d["reason"] = dealforecast.reason_of(d["title"], it.get("summary", ""), kind=d["kind"])
            got = pricebook.run(memory, "INSERT OR IGNORE INTO deals (seen_on, source, title, url, price, store, kind, "
                                        "posted, reason, model, domain, item, regular_price) VALUES (?, ?, ?, ?, ?, ?, ?, "
                                        "?, ?, ?, ?, ?, ?) RETURNING id",
                                (today, d["source"], d["title"], d["url"], d["price"], d["store"], d["kind"],
                                 d["posted"], d["reason"], dealforecast.model_key(d["item"] if travel else d["title"]),
                                 d["domain"], d["item"], d["regular"]))
            if got:
                added += 1
                new.append({**d, "id": got[0]["id"]})
                if d["price"]:  # every deal's price is history too
                    # A fare with no airline named is filed under none: the feed's own name is not an airline.
                    pricebook.observe(memory, d["domain"], d["item"] if travel else d["title"], d["price"],
                                      seller=d["store"] or ("deal feed" if travel else ""), url=d["url"],
                                      regular_price=d["regular"], on_sale=True if d["regular"] else None,
                                      source=f["name"], title=d["title"])
        status.append(f"{f['name']}: {added} new")
    if not later:
        _note_failures(memory, status)
    if new:  # filed under their products here and now, so that no page has to do it before it can answer
        from . import pricebook  # noqa: PLC0415
        pricebook.identify(memory)
    return new, status


def _day_status(memory: Memory, status: list[str], added: int) -> dict:
    """The day's status with one more read folded in: each feed's count is the day's total, not the last read's."""
    try:
        kept = json.loads(memory.get(STATUS_KEY) or "{}") or {}
    except ValueError:
        kept = {}
    now = local_now()
    if kept.get("day") != now.date().isoformat():
        kept = {}
    before = {}
    for line in kept.get("feeds") or []:
        name, _, said = str(line).partition(": ")
        m = re.fullmatch(r"(\d+) new", said)
        if m:
            before[name] = int(m.group(1))
    lines = []
    for line in status:
        name, _, said = line.partition(": ")
        m = re.fullmatch(r"(\d+) new", said)
        lines.append(f"{name}: {before.get(name, 0) + int(m.group(1))} new" if m else
                     f"{name}: {before[name]} new" if name in before else line)  # it answered earlier today
    return {**kept, "day": now.date().isoformat(), "feeds": lines, "new_deals": int(kept.get("new_deals") or 0) + added,
            "reads": int(kept.get("reads") or 0) + 1, "last_read": now.strftime("%H:%M")}


def later_due(memory: Memory, now: datetime | None = None, env=None) -> bool:
    """Is it time to read the feeds again, between the morning runs?"""
    now = now or local_now()
    every = read_every_hours(env)
    if memory.get(OFF_KEY) == "1" or not 0 < every < 24 or not READ_FROM_HOUR <= now.hour < READ_UNTIL_HOUR:
        return False
    if due(memory, now):
        return False  # the morning run is due, and it reads the feeds itself
    try:
        last = datetime.fromisoformat(memory.get(LAST_READ_KEY) or "")
    except ValueError:
        return True
    return (now - last).total_seconds() >= every * 3600


def read_later(memory: Memory, fetch=None) -> dict:
    """One of the day's later reads: the feeds only. New deals and their prices are kept, and deals the owner asked
    Buddy to watch for are noted for the morning. No price checks (they use paid credits and stay once a day) and
    nothing is told."""
    new, status = read_feeds(memory, fetch, later=True)
    try:
        from . import dealforecast  # noqa: PLC0415
        dealforecast.check(memory, new)
    except Exception:
        log.warning("watched deals could not be checked after a later read", exc_info=True)
    memory.set(STATUS_KEY, json.dumps(_day_status(memory, status, len(new))))
    return {"new": len(new), "status": status}


def page_status(memory: Memory) -> dict:
    """For the Deals page: when the feeds and the deals inbox were last read and what each one said, so an empty
    page can say why it is empty."""
    def kept(key):
        try:
            return json.loads(memory.get(key) or "null")
        except ValueError:
            return None
    feeds_run, inbox = kept(STATUS_KEY) or {}, kept("dealmail:status")
    return {"off": memory.get(OFF_KEY) == "1", "last_run": feeds_run.get("day") or "",
            "feeds": [str(line)[:200] for line in feeds_run.get("feeds") or []],
            "new_deals": feeds_run.get("new_deals"), "starts_at_hour": START_HOUR,
            "reads": int(feeds_run.get("reads") or 0), "last_read": str(feeds_run.get("last_read") or ""),
            "every_hours": read_every_hours(),
            "inbox": json.dumps(inbox)[:300] if inbox else ""}


# --- what the owner wants -------------------------------------------------------------------------------------
def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]{3,}", (text or "").lower())} - {
        "the", "and", "for", "with", "new", "free", "shipping", "off", "deal", "sale", "pack"}


def wanted(memory: Memory) -> list[dict]:
    """Things the owner wants: considering, price watches, wish list (name, kind, a price they mentioned)."""
    out = []
    queries = (("SELECT item AS name, kind, NULL AS price FROM shop_considering WHERE bought_on IS NULL", "considering"),
               ("SELECT what AS name, '' AS kind, target AS price FROM price_watches WHERE until > ?", "price watch"),
               ("SELECT item AS name, '' AS kind, price FROM fin_wishes", "wish list"))
    for sql, why in queries:
        try:
            args = (datetime.now(timezone.utc).isoformat(),) if "until" in sql else ()
            out += [{**dict(r), "why": why} for r in memory.db.execute(sql, args)]
        except Exception:
            continue
    seen, uniq = set(), []
    for w in out:
        k = " ".join(sorted(_words(w["name"])))
        if k and k not in seen:
            seen.add(k)
            uniq.append(w)
    return uniq


def matches(deal: dict, want: dict) -> bool:
    need = _words(want["name"])
    have = _words(deal["title"])
    return bool(need) and len(need & have) >= max(1, min(len(need), 3) if len(need) <= 3 else 3)


def likes(memory: Memory) -> dict:
    """Kinds and brands the owner likes or avoids, from their shopping profile."""
    from .shopper import KINDS, profile  # noqa: PLC0415
    out = {"brands": set(), "avoid": set(), "kinds": set()}
    for k in KINDS:
        prof, done = profile(memory, k)
        out["brands"] |= {b.lower() for b in prof.get("trusted_brands", [])}
        out["avoid"] |= {b.lower() for b in prof.get("avoid_brands", [])}
        if (prof or done) and k != "general":
            out["kinds"].add(k)
    return out


def for_you(memory: Memory, deals: list[dict]) -> list[dict]:
    """Deals in kinds and brands they like (never brands they avoid), best first."""
    taste = likes(memory)
    picked = []
    for d in deals:
        low = d["title"].lower()
        if any(b and b in low for b in taste["avoid"]):
            continue
        score = (2 if any(b and re.search(rf"\b{re.escape(b)}\b", low) for b in taste["brands"]) else 0) + \
                (1 if d["kind"] in taste["kinds"] else 0)
        if score:
            picked.append({**d, "score": score})
    return sorted(picked, key=lambda d: -d["score"])[:10]


# --- daily price checks for wanted things ----------------------------------------------------------------------
def google_shopping(memory: Memory, name: str, env=None, fetch=None) -> list[dict]:
    got = gateway.call(memory, "serper_shopping", "/shopping", json_body={"q": name, "gl": "us", "num": 20},
                       env=env, fetch=fetch, purpose="deals") or {}
    out = []
    for o in got.get("shopping") or []:
        p = PRICE.search(str(o.get("price", "")))
        if p:
            out.append({"price": float(p.group(1).replace(",", "")), "store": str(o.get("source", ""))[:60],
                        "url": o.get("link", ""), "title": str(o.get("title", ""))[:200]})
    return out


def bestbuy(memory: Memory, name: str, env=None, fetch=None) -> list[dict]:
    key = gateway._secret(env, "BUDDY_BESTBUY_KEY")
    words = [w for w in re.findall(r"[A-Za-z0-9-]+", name) if len(w) > 1][:6]
    if not key or not words:
        return []
    search = "&".join(f"search={urllib.parse.quote(w)}" for w in words)
    q = urllib.parse.urlencode({"apiKey": key, "format": "json", "pageSize": 5,
                                "show": "sku,name,salePrice,regularPrice,onSale,url,priceUpdateDate"})
    got = gateway.call(memory, "bestbuy", f"/v1/products(({search}))?{q}", env=env, fetch=fetch,
                       purpose="deals") or {}
    return [{"price": float(p["salePrice"]), "store": "Best Buy", "url": p.get("url", ""),
             "title": str(p.get("name", ""))[:200], "regular": p.get("regularPrice"), "on_sale": p.get("onSale")}
            for p in got.get("products") or [] if p.get("salePrice")]


def _ebay_token(memory: Memory, env=None, fetch=None) -> str:
    import base64  # noqa: PLC0415
    saved = json.loads(memory.get("dealfeed:ebay_token") or "{}")
    if saved.get("token") and saved.get("until", 0) > datetime.now(timezone.utc).timestamp() + 60:
        return saved["token"]
    cid, secret = gateway._secret(env, "BUDDY_EBAY_CLIENT_ID"), gateway._secret(env, "BUDDY_EBAY_CLIENT_SECRET")
    if not (cid and secret):
        return ""
    body = urllib.parse.urlencode({"grant_type": "client_credentials",
                                   "scope": "https://api.ebay.com/oauth/api_scope"}).encode()
    got = gateway.call_url(memory, "ebay", ["https://api.ebay.com/identity/v1/oauth2/token"], data=body,
                           headers={"Authorization": "Basic " + base64.b64encode(f"{cid}:{secret}".encode()).decode(),
                                    "Content-Type": "application/x-www-form-urlencoded"},
                           fetch=fetch, purpose="deals", endpoint="/identity/v1/oauth2/token") or {}
    token = got.get("access_token", "")
    if token:
        memory.set("dealfeed:ebay_token", json.dumps(
            {"token": token, "until": datetime.now(timezone.utc).timestamp() + int(got.get("expires_in", 3600))}))
    return token


def ebay(memory: Memory, name: str, env=None, fetch=None) -> list[dict]:
    token = _ebay_token(memory, env, fetch)
    if not token:
        return []
    q = urllib.parse.urlencode({"q": name, "limit": 10, "filter": "conditions:{NEW}"})
    got = gateway.call_url(memory, "ebay", [f"https://api.ebay.com/buy/browse/v1/item_summary/search?{q}"],
                           headers={"Authorization": f"Bearer {token}", "X-EBAY-C-MARKETPLACE-ID": "EBAY_US"},
                           fetch=fetch, purpose="deals", endpoint="/buy/browse/v1/item_summary/search") or {}
    return [{"price": float(i["price"]["value"]), "store": "eBay", "url": i.get("itemWebUrl", ""),
             "title": str(i.get("title", ""))[:200]}
            for i in got.get("itemSummaries") or [] if (i.get("price") or {}).get("value")]


def check_prices(memory: Memory, env=None, fetch=None) -> list[dict]:
    """Today's best price for each wanted thing, from every connected source; all of them go into the price
    history. Returns what's worth telling: a new low (5% under the best seen before) or a sale price."""
    from . import prices  # noqa: PLC0415
    from .budgets import BudgetStop  # noqa: PLC0415
    news = []
    for w in wanted(memory)[:15]:
        offers = []
        for source in (google_shopping, bestbuy, ebay):
            try:
                offers += [o for o in source(memory, w["name"], env, fetch) if _close(w["name"], o["title"])]
            except (gateway.ConnectError, BudgetStop, KeyError, ValueError, TypeError) as e:
                log.info("deal price check %s: %s", source.__name__, e)
        if not offers:
            continue
        before = _lowest(memory, w["name"])
        for o in offers:
            prices.record(memory, w["name"], o["price"], "USD", o["store"], o["url"], source="daily check",
                          regular_price=o.get("regular"), on_sale=o.get("on_sale"))
        best = min(offers, key=lambda o: o["price"])
        if before and best["price"] <= before * 0.95:
            news.append({**best, "want": w, "why": f"lowest yet: was ${before:,.2f}"})
        elif best.get("on_sale") and best.get("regular") and best["price"] <= float(best["regular"]) * 0.85:
            news.append({**best, "want": w, "why": f"on sale from ${float(best['regular']):,.2f}"})
    return news


def _close(name: str, title: str) -> bool:
    need = _words(name)
    return bool(need) and len(need & _words(title)) >= max(1, min(len(need), 2))


def _lowest(memory: Memory, name: str) -> float | None:
    from . import pricebook  # noqa: PLC0415
    return pricebook.lowest(memory, name)


# --- the catalog: everything that keeps showing up, tracked without being asked -----------------------------------
CATALOG_SIZE = int(os.getenv("BUDDY_CATALOG_SIZE", "150"))    # products followed
CATALOG_DAILY = int(os.getenv("BUDDY_CATALOG_DAILY", "40"))   # price checks a day (about $0.001 each)
AUTO_ROUTES = 10


def catalog(memory: Memory, size: int = 0) -> list[dict]:
    """Products worth following: every model seen on deal on 2 or more days in the last 60, most seen first."""
    from . import pricebook  # noqa: PLC0415
    since = (local_now().date() - timedelta(days=60)).isoformat()
    return pricebook.run(memory, "SELECT model, MAX(title) AS title, COUNT(DISTINCT seen_on) AS days FROM deals "
                                 "WHERE COALESCE(domain, 'product') = 'product' AND COALESCE(model, '') != '' "
                                 "AND length(model) >= 5 AND seen_on >= ? GROUP BY model HAVING COUNT(DISTINCT seen_on) "
                                 f">= 2 ORDER BY days DESC, model LIMIT {int(size or CATALOG_SIZE)}", (since,))


def check_catalog(memory: Memory, env=None, fetch=None, budget: int = 0) -> int:
    """Today's share of the catalog gets a price check (Google Shopping), in rotation, so every product is checked
    every few days and has a steady price history, on deal or not. Returns how many were checked."""
    from . import dealforecast, prices  # noqa: PLC0415
    from .budgets import BudgetStop  # noqa: PLC0415
    items = catalog(memory)
    if not items or not gateway._secret(env, "BUDDY_SERPER_KEY"):
        return 0
    budget = budget or CATALOG_DAILY
    start = int(memory.get("dealfeed:catalog_at") or 0) % len(items)
    todo = (items[start:] + items[:start])[:budget]
    memory.set("dealfeed:catalog_at", str((start + len(todo)) % len(items)))
    done = 0
    for it in todo:
        try:
            offers = [o for o in google_shopping(memory, it["model"], env, fetch) if _close(it["model"], o["title"])]
        except (gateway.ConnectError, BudgetStop) as e:
            log.info("catalog check stopped: %s", e)
            break
        done += 1
        for o in offers[:6]:
            prices.record(memory, it["model"], o["price"], "USD", o["store"], o["url"], source="catalog check")
        if offers:
            best = min(offers, key=lambda o: o["price"])
            dealforecast.check(memory, [{"title": it["model"] + " " + best["title"], "price": best["price"],
                                         "store": best["store"], "url": best["url"], "source": "catalog check"}])
    return done


def auto_routes(memory: Memory, env=None) -> int:
    """Flight routes that keep showing up in fare emails and notifications are followed daily (free, through
    Travelpayouts) for this month and the next two. Returns how many routes are followed this way."""
    from . import pricebook  # noqa: PLC0415
    from .travel import Travel  # noqa: PLC0415
    trips = Travel(memory, env)
    if not trips.tp.configured():
        return 0
    since = (local_now().date() - timedelta(days=45)).isoformat()
    routes = pricebook.run(memory, "SELECT item, COUNT(*) AS n FROM deals WHERE domain = 'flight' AND seen_on >= ? "
                                   "AND regexp_matches(item, '^[a-z]{3}-[a-z]{3}$') GROUP BY item ORDER BY n DESC "
                                   f"LIMIT {AUTO_ROUTES}", (since,))
    month = local_now().date().replace(day=1)
    for r in routes:
        o, d = r["item"].upper().split("-")
        m = month
        for _ in range(3):
            try:
                trips.watch(o, d, m.strftime("%Y-%m"))
            except ToolError:
                break
            m = (m + timedelta(days=32)).replace(day=1)
    return len(routes)


# --- the daily run --------------------------------------------------------------------------------------------
def due(memory: Memory, now: datetime | None = None) -> bool:
    now = now or local_now()
    return memory.get(OFF_KEY) != "1" and now.hour >= START_HOUR and memory.get(DONE_KEY) != now.date().isoformat()


def run(memory: Memory, env=None, fetch=None) -> dict:
    """Read the feeds, check wanted prices, pick what to tell; save the day's report. Code only (no AI)."""
    new, status = read_feeds(memory, fetch)
    want = wanted(memory)
    hits = []
    for d in new:
        w = next((w for w in want if matches(d, w)), None)
        if w:
            hits.append({**d, "want": w})
    price_news = check_prices(memory, env, fetch)
    tracked = routes = 0
    try:
        tracked = check_catalog(memory, env, fetch)
        routes = auto_routes(memory, env)
    except Exception:
        log.warning("catalog tracking failed", exc_info=True)
    try:  # every open buy-or-wait call gets its price followed, so its outcome is known and Buddy can learn
        from . import whentobuy  # noqa: PLC0415
        whentobuy.follow_open(memory, env, fetch)
    except Exception:
        log.warning("following open calls failed", exc_info=True)
    from . import dealforecast  # noqa: PLC0415  watched-for deals, from the feeds and today's price checks
    dealforecast.check(memory, new + [{"title": n["want"]["name"] + " " + n["title"], "price": n["price"],
                                       "store": n["store"], "url": n["url"], "source": "daily price check",
                                       "reason": n["why"]} for n in price_news])
    picks = for_you(memory, [d for d in new if d not in hits])
    memory.set(STATUS_KEY, json.dumps({**_day_status(memory, status, len(new)),  # with any read before the morning's
                                       "matches": len(hits), "price_news": len(price_news),
                                       "catalog": {"products_followed": len(catalog(memory)), "checked_today": tracked,
                                                   "flight_routes_followed": routes}}))
    return {"matches": hits, "price_news": price_news, "for_you": picks, "status": status, "new": len(new)}


def messages(result: dict) -> list[tuple[str, list[dict]]]:
    """(text, link cards) to send: matches and price news first, at most MAX_ALERTS."""
    out = []
    for d in result["matches"]:
        price = f" for ${d['price']:,.2f}" if d["price"] else ""
        out.append((f"🏷️ Deal on something you want ({d['want']['why']}: {d['want']['name']}): {d['title']}{price}"
                    + (f" at {d['store']}" if d["store"] else "") + f", via {d['source']}.",
                    [{"kind": "link", "title": d["title"][:80], "subtitle": d["source"], "url": d["url"]}]))
    for n in result["price_news"]:
        out.append((f"🏷️ {n['want']['name']}: ${n['price']:,.2f} at {n['store']}, {n['why']}. "
                    "Ask me 'should I buy it now?' for the call.",
                    [{"kind": "link", "title": n["title"][:80], "subtitle": n["store"], "url": n["url"]}]))
    return out[:MAX_ALERTS]


def report(result: dict) -> str:
    lines = [f"# Deals for you, {local_now():%A %B %d}", ""]
    if result["matches"] or result["price_news"]:
        lines += ["## On your list", ""]
        lines += [f"- [{d['title']}]({d['url']}) ({d['source']}) — matches *{d['want']['name']}*"
                  for d in result["matches"]]
        lines += [f"- {n['want']['name']}: **${n['price']:,.2f}** at [{n['store']}]({n['url']}) — {n['why']}"
                  for n in result["price_news"]]
        lines.append("")
    if result["for_you"]:
        lines += ["## In the kinds and brands you like", ""]
        lines += [f"- [{d['title']}]({d['url']}) ({d['source']})" for d in result["for_you"]]
        lines.append("")
    lines += ["## Sources today", ""] + [f"- {s}" for s in result["status"]]
    lines += ["", "_Deals are read from public feeds; check the price and the seller before buying. Buddy never "
              "buys._"]
    return "\n".join(lines)


def save_report(memory: Memory, result: dict) -> None:
    from .finance import purge_reports  # noqa: PLC0415
    from .jobs import SCHEMA as JOBS_SCHEMA  # noqa: PLC0415
    now = datetime.now(timezone.utc).isoformat()
    memory.db.executescript(JOBS_SCHEMA)
    purge_reports(memory)
    title = f"Deals for you, {local_now():%b %d, %Y}"
    summary = (f"{len(result['matches']) + len(result['price_news'])} on your list, "
               f"{len(result['for_you'])} in kinds and brands you like, {result['new']} new deals read.")
    memory.db.execute("DELETE FROM jobs WHERE kind = 'daily_deals' AND title = ?", (title,))
    memory.db.execute(
        "INSERT INTO jobs (kind, title, params, status, created_at, started_at, finished_at, summary, report, findings, "
        "sources, delivered) VALUES ('daily_deals', ?, '{}', 'done', ?, ?, ?, ?, ?, '[]', '[]', 1)",
        (title, now, now, now, summary, report(result)))
    memory.db.commit()


def prune(memory: Memory, days: int = 365) -> None:
    """A safety cap: deals older than six months go even if the nightly learning never ran (normally they go at 90
    days, once learned from: patterns.prune_raw)."""
    from . import pricebook  # noqa: PLC0415  the deal list is trimmed; their prices stay as history
    pricebook.run(memory, "DELETE FROM deals WHERE seen_on < ?", ((date.today() - timedelta(days=days)).isoformat(),))


TOOL = {
    "name": "daily_deals",
    "description": (
        "Buddy's daily deals collector: every morning it reads deal sites (Slickdeals, DealNews, deal subreddits and "
        "any feed the owner adds), checks today's prices for what they want (things they're considering, price "
        "watches, wish list) at Google Shopping, Best Buy and eBay, keeps every price in the price history, and "
        "tells them about matches and new lows. today: run it now and give the results (also kept in Reports). "
        "search: deals seen in the last days matching words (query, days). feeds: list the feeds; add_feed: url "
        "(and name) of a deal site's RSS or Atom feed; remove_feed: which. status: how each feed did today. "
        "on/off. read_inbox: read the deals inbox and the marketing mail in the owner's email accounts now "
        "(normally every night at 1 AM). card_offers (query?: a merchant): the offers on the owner's cards "
        "found in their card issuers' emails (spend $250, get $50 back at a store, until a date) that are "
        "still running; mention a matching one whenever they're about to buy at that store. All deals, with their price history, are on the Deals page (Buddy's menu → Deals). "
        "Deals come from public feeds and newsletters: suggest checking the price and seller; never buy for them."),
    "input_schema": {"type": "object", "properties": {
        "action": {"type": "string", "enum": ["today", "search", "feeds", "add_feed", "remove_feed", "status",
                                              "on", "off", "read_inbox", "card_offers"]},
        "query": {"type": "string"}, "days": {"type": "integer"}, "url": {"type": "string"},
        "name": {"type": "string"}, "which": {"type": "string"}},
        "required": ["action"], "additionalProperties": False},
}


def search(memory: Memory, query: str, days: int = 14) -> list[dict]:
    from . import pricebook  # noqa: PLC0415
    since = (local_now().date() - timedelta(days=max(1, min(days, 180)))).isoformat()
    want = _words(query)
    rows = [{**r, "seen_on": str(r["seen_on"])} for r in pricebook.run(
        memory, "SELECT title, url, price, store, source, seen_on FROM deals WHERE seen_on >= ? ORDER BY id DESC "
                "LIMIT 2000", (since,))]
    return [r for r in rows if want and len(want & _words(r["title"])) >= max(1, min(len(want), 2))][:15]


def run_tool(memory: Memory, args: dict) -> str:
    action = args.get("action")
    if action == "today":
        result = run(memory)
        save_report(memory, result)
        memory.set(DONE_KEY, local_now().date().isoformat())
        return json.dumps({"on_your_list": [m["title"] for m in result["matches"]],
                           "price_news": [f"{n['want']['name']}: ${n['price']:,.2f} at {n['store']} ({n['why']})"
                                          for n in result["price_news"]],
                           "for_you": [d["title"] for d in result["for_you"][:5]], "sources": result["status"],
                           "note": "The full list with links is in Reports (Deals for you)."}, ensure_ascii=False)
    if action == "search":
        return json.dumps(search(memory, args.get("query", ""), int(args.get("days") or 14)), ensure_ascii=False)
    if action == "feeds":
        return json.dumps(feeds(memory), ensure_ascii=False)
    if action == "add_feed":
        return add_feed(memory, args.get("url", ""), args.get("name", ""))
    if action == "remove_feed":
        return remove_feed(memory, args.get("which", ""))
    if action == "status":
        return json.dumps({"feeds": json.loads(memory.get(STATUS_KEY) or "null") or "not run yet today",
                           "deals_inbox": json.loads(memory.get("dealmail:status") or "null") or "not run yet",
                           "app_notifications": json.loads(memory.get("dealnotif:status") or "null")
                           or "none read yet"})
    if action == "read_inbox":
        from . import dealmail  # noqa: PLC0415
        return dealmail.run(memory)
    if action == "card_offers":
        from . import dealmail  # noqa: PLC0415
        found = dealmail.offers(memory, args.get("query") or "")
        return json.dumps({"offers": [o["offer"] for o in found] or "None running"
                           + (f" at {args['query']}" if args.get("query") else "") + " that I've seen in your email.",
                           "note": "From your card issuers' emails. An offer usually has to be added to the card in "
                                   "the issuer's app before you pay: check there."})
    if action == "off":
        memory.set(OFF_KEY, "1")
        return "The daily deals collector is off."
    if action == "on":
        memory.set(OFF_KEY, "")
        return "The daily deals collector is on: every morning from 9."
    raise ToolError("Use today, search, feeds, add_feed, remove_feed, status, on or off.")


def check_bestbuy(memory=None, env=None, fetch=None) -> None:
    gateway.call(memory, "bestbuy", "/v1/products((search=cable))?" + urllib.parse.urlencode(
        {"apiKey": gateway._secret(env, "BUDDY_BESTBUY_KEY"), "format": "json", "pageSize": 1, "show": "sku"}),
        env=env, fetch=fetch, purpose="health")


def check_ebay(memory=None, env=None, fetch=None) -> None:
    if not _ebay_token(memory, env, fetch):
        raise gateway.ConnectError("ebay", "eBay didn't give a token: check the client id and secret.")
