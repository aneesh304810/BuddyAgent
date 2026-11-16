"""News sentiment: how the news on a company reads, as a number, from two services that publish it.

  Alpha Vantage   news with a sentiment score per company (free key: BUDDY_ALPHAVANTAGE_KEY, about 25 requests
                  a day on the free plan)
  Marketaux       company-tagged news with a sentiment score (free key: BUDDY_MARKETAUX_KEY, about 100 a day)

Each weekday Buddy asks for the companies it is already judging (the recommended watchlist and your own US
symbols, at most 12 from each service) and keeps one number per company, service and day: the average score
from -1 (bad) to +1 (good) and how many articles it rests on. Only ticker symbols go out. The scores are the
services' own automatic reading of headlines, so they are sometimes wrong; Buddy keeps them as one more figure
with each paper trade (f_sentiment_7) and shows them, and no trade is decided on them alone.
"""

import logging
import urllib.parse
from datetime import date, timedelta

from . import gateway
from .memory import Memory
from .tools import local_now

log = logging.getLogger("buddy.newssent")
A_DAY = 12
SERVICES = {"alphavantage": "BUDDY_ALPHAVANTAGE_KEY", "marketaux": "BUDDY_MARKETAUX_KEY"}
DAY_KEY = "newssent:day"
SCHEMA = ["CREATE TABLE IF NOT EXISTS news_sentiment (symbol VARCHAR, day DATE, source VARCHAR, score DOUBLE, "
          "articles INTEGER, PRIMARY KEY (symbol, day, source))"]


def _run(memory: Memory, sql: str, params: tuple = ()) -> list[dict]:
    from . import pricebook  # noqa: PLC0415
    return pricebook.run(memory, sql, params)


def _ready(memory: Memory) -> None:
    for statement in SCHEMA:
        _run(memory, statement)


def configured(env=None) -> list[str]:
    return [name for name, key in SERVICES.items() if gateway._secret(env, key)]


def _alphavantage(memory: Memory, symbol: str, env=None, fetch=None) -> tuple[float, int] | None:
    q = urllib.parse.urlencode({"function": "NEWS_SENTIMENT", "tickers": symbol, "limit": 50,
                                "apikey": gateway._secret(env, SERVICES["alphavantage"])})
    got = gateway.call(memory, "alphavantage", "/query?" + q, env=env, fetch=fetch, purpose="news sentiment") or {}
    scores = []
    for item in got.get("feed") or []:
        for t in item.get("ticker_sentiment") or []:
            try:
                if str(t.get("ticker")).upper() == symbol and float(t.get("relevance_score") or 0) >= 0.3:
                    scores.append(float(t["ticker_sentiment_score"]))
            except (TypeError, ValueError, KeyError):
                continue
    return (round(sum(scores) / len(scores), 4), len(scores)) if scores else None


def _marketaux(memory: Memory, symbol: str, env=None, fetch=None) -> tuple[float, int] | None:
    q = urllib.parse.urlencode({"symbols": symbol, "filter_entities": "true", "language": "en",
                                "api_token": gateway._secret(env, SERVICES["marketaux"])})
    got = gateway.call(memory, "marketaux", "/v1/news/all?" + q, env=env, fetch=fetch, purpose="news sentiment") or {}
    scores = []
    for item in got.get("data") or []:
        for e in item.get("entities") or []:
            try:
                if str(e.get("symbol")).upper() == symbol and e.get("sentiment_score") is not None:
                    scores.append(float(e["sentiment_score"]))
            except (TypeError, ValueError):
                continue
    return (round(sum(scores) / len(scores), 4), len(scores)) if scores else None


READERS = {"alphavantage": _alphavantage, "marketaux": _marketaux}


def symbols(memory: Memory) -> list[str]:
    out = []
    try:
        from . import watchpicks  # noqa: PLC0415
        out += sorted(watchpicks.symbols(memory))
    except Exception:
        pass
    try:
        out += [r["symbol"] for r in memory.db.execute("SELECT symbol FROM mkt_watch WHERE market = 'us' ORDER BY symbol")]
    except Exception:
        pass
    return list(dict.fromkeys(s.upper() for s in out))


def refresh(memory: Memory, env=None, fetch=None, today: date | None = None, names: list[str] | None = None) -> dict:
    """Ask each service that is set up for today's companies. Returns {"asked", "kept", "failed"}."""
    today = today or local_now().date()
    _ready(memory)
    out = {"asked": 0, "kept": 0, "failed": {}}
    names = names if names is not None else symbols(memory)
    for service in configured(env):
        misses = 0
        for symbol in names[:A_DAY]:
            if misses >= 3:
                break  # three failures in a row: the service is down or its daily allowance is spent
            out["asked"] += 1
            try:
                got = READERS[service](memory, symbol, env, fetch)
                misses = 0
            except Exception as e:
                misses += 1
                out["failed"][service] = f"{type(e).__name__}: {str(e)[:100]}"
                continue
            if got:
                _run(memory, "INSERT OR REPLACE INTO news_sentiment (symbol, day, source, score, articles) VALUES "
                             "(?, ?, ?, ?, ?)", (symbol, today.isoformat(), service, got[0], got[1]))
                out["kept"] += 1
    return out


def recent(memory: Memory, symbol: str, days: int = 7, today: date | None = None) -> dict | None:
    """The average score over the last `days`, weighted by articles, across the services. None when there is none."""
    today = today or local_now().date()
    try:
        _ready(memory)
        rows = _run(memory, "SELECT score, articles FROM news_sentiment WHERE symbol = ? AND day >= ?",
                    (symbol.upper(), (today - timedelta(days=days)).isoformat()))
    except Exception:
        return None
    n = sum(r["articles"] for r in rows)
    return {"score": round(sum(r["score"] * r["articles"] for r in rows) / n, 4), "articles": int(n)} if n else None


def due(memory: Memory, now=None, env=None) -> bool:
    now = now or local_now()
    return bool(configured(env)) and now.weekday() < 5 and now.hour >= 7 and memory.get(DAY_KEY) != now.date().isoformat()
