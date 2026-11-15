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


def test_company_playbook_and_upfront_predictions(tmp_path, memory):
    from buddy.sandbox_jobs import price_patterns
    folder = tmp_path / "input"
    folder.mkdir()
    rows = ["seen_on,store,kind,domain,model,price,regular_price,reason"]
    for i, day in enumerate(["2026-09-06", "2026-09-27", "2026-10-18", "2026-11-08"]):
        rows.append(f"{day},Best Buy,electronics,product,sony wh-1000xm6,{320 - i * 5},449,weekly sale")
        rows.append(f"{day},Best Buy,electronics,product,sony wf-1000xm5,{199 - i * 5},299,coupon")
    (folder / "deals.csv.gz").write_bytes(gzip.compress("\n".join(rows).encode()))
    patterns.keep(memory, price_patterns.main(str(tmp_path)))
    book = df.company(memory, "best buy", today=date(2026, 11, 14))["playbooks"][0]
    assert book["as"] == "store" and book["runs_deals_every_days"] == 21 and book["usual_weekday"] == "Sunday"
    assert book["why"] == {"weekly sale": 0.5, "coupon": 0.5} and book["next_deal_day_likely"] == "2026-11-29"
    assert df.company(memory, "Sony", today=date(2026, 11, 14))["playbooks"][0]["as"] == "brand"
    assert "No pattern learned" in df.company(memory, "Dyson")["note"]
    preds = df.predictions(memory, 45, today=date(2026, 11, 14))
    kinds = {p["type"] for p in preds}
    assert kinds == {"product", "company", "sale"}
    sony = next(p for p in preds if p["what"] == "sony wh-1000xm6")
    assert sony["when"] == "2026-11-29" and sony["expect_price"] == 305.0
    assert [p["when"] for p in preds] == sorted(p["when"] for p in preds)


def test_flights_and_hotels_are_predicted_too(memory):
    from datetime import timedelta
    today = date(2026, 11, 16)
    pricebook.deals_page(memory)  # the tables exist
    for day, price in (("2026-10-05", 420), ("2026-10-19", 398), ("2026-11-02", 410)):
        pricebook.run(memory, "INSERT INTO deals (seen_on, source, title, url, price, store, domain, item) VALUES "
                              "(?, 'inbox', 'JFK to LIM', ?, ?, 'Delta', 'flight', 'jfk-lim')",
                      (day, f"https://delta.example/{day}", price))
    pricebook.run(memory, "INSERT INTO deals (seen_on, source, title, url, price, store, domain, item) VALUES "
                          "('2026-11-10', 'inbox', 'Nairobi stay', 'https://agoda.example/1', 90, 'Agoda', 'hotel', "
                          "'hotels:nairobi')")
    learned = {"flights": {"ewr-nbo": {"cheapest_window": "61-120", "median": 900.0, "prices": 80,
                                       "by_days_before": {"8-21": 1.2, "61-120": 0.9}}},
               "hotels": {"hotels:nairobi": {"by_month": {"5": 0.8, "12": 1.3}, "weekend_premium": 0.12,
                                             "median": 150.0, "prices": 30}}}
    got = {p["what"]: p for p in df.travel(memory, learned, today)}
    fare = got["Flight JFK to LIM"]
    assert fare["when"] == "2026-11-16" and fare["expect_price"] == 398 and fare["type"] == "flight"
    assert "on deal 3 times" in fare["why"] and "about every 14 days" in fare["why"]
    assert "Hotels in Nairobi" in got and "Flight JFK to LIM" in got  # one deal alone predicts nothing by itself
    route = got["Flight EWR to NBO (any date)"]
    assert route["expect_price"] == 810 and "2 to 4 months before departure" in route["why"]
    assert "10% under" in route["why"] and route["confidence"] == "medium"
    stay = got["Hotels in Nairobi"]
    assert stay["when"] == "2027-05-01" and stay["expect_price"] == 120
    assert "cheapest month is May" in stay["why"] and "Friday and Saturday nights cost about 12% more" in stay["why"]
    pricebook.run(memory, "ALTER TABLE predictions ADD COLUMN IF NOT EXISTS travel_date DATE")
    pricebook.run(memory, "INSERT INTO predictions (made_on, domain, item, price, decision, chance, target, by_date, "
                          "travel_date) VALUES ('2026-11-10', 'flight', 'ewr-nbo', 950, 'wait', 0.7, 860, "
                          "'2026-12-01', '2027-02-10')")
    call = next(p for p in df.travel(memory, {}, today) if "travelling" in p["what"])
    assert call["what"] == "Flight EWR to NBO, travelling Feb 10" and call["when"] == "2026-12-01"
    assert call["expect_price"] == 860 and "my call: wait" in call["why"] and "70%" in call["why"]
    assert any(p["type"] == "flight" for p in df.predictions(memory, today=today))  # they reach the tab
    assert df.travel(memory, {}, today + timedelta(days=400)) == []  # old deals and expired calls say nothing
