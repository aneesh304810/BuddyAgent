"""Deal notifications from shopping and travel apps on the phone, read into the price book (no network)."""

import json
from datetime import date, datetime, timezone

import pytest

from buddy import dealforecast, dealnotif, pricebook, shopper, whentobuy
from buddy.connectors.hub import ConnectorHub
from buddy.memory import Memory


@pytest.fixture
def memory(tmp_path):
    m = Memory(tmp_path / "buddy.db")
    ConnectorHub(m)
    yield m
    pricebook.close_all()


def notify(m, app, title, text):
    now = datetime.now(timezone.utc).isoformat()
    m.db.execute("INSERT INTO records (source, external_id, kind, title, body, start_at, data, synced_at) "
                 "VALUES ('notifications', ?, 'notification', ?, ?, ?, ?, ?)",
                 (f"{app}|{title}|{text}", f"{app}: {title}", f"[Fold 8] {text}", now,
                  json.dumps({"app_name": app}), now))
    m.db.commit()


def test_only_shopping_and_travel_apps_and_only_deals(memory):
    notify(memory, "Amazon Shopping", "Price drop", 'The price of "Sony WH-1000XM6 Headphones" dropped to $289.99, was $399.99')
    notify(memory, "Home Depot", "DEWALT 20V MAX Drill Kit now $99", "Save $60. Limited-time deal.")
    notify(memory, "Hopper", "Price drop: JFK to LIM", "Fares from $372 round trip. Book now.")
    notify(memory, "Booking.com", "Deal in Lima", "Hotels in Lima from $89 per night this weekend")
    notify(memory, "Amazon Shopping", "Delivered", "Your package with Sony headphones $289.99 was delivered")
    notify(memory, "WhatsApp", "Sam", "that TV deal is $499 at Costco, price drop!")
    notify(memory, "Chase", "Payment", "You paid $289.99, a great deal")
    dealforecast.watch(memory, "DEWALT 20V MAX Drill Kit", "home", target=110)
    assert dealnotif.scan(memory) == 4
    assert dealnotif.scan(memory) == 0
    page = pricebook.deals_page(memory)
    assert page["counts"] == {"product": 2, "flight": 1, "hotel": 1}
    sony = next(d for d in page["deals"] if "Sony" in d["title"])
    assert sony["price"] == 289.99 and sony["regular_price"] == 399.99 and sony["source"] == "app: Amazon Shopping"
    assert pricebook.lowest(memory, "jfk-lim", "flight") == 372 and pricebook.lowest(memory, "hotels:lima", "hotel") == 89
    told = dealforecast.take_pending(memory)
    assert len(told) == 1 and "DEWALT 20V MAX Drill Kit" in told[0][0] and "$99.00" in told[0][0]


def test_construction_kinds_stores_and_sales():
    assert shopper.kind_of("Milwaukee M18 impact driver") == "home"
    assert shopper.kind_of("", "Harbor Freight") == "home" and shopper.kind_of("", "Menards") == "home"
    sales = [s["name"] for s in whentobuy.next_sales("DEWALT table saw", "home", date(2027, 3, 20), 120)]
    assert sales[0].startswith("Spring Black Friday") and "Father's Day tool deals" in sales
