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
