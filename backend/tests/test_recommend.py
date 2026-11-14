"""Buy and sell signals: the switches, the judgement, writing down and scoring, the record and 'unproven'."""

import json
from datetime import date, timedelta

import pytest

from buddy import dealforecast, finnhub, pricebook, recommend, signals
from buddy.connectors.hub import ConnectorHub
from buddy.memory import Memory

DAY = date(2026, 11, 16)  # a Monday


@pytest.fixture
def memory(tmp_path):
    m = Memory(tmp_path / "buddy.db")
    ConnectorHub(m)
    yield m
    pricebook.close_all()


def bars(start=100.0, step=0.5, n=120):
    return [{"d": f"d{i}", "o": start + step * i, "h": start + step * i + 1, "l": start + step * i - 1,
             "c": start + step * i, "v": 1000} for i in range(n)]


def wavy(up=True, n=120):
    """A steady trend with pullbacks, so RSI isn't pinned at 100."""
    out, p = [], 100.0
    for i in range(n):
        p += (1.0 if up else -1.0) * (0.9 if i % 3 else -1.2)
        out.append({"d": f"d{i}", "o": p, "h": p + 1, "l": p - 1, "c": p, "v": 1000})
    return out


class FakeData:
    def __init__(self, series, latest=None):
        self.series, self.now = series, latest or {}

    def bars(self, market, symbols, days=200):
        return {s: self.series[s] for s in symbols if s in self.series}

    def latest(self, market, symbols):
        return {s: self.now[s] for s in symbols if s in self.now}


def attention(memory, key, mentions, tone, category="stocks", day=DAY, history=1):
    signals._ready(memory)
    for i in range(1, 31):
        pricebook.run(memory, "INSERT INTO signal_days VALUES (?, ?, 'ticker', ?, ?, 0, 0)",
                      ((day - timedelta(days=i + 1)).isoformat(), category, key, history))
    pricebook.run(memory, "INSERT INTO signal_days VALUES (?, ?, 'ticker', ?, ?, ?, 0)",
                  ((day - timedelta(days=1)).isoformat(), category, key, mentions, tone))


def test_everything_is_on_until_switched_off(memory):
    assert all(recommend.categories(memory).values()) and set(recommend.categories(memory)) == set(signals.CATEGORIES)
    assert "options" in recommend.switch(memory, ["options"], False)
    recommend.switch(memory, ["shopping"], False)
    c = recommend.categories(memory)
    assert not c["options"] and not c["tools"] and not c["hotels"] and c["stocks"]
    recommend.switch(memory, ["all"], True)
    assert all(recommend.categories(memory).values())
    with pytest.raises(Exception, match="Categories"):
        recommend.switch(memory, ["crypto"], True)


def test_deal_categories_and_the_switch_holds_back_a_deal_alert(memory):
    assert recommend.category_of_item("DeWalt 20V drill kit") == "tools"
    assert recommend.category_of_item("Hyatt Regency Lima, $120 per night") == "hotels"
    assert recommend.category_of_item("Round-trip flights EWR to LIM") == "flights"
    assert recommend.category_of_item("LG C5 OLED TV 65 inch") == "electronics"
    assert recommend.category_of_item("PS5 Slim console") == "games"
    p = {"title": "DeWalt 20V drill kit", "price": 99.0, "lowest": None, "store": "Home Depot", "reason": "sale",
         "url": "https://example.com/d", "source": "feed"}
    memory.set(dealforecast.PENDING_KEY, json.dumps([p, {**p, "title": "LG C5 OLED TV"}]))
    recommend.switch(memory, ["tools"], False)
    told = dealforecast.take_pending(memory)
    assert len(told) == 1 and "LG C5" in told[0][0]


def test_judging_buy_sell_caution_and_nothing():
    hot = {"key": "NVDA", "category": "stocks", "mentions": 8, "times_usual": 8.0, "tone": 0.6}
    buy = recommend.judge(hot, wavy(True), {"insiders": 40.0, "analysts": {"buy": 30, "hold": 5, "sell": 1,
                                                                             "buy_share": 0.83},
                                            "earnings_on": "2026-11-20"})
    assert buy["action"] == "buy" and any("insiders" in r for r in buy["reasons"])
    assert any("earnings on 2026-11-20" in r for r in buy["reasons"]) and any("8 times" in r for r in buy["reasons"])
    sell = recommend.judge({**hot, "tone": -0.7}, wavy(False), {"insiders": -50.0})
    assert sell["action"] == "sell"
    assert recommend.judge(hot, bars(), {})["action"] == "caution"  # straight up, RSI 100, a crowd: late
    assert recommend.judge({**hot, "tone": 0.0}, wavy(True), {})["action"] == "watch"  # attention alone is no signal
    assert recommend.judge(hot, bars(n=20), {}) is None


def test_a_morning_writes_signals_down_and_says_unproven(memory):
    attention(memory, "NVDA", 8, 0.6)
    attention(memory, "SPY", 9, 0.5, "etfs")
    attention(memory, "QUIET", 1, 0.9)             # one mention: not unusual
    attention(memory, "BUSY", 6, 0.6, history=5)   # always talked about: 1.2 times usual
    data = FakeData({"NVDA": wavy(True), "SPY": wavy(True), "QUIET": wavy(True), "BUSY": wavy(True)})
    told = recommend.run(memory, DAY, data, lambda s: {"insiders": 30.0})
    assert len(told) == 2 and all("UNPROVEN" in t and "not advice" in t for t in told)
    assert {r["key"] for r in recommend.recent(memory, today=DAY)} == {"NVDA", "SPY"}
    assert recommend.run(memory, DAY + timedelta(days=1), data, lambda s: {}) == []  # the same call isn't repeated
    recommend.switch(memory, ["etfs"], False)
    pricebook.run(memory, "DELETE FROM recs")
    assert len(recommend.run(memory, DAY, data, lambda s: {"insiders": 30.0})) == 1  # etfs off: only NVDA
    recommend.switch(memory, ["investing"], False)
    assert recommend.run(memory, DAY, data, lambda s: {}) == []


def test_signals_are_scored_a_week_later_and_the_record_becomes_proven(memory):
    recommend._ready(memory)
    then = (DAY - timedelta(days=8)).isoformat()
    for key, action, price in (("UP", "buy", 100.0), ("DOWN", "buy", 100.0), ("FLAT", "buy", 100.0),
                               ("FELL", "sell", 100.0), ("HYPE", "caution", 100.0)):
        pricebook.run(memory, "INSERT INTO recs (day, category, key, action, reasons, facts, price_then) VALUES "
                              "(?, 'stocks', ?, ?, '[]', '{}', ?)", (then, key, action, price))
    pricebook.run(memory, "INSERT INTO recs (day, category, key, action, reasons, facts, price_then) VALUES "
                          "(?, 'stocks', 'NEW', 'buy', '[]', '{}', 50)", (DAY.isoformat(),))
    data = FakeData({}, {"UP": 105.0, "DOWN": 95.0, "FLAT": 100.5, "FELL": 90.0, "HYPE": 110.0, "NEW": 60.0})
    assert recommend.settle(memory, DAY, data) == 5
    r = recommend.track_record(memory, "stocks")
    assert (r["right"], r["wrong"], r["flat"], r["open"], r["scored"]) == (2, 2, 1, 1, 5)
    assert r["hit_rate"] == 0.4 and not r["proven"]
    for i in range(15):
        pricebook.run(memory, "INSERT INTO recs (day, category, key, action, reasons, facts, price_then, status) "
                              "VALUES (?, 'stocks', ?, 'buy', '[]', '{}', 1, 'right')", (then, f"X{i}"))
    r = recommend.track_record(memory, "stocks")
    assert r["proven"] and "17 right of 20 scored" in recommend.message(
        {"key": "A", "category": "stocks", "action": "buy", "reasons": ["x"]}, r)
    page = recommend.page(memory)
    assert page["record"]["stocks"]["scored"] == 20 and page["categories"]["stocks"]


def test_finnhub_reads_and_only_sends_symbols(memory):
    seen = []

    def fetch(url, data=None, headers=None):
        seen.append((url, headers or {}))
        if "/quote" in url:
            return json.dumps({"c": 101.5, "dp": 1.2, "h": 102, "l": 99, "o": 100, "pc": 100.3, "t": 1})
        if "calendar/earnings" in url:
            return json.dumps({"earningsCalendar": [{"date": "2026-11-20", "symbol": "NVDA"}]})
        if "insider-sentiment" in url:
            return json.dumps({"data": [{"mspr": 40}, {"mspr": 20}]})
        if "stock/recommendation" in url:
            return json.dumps([{"period": "2026-10-01", "buy": 1, "hold": 9, "sell": 0, "strongBuy": 0,
                                "strongSell": 0},
                               {"period": "2026-11-01", "buy": 20, "strongBuy": 10, "hold": 5, "sell": 1,
                                "strongSell": 0}])
        return json.dumps([{"id": 7, "headline": "Nvidia beats", "summary": "s", "url": "https://x.example/a",
                            "source": "Wire", "datetime": 1794000000, "related": "NVDA"}])

    env = {"BUDDY_FINNHUB_KEY": "k"}
    assert finnhub.quote(memory, "NVDA", env, fetch)["price"] == 101.5
    assert finnhub.earnings(memory, "NVDA", 14, env, fetch, DAY) == "2026-11-20"
    assert finnhub.insiders(memory, "NVDA", env, fetch, DAY) == 30.0
    assert finnhub.analysts(memory, "NVDA", env, fetch) == {"buy": 30, "hold": 5, "sell": 1, "buy_share": 0.83}
    news = finnhub.market_news(memory, env, fetch)
    assert news[0]["id"] == "fh_7" and news[0]["symbol"] == "NVDA"
    assert all(h.get("X-Finnhub-Token") == "k" and "k" not in u.split("?")[1] for u, h in seen)
    with pytest.raises(Exception, match="isn't set up"):
        finnhub.quote(memory, "NVDA", {}, fetch)
