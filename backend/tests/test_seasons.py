"""Seasonal price patterns from government price indexes: the maths, the refresh, and its use in advice."""

import json
import math
from datetime import date, datetime

import pytest

from buddy import dealforecast, pricebook, seasons, whentobuy
from buddy.connectors.hub import ConnectorHub
from buddy.memory import Memory

ENV = {"BUDDY_FRED_KEY": "k"}


@pytest.fixture
def memory(tmp_path):
    m = Memory(tmp_path / "buddy.db")
    ConnectorHub(m)
    yield m
    pricebook.close_all()


def series(months=133, low_month=12, depth=4.0, growth=0.002):
    """Monthly index, newest first like FRED: steady inflation, with a dip centred on low_month."""
    rows, y, m = [], 2015, 1
    for i in range(months):
        dip = -depth * math.cos((m - low_month) * math.pi / 6) / 2 - depth / 2
        rows.append((f"{y}-{m:02d}-01", round(100 * (1 + growth) ** i * (1 + dip / 100), 4)))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return rows[::-1]


def fake_fetch(missing=()):
    def fetch(url, data=None, headers=None):
        sid = url.split("series_id=")[1].split("&")[0]
        if sid in missing:
            raise RuntimeError("HTTP 400: The series does not exist.")
        low = 7 if sid == seasons.SERIES["airline fares"][0] else 12
        low = 1 if sid == seasons.SERIES["hotels"][0] else low
        return json.dumps({"observations": [{"date": d, "value": str(v)} for d, v in series(low_month=low)]})
    return fetch


def test_the_seasonal_shape_ignores_inflation():
    s = seasons.seasonal(series())
    assert s["cheapest"][0] == 12 and s["dearest"][0] == 6
    assert 3.5 < s["swing_pct"] < 4.5 and s["years"] >= 9
    assert abs(sum(s["by_month"].values())) < 0.1  # centred on the year's trend
    assert seasons.seasonal(series(months=30)) is None
    flat = seasons.seasonal(series(depth=0, growth=0.01))  # fast inflation alone is not a season
    assert flat["swing_pct"] < 0.2


def test_refresh_keeps_what_fred_has_and_names_what_it_doesnt(memory):
    status = seasons.refresh(memory, ENV, fake_fetch(missing={seasons.SERIES["toys"][0]}))
    assert "13 categories" in status and "toys" in status
    assert seasons.get(memory, "televisions")["cheapest"][0] == 12
    assert seasons.get(memory, "toys") is None
    assert "toys" in json.loads(memory.get(seasons.STATUS_KEY))["status"]


def test_no_key_says_so(memory):
    assert "FRED isn't set up" in seasons.refresh(memory, {}, fake_fetch())


def test_outlook_for_things_flights_and_hotels(memory):
    seasons.refresh(memory, ENV, fake_fetch())
    tv = seasons.outlook(memory, "LG C5 OLED TV 65 inch", today=date(2026, 9, 10), within_days=120)
    assert tv["category"] == "televisions" and tv["better_month_ahead"] == "Dec" and tv["gain_pct"] > 1
    assert "Dec" in seasons.reason(tv) and "government price data" in seasons.reason(tv)
    in_dec = seasons.outlook(memory, "oled tv", today=date(2026, 12, 5))
    assert in_dec["better_month_ahead"] == "" and "cheaper times of year" in seasons.reason(in_dec)
    assert seasons.outlook(memory, "flight to Lima", today=date(2026, 4, 1))["cheapest_months"][0] == "Jul"
    assert seasons.outlook(memory, "Marriott hotel in Lima", today=date(2026, 4, 1))["cheapest_months"][0] == "Jan"
    assert seasons.outlook(memory, "hotels")["category"] == "hotels"  # a category by name
    assert seasons.outlook(memory, "a bag of rice") is None


def test_buy_or_wait_and_the_forecast_use_the_season(memory):
    before = whentobuy.advise(memory, "LG OLED TV", 1500, "electronics", today=date(2026, 8, 20), save=False)
    seasons.refresh(memory, ENV, fake_fetch())
    after = whentobuy.advise(memory, "LG OLED TV", 1500, "electronics", today=date(2026, 8, 20), save=False)
    assert any("government price data" in r for r in after["reasons"])
    assert not any("government price data" in r for r in before["reasons"])
    assert after["chance_of_better_price"] >= before["chance_of_better_price"]
    f = dealforecast.forecast(memory, "LG OLED TV", "electronics", 1500, today=date(2026, 8, 20))
    mine = [x for x in f["next_deals"] if x["source"] == "season (government data)"]
    assert mine and mine[0]["expect_price"] < 1500


def test_monthly_and_the_tool(memory):
    now = datetime(2026, 11, 14, 9, 0)
    assert seasons.due(memory, now)
    memory.set(seasons.MONTH_KEY, "2026-11")
    assert not seasons.due(memory, now) and seasons.due(memory, datetime(2026, 12, 1, 9, 0))
    assert "refresh the seasonal prices" in seasons.run_tool(memory, {"action": "list"})
    seasons.refresh(memory, ENV, fake_fetch())
    assert json.loads(seasons.run_tool(memory, {"action": "list"}))["airline fares"]["cheapest"][0] == "Jul"
    assert json.loads(seasons.run_tool(memory, {"action": "show", "what": "dewalt drill"}))["category"] == \
        "tools and outdoor equipment"
