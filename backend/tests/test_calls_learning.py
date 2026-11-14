"""Shopping and travel calls: reasons kept with each call, outcomes followed, reasons re-weighed, travel timing."""

import json
from datetime import date, datetime, timedelta, timezone

import pytest

from buddy import patterns, pricebook, travelcall, whentobuy
from buddy.connectors.hub import ConnectorHub
from buddy.memory import Memory

DAY = date(2026, 3, 10)


@pytest.fixture
def memory(tmp_path):
    m = Memory(tmp_path / "buddy.db")
    ConnectorHub(m)
    patterns._cache.clear()
    yield m
    pricebook.close_all()
    patterns._cache.clear()


def call(memory, parts, fell, domain="product", n=1):
    """n judged calls made on these reasons, where the price then did or didn't come down."""
    whentobuy._ready(memory)
    for _ in range(n):
        pricebook.run(memory, "INSERT INTO predictions (domain, item, kind, made_on, price, decision, chance, target, "
                              "event, by_date, parts, outcome, best_seen) VALUES (?, 'x', 'other', '2026-01-01', 100, "
                              "'wait', 0.5, 90, '', '2026-02-01', ?, ?, ?)",
                      (domain, json.dumps(parts), "right" if fell else "wrong", 85.0 if fell else 99.0))


def test_a_call_keeps_the_reasons_it_was_made_on(memory):
    pricebook.observe(memory, "product", "Sony WH-1000XM6", 299.0, seen_at=datetime(2026, 1, 5, tzinfo=timezone.utc))
    got = whentobuy.advise(memory, "Sony WH-1000XM6", 399.0, "electronics", today=DAY)
    row = pricebook.run(memory, "SELECT parts FROM predictions WHERE id = ?", (got["prediction_id"],))[0]
    assert json.loads(row["parts"])["was_lower"] == 0.15


def test_reasons_are_re_weighed_only_with_enough_judged_calls(memory):
    call(memory, {"sale_ahead": 0.45}, fell=True, n=10)
    assert whentobuy.learn(memory)["sale_ahead"] == {"weight": 1.0, "calls": 10, "right": 10,
                                                      "note": "needs 15 judged calls"}
    call(memory, {"sale_ahead": 0.45}, fell=True, n=8)            # waiting for the sale kept paying off
    call(memory, {"near_lowest": -0.2}, fell=True, n=16)          # "it's at its low" kept being wrong
    call(memory, {"under_usual": -0.15}, fell=False, n=15)        # "already cheap" held: the price didn't fall
    got = whentobuy.learn(memory)
    assert got["sale_ahead"]["weight"] == 1.5 and got["near_lowest"]["weight"] == 0.5
    assert got["under_usual"] == {"weight": 1.5, "calls": 15, "right": 15}
    assert whentobuy.weights(memory)["near_lowest"]["weight"] == 0.5 and whentobuy.weights(memory, "flight") == {}


def test_a_learned_weight_changes_the_next_call(memory):
    pricebook.observe(memory, "product", "Sony WH-1000XM6", 399.0, seen_at=datetime(2026, 1, 5, tzinfo=timezone.utc))
    soon = date(2026, 11, 10)   # Black Friday ahead, so the chance isn't sitting on its floor
    before = whentobuy.advise(memory, "Sony WH-1000XM6", 399.0, "electronics", today=soon, save=False)
    call(memory, {"near_lowest": -0.2}, fell=True, n=16)
    whentobuy.learn(memory)
    after = whentobuy.advise(memory, "Sony WH-1000XM6", 399.0, "electronics", today=soon, save=False)
    assert round(after["chance_of_better_price"] - before["chance_of_better_price"], 2) == 0.1   # -0.2 now counts half


def test_open_calls_are_followed_so_they_get_an_outcome(memory):
    whentobuy.advise(memory, "Sony WH-1000XM6 headphones", 399.0, "electronics", today=local_today())
    seen = []

    def fetch(url, data=None, headers=None):
        seen.append(json.loads(data)["q"])
        return json.dumps({"shopping": [{"title": "Sony WH-1000XM6 Wireless Headphones", "price": "$329.00",
                                          "source": "Best Buy", "link": "https://example.com/x"},
                                         {"title": "Case for headphones", "price": "$9.99", "source": "X", "link": ""}]})

    assert whentobuy.follow_open(memory, {"BUDDY_SERPER_KEY": "k"}, fetch) == 1
    assert seen == ["Sony WH-1000XM6 headphones"]
    assert pricebook.lowest(memory, "Sony WH-1000XM6 headphones") == 329.0     # the case isn't the product
    assert whentobuy.follow_open(memory, {}, fetch) == 0                        # no key: nothing checked


def local_today():
    from buddy.tools import local_now
    return local_now().date()


def test_travel_timing_reasons_and_the_hotel_advice(memory):
    late = travelcall.advise(memory, "flight", "EWR-LIM", 620.0, "2026-03-20", DAY)
    assert late["decision"] == "book now" and "last three weeks" in late["reasons"][0] and "prediction_id" not in late
    far = travelcall.advise(memory, "flight", "EWR-LIM", 620.0, "2026-09-10", DAY)
    assert far["chance_of_a_lower_price"] == 0.4 and far["decision"] == "book now"   # a rule of thumb alone isn't enough
    assert any("no price history" in r for r in far["reasons"])
    for i in range(6):
        pricebook.observe(memory, "flight", "EWR-LIM", 500.0, travel_date="2026-09-10",
                          seen_at=datetime(2026, 2, 1 + i, tzinfo=timezone.utc))
    dear = travelcall.advise(memory, "flight", "EWR-LIM", 620.0, "2026-09-10", DAY)
    assert dear["decision"] == "wait" and dear["chance_of_a_lower_price"] == 0.55 and dear["recheck_by"] == "2026-03-31"
    row = pricebook.run(memory, "SELECT domain, parts, travel_date, decision FROM predictions WHERE id = ?",
                        (dear["prediction_id"],))[0]
    assert row["domain"] == "flight" and json.loads(row["parts"]) == {"window": 0.2, "vs_usual": 0.15}
    hotel = travelcall.advise(memory, "hotel", "Hyatt Lima", 180.0, "2026-06-01", DAY)
    assert "free cancellation" in hotel["note"] and hotel["decision"] == "book now"
    with pytest.raises(Exception, match="has passed"):
        travelcall.advise(memory, "flight", "EWR-LIM", 600.0, "2026-01-01", DAY)
    got = json.loads(travelcall.run_tool(memory, {"kind": "flight", "what": "ewr to lim", "price": 620,
                                                  "date": (local_today() + timedelta(days=200)).isoformat()}))
    assert got["what"] == "EWR-LIM"


def test_a_flight_call_is_judged_on_the_same_trip_and_taught_back(memory):
    for i in range(6):
        pricebook.observe(memory, "flight", "EWR-LIM", 500.0, travel_date="2026-09-10",
                          seen_at=datetime(2026, 2, 1 + i, tzinfo=timezone.utc))
    made = travelcall.advise(memory, "flight", "EWR-LIM", 620.0, "2026-09-10", DAY)       # wait
    pricebook.observe(memory, "flight", "EWR-LIM", 300.0, travel_date="2026-12-20",      # another trip: ignored
                      seen_at=datetime(2026, 3, 15, tzinfo=timezone.utc))
    pricebook.observe(memory, "flight", "EWR-LIM", 540.0, travel_date="2026-09-10",
                      seen_at=datetime(2026, 3, 20, tzinfo=timezone.utc))
    assert whentobuy.settle(memory, date(2026, 4, 5)) == 1
    row = pricebook.run(memory, "SELECT outcome, best_seen FROM predictions WHERE id = ?", (made["prediction_id"],))[0]
    assert row == {"outcome": "right", "best_seen": 540.0}                                # 13% lower: waiting paid
    w = whentobuy.weights(memory, "flight")
    assert w["window"]["calls"] == 1 and w["window"]["weight"] == 1.0                     # one call teaches nothing yet


def test_a_hotel_call_is_followed_every_three_days_and_judged_on_that_hotel(memory):
    today = date(2026, 3, 10)
    made = travelcall.advise(memory, "hotel", "Hyatt Centric Lima", 220.0, "2026-06-01", today, place="Lima", nights=3)
    area = travelcall.advise(memory, "hotel", "Miraflores, Lima", 150.0, "2026-06-01", today, place="Miraflores, Lima")
    asked = []

    def search(place, check_in, check_out):
        asked.append((place, check_in, check_out))
        return [{"name": "Hotel Cheap Lima", "per_night": 90.0}, {"name": "Hyatt Centric San Isidro Lima",
                                                                  "per_night": 185.0}, {"name": "No rate"}]

    assert whentobuy.follow_hotels(memory, {}, search, today) == 2
    assert asked[0] == ("Lima", "2026-06-01", "2026-06-04") and asked[1][2] == "2026-06-02"
    assert pricebook.lowest(memory, "Hyatt Centric Lima", "hotel") == 185.0       # that hotel, not the cheapest in town
    assert pricebook.lowest(memory, "Miraflores, Lima", "hotel") == 90.0          # a call about the area: its cheapest
    assert whentobuy.follow_hotels(memory, {}, search, today + timedelta(days=1)) == 0   # not again for three days
    assert whentobuy.follow_hotels(memory, {}, search, today + timedelta(days=3)) == 2
    assert whentobuy.settle(memory, date(2026, 4, 5)) == 2
    row = pricebook.run(memory, "SELECT outcome, best_seen FROM predictions WHERE id = ?", (made["prediction_id"],))[0]
    assert row == {"outcome": "wrong", "best_seen": 185.0}     # "book now" was the call, and it came down 16%
    assert area["decision"] == "book now" and whentobuy.follow_hotels(memory, {}, None, today) == 0  # no SerpApi key
