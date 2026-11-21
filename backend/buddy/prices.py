"""Price history: every price Buddy sees for something, so "is this a good price?" has data behind it.

Rows come from price watches (each check) and from the note_price tool (a price found while
searching or read off a page, or one the owner saw in a store and told Buddy). The price-history data tool turns
them into lowest, typical, trend and a buy-or-wait signal.

A sale the owner tells Buddy about (a shelf tag at a warehouse club, a markdown no site lists) also goes onto the
Deals page, beside the feeds' deals: stores such as Costco publish no feed and forbid reading their pages by
program, so what the owner saw is the one way those prices reach Buddy.
"""

import csv
import hashlib
import io
from datetime import datetime, timedelta, timezone

from .memory import Memory

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS price_seen (
    id INTEGER PRIMARY KEY,
    what TEXT NOT NULL,              -- lower case: "mia-lim flight in march", "sony wh-1000xm6"
    price REAL NOT NULL,
    currency TEXT NOT NULL DEFAULT '',
    seller TEXT NOT NULL DEFAULT '',
    url TEXT NOT NULL DEFAULT '',
    seen_at TEXT NOT NULL            -- UTC ISO
);
CREATE INDEX IF NOT EXISTS price_seen_what ON price_seen (what, seen_at);
"""


def _ready(memory: Memory) -> None:
    memory.db.executescript(SCHEMA_SQL)


def _key(what: str) -> str:
    return " ".join(str(what).lower().split())[:200]


def record(memory: Memory, what: str, price: float, currency: str = "", seller: str = "", url: str = "",
           seen_at: datetime | None = None, source: str = "", regular_price=None, on_sale=None) -> None:
    """A product price seen: into the price book (pricebook.py), Buddy's separate market-price database."""
    if not what or price is None or float(price) <= 0:
        return
    from . import pricebook  # noqa: PLC0415
    pricebook.observe(memory, "product", what, float(price), currency=currency or "USD", seller=seller, url=url,
                      source=source, regular_price=regular_price, on_sale=on_sale, seen_at=seen_at)


def items(memory: Memory) -> list[dict]:
    from . import pricebook  # noqa: PLC0415
    return [{"what": r["item"], "seen": r["seen"], "lowest": r["lowest"], "last": str(r["last"])} for r in pricebook.run(
        memory, "SELECT item, COUNT(*) AS seen, MIN(price) AS lowest, MAX(seen_at) AS last FROM observations "
                "WHERE domain = 'product' GROUP BY item ORDER BY last DESC LIMIT 30")]


def history_csv(memory: Memory, what: str, days: int = 365) -> str:
    """The prices seen for `what` (exact name, else every item containing those words) as CSV."""
    from . import pricebook  # noqa: PLC0415
    return pricebook._csv_rows(pricebook.history(memory, what, "product", days))


NOTE_PRICE_TOOL = {
    "name": "note_price",
    "description": "Write down a price you just found (a flight, hotel, product) in Buddy's price history, so later "
    "'is this a good price?' can be answered with process_data price-history (source: {kind: prices, item}). Use the "
    "same short name each time for the same thing, e.g. 'MIA-LIM flight March' or 'Sony WH-1000XM6'. Price watches "
    "are written down automatically. Also for a price the owner saw in a store and tells you ('Costco has the "
    "Dyson V8 for $249, normally $349, until the 30th'): give where, and regular_price and ends when they say "
    "them. With a regular_price it is a sale, and it also shows on the Deals page under that store.",
    "input_schema": {"type": "object", "properties": {
        "item": {"type": "string"}, "price": {"type": "number"}, "currency": {"type": "string"},
        "where": {"type": "string", "description": "Seller, airline, site or store"}, "url": {"type": "string"},
        "regular_price": {"type": "number", "description": "The usual price, when this one is a sale price"},
        "ends": {"type": "string", "description": "When the sale ends, as said ('Nov 30')"}},
        "required": ["item", "price"], "additionalProperties": False},
}


def told_deal(memory: Memory, item: str, price: float, where: str, regular: float, ends: str = "", url: str = "") -> bool:
    """A sale the owner told Buddy about, onto the Deals page (once a day for the same thing, store and price)."""
    from . import dealforecast, pricebook  # noqa: PLC0415
    from .shopper import kind_of  # noqa: PLC0415
    day = datetime.now(timezone.utc).date().isoformat()
    title = " ".join(str(item).split())[:300]
    link = url if str(url).startswith("https://") else "told:" + hashlib.sha1(
        f"{where}|{title.lower()}|{price}|{day}".encode(), usedforsecurity=False).hexdigest()
    got = pricebook.run(memory, "INSERT OR IGNORE INTO deals (seen_on, source, title, url, price, store, kind, posted, "
                                "domain, item, regular_price, ends, reason, model) VALUES (?, 'you told Buddy', ?, ?, ?, ?, "
                                "?, ?, 'product', '', ?, ?, ?, ?) RETURNING id",
                        (day, title, link[:500], float(price), str(where)[:60], kind_of(title), day, float(regular),
                         str(ends)[:40], dealforecast.reason_of(title, kind=kind_of(title)), dealforecast.model_key(title)))
    if got:
        pricebook.identify(memory)
    return bool(got)


def run_note_price(memory: Memory, args: dict) -> str:
    item, price = args.get("item", ""), float(args.get("price") or 0)
    regular = float(args.get("regular_price") or 0)
    sale = bool(item and 0 < price < regular)
    record(memory, item, price, args.get("currency", ""), args.get("where", ""), args.get("url", ""),
           source="owner saw it" if sale else "", regular_price=regular if sale else None, on_sale=True if sale else None)
    said = f"Noted {args.get('price')} {args.get('currency', '')} for {item}.".replace("  ", " ")
    if sale and (args.get("currency") or "USD").upper() == "USD":
        told_deal(memory, item, price, args.get("where", ""), regular, args.get("ends", ""), args.get("url", ""))
        said += f" It is on the Deals page as a sale{' at ' + args['where'] if args.get('where') else ''}."
    return said
