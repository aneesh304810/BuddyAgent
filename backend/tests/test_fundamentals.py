"""Company figures and the market figures (beta, volatility) the strategy lab's rules may read."""

import json
from datetime import date, timedelta

import pytest

from buddy import fundamentals, pricebook, stratlab, stratrules
from buddy.connectors.hub import ConnectorHub
from buddy.memory import Memory

DAY = date(2026, 11, 16)
ENV = {"BUDDY_FINNHUB_KEY": "k"}


@pytest.fixture
def memory(tmp_path):
    m = Memory(tmp_path / "buddy.db")
    ConnectorHub(m)
    yield m
    pricebook.close_all()


def fetch(url, data=None, headers=None):
    if "stock/metric" in url:
        return json.dumps({"metric": {"roeTTM": 24.0, "grossMarginTTM": 45.5, "peTTM": 18.2, "pbAnnual": 3.1,
                                      "totalDebt/totalEquityQuarterly": 0.6, "epsGrowthTTMYoy": None}})
    return json.dumps([{"period": "2026-06-30", "actual": 1.0, "estimate": 1.0, "surprisePercent": 0.0},
                       {"period": "2026-09-30", "actual": 1.32, "estimate": 1.2, "surprisePercent": 10.0}])


def test_company_figures_are_read_as_fractions_and_only_the_ticker_goes_out(memory):
    got = fundamentals.read(memory, "AAPL", ENV, fetch)
    assert got == {"roe": 0.24, "gross_margin": 0.455, "pe": 18.2, "pb": 3.1, "debt_to_equity": 0.6,
                   "earnings_surprise": 0.1}  # the newest quarter, and a missing figure is left out
    assert fundamentals.refresh(memory, ["AAPL", "MSFT"], ENV, fetch, DAY) == {"asked": 2, "kept": 2, "failed": 0}
    assert fundamentals.latest(memory, DAY)["MSFT"]["roe"] == 0.24
    assert fundamentals.latest(memory, DAY + timedelta(days=200)) == {}  # too old to use
    assert fundamentals.refresh(memory, ["AAPL"], {}, fetch, DAY)["note"] == "Finnhub isn't set up"
    assert fundamentals.status(memory) == {"companies": 2, "day": DAY.isoformat()}
    assert set(stratrules.LIVE_ONLY) == set(fundamentals.FIELDS) and all(f in stratrules.FIELDS for f in fundamentals.FIELDS)


def test_beta_and_volatility_come_from_returns_shared_with_the_market():
    start = date(2025, 1, 1)
    spy, twice, flat = [], [], []
    p = q = 100.0
    for i in range(300):
        move = 0.01 if i % 2 else -0.008
        p *= 1 + move
        q *= 1 + 2 * move                      # moves twice as much as the market every day
        d = (start + timedelta(days=i)).isoformat()
        spy.append({"d": d, "c": p})
        twice.append({"d": d, "c": q})
        flat.append({"d": d, "c": 50.0})
    index = stratlab.market_index({"SPY": spy, "TWICE": twice, "FLAT": flat})
    last = spy[-1]["d"]
    assert index["TWICE"][last][0] == 2.0 and index["SPY"][last][0] == 1.0 and index["FLAT"][last] == (0.0, 0.0)
    assert index["TWICE"][last][1] > index["SPY"][last][1] > 0   # the jumpier stock swings more over a year
    assert spy[100]["d"] not in index["TWICE"]                   # too little shared history to say


def test_a_rule_on_company_figures_trades_now_and_is_marked_as_having_no_history(memory):
    import asyncio

    async def ask(m, prompt):
        assert "earnings_surprise" in prompt and "beta" in prompt  # the model is told the fields exist
        return {"name": "Earnings beat", "summary": "Buys after a profit surprise.", "universe": "stocks",
                "buy": [{"field": "earnings_surprise", "op": ">=", "value": 0.05}, {"field": "beta", "op": "<", "value": 50}],
                "hold_days": 40, "needs": []}

    rule = asyncio.run(stratrules.propose(memory, "pead.pdf", "Buy stocks that beat earnings expectations by 5%. " * 40, ask))
    assert rule["live_only"] == ["earnings_surprise"] and rule["captures"].startswith("all of it")
    short = asyncio.run(stratrules.propose(memory, "note.txt", "Buy stocks that beat earnings expectations by 5%. " * 3, ask))
    assert short["captures"] == "part of it"  # too little text to be sure the whole strategy was there
    stratrules.decide(memory, rule["key"], "approve")
    closes = [100.0 + 0.3 * i for i in range(300)]
    start = DAY - timedelta(days=300)
    series = lambda cs: [{"d": (start + timedelta(days=i + 1)).isoformat(), "o": c, "h": c * 1.005, "l": c * 0.995, "c": c,  # noqa: E731
                          "v": 1000} for i, c in enumerate(cs)]

    class Data:
        def bars(self, market, symbols, days=0):
            return {s: b for s, b in {"AAPL": series(closes), "MSFT": series(closes), "SPY": series([100 + 0.1 * i for i in range(300)])}.items()
                    if s in symbols}

    fundamentals.refresh(memory, ["AAPL"], ENV, fetch, DAY)  # AAPL beat by 10%; nothing is known about MSFT
    opened = stratlab.run(memory, DAY, Data(), insiders={})["opened"]
    assert opened[rule["key"]] == 1
    trade = pricebook.run(memory, "SELECT symbol, features FROM lab_trades WHERE strategy = ?", (rule["key"],))[0]
    kept = json.loads(trade["features"])
    assert trade["symbol"] == "AAPL" and kept["earnings_surprise"] == 0.1 and "beta" in kept and kept["roe"] == 0.24
    assert stratlab.replay(memory, Data(), months=6, keys=[rule["key"]])["trades"].get(rule["key"], 0) == 0  # no history of it
