"""Live triggers: during US market hours Buddy checks the price of each symbol being followed once a minute and
says when something happens. Triggers, not predictions; code only, so they cost nothing.

  Followed (20 at most): the US watch list, the symbols with an open signal this week (recommend.py) and the
  trade ideas being watched (markets.py), in categories whose alerts are on.
  Prices: Finnhub quotes when its key is set (one small request per symbol a minute), else Alpaca's latest
  prices in one request. The free feeds cover part of the market's trading, so a price can lag a busy tape by a
  little. Option prices are NOT live on free data (15 minutes late): a trigger on the stock is the signal, and
  the message says to check the live option chain.
  Triggers, each said once a day per symbol:
    breakout / breakdown   through the high / low of the last 20 trading days
    big day                up or down 3% (then 6%) from yesterday's close; funds at 1.5% and 3%
    fast move              1.5% in five minutes (funds 0.8%)
  Every trigger is kept in the price book (`live_events`, a year), so the pattern learner can see what followed.

Off with BUDDY_LIVE_SIGNALS=0. Buddy never trades; a trigger is not advice.
"""

import json
import logging
import os
import time
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from . import recommend, signals
from .memory import Memory
from .tools import local_now

log = logging.getLogger(__name__)

MAX_SYMBOLS, MAX_A_DAY = 20, 8
MINUTE_KEY, TOLD_KEY, LEVELS_KEY, STATUS_KEY = "live:minute", "live:told", "live:levels", "live:status"
NY = ZoneInfo("America/New_York")
_ring: dict[str, list[tuple[float, float]]] = {}  # symbol: [(time, price)] for the last few minutes


def on() -> bool:
    return os.getenv("BUDDY_LIVE_SIGNALS", "1") not in ("", "0")


def _ready(memory: Memory) -> None:
    from . import pricebook  # noqa: PLC0415
    pricebook.run(memory, "CREATE TABLE IF NOT EXISTS live_events (seen TIMESTAMP, day DATE, symbol VARCHAR, "
                          "category VARCHAR, kind VARCHAR, price DOUBLE, change DOUBLE, detail VARCHAR)")


def category_of(symbol: str) -> str:
    return "commodities" if symbol in signals.COMMODITY_FUNDS else "etfs" if symbol in signals.ETFS else "stocks"


def followed(memory: Memory, today: date | None = None) -> list[dict]:
    """[{symbol, why}]: the watch list, this week's signals and the ideas being watched, in categories that are on."""
    from . import pricebook  # noqa: PLC0415
    out: dict[str, str] = {}
    try:
        for r in memory.db.execute("SELECT symbol FROM mkt_watch WHERE market = 'us' ORDER BY symbol"):
            out.setdefault(r["symbol"], "on your watch list")
        for r in memory.db.execute("SELECT symbol FROM mkt_setups WHERE market = 'us' AND status IN "
                                   "('watching', 'triggered') ORDER BY score DESC LIMIT 10"):
            out.setdefault(r["symbol"], "a trade idea Buddy is watching")
    except Exception:  # no markets tables yet
        pass
    try:
        recommend._ready(memory)
        since = ((today or local_now().date()) - timedelta(days=recommend.SCORE_AFTER_DAYS)).isoformat()
        for r in pricebook.run(memory, "SELECT key, action FROM recs WHERE day >= ? AND action != 'watch' "
                                       "ORDER BY id DESC", (since,)):
            out.setdefault(r["key"], f"Buddy's {r['action']} signal this week")
    except Exception:
        log.info("live: reading signals failed", exc_info=True)
    return [{"symbol": s, "why": w} for s, w in out.items()
            if recommend.enabled(memory, category_of(s))][:MAX_SYMBOLS]


def source(env=None) -> str:
    from . import finnhub  # noqa: PLC0415
    env = os.environ if env is None else env
    if finnhub.configured(env):
        return "finnhub"
    return "alpaca" if (env.get("BUDDY_ALPACA_KEY_ID") and env.get("BUDDY_ALPACA_SECRET")) else ""


def levels(memory: Memory, symbols: list[str], day: str, data=None) -> dict:
    """Per symbol, once a day: yesterday's close and the 20-day high and low, from daily prices."""
    saved = json.loads(memory.get(LEVELS_KEY) or "{}")
    if saved.get("day") != day:
        saved = {"day": day, "of": {}}
    missing = [s for s in symbols if s not in saved["of"]]
    if missing:
        from .markets import Data  # noqa: PLC0415
        got = (data or Data(memory)).bars("us", missing, days=60)
        for s in missing:
            rows = [b for b in got.get(s) or [] if b["d"] < day][-20:]
            saved["of"][s] = {"prev": rows[-1]["c"], "high": max(b["h"] for b in rows),
                              "low": min(b["l"] for b in rows)} if len(rows) >= 10 else None
        memory.set(LEVELS_KEY, json.dumps(saved))
    return saved["of"]


def prices(memory: Memory, symbols: list[str], data=None, quote=None, env=None) -> dict:
    """{symbol: price} now, from Finnhub (a quote each) or Alpaca (one request)."""
    if quote or source(env) == "finnhub":
        from . import finnhub  # noqa: PLC0415
        out = {}
        for s in symbols:
            try:
                q = quote(s) if quote else finnhub.quote(memory, s, env)
                if q:
                    out[s] = q["price"]
            except Exception as e:
                log.info("live quote %s: %s", s, e)
        return out
    from .markets import Data  # noqa: PLC0415
    return (data or Data(memory)).latest("us", symbols)


def triggers(symbol: str, price: float, lv: dict, before: float | None, told: list[str]) -> list[tuple[str, str]]:
    """[(kind, what happened)] that haven't been said today."""
    fund = category_of(symbol) != "stocks"
    big, huge, fast = (0.015, 0.03, 0.008) if fund else (0.03, 0.06, 0.015)
    change = price / lv["prev"] - 1
    found = []
    if price > lv["high"] * 1.001:
        found.append(("breakout", f"broke above its 20-day high (${lv['high']:,.2f})"))
    if price < lv["low"] * 0.999:
        found.append(("breakdown", f"fell below its 20-day low (${lv['low']:,.2f})"))
    for size, name in ((huge, "huge"), (big, "big")):
        if abs(change) >= size:
            found.append((f"{name}_{'up' if change > 0 else 'down'}",
                          f"is {'up' if change > 0 else 'down'} {abs(change):.1%} today"))
            break
    if before and abs(price / before - 1) >= fast:
        found.append((f"fast_{'up' if price > before else 'down'}",
                      f"moved {price / before - 1:+.1%} in five minutes"))
    if any(k.startswith("huge") for k, _ in found):  # the 6% line being crossed covers the 3% one
        told = told + [k.replace("huge", "big") for k, _ in found if k.startswith("huge")]
    return [(k, w) for k, w in found if k not in told]


def market_open(now: datetime | None = None) -> bool:
    t = (now or datetime.now(timezone.utc)).astimezone(NY)
    return t.weekday() < 5 and (9, 30) <= (t.hour, t.minute) <= (16, 0)


def due(memory: Memory, now: datetime | None = None) -> bool:
    now = now or datetime.now(timezone.utc)
    return on() and market_open(now) and memory.get(MINUTE_KEY) != now.strftime("%Y-%m-%dT%H:%M")


def check(memory: Memory, now: datetime | None = None, data=None, quote=None, env=None) -> list[str]:
    """One minute's check: the messages to send (empty most minutes)."""
    from . import pricebook  # noqa: PLC0415
    now = now or datetime.now(timezone.utc)
    day = now.astimezone(NY).date().isoformat()
    if not (quote or data or source(env)):
        memory.set(STATUS_KEY, "Live triggers need a Finnhub key (BUDDY_FINNHUB_KEY) or Alpaca keys.")
        return []
    follow = followed(memory, now.astimezone(NY).date())
    if not follow:
        memory.set(STATUS_KEY, "Nothing to follow yet: add symbols to your watch list.")
        return []
    symbols = [f["symbol"] for f in follow]
    lv = levels(memory, symbols, day, data)
    got = prices(memory, symbols, data, quote, env)
    told = json.loads(memory.get(TOLD_KEY) or "{}")
    if told.get("day") != day:
        told = {"day": day, "of": {}, "sent": 0}
    stamp, out = time.time() if now is None else now.timestamp(), []
    for f in follow:
        s, price = f["symbol"], got.get(f["symbol"])
        if not price or not lv.get(s):
            continue
        ring = [(t, p) for t, p in _ring.get(s, []) if stamp - t <= 420]
        before = next((p for t, p in ring if 240 <= stamp - t <= 420), None)
        _ring[s] = ring + [(stamp, price)]
        new = triggers(s, price, lv[s], before, told["of"].get(s, []))
        if not new:
            continue
        told["of"][s] = told["of"].get(s, []) + [k for k, _ in new]
        change = price / lv[s]["prev"] - 1
        _ready(memory)
        for kind, what in new:
            pricebook.run(memory, "INSERT INTO live_events VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                          (now.replace(tzinfo=None), day, s, category_of(s), kind, price, round(change, 4), what))
        if told["sent"] >= MAX_A_DAY:
            continue
        told["sent"] += 1
        options = (" Option prices on free data are 15 minutes late: check the live chain before acting."
                   if recommend.enabled(memory, "options") and category_of(s) == "stocks" else "")
        shown = "\n".join([f"**⚡ {s} ${price:,.2f}** ({change:+.1%} today)", *[f"- It {w}" for _, w in new],
                           f"- It's {f['why']}", *([f"-{options}"] if options else []),
                           "A live trigger, not advice: Buddy never trades."])
        out.append(f"⚡ {s} ${price:,.2f} {new[0][1]}. || {shown}")
    memory.set(TOLD_KEY, json.dumps(told))
    memory.set(STATUS_KEY, f"Following {len(symbols)} symbols by {'Finnhub' if quote or source(env) == 'finnhub' else 'Alpaca'}; "
                           f"{told['sent']} alerts today; last check {now.astimezone(NY):%H:%M} New York.")
    return out


def prune(memory: Memory, today: date | None = None) -> None:
    from . import pricebook  # noqa: PLC0415
    _ready(memory)
    pricebook.run(memory, "DELETE FROM live_events WHERE day < ?",
                  (((today or local_now().date()) - timedelta(days=signals.KEEP_DAYS)).isoformat(),))


def status(memory: Memory) -> dict:
    return {"on": on(), "price_source": source() or "none set up", "market_open_now": market_open(),
            "following": followed(memory), "last": memory.get(STATUS_KEY) or "not run yet",
            "note": "Stocks and funds only. Option prices are 15 minutes late on free data."}
