"""Free models for general work: picked by skill and speed, never given personal data."""

import asyncio
import json

import pytest

from buddy import free_models as fm
from buddy.finops import FinOps
from buddy.memory import Memory

ENV = {"BUDDY_FREE_MODELS": "1", "BUDDY_GEMINI_KEY": "g", "BUDDY_GROQ_KEY": "q", "BUDDY_MISTRAL_KEY": "m"}


class FakeHTTP:
    def __init__(self, fail=(), limited=()):
        self.calls, self.fail, self.limited = [], set(fail), set(limited)

    def __call__(self, url, body, headers, timeout):
        name = next(n for n, p in fm.PROVIDERS.items() if url.startswith(p["base"]))
        self.calls.append(name)
        assert url.startswith("https://")
        if name in self.limited:
            raise fm.RateLimited(30)
        if name in self.fail:
            raise OSError("down")
        return {"choices": [{"message": {"content": f"answer from {name}"}}]}


def free(http, env=ENV, memory=None):
    f = fm.FreeModels(memory or Memory(":memory:"), env=env, http=http)

    async def no_claude(system, prompt, kind=""):
        return "answer from claude"
    f._claude = no_claude
    return f


def test_off_unless_switched_on_with_a_key():
    assert fm.FreeModels(Memory(":memory:"), env={}).enabled() == []
    assert fm.FreeModels(Memory(":memory:"), env={"BUDDY_GROQ_KEY": "q"}).enabled() == []
    assert fm.FreeModels(Memory(":memory:"), env={"BUDDY_FREE_MODELS": "1", "BUDDY_GROQ_KEY": "q"}).enabled() == ["groq"]


def test_capability_order_for_quality_tasks():
    f = free(FakeHTTP())
    assert f.order("summarize") == ["gemini", "mistral", "groq"]
    assert f.order("rewrite")[0] == "mistral"


def test_speed_first_tasks_prefer_the_fastest_lately():
    f = free(FakeHTTP())
    for name in f.enabled():
        f.speed[name] = 5.0
    f.speed["mistral"] = 0.4
    assert f.order("quick_answer")[0] == "mistral"


def test_flaky_provider_drops_back():
    f = free(FakeHTTP())
    first = f.order("summarize")[0]
    for _ in range(5):
        f._learn(first, None)
    assert f.order("summarize")[0] != first


def test_personal_details_never_leave_claude():
    f = free(FakeHTTP())
    with pytest.raises(fm.PersonalData):
        asyncio.run(f.run("rewrite", "email me at jane.doe@example.com"))
    with pytest.raises(fm.PersonalData):
        asyncio.run(f.run("translate", "call +1 415 555 0134 tomorrow"))
    mem = Memory(":memory:")
    mem.remember("My sister Priya Nair lives in Bangalore")
    with pytest.raises(fm.PersonalData):
        asyncio.run(free(FakeHTTP(), memory=mem).run("summarize", "news about Priya Nair today"))
    assert f.http.calls == []


def test_general_tool_returns_the_refusal_as_text():
    out = asyncio.run(fm.run_general_tool(free(FakeHTTP()), {"kind": "rewrite", "text": "jane@example.com"}))
    assert "stays on Claude" in out


def test_answers_and_counts_the_call():
    mem = Memory(":memory:")
    http = FakeHTTP()
    result = asyncio.run(free(http, memory=mem).run("explain", "how do tides work"))
    assert result["free"] and result["answer"].startswith("answer from")
    assert FinOps(mem).routing()["free_model_calls"] == {http.calls[0]: 1}


def test_rate_limit_and_failure_move_on_then_cool_down():
    http = FakeHTTP(fail={"gemini"}, limited={"mistral"})
    f = free(http)
    result = asyncio.run(f.run("summarize", "a public article about tides"))
    assert result["answer"] == "answer from groq"
    assert {"gemini", "mistral"} <= set(f.status()["busy"])
    asyncio.run(f.run("summarize", "another public article"))
    assert http.calls.count("gemini") == 1  # cooling down, not retried


def test_everything_busy_falls_back_to_claude():
    mem = Memory(":memory:")
    f = free(FakeHTTP(fail=set(fm.PROVIDERS)), memory=mem)
    result = asyncio.run(f.run("brainstorm", "names for a cat"))
    assert result == {"answer": "answer from claude", "model": fm.settings.fast_model, "free": False,
                      "tried": result["tried"]}
    assert len(result["tried"]) == 3
    assert FinOps(mem).routing()["free_model_calls"]["fallback_claude"] == 1


def test_unknown_kind_is_refused():
    with pytest.raises(fm.ToolError):
        asyncio.run(free(FakeHTTP()).run("hack", "x"))


def test_general_text_still_goes_free_when_memory_has_facts():
    mem = Memory(":memory:")
    mem.remember("My sister Priya Nair lives in Bangalore")
    result = asyncio.run(free(FakeHTTP(), memory=mem).run("explain", "how do ocean tides work"))
    assert result["free"]
