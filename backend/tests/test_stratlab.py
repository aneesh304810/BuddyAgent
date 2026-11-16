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


# --- history replay ---------------------------------------------------------------------------------------------
def test_history_replays_each_rule_day_by_day_and_is_kept_apart_from_live_trades(memory):
    closes = rising(700, start=50.0, step=0.2)
    bars = {"AAPL": series(closes, last_volume=None), "SPY": series([100.0 + 0.01 * i for i in range(700)])}
    got = stratlab.replay(memory, Data(bars), months=12)
    assert got["trades"]["trend"] > 3 and got["trades"]["dip"] == 0 and "insiders" not in got["trades"]
    rows = pricebook.run(memory, "SELECT * FROM lab_trades")
    assert rows and all(r["backfilled"] and r["status"] == "closed" for r in rows)
    first = min(rows, key=lambda r: str(r["opened"]))
    assert str(first["opened"]) >= bars["SPY"][stratlab.WINDOW]["d"]  # no trade before a rule could see a year of prices
    assert all(str(r["closed"]) > str(r["opened"]) for r in rows)
    again = stratlab.replay(memory, Data(bars), months=12)
    assert again["trades"] == got["trades"] and len(pricebook.run(memory, "SELECT 1 FROM lab_trades")) == len(rows)  # replaced
    view = stratlab.view(memory)
    trend = next(s for s in view["strategies"] if s["key"] == "trend")
    assert trend["closed"] == 0 and trend["history"]["closed"] == got["trades"]["trend"]  # live and history are separate
    assert view["closed"] == [] and view["replay"]["state"] == "done" and "today's list" in view["history_note"]
    assert stratlab.replay(memory, Data({"SPY": series(rising(50))}))["note"].startswith("there is not enough")


# --- strategies from a document ---------------------------------------------------------------------------------
GOOD = {"name": "Oversold bounce", "summary": "Buys strong stocks after a sharp fall.", "universe": "stocks",
        "buy": [{"field": "rsi", "op": "<", "value": 35}, {"field": "above_200", "op": ">", "value": 0},
                {"field": "made_up", "op": ">", "value": 1}, {"field": "rsi", "op": "exec", "value": 1}],
        "rank": {"field": "rsi", "order": "low", "top": 2}, "sell": [{"field": "rsi", "op": ">", "value": 60}],
        "hold_days": 500, "needs": ["earnings estimates"], "code": "import os"}


def test_a_document_becomes_a_rule_only_in_the_fixed_form():
    from buddy import stratrules
    rule = stratrules.validate(GOOD)
    assert [c["field"] for c in rule["buy"]] == ["rsi", "above_200"]  # unknown fields and operators are dropped
    assert rule["hold_days"] == 120 and "code" not in rule and rule["needs"] == ["earnings estimates"]
    assert "Buy when rsi < 35 and above_200 > 0." in stratrules.words(rule) and "Sell when rsi > 60" in stratrules.words(rule)
    with pytest.raises(stratrules.ToolError):
        stratrules.validate({"name": "", "needs": ["it is an opinion piece"]})
    with pytest.raises(stratrules.ToolError):
        stratrules.validate({"name": "Options flow", "buy": [{"field": "options_volume", "op": ">", "value": 2}]})
    figs = {"AAA": {"rsi": 20.0, "above_200": 0.1}, "BBB": {"rsi": 30.0, "above_200": 0.2},
            "CCC": {"rsi": 33.0, "above_200": 0.2}, "DDD": {"rsi": 25.0, "above_200": -0.1}, "XLK": {"rsi": 10.0, "above_200": 0.3}}
    assert [s for s, _ in stratrules.picks(rule, figs)] == ["AAA", "BBB"]  # stocks only, the two lowest RSI that pass
    assert stratrules.text_of("notes.txt", b"plain text") == "plain text"
    with pytest.raises(stratrules.ToolError):
        stratrules.text_of("virus.exe", b"MZ")


def test_an_uploaded_strategy_waits_for_approval_then_trades_in_the_lab(memory):
    import asyncio
    from buddy import stratrules
    seen = []

    async def ask(m, prompt):
        seen.append(prompt)
        return {"name": "Year high", "summary": "Buys at a one-year high.", "universe": "all",
                "buy": [{"field": "high_252", "op": ">=", "value": 1}], "hold_days": 20, "needs": []}

    text = "Buy any stock making a new 52-week high and hold it for twenty trading days. " * 3
    rule = asyncio.run(stratrules.propose(memory, "paper.pdf", text, ask))
    assert rule["status"] == "proposed" and rule["key"] == "u_year_high" and "high_252 >= 1" in rule["in_words"]
    assert "not instructions to you" in seen[0] and "paper.pdf" in seen[0]
    bars = {"AAPL": series(rising(), last_volume=2000), "SPY": series([100.0] * 300)}
    assert "u_year_high" not in stratlab.run(memory, DAY, Data(bars), insiders={})["opened"]  # proposed: it does not trade
    assert stratrules.decide(memory, "u_year_high", "approve")["status"] == "active"
    assert stratlab.run(memory, DAY, Data(bars), insiders={})["opened"]["u_year_high"] == 2  # AAPL, and SPY (flat: also at its high)
    mine = next(s for s in stratlab.view(memory)["strategies"] if s["key"] == "u_year_high")
    assert mine["yours"] and mine["open"] == 2 and "paper.pdf" in mine["reads"] and mine["hold_days"] == 20
    assert stratrules.decide(memory, "u_year_high", "remove")["status"] == "removed"
    assert pricebook.run(memory, "SELECT 1 FROM lab_trades WHERE strategy = 'u_year_high'") == []
    with pytest.raises(stratrules.ToolError):
        asyncio.run(stratrules.propose(memory, "x.txt", "too short", ask))


def test_results_are_after_costs_and_history_is_cut_by_market_period_and_year(memory):
    from buddy import modeltrain
    stratlab._ready(memory)
    def trade(day, ret, spy, regime, strategy="trend"):
        pricebook.run(memory, "INSERT INTO lab_trades (strategy, symbol, opened, entry, stop, target, shares, hold_days, why, "
                              "features, spy_entry, closed, exit, ret, spy_ret, why_closed, regime, status, backfilled) VALUES "
                              "(?, 'AAPL', ?, 100, 95, 110, 10, 28, 'x', ?, 400, ?, 101, ?, ?, 'x', ?, 'closed', TRUE)",
                      (strategy, day, json.dumps({"rsi": 55.0, "above_50": 0.03, "high_20": True}), day, ret, spy, regime))
    for i in range(28):
        trade(f"2023-{1 + i % 12:02d}-15", 0.03, 0.01, "up")       # the earlier years: beats the market
    for i in range(12):
        trade(f"2025-{1 + i:02d}-15", 0.001, 0.02, "down")         # the latest stretch: does not
    h = next(s for s in stratlab.view(memory)["strategies"] if s["key"] == "trend")["history"]
    assert h["closed"] == 40 and h["won"] == 28                      # +0.1% is a loss once 0.2% of costs come off
    assert h["rising_market"]["average_vs_market"] == round(0.03 - 0.002 - 0.01, 4) and h["falling_market"]["closed"] == 12
    assert h["verdict"] == "beat the market earlier but not in the latest stretch"
    assert [y["year"] for y in h["by_year"]] == ["2023", "2025"] and "0.2%" in stratlab.view(memory)["note"]
    rows = modeltrain.rows(memory, "lab")
    assert len(rows) == 40 and rows[0]["when"] < rows[-1]["when"] and rows[-1]["y"] == 0 and rows[0]["y"] == 1
    assert rows[0]["x"] == {"rsi": 55.0, "above_50": 0.03, "high_20": 1.0, "rule_trend": 1.0, "market_rising": 1.0}
    assert rows[-1]["regime"] == "down" and "lab" in modeltrain.DOMAINS and modeltrain.readiness(memory, "lab")["rows"] == 40


def test_ideas_rank_picks_by_whether_the_rule_has_a_record_behind_it(memory):
    stratlab._ready(memory)
    def open_(strategy, symbol, day, odds=None):
        pricebook.run(memory, "INSERT INTO lab_trades (strategy, symbol, opened, entry, stop, target, shares, hold_days, why, "
                              "features, spy_entry, regime) VALUES (?, ?, ?, 100, 93, 114, 10, 28, 'met its rule', ?, 400, 'up')",
                      (strategy, symbol, day, json.dumps({"rsi": 55.0, **({"model_p": odds} if odds else {})})))
    proven = {"closed": 60, "average_vs_market": 0.012}
    table = [{"key": "trend", "name": "Trend following", "history": proven},
             {"key": "breakout", "name": "Breakout", "history": proven},
             {"key": "dip", "name": "Buy the dip", "history": {"closed": 60, "average_vs_market": -0.004}},
             {"key": "momentum", "name": "Momentum", "history": {"closed": 8, "average_vs_market": 0.03}}]
    today = DAY.isoformat()
    open_("trend", "AAPL", today); open_("breakout", "AAPL", today)      # two rules with a record
    open_("trend", "MSFT", today, 0.61)                                  # one, and the model likes it
    open_("trend", "NVDA", today, 0.40)                                  # one, and the model does not
    open_("dip", "KO", today); open_("momentum", "KO", today)            # a losing rule and one with too few trades
    open_("trend", "OLD", (DAY - timedelta(days=20)).isoformat())        # picked too long ago to be an idea now
    got = stratlab.ideas(memory, DAY, table)
    assert [(i["symbol"], i["call"]) for i in got["ideas"]] == [("AAPL", "Stronger"), ("MSFT", "Stronger"),
                                                               ("NVDA", "Worth a look"), ("KO", "Unproven")]
    assert got["counts"] == {"Stronger": 2, "Worth a look": 1, "Unproven": 1}
    aapl = got["ideas"][0]
    assert "2 rules picked it, 2 with a record that beat the market" in aapl["line"] and "+1.2% a trade" in aapl["rules"][0]["record"]
    assert "none with a proven record" in got["ideas"][3]["line"] and "not advice" in got["note"]
    assert "the model gives it 61%" in got["ideas"][1]["line"]


def test_rules_read_from_the_owners_papers_load_once_as_proposed_and_stay_removed(memory):
    from buddy import strategy_seeds, stratrules
    got = {r["key"]: r for r in stratrules.rules(memory)}
    assert set(got) == {s["key"] for s in strategy_seeds.SEEDS} and len(got) == 6
    assert all(r["status"] == "proposed" and r["captures"] == "part of it" and r["needs"] for r in got.values())
    assert got["u_faber_10_month_average"]["source_url"] == "https://ssrn.com/abstract=962461"
    assert got["u_earnings_drift"]["live_only"] == ["earnings_surprise"] and got["u_low_beta_low_lottery"]["live_only"] == []
    assert "lottery" in stratrules.FIELDS and "disagree" in got["u_52_week_high"]["summary"]
    bars = {"AAPL": series(rising(), last_volume=2000), "SPY": series([100.0] * 300)}
    assert not any(k.startswith("u_") for k in stratlab.run(memory, DAY, Data(bars), insiders={})["opened"])  # none trades unapproved
    stratrules.decide(memory, "u_52_week_high", "remove")
    assert "u_52_week_high" not in {r["key"] for r in stratrules.rules(memory)} and stratrules.seed(memory) == 0
    assert len(stratlab.view(memory)["not_rules"]) == 3
    assert stratlab.figures(series(rising()))["lottery"] > 0
