"""Learned pause timing: fragments split by the owner's usual pause are one request."""

import asyncio

from buddy import pausehabit, server
from buddy.memory import Memory


def test_wait_grows_with_the_owners_usual_pause_and_is_visible_as_a_setting():
    mem = Memory(":memory:")
    assert pausehabit.wait_seconds(mem, 3) == 3
    for gap in (4.0, 4.4, 3.8):
        pausehabit.record_gap(mem, gap)
    pausehabit.record_interrupt(mem)
    assert pausehabit.wait_seconds(mem, 3) > 5
    assert pausehabit.load(mem)["merged"] == 3 and mem.get("pause_habit")  # a stored setting
    assert pausehabit.wait_seconds(mem, 3) <= pausehabit.MAX_WAIT


def test_two_fragments_a_typical_pause_apart_become_one_request_and_teach_the_habit(monkeypatch):
    import base64
    from fastapi.testclient import TestClient
    from buddy import agent, whisper
    from tests.test_server import FakeStream

    mem = Memory(":memory:")
    monkeypatch.setattr(server, "memory", mem)
    pausehabit.record_gap(mem, 4.0)  # this owner usually pauses ~4 s: the wait is already longer than 3 s
    assert pausehabit.wait_seconds(mem, 3) >= 6
    heard = iter(["Why can't we use...", "the free model for the news?"])

    async def fake_transcribe(pcm, language="", prompt="", **kwargs):
        return next(heard), "en"

    asked = []

    def fake_stream(**kw):
        asked.append(kw["messages"][-1]["content"])
        return FakeStream("Because the news is live.")

    monkeypatch.setattr(whisper, "transcribe", fake_transcribe)
    monkeypatch.setattr(agent.client.beta.messages, "stream", fake_stream)
    monkeypatch.setattr(server, "FRAGMENT_WAIT_SECONDS", 30)
    audio = base64.b64encode(b"\x01\x00" * 800).decode()
    with TestClient(server.app) as c, c.websocket_connect("/ws") as ws:
        ws.send_json({"type": "audio", "pcm": audio, "lang": "en-US", "mode": "talk"})
        ws.send_json({"type": "user", "text": ws.receive_json()["text"]})
        assert ws.receive_json() == {"type": "waiting"}
        ws.send_json({"type": "audio", "pcm": audio, "lang": "en-US", "mode": "talk"})
        ws.send_json({"type": "user", "text": ws.receive_json()["text"]})
        got = [ws.receive_json() for _ in range(3)]
    assert [m["type"] for m in got] == ["thinking", "say", "done"]
    assert "Why can't we use the free model for the news?" in str(asked[-1])  # one request, not two
    assert pausehabit.load(mem)["merged"] == 2  # the new pause was learned too
