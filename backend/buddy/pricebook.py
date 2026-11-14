"""The price book: Buddy's separate database of market prices and deals (DuckDB), for history and predictions.

Raw prices and deals are kept 90 days, then deleted once the nightly learning in the sandbox (patterns.py) has
learned from them; the learned patterns (pattern_months), launches and predictions are kept for good.

What's in it (no personal data: products, routes, hotels, prices and dates):
  observations  every price Buddy sees: products (store, regular price, on sale), flights (route, travel date,
                airline, stops) and hotels (city or hotel, check-in, nights), with when and where it was seen
  deals         every deal read from the deal feeds (title, price, store, link, when)
  predictions   Buddy's buy-now-or-wait calls (the item, its price, the price worth waiting for, the date) and how
                each turned out, to measure and improve the calls

What stays in Buddy's encrypted personal database: what the owner wants, considers, bought and thinks of it, their
profile and price watches. Those only point into the price book by the item's name.

One file next to Buddy's database (pricebook.duckdb, or BUDDY_PRICEBOOK), not encrypted: it holds only market data,
so it can be copied, exported (CSV or Parquet) and analysed with any tool. Note that the routes, dates and hotels
Buddy looked up do show which trips were searched. DuckDB is a columnar file built for fast history queries; it is
kept small in memory (256 MB cap, one thread) for Buddy's server. The nightly backup includes it.

Older history from Buddy's personal database (price_seen, deals, buy_predictions, travel_prices) is copied in once.
"""

import csv
import json
import logging
import os
import re
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

log = logging.getLogger(__name__)

SCHEMA = """
CREATE SEQUENCE IF NOT EXISTS obs_id;
CREATE TABLE IF NOT EXISTS observations (
    id BIGINT DEFAULT nextval('obs_id') PRIMARY KEY,
    seen_at TIMESTAMP NOT NULL,       -- UTC
    domain VARCHAR NOT NULL,          -- product | flight | hotel
    item VARCHAR NOT NULL,            -- product name, MIA-LIM, hotels:<place> (lower case, one space)
    price DOUBLE NOT NULL,
    currency VARCHAR DEFAULT 'USD',
    seller VARCHAR DEFAULT '',        -- store, airline or booking site
    regular_price DOUBLE,
    on_sale BOOLEAN,
    url VARCHAR DEFAULT '',
    source VARCHAR DEFAULT '',        -- what saw it: price watch, deals collector, google shopping, best buy...
    travel_date DATE,                 -- flights: departure; hotels: check-in
    return_date DATE,
    stops INTEGER,
    nights INTEGER
);
CREATE INDEX IF NOT EXISTS obs_item ON observations (domain, item, seen_at);
CREATE SEQUENCE IF NOT EXISTS deal_id;
CREATE TABLE IF NOT EXISTS deals (
    id BIGINT DEFAULT nextval('deal_id') PRIMARY KEY,
    seen_on DATE NOT NULL,
    source VARCHAR NOT NULL,
    title VARCHAR NOT NULL,
    url VARCHAR NOT NULL UNIQUE,
    price DOUBLE,
    store VARCHAR DEFAULT '',
    kind VARCHAR DEFAULT 'other',
    posted VARCHAR DEFAULT ''
);
CREATE SEQUENCE IF NOT EXISTS pred_id;
CREATE TABLE IF NOT EXISTS predictions (
    id BIGINT DEFAULT nextval('pred_id') PRIMARY KEY,
    made_on DATE NOT NULL,
    domain VARCHAR DEFAULT 'product',
    item VARCHAR NOT NULL,
    kind VARCHAR DEFAULT 'other',
    price DOUBLE NOT NULL,
    decision VARCHAR NOT NULL,        -- buy_now | wait
    chance DOUBLE NOT NULL,
    target DOUBLE NOT NULL,
    event VARCHAR DEFAULT '',
    by_date DATE NOT NULL,
    outcome VARCHAR DEFAULT '',       -- '' open | right | wrong | unknown
    best_seen DOUBLE,
    settled_on DATE
);
CREATE TABLE IF NOT EXISTS launches (    -- product life cycles: when a model came out, at what price, and its successor
    item VARCHAR PRIMARY KEY,         -- the product, as in observations
    brand VARCHAR DEFAULT '',
    kind VARCHAR DEFAULT 'other',
    launch_date DATE,
    launch_price DOUBLE,
    successor VARCHAR DEFAULT '',
    successor_date DATE,              -- out already, or expected
    source VARCHAR DEFAULT '',
    noted_on DATE
);
CREATE TABLE IF NOT EXISTS pattern_months (  -- what the sandbox learned, one row per month (kept for good)
    month VARCHAR NOT NULL,
    name VARCHAR NOT NULL,
    data VARCHAR NOT NULL,            -- JSON (patterns.py merges the months)
    fitted_at VARCHAR,
    PRIMARY KEY (month, name)
);
CREATE TABLE IF NOT EXISTS meta (key VARCHAR PRIMARY KEY, value VARCHAR);
"""

_lock = threading.RLock()
_open: dict[str, object] = {}


def key(text: str) -> str:
    return " ".join(str(text or "").lower().split())[:200]


def path_for(memory) -> str:
    """Where the price book lives: BUDDY_PRICEBOOK, else next to Buddy's database; in memory for test databases."""
    configured = os.getenv("BUDDY_PRICEBOOK", "")
    if configured:
        return configured
    db = str(getattr(memory, "path", "") or "")
    if not db or db == ":memory:":
        return f":memory:{id(memory)}"
    return str(Path(db).with_name("pricebook.duckdb"))


def conn(memory):
    """The price book's connection for this Buddy database (opened once, schema made, old history copied in)."""
    import duckdb  # noqa: PLC0415
    where = path_for(memory)
    with _lock:
        c = _open.get(where)
        if c is None:
            c = duckdb.connect(":memory:" if where.startswith(":memory:") else where,
                               config={"memory_limit": "256MB", "threads": 1})
            c.execute(SCHEMA)
            _open[where] = c
            try:
                import_old(memory, c)
            except Exception:
                log.exception("price book: copying the older history failed")
        return c


def run(memory, sql: str, params=()) -> list[dict]:
    with _lock:
        cur = conn(memory).cursor()
        try:
            cur.execute(sql, params)
            if cur.description is None:
                return []
            names = [d[0] for d in cur.description]
            return [dict(zip(names, row)) for row in cur.fetchall()]
        finally:
            cur.close()


def utc(value) -> datetime:
    """A naive UTC timestamp (DuckDB's TIMESTAMP), from a datetime or an ISO string."""
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    value = value or datetime.now(timezone.utc)
    return value.astimezone(timezone.utc).replace(tzinfo=None) if value.tzinfo else value


def _date(text):
    text = str(text or "")[:10]
    return text if re.match(r"\d{4}-\d{2}-\d{2}$", text) else None


def observe(memory, domain: str, item: str, price: float, *, currency: str = "USD", seller: str = "",
            regular_price=None, on_sale=None, url: str = "", source: str = "", travel_date=None, return_date=None,
            stops=None, nights=None, seen_at: datetime | None = None) -> None:
    if not item or price is None or float(price) <= 0 or domain not in ("product", "flight", "hotel"):
        return
    run(memory, "INSERT INTO observations (seen_at, domain, item, price, currency, seller, regular_price, on_sale, "
                "url, source, travel_date, return_date, stops, nights) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (utc(seen_at), domain, key(item), float(price), (currency or "USD")[:8],
         str(seller or "")[:80], regular_price, on_sale, str(url or "")[:500], str(source or "")[:40],
         _date(travel_date), _date(return_date), stops, nights))


def history(memory, item: str, domain: str = "product", days: int = 365) -> list[dict]:
    since = utc(datetime.now(timezone.utc) - timedelta(days=days))
    rows = run(memory, "SELECT seen_at, price, currency, seller, source FROM observations WHERE domain = ? AND item = ? "
                       "AND seen_at >= ? ORDER BY seen_at", (domain, key(item), since))
    if not rows and key(item):
        words = key(item).split()
        rows = run(memory, "SELECT seen_at, price, currency, seller, source FROM observations WHERE domain = ? AND "
                           + " AND ".join(["item LIKE ?"] * len(words)) + " AND seen_at >= ? ORDER BY seen_at",
                   (domain, *[f"%{w}%" for w in words], since))
    return rows


def lowest(memory, item: str, domain: str = "product") -> float | None:
    got = run(memory, "SELECT MIN(price) AS low FROM observations WHERE domain = ? AND item = ?", (domain, key(item)))
    return got[0]["low"] if got and got[0]["low"] is not None else None


def stats(memory) -> dict:
    by = run(memory, "SELECT domain, COUNT(*) AS prices, COUNT(DISTINCT item) AS items, MIN(seen_at) AS since, "
                     "MAX(seen_at) AS last FROM observations GROUP BY domain ORDER BY domain")
    deals = run(memory, "SELECT COUNT(*) AS n, MIN(seen_on) AS since FROM deals")[0]
    preds = run(memory, "SELECT outcome, COUNT(*) AS n FROM predictions GROUP BY outcome")
    return {"where": path_for(memory), "observations": [{**r, "since": str(r["since"])[:10], "last": str(r["last"])[:10]}
                                                        for r in by],
            "deals": {"count": deals["n"], "since": str(deals["since"] or "")[:10]},
            "predictions": {(r["outcome"] or "open"): r["n"] for r in preds}}


def export(memory, folder: str, fmt: str = "csv") -> list[str]:
    """Every table to CSV or Parquet files in `folder` (for analysis in other tools)."""
    fmt = "parquet" if fmt == "parquet" else "csv"
    Path(folder).mkdir(parents=True, exist_ok=True)
    out = []
    for table in ("observations", "deals", "predictions", "launches"):
        target = str(Path(folder) / f"pricebook-{table}.{fmt}")
        opts = "(FORMAT PARQUET)" if fmt == "parquet" else "(HEADER, DELIMITER ',')"
        run(memory, f"COPY {table} TO '{target}' {opts}")
        out.append(target)
    return out


def import_old(memory, c) -> None:
    """Copy the price history that lived in Buddy's personal database, once."""
    if c.execute("SELECT value FROM meta WHERE key = 'imported'").fetchone() or getattr(memory, "db", None) is None:
        return
    db = memory.db

    def rows(sql):
        try:
            return db.execute(sql).fetchall()
        except Exception:  # that table was never made
            return []
    for r in rows("SELECT what, price, currency, seller, url, seen_at FROM price_seen"):
        c.execute("INSERT INTO observations (seen_at, domain, item, price, currency, seller, url, source) "
                  "VALUES (?, 'product', ?, ?, ?, ?, ?, 'earlier history')",
                  (utc(r["seen_at"]), key(r["what"]), r["price"], r["currency"] or "USD", r["seller"], r["url"]))
    for r in rows("SELECT route, day, source, price, currency, seen_at FROM travel_prices"):
        hotel = str(r["route"]).startswith("hotels:")
        c.execute("INSERT INTO observations (seen_at, domain, item, price, currency, source, travel_date) "
                  "VALUES (?, ?, ?, ?, ?, ?, ?)",
                  (utc(r["seen_at"]), "hotel" if hotel else "flight", key(r["route"]), r["price"], r["currency"],
                   r["source"], _date(r["day"])))
    for r in rows("SELECT source, title, url, price, store, kind, posted, seen_on FROM deals"):
        c.execute("INSERT OR IGNORE INTO deals (seen_on, source, title, url, price, store, kind, posted) "
                  "VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (r["seen_on"], r["source"], r["title"], r["url"], r["price"],
                                                       r["store"], r["kind"], r["posted"]))
    for r in rows("SELECT what, kind, made_on, price, decision, chance, target, event, by_date, outcome, best_seen, "
                  "settled_on FROM buy_predictions"):
        c.execute("INSERT INTO predictions (made_on, item, kind, price, decision, chance, target, event, by_date, "
                  "outcome, best_seen, settled_on) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                  (r["made_on"], r["what"], r["kind"], r["price"], r["decision"], r["chance"], r["target"], r["event"],
                   r["by_date"], r["outcome"], r["best_seen"], r["settled_on"]))
    c.execute("INSERT INTO meta VALUES ('imported', ?)", (datetime.now(timezone.utc).isoformat(),))


def snapshot(memory, folder: str) -> str:
    """A consistent copy for the nightly backup (Parquet files), made by Buddy itself since only one program may
    have the price book open."""
    import shutil  # noqa: PLC0415
    target = Path(folder)
    tmp = target.with_name(target.name + ".new")
    shutil.rmtree(tmp, ignore_errors=True)
    run(memory, f"EXPORT DATABASE '{tmp}' (FORMAT PARQUET)")
    shutil.rmtree(target, ignore_errors=True)
    tmp.rename(target)
    return str(target)


def close_all() -> None:
    with _lock:
        for c in _open.values():
            try:
                c.close()
            except Exception:
                pass
        _open.clear()


TOOL = {
    "name": "price_book",
    "description": (
        "Buddy's separate price database (market data only: products, flights, hotels, deals, and the buy-or-wait "
        "calls with outcomes). stats: how much history there is per kind and since when. history: every price seen "
        "for an item (domain product, flight like 'mia-lim', or hotel like 'hotels:lima'), for charts or 'is this a "
        "good price?'. export: write it all to CSV or Parquet files on the server for analysis in other tools. "
        "patterns: what the nightly learning in the sandbox found (real sale discounts per kind and year, the odds "
        "of a 10% drop, price by age since launch, flight booking windows per route, hotel weekday and month "
        "patterns, how Buddy's past calls did). learn: run that learning now. Raw prices are kept 90 days; the "
        "learned patterns are kept for good."),
    "input_schema": {"type": "object", "properties": {
        "action": {"type": "string", "enum": ["stats", "history", "export", "patterns", "learn"]},
        "item": {"type": "string"}, "domain": {"type": "string", "enum": ["product", "flight", "hotel"]},
        "days": {"type": "integer"}, "format": {"type": "string", "enum": ["csv", "parquet"]}},
        "required": ["action"], "additionalProperties": False},
}


def run_tool(memory, args: dict) -> str:
    action = args.get("action")
    if action == "stats":
        return json.dumps(stats(memory), default=str)
    if action == "history":
        rows = history(memory, args.get("item", ""), args.get("domain") or "product", int(args.get("days") or 365))
        return json.dumps({"item": args.get("item", ""), "prices": [
            {"seen": str(r["seen_at"])[:16], "price": r["price"], "currency": r["currency"], "where": r["seller"]}
            for r in rows[-200:]], "count": len(rows)}, default=str)
    if action == "export":
        where = str(Path(path_for(memory)).with_name("pricebook-export")) if not path_for(memory).startswith(
            ":memory:") else "/tmp/pricebook-export"
        files = export(memory, where, args.get("format") or "csv")
        return json.dumps({"files": files, "note": "Saved on Buddy's server. Copy them off with scp."})
    if action == "patterns":
        from . import patterns  # noqa: PLC0415
        return json.dumps(patterns.summary(memory), default=str)
    from .tools import ToolError  # noqa: PLC0415
    raise ToolError("Use stats, history, export, patterns or learn.")


def _csv_rows(rows: list[dict]) -> str:
    import io  # noqa: PLC0415
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["seen_at", "price", "currency", "where"])
    for r in rows:
        w.writerow([r["seen_at"], r["price"], r["currency"], r["seller"]])
    return out.getvalue() if rows else ""
