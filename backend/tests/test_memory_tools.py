from datetime import datetime, timedelta, timezone

import pytest

from buddy.memory import Memory
from buddy.tools import ToolError, run_tool


@pytest.fixture
def mem():
    return Memory(":memory:")


def test_remember_and_recall(mem):
    run_tool(mem, "remember_fact", {"fact": "Parked on level 3 of the mall"})
    run_tool(mem, "remember_fact", {"fact": "Sister Priya's birthday is 4 March"})
    assert "level 3" in run_tool(mem, "recall_memory", {"query": "where parked"})
    assert "Priya" in run_tool(mem, "recall_memory", {"query": ""})
    run_tool(mem, "forget_fact", {"text": "parked"})
    assert "Nothing" in run_tool(mem, "recall_memory", {"query": "parked"})


def test_reminder_lifecycle(mem):
    out = run_tool(mem, "set_reminder", {"text": "call Mom", "in_minutes": 0})
    assert out.startswith("Reminder 1 set")
    due = mem.due_reminders(datetime.now(timezone.utc) + timedelta(seconds=1))
    assert [r["text"] for r in due] == ["call Mom"]
    mem.mark_delivered(due[0]["id"])
    assert mem.due_reminders() == []


def test_reminder_at_local_time(mem):
    future = (datetime.now() + timedelta(days=1)).replace(microsecond=0).isoformat()
    assert "set for" in run_tool(mem, "set_reminder", {"text": "gym", "at": future})
    assert "gym" in run_tool(mem, "list_reminders", {})
    assert run_tool(mem, "cancel_reminder", {"id": 1}) == "Cancelled."


def test_reminder_rejects_bad_input(mem):
    with pytest.raises(ToolError):
        run_tool(mem, "set_reminder", {"text": "x"})
    with pytest.raises(ToolError):
        run_tool(mem, "set_reminder", {"text": "x", "at": "tomorrow-ish"})
    with pytest.raises(ToolError):
        run_tool(mem, "set_reminder", {"text": "x", "at": "2001-01-01T10:00"})


def test_goals(mem):
    run_tool(mem, "add_goal", {"goal": "Finish the Buddy MVP"})
    assert "Buddy MVP" in run_tool(mem, "list_goals", {})
    run_tool(mem, "complete_goal", {"id": 1})
    assert run_tool(mem, "list_goals", {}) == "No open goals."
