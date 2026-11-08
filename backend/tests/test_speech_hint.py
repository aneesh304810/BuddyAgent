"""A corrected word (learn_word) tells the phone to prefer Whisper for a few days."""

import asyncio

from buddy.agent import Conversation
from buddy.memory import Memory


def test_learn_word_sends_a_speech_hint():
    convo = Conversation(Memory(":memory:"))
    hints = []

    async def hint(days=3):
        hints.append(days)
    convo.speech_hint = hint
    out = asyncio.run(convo._dispatch("learn_word", {"word": "Miraflores", "heard_as": "mirror flowers"}))
    assert "Miraflores" in out and hints == [3]


def test_english_only_talk_is_transcribed_as_english(monkeypatch):
    from buddy import whisper
    seen = {}

    def fake(pcm, hint, prompt, stats, allowed, talk, meta):
        seen["hint"] = hint
        return "hello", hint or ""
    monkeypatch.setattr(whisper, "_transcribe", fake)
    monkeypatch.setattr(whisper, "groq_on", lambda: False)
    monkeypatch.setattr(whisper, "known_languages", lambda: ["en"])
    asyncio.run(whisper.transcribe(b"\0" * 64000, "", mode="talk"))
    assert seen["hint"] == "en"
    asyncio.run(whisper.transcribe(b"\0" * 64000, "", mode="ambient"))
    assert seen["hint"] is None  # Listen-in still detects: other people may speak other languages
    monkeypatch.setattr(whisper, "known_languages", lambda: ["en", "ml"])
    asyncio.run(whisper.transcribe(b"\0" * 64000, "", mode="talk"))
    assert seen["hint"] is None
