"""Buddy's practice book: signals paper-traded by rule, closed by stop, target, time or a sell, and learned from."""

import json
from datetime import date, timedelta

import pytest

from buddy import pricebook, recommend, simbook
from buddy.connectors.hub import ConnectorHub
from buddy.memory import Memory

DAY = date(2026, 11, 16)


@pytest.fixture
def memory(tmp_path):
    m = Memory(tmp_path / "buddy.db")
    ConnectorHub(m)
    yield m
    pricebook.close_all()


def sig(key, action="buy", price=100.0, parts=None, read="fair", category="stocks"):
    return {"key": key, "category": category, "action": action, "price": price,
            "facts": {"parts": parts or {"tone": 1, "trend": 1}, "lenses": {"price": read}}}


class Days:
    """Daily bars after the open date: {symbol: [(high, low, close), ...]}, one a day."""

    def __init__(self, moves, start=DAY):
        self.moves, self.start = moves, start

    def bars(self, market, symbols, days=60):
        return {s: [{"d": (self.start + timedelta(days=i + 1)).isoformat(), "o": c, "h": h, "l": lo, "c": c, "v": 1}
                    for i, (h, lo, c) in enumerate(self.moves.get(s, []))] for s in symbols}


def test_a_buy_opens_a_position_and_the_target_or_stop_closes_it(memory):
    assert simbook.open_from(memory, [sig("WIN"), sig("LOSE"), sig("WAIT"), sig("X", "watch")], DAY, 500.0) == 3
    assert simbook.open_from(memory, [sig("WIN")], DAY, 500.0) == 0          # already held: not doubled
    row = pricebook.run(memory, "SELECT shares, stop, target FROM sim_trades WHERE symbol = 'WIN'")[0]
    assert row == {"shares": 50.0, "stop": 93.0, "target": 114.0}
    data = Days({"WIN": [(105, 99, 104), (115, 103, 113)], "LOSE": [(101, 92, 94)], "WAIT": [(103, 98, 101)],
                 "SPY": [(0, 0, 505), (0, 0, 510)]})
    assert simbook.update(memory, DAY + timedelta(days=2), data) == 2
    done = {t["symbol"]: t for t in pricebook.run(memory, "SELECT * FROM sim_trades WHERE status = 'closed'")}
    assert done["WIN"]["why"] == "reached the target" and done["WIN"]["ret"] == 0.14
    assert done["WIN"]["spy_ret"] == 0.02 and done["LOSE"]["why"] == "stopped out" and done["LOSE"]["ret"] == -0.07
    s = simbook.stats(memory)
    assert s["buys"]["trades"] == 2 and s["buys"]["win_rate"] == 0.5 and s["paper_profit"] == 350.0
    assert s["buys"]["average_vs_spy"] == round(((0.14 - 0.02) + (-0.07 - 0.01)) / 2, 4)
    assert [o["symbol"] for o in s["open"]] == ["WAIT"] and "far too few to judge" in s["note"]


def test_time_runs_out_a_sell_signal_closes_and_avoids_are_scored(memory):
    simbook.open_from(memory, [sig("SLOW"), sig("TURN"), sig("BAD", "sell"), sig("HYPE", "caution")], DAY, 500.0)
    flat = [(102, 98, 100 + i * 0.1) for i in range(30)]
    data = Days({"SLOW": flat, "TURN": flat, "BAD": [(101, 92, 93)], "HYPE": [(108, 100, 107)]})
    simbook.open_from(memory, [sig("TURN", "sell", 96.0)], DAY + timedelta(days=3), 490.0)   # Buddy changed its mind
    turn = pricebook.run(memory, "SELECT why, ret, status FROM sim_trades WHERE symbol = 'TURN' AND side = 'long'")[0]
    assert turn == {"why": "Buddy's sell signal", "ret": -0.04, "status": "closed"}
    assert simbook.update(memory, DAY + timedelta(days=5), data) == 2         # the two avoids resolve
    by = {t["symbol"]: t for t in pricebook.run(memory, "SELECT * FROM sim_trades WHERE side = 'avoid' AND "
                                                        "status = 'closed'")}
    assert by["BAD"]["why"] == "it fell 7%" and by["HYPE"]["why"] == "it rose 7%"
    s = simbook.stats(memory)
    assert s["avoids"]["won"] == 1 and s["avoids"]["trades"] == 2             # BAD right, HYPE wrong
    assert simbook.update(memory, DAY + timedelta(days=20), data) == 0        # SLOW: still inside 28 days
    assert simbook.update(memory, DAY + timedelta(days=29), data) == 1        # SLOW: time's up (TURN's avoid is newer)
    slow = pricebook.run(memory, "SELECT why FROM sim_trades WHERE symbol = 'SLOW'")[0]
    assert slow["why"] == "28 days were up"


def test_weights_move_only_with_enough_trades_and_stay_bounded(memory):
    simbook._ready(memory)

    def closed(n, won, parts, side="long"):
        for i in range(n):
            ret = (0.1 if i < won else -0.07) * (1 if side == "long" else -1)
            pricebook.run(memory, "INSERT INTO sim_trades (opened, symbol, category, side, entry, shares, stop, "
                                  "target, parts, read, status, closed, exit, why, ret) VALUES (?, 'S', 'stocks', ?, "
                                  "100, 50, 93, 114, ?, 'fair', 'closed', ?, 100, 'x', ?)",
                          (DAY.isoformat(), side, json.dumps(parts), DAY.isoformat(), ret))

    closed(10, 9, {"tone": 1})
    assert simbook.learn(memory)["tone"] == {"weight": 1.0, "trades": 10, "won": 9, "note": "needs 15 trades"}
    closed(10, 9, {"tone": 1})                                   # 18 of 20 won with tone pointing the way
    closed(20, 4, {"insiders": 1, "tone": 0})                    # insider buying kept being wrong
    closed(16, 16, {"trend": -1}, side="avoid")                  # a falling trend kept making avoids right
    got = simbook.learn(memory)
    assert got["tone"]["weight"] == 1.4 and got["insiders"]["weight"] == 0.7
    assert got["trend"] == {"weight": 1.5, "trades": 16, "won": 16}             # capped at 1.5
    assert got["analysts"]["weight"] == 1.0 and simbook.weights(memory)["tone"]["weight"] == 1.4


def test_learned_weights_change_what_becomes_a_signal(memory):
    def wavy(n=120):
        out, p = [], 100.0
        for i in range(n):
            p += 0.9 if i % 3 else -1.2
            out.append({"d": f"d{i}", "o": p, "h": p + 1, "l": p - 1, "c": p, "v": 1})
        return out
    hot = {"key": "NVDA", "category": "stocks", "mentions": 8, "times_usual": 8.0, "tone": 0.6}
    j = recommend.judge(hot, wavy(), {})
    assert j["action"] == "buy" and j["facts"]["parts"] == {"tone": 1, "trend": 1} and j["facts"]["score"] == 2.0
    weak = {"tone": {"weight": 0.5}, "trend": {"weight": 1.0}}   # the paper trades showed tone hasn't been paying
    j = recommend.judge(hot, wavy(), {}, weak)
    assert j["action"] == "watch" and j["facts"]["score"] == 1.5


def test_a_morning_paper_trades_its_signals_and_the_scorecard_shows_it(memory):
    from tests.test_recommend import NO_LENS, FakeData, attention, wavy
    attention(memory, "NVDA", 8, 0.6)
    data = FakeData({"NVDA": wavy(True, 260), "SPY": wavy(True, 260)})
    assert len(recommend.run(memory, DAY, data, lambda s: {"insiders": 30.0}, NO_LENS)) == 1
    open_ = simbook.stats(memory)["open"]
    assert [o["symbol"] for o in open_] == ["NVDA"] and open_[0]["side"] == "long"
    card = recommend.scorecard(memory)
    assert card["investing_paper_trades"]["closed"] == 0 and "shopping_and_travel_calls" in card
    assert recommend.page(memory)["paper"]["weights"]["tone"]["weight"] == 1.0
