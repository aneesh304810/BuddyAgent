"""The Health page: the owner's own numbers by rules, the plan set by a form, habits ticked and meals noted there."""

import asyncio
import inspect
from datetime import timedelta

import pytest

from buddy import coach, healthpage, oura
from buddy.memory import Memory
from buddy.tools import ToolError, local_now
from tests.test_coach import PLAN, _ring


@pytest.fixture
def m(tmp_path):
    return Memory(tmp_path / "b.db")


def seed(m, now, days=30):
    for i in range(1, days):
        _ring(m, now.date() - timedelta(days=i), readiness=80, hrv=45, sleep_minutes=450, steps=9000, lowest_hr=52)
    _ring(m, now.date(), readiness=60, hrv=30, sleep_minutes=330, steps=4000, lowest_hr=60, temp_deviation=0.6)


def test_an_empty_server_shows_an_empty_page_and_says_where_health_is_discussed(m):
    got = healthpage.view(m, env={})
    assert got["has_data"] is False and got["measures"] == [] and got["plan"] is None and got["progress"] is None
    assert got["today"] == {"readings": {}, "means": [], "habits": []}
    assert got["sources"]["ring"] == {"connected": False, "read": ""} and got["privacy"]["ranges"] is False
    assert "No AI model is given" in got["privacy"]["note"] and "not medical advice" in got["note"]
    assert healthpage.view(m, env={"BUDDY_HEALTH_RANGES": "on"})["privacy"]["ranges"] is True


def test_each_measure_is_shown_against_the_owners_own_usual(m):
    now = local_now().replace(hour=19, minute=0)
    seed(m, now)
    got = healthpage.view(m, days=14, now=now, env={})
    by = {x["key"]: x for x in got["measures"]}
    assert list(by)[:3] == ["sleep_minutes", "readiness", "hrv"] and "resting_hr" not in by   # only what has readings
    sleep = by["sleep_minutes"]
    assert (sleep["latest"], sleep["usual"], sleep["unit"], sleep["latest_band"]) == (5.5, 7.5, "h", "below usual")
    assert sleep["latest_day"] == now.date().isoformat() and len(sleep["series"]) == 14 and sleep["series"][-1]["v"] == 5.5
    assert by["readiness"]["latest_band"] == "below usual" and by["readiness"]["week_band"] == "about usual"
    assert by["lowest_hr"]["latest_band"] == "above usual" and by["lowest_hr"]["better"] == "down"
    assert by["temp_deviation"]["usual"] is None and by["temp_deviation"]["latest_band"] == ""   # one reading: no usual yet
    assert got["today"]["readings"]["readiness"] == 60
    said = " ".join(got["today"]["means"])
    assert "low recovery" in said and "short sleep" in said and "temperature is up" in said
    assert healthpage.view(m, days=500, now=now, env={})["days"] == 90 and healthpage.view(m, days=1, now=now, env={})["days"] == 14


def test_the_plan_is_saved_from_the_form_and_habits_and_meals_are_noted_there(m):
    now = local_now().replace(hour=19, minute=0)
    seed(m, now)
    with pytest.raises(ToolError, match="target"):
        healthpage.act(m, {"action": "plan", "plan": {"goals": ["x"], "habits": [{"text": "walk", "metric": "steps", "target": ""}]}})
    assert healthpage.act(m, {"action": "plan", "plan": PLAN}) == {"ok": True, "habits": 5}
    got = healthpage.view(m, now=now, env={})
    today = {h["metric"]: h for h in got["today"]["habits"]}
    assert today["steps"]["done"] is False and today["manual"]["done"] is None
    assert healthpage.act(m, {"action": "habit", "habit": "h5"})["logged"] == "2 litres of water"
    with pytest.raises(ToolError):
        healthpage.act(m, {"action": "habit", "habit": "h9"})
    assert healthpage.act(m, {"action": "meal", "what": "Paneer wrap", "calories": "520", "protein_g": 30}) == {
        "ok": True, "calories": 520, "protein_g": 30}
    healthpage.act(m, {"action": "meal", "what": "An apple"})                       # nothing typed: nothing counted
    got = healthpage.view(m, env={})
    assert {h["metric"]: h for h in got["today"]["habits"]}["manual"]["done"] is True
    food = got["food"]
    assert food["left"] == {"calories": 1480, "protein_g": 90} and food["targets"] == {"calories": 2000, "protein_g": 120}
    assert {r["what"]: r["calories"] for r in food["recent"]} == {"Paneer wrap": 520, "An apple": None}
    week = {h["habit"]: h for h in got["progress"]["week"]}
    assert week["Walk 8,000 steps"]["of_days"] >= 6 and got["progress"]["start"]["steps"] is not None
    assert healthpage.act(m, {"action": "forget"}) == {"ok": True}
    after = healthpage.view(m, env={})
    assert after["plan"] is None and after["food"]["recent"] == [] and after["measures"]   # the readings stay
    with pytest.raises(ToolError):
        healthpage.act(m, {"action": "nonsense"})


def test_reading_the_ring_needs_it_connected(m, monkeypatch):
    with pytest.raises(ToolError, match="isn't connected"):
        healthpage.act(m, {"action": "read"}, env={})
    monkeypatch.setattr(oura, "linked", lambda memory, env=None: True)
    monkeypatch.setattr(oura, "sync", lambda memory, env=None, fetch=None: 14)
    assert healthpage.act(m, {"action": "read"}, env={}) == {"ok": True, "days": 14}


def test_the_page_calls_no_model():
    source = inspect.getsource(healthpage)
    for name in ("maestro", "claude_client", "free_models", "agent"):
        assert f"import {name}" not in source and f"{name}." not in source
    assert asyncio.iscoroutinefunction(healthpage.view) is False and coach.plan
