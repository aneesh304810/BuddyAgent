"""Live triggers: what is followed, the triggers, once a day each, the daily cap, market hours and the switches."""

from datetime import datetime, timedelta, timezone

import pytest

from buddy import livewatch, pricebook, recommend
from buddy.connectors.hub import ConnectorHub
from buddy.memory import Memory

OPEN = datetime(2026, 11, 16, 15, 0, tzinfo=timezone.utc)  # Monday 10:00 in New York


@pytest.fixture
def memory(tmp_path, monkeypatch):
    monkeypatch.delenv("BUDDY_LIVE_SIGNALS", raising=False)
    m = Memory(tmp_path / "buddy.db")
    ConnectorHub(m)
    m.db.execute("CREATE TABLE IF NOT EXISTS mkt_watch (symbol TEXT, market TEXT, note TEXT)")
    m.db.execute("CREATE TABLE IF NOT EXISTS mkt_setups (symbol TEXT, market TEXT, status TEXT, score REAL)")
    livewatch._ring.clear()
    yield m
    pricebook.close_all()


class FakeData:
    """Twenty quiet days around 100: yesterday's close 100, 20-day high 102, low 98."""

    def bars(self, market, symbols, days=60):
        return {s: [{"d": f"2026-10-{i + 10:02d}", "o": 100, "h": 102, "l": 98, "c": 100, "v": 1} for i in range(20)]
                for s in symbols if s != "NEWCO"}


def watch(memory, *symbols):
    for s in symbols:
        memory.db.execute("INSERT INTO mkt_watch VALUES (?, 'us', '')", (s,))


def check(memory, price, at=OPEN, **kw):
    return livewatch.check(memory, at, FakeData(), lambda s: {"price": price[s]} if s in price else None, **kw)


def test_what_is_followed_and_the_category_switches(memory):
    watch(memory, "NVDA", "SPY", "GLD")
    memory.db.execute("INSERT INTO mkt_setups VALUES ('AMD', 'us', 'watching', 9)")
    recommend._ready(memory)
    pricebook.run(memory, "INSERT INTO recs (day, category, key, action, reasons, facts, price_then) VALUES "
                          "(?, 'stocks', 'PLTR', 'buy', '[]', '{}', 50)", (OPEN.date().isoformat(),))
    got = {f["symbol"]: f["why"] for f in livewatch.followed(memory, OPEN.date())}
    assert got["NVDA"] == "on your watch list" and "trade idea" in got["AMD"] and "buy signal" in got["PLTR"]
    recommend.switch(memory, ["etfs", "commodities"], False)
    assert {f["symbol"] for f in livewatch.followed(memory, OPEN.date())} == {"NVDA", "AMD", "PLTR"}


def test_breakout_big_day_and_each_said_once(memory):
    watch(memory, "NVDA")
    assert check(memory, {"NVDA": 101.0}) == []                      # an ordinary minute
    told = check(memory, {"NVDA": 103.5}, OPEN + timedelta(minutes=1))
    assert len(told) == 1 and "broke above its 20-day high ($102.00)" in told[0] and "up 3.5% today" in told[0]
    assert "on your watch list" in told[0] and "not advice" in told[0] and "15 minutes late" in told[0]
    assert check(memory, {"NVDA": 103.8}, OPEN + timedelta(minutes=2)) == []   # already said today
    more = check(memory, {"NVDA": 106.5}, OPEN + timedelta(minutes=30))
    assert len(more) == 1 and "up 6.5% today" in more[0] and "20-day high" not in more[0]
    events = pricebook.run(memory, "SELECT kind FROM live_events ORDER BY seen")
    assert [e["kind"] for e in events] == ["breakout", "big_up", "huge_up"]
    next_day = check(memory, {"NVDA": 103.5}, OPEN + timedelta(days=1))
    assert len(next_day) == 1  # a new day: said again


def test_a_fast_move_and_a_breakdown(memory):
    watch(memory, "AMD")
    check(memory, {"AMD": 100.0})
    for i in range(1, 5):
        assert check(memory, {"AMD": 100.2}, OPEN + timedelta(minutes=i)) == []
    told = check(memory, {"AMD": 98.3}, OPEN + timedelta(minutes=5))
    assert len(told) == 1 and "in five minutes" in told[0] and "-1.7%" in told[0]
    told = check(memory, {"AMD": 97.5}, OPEN + timedelta(minutes=6))
    assert "fell below its 20-day low ($98.00)" in told[0]


def test_funds_use_smaller_moves_and_options_off_drops_the_note(memory):
    watch(memory, "SPY", "NVDA")
    recommend.switch(memory, ["options"], False)
    told = check(memory, {"SPY": 101.6, "NVDA": 101.6})
    assert len(told) == 1 and told[0].startswith("⚡ SPY") and "15 minutes late" not in told[0]


def test_the_daily_cap_and_symbols_without_history(memory):
    names = [f"S{i}" for i in range(12)] + ["NEWCO"]
    watch(memory, *names)
    told = check(memory, {s: 104.0 for s in names})
    assert len(told) == livewatch.MAX_A_DAY                      # the rest are kept, not sent
    assert pricebook.run(memory, "SELECT COUNT(DISTINCT symbol) AS n FROM live_events")[0]["n"] == 12


def test_only_in_market_hours_and_can_be_turned_off(memory, monkeypatch):
    assert livewatch.due(memory, OPEN)
    memory.set(livewatch.MINUTE_KEY, OPEN.strftime("%Y-%m-%dT%H:%M"))
    assert not livewatch.due(memory, OPEN) and livewatch.due(memory, OPEN + timedelta(minutes=1))
    assert not livewatch.due(memory, OPEN.replace(hour=3))            # before the open
    assert not livewatch.due(memory, OPEN + timedelta(days=5))        # Saturday
    monkeypatch.setenv("BUDDY_LIVE_SIGNALS", "0")
    assert not livewatch.due(memory, OPEN + timedelta(minutes=2))


def test_no_price_source_or_nothing_followed_says_so(memory):
    assert livewatch.check(memory, OPEN, env={}) == []
    assert "Finnhub key" in memory.get(livewatch.STATUS_KEY)
    assert check(memory, {}) == [] and "watch list" in memory.get(livewatch.STATUS_KEY)
    assert livewatch.source({"BUDDY_FINNHUB_KEY": "k"}) == "finnhub"
    assert livewatch.source({"BUDDY_ALPACA_KEY_ID": "a", "BUDDY_ALPACA_SECRET": "b"}) == "alpaca"
