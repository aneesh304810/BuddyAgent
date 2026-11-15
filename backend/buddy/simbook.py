"""Buddy's practice book: every signal Buddy makes is paper-traded automatically, by fixed rules, so there is an
honest record of what following the signals would have done, and the signal engine can learn from it.

This is Buddy's own test of its own signals, separate from the owner's paper portfolio (paper.py). No real
money, no real orders.

  A buy signal        opens a paper position at that day's close, sized by risk (see Money management), with a
                      stop below, a target twice as far above, closed after 28 days if neither is reached, or
                      when Buddy signals sell on it
  A sell or caution   is scored as an "avoid": right if the price then falls 7%, wrong if it rises 7%, else
                      judged by where it stands after 28 days
  Every close         keeps the result, the S&P 500 fund's move over the same days (the honest comparison), and
                      the reasons the signal was made on (tone, trend, insiders, analysts, the checklists' read)

  Money management, the same rules a careful trader would write down first:
    the book          starts at $100,000 of pretend money; its value is that plus the closed trades' results
    the stop          twice the stock's average daily range below the entry (a calm stock gets a tight stop, a
                      jumpy one a wide stop), never under 4% or over 12%; 7% when the range isn't known
    risk per trade    1% of the book: the position is sized so that being stopped out loses that much, so a
                      wide stop means a smaller position
    position cap      no position over 20% of the book, and never more than the cash left
    total risk cap    all open positions together may not risk more than 6% of the book; a signal that would
                      break it is written down as skipped, not traded
  The results show the book's value, its worst fall from a peak (drawdown), and each trade's result in units of
  the amount risked (R), which is how the rules are judged.

  Learning: for each reason, how often trades won when that reason pointed the trade's way. Once a reason has
  15 closed trades behind it, its weight in the signal score moves between 0.5 and 1.5 (a reason that keeps
  being right counts for more, one that keeps being wrong for less). The weights and the counts behind them are
  kept and shown; nothing is learned from fewer trades than that.

Daily prices decide stops and targets (the day's low and high), so a real fill could differ; costs and taxes are
not taken off. A few dozen trades prove little: results say so until there are enough.
"""

import json
import logging
from datetime import date

from .memory import Memory
from .tools import local_now

log = logging.getLogger(__name__)

START, RISK, MAX_POSITION, MAX_OPEN_RISK = 100_000.0, 0.01, 0.20, 0.06
STOP, HOLD_DAYS = 0.07, 28  # the stop when the stock's daily range isn't known; avoids are judged at 7% either way
STOP_MIN, STOP_MAX = 0.04, 0.12
MIN_TO_LEARN, MIN_TO_TRUST = 15, 30
PARTS = ("tone", "trend", "insiders", "analysts")
WEIGHTS_KEY = "simbook:weights"


def _ready(memory: Memory) -> None:
    from . import pricebook  # noqa: PLC0415
    pricebook.run(memory, "CREATE SEQUENCE IF NOT EXISTS sim_id")
    pricebook.run(memory, "CREATE TABLE IF NOT EXISTS sim_trades (id BIGINT PRIMARY KEY DEFAULT nextval('sim_id'), "
                          "opened DATE, symbol VARCHAR, category VARCHAR, side VARCHAR, entry DOUBLE, "
                          "shares DOUBLE, stop DOUBLE, target DOUBLE, parts VARCHAR, read VARCHAR, "
                          "spy_entry DOUBLE, status VARCHAR DEFAULT 'open', closed DATE, exit DOUBLE, "
                          "why VARCHAR, ret DOUBLE, spy_ret DOUBLE)")
    pricebook.run(memory, "ALTER TABLE sim_trades ADD COLUMN IF NOT EXISTS risk DOUBLE")
    pricebook.run(memory, "ALTER TABLE sim_trades ADD COLUMN IF NOT EXISTS features VARCHAR")


FIGURES = ("score", "rsi", "tone", "mentions", "times_usual", "atr_pct", "insiders")


def figures(facts: dict) -> dict:
    """The numbers behind a signal, kept with its paper trade so a model can learn from them later."""
    out = {k: facts[k] for k in FIGURES if isinstance(facts.get(k), (int, float))}
    lens = facts.get("lenses") or {}
    out["business"] = {"strong": 1, "weak": -1}.get(lens.get("business"), 0)
    out.update({k: v for k, v in (facts.get("factors") or {}).items() if isinstance(v, (int, float))})  # factors.py
    return out


def features_of(t: dict) -> dict:
    """A trade's features for training and for the model's odds: the reasons' signs (turned to point the
    trade's way), the figures, and the checklists' read of the price. `t`: a sim_trades row, or the same keys."""
    way = 1 if t.get("side", "long") == "long" else -1
    x = {f"reason_{k}": v * way for k, v in (json.loads(t["parts"]) if isinstance(t.get("parts"), str)
                                             else t.get("parts") or {}).items()}
    x.update(json.loads(t["features"]) if isinstance(t.get("features"), str) else t.get("features") or {})
    x["is_buy"] = 1 if way == 1 else 0
    for name in ("cheap", "expensive", "no profits to value"):
        x[f"price_{name.split()[0]}"] = 1 if t.get("read") == name else 0
    return x


def money(memory: Memory) -> dict:
    """The book now: its value, the cash not in positions, and what the open positions risk."""
    from . import pricebook  # noqa: PLC0415
    _ready(memory)
    longs = pricebook.run(memory, "SELECT status, shares, entry, exit, stop FROM sim_trades WHERE side = 'long' "
                                  "AND status IN ('open', 'closed')")
    equity = START + sum(t["shares"] * (t["exit"] - t["entry"]) for t in longs if t["status"] == "closed")
    held = [t for t in longs if t["status"] == "open"]
    return {"equity": round(equity, 2), "cash": round(equity - sum(t["shares"] * t["entry"] for t in held), 2),
            "open_risk": round(sum(t["shares"] * (t["entry"] - t["stop"]) for t in held), 2)}


def size(price: float, atr_pct: float | None, book: dict) -> dict:
    """The stop, target and number of shares for a buy at `price`, by the rules above. shares 0 with a reason
    when the rules say not to take it."""
    stop_pct = max(STOP_MIN, min(2 * atr_pct, STOP_MAX)) if atr_pct else STOP
    risk = book["equity"] * RISK
    out = {"stop_pct": round(stop_pct, 4), "stop": round(price * (1 - stop_pct), 4),
           "target": round(price * (1 + 2 * stop_pct), 4), "shares": 0.0, "risk": 0.0, "skipped": ""}
    if book["open_risk"] + risk > book["equity"] * MAX_OPEN_RISK + 0.005:
        out["skipped"] = "the open positions already risk the most the book allows (6%)"
        return out
    shares = min(risk / (price * stop_pct), book["equity"] * MAX_POSITION / price, max(book["cash"], 0) / price)
    if shares * price < 100:
        out["skipped"] = "there isn't enough cash left in the book"
        return out
    out["shares"], out["risk"] = round(shares, 4), round(shares * price * stop_pct, 2)
    return out


def open_from(memory: Memory, signals: list[dict], today: date, spy: float | None) -> int:
    """Paper-trade today's signals (recommend.judge results). Returns how many trades were opened."""
    from . import pricebook  # noqa: PLC0415
    _ready(memory)
    opened = 0
    for j in signals:
        if j["action"] == "watch" or not j.get("price"):
            continue
        side = "long" if j["action"] == "buy" else "avoid"
        held = pricebook.run(memory, "SELECT id, side, entry, spy_entry FROM sim_trades WHERE symbol = ? AND "
                                     "status = 'open'", (j["key"],))
        if side == "avoid":
            for h in held:
                if h["side"] == "long":  # Buddy now says sell: the paper position is closed at today's price
                    _close(memory, h, today, j["price"], "Buddy's sell signal", spy)
        if any(h["side"] == side for h in held):
            continue
        price = j["price"]
        facts = j.get("facts") or {}
        if side == "long":
            plan = size(price, facts.get("atr_pct"), money(memory))
            stop, target, shares, risk = plan["stop"], plan["target"], plan["shares"], plan["risk"]
        else:  # an avoid puts no money on: it is wrong 7% up, right 7% down
            plan, stop, target, shares, risk = {"skipped": ""}, price * (1 + STOP), price * (1 - STOP), 0.0, 0.0
        pricebook.run(memory, "INSERT INTO sim_trades (opened, symbol, category, side, entry, shares, stop, target, "
                              "parts, read, spy_entry, risk, status, why, features) VALUES (?, ?, ?, ?, ?, ?, ?, ?, "
                              "?, ?, ?, ?, ?, ?, ?)",
                      (today.isoformat(), j["key"], j["category"], side, price, shares, round(stop, 4),
                       round(target, 4), json.dumps(facts.get("parts") or {}),
                       (facts.get("lenses") or {}).get("price", ""), spy, risk,
                       "skipped" if plan["skipped"] else "open", plan["skipped"] or None,
                       json.dumps(figures(facts))))
        opened += 0 if plan["skipped"] else 1
    return opened


def _close(memory: Memory, t: dict, day: date, price: float, why: str, spy: float | None) -> None:
    from . import pricebook  # noqa: PLC0415
    ret = price / t["entry"] - 1
    pricebook.run(memory, "UPDATE sim_trades SET status = 'closed', closed = ?, exit = ?, why = ?, ret = ?, "
                          "spy_ret = ? WHERE id = ?",
                  (day.isoformat(), round(price, 4), why, round(ret, 5),
                   round(spy / t["spy_entry"] - 1, 5) if spy and t.get("spy_entry") else None, t["id"]))


def update(memory: Memory, today: date, data) -> int:
    """Walk each open trade through the days since it opened: stop, target, or the time limit. Returns closes."""
    from . import pricebook  # noqa: PLC0415
    _ready(memory)
    trades = pricebook.run(memory, "SELECT * FROM sim_trades WHERE status = 'open'")
    if not trades:
        return 0
    bars = data.bars("us", sorted({t["symbol"] for t in trades} | {"SPY"}), days=HOLD_DAYS + 30)
    spy = {b["d"]: b["c"] for b in bars.get("SPY") or []}
    closed = 0
    for t in trades:
        opened = str(t["opened"])
        rows = [b for b in bars.get(t["symbol"]) or [] if b["d"] > opened]
        done = None
        for b in rows:
            hit_stop = b["l"] <= t["stop"] if t["side"] == "long" else b["h"] >= t["stop"]
            hit_target = b["h"] >= t["target"] if t["side"] == "long" else b["l"] <= t["target"]
            if hit_stop:  # both in one day: the stop is assumed first (the cautious reading)
                done = (b["d"], t["stop"], "stopped out" if t["side"] == "long" else "it rose 7%")
            elif hit_target:
                done = (b["d"], t["target"], "reached the target" if t["side"] == "long" else "it fell 7%")
            if done:
                break
        if not done and rows and (today - date.fromisoformat(opened)).days >= HOLD_DAYS:
            done = (rows[-1]["d"], rows[-1]["c"], f"{HOLD_DAYS} days were up")
        if done:
            _close(memory, t, date.fromisoformat(done[0]), done[1], done[2], spy.get(done[0]))
            closed += 1
    return closed


def _won(t: dict) -> bool:
    return (t["ret"] > 0) if t["side"] == "long" else (t["ret"] < 0)


def stats(memory: Memory) -> dict:
    """How the practice book has done: overall, per side and category, and per checklist read."""
    from . import pricebook  # noqa: PLC0415
    _ready(memory)
    done = pricebook.run(memory, "SELECT * FROM sim_trades WHERE status = 'closed' ORDER BY closed")
    open_ = pricebook.run(memory, "SELECT opened, symbol, side, entry, stop, target FROM sim_trades WHERE "
                                  "status = 'open' ORDER BY opened")

    def group(rows: list[dict]) -> dict | None:
        if not rows:
            return None
        way = [t["ret"] if t["side"] == "long" else -t["ret"] for t in rows]
        longs = [t for t in rows if t["side"] == "long" and t["spy_ret"] is not None]
        return {"trades": len(rows), "won": sum(_won(t) for t in rows),
                "win_rate": round(sum(_won(t) for t in rows) / len(rows), 2),
                "average_result": round(sum(way) / len(way), 4),
                "average_vs_spy": round(sum(t["ret"] - t["spy_ret"] for t in longs) / len(longs), 4) if longs else None}

    out = {"closed": len(done), "open": [{**o, "opened": str(o["opened"])} for o in open_],
           "overall": group(done),
           "buys": group([t for t in done if t["side"] == "long"]),
           "avoids": group([t for t in done if t["side"] == "avoid"]),
           "by_category": {c: group([t for t in done if t["category"] == c])
                           for c in sorted({t["category"] for t in done})},
           "buys_by_checklist_read": {r: group([t for t in done if t["side"] == "long" and t["read"] == r])
                                      for r in sorted({t["read"] for t in done if t["read"]})},
           "paper_profit": round(sum(t["shares"] * (t["exit"] - t["entry"]) for t in done if t["side"] == "long"), 2),
           "weights": weights(memory)}
    longs = [t for t in done if t["side"] == "long"]
    value, peak, worst = START, START, 0.0
    for t in longs:  # the book's value after each close, and its deepest fall from a peak
        value += t["shares"] * (t["exit"] - t["entry"])
        peak = max(peak, value)
        worst = min(worst, value / peak - 1)
    rs = [t["shares"] * (t["exit"] - t["entry"]) / t["risk"] for t in longs if t.get("risk")]
    skipped = pricebook.run(memory, "SELECT COUNT(*) AS n FROM sim_trades WHERE status = 'skipped'")[0]["n"]
    out["money"] = {**money(memory), "started_with": START, "return_pct": round(value / START - 1, 4),
                    "worst_drawdown_pct": round(worst, 4),
                    "average_result_in_r": round(sum(rs) / len(rs), 2) if rs else None,
                    "skipped_for_risk": int(skipped),
                    "rules": "1% of the book risked per trade; stop at twice the daily range (4% to 12%); target "
                             "twice the stop; no position over 20%; at most 6% at risk in total"}
    out["note"] = ("Buddy's own paper test of its signals, sized by risk, held up to 28 days; no costs. "
                   + (f"Only {len(done)} closed so far: far too few to judge." if len(done) < MIN_TO_TRUST
                      else "Enough trades to start reading, not to rely on."))
    return out


def learn(memory: Memory) -> dict:
    """Re-weigh each reason from the closed trades it pointed the way of. Kept with the counts behind it."""
    from . import pricebook  # noqa: PLC0415
    _ready(memory)
    done = pricebook.run(memory, "SELECT side, ret, parts FROM sim_trades WHERE status = 'closed'")
    out = {}
    for p in PARTS:
        with_it = []
        for t in done:
            sign = (json.loads(t["parts"] or "{}")).get(p) or 0
            if sign and (sign > 0) == (t["side"] == "long"):  # the reason pointed the same way as the trade
                with_it.append(_won(t))
        n = len(with_it)
        if n >= MIN_TO_LEARN:
            rate = sum(with_it) / n
            out[p] = {"weight": round(max(0.5, min(1.5, 1 + (rate - 0.5))), 2), "trades": n, "won": sum(with_it)}
        else:
            out[p] = {"weight": 1.0, "trades": n, "won": sum(with_it), "note": f"needs {MIN_TO_LEARN} trades"}
    memory.set(WEIGHTS_KEY, json.dumps({"on": local_now().date().isoformat(), "parts": out}))
    return out


def weights(memory: Memory) -> dict:
    """{reason: weight} for the signal score (1.0 until learned), with what they rest on."""
    saved = json.loads(memory.get(WEIGHTS_KEY) or "{}")
    parts = saved.get("parts") or {}
    return {"learned_on": saved.get("on"), **{p: (parts.get(p) or {"weight": 1.0, "trades": 0}) for p in PARTS}}


def run(memory: Memory, signals: list[dict], today: date | None = None, data=None) -> dict:
    """The morning's bookkeeping: close what's due, open today's signals, re-learn the weights."""
    from .markets import Data  # noqa: PLC0415
    today = today or local_now().date()
    data = data or Data(memory)
    closed = update(memory, today, data)
    spy = None
    try:
        rows = data.bars("us", ["SPY"], days=10).get("SPY") or []
        spy = rows[-1]["c"] if rows else None
    except Exception:
        log.info("simbook: no SPY price", exc_info=True)
    opened = open_from(memory, signals, today, spy)
    return {"closed": closed, "opened": opened, "weights": learn(memory)}
