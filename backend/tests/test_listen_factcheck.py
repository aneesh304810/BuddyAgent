"""Listen-in: general claims fact-checked on free models (two must agree), and conversation coaching."""

import asyncio
import json

from buddy import ambient, tutor
from buddy.memory import Memory


class FakeFree:
    """Two free providers with set answers."""
    def __init__(self, memory, answers):
        self.memory = memory
        self.answers = answers
        self._last_model = "m"

    def enabled(self):
        return list(self.answers)

    def order(self, kind):
        return list(self.answers)

    def model_of(self, name, kind=""):
        return "m"

    async def run(self, kind, text, system=None, purpose="", claude_fallback=True, **kw):
        name = list(self.answers)[0]
        return {"answer": self.answers[name], "model": f"{name}/m"}

    def _call(self, name, system, prompt, timeout, max_tokens):
        return self.answers[name], {}


def verdict(v, conf="high"):
    return json.dumps({"checks": [{"claim": "Humans use only 10% of their brains.", "verdict": v,
                                   "confidence": conf, "correct": "People use virtually all of the brain.",
                                   "ask": "Where did you read that?"}]})


def run_check(monkeypatch, answers):
    m = Memory(":memory:")
    import buddy.free_models as fm
    monkeypatch.setattr(fm, "FreeModels", lambda memory: FakeFree(memory, answers))
    return asyncio.run(ambient.fact_check(m, ["Humans use only 10% of their brains."]))


def test_a_false_claim_is_agreed_only_when_a_second_free_model_says_so(monkeypatch):
    agreed = run_check(monkeypatch, {"gemini": verdict("false"), "github": verdict("false", "medium")})
    assert agreed[0]["agreed"] is True
    split = run_check(monkeypatch, {"gemini": verdict("false"), "github": verdict("unsure")})
    assert split[0]["agreed"] is False  # the free models disagree: say nothing


def test_the_whisper_gives_the_fact_and_a_polite_question(monkeypatch):
    said = []

    async def whisper(kind, text):
        said.append((kind, text))
    listener = ambient.AmbientListener.__new__(ambient.AmbientListener)
    listener.memory, listener.whisper, listener._fact_at = Memory(":memory:"), whisper, 0.0
    listener.whispered = __import__("collections").deque(maxlen=8)

    async def fake(memory, claims):
        return [{"verdict": "false", "agreed": True, "correct": "People use virtually all of the brain.",
                 "ask": "Where did you read that?"}]
    monkeypatch.setattr(ambient, "fact_check", fake)
    asyncio.run(listener._fact_check(["x"]))
    assert said == [("correction", "Not quite. People use virtually all of the brain. You could ask: Where did you "
                                   "read that?")]


def test_conversation_coaching_keeps_only_your_lines_when_on():
    m = Memory(":memory:")
    tutor.log_utterance(m, "I am agree with you about the plan", "room")
    assert tutor.sentences(m, 1) == []  # off by default
    tutor.run_tutor_tool(m, None, {"action": "coach_on"})
    tutor.log_utterance(m, "I am agree with you about the plan", "room")
    tutor.log_utterance(m, "remind me to call mum tomorrow", "user")
    assert tutor.sentences(m, 1) == ["(with people) I am agree with you about the plan",
                                     "remind me to call mum tomorrow"]
    assert "deleted" in tutor.run_tutor_tool(m, None, {"action": "coach_off"})
    assert tutor.sentences(m, 1) == ["remind me to call mum tomorrow"]


def test_a_plan_heard_in_the_room_gets_a_reminder_offer_once():
    said = []

    async def whisper(kind, text):
        said.append((kind, text))
    listener = ambient.AmbientListener.__new__(ambient.AmbientListener)
    listener.whisper, listener._plan_at = whisper, 0.0
    listener.whispered = __import__("collections").deque(maxlen=8)
    listener.offered = __import__("collections").deque(maxlen=30)
    asyncio.run(listener._offer_reminder([{"what": "Aishan's soccer", "when": "today at 10"}]))
    assert said == [("plan", "Sounds like a plan: Aishan's soccer, today at 10. Want a reminder? Say \"Hey Buddy, "
                             "remind me\".")]
    listener._plan_at = 0.0
    asyncio.run(listener._offer_reminder([{"what": "Aishan's soccer", "when": "today at 10"}]))
    assert len(said) == 1  # not offered twice
