"""Finnhub (the owner's free key): company and market headlines, the earnings calendar, insider buying and
selling, analysts' ratings and a live quote. Only ticker symbols and dates go to Finnhub; never the owner's
holdings or watch list as such (a symbol is asked about because it is being discussed publicly or scanned).

Set the key with: sudo bash /opt/buddy/deploy/set.sh BUDDY_FINNHUB_KEY
"""

import hashlib
import logging
import urllib.parse
from datetime import date, datetime, timedelta, timezone

from . import gateway
from .memory import Memory
from .tools import ToolError

log = logging.getLogger(__name__)


def configured(env=None) -> bool:
    return bool(gateway._secret(env, "BUDDY_FINNHUB_KEY"))


def _get(memory: Memory, path: str, params: dict, env=None, fetch=None):
    if not configured(env):
        raise ToolError("Finnhub isn't set up: sudo bash /opt/buddy/deploy/set.sh BUDDY_FINNHUB_KEY")
    return gateway.call(memory, "finnhub", f"/api/v1/{path}?{urllib.parse.urlencode(params)}", env=env, fetch=fetch,
                        purpose="signals")


def company_news(memory: Memory, symbol: str, days: int = 3, env=None, fetch=None, today: date | None = None) -> list:
    today = today or date.today()
    got = _get(memory, "company-news", {"symbol": symbol, "from": (today - timedelta(days=days)).isoformat(),
                                        "to": today.isoformat()}, env, fetch) or []
    return [_headline(n, symbol) for n in got if n.get("headline")][:30]


def market_news(memory: Memory, env=None, fetch=None) -> list:
    got = _get(memory, "news", {"category": "general"}, env, fetch) or []
    return [_headline(n, n.get("related") or "") for n in got if n.get("headline")][:60]


def _headline(n: dict, symbol: str) -> dict:
    when = datetime.fromtimestamp(int(n.get("datetime") or 0), timezone.utc).date().isoformat() if n.get(
        "datetime") else ""
    key = n.get("id") or hashlib.sha1(n["headline"].encode(), usedforsecurity=False).hexdigest()[:12]  # an id, not a secret
    return {"id": f"fh_{key}", "title": str(n["headline"])[:300],
            "text": str(n.get("summary") or "")[:400], "url": str(n.get("url") or "")[:300],
            "source": str(n.get("source") or ""), "day": when, "symbol": symbol.upper()}


def earnings(memory: Memory, symbol: str, days: int = 14, env=None, fetch=None, today: date | None = None) -> str:
    """The next earnings date within `days`, or ''."""
    today = today or date.today()
    got = _get(memory, "calendar/earnings", {"symbol": symbol, "from": today.isoformat(),
                                             "to": (today + timedelta(days=days)).isoformat()}, env, fetch) or {}
    dates = sorted(str(e.get("date")) for e in got.get("earningsCalendar") or [] if e.get("date"))
    return dates[0] if dates else ""


def earnings_around(memory: Memory, symbol: str, day: date, env=None, fetch=None) -> list[dict]:
    """Earnings reports from four days before `day` up to it: [{"date", "hour"}]. hour is bmo (before the market
    opens), amc (after it closes), dmh (during the session) or ''."""
    got = _get(memory, "calendar/earnings", {"symbol": symbol, "from": (day - timedelta(days=4)).isoformat(),
                                             "to": day.isoformat()}, env, fetch) or {}
    return [{"date": str(e.get("date")), "hour": str(e.get("hour") or "").lower()}
            for e in got.get("earningsCalendar") or [] if e.get("date")]


def insiders(memory: Memory, symbol: str, env=None, fetch=None, today: date | None = None) -> float | None:
    """Insider sentiment over the last three months: Finnhub's monthly share purchase ratio, -100 (all selling)
    to 100 (all buying), averaged. None when there is none."""
    today = today or date.today()
    got = _get(memory, "stock/insider-sentiment", {"symbol": symbol, "from": (today - timedelta(days=100)).isoformat(),
                                                   "to": today.isoformat()}, env, fetch) or {}
    vals = [float(d["mspr"]) for d in got.get("data") or [] if d.get("mspr") is not None]
    return round(sum(vals) / len(vals), 1) if vals else None


def analysts(memory: Memory, symbol: str, env=None, fetch=None) -> dict | None:
    """The latest month of analyst ratings: how many say buy, hold, sell."""
    got = _get(memory, "stock/recommendation", {"symbol": symbol}, env, fetch) or []
    if not got:
        return None
    r = sorted(got, key=lambda x: str(x.get("period")))[-1]
    buy, hold = int(r.get("strongBuy") or 0) + int(r.get("buy") or 0), int(r.get("hold") or 0)
    sell = int(r.get("sell") or 0) + int(r.get("strongSell") or 0)
    total = buy + hold + sell
    return {"buy": buy, "hold": hold, "sell": sell, "buy_share": round(buy / total, 2)} if total else None


def quote(memory: Memory, symbol: str, env=None, fetch=None) -> dict | None:
    """Now: price, the day's change in %, high, low, open, yesterday's close. None when Finnhub has no price."""
    q = _get(memory, "quote", {"symbol": symbol}, env, fetch) or {}
    if not q.get("c"):
        return None
    return {"price": float(q["c"]), "change_pct": float(q.get("dp") or 0), "high": float(q.get("h") or 0),
            "low": float(q.get("l") or 0), "open": float(q.get("o") or 0), "prev_close": float(q.get("pc") or 0),
            "at": int(q.get("t") or 0)}


def facts(memory: Memory, symbol: str, env=None, fetch=None, today: date | None = None) -> dict:
    """What Finnhub knows about a symbol, each part on its own (one failing leaves the others)."""
    out = {}
    for name, fn in (("earnings_on", lambda: earnings(memory, symbol, 14, env, fetch, today)),
                     ("insiders", lambda: insiders(memory, symbol, env, fetch, today)),
                     ("analysts", lambda: analysts(memory, symbol, env, fetch)),
                     ("headlines", lambda: [n["title"] for n in company_news(memory, symbol, 3, env, fetch, today)][:5])):
        try:
            out[name] = fn()
        except Exception as e:
            log.info("finnhub %s %s: %s", name, symbol, e)
    return out


def check(memory=None, env=None, fetch=None) -> None:
    """Health check for Buddy Connect: a quote for SPY."""
    if not quote(memory, "SPY", env, fetch):
        raise ToolError("Finnhub answered without a price for SPY.")
