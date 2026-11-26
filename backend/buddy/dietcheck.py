"""Does this food fit? Before a looked-up, scanned or phone-named food is added, Buddy's code checks it at the
portion the owner picked against their own plan:

- allergies and foods to avoid (the plan's lists, against the food's name, its ingredients and a pack's allergen
  list where Open Food Facts has one) and the menu's vegetarian or egg choice;
- the goal: today's calories after it against the target, how much of the day it is in one go, and whether the
  weight trend matches keep, lose or gain;
- the Dietary Guidelines 2025-2030 limits, with FDA's daily values (5% or less is low, 20% or more is high);
- the owner's labs and conditions: A1c or prediabetes (carbohydrates with little fibre, white rice over a cup),
  LDL (saturated fat at 6% of calories, the American Heart Association's advice), blood pressure (sodium at
  1,500 mg).

Code only: no AI model sees any of it, and every line says what it rests on. Not medical advice.
"""

from __future__ import annotations

import re

from .memory import Memory
from .tools import ToolError, local_now

NUMBERS = ("kcal", "protein_g", "carbs_g", "fat_g", "fiber_g", "sodium_mg", "sat_fat_g", "sugar_g", "added_sugar_g")
DV = {"sat_fat_g": 20.0, "sodium_mg": 2300.0, "fiber_g": 28.0}  # FDA's Daily Values for adults (21 CFR 101.9)
FDA = "FDA's daily values: 5% or less of a day's value is low, 20% or more is high"
DGA = "Dietary Guidelines for Americans 2025-2030"
AHA_SAT = "American Heart Association: 5 to 6% of calories from saturated fat for people who need to lower LDL"
AHA_NA = "American Heart Association: 1,500 mg of sodium a day is the ideal limit, above all with high blood pressure"
ADA = "American Diabetes Association Standards of Care: fibre-rich carbohydrates over refined ones, in set portions"

ALLERGENS: dict[str, tuple[str, ...]] = {  # an owner's word -> the words that give it away in a name or ingredients
    "peanut": ("peanut", "groundnut", "arachis"),
    "tree nut": ("almond", "cashew", "walnut", "pistachio", "hazelnut", "pecan", "macadamia", "brazil nut",
                 "pine nut", "badam", "kaju"),
    "milk": ("milk", "cheese", "curd", "yogurt", "yoghurt", "paneer", "ghee", "butter", "cream", "whey", "casein",
             "lactose", "buttermilk", "khoa", "malai", "dahi", "lassi", "raita"),
    "egg": ("egg", "omelet", "omelette", "albumen", "mayonnaise", "meringue"),
    "wheat": ("wheat", "maida", "atta", "semolina", "rava", "sooji", "suji", "durum", "spelt", "bread", "chapati",
              "chapatti", "roti", "naan", "paratha", "porotta", "parotta", "pasta", "noodle", "couscous", "bulgur",
              "seitan", "upma"),
    "soy": ("soy", "soya", "soybean", "tofu", "edamame", "tempeh", "miso"),
    "fish": ("fish", "salmon", "tuna", "sardine", "mackerel", "anchovy", "cod", "tilapia", "pomfret", "karimeen",
             "mathi", "ayala", "seer", "trout", "halibut"),
    "shellfish": ("shrimp", "prawn", "crab", "lobster", "clam", "mussel", "oyster", "scallop", "squid", "chemmeen",
                  "kakka", "crustacean"),
    "sesame": ("sesame", "tahini", "gingelly", "hummus"),
    "mustard": ("mustard",),
    "coconut": ("coconut",),
}
ALLERGENS["gluten"] = ALLERGENS["wheat"] + ("barley", "rye", "malt")
SAYS = {"peanuts": "peanut", "nut": "tree nut", "nuts": "tree nut", "tree nuts": "tree nut", "dairy": "milk",
        "lactose": "milk", "eggs": "egg", "seafood": "shellfish", "shrimp": "shellfish", "prawns": "shellfish",
        "soya": "soy", "soybeans": "soy", "sesame seeds": "sesame"}
NOT_DAIRY = ("peanut butter", "almond butter", "cashew butter", "nut butter", "cocoa butter", "apple butter",
             "shea butter", "coconut milk", "almond milk", "soy milk", "oat milk", "rice milk", "cashew milk",
             "coconut cream", "cream of tartar")
OFF_TAGS = {"milk": "milk", "gluten": "gluten", "eggs": "egg", "fish": "fish", "crustaceans": "shellfish",
            "molluscs": "shellfish", "peanuts": "peanut", "nuts": "tree nut", "soybeans": "soy",
            "sesame-seeds": "sesame", "mustard": "mustard"}
MEAT = ("chicken", "mutton", "beef", "pork", "lamb", "goat", "meat", "bacon", "ham", "sausage", "turkey", "duck",
        "keema", "pepperoni", "salami", "gelatin") + ALLERGENS["fish"] + ALLERGENS["shellfish"]
WHITE_RICE_OK = re.compile(r"brown|red|wild|black|matta|parboiled|cauliflower", re.I)
ORDER = {"stop": 0, "watch": 1, "good": 2, "info": 3}


def _has(text: str, words, skip=()) -> str:
    """The first of these words in the text (as a word, plurals too), after taking out phrases that only look so."""
    low = f" {text.lower()} "
    for phrase in skip:
        low = low.replace(phrase, " ")
    for w in words:
        if re.search(rf"\b{re.escape(w)}(?:e?s)?\b", low):
            return w
    return ""


def _terms(value) -> list[str]:
    """The owner's list as typed: a list, or words with commas, 'and' or semicolons between them."""
    items = value if isinstance(value, list) else re.split(r"[,;/]|\band\b", str(value or ""))
    return list(dict.fromkeys(" ".join(str(i).lower().split()) for i in items if str(i).strip()))[:20]


def _words_for(term: str) -> tuple[str | None, tuple[str, ...]]:
    """An allergy the owner typed, as Buddy's group of words for it ("dairy": milk, paneer, ghee...), or the word."""
    key = SAYS.get(term, term)
    key = key[:-1] if key not in ALLERGENS and key.endswith("s") and key[:-1] in ALLERGENS else key
    if key in ALLERGENS:
        return key, ALLERGENS[key]
    stems = [term] + ([term[:-2]] if term.endswith("oes") else [term[:-1]] if term.endswith("s") and len(term) > 3 else [])
    return None, tuple(dict.fromkeys(stems))


def weight_trend(memory: Memory, goal: str, days: int = 28) -> dict | None:
    """The weight's slope over the last four weeks (a straight line through the day averages), against the goal."""
    from datetime import date  # noqa: PLC0415

    from .healthrecords import _ready  # noqa: PLC0415
    _ready(memory)
    since = (local_now().date().toordinal() - days)
    rows = [(date.fromisoformat(r["day"]).toordinal(), float(r["value"])) for r in memory.db.execute(
        "SELECT substr(at, 1, 10) AS day, AVG(value) AS value FROM health_records WHERE measure = 'weight' "
        "GROUP BY day ORDER BY day")]
    rows = [(d, v) for d, v in rows if d >= since and 25 <= v <= 350]
    if len(rows) < 4 or rows[-1][0] - rows[0][0] < 14:
        return None
    n = len(rows)
    mx, my = sum(d for d, _ in rows) / n, sum(v for _, v in rows) / n
    slope = sum((d - mx) * (v - my) for d, v in rows) / sum((d - mx) ** 2 for d, _ in rows) * 7  # kg a week
    weeks = round((rows[-1][0] - rows[0][0]) / 7)
    said = f"{'down' if slope < 0 else 'up'} {abs(slope):.1f} kg a week over {weeks} weeks"
    if abs(slope) < 0.05:
        said = f"steady over {weeks} weeks"
    ok = {"keep": abs(slope) <= 0.2, "lose": -1.0 <= slope <= -0.1, "gain": 0.1 <= slope <= 0.5}[goal]
    aim = {"keep": "keep it steady", "lose": "lose slowly", "gain": "gain slowly"}[goal]
    if ok:
        text = f"Your weight is {said}: in line with your goal to {aim}."
    elif goal == "lose" and slope < -1.0:
        text = f"Your weight is {said}: faster than slowly. If that isn't planned, talk it over with your doctor."
    elif goal == "gain" and slope > 0.5:
        text = f"Your weight is {said}: faster than slowly."
    else:
        text = f"Your weight is {said}, and your goal is to {aim}."
    return {"level": "good" if ok else "watch", "topic": "goal", "text": text,
            "why": f"Your weight readings ({n} days); a slow change is about 0.25 to 0.5 kg a week"}


def check(memory: Memory, what: str, per100: dict, grams, ingredients: str = "", allergens=(), now=None) -> dict:
    """The food at this portion against the owner's plan: a verdict (fits, watch, stop) and the reasons."""
    from . import coach, dietplan, prevention  # noqa: PLC0415
    now = now or local_now()
    name = " ".join(str(what or "").split())[:200]
    if not name:
        raise ToolError("Which food?")
    try:
        grams = float(grams)
    except (TypeError, ValueError):
        grams = 0.0
    if not 0 < grams <= 3000:
        raise ToolError("An amount between 1 and 3,000 grams.")
    amt = {}
    for k in NUMBERS:
        v = (per100 or {}).get(k)
        if isinstance(v, (int, float)) and not isinstance(v, bool) and 0 <= v <= (5000 if k == "sodium_mg" else 1000):
            amt[k] = v * grams / 100
    if "kcal" not in amt:
        raise ToolError("USDA has no calorie number for this food, so it can't be checked.")
    ingredients = " ".join(str(ingredients or "").split())[:2000]
    tags = {OFF_TAGS.get(str(t).split(":")[-1]) for t in (allergens or [])[:40]} - {None}
    text = f"{name} {ingredients}"
    lines: list[dict] = []

    def say(level, topic, words, why):
        lines.append({"level": level, "topic": topic, "text": words, "why": why})

    # allergies, foods to avoid, the menu's choices
    p = coach.plan(memory)
    diet = p.get("diet") or {}
    seen = "its name" + (" and ingredients" if ingredients else "") + (" and the pack's allergen list" if tags else "")
    allergies = _terms(diet.get("allergies"))
    for term in allergies:
        key, words = _words_for(term)
        found = _has(text, words, NOT_DAIRY if key == "milk" else ())
        if found or (key and key in tags):
            say("stop", "allergy", f"Has {found or key}: {term} is on your allergy list.", f"Your plan, against {seen}")
    for term in _terms(diet.get("avoid")):
        key, words = _words_for(term)
        if (found := _has(text, words, NOT_DAIRY if key == "milk" else ())):
            say("watch", "allergy", f"Has {found}: {term} is on your list of foods to avoid.", f"Your plan, against {seen}")
    if allergies and not ingredients and not tags and not any(x["level"] == "stop" for x in lines):
        say("info", "allergy", "Only the name was checked against your allergies: check the pack, or ask how it's made.",
            "No ingredients list for this food")
    eats = set(dietplan.prefs(memory)["eats"])
    if "nonveg" not in eats and (found := _has(text, MEAT)):
        say("watch", "allergy", f"Has {found}: your menu is set to vegetarian.", "Plan → Menu")
    elif "egg" not in eats and (found := _has(text, ALLERGENS["egg"])):
        say("watch", "allergy", f"Has {found}: your menu is set to no eggs.", "Plan → Menu")

    # the goal
    t = dietplan.targets(memory)
    ate = coach.eaten(memory, now.date())
    kcal, target = round(amt["kcal"]), t["calories"]["value"]
    goal = dietplan.GOALS[t["goal"]]
    if target:
        after = ate["calories"] + kcal
        why = f"Your {target:,} a day: {t['calories']['how']} (goal: {goal})"
        if after > target * 1.05:
            say("watch", "goal", f"Takes today to {after:,} of your {target:,} calories: {after - target:,} over.", why)
        else:
            say("good", "goal", f"Leaves {max(target - after, 0):,} of your {target:,} calories for today.", why)
        if kcal >= 0.45 * target:
            say("watch", "goal", f"{round(100 * kcal / target)}% of the day's calories in one go: a smaller portion "
                                 "leaves room for the rest of the day.", why)
    else:
        say("info", "goal", "No calorie target yet: Buddy needs " + ", ".join(t["missing"]) + ".", "Plan → Menu")

    # the owner's labs and conditions set some limits lower
    me = dietplan._person(memory)
    cond = me["conditions"]
    a1c, ldl = prevention._latest_lab(memory, "hba1c"), prevention._latest_lab(memory, "ldl")
    sys_bp, dia_bp = prevention._recent(memory, "bp_systolic", 30), prevention._recent(memory, "bp_diastolic", 30)
    sat_limit, sat_why, sat_topic = (t.get("sat_fat_g") or {}).get("value"), f"At most 10% of calories ({DGA})", "limits"
    high_ldl = (ldl and ldl["unit"] == "mg/dL" and ldl["value"] >= 130) or "high_cholesterol" in cond
    if high_ldl and target:
        sat_limit, sat_topic = round(0.06 * target / 9), "labs"
        sat_why = (f"Your LDL of {ldl['value']:g} mg/dL ({ldl['at'][:10]})" if ldl else "High cholesterol") + f"; {AHA_SAT}"
    high_bp = "high_bp" in cond or bool(sys_bp and sys_bp["value"] >= 130) or bool(dia_bp and dia_bp["value"] >= 80)
    na_limit, na_why, na_topic = 2300, f"Under 2,300 mg a day ({DGA})", "limits"
    if high_bp:
        na_limit, na_topic = 1500, "labs"
        bp = f"{sys_bp['value']:g}/{dia_bp['value']:g}" if sys_bp and dia_bp else \
            f"a top number of {sys_bp['value']:g}" if sys_bp else f"a bottom number of {dia_bp['value']:g}" if dia_bp else ""
        na_why = (f"Your blood pressure averages {bp} over the last readings" if bp else "High blood pressure") + \
            f"; {AHA_NA}"

    # guideline limits
    pct = {k: round(100 * amt[k] / DV[k]) for k in DV if k in amt}
    pro, (plo, phi) = amt.get("protein_g"), (t["protein_g"]["low"], t["protein_g"]["high"])
    if pro is not None:
        if pro >= 10:
            day = f" (today {ate['protein_g'] + pro:.0f} of {plo:g} to {phi:g} g)" if plo else ""
            say("good", "limits", f"{pro:.0f} g protein{day}.", t["protein_g"]["how"])
        elif kcal >= 300 and pro * 4 < 0.1 * kcal:
            more = "dal, curd, eggs or fish" if {"egg", "nonveg"} <= eats else "dal, curd or eggs" if "egg" in eats \
                else "dal, curd, paneer or chickpeas" if "nonveg" not in eats else "dal, curd, fish or chicken"
            say("watch", "limits", f"Only {pro:.0f} g protein for {kcal:,} calories: add {more}.", t["protein_g"]["how"])
    if (fib := amt.get("fiber_g")) is not None:
        if pct["fiber_g"] >= 20:
            say("good", "limits", f"High in fibre: {fib:.0f} g, {pct['fiber_g']}% of a day's value.", FDA)
        elif kcal >= 300 and pct["fiber_g"] < 10:
            say("watch", "limits", f"Little fibre for a meal ({fib:.1f} g): add vegetables, dal or a whole grain.",
                (t.get("fiber_g") or {}).get("how") or FDA)
    if (sat := amt.get("sat_fat_g")) is not None:
        if pct["sat_fat_g"] >= 20:
            say("watch", sat_topic, f"High in saturated fat: {sat:.1f} g, {pct['sat_fat_g']}% of a day's value.", FDA)
        if sat_limit and ate.get("sat_fat_g") is not None and ate["sat_fat_g"] + sat > sat_limit:
            say("watch", sat_topic, f"Takes today's saturated fat to {ate['sat_fat_g'] + sat:.0f} g, over your "
                                    f"{sat_limit:g} g.", sat_why)
        elif high_ldl and pct["sat_fat_g"] <= 5:
            say("good", "labs", "Low in saturated fat: good with your LDL.", sat_why)
    if (na := amt.get("sodium_mg")) is not None:
        if pct["sodium_mg"] >= 20:
            say("watch", na_topic, f"High in sodium: {na:,.0f} mg, {pct['sodium_mg']}% of a day's value.", FDA)
        if ate.get("sodium_mg") is not None and ate["sodium_mg"] + na > na_limit:
            say("watch", na_topic, f"Takes today's sodium to {ate['sodium_mg'] + na:,.0f} mg, over {na_limit:,} mg.",
                na_why)
        elif high_bp and pct["sodium_mg"] <= 5:
            say("good", "labs", "Low in sodium: good with your blood pressure.", na_why)
    if (added := amt.get("added_sugar_g")) is not None:
        if added > 10:
            say("watch", "limits", f"{added:.0f} g added sugar: none is recommended, at most 10 g in a meal.", DGA)
    elif (sugar := amt.get("sugar_g")) is not None and sugar > 10:
        say("info", "limits", f"{sugar:.0f} g sugar. USDA doesn't say how much is added (fruit and milk sugars aren't).",
            DGA)

    # carbohydrates, with diabetes or prediabetes
    diabetes = "diabetes" in cond or bool(a1c and a1c["value"] >= 6.5)
    if diabetes or t.get("prediabetes"):
        who = f"your A1c of {a1c['value']:g}%" if a1c else "diabetes" if diabetes else "prediabetes"
        carbs, fib = amt.get("carbs_g"), amt.get("fiber_g")
        if carbs is not None and carbs >= 45 and (fib is None or fib < 3):
            say("watch", "labs", f"{carbs:.0f} g carbohydrate with little fibre: with {who}, a smaller portion with "
                                 "protein and vegetables keeps blood sugar steadier.", ADA)
        if re.search(r"\brice\b", name, re.I) and not WHITE_RICE_OK.search(name) and grams > 160:
            say("watch", "labs", f"More than a cup of white rice ({grams:.0f} g): with {who}, a cup (158 g) or less, "
                                 "or Kerala matta or brown rice.", ADA)

    lines.sort(key=lambda x: ORDER[x["level"]])
    levels = {x["level"] for x in lines}
    verdict = "stop" if "stop" in levels else "watch" if "watch" in levels else "fits" if target else "partial"
    return {"verdict": verdict,
            "headline": {"stop": "Not for you: it's on your allergy list", "watch": "Fits, with care",
                         "fits": "Fits your plan", "partial": "Not fully checked"}[verdict],
            "amount": {k: round(v, 1) for k, v in amt.items()} | {"kcal": kcal, "grams": round(grams)},
            "lines": lines,
            "goal": weight_trend(memory, t["goal"]),  # the plan's own course: shown with the food, not part of it
            "note": "Worked out by Buddy's code from the food's numbers and your plan. No AI model sees it. Not "
                    "medical advice."}
