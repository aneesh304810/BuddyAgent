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

  History      so that a rule does not need months of live trades to be judged, every rule is also replayed over
               the past years of daily prices, day by day, seeing only what was known on each day. Those trades
               are kept apart and marked as history. One caution the page states: the list of companies is
               today's (those that failed or shrank are missing, which flatters the past).
  Your rules   a strategy document you upload becomes a rule here once you approve it (stratrules.py).

  Costs        every result shown has 0.2% taken off for buying and selling.
  The splits   a rule's history is also shown by the market when the trade opened (rising or falling), for the
               earlier 70% of the period against the latest 30%, and year by year, so one good stretch cannot
               carry it.
  The model    closed lab trades, live and replayed, are rows for the shared learning core (modeltrain.py, domain
               "lab"): did the trade make money after costs, given the figures it was opened on, the rule, and
               the market then. A model is used only if it passes the core's tests; then each new trade shows
               its odds. The rules keep trading either way, so the data keeps coming.

Daily prices decide stops and targets, so a real fill could differ; taxes are not taken off. A rule is called
unproven until 30 of its trades have closed.
"""

import json
import logging
from datetime import date

from .memory import Memory
from .tools import local_now

log = logging.getLogger("buddy.stratlab")

STAKE, A_DAY, PROVEN_AFTER = 1000.0, 3, 30
COST = 0.002  # buying and selling, taken off every result shown (0.2% of the trade)
STOP_MIN, STOP_MAX, STOP = 0.04, 0.12, 0.07
DAY_KEY, REPLAY_KEY = "stratlab:day", "stratlab:replay"
REPLAY_MONTHS, WINDOW = 60, 260  # how far back history is replayed; the days of prices a rule may look at
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
    status VARCHAR DEFAULT 'open', closed DATE, exit DOUBLE, ret DOUBLE, spy_ret DOUBLE, why_closed VARCHAR)""",
          "ALTER TABLE lab_trades ADD COLUMN IF NOT EXISTS backfilled BOOLEAN DEFAULT FALSE",
          "ALTER TABLE lab_trades ADD COLUMN IF NOT EXISTS regime VARCHAR DEFAULT ''"]


def _run(memory: Memory, sql: str, params: tuple = ()) -> list[dict]:
    from . import pricebook  # noqa: PLC0415
    return pricebook.run(memory, sql, params)


def _ready(memory: Memory) -> None:
    for statement in SCHEMA:
        _run(memory, statement)


def universe(memory: Memory | None = None) -> list[str]:
    """The built-in list of large US stocks and funds, plus the stock list chosen for the lab (benchmarks.py)."""
    from . import market_school  # noqa: PLC0415
    from .markets import US  # noqa: PLC0415
    extra = []
    if memory is not None:
        try:
            from . import benchmarks  # noqa: PLC0415
            extra = benchmarks.lab_symbols(memory)
        except Exception:
            log.info("lab: no chosen stock list", exc_info=True)
    return sorted(set(US) | set(market_school.SECTORS) | set(extra))


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
            "above_20": round(price / sma(closes, 20) - 1, 4),
            "above_50": round(price / a50 - 1, 4), "above_200": round(price / a200 - 1, 4),
            "avg50_above_200": a50 > a200, "ret_5": round(price / closes[-6] - 1, 4),
            "ret_252": round(price / closes[-252] - 1, 4), "below_high": round(price / max(closes[-252:]) - 1, 4),
            "ret_20": round(price / closes[-21] - 1, 4),
            "ret_63": round(price / closes[-64] - 1, 4), "ret_126": round(closes[-6] / closes[-127] - 1, 4),
            "high_20": price >= max(closes[-20:]), "high_252": price >= max(closes[-252:]),
            "volume_times": round(volumes[-1] / usual, 2) if usual else None,
            "atr_pct": round(swing / price, 4) if swing else None}


def market_index(bars: dict) -> dict:
    """{symbol: {day: (beta, volatility)}} from daily returns: beta is how much the stock has moved with the S&P 500
    fund over the last 252 trading days it shares with it, volatility its yearly swing over the last 63. Worked out
    with running sums, so a whole history costs one pass per symbol."""
    spy = {b["d"]: b["c"] for b in bars.get("SPY") or []}
    out = {}
    for s, bs in bars.items():
        days, xs, ys = [], [], []
        for a, b in zip(bs, bs[1:]):
            if a["d"] in spy and b["d"] in spy and a["c"] and spy[a["d"]]:
                days.append(b["d"])
                xs.append(b["c"] / a["c"] - 1)
                ys.append(spy[b["d"]] / spy[a["d"]] - 1)
        sx, sy, sxy, syy, sxx = [0.0], [0.0], [0.0], [0.0], [0.0]
        for x, y in zip(xs, ys):
            sx.append(sx[-1] + x); sy.append(sy[-1] + y); sxy.append(sxy[-1] + x * y)  # noqa: E702
            syy.append(syy[-1] + y * y); sxx.append(sxx[-1] + x * x)  # noqa: E702
        got = {}
        for i in range(120, len(days) + 1):
            j, k = max(0, i - 252), max(0, i - 63)
            n, m = i - j, i - k
            var = (syy[i] - syy[j]) / n - ((sy[i] - sy[j]) / n) ** 2
            cov = (sxy[i] - sxy[j]) / n - ((sx[i] - sx[j]) / n) * ((sy[i] - sy[j]) / n)
            swing = max(0.0, (sxx[i] - sxx[k]) / m - ((sx[i] - sx[k]) / m) ** 2) ** 0.5 * 252 ** 0.5
            got[days[i - 1]] = (round(cov / var, 3) if var > 1e-12 else None, round(swing, 4))
        out[s] = got
    return out


def _with_market(f: dict, index: dict, symbol: str, day: str) -> dict:
    beta, swing = (index.get(symbol) or {}).get(day) or (None, None)
    return {**f, "beta": beta, "vol_63": swing}


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


def rules_in_use(memory: Memory) -> dict:
    """Every rule that trades: the six built in, then the ones the owner added and approved.
    {key: {"name", "rule", "reads", "hold", "custom": the added rule or None}}"""
    out = {k: {"name": n, "rule": r, "reads": reads, "hold": hold, "custom": None}
           for k, (n, r, reads, hold) in STRATEGIES.items()}
    try:
        from . import stratrules  # noqa: PLC0415
        for r in stratrules.rules(memory, "active"):
            out[r["key"]] = {"name": r["name"], "rule": r["in_words"], "reads": "Your document: " + (r["source"] or "pasted text"),
                             "hold": r["rule"]["hold_days"], "custom": r["rule"]}
    except Exception:
        log.info("lab: added rules unavailable", exc_info=True)
    return out


def _picks(key: str, spec: dict, figs: dict, extra: dict | None) -> list[tuple[str, str]]:
    if spec["custom"]:
        from . import stratrules  # noqa: PLC0415
        return stratrules.picks(spec["custom"], figs)
    return picks(key, figs, extra)


def _regime(figs: dict) -> str:
    """The market when a trade opens, as known that day: rising when the S&P 500 fund is above its 200-day average."""
    spy = figs.get("SPY")
    return "" if not spy else "up" if spy["above_200"] > 0 else "down"


def features_of(strategy: str, features: dict, regime: str) -> dict:
    """A lab trade's figures for a model: the numbers it was opened on, which rule opened it, and the market then."""
    x = {k: float(v) for k, v in features.items() if isinstance(v, (int, float, bool)) and k != "model_p"}
    x["rule_" + strategy] = 1.0
    x["market_rising"] = 1.0 if regime == "up" else 0.0
    return x


def model_rows(memory: Memory, limit: int = 6000) -> list[dict]:
    """Closed lab trades (live and replayed) as rows for the learning core: did the trade make money after costs?"""
    _ready(memory)
    got = _run(memory, "SELECT closed, strategy, ret, features, regime FROM lab_trades WHERE status = 'closed' AND ret IS "
                       "NOT NULL ORDER BY closed DESC LIMIT ?", (limit,))
    return [{"when": str(t["closed"]), "x": features_of(t["strategy"], json.loads(t["features"] or "{}"), t["regime"] or ""),
             "y": int(t["ret"] - COST > 0), **({"regime": t["regime"]} if t["regime"] else {})} for t in reversed(got)]


def _stop_pct(f: dict) -> float:
    return max(STOP_MIN, min(2 * f["atr_pct"], STOP_MAX)) if f["atr_pct"] else STOP


def replay(memory: Memory, data=None, months: int = REPLAY_MONTHS, keys: list[str] | None = None) -> dict:
    """Run the rules over past daily prices, one day at a time, each day seeing only the prices up to that day.
    Finished trades are kept, marked as history; a rule's earlier history is replaced. Returns counts per rule."""
    from . import stratrules  # noqa: PLC0415
    _ready(memory)
    if data is None:
        from .markets import Data  # noqa: PLC0415
        data = Data(memory)
    specs = {k: v for k, v in rules_in_use(memory).items() if (not keys or k in keys) and k != "insiders"}
    bars = data.bars("us", sorted(set(universe(memory)) | {"SPY"}), days=months * 31 + 420)
    spy = bars.get("SPY") or []
    if len(spy) < WINDOW + 30:
        return {"trades": {}, "note": "there is not enough price history to replay"}
    at = {s: {b["d"]: i for i, b in enumerate(bs)} for s, bs in bars.items()}
    spy_close = {b["d"]: b["c"] for b in spy}
    index = market_index(bars)
    held: dict[str, dict[str, dict]] = {k: {} for k in specs}
    done: list[tuple] = []
    for day in [b["d"] for b in spy][WINDOW:]:
        today = date.fromisoformat(day)
        figs, bar = {}, {}
        for s, bs in bars.items():
            i = at[s].get(day)
            if i is None or i + 1 < WINDOW:
                continue
            bar[s] = bs[i]
            f = figures(bs[i + 1 - WINDOW: i + 1])
            if f:
                figs[s] = _with_market(f, index, s, day)
        for key, spec in specs.items():
            for s, t in list(held[key].items()):
                b = bar.get(s)
                if not b:
                    continue
                end = None
                if b["l"] <= t["stop"]:
                    end = (t["stop"], "stopped out")
                elif b["h"] >= t["target"]:
                    end = (t["target"], "reached the target")
                elif spec["custom"] and spec["custom"]["sell"] and s in figs and stratrules.passes(figs[s], spec["custom"]["sell"]):
                    end = (b["c"], "the rule's sell condition")
                elif (today - t["opened"]).days >= spec["hold"]:
                    end = (b["c"], f"{spec['hold']} days were up")
                if end:
                    done.append((key, s, t["opened"].isoformat(), t["entry"], t["stop"], t["target"], round(STAKE / t["entry"], 4),
                                 spec["hold"], t["why"], json.dumps(t["features"]), t["spy"], day, round(end[0], 4),
                                 round(end[0] / t["entry"] - 1, 5),
                                 round(spy_close[day] / t["spy"] - 1, 5) if t["spy"] else None, end[1], t["regime"]))
                    del held[key][s]
            opened = 0
            for s, why in _picks(key, spec, figs, {}):
                if opened >= A_DAY:
                    break
                if s in held[key]:
                    continue
                f = figs[s]
                pct = _stop_pct(f)
                held[key][s] = {"opened": today, "regime": _regime(figs), "entry": f["price"],
                                "stop": round(f["price"] * (1 - pct), 4),
                                "target": round(f["price"] * (1 + 2 * pct), 4), "why": why,
                                "features": {k: v for k, v in f.items() if k != "price"}, "spy": spy_close.get(day)}
                opened += 1
    for key in specs:
        _run(memory, "DELETE FROM lab_trades WHERE backfilled AND strategy = ?", (key,))
    for row in done:
        _run(memory, "INSERT INTO lab_trades (strategy, symbol, opened, entry, stop, target, shares, hold_days, why, features, "
                     "spy_entry, closed, exit, ret, spy_ret, why_closed, regime, status, backfilled) VALUES (?, ?, ?, ?, ?, ?, "
                     "?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'closed', TRUE)", row)
    counts = {k: sum(1 for r in done if r[0] == k) for k in specs}
    memory.set(REPLAY_KEY, json.dumps({"state": "done", "at": local_now().isoformat(timespec="minutes"), "months": months,
                                       "from": spy[WINDOW]["d"], "to": spy[-1]["d"], "trades": sum(counts.values())}))
    return {"trades": counts, "from": spy[WINDOW]["d"], "to": spy[-1]["d"]}


def replay_in_background(memory: Memory, keys: list[str] | None = None) -> str:
    """Start the replay without making the caller wait (it can take a minute or two). One at a time."""
    import threading  # noqa: PLC0415
    try:
        state = json.loads(memory.get(REPLAY_KEY) or "{}")
    except ValueError:
        state = {}
    if state.get("state") == "running":
        return "A replay is already running."
    memory.set(REPLAY_KEY, json.dumps({**state, "state": "running", "started": local_now().isoformat(timespec="minutes")}))

    def work():
        try:
            replay(memory, keys=keys)
        except Exception as e:
            log.exception("the history replay failed")
            memory.set(REPLAY_KEY, json.dumps({"state": "failed", "at": local_now().isoformat(timespec="minutes"),
                                               "why": f"{type(e).__name__}: {str(e)[:120]}"}))
    threading.Thread(target=work, name="lab-replay", daemon=True).start()
    return "Replaying the rules over past prices. This takes a minute or two; refresh to see the result."


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
    names = sorted(set(universe(memory)) | {h["symbol"] for h in held} | {"SPY"})
    bars = data.bars("us", names, days=400)
    out = {"closed": settle(memory, today, bars), "opened": {}}
    figs = {s: f for s, f in ((s, figures(b)) for s, b in bars.items() if b) if f}
    if not figs:
        return {**out, "note": "no daily prices could be read"}
    index = market_index(bars)
    try:  # this week's company figures (earnings surprise, margins, debt): today's values only
        from . import fundamentals  # noqa: PLC0415
        company = fundamentals.latest(memory, today)
    except Exception:
        company = {}
    figs = {s: {**_with_market(f, index, s, bars[s][-1]["d"]), **company.get(s, {})} for s, f in figs.items()}
    specs = rules_in_use(memory)
    from . import stratrules  # noqa: PLC0415
    for t in _run(memory, "SELECT id, strategy, symbol, entry, spy_entry FROM lab_trades WHERE status = 'open'"):
        sell = ((specs.get(t["strategy"]) or {}).get("custom") or {}).get("sell")
        f = figs.get(t["symbol"])
        if sell and f and stratrules.passes(f, sell):  # an added rule's own reason to sell, at today's close
            spy_now = (bars.get("SPY") or [{}])[-1].get("c")
            _run(memory, "UPDATE lab_trades SET status = 'closed', closed = ?, exit = ?, ret = ?, spy_ret = ?, "
                         "why_closed = ? WHERE id = ?",
                 (today.isoformat(), f["price"], round(f["price"] / t["entry"] - 1, 5),
                  round(spy_now / t["spy_entry"] - 1, 5) if spy_now and t["spy_entry"] else None,
                  "the rule's sell condition", t["id"]))
            out["closed"] += 1
    extra = _insiders(memory, today) if insiders is None else insiders
    spy = (bars.get("SPY") or [{}])[-1].get("c")
    have = {(h["strategy"], h["symbol"]) for h in _run(memory, "SELECT strategy, symbol FROM lab_trades WHERE "
                                                               "status = 'open'")}
    regime = _regime(figs)
    for key, spec in specs.items():
        opened, hold = 0, spec["hold"]
        for symbol, why in _picks(key, spec, figs, extra):
            if opened >= A_DAY:
                break
            if (key, symbol) in have:
                continue
            f = figs[symbol]
            stop_pct = _stop_pct(f)
            kept = {k: v for k, v in f.items() if k != "price"}
            try:  # a trained model's odds for this trade, when one has passed its test (modeltrain.py)
                from . import modeltrain  # noqa: PLC0415
                m = modeltrain.predict(memory, "lab", features_of(key, kept, regime))
            except Exception:
                m = None
            if m:
                kept["model_p"] = m["p"]
                why += f"; the trained model gives it a {m['p']:.0%} chance of making money"
            _run(memory, "INSERT INTO lab_trades (strategy, symbol, opened, entry, stop, target, shares, hold_days, why, "
                         "features, spy_entry, regime) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                 (key, symbol, today.isoformat(), f["price"], round(f["price"] * (1 - stop_pct), 4),
                  round(f["price"] * (1 + 2 * stop_pct), 4), round(STAKE / f["price"], 4), hold, why,
                  json.dumps(kept), spy, regime))
            opened += 1
        out["opened"][key] = opened
    return out


def due(memory: Memory, now=None) -> bool:
    now = now or local_now()
    return now.weekday() < 5 and now.hour >= 8 and memory.get(DAY_KEY) != now.date().isoformat()


def _record(rows: list[dict]) -> dict:
    """A record after costs: how many made money, the average result, and the average against the market."""
    net = [r["ret"] - COST for r in rows]
    against = [r["ret"] - COST - r["spy_ret"] for r in rows if r["spy_ret"] is not None]
    won = sum(1 for v in net if v > 0)
    return {"closed": len(rows), "won": won,
            "average_result": round(sum(net) / len(net), 4) if net else None,
            "average_vs_market": round(sum(against) / len(against), 4) if against else None,
            "profit": round(sum(STAKE * v for v in net), 2)}


def _splits(rows: list[dict]) -> dict:
    """History cut three ways, so one good stretch cannot carry a rule: by the market when the trade opened, the
    earlier 70% of the period against the latest 30% (which the rule was not picked on), and year by year."""
    if not rows:
        return {}
    rows = sorted(rows, key=lambda r: str(r["closed"]))
    cut = int(len(rows) * 0.7)
    earlier, latest = _record(rows[:cut]), _record(rows[cut:])
    years: dict[str, list] = {}
    for r in rows:
        years.setdefault(str(r["closed"])[:4], []).append(r)
    good = lambda rec: rec["closed"] >= 10 and (rec["average_vs_market"] or 0) > 0  # noqa: E731
    verdict = ("too few trades to say" if len(rows) < PROVEN_AFTER
               else "beat the market in both the earlier years and the latest stretch" if good(earlier) and good(latest)
               else "beat the market earlier but not in the latest stretch" if good(earlier)
               else "did not beat the market after costs")
    return {"rising_market": _record([r for r in rows if r.get("regime") == "up"]),
            "falling_market": _record([r for r in rows if r.get("regime") == "down"]),
            "earlier": earlier, "latest": latest,
            "by_year": [{"year": y, **_record(v)} for y, v in sorted(years.items())], "verdict": verdict}


def view(memory: Memory) -> dict:
    """For the Trading tab: each rule with its live record and its record on past prices, the latest live trades,
    and the rules the owner added (proposed, active or paused)."""
    _ready(memory)
    closed = _run(memory, "SELECT strategy, backfilled, ret, spy_ret, closed, regime FROM lab_trades WHERE status = 'closed'")
    live = _run(memory, "SELECT * FROM lab_trades WHERE NOT COALESCE(backfilled, FALSE) ORDER BY opened DESC, id DESC LIMIT 400")
    table = []
    for key, spec in rules_in_use(memory).items():
        mine = [r for r in closed if r["strategy"] == key]
        now, past = _record([r for r in mine if not r["backfilled"]]), _record([r for r in mine if r["backfilled"]])
        table.append({"key": key, "name": spec["name"], "rule": spec["rule"], "reads": spec["reads"],
                      "hold_days": spec["hold"], "yours": bool(spec["custom"]),
                      "open": sum(1 for r in live if r["strategy"] == key and r["status"] == "open"), **now,
                      "history": {**past, **_splits([r for r in mine if r["backfilled"]])},
                      "says": ("Unproven: " + (f"{now['closed']} of {PROVEN_AFTER} closed trades" if now["closed"]
                                               else "no closed trades yet")) if now["closed"] < PROVEN_AFTER
                      else f"{now['won']} of {now['closed']} closed trades made money"})
    names = {k: v["name"] for k, v in rules_in_use(memory).items()}

    def trade(r: dict) -> dict:
        return {"strategy": names.get(r["strategy"], r["strategy"]), "symbol": r["symbol"],
                "opened": str(r["opened"]), "entry": r["entry"], "stop": r["stop"], "target": r["target"],
                "why": r["why"], "closed": str(r["closed"]) if r["closed"] else "", "exit": r["exit"],
                "change": r["ret"], "market_change": r["spy_ret"], "why_closed": r["why_closed"] or ""}
    try:
        from . import stratrules  # noqa: PLC0415
        added, fields = stratrules.rules(memory), stratrules.FIELDS
    except Exception:
        added, fields = [], {}
    try:
        replayed = json.loads(memory.get(REPLAY_KEY) or "{}")
    except ValueError:
        replayed = {}
    return {"strategies": table, "open": [trade(r) for r in live if r["status"] == "open"][:40],
            "closed": sorted((trade(r) for r in live if r["status"] == "closed"), key=lambda t: t["closed"],
                             reverse=True)[:40],
            "added": added, "fields": fields, "replay": replayed,
            "history_note": "History is each rule replayed over past daily prices, seeing only what was known each day. "
                            "It uses today's list of companies, so those that failed or shrank are missing, which makes "
                            "the past look better than it was.",
            "note": f"Each trade is ${STAKE:,.0f} of pretend money, opened by rule every weekday morning with no AI "
                    f"deciding. Every result shown has {COST:.1%} taken off for the cost of buying and selling. Daily "
                    "closes decide stops and targets. Nothing real is bought."}
