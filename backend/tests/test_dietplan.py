"""The daily diet: targets worked out by code from the owner's own numbers and the 2025-2030 guidelines, and a day's
menu from the cuisines they eat, with USDA's numbers."""

from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from buddy import coach, dietfoods, dietplan, healthrecords, labs, prevention
from buddy.memory import Memory
from buddy.tools import ToolError, local_now

TODAY = local_now().date()


def _me(m, steps=8200, a1c=None, **answers):
    for d in range(10):
        healthrecords.record(m, "health_connect", (TODAY - timedelta(days=d)).isoformat(),
                             {"weight_kg": 70.5, "steps": steps})
    prevention.save_profile(m, {"birth_year": TODAY.year - 45, "sex": "male", "ancestry": ["indian_kerala"],
                                "height_cm": 170} | answers)
    if a1c is not None:
        labs._ready(m)
        m.db.execute("INSERT INTO lab_results (report_id, source, test, printed, value, value_text, unit, band, "
                     "collected) VALUES (1, 'lab_upload', 'hba1c', 'A1c', ?, ?, '%', 'within', '2026-03-02')",
                     (a1c, str(a1c)))
        m.db.commit()


def test_every_dish_has_usdas_numbers_and_a_portion():
    for key, d in dietfoods.DISHES.items():
        assert d["fdc_id"] > 2_700_000 and d["grams"] > 0 and d["meals"], key
        assert d["diet"] in ("veg", *dietplan.EATS), key
        assert {"kcal", "protein_g", "carbs_g", "fat_g", "fiber_g", "sodium_mg"} <= set(d["per100"]), key
    for _, cuisine, meal, items in dietplan.TEMPLATES:
        assert cuisine in dietplan.CUISINES and meal in "blds"
        assert all(dish in dietfoods.DISHES for dish, _, _ in items)


def test_targets_from_weight_height_age_sex_and_steps(tmp_path):
    m = Memory(tmp_path / "b.db")
    assert dietplan.targets(m)["calories"]["value"] is None  # nothing known yet
    assert "your height (Labs → Checkups)" in dietplan.targets(m)["missing"]
    _me(m)
    t = dietplan.targets(m)
    resting = 10 * 70.5 + 6.25 * 170 - 5 * 45 + 5  # Mifflin-St Jeor, a man
    assert t["calories"]["value"] == round(resting * 1.55 / 10) * 10 and "moderately active" in t["calories"]["how"]
    assert (t["protein_g"]["low"], t["protein_g"]["high"]) == (85, 113)  # 1.2 to 1.6 g per kg
    assert t["fiber_g"]["value"] == round(14 * t["calories"]["value"] / 1000)
    assert t["sodium_mg"]["value"] == 2300 and "at most 10 g in a meal" in t["added_sugar"]
    assert "South Asian ancestry" in t["notes"][0] and not t["prediabetes"]
    dietplan.set_prefs(m, "lose", ["egg"], ["american"], ["kerala"])
    assert dietplan.targets(m)["calories"]["value"] == round((resting * 1.55 - 500) / 10) * 10


def test_devices_burn_and_the_owners_own_numbers_win(tmp_path):
    m = Memory(tmp_path / "b.db")
    _me(m)
    for d in range(8):
        healthrecords.record(m, "health_connect", (TODAY - timedelta(days=d)).isoformat(), {"calories": 2600})
    t = dietplan.targets(m)
    assert t["calories"]["value"] == 2600 and "devices estimate" in t["calories"]["how"]
    coach.set_plan(m, {"goals": ["Eat better"], "habits": [{"text": "Walk"}], "diet": {"calories": 2000,
                                                                                         "protein_g": 100}})
    t = dietplan.targets(m)
    assert t["calories"]["value"] == 2000 and t["protein_g"]["low"] == 100 and "you set" in t["calories"]["how"]


def test_a_day_fits_the_targets_and_follows_the_choices(tmp_path):
    m = Memory(tmp_path / "b.db")
    _me(m, a1c=5.9)
    days = [dietplan.menu(m, TODAY + timedelta(days=i)) for i in range(6)]
    for mn in days:
        target = mn["targets"]["calories"]["value"]
        assert abs(mn["totals"]["kcal"] - target) <= 0.15 * target
        assert mn["totals"]["protein_g"] >= mn["targets"]["protein_g"]["low"] - 1
        assert [x["meal"] for x in mn["meals"]] == list("blds")
        assert not any(i["key"] == "rice_white" for x in mn["meals"] for i in x["items"])  # prediabetes: brown rice
    assert len({x["template"] for mn in days for x in mn["meals"]}) >= 8  # it rotates
    dietplan.set_prefs(m, "keep", [], ["american"], ["kerala"], where="india")
    veg = dietplan.menu(m, TODAY)
    assert all(dietfoods.DISHES[i["key"]]["diet"] == "veg" for x in veg["meals"] for i in x["items"])
    assert all(x["cuisine"] == "Kerala home food" for x in veg["meals"])
    options = [tp for tp in dietplan.TEMPLATES if tp[2] == "l" and dietplan._allowed(tp, dietplan.prefs(m))]
    assert len(options) == 1 or dietplan.menu(m, TODAY, {"l": 1})["meals"][1]["template"] != veg["meals"][1]["template"]
    with pytest.raises(ToolError):
        dietplan.set_prefs(m, "keep", [], [], ["kerala"])


@pytest.mark.parametrize("eats", [[], ["egg"], ["nonveg"], ["egg", "nonveg"]])
@pytest.mark.parametrize("where", ["us", "india"])
def test_every_place_and_diet_gets_all_four_meals(tmp_path, eats, where):
    m = Memory(tmp_path / "b.db")
    _me(m)
    dietplan.set_prefs(m, "keep", eats, dietplan.PLACE_DEFAULTS["us"], dietplan.PLACE_DEFAULTS["india"], where=where)
    meals = dietplan.menu(m, TODAY)["meals"]
    assert [x["meal"] for x in meals] == list("blds")
    assert all(dietfoods.DISHES[i["key"]]["diet"] in ["veg", *eats] for x in meals for i in x["items"])


def test_the_menu_follows_where_the_phone_is(tmp_path):
    import json
    m = Memory(tmp_path / "b.db")
    _me(m)
    assert dietplan.prefs(m)["here"] == "us" and "no phone location" in dietplan.prefs(m)["here_how"]
    m.set("location:current", json.dumps({"area": "Edison", "city": "Edison", "country": "US"}))
    us = dietplan.menu(m, TODAY)
    assert {x["cuisine"] for x in us["meals"]} <= {"American (grilled chicken, salads)", "Mediterranean"}
    m.set("location:current", json.dumps({"area": "Kochi", "city": "Kochi", "country": "IN"}))
    india = dietplan.menu(m, TODAY)
    assert india["prefs"]["here"] == "india" and india["prefs"]["here_how"] == "your phone is in India"
    assert {x["cuisine"] for x in india["meals"]} <= {"Kerala home food", "Indian (biryani, palak paneer, dal, chana)"}
    dietplan.set_prefs(m, "keep", ["egg", "nonveg"], ["american", "indian"], ["kerala"], where="us")
    assert dietplan.prefs(m)["cuisines"] == ["american", "indian"]  # the owner's own choice wins over the phone


def test_eating_a_menu_meal_keeps_all_its_numbers(tmp_path):
    m = Memory(tmp_path / "b.db")
    _me(m)
    meal = dietplan.menu(m, TODAY)["meals"][0]
    dietplan.eat(m, meal)
    ate = coach.eaten(m, local_now().date())
    assert ate["calories"] == round(meal["totals"]["kcal"]) and ate["fiber_g"] == round(meal["totals"]["fiber_g"], 1)
    assert ate["meals"][0]["what"].startswith("Breakfast: ") and ate["sodium_mg"] is not None
    coach.add_meal(m, "Tea")  # a typed meal without numbers counts toward none of them
    assert coach.eaten(m, local_now().date())["fiber_g_meals"] == 1


def test_the_page_endpoints(tmp_path, monkeypatch):
    from buddy import server
    mem = Memory(tmp_path / "b.db")
    _me(mem)
    monkeypatch.setattr(server, "memory", mem)
    monkeypatch.setattr(server, "authorized", lambda t: t == "s3cret")
    client, auth = TestClient(server.app), {"Authorization": "Bearer s3cret"}
    got = client.get("/api/health/diet?l=1", headers=auth).json()
    assert got["targets"]["calories"]["value"] and len(got["meals"]) == 4 and got["choices"]["eats"]
    ate = client.post("/api/health/diet", json={"action": "eat", "meal": "l", "swap": {"l": 1}}, headers=auth).json()
    assert ate["text"] == "Lunch added to today's meals."
    assert coach.eaten(mem, local_now().date())["calories"] == round(got["meals"][1]["totals"]["kcal"])
    bad = client.post("/api/health/diet", json={"action": "prefs", "goal": "x", "eats": [], "us": [], "india": []},
                      headers=auth)
    assert bad.status_code == 400
    assert date.fromisoformat(got["day"])
