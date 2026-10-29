"""Organiser: to-do lists by voice, and what the briefings say about them."""

import json
from datetime import timedelta

import pytest

from buddy.memory import Memory
from buddy.organiser import Organiser, run_task_tool
from buddy.tools import ToolError, local_now


def test_add_list_complete_delete():
    org = Organiser(Memory(":memory:"))
    run_task_tool(org, "add_task", {"text": "Buy milk", "list": "Shopping"})
    run_task_tool(org, "add_task", {"text": "Pay rent", "due": "today", "priority": "high"})
    run_task_tool(org, "add_task", {"text": "Call dentist", "due": "tomorrow"})
    listed = json.loads(run_task_tool(org, "list_tasks", {}))
    assert [t["text"] for t in listed["tasks"]] == ["Pay rent", "Call dentist", "Buy milk"]
    assert listed["lists"] == {"inbox": 2, "shopping": 1}
    today = json.loads(run_task_tool(org, "list_tasks", {"due_by": "today"}))
    assert [t["text"] for t in today["tasks"]] == ["Pay rent"]
    assert run_task_tool(org, "complete_task", {"task": "milk"}) == "Ticked off: Buy milk."
    assert [t["text"] for t in org.done_today()] == ["Buy milk"]
    assert "Removed" in run_task_tool(org, "delete_task", {"task": "dentist"})
    with pytest.raises(ToolError, match="No open task"):
        org.complete("milk")  # already done


def test_ambiguous_task_asks_which():
    org = Organiser(Memory(":memory:"))
    org.add("Email Priya")
    org.add("Email Raj")
    with pytest.raises(ToolError, match="Which one"):
        org.complete("email")


def test_briefing_lines():
    org = Organiser(Memory(":memory:"))
    yesterday = (local_now().date() - timedelta(days=1)).isoformat()
    org.add("Renew passport", due=yesterday)
    org.add("Gym", due="today")
    org.add("Tax documents", priority="high")
    line = org.morning_line()
    assert "2 due today (1 overdue)" in line and "Also important: Tax documents" in line
    org.complete("gym")
    facts = org.wrap_up_facts()
    assert "Done today: Gym" in facts and "Renew passport" in facts
