"""Daily stock prices, kept. Every daily price Buddy fetches for the strategy lab is stored in the price book, so
the history does not depend on a provider's free plan, a test can be repeated, and a replay does not download
five years of prices every time.

  keep     each symbol's daily open, high, low, close and volume, with the source it came from and when it was
           fetched. Market data only: nothing about the owner.
  fetch    what the lab calls in place of asking the provider directly. It asks the provider only for what the
           store lacks (the days since the last one held, or the whole window for a symbol it has never seen), keeps
           it, and returns the bars from the store.
  revised  a provider re-states past prices after a share split. When a newly fetched price for a day already held
           differs by more than half a percent, that symbol's whole history is fetched again and replaced, and the
           change is written down (price_revisions), so it is known that an earlier test used different numbers.
           The newest day held is never compared: it may have been fetched before the market closed.

What this is not: a record of companies that no longer trade, or of which companies were in an index on a past
date. It keeps what Buddy has asked for, from the day it first asked. One source at a time: prices from different
providers are kept apart and never mixed in one series.
"""

import csv
import logging
import os
import tempfile
from datetime import date, datetime, timedelta, timezone

from .memory import Memory

log = logging.getLogger("buddy.pricestore")

SCHEMA = ["""CREATE TABLE IF NOT EXISTS daily_prices (
    symbol VARCHAR NOT NULL, day DATE NOT NULL, source VARCHAR NOT NULL, o DOUBLE, h DOUBLE, l DOUBLE, c DOUBLE NOT NULL,
    v DOUBLE, fetched TIMESTAMP NOT NULL, PRIMARY KEY (symbol, day, source))""",
          """CREATE TABLE IF NOT EXISTS price_revisions (
    symbol VARCHAR NOT NULL, source VARCHAR NOT NULL, noticed DATE NOT NULL, day DATE NOT NULL, was DOUBLE, now DOUBLE)"""]
REVISED, TAIL_EXTRA = 0.005, 10   # a past close differing by more than this was re-stated; days re-fetched for overlap


def _run(memory: Memory, sql: str, params: tuple = ()) -> list[dict]:
    from . import pricebook  # noqa: PLC0415
    return pricebook.run(memory, sql, params)


def _ready(memory: Memory) -> None:
    for statement in SCHEMA:
        _run(memory, statement)


def source_of(data) -> str:
    """The name the prices are kept under: where this Data object gets US daily prices from."""
    return str(getattr(data, "source_name", "") or ("alpaca" if getattr(data, "alpaca", None) else "other"))[:20]


def keep(memory: Memory, bars: dict[str, list[dict]], source: str, replace: bool = False) -> int:
    """Store fetched bars ({symbol: [{"d", "o", "h", "l", "c", "v"}]}). replace: these are a symbol's whole history,
    so what was held for it is dropped first. Returns how many day-prices were written."""
    _ready(memory)
    rows = [(s, b["d"], source, b.get("o"), b.get("h"), b.get("l"), b["c"], b.get("v"))
            for s, bs in bars.items() for b in bs if b.get("d") and b.get("c") is not None]
    if replace and bars:
        marks = ", ".join("?" for _ in bars)
        _run(memory, f"DELETE FROM daily_prices WHERE source = ? AND symbol IN ({marks})", (source, *bars))
    if not rows:
        return 0
    stamp = datetime.now(timezone.utc).replace(tzinfo=None).isoformat(sep=" ", timespec="seconds")
    handle, path = tempfile.mkstemp(suffix=".csv", prefix="buddy-prices-")   # one bulk load, not a row at a time
    try:
        with os.fdopen(handle, "w", newline="") as f:
            csv.writer(f).writerows(rows)
        quoted = path.replace("'", "''")
        _run(memory, "INSERT INTO daily_prices (symbol, day, source, o, h, l, c, v, fetched) "
                     f"SELECT column0, column1, column2, column3, column4, column5, column6, column7, CAST('{stamp}' AS TIMESTAMP) "
                     f"FROM read_csv('{quoted}', header = false, columns = {{'column0': 'VARCHAR', 'column1': 'DATE', "
                     "'column2': 'VARCHAR', 'column3': 'DOUBLE', 'column4': 'DOUBLE', 'column5': 'DOUBLE', "
                     "'column6': 'DOUBLE', 'column7': 'DOUBLE'}) "
                     "ON CONFLICT (symbol, day, source) DO UPDATE SET o = EXCLUDED.o, h = EXCLUDED.h, l = EXCLUDED.l, "
                     "c = EXCLUDED.c, v = EXCLUDED.v, fetched = EXCLUDED.fetched")
    finally:
        os.unlink(path)
    return len(rows)


def held(memory: Memory, symbols: list[str], source: str) -> dict[str, dict]:
    """{symbol: {"first", "last", "days"}} for what the store holds."""
    _ready(memory)
    if not symbols:
        return {}
    marks = ", ".join("?" for _ in symbols)
    return {r["symbol"]: {"first": str(r["first"]), "last": str(r["last"]), "days": int(r["n"])} for r in _run(
        memory, f"SELECT symbol, MIN(day) AS first, MAX(day) AS last, COUNT(*) AS n FROM daily_prices WHERE source = ? "
                f"AND symbol IN ({marks}) GROUP BY symbol", (source, *symbols))}


def load(memory: Memory, symbols: list[str], source: str, days: int) -> dict[str, list[dict]]:
    """Bars from the store, oldest first, each symbol's last `days` calendar days."""
    _ready(memory)
    if not symbols:
        return {}
    marks = ", ".join("?" for _ in symbols)
    out: dict[str, list[dict]] = {}
    for r in _run(memory, f"SELECT symbol, CAST(day AS VARCHAR) AS d, o, h, l, c, v FROM daily_prices WHERE source = ? "
                          f"AND symbol IN ({marks}) ORDER BY symbol, day", (source, *symbols)):
        out.setdefault(r.pop("symbol"), []).append(r)
    for s, bs in out.items():
        start = (date.fromisoformat(bs[-1]["d"]) - timedelta(days=days)).isoformat()
        out[s] = [b for b in bs if b["d"] >= start]
    return out


def fetch(memory: Memory, data, symbols: list[str], days: int = 420, today: date | None = None) -> dict[str, list[dict]]:
    """Daily bars for the lab, from the store, topped up from the provider first (see the note at the top). If the
    store cannot be used for any reason, the provider's answer is returned as it always was."""
    try:
        return _fetch(memory, data, sorted(set(symbols)), days, today or date.today())
    except Exception:
        log.warning("price store: unavailable; using the provider's prices directly", exc_info=True)
        return data.bars("us", symbols, days=days)


def _fetch(memory: Memory, data, symbols: list[str], days: int, today: date) -> dict[str, list[dict]]:
    source = source_of(data)
    have = held(memory, symbols, source)
    want_from = (today - timedelta(days=days)).isoformat()
    # a symbol never seen, or held from too late a start, needs its whole window; the rest only the recent days
    whole = [s for s in symbols if s not in have or (have[s]["first"] > want_from and have[s]["days"] < days * 0.6)]
    tail = [s for s in symbols if s not in whole]
    if tail:
        gap = max((today - date.fromisoformat(min(have[s]["last"] for s in tail))).days, 0) + TAIL_EXTRA
        got = data.bars("us", tail, days=gap)
        stored = load(memory, tail, source, gap + 400)
        restated = []
        for s in tail:
            new = {b["d"]: b["c"] for b in got.get(s) or []}
            old = {b["d"]: b["c"] for b in (stored.get(s) or [])[:-1]}      # not the newest day held (see the note)
            shared = sorted(set(new) & set(old))
            off = next((d for d in shared if old[d] and abs(new[d] / old[d] - 1) > REVISED), None)
            if off or (new and not shared):
                restated.append(s)
                if off:
                    _run(memory, "INSERT INTO price_revisions (symbol, source, noticed, day, was, now) VALUES (?, ?, ?, ?, ?, ?)",
                         (s, source, today.isoformat(), off, old[off], new[off]))
        keep(memory, {s: bs for s, bs in got.items() if s not in restated}, source)
        whole += restated
    if whole:
        keep(memory, data.bars("us", whole, days=days), source, replace=True)
    return load(memory, symbols, source, days)


def status(memory: Memory) -> dict:
    """For the page: what is held, from when to when, from which source, and how often prices were re-stated."""
    _ready(memory)
    rows = _run(memory, "SELECT source, COUNT(DISTINCT symbol) AS symbols, COUNT(*) AS prices, CAST(MIN(day) AS VARCHAR) AS first, "
                        "CAST(MAX(day) AS VARCHAR) AS last, CAST(MAX(fetched) AS VARCHAR) AS fetched FROM daily_prices "
                        "GROUP BY source ORDER BY prices DESC")
    revised = _run(memory, "SELECT COUNT(*) AS n, COUNT(DISTINCT symbol) AS symbols FROM price_revisions")[0]
    return {"sources": rows, "symbols": sum(r["symbols"] for r in rows), "prices": sum(r["prices"] for r in rows),
            "first": min((r["first"] for r in rows), default=""), "last": max((r["last"] for r in rows), default=""),
            "restated": int(revised["n"]), "restated_symbols": int(revised["symbols"]),
            "note": "Daily prices Buddy has fetched for the strategy lab, kept from the day it first asked. Companies "
                    "that stopped trading before then are not in it. A provider re-states past prices after a share "
                    "split; when that is noticed the symbol's history is fetched again and the change is counted here."}
