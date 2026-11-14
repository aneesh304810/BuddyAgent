"""The daily deals collector: every morning Buddy reads the deal sites and checks the prices of what the owner
wants, keeps every price it sees (so real price history builds up), and tells them about the deals that fit them.

  1. Deal feeds (RSS / Atom, published by the sites for readers): Slickdeals front page and popular deals,
     DealNews, and deal subreddits (r/buildapcsales, r/deals, r/frugalmalefashion). The owner can add any other
     deal site's feed ("add the feed https://..."). Each site's robots.txt is checked first and a feed it disallows
     is skipped (and shown as skipped). Read once a day, politely.
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
import re
import urllib.parse
import urllib.robotparser
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

from . import gateway
from .memory import Memory
from .tools import ToolError, local_now

log = logging.getLogger(__name__)

AGENT = "Buddy personal deal reader (one visit a day)"
START_HOUR = 9
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
    {"name": "r/BuyItForLife", "service": "reddit", "url": "https://www.reddit.com/r/BuyItForLife/new/.rss"},
    # Addresses not confirmed when this was written: paused by themselves after 3 days of failing (see status).
    {"name": "Ben's Bargains", "service": "deal_feed", "url": "https://bensbargains.com/rss/"},
    {"name": "TechBargains", "service": "deal_feed", "url": "https://www.techbargains.com/rss.xml"},
    {"name": "Brad's Deals", "service": "deal_feed", "url": "https://www.bradsdeals.com/deals/feed"},
    {"name": "DealCatcher", "service": "deal_feed", "url": "https://www.dealcatcher.com/rss"},
    {"name": "Woot", "service": "deal_feed", "url": "https://www.woot.com/blog/feed.rss"},
]
FAIL_DAYS = 3  # a feed that fails this many days running is paused (shown in status; "add" it again to retry)



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


def add_feed(memory: Memory, url: str, name: str = "") -> str:
    url = (url or "").strip()
    if not re.match(r"https://[^\s/]+\.[^\s]+", url):
        raise ToolError("Give the feed's full https address (the site's RSS or Atom link).")
    have = feeds(memory)
    if any(f["url"] == url for f in have):
        fails = _failures(memory)
        for f in have:
            if f["url"] == url:
                fails.pop(f["name"], None)
        memory.set("dealfeed:failures", json.dumps(fails))
        return "That feed is already on the list; I'll try it again tomorrow morning."
    have.append({"name": name or urllib.parse.urlparse(url).netloc, "service": _service_for(url), "url": url})
    memory.set(FEEDS_KEY, json.dumps(have))
    return f"Added {have[-1]['name']}: I'll read it every morning (if its robots rules allow)."


def remove_feed(memory: Memory, which: str) -> str:
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
            rules = "" if getattr(e, "status", 0) in (404, 410) else "User-agent: *\nDisallow: /"
        memory.set(key, rules)
    rp = urllib.robotparser.RobotFileParser()
    rp.parse(rules.splitlines())
    return rp.can_fetch(AGENT, url)


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
    root = ET.fromstring(raw)
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


def deal_from(item: dict, source: str) -> dict:
    from .shopper import kind_of  # noqa: PLC0415
    title = " ".join(item["title"].split())
    price = PRICE.search(title) or PRICE.search(item.get("summary", ""))
    store = STORE.search(title) or PAREN_STORE.search(title)
    posted = item.get("posted", "")
    try:
        posted = parsedate_to_datetime(posted).date().isoformat()
    except (TypeError, ValueError, IndexError):
        posted = posted[:10]
    return {"source": source, "title": title[:300], "url": item["url"][:500],
            "price": float(price.group(1).replace(",", "")) if price else None,
            "store": (store.group(1).strip() if store else "")[:60], "kind": kind_of(title),
            "posted": posted}


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
        if said.startswith("couldn't read it"):
            fails[name] = fails.get(name, 0) + 1
        elif said.endswith(" new"):
            fails.pop(name, None)
    memory.set("dealfeed:failures", json.dumps(fails))


def read_feeds(memory: Memory, fetch=None) -> tuple[list[dict], list[str]]:
    """Today's new deals from every feed, and a status line per feed."""
    _ready(memory)
    today = local_now().date().isoformat()
    new, status = [], []
    fails = _failures(memory)
    for f in feeds(memory):
        if fails.get(f["name"], 0) >= FAIL_DAYS:
            status.append(f"{f['name']}: paused after failing {FAIL_DAYS} days in a row (check its feed address)")
            continue
        try:
            if not allowed(memory, f["service"], f["url"], fetch):
                status.append(f"{f['name']}: skipped, the site's robots rules don't allow it")
                continue
            items = parse_feed(_get(memory, f["service"], f["url"], fetch) or b"")
        except (gateway.ConnectError, ET.ParseError) as e:
            status.append(f"{f['name']}: couldn't read it ({str(e)[:80]})")
            continue
        added = 0
        from . import pricebook  # noqa: PLC0415
        for it in items:
            d = deal_from(it, f["name"])
            got = pricebook.run(memory, "INSERT OR IGNORE INTO deals (seen_on, source, title, url, price, store, kind, "
                                        "posted) VALUES (?, ?, ?, ?, ?, ?, ?, ?) RETURNING id",
                                (today, d["source"], d["title"], d["url"], d["price"], d["store"], d["kind"],
                                 d["posted"]))
            if got:
                added += 1
                new.append({**d, "id": got[0]["id"]})
                if d["price"]:  # every deal's price is history too
                    pricebook.observe(memory, "product", d["title"], d["price"], seller=d["store"], url=d["url"],
                                      source=f["name"])
        status.append(f"{f['name']}: {added} new")
    _note_failures(memory, status)
    return new, status


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
    picks = for_you(memory, [d for d in new if d not in hits])
    memory.set(STATUS_KEY, json.dumps({"day": local_now().date().isoformat(), "feeds": status,
                                       "new_deals": len(new), "matches": len(hits), "price_news": len(price_news)}))
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


def prune(memory: Memory, days: int = 180) -> None:
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
        "on/off. Deals come from public feeds: suggest checking the price and seller; never buy for them."),
    "input_schema": {"type": "object", "properties": {
        "action": {"type": "string", "enum": ["today", "search", "feeds", "add_feed", "remove_feed", "status",
                                              "on", "off"]},
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
        return memory.get(STATUS_KEY) or "It hasn't run yet today."
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
