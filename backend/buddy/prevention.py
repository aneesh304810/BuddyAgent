"""Checkups: which screenings apply to the owner, what they usually cost, and where their numbers sit.

From a few answers the owner gives on the Health page (birth year, sex, ancestry, where they were born or lived,
smoking, family history, known conditions, height and waist), their lab results and their devices' readings, plain
code works out:

- **Screenings for you:** the US Preventive Services Task Force's A and B adult recommendations (and its C for
  prostate, as a decision), each with why it applies, how often, when it was last done, and a coverage mark. Ancestry
  changes the rules where the guidelines say so: the task force screens Asian Americans for diabetes from a BMI of 23
  (not 25), the ADA lists Asian, Black, Hispanic, Native American and Pacific Islander ancestry as a risk factor, the
  2026 ACC/AHA dyslipidemia guideline counts higher-risk ancestry (South Asian, Filipino) as a risk enhancer, and the WHO's Asian BMI action points
  are 23 and 27.5. Being born in, or having lived in, a country where hepatitis B or TB is common (most of Asia,
  India and China included) brings those two screenings in.
- **Worth asking about:** lipoprotein(a) once, a glucose sensor (CGM) and genetic testing: whether either is worth
  raising with a doctor, by fixed rules on the owner's answers and results, and when it isn't needed.
- **Compared with others:** where the owner's BMI, waist, blood pressure, A1c, cholesterol and glucose sit among US
  adults of their sex and age, and among those of Asian ancestry (NHANES, nhanesref.py).

Coverage marks are general: under the ACA most non-grandfathered private plans cover A and B screenings in-network
with no cost sharing (upheld by the Supreme Court in Kennedy v. Braidwood, June 2025); Medicare and Medicaid have their
own lists, and the owner's plan type (which they pick; no insurer, member number or money) adjusts the wording.

Buddy never orders a test, books a visit or contacts a doctor. Not medical advice. No AI model is given any of it
(the Privacy Gateway's health rule): the answers are kept in the owner's database until they change or delete them.
"""

import json
import re

from .memory import Memory
from .tools import ToolError, local_now

PROFILE_KEY, DONE_KEY = "prevention:profile", "prevention:done"
CHECKED = "2026-11-26"  # the day the rules below were checked against the task force's pages
USPSTF = "https://www.uspreventiveservicestaskforce.org/uspstf/recommendation/"

ANCESTRY = [  # key, what the owner sees, the group the guidelines use
    ("indian_kerala", "Indian: Kerala", "south_asian"), ("indian_south", "Indian: other South India", "south_asian"),
    ("indian_other", "Indian: elsewhere in India", "south_asian"), ("pakistani", "Pakistani", "south_asian"),
    ("bangladeshi", "Bangladeshi", "south_asian"), ("sri_lankan", "Sri Lankan", "south_asian"),
    ("nepali", "Nepali", "south_asian"), ("chinese", "Chinese", "east_asian"), ("japanese", "Japanese", "east_asian"),
    ("korean", "Korean", "east_asian"), ("filipino", "Filipino", "southeast_asian"),
    ("vietnamese", "Vietnamese", "southeast_asian"), ("asian_other", "Other Asian", "asian"),
    ("black", "Black or African American", "black"), ("hispanic", "Hispanic or Latino", "hispanic"),
    ("white", "White", "white"), ("middle_eastern", "Middle Eastern or North African", "middle_eastern"),
    ("native", "American Indian or Alaska Native", "native"), ("pacific", "Native Hawaiian or Pacific Islander", "pacific"),
    ("ashkenazi", "Ashkenazi Jewish", "ashkenazi"),
]
GROUP = {k: g for k, _, g in ANCESTRY}
ASIAN = {"south_asian", "east_asian", "southeast_asian", "asian"}
DIABETES_RISK_ANCESTRY = ASIAN | {"black", "hispanic", "native", "pacific"}  # the ADA's higher-risk groups
FAMILY = {  # a parent, brother, sister or child, unless it says otherwise
    "diabetes": "Diabetes",
    "heart_early": "Heart attack or stroke young (a man before 55, a woman before 65)",
    "colon_cancer": "Colon or rectal cancer",
    "colon_cancer_young": "Colon, rectal or uterine cancer before 50",
    "breast_ovarian": "Breast, ovarian, tubal or peritoneal cancer (any relative)",
    "prostate_cancer": "Prostate cancer",
    "very_high_cholesterol": "Very high cholesterol",
}
CONDITIONS = {"diabetes": "Diabetes", "prediabetes": "Prediabetes", "high_bp": "High blood pressure",
              "high_cholesterol": "High cholesterol", "heart_disease": "Heart disease or stroke",
              "pcos": "Polycystic ovary syndrome", "gestational_diabetes": "Diabetes in a past pregnancy"}
PLANS = {
    "private": "Through work or the marketplace (ACA)",
    "medicare": "Medicare",
    "medicaid": "Medicaid",
    "other": "Grandfathered, short-term, or none",
    "": "Not sure",
}
COVER = {
    "free": "$0 in-network on most plans (a task force A or B screening)",
    "free_ask": "Often $0 as screening; ask your plan first",
    "billed": "Usually billed like any test your doctor orders: your plan's usual cost applies",
    "visit": "Asked at a yearly checkup ($0 in-network on most plans)",
}


def _profile(memory: Memory) -> dict:
    try:
        got = json.loads(memory.get(PROFILE_KEY) or "{}")
    except ValueError:
        got = {}
    return got if isinstance(got, dict) else {}


def _done(memory: Memory) -> dict:
    try:
        got = json.loads(memory.get(DONE_KEY) or "{}")
    except ValueError:
        got = {}
    return got if isinstance(got, dict) else {}


def _number(v, low: float, high: float) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if low <= f <= high else None


def save_profile(memory: Memory, answers: dict) -> dict:
    """Keep the owner's answers, each checked against what it may be; anything else is dropped."""
    year = local_now().year
    birth = _number(answers.get("birth_year"), year - 110, year - 18)
    out = {
        "birth_year": int(birth) if birth else None,
        "sex": answers.get("sex") if answers.get("sex") in ("male", "female") else "",
        "ancestry": [a for a in (answers.get("ancestry") or []) if a in GROUP],
        "born_high": answers.get("born_high") if answers.get("born_high") in ("yes", "no") else "",
        "smoking": answers.get("smoking") if answers.get("smoking") in ("never", "former", "current") else "",
        "pack_years": _number(answers.get("pack_years"), 0, 200),
        "quit_year": int(q) if (q := _number(answers.get("quit_year"), year - 90, year)) else None,
        "family": [f for f in (answers.get("family") or []) if f in FAMILY],
        "conditions": [c for c in (answers.get("conditions") or []) if c in CONDITIONS],
        "postmenopausal": bool(answers.get("postmenopausal")) and answers.get("sex") == "female",
        "height_cm": _number(answers.get("height_cm"), 120, 230),
        "waist_cm": _number(answers.get("waist_cm"), 50, 200),
        "plan": answers.get("plan") if answers.get("plan") in PLANS else "",
        "bp_meds": bool(answers.get("bp_meds")),  # for the PREVENT risk estimate
        "statin": bool(answers.get("statin")),
    }
    memory.set(PROFILE_KEY, json.dumps(out))
    return out


def mark_done(memory: Memory, key: str, month: str, method: str = "") -> str:
    if key not in {i["key"] for i in _items({}, {})}:
        raise ToolError("That isn't one of the screenings.")
    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", str(month or "")):
        raise ToolError("Pick the month it was done.")
    if month > local_now().date().isoformat()[:7]:
        raise ToolError("That month hasn't come yet.")
    done = _done(memory)
    done[key] = {"month": month, "method": method if method in METHODS.get(key, {}) else ""}
    memory.set(DONE_KEY, json.dumps(done))
    return "Noted."


def unmark(memory: Memory, key: str) -> str:
    done = _done(memory)
    done.pop(key, None)
    memory.set(DONE_KEY, json.dumps(done))
    return "Cleared."


def forget(memory: Memory) -> str:
    memory.set(PROFILE_KEY, "")
    memory.set(DONE_KEY, "")
    return "Your answers and the dates you noted were deleted."


# --- what the owner's own data says: their age, BMI, latest lab values and home readings

def _age(p: dict) -> int | None:
    return local_now().year - p["birth_year"] if p.get("birth_year") else None


def _groups(p: dict) -> set:
    return {GROUP[a] for a in p.get("ancestry") or [] if a in GROUP}


def _latest_lab(memory: Memory, *tests: str) -> dict | None:
    """The newest kept result among these tests, in mg/dL or % (a mmol/L result converted)."""
    try:
        from . import labs  # noqa: PLC0415
        labs._ready(memory)
    except Exception:
        return None
    marks = ",".join("?" * len(tests))
    row = memory.db.execute(f"SELECT test, value, unit, collected FROM lab_results WHERE test IN ({marks}) "  # nosec B608
                            "ORDER BY collected DESC, id DESC LIMIT 1", tests).fetchone()
    if not row:
        return None
    value, unit = float(row["value"]), str(row["unit"] or "")
    if unit.lower().replace(" ", "") in ("mmol/l", "mmol/liter"):
        value = value * (18.0 if row["test"] == "glucose" else 38.67)
        unit = "mg/dL"
    return {"test": row["test"], "value": round(value, 1), "unit": unit, "at": row["collected"]}


def _recent(memory: Memory, measure: str, days: int = 30) -> dict | None:
    """The average of a device measure over its last `days` days held, and its newest day."""
    try:
        from .healthrecords import _ready  # noqa: PLC0415
        _ready(memory)
    except Exception:
        return None
    rows = memory.db.execute("SELECT value, at FROM health_records WHERE measure = ? ORDER BY at DESC LIMIT ?",
                             (measure, days)).fetchall()
    if not rows:
        return None
    return {"value": round(sum(float(r["value"]) for r in rows) / len(rows), 1), "at": rows[0]["at"][:10],
            "days": len(rows)}


def _weight(memory: Memory) -> float | None:
    w = _recent(memory, "weight", 7)
    if w:
        return w["value"]
    try:
        from . import oura  # noqa: PLC0415
        return _number(oura.profile(memory).get("weight"), 25, 350)
    except Exception:
        return None


def _bmi(memory: Memory, p: dict) -> float | None:
    w, h = _weight(memory), p.get("height_cm")
    return round(w / (h / 100) ** 2, 1) if w and h else None


def suggested(memory: Memory) -> dict:
    """What the Oura profile already says (age, sex, height), offered on the form; nothing is saved from it."""
    try:
        from . import oura  # noqa: PLC0415
        o = oura.profile(memory)
    except Exception:
        return {}
    out = {}
    if (age := _number(o.get("age"), 18, 110)) is not None:
        out["birth_year"] = local_now().year - int(age)
    if str(o.get("biological_sex") or "").lower() in ("male", "female"):
        out["sex"] = str(o["biological_sex"]).lower()
    if (h := _number(o.get("height"), 1.2, 2.3)) is not None:
        out["height_cm"] = round(h * 100)
    return out


# --- the screenings

METHODS = {
    "colorectal": {"colonoscopy": ("Colonoscopy", 120), "fit": ("Stool test (FIT)", 12),
                   "stool_dna": ("Stool DNA test (Cologuard)", 36), "ct": ("CT colonography", 60),
                   "sigmoidoscopy": ("Flexible sigmoidoscopy", 60)},
    "cervical": {"pap": ("Pap test", 36), "hpv": ("HPV test", 60), "cotest": ("Pap and HPV together", 60)},
}


def _items(p: dict, labs: dict) -> list[dict]:
    """Every screening that applies (all of them when p is empty, for the keys)."""
    every = not p
    age, sex, groups = _age(p), p.get("sex") or "", _groups(p)
    fam, cond = set(p.get("family") or []), set(p.get("conditions") or [])
    bmi = labs.get("bmi")
    asian = bool(groups & ASIAN)
    smoker = p.get("smoking") in ("current", "former")
    a = lambda lo, hi=200: every or (age is not None and lo <= age <= hi)  # noqa: E731
    items = []

    def add(key, title, test, often, why, grade, cover, url, months=None, once=False, lab=None, status=None):
        items.append({"key": key, "title": title, "test": test, "often": often, "why": [w for w in why if w],
                      "grade": grade, "cover": cover, "url": url, "months": months, "once": once, "lab": lab,
                      "status": status})

    if a(18):
        high = (age or 0) >= 40 or bool(bmi and bmi >= (23 if asian else 25)) or "black" in groups
        add("blood_pressure", "Blood pressure", "A reading at a visit (home readings confirm it)",
            "Every year" if high else "Every 3 to 5 years",
            ["Every adult 18 and over (task force A)."
             + (" Yearly from 40, or with a higher risk." if high else "")], "A", "free",
            USPSTF + "hypertension-in-adults-screening", months=12 if high else 36)

    if every or "diabetes" not in cond:
        cut = 23 if asian else 25
        why, cover, grade, applies = [], "free", "B", False
        if a(35, 70) and bmi and bmi >= 25:
            why.append(f"Age 35 to 70 with a BMI of 25 or more (yours: {bmi}) (task force B).")
            applies = True
        elif a(35, 70) and asian and bmi and bmi >= 23:
            why.append(f"Age 35 to 70 and of Asian ancestry, with a BMI of 23 or more (yours: {bmi}): the task force "
                       "screens Asian Americans from 23, not 25.")
            cover, applies = "free_ask", True
        if age is not None and age >= 35 and not applies:
            why.append("The ADA tests every adult from 35, every 3 years.")
            cover, grade, applies = "free_ask", "ADA", True
        risks = []
        if groups & DIABETES_RISK_ANCESTRY:
            risks.append("your ancestry")
        if "diabetes" in fam:
            risks.append("diabetes in the family")
        risks += [CONDITIONS[c].lower() for c in ("high_bp", "pcos", "gestational_diabetes") if c in cond]
        if age is not None and age < 35 and bmi and bmi >= cut and risks:
            why.append(f"Under 35, but a BMI of {cut} or more (yours: {bmi}) with {', '.join(risks)}: the ADA tests "
                       "now.")
            cover, grade, applies = "billed", "ADA", True
        if "indian_kerala" in (p.get("ancestry") or []):
            why.append("About 1 in 4 adults in Kerala had diabetes in India's ICMR-INDIAB study (Lancet Diabetes & "
                       "Endocrinology, 2023), against 11.4% across India.")
        pre = "prediabetes" in cond or bool(labs.get("a1c") and 5.7 <= labs["a1c"]["value"] < 6.5)
        if applies or every:
            add("diabetes", "Diabetes and prediabetes", "A1c or fasting glucose (a blood test)",
                "Every year (prediabetes range)" if pre else "Every 3 years while normal",
                why + (["In the prediabetes range, the ADA tests every year."] if pre else []),
                grade, cover, USPSTF + "screening-for-prediabetes-and-type-2-diabetes",
                months=12 if pre else 36, lab=("hba1c", "glucose"))

    if a(20):
        why = ["Adults have cholesterol checked from 20 (ACC/AHA); the task force uses it from 40 to 75 to decide on a "
               "statin. Buddy reminds you every 5 years, sooner if it was high."]
        if "south_asian" in groups or "filipino" in (p.get("ancestry") or []):
            why.append("Higher-risk ancestry (South Asian or Filipino) is a risk enhancer in the 2026 ACC/AHA "
                       "dyslipidemia guideline, outside the risk calculator (which has no ancestry in it): tell your "
                       "doctor when they work out your heart risk; it counts toward a statin when the risk is "
                       "borderline or middling.")
        if "heart_early" in fam:
            why.append("Early heart disease in the family is a risk enhancer too.")
        add("cholesterol", "Cholesterol (lipid panel)", "Total, LDL and HDL cholesterol, triglycerides",
            "Every 5 years, more often if high", why, "B" if a(40, 75) else "ACC/AHA",
            "free" if a(40, 75) else "free_ask", USPSTF + "statin-use-in-adults-preventive-medication", months=60,
            lab=("ldl", "cholesterol_total", "hdl"))

    if a(45, 75):
        why = ["Every adult 45 to 75 (task force A from 50, B from 45)."]
        if "colon_cancer" in fam or "colon_cancer_young" in fam:
            why.append("With colorectal cancer in the family, specialists start at 40 or 10 years before the "
                       "relative's age at diagnosis, by colonoscopy: ask your doctor.")
        add("colorectal", "Colorectal cancer", "Stool test each year, stool DNA every 3 years, or colonoscopy every 10",
            "By the test chosen", why + ["A colonoscopy after a positive stool test is part of the screening, so it is "
                                         "covered too."], "A", "free", USPSTF + "colorectal-cancer-screening",
            months=120)
    elif age is not None and 40 <= age < 45 and ("colon_cancer" in fam or "colon_cancer_young" in fam):
        add("colorectal", "Colorectal cancer", "Colonoscopy", "Ask your doctor",
            ["With colorectal cancer in the family, specialists often start at 40."], "ask", "billed",
            USPSTF + "colorectal-cancer-screening", months=60)

    if every or (a(50, 80) and smoker and (p.get("pack_years") or 0) >= 20
                 and (p.get("smoking") == "current" or (p.get("quit_year") and local_now().year - p["quit_year"] <= 15))):
        add("lung", "Lung cancer", "Low-dose CT scan", "Every year",
            ["Age 50 to 80, 20 pack-years or more, smoking now or quit within 15 years (task force B)."], "B", "free",
            USPSTF + "lung-cancer-screening", months=12)

    if every or (sex == "male" and a(65, 75) and smoker):
        add("aaa", "Abdominal aortic aneurysm", "One ultrasound", "Once",
            ["Men 65 to 75 who have ever smoked (task force B)."], "B", "free",
            USPSTF + "abdominal-aortic-aneurysm-screening", once=True)

    if every or (sex == "female" and a(40, 74)):
        add("breast", "Breast cancer", "Mammogram", "Every 2 years", ["Women 40 to 74 (task force B)."], "B", "free",
            USPSTF + "breast-cancer-screening", months=24)

    if every or (sex == "female" and a(21, 65)):
        add("cervical", "Cervical cancer", "Pap test every 3 years; from 30, an HPV test every 5 years instead",
            "By the test chosen", ["Women 21 to 65 (task force A)."], "A", "free",
            USPSTF + "cervical-cancer-screening", months=36)

    if every or (sex == "female" and ("breast_ovarian" in fam or "ashkenazi" in groups)):
        add("brca", "BRCA risk check", "A short family-history questionnaire; genetic counselling if it is positive",
            "Once, and when the family history changes",
            ["Women with breast, ovarian, tubal or peritoneal cancer in the family, or Ashkenazi Jewish ancestry "
             "(task force B)."], "B", "free",
            USPSTF + "brca-related-cancer-risk-assessment-genetic-counseling-and-genetic-testing", once=True)

    if every or (sex == "male" and a(55, 69)):
        why = ["Men 55 to 69: a choice to make with your doctor, weighing a small benefit against false alarms and "
               "treatment you may not need (task force C)."]
        if "prostate_cancer" in fam or "black" in groups:
            why.append("Prostate cancer in the family" + (" or Black ancestry" if "black" in groups else "")
                       + " raises the risk, which favours talking about it.")
        add("prostate", "Prostate cancer (PSA)", "PSA blood test", "Decide with your doctor", why, "C", "billed",
            USPSTF + "prostate-cancer-screening", months=24, lab=("psa",), status="decide")

    if every or (sex == "female" and (a(65) or p.get("postmenopausal"))):
        add("bone", "Osteoporosis", "Bone density scan (DXA)", "Ask how often",
            ["Women 65 and over, or after menopause with a higher fracture risk (task force B)."], "B", "free",
            USPSTF + "osteoporosis-screening")

    if a(18, 79):
        add("hep_c", "Hepatitis C", "One blood test", "Once", ["Every adult 18 to 79 (task force B)."], "B", "free",
            USPSTF + "hepatitis-c-screening", once=True)
    if a(15, 65):
        add("hiv", "HIV", "One blood test", "Once, more often with a risk", ["Everyone 15 to 65 (task force A)."], "A",
            "free", USPSTF + "human-immunodeficiency-virus-hiv-infection-screening", once=True)
    if every or p.get("born_high") == "yes":
        add("hep_b", "Hepatitis B", "One blood test", "Once",
            ["Born in, or lived in, a country where 2 or more in 100 carry hepatitis B, such as most of Asia "
             "(India and China included), Africa and the Pacific Islands (task force B)."], "B", "free",
            USPSTF + "hepatitis-b-virus-infection-screening", once=True)
        add("tb", "Latent tuberculosis", "One blood test (IGRA) or skin test", "Once",
            ["Born in, or lived in, a country where TB is common, India and China included (task force B)."], "B",
            "free", USPSTF + "latent-tuberculosis-infection-screening", once=True)

    if a(18):
        asks = ["depression", "alcohol and drug use", "smoking"] + (["anxiety"] if every or (age or 0) <= 64 else [])
        if sex == "female" and (age or 0) <= 50:
            asks.append("safety at home")
        add("checkup_questions", "Questions at your checkup", "A few questions: " + ", ".join(asks),
            "Every year", ["Task force B for adults; a short questionnaire at your visit."], "B", "visit",
            USPSTF + "screening-depression-suicide-risk-adults", months=12)
    if every or p.get("smoking") == "current":
        add("quit", "Help to stop smoking", "Counselling and medicines", "Now",
            ["Everyone who smokes (task force A); covered, including the medicines."], "A", "free",
            USPSTF + "tobacco-use-in-adults-and-pregnant-women-counseling-and-interventions", status="due")
    pre = "prediabetes" in cond or bool(labs.get("a1c") and 5.7 <= labs["a1c"]["value"] < 6.5)
    if every or (bmi and bmi >= 30) or pre or (cond & {"high_bp", "high_cholesterol", "diabetes"}):
        add("lifestyle", "Diet and activity coaching",
            "Sessions with a dietitian or a lifestyle programme", "Ask",
            ["With a BMI of 30 or more, prediabetes, or heart risks such as high blood pressure or cholesterol "
             "(task force B)."]
            + (["The CDC's National Diabetes Prevention Program is made for prediabetes."] if pre else []), "B", "free",
            USPSTF + "healthy-diet-and-physical-activity-counseling-adults-with-high-risk-of-cvd", status="ask")
    if a(65):
        add("falls", "Falls prevention", "Exercise for strength and balance", "Ask",
            ["Adults 65 and over with a higher risk of falling (task force B)."], "B", "free",
            USPSTF + "falls-prevention-community-dwelling-older-adults-interventions", status="ask")
    return items


def _month(iso: str) -> str:
    return str(iso or "")[:7]


def _plus(month: str, months: int) -> str:
    y, m = int(month[:4]), int(month[5:7]) - 1 + months
    return f"{y + m // 12:04d}-{m % 12 + 1:02d}"


def _status(item: dict, memory: Memory, done: dict) -> dict:
    """When it was last done (the owner's note, or a matching lab result) and whether it is due."""
    mine = done.get(item["key"]) or {}
    last, how = mine.get("month") or "", ""
    months = item["months"]
    if mine.get("method") in METHODS.get(item["key"], {}):
        how, months = METHODS[item["key"]][mine["method"]]
    if item["lab"] and (got := _latest_lab(memory, *item["lab"])) and _month(got["at"]) > last:
        last, how = _month(got["at"]), "from your lab results"
    now = local_now().date().isoformat()[:7]
    nxt = _plus(last, months) if last and months else ""
    if item["status"]:
        status = item["status"]
    elif item["once"]:
        status = "ok" if last else "due"
    elif not months:
        status = "ask"
    else:
        status = "due" if not last or nxt <= now else "ok"
    return {**item, "last": last, "how": how, "next": nxt, "status": status}


def _cover_text(cover: str, plan: str) -> str:
    if plan == "medicare":
        return ("Medicare covers its own list of preventive services, most at $0 with a provider who accepts it: "
                "check medicare.gov/coverage." if cover in ("free", "free_ask", "visit") else COVER["billed"])
    if plan == "medicaid":
        return ("Medicaid covers task force A and B screenings at no cost for most adults; ask your state plan."
                if cover in ("free", "visit") else COVER[cover])
    if plan == "other":
        return "Your plan may not cover it: ask before you book." if cover != "billed" else COVER["billed"]
    return COVER[cover]


# --- worth asking about: Lp(a), a glucose sensor, genetic testing

def _asks(memory: Memory, p: dict) -> list[dict]:
    groups, fam, cond = _groups(p), set(p.get("family") or []), set(p.get("conditions") or [])
    a1c, glucose = _latest_lab(memory, "hba1c"), _latest_lab(memory, "glucose")
    ldl, lpa = _latest_lab(memory, "ldl"), _latest_lab(memory, "lpa")
    out = []

    # lipoprotein(a)
    if lpa:
        high = (lpa["unit"].lower().startswith("nmol") and lpa["value"] >= 125) or (
            lpa["unit"].lower().startswith("mg") and lpa["value"] >= 50)
        out.append({"key": "lpa", "title": "Lipoprotein(a)", "status": "ok",
                    "text": f"Measured {lpa['at'][:10]}: {lpa['value']:g} {lpa['unit']}. "
                            + ("That is high (50 mg/dL or 125 nmol/L and over): a risk enhancer in the 2026 ACC/AHA "
                               "dyslipidemia guideline. Tell your doctor; it doesn't change with diet, and the guideline "
                               "suggests your parents, brothers, sisters and children have it measured too."
                               if high else "Once is enough for most people: it barely changes."),
                    "source": "ACC/AHA dyslipidemia guideline (2026)"})
    else:
        why = []
        if "south_asian" in groups:
            why.append("high Lp(a) is more common with South Asian ancestry")
        if "heart_early" in fam:
            why.append("there is early heart disease in your family")
        out.append({"key": "lpa", "title": "Lipoprotein(a), once", "status": "ask",
                    "text": ("Worth asking for once" + (f": {' and '.join(why)}. " if why else ". ")
                             + "The 2026 ACC/AHA dyslipidemia guideline recommends measuring it at least once in "
                               "every adult. It is inherited, doesn't change with diet, and a high level (50 mg/dL or "
                               "125 nmol/L and over) counts toward treating cholesterol sooner. Often billed as an "
                               "ordinary test, not screening."),
                    "source": "ACC/AHA dyslipidemia guideline (2026)"})

    # a glucose sensor (CGM)
    diabetic = "diabetes" in cond or (a1c and a1c["value"] >= 6.5) or (glucose and glucose["value"] >= 126)
    pre = "prediabetes" in cond or (a1c and 5.7 <= a1c["value"] < 6.5) or (glucose and 100 <= glucose["value"] < 126)
    shown = f"Your last A1c: {a1c['value']:g}% ({a1c['at'][:10]}). " if a1c else ""
    if diabetic:
        text = (shown + ("That is in the diabetes range: see your doctor soon to confirm it. " if "diabetes" not in cond
                         else "") + "With diabetes a CGM is worth asking about: the ADA recommends one for people on "
                "insulin and says it can help on other diabetes medicines, and then it is often covered.")
        status = "ask"
    elif pre:
        text = (shown + "In the prediabetes range. A CGM isn't needed for it: the proven step is a lifestyle "
                "programme (the CDC's National Diabetes Prevention Program, often covered) and an A1c every year. "
                "If you're curious which meals raise your glucose, sensors sold without a prescription since 2024 "
                "can show it, paid by you.")
        status = "optional"
    elif a1c:
        text = shown + "In the normal range: a CGM isn't needed. Screening as above is enough."
        status = "not_needed"
    else:
        text = "Not needed before an A1c: get one first (see Diabetes and prediabetes above), then this updates."
        status = "first"
    if _recent(memory, "glucose_home", 30):
        text += " You already send glucose readings from a meter or sensor; they show on Trends."
    out.append({"key": "cgm", "title": "A glucose sensor (CGM)", "status": status, "text": text,
                "source": "ADA Standards of Care in Diabetes (2026)"})

    # genetic testing
    reasons = []
    if p.get("sex") == "female" and ("breast_ovarian" in fam or "ashkenazi" in groups):
        reasons.append("Breast or ovarian cancer in the family" + (" or Ashkenazi ancestry" if "ashkenazi" in groups
                       else "") + ": the BRCA risk check above leads to genetic counselling, and testing if the "
                       "counsellor advises it ($0 when the task force's rule applies).")
    if "colon_cancer_young" in fam:
        reasons.append("Colon, rectal or uterine cancer before 50 in the family can point to Lynch syndrome: ask "
                       "about genetic counselling.")
    if (ldl and ldl["value"] >= 190) or "very_high_cholesterol" in fam:
        reasons.append((f"An LDL of {ldl['value']:g} mg/dL ({ldl['at'][:10]})" if ldl and ldl["value"] >= 190
                        else "Very high cholesterol in the family")
                       + ": possible familial hypercholesterolemia. The ACC/AHA guideline (2026) treats an LDL of 190 "
                         "or more as its own group for a statin; a genetic test can confirm it, and your relatives "
                         "may need checking too.")
    if reasons:
        out.append({"key": "dna", "title": "Genetic testing", "status": "ask", "text": " ".join(reasons),
                    "source": "USPSTF BRCA (2019); ACC/AHA dyslipidemia guideline (2026)"})
    else:
        out.append({"key": "dna", "title": "Genetic testing", "status": "not_needed",
                    "text": "Not needed for your health now: nothing in your answers or results points to an "
                            "inherited condition a test would change. A consumer DNA test isn't a diagnostic test, "
                            "and the file is very sensitive: it says things about your relatives too, and a "
                            "company's DNA data can change hands (23andMe was sold in bankruptcy in 2025). Buddy "
                            "doesn't take DNA files.",
                    "source": "USPSTF; ACC/AHA"})
    return out


# --- compared with others (NHANES)

LABELS = {"bmi": "BMI", "waist_cm": "Waist", "systolic": "Blood pressure, top number",
          "diastolic": "Blood pressure, bottom number", "a1c": "A1c", "total_chol": "Total cholesterol",
          "hdl": "HDL cholesterol", "fasting_glucose": "Glucose (if fasting)", "ldl": "LDL cholesterol"}
BETTER_LOW = {"bmi", "waist_cm", "systolic", "diastolic", "a1c", "total_chol", "fasting_glucose", "ldl"}


def _band(age: int | None) -> str:
    if age is None:
        return "all"
    return "20-39" if age < 40 else "40-59" if age < 60 else "60+"


def rank(value: float, pcts: tuple) -> int:
    """Roughly how many in 100 have a lower value (5 to 95; 2 or 98 beyond the ends)."""
    from .nhanesref import PCTS  # noqa: PLC0415
    if value < pcts[0]:
        return 2
    if value > pcts[-1]:
        return 98
    for (p0, v0), (p1, v1) in zip(zip(PCTS, pcts), zip(PCTS[1:], pcts[1:])):
        if v0 <= value <= v1:
            return round(p0 if v1 == v0 else p0 + (p1 - p0) * (value - v0) / (v1 - v0))
    return 50


def _ref(group: str, sex: str, band: str, measure: str) -> tuple[str, tuple] | None:
    """The narrowest group with 80 or more people: this sex and age, then this sex, then everyone in the group."""
    from .nhanesref import REF  # noqa: PLC0415
    for s, b in ((sex or "any", band), (sex or "any", "all"), ("any", band), ("any", "all")):
        got = REF.get(f"{group}|{s}|{b}", {}).get(measure)
        if got and got[0] >= 80:
            who = {"male": "men", "female": "women", "any": "adults"}[s] + ("" if b == "all" else f" aged {b}")
            return who, got
    return None


def compare(memory: Memory, p: dict) -> dict:
    from .nhanesref import CYCLES, UNITS  # noqa: PLC0415
    age, sex = _age(p), p.get("sex") or ""
    mine = {}
    if (bmi := _bmi(memory, p)) is not None:
        mine["bmi"] = (bmi, "now")
    if p.get("waist_cm"):
        mine["waist_cm"] = (p["waist_cm"], "your answer")
    for m, measure in (("systolic", "bp_systolic"), ("diastolic", "bp_diastolic")):
        if (r := _recent(memory, measure, 30)):
            mine[m] = (r["value"], f"average of {r['days']} day{'s' if r['days'] != 1 else ''} to {r['at']}")
    for m, tests in (("a1c", ("hba1c",)), ("total_chol", ("cholesterol_total",)), ("hdl", ("hdl",)),
                     ("ldl", ("ldl",)), ("fasting_glucose", ("glucose",))):
        if (r := _latest_lab(memory, *tests)) and (m == "a1c" or r["unit"] == "mg/dL"):
            mine[m] = (r["value"], r["at"][:10])
    asian = bool(_groups(p) & ASIAN)
    rows = []
    for m, (value, when) in mine.items():
        row = {"measure": m, "label": LABELS[m], "value": value, "unit": UNITS[m], "when": when,
               "lower_is_better": m in BETTER_LOW}
        for group, field in (("asian", "asian"), ("all", "all")):
            if group == "asian" and not asian:
                continue
            got = _ref(group, sex, _band(age), m)
            if got:
                who, (n, pcts) = got
                row[field] = {"who": ("US " + who + (" of Asian ancestry" if group == "asian" else "")),
                              "rank": rank(value, pcts), "median": pcts[3], "people": n}
        rows.append(row)
    cutoffs = []
    if bmi is not None and asian:
        cutoffs.append(f"BMI {bmi}: the WHO's action points for Asian people are 23 (a higher risk) and 27.5 (a high "
                       "risk), lower than the usual 25 and 30.")
    if p.get("waist_cm") and _groups(p) & {"south_asian", "east_asian"} and sex:
        line = 90 if sex == "male" else 80
        cutoffs.append(f"Waist {p['waist_cm']:g} cm: for South Asian, Chinese and Japanese people the International "
                       f"Diabetes Federation's line is {line} cm for {'men' if sex == 'male' else 'women'}.")
    return {"rows": rows, "cutoffs": cutoffs,
            "note": f"From {CYCLES}: the CDC's national health survey, weighted to stand for US adults. Its Asian "
                    "group mixes South, East and Southeast Asian ancestry; it can't be split into Indian or Chinese. "
                    "Where a number sits among others isn't whether it is healthy: the ranges on Labs → Results are "
                    "that."}


# --- the page

def view(memory: Memory) -> dict:
    p, done = _profile(memory), _done(memory)
    filled = bool(p.get("birth_year") and p.get("sex"))
    labs = {"bmi": _bmi(memory, p), "a1c": _latest_lab(memory, "hba1c")}
    items = [_status(i, memory, done) for i in _items(p, labs)] if filled else []
    for i in items:
        i["cover_text"] = _cover_text(i["cover"], p.get("plan") or "")
        i["methods"] = {k: v[0] for k, v in METHODS.get(i["key"], {}).items()}
    from . import freshness  # noqa: PLC0415  a newer task force recommendation than these rules: under review
    under, verbatim = freshness.reviews(memory), freshness.words(memory)
    for i in items:
        if i["key"] in under:
            i["review"] = under[i["key"]]
            i["status"] = "review"
        if verbatim.get(i["key"]):
            i["uspstf_words"] = verbatim[i["key"]][:4]
    order = {"review": 0, "due": 1, "decide": 2, "ask": 3, "ok": 4}
    items.sort(key=lambda i: order.get(i["status"], 5))
    return {
        "profile": p, "filled": filled, "suggested": suggested(memory) if not filled else {},
        "choices": {"ancestry": [{"key": k, "label": label, "group": g} for k, label, g in ANCESTRY],
                    "family": FAMILY, "conditions": CONDITIONS, "plans": PLANS},
        "items": items, "asks": _asks(memory, p) if filled else [],
        "compare": compare(memory, p) if filled else {"rows": [], "cutoffs": [], "note": ""},
        "checked": CHECKED,
        "note": "Worked out by fixed rules from the US Preventive Services Task Force (checked " + CHECKED + "; the "
                "task force resumed work in November 2026 and may change some), the ADA, the ACC/AHA and the WHO. "
                "Coverage marks are general: under the ACA most non-grandfathered plans cover A and B screenings "
                "in-network at $0; ask your plan before you book. Buddy never orders a test or books a visit. Not "
                "medical advice. Kept on your server; no AI model is given any of it.",
    }


def due_count(memory: Memory) -> int:
    """How many screenings are due, for a line on Today (0 before the answers are in)."""
    v = view(memory)
    return sum(1 for i in v["items"] if i["status"] == "due")
