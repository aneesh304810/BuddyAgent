"""The Intelligence Platform's model choices: one per job, a monthly limit, a fallback, and a test to compare them."""

import asyncio
import json

import pytest

from buddy import maestro as models
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
    assert [j["id"] for j in page["jobs"] if j["in_use"]] == ["label", "write", "extract", "summarise_search", "summarise_reviews"]
    with pytest.raises(ToolError):
        asyncio.run(models.test(memory, "openai:cheap", perfect_but_one, env={}))


def test_the_endpoints_need_the_owner(memory, monkeypatch):
    from fastapi.testclient import TestClient

    from buddy import server
    monkeypatch.setattr(server, "memory", memory)
    monkeypatch.setattr(server, "authorized", lambda t: t == "owner-token")
    client = TestClient(server.app)
    assert client.get("/api/maestro").status_code == 401
    assert client.post("/api/maestro", json={"job": "label", "choice": "claude:fast"}).status_code == 401
    ok = {"Authorization": "Bearer owner-token"}
    out = client.post("/api/maestro", json={"job": "label", "choice": "claude:fast"}, headers=ok).json()
    assert next(j for j in out["jobs"] if j["id"] == "label")["choice"] == "claude:fast"
    assert client.post("/api/maestro", json={"job": "label", "choice": "nope"}, headers=ok).status_code == 400
    assert client.post("/api/maestro", json={"cap": 12}, headers=ok).json()["spending"]["cap"] == 12.0


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


LIST = {"data": [
    {"id": "anthropic/claude-haiku", "name": "Claude Haiku", "pricing": {"prompt": "0.0000001", "completion": "0.0000005"},
     "context_length": 200000},
    {"id": "mistralai/mistral-small", "name": "Mistral Small", "pricing": {"prompt": "0.0000002", "completion": "0.0000006"},
     "context_length": 32000},
    {"id": "meta-llama/llama-mini:free", "name": "Llama Mini (free)", "pricing": {"prompt": "0", "completion": "0"},
     "context_length": 16000},
    {"id": "openai/big-reasoner", "name": "Big Reasoner", "pricing": {"prompt": "0.00001", "completion": "0.00005"},
     "context_length": 400000},
    {"id": "deepseek/deepseek-coder", "name": "DeepSeek Coder", "pricing": {"prompt": "0.0000006", "completion": "0.000002"},
     "context_length": 64000},
    {"id": "acme/image-maker", "name": "Image Maker", "pricing": {"prompt": "0.000001", "completion": "0"},
     "architecture": {"output_modalities": ["image"]}, "context_length": 4000},
    {"id": "broken", "name": "No maker", "pricing": {"prompt": "x"}}]}


def test_the_price_list_is_read_from_the_public_list_and_kept(memory):
    asked = []

    def fetch(url, headers=None):
        asked.append(url)
        return json.dumps(LIST)
    got = models.refresh_catalog(memory, fetch)
    assert asked == ["https://openrouter.ai/api/v1/models"] and len(got["models"]) == 5  # no image model, no broken row
    haiku = next(m for m in got["models"] if m["id"] == "anthropic/claude-haiku")
    assert (haiku["in"], haiku["out"], haiku["context"], haiku["free"]) == (0.1, 0.5, 200000, False)
    assert models.refresh_catalog(memory, lambda u, h=None: json.dumps({"data": []}))["models"] == got["models"]
    assert models.page(memory)["catalog_size"] == 5


def test_the_finder_suits_models_to_a_task_and_says_how_buddy_can_use_each(memory):
    models.refresh_catalog(memory, lambda u, h=None: json.dumps(LIST))
    sort = models.find(memory, "sort", env={"BUDDY_MISTRAL_KEY": "m"})
    assert [m["id"] for m in sort["models"]] == ["meta-llama/llama-mini:free", "anthropic/claude-haiku",
                                                 "mistralai/mistral-small"]  # free first, then by price
    assert "cheapest first" in sort["note"] and "48-headline test" in sort["advice"]
    by = {m["id"]: m for m in sort["models"]}
    assert by["mistralai/mistral-small"]["via"] == "Mistral directly" and by["mistralai/mistral-small"]["ready"]
    assert "OpenRouter" in by["meta-llama/llama-mini:free"]["via"]
    assert [m["id"] for m in models.find(memory, "summarise")["models"]] == ["anthropic/claude-haiku"]  # needs 200k
    assert models.find(memory, "summarise")["models"][0]["via"] == "Claude, already set up"
    assert [m["id"] for m in models.find(memory, "code")["models"]] == ["deepseek/deepseek-coder"]
    hard = models.find(memory, "reason")
    assert hard["models"][0]["id"] == "openai/big-reasoner" and "dearest first" in hard["note"]
    assert "needs its key" in hard["models"][0]["via"]
    mine = models.find(memory, "personal")
    assert mine["models"] == [] and "nowhere else" in mine["note"] and len(mine["claude"]) == 3
    found = models.find(memory, "", "claude haiku")
    assert [m["id"] for m in found["models"]] == ["anthropic/claude-haiku"] and found["found"] == 1
    assert len(models.find(memory)["models"]) == 5 and len(models.find(memory)["tasks"]) == 7


def test_setup_gives_the_page_and_the_command_and_a_key_can_be_checked_without_showing_it(memory):
    rows = {r["id"]: r for r in models.setup(memory, {"BUDDY_MISTRAL_KEY": "secret-key-value"})}
    assert rows["mistral"]["set_up"] and not rows["nvidia"]["set_up"]
    assert rows["nvidia"]["command"] == "sudo bash /opt/buddy/deploy/set.sh BUDDY_NVIDIA_KEY"
    assert all(r["page"].startswith("https://") for r in rows.values())
    seen = []

    def lists(url, headers=None):
        seen.append((url, headers))
        return json.dumps({"data": [{"id": "mistral-small-latest"}, {"id": "mistral-large-latest"}]})
    good = models.check_key(memory, "mistral", {"BUDDY_MISTRAL_KEY": "secret-key-value"}, lists)
    assert good["ok"] and good["models"] == 2 and "works" in good["says"]
    assert "secret-key-value" not in json.dumps(good) and seen[0][1]["Authorization"] == "Bearer secret-key-value"
    assert models.check_key(memory, "mistral", {}, lists) == {"provider": "mistral", "ok": False,
                                                              "says": "Its key isn't set yet."}

    def refuses(url, headers=None):
        raise OSError("401 unauthorized")
    bad = models.check_key(memory, "mistral", {"BUDDY_MISTRAL_KEY": "wrong"}, refuses)
    assert not bad["ok"] and "401" in bad["says"] and "wrong" not in bad["says"]
    assert not models.check_key(memory, "claude", {})["ok"] and not models.check_key(memory, "added:nope", {})["ok"]


def test_the_chat_tool_and_the_endpoints(memory, monkeypatch):
    models.refresh_catalog(memory, lambda u, h=None: json.dumps(LIST))
    out = json.loads(asyncio.run(models.run_tool(memory, {"action": "find_models", "task": "sort"})))
    assert out["models"][0]["free"] and out["title"] == "Sort and label"
    shown = json.loads(asyncio.run(models.run_tool(memory, {"action": "show"})))
    assert "nowhere else" in shown["rule"] and len(shown["providers"]) == 9
    assert "BUDDY_NVIDIA_KEY" in asyncio.run(models.run_tool(memory, {"action": "setup"}))
    assert models.TOOL["name"] == "maestro" and "cannot sign up" in models.TOOL["description"]
    from fastapi.testclient import TestClient

    from buddy import server
    monkeypatch.setattr(server, "memory", memory)
    monkeypatch.setattr(server, "authorized", lambda t: t == "owner-token")
    client = TestClient(server.app)
    assert client.get("/api/maestro/models?task=sort").status_code == 401
    ok = {"Authorization": "Bearer owner-token"}
    assert client.get("/api/maestro/models?task=code", headers=ok).json()["models"][0]["id"] == "deepseek/deepseek-coder"
    checked = client.post("/api/maestro", json={"check": "nvidia"}, headers=ok).json()
    assert checked["checked"]["ok"] is False and "isn't set" in checked["text"]


def test_every_place_text_can_leave_claude_calls_the_one_check():
    import inspect

    from buddy import agent, free_models, makers, model_advisor, webread
    for module in (agent, free_models, makers, model_advisor, webread):
        source = inspect.getsource(module)
        assert "check_public(" in source, module.__name__
        calls = [line for line in source.splitlines() if "check_general(" in line and "def check_general" not in line
                 and not line.strip().startswith(("#", '"', "from"))]
        assert all("free_models.check_general:" in c or "(free_models.check_general)" in c for c in calls), (module.__name__, calls)
    with pytest.raises(free_models.PersonalData):
        models.check_public("email jane.doe@example.com about the invoice")
    models.check_public("how do tides work")  # nothing personal: no error


def test_buddy_connect_refuses_personal_data_for_every_ai_provider_but_claude(memory):
    for name, entry in gateway.CATALOG.items():
        if entry.get("group") != "AI models" or name == "claude":
            continue
        with pytest.raises(gateway.ConnectError, match="personal data"):
            gateway.call_url(memory, name, ["https://example.com/x"], personal=True,
                             fetch=lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not be called")))


def test_the_page_reports_all_of_buddys_ai_use_including_the_main_conversation(memory):
    from buddy import usage
    usage.log_call(memory, provider="claude", model="claude-sonnet-5-5", purpose="chat", input_tokens=1000,
                   output_tokens=200, cost=0.04, seconds=1.2)
    usage.log_call(memory, provider="claude", model="claude-haiku-5-5", purpose="mail", input_tokens=500,
                   output_tokens=50, cost=0.01, seconds=0.4)
    usage.log_call(memory, provider="cerebras", model="llama", purpose="general:translate", seconds=0.3)
    got = models.page(memory)["everything"]
    assert got["requests"] == 3 and got["claude_requests"] == 2 and got["free_requests"] == 1
    assert got["usd"] == 0.05 and got["by_purpose"][0] == {"name": "Talking with you", "requests": 1, "usd": 0.04}
    assert got["by_model"][0]["name"] == "claude · claude-sonnet-5-5"
