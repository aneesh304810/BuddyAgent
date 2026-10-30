"""English tutor, speech vocabulary and recognition-friendly understanding."""

import asyncio
import json
from types import SimpleNamespace

from buddy import agent, tutor, vocabulary
from buddy.jobs import JobContext, JobRunner
from buddy.memory import Memory


def test_vocabulary_learns_corrections_and_memory_names():
    mem = Memory(":memory:")
    mem.remember("My sister Priya lives in Miraflores")
    vocabulary.learn(mem, "Barranco")
    vocabulary.learn(mem, "barranco")  # no duplicates; latest spelling first
    words = vocabulary.words(mem)
    assert words[0] == "barranco" and "Priya" in words and "Miraflores" in words and "My" not in words
    assert "Priya" in vocabulary.whisper_prompt(mem)


def test_utterances_and_speech_are_kept_for_the_tutor():
    mem = Memory(":memory:")
    tutor.log_utterance(mem, "I am go to the market yesterday", "user")
    tutor.log_utterance(mem, "ok", "user")  # too short to learn from
    assert tutor.sentences(mem, 1) == ["I am go to the market yesterday"]
    tutor.log_speech(mem, {"words": 12, "seconds": 3.0, "clarity": 0.8, "unclear": ["comfortable"]}, "en")
    tutor.log_speech(mem, {"words": 8, "seconds": 2.0, "clarity": 0.6, "unclear": ["comfortable", "vegetable"]}, "en")
    s = tutor.speech_summary(mem, 1)
    assert s["words_per_second"] == 4.0 and s["unclear_words"][0] == "comfortable (2x)"


def test_review_is_written_with_grammar_pronunciation_pace_and_tone(monkeypatch):
    mem = Memory(":memory:")
    JobRunner(mem, None)
    for s in ["I am go to the market yesterday", "She don't like it", "Can you remind me to call him"]:
        tutor.log_utterance(mem, s, "user")
    tutor.log_speech(mem, {"words": 30, "seconds": 7.5, "clarity": 0.7, "unclear": ["comfortable"]})
    review = {"summary": "Good progress.", "level": "B1", "score": 6, "strengths": ["Clear requests"],
              "corrections": [{"you_said": "I am go", "better": "I went", "why": "past tense"}],
              "patterns": [{"pattern": "Past tense", "rule": "Use the past form", "example": "I went"}],
              "vocabulary": [], "pronunciation": [{"word": "comfortable", "say_it": "KUMF-ter-bul", "tip": "3 syllables"}],
              "pace": "A little fast: 4 words a second.", "tone": "Friendly and direct.",
              "tips": ["Slow down a bit"], "practice": ["Say 3 sentences about yesterday"]}
    asked = {}

    async def create(**kw):
        asked.update(kw)
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=json.dumps(review))], stop_reason="end_turn",
                               model="claude-haiku-5-5", usage=SimpleNamespace(input_tokens=10, output_tokens=5))
    monkeypatch.setattr(tutor.client.beta.messages, "create", create)
    mem.db.execute("INSERT INTO jobs (id, kind, title, params, status, created_at) VALUES (1, 'tutor', 't', '{}', "
                   "'running', '2026')")
    job = {"id": 1, "title": "English review", "params": {"days": 1}}
    result = asyncio.run(tutor.run_review(job, JobContext(mem, job)))
    prompt = asked["messages"][0]["content"]
    assert "She don't like it" in prompt and "comfortable (1x)" in prompt and "words_per_second" in prompt
    assert 'Top fix: say "I went"' in result.summary
    assert "KUMF-ter-bul" in result.report and "## Pace" in result.report and "## Tone" in result.report


def test_live_tips_ride_along_with_the_question(monkeypatch):
    mem = Memory(":memory:")
    convo = agent.Conversation(mem)
    assert "tips are on" in tutor.run_tutor_tool(mem, None, {"action": "tips_on"})
    sent = []

    class Stream:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        def __aiter__(self):
            async def g():
                yield SimpleNamespace(type="text", text="Sure.")
            return g()

        async def get_final_message(self):
            return SimpleNamespace(stop_reason="end_turn", model="m", content=[SimpleNamespace(type="text", text="Sure.")],
                                   usage=SimpleNamespace(input_tokens=1, output_tokens=1))

    def stream(**kw):
        sent.append(kw["messages"][-1]["content"])
        return Stream()
    monkeypatch.setattr(agent.client.beta.messages, "stream", stream)

    async def say(s):
        pass
    asyncio.run(convo.reply("can you remind me tomorrow", say))
    assert "English tips are on" in sent[0]
    tutor.run_tutor_tool(mem, None, {"action": "tips_off"})
    asyncio.run(convo.reply("and what about friday", say))
    assert "English tips" not in sent[-1]


def test_room_speech_is_never_kept():
    from types import SimpleNamespace

    from buddy import usage

    mem = Memory(":memory:")
    tutor.log_utterance(mem, "hey buddy what time is the game", "room")  # could be anyone in the room
    assert tutor.sentences(mem, 1) == []
    response = SimpleNamespace(content=[SimpleNamespace(type="text", text='{"translation": "where is the station"}')],
                               stop_reason="end_turn")
    usage.record(mem, "claude-haiku-5-5", SimpleNamespace(input_tokens=5, output_tokens=5), purpose="listen",
                 response=response, request="room lines")
    row = mem.db.execute("SELECT request, reply, purpose FROM model_calls").fetchone()
    assert (row["request"], row["reply"], row["purpose"]) == ("", "", "listen")  # counted, not kept


def test_whisper_hears_malayalam_and_english_mixed(monkeypatch):
    from buddy import whisper

    seen = {}
    monkeypatch.setattr(whisper, "_transcribe", lambda pcm, hint, prompt, stats=None, allowed=None, fallback=True: (seen.update(hint=hint, prompt=prompt, allowed=allowed), ("ok", "ml"))[1])
    asyncio.run(whisper.transcribe(b"\x00\x00" * 16000 * 2, "auto:ml-IN,en-IN", "Names: Priya."))  # 2 s
    assert seen["hint"] is None and "Malayalam and English" in seen["prompt"] and "Priya" in seen["prompt"]
    assert seen["allowed"] == ["ml", "en"]  # detection stays within the chosen languages
    asyncio.run(whisper.transcribe(b"\x00\x00", "ml-IN"))
    assert seen["hint"] == "ml"
