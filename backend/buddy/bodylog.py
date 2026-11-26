"""Blood pressure, weight and waist: readings typed on the Health page or sent by a cuff or scale app through Health
Connect, each kept with its time until the owner deletes it, and what they say by published cut-offs. Code only:
no AI model sees any of it.

Blood pressure (the 2025 AHA/ACC guideline; its categories are unchanged from 2017):
- every reading is kept with its time: morning (4 am to noon), evening (5 pm to 4 am) or other;
- the last 7 days' home average, once there are enough readings (6, over 3 days), against the categories: normal
  under 120/80; elevated 120-129 and under 80; stage 1 130-139 or 80-89; stage 2 140/90 or more. A home average
  of 135/85 matches 140/90 in the office (the ACC/AHA table of matching home and office values, 2017), so
  stage 2 starts there for home readings;
- morning against evening, and this week against last;
- a reading of 180/120 or more is answered at once with the American Heart Association's advice, and a low one
  (under 90/60) with when to tell a doctor.

Weight and waist: typed ("entered by you") or from a scale. BMI by height, with the WHO cut-offs (25 and 30) and,
for Asian ancestry, the WHO expert consultation's lower ones (23 and 27.5); waist against the NIH's (over 102 cm
for men, 88 cm for women) and, for Asian ancestry, the IDF's (90 and 80 cm); and waist to height (NICE: keep it
under 0.5).
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta, timezone

from .memory import Memory
from .tools import ToolError, local_now

SCHEMA = """
CREATE TABLE IF NOT EXISTS bp_readings (           -- every blood pressure reading, kept until the owner deletes it
    id INTEGER PRIMARY KEY, at TEXT NOT NULL, systolic INTEGER NOT NULL, diastolic INTEGER NOT NULL, pulse INTEGER,
    source TEXT NOT NULL DEFAULT 'typed', UNIQUE (at, source)
);
"""
ENTERED = "entered"  # health_records connector for what the owner types
CRISIS = (180, 120)
LOW = (90, 60)
MIN_READINGS, MIN_DAYS = 6, 3
CATEGORY = {"normal": "Normal", "elevated": "Elevated", "stage1": "High blood pressure, stage 1",
            "stage2": "High blood pressure, stage 2"}
GUIDE = "2025 AHA/ACC high blood pressure guideline"
HOW_TO = ["Use a validated upper-arm cuff on bare skin, the right size for your arm.",
          "No coffee, exercise or smoking in the 30 minutes before; empty your bladder.",
          "Sit quietly for 5 minutes, back supported, feet flat, arm resting at heart level; don't talk.",
          "Take 2 readings a minute apart, in the morning before food or medicine, and in the evening.",
          "Measure for a week before a doctor's visit and bring the readings."]
CRISIS_ADVICE = ("That's 180/120 or more. Sit quietly for 5 minutes and measure again. If it's still that high, call "
                 "your doctor now. If you have chest pain, shortness of breath, back pain, numbness or weakness, a "
                 "change in vision or trouble speaking, call 911 now. (American Heart Association.)")
LOW_ADVICE = "That's low. If you feel dizzy, faint or unwell, sit or lie down and tell your doctor."
WAIST_HOW = ("Stand, breathe out normally, and measure around your bare waist just above the top of your hip bones, "
             "the tape level and snug but not pressing into the skin (NIH).")


def _ready(memory: Memory) -> None:
    memory.db.executescript(SCHEMA)


def _int(v, low: int, high: int, what: str) -> int:
    try:
        n = round(float(v))
    except (TypeError, ValueError):
        raise ToolError(f"Type the {what} as a number.") from None
    if not low <= n <= high:
        raise ToolError(f"A {what} between {low} and {high}.")
    return n


def period(at: str) -> str:
    """Morning (4 am to noon), evening (5 pm to 4 am) or other, by the reading's local clock time."""
    try:
        hour = int(str(at)[11:13])
    except ValueError:
        return "other"
    return "morning" if 4 <= hour < 12 else "evening" if hour >= 17 or hour < 4 else "other"


def category(sys_avg: float, dia_avg: float, home: bool = True) -> str:
    """The guideline's category for an average (home: stage 2 from 135/85, matching 140/90 in the office)."""
    two = (135, 85) if home else (140, 90)
    if sys_avg >= two[0] or dia_avg >= two[1]:
        return "stage2"
    if sys_avg >= 130 or dia_avg >= 80:
        return "stage1"
    if sys_avg >= 120:
        return "elevated"
    return "normal"


def _record_day(memory: Memory, day: str) -> None:
    """The day's average of the typed readings, in the common form beside the devices' (for the model and checks)."""
    from . import healthrecords  # noqa: PLC0415
    rows = memory.db.execute("SELECT systolic, diastolic FROM bp_readings WHERE source = 'typed' AND substr(at, 1, 10) "
                             "= ?", (day,)).fetchall()
    healthrecords._ready(memory)
    for measure in ("bp_systolic", "bp_diastolic"):
        memory.db.execute("DELETE FROM health_records WHERE connector = ? AND measure = ? AND at = ?",
                          (ENTERED, measure, day))
    if rows:
        _write(memory, "bp_systolic", day, sum(r["systolic"] for r in rows) / len(rows), "mmHg", "typed readings")
        _write(memory, "bp_diastolic", day, sum(r["diastolic"] for r in rows) / len(rows), "mmHg", "typed readings")
    memory.db.commit()


def _write(memory: Memory, measure: str, day: str, value: float, their_unit: str, ref: str) -> None:
    from . import healthrecords  # noqa: PLC0415
    healthrecords._ready(memory)
    _, unit, loinc, _ = healthrecords.MEASURES[measure]
    memory.db.execute(
        "INSERT INTO health_records (connector, measure, at, value, unit, original_value, original_unit, quality, "
        "loinc, source_ref, imported_at, sync_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL) "
        "ON CONFLICT(connector, measure, at) DO UPDATE SET value = excluded.value, original_value = "
        "excluded.original_value, imported_at = excluded.imported_at",
        (ENTERED, measure, day, round(float(value), 1), unit, json.dumps(value), their_unit, healthrecords.ENTERED,
         loinc, f"{ref}:{day}", datetime.now(timezone.utc).isoformat(timespec="seconds")))


def add_bp(memory: Memory, systolic, diastolic, pulse=None, at: str | None = None, source: str = "typed") -> dict:
    """Keep one reading; say at once if it is very high or low."""
    s = _int(systolic, 60, 260, "top number (systolic)")
    d = _int(diastolic, 30, 160, "bottom number (diastolic)")
    if s <= d:
        raise ToolError("The top number should be higher than the bottom one: check the order.")
    p = _int(pulse, 30, 220, "pulse") if pulse not in (None, "") else None
    at = at or local_now().strftime("%Y-%m-%dT%H:%M:%S")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2})?", at):
        raise ToolError("That isn't a time.")
    _ready(memory)
    memory.db.execute("INSERT INTO bp_readings (at, systolic, diastolic, pulse, source) VALUES (?, ?, ?, ?, ?) "
                      "ON CONFLICT(at, source) DO UPDATE SET systolic = excluded.systolic, diastolic = "
                      "excluded.diastolic, pulse = excluded.pulse", (at, s, d, p, source))
    memory.db.commit()
    if source == "typed":
        _record_day(memory, at[:10])
    alert = CRISIS_ADVICE if (s >= CRISIS[0] or d >= CRISIS[1]) else LOW_ADVICE if (s < LOW[0] or d < LOW[1]) else ""
    return {"reading": {"at": at, "systolic": s, "diastolic": d, "pulse": p, "period": period(at)}, "alert": alert,
            "level": "crisis" if alert == CRISIS_ADVICE else "low" if alert else ""}


def ingest_phone(memory: Memory, readings) -> int:
    """Each reading a cuff app wrote to Health Connect, with its own time (sent by the phone). Returns how many."""
    n = 0
    for r in (readings or [])[:500]:
        if not isinstance(r, dict):
            continue
        try:
            add_bp(memory, r.get("systolic"), r.get("diastolic"), r.get("pulse"), str(r.get("at") or "")[:19],
                   source="health_connect")
            n += 1
        except ToolError:
            continue
    return n


def delete_bp(memory: Memory, reading_id) -> None:
    _ready(memory)
    row = memory.db.execute("SELECT at, source FROM bp_readings WHERE id = ?", (int(reading_id),)).fetchone()
    if not row:
        raise ToolError("That reading isn't there.")
    memory.db.execute("DELETE FROM bp_readings WHERE id = ?", (int(reading_id),))
    memory.db.commit()
    if row["source"] == "typed":
        _record_day(memory, row["at"][:10])


def _avg(rows, k: str):
    return round(sum(r[k] for r in rows) / len(rows)) if rows else None


def bp_view(memory: Memory, today: date | None = None) -> dict:
    """This week's readings against the guideline, morning against evening, and the last 30 readings."""
    _ready(memory)
    today = today or local_now().date()
    since, before = (today - timedelta(days=6)).isoformat(), (today - timedelta(days=13)).isoformat()
    rows = [dict(r) | {"period": period(r["at"])} for r in memory.db.execute(
        "SELECT * FROM bp_readings WHERE substr(at, 1, 10) >= ? ORDER BY at DESC", (before,))]
    week = [r for r in rows if r["at"][:10] >= since]
    last = [r for r in rows if r["at"][:10] < since]
    days = len({r["at"][:10] for r in week})
    out = {"how_to": HOW_TO, "readings": [dict(r) | {"period": period(r["at"])} for r in memory.db.execute(
        "SELECT * FROM bp_readings ORDER BY at DESC LIMIT 30")], "count": len(week), "days": days,
        "source": GUIDE}
    out["latest"] = out["readings"][0] if out["readings"] else None
    if week:
        out["week"] = {"systolic": _avg(week, "systolic"), "diastolic": _avg(week, "diastolic"),
                       "pulse": _avg([r for r in week if r["pulse"]], "pulse")}
        for p in ("morning", "evening"):
            got = [r for r in week if r["period"] == p]
            out[p] = {"systolic": _avg(got, "systolic"), "diastolic": _avg(got, "diastolic"), "count": len(got)} \
                if got else None
    enough = len(week) >= MIN_READINGS and days >= MIN_DAYS
    out["enough"] = enough
    if enough:
        w = out["week"]
        key = category(w["systolic"], w["diastolic"])
        out["category"] = {"key": key, "label": CATEGORY[key]}
        out["next"] = {
            "normal": "Normal. Keep the habits that keep it there, and check now and then.",
            "elevated": "Elevated: not high blood pressure yet. Less salt, more activity and a healthy weight bring "
                        "it down.",
            "stage1": "Stage 1. The guideline: blood pressure medicine if your 10-year PREVENT risk is 7.5% or more, "
                      "or with diabetes, kidney disease or heart disease; otherwise 3 to 6 months of lifestyle change "
                      "first, then medicine if it stays at 130/80 or more. Talk it over with your doctor and bring "
                      "this week's readings.",
            "stage2": "Stage 2. The guideline advises medicine for everyone at this level, with lifestyle change. "
                      "See your doctor soon and bring this week's readings.",
        }[key]
        m, e = out.get("morning"), out.get("evening")
        if m and e and m["count"] >= 3 and e["count"] >= 3 and m["systolic"] - e["systolic"] >= 10:
            out["pattern"] = (f"Your mornings average {m['systolic'] - e['systolic']} higher (top number) than your "
                              "evenings. Mention it to your doctor; it can matter for when medicine is taken.")
    else:
        out["needed"] = (f"{len(week)} of at least {MIN_READINGS} readings over {MIN_DAYS} days this week: two each "
                         "morning and evening give a fair average.")
    if last and week:
        out["change"] = _avg(week, "systolic") - _avg(last, "systolic")
    out["crisis"] = [r for r in week if r["systolic"] >= CRISIS[0] or r["diastolic"] >= CRISIS[1]]
    out["target"] = ("With high blood pressure, the guideline's target is a top number under 130, and under 120 is "
                     "encouraged.")
    out["note"] = ("Worked out by Buddy's code from your readings and the " + GUIDE + ". No AI model sees them. "
                   "Not medical advice.")
    return out


def home_average(memory: Memory) -> dict | None:
    """The last 7 days' average when there are enough readings (for the risk estimate and the fit check)."""
    v = bp_view(memory)
    return (v["week"] | {"days": v["days"], "count": v["count"]}) if v.get("enough") else None


def daily(memory: Memory, days: int = 730) -> dict[str, dict]:
    """Each day's averages (all, morning, evening top numbers), for Buddy's own model."""
    _ready(memory)
    since = (local_now().date() - timedelta(days=days)).isoformat()
    out: dict[str, dict] = {}
    for r in memory.db.execute("SELECT at, systolic, diastolic FROM bp_readings WHERE substr(at, 1, 10) >= ?",
                               (since,)):
        d = out.setdefault(r["at"][:10], {"_all": [], "_morning": [], "_evening": []})
        d["_all"].append(r["systolic"])
        d.setdefault("_dia", []).append(r["diastolic"])
        if (p := period(r["at"])) in ("morning", "evening"):
            d[f"_{p}"].append(r["systolic"])
    for d in out.values():
        allv, dia, am, pm = d.pop("_all"), d.pop("_dia"), d.pop("_morning"), d.pop("_evening")
        d.update(bp_sys=round(sum(allv) / len(allv), 1), bp_dia=round(sum(dia) / len(dia), 1))
        if am:
            d["bp_sys_am"] = round(sum(am) / len(am), 1)
        if pm:
            d["bp_sys_pm"] = round(sum(pm) / len(pm), 1)
    return out


def forget(memory: Memory) -> int:
    """Everything typed here: blood pressure readings (and the cuff app's), typed weights and waists, the heart risk
    estimates and the strength sessions noted. Returns how many rows went."""
    from . import activity, healthrecords, prevent  # noqa: PLC0415
    _ready(memory)
    healthrecords._ready(memory)
    n = memory.db.execute("DELETE FROM bp_readings").rowcount
    n += memory.db.execute("DELETE FROM health_records WHERE connector = ?", (ENTERED,)).rowcount
    memory.db.executescript(prevent.SCHEMA + activity.SCHEMA)
    n += memory.db.execute("DELETE FROM prevent_history").rowcount
    n += memory.db.execute("DELETE FROM strength_log").rowcount
    memory.db.commit()
    return n


# --- weight and waist

def add_measure(memory: Memory, what: str, value, unit: str = "", day: str | None = None) -> dict:
    """A weight (kg or lb) or waist (cm or in) the owner typed, kept as 'entered by you'."""
    from . import healthrecords  # noqa: PLC0415
    day = day or local_now().date().isoformat()
    try:
        v = float(value)
    except (TypeError, ValueError):
        raise ToolError("Type a number.") from None
    if what == "weight":
        kg = v * 0.45359237 if unit == "lb" else v
        if not 25 <= kg <= 350:
            raise ToolError("A weight between 25 and 350 kg (55 and 770 lb).")
        _write(memory, "weight", day, kg, unit or "kg", "typed")
    elif what == "waist":
        cm = v * 2.54 if unit == "in" else v
        if not 50 <= cm <= 200:
            raise ToolError("A waist between 50 and 200 cm (20 and 79 inches).")
        _write(memory, "waist_cm", day, cm, unit or "cm", "typed")
    else:
        raise ToolError("Weight or waist.")
    memory.db.commit()
    healthrecords.prune(memory, ENTERED)
    return body_view(memory)


def _series(memory: Memory, measure: str, days: int = 365) -> list[dict]:
    from . import healthrecords  # noqa: PLC0415
    healthrecords._ready(memory)
    since = (local_now().date() - timedelta(days=days)).isoformat()
    rows = memory.db.execute(
        "SELECT at, value, connector FROM health_records WHERE measure = ? AND at >= ? ORDER BY at, "
        "CASE connector WHEN 'entered' THEN 0 ELSE 1 END", (measure, since)).fetchall()
    by_day: dict[str, dict] = {}
    for r in rows:  # one a day: what the owner typed wins over a device's
        by_day.setdefault(r["at"][:10], {"day": r["at"][:10], "value": round(float(r["value"]), 1),
                                         "source": r["connector"]})
    return list(by_day.values())


def bmi_category(bmi: float, asian: bool) -> dict:
    if bmi < 18.5:
        key, label = "under", "Underweight"
    elif bmi < (23 if asian else 25):
        key, label = "healthy", "Healthy range"
    elif bmi < (27.5 if asian else 30):
        key, label = "over", "Overweight" + (" (the cut-off for Asian ancestry is 23)" if asian else "")
    else:
        key, label = "obese", "Obesity range" + (" (the cut-off for Asian ancestry is 27.5)" if asian else "")
    return {"key": key, "label": label,
            "why": "WHO expert consultation for Asian populations (23 and 27.5)" if asian else "WHO and NIH (25 and 30)"}


def body_view(memory: Memory) -> dict:
    """Weight (and its course), BMI with its category, the healthy range for the owner's height, waist and waist
    to height."""
    from . import dietcheck, dietplan, prevention  # noqa: PLC0415
    p = prevention._profile(memory)
    height = p.get("height_cm") or prevention.suggested(memory).get("height_cm")
    asian = bool(prevention._groups(p) & prevention.ASIAN)
    sex = p.get("sex") or ""
    weights, waists = _series(memory, "weight"), _series(memory, "waist_cm", 3650)
    out: dict = {"weights": weights[-90:], "waists": waists[-24:], "height_cm": height, "asian": asian,
                 "waist_how": WAIST_HOW, "missing": []}
    w = weights[-1] if weights else None
    if not w and (pw := prevention._weight(memory)):
        w = {"day": "", "value": pw, "source": "oura"}
    out["weight"] = w
    if not height:
        out["missing"].append("your height (Labs → Checkups)")
    if not w:
        out["missing"].append("your weight (type it here, or a scale through Health Connect)")
    if w and height:
        m = height / 100
        bmi = round(w["value"] / m ** 2, 1)
        top = 22.9 if asian else 24.9
        out["bmi"] = {"value": bmi, **bmi_category(bmi, asian),
                      "healthy_kg": [round(18.5 * m * m, 1), round(top * m * m, 1)]}
        out["trend"] = dietcheck.weight_trend(memory, dietplan.prefs(memory)["goal"])
    waist = waists[-1]["value"] if waists else p.get("waist_cm")
    if waist:
        cut, cut_why = ((90, "IDF, South Asian men") if sex == "male" else (80, "IDF, South Asian women")) if asian \
            else ((102, "NIH, men") if sex == "male" else (88, "NIH, women"))
        over = waist >= cut if asian else waist > cut
        out["waist"] = {"value": waist, "day": waists[-1]["day"] if waists else "your Checkups answer", "cut": cut,
                        "over": over, "why": cut_why, "label": "Above the cut-off" if over else "Under the cut-off"}
        if height:
            ratio = round(waist / height, 2)
            out["waist"]["ratio"] = {"value": ratio, "label": "Keep it under 0.5 (NICE)" if ratio < 0.5 else
                                     "Increased (0.5 to 0.59, NICE)" if ratio < 0.6 else "High (0.6 or more, NICE)",
                                     "over": ratio >= 0.5}
    else:
        out["missing"].append("your waist (measure it: " + WAIST_HOW + ")")
    out["note"] = "Worked out by Buddy's code. No AI model sees it. Not medical advice."
    return out
