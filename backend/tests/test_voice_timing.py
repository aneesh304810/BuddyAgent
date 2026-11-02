"""Voice timing: each step from the end of your speech to Buddy's first word, for Insights."""

from buddy import insights
from buddy.memory import Memory


def test_voice_timings_show_where_the_time_goes():
    mem = Memory(":memory:")
    for stt in (450, 520, 610):
        insights.record_voice_timing(mem, {"engine": "groq", "wait_ms": 1000, "network_ms": 180, "stt_ms": stt,
                                           "to_thinking_ms": 150, "first_word_ms": 1900, "total_ms": 3700})
    insights.record_voice_timing(mem, {"engine": "local", "wait_ms": 1000, "network_ms": 200, "stt_ms": 2600,
                                       "to_thinking_ms": 160, "first_word_ms": 2000, "total_ms": 5900})
    insights.record_voice_timing(mem, {"engine": "groq", "missed": "timeout", "wait_ms": "nonsense"})
    v = insights.report(mem, days=1)["voice"]
    assert v["utterances"] == 5 and v["missed"] == {"timeout": 1}
    assert v["engines"]["groq"]["stt_ms"] == 520 and v["engines"]["local"]["stt_ms"] == 2600
    assert v["slowest"] == "Buddy's first word"
    assert [s["step"] for s in v["steps"]][0] == "Pause that ends your sentence"
