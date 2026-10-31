"""Price history: every price Buddy sees for something, so "is this a good price?" has data behind it.

Rows come from price watches (each check) and from the note_price tool (a price found while
searching or read off a page). The price-history data tool turns them into lowest, typical,
trend and a buy-or-wait signal.
"""

import csv
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
           seen_at: datetime | None = None) -> None:
    if not what or price is None or float(price) <= 0:
        return
    _ready(memory)
    memory.db.execute("INSERT INTO price_seen (what, price, currency, seller, url, seen_at) VALUES (?, ?, ?, ?, ?, ?)",
                      (_key(what), float(price), str(currency)[:8], str(seller)[:80], str(url)[:300],
                       (seen_at or datetime.now(timezone.utc)).isoformat()))
    memory.db.commit()


def items(memory: Memory) -> list[dict]:
    _ready(memory)
    return [dict(r) for r in memory.db.execute(
        "SELECT what, COUNT(*) AS seen, MIN(price) AS lowest, MAX(seen_at) AS last FROM price_seen "
        "GROUP BY what ORDER BY last DESC LIMIT 30")]


def history_csv(memory: Memory, what: str, days: int = 365) -> str:
    """The prices seen for `what` (exact name, else every item containing those words) as CSV."""
    _ready(memory)
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    key = _key(what)
    rows = memory.db.execute("SELECT seen_at, price, currency, seller FROM price_seen WHERE what = ? AND seen_at >= ? "
                             "ORDER BY seen_at", (key, since)).fetchall()
    if not rows and key:
        words = key.split()
        rows = memory.db.execute(
            "SELECT seen_at, price, currency, seller FROM price_seen WHERE " + " AND ".join(["what LIKE ?"] * len(words))
            + " AND seen_at >= ? ORDER BY seen_at", (*[f"%{w}%" for w in words], since)).fetchall()
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["seen_at", "price", "currency", "where"])
    for r in rows:
        w.writerow([r["seen_at"], r["price"], r["currency"], r["seller"]])
    return out.getvalue() if rows else ""


NOTE_PRICE_TOOL = {
    "name": "note_price",
    "description": "Write down a price you just found (a flight, hotel, product) in Buddy's price history, so later "
    "'is this a good price?' can be answered with process_data price-history (source: {kind: prices, item}). Use the "
    "same short name each time for the same thing, e.g. 'MIA-LIM flight March' or 'Sony WH-1000XM6'. Price watches "
    "are written down automatically.",
    "input_schema": {"type": "object", "properties": {
        "item": {"type": "string"}, "price": {"type": "number"}, "currency": {"type": "string"},
        "where": {"type": "string", "description": "Seller, airline or site"}, "url": {"type": "string"}},
        "required": ["item", "price"], "additionalProperties": False},
}


def run_note_price(memory: Memory, args: dict) -> str:
    record(memory, args.get("item", ""), float(args.get("price") or 0), args.get("currency", ""),
           args.get("where", ""), args.get("url", ""))
    return f"Noted {args.get('price')} {args.get('currency', '')} for {args.get('item', '')}.".replace("  ", " ")
