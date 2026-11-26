"""Is this product good for me? A verdict on a food or a scanned pack, by code, for the owner in particular.

  judge(memory, food) -> {verdict: good | sometimes | skip, headline, benefits, concerns, serving, per_serving, ...}

A grocery question, not "does it fit today" (that is dietcheck.py): one label serving of the product, judged on
- what it gives (FDA's nutrient content claims: a good source is 10-19% of a day's value in a serving, high 20%
  or more; low sodium 140 mg or less; low saturated fat 1 g or less; no added sugar; whole grain first)
- what to watch (FDA's 5/20 rule: 20% of a day's value or more in a serving is high), made stricter by the
  owner's own numbers: an LDL of 130 mg/dL or more (saturated fat), blood pressure averaging 130/80 or more
  (sodium), an A1c of 5.7% or more (added sugar and refined grains), kidney function below 60 (potassium: ask)
- the owner's allergies, foods to avoid and menu choices (vegetarian, no eggs), and their goal (calorie-dense
  foods when losing weight, protein for its calories)
- how processed it is (Open Food Facts' NOVA group and the additives on the label)
Nothing here goes to any AI model. Not medical advice.

The products the owner scans are kept (food_scans), with the product's public details, until the owner deletes
them; each is judged again when looked at, so a new lab result changes the verdict.
"""

from __future__ import annotations

import json
import re

from .memory import Memory
from .tools import ToolError, local_now

DV = {"protein_g": 50.0, "fiber_g": 28.0, "potassium_mg": 4700.0, "calcium_mg": 1300.0, "iron_mg": 18.0,
      "sodium_mg": 2300.0, "sat_fat_g": 20.0, "added_sugar_g": 50.0}  # FDA Daily Values, adults (21 CFR 101.9)
GIVES = {"protein_g": "protein", "fiber_g": "fibre", "potassium_mg": "potassium", "calcium_mg": "calcium",
         "iron_mg": "iron"}
CLAIMS = "FDA nutrient content claims: a good source is 10 to 19% of a day's value in a serving, high 20% or more"
FIVE_TWENTY = "FDA's daily values: 5% or less of a day's value in a serving is low, 20% or more is high"
LOW_NA = "FDA: 'low sodium' is 140 mg or less a serving"
LOW_SAT = "FDA: 'low saturated fat' is 1 g or less a serving"
NOVA = {1: "unprocessed or minimally processed", 2: "a processed culinary ingredient", 3: "processed",
        4: "ultra-processed"}
NOVA_WHY = "Open Food Facts' processing group (NOVA)"
WHOLE = re.compile(r"^\W*(?:organic\s+)?(?:100%\s+)?(?:whole|stone[- ]ground whole|sprouted whole|brown rice|"
                   r"oats?\b|rolled oats|steel[- ]cut|quinoa|millet|ragi|bulgur|barley|buckwheat)", re.I)
REFINED = re.compile(r"^\W*(?:organic\s+)?(?:enriched|unbleached enriched|bleached|white flour|wheat flour|"
                     r"refined|maida|white rice|semolina|rava|sooji|corn starch)", re.I)
VERDICTS = {"good": "Good for you", "sometimes": "Fine sometimes", "skip": "Better to skip"}
RANK = {"good": 2, "sometimes": 1, "skip": 0}
SCHEMA = """
CREATE TABLE IF NOT EXISTS food_scans (
    id INTEGER PRIMARY KEY,
    at TEXT NOT NULL,
    barcode TEXT NOT NULL DEFAULT '',
    name TEXT NOT NULL,
    brand TEXT NOT NULL DEFAULT '',
    food TEXT NOT NULL,              -- the product's public details (numbers per 100 g, serving, ingredients)
    verdict TEXT NOT NULL DEFAULT ''  -- the verdict when it was scanned
);
"""


def _ready(memory: Memory) -> None:
    memory.db.executescript(SCHEMA)


def serving(food: dict) -> tuple[float, str]:
    """The label serving when the pack gives one, else its first household portion, else 100 g."""
    portions = [p for p in food.get("portions") or [] if isinstance(p.get("grams"), (int, float)) and p["grams"] > 0]
    label = next((p for p in portions if "label serving" in str(p.get("label") or "")), None) or \
        (portions[0] if portions else None)
    if label:
        name = re.sub(r"\s*\(label serving\)\s*$", "", str(label.get("label") or ""), flags=re.I) or "a serving"
        if re.fullmatch(r"\s*[\d.,]+\s*(g|gr|grams?)\.?\s*(\([^)]*\))?\s*", name, flags=re.I) \
                or re.search(r"\(\s*[\d.,]+\s*g\s*\)\s*$", name):  # "126g", "2 Tbsp (32 g)": already says it
            return float(label["grams"]), (name if "(" in name else f"{label['grams']:g} g")
        return float(label["grams"]), f"{name} ({label['grams']:g} g)"
    return 100.0, "100 g"


def _me(memory: Memory) -> dict:
    """What about the owner changes the verdict, each with the words that say why."""
    from . import coach, dietplan, prevention  # noqa: PLC0415
    out: dict = {}
    me = dietplan._person(memory)
    cond = me["conditions"]
    ldl, a1c = prevention._latest_lab(memory, "ldl"), prevention._latest_lab(memory, "hba1c")
    if ldl and ldl["unit"] == "mg/dL" and ldl["value"] >= 130:
        out["ldl"] = f"your LDL of {ldl['value']:g} mg/dL ({ldl['at'][:10]})"
    elif "high_cholesterol" in cond:
        out["ldl"] = "your high cholesterol"
    sys_bp, dia_bp = prevention._recent(memory, "bp_systolic", 30), prevention._recent(memory, "bp_diastolic", 30)
    if (sys_bp and sys_bp["value"] >= 130) or (dia_bp and dia_bp["value"] >= 80):
        both = f"{sys_bp['value']:g}/{dia_bp['value']:g}" if sys_bp and dia_bp else \
            f"{(sys_bp or dia_bp)['value']:g}"
        out["bp"] = f"your blood pressure averaging {both}"
    elif "high_bp" in cond:
        out["bp"] = "your high blood pressure"
    if a1c and a1c["value"] >= 6.5 or "diabetes" in cond:
        out["sugar"] = f"your A1c of {a1c['value']:g}%" if a1c else "diabetes"
    elif a1c and a1c["value"] >= 5.7 or "prediabetes" in cond:
        out["sugar"] = f"your A1c of {a1c['value']:g}% (prediabetes range)" if a1c else "prediabetes"
    egfr = prevention._latest_lab(memory, "egfr")
    if not egfr:
        creat = prevention._latest_lab(memory, "creatinine")
        if creat and creat["unit"].lower() == "mg/dl" and me["age"] and me["sex"] in ("male", "female"):
            from .prevent import egfr_ckd_epi_2021  # noqa: PLC0415
            egfr = {"value": round(egfr_ckd_epi_2021(creat["value"], me["age"], me["sex"]))}
    if egfr and egfr["value"] < 60:
        out["kidney"] = f"kidney function (eGFR) of {egfr['value']:g}"
    diet = coach.plan(memory).get("diet") or {}
    from .dietcheck import _terms  # noqa: PLC0415
    out["allergies"], out["avoid"] = _terms(diet.get("allergies")), _terms(diet.get("avoid"))
    pr = dietplan.prefs(memory)
    out["eats"], out["goal"] = set(pr["eats"]), pr["goal"]
    return out


def judge(memory: Memory, food: dict) -> dict:
    """One serving of this food, for the owner: the verdict, what it gives, what to watch, and why."""
    from . import dietcheck as dc  # noqa: PLC0415
    name = " ".join(str(food.get("name") or "").split())[:200]
    if not name:
        raise ToolError("Which food?")
    per100 = food.get("per100") if isinstance(food.get("per100"), dict) else {}
    if not isinstance(per100.get("kcal"), (int, float)):
        raise ToolError("There are no nutrition numbers for this product, so it can't be judged: check the label.")
    grams, label = serving(food)
    amt = {k: float(v) * grams / 100 for k, v in per100.items()
           if isinstance(v, (int, float)) and not isinstance(v, bool) and v >= 0}
    pct = {k: round(100 * amt[k] / DV[k]) for k in DV if k in amt}
    ingredients = " ".join(str(food.get("ingredients") or "").split())[:2000]
    me = _me(memory)
    benefits: list[dict] = []
    concerns: list[dict] = []

    def gives(text, why):
        benefits.append({"text": text, "why": why})

    def watch(text, why, serious=False, mine=False):
        concerns.append({"text": text, "why": why, "serious": serious, "yours": mine})

    # allergies, foods to avoid, the menu's choices
    text = f"{name} {ingredients}"
    tags = {dc.OFF_TAGS.get(str(t).split(":")[-1]) for t in (food.get("allergens") or [])[:40]} - {None}
    stop = False
    for term in me["allergies"]:
        key, words = dc._words_for(term)
        found = dc._has(text, words, dc.NOT_DAIRY if key == "milk" else ())
        if found or (key and key in tags):
            watch(f"Has {found or key}: {term} is on your allergy list.", "Your plan, against the label", True, True)
            stop = True
    for term in me["avoid"]:
        key, words = dc._words_for(term)
        if (found := dc._has(text, words, dc.NOT_DAIRY if key == "milk" else ())):
            watch(f"Has {found}: {term} is on your list of foods to avoid.", "Your plan", True, True)
            stop = True
    if "nonveg" not in me["eats"] and (found := dc._has(text, dc.MEAT)):
        watch(f"Has {found}: your menu is set to vegetarian.", "Plan → Menu", True, True)
        stop = True
    elif "egg" not in me["eats"] and (found := dc._has(text, dc.ALLERGENS["egg"])):
        watch(f"Has {found}: your menu is set to no eggs.", "Plan → Menu", True, True)
        stop = True

    # what it gives
    for k, word in GIVES.items():
        if k in pct and pct[k] >= 10:
            unit = "mg" if k.endswith("_mg") else "g"
            gives(f"{'High in' if pct[k] >= 20 else 'A good source of'} {word}: {amt[k]:.0f} {unit}, "
                  f"{pct[k]}% of a day's value.", CLAIMS)
    if "sodium_mg" in amt and amt["sodium_mg"] <= 140:
        gives(f"Low in sodium: {amt['sodium_mg']:.0f} mg." + (" Good with " + me["bp"] + "." if me.get("bp") else ""),
              LOW_NA)
    if "sat_fat_g" in amt and amt["sat_fat_g"] <= 1 and amt.get("kcal", 0) > 20:
        gives(f"Low in saturated fat: {amt['sat_fat_g']:.1f} g." + (" Good with " + me["ldl"] + "." if me.get("ldl")
                                                                   else ""), LOW_SAT)
    if amt.get("added_sugar_g") == 0 and amt.get("kcal", 0) > 20:
        gives("No added sugar.", "The label")
    if ingredients and WHOLE.search(ingredients):
        gives("A whole grain comes first in the ingredients.", "Dietary Guidelines: make at least half your grains whole")
    nova = food.get("nova") if isinstance(food.get("nova"), int) and food.get("nova") in NOVA else None
    if nova == 1:
        gives("Unprocessed or minimally processed.", NOVA_WHY)
    if me["goal"] in ("lose", "gain") and amt.get("protein_g") and amt.get("kcal"):
        if amt["protein_g"] * 4 >= 0.3 * amt["kcal"] and amt["protein_g"] >= 5:
            gives(f"Protein-rich for its calories: {amt['protein_g']:.0f} g for {amt['kcal']:.0f} kcal.",
                  "Your goal (Plan → Menu): protein keeps you full and keeps muscle")

    # what to watch: FDA's 20%, stricter with the owner's own numbers
    if "sodium_mg" in pct:
        if pct["sodium_mg"] >= 20:
            watch(f"High in sodium: {amt['sodium_mg']:.0f} mg, {pct['sodium_mg']}% of a day's value.",
                  FIVE_TWENTY + (f"; with {me['bp']}, aim for 1,500 mg a day (American Heart Association)"
                                 if me.get("bp") else ""), True, bool(me.get("bp")))
        elif me.get("bp") and pct["sodium_mg"] >= 10:
            watch(f"{amt['sodium_mg']:.0f} mg sodium ({pct['sodium_mg']}% of a day's value): with {me['bp']}, "
                  "a day's 1,500 mg goes quickly.", "American Heart Association: 1,500 mg a day is the ideal limit",
                  True, True)
    if "sat_fat_g" in pct:
        if pct["sat_fat_g"] >= 20:
            watch(f"High in saturated fat: {amt['sat_fat_g']:.1f} g, {pct['sat_fat_g']}% of a day's value.",
                  FIVE_TWENTY + (f"; with {me['ldl']}, keep it to 5 to 6% of calories (American Heart Association)"
                                 if me.get("ldl") else ""), True, bool(me.get("ldl")))
        elif me.get("ldl") and pct["sat_fat_g"] >= 10:
            watch(f"{amt['sat_fat_g']:.1f} g saturated fat ({pct['sat_fat_g']}% of a day's value): with {me['ldl']}, "
                  "pick lower.", "American Heart Association: 5 to 6% of calories from saturated fat to lower LDL",
                  True, True)
    if "added_sugar_g" in amt:
        if pct["added_sugar_g"] >= 20:
            watch(f"High in added sugar: {amt['added_sugar_g']:.0f} g, {pct['added_sugar_g']}% of a day's value.",
                  FIVE_TWENTY, True, bool(me.get("sugar")))
        elif me.get("sugar") and amt["added_sugar_g"] >= 5:
            watch(f"{amt['added_sugar_g']:.0f} g added sugar: with {me['sugar']}, as little as you can.",
                  dc.ADA, True, True)
    elif amt.get("sugar_g", 0) >= 10:
        watch(f"{amt['sugar_g']:.0f} g sugar a serving (the label here doesn't say how much is added).",
              FIVE_TWENTY, False, bool(me.get("sugar")))
    if me.get("sugar") and ingredients and REFINED.search(ingredients) and not WHOLE.search(ingredients):
        watch(f"Refined flour or grain comes first: with {me['sugar']}, whole grains keep blood sugar steadier.",
              dc.ADA, False, True)
    if me.get("kidney") and pct.get("potassium_mg", 0) >= 20:
        watch(f"High in potassium ({amt['potassium_mg']:.0f} mg): with {me['kidney']}, ask your doctor whether you "
              "need to limit it.", "KDIGO: potassium advice depends on your own blood tests", False, True)
    if me["goal"] == "lose" and per100.get("kcal", 0) >= 400:
        watch(f"Calorie-dense: {per100['kcal']:.0f} kcal per 100 g ({amt['kcal']:.0f} a serving). Easy to eat more "
              "than you mean to.", "Your goal: lose slowly (Plan → Menu)", False, True)
    additives = [str(a).split(":")[-1].upper() for a in (food.get("additives") or [])[:30] if str(a).strip()]
    if nova == 4:
        watch("Ultra-processed (NOVA group 4)" + (f", with {len(additives)} additive{'s' if len(additives) != 1 else ''}"
                                                   f" ({', '.join(additives[:5])})" if additives else "") + ".",
              NOVA_WHY + ": eat mostly unprocessed and minimally processed foods")

    serious = sum(1 for c in concerns if c["serious"])
    verdict = "skip" if stop or serious >= 2 else "sometimes" if concerns else "good"
    headline = VERDICTS[verdict]
    if stop:
        headline += ": " + next(c["text"].split(": ", 1)[1] for c in concerns if c["yours"] and c["serious"])
    elif verdict == "good" and not benefits:
        headline += ": nothing in it works against your plan"
    score = RANK[verdict] * 10 - serious * 2 - (len(concerns) - serious) + 0.5 * len(benefits)
    return {"verdict": verdict, "headline": headline, "name": name, "brand": str(food.get("brand") or "")[:60],
            "barcode": str(food.get("barcode") or "")[:20], "serving": {"grams": round(grams, 1), "label": label},
            "per_serving": {k: round(v, 1) for k, v in amt.items()} | {"kcal": round(amt.get("kcal", 0))},
            "pct": pct, "benefits": benefits, "concerns": concerns,
            "processing": {"nova": nova, "nova_says": NOVA.get(nova, ""), "additives": additives[:12]},
            "used": [v for k, v in me.items() if k in ("ldl", "bp", "sugar", "kidney") and v],
            "score": round(score, 1),
            "note": (str(food["energy"]) + " " if food.get("energy") else "") +
                    "Worked out by Buddy's code from the label and your own numbers, per serving. No AI model sees "
                    "it. Not medical advice."}


# --- the products the owner scanned ------------------------------------------------------------------------------
KEEP = ("id", "name", "kind", "brand", "per100", "portions", "barcode", "ingredients", "allergens", "nova", "additives",
        "named", "energy")


def keep(memory: Memory, food: dict, verdict: str) -> int:
    """Keep a scanned product (one row per product: scanning again moves it to the top)."""
    _ready(memory)
    slim = {k: food.get(k) for k in KEEP if food.get(k) not in (None, "", [])}
    code = str(food.get("barcode") or "")[:20]
    name = " ".join(str(food.get("name") or "").split())[:200]
    if code:
        memory.db.execute("DELETE FROM food_scans WHERE barcode = ?", (code,))
    else:
        memory.db.execute("DELETE FROM food_scans WHERE barcode = '' AND lower(name) = lower(?)", (name,))
    cur = memory.db.execute("INSERT INTO food_scans (at, barcode, name, brand, food, verdict) VALUES (?, ?, ?, ?, ?, ?)",
                            (local_now().isoformat(timespec="seconds"), code, name, str(food.get("brand") or "")[:60],
                             json.dumps(slim)[:20000], verdict))
    memory.db.commit()
    return int(cur.lastrowid)


def named(memory: Memory, key: str) -> dict | None:
    """The food the owner picked for a barcode no list had, or for an item on a store's own label (foodfacts.scanned),
    from their checked products: deleting it there forgets the name."""
    _ready(memory)
    for r in memory.db.execute("SELECT food FROM food_scans WHERE barcode = ? ORDER BY at DESC, id DESC",
                               (str(key or "")[:20],)):
        food = json.loads(r["food"])
        if food.get("named"):
            return food
    return None


def scans(memory: Memory, limit: int = 60) -> list[dict]:
    """The products the owner scanned, newest first, each judged again with today's numbers."""
    _ready(memory)
    out = []
    for r in memory.db.execute("SELECT * FROM food_scans ORDER BY at DESC, id DESC LIMIT ?", (limit,)):
        food = json.loads(r["food"])
        try:
            got = judge(memory, food)
        except ToolError:
            continue
        out.append(got | {"id": r["id"], "at": r["at"], "food": food, "was": r["verdict"]})
    return out


def compare(memory: Memory, ids: list[int]) -> dict:
    """Two or more scanned products side by side, the better one for the owner first."""
    picked = [s for s in scans(memory, 500) if s["id"] in set(ids)]
    if len(picked) < 2:
        raise ToolError("Pick two or more scanned products to compare.")
    picked.sort(key=lambda s: -s["score"])
    best = picked[0]
    tie = picked[1]["score"] == best["score"]
    if tie:
        return {"products": picked, "better": None, "why": "Too close to call: pick by taste or price."}
    serious, good = sum(1 for c in best["concerns"] if c["serious"]), len(best["benefits"])
    watch = "nothing serious to watch" if not serious else f"{serious} thing{'s' if serious != 1 else ''} to watch"
    return {"products": picked, "better": best["id"],
            "why": f"{best['name']} is the better pick for you: {best['headline'].split(':')[0].lower()}, {watch}, "
                   f"{good} good point{'s' if good != 1 else ''} a serving."}


def forget(memory: Memory, scan_id: int | None = None) -> int:
    _ready(memory)
    if scan_id:
        n = memory.db.execute("DELETE FROM food_scans WHERE id = ?", (int(scan_id),)).rowcount
    else:
        n = memory.db.execute("DELETE FROM food_scans").rowcount
    memory.db.commit()
    return n
