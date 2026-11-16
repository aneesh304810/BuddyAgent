"""Strategy lab: each rule picks by its own conditions, trades are opened and closed by rule, and the record."""

import json
from datetime import date, timedelta

import pytest

from buddy import pricebook, stratlab
from buddy.connectors.hub import ConnectorHub
from buddy.memory import Memory

DAY = date(2026, 11, 16)  # a Monday


@pytest.fixture
def memory(tmp_path):
    m = Memory(tmp_path / "buddy.db")
    ConnectorHub(m)
    yield m
    pricebook.close_all()


def series(closes, volume=1000, last_volume=None):
    start = DAY - timedelta(days=len(closes))
    out = [{"d": (start + timedelta(days=i + 1)).isoformat(), "o": c, "h": c * 1.005, "l": c * 0.995, "c": c, "v": volume}
           for i, c in enumerate(closes)]
    if last_volume:
        out[-1]["v"] = last_volume
    return out


def rising(n=300, start=100.0, step=0.3):
    return [start + step * i for i in range(n)]


class Data:
    def __init__(self, bars):
        self.all = bars

    def bars(self, market, symbols, days=200):
        return {s: self.all[s] for s in symbols if s in self.all}


def test_each_rule_picks_by_its_own_conditions():
    up = stratlab.figures(series(rising(), last_volume=2000))
    assert up["high_20"] and up["high_252"] and up["avg50_above_200"] and up["volume_times"] == 2.0
    dipped = rising()[:-8] + [rising()[-9] * (1 - 0.012 * i) for i in range(1, 9)]  # a long rise, then eight falls
    dip = stratlab.figures(series(dipped))
    assert dip["rsi"] < 30 and dip["above_200"] > 0 and not dip["high_20"]
    flat = stratlab.figures(series([100.0] * 300))
    figs = {"UP": up, "DIP": dip, "FLAT": flat, "XLK": up, "XLE": flat}
    assert [s for s, _ in stratlab.picks("trend", figs)] == ["UP", "XLK"]
    assert [s for s, _ in stratlab.picks("dip", figs)] == ["DIP"]
    assert [s for s, _ in stratlab.picks("breakout", figs)] == ["UP", "XLK"]
    assert [s for s, _ in stratlab.picks("sectors", figs)] == ["XLK"]  # a flat sector is not bought
    assert {s for s, _ in stratlab.picks("momentum", figs)} == {"UP", "XLK", "DIP"}
    assert stratlab.picks("insiders", figs, {"UP": 45.0, "FLAT": 80.0, "DIP": 5.0})[0][0] == "UP"  # FLAT is not above its average
    assert stratlab.figures(series(rising(100))) is None  # under a year of prices: not judged


def test_a_weekday_opens_trades_by_rule_then_closes_them_at_stop_target_or_time(memory):
    bars = {"AAPL": series(rising(), last_volume=2000), "SPY": series([100.0] * 300)}  # only listed symbols are read
    got = stratlab.run(memory, DAY, Data(bars), insiders={})
    assert got["opened"]["trend"] == 1 and got["opened"]["breakout"] == 1 and got["opened"]["dip"] == 0
    assert stratlab.run(memory, DAY, Data(bars), insiders={})["opened"]["trend"] == 0  # one at a time per rule and symbol
    trade = pricebook.run(memory, "SELECT * FROM lab_trades WHERE strategy = 'trend'")[0]
    assert trade["symbol"] == "AAPL" and round(trade["shares"] * trade["entry"]) == 1000 and trade["stop"] < trade["entry"]
    assert "rsi" in json.loads(trade["features"]) and "20-day high" in trade["why"]
    nxt = (DAY + timedelta(days=1)).isoformat()
    bars["AAPL"].append({"d": nxt, "o": trade["entry"], "h": trade["target"] + 1, "l": trade["entry"], "c": trade["target"], "v": 1000})
    bars["SPY"].append({"d": nxt, "o": 130, "h": 131, "l": 129, "c": bars["SPY"][-1]["c"] * 1.01, "v": 1000})
    assert stratlab.settle(memory, DAY + timedelta(days=2), bars) >= 2
    view = stratlab.view(memory)
    trend = next(s for s in view["strategies"] if s["key"] == "trend")
    assert trend["closed"] == 1 and trend["won"] == 1 and trend["says"].startswith("Unproven: 1 of 30")
    assert trend["average_vs_market"] == round(trend["average_result"] - 0.01, 4)
    assert view["closed"][0]["why_closed"] == "reached the target" and len(view["strategies"]) == 6


def test_no_prices_opens_nothing_and_says_so(memory):
    got = stratlab.run(memory, DAY, Data({}), insiders={})
    assert got["closed"] == 0 and got["opened"] == {} and "no daily prices" in got["note"]
    assert stratlab.view(memory)["open"] == []
