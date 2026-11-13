"""Market school: the weekday lesson, FRED / EIA / SEC data, themes and concepts (no network)."""

import asyncio
import json
from datetime import date, datetime, timedelta, timezone

import pytest

from buddy import market_school as ms
from buddy import markets
from buddy.memory import Memory

ENV = {"BUDDY_FRED_KEY": "f", "BUDDY_EIA_KEY": "e", "BUDDY_SEC_CONTACT": "me@example.com"}


def fake_fetch(seen):
    def fetch(url, data=None, headers=None, *rest):
        seen.append((url, headers or {}))
        if "stlouisfed" in url:
            series = url.split("series_id=")[1].split("&")[0]
            today = date(2026, 11, 12)
            if series == "CPIAUCSL":
                obs = [{"date": f"2026-{12 - i:02d}-01" if i < 12 else "2025-11-01", "value": str(104 - i * 0.3)}
                       for i in range(13)]
                obs[12]["value"] = "100"
                obs[0]["value"] = "104"
            elif series == "UNRATE":
                obs = [{"date": "2026-10-01", "value": "4.3"}]
            else:
                base = {"DFF": 3.88, "DGS2": 4.5, "DGS10": 4.2}[series]
                obs = [{"date": (today - timedelta(days=i)).isoformat(), "value": "." if i == 1 else str(base - i * 0.01)}
                       for i in range(60)]
            return json.dumps({"observations": obs}).encode()
        if "eia.gov" in url:
            rows = [{"period": (date(2026, 11, 12) - timedelta(days=i)).isoformat(), "value": 80 - i * 0.5}
                    for i in range(20)][::-1]
            return json.dumps({"response": {"data": rows}}).encode()
        if "company_tickers" in url:
            return json.dumps({"0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."}}).encode()
        if "companyfacts" in url:
            def fy(year, val, start=True):
                row = {"fy": year, "fp": "FY", "form": "10-K", "end": f"{year}-09-30", "val": val,
                       "filed": f"{year}-11-01"}
                if start:
                    row["start"] = f"{year - 1}-10-01"
                return row
            quarter = {"fy": 2026, "fp": "FY", "form": "10-K", "start": "2026-07-01", "end": "2026-09-30",
                       "val": 1, "filed": "2026-11-01"}
            return json.dumps({"entityName": "Apple Inc.", "facts": {"us-gaap": {
                "Revenues": {"units": {"USD": [fy(2025, 400.0), fy(2026, 440.0), quarter]}},
                "NetIncomeLoss": {"units": {"USD": [fy(2025, 100.0), fy(2026, 110.0)]}},
                "EarningsPerShareDiluted": {"units": {"USD/shares": [fy(2026, 7.1)]}},
                "CashAndCashEquivalentsAtCarryingValue": {"units": {"USD": [fy(2026, 30.0, start=False)]}}}}}).encode()
        raise AssertionError(url)
    return fetch


class FakeData:
    def __init__(self, moves):
        self.moves = moves

    def bars(self, market, symbols, days=400):
        out = {}
        start = date(2026, 1, 2)
        for s in symbols:
            day = self.moves.get(s, 0.0)
            closes = [100.0] * 70 + [100.0 * (1 + day / 100)]
            out[s] = [{"d": (start + timedelta(days=i)).isoformat(), "c": c} for i, c in enumerate(closes)]
        return out


def test_macro_reads_rates_inflation_curve_and_energy(tmp_path, monkeypatch):
    memory, seen = Memory(tmp_path / "a.db"), []
    m = ms.macro(memory, ENV, fake_fetch(seen))
    assert m["fed_funds"]["value"] == 3.88 and m["ten_year"]["value"] == 4.2
    assert m["curve"] == round(4.2 - 4.5, 2) and m["curve"] < 0
    assert m["inflation"]["value"] == 4.0 and m["unemployment"]["value"] == 4.3
    assert m["oil"]["value"] == 80 and m["oil"]["week_pct"] is not None and not m["missing"]
    assert all("api_key=" in u for u, _ in seen)  # keys travel in the address, which the logs redact
    n = len(seen)
    ms.macro(memory, ENV, fake_fetch(seen))
    assert len(seen) == n  # kept for 6 hours


def test_macro_without_keys_says_how_to_connect(tmp_path):
    m = ms.macro(Memory(tmp_path / "b.db"), {}, fake_fetch([]))
    assert any("BUDDY_FRED_KEY" in x for x in m["missing"]) and any("BUDDY_EIA_KEY" in x for x in m["missing"])


def test_company_from_sec_filings_skips_quarters_and_names_a_contact(tmp_path):
    seen = []
    c = ms.company(Memory(tmp_path / "c.db"), "aapl", ENV, fake_fetch(seen))
    assert c["company"] == "Apple Inc." and [r["year"] for r in c["years"]] == [2025, 2026]
    assert c["years"][-1]["revenue"] == 440.0 and c["years"][-1]["margin_pct"] == 25.0
    assert c["revenue_growth_pct"] == 10.0 and c["years"][-1]["eps"] == 7.1
    assert all("me@example.com" in h.get("User-Agent", "") for _, h in seen)
    with pytest.raises(Exception, match="ETFs"):
        ms.company(Memory(tmp_path / "c2.db"), "VOO", ENV, fake_fetch([]))
    with pytest.raises(Exception, match="BUDDY_SEC_CONTACT"):
        ms.company(Memory(tmp_path / "c3.db"), "AAPL", {}, fake_fetch([]))


def test_lesson_picks_the_concept_today_calls_for_and_falls_back_to_plain_words(tmp_path, monkeypatch):
    memory = Memory(tmp_path / "d.db")
    async def no_model(memory, prompt):
        raise RuntimeError("paused")
    monkeypatch.setattr(markets, "_ask_claude", no_model)
    data = FakeData({"USO": -4.5, "XLE": -2.5, "XLK": 1.2, "SPY": 0.4, "GLD": 1.0})
    job, text = asyncio.run(ms.lesson(memory, ENV, fake_fetch([]), data))
    assert text.startswith("📚 Market school:") and "oil and gas funds" in text.lower()
    row = memory.db.execute("SELECT kind, report FROM jobs WHERE id = ?", (job,)).fetchone()
    assert row["kind"] == "market_school" and "## Themes to learn about" in row["report"]
    assert "Rates and inflation (FRED)" in row["report"] and "Education, not advice" in row["report"]
    # the same call isn't repeated within two weeks: next time the yield curve (inverted) is due instead
    _, text2 = asyncio.run(ms.lesson(memory, ENV, fake_fetch([]), data))
    assert "yield curve" in text2.lower()


def test_themes_rank_by_month():
    prices = ms.moves(None, data=FakeData({"SMH": 5.0, "GLD": -2.0}))
    rows = ms.themes(prices)
    assert rows[0]["theme"] == "AI chips" and rows[-1]["month"] <= 0


def test_due_weekday_mornings_new_york_once_and_off_switch(tmp_path):
    memory = Memory(tmp_path / "e.db")
    ny = lambda y, mo, d, h, mi: datetime(y, mo, d, h, mi, tzinfo=timezone(timedelta(hours=-5)))  # noqa: E731
    assert not ms.due(memory, ny(2026, 11, 13, 7, 59))
    assert ms.due(memory, ny(2026, 11, 13, 8, 0))
    assert not ms.due(memory, ny(2026, 11, 14, 9, 0))  # Saturday
    memory.set(ms.DONE_KEY, ms.school_day(ny(2026, 11, 13, 9, 0)))
    assert not ms.due(memory, ny(2026, 11, 13, 9, 0))
    memory.set(ms.DONE_KEY, "")
    asyncio.run(ms.run_tool(memory, {"action": "off"}))
    assert not ms.due(memory, ny(2026, 11, 13, 9, 0))
    asyncio.run(ms.run_tool(memory, {"action": "on"}))
    assert ms.due(memory, ny(2026, 11, 13, 9, 0))


def test_concepts_known_and_unknown(tmp_path):
    memory = Memory(tmp_path / "f.db")
    got = json.loads(asyncio.run(ms.run_tool(memory, {"action": "concept", "concept": "expense ratio"})))
    assert got["title"] == "Expense ratios"
    other = json.loads(asyncio.run(ms.run_tool(memory, {"action": "concept", "concept": "short squeeze"})))
    assert "explain it yourself" in other["note"]


def test_connectors_and_scan_settings():
    from buddy import gateway
    for name in ("fred", "eia", "sec"):
        assert gateway.CATALOG[name]["health"]["custom"].startswith("market_school.")
    assert "GLD" in markets.US and "USO" in markets.US
    assert markets.Markets(Memory(":memory:")).settings()["markets"] == ["us", "crypto"]
