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
