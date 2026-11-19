"""Day-trade practice: the plan, the replay, the size, the limits, and practice trades settled from real bars."""

import io
import json
import random
import urllib.error
from datetime import date, datetime, timedelta, timezone

import pytest

from buddy import dayplan as dp
from buddy.markets import Data
from buddy.memory import Memory
from buddy.tools import ToolError

TODAY = date(2026, 11, 19)  # a Thursday


def stamp(day: date, slot: int) -> str:
    at = datetime(day.year, day.month, day.day, 9, 30, tzinfo=dp.NY) + timedelta(minutes=5 * slot)
    return at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def session(day: date, seed: int, drift: float = 0.0, start: float = 100.0) -> list[dict]:
    rng, price, out = random.Random(seed), start, []
    for i in range(dp.SESSION_BARS):
        close = round(price * (1 + drift + rng.uniform(-0.002, 0.002)), 4)
        out.append({"t": stamp(day, i), "o": price, "h": max(price, close) * 1.0005, "l": min(price, close) * 0.9995,
                    "c": close, "v": 1000})
        price = close
    return out


def weekdays(n: int, before: date) -> list[date]:
    out, d = [], before
    while len(out) < n:
        d -= timedelta(days=1)
        if d.weekday() < 5:
            out.append(d)
    return out[::-1]


def breakout_day(day: date) -> list[dict]:
    """The first 15 minutes trade between 100 and 101; the 9:45 bar closes at 101.5; the 9:50 bar reaches 102.6."""
    bars = [{"o": 100.2, "h": 101.0, "l": 100.0, "c": 100.5}, {"o": 100.5, "h": 100.9, "l": 100.2, "c": 100.6},
            {"o": 100.6, "h": 100.8, "l": 100.3, "c": 100.7}, {"o": 100.7, "h": 101.6, "l": 100.6, "c": 101.5},
            {"o": 101.5, "h": 102.6, "l": 101.4, "c": 102.4}]
    bars += [{"o": 102.4, "h": 102.5, "l": 102.3, "c": 102.4} for _ in range(dp.SESSION_BARS - len(bars))]
    return [{"t": stamp(day, i), "v": 1000, **b} for i, b in enumerate(bars)]


def all_bars() -> list[dict]:
    return [b for i, d in enumerate(weekdays(30, TODAY)) for b in session(d, seed=i)] + breakout_day(TODAY)


class FakeData:
    def __init__(self, bars):
        self.bars, self.calls = bars, []

    def intraday(self, symbol, minutes=5, days=95):
        self.calls.append((symbol, minutes, days))
        return self.bars


@pytest.fixture
def memory(tmp_path):
    return Memory(tmp_path / "d.db")


def at(hour: int, minute: int = 0, day: date = TODAY) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=dp.NY)


def test_bars_become_new_york_sessions_and_gaps_are_flat():
    assert dp.slot_of("2026-11-19T14:30:00Z") == ("2026-11-19", 0)          # 9:30 AM in New York, in winter
    assert dp.slot_of("2026-07-01T13:30:00Z") == ("2026-07-01", 0)          # and in summer
    assert dp.slot_of("2026-11-19T13:30:00Z") is None and dp.slot_of("2026-11-19T21:00:00Z") is None
    day = dp.sessions(breakout_day(TODAY))["2026-11-19"]
    assert len(day) == 78 and dp.clock(3) == "9:45 AM" and dp.clock(77) == "3:55 PM"
    day[10] = None
    whole = dp.filled(day)
    assert whole[10] == {"o": 102.4, "h": 102.4, "l": 102.4, "c": 102.4, "v": 0}   # no trade: flat at the last close
    assert dp.filled([None] + day[1:]) is None                                     # no opening bar: not a session


def test_the_plan_waits_for_a_close_beyond_the_opening_range():
    day = dp.sessions(breakout_day(TODAY))["2026-11-19"]
    assert dp.make_plan(day[:2] + [None] * 76)["status"] == "too_early"
    waiting = dp.make_plan(day[:3] + [None] * 75)
    assert waiting["status"] == "waiting" and "close above 101.00" in waiting["message"]
    plan = dp.make_plan(day)
    assert (plan["status"], plan["entry"], plan["stop"], plan["target"]) == ("triggered", 101.5, 100.5, 102.5)
    assert plan["entry_bar"] == 3 and plan["entry_time"] == "9:50 AM" and plan["flat_by"] == "3:55 PM"
    far = dp.make_plan(day, stop_rule="far", reward=2.0)
    assert (far["stop"], far["target"], far["risk_per_share"]) == (100.0, 104.5, 1.5)
    assert dp.make_plan(day, side="short")["status"] == "waiting"                  # it never closed below the range


def test_what_happened_on_a_real_day_counts_a_bar_that_touches_both_as_the_stop():
    day = dp.sessions(breakout_day(TODAY))["2026-11-19"]
    assert dp.walk(day, 3, 101.5, 100.5, 102.5, "buy") == {"outcome": "target", "exit": 102.5, "r": 1.0, "bar": 4}
    assert dp.walk(day, 3, 101.5, 101.45, 102.5, "buy")["outcome"] == "stop"       # the same bar touched both
    assert dp.walk(day, 3, 101.5, 100.5, 110.0, "buy") == {"outcome": "close", "exit": 102.4, "r": 0.9, "bar": 76}
    assert dp.walk(day[:5] + [None] * 73, 4, 102.4, 101.0, 104.0, "buy") == {"outcome": "open"}


def test_the_replay_is_repeatable_adds_up_and_follows_the_stocks_own_bars():
    flat = [session(d, seed=i) for i, d in enumerate(weekdays(30, TODAY))]
    one = dp.replay(flat, 101.5, 100.5, 102.5, 3, replays=2000)
    assert one == dp.replay(flat, 101.5, 100.5, 102.5, 3, replays=2000)            # the same seed, the same answer
    assert abs(one["target_first"] + one["stop_first"] + one["neither_by_the_close"] - 1) < 0.002
    assert one["sessions_used"] == 30 and one["bars_left"] == 73
    rising = [session(d, seed=i, drift=0.0012) for i, d in enumerate(weekdays(30, TODAY))]
    up = dp.replay(rising, 101.5, 100.5, 102.5, 3, replays=2000)
    assert up["target_first"] > 0.8 and up["verdict"] == "The replays lean toward the target."
    assert dp.replay(rising, 101.5, 102.5, 100.5, 3, side="short", replays=2000)["stop_first"] > 0.8
    assert dp.replay(flat, 101.5, 100.5, 102.5, 3, replays=2000, cost=0.1)["average_r"] < one["average_r"]
    with pytest.raises(ToolError, match="at least 20 full past sessions"):
        dp.replay(flat[:5], 101.5, 100.5, 102.5, 3)
    past = dp.history(rising)
    assert past["sessions"] == 30 and past["fired"] > 20 and past["target_first"] > past["stop_first"]


def test_the_size_fits_the_loss_limit_and_the_limits_block_in_plain_words(memory):
    lim = dp.limits(memory)
    assert dp.size(101.5, 100.5, lim) == {"shares": 29, "cost": 2943.5, "loss_at_the_stop": 29.0,
                                          "limited_by": "your limit on one position"}
    assert dp.size(20.0, 19.5, lim)["shares"] == 100 and "loss of one trade" in dp.size(20.0, 19.5, lim)["limited_by"]
    plan = dp.make_plan(dp.sessions(breakout_day(TODAY))["2026-11-19"])
    assert dp.check(plan, dp.size(101.5, 100.5, lim), lim, {"trades": 0, "lost": 0}) == {"verdict": "pass", "reasons": []}
    tight = dp.set_limits(memory, {"max_loss": 0.5, "min_reward_to_risk": 2, "max_trades_per_day": 1, "last_entry": "9:45"})
    blocked = dp.check(plan, dp.size(101.5, 100.5, tight), tight, {"trades": 1, "lost": 150.5})
    said = " ".join(blocked["reasons"])
    assert blocked["verdict"] == "block" and len(blocked["reasons"]) == 5
    assert "One share already risks $1.00, more than your limit of $0.50" in said and "at least 2" in said and "limit is 1" in said
    assert "daily limit of $150" in said and "after your last entry time of 09:45" in said
    with pytest.raises(ToolError):
        dp.set_limits(memory, {"max_loss": 0})
    with pytest.raises(ToolError):
        dp.set_limits(memory, {"last_entry": "25:00"})


def test_a_plan_is_kept_as_a_practice_trade_and_settled_from_the_days_real_bars(memory):
    data = FakeData(all_bars())
    waiting = dp.day_plan(memory, "tsla", data=data, now=at(9, 46), replays=500)
    assert waiting["plan"]["status"] == "waiting" and "replay" not in waiting and waiting["history"]["sessions"] == 30
    got = json.loads(dp.run_tool(memory, {"action": "plan", "symbol": "TSLA"}, data, at(10, 0)))
    assert got["plan"]["entry"] == 101.5 and got["size"]["shares"] == 29 and got["check"]["verdict"] == "pass"
    assert got["replay"]["replays"] == 10000 and got["prices"].startswith("Prices are about 15 minutes behind")
    assert "not a prediction" in got["note"] and "never places an order" in got["note"]
    kept = json.loads(dp.run_tool(memory, {"action": "keep", "symbol": "tsla"}, data, at(10, 5)))
    assert kept["kept"] and kept["shares"] == 29
    with pytest.raises(ToolError, match="already kept"):
        dp.keep(memory, "TSLA", at(10, 6))
    assert dp.today_so_far(memory, "2026-11-19") == {"trades": 1, "lost": 29.0}    # what is at risk counts for the day
    assert dp.settle(memory, data, at(15, 0)) == 0                                # the day is not over
    book = json.loads(dp.run_tool(memory, {"action": "record"}, data, at(16, 30)))
    trade = book["trades"][0]
    assert (trade["ended"], trade["exit"], trade["r"], trade["usd"]) == ("target", 102.5, 1.0, 28.42)   # 29 x (1.00 - 0.02)
    assert book["closed"] == 1 and book["it_happened"] == 1.0 and 0 <= book["replays_said_target_first"] <= 1

    weekend = dp.day_plan(memory, "TSLA", data=data, now=at(11, 0, date(2026, 11, 21)), replays=500)
    assert "last session (2026-11-19), for study" in weekend["market"]
    with pytest.raises(ToolError, match="no plan for TSLA from today"):
        dp.keep(memory, "TSLA", at(11, 5, date(2026, 11, 21)))
    with pytest.raises(ToolError):
        dp.day_plan(memory, "TSLA", side="sideways", data=data)


def test_five_minute_bars_come_from_alpaca_with_the_whole_market_feed_when_the_plan_allows(memory):
    seen = []

    def fetch(url, headers=None, data=None):
        seen.append(url)
        if "feed=sip" in url:
            raise urllib.error.HTTPError(url, 403, "Forbidden", {}, io.BytesIO(b'{"message": "subscription does not permit"}'))
        if "page_token=p2" in url:
            return {"bars": {"TSLA": [{"t": "2026-11-19T14:35:00Z", "o": 2, "h": 3, "l": 1, "c": 2.5, "v": 9}]}}
        return {"bars": {"TSLA": [{"t": "2026-11-19T14:30:00Z", "o": 1, "h": 2, "l": 1, "c": 2}]}, "next_page_token": "p2"}

    data = Data(memory, env={"BUDDY_ALPACA_KEY_ID": "k", "BUDDY_ALPACA_SECRET": "s"}, fetch=fetch)
    bars = data.intraday("tsla")
    assert [b["c"] for b in bars] == [2, 2.5] and bars[0]["v"] == 0
    assert "timeframe=5Min" in seen[0] and "symbols=TSLA" in seen[0] and "feed=sip" in seen[0] and "end=" in seen[0]
    assert "feed=iex" in seen[1] and "end=" not in seen[1] and memory.get("markets:feed") == "iex"
    with pytest.raises(ToolError, match="Alpaca market data key"):
        Data(memory, env={}, fetch=fetch).intraday("TSLA")
