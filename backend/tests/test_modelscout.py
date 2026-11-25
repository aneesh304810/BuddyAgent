"""The model scout: each free provider's own list, a test question, and a switch when the model in use is dead."""

import asyncio
import json
import urllib.error
from datetime import datetime

import pytest

from buddy import modelscout as ms
from buddy.connectors.hub import ConnectorHub
from buddy.free_models import PROVIDERS, FreeModels, RateLimited
from buddy.memory import Memory
from buddy.tools import ToolError

ENV = {"BUDDY_FREE_MODELS": "1", "BUDDY_GEMINI_KEY": "g", "BUDDY_GROQ_KEY": "q"}
LISTS = {"gemini": ["models/gemini-flash-latest", "models/gemini-3.0-flash", "models/gemini-2.5-flash-lite",
                    "models/gemini-3.0-flash-preview", "models/text-embedding-004", "models/imagen-4",
                    "models/gemini-2.5-flash-native-audio"],
         "groq": ["llama-3.3-70b-versatile", "openai/gpt-oss-120b", "whisper-large-v3", "llama-3.1-8b-instant"]}


@pytest.fixture
def memory(tmp_path):
    m = Memory(tmp_path / "buddy.db")
    ConnectorHub(m)
    return m


def setup(memory, answers, env=ENV, lists=LISTS):
    """answers: {model: reply text | Exception}. Unlisted models time out."""
    asked = []

    def http(url, body, headers, timeout):
        asked.append(body["model"])
        assert "ready" in body["messages"][1]["content"]  # the only thing ever sent: the one-word test
        got = answers.get(body["model"], TimeoutError("timed out"))
        if isinstance(got, Exception):
            raise got
        return {"choices": [{"message": {"content": got}}], "usage": {}}

    def fetch(url, headers):
        provider = next(p for p in ms.SCOUTED if PROVIDERS[p]["base"] in url or url == ms.LISTS.get(p))
        assert headers["Authorization"].startswith("Bearer ")
        return json.dumps({"data": [{"id": i} for i in lists[provider]]})
    return FreeModels(memory, env=env, http=http), fetch, asked


def test_ranking_prefers_the_right_family_and_never_picks_speech_or_image_models():
    got = ms.rank("gemini", [m.removeprefix("models/") for m in LISTS["gemini"]], skip="gemini-flash-latest")
    assert got == ["gemini-3.0-flash", "gemini-3.0-flash-preview", "gemini-2.5-flash-lite"]
    assert ms.rank("groq", LISTS["groq"])[:2] == ["openai/gpt-oss-120b", "llama-3.3-70b-versatile"]
    assert "whisper-large-v3" not in ms.rank("groq", LISTS["groq"])


def test_a_working_model_is_left_alone_and_a_resting_provider_comes_back(memory):
    memory.set("free:rest:gemini", "99999999999")
    free, fetch, asked = setup(memory, {"gemini-flash-latest": "ready", "llama-3.3-70b-versatile": "Ready."})
    got = ms.run(memory, free, fetch=fetch)
    assert [(r["provider"], r["state"]) for r in got["providers"]] == [("gemini", "working")]  # Groq: speech only
    assert asked == ["gemini-flash-latest"]  # one question each, no more
    assert not free.resting("gemini") and ms.message(got) == ""


def test_a_dead_model_is_replaced_by_the_first_one_that_answers(memory):
    free, fetch, asked = setup(memory, {"gemini-3.0-flash": urllib.error.HTTPError("u", 404, "gone", {}, None),
                                        "gemini-3.0-flash-preview": "ready",
                                        "llama-3.3-70b-versatile": "ready"})
    got = ms.run(memory, free, fetch=fetch)
    gem = got["providers"][0]
    assert gem["state"] == "switched" and gem["switched_from"] == "gemini-flash-latest"
    assert gem["model"] == "gemini-3.0-flash-preview" and "TimeoutError" in gem["detail"]
    assert asked[:3] == ["gemini-flash-latest", "gemini-3.0-flash", "gemini-3.0-flash-preview"]
    assert free.model_of("gemini") == "gemini-3.0-flash-preview"  # the next real request uses it
    text = ms.message(got)
    assert "Gemini" in text and "stopped answering, so I switched to gemini-3.0-flash-preview" in text
    again = ms.run(memory, free, fetch=fetch)
    assert again["providers"][0]["state"] == "working" and ms.message(again) == ""


def test_a_provider_at_its_limit_or_paused_by_the_budget_is_not_switched(memory):
    from buddy.budgets import BudgetStop
    free, fetch, asked = setup(memory, {"gemini-flash-latest": RateLimited(60),
                                        "mistral-small-latest": BudgetStop("api.mistral.ai: paused")},
                               env={**ENV, "BUDDY_MISTRAL_KEY": "m"},
                               lists={**LISTS, "mistral": ["mistral-small-latest", "mistral-medium-latest"]})
    got = ms.run(memory, free, fetch=fetch)
    assert [r["state"] for r in got["providers"]] == ["at its free limit", "paused by your budget"]
    assert asked == ["gemini-flash-latest", "mistral-small-latest"] and ms.message(got) == ""
    assert memory.get("free:model:gemini") in (None, "")


def test_a_model_set_by_hand_is_checked_but_never_changed(memory):
    env = {**ENV, "BUDDY_GEMINI_MODEL": "gemini-2.5-flash-lite"}
    free, fetch, asked = setup(memory, {"gemini-3.0-flash": "ready", "llama-3.3-70b-versatile": "ready"}, env=env)
    got = ms.run(memory, free, ["gemini"], fetch=fetch)
    row = got["providers"][0]
    assert row["state"] == "not answering" and "set by hand" in row["detail"] and asked == ["gemini-2.5-flash-lite"]
    assert free.model_of("gemini") == "gemini-2.5-flash-lite" and "Gemini" in ms.message(got)


def test_nothing_working_is_said_plainly_and_new_models_are_noted(memory):
    free, fetch, _ = setup(memory, {"gemini-flash-latest": "ready"})
    ms.run(memory, free, ["gemini"], fetch=fetch)
    more = {**LISTS, "gemini": LISTS["gemini"][1:] + ["models/gemini-4.0-flash"]}
    free, fetch, _ = setup(memory, {}, lists=more)
    got = ms.run(memory, free, ["gemini"], fetch=fetch)["providers"][0]
    assert got["state"] == "not answering" and "also tried" in got["detail"]
    assert got["new"] == ["gemini-4.0-flash"] and got["gone"] == ["gemini-flash-latest"]
    assert ms.status(memory)["providers"][0]["provider"] == "gemini"


def test_a_rested_provider_is_looked_at_soon_and_the_daily_run_once(memory):
    assert ms.due(memory, datetime(2026, 11, 16, 4, 0)) is None
    assert ms.due(memory, datetime(2026, 11, 16, 6, 0)) == []  # the daily look at all of them
    ms.mark_done(memory, daily=True, now=datetime(2026, 11, 16, 6, 0))
    assert ms.due(memory, datetime(2026, 11, 16, 9, 0)) is None
    free, _, _ = setup(memory, {})
    for _ in range(free.REST_AFTER):
        free._failed("gemini")
    assert free.resting("gemini") and ms.due(memory, datetime(2026, 11, 16, 9, 0)) == ["gemini"]
    ms.mark_done(memory, daily=False)
    assert ms.due(memory, datetime(2026, 11, 16, 9, 5)) is None


def test_budget_pause_is_not_counted_against_the_provider(memory):
    from buddy.budgets import BudgetStop

    def http(url, body, headers, timeout):
        if "groq" in url:
            raise BudgetStop("api.groq.com: background paid work is paused")
        return {"choices": [{"message": {"content": "Paris is the capital of France."}}], "usage": {}}
    free = FreeModels(memory, env=ENV, http=http)
    got = asyncio.run(free.run("quick_answer", "What is the capital of France?"))
    assert got["free"] and got["model"].startswith("gemini/")
    assert memory.get("free:fails:groq") in (None, "", "0") and not memory.get("free:lasterr:groq")
    assert not json.loads(memory.get("connect:last:groq") or "{}").get("error")


def test_the_owner_can_pin_a_model_only_if_it_answers(memory):
    free, _, _ = setup(memory, {"gemini-3.0-flash": "ready"})
    assert "didn't pass the test" in ms.use(memory, free, "gemini", "gemini-9-ultra")
    assert "now uses gemini-3.0-flash" in ms.use(memory, free, "Gemini", "gemini-3.0-flash")
    assert free.model_of("gemini") == "gemini-3.0-flash"
    with pytest.raises(ToolError):
        ms.use(memory, free, "openrouter", "x")
    assert "hasn't run yet" in asyncio.run(ms.run_tool(Memory(":memory:"), {"action": "status"}, free))


def test_a_bank_with_no_cards_is_not_logged_as_a_failure(memory, monkeypatch):
    from buddy import plaid
    monkeypatch.setenv("BUDDY_PLAID_CLIENT_ID", "c")
    monkeypatch.setenv("BUDDY_PLAID_SECRET", "s")
    from buddy import gateway
    gateway._table(memory)
    p = plaid.Plaid(memory)
    p._http = lambda url, payload: {"error_code": "NO_LIABILITY_ACCOUNTS", "error_message": "none"}
    with pytest.raises(plaid.PlaidError):
        p.call("/liabilities/get", {"access_token": "t"})
    row = memory.db.execute("SELECT ok, error FROM api_calls WHERE provider = 'plaid'").fetchone()
    assert row["ok"] == 1 and row["error"] == ""
