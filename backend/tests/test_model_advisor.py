"""Which model: free when the code holds none of the user's data and a free model is good enough; otherwise say so."""

import asyncio
import json

from buddy import codejob, model_advisor
from buddy.memory import Memory
from buddy.selfcode import REPO

ENV = {"BUDDY_FREE_MODELS": "1", "BUDDY_GITHUB_MODELS_KEY": "k", "BUDDY_GEMINI_KEY": "k"}


def free(m):
    from buddy.free_models import FreeModels
    return FreeModels(m, env=ENV)


def test_a_small_change_to_buddys_own_code_goes_to_a_free_model():
    m = Memory(":memory:")
    a = model_advisor.advise_code(m, "The change: rename the label 'Daily health report' to 'Daily health check'",
                                  REPO, free(m))
    assert a["route"] == "free" and a["provider"] in model_advisor.TOOL_CAPABLE
    assert "free model" in model_advisor.say(a) and "none of your data" in model_advisor.say(a)


def test_no_free_model_when_its_big_or_has_data_or_another_repo_and_it_says_which_to_use():
    m = Memory(":memory:")
    big = model_advisor.advise_code(m, "The change: refactor the budgets across several files", REPO, free(m))
    assert big["route"] == "claude" and big["paid"] == "sonnet"
    assert model_advisor.say(big).startswith("None of the free models is good for this one")
    mine = model_advisor.advise_code(m, "The change: email aneesh@example.com the report", REPO, free(m))
    assert mine["route"] == "claude" and "personal" in mine["why"]
    other = model_advisor.advise_code(m, "rename a label", "https://github.com/someone/private-data", free(m))
    assert other["route"] == "claude" and "Haiku" in model_advisor.say(other)


def test_a_free_model_whose_code_tasks_keep_failing_is_skipped():
    m = Memory(":memory:")
    first = model_advisor.advise_code(m, "rename a label", REPO, free(m))["provider"]
    for _ in range(3):
        model_advisor.note_code_result(m, first, False)
    again = model_advisor.advise_code(m, "rename a label", REPO, free(m))
    assert again.get("provider") != first


def test_the_free_loop_uses_tools_and_finishes(monkeypatch):
    m = Memory(":memory:")
    monkeypatch.setenv("BUDDY_FREE_MODELS", "1")
    monkeypatch.setenv("BUDDY_GITHUB_MODELS_KEY", "k")
    replies = iter([
        {"choices": [{"message": {"content": "", "tool_calls": [
            {"id": "1", "function": {"name": "write_file", "arguments": json.dumps({"path": "a.py", "content": "x"})}}]}}]},
        {"choices": [{"message": {"content": "", "tool_calls": [
            {"id": "2", "function": {"name": "finish", "arguments": json.dumps({"summary": "Renamed it."})}}]}}]},
    ])
    import buddy.free_models as fm
    monkeypatch.setattr(fm, "_post", lambda *a, **k: next(replies))
    ran = []

    async def tool(ws, name, args, log):
        ran.append(name)
        return "Written."
    monkeypatch.setattr(codejob, "_tool", tool)

    class Ctx:
        memory = m
    finished, problem = asyncio.run(codejob._free_loop(Ctx(), "github", "sys", "task", "ws", []))
    assert problem == "" and finished["summary"] == "Renamed it." and ran == ["write_file"]
