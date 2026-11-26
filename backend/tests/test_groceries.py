"""Groceries: the shopping list ticked off by scans, My kitchen with "Ate it" and Finished, a warning before a product
that's better to skip, and kitchen foods in the next-meal hint. Code only; no AI model sees the kitchen."""

from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from buddy import coach, foodverdict, groceries, scanmeal
from buddy.memory import Memory
from buddy.organiser import Organiser
from buddy.tools import ToolError
from tests.test_dietcheck import _me
from tests.test_foodverdict import BREAD, CHIPS, OATS

LUNCH = datetime(2026, 11, 26, 12, 30)
YOGURT = {"name": "Total 0% Greek Yogurt", "brand": "Fage", "barcode": "689544080015",
          "per100": {"kcal": 59.0, "protein_g": 10.3, "fat_g": 0.0, "sat_fat_g": 0.0, "sodium_mg": 36.0,
                     "added_sugar_g": 0.0},
          "portions": [{"label": "3/4 cup (label serving)", "grams": 170}], "ingredients": "Milk, cultures",
          "allergens": ["en:milk"], "nova": 1}
PEANUT_BREAD = BREAD | {"name": "Peanut butter bread", "barcode": "000111", "ingredients": "Wheat flour, peanuts"}


def _owner(tmp_path):
    m = Memory(tmp_path / "b.db")
    _me(m, plan_diet={"calories": 2000, "allergies": "peanuts"})
    return m


def test_a_scan_ticks_off_the_list_and_goes_into_the_kitchen(tmp_path):
    m = _owner(tmp_path)
    groceries.add_buy(m, "Greek yogurt")
    groceries.add_buy(m, "oats")
    assert [t["text"] for t in groceries.to_buy(m)] == ["Greek yogurt", "oats"]
    assert [t["text"] for t in Organiser(m).open("shopping")] == ["Greek yogurt", "oats"]  # the same list Buddy fills
    scan = foodverdict.keep(m, YOGURT, "good")
    got = groceries.keep(m, scan)
    assert got["added"] and got["ticked"] == "Greek yogurt" and not got["already"]
    assert [t["text"] for t in got["to_buy"]] == ["oats"]
    k = got["kitchen"][0]
    assert k["name"] == "Total 0% Greek Yogurt" and k["verdict"] == "good" and k["packs"] == 1
    again = groceries.keep(m, foodverdict.keep(m, YOGURT, "good"))  # a second pot
    assert again["already"] and again["kitchen"][0]["packs"] == 2 and len(again["kitchen"]) == 1


def test_a_product_to_skip_waits_and_shows_a_better_one(tmp_path):
    m = _owner(tmp_path)
    good = foodverdict.keep(m, OATS | {"name": "Whole grain bread", "barcode": "222"}, "good")
    bad = foodverdict.keep(m, PEANUT_BREAD, "skip")
    got = groceries.keep(m, bad)
    assert not got["added"] and got["warn"]["headline"].startswith("Better to skip")
    assert "peanuts is on your allergy list" in " ".join(got["warn"]["why"])
    assert got["warn"]["better"]["id"] == good and got["warn"]["better"]["name"] == "Whole grain bread"
    assert groceries.kitchen(m) == []
    assert groceries.keep(m, bad, anyway=True)["added"]


def test_ate_it_notes_a_serving_and_finished_takes_it_off(tmp_path):
    m = _owner(tmp_path)
    item = groceries.keep(m, foodverdict.keep(m, CHIPS, "sometimes"))["kitchen"][0]
    got = groceries.ate(m, item["id"], now=LUNCH)
    assert got["slot"] == "lunch" and got["what"] == "Sea salt kettle chips (1 oz (28 g))"
    assert coach.eaten(m, LUNCH.date())["calories"] == 150
    assert got["kitchen"][0]["eaten"] == 1  # it stays: a bag lasts more than one meal
    foodverdict.forget(m)  # deleting the checked products doesn't empty the kitchen
    assert groceries.ate(m, item["id"], now=LUNCH)["kitchen"][0]["eaten"] == 2
    assert groceries.finish(m, item["id"]) == []
    with pytest.raises(ToolError):
        groceries.finish(m, item["id"])
    with pytest.raises(ToolError):
        groceries.ate(m, item["id"])


def test_bought_by_hand_and_a_home_scan_keeps_the_kitchen_in_step(tmp_path):
    m = _owner(tmp_path)
    task = groceries.add_buy(m, "Bananas")[0]
    got = groceries.bought(m, task["id"])
    assert got["to_buy"] == [] and got["kitchen"][0]["name"] == "Bananas" and "serving" not in got["kitchen"][0]
    ate = groceries.ate(m, got["kitchen"][0]["id"], now=LUNCH)
    assert ate["what"] == "Bananas" and coach.eaten(m, LUNCH.date())["meals"][0]["what"] == "Bananas"
    scan = foodverdict.keep(m, YOGURT, "good")
    groceries.keep(m, scan)
    scanmeal.eat(m, scan, 1, now=LUNCH)
    assert groceries.note_eaten(m, scan) == "Total 0% Greek Yogurt"
    assert next(k for k in groceries.kitchen(m) if k["name"] == "Total 0% Greek Yogurt")["eaten"] == 1
    assert groceries.note_eaten(m, foodverdict.keep(m, OATS, "good")) == ""  # not in the kitchen
    with pytest.raises(ToolError):
        groceries.bought(m, task["id"])
    with pytest.raises(ToolError):
        groceries.add_buy(m, "  ")


def test_kitchen_foods_in_the_next_meal_hint(tmp_path):
    m = _owner(tmp_path)
    groceries.keep(m, foodverdict.keep(m, YOGURT, "good"))
    groceries.keep(m, foodverdict.keep(m, CHIPS, "sometimes"))
    chips = foodverdict.keep(m, CHIPS, "sometimes")
    for _ in range(2):
        scanmeal.eat(m, chips, 4, now=LUNCH)  # 18 g of saturated fat by lunch
    got = scanmeal.ontrack(m, LUNCH)
    assert [k["name"] for k in got["from_kitchen"]] == ["Total 0% Greek Yogurt"]  # the chips don't fit what's left
    assert got["from_kitchen"][0]["numbers"]["sat_fat_g"] == 0


def test_api(tmp_path, monkeypatch):
    from buddy import server
    m = _owner(tmp_path)
    monkeypatch.setattr(server, "memory", m)
    monkeypatch.setattr(server, "authorized", lambda t: t == "s3cret")
    client, auth = TestClient(server.app), {"Authorization": "Bearer s3cret"}
    assert client.post("/api/health/groceries", json={"action": "buy", "text": "greek yogurt"}, headers=auth).json()["to_buy"]
    scan = client.post("/api/health/verdict", json={"food": YOGURT, "keep": True}, headers=auth).json()["id"]
    kept = client.post("/api/health/groceries", json={"action": "keep", "scan": scan}, headers=auth).json()
    assert kept["ticked"] == "greek yogurt"
    lists = client.get("/api/health/groceries", headers=auth).json()
    assert lists["to_buy"] == [] and lists["kitchen"][0]["name"] == "Total 0% Greek Yogurt" and "No AI model" in lists["note"]
    item = lists["kitchen"][0]["id"]
    assert client.post("/api/health/groceries", json={"action": "ate", "id": item}, headers=auth).json()["slot"]
    eaten = client.post("/api/health/scans", json={"action": "eat", "id": scan}, headers=auth).json()
    assert eaten["from_kitchen"] == "Total 0% Greek Yogurt"
    assert client.post("/api/health/groceries", json={"action": "finished", "id": item}, headers=auth).json()["kitchen"] == []
    assert client.post("/api/health/groceries", json={"action": "finished"}, headers=auth).status_code == 400
    assert client.get("/api/health/groceries").status_code in (401, 403)


def test_who_it_is_for(tmp_path):
    m = _owner(tmp_path)
    assert groceries.set_people(m, [" Priya ", "Ishaan", "priya", "me", ""]) == ["Priya", "Ishaan"]
    groceries.add_buy(m, "Greek yogurt", "ishaan")
    assert groceries.to_buy(m)[0]["who"] == "Ishaan"
    got = groceries.keep(m, foodverdict.keep(m, YOGURT, "good"))  # the list item says whom it's for
    assert got["who"] == "Ishaan" and got["kitchen"][0]["who"] == "Ishaan" and got["item_id"]
    assert groceries.set_who(m, got["item_id"], "everyone")[0]["who"] == ""
    assert groceries.keep(m, foodverdict.keep(m, OATS, "good"), who="me")["who"] == "me"
    with pytest.raises(ToolError):
        groceries.set_who(m, got["item_id"], "Bob")


def test_dates_use_soon_today_strip_and_check_in(tmp_path, monkeypatch):
    from buddy import checkin, today
    m = _owner(tmp_path)
    groceries.set_people(m, ["Ishaan"])
    item = groceries.keep(m, foodverdict.keep(m, YOGURT, "good"), who="Ishaan")["item_id"]
    later = groceries.keep(m, foodverdict.keep(m, OATS, "good"))["item_id"]
    groceries.set_expires(m, later, days=30)
    assert groceries.use_soon(m) == [] and groceries.soon_line(m) == ""
    groceries.set_expires(m, item, days=1, how="read")
    soon = groceries.use_soon(m)
    assert [k["name"] for k in soon] == ["Total 0% Greek Yogurt"] and soon[0]["when"] == "use by tomorrow"
    assert soon[0]["expires_how"] == "read"
    assert groceries.soon_line(m) == "From your kitchen, Total 0% Greek Yogurt (Ishaan's): use by tomorrow."
    assert today.strip(m)["use"] == {"title": "Total 0% Greek Yogurt (Ishaan's)", "when": "use by tomorrow",
                                     "source": "My kitchen"}
    assert "Total 0% Greek Yogurt (Ishaan's): use by tomorrow" in checkin.readback(m, {})
    groceries.set_expires(m, item, days=-3)
    assert groceries.use_soon(m)[0]["when"] == "expired 3 days ago"
    for bad in ("tomorrow", "1999-01-01"):
        with pytest.raises(ToolError):
            groceries.set_expires(m, item, bad)


def test_a_kitchen_from_before_gets_the_new_columns(tmp_path):
    m = Memory(tmp_path / "b.db")
    m.db.executescript("CREATE TABLE kitchen (id INTEGER PRIMARY KEY, scan_id INTEGER NOT NULL DEFAULT 0, name TEXT NOT NULL, "
                       "brand TEXT NOT NULL DEFAULT '', barcode TEXT NOT NULL DEFAULT '', food TEXT NOT NULL DEFAULT '{}', "
                       "packs INTEGER NOT NULL DEFAULT 1, eaten INTEGER NOT NULL DEFAULT 0, added_at TEXT NOT NULL, "
                       "last_eaten TEXT); INSERT INTO kitchen (name, added_at) VALUES ('Bananas', '2026-11-26T10:00:00');")
    got = groceries.kitchen(m)
    assert got[0]["name"] == "Bananas" and got[0]["who"] == "" and got[0]["expires"] is None


def test_spending_by_store_and_receipts(tmp_path):
    from buddy import shopper
    from buddy.money import Money
    m = _owner(tmp_path)
    Money(m)
    for i, (day, merchant, amount, cat) in enumerate([("2026-11-02", "ShopRite", 84.10, "groceries"),
                                                      ("2026-11-20", "ShopRite", 40.00, "groceries"),
                                                      ("2026-11-12", "ACME", 22.5, "groceries"),
                                                      ("2026-10-30", "Walmart", 61.0, "groceries"),
                                                      ("2026-11-03", "Shell", 50.0, "transport")]):
        m.db.execute("INSERT INTO transactions (day, amount, currency, merchant, category, source, created_at) "
                     "VALUES (?, ?, 'USD', ?, ?, ?, '2026-11-26')", (day, amount, merchant, cat, f"manual:{i}"))
    shopper._ready(m)
    m.db.execute("INSERT INTO shop_purchases (record_id, day, merchant, item, amount, kind) VALUES "
                 "(1, '2026-11-10', 'Walmart.com', 'Great Value whole milk', 3.48, 'other'), "
                 "(2, '2026-11-11', 'Best Buy', 'Headphones', 99, 'electronics')")
    m.db.commit()
    got = groceries.spend(m, now=LUNCH)
    nov = got["months"][0]
    assert nov["month"] == "2026-11" and nov["total"] == 146.6
    assert nov["stores"][0] == {"store": "ShopRite", "total": 124.1, "visits": 2}
    assert got["months"][1] == {"month": "2026-10", "total": 61.0, "stores": [{"store": "Walmart", "total": 61.0, "visits": 1}]}
    assert [r["item"] for r in got["receipts"]] == ["Great Value whole milk"]


def test_lower_prices_from_store_emails(tmp_path):
    from buddy import pricebook
    m = _owner(tmp_path)
    pricebook.observe(m, "product", "Fage Total Greek Yogurt 32 oz", 5.99, seller="ShopRite", regular_price=7.49,
                      url="https://www.shoprite.example/offer/1", source="deals inbox")
    pricebook.observe(m, "product", "Fage Total Greek Yogurt 32 oz", 6.49, seller="ACME", source="deals inbox")
    pricebook.observe(m, "product", "Chobani Oat Milk", 3.99, seller="Walmart", source="deals inbox")
    got = groceries.deals_for(m, "Greek yogurt")
    assert [(d["store"], d["price"]) for d in got] == [("ShopRite", 5.99), ("ACME", 6.49)]
    assert got[0]["link"] == "https://www.shoprite.example/offer/1" and got[0]["regular"] == 7.49
    assert [d["store"] for d in groceries.deals_for(m, "Total 0% Greek Yogurt", "Fage")] == ["ShopRite", "ACME"]
    assert groceries.deals_for(m, "Bananas") == []
    groceries.add_buy(m, "greek yogurt")
    assert groceries.lists(m)["to_buy"][0]["deals"][0]["store"] == "ShopRite"


def test_api_v2(tmp_path, monkeypatch):
    from buddy import pricebook, server
    m = _owner(tmp_path)
    monkeypatch.setattr(server, "memory", m)
    monkeypatch.setattr(server, "authorized", lambda t: t == "s3cret")
    client, auth = TestClient(server.app), {"Authorization": "Bearer s3cret"}
    post = lambda body: client.post("/api/health/groceries", json=body, headers=auth)  # noqa: E731
    assert post({"action": "people", "names": ["Priya", "Ishaan"]}).json()["people"] == ["Priya", "Ishaan"]
    assert post({"action": "buy", "text": "greek yogurt", "who": "Priya"}).json()["to_buy"][0]["who"] == "Priya"
    scan = client.post("/api/health/verdict", json={"food": YOGURT, "keep": True}, headers=auth).json()["id"]
    kept = post({"action": "keep", "scan": scan}).json()
    assert kept["who"] == "Priya"
    assert post({"action": "expires", "id": kept["item_id"], "days": 1}).json()["kitchen"][0]["expires"]
    assert post({"action": "who", "id": kept["item_id"], "who": "Ishaan"}).json()["kitchen"][0]["who"] == "Ishaan"
    assert post({"action": "expires", "id": kept["item_id"], "date": "soon"}).status_code == 400
    got = client.get("/api/health/groceries", headers=auth).json()
    assert got["people"] == ["Priya", "Ishaan"] and got["use_soon"][0]["who"] == "Ishaan" and "months" in got["spend"]
    pricebook.observe(m, "product", "Fage Total Greek Yogurt", 5.49, seller="Walmart", source="deals inbox")
    assert client.get(f"/api/health/groceries/deals?scan={scan}", headers=auth).json()["deals"][0]["store"] == "Walmart"
    assert client.get("/api/health/groceries/deals?q=greek+yogurt", headers=auth).json()["deals"]
