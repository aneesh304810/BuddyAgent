"""Today: the home strip (next thing, next payment) and the bell's count of new alerts."""

from datetime import datetime, timedelta, timezone

import pytest

from buddy import attention, today
from buddy.connectors.hub import ConnectorHub
from buddy.memory import Memory
from buddy.tools import local_now


@pytest.fixture
def memory(tmp_path):
    m = Memory(tmp_path / "buddy.db")
    ConnectorHub(m)
    return m


def test_an_empty_day_shows_nothing_and_does_not_fail(memory):
    out = today.strip(memory)
    assert out["next"] is None and out["due"] is None and out["alerts"]["new"] == 0 and out["as_of"]


def test_the_next_reminder_shows_with_its_day_time_and_source(memory):
    now = local_now().replace(hour=9, minute=0, second=0, microsecond=0)
    memory.add_reminder("Dentist", now + timedelta(days=9))  # too far off
    memory.add_reminder("Call the school", now + timedelta(hours=6, minutes=30))
    got = today.strip(memory, now)["next"]
    assert got == {"title": "Call the school", "when": "Today, 3:30 PM", "source": "Your reminder"}


def test_the_bell_counts_what_was_sent_since_alerts_was_last_opened(memory):
    now = datetime.now(timezone.utc)
    attention.plan(memory, "money", "🏦 New fund filings", now=now - timedelta(hours=2))
    attention.plan(memory, "reminder", "Reminder: call the school", now=now - timedelta(hours=1))
    counted = today.strip(memory)["alerts"]["new"]
    loud = memory.db.execute("SELECT COUNT(*) AS n FROM alerts WHERE level IN ('notify', 'alert')").fetchone()["n"]
    assert counted == loud >= 1  # silenced kinds are not counted
    assert today.seen(memory) == {"new": 0}
    assert today.strip(memory)["alerts"]["new"] == 0
    attention.plan(memory, "reminder", "Reminder: pick up the parcel", now=datetime.now(timezone.utc) + timedelta(seconds=1))
    assert today.strip(memory)["alerts"]["new"] == 1


def test_one_failing_part_does_not_take_the_strip_down(memory, monkeypatch):
    monkeypatch.setattr(today, "_due", lambda *a: 1 / 0)
    out = today.strip(memory)
    assert out["due"] is None and "alerts" in out
