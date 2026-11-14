"""Buddy's practice book: every signal Buddy makes is paper-traded automatically, by fixed rules, so there is an
honest record of what following the signals would have done, and the signal engine can learn from it.

This is Buddy's own test of its own signals, separate from the owner's paper portfolio (paper.py). No real
money, no real orders.

  A buy signal        opens a paper position of $5,000 at that day's close: stop 7% below, target 14% above,
                      closed after 28 days if neither is reached, or when Buddy signals sell on it
  A sell or caution   is scored as an "avoid": right if the price then falls 7%, wrong if it rises 7%, else
                      judged by where it stands after 28 days
  Every close         keeps the result, the S&P 500 fund's move over the same days (the honest comparison), and
                      the reasons the signal was made on (tone, trend, insiders, analysts, the checklists' read)

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

SIZE, STOP, TARGET, HOLD_DAYS = 5_000.0, 0.07, 0.14, 28
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
        stop, target = (price * (1 - STOP), price * (1 + TARGET)) if side == "long" \
            else (price * (1 + STOP), price * (1 - STOP))  # an avoid is wrong 7% up, right 7% down
        facts = j.get("facts") or {}
        pricebook.run(memory, "INSERT INTO sim_trades (opened, symbol, category, side, entry, shares, stop, target, "
                              "parts, read, spy_entry) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                      (today.isoformat(), j["key"], j["category"], side, price, round(SIZE / price, 4), round(stop, 4),
                       round(target, 4), json.dumps(facts.get("parts") or {}),
                       (facts.get("lenses") or {}).get("price", ""), spy))
        opened += 1
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
    out["note"] = ("Buddy's own paper test of its signals: $5,000 a trade, 7% stop, 14% target, 28 days; no costs. "
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
