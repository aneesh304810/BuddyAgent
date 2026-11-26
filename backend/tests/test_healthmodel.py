"""The personal health model: patterns in the owner's own days that hold when redrawn, and a small model that has to
beat guessing the average on days it never saw; learned by code, kept without daily values, shown to the owner only."""

import json
import random
from datetime import timedelta

from buddy import coach, health_data, healthmodel, oura
from buddy.memory import Memory
from buddy.tools import local_now


def _seed(m, days=120, effect=0.004, seed=3):
    """Made-up days: more steps one day, higher readiness the next morning (plus noise); bedtime has no effect."""
    rng = random.Random(seed)
    today = local_now().date()
    oura._ready(m)
    steps = {}
    for i in range(days, -1, -1):
        day = (today - timedelta(days=i)).isoformat()
        steps[day] = rng.randint(3000, 15000)
    health_data.ingest(m, {"days": [{"day": d, "steps": s} for d, s in steps.items()]})
    for i in range(days - 1, -1, -1):
        day = (today - timedelta(days=i)).isoformat()
        before = (today - timedelta(days=i + 1)).isoformat()
        readiness = round(55 + effect * steps[before] + rng.gauss(0, 4))
        bedtime = f"{rng.choice([22, 23])}:{rng.randint(0, 59):02d}"
        m.db.execute("INSERT OR REPLACE INTO oura_daily (day, readiness, hrv, bedtime, workouts, updated_at) "
                     "VALUES (?, ?, ?, ?, '[]', 'x')", (day, readiness, rng.randint(30, 60), bedtime))
    m.db.commit()


def test_a_real_pattern_is_found_and_held_and_noise_is_not(tmp_path):
    m = Memory(tmp_path / "b.db")
    _seed(m)
    got = healthmodel.learn(m)
    steps = [p for p in got["patterns"] if p["factor"] == "steps" and p["outcome"] == "readiness"]
    assert steps and steps[0]["better"] and steps[0]["share"] >= 0.9 and steps[0]["difference"] > 0
    assert steps[0]["text"].startswith("After days with more steps (over ")
    assert not [p for p in got["patterns"] if p["factor"] == "bedtime" and p["outcome"] == "readiness"]
    readiness = [x for x in got["models"] if x["outcome"] == "readiness"][0]
    assert readiness["passed"] and readiness["weights"][0]["factor"] == "steps" and readiness["test_days"] >= 30
    assert "never saw" in readiness["text"] and got["needs"] == ""


def test_no_daily_value_is_kept_with_the_result(tmp_path):
    m = Memory(tmp_path / "b.db")
    _seed(m)
    got = healthmodel.learn(m)
    kept = m.db.execute("SELECT result FROM health_model_runs").fetchone()["result"]
    assert json.loads(kept)["days"] == got["days"] and "2026-" not in json.dumps(got["patterns"])
    assert healthmodel.latest(m)["at"] == got["at"]


def test_too_few_days_says_how_many_are_needed(tmp_path):
    m = Memory(tmp_path / "b.db")
    _seed(m, days=15)
    got = healthmodel.learn(m)
    assert got["models"] == [] and "the model 60" in got["needs"]


def test_a_habit_the_owner_ticks_is_a_factor(tmp_path):
    m = Memory(tmp_path / "b.db")
    _seed(m, effect=0.0)
    m.set(coach.PLAN_KEY, json.dumps({"goals": ["sleep"], "habits": [
        {"id": "h1", "text": "No caffeine after noon", "metric": "manual", "target": 1}]}))
    coach._ready(m)
    today = local_now().date()
    rows = {r["day"]: r for r in m.db.execute("SELECT day, readiness FROM oura_daily")}
    for i in range(1, 100):
        day, nxt = (today - timedelta(days=i)).isoformat(), (today - timedelta(days=i - 1)).isoformat()
        if i % 2 == 0:  # ticked every other day, and the next morning is better
            m.db.execute("INSERT OR REPLACE INTO coach_log (day, habit, value, at) VALUES (?, 'h1', 1, 'x')", (day,))
            m.db.execute("UPDATE oura_daily SET readiness = ? WHERE day = ?", ((rows[nxt]["readiness"] or 60) + 8, nxt))
    m.db.commit()
    got = healthmodel.learn(m)
    habit = [p for p in got["patterns"] if p["factor"] == "habit:h1" and p["outcome"] == "readiness"]
    assert habit and habit[0]["text"].startswith('On days you ticked "No caffeine after noon"') and habit[0]["better"]


def test_bedtime_is_minutes_after_six_and_due_weekly(tmp_path):
    assert healthmodel._minutes_after_six("23:41") == 341 and healthmodel._minutes_after_six("00:30") == 390
    assert healthmodel._clock(341) == "23:41"
    m = Memory(tmp_path / "b.db")
    from datetime import datetime
    sunday = datetime(2026, 11, 29, 3)
    assert healthmodel.due(m, datetime(2026, 11, 26, 3))  # never learned yet
    m.set(healthmodel.WEEK_KEY, "2026-48")
    assert not healthmodel.due(m, sunday)
    assert healthmodel.due(m, datetime(2026, 12, 6, 3))  # the next Sunday
    assert not healthmodel.due(m, datetime(2026, 12, 3, 3))
