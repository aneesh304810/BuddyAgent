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


def agreed() -> Memory:
    """A memory where the user ticked the box and tapped I agree on the consent screen."""
    mem = Memory(":memory:")
    assert voiceprint.record_consent(mem, voiceprint.POLICY_VERSION)
    return mem


def test_enrol_then_only_my_voice_counts():
    mem = agreed()
    result = voiceprint.enroll(mem, [SECOND * 3] * 5, embedder=ME)
    assert result["ok"] and result["used"] == 5 and voiceprint.on(mem)
    assert voiceprint.check(mem, SECOND * 2, since_buddy_spoke=999, embedder=ME)[0] == "you"
    who, score = voiceprint.check(mem, SECOND * 2, since_buddy_spoke=999, embedder=OTHER)
    assert who == "not_you" and score < voiceprint.THRESHOLD


def test_too_little_speech_to_enrol():
    mem = agreed()
    result = voiceprint.enroll(mem, [b"\x00\x00" * 100] * 5, embedder=ME)
    assert not result["ok"] and not voiceprint.on(mem)


def test_very_short_clips_pass_only_right_after_buddy_spoke():
    mem = agreed()
    voiceprint.enroll(mem, [SECOND * 3] * 5, embedder=ME)
    tiny = b"\x01\x00" * 4000  # a quarter of a second of speech
    keep = lambda pcm: pcm  # noqa: E731  (no silence to cut in these clips)
    assert voiceprint.check(mem, tiny, since_buddy_spoke=5, embedder=OTHER, trim=keep)[0] == "you"  # "ok" after a reply
    assert voiceprint.check(mem, tiny, since_buddy_spoke=120, embedder=ME, trim=keep)[0] == "short"  # out of the blue


def test_a_lone_buddy_is_judged_on_its_speech_with_a_stricter_bar():
    mem = agreed()
    voiceprint.enroll(mem, [SECOND * 3] * 5, embedder=ME)
    clip = b"\x00\x00" * 16000 + b"\x01\x00" * 9600  # silence, then 0.6 s of "Buddy"
    only_speech = lambda pcm: pcm[32000:]  # noqa: E731
    seen = []
    def embed(pcm):
        seen.append(len(pcm))
        return ME(pcm)
    who, score = voiceprint.check(mem, clip, since_buddy_spoke=999, embedder=embed, trim=only_speech)
    assert who == "you" and seen == [19200]  # judged on the 0.6 s of speech, not the silence
    near = voice(1.0, 1.6, 0.0)  # scores about 0.61: enough for a long clip, not for a short one
    assert voiceprint.check(mem, clip, 999, embedder=near, trim=only_speech)[0] == "not_you"
    long_clip = b"\x01\x00" * 32000
    assert voiceprint.check(mem, long_clip, 999, embedder=near, trim=lambda pcm: pcm)[0] == "you"


def test_that_was_me_and_forget():
    mem = agreed()
    voiceprint.enroll(mem, [SECOND * 3] * 5, embedder=ME)
    voiceprint.check(mem, SECOND * 2, since_buddy_spoke=999, embedder=OTHER)
    before = json.loads(mem.get(voiceprint.KEY))["samples"]
    assert "added" in voiceprint.that_was_me(mem)
    assert json.loads(mem.get(voiceprint.KEY))["samples"] == before + 2
    assert "deleted" in voiceprint.run_tool(mem, {"action": "forget"})
    assert not voiceprint.on(mem)
    assert voiceprint.check(mem, SECOND * 2, since_buddy_spoke=999, embedder=OTHER)[0] == "you"  # no print: all pass


def test_no_voiceprint_means_everyone_passes():
    assert voiceprint.check(Memory(":memory:"), SECOND, 999, embedder=OTHER) == ("you", None)


def test_no_voiceprint_without_written_consent_to_this_policy():
    mem = Memory(":memory:")
    result = voiceprint.enroll(mem, [SECOND * 3] * 5, embedder=ME)
    assert not result["ok"] and "consent" in result["why"] and not voiceprint.stored(mem)
    assert not voiceprint.record_consent(mem, "1999-01-01")  # an old version of the policy doesn't count
    assert voiceprint.record_consent(mem, voiceprint.POLICY_VERSION)
    assert voiceprint.enroll(mem, [SECOND * 3] * 5, embedder=ME)["ok"]
    assert voiceprint.consents(mem)[-1]["version"] == voiceprint.POLICY_VERSION


def test_turning_voice_match_off_destroys_the_voiceprint_and_keeps_the_consent_record():
    mem = agreed()
    voiceprint.enroll(mem, [SECOND * 3] * 5, embedder=ME)
    voiceprint.run_tool(mem, {"action": "turn_off"})
    assert voiceprint.stored(mem) is None and not voiceprint.on(mem)
    record = voiceprint.consents(mem)[-1]
    assert record["withdrawn"] and "vector" not in json.dumps(record)  # proof of consent, no biometric data
    assert not voiceprint.consented(mem)  # teaching it again asks for consent again
    assert not voiceprint.enroll(mem, [SECOND * 3] * 5, embedder=ME)["ok"]


def test_a_voiceprint_unused_for_twelve_months_is_destroyed():
    import time
    mem = agreed()
    voiceprint.enroll(mem, [SECOND * 3] * 5, embedder=ME)
    assert not voiceprint.purge_unused(mem)
    later = time.time() + (voiceprint.UNUSED_DAYS + 2) * 86400
    assert voiceprint.purge_unused(mem, today=later) and voiceprint.stored(mem) is None


def test_deleted_data_is_overwritten_on_disk():
    mem = Memory(":memory:")
    assert mem.db.execute("PRAGMA secure_delete").fetchone()[0] == 1
