"""Food, supplement and medicine facts from US government sources, looked up when the owner asks on the Health page.

- **Foods: USDA FoodData Central** (api.nal.usda.gov, a free api.data.gov key in BUDDY_USDA_KEY). Basic foods,
  survey foods (with household portions such as "1 cup") and branded products, each with calories, protein, carbs,
  fat, fibre, sugar and sodium per 100 g. The page works out a portion by code and can add it to today's meals.
- **Supplements: the NIH Office of Dietary Supplements' Label Database (DSLD)** (api.ods.od.nih.gov, no key): a
  product's label, ingredient by ingredient, per serving, with each ingredient's daily amount at the label's most
  servings a day set against the adult Tolerable Upper Intake Level (the National Academies' limits, as ODS gives
  them), and a link to ODS's fact sheet for it.
- **Medicines: openFDA's drug labels and recalls**, through health_data.drug and health_data.recalls.
- **Packs scanned on the phone**: the phone reads the barcode itself (on-device; no photo leaves it) and only the
  number comes here, to be looked up in USDA's branded foods and in Open Food Facts (Open Database License). A barcode
  neither list has, and a store's own label (meat, deli, bakery or produce weighed or packed in the store, whose number
  means something only at that store), asks the owner what it is; the food they pick is remembered for that barcode
  (or that store item) among their checked products, so the next scan needs no lookup at all (`scanned`).

Only the words the owner types go out (a food or product name; for a label, the database's own number); nothing
about the owner. Public data, so no AI model is involved and nothing is kept but a day's cache of the answers.
Not medical advice.
"""

import re
import time
import urllib.parse

from . import gateway
from .memory import Memory
from .tools import ToolError

USDA, DSLD = "usda_fdc", "nih_dsld"
KEY_SETTING = "BUDDY_USDA_KEY"
CACHE: dict[str, tuple[float, object]] = {}
CACHE_SECONDS, CACHE_MAX = 86400, 300
NUTRIENTS = {  # FoodData Central nutrient id -> our name (per 100 g in search results)
    1008: "kcal", 2047: "kcal", 2048: "kcal", 1003: "protein_g", 1005: "carbs_g", 1004: "fat_g", 1079: "fiber_g",
    2000: "sugar_g", 1093: "sodium_mg", 1258: "sat_fat_g", 1235: "added_sugar_g",
    1087: "calcium_mg", 1089: "iron_mg", 1092: "potassium_mg",  # for the grocery verdict (foodverdict.py)
}
KINDS = {"Foundation": "USDA basic food", "SR Legacy": "USDA basic food", "Survey (FNDDS)": "As people eat it",
         "Branded": "Branded product"}
UPPER = {  # adult (19+) Tolerable Upper Intake Levels from supplements, as NIH ODS lists them: name -> (amount, unit)
    "vitamin d": (100, "mcg", "4,000 IU (100 mcg)"), "vitamin c": (2000, "mg", "2,000 mg"),
    "vitamin e": (1000, "mg", "1,000 mg"), "vitamin b6": (100, "mg", "100 mg"),
    "niacin": (35, "mg", "35 mg (from supplements)"), "calcium": (2500, "mg", "2,500 mg (2,000 mg from age 51)"),
    "iron": (45, "mg", "45 mg"), "zinc": (40, "mg", "40 mg"),
    "magnesium": (350, "mg", "350 mg (from supplements)"), "selenium": (400, "mcg", "400 mcg"),
    "iodine": (1100, "mcg", "1,100 mcg"), "copper": (10000, "mcg", "10 mg"), "manganese": (11, "mg", "11 mg"),
    "choline": (3500, "mg", "3,500 mg"),
}
FACT_SHEETS = {  # ingredient words -> the ODS consumer fact sheet's name
    "vitamin d": "VitaminD", "vitamin b12": "VitaminB12", "vitamin c": "VitaminC", "vitamin a": "VitaminA",
    "vitamin e": "VitaminE", "vitamin k": "VitaminK", "vitamin b6": "VitaminB6", "folate": "Folate",
    "folic acid": "Folate", "calcium": "Calcium", "iron": "Iron", "magnesium": "Magnesium", "zinc": "Zinc",
    "potassium": "Potassium", "iodine": "Iodine", "selenium": "Selenium", "copper": "Copper",
    "manganese": "Manganese", "chromium": "Chromium", "niacin": "Niacin", "riboflavin": "Riboflavin",
    "thiamin": "Thiamin", "biotin": "Biotin", "choline": "Choline", "omega-3": "Omega3FattyAcids",
    "fish oil": "Omega3FattyAcids", "dha": "Omega3FattyAcids", "epa": "Omega3FattyAcids", "probiotic": "Probiotics",
}


def _words(text: str, what: str) -> str:
    q = " ".join(re.sub(r"[^\w\s'&,.-]", " ", str(text or "")).split())[:80]
    if len(q) < 2:
        raise ToolError(f"Type a {what} to look up.")
    return q


def _cached(key: str, get):
    hit = CACHE.get(key)
    if hit and time.time() - hit[0] < CACHE_SECONDS:
        return hit[1]
    value = get()
    if len(CACHE) >= CACHE_MAX:
        CACHE.pop(next(iter(CACHE)))
    CACHE[key] = (time.time(), value)
    return value


def set_up(env=None) -> bool:
    return bool(gateway._secret(env, KEY_SETTING))


# --- foods

def _food(f: dict) -> dict:
    per100: dict[str, float] = {}
    for n in f.get("foodNutrients") or []:
        name, value = NUTRIENTS.get(n.get("nutrientId")), n.get("value")
        if name and isinstance(value, (int, float)) and name not in per100:
            if name == "kcal" and str(n.get("unitName") or "").upper() not in ("KCAL", ""):
                continue
            per100[name] = round(float(value), 1)
    portions = []
    for m in f.get("foodMeasures") or []:
        label, grams = str(m.get("disseminationText") or "").strip(), m.get("gramWeight")
        if label and isinstance(grams, (int, float)) and grams > 0 and "not specified" not in label.lower():
            portions.append({"label": label[:60], "grams": round(float(grams), 1)})
    if f.get("dataType") == "Branded" and isinstance(f.get("servingSize"), (int, float)) \
            and str(f.get("servingSizeUnit") or "").lower() in ("g", "grm"):
        label = str(f.get("householdServingFullText") or "").strip() or "1 serving"
        portions.insert(0, {"label": f"{label} (label serving)"[:60], "grams": round(float(f["servingSize"]), 1)})
    brand = " ".join(str(f.get("brandName") or f.get("brandOwner") or "").split())
    return {"id": f.get("fdcId"), "name": " ".join(str(f.get("description") or "").split()).capitalize()[:120],
            "kind": KINDS.get(f.get("dataType"), f.get("dataType") or ""), "brand": brand[:60], "per100": per100,
            "portions": portions[:8], "ingredients": " ".join(str(f.get("ingredients") or "").split())[:1500]}


def foods(memory: Memory, text: str, env=None, fetch=None) -> list[dict]:
    """Foods matching the words, the basic and survey foods first, then branded products."""
    q = _words(text, "food")
    if not set_up(env):
        raise ToolError(f"Food facts need a free key from api.data.gov: sudo bash /opt/buddy/deploy/set.sh {KEY_SETTING}")
    path = "/fdc/v1/foods/search?" + urllib.parse.urlencode(
        {"query": q, "pageSize": 15, "dataType": "Foundation,SR Legacy,Survey (FNDDS),Branded"})
    got = _cached(f"fdc:{q.lower()}", lambda: gateway.call(memory, USDA, path, env=env, fetch=fetch,
                                                         purpose="food lookup"))
    out = [_food(f) for f in (got or {}).get("foods") or [] if f.get("fdcId")]
    order = {"USDA basic food": 0, "As people eat it": 1, "Branded product": 2}
    return sorted((f for f in out if f["per100"]), key=lambda f: order.get(f["kind"], 3))


def gtin_ok(code: str) -> bool:
    """A barcode as printed on packs (EAN-8, UPC-A, EAN-13, GTIN-14): digits with a valid check digit."""
    if not re.fullmatch(r"\d{8}|\d{12,14}", code or ""):
        return False
    digits = [int(c) for c in code]
    total = sum(d * (3 if i % 2 == 0 else 1) for i, d in enumerate(reversed(digits[:-1])))
    return (10 - total % 10) % 10 == digits[-1]


OFF_FIELDS = {"energy-kcal_100g": "kcal", "proteins_100g": "protein_g", "carbohydrates_100g": "carbs_g",
              "fat_100g": "fat_g", "fiber_100g": "fiber_g", "sugars_100g": "sugar_g", "saturated-fat_100g": "sat_fat_g"}


def upce_to_upca(code: str) -> str:
    """A short UPC-E (8 digits, small US packs) written out as the 12-digit UPC-A it stands for ('' if it isn't one)."""
    if not re.fullmatch(r"[01]\d{7}", code or ""):
        return ""
    ns, d, check = code[0], code[1:7], code[7]
    last = d[5]
    if last in "012":
        body = d[0:2] + last + "0000" + d[2:5]
    elif last == "3":
        body = d[0:3] + "00000" + d[3:5]
    elif last == "4":
        body = d[0:4] + "00000" + d[4]
    else:
        body = d[0:5] + "0000" + last
    upca = ns + body + check
    return upca if gtin_ok(upca) else ""


def normal_code(code: str) -> str:
    """The digits of a barcode as the phone read it (a short UPC-E written out as its UPC-A), checked."""
    code = re.sub(r"\D", "", str(code or ""))
    if len(code) == 8 and not gtin_ok(code):
        code = upce_to_upca(code) or code  # a UPC-E: looked up as its UPC-A
    if not gtin_ok(code):
        raise ToolError("That isn't a barcode from a food pack: scan it again, or type the numbers under it.")
    return code


def in_store(code: str) -> str:
    """A label the store printed itself, for meat, fish, deli, cheese, bakery or produce weighed or packed there: the
    item it stands for, without the price or weight that follow ('' for a maker's barcode). In the US and Canada these
    are 12-digit UPC-As starting with 2 (13 digits, 02…, as some phones read them); elsewhere EAN-13s starting 20 to
    29. No food list has them: the number means something only at that store."""
    if len(code) == 13 and code.startswith("02"):
        code = code[1:]
    if len(code) in (12, 13) and code[0] == "2":
        return "store-" + code[:6 if len(code) == 12 else 7]
    return ""


def scanned(memory: Memory, code: str, env=None, fetch=None) -> dict:
    """A barcode the phone read. What the owner named it after an earlier scan (the same barcode, or the same item
    on a store's own label) is looked up nowhere; a store's own label goes nowhere either, since no list has it; any
    other barcode goes to USDA and Open Food Facts by the number. `key` is what a name the owner gives it is kept
    under; `label` is "named", "store" or ""."""
    from . import foodverdict  # noqa: PLC0415
    code = normal_code(code)
    store = in_store(code)
    key = store or (code[1:] if len(code) == 13 and code.startswith("0") else code)
    mine = foodverdict.named(memory, key)
    if mine:
        return {"foods": [mine], "key": key, "label": "named", "source": "What you named this barcode before"}
    if store:
        return {"foods": [], "key": key, "label": "store", "source": "A store's own label: looked up nowhere"}
    return {"foods": barcode(memory, code, env=env, fetch=fetch), "key": key, "label": "",
            "source": "USDA FoodData Central and Open Food Facts, by the barcode only"}


def _off(memory: Memory, code: str, env=None, fetch=None) -> dict:
    """Open Food Facts' page for a barcode; {} when it has none (it answers HTTP 404 then)."""
    try:
        return gateway.call(memory, "openfoodfacts", f"/api/v2/product/{code}.json", env=env, fetch=fetch,
                            purpose="food lookup") or {}
    except gateway.ConnectError as e:
        if e.status == 404:
            return {}
        raise


def barcode(memory: Memory, code: str, env=None, fetch=None) -> list[dict]:
    """A pack scanned on the phone: only its barcode number goes out, to USDA's branded foods (when the owner has
    the key) and to Open Food Facts."""
    code = normal_code(code)
    out = []
    if set_up(env):
        got = _cached(f"fdc:gtin:{code}", lambda: gateway.call(memory, USDA, "/fdc/v1/foods/search?" + urllib.parse.urlencode(
            {"query": code, "dataType": "Branded", "pageSize": 5}), env=env, fetch=fetch, purpose="food lookup"))
        for f in (got or {}).get("foods") or []:
            if str(f.get("gtinUpc") or "").lstrip("0") == code.lstrip("0") and f.get("fdcId"):
                food = _food(f)
                if food["per100"] and _energy(food):
                    out.append(food | {"kind": "Branded product (USDA)", "barcode": code})
    got = _cached(f"off:{code}", lambda: _off(memory, code, env=env, fetch=fetch))
    p = (got or {}).get("product") or {}
    n = p.get("nutriments") or {}
    per100 = {ours: round(float(n[theirs]), 2) for theirs, ours in OFF_FIELDS.items()
              if isinstance(n.get(theirs), (int, float))}
    for theirs, ours in (("sodium_100g", "sodium_mg"), ("potassium_100g", "potassium_mg"),
                         ("calcium_100g", "calcium_mg"), ("iron_100g", "iron_mg")):  # Open Food Facts gives grams
        if isinstance(n.get(theirs), (int, float)):
            per100[ours] = round(float(n[theirs]) * 1000, 1)
    if isinstance(n.get("added-sugars_100g"), (int, float)):
        per100["added_sugar_g"] = round(float(n["added-sugars_100g"]), 2)
    if p.get("product_name") and per100 and _energy(off := {"per100": per100}):
        portions = []
        serving = p.get("serving_quantity")
        if isinstance(serving, (int, float)) and 0 < serving < 3000 and str(p.get("serving_quantity_unit") or "g") == "g":
            portions.append({"label": f"{p.get('serving_size') or 'a serving'} (label serving)"[:60],
                             "grams": round(float(serving), 1)})
        out.append({"id": f"off:{code}", "name": " ".join(str(p["product_name"]).split())[:120],
                    "kind": "Branded product (Open Food Facts)",
                    "brand": " ".join(str(p.get("brands") or "").split(","))[:60].strip(), "per100": per100,
                    "portions": portions, "barcode": code,
                    "ingredients": " ".join(str(p.get("ingredients_text_en") or p.get("ingredients_text") or "")
                                            .split())[:1500],
                    "allergens": [str(t)[:40] for t in (p.get("allergens_tags") or [])[:30]],
                    "nova": p.get("nova_group") if p.get("nova_group") in (1, 2, 3, 4) else None,
                    **({"energy": off["energy"]} if off.get("energy") else {}),
                    "additives": [str(t)[:40] for t in (p.get("additives_tags") or [])[:30]]})
    return out


def _energy(food: dict) -> bool:
    """A pack whose list gives no calories, or 0, though it gives carbs, protein or fat (crowd-filled entries do: a
    banana at 0 kcal): its calories are worked out from them at 4, 4 and 9 kcal a gram, and said so. False when there's
    nothing to count from, so the pack is treated as one no list has."""
    per100 = food.get("per100") or {}
    if per100.get("kcal"):
        return True
    made = 4 * float(per100.get("carbs_g") or 0) + 4 * float(per100.get("protein_g") or 0) + 9 * float(per100.get("fat_g") or 0)
    if made < 1:
        return False
    per100["kcal"] = round(made, 1)
    food["energy"] = "Calories worked out from the label's carbs, protein and fat (the list gave none)."
    return True


def portion(food: dict, grams: float) -> dict:
    """A food's numbers for an amount in grams (calories and protein rounded for the meal log)."""
    if not 0 < grams <= 3000:
        raise ToolError("An amount between 1 and 3,000 grams.")
    out = {k: round(v * grams / 100, 1) for k, v in food["per100"].items()}
    if "kcal" in out:
        out["kcal"] = round(out["kcal"])
    return out


# --- supplements

def supplements(memory: Memory, text: str, env=None, fetch=None) -> list[dict]:
    q = _words(text, "supplement")
    got = _cached(f"dsld:{q.lower()}", lambda: gateway.call(
        memory, DSLD, "/dsld/v9/search-filter?" + urllib.parse.urlencode({"q": q, "size": 12}), env=env, fetch=fetch,
        purpose="supplement lookup"))
    out = []
    for h in (got or {}).get("hits") or []:
        s = h.get("_source") or {}
        out.append({"id": str(h.get("_id") or ""), "name": " ".join(str(s.get("fullName") or "").split())[:120],
                    "brand": " ".join(str(s.get("brandName") or "").split())[:60],
                    "off_market": bool(s.get("offMarket")),
                    "ingredients": list(dict.fromkeys(i.get("name") for i in s.get("allIngredients") or []
                                                      if i.get("name")))[:8]})
    return sorted((o for o in out if o["id"].isdigit() and o["name"]), key=lambda o: o["off_market"])  # sold now first


def _unit(unit: str) -> str:
    u = str(unit or "").lower().replace("µ", "mc").replace(" ", "")
    return {"ug": "mcg", "mcg": "mcg", "mg": "mg", "g": "g", "iu": "iu"}.get(u.split("dfe")[0].split("rae")[0], u)


def _in(amount: float, unit: str, want: str, name: str) -> float | None:
    """An amount in the limit's unit, or None when it can't be converted honestly."""
    u = _unit(unit)
    if u == want:
        return amount
    if u == "g" and want == "mg":
        return amount * 1000
    if u == "mg" and want == "mcg":
        return amount * 1000
    if u == "iu" and want == "mcg" and name == "vitamin d":
        return amount / 40  # 1 mcg of vitamin D is 40 IU
    return None


def _sheet(name: str) -> str:
    low = name.lower()
    for words, sheet in FACT_SHEETS.items():
        if words in low:
            return f"https://ods.od.nih.gov/factsheets/{sheet}-Consumer/"
    return ""


def label(memory: Memory, dsld_id: str, env=None, fetch=None) -> dict:
    """One product's label: each ingredient per serving, its share of the adult upper limit, and fact sheets."""
    if not re.fullmatch(r"\d{1,9}", str(dsld_id or "")):
        raise ToolError("That isn't a label number from the database.")
    d = _cached(f"dsld:label:{dsld_id}", lambda: gateway.call(memory, DSLD, f"/dsld/v9/label/{dsld_id}", env=env,
                                                              fetch=fetch, purpose="supplement lookup")) or {}
    serving = (d.get("servingSizes") or [{}])[0]
    most = serving.get("maxDailyServings") or 1
    per = f"{serving.get('maxQuantity') or serving.get('minQuantity') or 1:g} {serving.get('unit') or 'serving'}"
    rows = []
    for r in d.get("ingredientRows") or []:
        q = next((x for x in r.get("quantity") or [] if x.get("servingSizeOrder") in (1, None)), None)
        name = " ".join(str(r.get("name") or "").split())
        if not q or not name or not isinstance(q.get("quantity"), (int, float)) or name.lower().startswith("calories"):
            continue
        amount, unit = float(q["quantity"]), str(q.get("unit") or "")
        dv = next((g.get("percent") for g in q.get("dailyValueTargetGroup") or [] if g.get("percent") is not None),
                  None)
        row = {"name": name, "amount": amount, "unit": unit, "daily_value_pct": dv, "fact_sheet": _sheet(name)}
        limit = next((v for k, v in UPPER.items() if name.lower().startswith(k)), None)
        if limit:
            got = _in(amount * most, unit, limit[1], next(k for k in UPPER if name.lower().startswith(k)))
            if got is not None:
                row["upper_pct"] = round(100 * got / limit[0])
                row["upper"] = limit[2]
        rows.append(row)
    over = [r["name"] for r in rows if r.get("upper_pct", 0) > 100]
    return {"id": dsld_id, "name": " ".join(str(d.get("fullName") or "").split()),
            "brand": " ".join(str(d.get("brandName") or "").split()), "serving": per, "most_servings": most,
            "rows": rows, "off_market": bool(d.get("offMarket")),
            "page": f"https://dsld.od.nih.gov/label/{dsld_id}",
            "warning": (f"At the label's most servings a day, {', '.join(over)} would be above the adult upper limit "
                        "from supplements alone. Ask your doctor before taking that much." if over else ""),
            "note": "From the NIH Office of Dietary Supplements' Label Database: what the label says, not a test of "
                    "the product. Upper limits are the National Academies' adult limits, as ODS gives them; food "
                    "adds to them. Not medical advice."}


# --- medicines (openFDA, through health_data)

def medicine(memory: Memory, text: str, fetch=None) -> dict:
    from . import health_data  # noqa: PLC0415
    q = _words(text, "medicine")
    got = _cached(f"fda:{q.lower()}", lambda: {"label": health_data.drug(memory, q, fetch=fetch),
                                              "recalls": health_data.recalls(memory, q, fetch=fetch)})
    return {**got, "note": "From openFDA, the US FDA's public drug labels and recalls; not endorsed by the FDA. "
                           "Not medical advice: ask your pharmacist or doctor about your own medicines."}


def check(memory, env=None, fetch=None) -> None:
    """Buddy Connect's test for FoodData Central: one food."""
    gateway.call(memory, USDA, "/fdc/v1/foods/search?query=apple&pageSize=1", env=env, fetch=fetch, purpose="health")
