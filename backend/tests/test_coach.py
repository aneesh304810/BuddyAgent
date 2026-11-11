"""The health plan: saved only as agreed, habits tracked from the ring and the phone, meals against the day's targets,
progress over the week, and nudges at the right moments (each once)."""

import json
from datetime import timedelta

import pytest

from buddy import coach, health_data, oura
from buddy.memory import Memory
from buddy.tools import ToolError, local_now

PLAN = {"goals": ["Lose 5 kg by March", "Sleep better"], "why": "more energy for the kids",
        "habits": [{"text": "Walk 8,000 steps", "metric": "steps", "target": "8000"},
                   {"text": "Lights out by 22:45", "metric": "bedtime", "target": "22:45"},
                   {"text": "7 hours of sleep", "metric": "sleep_hours", "target": "7"},
                   {"text": "3 workouts a week", "metric": "workouts", "target": "3"},
                   {"text": "2 litres of water", "metric": "manual"}],
        "diet": {"style": "vegetarian, high protein", "calories": 2000, "protein_g": 120, "allergies": ["peanuts"]}}


def _ring(m, day, **v):
    oura._ready(m)
    row = {f: None for f in oura.FIELDS} | v
    m.db.execute(f"INSERT OR REPLACE INTO oura_daily (day, {', '.join(oura.FIELDS)}, workouts, updated_at) "
                 f"VALUES (?, {', '.join('?' for _ in oura.FIELDS)}, ?, 'x')",
                 (day.isoformat(), *[row[f] for f in oura.FIELDS], json.dumps(v.get("workouts_list", []))))
    m.db.commit()


@pytest.fixture
def m(tmp_path):
    return Memory(tmp_path / "b.db")


def test_plan_needs_goals_habits_and_targets(m):
    with pytest.raises(ToolError):
        coach.set_plan(m, {"goals": ["x"], "habits": []})
    with pytest.raises(ToolError, match="target"):
        coach.set_plan(m, {"goals": ["x"], "habits": [{"text": "walk", "metric": "steps"}]})
    p = coach.set_plan(m, PLAN)
    assert [h["id"] for h in p["habits"]] == ["h1", "h2", "h3", "h4", "h5"]
    assert p["habits"][1]["target"] == "22:45" and p["habits"][0]["target"] == 8000
    assert p["diet"]["allergies"] == ["peanuts"]


def test_today_tracks_from_the_ring_and_the_phone(m):
    now = local_now().replace(hour=19, minute=0)
    coach.set_plan(m, PLAN)
    _ring(m, now.date(), steps=5200, sleep_minutes=400, bedtime="23:10", readiness=62, hrv=30)
    for i in range(1, 7):
        _ring(m, now.date() - timedelta(days=i), hrv=45, readiness=80)
    health_data.ingest(m, {"days": [{"day": now.date().isoformat(), "active_minutes": 40,
                                     "workouts": [{"type": "run", "minutes": 30}]}]})
    t = coach.today(m, now)
    by = {h["metric"]: h for h in t["habits"]}
    assert by["steps"]["value"] == 5200 and by["steps"]["done"] is False
    assert by["bedtime"]["done"] is False and by["sleep_hours"]["value"] == 6.7
    assert by["workouts"]["value"] == 1 and by["manual"]["done"] is None
    advice = " ".join(t["body"]["advice"])
    assert "keep it easy" in advice and "HRV 30" in advice
    coach.log_habit(m, "water", 2)
    assert {h["metric"]: h for h in coach.today(m, now)["habits"]}["manual"]["done"] is True


def test_bedtime_after_midnight_is_late():
    assert coach._bed_ok("22:30", "22:45") and not coach._bed_ok("00:15", "22:45")
    assert coach._bed_ok("23:59", "00:30")


def test_meals_count_against_the_days_targets(m):
    coach.set_plan(m, PLAN)
    now = local_now().replace(hour=13, minute=0)
    coach.add_meal(m, "Greek yoghurt with berries", 250, 20, now=now)
    coach.add_meal(m, "Paneer wrap", 550, 30, now=now)
    f = coach.food(m, now)
    assert f["next_meal"] == "lunch" and f["left_today"] == {"calories": 1200, "protein_g": 70}
    assert f["diet"]["allergies"] == ["peanuts"]


def test_nudges_come_at_the_right_time_once_a_day(m):
    coach.set_plan(m, PLAN)
    day = local_now().replace(hour=8, minute=0, second=0, microsecond=0)
    _ring(m, day.date(), steps=5200, sleep_minutes=380, readiness=62)
    morning = coach.nudges(m, day)
    assert len(morning) == 1 and morning[0]["text"].startswith("Readiness 62, 6h20 of sleep. Low recovery")
    assert "lights out by 22:45" in morning[0]["text"] and morning[0]["key"].endswith(day.date().isoformat())
    evening = {n["key"].split(":")[1]: n["text"] for n in coach.nudges(m, day.replace(hour=19))}
    assert "2,800 to your 8,000" in evening["steps"]
    assert "manual" in {n["key"].split(":")[1] for n in coach.nudges(m, day.replace(hour=21))}
    assert "bed" in {n["key"].split(":")[1] for n in coach.nudges(m, day.replace(hour=22, minute=15))}
    assert coach.nudges(Memory(":memory:"), day) == []  # no plan: nothing


def test_progress_counts_days_with_data_only(m):
    coach.set_plan(m, PLAN)
    now = local_now().replace(hour=12)
    for i, steps in enumerate([9000, 8500, 4000, None, 8100], start=1):
        if steps:
            _ring(m, now.date() - timedelta(days=i), steps=steps)
    p = {h["habit"]: h for h in coach.progress(m, 7, now)["habits"]}
    assert p["Walk 8,000 steps"]["done_days"] == 3 and p["Walk 8,000 steps"]["of_days"] == 4
    assert p["Walk 8,000 steps"]["streak"] == 2 and p["Walk 8,000 steps"]["rate"] == 75


def test_tool_without_a_plan_asks_to_set_one_and_forget_deletes(m):
    assert "No health plan yet" in coach.run_tool(m, {"action": "today"})
    coach.run_tool(m, {"action": "set_plan", **PLAN})
    coach.run_tool(m, {"action": "meal", "what": "oats", "calories": 300, "protein_g": 10})
    assert json.loads(coach.run_tool(m, {"action": "show"}))["plan"]["goals"][0] == "Lose 5 kg by March"
    coach.run_tool(m, {"action": "forget"})
    assert coach.plan(m) == {} and coach.eaten(m, local_now().date())["meals"] == []
    assert coach.morning_line(m) == ""
