"""Groceries: is this product good for me? One label serving judged by code against FDA's rules and the owner's own
numbers; the products checked are kept and compared. Meals by breakfast, lunch, snack and dinner."""

from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from buddy import coach, foodverdict, healthpage
from buddy.memory import Memory
from buddy.tools import ToolError
from tests.test_dietcheck import _me

BREAD = {"name": "Italian Bread", "brand": "Maier's", "barcode": "071314001530", "kind": "Branded product (Open Food Facts)",
         "per100": {"kcal": 250.0, "protein_g": 9.4, "carbs_g": 50.0, "fat_g": 1.6, "fiber_g": 1.6, "sodium_mg": 470.0,
                    "sat_fat_g": 0.3, "sugar_g": 3.1, "added_sugar_g": 3.1},
         "portions": [{"label": "1 slice (label serving)", "grams": 32}],
         "ingredients": "Enriched wheat flour (flour, niacin, iron), water, sugar, yeast, salt, soybean oil",
         "allergens": ["en:gluten"], "nova": 4, "additives": ["en:e282", "en:e471"]}
OATS = {"name": "Old fashioned rolled oats", "brand": "Brand", "barcode": "030000010204",
        "per100": {"kcal": 375.0, "protein_g": 12.5, "carbs_g": 67.5, "fat_g": 6.3, "fiber_g": 10.0, "sodium_mg": 0.0,
                   "sat_fat_g": 1.3, "added_sugar_g": 0.0, "iron_mg": 4.5, "potassium_mg": 360.0},
        "portions": [{"label": "1/2 cup (label serving)", "grams": 40}], "ingredients": "Whole grain rolled oats",
        "allergens": [], "nova": 1, "additives": []}
CHIPS = {"name": "Sea salt kettle chips", "barcode": "084114032614",
         "per100": {"kcal": 536.0, "protein_g": 7.1, "fat_g": 32.0, "fiber_g": 3.6, "sodium_mg": 900.0,
                    "sat_fat_g": 8.0, "added_sugar_g": 0.0},
         "portions": [{"label": "1 oz (label serving)", "grams": 28}], "ingredients": "Potatoes, palm oil, sea salt",
         "allergens": [], "nova": 3}
EGGS = {"name": "Cage free large brown eggs", "barcode": "079893401508",
        "per100": {"kcal": 143.0, "protein_g": 12.6, "fat_g": 9.5, "sat_fat_g": 3.1, "sodium_mg": 142.0},
        "portions": [{"label": "1 egg (label serving)", "grams": 50}], "ingredients": "Eggs", "allergens": ["en:eggs"]}


def test_oats_are_good_with_the_reasons(tmp_path):
    m = Memory(tmp_path / "b.db")
    got = foodverdict.judge(m, OATS)
    assert got["verdict"] == "good" and got["headline"] == "Good for you"
    assert got["serving"] == {"grams": 40.0, "label": "1/2 cup (40 g)"}
    texts = [b["text"] for b in got["benefits"]]
    assert "High in fibre: 4 g, 14% of a day's value." not in texts
    assert "A good source of fibre: 4 g, 14% of a day's value." in texts
    assert "A good source of iron: 2 mg, 10% of a day's value." in texts
    assert "Low in sodium: 0 mg." in texts and "No added sugar." in texts
    assert "A whole grain comes first in the ingredients." in texts and "Unprocessed or minimally processed." in texts
    assert got["concerns"] == [] and "No AI model" in got["note"]


def test_the_owners_numbers_make_it_stricter(tmp_path):
    m = Memory(tmp_path / "b.db")
    plain = foodverdict.judge(m, BREAD)
    assert plain["verdict"] == "sometimes"  # ultra-processed, nothing else on one slice for someone with no numbers
    assert [c["text"].split(" (")[0] for c in plain["concerns"]] == ["Ultra-processed"]
    _me(m, lab=[("hba1c", 5.9, "%"), ("ldl", 141, "mg/dL")], bp=(136, 86))
    mine = foodverdict.judge(m, BREAD)
    texts = [c["text"] for c in mine["concerns"]]
    assert any(t.startswith("Refined flour or grain comes first: with your A1c of 5.9%") for t in texts)
    assert "Ultra-processed (NOVA group 4), with 2 additives (E282, E471)." in texts
    assert mine["verdict"] == "sometimes" and mine["used"][0].startswith("your LDL of 141")
    chips = foodverdict.judge(m, CHIPS)  # 252 mg sodium (11%) and 2.2 g saturated fat (11%): both count for this owner
    assert chips["verdict"] == "skip" and {c["yours"] for c in chips["concerns"] if c["serious"]} == {True}
    assert any("with your blood pressure averaging 136/86" in c["text"] for c in chips["concerns"])
    assert any("with your LDL of 141 mg/dL" in c["text"] for c in chips["concerns"])
    assert foodverdict.judge(Memory(tmp_path / "c.db"), CHIPS)["verdict"] == "good"  # nobody's numbers: not high


def test_allergies_menu_and_goal(tmp_path):
    m = Memory(tmp_path / "b.db")
    _me(m, plan_diet={"allergies": "eggs"})
    got = foodverdict.judge(m, EGGS)
    assert got["verdict"] == "skip" and got["headline"] == "Better to skip: eggs is on your allergy list."
    assert "Has egg" in got["concerns"][0]["text"]
    with pytest.raises(ToolError):
        foodverdict.judge(m, {"name": "Mystery", "per100": {}})
    with pytest.raises(ToolError):
        foodverdict.judge(m, {"name": "", "per100": {"kcal": 1}})


def test_kept_compared_and_deleted(tmp_path, monkeypatch):
    from buddy import server
    m = Memory(tmp_path / "b.db")
    monkeypatch.setattr(server, "memory", m)
    monkeypatch.setattr(server, "authorized", lambda t: t == "s3cret")
    client, auth = TestClient(server.app), {"Authorization": "Bearer s3cret"}
    assert client.post("/api/health/verdict", json={"food": BREAD, "keep": True}, headers=auth).json()["id"]
    client.post("/api/health/verdict", json={"food": BREAD, "keep": True}, headers=auth)  # scanned again: one row
    oats = client.post("/api/health/verdict", json={"food": OATS, "keep": True}, headers=auth).json()
    assert client.post("/api/health/verdict", json={"food": CHIPS}, headers=auth).json()["verdict"] == "good"
    scans = client.get("/api/health/scans", headers=auth).json()["scans"]
    assert [s["name"] for s in scans] == ["Old fashioned rolled oats", "Italian Bread"]  # not kept without keep
    cmp = client.post("/api/health/scans", json={"action": "compare", "ids": [s["id"] for s in scans]}, headers=auth).json()
    assert cmp["better"] == oats["id"] and cmp["products"][0]["name"] == "Old fashioned rolled oats"
    assert client.post("/api/health/scans", json={"action": "compare", "ids": [oats["id"]]}, headers=auth).status_code == 400
    left = client.post("/api/health/scans", json={"action": "delete", "id": oats["id"]}, headers=auth).json()
    assert left["deleted"] == 1 and [s["name"] for s in left["scans"]] == ["Italian Bread"]
    assert client.post("/api/health/scans", json={"action": "forget"}, headers=auth).json()["scans"] == []
    assert client.post("/api/health/verdict", json={"food": {"name": "x"}}, headers=auth).status_code == 400


def test_meals_by_slot_moved_and_missing_in_the_evening(tmp_path):
    m = Memory(tmp_path / "b.db")
    day = datetime(2026, 11, 26)
    assert [coach.slot_for(day.replace(hour=h, minute=mi)) for h, mi in
            ((7, 0), (11, 0), (14, 59), (15, 0), (17, 29), (17, 30), (23, 0), (1, 0))] == \
        ["breakfast", "lunch", "lunch", "snack", "snack", "dinner", "dinner", "snack"]
    coach.add_meal(m, "Eggs and toast", 380, 22, now=day.replace(hour=8))
    coach.add_meal(m, "Apple", 95, 0, now=day.replace(hour=10, minute=30), slot="snack")
    got = coach.eaten(m, day.date())
    assert [(x["what"], x["slot"]) for x in got["meals"]] == [("Eggs and toast", "breakfast"), ("Apple", "snack")]
    assert got["by_slot"]["breakfast"] == {"calories": 380, "protein_g": 22, "meals": 1}
    assert coach.missing_line(m, day.replace(hour=21, minute=30)) == \
        "Not noted today: lunch and dinner. Note it on Health → Plan → Food when you can."
    healthpage.act(m, {"action": "meal_slot", "id": got["meals"][1]["id"], "slot": "lunch"})
    assert coach.missing_line(m, day.replace(hour=21, minute=30)).startswith("Not noted today: dinner.")
    assert coach.missing_line(m, day.replace(hour=9)) == ""  # nothing due yet
    with pytest.raises(ToolError):
        healthpage.act(m, {"action": "meal_slot", "id": 999, "slot": "lunch"})
    with pytest.raises(ToolError):
        coach.set_slot(m, 1, "brunch")
    assert coach.missing_line(Memory(tmp_path / "c.db"), day.replace(hour=22)) == ""  # never notes meals: no nagging
    healthpage.act(m, {"action": "meal", "what": "Rice and fish curry", "calories": 600, "slot": "dinner"})
    assert any(r["slot"] == "dinner" for r in coach.eaten(m, coach.local_now().date())["meals"])
