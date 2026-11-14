"""Why deals happen, rhythms, forecasts, alerts when the deal comes, and the weekly 'deals to watch for'."""

import gzip
import json
from datetime import date, datetime

import pytest

from buddy import dealforecast as df
from buddy import patterns, pricebook, shopper
from buddy.connectors.hub import ConnectorHub
from buddy.memory import Memory


@pytest.fixture
def memory(tmp_path):
    m = Memory(tmp_path / "buddy.db")
    ConnectorHub(m)
    yield m
    pricebook.close_all()


def test_reasons_from_words_and_the_calendar():
    assert df.reason_of("Sony headphones $299", day=date(2026, 11, 28), kind="electronics") == \
        "holiday sale: Black Friday to Cyber Monday"
    assert df.reason_of("Winter jackets clearance up to 70% off", day=date(2026, 3, 10)) == "clearance"
    assert df.reason_of("Lightning deal: Echo Dot $22", day=date(2026, 3, 10)) == "flash deal"
    assert df.reason_of("Extra 20% off with code SAVE20", day=date(2026, 3, 10)) == "coupon"
    assert df.reason_of("Pixel 9 (previous gen) $499", day=date(2026, 3, 10)) == "new model"
    assert df.reason_of("Ninja air fryer $79", day=date(2026, 3, 10)) == "price drop"


def test_model_key_matches_across_stores():
    a = df.model_key("Sony WH-1000XM6 Wireless Noise Cancelling Headphones")
    b = df.model_key("Sony WH-1000XM6 Headphones - Black, Best Buy")
    assert a == b == "sony wh-1000xm6" and df._same(a, b)
    assert not df._same(df.model_key("Sony WH-1000XM5"), a)


def test_forecast_from_its_own_rhythm_then_alert_when_it_comes(memory):
    patterns.keep(memory, {"fitted_at": "x", "prices": 10, "item_deals": {"sony wh-1000xm6": {
        "dates": ["2026-07-10", "2026-08-25", "2026-10-09"], "prices": [329.0, 319.0, 299.0],
        "depths": [0.25, 0.27, 0.33], "stores": ["Best Buy"]}}})
    f = df.forecast(memory, "Sony WH-1000XM6 headphones", "electronics", 399.0, today=date(2026, 11, 14))
    own = next(n for n in f["next_deals"] if n["source"] == "its own deal history")
    assert own["when"] == "2026-11-24" and own["expect_price"] == 299.0 and "about every 46 days" in own["why"]
    assert any(n["source"] == "sales calendar" and "Black Friday" in n["why"] for n in f["next_deals"])
    w = df.watch(memory, "Sony WH-1000XM6 headphones", "electronics", price=399.0)
    assert w["target"] == f["next_deals"][0]["expect_price"]
    # a deal arrives from the inbox overnight: it waits for the morning, once
    assert df.check(memory, [{"title": "Sony WH-1000XM6 Wireless Headphones", "price": 279.0, "store": "Best Buy",
                              "url": "https://bestbuy.example/x", "source": "email: Best Buy"}]) == 1
    assert df.check(memory, [{"title": "Sony WH-1000XM6", "price": 275.0}]) == 0  # once a day
    msgs = df.take_pending(memory)
    assert len(msgs) == 1 and "The deal you were watching for is here" in msgs[0][0] and msgs[0][1][0]["url"]
    assert df.take_pending(memory) == []
    assert "Stopped 1" in df.stop(memory, "sony")


def test_too_expensive_or_different_model_is_not_told(memory):
    df.watch(memory, "LG C5 OLED TV", "electronics", target=1299.0)
    assert df.check(memory, [{"title": "LG C5 OLED TV 65 inch", "price": 1499.0}]) == 0
    assert df.check(memory, [{"title": "LG C4 OLED TV", "price": 999.0}]) == 0
    assert df.check(memory, [{"title": "LG 65\" C5 OLED TV", "price": 1199.0}]) == 1


def test_learner_finds_rhythms_and_reasons_and_they_merge(tmp_path):
    from buddy.sandbox_jobs import price_patterns
    folder = tmp_path / "input"
    folder.mkdir()
    rows = ["seen_on,store,kind,domain,model,price,regular_price,reason"]
    for i, day in enumerate(["2026-08-02", "2026-08-23", "2026-09-13", "2026-10-04"]):
        rows.append(f"{day},Best Buy,electronics,product,sony wh-1000xm6,{320 - i * 5},449,weekly sale")
        rows.append(f"{day},Best Buy,electronics,product,bose qc ultra,{349 - i * 5},429,price drop")
    (folder / "deals.csv.gz").write_bytes(gzip.compress("\n".join(rows).encode()))
    got = price_patterns.main(str(tmp_path))
    rhythm = got["store_rhythm"]["Best Buy|electronics"]
    assert rhythm["every_days"] == 21 and rhythm["weekday"] == "Sunday" and rhythm["deals"] == 8
    assert got["deal_reasons"]["electronics"]["weekly sale"]["deals"] == 4
    assert got["item_deals"]["sony wh-1000xm6"]["dates"][0] == "2026-08-02"
    later = {"item_deals": {"sony wh-1000xm6": {"dates": ["2026-11-27"], "prices": [279.0], "depths": [0.38],
                                                 "stores": ["Amazon"]}}}
    merged = patterns.merge([got, later])
    item = merged["item_deals"]["sony wh-1000xm6"]
    assert len(item["dates"]) == 5 and item["stores"] == ["Amazon", "Best Buy"]
    assert merged["deal_reasons"]["electronics"]["weekly sale"]["share"] == 0.5


def test_weekly_suggests_what_to_watch(memory):
    shopper.considering(memory, "Dyson V15 vacuum", "home")
    shopper.save(memory, "home", {"stores": ["Home Depot"]}, ["where"])
    note = df.weekly(memory, today=date(2026, 11, 14))
    assert note.startswith("📅 Deals to watch for this month") and "Black Friday to Cyber Monday" in note
    assert df.weekly_due(memory, datetime(2026, 11, 16, 10, 5)) and not df.weekly_due(memory, datetime(2026, 11, 17, 10))
