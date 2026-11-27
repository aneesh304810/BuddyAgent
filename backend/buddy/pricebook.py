"""The price book: Buddy's separate database of market prices and deals (DuckDB), for history and predictions.

Raw prices and deals are kept a year, then deleted once the nightly learning in the sandbox (patterns.py) has
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

When the engine fails. DuckDB has twice stopped on this price book with an internal error of its own ("Attempted to
dereference shared_ptr that is NULL"), and after one it refuses every statement until the file is opened again.
So run() opens the price book again and asks once more; if the same statement fails the same way, it rebuilds the
file once (rebuild(): the engine's own export and import, into a new file, the old one kept beside it) and asks a
last time. check() says what can be read; deploy/pricebook.sh runs both by hand. The tables have no secondary
index: every internal error so far involved a table that had one, and the price book is small enough without.
"""

import csv
import json
import logging
import os
import re
import tempfile
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
_filing_off: set[str] = set()   # price books where filing under products failed in this process: not tried again
_keep_index: set[str] = set()   # price books where the old index could not be removed in this process
_rebuilt: set[str] = set()      # price books rebuilt in this process after a repeated internal error: once only
CONFIG = {"memory_limit": "256MB", "threads": 1}
OLD_INDEXES = ("obs_product", "obs_item")  # made by earlier versions; removed when the price book is opened


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
            c = duckdb.connect(":memory:" if where.startswith(":memory:") else where, config=CONFIG)
            c.execute(SCHEMA)
            for column in ("domain VARCHAR DEFAULT 'product'", "item VARCHAR DEFAULT ''", "regular_price DOUBLE",
                           "ends VARCHAR DEFAULT ''", "reason VARCHAR DEFAULT ''", "model VARCHAR DEFAULT ''"):  # deals from the deals inbox carry these too
                c.execute(f"ALTER TABLE deals ADD COLUMN IF NOT EXISTS {column}")
            for table in ("deals", "observations"):  # which product a price is for, its brand and its category
                for column in ("product VARCHAR", "brand VARCHAR", "category VARCHAR", "place VARCHAR"):
                    c.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column}")
            for column in ("parts VARCHAR", "travel_date DATE", "nights INTEGER", "factors VARCHAR"):  # what a
                c.execute(f"ALTER TABLE predictions ADD COLUMN IF NOT EXISTS {column}")     # buy-or-wait call keeps
            # No secondary index. Filing prices under their products rewrites a column, and rewriting an indexed
            # column made the database engine stop with an internal error on a real price book; later the history
            # query stopped it too. The price book is small enough to be read without an index.
            if where not in _keep_index:
                try:
                    for index in OLD_INDEXES:
                        c.execute(f"DROP INDEX IF EXISTS {index}")
                except Exception:
                    _keep_index.add(where)
                    log.warning("price book: an old index could not be removed", exc_info=True)
            _open[where] = c
            try:
                import_old(memory, c)
            except Exception:
                log.exception("price book: copying the older history failed")
        return c


def _stopped(error: Exception) -> bool:
    """The engine's own failure, not a mistake in the statement: its internal error, and the refusal every later
    statement gets until the database is opened again."""
    return (type(error).__name__ in ("FatalException", "InternalException")
            or "database has been invalidated" in str(error) or "INTERNAL Error" in str(error))


def _reopen(memory) -> None:
    old = _open.pop(path_for(memory), None)
    try:
        if old is not None:
            old.close()
    except Exception:
        pass


def run(memory, sql: str, params=(), heal: bool = True) -> list[dict]:
    """Run one statement and give its rows. heal=False: never rebuild the file for it (check() asks that way)."""
    with _lock:
        for attempt in (0, 1, 2):
            try:
                cur = conn(memory).cursor()
                try:
                    cur.execute(sql, params)
                    if cur.description is None:
                        return []
                    names = [d[0] for d in cur.description]
                    return [dict(zip(names, row)) for row in cur.fetchall()]
                finally:
                    try:
                        cur.close()
                    except Exception:
                        pass
            except Exception as e:
                where = path_for(memory)
                if not _stopped(e) or where.startswith(":memory:"):
                    raise
                # The engine stopped, on this statement or an earlier one: open the price book again and ask once
                # more, so one internal error does not take every page down until Buddy is restarted.
                if attempt == 0:
                    log.error("price book: the database engine stopped and was opened again: %s",
                              " ".join(str(e).split())[:300])
                    _reopen(memory)
                    continue
                # The same failure on a freshly opened file: the file itself trips the engine. Rebuild it once.
                if attempt == 1 and heal and where not in _rebuilt:
                    _rebuilt.add(where)
                    try:
                        done = rebuild(memory)
                    except Exception:
                        log.exception("price book: rebuilding the file failed; it is left as it was")
                        raise e from None
                    log.error("price book: the file was rebuilt after a repeated internal error (%s rows; the old "
                              "file is kept as %s)", sum(done["rows"].values()), done["kept"])
                    continue
                raise
        return []


def _quoted(path: str) -> str:
    return "'" + str(path).replace("'", "''") + "'"


def _counts(c) -> dict:
    tables = [r[0] for r in c.execute("SELECT table_name FROM information_schema.tables WHERE table_schema = 'main' "
                                      "AND table_type = 'BASE TABLE' ORDER BY table_name").fetchall()]
    return {t: int(c.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]) for t in tables}


def rebuild(memory) -> dict:
    """Write the price book out and read it into a new file, with the engine's own export and import: everything in
    it is kept (tables, their numbering, what was learned) and the file is laid out afresh, without the old indexes.
    For a price book on which the engine keeps stopping. The old file stays beside the new one as
    pricebook.duckdb.before-rebuild. If the old file cannot be read in full, or the copy does not hold the same
    number of rows, this raises and nothing is changed. Needs about as much free disk as the file is large."""
    import duckdb  # noqa: PLC0415
    import shutil  # noqa: PLC0415
    where = path_for(memory)
    if where.startswith(":memory:"):
        raise ValueError("A price book kept in memory has no file to rebuild.")
    with _lock:
        _reopen(memory)
        folder, fresh, kept = where + ".export", where + ".rebuilt", where + ".before-rebuild"
        shutil.rmtree(folder, ignore_errors=True)
        for leftover in (fresh, fresh + ".wal"):
            if os.path.exists(leftover):
                os.remove(leftover)
        old = duckdb.connect(where, config=CONFIG)
        try:
            before = _counts(old)
            old.execute(f"EXPORT DATABASE {_quoted(folder)} (FORMAT PARQUET)")
        finally:
            old.close()
        new = duckdb.connect(fresh, config=CONFIG)
        try:
            new.execute(f"IMPORT DATABASE {_quoted(folder)}")
            for index in OLD_INDEXES:
                new.execute(f"DROP INDEX IF EXISTS {index}")
            after = _counts(new)
            new.execute("CHECKPOINT")
        finally:
            new.close()
        if after != before:
            os.remove(fresh)
            raise RuntimeError("The rebuilt price book does not hold the same rows as the old one; nothing was changed.")
        for suffix in ("", ".wal"):
            if os.path.exists(where + suffix):
                os.replace(where + suffix, kept + suffix)
        os.replace(fresh, where)
        shutil.rmtree(folder, ignore_errors=True)
        _filing_off.discard(where)
        _keep_index.discard(where)
    return {"rows": after, "kept": kept}


def check(memory) -> dict:
    """Can the price book be read? The engine's version, the file's size, each table's rows, and the statements the
    Deals page depends on, each tried by itself. Nothing is changed and nothing is rebuilt."""
    import duckdb  # noqa: PLC0415
    where = path_for(memory)
    out = {"engine": duckdb.__version__, "file": where, "tables": {}, "queries": {}, "ok": True}
    if not where.startswith(":memory:") and os.path.exists(where):
        out["size_mb"] = round(os.path.getsize(where) / 1e6, 1)

    def tried(sql: str, params=()):
        try:
            return run(memory, sql, params, heal=False), ""
        except Exception as e:
            out["ok"] = False
            return None, f"{type(e).__name__}: {' '.join(str(e).split())[:200]}"

    names, error = tried("SELECT table_name AS t FROM information_schema.tables WHERE table_schema = 'main' AND "
                         "table_type = 'BASE TABLE' ORDER BY 1")
    if names is None:
        out["tables"] = {"(the list of tables)": error}
        return out
    for row in names:
        got, error = tried(f'SELECT COUNT(*) AS n FROM "{row["t"]}"')
        out["tables"][row["t"]] = int(got[0]["n"]) if got else error
    since = utc(datetime.now(timezone.utc) - timedelta(days=365))
    for name, sql, params in (
        ("price history", "SELECT CAST(seen_at AS DATE) AS day, MIN(price) AS price FROM observations WHERE domain = ? AND "
                          "(item = ? OR product = ? OR product = ?) AND seen_at >= ? GROUP BY day ORDER BY day",
         ("product", "x", "x", "x", since)),
        ("prices not yet filed", "SELECT COUNT(*) AS n FROM observations WHERE product IS NULL", ()),
        ("lowest prices", "SELECT domain, product, MIN(price) AS low FROM observations GROUP BY 1, 2 LIMIT 1", ()),
        ("recent deals", "SELECT id, title, product FROM deals ORDER BY seen_on DESC, id DESC LIMIT 1", ()),
        ("indexes", "SELECT index_name FROM duckdb_indexes()", ()),
    ):
        got, error = tried(sql, params)
        out["queries"][name] = error or ("ok" if name != "indexes" else ", ".join(r["index_name"] for r in got) or "none")
    return out


def check_text(memory) -> str:
    c = check(memory)
    lines = [f"Price book: {c['file']} ({c.get('size_mb', '?')} MB), database engine {c['engine']}.",
             "It can be read." if c["ok"] else "PART OF IT CANNOT BE READ: see below. To rebuild the file: "
                                               "sudo bash /opt/buddy/deploy/pricebook.sh rebuild"]
    lines += [f"  table {name}: {rows if isinstance(rows, str) else f'{rows} rows'}" for name, rows in c["tables"].items()]
    lines += [f"  {name}: {result}" for name, result in c["queries"].items()]
    return "\n".join(lines)


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
            stops=None, nights=None, seen_at: datetime | None = None, title: str = "", cabin: str = "") -> None:
    """One price seen. It is filed at once under its product, brand and category (productid.py): for a flight, the
    route, the airline and the cabin; for a hotel, the place and the chain. title: the words it came with, when
    they say more than the item (an emailed fare's headline names the airline and the cabin)."""
    if not item or price is None or float(price) <= 0 or domain not in ("product", "flight", "hotel"):
        return
    got = _identity(domain, item, seller or source, title, cabin)
    run(memory, "INSERT INTO observations (seen_at, domain, item, price, currency, seller, regular_price, on_sale, "
                "url, source, travel_date, return_date, stops, nights, product, brand, category, place) VALUES (?, ?, ?, "
                "?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (utc(seen_at), domain, key(item), float(price), (currency or "USD")[:8],
         str(seller or "")[:80], regular_price, on_sale, str(url or "")[:500], str(source or "")[:40],
         _date(travel_date), _date(return_date), stops, nights, got["product"] or key(item), got["brand"],
         got["category"], got.get("place", "")))


def _identity(domain: str, item: str, seller: str = "", title: str = "", cabin: str = "") -> dict:
    from . import productid  # noqa: PLC0415
    if domain == "product":
        return {**productid.identity(item), "place": ""}
    return productid.travel(domain, item, seller, title, cabin)


def identify(memory, limit: int = 20000) -> int:
    """File every price and deal that has no product yet under its product, brand and category (productid.py).
    Prices recorded before this existed are filed the first time this runs. Returns how many were filed."""
    where = path_for(memory)
    if where in _filing_off:
        return 0
    try:
        return _file_all(memory, limit)
    except Exception:
        # Filing is a help, not a condition: the pages list an unfiled price under its own name. It is not tried
        # again in this process, so a statement the engine cannot run does not stop the price book over and over.
        _filing_off.add(where)
        log.warning("price book: filing prices under their products failed and is off until Buddy restarts", exc_info=True)
        return 0


FILE_AT_ONCE = 2000  # names filed by one statement
# What a reader (a page, a buy-or-wait call) files before it answers, at most. What adds prices and deals files them
# as they arrive, and the server files any backlog once when it starts, so a reader normally finds nothing to file;
# this keeps a reader from waiting on a backlog all the same. What is left shows under its own name until filed.
FILE_ON_READ = 2000


def _filed(rows: list[dict], found: list[dict], *keys: str) -> tuple:
    """The lists one filing statement takes: each key's values, then the product, brand, category and place."""
    return (*[[r[k] for r in rows] for k in keys],
            [g["product"] or key(r["t"]) for r, g in zip(rows, found)], [g["brand"] for g in found],
            [g["category"] for g in found], [g.get("place", "") for g in found])


def _lists(names: tuple) -> str:
    return ", ".join(f"UNNEST(CAST(? AS VARCHAR[])) AS {n}" for n in names)


def _file_all(memory, limit: int) -> int:
    """File what is unfiled, a batch by one statement. (It was one statement for each name, each reading the whole
    table and saved on its own: the first page opened after a night of deal emails waited minutes for it, and so
    did a trip search, since every reader of the price book files first.)"""
    done = 0
    todo = run(memory, "SELECT DISTINCT item AS t, domain AS d, COALESCE(NULLIF(seller, ''), source) AS who FROM observations "
                       f"WHERE product IS NULL LIMIT {int(limit)}")
    for at in range(0, len(todo), FILE_AT_ONCE):
        rows = todo[at:at + FILE_AT_ONCE]
        found = [_identity(r["d"], r["t"], r["who"] or "") for r in rows]
        run(memory, "UPDATE observations SET product = f.product, brand = f.brand, category = f.category, place = f.place "
                    f"FROM (SELECT {_lists(('t', 'd', 'who', 'product', 'brand', 'category', 'place'))}) f "
                    "WHERE observations.product IS NULL AND observations.item = f.t AND observations.domain = f.d AND "
                    "COALESCE(NULLIF(observations.seller, ''), observations.source) IS NOT DISTINCT FROM f.who",
            _filed(rows, found, "t", "d", "who"))
    done += len(todo)
    todo = run(memory, "SELECT DISTINCT title AS t, COALESCE(domain, 'product') AS d, COALESCE(item, '') AS i, "
                       f"COALESCE(store, '') AS who FROM deals WHERE product IS NULL LIMIT {int(limit)}")
    for at in range(0, len(todo), FILE_AT_ONCE):
        rows = todo[at:at + FILE_AT_ONCE]
        found = [_identity(r["d"], r["t"] if r["d"] == "product" else (r["i"] or r["t"]), r["who"], r["t"]) for r in rows]
        run(memory, "UPDATE deals SET product = f.product, brand = f.brand, category = f.category, place = f.place "
                    f"FROM (SELECT {_lists(('t', 'd', 'i', 'who', 'product', 'brand', 'category', 'place'))}) f "
                    "WHERE deals.product IS NULL AND deals.title = f.t AND COALESCE(deals.domain, 'product') = f.d AND "
                    "COALESCE(deals.item, '') = f.i AND COALESCE(deals.store, '') = f.who",
            _filed(rows, found, "t", "d", "i", "who"))
    done += len(todo)
    return done


def _names(item: str, domain: str) -> tuple[str, str]:
    """The two names an item may be filed under: as given, and as the product it names."""
    if domain != "product":
        return key(item), key(item)
    from . import productid  # noqa: PLC0415
    return key(item), productid.product(item) or key(item)


def history(memory, item: str, domain: str = "product", days: int = 365) -> list[dict]:
    since = utc(datetime.now(timezone.utc) - timedelta(days=days))
    identify(memory, FILE_ON_READ)
    rows = run(memory, "SELECT seen_at, price, currency, seller, source FROM observations WHERE domain = ? AND (item = ? "
                       "OR product = ? OR product = ?) AND seen_at >= ? ORDER BY seen_at",
               (domain, key(item), *_names(item, domain), since))
    if not rows and key(item):
        words = key(item).split()
        rows = run(memory, "SELECT seen_at, price, currency, seller, source FROM observations WHERE domain = ? AND "
                           + " AND ".join(["item LIKE ?"] * len(words)) + " AND seen_at >= ? ORDER BY seen_at",
                   (domain, *[f"%{w}%" for w in words], since))
    return rows


def lowest(memory, item: str, domain: str = "product") -> float | None:
    identify(memory, FILE_ON_READ)
    got = run(memory, "SELECT MIN(price) AS low FROM observations WHERE domain = ? AND (item = ? OR product = ? OR "
                      "product = ?)", (domain, key(item), *_names(item, domain)))
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


SEEN_DAYS, USUAL_MIN = 3, 5  # days a product must have been seen to call a price its lowest; deals for a "usual"
# A "crazy deal", as the Deals page uses the words. One of three, and the page says which:
#   half price or better against the regular price the seller states;
#   30% or more under the usual (median) price Buddy has seen for the same product, the same route in the same cabin
#   or the same hotel, once it has been seen on SEEN_DAYS days with USUAL_MIN prices;
#   the source itself calls it a price mistake ("error fare").
# More than 80% under the usual price is not counted: that is far more often another thing filed under the same
# name (a case under its phone) than a price.
# A stated regular price is not taken on trust once Buddy has its own history of the thing: when Buddy has usually
# seen it at 25% or more under the "regular" price, the deal says so and is judged by Buddy's own usual price only
# (a "was $600" on something that has sold for about $290 for weeks is not half price).
CRAZY_OFF, CRAZY_UNDER, UNDER_MAX, REGULAR_DOUBT = 0.5, 0.3, 0.8, 0.25
MISTAKE = re.compile(r"\b(?:error|mistake|glitch)\s+(?:fares?|rates?|prices?)\b|\bprice\s+(?:error|mistake|glitch)\b|"
                     r"\bmistake(?:nly)?\s+priced\b", re.I)
# What each kind of deal can be grouped by on the Deals page, and the column that holds it.
GROUPS = {"product": {"category": "category", "brand": "brand", "product": "product"},
          "flight": {"route": "product", "airline": "brand"},
          "hotel": {"place": "place", "chain": "brand"}}
POOL = 5000  # how many of the newest deals of a kind are looked through to sort by saving or to find the crazy ones


def group_label(domain: str, by: str, name: str) -> str:
    """A group's name as the page shows it: "JFK to LIM", "LG", "Earbuds and headphones"."""
    name = str(name or "")
    if not name:
        return "Not filed"
    if by == "route":
        ends = name.split("-")
        if len(ends) == 2 and all(len(e) == 3 and e.isalpha() for e in ends):
            return f"{ends[0].upper()} to {ends[1].upper()}"
        return " to ".join(e.strip().title() for e in ends) if len(ends) == 2 else name.title()
    if by == "category":
        return name.upper() if len(name) <= 2 else name[:1].upper() + name[1:]   # "TV", "Power tool"
    if by == "product":
        return name
    return name.upper() if len(name) <= 3 else " ".join(w[:1].upper() + w[1:] for w in name.split())


def _deal_where(days: int, query: str, groceries: str = "") -> tuple[list, list]:
    since = (datetime.now(timezone.utc).date() - timedelta(days=max(1, min(days, 90)))).isoformat()
    where, args = ["seen_on >= ?"], [since]
    if groceries in ("only", "exclude"):  # the Groceries page has its own deals; the Deals page the rest
        from .grocerysavings import sql_filter  # noqa: PLC0415
        cond, params = sql_filter(groceries == "only")
        where.append(cond)
        args += list(params)
    for w in [w for w in re.findall(r"[a-z0-9]{2,}", (query or "").lower())][:6]:
        where.append("lower(title || ' ' || COALESCE(store, '') || ' ' || COALESCE(item, '')) LIKE ?")
        args.append(f"%{w}%")
    return where, args


def deal_groups(memory, days: int = 7, domain: str = "product", by: str = "category", query: str = "",
                limit: int = 200, groceries: str = "") -> dict:
    """The deals of one kind counted by group, the biggest group first: products by category, brand or product;
    flights by route or airline; hotels by place or chain. Each group: how many deals, the lowest price among them,
    the biggest stated saving, and the day of its newest deal."""
    column = (GROUPS.get(domain) or {}).get(by)
    if not column:
        raise ValueError(f"{domain or 'These'} deals can be grouped by: {', '.join(GROUPS.get(domain) or []) or 'nothing'}.")
    try:
        identify(memory, FILE_ON_READ)
    except Exception:
        log.warning("price book: filing deals under their products failed", exc_info=True)
    where, args = _deal_where(days, query, groceries)
    rows = run(memory, f"SELECT COALESCE(d.{column}, '') AS name, COUNT(*) AS deals, MIN(d.price) AS lowest, "
                       "MAX(CASE WHEN d.regular_price > d.price AND d.price > 0 THEN 1 - d.price / d.regular_price END) "
                       "AS best_off, MAX(d.seen_on) AS newest FROM deals d WHERE " + " AND ".join(where)
               + f" AND COALESCE(d.domain, 'product') = ? GROUP BY 1 ORDER BY deals DESC, name LIMIT {int(limit) + 1}",
               (*args, domain))
    groups = [{"name": r["name"], "label": group_label(domain, by, r["name"]), "deals": int(r["deals"]),
               "lowest": r["lowest"], "best_off": round(r["best_off"], 2) if r["best_off"] else None,
               "newest": str(r["newest"])} for r in rows[:limit]]
    return {"domain": domain, "by": by, "groups": groups, "more": len(rows) > limit, "days": days,
            "deals": sum(g["deals"] for g in groups)}


def deals_page(memory, days: int = 7, domain: str = "", query: str = "", limit: int = 300, by: str = "",
               group: str | None = None, sort: str = "", crazy: bool = False, groceries: str = "") -> dict:
    """The Deals page's list: recent deals (feeds and the deals inbox), each filed under its product, brand and
    category, with the lowest price seen for that product. A deal is called the lowest only when the product has been
    seen on SEEN_DAYS different days: the first sighting of anything is trivially its lowest. Market data only.

    `by` and `group` keep one group's deals (see GROUPS; an empty group is the deals Buddy could not file). `sort` is
    "new" (the default), "saving" (the biggest saving first) or "price" (the cheapest first). `crazy` keeps only the
    crazy deals (see CRAZY_OFF above). `groceries`: "only" keeps grocery deals (the Groceries page), "exclude"
    leaves them out (the Deals page), "" keeps all (grocerysavings.is_grocery says which are groceries)."""
    try:
        identify(memory, FILE_ON_READ)
    except Exception:  # filing deals under their products is a help, not a condition: the page still lists them
        log.warning("price book: filing deals under their products failed", exc_info=True)
    where, args = _deal_where(days, query, groceries)
    # Each kind gets its own newest `limit`: a night with thousands of product deals must not push the few
    # flight and hotel deals off the page.
    kinds = (domain,) if domain in ("product", "flight", "hotel") else ("product", "flight", "hotel")
    column = (GROUPS.get(domain) or {}).get(by) if group is not None else None
    ranked = crazy or sort in ("saving", "price")  # then the newest `limit` are not enough: look through more
    rows = []
    for kind in kinds:
        rows += run(memory, "SELECT d.id, d.seen_on, d.source, d.title, d.url, d.price, d.store, "
                            "COALESCE(d.domain, 'product') AS domain, COALESCE(NULLIF(d.item, ''), lower(d.title)) AS item, "
                            "d.regular_price, d.ends, d.product, d.brand, d.category, d.place FROM deals d WHERE " + " AND ".join(where)
                    + " AND COALESCE(d.domain, 'product') = ?" + (f" AND COALESCE(d.{column}, '') = ?" if column else "")
                    + f" ORDER BY d.seen_on DESC, d.id DESC LIMIT {POOL if ranked else int(limit)}",
                    (*args, kind, *([group] if column else [])))
    totals = {r["domain"]: int(r["n"]) for r in run(
        memory, "SELECT COALESCE(domain, 'product') AS domain, COUNT(*) AS n FROM deals d WHERE " + " AND ".join(where)
                + " GROUP BY 1", tuple(args))}
    # The price history of the products on this page only: the whole book is not read to show 300 deals.
    wanted = sorted({r["product"] or key(r["item"]) for r in rows})
    makers = sorted({r["brand"] for r in rows if r["domain"] == "product" and r["brand"]})
    # A flight is compared within its cabin only: a business fare is not a bad economy fare.
    lows = {(r["domain"], r["product"], r["cabin"]): r for r in run(
        memory, "SELECT domain, product, CASE WHEN domain = 'flight' THEN category ELSE '' END AS cabin, MIN(price) AS low, "
                "MEDIAN(price) AS mid, COUNT(DISTINCT CAST(seen_at AS DATE)) AS days, COUNT(*) AS n FROM observations "
                "WHERE product IN (SELECT UNNEST(CAST(? AS VARCHAR[]))) GROUP BY 1, 2, 3", (wanted,))} if wanted else {}
    usual = {(r["brand"], r["category"]): r for r in run(
        memory, "SELECT brand, category, MEDIAN(price) AS mid, COUNT(DISTINCT product) AS products, COUNT(*) AS n FROM "
                "observations WHERE domain = 'product' AND brand IN (SELECT UNNEST(CAST(? AS VARCHAR[]))) "
                "GROUP BY brand, category HAVING COUNT(*) >= ?", (makers, USUAL_MIN))} if makers else {}
    out = []
    for r in rows:
        seen = lows.get((r["domain"], r["product"] or key(r["item"]), r["category"] if r["domain"] == "flight" else "")) or {}
        low, days_seen = seen.get("low"), int(seen.get("days") or 0)
        peers = usual.get((r["brand"], r["category"])) if r["domain"] == "product" else None
        price, mid = r["price"], seen.get("mid")
        off = round(1 - price / r["regular_price"], 2) if price and r["regular_price"] and r["regular_price"] > price else None
        # How far under its own usual price, once there is enough of a usual to speak of.
        under = round(1 - price / mid, 2) if (price and mid and mid > price and days_seen >= SEEN_DAYS
                                              and int(seen.get("n") or 0) >= USUAL_MIN) else None
        if under is not None and under > UNDER_MAX:
            under = None
        known = mid if (mid and days_seen >= SEEN_DAYS and int(seen.get("n") or 0) >= USUAL_MIN) else None
        doubt = bool(off is not None and known and known <= r["regular_price"] * (1 - REGULAR_DOUBT))
        why = []
        if off is not None and off >= CRAZY_OFF and not doubt:
            why.append(f"{off:.0%} off the regular price")
        if under is not None and under >= CRAZY_UNDER:
            why.append(f"{under:.0%} under the usual price Buddy has seen")
        if MISTAKE.search(str(r["title"])):
            why.append("called a price mistake by the source")
        out.append({**r, "seen_on": str(r["seen_on"]), "lowest_seen": low if days_seen >= 2 else None,
                    "seen_days": days_seen,
                    "is_lowest": bool(price and low and days_seen >= SEEN_DAYS and price <= low * 1.001),
                    "usual": ({"price": round(peers["mid"], 2), "deals": int(peers["n"]), "products": int(peers["products"])}
                              if peers else None),
                    "off": off, "under": under, "usual_price": round(mid, 2) if under is not None or doubt else None,
                    "regular_doubt": doubt, "crazy": bool(why), "crazy_why": why,
                    "url": r["url"] if str(r["url"]).startswith("https://") else ""})
    if crazy:
        out = [d for d in out if d["crazy"]]
    if sort == "saving" or (crazy and sort != "price"):
        # stable: the newest first among equals. A doubted regular price does not count as a saving.
        out.sort(key=lambda d: -max(0.0 if d["regular_doubt"] else d["off"] or 0.0, d["under"] or 0.0))
    elif sort == "price":
        out.sort(key=lambda d: (d["price"] is None, d["price"] or 0.0))
    found = {d: sum(1 for r in out if r["domain"] == d) for d in ("product", "flight", "hotel")}
    if ranked:  # the pool is cut to the page's size per kind only after it was sorted
        kept: dict = {}
        out = [d for d in out if kept.setdefault(d["domain"], []).append(1) or len(kept[d["domain"]]) <= limit]
    counts = {d: totals.get(d, 0) for d in ("product", "flight", "hotel")}  # all of them, not just those shown
    return {"deals": out, "counts": counts, "shown": {d: sum(1 for r in out if r["domain"] == d) for d in counts},
            "found": found, "days": days, "by": by if column else "", "group": group if column else None,
            "group_label": group_label(domain, by, group) if column else "", "sort": sort or "new", "crazy": bool(crazy)}


LEVEL_MIN = 3  # prices a product needs before its dips count towards its brand's or category's


def _dips(memory, where: str, params: tuple, since) -> dict | None:
    """How deep the dips run across a group of products: for each with LEVEL_MIN or more prices, how far its lowest
    price sat below its usual (median) price; then the average of those. Prices of different products are never
    averaged together (a $30 camera and a $250 kit have nothing to say about each other's level, only about how far
    such things fall)."""
    rows = run(memory, "SELECT product, category, MEDIAN(price) AS mid, MIN(price) AS low FROM observations WHERE "
                       f"{where} AND seen_at >= ? GROUP BY product, category HAVING COUNT(*) >= {LEVEL_MIN}", (*params, since))
    dips = [(r["mid"] - r["low"]) / r["mid"] for r in rows if r["mid"]]
    return {"dip": round(sum(dips) / len(dips), 4), "products": len(dips)} if dips else None


def levels(memory, domain: str, item: str, seller: str = "", title: str = "", cabin: str = "", days: int = 365) -> dict:
    """What the price book knows about an item at three levels, for the buy-or-wait engines:

      own    this product's own prices (for a flight: this route in this cabin)
      peers  the same brand in the same category (a flight: the same airline in the same cabin, on any route;
             a hotel: the same chain)
      group  the whole category (a flight: every route to the same destination in the same cabin; a hotel: the
             same place)

    A product Buddy has never seen can still be judged by how far its peers usually fall."""
    identify(memory, FILE_ON_READ)
    got = _identity(domain, item, seller, title, cabin)
    since = utc(datetime.now(timezone.utc) - timedelta(days=days))
    flight = domain == "flight"
    known_cabin = flight and got["category"] != "not stated"
    own_where = "domain = ? AND product = ?" + (" AND category = ?" if known_cabin else "")
    own_args = (domain, got["product"], *((got["category"],) if known_cabin else ()))
    own = run(memory, f"SELECT COUNT(*) AS n, COUNT(DISTINCT CAST(seen_at AS DATE)) AS days, MIN(price) AS low, "
                      f"MEDIAN(price) AS mid FROM observations WHERE {own_where} AND seen_at >= ?", (*own_args, since))[0]
    peers = group = None
    if got["brand"]:
        if domain == "hotel":
            peers = _dips(memory, "domain = 'hotel' AND brand = ?", (got["brand"],), since)
        else:
            peers = _dips(memory, "domain = ? AND brand = ? AND category = ?", (domain, got["brand"], got["category"]), since)
    if domain == "product" and got["category"] not in ("", "other"):
        group = _dips(memory, "domain = 'product' AND category = ?", (got["category"],), since)
    elif got.get("place"):
        group = _dips(memory, "domain = ? AND place = ?" + (" AND category = ?" if known_cabin else ""),
                      (domain, got["place"], *((got["category"],) if known_cabin else ())), since)
    return {"identity": got, "own": {"prices": int(own["n"]), "days": int(own["days"]), "low": own["low"], "usual": own["mid"]},
            "peers": peers, "group": group}


def chart(memory, item: str, domain: str = "product", days: int = 365) -> dict:
    """One item's price history for the Deals page's chart: the day's lowest price, per day."""
    identify(memory, FILE_ON_READ)
    rows = run(memory, "SELECT CAST(seen_at AS DATE) AS day, MIN(price) AS price FROM observations WHERE domain = ? "
                       "AND (item = ? OR product = ? OR product = ?) AND seen_at >= ? GROUP BY day ORDER BY day",
               (domain, key(item), *_names(item, domain), utc(datetime.now(timezone.utc) - timedelta(days=days))))
    return {"item": item, "domain": domain, "points": [{"day": str(r["day"]), "price": r["price"]} for r in rows],
            "lowest": min((r["price"] for r in rows), default=None)}


def close_all() -> None:
    with _lock:
        for c in _open.values():
            try:
                c.close()
            except Exception:
                pass
        _open.clear()
        _filing_off.clear()
        _keep_index.clear()
        _rebuilt.clear()


TOOL = {
    "name": "price_book",
    "description": (
        "Buddy's separate price database (market data only: products, flights, hotels, deals, and the buy-or-wait "
        "calls with outcomes). stats: how much history there is per kind and since when. history: every price seen "
        "for an item (domain product, flight like 'mia-lim', or hotel like 'hotels:lima'), for charts or 'is this a "
        "good price?'. export: write it all to CSV or Parquet files on the server for analysis in other tools. "
        "patterns: what the nightly learning in the sandbox found (real sale discounts per kind and year, the odds "
        "of a 10% drop, price by age since launch, flight booking windows per route, hotel weekday and month "
        "patterns, how Buddy's past calls did). learn: run that learning now. Raw prices are kept a year; the "
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
            ":memory:") else str(Path(tempfile.gettempdir()) / "pricebook-export")
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
