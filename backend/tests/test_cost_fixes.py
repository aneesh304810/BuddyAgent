"""Cost fixes: nudges written once (not on every 15-second pass while the phone is away); failing free models rest."""

import asyncio

from buddy import proactive
from buddy.memory import Memory


def test_a_reminder_is_written_once_while_the_phone_is_away(monkeypatch):
    m = Memory(":memory:")
    calls = []

    async def write():
        calls.append(1)
        return "Hey, time to call Amma."
    for _ in range(5):  # five passes, phone not connected: one AI call
        assert asyncio.run(proactive._written_once(m, "reminder:7", write)) == "Hey, time to call Amma."
    assert len(calls) == 1
    proactive._sent(m, "reminder:7")
    asyncio.run(proactive._written_once(m, "reminder:7", write))
    assert len(calls) == 2  # delivered and cleared: a new one is written fresh


def test_nothing_is_written_while_no_phone_is_connected(monkeypatch):
    m = Memory(":memory:")
    from datetime import datetime, timezone
    m.add_reminder("call Amma", datetime(2020, 1, 1, tzinfo=timezone.utc))
    wrote = []

    async def fake_nudge(memory, reminder):
        wrote.append(reminder)
        return "x"
    monkeypatch.setattr(proactive, "reminder_nudge", fake_nudge)
    monkeypatch.setattr(proactive, "briefing_due", lambda memory: True)

    async def fake_brief(memory):
        wrote.append("brief")
        return "b"
    monkeypatch.setattr(proactive, "morning_briefing", fake_brief)

    async def deliver(kind, text, **_):
        return False

    async def one_pass():
        task = asyncio.create_task(proactive.run(m, deliver, connected=lambda: False))
        await asyncio.sleep(0.2)
        task.cancel()
    asyncio.run(one_pass())
    assert wrote == []


def test_a_failing_free_model_rests():
    from buddy.free_models import FreeModels
    m = Memory(":memory:")
    f = FreeModels(m, env={})
    for _ in range(5):
        f._failed("github")
    assert f.resting("github")
    assert FreeModels(m, env={}).resting("github")  # remembered by a new client too
    f2 = FreeModels(m, env={})
    f2._worked("groq")
    assert not f2.resting("groq")
