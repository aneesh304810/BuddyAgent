"""Blood pressure, weight and waist (bodylog), the PREVENT risk (prevent), the week's activity targets (activity)
and the one view of it all (healthnow): code only, from the owner's own numbers and published cut-offs."""

import json
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from buddy import activity, bodylog, checkin, health_data, healthmodel, healthnow, healthrecords, labs, oura, \
    prevent, prevention
from buddy.memory import Memory
from buddy.tools import ToolError, local_now

TODAY = local_now().date()


def _lab(m, test, value, unit, day="2026-10-01"):
    labs._ready(m)
    m.db.execute("INSERT INTO lab_results (report_id, source, test, printed, value, value_text, unit, band, collected) "
                 "VALUES (1, 'lab_upload', ?, ?, ?, ?, ?, 'within', ?)", (test, test, value, str(value), unit, day))
    m.db.commit()


def _week_of_bp(m, morning=(138, 86), evening=(126, 80), days=4):
    for d in range(days):
        day = (TODAY - timedelta(days=d)).isoformat()
        for minute, (s, dia) in ((0, morning), (1, morning)):
            bodylog.add_bp(m, s, dia, 70, at=f"{day}T07:0{minute}:00")
        for minute, (s, dia) in ((0, evening), (1, evening)):
            bodylog.add_bp(m, s, dia, 68, at=f"{day}T21:0{minute}:00")


# --- blood pressure

def test_a_very_high_or_low_reading_is_answered_at_once(tmp_path):
    m = Memory(tmp_path / "b.db")
    got = bodylog.add_bp(m, 182, 100, 80)
    assert got["level"] == "crisis" and "call 911" in got["alert"] and "measure again" in got["alert"]
    assert bodylog.add_bp(m, 88, 58)["level"] == "low"
    assert bodylog.add_bp(m, 121, 79)["alert"] == ""
    with pytest.raises(ToolError, match="higher than the bottom"):
        bodylog.add_bp(m, 80, 90)
    with pytest.raises(ToolError, match="between 60 and 260"):
        bodylog.add_bp(m, 400, 90)


@pytest.mark.parametrize("sys_dia,key", [((118, 76), "normal"), ((124, 78), "elevated"), ((132, 78), "stage1"),
                                         ((126, 82), "stage1"), ((136, 80), "stage2"), ((128, 86), "stage2")])
def test_home_averages_by_the_guideline(sys_dia, key):
    assert bodylog.category(*sys_dia) == key  # home: stage 2 from 135/85 (140/90 in the office)
    assert bodylog.category(138, 88, home=False) == "stage1"


def test_the_week_morning_against_evening_and_enough_readings(tmp_path):
    m = Memory(tmp_path / "b.db")
    bodylog.add_bp(m, 130, 85, at=f"{TODAY.isoformat()}T14:00:00")
    v = bodylog.bp_view(m)
    assert not v["enough"] and "1 of at least 6" in v["needed"]
    _week_of_bp(m)
    v = bodylog.bp_view(m)
    assert v["enough"] and v["count"] == 17 and v["days"] == 4
    assert v["morning"]["systolic"] == 138 and v["evening"]["systolic"] == 126
    assert v["category"]["key"] == "stage1" and "7.5%" in v["next"]
    assert "mornings average 12 higher" in v["pattern"]
    assert bodylog.period("2026-11-26T01:30:00") == "evening" and bodylog.period("2026-11-26T14:00") == "other"
    # typed readings are kept in the common form too (a day's average), for the model and the other checks
    day = healthrecords._ready(m) or m.db.execute(
        "SELECT value FROM health_records WHERE connector = 'entered' AND measure = 'bp_systolic' AND at = ?",
        (TODAY.isoformat(),)).fetchone()
    assert day["value"] == pytest.approx((130 + 138 * 2 + 126 * 2) / 5, abs=0.1)
    first = v["readings"][-1]["id"]
    bodylog.delete_bp(m, first)
    assert bodylog.bp_view(m)["count"] == 16


def test_the_phone_sends_each_cuff_reading_with_its_time(tmp_path):
    m = Memory(tmp_path / "b.db")
    health_data.ingest(m, {"days": [{"day": TODAY.isoformat(), "steps": 5000}],
                           "bp_readings": [{"at": f"{TODAY.isoformat()}T07:15:00", "systolic": 131, "diastolic": 84},
                                           {"at": f"{TODAY.isoformat()}T22:40", "systolic": 124.0, "diastolic": 79.0},
                                           {"at": "bad", "systolic": 120, "diastolic": 80}, "junk"]})
    got = bodylog.bp_view(m)["readings"]
    assert [(r["period"], r["source"]) for r in got] == [("evening", "health_connect"), ("morning", "health_connect")]
    health_data.ingest(m, {"days": [], "bp_readings": [{"at": f"{TODAY.isoformat()}T07:15:00", "systolic": 131,
                                                         "diastolic": 84}]})
    assert len(bodylog.bp_view(m)["readings"]) == 2  # sent again: not doubled


# --- weight and waist

def test_bmi_and_waist_with_the_asian_cut_offs(tmp_path):
    m = Memory(tmp_path / "b.db")
    prevention.save_profile(m, {"birth_year": 1982, "sex": "male", "height_cm": 180, "ancestry": ["indian_kerala"]})
    v = bodylog.body_view(m)
    assert "your weight" in " ".join(v["missing"]) and "bmi" not in v
    bodylog.add_measure(m, "weight", 172, "lb")  # 78.0 kg
    v = bodylog.add_measure(m, "waist", 37, "in")  # 94 cm
    assert v["weight"]["value"] == 78.0 and v["weight"]["source"] == "entered"
    assert v["bmi"]["value"] == 24.1 and v["bmi"]["key"] == "over" and "Asian ancestry is 23" in v["bmi"]["label"]
    assert v["bmi"]["healthy_kg"] == [59.9, 74.2]
    assert v["waist"]["value"] == 94.0 and v["waist"]["over"] and v["waist"]["cut"] == 90
    assert v["waist"]["ratio"]["value"] == 0.52 and v["waist"]["ratio"]["over"]
    assert prevention._weight(m) == 78.0  # the typed weight is the one the other checks use
    prevention.save_profile(m, {"birth_year": 1982, "sex": "male", "height_cm": 180, "ancestry": ["white"]})
    v = bodylog.body_view(m)
    assert v["bmi"]["key"] == "healthy" and v["waist"]["cut"] == 102 and not v["waist"]["over"]
    with pytest.raises(ToolError):
        bodylog.add_measure(m, "weight", 5)


# --- PREVENT

def test_prevent_matches_the_published_example_and_its_ranges():
    a = dict(age=50, total_c=200, hdl=45, sbp=160, diabetes=True, smoker=False, egfr=90, bp_meds=True, statin=False)
    # the R package preventr's documented example: 14.7% total CVD, 9.2% ASCVD
    assert prevent.risk("female", "cvd", **a) == 14.7 and prevent.risk("female", "ascvd", **a) == 9.2
    assert prevent.risk("male", "cvd", **a) > prevent.risk("male", "cvd", **(a | {"diabetes": False}))
    with pytest.raises(ToolError, match="ages 30 to 79"):
        prevent.risk("male", "cvd", **(a | {"age": 82}))
    assert prevent.egfr_ckd_epi_2021(1.0, 50, "male") == 91.7


def test_prevent_from_the_owners_own_numbers(tmp_path):
    m = Memory(tmp_path / "b.db")
    r = prevent.estimate(m)
    assert r["status"] == "missing" and "lipid panel" in r["detail"] and "creatinine" in r["detail"]
    prevention.save_profile(m, {"birth_year": TODAY.year - 44, "sex": "male", "height_cm": 180, "smoking": "never"})
    _lab(m, "cholesterol_total", 210, "mg/dL")
    _lab(m, "hdl", 42, "mg/dL")
    _lab(m, "creatinine", 88.4, "umol/L")  # 1.0 mg/dL
    _week_of_bp(m)
    r = prevent.estimate(m)
    assert r["status"] == "ok" and r["inputs"]["egfr"] == prevent.egfr_ckd_epi_2021(1.0, 44, "male")
    assert r["inputs"]["sbp"] == 132 and "home average over 4 days" in r["inputs"]["sbp_how"]
    assert r["cvd"] == prevent.risk("male", "cvd", 44, 210, 42, 132, False, False, r["inputs"]["egfr"], False, False)
    assert r["moves"] and r["moves"][0]["cvd"] < r["cvd"]
    assert r["history"][0]["cvd"] == r["cvd"]  # kept, for future reference
    prevention.save_profile(m, {"birth_year": TODAY.year - 44, "sex": "male", "conditions": ["heart_disease"]})
    assert prevent.estimate(m)["status"] == "not_for"


# --- the week's activity

def _oura_day(m, day, moderate, vigorous, steps, sleep_min, workouts=(), readiness=80, hrv=40):
    oura._ready(m)
    m.db.execute("INSERT OR REPLACE INTO oura_daily (day, steps, sleep_minutes, readiness, hrv, moderate_minutes, "
                 "vigorous_minutes, workouts, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'x')",
                 (day, steps, sleep_min, readiness, hrv, moderate, vigorous, json.dumps(list(workouts))))
    m.db.commit()


def test_oura_moderate_and_vigorous_minutes_are_kept():
    got = oura.days_from({"daily_activity": [{"day": "2026-11-20", "score": 80, "steps": 9000,
                                              "medium_activity_time": 1800, "high_activity_time": 600}]})
    assert got["2026-11-20"]["moderate_minutes"] == 30 and got["2026-11-20"]["vigorous_minutes"] == 10


def test_the_week_against_the_targets_and_the_reminders(tmp_path):
    m = Memory(tmp_path / "b.db")
    thursday = date(2026, 11, 26)
    assert thursday.weekday() == 3
    for i, (mod, vig, steps, sleep, wk) in enumerate([(10, 0, 9000, 450, [{"type": "strength training"}]),
                                                      (10, 0, 6000, 400, []), (15, 5, 7000, 380, [])]):
        _oura_day(m, (thursday - timedelta(days=3 - i)).isoformat(), mod, vig, steps, sleep, wk)
    _oura_day(m, thursday.isoformat(), 0, 0, 2000, 390, readiness=62)
    w = activity.week(m, thursday)
    items = {i["key"]: i for i in w["items"]}
    assert items["cardio"]["done"] == 10 + 10 + 15 + 2 * 5 and items["cardio"]["status"] == "behind"
    assert "about 27 minutes of brisk walking a day for the next 4 days" in items["cardio"]["say"]
    assert items["strength"]["done"] == 1 and items["strength"]["status"] == "on_track"
    assert items["steps"]["met_days"] == 1 and items["steps"]["counted_days"] == 3 and items["steps"]["yesterday"] == 7000
    assert items["sleep"]["done"] == 1 and items["sleep"]["last_night"] == 6.5
    assert "Go easy today" in w["easy"]
    activity.log_strength(m, thursday.isoformat())
    assert {i["key"]: i for i in activity.week(m, thursday)["items"]}["strength"]["done"] == 2
    activity.set_targets(m, {"cardio": 200, "steps": 10000})
    assert activity.targets(m)["cardio"] == 200 and activity.targets(m)["strength"] == 2
    with pytest.raises(ToolError):
        activity.set_targets(m, {"steps": 50})


def test_reminders_in_the_briefing_and_the_check_in_are_code_only(tmp_path, monkeypatch):
    m = Memory(tmp_path / "b.db")
    for d in range(3):
        _oura_day(m, (TODAY - timedelta(days=d)).isoformat(), 5, 0, 3000, 360)
    line = activity.morning_line(m)
    assert line.startswith("Your week so far: ") and "cardio minutes this week" in line
    assert "Yesterday 3,000 steps" in line and "You slept 6.0 hours" in line
    assert "steps to go today" in activity.evening_line(m)
    said = checkin.readback(m, {"mood": 4})
    assert "Still open:" in said


def test_the_morning_briefing_adds_the_week_after_the_models_part(tmp_path, monkeypatch):
    import asyncio
    from buddy import proactive
    m = Memory(tmp_path / "b.db")
    _oura_day(m, TODAY.isoformat(), 5, 0, 3000, 360)
    seen = {}

    async def compose(system, prompt, memory, **kw):
        seen["prompt"] = prompt
        return "Good morning."
    monkeypatch.setattr(proactive, "compose", compose)
    monkeypatch.setattr(activity, "morning_line", lambda memory: "Your week so far: 5 of 150 cardio minutes.")
    out = asyncio.run(proactive.morning_briefing(m))
    assert out.startswith("Good morning. Your week so far: 5 of 150")
    assert "cardio" not in seen["prompt"]  # the model never saw the numbers


# --- the model and the one view

def test_the_model_learns_from_blood_pressure_cardio_and_sodium(tmp_path):
    m = Memory(tmp_path / "b.db")
    day, nxt = (TODAY - timedelta(days=2)).isoformat(), (TODAY - timedelta(days=1)).isoformat()
    _oura_day(m, day, 30, 10, 8000, 420)
    _oura_day(m, nxt, 20, 0, 7000, 400)
    bodylog.add_bp(m, 128, 80, at=f"{nxt}T07:00:00")
    rows = {r["day"]: r for r in healthmodel.table(m, 10)}
    assert rows[day]["cardio_minutes"] == 50 and rows[day]["next:bp_sys_am"] == 128


def test_health_now_and_the_endpoints(tmp_path, monkeypatch):
    from buddy import server
    m = Memory(tmp_path / "b.db")
    prevention.save_profile(m, {"birth_year": TODAY.year - 44, "sex": "male", "height_cm": 180,
                                "ancestry": ["indian_kerala"], "smoking": "never"})
    bodylog.add_measure(m, "weight", 80)
    bodylog.add_measure(m, "waist", 95)
    _week_of_bp(m)
    _lab(m, "hba1c", 6.0, "%")
    v = healthnow.view(m)
    rows = {r["key"]: r for r in v["rows"]}
    assert rows["bmi"]["level"] == "watch" and rows["waist"]["level"] == "watch" and rows["bp"]["level"] == "act"
    assert rows["a1c"]["level"] == "watch" and "Prediabetes range" in rows["a1c"]["detail"]
    assert rows["prevent"]["level"] == "none" and "lipid panel" in rows["prevent"]["detail"]
    keys = {t["key"] for t in v["tests"]}
    assert {"bp_tests", "liver", "prevent", "lpa"} <= keys
    assert {t["key"] for t in v["todo"]} >= {"weight", "bp", "a1c"} and "No AI" in v["note"]
    monkeypatch.setattr(server, "memory", m)
    monkeypatch.setattr(server, "authorized", lambda t: t == "s3cret")
    client, auth = TestClient(server.app), {"Authorization": "Bearer s3cret"}
    got = client.get("/api/health/body", headers=auth).json()
    assert set(got) >= {"now", "bp", "body", "risk", "week", "targets"}
    out = client.post("/api/health/body", json={"action": "bp", "systolic": 185, "diastolic": 95}, headers=auth).json()
    assert out["said"]["level"] == "crisis" and out["now"]["watch"][0]["key"] == "bp_crisis"
    assert client.post("/api/health/body", json={"action": "bp", "systolic": "x", "diastolic": 80},
                       headers=auth).status_code == 400
    assert client.post("/api/health/body", json={"action": "strength"}, headers=auth).status_code == 200
    assert client.post("/api/health/body", json={"action": "nope"}, headers=auth).status_code == 400
    gone = client.post("/api/health/body", json={"action": "forget"}, headers=auth).json()
    assert gone["said"]["deleted"] > 0 and gone["bp"]["readings"] == [] and gone["body"]["weight"] is None
