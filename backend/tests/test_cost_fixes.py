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


def test_repeated_market_scan_reports_are_cleared_to_one_per_day():
    from buddy.finance import purge_reports
    from buddy.jobs import SCHEMA
    m = Memory(":memory:")
    m.db.executescript(SCHEMA)
    for i in range(6):
        m.db.execute("INSERT INTO jobs (kind, title, params, status, created_at, summary, report, findings, sources, "
                     "delivered) VALUES ('market_scan', 'Market scan, Nov 07, 2026', '{}', 'done', datetime('now'), "
                     "?, '', '[]', '[]', 1)", (f"copy {i}",))
    m.db.execute("INSERT INTO jobs (kind, title, params, status, created_at, summary, report, findings, sources, "
                 "delivered) VALUES ('market_scan', 'Market scan, Nov 06, 2026', '{}', 'done', datetime('now'), "
                 "'other day', '', '[]', '[]', 1)")
    purge_reports(m)
    rows = m.db.execute("SELECT title, summary FROM jobs WHERE kind = 'market_scan' ORDER BY title").fetchall()
    assert [(r["title"], r["summary"]) for r in rows] == [("Market scan, Nov 06, 2026", "other day"),
                                                          ("Market scan, Nov 07, 2026", "copy 5")]


def test_an_empty_or_failing_write_is_tried_at_most_3_times_a_day_10_minutes_apart(monkeypatch):
    from datetime import datetime as real, timedelta, timezone
    m = Memory(":memory:")
    calls = []
    clock = [real(2026, 11, 8, 12, 0, tzinfo=timezone.utc)]

    class Clock(real):
        @classmethod
        def now(cls, tz=None):
            return clock[0]
    monkeypatch.setattr(proactive, "datetime", Clock)

    async def empty():
        calls.append(1)
        return ""
    for _ in range(100):  # 100 passes, 15 seconds apart, Claude answering nothing
        asyncio.run(proactive._written_once(m, "briefing:x", empty))
        clock[0] += timedelta(seconds=15)
    assert len(calls) == 3  # not 100

    async def boom():
        calls.append(1)
        raise RuntimeError("overloaded")
    assert asyncio.run(proactive._written_once(m, "reminder:9", boom, plain="Hey, reminder: x.")) == "Hey, reminder: x."
    assert asyncio.run(proactive._written_once(m, "reminder:9", boom, plain="Hey, reminder: x.")) == "Hey, reminder: x."
    assert len(calls) == 4  # the error counted once; the next pass used plain words, no retry


def test_short_reminders_need_no_ai_and_background_writing_is_capped(monkeypatch):
    from buddy import agent
    m = Memory(":memory:")
    assert asyncio.run(proactive.reminder_nudge(m, {"text": "call Amma"})) == "Hey, reminder: call Amma."

    class Fake:
        class beta:
            class messages:
                @staticmethod
                async def create(**kw):
                    raise AssertionError("over the cap: no call")
    m.set(f"compose:briefing:{__import__('datetime').datetime.now(__import__('datetime').timezone.utc):%Y-%m-%dT%H}",
          str(agent.COMPOSE_PER_HOUR))
    monkeypatch.setattr(agent, "client", Fake)
    import pytest
    with pytest.raises(agent.ComposeCapped):
        asyncio.run(agent.compose("s", "i", m, label="briefing"))
