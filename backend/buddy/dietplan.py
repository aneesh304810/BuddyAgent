"""The owner's daily diet: targets worked out by code, and a day's menu built from dishes they eat, with USDA numbers.

**Targets** (each says where it comes from):
- Calories: what the owner's devices estimate they burn (the average of 7 or more of the last 14 days), or, without
  that, the Mifflin-St Jeor equation from weight, height, age and sex times an activity factor from their steps;
  then their weight goal (keep; lose slowly, 500 a day less but never under 1,500 for men or 1,200 for women; gain
  slowly, 300 more). A calorie or protein number the owner typed in their plan wins.
- Protein: 1.2 to 1.6 g per kg of body weight (Dietary Guidelines for Americans 2025-2030).
- Fibre: 14 g per 1,000 calories (the National Academies' adequate intake; the 2025-2030 guidelines give no number).
- Sodium under 2,300 mg; saturated fat at most 10% of calories; added sugar: none recommended, at most 10 g a meal
  (all three from the 2025-2030 guidelines).
- With prediabetes-range numbers or South Asian ancestry, a carbohydrate note: whole grains over refined ones,
  brown rice over white, rice at the portion shown, protein and vegetables at every meal.

**The day's menu**: breakfast, lunch, dinner and a snack from meal templates in the cuisines the owner eats where
they are: in the US (Kerala food is hard to find there) American grilled chicken and salads and Mediterranean, in
India Kerala home food and other Indian dishes, each list the owner's to change. Where they are comes from the
phone's last location (its country only), or the owner sets it. Rotated by date, each dish with USDA FNDDS numbers
and portions (dietfoods.py). The staple of each meal (rice, idli, dosa, quinoa, oatmeal, bread) is scaled so the
meal fits its share of the day's calories, protein is topped up at the snack when the day falls short, and the day is
checked against fibre, sodium and saturated fat. Brown rice replaces white with prediabetes-range numbers. A meal can
be added to the day's meals with all its numbers.

Plain code, on the owner's server; no AI model is given any of it. Not medical advice: a registered dietitian (often
covered; see Labs -> Checkups) can tailor it.
"""

import json
from datetime import date, timedelta

from . import dietfoods
from .memory import Memory
from .tools import ToolError, local_now

PREFS_KEY = "diet:prefs"
GOALS = {"keep": "Keep my weight", "lose": "Lose slowly", "gain": "Gain slowly"}
CUISINES = {"kerala": "Kerala home food", "indian": "Indian (biryani, palak paneer, dal, chana)",
            "american": "American (grilled chicken, salads)", "mediterranean": "Mediterranean"}
PLACES = {"us": "In the US", "india": "In India"}
WHERE = {"auto": "Where my phone is", "us": "The US", "india": "India"}
PLACE_DEFAULTS = {"us": ["american", "mediterranean"], "india": ["kerala", "indian"]}
EATS = {"egg": "Eggs", "nonveg": "Fish, chicken or meat"}  # plant dishes always; with neither, vegetarian
SHARE = {"b": 0.25, "l": 0.35, "d": 0.30, "s": 0.10}
MEAL_NAMES = {"b": "Breakfast", "l": "Lunch", "d": "Dinner", "s": "Snack"}
NUMBERS = ("kcal", "protein_g", "carbs_g", "fat_g", "fiber_g", "sodium_mg", "sat_fat_g", "sugar_g")
# key, cuisine, meal, items: (dish, portions, the staple that is scaled: (step, least, most) or None)
TEMPLATES = [
    ("idli_sambar", "kerala", "b", [("idli", 3, (1, 2, 6)), ("sambar", 1, None)]),
    ("dosa_sambar", "kerala", "b", [("dosa", 1, (1, 1, 3)), ("sambar", 1, None)]),
    ("upma_eggs", "kerala", "b", [("upma", 1, (0.5, 0.5, 2)), ("egg_boiled", 2, None)]),
    ("oatmeal_eggs", "american", "b", [("oatmeal", 1, (0.5, 0.5, 2)), ("egg_boiled", 2, None), ("banana", 1, None)]),
    ("omelet_toast", "american", "b", [("omelet", 2, None), ("bread_wheat", 2, (1, 1, 3)), ("apple", 1, None)]),
    ("yogurt_bowl", "mediterranean", "b", [("greek_yogurt", 1, None), ("banana", 1, None),
                                           ("almonds", 1, (0.5, 0.5, 2))]),
    ("rice_fish_curry", "kerala", "l", [("rice_white", 1, (0.5, 0.5, 2.5)), ("fish_curry", 1, None),
                                        ("vegetable_curry", 0.5, None), ("buttermilk", 1, None)]),
    ("rice_sambar_dal", "kerala", "l", [("rice_white", 1, (0.5, 0.5, 2.5)), ("sambar", 1, None), ("dal", 0.5, None),
                                        ("curd", 0.5, None)]),
    ("grilled_chicken_salad", "american", "l", [("chicken_grilled", 1, None), ("salad", 2, None), ("olive_oil", 1, None),
                                                ("apple", 1, None), ("bread_wheat", 1, (1, 1, 4))]),
    ("greek_salad_quinoa", "mediterranean", "l", [("greek_salad", 2, None), ("quinoa", 1, (0.5, 0.5, 2)),
                                                  ("hummus", 4, None), ("chickpeas", 0.5, None)]),
    ("rice_chicken_curry", "kerala", "d", [("rice_white", 1, (0.5, 0.5, 2.5)), ("chicken_curry", 1, None),
                                           ("vegetable_curry", 0.5, None)]),
    ("rice_dal_egg", "kerala", "d", [("rice_white", 1, (0.5, 0.5, 2.5)), ("dal", 1, None), ("vegetable_curry", 0.5, None),
                                     ("egg_boiled", 1, None)]),
    ("baked_fish_quinoa", "american", "d", [("fish_baked", 1, None), ("salad", 2, None), ("olive_oil", 1, None),
                                            ("quinoa", 0.5, (0.5, 0.5, 1.5))]),
    ("lentil_soup_tabbouleh", "mediterranean", "d", [("lentil_soup", 1.5, None), ("tabbouleh", 1, None),
                                                     ("chicken_grilled", 1, None), ("quinoa", 0.5, (0.5, 0.5, 1.5))]),
    ("lentil_soup_salad", "mediterranean", "l", [("lentil_soup", 1.5, None), ("greek_salad", 1, None),
                                                 ("quinoa", 0.5, (0.5, 0.5, 2))]),
    ("egg_salad_toast", "american", "l", [("egg_boiled", 2, None), ("salad", 2, None), ("olive_oil", 1, None),
                                          ("apple", 1, None), ("bread_wheat", 2, (1, 1, 4))]),
    ("quinoa_chickpea_bowl", "mediterranean", "d", [("quinoa", 1, (0.5, 0.5, 2)), ("chickpeas", 1, None),
                                                    ("greek_salad", 1, None), ("olive_oil", 0.5, None)]),
    ("omelet_salad", "american", "d", [("omelet", 2, None), ("salad", 2, None), ("olive_oil", 0.5, None),
                                       ("bread_wheat", 1, None), ("quinoa", 1, (0.5, 0.5, 2))]),
    ("apple_almonds", "american", "s", [("apple", 1, None), ("almonds", 1, None)]),
    ("buttermilk_banana", "kerala", "s", [("buttermilk", 1, None), ("banana", 1, None)]),
    ("yogurt_papaya", "kerala", "s", [("curd", 1, None), ("papaya", 1, None)]),
    ("hummus_salad", "mediterranean", "s", [("hummus", 3, (1, 2, 6)), ("salad", 1, None)]),
    ("dosa_curd", "indian", "b", [("dosa", 1, (1, 1, 3)), ("sambar", 1, None), ("curd", 0.5, None)]),
    ("upma_curd_banana", "indian", "b", [("upma", 1, (0.5, 0.5, 2)), ("curd", 0.5, None), ("egg_boiled", 1, None)]),
    ("chicken_biryani_raita", "indian", "l", [("biryani_chicken", 1.5, (0.5, 1, 3)), ("curd", 0.5, None),
                                              ("salad", 1, None)]),
    ("rice_palak_paneer_dal", "indian", "l", [("rice_white", 1, (0.5, 0.5, 2.5)), ("palak_paneer", 1, None),
                                              ("dal", 0.5, None)]),
    ("rice_chana", "indian", "d", [("rice_white", 1, (0.5, 0.5, 2.5)), ("chickpeas", 1, None),
                                   ("vegetable_curry", 0.5, None)]),
    ("veg_biryani_dal", "indian", "d", [("biryani_veg", 1.5, (0.5, 1, 3)), ("dal", 0.5, None), ("curd", 0.5, None)]),
    ("rice_lentil_curry_egg", "indian", "d", [("rice_white", 1, (0.5, 0.5, 2.5)), ("lentil_curry", 1, None),
                                              ("egg_boiled", 1, None)]),
    ("curd_banana", "indian", "s", [("curd", 1, None), ("banana", 1, None)]),
]
TOP_UP = ["egg_boiled", "greek_yogurt", "chicken_grilled", "dal"]  # protein at the snack, when the day is short


def place(memory: Memory, chosen: str = "auto") -> tuple[str, str]:
    """("us" or "india", how it was decided): the owner's choice, else the phone's last country (India, or the US
    list anywhere else)."""
    if chosen in PLACES:
        return chosen, "you set it"
    try:
        country = (json.loads(memory.get("location:current") or "{}") or {}).get("country") or ""
    except ValueError:
        country = ""
    if country == "IN":
        return "india", "your phone is in India"
    if country:
        return "us", "your phone is in the US" if country == "US" else "your phone is outside India (the US list)"
    return "us", "no phone location yet (the US list)"


def prefs(memory: Memory) -> dict:
    try:
        got = json.loads(memory.get(PREFS_KEY) or "{}")
    except ValueError:
        got = {}
    got = got if isinstance(got, dict) else {}
    places = {}
    for p_key, default in PLACE_DEFAULTS.items():
        chosen = (got.get("places") or {}).get(p_key)
        places[p_key] = [c for c in chosen or default if c in CUISINES] or default
    where = got.get("where") if got.get("where") in WHERE else "auto"
    here, how = place(memory, where)
    return {"goal": got.get("goal") if got.get("goal") in GOALS else "keep",
            "eats": [e for e in (got["eats"] if isinstance(got.get("eats"), list) else list(EATS)) if e in EATS],
            "places": places, "where": where, "here": here, "here_how": how, "cuisines": places[here]}


def set_prefs(memory: Memory, goal: str, eats: list, us: list, india: list, where: str = "auto") -> dict:
    if goal not in GOALS or where not in WHERE:
        raise ToolError("Pick a weight goal and where you are.")
    eats = [e for e in eats or [] if e in EATS]  # none: vegetarian
    places = {"us": [c for c in us or [] if c in CUISINES], "india": [c for c in india or [] if c in CUISINES]}
    if not places["us"] or not places["india"]:
        raise ToolError("Pick at least one cuisine for each place.")
    memory.set(PREFS_KEY, json.dumps({"goal": goal, "eats": eats, "places": places, "where": where}))
    return prefs(memory)


# --- targets

def _person(memory: Memory) -> dict:
    """Weight, height, age and sex from the owner's own data (Checkups answers first, then the Oura profile)."""
    from . import prevention  # noqa: PLC0415
    p = prevention._profile(memory)
    s = prevention.suggested(memory)
    age = local_now().year - (p.get("birth_year") or s.get("birth_year")) if (p.get("birth_year") or s.get("birth_year")) \
        else None
    return {"weight": prevention._weight(memory), "height_cm": p.get("height_cm") or s.get("height_cm"),
            "age": age, "sex": p.get("sex") or s.get("sex") or "", "groups": prevention._groups(p),
            "conditions": set(p.get("conditions") or [])}


def _average(memory: Memory, measure: str, days: int = 14) -> tuple[float | None, int]:
    from .healthrecords import _ready  # noqa: PLC0415
    _ready(memory)
    since = (local_now().date() - timedelta(days=days)).isoformat()
    rows = [float(r["value"]) for r in memory.db.execute(
        "SELECT AVG(value) AS value FROM health_records WHERE measure = ? AND at >= ? GROUP BY substr(at, 1, 10)",
        (measure, since)) if r["value"] is not None]
    return (sum(rows) / len(rows) if rows else None), len(rows)


def activity_factor(steps: float | None) -> tuple[float, str]:
    if steps is None:
        return 1.375, "lightly active (no step count yet)"
    if steps < 5000:
        return 1.2, f"about {round(steps):,} steps a day: mostly sitting"
    if steps < 7500:
        return 1.375, f"about {round(steps):,} steps a day: lightly active"
    if steps < 10000:
        return 1.55, f"about {round(steps):,} steps a day: moderately active"
    return 1.725, f"about {round(steps):,} steps a day: very active"


def mifflin(weight: float, height_cm: float, age: int, sex: str) -> float:
    """Resting calories (Mifflin-St Jeor, 1990)."""
    return 10 * weight + 6.25 * height_cm - 5 * age + (5 if sex == "male" else -161)


def targets(memory: Memory) -> dict:
    from . import coach, prevention  # noqa: PLC0415
    me, pr = _person(memory), prefs(memory)
    typed = (coach.plan(memory).get("diet") or {})
    missing = [w for w, ok in (("your weight (a scale through Health Connect, or Oura)", me["weight"]),
                               ("your height (Labs → Checkups)", me["height_cm"]),
                               ("your birth year (Labs → Checkups)", me["age"]),
                               ("your sex (Labs → Checkups)", me["sex"])) if not ok]
    out: dict = {"goal": pr["goal"], "missing": missing}
    burned, burn_days = _average(memory, "calories")
    steps, _ = _average(memory, "steps")
    if coach._num(typed.get("calories")):
        kcal, how = float(typed["calories"]), "the number you set in your plan"
    else:
        if burned and burn_days >= 7:
            base, how = burned, f"what your devices estimate you burn (average of {burn_days} days)"
        elif not missing:
            factor, words = activity_factor(steps)
            base = mifflin(me["weight"], me["height_cm"], me["age"], me["sex"]) * factor
            how = f"the Mifflin-St Jeor equation from your weight, height, age and sex, {words}"
        else:
            base, how = None, ""
        if base is None:
            kcal = None
        else:
            floor = 1500 if me["sex"] == "male" else 1200
            kcal = {"keep": base, "lose": max(base - 500, floor), "gain": base + 300}[pr["goal"]]
            how += {"keep": "", "lose": "; 500 a day less to lose slowly", "gain": "; 300 a day more to gain slowly"}[
                pr["goal"]]
    out["calories"] = {"value": round(kcal / 10) * 10 if kcal else None, "how": how}
    if coach._num(typed.get("protein_g")):
        out["protein_g"] = {"low": float(typed["protein_g"]), "high": float(typed["protein_g"]),
                            "how": "the number you set in your plan"}
    elif me["weight"]:
        out["protein_g"] = {"low": round(1.2 * me["weight"]), "high": round(1.6 * me["weight"]),
                            "how": f"1.2 to 1.6 g per kg of your {me['weight']:g} kg (Dietary Guidelines 2025-2030)"}
    else:
        out["protein_g"] = {"low": None, "high": None, "how": "needs your weight"}
    k = out["calories"]["value"]
    out["fiber_g"] = {"value": round(14 * k / 1000) if k else None,
                      "how": "14 g per 1,000 calories (National Academies)"}
    out["sat_fat_g"] = {"value": round(0.10 * k / 9) if k else None,
                        "how": "at most 10% of calories (Dietary Guidelines 2025-2030)"}
    out["sodium_mg"] = {"value": 2300, "how": "under 2,300 mg (Dietary Guidelines 2025-2030)"}
    out["added_sugar"] = "None recommended; at most 10 g in a meal (Dietary Guidelines 2025-2030)."
    a1c = prevention._latest_lab(memory, "hba1c")
    pre = "prediabetes" in me["conditions"] or bool(a1c and 5.7 <= a1c["value"] < 6.5)
    out["prediabetes"] = pre
    notes = []
    if pre:
        notes.append(f"Your A1c of {a1c['value']:g}% is in the prediabetes range" if a1c and not
                     "prediabetes" in me["conditions"] else "With prediabetes")
        notes[-1] += (": whole grains over refined ones (2 to 4 servings a day), brown rice instead of white, rice "
                      "at the portion shown, and protein and vegetables at every meal. The menu does that.")
    elif "south_asian" in me["groups"]:
        notes.append("With South Asian ancestry diabetes comes at a lower weight: whole grains over refined ones, rice "
                     "at the portion shown, and protein and vegetables at every meal.")
    out["notes"] = notes
    return out


# --- the day's menu

def _amount(dish: dict, portions: float) -> dict:
    grams = dish["grams"] * portions
    return {k: round(dish["per100"].get(k, 0) * grams / 100, 1) for k in NUMBERS} | {"grams": round(grams)}


def _sum(items: list[dict]) -> dict:
    return {k: round(sum(i[k] for i in items), 1) for k in NUMBERS}


PLURAL = {"cup": "cups", "egg": "eggs", "tablespoon": "tablespoons", "banana": "bananas", "item": "pieces",
          "medium or regular slice": "medium slices", "small breast": "small breasts", "large fillet": "large fillets",
          "6 oz container": "6 oz containers", "medium": "medium"}


def _portion_words(dish: dict, portions: float) -> str:
    """'2 cups, cooked', '3 pieces', '1 small breast': FNDDS's own portion, counted."""
    first, _, unit = dish["portion"].partition(" ")
    if first != "1" or portions == 1:
        return f"{portions:g} × {dish['portion']}" if first != "1" else dish["portion"]
    head, _, rest = unit.partition(",")
    return f"{portions:g} {PLURAL.get(head, head)}" + (f",{rest}" if rest else "")


def _allowed(template, pr: dict) -> bool:
    _, cuisine, _, items = template
    return cuisine in pr["cuisines"] and all(dietfoods.DISHES[d]["diet"] in ["veg", *pr["eats"]] for d, _, _ in items)


def _build(template, kcal_target: float | None, pre: bool) -> dict:
    key, cuisine, meal, items = template
    chosen = []
    for dish_key, portions, flex in items:
        if pre and dish_key == "rice_white":
            dish_key = "rice_brown"
        chosen.append([dish_key, float(portions), flex])
    if kcal_target:
        flex = next((c for c in chosen if c[2]), None)
        if flex:
            step, least, most = flex[2]
            others = sum(_amount(dietfoods.DISHES[d], p)["kcal"] for d, p, f in chosen if f is None)
            per = _amount(dietfoods.DISHES[flex[0]], step)["kcal"]
            want = (kcal_target - others) / per if per else 0
            flex[1] = min(most, max(least, round(want) * step))
    rows = []
    for dish_key, portions, _ in chosen:
        dish = dietfoods.DISHES[dish_key]
        rows.append({"key": dish_key, "name": dish["name"], "portions": portions,
                     "amount": _portion_words(dish, portions), "fdc_id": dish["fdc_id"], **_amount(dish, portions)})
    return {"meal": meal, "name": MEAL_NAMES[meal], "template": key, "cuisine": CUISINES[cuisine], "items": rows,
            "totals": _sum(rows)}


def menu(memory: Memory, day: date | None = None, swap: dict | None = None) -> dict:
    """A day's menu fitted to the targets. swap: meal letter -> how many templates further to rotate."""
    day = day or local_now().date()
    t, pr = targets(memory), prefs(memory)
    kcal = t["calories"]["value"]
    meals = []
    for m in "blds":
        options = [tp for tp in TEMPLATES if tp[2] == m and _allowed(tp, pr)]
        if not options:
            continue
        pick = options[(day.toordinal() + "blds".index(m) + int((swap or {}).get(m, 0))) % len(options)]
        meals.append(_build(pick, kcal * SHARE[m] if kcal else None, t["prediabetes"]) | {"options": len(options)})
    low = t["protein_g"]["low"]
    snack = next((x for x in meals if x["meal"] == "s"), None)
    added = 0
    for extra in TOP_UP:
        day_protein = sum(x["totals"]["protein_g"] for x in meals)
        if not low or day_protein >= low or snack is None or added >= 2:
            break
        dish = dietfoods.DISHES[extra]
        if dish["diet"] not in ["veg", *pr["eats"]]:
            continue
        portions = 2.0 if extra == "egg_boiled" else 1.0
        snack["items"].append({"key": extra, "name": dish["name"], "portions": portions, "fdc_id": dish["fdc_id"],
                               "amount": _portion_words(dish, portions), "added_for": "protein",
                               **_amount(dish, portions)})
        snack["totals"] = _sum(snack["items"])
        added += 1
    totals = _sum([x["totals"] for x in meals]) if meals else {k: 0 for k in NUMBERS}
    checks = []
    if kcal:
        checks.append(("Calories", totals["kcal"], kcal, abs(totals["kcal"] - kcal) <= 0.1 * kcal))
    if low:
        checks.append(("Protein (g)", totals["protein_g"], low, totals["protein_g"] >= low))
    if t["fiber_g"]["value"]:
        checks.append(("Fibre (g)", totals["fiber_g"], t["fiber_g"]["value"], totals["fiber_g"] >= t["fiber_g"]["value"]))
    checks.append(("Sodium (mg)", totals["sodium_mg"], 2300, totals["sodium_mg"] < 2300))
    if t["sat_fat_g"]["value"]:
        checks.append(("Saturated fat (g)", totals["sat_fat_g"], t["sat_fat_g"]["value"],
                       totals["sat_fat_g"] <= t["sat_fat_g"]["value"]))
    tips = []
    for name, got, want, ok in checks:
        if not ok and name.startswith("Fibre"):
            tips.append("Short on fibre: add a cup of salad or cooked vegetables, or swap white rice for brown.")
        if not ok and name.startswith("Sodium"):
            tips.append("Over on sodium: USDA's recipes are salted as people usually cook them; less salt at home "
                        "brings it down.")
    return {"day": day.isoformat(), "targets": t, "prefs": pr, "meals": meals, "totals": totals,
            "choices": {"goals": GOALS, "cuisines": CUISINES, "eats": EATS, "places": PLACES, "where": WHERE},
            "checks": [{"what": n, "got": g, "target": w, "ok": ok} for n, g, w, ok in checks], "tips": tips,
            "source": dietfoods.SOURCE,
            "note": "A suggestion worked out by code from USDA's numbers, not medical advice. Home recipes differ from "
                    "USDA's; a registered dietitian can tailor it (often covered: see Labs → Checkups). No AI model "
                    "is given any of it."}


def eat(memory: Memory, meal: dict) -> dict:
    """Add one menu meal to today's meals, with all its numbers."""
    from . import coach  # noqa: PLC0415
    what = f"{meal['name']}: " + ", ".join(f"{i['name']} ({i['amount']})" for i in meal["items"])
    return coach.add_meal(memory, what, meal["totals"]["kcal"], meal["totals"]["protein_g"],
                          extra={k: meal["totals"][k] for k in coach.EXTRA if k in meal["totals"]})
