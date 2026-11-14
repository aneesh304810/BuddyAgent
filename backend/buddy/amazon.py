"""Amazon prices, cheaply, through Buddy Connect's Shopping group.

1. Keepa (optional, paid; BUDDY_KEEPA_KEY): Amazon's own price and the 90-day average for a product, by its ASIN.
2. Google Shopping through Serper (the search key Buddy already has): the Amazon listing's price, about $0.001.
3. Neither found it: None, and the caller falls back to its web search (deals.py: a few cents).

Amazon's own pages are never scraped (their terms forbid it and they block bots). Only the product's name or ID is
sent: public words, never the owner's data.
"""

import re
import urllib.parse

from . import gateway
from .tools import ToolError

ASIN = re.compile(r"(?:/dp/|/gp/product/|/product/|\basin[:= ]\s*)([A-Z0-9]{10})\b", re.I)
AMAZON = re.compile(r"\bamazon\b|\bamzn\.(to|com)\b|\ba\.co/", re.I)
PRICE = re.compile(r"(\d[\d,]*\.?\d*)")


def is_amazon(what: str) -> bool:
    return bool(AMAZON.search(what or "") or ASIN.search(what or ""))


def asin_of(what: str) -> str:
    m = ASIN.search(what or "")
    return m.group(1).upper() if m else ""


def name_of(what: str) -> str:
    """Search words for a product: the text without links, or the words in an Amazon link's path."""
    text = what or ""
    for url in re.findall(r"https?://\S+", text):
        path = urllib.parse.urlparse(url).path
        slug = path.split("/dp/")[0].strip("/").split("/")[-1] if "/dp/" in path else ""
        text = text.replace(url, " " + slug.replace("-", " ") + " ")
    text = re.sub(r"(?i)\b(on|from|at)\s+amazon(\.com)?\b|\bamazon(\.com)?\b|\bprice\b|\bwatch\b", " ", text)
    return " ".join(text.split())[:120]


def _money(value) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    m = PRICE.search(str(value or "").replace(",", ""))
    return float(m.group(1)) if m else 0.0


def keepa(memory, asin: str, env=None, fetch=None) -> dict | None:
    """Amazon's price now (or the lowest new offer) and the 90-day average, from Keepa. Prices are in cents there;
    -1 means none. Untested against the live service until a key is added: anything unexpected returns None."""
    key = gateway._secret(env, "BUDDY_KEEPA_KEY")
    if not key or not asin:
        return None
    url = "https://api.keepa.com/product?" + urllib.parse.urlencode(
        {"key": key, "domain": 1, "asin": asin, "stats": 365})  # stats over a year: avg90, avg180, avg365
    got = gateway.call_url(memory, "keepa", [url], purpose="shopping", endpoint="/product", fetch=fetch)
    try:
        product = (got.get("products") or [])[0]
        stats = product.get("stats") or {}
        current = stats.get("current") or []
        cents = next((c for c in current[:2] if isinstance(c, (int, float)) and c > 0), 0)
        avg = (stats.get("avg90") or [0])[0]
        longer = {k: (stats.get(k) or [0])[0] for k in ("avg180", "avg365")}
    except (AttributeError, IndexError, TypeError):
        return None
    if not cents:
        return None
    note = f"90-day average ${avg / 100:.2f}" if isinstance(avg, (int, float)) and avg > 0 else ""
    dollars = {k: round(v / 100, 2) for k, v in {"avg90": avg, **longer}.items() if isinstance(v, (int, float)) and v > 0}
    return {"found": True, "price": round(cents / 100, 2), "where": "Amazon (Keepa)",
            "url": f"https://www.amazon.com/dp/{asin}", "note": note, "title": str(product.get("title") or "")[:120],
            **dollars}


def google_shopping(memory, query: str, env=None, fetch=None) -> dict | None:
    """The cheapest Amazon listing Google Shopping shows for the words, through Serper."""
    if not query or not gateway._secret(env, "BUDDY_SERPER_KEY"):
        return None
    got = gateway.call(memory, "serper_shopping", "/shopping", json_body={"q": query, "gl": "us", "num": 20},
                       env=env, fetch=fetch, purpose="shopping")
    offers = [o for o in (got or {}).get("shopping") or [] if "amazon" in str(o.get("source", "")).lower()]
    priced = [(p, o) for o in offers if (p := _money(o.get("price"))) > 0]
    if not priced:
        return None
    price, best = min(priced, key=lambda x: x[0])
    return {"found": True, "price": price, "where": "Amazon (Google Shopping)", "url": best.get("link", ""),
            "note": "", "title": str(best.get("title") or "")[:120]}


def lookup(memory, what: str, env=None, fetch=None) -> dict | None:
    """Amazon's price for a product (a link, an ASIN or a name): Keepa first, then Google Shopping; None when
    neither found it (or neither is set up)."""
    for attempt in (lambda: keepa(memory, asin_of(what), env, fetch),
                    lambda: google_shopping(memory, name_of(what), env, fetch)):
        try:
            found = attempt()
        except gateway.ConnectError:
            found = None
        if found:
            try:  # every price looked up builds Buddy's own history for the item (buy-or-wait reads it)
                from . import prices  # noqa: PLC0415
                prices.record(memory, name_of(what) or found.get("title") or what, found["price"], "USD",
                              found["where"], found.get("url", ""))
            except Exception:
                pass
            return found
    return None


TOOL = {
    "name": "amazon_price",
    "description": "Amazon's price for a product right now (a link, an ASIN, or a name), from Buddy Connect's "
    "Shopping services: Keepa (with the 90-day average) or Google Shopping. About a tenth of a cent, so use this "
    "before a web search for Amazon prices. To keep tracking a price and hear when it drops, use watch_price with "
    "the Amazon link or name: Amazon watches are checked this cheap way too.",
    "input_schema": {"type": "object", "properties": {"what": {"type": "string"}}, "required": ["what"],
                     "additionalProperties": False},
}


def run_tool(memory, args: dict) -> str:
    what = (args.get("what") or "").strip()
    if not what:
        raise ToolError("Which product? A link, an ASIN, or its name.")
    found = lookup(memory, what)
    if not found:
        return ("I couldn't get an Amazon price that way: Buddy Connect's Shopping services need the Serper key "
                "(or a Keepa key) and the product named clearly. A web search can still look it up.")
    return (f"{found.get('title') or what}: ${found['price']:.2f} at {found['where']}"
            + (f" ({found['note']})" if found.get("note") else "") + (f". {found['url']}" if found.get("url") else "."))
