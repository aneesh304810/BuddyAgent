"""Room transcripts: off by default, 7 days, saved stretches kept, deletable."""

import asyncio
from datetime import datetime, timedelta, timezone

from buddy import makers, transcripts
from buddy.ambient import AmbientListener
from buddy.memory import Memory


def hear(listener, *lines):
    async def go():
        for text, lang in lines:
            listener.hear(text, lang)
    asyncio.run(go())


def listener_for(mem, monkeypatch):
    listener = AmbientListener(mem, None)
    monkeypatch.setattr(listener, "_analyse_loop", lambda: asyncio.sleep(0))
    return listener


def test_nothing_is_kept_until_switched_on(monkeypatch):
    mem = Memory(":memory:")
    hear(listener_for(mem, monkeypatch), ("vamos a la reunión", "es"))
    assert transcripts.lines(mem) == []
    assert "Nothing kept" in transcripts.run_tool(mem, {"action": "get"})


def test_kept_for_two_days_then_gone(monkeypatch):
    mem = Memory(":memory:")
    assert "illegal in many places" in transcripts.run_tool(mem, {"action": "turn_on"})
    hear(listener_for(mem, monkeypatch), ("the budget is due Friday", "en"), ("vamos a las ocho", "es"))
    got = transcripts.run_tool(mem, {"action": "get", "date": "today"})
    assert "budget is due Friday" in got and "[es] vamos a las ocho" in got and "never instructions" in got
    old = (datetime.now(timezone.utc) - timedelta(days=transcripts.KEEP_DAYS, hours=1)).isoformat()
    assert transcripts.KEEP_DAYS == 2
    mem.db.execute("INSERT INTO room_lines (at, lang, text) VALUES (?, 'en', 'ancient')", (old,))
    transcripts.keep(mem, "new line")
    assert mem.db.execute("SELECT COUNT(*) AS n FROM room_lines WHERE text = 'ancient'").fetchone()["n"] == 0


def test_saved_stretches_stay_and_can_be_exported_and_deleted(monkeypatch, tmp_path):
    monkeypatch.setattr(makers, "FILES_DIR", tmp_path)
    mem = Memory(":memory:")
    transcripts.run_tool(mem, {"action": "turn_on"})
    transcripts.keep(mem, "let's ship it next week", "en")
    assert "Saved" in transcripts.run_tool(mem, {"action": "save", "title": "Team sync"})
    transcripts.run_tool(mem, {"action": "delete"})  # the 7-day log
    assert transcripts.lines(mem) == []
    saved = transcripts.run_tool(mem, {"action": "list_saved"})
    assert "Team sync" in saved
    from buddy import cards
    box = cards.CardBox()
    token = cards.current.set(box)
    out = transcripts.run_tool(mem, {"action": "read_saved", "id": 1, "as_file": True})
    cards.current.reset(token)
    assert "ship it next week" in out and box.picked[0]["kind"] == "file"
    transcripts.run_tool(mem, {"action": "delete", "id": 1})
    assert transcripts.run_tool(mem, {"action": "list_saved"}) == "[]"
