"""Short selling in the watchlist's US stocks, from FINRA. For the owner's personal, non-commercial use only.

Two kinds of public data from FINRA, the US brokers' regulator:

  short sale volume   each trading day, for every stock traded away from the exchanges (dark pools, wholesalers),
                      how many of the shares sold were sold short. One file a day (cdn.finra.org); Buddy downloads
                      it, keeps only the watchlist's stocks, and works out the short share of the day's volume
                      against the stock's own average over the last 20 trading days. Nothing is sent but the date.
  short interest      twice a month, how many shares were held short at the settlement date, the change from the
                      time before, and "days to cover" (short shares divided by average daily volume). Asked of
                      FINRA's API for the watchlist's ticker symbols, from about 8 business days after each
                      settlement date (mid-month and month-end), when FINRA publishes it: once a day from 23 days
                      after the latest settlement date held until the next one arrives.

How to read it. About half of off-exchange volume is usually marked short, because market makers sell short to
fill buyers' orders: that is not a bet against the stock. What says something is a change against the stock's
own usual level, and short interest that is rising or takes many days to cover.

FINRA's terms. FINRA's data is for personal, non-commercial use (its Equity Data terms; redistribution to others
and commercial use need a FINRA licence). So Buddy keeps only the watchlist's stocks, shows the numbers on the
Investments page by code, and does not give them to an AI model or use them in its learning or paper trading.
The service can be switched off on the Security page like any other.
"""

import json
import logging
import re
import time
from datetime import date, datetime, timedelta

from . import gateway
from .memory import Memory
from .tools import local_now

log = logging.getLogger(__name__)

FILE_URL = "https://cdn.finra.org/equity/regsho/daily/CNMSshvol{day}.txt"
SI_URL = "https://api.finra.org/data/group/otcMarket/name/consolidatedShortInterest"
SI_FIELDS = ["symbolCode", "settlementDate", "issueName", "currentShortPositionQuantity",
             "previousShortPositionQuantity", "averageDailyVolumeQuantity", "daysToCoverQuantity", "changePercent"]
MAX_SYMBOLS, BACKFILL_DAYS, AVERAGE_DAYS, MIN_DAYS = 50, 25, 20, 10
UNUSUAL_POINTS = 0.10  # a day's short share this far from the stock's own average is "more" or "less than usual"
HIGH_COVER, BIG_CHANGE = 5.0, 20.0  # days to cover; short interest change in percent
SI_WAIT_DAYS = 23  # settlement dates are about 15 days apart, each published about 8 business days later
KEEP_DAYS = 400
PACE = 1.5  # seconds between daily files when catching up (not with a test's fetch)
DAY_KEY, SI_KEY, ASKED_KEY = "finra:volume_on", "finra:interest_on", "finra:interest_asked"
TERMS = ("FINRA data, for your personal, non-commercial use only (FINRA's terms). Shown here by code; it isn't given "
         "to an AI model or used in Buddy's learning.")
NOTE = ("About half of off-exchange volume is usually marked short: market makers sell short to fill buyers' orders, "
        "so that isn't a bet against the stock. What says something is a change against the stock's own usual "
        "level. Short sale volume covers off-exchange trading only. Short interest counts positions held at a "
        "settlement date and is published about 8 business days later. For learning, not advice.")
SYMBOL = re.compile(r"^[A-Z][A-Z0-9.\-/]{0,9}$")

SCHEMA = (
    "CREATE TABLE IF NOT EXISTS finra_short_volume (symbol VARCHAR, day DATE, short_volume DOUBLE, "
    "short_exempt DOUBLE, total_volume DOUBLE, PRIMARY KEY (symbol, day))",
    "CREATE TABLE IF NOT EXISTS finra_short_interest (symbol VARCHAR, settlement DATE, name VARCHAR, "
    "short_shares DOUBLE, previous_shares DOUBLE, average_volume DOUBLE, days_to_cover DOUBLE, change_pct DOUBLE, "
    "PRIMARY KEY (symbol, settlement))",
)


def _run(memory: Memory, sql: str, params: tuple = ()) -> list[dict]:
    from . import pricebook  # noqa: PLC0415
    return pricebook.run(memory, sql, params)


def _ready(memory: Memory) -> None:
    for statement in SCHEMA:
        _run(memory, statement)


def _number(value) -> float | None:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if f == f else None


def _day(value) -> date | None:
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def norm(symbol: str) -> str:
    """One spelling for a share class: BRK.B, BRK-B and FINRA's BRK/B are the same stock."""
    return re.sub(r"[.\-/]", ".", str(symbol or "").strip().upper())


def watched(memory: Memory) -> list[str]:
    """The watchlist's US stocks (the owner chose them), up to MAX_SYMBOLS."""
    try:
        rows = memory.db.execute("SELECT symbol FROM mkt_watch WHERE market = 'us' ORDER BY symbol").fetchall()
    except Exception:
        return []
    return [s for s in dict.fromkeys(norm(r["symbol"]) for r in rows) if SYMBOL.match(s)][:MAX_SYMBOLS]


def parse_file(body, symbols: set[str]) -> list[tuple]:
    """(symbol, day, short, exempt, total) for the given symbols from one daily file:
    Date|Symbol|ShortVolume|ShortExemptVolume|TotalVolume|Market, and a record count on the last line."""
    text = body.decode("utf-8", "replace") if isinstance(body, bytes) else str(body or "")
    out = []
    for line in text.splitlines()[1:]:
        parts = line.strip().split("|")
        if len(parts) < 5 or norm(parts[1]) not in symbols:
            continue
        day = _day(f"{parts[0][:4]}-{parts[0][4:6]}-{parts[0][6:8]}")
        short, exempt, total = _number(parts[2]), _number(parts[3]), _number(parts[4])
        if day and short is not None and total:
            out.append((norm(parts[1]), day, short, exempt or 0.0, total))
    return out


def _weekdays(start: date, end: date) -> list[date]:
    days, d = [], start
    while d <= end:
        if d.weekday() < 5:
            days.append(d)
        d += timedelta(days=1)
    return days


def latest_volume_day(memory: Memory) -> date | None:
    try:
        _ready(memory)
        got = _run(memory, "SELECT MAX(day) AS day FROM finra_short_volume")
    except Exception:
        return None
    return _day(got[0]["day"]) if got and got[0]["day"] else None


def pull_volume(memory: Memory, fetch=None, now: datetime | None = None, max_days: int = BACKFILL_DAYS) -> dict:
    """Read the daily files Buddy doesn't have yet (the last BACKFILL_DAYS weekdays the first time), keeping only
    the watchlist's stocks. A day with no file (a holiday, or not out yet) is skipped. Today's file counts only
    after 7 pm, when FINRA has posted it."""
    now = now or local_now()
    symbols = set(watched(memory))
    out = {"days": 0, "rows": 0, "missing": 0, "symbols": len(symbols)}
    if not symbols:
        return {**out, "note": "No US stocks on the watchlist."}
    if gateway.is_off(memory, "finra"):  # its "switched off" refusal would read as a missing file
        return {**out, "note": "FINRA is switched off."}
    _ready(memory)
    last = now.date() if now.hour >= 19 else now.date() - timedelta(days=1)
    latest = latest_volume_day(memory)
    first = latest + timedelta(days=1) if latest else last - timedelta(days=BACKFILL_DAYS * 7 // 5 + 2)
    days = _weekdays(first, last)[-max_days:]
    for i, day in enumerate(days):
        if i and fetch is None:
            time.sleep(PACE)
        try:
            body = gateway.call_url(memory, "finra", [FILE_URL.format(day=day.strftime("%Y%m%d"))], raw=True,
                                    fetch=fetch, purpose="short sale volume")
        except gateway.ConnectError as e:
            if e.status in (403, 404):  # no file that day: a holiday, or not posted yet
                out["missing"] += 1
                continue
            raise
        rows = parse_file(body, symbols)
        for r in rows:
            _run(memory, "INSERT OR REPLACE INTO finra_short_volume (symbol, day, short_volume, short_exempt, "
                         "total_volume) VALUES (?, ?, ?, ?, ?)", (r[0], r[1].isoformat(), r[2], r[3], r[4]))
        out["days"] += 1
        out["rows"] += len(rows)
    _run(memory, "DELETE FROM finra_short_volume WHERE day < ?", ((now.date() - timedelta(days=KEEP_DAYS)).isoformat(),))
    memory.set(DAY_KEY, now.date().isoformat())
    return out


def latest_settlement(memory: Memory) -> date | None:
    try:
        _ready(memory)
        got = _run(memory, "SELECT MAX(settlement) AS day FROM finra_short_interest")
    except Exception:
        return None
    return _day(got[0]["day"]) if got and got[0]["day"] else None


def _asked(memory: Memory) -> set[str]:
    """The symbols short interest was asked for already (FINRA may have none for some, like a share class)."""
    try:
        return set(json.loads(memory.get(ASKED_KEY) or "[]"))
    except ValueError:
        return set()


def pull_interest(memory: Memory, fetch=None, today: date | None = None) -> dict:
    """Short interest for the watchlist's stocks, in one request: the last four months the first time (or for a
    stock just added), then from the latest settlement date held."""
    today = today or local_now().date()
    symbols = watched(memory)
    out = {"rows": 0, "symbols": len(symbols)}
    if not symbols:
        return {**out, "note": "No US stocks on the watchlist."}
    if gateway.is_off(memory, "finra"):
        return {**out, "note": "FINRA is switched off."}
    _ready(memory)
    latest = latest_settlement(memory)
    new = [s for s in symbols if s not in _asked(memory)]
    start = today - timedelta(days=120) if (latest is None or new) else latest - timedelta(days=1)
    body = {"limit": 1000, "fields": SI_FIELDS,
            "domainFilters": [{"fieldName": "symbolCode", "values": symbols}],
            "dateRangeFilters": [{"fieldName": "settlementDate", "startDate": start.isoformat(),
                                  "endDate": today.isoformat()}]}
    got = gateway.call_url(memory, "finra", [SI_URL], data=json.dumps(body).encode(),
                           headers={"Content-Type": "application/json", "Accept": "application/json"}, fetch=fetch,
                           purpose="short interest")
    for r in got if isinstance(got, list) else []:
        symbol, day = norm(r.get("symbolCode")), _day(r.get("settlementDate"))
        short = _number(r.get("currentShortPositionQuantity"))
        if not symbol or day is None or short is None:
            continue
        _run(memory, "INSERT OR REPLACE INTO finra_short_interest (symbol, settlement, name, short_shares, "
                     "previous_shares, average_volume, days_to_cover, change_pct) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
             (symbol, day.isoformat(), str(r.get("issueName") or "")[:120], short,
              _number(r.get("previousShortPositionQuantity")), _number(r.get("averageDailyVolumeQuantity")),
              _number(r.get("daysToCoverQuantity")), _number(r.get("changePercent"))))
        out["rows"] += 1
    _run(memory, "DELETE FROM finra_short_interest WHERE settlement < ?", ((today - timedelta(days=KEEP_DAYS)).isoformat(),))
    memory.set(SI_KEY, today.isoformat())
    memory.set(ASKED_KEY, json.dumps(sorted(set(symbols))))
    return out


def volume_due(memory: Memory, now: datetime | None = None) -> bool:
    """Once a day, after 7 pm (FINRA posts the day's file in the evening, US Eastern), or the next morning."""
    now = now or local_now()
    return bool(watched(memory)) and memory.get(DAY_KEY) != now.date().isoformat() and (now.hour >= 19 or now.hour < 12)


def interest_due(memory: Memory, now: datetime | None = None) -> bool:
    """Once a day at most, when a new report should be out, or a stock on the watchlist has none yet."""
    now = now or local_now()
    symbols = watched(memory)
    if not symbols or memory.get(SI_KEY) == now.date().isoformat():
        return False
    latest = latest_settlement(memory)
    if latest is None or set(symbols) - _asked(memory):
        return True
    return (now.date() - latest).days >= SI_WAIT_DAYS


def nightly(memory: Memory, fetch=None, now: datetime | None = None) -> str:
    """The day's file and, when due, short interest. A line for the log."""
    now = now or local_now()
    said = []
    if volume_due(memory, now):
        got = pull_volume(memory, fetch=fetch, now=now)
        said.append(f"short sale volume: {got['days']} days, {got['rows']} rows")
    if interest_due(memory, now):
        got = pull_interest(memory, fetch=fetch, today=now.date())
        said.append(f"short interest: {got['rows']} rows")
    return "FINRA " + "; ".join(said) if said else ""


def _shares(n: float | None) -> str:
    if n is None:
        return "–"
    for size, word in ((1e9, "billion"), (1e6, "million"), (1e3, "thousand")):
        if abs(n) >= size:
            return f"{n / size:.1f} {word}"
    return f"{n:,.0f}"


def stock(memory: Memory, symbol: str, volume: list[dict], interest: list[dict]) -> dict:
    """One stock's short selling, worked out against its own history, with a plain reading."""
    out = {"symbol": symbol, "flags": []}
    words = []
    if volume:
        now = volume[-1]
        share = now["short_volume"] / now["total_volume"] if now["total_volume"] else None
        before = [v["short_volume"] / v["total_volume"] for v in volume[-AVERAGE_DAYS - 1:-1] if v["total_volume"]]
        usual = sum(before) / len(before) if len(before) >= MIN_DAYS else None
        out.update({"day": str(_day(now["day"])), "short_share": round(share, 3) if share is not None else None,
                    "usual_share": round(usual, 3) if usual is not None else None, "days": len(before),
                    "volume": now["total_volume"], "trend": [round(v["short_volume"] / v["total_volume"], 3)
                                                             for v in volume[-AVERAGE_DAYS:] if v["total_volume"]]})
        if share is not None:
            text = f"On {out['day']}, {share:.0%} of its off-exchange volume was short sales"
            if usual is None:
                text += f" (only {len(before)} earlier days to compare with so far)."
            else:
                gap = share - usual
                text += f", against {usual:.0%} on an average day over the {len(before)} trading days before"
                text += (": more short selling than usual." if gap >= UNUSUAL_POINTS else
                         ": less short selling than usual." if gap <= -UNUSUAL_POINTS else ": about usual.")
                if abs(gap) >= UNUSUAL_POINTS:
                    out["flags"].append("more short selling than usual" if gap > 0 else "less short selling than usual")
            words.append(text)
    if interest:
        si = interest[-1]
        out.update({"settlement": str(_day(si["settlement"])), "name": si["name"], "short_shares": si["short_shares"],
                    "change_pct": si["change_pct"], "days_to_cover": si["days_to_cover"]})
        text = f"{_shares(si['short_shares'])} shares were held short on {out['settlement']}"
        if si["change_pct"] is not None:
            text += f" ({'up' if si['change_pct'] >= 0 else 'down'} {abs(si['change_pct']):.1f}% from the time before)"
        if si["days_to_cover"] is not None:
            text += f", {si['days_to_cover']:.1f} days of average volume to buy back"
        words.append(text + ".")
        if (si["days_to_cover"] or 0) >= HIGH_COVER:
            out["flags"].append(f"{si['days_to_cover']:.1f} days to cover")
        if (si["change_pct"] or 0) >= BIG_CHANGE:
            out["flags"].append(f"short interest up {si['change_pct']:.0f}%")
    out["reading"] = " ".join(words)
    return out


def page(memory: Memory) -> dict:
    """The watchlist's short selling, the unusual ones first."""
    symbols = watched(memory)
    base = {"terms": TERMS, "note": NOTE, "stocks": [],
            "source": "FINRA daily short sale volume and consolidated short interest"}
    if gateway.is_off(memory, "finra"):
        return {**base, "empty": "FINRA is switched off on the Security page."}
    if not symbols:
        return {**base, "empty": "Add US stocks to the watchlist to see their short selling here."}
    try:
        _ready(memory)
        marks = ", ".join("?" * len(symbols))
        volume = _run(memory, f"SELECT * FROM finra_short_volume WHERE symbol IN ({marks}) ORDER BY symbol, day",  # nosec B608: placeholders only
                      tuple(symbols))
        interest = _run(memory, f"SELECT * FROM finra_short_interest WHERE symbol IN ({marks}) "  # nosec B608: placeholders only
                                "ORDER BY symbol, settlement", tuple(symbols))
    except Exception:
        log.info("finra: none held", exc_info=True)
        volume, interest = [], []
    by_v: dict[str, list] = {}
    by_i: dict[str, list] = {}
    for r in volume:
        by_v.setdefault(r["symbol"], []).append(r)
    for r in interest:
        by_i.setdefault(r["symbol"], []).append(r)
    stocks = [stock(memory, s, by_v.get(s, []), by_i.get(s, [])) for s in symbols if by_v.get(s) or by_i.get(s)]
    stocks.sort(key=lambda x: (not x["flags"], x["symbol"]))
    missing = [s for s in symbols if not (by_v.get(s) or by_i.get(s))]
    return {**base, "stocks": stocks, "waiting": missing,
            "empty": "" if stocks else "Nothing read yet: Buddy reads FINRA's files each evening, or press Read now.",
            "as_of": max((x.get("day", "") for x in stocks), default="")}


def check(memory, env=None, fetch=None) -> None:
    """Buddy Connect's test: one row of short interest, for no one's stock in particular."""
    gateway.call_url(memory, "finra", [SI_URL], data=json.dumps({"limit": 1}).encode(),
                     headers={"Content-Type": "application/json", "Accept": "application/json"}, fetch=fetch,
                     purpose="health")
