"""Company figures for the strategy lab: the latest earnings surprise and a few basic financial ratios.

Some well-known strategies cannot be written from prices alone: buying after an earnings surprise, or buying
quality companies (profitable, not deep in debt). Finnhub, whose key Buddy already uses, publishes these per
company. Once a week Buddy asks for the lab's companies and keeps one value per company, figure and day:

  earnings_surprise   the latest reported quarter's profit against what analysts expected, as a fraction
  roe                 return on equity over the last twelve months, as a fraction
  gross_margin        gross margin over the last twelve months, as a fraction
  eps_growth          profit per share against a year before, as a fraction
  pe, pb              price against profits, and against book value
  debt_to_equity      total debt against shareholders' equity

Only ticker symbols go out. These are today's values: there is no past record of them here, so a rule that
uses one can be paper-traded from now on but cannot be replayed over past prices, and the page says so.
"""

import logging
import time
from datetime import date, timedelta

from .memory import Memory
from .tools import local_now

log = logging.getLogger("buddy.fundamentals")

WEEK_KEY = "fundamentals:week"
FRESH_DAYS, MAX_SYMBOLS, PAUSE = 120, 160, 1.3  # how long a value is used; companies a week; seconds between calls
PACED = True
METRICS = {  # our name -> (Finnhub's names, tried in order; divide by 100 when it is a percent)
    "roe": (("roeTTM", "roeRfy"), True), "gross_margin": (("grossMarginTTM", "grossMarginAnnual"), True),
    "eps_growth": (("epsGrowthTTMYoy", "epsGrowthQuarterlyYoy"), True),
    "pe": (("peTTM", "peBasicExclExtraTTM", "peAnnual"), False), "pb": (("pbQuarterly", "pbAnnual", "pb"), False),
    "debt_to_equity": (("totalDebt/totalEquityQuarterly", "totalDebt/totalEquityAnnual"), False),
}
FIELDS = ("earnings_surprise", *METRICS)
SCHEMA = ["CREATE TABLE IF NOT EXISTS fundamentals (symbol VARCHAR, day DATE, field VARCHAR, value DOUBLE, "
          "PRIMARY KEY (symbol, day, field))"]


def _run(memory: Memory, sql: str, params: tuple = ()) -> list[dict]:
    from . import pricebook  # noqa: PLC0415
    return pricebook.run(memory, sql, params)


def _ready(memory: Memory) -> None:
    for statement in SCHEMA:
        _run(memory, statement)


def _number(v) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f and abs(f) < 1e9 else None


def read(memory: Memory, symbol: str, env=None, fetch=None) -> dict:
    """One company's figures from Finnhub: {field: value}, only those it returned."""
    from . import finnhub  # noqa: PLC0415
    out = {}
    metric = ((finnhub._get(memory, "stock/metric", {"symbol": symbol, "metric": "all"}, env, fetch) or {}).get("metric")) or {}
    for name, (keys, percent) in METRICS.items():
        v = next((n for n in (_number(metric.get(k)) for k in keys) if n is not None), None)
        if v is not None:
            out[name] = round(v / 100 if percent else v, 4)
    earned = finnhub._get(memory, "stock/earnings", {"symbol": symbol}, env, fetch) or []
    latest = max((e for e in earned if isinstance(e, dict) and e.get("period")), key=lambda e: str(e["period"]), default=None)
    if latest:
        pct, actual, estimate = _number(latest.get("surprisePercent")), _number(latest.get("actual")), _number(latest.get("estimate"))
        if pct is not None:
            out["earnings_surprise"] = round(pct / 100, 4)
        elif actual is not None and estimate:
            out["earnings_surprise"] = round((actual - estimate) / abs(estimate), 4)
    return out


def refresh(memory: Memory, symbols: list[str], env=None, fetch=None, today: date | None = None) -> dict:
    """Ask for each company (paced, so the service's limit is kept) and keep what came back."""
    from . import finnhub  # noqa: PLC0415
    today = today or local_now().date()
    _ready(memory)
    out = {"asked": 0, "kept": 0, "failed": 0}
    if not finnhub.configured(env):
        return {**out, "note": "Finnhub isn't set up"}
    misses = 0
    for symbol in symbols[:MAX_SYMBOLS]:
        if misses >= 5:
            break  # five failures in a row: the service is down or the limit is spent
        out["asked"] += 1
        try:
            got = read(memory, symbol, env, fetch)
            misses = 0
        except Exception as e:
            misses += 1
            out["failed"] += 1
            log.info("fundamentals: %s failed (%s)", symbol, type(e).__name__)
            got = {}
        for field, value in got.items():
            _run(memory, "INSERT OR REPLACE INTO fundamentals (symbol, day, field, value) VALUES (?, ?, ?, ?)",
                 (symbol, today.isoformat(), field, value))
        out["kept"] += bool(got)
        if PACED and fetch is None:
            time.sleep(PAUSE)
    _run(memory, "DELETE FROM fundamentals WHERE day < ?", ((today - timedelta(days=800)).isoformat(),))
    return out


def latest(memory: Memory, today: date | None = None) -> dict:
    """{symbol: {field: value}} from the newest value of each figure, none older than FRESH_DAYS."""
    today = today or local_now().date()
    try:
        _ready(memory)
        rows = _run(memory, "SELECT symbol, field, value FROM fundamentals f WHERE day >= ? AND day = (SELECT MAX(day) FROM "
                            "fundamentals g WHERE g.symbol = f.symbol AND g.field = f.field)",
                    ((today - timedelta(days=FRESH_DAYS)).isoformat(),))
    except Exception:
        log.info("fundamentals: none held", exc_info=True)
        return {}
    out: dict[str, dict] = {}
    for r in rows:
        out.setdefault(r["symbol"], {})[r["field"]] = r["value"]
    return out


def due(memory: Memory, now=None) -> bool:
    from . import finnhub  # noqa: PLC0415
    now = now or local_now()
    week = f"{now.isocalendar()[0]}-{now.isocalendar()[1]:02d}"
    return finnhub.configured() and now.weekday() >= 5 and memory.get(WEEK_KEY) != week


def status(memory: Memory) -> dict:
    try:
        _ready(memory)
        got = _run(memory, "SELECT COUNT(DISTINCT symbol) AS n, MAX(day) AS day FROM fundamentals")[0]
    except Exception:
        return {"companies": 0, "day": ""}
    return {"companies": int(got["n"] or 0), "day": str(got["day"]) if got["day"] else ""}
