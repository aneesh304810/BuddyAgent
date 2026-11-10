"""Free time, meeting proposals from calls Buddy answered, and adding events to the phone's calendar after a yes."""

import asyncio
import json
from datetime import datetime, timedelta

from buddy import calls, scheduling
from buddy.memory import Memory

MON = datetime(2026, 11, 16, 8, 0)  # a Monday morning


def test_free_time_skips_meetings_commutes_weekends_and_the_past(tmp_path, monkeypatch):
    m = Memory(tmp_path / "b.db")
    busy = {MON.date(): [(MON.replace(hour=10), MON.replace(hour=11, minute=30))],
            (MON + timedelta(days=1)).date(): [(MON.replace(day=17, hour=9), MON.replace(day=17, hour=18))]}
    monkeypatch.setattr(scheduling, "_busy", lambda memory, day: busy.get(day, []))
    slots = scheduling.free_slots(m, days=6, now=MON, env={}, limit=10)
    assert slots[0] == (MON.replace(hour=9), MON.replace(hour=10))
    assert slots[1] == (MON.replace(hour=11, minute=30), MON.replace(hour=18))
    assert all(a.date() != MON.replace(day=17).date() for a, _ in slots)  # Tuesday is booked solid
    assert all(a.weekday() < 5 for a, _ in slots)
    later = scheduling.free_slots(m, days=0, now=MON.replace(hour=14, minute=10), env={})
    assert later[0][0] == MON.replace(hour=15)  # 30 minutes from now, rounded up to the half hour
    assert scheduling.free_slots(m, days=0, now=MON, env={"BUDDY_MEETING_HOURS": "08:00-08:45"}) == []  # too soon
    text = scheduling.describe(slots[:2])
    assert text == "Mon Nov 16: 9 am to 10 am; Mon Nov 16: 11:30 am to 6 pm"


def test_meetings_from_a_call_are_only_pencilled_in(tmp_path, monkeypatch):
    monkeypatch.setenv("BUDDY_OWNER_FULL_NAME", "Sam Rivera")
    m = Memory(tmp_path / "b.db")
    call = calls.screen_call(m, {"From": "12015550111", "CallUUID": "c1"})
    prompt = calls.system_prompt(m, call)
    assert "offer two of Sam's free times" in prompt and "Never promise it's booked" in prompt
    move = calls.parse(json.dumps({"say": "Perfect", "action": "end", "priority": "normal", "caller": "Priya",
                                   "meeting": {"title": "lunch planning", "start": "2026-11-19 14:00", "minutes": 30},
                                   "outcome": "Wants to plan the team lunch."}))
    assert move["meeting"] == {"title": "lunch planning", "start": "2026-11-19T14:00:00", "minutes": 30}
    calls.screened(m, call, move)
    [p] = scheduling.pending(m)
    assert p["who"] == "Priya" and p["status"] == "pending"
    told = calls.outcome_message(call, move)
    assert "Thursday Nov 19, 2 pm to 2:30 pm (lunch planning)" in told and "add it to my calendar" in told
    assert calls.parse('{"say": "x", "meeting": {"start": "soon"}}')["meeting"] is None


class Thrift:
    def __init__(self):
        self.asked = []

    def ask_action(self, question, run):
        self.asked.append((question, run))
        return "APPROVAL NEEDED"


def test_adding_to_the_calendar_needs_a_yes_and_the_phone(tmp_path):
    m = Memory(tmp_path / "b.db")
    scheduling.propose(m, {"title": "lunch planning", "start": "2026-11-19T14:00:00", "minutes": 30}, "Priya",
                       "+12015550111")
    sent, t = [], Thrift()

    async def phone(message, timeout=25):
        sent.append(message)
        return {"ok": True}
    assert asyncio.run(scheduling.run_tool(m, t, phone, {"action": "proposal"})) == "APPROVAL NEEDED"
    assert not sent  # nothing until the owner says yes
    question, run = t.asked[0]
    assert question == ('Add "lunch planning (Priya)" on Thursday Nov 19, 2 pm to 2:30 pm to your phone calendar?')
    assert asyncio.run(run()).startswith("Added to your calendar")
    assert sent[0]["type"] == "calendar_add" and sent[0]["end_ms"] - sent[0]["start_ms"] == 30 * 60_000
    assert scheduling.pending(m) == []

    async def refused(message, timeout=25):
        return {"ok": False, "error": "permission"}
    asyncio.run(scheduling.run_tool(m, t, refused, {"action": "event", "title": "Dentist", "start": "2026-11-20 09:00",
                                                     "minutes": 45}))
    assert "Permissions > Calendar" in asyncio.run(t.asked[-1][1]())
    assert asyncio.run(scheduling.run_tool(m, t, phone, {"action": "proposal"})) == "No meeting requests waiting."


def test_declining_and_free_times(tmp_path, monkeypatch):
    m = Memory(tmp_path / "b.db")
    scheduling.propose(m, {"title": "demo", "start": "2026-11-19T10:00:00", "minutes": 60}, "Raj", "+12015550222")
    out = asyncio.run(scheduling.run_tool(m, None, None, {"action": "decline"}))
    assert "Dropped Raj's request" in out and scheduling.pending(m) == []
    monkeypatch.setattr(scheduling, "free_slots", lambda memory, minutes=30, **k: [(MON.replace(hour=9),
                                                                                    MON.replace(hour=10))])
    assert asyncio.run(scheduling.run_tool(m, None, None, {"action": "free"})).endswith("Mon Nov 16: 9 am to 10 am")
