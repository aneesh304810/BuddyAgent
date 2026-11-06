"""Free models never get the user's own data: not uploads, not what a tool read this turn."""

import asyncio
from datetime import datetime, timedelta, timezone

from buddy import agent
from buddy.agent import Conversation
from buddy.memory import Memory




def test_general_task_refuses_once_the_turn_used_personal_data(monkeypatch):
    convo = Conversation(Memory(":memory:"))
    sent = []

    async def fake_general(free, args):
        sent.append(args)
        return "done"
    monkeypatch.setattr(agent, "run_general_tool", fake_general)
    convo._personal_turn = False
    assert asyncio.run(convo._dispatch("general_task", {"kind": "summarize", "text": "what is a black hole"})) == "done"
    asyncio.run(convo._dispatch("library", {"action": "list"}))  # the user's files: personal from here on
    out = asyncio.run(convo._dispatch("general_task", {"kind": "summarize", "text": "Hemoglobin 11.2, ferritin low"}))
    assert out.startswith("Not sent") and len(sent) == 1


def test_web_tools_keep_free_models_available(monkeypatch):
    convo = Conversation(Memory(":memory:"))
    convo._personal_turn = False

    async def fake_general(free, args):
        return "done"
    monkeypatch.setattr(agent, "run_general_tool", fake_general)
    asyncio.run(convo._dispatch("transit", {"action": "list"}))
    assert asyncio.run(convo._dispatch("general_task", {"kind": "rewrite", "text": "hello"})) == "done"


def test_messages_right_after_an_upload_stay_on_claude():
    memory = Memory(":memory:")
    assert not agent._just_uploaded(memory)
    from buddy.library import Library
    Library(memory).add("labs.pdf", b"%PDF-1.4 test")
    assert agent._just_uploaded(memory)
    memory.set("library:last_added", (datetime.now(timezone.utc) - timedelta(minutes=20)).isoformat())
    assert not agent._just_uploaded(memory)
