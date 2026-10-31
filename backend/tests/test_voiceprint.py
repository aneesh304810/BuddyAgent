"""Voice match: Talk mode answers only the user's voice, and stays quiet when unsure."""

import json

from buddy import voiceprint
from buddy.memory import Memory

SECOND = b"\x01\x00" * 16000


def voice(*direction):
    """A fake embedder: each 'speaker' is a direction; the clip's first byte picks it."""
    def embed(pcm):
        return voiceprint._unit(list(direction))
    return embed


ME, OTHER = voice(1.0, 0.1, 0.0), voice(0.0, 1.0, 0.2)


def test_enrol_then_only_my_voice_counts():
    mem = Memory(":memory:")
    result = voiceprint.enroll(mem, [SECOND * 3] * 5, embedder=ME)
    assert result["ok"] and result["used"] == 5 and voiceprint.on(mem)
    assert voiceprint.check(mem, SECOND * 2, since_buddy_spoke=999, embedder=ME)[0] == "you"
    who, score = voiceprint.check(mem, SECOND * 2, since_buddy_spoke=999, embedder=OTHER)
    assert who == "not_you" and score < voiceprint.THRESHOLD


def test_too_little_speech_to_enrol():
    mem = Memory(":memory:")
    result = voiceprint.enroll(mem, [b"\x00\x00" * 100] * 5, embedder=ME)
    assert not result["ok"] and not voiceprint.on(mem)


def test_short_clips_pass_only_right_after_buddy_spoke():
    mem = Memory(":memory:")
    voiceprint.enroll(mem, [SECOND * 3] * 5, embedder=ME)
    half = b"\x01\x00" * 8000
    assert voiceprint.check(mem, half, since_buddy_spoke=5, embedder=OTHER)[0] == "you"  # "thanks" after a reply
    assert voiceprint.check(mem, half, since_buddy_spoke=120, embedder=ME)[0] == "short"  # out of the blue: quiet


def test_that_was_me_and_forget():
    mem = Memory(":memory:")
    voiceprint.enroll(mem, [SECOND * 3] * 5, embedder=ME)
    voiceprint.check(mem, SECOND * 2, since_buddy_spoke=999, embedder=OTHER)
    before = json.loads(mem.get(voiceprint.KEY))["samples"]
    assert "added" in voiceprint.that_was_me(mem)
    assert json.loads(mem.get(voiceprint.KEY))["samples"] == before + 2
    assert "Deleted" in voiceprint.run_tool(mem, {"action": "forget"})
    assert not voiceprint.on(mem)
    assert voiceprint.check(mem, SECOND * 2, since_buddy_spoke=999, embedder=OTHER)[0] == "you"  # no print: all pass


def test_no_voiceprint_means_everyone_passes():
    assert voiceprint.check(Memory(":memory:"), SECOND, 999, embedder=OTHER) == ("you", None)
