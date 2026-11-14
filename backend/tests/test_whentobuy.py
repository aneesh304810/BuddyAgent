"""When to buy: the sales calendar, buy-or-wait, predictions and the track record (no network)."""

import json
from datetime import date, datetime, timezone

import pytest

from buddy import prices, shopper, whentobuy as w
from buddy.connectors.hub import ConnectorHub
from buddy.memory import Memory
from buddy.tools import ToolError


@pytest.fixture
def memory(tmp_path):
    m = Memory(tmp_path / "w.db")
    ConnectorHub(m)
    return m


def test_calendar_dates():
    cal = {e["name"]: e for e in w.calendar(2026)}
    assert cal["Black Friday to Cyber Monday"]["start"] == date(2026, 11, 27)  # Thanksgiving is Nov 26, 2026
    assert cal["Black Friday to Cyber Monday"]["end"] == date(2026, 11, 30)
    assert cal["Labor Day sales"]["end"] == date(2026, 9, 7)
    assert cal["Memorial Day sales"]["end"] == date(2026, 5, 25)
    assert cal["Presidents' Day sales"]["end"] == date(2026, 2, 16)


def test_wait_for_black_friday_when_patient(memory):
    shopper.save(memory, "general", {"patience": "a few weeks for a good deal"})
    got = w.advise(memory, "Sony WH-1000XM6 headphones", 399.0, "electronics", today=date(2026, 11, 14))
    assert got["decision"] == "wait" and got["for"] == "Black Friday to Cyber Monday"
    assert got["wait_until"] == "2026-11-30" and got["price_worth_waiting_for"] < 399
    assert any("starts in 13 days" in r for r in got["reasons"]) and "prediction_id" in got


def test_buy_now_at_the_lowest_with_no_sale_ahead(memory):
    shopper.save(memory, "general", {"patience": "I buy right away"})
    for p in (329.0, 349.0, 299.0, 319.0):
        prices.record(memory, "Sony WH-1000XM6 headphones", p, "USD")
    got = w.advise(memory, "Sony WH-1000XM6 headphones", 299.0, "electronics", today=date(2026, 3, 10))
    assert got["decision"] == "buy_now" and any("lowest price seen" in r for r in got["reasons"])


def test_new_model_and_target_discount(memory):
    shopper.save(memory, "electronics", {"target_discount_pct": 20, "patience": "2 months"})
    got = w.advise(memory, "Pixel 10 phone", 799.0, "electronics", new_model="2027-01-20",
                   today=date(2026, 12, 15))
    assert got["price_worth_waiting_for"] == round(799 * 0.8, 2)
    assert any("new model" in r for r in got["reasons"])


def test_flights_go_to_travel_tools(memory):
    with pytest.raises(ToolError, match="travel tools"):
        w.advise(memory, "flight to Lima", 450.0)


def test_predictions_settle_from_prices_and_order_emails(memory):
    a = w.advise(memory, "Dyson V15 vacuum", 649.0, "home", today=date(2026, 11, 14))
    b = w.advise(memory, "Kindle Paperwhite", 159.0, "electronics", average=165.0, lowest=150.0,
                 today=date(2026, 11, 14))
    c = w.advise(memory, "Ninja air fryer", 129.0, "home", today=date(2026, 11, 14))
    prices.record(memory, "Dyson V15 vacuum", 499.0, "USD", seen_at=datetime(2026, 11, 28, tzinfo=timezone.utc))
    memory.db.execute("INSERT INTO shop_purchases (record_id, day, merchant, item, amount, kind, on_sale, event) "
                      "VALUES (1, '2026-11-20', 'Amazon', 'Kindle Paperwhite 16GB', 155.0, 'electronics', 0, 'order')"
                      ) if shopper._ready(memory) is None else None
    memory.db.commit()
    assert w.settle(memory, today=date(2027, 1, 5)) == 3
    from buddy import pricebook
    rows = {r["id"]: dict(r) for r in pricebook.run(memory, "SELECT * FROM predictions")}
    assert rows[a["prediction_id"]]["outcome"] == ("right" if a["decision"] == "wait" else "wrong")
    assert rows[b["prediction_id"]]["best_seen"] == 155.0
    assert rows[c["prediction_id"]]["outcome"] == "unknown"
    rec = json.loads(w.run_tool(memory, {"action": "track_record"}))
    assert rec["calls"] == 3 and rec["no_data"] == 1 and "of 2" in rec["right_overall"]


def test_calendar_action_lists_sales():
    out = json.loads(w.run_tool(Memory(":memory:"), {"action": "calendar", "kind": "home", "what": "washer"}))
    assert out and all("usual_discount" in s for s in out)


def test_amazon_items_get_price_and_keepa_average(memory, monkeypatch):
    from buddy import amazon
    monkeypatch.setattr(amazon, "lookup", lambda m, what, env=None, fetch=None: {
        "found": True, "price": 278.0, "where": "Amazon (Keepa)", "avg90": 310.0, "avg365": 330.0})
    got = json.loads(w.run_tool(memory, {"action": "advise", "what": "https://www.amazon.com/dp/B09XS7JWHH",
                                         "kind": "electronics"}))
    assert got["price_now"] == 278.0 and got["price_from"] == "Amazon (Keepa)"
    assert any("well under its usual $330.00" in r for r in got["reasons"])


def test_life_cycle_new_launch_holds_and_replaced_model_drops(memory):
    new = w.advise(memory, "Pixel 11 Pro phone", 999.0, "electronics", launch_date="2026-10-20",
                   launch_price=999.0, today=date(2026, 11, 14), save=False)
    assert new["life_stage"] == "just launched" and any("launched only 25 days ago" in r for r in new["reasons"])
    assert new["expect_around"] is None or new["expect_around"] >= 999 * 0.85  # the first discount is small
    old = w.advise(memory, "Pixel 10 Pro phone", 799.0, "electronics", launch_date="2025-08-20", launch_price=999.0,
                   successor_date="2026-10-20", today=date(2026, 11, 14), save=False)
    assert old["life_stage"] == "replaced" and old["decision"] == "wait"
    assert any("successor is already out" in r for r in old["reasons"])
    assert any("20% under its launch price" in r for r in old["reasons"])


def test_note_launch_is_kept_and_used(memory):
    json.loads(w.run_tool(memory, {"action": "note_launch", "what": "Sony WH-1000XM6", "launch_date": "2025-05-15",
                                   "launch_price": 449.0, "kind": "electronics", "brand": "Sony"}))
    got = w.advise(memory, "Sony WH-1000XM6", 449.0, "electronics", today=date(2026, 11, 14), save=False)
    assert got["life_stage"] == "mature"
