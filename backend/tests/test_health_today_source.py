"""The Health page's Today tiles say which night they are from (today's, or yesterday's when today's isn't in yet)
and where each number came from (the Oura Ring first, then WHOOP, then the phone), and opening the page reads the
ring when the last read is more than 20 minutes old."""

import json
from datetime import datetime, timedelta, timezone

import pytest

from buddy import health_data, healthpage, oura
from buddy.connectors.hub import ConnectorHub
from buddy.memory import Memory
from buddy.tools import local_now


@pytest.fixture
def memory(tmp_path):
    m = Memory(tmp_path / "buddy.db")
    ConnectorHub(m)
    return m


def _oura(memory, day, **values):
    oura._ready(memory)
    memory.db.execute(f"INSERT OR REPLACE INTO oura_daily (day, {', '.join(values)}, workouts, updated_at) VALUES "
                      f"(?, {', '.join('?' for _ in values)}, '[]', ?)", (day, *values.values(), "2026-11-27"))
    memory.db.commit()


def _phone(memory, day, **values):
    health_data._ready(memory)
    memory.db.execute(f"INSERT OR REPLACE INTO health_daily (day, {', '.join(values)}, workouts, updated_at) VALUES "
                      f"(?, {', '.join('?' for _ in values)}, '[]', ?)", (day, *values.values(), "2026-11-27"))
    memory.db.commit()


def test_the_tiles_say_which_night_and_where_each_number_came_from(memory):
    now = local_now()
    today, yesterday = now.date().isoformat(), (now.date() - timedelta(days=1)).isoformat()
    _oura(memory, today, readiness=62, sleep_score=55, hrv=14, lowest_hr=71)
    _phone(memory, today, sleep_minutes=228)  # the ring's night isn't processed yet; the phone's sleep is
    t = healthpage.view(memory, now=now)["today"]
    assert t["day"] == today == t["today"]
    assert t["sources"]["readiness"] == "Oura Ring" and t["sources"]["sleep_minutes"] == "your phone (Health Connect)"
    _oura(memory, today, readiness=62, sleep_score=55, hrv=14, lowest_hr=71, sleep_minutes=410)
    assert healthpage.view(memory, now=now)["today"]["sources"]["sleep_minutes"] == "Oura Ring"  # the ring first


def test_yesterdays_numbers_are_said_to_be_yesterdays(memory):
    now = local_now()
    yesterday = (now.date() - timedelta(days=1)).isoformat()
    _oura(memory, yesterday, readiness=80, hrv=30)
    t = healthpage.view(memory, now=now)["today"]
    assert t["day"] == yesterday and t["today"] != yesterday and t["readings"]["readiness"] == 80


def test_opening_the_page_reads_the_ring_when_the_last_read_is_old(memory, monkeypatch):
    calls = []
    monkeypatch.setattr(oura, "linked", lambda m, env=None: True)
    monkeypatch.setattr(oura, "sync", lambda m, n=None, env=None, fetch=None, kind="": calls.append(1) or 1)
    memory.set(oura.SYNC_KEY, (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat())
    healthpage.view(memory)
    assert calls == []  # read 5 minutes ago
    memory.set(oura.SYNC_KEY, (datetime.now(timezone.utc) - timedelta(minutes=45)).isoformat())
    healthpage.view(memory)
    assert calls == [1]  # 45 minutes ago: read again (the background job waits 3 hours)
