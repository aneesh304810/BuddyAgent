"""A second opinion on the kept daily prices. With one source, a wrong price is kept as it stands and every test
built on it is wrong too. This compares a few symbols a day against a second, independent provider (Tiingo, on
its free plan) and says where the two disagree.

  off      until the owner sets BUDDY_TIINGO_KEY. Nothing is sent and nothing is claimed.
  each day CHECK_A_DAY symbols from the price store, the ones checked longest ago first, so the whole list is
           gone round in a week or so. One request a symbol, well inside the free plan's hourly limit.
  compare  the last WINDOW_DAYS of closes, day by day, against the second source's unadjusted close. A day that
           differs by more than DIFFERS is a disagreement. The newest day held is left out (it may have been
           fetched before the close), and a symbol whose shares split inside the window is not compared (one
           source adjusts for the split and the other reports the price as it was).
  record   per symbol: agrees, differs (with the worst day and both prices), or not compared and why.

What a disagreement means: one of the two sources is wrong, or they treat a corporate event differently. It does
not say which. Nothing is corrected automatically; the lab keeps using its own source. Only ticker symbols and
dates are sent.
"""

import logging
import urllib.parse
from datetime import date, timedelta

from . import gateway
from .memory import Memory
from .tools import local_now

log = logging.getLogger("buddy.pricecheck")

KEY, DAY_KEY = "BUDDY_TIINGO_KEY", "pricecheck:day"
CHECK_A_DAY, WINDOW_DAYS, DIFFERS = 20, 30, 0.01
SCHEMA = """CREATE TABLE IF NOT EXISTS price_checks (
    symbol VARCHAR PRIMARY KEY, checked DATE NOT NULL, state VARCHAR NOT NULL, days INTEGER DEFAULT 0, worst DOUBLE,
    day DATE, ours DOUBLE, theirs DOUBLE, note VARCHAR DEFAULT '')"""


def _run(memory: Memory, sql: str, params: tuple = ()) -> list[dict]:
    from . import pricebook  # noqa: PLC0415
    return pricebook.run(memory, sql, params)


def configured(env=None) -> bool:
    return bool(gateway._secret(env, KEY))


def due(memory: Memory, now=None, env=None) -> bool:
    now = now or local_now()
    return configured(env) and now.hour >= 10 and memory.get(DAY_KEY) != now.date().isoformat()


def compare(ours: list[dict], theirs: list[dict]) -> dict:
    """One symbol: our kept bars ({"d", "c"}, oldest first) against the second source's rows for the same days."""
    if any(float(r.get("splitFactor") or 1) != 1 for r in theirs):
        return {"state": "not compared", "days": 0, "note": "its shares split inside the window"}
    second = {str(r.get("date") or "")[:10]: r.get("close") for r in theirs if r.get("close") is not None}
    shared = [(b["d"], b["c"], second[b["d"]]) for b in ours[:-1] if b["d"] in second and b["c"] and second[b["d"]]]
    if len(shared) < 5:
        return {"state": "not compared", "days": len(shared), "note": "fewer than five days in common"}
    day, mine, other = max(shared, key=lambda x: abs(x[1] / x[2] - 1))
    worst = abs(mine / other - 1)
    return {"state": "differs" if worst > DIFFERS else "agrees", "days": len(shared), "worst": round(worst, 4),
            "day": day, "ours": mine, "theirs": other, "note": ""}


def daily(memory: Memory, env=None, fetch=None, today: date | None = None, source: str = "") -> dict:
    """Check today's share of the kept symbols. Returns {"checked", "agree", "differ", "skipped", "differing"}."""
    from . import pricestore  # noqa: PLC0415
    today = today or local_now().date()
    out = {"checked": 0, "agree": 0, "differ": 0, "skipped": 0, "differing": []}
    if not configured(env):
        return out
    _run(memory, SCHEMA)
    pricestore._ready(memory)
    if not source:
        top = _run(memory, "SELECT source FROM daily_prices GROUP BY source ORDER BY COUNT(*) DESC LIMIT 1")
        source = top[0]["source"] if top else ""
    names = [r["symbol"] for r in _run(
        memory, "SELECT p.symbol FROM (SELECT DISTINCT symbol FROM daily_prices WHERE source = ?) p LEFT JOIN price_checks c "
                "ON c.symbol = p.symbol ORDER BY COALESCE(c.checked, DATE '1970-01-01'), p.symbol LIMIT ?", (source, CHECK_A_DAY))]
    kept = pricestore.load(memory, names, source, WINDOW_DAYS)
    for symbol in names:
        bars = kept.get(symbol) or []
        if not bars:
            continue
        query = urllib.parse.urlencode({"startDate": bars[0]["d"], "endDate": bars[-1]["d"]})
        ticker = urllib.parse.quote(symbol.replace(".", "-").replace("/", "-"))
        try:
            rows = gateway.call(memory, "tiingo", f"/tiingo/daily/{ticker}/prices?{query}", env=env, fetch=fetch,
                                purpose="price cross-check") or []
            got = compare(bars, rows if isinstance(rows, list) else [])
        except gateway.ConnectError as e:
            if e.status == 429:   # the hour's allowance is used up: stop, and carry on tomorrow
                log.info("price check: the second source's limit was reached after %d symbols", out["checked"])
                break
            got = {"state": "not compared", "days": 0, "note": "the second source has no prices for it" if e.status == 404
                   else f"the second source could not be read ({e.status or type(e).__name__})"}
        out["checked"] += 1
        out[{"agrees": "agree", "differs": "differ"}.get(got["state"], "skipped")] += 1
        if got["state"] == "differs":
            out["differing"].append({"symbol": symbol, "day": got["day"], "ours": got["ours"], "theirs": got["theirs"],
                                     "by": got["worst"]})
        _run(memory, "INSERT OR REPLACE INTO price_checks (symbol, checked, state, days, worst, day, ours, theirs, note) "
                     "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
             (symbol, today.isoformat(), got["state"], got.get("days", 0), got.get("worst"), got.get("day"),
              got.get("ours"), got.get("theirs"), got.get("note", "")))
    return out


def message(got: dict) -> str:
    if not got["differing"]:
        return ""
    worst = sorted(got["differing"], key=lambda d: -d["by"])[:5]
    return ("📏 Price cross-check: the second source disagrees with the kept price for "
            + ", ".join(f"{d['symbol']} on {d['day']} ({d['ours']:.2f} against {d['theirs']:.2f})" for d in worst)
            + (f", and {len(got['differing']) - 5} more" if len(got["differing"]) > 5 else "")
            + ". One of the two is wrong, or they treat a company event differently. Nothing was changed; see "
              "Intelligence → Strategies → Test.")


def status(memory: Memory, env=None) -> dict:
    """For the page: is the check set up, how much of the list has been checked, and where the sources disagree."""
    on = configured(env)
    try:
        _run(memory, SCHEMA)
        counts = {r["state"]: int(r["n"]) for r in _run(memory, "SELECT state, COUNT(*) AS n FROM price_checks GROUP BY state")}
        differing = _run(memory, "SELECT symbol, CAST(checked AS VARCHAR) AS checked, CAST(day AS VARCHAR) AS day, ours, theirs, "
                                 "worst FROM price_checks WHERE state = 'differs' ORDER BY worst DESC LIMIT 20")
        last = _run(memory, "SELECT CAST(MAX(checked) AS VARCHAR) AS d FROM price_checks")[0]["d"] or ""
    except Exception:
        log.info("price check: no record to read", exc_info=True)
        counts, differing, last = {}, [], ""
    return {"set_up": on, "key": KEY, "agree": counts.get("agrees", 0), "differ": counts.get("differs", 0),
            "not_compared": counts.get("not compared", 0), "differing": differing, "last": last, "a_day": CHECK_A_DAY,
            "note": ("A second, independent source checks a few kept prices each day. A disagreement means one of the "
                     "two is wrong or they treat a company event differently; nothing is corrected automatically.")
            if on else f"Not set up. With a free key from the second source, Buddy compares {CHECK_A_DAY} kept symbols a "
                       f"day against it: sudo bash /opt/buddy/deploy/set.sh {KEY}"}
