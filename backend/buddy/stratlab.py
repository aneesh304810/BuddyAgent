"""Strategy lab: well-known trading rules, each paper-traded every weekday, to see which hold up and to collect
the data a model needs.

The Signals book (simbook.py) waits for a Reddit spike or Intelligence evidence. The lab does not wait for
anything: each weekday it reads the daily prices of about a hundred large US stocks and funds and lets each
rule below open paper trades by itself. No AI decides a trade, and no real order is ever placed.

  trend       Trend following: the price is above its 50-day average, that is above the 200-day average, and
              today's close is the highest of the last 20 days.
  momentum    Momentum: the three strongest of the list over the last six months (leaving out the last week),
              each still above its 200-day average.
  dip         Buy the dip: a stock in a long-term rise (above its 200-day average) that has fallen hard in the
              short term (14-day RSI under 30). Held 10 days at most.
  breakout    Breakout: a new one-year high on at least one and a half times the usual volume.
  sectors     Sector rotation: the three strongest of the eleven sector funds over three months.
  insiders    Insider buying: a company on the recommended watchlist or yours whose insiders have been net
              buyers for three months, with the price above its 50-day average (needs the Finnhub key).

  Every trade   is $1,000 of pretend money, bought at that day's close. The stop is twice the average daily range
                below (never under 4% or over 12%), the target twice as far above, and it is sold after the
                rule's time limit if neither is reached. A rule opens at most three new trades a day and holds a
                symbol once at a time.
  Every close   keeps the result, the S&P 500 fund's move over the same days, and the figures the trade was
                opened on (RSI, distance from the averages, recent returns, volume, daily range), so a model can
                later learn which conditions went with the wins.

Daily prices decide stops and targets, so a real fill could differ; costs and taxes are not taken off. A rule is
called unproven until 30 of its trades have closed.
"""

import json
import logging
from datetime import date

from .memory import Memory
from .tools import local_now

log = logging.getLogger("buddy.stratlab")

STAKE, A_DAY, PROVEN_AFTER = 1000.0, 3, 30
STOP_MIN, STOP_MAX, STOP = 0.04, 0.12, 0.07
DAY_KEY = "stratlab:day"
STRATEGIES = {  # key -> (name, the rule in words, what it reads, days held at most)
    "trend": ("Trend following", "Above the 50-day average, itself above the 200-day, at a 20-day high", "Price", 28),
    "momentum": ("Momentum", "The three strongest over six months, each above its 200-day average", "Price", 28),
    "dip": ("Buy the dip", "In a long-term rise but fallen hard in the short term (RSI under 30)", "Price", 10),
    "breakout": ("Breakout", "A new one-year high on at least 1.5 times the usual volume", "Price and volume", 28),
    "sectors": ("Sector rotation", "The three strongest sector funds over three months", "Sector funds", 28),
    "insiders": ("Insider buying", "Insiders net buyers for three months, price above its 50-day average",
                 "Company insiders' filings", 28),
}
SCHEMA = ["CREATE SEQUENCE IF NOT EXISTS lab_id",
          """CREATE TABLE IF NOT EXISTS lab_trades (
    id BIGINT PRIMARY KEY DEFAULT nextval('lab_id'), strategy VARCHAR, symbol VARCHAR, opened DATE, entry DOUBLE,
    stop DOUBLE, target DOUBLE, shares DOUBLE, hold_days INTEGER, why VARCHAR, features VARCHAR, spy_entry DOUBLE,
    status VARCHAR DEFAULT 'open', closed DATE, exit DOUBLE, ret DOUBLE, spy_ret DOUBLE, why_closed VARCHAR)"""]


def _run(memory: Memory, sql: str, params: tuple = ()) -> list[dict]:
    from . import pricebook  # noqa: PLC0415
    return pricebook.run(memory, sql, params)


def _ready(memory: Memory) -> None:
    for statement in SCHEMA:
        _run(memory, statement)


def universe() -> list[str]:
    from . import market_school  # noqa: PLC0415
    from .markets import US  # noqa: PLC0415
    return sorted(set(US) | set(market_school.SECTORS))


def figures(bars: list[dict]) -> dict | None:
    """The numbers every rule reads, from one symbol's daily prices. None without a year of them."""
    from .markets import atr, rsi, sma  # noqa: PLC0415
    closes = [b["c"] for b in bars]
    if len(closes) < 252:
        return None
    price, a50, a200 = closes[-1], sma(closes, 50), sma(closes, 200)
    swing = atr(bars)
    volumes = [b.get("v") or 0 for b in bars]
    usual = sum(volumes[-21:-1]) / 20
    return {"price": price, "rsi": round((rsi(closes) or [50.0])[-1], 1),
            "above_50": round(price / a50 - 1, 4), "above_200": round(price / a200 - 1, 4),
            "avg50_above_200": a50 > a200, "ret_20": round(price / closes[-21] - 1, 4),
            "ret_63": round(price / closes[-64] - 1, 4), "ret_126": round(closes[-6] / closes[-127] - 1, 4),
            "high_20": price >= max(closes[-20:]), "high_252": price >= max(closes[-252:]),
            "volume_times": round(volumes[-1] / usual, 2) if usual else None,
            "atr_pct": round(swing / price, 4) if swing else None}


def picks(strategy: str, figs: dict, extra: dict | None = None) -> list[tuple[str, str]]:
    """(symbol, why) for one rule, strongest first. `figs`: {symbol: figures}. `extra`: insiders' net buying."""
    from . import market_school  # noqa: PLC0415
    pct = lambda v: f"{v:+.1%}"  # noqa: E731
    if strategy == "trend":
        got = [(s, f) for s, f in figs.items() if f["above_50"] > 0 and f["avg50_above_200"] and f["high_20"]]
        got.sort(key=lambda x: -x[1]["ret_20"])
        return [(s, f"a 20-day high, {pct(f['above_50'])} above its 50-day average, which is above the 200-day")
                for s, f in got]
    if strategy == "momentum":
        got = sorted(((s, f) for s, f in figs.items() if f["above_200"] > 0), key=lambda x: -x[1]["ret_126"])[:3]
        return [(s, f"{pct(f['ret_126'])} over six months, among the three strongest, above its 200-day average")
                for s, f in got]
    if strategy == "dip":
        got = sorted(((s, f) for s, f in figs.items() if f["above_200"] > 0 and f["rsi"] < 30), key=lambda x: x[1]["rsi"])
        return [(s, f"RSI {f['rsi']:g} (fallen hard) while still {pct(f['above_200'])} above its 200-day average")
                for s, f in got]
    if strategy == "breakout":
        got = [(s, f) for s, f in figs.items() if f["high_252"] and (f["volume_times"] or 0) >= 1.5]
        got.sort(key=lambda x: -(x[1]["volume_times"] or 0))
        return [(s, f"a new one-year high on {f['volume_times']:g} times its usual volume") for s, f in got]
    if strategy == "sectors":
        got = sorted(((s, f) for s, f in figs.items() if s in market_school.SECTORS), key=lambda x: -x[1]["ret_63"])[:3]
        return [(s, f"{market_school.SECTORS[s]}: {pct(f['ret_63'])} over three months, among the three strongest "
                    "sectors") for s, f in got if f["ret_63"] > 0]
    if strategy == "insiders":
        got = sorted(((s, n) for s, n in (extra or {}).items() if n >= 20 and s in figs and figs[s]["above_50"] > 0),
                     key=lambda x: -x[1])
        return [(s, "insiders have been net buyers for three months, and the price is above its 50-day average")
                for s, _ in got]
    return []


def _insiders(memory: Memory, today: date) -> dict:
    """Net insider buying for the recommended watchlist and the owner's own symbols (at most 20 lookups)."""
    from . import finnhub  # noqa: PLC0415
    if not finnhub.configured():
        return {}
    symbols = []
    try:
        from . import watchpicks  # noqa: PLC0415
        symbols += sorted(watchpicks.symbols(memory))
        symbols += [r["symbol"] for r in memory.db.execute("SELECT symbol FROM mkt_watch WHERE market = 'us'")]
    except Exception:
        log.info("lab: no watchlists for the insiders rule", exc_info=True)
    out = {}
    for s in list(dict.fromkeys(symbols))[:20]:
        try:
            n = finnhub.insiders(memory, s, today=today)
            if n is not None:
                out[s] = float(n)
        except Exception:
            log.info("lab: insiders for %s unavailable", s)
    return out


def settle(memory: Memory, today: date, bars: dict) -> int:
    """Walk each open trade through the days since it opened: stop, target, or its time limit. Returns closes."""
    spy = {b["d"]: b["c"] for b in bars.get("SPY") or []}
    closed = 0
    for t in _run(memory, "SELECT * FROM lab_trades WHERE status = 'open'"):
        opened = str(t["opened"])
        rows = [b for b in bars.get(t["symbol"]) or [] if b["d"] > opened]
        done = None
        for b in rows:
            if b["l"] <= t["stop"]:  # both in one day: the stop is assumed first (the cautious reading)
                done = (b["d"], t["stop"], "stopped out")
            elif b["h"] >= t["target"]:
                done = (b["d"], t["target"], "reached the target")
            if done:
                break
        if not done and rows and (today - date.fromisoformat(opened)).days >= t["hold_days"]:
            done = (rows[-1]["d"], rows[-1]["c"], f"{t['hold_days']} days were up")
        if done:
            day, price, why = done
            _run(memory, "UPDATE lab_trades SET status = 'closed', closed = ?, exit = ?, ret = ?, spy_ret = ?, "
                         "why_closed = ? WHERE id = ?",
                 (day, round(price, 4), round(price / t["entry"] - 1, 5),
                  round(spy[day] / t["spy_entry"] - 1, 5) if spy.get(day) and t["spy_entry"] else None, why, t["id"]))
            closed += 1
    return closed


def run(memory: Memory, today: date | None = None, data=None, insiders: dict | None = None) -> dict:
    """One weekday: close what reached its stop, target or time limit, then let each rule open its trades."""
    today = today or local_now().date()
    _ready(memory)
    if data is None:
        from .markets import Data  # noqa: PLC0415
        data = Data(memory)
    held = _run(memory, "SELECT strategy, symbol FROM lab_trades WHERE status = 'open'")
    names = sorted(set(universe()) | {h["symbol"] for h in held} | {"SPY"})
    bars = data.bars("us", names, days=400)
    out = {"closed": settle(memory, today, bars), "opened": {}}
    figs = {s: f for s, f in ((s, figures(b)) for s, b in bars.items() if b) if f}
    if not figs:
        return {**out, "note": "no daily prices could be read"}
    extra = _insiders(memory, today) if insiders is None else insiders
    spy = (bars.get("SPY") or [{}])[-1].get("c")
    have = {(h["strategy"], h["symbol"]) for h in _run(memory, "SELECT strategy, symbol FROM lab_trades WHERE "
                                                               "status = 'open'")}
    for key, (_, _, _, hold) in STRATEGIES.items():
        opened = 0
        for symbol, why in picks(key, figs, extra):
            if opened >= A_DAY:
                break
            if (key, symbol) in have:
                continue
            f = figs[symbol]
            stop_pct = max(STOP_MIN, min(2 * f["atr_pct"], STOP_MAX)) if f["atr_pct"] else STOP
            _run(memory, "INSERT INTO lab_trades (strategy, symbol, opened, entry, stop, target, shares, hold_days, why, "
                         "features, spy_entry) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                 (key, symbol, today.isoformat(), f["price"], round(f["price"] * (1 - stop_pct), 4),
                  round(f["price"] * (1 + 2 * stop_pct), 4), round(STAKE / f["price"], 4), hold, why,
                  json.dumps({k: v for k, v in f.items() if k != "price"}), spy))
            opened += 1
        out["opened"][key] = opened
    return out


def due(memory: Memory, now=None) -> bool:
    now = now or local_now()
    return now.weekday() < 5 and now.hour >= 8 and memory.get(DAY_KEY) != now.date().isoformat()


def view(memory: Memory) -> dict:
    """For the Trading tab: each rule with its record, and the latest trades bought and sold."""
    _ready(memory)
    rows = _run(memory, "SELECT * FROM lab_trades ORDER BY opened DESC, id DESC LIMIT 600")
    table = []
    for key, (name, rule, reads, hold) in STRATEGIES.items():
        mine = [r for r in rows if r["strategy"] == key]
        done = [r for r in mine if r["status"] == "closed"]
        against = [r["ret"] - r["spy_ret"] for r in done if r["spy_ret"] is not None]
        won = sum(1 for r in done if r["ret"] > 0)
        table.append({"key": key, "name": name, "rule": rule, "reads": reads, "hold_days": hold,
                      "open": len(mine) - len(done), "closed": len(done), "won": won,
                      "average_result": round(sum(r["ret"] for r in done) / len(done), 4) if done else None,
                      "average_vs_market": round(sum(against) / len(against), 4) if against else None,
                      "profit": round(sum(r["shares"] * (r["exit"] - r["entry"]) for r in done), 2),
                      "says": ("Unproven: " + (f"{len(done)} of {PROVEN_AFTER} closed trades" if done
                                               else "no closed trades yet")) if len(done) < PROVEN_AFTER
                      else f"{won} of {len(done)} closed trades made money"})

    def trade(r: dict) -> dict:
        return {"strategy": STRATEGIES.get(r["strategy"], (r["strategy"],))[0], "symbol": r["symbol"],
                "opened": str(r["opened"]), "entry": r["entry"], "stop": r["stop"], "target": r["target"],
                "why": r["why"], "closed": str(r["closed"]) if r["closed"] else "", "exit": r["exit"],
                "change": r["ret"], "market_change": r["spy_ret"], "why_closed": r["why_closed"] or ""}
    return {"strategies": table, "open": [trade(r) for r in rows if r["status"] == "open"][:40],
            "closed": sorted((trade(r) for r in rows if r["status"] == "closed"), key=lambda t: t["closed"],
                             reverse=True)[:40],
            "note": f"Each trade is ${STAKE:,.0f} of pretend money, opened by rule every weekday morning with no AI "
                    "deciding. Daily closes decide stops and targets; no costs are taken off. Nothing real is bought."}
