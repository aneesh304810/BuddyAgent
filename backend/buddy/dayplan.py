"""Day-trade practice: a plan before the trade, the odds by replay, the size and the limits. Paper only.

  plan     The opening range (the first 15 minutes), an entry when a 5-minute bar closes beyond it, a stop and a
           target fixed beforehand, and out by 3:55 PM New York time whatever happens.
  replay   The rest of the day replayed 10,000 times from the stock's own 5-minute bars at the same time of day
           over its last sessions, and the same rule re-run on each of those real sessions: how often the target
           came first, the stop first, or neither by the close.
  size     How many shares fit the most the owner will lose on one trade.
  check    Pass or block against the owner's limits: loss per trade, loss per day, trades per day, reward to risk,
           time of day and position size.

  earnings The session that reacts to an earnings report is unlike the stock's ordinary days, so a plan on that day
           is blocked (Finnhub's earnings calendar, when its key is set).
  scan     Each morning, the owner's watched stocks are checked for a close beyond the opening range, and the ones
           that pass the limits are sent in one message.

A plan can be kept as a practice trade. It is settled from that day's real bars after the close, so the replay's
odds can be compared with what happened. Buddy never places an order: nothing here can reach a broker.

Bars come from Alpaca's market data through Buddy Connect (markets.Data.intraday): only the ticker symbol is
sent. Everything else is arithmetic on those bars, with the standard library only. Practice trades are kept in the
price book, beside Buddy's other practice trades.

The method follows the "Time Machine" in day-trader-skill (MIT licence, copyright (c) 2026 Jake Schincariol). This
is Buddy's own code. What differs: official bars instead of an unofficial chart address; bars are drawn in short
runs from one past session, so a move's follow-through is kept; a cost per share for the spread; and no broker.
"""

import json
import logging
import math
import random
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from .memory import Memory
from .tools import ToolError

log = logging.getLogger("buddy.dayplan")

NY = ZoneInfo("America/New_York")
BAR, OPEN, SESSION_BARS = 5, 9 * 60 + 30, 78      # five-minute bars from 9:30 AM to 4:00 PM New York time
RANGE_BARS, LAST = 3, 76                          # the opening range is 15 minutes; out when the 3:50 bar closes
MIN_SESSIONS, USE_SESSIONS = 20, 60
LIMITS_KEY = "dayplan:limits"
LIMITS = {"account": 5000.0, "max_loss": 50.0, "max_daily_loss": 150.0, "max_trades_per_day": 3,
          "min_reward_to_risk": 1.0, "max_position_pct": 60.0, "last_entry": "15:00", "cost_per_share": 0.02,
          "block_on_earnings": True, "morning_scan": True}
SWITCHES = ("block_on_earnings", "morning_scan")
SCAN_KEY, SCAN_AT, SCAN_UNTIL, SCAN_MAX, SCAN_REPLAYS = "dayplan:scan:day", 10 * 60 + 20, 12 * 60, 30, 2000
LIQUID = ("SPY", "QQQ", "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA", "AMD", "NFLX", "AVGO", "JPM", "XOM",
          "COST")  # scanned when the owner's own lists are short
NOTE = ("Practice only. A replay is built from the stock's own recent bars: it shows how much the price moves, "
        "not which way, and it is not a prediction. Day trading loses most people money. Buddy never places an order.")


# --- bars into sessions ---------------------------------------------------------------------------------------
def slot_of(t: str) -> tuple[str, int] | None:
    """A bar's start time (UTC) as (New York day, slot 0..77), or None outside the regular session."""
    at = datetime.fromisoformat(str(t).replace("Z", "+00:00")).astimezone(NY)
    minutes = at.hour * 60 + at.minute - OPEN
    if minutes < 0 or minutes >= SESSION_BARS * BAR or minutes % BAR:
        return None
    return at.date().isoformat(), minutes // BAR


def sessions(bars: list[dict]) -> dict[str, list]:
    """{New York day: 78 slots}, oldest day first; a slot with no bar is None."""
    out: dict[str, list] = {}
    for b in bars:
        got = slot_of(b["t"])
        if got:
            out.setdefault(got[0], [None] * SESSION_BARS)[got[1]] = b
    return dict(sorted(out.items()))


def filled(day: list) -> list[dict] | None:
    """A whole session. Five minutes with no trade become a flat bar at the last close; None when the open or more
    than a tenth of the session is missing."""
    if day[0] is None or sum(b is None for b in day) > SESSION_BARS // 10:
        return None
    out, last = [], None
    for b in day:
        if b is None:
            b = {"o": last, "h": last, "l": last, "c": last, "v": 0}
        out.append(b)
        last = b["c"]
    return out


def clock(slot: int) -> str:
    """The New York time a slot starts, as 10:15 AM."""
    minutes = OPEN + slot * BAR
    return f"{(minutes // 60 - 1) % 12 + 1}:{minutes % 60:02d} {'AM' if minutes < 720 else 'PM'}"


# --- the plan -------------------------------------------------------------------------------------------------
def opening_range(day: list) -> tuple[float, float]:
    first = day[:RANGE_BARS]
    return max(b["h"] for b in first), min(b["l"] for b in first)


def trigger(day: list, high: float, low: float, side: str, upto: int = LAST - 1) -> int | None:
    """The first bar after the opening range that closes beyond it: above for a buy, below for a short."""
    for i in range(RANGE_BARS, upto + 1):
        b = day[i]
        if b is None:
            return None  # today's bars end here
        if (b["c"] > high) if side == "buy" else (b["c"] < low):
            return i
    return None


def levels(entry: float, high: float, low: float, side: str, stop_rule: str, reward: float) -> tuple[float, float] | None:
    """(stop, target) for an entry: the stop at the middle of the range or at its far side, the target `reward`
    times that risk away. None when the entry is not beyond the stop."""
    stop = (high + low) / 2 if stop_rule == "middle" else (low if side == "buy" else high)
    risk = entry - stop if side == "buy" else stop - entry
    if risk <= 0:
        return None
    return round(stop, 2), round(entry + reward * risk if side == "buy" else entry - reward * risk, 2)


def make_plan(day: list, side: str = "buy", stop_rule: str = "middle", reward: float = 1.0) -> dict:
    """The plan for a session's bars so far (None after the last closed bar)."""
    seen = [i for i, b in enumerate(day) if b is not None]
    if len(seen) < RANGE_BARS or any(day[i] is None for i in range(RANGE_BARS)):
        return {"status": "too_early", "message": "The opening range is the first 15 minutes after 9:30 AM New York "
                                                  "time. Ask again once they have passed."}
    high, low = opening_range(day)
    out = {"side": side, "range_high": round(high, 2), "range_low": round(low, 2), "bars_so_far": seen[-1] + 1}
    k = trigger(day, high, low, side)
    if k is None:
        level = high if side == "buy" else low
        return {**out, "status": "waiting", "message": f"Not triggered yet. The rule waits for a 5-minute bar to close "
                                                      f"{'above' if side == 'buy' else 'below'} {level:,.2f}."}
    got = levels(day[k]["c"], high, low, side, stop_rule, reward)
    if not got:
        return {**out, "status": "no_plan", "message": "The entry is not beyond the stop, so there is no trade to plan."}
    entry, (stop, target) = round(day[k]["c"], 2), got
    return {**out, "status": "triggered", "entry": entry, "stop": stop, "target": target,
            "risk_per_share": round(abs(entry - stop), 2), "reward_to_risk": reward, "stop_rule": stop_rule,
            "entry_bar": k, "entry_time": clock(k + 1), "bars_since_entry": seen[-1] - k, "flat_by": clock(LAST + 1)}


# --- what happened, and what could happen ---------------------------------------------------------------------
def walk(day: list, k: int, entry: float, stop: float, target: float, side: str) -> dict:
    """After bar k on a real session: the target first, the stop first, or neither by the close. A bar that touches
    both counts as the stop."""
    long, risk = side == "buy", abs(entry - stop)
    for i in range(k + 1, LAST + 1):
        b = day[i]
        if b is None:
            return {"outcome": "open"}
        if (b["l"] <= stop) if long else (b["h"] >= stop):
            return {"outcome": "stop", "exit": stop, "r": -1.0, "bar": i}
        if (b["h"] >= target) if long else (b["l"] <= target):
            return {"outcome": "target", "exit": target, "r": round(abs(target - entry) / risk, 2), "bar": i}
    close = day[LAST]["c"]
    return {"outcome": "close", "exit": close, "r": round(((close - entry) if long else (entry - close)) / risk, 2),
            "bar": LAST}


def history(past: list[list[dict]], side: str = "buy", stop_rule: str = "middle", reward: float = 1.0) -> dict:
    """The same rule on each past session: how often it fired and how those trades ended."""
    ended = []
    for day in past:
        high, low = opening_range(day)
        k = trigger(day, high, low, side)
        got = levels(day[k]["c"], high, low, side, stop_rule, reward) if k is not None else None
        if got:
            ended.append(walk(day, k, day[k]["c"], got[0], got[1], side))
    n = len(ended)
    count = {o: sum(1 for e in ended if e["outcome"] == o) for o in ("target", "stop", "close")}
    return {"sessions": len(past), "fired": n, "target_first": count["target"], "stop_first": count["stop"],
            "neither": count["close"], "average_r": round(sum(e["r"] for e in ended) / n, 2) if n else None}


def replay(past: list[list[dict]], entry: float, stop: float, target: float, k: int, side: str = "buy",
           replays: int = 10000, seed: int = 19, run: int = 3, shift: int = 2, cost: float = 0.0) -> dict:
    """The rest of the day after bar k, `replays` times. Each replay takes runs of `run` bars in a row from one
    past session (at the same time of day, shifted by up to `shift` bars), as moves relative to the bar before.
    A bar that touches both levels counts as the stop. cost: per share, taken off every outcome (the spread)."""
    long, risk = side == "buy", abs(entry - stop)
    pool = [[None] + [(d[i]["h"] / d[i - 1]["c"], d[i]["l"] / d[i - 1]["c"], d[i]["c"] / d[i - 1]["c"])
                      for i in range(1, SESSION_BARS)] for d in past if all(b["c"] > 0 for b in d)]
    if len(pool) < MIN_SESSIONS or risk <= 0:
        raise ToolError(f"A replay needs at least {MIN_SESSIONS} full past sessions of this stock; there are {len(pool)}.")
    rng, r_target, fee = random.Random(seed), abs(target - entry) / risk, cost / risk
    count, total = {"target": 0, "stop": 0, "close": 0}, 0.0
    for _ in range(replays):
        price, outcome, i = entry, "", k + 1
        while i <= LAST and not outcome:
            source, off = pool[rng.randrange(len(pool))], rng.randint(-shift, shift)
            for j in range(i, min(i + run, LAST + 1)):
                high, low, close = source[min(SESSION_BARS - 1, max(1, j + off))]
                if (price * low <= stop) if long else (price * high >= stop):
                    outcome = "stop"
                    break
                if (price * high >= target) if long else (price * low <= target):
                    outcome = "target"
                    break
                price *= close
            i += run
        outcome = outcome or "close"
        count[outcome] += 1
        total += (r_target if outcome == "target" else -1.0 if outcome == "stop"
                  else ((price - entry) if long else (entry - price)) / risk) - fee
    p = {o: round(count[o] / replays, 3) for o in count}
    avg = round(total / replays, 3)
    lean = ("The replays lean toward the target." if avg > 0.1 and p["target"] - p["stop"] > 0.05 else
            "The replays lean toward the stop." if avg < -0.1 or p["stop"] - p["target"] > 0.05 else
            "Close to a coin flip.")
    return {"replays": replays, "sessions_used": len(pool), "bars_left": LAST - k, "target_first": p["target"],
            "stop_first": p["stop"], "neither_by_the_close": p["close"], "average_r": avg, "verdict": lean}


# --- the size and the limits ----------------------------------------------------------------------------------
def limits(memory: Memory) -> dict:
    try:
        kept = json.loads(memory.get(LIMITS_KEY) or "{}")
    except ValueError:
        kept = {}
    return {**LIMITS, **{k: v for k, v in kept.items() if k in LIMITS}}


def set_limits(memory: Memory, changes: dict) -> dict:
    now = limits(memory)
    for key, value in (changes or {}).items():
        if key not in LIMITS or value in (None, ""):
            continue
        if key in SWITCHES:
            now[key] = value if isinstance(value, bool) else str(value).strip().lower() in ("1", "true", "yes", "on")
            continue
        if key == "last_entry":
            hour, _, minute = str(value).partition(":")
            if not (hour.isdigit() and minute.isdigit() and 9 <= int(hour) <= 15 and int(minute) < 60):
                raise ToolError("last_entry is a New York time between 09:45 and 15:55, such as 15:00.")
            now[key] = f"{int(hour):02d}:{int(minute):02d}"
            continue
        number = float(value)
        if number < 0 or (key in ("account", "max_loss", "max_daily_loss", "max_trades_per_day") and number <= 0):
            raise ToolError(f"{key} must be more than zero.")
        now[key] = int(number) if key == "max_trades_per_day" else number
    memory.set(LIMITS_KEY, json.dumps(now))
    return now


def size(entry: float, stop: float, lim: dict) -> dict:
    """Whole shares so that the stop costs no more than the limit for one trade, and the position no more than its
    share of the account."""
    risk = abs(entry - stop)
    by_loss = math.floor(lim["max_loss"] / risk) if risk > 0 else 0
    by_position = math.floor(lim["account"] * lim["max_position_pct"] / 100 / entry) if entry > 0 else 0
    shares = max(0, min(by_loss, by_position))
    return {"shares": shares, "cost": round(shares * entry, 2), "loss_at_the_stop": round(shares * risk, 2),
            "limited_by": "your limit on one position" if by_position < by_loss else "your limit on the loss of one trade"}


def check(plan: dict, sizing: dict, lim: dict, today: dict, earnings: dict | None = None) -> dict:
    """Pass or block, with each reason in plain words. today: {"trades", "lost"} for this day's practice trades."""
    blocks = []
    if earnings and earnings.get("reaction_day") and lim.get("block_on_earnings", True):
        blocks.append(earnings["text"] + " The replay is built from its ordinary days and does not describe today.")
    if sizing["shares"] < 1:
        blocks.append(f"One share already risks ${plan['risk_per_share']:,.2f}, more than your limit of "
                      f"${lim['max_loss']:,.2f} a trade (or than your limit on one position).")
    if plan["reward_to_risk"] < lim["min_reward_to_risk"]:
        blocks.append(f"The target is {plan['reward_to_risk']:g} times the risk; your limit is at least "
                      f"{lim['min_reward_to_risk']:g}.")
    if today["trades"] >= lim["max_trades_per_day"]:
        blocks.append(f"You already have {today['trades']} practice trades today; your limit is {lim['max_trades_per_day']}.")
    if today["lost"] + sizing["loss_at_the_stop"] > lim["max_daily_loss"]:
        blocks.append(f"With today's ${today['lost']:,.0f} already lost, this stop would pass your daily limit of "
                      f"${lim['max_daily_loss']:,.0f}.")
    hour, _, minute = lim["last_entry"].partition(":")
    if OPEN + (plan["entry_bar"] + 1) * BAR > int(hour) * 60 + int(minute):
        blocks.append(f"The entry came at {plan['entry_time']}, after your last entry time of {lim['last_entry']} New York time.")
    return {"verdict": "block" if blocks else "pass", "reasons": blocks}


# --- earnings -------------------------------------------------------------------------------------------------
def earnings_today(memory: Memory, symbol: str, day: str, env=None, fetch=None) -> dict | None:
    """Is `day` the session that reacts to an earnings report? None when Finnhub is not set up or did not answer.
    A report before the open or during the session makes that day the reaction; one after the close makes the next
    session the reaction, and a day trade on the report's own day is out before it."""
    from . import finnhub  # noqa: PLC0415
    if not finnhub.configured(env):
        return None
    try:
        reports = finnhub.earnings_around(memory, symbol, date.fromisoformat(day), env, fetch)
    except Exception as e:
        log.info("earnings for %s could not be read (%s)", symbol, type(e).__name__)
        return None
    before = date.fromisoformat(day) - timedelta(days=1)
    while before.weekday() > 4:
        before -= timedelta(days=1)
    for r in reports:
        if r["date"] == day and r["hour"] != "amc":
            when = " before the open" if r["hour"] == "bmo" else ""
            return {"reaction_day": True, "text": f"{symbol} reports earnings today{when}."}
        if r["date"] == before.isoformat() and r["hour"] == "amc":
            return {"reaction_day": True, "text": f"{symbol} reported earnings after the last close, so today is the "
                                                  "session that reacts to it."}
    if any(r["date"] == day for r in reports):
        return {"reaction_day": False, "text": f"{symbol} reports earnings after today's close. A day trade is out before then."}
    return {"reaction_day": False, "text": ""}


# --- practice trades, kept in the price book ------------------------------------------------------------------
def _ready(memory: Memory) -> None:
    from . import pricebook  # noqa: PLC0415
    pricebook.run(memory, "CREATE SEQUENCE IF NOT EXISTS day_id")
    pricebook.run(memory, "CREATE TABLE IF NOT EXISTS day_trades (id BIGINT PRIMARY KEY DEFAULT nextval('day_id'), "
                          "day DATE, symbol VARCHAR, side VARCHAR, entry DOUBLE, stop DOUBLE, target DOUBLE, "
                          "shares DOUBLE, entry_bar INTEGER, odds VARCHAR, status VARCHAR DEFAULT 'open', "
                          "exit DOUBLE, r DOUBLE, usd DOUBLE, settled DATE)")


def today_so_far(memory: Memory, day: str) -> dict:
    from . import pricebook  # noqa: PLC0415
    _ready(memory)
    rows = pricebook.run(memory, "SELECT status, usd, shares, entry, stop FROM day_trades WHERE day = ?", (day,))
    lost = sum(-(r["usd"] or 0) for r in rows if r["status"] != "open" and (r["usd"] or 0) < 0)
    at_risk = sum(r["shares"] * abs(r["entry"] - r["stop"]) for r in rows if r["status"] == "open")
    return {"trades": len(rows), "lost": round(lost + at_risk, 2)}


def keep(memory: Memory, symbol: str, now: datetime | None = None) -> dict:
    """Keep the plan made for this symbol today as a practice trade."""
    from . import pricebook  # noqa: PLC0415
    now = now or datetime.now(NY)
    try:
        last = json.loads(memory.get(f"dayplan:last:{symbol.upper()}") or "null")
    except ValueError:
        last = None
    if not last or last.get("day") != now.date().isoformat() or last["plan"].get("status") != "triggered":
        raise ToolError(f"There is no plan for {symbol.upper()} from today to keep. Make one first (action plan).")
    if last["check"]["verdict"] != "pass":
        raise ToolError("That plan was blocked by your limits: " + " ".join(last["check"]["reasons"]))
    _ready(memory)
    if pricebook.run(memory, "SELECT 1 FROM day_trades WHERE day = ? AND symbol = ?", (last["day"], last["symbol"])):
        raise ToolError(f"A practice trade in {last['symbol']} is already kept for today.")
    plan = last["plan"]
    got = pricebook.run(memory, "INSERT INTO day_trades (day, symbol, side, entry, stop, target, shares, entry_bar, odds) "
                                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) RETURNING id",
                        (last["day"], last["symbol"], plan["side"], plan["entry"], plan["stop"], plan["target"],
                         last["size"]["shares"], plan["entry_bar"], json.dumps(last.get("replay") or {})))
    return {"kept": True, "id": got[0]["id"], "symbol": last["symbol"], "shares": last["size"]["shares"],
            "entry": plan["entry"], "stop": plan["stop"], "target": plan["target"],
            "note": "A practice trade only. It is settled from the day's real bars after the close."}


def settle(memory: Memory, data, now: datetime | None = None) -> int:
    """Close every practice trade whose day is over, from that day's real bars. Returns how many were settled."""
    from . import pricebook  # noqa: PLC0415
    now = now or datetime.now(NY)
    _ready(memory)
    done = 0
    over = now.date().isoformat() if now.hour * 60 + now.minute >= OPEN + SESSION_BARS * BAR + 20 else ""
    cost = limits(memory)["cost_per_share"]
    for t in pricebook.run(memory, "SELECT * FROM day_trades WHERE status = 'open' ORDER BY day"):
        day = str(t["day"])
        if not (day < now.date().isoformat() or day == over):
            continue
        try:
            whole = filled(sessions(data.intraday(t["symbol"], BAR, (now.date() - date.fromisoformat(day)).days + 3)).get(day)
                           or [None] * SESSION_BARS)
        except Exception as e:
            log.info("day trade %s: its bars could not be read (%s)", t["id"], type(e).__name__)
            continue
        if not whole:
            continue
        end = walk(whole, t["entry_bar"], t["entry"], t["stop"], t["target"], t["side"])
        per_share = (end["exit"] - t["entry"]) if t["side"] == "buy" else (t["entry"] - end["exit"])
        pricebook.run(memory, "UPDATE day_trades SET status = ?, exit = ?, r = ?, usd = ?, settled = ? WHERE id = ?",
                      (end["outcome"], end["exit"], end["r"], round(t["shares"] * (per_share - cost), 2),
                       now.date().isoformat(), t["id"]))
        done += 1
    return done


def record(memory: Memory) -> dict:
    """Every practice day trade, newest first, and whether the replays' odds matched what happened."""
    from . import pricebook  # noqa: PLC0415
    _ready(memory)
    rows = pricebook.run(memory, "SELECT * FROM day_trades ORDER BY day DESC, id DESC LIMIT 60")
    closed = [r for r in rows if r["status"] != "open"]
    said = [json.loads(r["odds"] or "{}").get("target_first") for r in closed]
    said = [s for s in said if isinstance(s, (int, float))]
    return {"trades": [{"day": str(r["day"]), "symbol": r["symbol"], "side": r["side"], "shares": r["shares"],
                        "entry": r["entry"], "stop": r["stop"], "target": r["target"], "ended": r["status"],
                        "exit": r["exit"], "r": r["r"], "usd": r["usd"]} for r in rows],
            "closed": len(closed), "target_first": sum(1 for r in closed if r["status"] == "target"),
            "stop_first": sum(1 for r in closed if r["status"] == "stop"),
            "total_usd": round(sum(r["usd"] or 0 for r in closed), 2),
            "replays_said_target_first": round(sum(said) / len(said), 2) if said else None,
            "it_happened": round(sum(1 for r in closed if r["status"] == "target") / len(closed), 2) if closed else None,
            "note": NOTE}


# --- the whole thing for one stock ----------------------------------------------------------------------------
def _closed(data, sym: str, now: datetime) -> dict[str, list]:
    """The stock's sessions, from bars that have closed by `now`."""
    return sessions([b for b in data.intraday(sym, BAR, 95)
                     if datetime.fromisoformat(str(b["t"]).replace("Z", "+00:00")) + timedelta(minutes=BAR) <= now])


def _assess(memory: Memory, sym: str, by_day: dict, day: str, side: str, stop_rule: str, reward: float, lim: dict,
            replays: int, earnings) -> dict:
    """The plan for one stock and side on `day`; with its replay, size and check when the rule has triggered.
    earnings: a function giving that day's earnings note, asked only when there is a plan to check."""
    past = [f for d, s in by_day.items() if d < day for f in [filled(s)] if f][-USE_SESSIONS:]
    out = {"plan": make_plan(by_day[day], side, stop_rule, reward), "past": past}
    plan = out["plan"]
    if plan["status"] == "triggered":
        out["earnings"] = earnings()
        out["replay"] = replay(past, plan["entry"], plan["stop"], plan["target"], plan["entry_bar"], side,
                               replays=replays, cost=lim["cost_per_share"])
        out["size"] = size(plan["entry"], plan["stop"], lim)
        out["check"] = check(plan, out["size"], lim, today_so_far(memory, day), out["earnings"])
    return out


def day_plan(memory: Memory, symbol: str, side: str = "buy", stop_rule: str = "middle", reward: float = 1.0,
             data=None, now: datetime | None = None, replays: int = 10000, env=None, fetch=None) -> dict:
    from .markets import Data  # noqa: PLC0415
    sym = "".join(c for c in str(symbol).upper() if c.isalnum() or c in ".-")[:10]
    if not sym:
        raise ToolError("Which stock? Give its ticker symbol.")
    if side not in ("buy", "short") or stop_rule not in ("middle", "far") or not 0.5 <= float(reward) <= 5:
        raise ToolError("side is buy or short; stop is middle or far; reward is between 0.5 and 5 times the risk.")
    data, now = data or Data(memory), now or datetime.now(NY)
    by_day = _closed(data, sym, now)
    if not by_day:
        raise ToolError(f"No 5-minute bars came back for {sym}. Check the symbol.")
    today = now.date().isoformat()
    day = today if today in by_day else max(by_day)
    lim = limits(memory)
    got = _assess(memory, sym, by_day, day, side, stop_rule, float(reward), lim, replays,
                  lambda: earnings_today(memory, sym, day, env, fetch))
    out = {"symbol": sym, "day": day, "plan": got["plan"],
           "history": history(got["past"], side, stop_rule, float(reward)), "limits": lim, "note": NOTE}
    if day != today:
        out["market"] = f"The market has no bars for today yet, so this is the last session ({day}), for study."
    if (memory.get("markets:feed") or "sip") == "sip":
        out["prices"] = "Prices are about 15 minutes behind on this data plan."
    if got["plan"]["status"] == "triggered":
        out.update({k: got[k] for k in ("replay", "size", "check")})
        if got["earnings"] and got["earnings"]["text"]:
            out["earnings"] = got["earnings"]["text"]
        if day == today:
            memory.set(f"dayplan:last:{sym}", json.dumps({k: out[k] for k in ("symbol", "day", "plan", "replay", "size", "check")}))
    return out


# --- the morning scan -----------------------------------------------------------------------------------------
def universe(memory: Memory) -> list[str]:
    """The stocks to scan: the ones in Buddy's open practice trades and on the watchlists first, then a few of the
    most traded, up to SCAN_MAX."""
    mine = []
    try:
        from . import newsdesk  # noqa: PLC0415
        mine = list(newsdesk.acting_on(memory))
    except Exception:
        log.info("day-trade scan: the watchlists could not be read", exc_info=True)
    seen = []
    for s in [*mine, *LIQUID]:
        s = str(s).upper().strip()
        if s not in seen and 0 < len(s) <= 6 and all(c.isalpha() or c == "." for c in s):
            seen.append(s)
    return seen[:SCAN_MAX]


def scan(memory: Memory, symbols: list[str] | None = None, data=None, now: datetime | None = None,
         replays: int = SCAN_REPLAYS, env=None, fetch=None) -> dict:
    """Which of these stocks has closed beyond its opening range today, each with its plan, replay, size and check.
    Those that pass the limits come first, best replay first."""
    from .markets import Data  # noqa: PLC0415
    data, now, lim = data or Data(memory), now or datetime.now(NY), limits(memory)
    today, found, errors, open_today = now.date().isoformat(), [], [], 0
    symbols = [str(s).upper() for s in (symbols or universe(memory))][:SCAN_MAX]
    for sym in symbols:
        try:
            by_day = _closed(data, sym, now)
        except Exception as e:
            errors.append(f"{sym}: {' '.join(str(e).split())[:80] or type(e).__name__}")
            continue
        if today not in by_day:
            continue
        open_today += 1
        for side in ("buy", "short"):
            try:
                got = _assess(memory, sym, by_day, today, side, "middle", 1.0, lim, replays,
                              lambda sym=sym: earnings_today(memory, sym, today, env, fetch))
            except ToolError:
                continue  # too little history for a replay
            if got["plan"]["status"] != "triggered":
                continue
            plan, odds = got["plan"], got["replay"]
            memory.set(f"dayplan:last:{sym}", json.dumps({"symbol": sym, "day": today, "plan": plan, "replay": odds,
                                                          "size": got["size"], "check": got["check"]}))
            found.append({"symbol": sym, "side": side, "entry": plan["entry"], "stop": plan["stop"],
                          "target": plan["target"], "entry_time": plan["entry_time"],
                          "target_first": odds["target_first"], "stop_first": odds["stop_first"],
                          "average_r": odds["average_r"], "verdict": odds["verdict"], "shares": got["size"]["shares"],
                          "check": got["check"]["verdict"], "reasons": got["check"]["reasons"],
                          "earnings": (got["earnings"] or {}).get("text", "")})
    found.sort(key=lambda r: (r["check"] != "pass", -r["average_r"]))
    out = {"day": today, "scanned": len(symbols), "trading_today": open_today, "triggered": found[:12],
           "errors": errors[:5], "note": NOTE}
    if not open_today:
        out["market"] = "None of these stocks has bars for today yet: the market may be closed, or it is too early."
    return out


def scan_due(memory: Memory, now: datetime | None = None) -> bool:
    """Once each weekday morning, 50 minutes after the open, so the opening range and a few bars after it are in."""
    now = now or datetime.now(NY)
    minutes = now.hour * 60 + now.minute
    return (limits(memory)["morning_scan"] and now.weekday() < 5 and SCAN_AT <= minutes < SCAN_UNTIL
            and memory.get(SCAN_KEY) != now.date().isoformat())


def scan_message(result: dict) -> str:
    """The morning message, as "spoken || shown"; empty when nothing passed the limits (then nothing is sent)."""
    good = [r for r in result["triggered"] if r["check"] == "pass"]
    if not good:
        return ""
    lines = [f"- **{r['symbol']}** {r['side']}: entry {r['entry']:,.2f} at {r['entry_time']}, stop {r['stop']:,.2f}, "
             f"target {r['target']:,.2f}. Replays: target first {r['target_first']:.0%}, stop first {r['stop_first']:.0%}. "
             f"{r['shares']} shares." + (f" {r['earnings']}" if r["earnings"] else "") for r in good[:3]]
    more = len(result["triggered"]) - len(good)
    spoken = (f"Day-trade practice scan: {len(good)} of {result['trading_today']} stocks closed beyond their opening "
              f"range and passed your limits. The best by replay is {good[0]['symbol']}.")
    shown = ("**Day-trade practice scan**\n" + "\n".join(lines)
             + (f"\n{more} more triggered and were blocked by your limits." if more else "")
             + "\nPractice only: a replay is not a prediction. Say \"keep\" with a symbol to follow one on paper.")
    return f"{spoken} || {shown}"


TOOL = {
    "name": "day_trade_practice",
    "description": (
        "Practice day trading on paper, one US stock at a time; Buddy never places an order. plan: for a symbol, the "
        "opening-range plan (entry when a 5-minute bar closes beyond the first 15 minutes' range, a stop and a target "
        "fixed beforehand, out by 3:55 PM New York time), the odds from 10,000 replays of the rest of the day built "
        "from the stock's own recent bars, how the same rule ended on each past session, how many shares fit the "
        "owner's loss limit, and pass or block against their limits. keep: keep today's plan for a symbol as a "
        "practice trade (ask first); it is settled from real bars after the close. record: the practice trades and "
        "whether the replays' odds matched what happened. scan: which watched stocks have closed beyond their opening "
        "range today (or the symbols given), best replay first; Buddy also runs it each weekday morning. limits: see "
        "or change the limits (account, max_loss, max_daily_loss, max_trades_per_day, min_reward_to_risk, "
        "max_position_pct, last_entry, cost_per_share, block_on_earnings, morning_scan). "
        "Always say it is practice, that a replay is not a prediction, and that day trading loses most people money."),
    "input_schema": {"type": "object", "properties": {
        "action": {"type": "string", "enum": ["plan", "keep", "record", "scan", "limits"]},
        "symbol": {"type": "string"},
        "symbols": {"type": "array", "items": {"type": "string"}, "description": "scan: these instead of the watchlists"},
        "side": {"type": "string", "enum": ["buy", "short"]},
        "stop": {"type": "string", "enum": ["middle", "far"], "description": "the middle of the opening range, or its far side"},
        "reward": {"type": "number", "description": "the target as a multiple of the risk (default 1)"},
        "changes": {"type": "object", "description": "limits: the limits to change"}},
        "required": ["action"], "additionalProperties": False},
}


def run_tool(memory: Memory, args: dict, data=None, now: datetime | None = None) -> str:
    from .markets import Data  # noqa: PLC0415
    action = args.get("action")
    if action == "plan":
        return json.dumps(day_plan(memory, args.get("symbol", ""), args.get("side") or "buy", args.get("stop") or "middle",
                                   float(args.get("reward") or 1.0), data, now), default=str)
    if action == "keep":
        return json.dumps(keep(memory, args.get("symbol", ""), now), default=str)
    if action == "record":
        settle(memory, data or Data(memory), now)
        return json.dumps(record(memory), default=str)
    if action == "scan":
        return json.dumps(scan(memory, args.get("symbols") or None, data, now), default=str)
    if action == "limits":
        return json.dumps({"limits": set_limits(memory, args.get("changes") or {}), "note": NOTE})
    raise ToolError("day_trade_practice: plan, keep, record, scan or limits.")
