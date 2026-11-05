"""Markets: setups found by code on daily bars, plans with entry/stop/targets, chatter, tracking, never a trade."""

import asyncio
import json
from datetime import date, datetime, timedelta, timezone

from buddy import markets as mk
from buddy.markets import Markets, find_setups
from buddy.memory import Memory


def series(closes, vol=5_000_000, last_vol=None, spread=0.01):
    start = date(2025, 1, 1)
    out = []
    for i, c in enumerate(closes):
        out.append({"d": (start + timedelta(days=i)).isoformat(), "o": c, "h": c * (1 + spread), "l": c * (1 - spread),
                    "c": c, "v": vol})
    if last_vol:
        out[-1]["v"] = last_vol
    return out


def uptrend_then_base(n=260):
    closes = [50 + i * 0.25 for i in range(n - 20)]            # a steady climb
    top = closes[-1]
    closes += [top * (0.985 + 0.004 * ((i % 3) - 1)) for i in range(20)]   # three weeks tight under the high
    return closes


def test_a_breakout_on_volume_gets_a_plan_with_stop_and_targets():
    closes = uptrend_then_base()
    closes.append(closes[-21] * 1.03)                            # closes above the 20-day high
    bars = series(closes, last_vol=12_000_000)
    found = find_setups(bars, series([100 + i * 0.05 for i in range(len(closes))]))
    assert found and found[0]["pattern"] == "breakout"
    p = found[0]
    assert p["stop"] < p["entry"] < p["t1"] < p["t2"]
    assert abs((p["t1"] - p["entry"]) - 2 * (p["entry"] - p["stop"])) < 1e-6
    assert any("volume" in n for n in p["notes"]) and any("stronger than the market" in n for n in p["notes"])


def quiet_base():
    """A climb with normal daily swings, then three weeks quieter, just under the high."""
    bars = series(uptrend_then_base())
    for b in bars[-20:]:
        b["h"], b["l"] = b["c"] * 1.002, b["c"] * 0.998
    return bars


def test_a_tight_base_under_the_high_waits_for_the_pivot():
    bars = quiet_base()
    pats = {p["pattern"]: p for p in find_setups(bars)}
    assert "base" in pats and pats["base"]["entry"] > bars[-1]["c"]


def test_thin_stocks_and_short_histories_are_skipped():
    assert find_setups(series(uptrend_then_base(), vol=1_000)) == []
    assert find_setups(series([10, 11, 12])) == []


class FakeWeb:
    """Alpaca bars and snapshots, Reddit search, StockTwits and news, all made up."""

    def __init__(self, bars, price=None):
        self.bars, self.price, self.urls = bars, price, []

    def __call__(self, url, headers=None, data=None):
        self.urls.append(url)
        if "/v2/stocks/bars" in url:
            sym = url.split("symbols=")[1].split("&")[0].replace("%2C", ",").split(",")
            return {"bars": {s: [{"t": b["d"] + "T05:00:00Z", "o": b["o"], "h": b["h"], "l": b["l"], "c": b["c"],
                                  "v": b["v"]} for b in self.bars[s]] for s in sym if s in self.bars}}
        if "/v2/stocks/snapshots" in url:
            return {"NVDA": {"latestTrade": {"p": self.price}}}
        if "reddit.com" in url:
            return {"data": {"children": [{"data": {"title": "NVDA breaking out of the base?", "subreddit": "stocks",
                                                    "score": 812, "num_comments": 140, "permalink": "/r/stocks/x"}}]}}
        if "stocktwits" in url:
            return {"messages": [{"entities": {"sentiment": {"basic": "Bullish"}}}] * 3}
        if "/news" in url:
            return {"news": [{"headline": "Nvidia unveils new chip", "source": "benzinga", "url": "https://x.com/n",
                              "created_at": "2026-11-04T10:00:00Z"}]}
        if "options/snapshots" in url:
            return {"snapshots": {"NVDA261218C00200000": {"latestQuote": {"bp": 9.8, "ap": 10.2},
                                                          "greeks": {"delta": 0.61}, "impliedVolatility": 0.42}}}
        return {}


ENV = {"BUDDY_ALPACA_KEY_ID": "k", "BUDDY_ALPACA_SECRET": "s"}


def test_the_daily_scan_writes_a_report_with_chatter_and_tracks_the_ideas():
    mem = Memory(":memory:")
    closes = uptrend_then_base()
    nvda = quiet_base()
    for b in nvda:
        for k in ("o", "h", "l", "c"):
            b[k] *= 3
    bars = {"NVDA": nvda, "SPY": series([400 + i * 0.1 for i in range(len(closes))])}
    web = FakeWeb(bars)
    asked = []

    async def ask(memory, prompt):
        asked.append(prompt)
        assert "NVDA breaking out of the base?" in prompt and "Nvidia unveils new chip" in prompt
        return {"ideas": [{"symbol": "NVDA", "take": "A tight base with real news behind it.", "sentiment": "bullish",
                           "risks": "earnings in two weeks"}]}
    m = Markets(mem, env=ENV, fetch=web, ask=ask)
    m.set(markets=["us"], account=50000, risk_pct=1)
    job, summary = asyncio.run(m.scan(today=date(2026, 11, 5)))
    assert job and "NVDA" in summary and "not advice" in summary
    report = mem.db.execute("SELECT report FROM jobs WHERE id = ?", (job,)).fetchone()[0]
    for bit in ("## NVDA: Tight base under the high", "A tight base with real news behind it.", "Reddit:** 1 posts",
                "StockTwits:** 3 bullish", "Option idea:** NVDA 2026-12-18 $200 call, about $10.00", "shares risks 1%",
                "not financial advice", "earnings in two weeks"):
        assert bit in report, bit
    setup = mem.db.execute("SELECT * FROM mkt_setups").fetchone()
    assert setup["status"] == "watching"

    # in market hours the price breaks the entry: one alert with the plan
    web.price = setup["entry"] * 1.01
    open_ny = datetime(2026, 11, 6, 15, 0, tzinfo=timezone.utc)   # 10:00 in New York, a Friday
    alerts = m.check(open_ny)
    assert len(alerts) == 1 and "NVDA just broke above" in alerts[0]["text"] and "I don't trade" in alerts[0]["text"]
    assert m.check(open_ny) == []                                  # said once

    # the user buys: stop and targets watched
    out = m.bought("nvda", setup["entry"] * 1.01)
    assert "Tracking NVDA" in out and "You place any order yourself" in out
    pos = m.positions()[0]
    web.price = pos["t1"] + 0.01
    assert "target 1" in m.check(open_ny)[0]["text"]
    web.price = pos["stop"] - 0.01
    assert "hit your stop" in m.check(open_ny)[0]["text"]
    assert "Closed NVDA" in m.sold("NVDA", pos["stop"])


def test_nothing_is_checked_outside_market_hours_and_the_scan_waits_for_the_close():
    mem = Memory(":memory:")
    m = Markets(mem, env=ENV, fetch=FakeWeb({}, price=1))
    assert not m.open_hours("us", datetime(2026, 11, 7, 15, 0, tzinfo=timezone.utc))      # Saturday
    assert m.open_hours("crypto", datetime(2026, 11, 7, 15, 0, tzinfo=timezone.utc))
    assert not mk.scan_due(mem, datetime(2026, 11, 5, 20, 0, tzinfo=timezone.utc))        # 3 PM New York
    assert mk.scan_due(mem, datetime(2026, 11, 5, 22, 0, tzinfo=timezone.utc))            # 5 PM
    mem.set("markets:scanned", "2026-11-05")
    assert not mk.scan_due(mem, datetime(2026, 11, 5, 22, 0, tzinfo=timezone.utc))


def test_symbols_and_the_tool():
    assert Markets.normal("$btc") == ("BTC/USD", "crypto")
    assert Markets.normal("reliance") == ("RELIANCE", "india")
    assert Markets.normal("aapl") == ("AAPL", "us")
    mem = Memory(":memory:")
    assert "watchlist" in mk.run_tool(mem, {"action": "watch", "symbol": "PLTR"})
    assert "Scanning now" in mk.run_tool(mem, {"action": "scan"}) and mem.get("markets:scan_requested") == "1"
    dash = json.loads(mk.run_tool(mem, {"action": "ideas"}))
    assert dash["watchlist"][0]["symbol"] == "PLTR" and "not financial advice" in dash["note"]
