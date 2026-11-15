"""The Intelligence Platform's model choices: one per job, a monthly limit, a fallback, and a test to compare them."""

import asyncio
import json

import pytest

from buddy import orchestra as models
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
    assert [j["id"] for j in page["jobs"] if j["in_use"]] == ["label", "write", "extract"]
    with pytest.raises(ToolError):
        asyncio.run(models.test(memory, "openai:cheap", perfect_but_one, env={}))


def test_the_endpoints_need_the_owner(memory, monkeypatch):
    from fastapi.testclient import TestClient

    from buddy import server
    monkeypatch.setattr(server, "memory", memory)
    monkeypatch.setattr(server, "authorized", lambda t: t == "owner-token")
    client = TestClient(server.app)
    assert client.get("/api/orchestra").status_code == 401
    assert client.post("/api/orchestra", json={"job": "label", "choice": "claude:fast"}).status_code == 401
    ok = {"Authorization": "Bearer owner-token"}
    out = client.post("/api/orchestra", json={"job": "label", "choice": "claude:fast"}, headers=ok).json()
    assert next(j for j in out["jobs"] if j["id"] == "label")["choice"] == "claude:fast"
    assert client.post("/api/orchestra", json={"job": "label", "choice": "nope"}, headers=ok).status_code == 400
    assert client.post("/api/orchestra", json={"cap": 12}, headers=ok).json()["spending"]["cap"] == 12.0


def test_only_claude_may_see_anything_about_the_owner():
    for name, _title, _role, personal, _paid in models.PROVIDERS:
        assert models.may_see(name, True) is (name == "claude") and personal is (name == "claude")
        assert models.may_see(name, False) is True
    assert models.may_see("claude:fast", True) and not models.may_see("added:anything", True)
    assert not models.may_see("pool:mistral", True) and not models.may_see("openai:cheap", True)
    assert not models.may_see("free", True)


def test_personal_work_goes_to_claude_whatever_the_job_is_set_to_and_never_falls_back(memory, monkeypatch):
    asked = []

    async def fake(memory_, choice, prompt, system, fetch=None, env=None):
        asked.append(choice)
        if len(asked) == 3:
            raise RuntimeError("Claude is down")
        return {"text": '{"ok": 1}', "usd": 0.001, "in": 5, "out": 2, "seconds": 0}
    monkeypatch.setattr(models, "ask_with", fake)
    models.set_choice(memory, "label", "pool:mistral", {"BUDDY_MISTRAL_KEY": "m"})
    assert asyncio.run(models.ask(memory, "label", "about my salary", personal=True)) == {"ok": 1}
    assert asyncio.run(models.ask(memory, "label", "write to jane.doe@example.com about it")) == {"ok": 1}  # found by the check
    assert asked == ["claude:chat", "claude:chat"]  # never the job's own choice
    assert asyncio.run(models.ask(memory, "label", "my bank login", personal=True)) is None
    assert asked == ["claude:chat"] * 3  # Claude failed: nothing else was tried
    models.set_choice(memory, "label", "claude:fast")
    asyncio.run(models.ask(memory, "label", "my diary", personal=True))
    assert asked[-1] == "claude:fast"  # a Claude tier chosen for the job is kept


def test_a_provider_can_be_added_later_used_for_a_job_and_removed(memory):
    env = {"BUDDY_AI_TOGETHER_KEY": "t" * 30}
    with pytest.raises(ToolError, match="https"):
        models.add_provider(memory, "together", "http://api.together.xyz/v1", "some-model")
    for bad in ("https://10.0.0.5/v1", "https://box.internal/v1", "https://localhost/v1"):
        with pytest.raises(ToolError):
            models.add_provider(memory, "together", bad, "some-model")
    for name in ("claude", "openai", "free", "x", "has space", "Bad-Name"):
        with pytest.raises(ToolError):
            models.add_provider(memory, name, "https://api.together.xyz/v1", "some-model")
    with pytest.raises(ToolError):
        models.add_provider(memory, "together", "https://api.together.xyz/v1", "some-model", "cheap", 1)
    said = models.add_provider(memory, "together", "https://api.together.xyz/v1/", "some-model", 0.2, 0.6)
    assert "BUDDY_AI_TOGETHER_KEY" in said and "public work only" in said
    listed = {c["id"]: c for c in models.choices({}, memory)}
    assert not listed["added:together"]["ready"] and "BUDDY_AI_TOGETHER_KEY" in listed["added:together"]["setup"]
    with pytest.raises(ToolError):
        models.set_choice(memory, "label", "added:together", {})
    assert "Together" in models.set_choice(memory, "label", "added:together", env)
    sent = []

    def fetch(url, data=None, headers=None):
        sent.append((url, json.loads(data), headers))
        return json.dumps({"choices": [{"message": {"content": '{"labels": {"1": "good"}}'}}],
                           "usage": {"prompt_tokens": 1_000_000, "completion_tokens": 500_000}})
    got = asyncio.run(models.ask(memory, "label", "Utility orders four reactors", fetch=fetch, env=env))
    assert got == {"labels": {"1": "good"}} and sent[0][0] == "https://api.together.xyz/v1/chat/completions"
    assert sent[0][1]["model"] == "some-model" and sent[0][2]["Authorization"] == "Bearer " + "t" * 30
    assert models.spending(memory)["jobs"]["label"]["usd"] == pytest.approx(0.5)  # 0.2 in + 0.3 out
    row = next(p for p in models.providers(memory, env) if p.get("added") == "together")
    assert row["set_up"] and row["may_see_personal"] is False and row["paid"] == "paid per use"
    assert "back on the free pool" in models.remove_provider(memory, "together")
    assert models.choice_for(memory, "label") == "free" and models.added(memory) == []
    with pytest.raises(ToolError):
        models.remove_provider(memory, "together")


def test_the_page_lists_every_provider_with_its_role_and_the_rule(memory):
    page = models.page(memory, {"BUDDY_MISTRAL_KEY": "m", "BUDDY_GROQ_KEY": "q"})
    by = {p["id"]: p for p in page["providers"]}
    assert list(by) == ["claude", "openai", "mistral", "cerebras", "gemini", "nvidia", "openrouter", "ollama", "groq"]
    assert by["claude"]["may_see_personal"] and by["claude"]["set_up"] and by["mistral"]["set_up"]
    assert not by["ollama"]["set_up"] and not by["cerebras"]["set_up"] and "Speech to text only" in by["groq"]["role"]
    assert "Fallback only" in by["openrouter"]["role"] and "nowhere else" in page["rule"]
    listed = {c["id"]: c for c in page["choices"]}
    assert listed["pool:mistral"]["ready"] and not listed["pool:nvidia"]["ready"]


def test_one_pool_provider_can_be_chosen_for_a_job(memory, monkeypatch):
    from buddy import free_models as fm
    monkeypatch.setenv("BUDDY_FREE_MODELS", "1")
    monkeypatch.setenv("BUDDY_MISTRAL_KEY", "m")
    called = []

    def call(self, name, system, prompt, *a, **k):
        called.append(name)
        return '{"labels": {"1": "bad"}}', {}
    monkeypatch.setattr(fm.FreeModels, "_call", call)
    models.set_choice(memory, "label", "pool:mistral")
    assert asyncio.run(models.ask(memory, "label", "Reactor project cancelled")) == {"labels": {"1": "bad"}}
    assert called == ["mistral"]  # that provider and no other
    monkeypatch.delenv("BUDDY_MISTRAL_KEY")
    with pytest.raises(ToolError):
        models._pool_one(memory, "mistral", "p", "s")
