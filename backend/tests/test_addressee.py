"""Listen-in: telling sentences for Buddy apart from room talk."""

import asyncio
import json
from dataclasses import replace
from types import SimpleNamespace

from fastapi.testclient import TestClient

from buddy import addressee, agent, server
from buddy.memory import Memory


def fake_model(answer):
    calls = []

    async def create(**kw):
        calls.append(kw)
        usage = SimpleNamespace(input_tokens=300, output_tokens=20, cache_read_input_tokens=0,
                                cache_creation_input_tokens=0, server_tool_use=None)
        return SimpleNamespace(model="claude-haiku-5-5", usage=usage, stop_reason="end_turn",
                               content=[SimpleNamespace(type="text", text=json.dumps(answer))])
    return SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(create=create))), calls


def test_what_gets_checked(monkeypatch):
    monkeypatch.setattr(addressee, "settings", replace(addressee.settings, smart_addressing=True))
    assert addressee.worth_checking("hey can you hear me", 9999)          # sounds like it's for an assistant
    assert addressee.worth_checking("and tomorrow then", 40)              # a reply soon after Buddy spoke
    assert not addressee.worth_checking("the camera was off again", 9999)  # room talk, long after
    assert not addressee.worth_checking("okay", 10)                       # too short to judge


def test_only_confident_yes_counts(monkeypatch):
    for answer, expected in [({"to_assistant": True, "confidence": "high"}, True),
                             ({"to_assistant": True, "confidence": "medium"}, False),
                             ({"to_assistant": False, "confidence": "high"}, False)]:
        fake, calls = fake_model(answer)
        monkeypatch.setattr(addressee, "client", fake)
        got = asyncio.run(addressee.is_for_buddy(Memory(":memory:"), "and tomorrow?", "Sunny today in Lima.", 25,
                                                 ["so the meeting is at three"]))
        assert got is expected
        assert "Sunny today in Lima." in calls[0]["messages"][0]["content"]


def test_follow_up_without_wake_word_is_answered(monkeypatch):
    monkeypatch.setattr(addressee, "settings", replace(addressee.settings, smart_addressing=True))
    fake, _ = fake_model({"to_assistant": True, "confidence": "high"})
    monkeypatch.setattr(addressee, "client", fake)

    async def fake_reply(self, text, say, via="user"):
        await say(f"You asked: {text}")

    monkeypatch.setattr(agent.Conversation, "reply", fake_reply)
    with TestClient(server.app) as c, c.websocket_connect("/ws") as ws:
        ws.send_json({"type": "ambient", "text": "hey can you hear me", "lang": "en-US"})
        got = [ws.receive_json() for _ in range(3)]
    assert [m["type"] for m in got] == ["thinking", "say", "done"]
    assert got[1]["text"] == "You asked: hey can you hear me"
