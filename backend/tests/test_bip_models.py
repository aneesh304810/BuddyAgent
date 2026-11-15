"""The Intelligence Platform's model choices: one per job, a monthly limit, a fallback, and a test to compare them."""

import asyncio
import json

import pytest

from buddy import bip_models as models
from buddy import gateway
from buddy.connectors.hub import ConnectorHub
from buddy.memory import Memory
from buddy.tools import ToolError

OPENAI = {"BUDDY_OPENAI_KEY": "k" * 40, "BUDDY_OPENAI_CHEAP_MODEL": "cheap-model", "BUDDY_OPENAI_CHEAP_PRICE": "0.10,0.50"}


@pytest.fixture
def memory(tmp_path):
    gateway._recent.clear()
    m = Memory(tmp_path / "buddy.db")
    ConnectorHub(m)
    return m


def test_openai_is_offered_only_when_key_model_and_price_are_all_set():
    assert models.openai_setting("openai:cheap", {}) is None
    assert models.openai_setting("openai:cheap", {**OPENAI, "BUDDY_OPENAI_CHEAP_PRICE": "cheap"}) is None
    assert models.openai_setting("openai:cheap", {k: v for k, v in OPENAI.items() if k != "BUDDY_OPENAI_KEY"}) is None
    assert models.openai_setting("openai:cheap", OPENAI) == {"model": "cheap-model", "in": 0.10, "out": 0.50}
    listed = {c["id"]: c for c in models.choices(OPENAI)}
    assert listed["openai:cheap"]["ready"] and not listed["openai:analysis"]["ready"]
    assert "BUDDY_OPENAI_ANALYSIS_MODEL" in listed["openai:analysis"]["setup"] and listed["free"]["paid"] is False


def test_a_job_keeps_its_own_choice_and_an_unready_choice_is_refused(memory):
    assert models.choice_for(memory, "label") == "free"
    assert "Claude, cheap model" in models.set_choice(memory, "label", "claude:fast")
    assert models.choice_for(memory, "label") == "claude:fast" and models.choice_for(memory, "write") == "free"
    with pytest.raises(ToolError, match="BUDDY_OPENAI_KEY"):
        models.set_choice(memory, "label", "openai:cheap", {})
    with pytest.raises(ToolError):
        models.set_choice(memory, "nope", "free")
    with pytest.raises(ToolError):
        models.set_choice(memory, "label", "made-up")
    assert models.set_choice(memory, "label", "openai:cheap", OPENAI).startswith("Label headlines now uses: OpenAI")


def test_an_openai_answer_is_priced_from_the_owners_price_and_gets_only_the_prompt(memory):
    sent = []

    def fetch(url, data=None, headers=None):
        sent.append((url, json.loads(data), headers))
        return json.dumps({"choices": [{"message": {"content": '{"labels": {"1": "good"}}'}}],
                           "usage": {"prompt_tokens": 1_000_000, "completion_tokens": 200_000}})
    got = models._openai(memory, "openai:cheap", "the prompt", "the system", fetch, OPENAI)
    assert got["usd"] == pytest.approx(0.20) and got["text"] == '{"labels": {"1": "good"}}'
    url, body, headers = sent[0]
    assert url == "https://api.openai.com/v1/chat/completions" and body["model"] == "cheap-model"
    assert [m["content"] for m in body["messages"]] == ["the system", "the prompt"]
    assert headers["Authorization"] == "Bearer " + "k" * 40


def test_spending_is_counted_per_job_and_the_limit_sends_a_job_back_to_free(memory, monkeypatch):
    asked = []

    async def fake(memory_, choice, prompt, system, fetch=None, env=None):
        asked.append(choice)
        return {"text": '{"labels": {"1": "bad"}}', "usd": 0.0 if choice == "free" else 3.0, "in": 10, "out": 5, "seconds": 0.1}
    monkeypatch.setattr(models, "ask_with", fake)
    models.set_choice(memory, "label", "claude:chat")
    assert asyncio.run(models.ask(memory, "label", "p")) == {"labels": {"1": "bad"}}
    assert asyncio.run(models.ask(memory, "label", "p")) == {"labels": {"1": "bad"}}
    spent = models.spending(memory)
    assert spent["total"] == 6.0 and spent["jobs"]["label"] == {"calls": 2, "in": 20, "out": 10, "usd": 6.0}
    asyncio.run(models.ask(memory, "label", "p"))  # $6 spent against a $5 limit
    assert asked == ["claude:chat", "claude:chat", "free"] and models.spending(memory)["total"] == 6.0
    assert "$20.00" in models.set_cap(memory, 20)
    asyncio.run(models.ask(memory, "label", "p"))
    assert asked[-1] == "claude:chat"
    with pytest.raises(ToolError):
        models.set_cap(memory, -1)
    with pytest.raises(ToolError):
        models.set_cap(memory, "lots")


def test_a_choice_that_fails_or_answers_badly_falls_back_to_free(memory, monkeypatch):
    asked = []

    async def fake(memory_, choice, prompt, system, fetch=None, env=None):
        asked.append(choice)
        if choice == "claude:deep":
            raise RuntimeError("down")
        if choice == "claude:chat":
            return {"text": "sorry, no JSON here", "usd": 0.01, "in": 1, "out": 1, "seconds": 0}
        return {"text": '{"how": "they share cooling"}', "usd": 0.0, "in": 0, "out": 0, "seconds": 0}
    monkeypatch.setattr(models, "ask_with", fake)
    models.set_choice(memory, "write", "claude:deep")
    assert asyncio.run(models.ask(memory, "write", "p")) == {"how": "they share cooling"}
    models.set_choice(memory, "write", "claude:chat")
    assert asyncio.run(models.ask(memory, "write", "p")) == {"how": "they share cooling"}
    assert asked == ["claude:deep", "free", "claude:chat", "free"]
    assert asyncio.run(models.ask(memory, "label", "p")) == {"how": "they share cooling"} and asked[-1] == "free"


def test_the_test_set_is_balanced_and_scores_a_choice(memory):
    from buddy.themes import TONES
    labels = [expected for _, _, expected in models.TEST]
    assert len(models.TEST) == 48 and set(labels) == set(TONES) and min(labels.count(t) for t in TONES) >= 12
    assert len({title for _, title, _ in models.TEST}) == 48

    async def perfect_but_one(memory_, choice, prompt, system, fetch=None, env=None):
        lines = [line for line in prompt.splitlines() if line[:1].isdigit()]
        right = {title: expected for _, title, expected in models.TEST}
        said = {line.split(".")[0]: right[line.split("] ", 1)[1]] for line in lines}
        if "1. [Small modular reactors]" in prompt:
            said["1"] = "bad"
        return {"text": json.dumps({"labels": said}), "usd": 0.002, "seconds": 1.5}
    got = asyncio.run(models.test(memory, "claude:fast", perfect_but_one))
    assert (got["right"], got["answered"], got["headlines"]) == (47, 48, 48) and got["share"] == 0.98
    assert got["usd"] == 0.006 and got["seconds"] == 4.5 and got["wrong"][0]["said"] == "bad"
    page = models.page(memory)
    assert page["tests"][0]["choice"] == "claude:fast" and page["spending"]["jobs"]["test"]["usd"] == 0.006
    assert [j["id"] for j in page["jobs"] if j["in_use"]] == ["label", "write"]
    with pytest.raises(ToolError):
        asyncio.run(models.test(memory, "openai:cheap", perfect_but_one, env={}))


def test_the_endpoints_need_the_owner(memory, monkeypatch):
    from fastapi.testclient import TestClient

    from buddy import server
    monkeypatch.setattr(server, "memory", memory)
    monkeypatch.setattr(server, "authorized", lambda t: t == "owner-token")
    client = TestClient(server.app)
    assert client.get("/api/intel/models").status_code == 401
    assert client.post("/api/intel/models", json={"job": "label", "choice": "claude:fast"}).status_code == 401
    ok = {"Authorization": "Bearer owner-token"}
    out = client.post("/api/intel/models", json={"job": "label", "choice": "claude:fast"}, headers=ok).json()
    assert next(j for j in out["jobs"] if j["id"] == "label")["choice"] == "claude:fast"
    assert client.post("/api/intel/models", json={"job": "label", "choice": "nope"}, headers=ok).status_code == 400
    assert client.post("/api/intel/models", json={"cap": 12}, headers=ok).json()["spending"]["cap"] == 12.0
