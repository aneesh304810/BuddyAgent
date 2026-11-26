"""Your health now: one view of where the owner stands, what to watch, what to do and which tests to ask about,
from their own numbers and published guidance. Code only: no AI model sees any of it. Not medical advice.

What it reads: weight, BMI and waist (bodylog), the week's home blood pressure (bodylog), A1c and LDL (Labs), the
PREVENT 10-year risk (prevent), this week's activity and sleep (activity), smoking (Checkups).

The cut-offs it uses: BMI and waist as in bodylog; blood pressure as the 2025 AHA/ACC guideline; A1c by the
American Diabetes Association (under 5.7% normal, 5.7 to 6.4 prediabetes, 6.5 or more diabetes range); LDL by
the NCEP ATP III bands (under 100 optimal, 100-129 near optimal, 130-159 borderline high, 160-189 high, 190 or more
very high); activity by the Physical Activity Guidelines; sleep 7 hours or more.
"""

from __future__ import annotations

from .memory import Memory
from .tools import local_now

OK, WATCH, ACT, NONE = "ok", "watch", "act", "none"


def _a1c_band(v: float) -> tuple[str, str]:
    if v >= 6.5:
        return ACT, "Diabetes range (6.5% or more)"
    if v >= 5.7:
        return WATCH, "Prediabetes range (5.7 to 6.4%)"
    return OK, "Normal (under 5.7%)"


def _ldl_band(v: float) -> tuple[str, str]:
    if v >= 190:
        return ACT, "Very high (190 or more)"
    if v >= 160:
        return ACT, "High (160 to 189)"
    if v >= 130:
        return WATCH, "Borderline high (130 to 159)"
    if v >= 100:
        return OK, "Near optimal (100 to 129)"
    return OK, "Optimal (under 100)"


def view(memory: Memory) -> dict:
    from . import activity, bodylog, prevent, prevention  # noqa: PLC0415
    p = prevention._profile(memory)
    body, bp, risk, week = bodylog.body_view(memory), bodylog.bp_view(memory), prevent.estimate(memory), \
        activity.week(memory)
    a1c, ldl = prevention._latest_lab(memory, "hba1c"), prevention._latest_lab(memory, "ldl")
    rows, watch, todo, tests = [], [], [], []

    def row(key, label, value, level, detail, why=""):
        rows.append({"key": key, "label": label, "value": value, "level": level, "detail": detail, "why": why})
        if level in (WATCH, ACT):
            watch.append({"key": key, "label": label, "level": level, "detail": detail})

    # weight and waist
    b = body.get("bmi")
    if b:
        level = OK if b["key"] == "healthy" else WATCH if b["key"] in ("over", "under") else ACT
        row("bmi", "BMI", f"{b['value']}", level, b["label"] + f"; a healthy weight for your height is "
            f"{b['healthy_kg'][0]:g} to {b['healthy_kg'][1]:g} kg", b["why"])
        if b["key"] in ("over", "obese"):
            w = body["weight"]["value"]
            todo.append({"key": "weight", "text": f"Aim to lose 5 to 10% of your weight ({round(w * 0.05, 1):g} to "
                         f"{round(w * 0.1, 1):g} kg) over about 6 months, at about 0.5 kg a week: even 5% lowers blood "
                         "pressure, blood sugar and cholesterol. Set Plan → Menu to 'Lose slowly'.",
                         "why": "NIH and the Diabetes Prevention Program"})
    else:
        row("bmi", "BMI", "", NONE, "Needs " + " and ".join(m for m in body["missing"] if "waist" not in m))
    wst = body.get("waist")
    if wst:
        level = WATCH if wst["over"] or (wst.get("ratio") or {}).get("over") else OK
        ratio = f"; waist to height {wst['ratio']['value']} ({wst['ratio']['label']})" if wst.get("ratio") else ""
        row("waist", "Waist", f"{wst['value']:g} cm", level, f"{wst['label']} of {wst['cut']} cm ({wst['why']}){ratio}")
        if level == WATCH:
            tests.append({"key": "liver", "text": "Liver tests (ALT, AST and platelets: a FIB-4 score for fatty liver), "
                          "with A1c and a lipid panel", "why": "Extra belly fat"})
    else:
        row("waist", "Waist", "", NONE, "Measure it: " + bodylog.WAIST_HOW)
    # blood pressure
    if bp.get("enough"):
        c = bp["category"]["key"]
        level = OK if c == "normal" else WATCH if c == "elevated" else ACT
        row("bp", "Blood pressure", f"{bp['week']['systolic']}/{bp['week']['diastolic']}", level,
            f"{bp['category']['label']} (home average, {bp['count']} readings over {bp['days']} days)", bodylog.GUIDE)
        if c != "normal":
            todo.append({"key": "bp", "text": "For blood pressure: less salt (under 1,500 mg a day is ideal), more "
                         "fruit, vegetables, beans and low-fat dairy (the DASH way of eating), cardio most days, a "
                         "healthy weight, and little or no alcohol. Keep measuring morning and evening.",
                         "why": "American Heart Association"})
        if c in ("stage1", "stage2"):
            tests.append({"key": "bp_tests", "text": "The guideline's starting tests for high blood pressure: blood "
                          "glucose or A1c, a lipid panel, creatinine with eGFR, sodium, potassium, calcium, TSH, a "
                          "complete blood count, a urinalysis with urine albumin-to-creatinine ratio, and an ECG",
                          "why": f"Home average {bp['week']['systolic']}/{bp['week']['diastolic']}"})
    else:
        row("bp", "Blood pressure", "", NONE, bp.get("needed") or "No readings yet")
    if bp.get("crisis"):
        watch.insert(0, {"key": "bp_crisis", "label": "Very high reading", "level": ACT,
                         "detail": "A reading of 180/120 or more this week. " + bodylog.CRISIS_ADVICE})
    # labs
    if a1c:
        level, label = _a1c_band(a1c["value"])
        row("a1c", "A1c", f"{a1c['value']:g}%", level, f"{label}, {a1c['at'][:10]}", "American Diabetes Association")
        if level == WATCH:
            todo.append({"key": "a1c", "text": "For prediabetes: lose 7% of your weight and do 150 minutes of activity "
                         "a week (the Diabetes Prevention Program's goals), whole grains over refined ones, rice at a "
                         "cup or less. Recheck A1c every year.", "why": "Diabetes Prevention Program; ADA"})
    else:
        row("a1c", "A1c", "", NONE, "No result yet")
        tests.append({"key": "a1c", "text": "A1c: from age 35, every 3 years if normal; earlier with a BMI of 23 or "
                      "more for Asian ancestry", "why": "American Diabetes Association"})
    if ldl and ldl["unit"] == "mg/dL":
        level, label = _ldl_band(ldl["value"])
        row("ldl", "LDL cholesterol", f"{ldl['value']:g} mg/dL", level, f"{label}, {ldl['at'][:10]}", "NCEP ATP III")
    else:
        row("ldl", "LDL cholesterol", "", NONE, "No result yet")
    # heart risk
    if risk["status"] == "ok":
        level = ACT if risk["cvd"] >= prevent.HIGH else OK
        row("prevent", "10-year heart and stroke risk", f"{risk['cvd']:g}%", level,
            f"{risk['label']}; heart attack or stroke alone {risk['ascvd']:g}%", "AHA PREVENT")
        if risk.get("moves"):
            m = risk["moves"][0]
            todo.append({"key": "prevent", "text": f"What would lower your risk most: {m['if']} would take it from "
                         f"{risk['cvd']:g}% to {m['cvd']:g}%.", "why": "AHA PREVENT, worked out for you"})
    else:
        row("prevent", "10-year heart and stroke risk", "", NONE, risk.get("detail") or "")
        missing = " ".join(risk["inputs"].get("missing") or [])
        if "lipid" in missing or "creatinine" in missing:
            tests.append({"key": "prevent", "text": "For your heart risk estimate: a lipid panel (total and HDL "
                          "cholesterol) and creatinine (a basic metabolic panel)", "why": "AHA PREVENT needs them"})
    # activity and sleep
    items = {i["key"]: i for i in week["items"]}
    if week["has_data"]:
        c = items["cardio"]
        row("cardio", "Cardio this week", f"{c['done']} of {c['goal']} min",
            OK if c["status"] in ("met", "on_track") else WATCH, c["say"], activity.GUIDE)
        s = items["strength"]
        row("strength", "Strength this week", f"{s['done']} of {s['goal']}",
            OK if s["status"] in ("met", "on_track") else WATCH, s["say"], activity.GUIDE)
        sl = items["sleep"]
        if sl["status"] != "no_data":
            row("sleep", "Sleep", f"{sl['done']} of {sl['goal']} nights at {sl['target_h']:g} h",
                OK if sl["status"] == "met" else WATCH, sl["say"], "Adults: 7 hours or more")
        if c["status"] == "behind" or s["status"] == "behind":
            todo.append({"key": "move", "text": " ".join(i["say"] for i in (c, s) if i["status"] == "behind"),
                         "why": activity.GUIDE})
    if p.get("smoking") == "current":
        watch.append({"key": "smoking", "label": "Smoking", "level": ACT, "detail": "The biggest single risk you can "
                      "change."})
        todo.append({"key": "smoking", "text": "Quitting smoking lowers heart risk faster than anything else: free "
                     "help at 1-800-QUIT-NOW.", "why": "CDC"})
    if not prevention._latest_lab(memory, "lpa"):
        tests.append({"key": "lpa", "text": "Lipoprotein(a), once in your life", "why": "2026 ACC/AHA guideline"})
    order = {ACT: 0, WATCH: 1}
    watch.sort(key=lambda w: order.get(w["level"], 2))
    return {"rows": rows, "watch": watch, "todo": todo, "tests": tests, "checked": local_now().isoformat(
        timespec="minutes"), "note": "Worked out by Buddy's code from your own numbers and published guidance. No AI "
            "model sees any of it. Not medical advice: talk it over with your doctor."}
