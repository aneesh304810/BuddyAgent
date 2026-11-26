"""The fit check: a food at the picked portion against the owner's goal, the guideline limits, their labs and
conditions, and their allergies, worked out by code before it is added."""

from datetime import timedelta

from fastapi.testclient import TestClient

from buddy import coach, dietcheck, dietplan, healthrecords, labs, prevention
from buddy.memory import Memory
from buddy.tools import local_now

TODAY = local_now().date()
DOSA = {"kcal": 210.0, "protein_g": 5.7, "carbs_g": 37.0, "fat_g": 4.1, "fiber_g": 1.8, "sodium_mg": 309.0,
        "sat_fat_g": 0.6, "sugar_g": 0.3}
RICE = {"kcal": 130.0, "protein_g": 2.7, "carbs_g": 28.6, "fat_g": 0.3, "fiber_g": 0.4, "sodium_mg": 1.0,
        "sat_fat_g": 0.1}
PIZZA = {"kcal": 266.0, "protein_g": 11.0, "carbs_g": 33.0, "fat_g": 10.0, "fiber_g": 2.3, "sodium_mg": 640.0,
         "sat_fat_g": 4.5, "sugar_g": 3.6, "added_sugar_g": 0.5}


def _me(m, weights=None, plan_diet=None, lab=None, bp=None, **answers):
    weights = weights or [70.5] * 10
    for d, w in enumerate(weights):
        healthrecords.record(m, "health_connect", (TODAY - timedelta(days=d)).isoformat(),
                             {"weight_kg": w, "steps": 8200} | ({"bp_systolic": bp[0], "bp_diastolic": bp[1]} if bp else {}))
    prevention.save_profile(m, {"birth_year": TODAY.year - 45, "sex": "male", "ancestry": ["indian_kerala"],
                                "height_cm": 170} | answers)
    if plan_diet is not None:
        coach.set_plan(m, {"goals": ["Eat well"], "habits": [{"text": "Walk", "metric": "steps", "target": 8000}],
                           "diet": plan_diet})
    for test, value, unit in lab or []:
        labs._ready(m)
        m.db.execute("INSERT INTO lab_results (report_id, source, test, printed, value, value_text, unit, band, "
                     "collected) VALUES (1, 'lab_upload', ?, ?, ?, ?, ?, 'within', '2026-03-02')",
                     (test, test, value, str(value), unit))
        m.db.commit()


def _texts(got):
    return [line["text"] for line in got["lines"]]


def test_a_plain_breakfast_fits_and_says_why(tmp_path):
    m = Memory(tmp_path / "b.db")
    _me(m)
    got = dietcheck.check(m, "Dosa, plain", DOSA, 80)
    target = dietplan.targets(m)["calories"]["value"]
    assert got["verdict"] == "fits" and got["headline"] == "Fits your plan"
    assert got["amount"]["kcal"] == 168 and got["amount"]["grams"] == 80
    assert f"Leaves {target - 168:,} of your {target:,} calories for today." in _texts(got)
    two = dietcheck.check(m, "Dosa, plain", DOSA, 160)  # USDA's numbers: 494 mg of sodium in two
    assert two["verdict"] == "watch" and "High in sodium: 494 mg, 21% of a day's value." in _texts(two)
    assert got["goal"] is None  # 10 days of weights is too short for a trend
    assert "No AI model" in got["note"]


def test_an_allergy_stops_it_and_dairy_words_are_understood(tmp_path):
    m = Memory(tmp_path / "b.db")
    _me(m, plan_diet={"allergies": "dairy, peanuts", "avoid": ["maida"]})
    got = dietcheck.check(m, "Paneer butter masala", {"kcal": 220, "sat_fat_g": 9}, 200)
    assert got["verdict"] == "stop" and got["lines"][0]["level"] == "stop"
    assert got["lines"][0]["text"] == "Has paneer: dairy is on your allergy list."
    pb = dietcheck.check(m, "Peanut butter", {"kcal": 590}, 32, ingredients="Roasted peanuts, salt")
    assert [x["text"] for x in pb["lines"] if x["level"] == "stop"] == ["Has peanut: peanuts is on your allergy list."]
    # peanut butter isn't dairy, coconut milk isn't milk
    assert not any("dairy" in t for t in _texts(pb))
    curry = dietcheck.check(m, "Vegetable curry", {"kcal": 120}, 200, ingredients="Coconut milk, beans, carrot")
    assert curry["verdict"] != "stop"
    # a pack's own allergen list counts too, and the avoid list only warns
    bar = dietcheck.check(m, "Protein bar", {"kcal": 380}, 60, ingredients="Oats, maida, whey isolate",
                          allergens=["en:milk"])
    assert bar["verdict"] == "stop" and any("maida is on your list of foods to avoid" in t for t in _texts(bar))
    plain = dietcheck.check(m, "Mixed vegetable sabzi", {"kcal": 90}, 150)
    assert "Only the name was checked against your allergies" in _texts(plain)[-1]


def test_a_vegetarian_menu_and_eggs(tmp_path):
    m = Memory(tmp_path / "b.db")
    _me(m)
    dietplan.set_prefs(m, "keep", ["egg"], ["american"], ["kerala"])
    got = dietcheck.check(m, "Chicken biryani", {"kcal": 180}, 250)
    assert "Has chicken: your menu is set to vegetarian." in _texts(got)
    dietplan.set_prefs(m, "keep", [], ["american"], ["kerala"])
    assert dietcheck.check(m, "Eggplant curry", {"kcal": 90}, 150)["verdict"] == "fits"  # not an egg
    assert "Has omelette: your menu is set to no eggs." in _texts(dietcheck.check(m, "Masala omelette", {"kcal": 150}, 120))


def test_over_the_day_and_too_much_at_once(tmp_path):
    m = Memory(tmp_path / "b.db")
    _me(m)
    target = dietplan.targets(m)["calories"]["value"]
    coach.add_meal(m, "Breakfast and lunch", target - 300, 60)
    got = dietcheck.check(m, "Pizza", PIZZA, 500)  # 1,330 kcal
    assert got["verdict"] == "watch"
    assert f"Takes today to {target - 300 + 1330:,} of your {target:,} calories: {1330 - 300:,} over." in _texts(got)
    assert f"{round(133000 / target)}% of the day's calories in one go: a smaller portion leaves room for the rest of " \
           "the day." in _texts(got)
    assert any(t.startswith("High in saturated fat: 22.5 g") for t in _texts(got))
    assert any(t.startswith("High in sodium: 3,200 mg") for t in _texts(got))


def test_labs_and_conditions_set_lower_limits(tmp_path):
    m = Memory(tmp_path / "b.db")
    _me(m, lab=[("hba1c", 6.1, "%"), ("ldl", 162, "mg/dL")], bp=(138, 86))
    rice = dietcheck.check(m, "Rice, white, cooked", RICE, 300)
    words = _texts(rice)
    assert any(t.startswith("86 g carbohydrate with little fibre: with your A1c of 6.1%") for t in words)
    assert any(t.startswith("More than a cup of white rice (300 g)") for t in words)
    assert not any("white rice" in t for t in _texts(dietcheck.check(m, "Rice, brown, cooked", RICE, 300)))
    assert not any("white rice" in t for t in _texts(dietcheck.check(m, "Kerala matta rice", RICE, 300)))
    target = dietplan.targets(m)["calories"]["value"]
    coach.add_meal(m, "Lunch", 600, 30, extra={"sat_fat_g": round(0.06 * target / 9) - 2, "sodium_mg": 1200})
    pizza = dietcheck.check(m, "Pizza", PIZZA, 100)
    sat = next(x for x in pizza["lines"] if x["text"].startswith("Takes today's saturated fat"))
    assert sat["topic"] == "labs" and "LDL of 162 mg/dL" in sat["why"] and "5 to 6% of calories" in sat["why"]
    na = next(x for x in pizza["lines"] if x["text"].startswith("Takes today's sodium"))
    assert "over 1,500 mg" in na["text"] and "138/86" in na["why"]
    salad = dietcheck.check(m, "Greek salad", {"kcal": 100, "sat_fat_g": 0.5, "sodium_mg": 60, "fiber_g": 2}, 150)
    assert {"Low in saturated fat: good with your LDL.", "Low in sodium: good with your blood pressure."} <= set(_texts(salad))


def test_the_weight_trend_against_the_goal(tmp_path):
    m = Memory(tmp_path / "b.db")
    _me(m, weights=[70 + 0.5 / 7 * d for d in range(20)])  # today first: 0.5 kg a week lighter
    dietplan.set_prefs(m, "lose", ["egg", "nonveg"], ["american"], ["kerala"])
    trend = dietcheck.weight_trend(m, "lose")
    assert trend["level"] == "good" and "down 0.5 kg a week" in trend["text"]
    assert dietcheck.weight_trend(m, "keep")["level"] == "watch"
    assert dietcheck.weight_trend(m, "gain")["text"].endswith("and your goal is to gain slowly.")
    fine = dietcheck.check(m, "Dosa, plain", DOSA, 80)  # the trend is shown beside a food, not held against it
    assert fine["verdict"] == "fits" and fine["goal"]["level"] == "good"


def test_protein_fibre_and_added_sugar(tmp_path):
    m = Memory(tmp_path / "b.db")
    _me(m)
    words = _texts(dietcheck.check(m, "White bread and jam", {"kcal": 300, "protein_g": 4, "fiber_g": 1,
                                                                "added_sugar_g": 12}, 150))
    assert "Only 6 g protein for 450 calories: add dal, curd, eggs or fish." in words
    assert any(t.startswith("Little fibre for a meal") for t in words)
    assert "18 g added sugar: none is recommended, at most 10 g in a meal." in words
    beans = _texts(dietcheck.check(m, "Chickpeas, cooked", {"kcal": 164, "protein_g": 8.9, "fiber_g": 7.6}, 160))
    assert any(t.startswith("14 g protein (today 14 of") for t in beans) and any(t.startswith("High in fibre") for t in beans)
    banana = _texts(dietcheck.check(m, "Banana", {"kcal": 89, "sugar_g": 12.2}, 120))
    assert any(t.startswith("15 g sugar. USDA doesn't say how much is added") for t in banana)


def test_missing_numbers_and_the_endpoint(tmp_path, monkeypatch):
    from buddy import server
    m = Memory(tmp_path / "b.db")
    got = dietcheck.check(m, "Idli", {"kcal": 128}, 80)
    assert got["lines"][0]["text"].startswith("No calorie target yet: Buddy needs your weight")
    monkeypatch.setattr(server, "memory", m)
    monkeypatch.setattr(server, "authorized", lambda t: t == "s3cret")
    client, auth = TestClient(server.app), {"Authorization": "Bearer s3cret"}
    ok = client.post("/api/health/fit", json={"what": "Idli", "per100": {"kcal": 128}, "grams": 80}, headers=auth)
    assert ok.status_code == 200 and ok.json()["verdict"] == "partial"  # no calorie target: not "fits"
    bad = client.post("/api/health/fit", json={"what": "Idli", "per100": {}, "grams": 80}, headers=auth)
    assert bad.status_code == 400 and "no calorie number" in bad.json()["detail"]
    assert client.post("/api/health/fit", json={"what": "Idli", "per100": {"kcal": 1}, "grams": 0},
                       headers=auth).status_code == 400


def test_allergies_are_set_on_the_page_and_cleared_there(tmp_path, monkeypatch):
    from buddy import server
    m = Memory(tmp_path / "b.db")
    _me(m)
    monkeypatch.setattr(server, "memory", m)
    monkeypatch.setattr(server, "authorized", lambda t: t == "s3cret")
    client, auth = TestClient(server.app), {"Authorization": "Bearer s3cret"}
    plan = {"goals": ["Eat well"], "habits": [{"text": "Walk", "metric": "steps", "target": 8000}],
            "diet": {"allergies": "  Shellfish,   peanuts " + "x" * 300, "avoid": "maida"}}
    assert client.post("/api/health/page", json={"action": "plan", "plan": plan}, headers=auth).status_code == 200
    saved = coach.plan(m)["diet"]
    assert saved["allergies"].startswith("Shellfish, peanuts x") and len(saved["allergies"]) == 200
    assert dietcheck.check(m, "Prawn curry", {"kcal": 150}, 200)["verdict"] == "stop"
    plan["diet"] = {"allergies": "", "avoid": "maida"}
    client.post("/api/health/page", json={"action": "plan", "plan": plan}, headers=auth)
    assert "allergies" not in coach.plan(m)["diet"]
    assert dietcheck.check(m, "Prawn curry", {"kcal": 150}, 200)["verdict"] == "fits"


def test_a_meal_keeps_its_saturated_fat_and_sugar(tmp_path):
    m = Memory(tmp_path / "b.db")
    day = coach.add_meal(m, "Pizza (2 slices)", 532, 22, extra={"sat_fat_g": 9, "sugar_g": 7.2, "added_sugar_g": 1})
    assert (day["sat_fat_g"], day["sugar_g"], day["added_sugar_g"]) == (9.0, 7.2, 1.0)
