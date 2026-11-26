"""The American Heart Association's PREVENT equations: the 10-year chance of cardiovascular disease (heart attack,
stroke or heart failure) and of atherosclerotic cardiovascular disease (heart attack or stroke), for adults aged 30
to 79 without heart disease. The 2025 AHA/ACC high blood pressure guideline uses it: with stage 1 high blood
pressure, medicine is advised at a 10-year PREVENT risk of 7.5% or more.

The base model (Khan SS et al., Circulation 2024;149:430-449): age, total and HDL cholesterol (non-HDL from them),
systolic blood pressure, diabetes, current smoking, eGFR, blood pressure medicine and statin use. The coefficients
are the published ones, as carried by the MIT-licensed pyprevent 0.1.7 (its source, src/model_coefficients.rs),
and this code is checked against its compiled calculator. eGFR is worked out from creatinine with the race-free
CKD-EPI 2021 equation (Inker LA et al., NEJM 2021).

Inputs come from the owner's own data: age and sex (Checkups), cholesterol, HDL, creatinine and A1c (Labs), the
week's home blood pressure (Trends → Body & BP), smoking, blood pressure medicine and statin (Checkups). Code only:
no AI model sees any of it. Each estimate is kept, with what it rested on, until the owner deletes it.
"""

from __future__ import annotations

import json
import math

from .memory import Memory
from .tools import ToolError, local_now

COEF = {  # (sex, outcome) -> published base-model coefficients (10-year)
    ("female", "cvd"): {"const": -3.307728, "age": 0.7939329, "nonhdl": 0.0305239, "hdl": -0.1606857, "sbp_low": -0.2394003, "sbp_high": 0.3600781, "diabetes": 0.8667604, "smoker": 0.5360739, "egfr_low": 0.6045917, "egfr_high": 0.0433769, "bp_meds": 0.3151672, "statin": -0.1477655, "bp_meds_x_sbp": -0.0663612, "statin_x_nonhdl": 0.1197879, "age_x_nonhdl": -0.0819715, "age_x_hdl": 0.0306769, "age_x_sbp": -0.0946348, "age_x_diabetes": -0.27057, "age_x_smoker": -0.078715, "age_x_egfr_low": -0.1637806},
    ("male", "cvd"): {"const": -3.031168, "age": 0.7688528, "nonhdl": 0.0736174, "hdl": -0.0954431, "sbp_low": -0.4347345, "sbp_high": 0.3362658, "diabetes": 0.7692857, "smoker": 0.4386871, "egfr_low": 0.5378979, "egfr_high": 0.0164827, "bp_meds": 0.288879, "statin": -0.1337349, "bp_meds_x_sbp": -0.0475924, "statin_x_nonhdl": 0.150273, "age_x_nonhdl": -0.0517874, "age_x_hdl": 0.0191169, "age_x_sbp": -0.1049477, "age_x_diabetes": -0.2251948, "age_x_smoker": -0.0895067, "age_x_egfr_low": -0.1543702},
    ("female", "ascvd"): {"const": -3.819975, "age": 0.719883, "nonhdl": 0.1176967, "hdl": -0.151185, "sbp_low": -0.0835358, "sbp_high": 0.3592852, "diabetes": 0.8348585, "smoker": 0.4831078, "egfr_low": 0.4864619, "egfr_high": 0.0397779, "bp_meds": 0.2265309, "statin": -0.0592374, "bp_meds_x_sbp": -0.0395762, "statin_x_nonhdl": 0.0844423, "age_x_nonhdl": -0.0567839, "age_x_hdl": 0.0325692, "age_x_sbp": -0.1035985, "age_x_diabetes": -0.2417542, "age_x_smoker": -0.0791142, "age_x_egfr_low": -0.1671492},
    ("male", "ascvd"): {"const": -3.500655, "age": 0.7099847, "nonhdl": 0.1658663, "hdl": -0.1144285, "sbp_low": -0.2837212, "sbp_high": 0.3239977, "diabetes": 0.7189597, "smoker": 0.3956973, "egfr_low": 0.3690075, "egfr_high": 0.0203619, "bp_meds": 0.2036522, "statin": -0.0865581, "bp_meds_x_sbp": -0.0322916, "statin_x_nonhdl": 0.114563, "age_x_nonhdl": -0.0300005, "age_x_hdl": 0.0232747, "age_x_sbp": -0.0927024, "age_x_diabetes": -0.2018525, "age_x_smoker": -0.0970527, "age_x_egfr_low": -0.1217081},
}
MG_TO_MMOL = 0.02586  # cholesterol, mg/dL to mmol/L, as in the published equations
RANGES = {"age": (30, 79), "total_c": (130, 320), "hdl": (20, 100), "sbp": (90, 200), "egfr": (15, 140)}
HIGH = 7.5  # the 2025 BP guideline's line for "high" 10-year PREVENT risk (stage 1: medicine at or above it)
SCHEMA = """
CREATE TABLE IF NOT EXISTS prevent_history (     -- each PREVENT estimate, with what it rested on; until deleted
    day TEXT PRIMARY KEY, cvd REAL NOT NULL, ascvd REAL NOT NULL, inputs TEXT NOT NULL
);
"""


def log_odds(c: dict, age: float, total_c: float, hdl: float, sbp: float, diabetes: bool, smoker: bool,
             egfr: float, bp_meds: bool, statin: bool) -> float:
    """The PREVENT base model's log-odds (cholesterol in mg/dL)."""
    a = (age - 55) / 10
    nonhdl = MG_TO_MMOL * (total_c - hdl) - 3.5
    h = (MG_TO_MMOL * hdl - 1.3) / 0.3
    sbp_low, sbp_high = (min(sbp, 110) - 110) / 20, (max(sbp, 110) - 130) / 20
    egfr_low, egfr_high = (min(egfr, 60) - 60) / -15, (max(egfr, 60) - 90) / -15
    return (c["const"] + c["age"] * a + c["nonhdl"] * nonhdl + c["hdl"] * h + c["sbp_low"] * sbp_low
            + c["sbp_high"] * sbp_high + c["diabetes"] * diabetes + c["smoker"] * smoker + c["egfr_low"] * egfr_low
            + c["egfr_high"] * egfr_high + c["bp_meds"] * bp_meds + c["statin"] * statin
            + c["bp_meds_x_sbp"] * bp_meds * sbp_high + c["statin_x_nonhdl"] * statin * nonhdl
            + c["age_x_nonhdl"] * a * nonhdl + c["age_x_hdl"] * a * h + c["age_x_sbp"] * a * sbp_high
            + c["age_x_diabetes"] * a * diabetes + c["age_x_smoker"] * a * smoker + c["age_x_egfr_low"] * a * egfr_low)


def risk(sex: str, outcome: str, age: float, total_c: float, hdl: float, sbp: float, diabetes: bool, smoker: bool,
         egfr: float, bp_meds: bool, statin: bool) -> float:
    """10-year risk in percent, one decimal. Raises ToolError outside the equations' ranges."""
    if sex not in ("male", "female") or outcome not in ("cvd", "ascvd"):
        raise ToolError("PREVENT needs sex (male or female) and an outcome.")
    for name, v in (("age", age), ("total_c", total_c), ("hdl", hdl), ("sbp", sbp), ("egfr", egfr)):
        low, high = RANGES[name]
        if not low <= v <= high:
            raise ToolError(f"PREVENT is made for {WORDS[name]} {low} to {high}; yours is {v:g}.")
    x = log_odds(COEF[(sex, outcome)], age, total_c, hdl, sbp, bool(diabetes), bool(smoker), egfr, bool(bp_meds),
                 bool(statin))
    return round(100 * math.exp(x) / (1 + math.exp(x)), 1)


WORDS = {"age": "ages", "total_c": "total cholesterol", "hdl": "HDL cholesterol", "sbp": "a top blood pressure number of",
         "egfr": "an eGFR of"}


def egfr_ckd_epi_2021(creatinine_mg_dl: float, age: float, sex: str) -> float:
    """eGFR (mL/min/1.73 m2) from serum creatinine, the race-free CKD-EPI 2021 equation."""
    k, alpha = (0.7, -0.241) if sex == "female" else (0.9, -0.302)
    r = creatinine_mg_dl / k
    return round(142 * min(r, 1) ** alpha * max(r, 1) ** -1.200 * 0.9938 ** age * (1.012 if sex == "female" else 1), 1)


def _lab(memory: Memory, *tests: str) -> dict | None:
    from . import prevention  # noqa: PLC0415
    return prevention._latest_lab(memory, *tests)


def _creatinine(memory: Memory) -> dict | None:
    """The newest creatinine in mg/dL (a result in umol/L converted)."""
    from . import labs  # noqa: PLC0415
    try:
        labs._ready(memory)
    except Exception:  # noqa: BLE001
        return None
    row = memory.db.execute("SELECT value, unit, collected FROM lab_results WHERE test = 'creatinine' "
                            "ORDER BY collected DESC, id DESC LIMIT 1").fetchone()
    if not row:
        return None
    v, unit = float(row["value"]), str(row["unit"] or "").lower().replace(" ", "")
    if "mol" in unit:  # umol/L
        v = v / 88.42
    return {"value": round(v, 2), "at": row["collected"]}


def inputs(memory: Memory) -> dict:
    """What PREVENT rests on, from the owner's own data, with where each came from, and what's missing."""
    from . import bodylog, prevention  # noqa: PLC0415
    p = prevention._profile(memory)
    s = prevention.suggested(memory)
    out: dict = {"missing": []}
    year = p.get("birth_year") or s.get("birth_year")
    out["age"] = local_now().year - year if year else None
    out["sex"] = p.get("sex") or s.get("sex") or ""
    if not out["age"] or not out["sex"]:
        out["missing"].append("your birth year and sex (Labs → Checkups)")
    tc, hdl = _lab(memory, "cholesterol_total"), _lab(memory, "hdl")
    out["total_c"] = tc["value"] if tc and tc["unit"] == "mg/dL" else None
    out["hdl"] = hdl["value"] if hdl and hdl["unit"] == "mg/dL" else None
    out["lipids_at"] = (tc or {}).get("at", "")[:10]
    if out["total_c"] is None or out["hdl"] is None:
        out["missing"].append("total and HDL cholesterol (a lipid panel: Labs → Add a report)")
    cr = _creatinine(memory)
    if cr and out["age"] and out["sex"]:
        out["egfr"], out["egfr_how"] = egfr_ckd_epi_2021(cr["value"], out["age"], out["sex"]), (
            f"from your creatinine of {cr['value']:g} mg/dL ({cr['at'][:10]}), CKD-EPI 2021")
    elif (e := _lab(memory, "egfr")):
        out["egfr"], out["egfr_how"] = e["value"], f"your lab's eGFR ({e['at'][:10]})"
    else:
        out["egfr"] = None
        out["missing"].append("creatinine, for kidney function (a basic metabolic panel)")
    bp = bodylog.home_average(memory)
    if bp:
        out["sbp"], out["sbp_how"] = bp["systolic"], f"your home average over {bp['days']} days"
    elif (r := prevention._recent(memory, "bp_systolic", 14)):
        out["sbp"], out["sbp_how"] = r["value"], f"your readings to {r['at']}"
    else:
        out["sbp"] = None
        out["missing"].append("a week of blood pressure readings (Trends → Body & BP)")
    a1c = _lab(memory, "hba1c")
    out["diabetes"] = "diabetes" in (p.get("conditions") or []) or bool(a1c and a1c["value"] >= 6.5)
    out["smoker"] = p.get("smoking") == "current"
    out["bp_meds"], out["statin"] = bool(p.get("bp_meds")), bool(p.get("statin"))
    out["asked_meds"] = "bp_meds" in p
    if "heart_disease" in (p.get("conditions") or []):
        out["not_for"] = ("PREVENT is for people without heart disease or stroke; with it, your doctor manages your "
                          "risk directly.")
    return out


def estimate(memory: Memory, keep: bool = True) -> dict:
    """The 10-year PREVENT risks, what moves them, or what's still needed. Kept once a day while it changes."""
    i = inputs(memory)
    out: dict = {"inputs": i, "high": HIGH,
                 "note": "Worked out by Buddy's code with the American Heart Association's PREVENT equations from "
                         "your own numbers. No AI model sees them. An estimate for people, not a diagnosis: talk it "
                         "over with your doctor."}
    if i.get("not_for"):
        return out | {"status": "not_for", "detail": i["not_for"]}
    if i["missing"]:
        return out | {"status": "missing", "detail": "Still needed: " + "; ".join(i["missing"]) + "."}
    args = dict(age=i["age"], total_c=i["total_c"], hdl=i["hdl"], sbp=i["sbp"], diabetes=i["diabetes"],
                smoker=i["smoker"], egfr=i["egfr"], bp_meds=i["bp_meds"], statin=i["statin"])
    try:
        cvd, ascvd = risk(i["sex"], "cvd", **args), risk(i["sex"], "ascvd", **args)
    except ToolError as e:
        return out | {"status": "out_of_range", "detail": str(e)}
    out |= {"status": "ok", "cvd": cvd, "ascvd": ascvd,
            "label": "High (7.5% or more, the 2025 guideline's line)" if cvd >= HIGH else "Under the 7.5% line"}
    moves = []  # what each change alone would do (the published optimal levels: SBP 120, non-HDL 135, no smoking)
    for words, change in (("a top blood pressure number of 120", {"sbp": 120}),
                          ("non-HDL cholesterol of 135 mg/dL", {"total_c": max(i["hdl"] + 135, 130)}),
                          ("not smoking", {"smoker": False})):
        try:
            new = risk(i["sex"], "cvd", **(args | change))
        except ToolError:
            continue
        if new < cvd - 0.05:
            moves.append({"if": words, "cvd": new, "less": round(cvd - new, 1)})
    out["moves"] = sorted(moves, key=lambda m: -m["less"])
    if keep:
        memory.db.executescript(SCHEMA)
        memory.db.execute("INSERT INTO prevent_history (day, cvd, ascvd, inputs) VALUES (?, ?, ?, ?) ON CONFLICT(day) "
                          "DO UPDATE SET cvd = excluded.cvd, ascvd = excluded.ascvd, inputs = excluded.inputs",
                          (local_now().date().isoformat(), cvd, ascvd, json.dumps(args)))
        memory.db.commit()
    out["history"] = history(memory)
    return out


def history(memory: Memory) -> list[dict]:
    memory.db.executescript(SCHEMA)
    return [dict(r) for r in memory.db.execute("SELECT day, cvd, ascvd FROM prevent_history ORDER BY day DESC LIMIT 24")]


def forget(memory: Memory) -> None:
    memory.db.executescript(SCHEMA)
    memory.db.execute("DELETE FROM prevent_history")
    memory.db.commit()
