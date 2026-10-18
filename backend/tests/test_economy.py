"""Economy mode: cheap chat model, Listen-in first pass and batching."""

import asyncio
import dataclasses
import json
from types import SimpleNamespace

from buddy import agent, ambient
from buddy.memory import Memory


def reply(payload, model):
    return SimpleNamespace(
        stop_reason="end_turn", model=model,
        usage=SimpleNamespace(input_tokens=10, output_tokens=5),
        content=[SimpleNamespace(type="text", text=json.dumps(payload))],
    )


class FakeClaude:
    """Haiku answers the triage; the listen model answers the close look."""

    def __init__(self, attention):
        self.attention = attention
        self.calls = []

    async def create(self, **kw):
        self.calls.append(kw)
        if kw["model"].startswith("claude-haiku"):
            return reply({"attention": self.attention}, kw["model"])
        return reply({"whisper": True, "kind": "translation", "message": "They're asking about the train."}, kw["model"])


def economy(monkeypatch, batch=0.0):
    monkeypatch.setattr(ambient, "settings", dataclasses.replace(
        ambient.settings, listen_triage_model="claude-haiku-5-5",
        listen_model="claude-opus-5-5", listen_batch_seconds=batch,
    ))


def listen(fake, phrases, monkeypatch, wait=0.3):
    monkeypatch.setattr(ambient.client.beta.messages, "create", fake.create)
    heard = []

    async def whisper(kind, text):
        heard.append((kind, text))

    async def scenario():
        listener = ambient.AmbientListener(Memory(":memory:"), whisper)
        for text in phrases:
            listener.hear(text, "es-419")
            await asyncio.sleep(0.01)
        await asyncio.sleep(wait)

    asyncio.run(scenario())
    return heard


def test_quiet_room_stops_at_the_cheap_first_pass(monkeypatch):
    economy(monkeypatch)
    fake = FakeClaude(attention=False)
    heard = listen(fake, ["so the meeting is at three"], monkeypatch)
    assert heard == []
    assert [c["model"] for c in fake.calls] == ["claude-haiku-5-5"]
    assert "fallbacks" not in fake.calls[0]  # Haiku has no server-side fallback


def test_flagged_speech_gets_a_close_look(monkeypatch):
    economy(monkeypatch)
    fake = FakeClaude(attention=True)
    heard = listen(fake, ["¿a qué hora sale el tren?"], monkeypatch)
    assert heard == [("translation", "They're asking about the train.")]
    assert [c["model"] for c in fake.calls] == ["claude-haiku-5-5", "claude-opus-5-5"]
    assert fake.calls[1]["fallbacks"] == "default"


def test_phrases_are_batched_into_one_check(monkeypatch):
    economy(monkeypatch, batch=0.2)
    fake = FakeClaude(attention=False)
    listen(fake, ["uno", "dos", "tres"], monkeypatch, wait=0.5)
    assert len(fake.calls) == 1
    content = fake.calls[0]["messages"][0]["content"]
    assert "uno" in content and "dos" in content and "tres" in content


def test_triage_failure_errs_toward_a_close_look(monkeypatch):
    economy(monkeypatch)
    fake = FakeClaude(attention=True)

    async def flaky(**kw):
        if kw["model"].startswith("claude-haiku"):
            raise RuntimeError("network")
        return await fake.create(**kw)

    monkeypatch.setattr(ambient.client.beta.messages, "create", flaky)
    heard = []

    async def whisper(kind, text):
        heard.append(kind)

    async def scenario():
        listener = ambient.AmbientListener(Memory(":memory:"), whisper)
        listener.hear("hola", "es-419")
        await asyncio.sleep(0.3)

    asyncio.run(scenario())
    assert heard == ["translation"]


def test_chat_runs_on_the_chat_model():
    kwargs = agent._request_kwargs("system")
    assert kwargs["model"] == agent.settings.chat_model == "claude-sonnet-5-5"
    assert kwargs["fallbacks"] == "default"
    assert agent.fallback_kwargs("claude-haiku-5-5") == {}
