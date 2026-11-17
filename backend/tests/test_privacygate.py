"""Privacy Gateway: health information reaches no AI model, other personal details reach Claude only, a refusal
is final, and a failed check refuses. The record keeps no content."""

import asyncio
import json
import re
from pathlib import Path

import pytest

from buddy import agent, config, free_models, maestro, privacygate
from buddy.memory import Memory
from tests.test_conversation import Scripted, text, tool_use

OTHERS = ("openai", "gemini", "mistral", "cerebras", "nvidia", "openrouter", "groq", "ollama", "a-future-model")
EVERY = ("claude", *OTHERS)
HEALTH = {
    "Oura HRV": "My HRV was 42 last night",
    "sleep metrics": "Review my Oura sleep data: sleep score 82, deep sleep 61 minutes",
    "blood test": "Analyze my blood test results: LDL 130 mg/dL, HDL 48",
    "symptoms": "I've been having headaches and nausea for a week",
    "medical history": "My medical history includes asthma and a knee surgery",
    "medicine": "I take 500 mg metformin every morning",
    "stored fields": '{"oura_ring": {"readiness": 81, "hrv": 40, "lowest_hr": 52}}',
    "nested": json.dumps({"context": {"notes": [{"t": "resting heart rate 58 bpm"}]}}),
    "no name, no pronoun": "blood pressure 128/82",
    "food log": "I ate two eggs and toast, about 420 calories",
    "plan": "Generate a health plan from my medical history",
    "memory fact": "The user is allergic to peanuts",
}
GENERIC = ("What does HDL cholesterol mean?", "Explain how sleep stages work.", "Summarize public research on magnesium.",
           "Explain general nutrition principles.", "What is a normal resting heart rate for adults?",
           "Is ibuprofen safe with alcohol?")
EVERYDAY = ("Remind me to call mom at 5", "My flight is at 9, when should I leave?", "I'm tired of this app crashing",
            "Take 3 steps: open the app, tap Money, tap Invest", "My portfolio is down 2%", "Sleep on it and tell me",
            "I ate at Nobu last week, find something similar", "What is the weight limit for carry-on bags?")


@pytest.fixture
def memory(tmp_path):
    m = Memory(tmp_path / "buddy.db")
    privacygate.attach(m)
    yield m
    privacygate._memory = None


@pytest.mark.parametrize("what", sorted(HEALTH))
def test_health_information_is_refused_by_every_model_claude_included(memory, what):
    for provider in EVERY:
        with pytest.raises(privacygate.PrivacyBlocked) as e:
            privacygate.authorize(provider, HEALTH[what], "a test")
        assert e.value.kind == "health" and "health information" in str(e.value)


def test_general_questions_and_everyday_requests_are_not_health(memory):
    for question in GENERIC + EVERYDAY:
        assert privacygate.health(question) == [], question
        assert privacygate.authorize("claude", question, "a test")["level"] == privacygate.PUBLIC
    for question in GENERIC:   # with nothing personal in them, they may go to any model
        assert privacygate.authorize("mistral", question, "a test")["kinds"] == []
    for marker in (privacygate.BLOCKED_HEALTH, privacygate.WITHHELD, privacygate.REMOVED):
        assert privacygate.health(marker) == []   # the gateway's own words are never mistaken for health data


@pytest.mark.parametrize("what, payload", [
    ("an email address", "Write to ana.silva@example.com about the invoice"),
    ("a phone number", "Call (415) 555-0142 and ask for the manager"),
    ("a street address", "Send it to 742 Evergreen Terrace"),
    ("a card, account or identity number", "My SSN is 123-45-6789"),
    ("a date of birth", "date of birth: 14 March 1985"),
    ("a key, password or token", "use the key sk-abcdefghijklmnopqrstuvwx to sign in"),
])
def test_personal_details_go_to_claude_only(memory, what, payload):
    for provider in OTHERS:
        with pytest.raises(privacygate.PrivacyBlocked) as e:
            privacygate.authorize(provider, payload, "a test")
        assert e.value.kind == "personal"
    got = privacygate.authorize("claude", payload, "a test")
    assert got["level"] == privacygate.CONFIDENTIAL and what in got["reasons"]


def test_a_name_the_owner_has_told_buddy_is_personal(memory):
    memory.remember("My sister Priyanka Raman lives in Lisbon")
    with pytest.raises(privacygate.PrivacyBlocked):
        privacygate.authorize("openai", "Plan a surprise for Priyanka Raman in Lisbon", "a test", memory)


def test_a_claude_request_has_health_cut_out_of_instructions_history_and_tool_results(memory):
    body = {"model": "x", "tools": [{"name": "my_health", "description": "sleep, HRV and heart rate of the user"}],
            "system": "You are Buddy.\nWhat you remember:\n- The user is allergic to peanuts\n- The user likes jazz",
            "messages": [
                {"role": "user", "content": "(Monday) My HRV was 42 last night\nAlso book a table for two"},
                {"role": "assistant", "content": [{"type": "text", "text": "Booked."},
                                                  {"type": "tool_use", "id": "t1", "name": "note", "input": {"text": "LDL 130 mg/dL"}}]},
                {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1",
                                              "content": [{"type": "text", "text": '{"oura_ring": {"readiness": 81}}'}]},
                                             {"type": "text", "text": "What does HDL cholesterol mean?"}]}]}
    cut = privacygate.sanitize_request(body, "a test")
    sent = json.dumps(body)
    assert cut == 4
    for gone in ("peanuts", "HRV was 42", "LDL 130", "readiness", "oura_ring"):
        assert gone not in sent
    for kept in ("likes jazz", "book a table for two", "Booked.", "What does HDL cholesterol mean?"):
        assert kept in sent
    assert body["tools"][0]["description"].startswith("sleep, HRV")       # Buddy's own tool text is left alone
    assert privacygate.health(privacygate.text_of({k: body[k] for k in ("system", "messages")})) == []
    assert privacygate.sanitize_request({"system": "You are Buddy.", "messages": [{"role": "user", "content": "hi"}]}) == 0


def test_every_claude_request_passes_the_gateway_at_the_client(memory, monkeypatch):
    import anthropic
    sent = []

    async def parent(self, cast_to, options, **kw):
        sent.append(options.json_data)
        return None
    monkeypatch.setattr(anthropic.AsyncAnthropic, "request", parent)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    from buddy import budgets
    monkeypatch.setattr(budgets, "check_claude", lambda: None)
    client = config.claude_client()
    kw = {"model": "m", "max_tokens": 10, "system": "The user takes 20 mg of a statin",
          "messages": [{"role": "user", "content": "My resting heart rate is 58\nWhat's the weather?"}]}
    asyncio.run(client.messages.create(**kw))
    asyncio.run(client.beta.messages.create(**kw))            # the other way in: the same door
    assert len(sent) == 2
    for body in sent:
        text_sent = json.dumps(body, default=str)
        assert "statin" not in text_sent and "heart rate is 58" not in text_sent and "What's the weather?" in text_sent
    events = privacygate.status(memory)["events"]
    assert [e["action"] for e in events] == ["sanitized", "sanitized"]

    class NoBody:
        json_data = "not a request body"
    with pytest.raises(privacygate.PrivacyBlocked):          # a body that cannot be read is refused, not sent
        asyncio.run(client.request(dict, NoBody()))
    with pytest.raises(privacygate.PrivacyBlocked):
        asyncio.run(client.request(dict))
    assert len(sent) == 2


def test_the_free_models_door_refuses_health_and_personal_details_before_anything_is_sent(memory, monkeypatch):
    opened = []
    monkeypatch.setattr(free_models.urllib.request, "urlopen", lambda *a, **k: opened.append(a) or (_ for _ in ()).throw(OSError()))
    for payload in (HEALTH["Oura HRV"], "Write to ana.silva@example.com"):
        with pytest.raises(privacygate.PrivacyBlocked):
            free_models._post("https://api.groq.com/openai/v1/chat/completions",
                              {"model": "m", "messages": [{"role": "user", "content": payload}]}, {})
    assert opened == []
    with pytest.raises(OSError):                               # a public question does get as far as the network
        free_models._post("https://api.groq.com/openai/v1/chat/completions",
                          {"model": "m", "messages": [{"role": "user", "content": GENERIC[0]}]}, {})
    assert len(opened) == 1


def test_a_privacy_refusal_is_final_no_other_model_is_tried(memory, monkeypatch):
    tried = []

    async def ask_with(memory_, choice, prompt, system, fetch=None, env=None):
        tried.append(choice)
        privacygate.authorize(choice, prompt, "Maestro", memory_)
        return {"text": '{"ok": 1}', "usd": 0, "in": 1, "out": 1}
    monkeypatch.setattr(maestro, "ask_with", ask_with)
    monkeypatch.setattr(maestro, "_public", lambda prompt, memory_: True)     # as if the older check had missed it
    monkeypatch.setattr(maestro, "choice_for", lambda memory_, job: "openai:gpt")
    monkeypatch.setattr(maestro, "spending", lambda memory_: {"total": 0})
    assert asyncio.run(maestro.ask(memory, "label", HEALTH["blood test"])) is None
    assert tried == ["openai:gpt"]                                             # the free choice was not tried after it
    tried.clear()
    assert asyncio.run(maestro.ask(memory, "label", "Label this headline: reactor order signed")) == {"ok": 1}


def test_maestro_checks_before_any_model_is_called(memory, monkeypatch):
    called = []
    monkeypatch.setattr(maestro, "_pool_one", lambda *a: called.append(a) or {"text": "{}", "usd": 0, "in": 0, "out": 0})
    for payload in (HEALTH["symptoms"], "Call (415) 555-0142"):
        with pytest.raises(privacygate.PrivacyBlocked):
            asyncio.run(maestro.ask_with(memory, "pool:groq", payload, "system"))
    with pytest.raises(privacygate.PrivacyBlocked):
        asyncio.run(maestro.ask_with(memory, next(iter(maestro.CLAUDE)), HEALTH["symptoms"], "system"))
    assert called == []


def test_if_the_check_itself_fails_the_request_is_refused(memory, monkeypatch):
    def broken(*a, **k):
        raise RuntimeError("classifier down")
    monkeypatch.setattr(privacygate, "classify", broken)
    for provider in EVERY:
        with pytest.raises(privacygate.PrivacyBlocked) as e:
            privacygate.authorize(provider, "hello", "a test")
        assert e.value.kind == "unsure"
    monkeypatch.setattr(privacygate, "health", broken)
    with pytest.raises(privacygate.PrivacyBlocked) as e:
        privacygate.sanitize_request({"system": "x", "messages": [{"role": "user", "content": "hello"}]})
    assert e.value.kind == "unsure"


def test_the_record_keeps_what_kind_of_thing_was_found_and_never_the_thing(memory, caplog):
    secret_words = ("HRV was", "LDL", "mg/dL", "metformin", "headaches", "ana.silva", "555-0142", "peanuts")  # not bare
    # numbers: a random reference or a time of day can contain "42"
    with caplog.at_level("INFO"):
        for payload in (HEALTH["Oura HRV"], HEALTH["blood test"], HEALTH["medicine"], HEALTH["symptoms"], HEALTH["memory fact"]):
            with pytest.raises(privacygate.PrivacyBlocked):
                privacygate.authorize("claude", payload, "a test")
        for payload in ("Write to ana.silva@example.com", "Call (415) 555-0142"):
            with pytest.raises(privacygate.PrivacyBlocked):
                privacygate.authorize("openai", payload, "a test")
        privacygate.withhold("my_health", '{"oura_ring": {"hrv": 42}}')
    got = privacygate.status(memory)
    kept = privacygate.dumps(got) + caplog.text + json.dumps([dict(r) for r in memory.db.execute("SELECT * FROM privacy_events")])
    assert not any(w in kept for w in secret_words)
    assert got["blocked"] == 7 and got["withheld"] == 1 and len(got["events"]) == 8
    newest = got["events"][0]
    assert newest["action"] == "withheld" and newest["kinds"] == "health" and len(newest["ref"]) == 10
    assert got["enabled"] is True and got["can_be_switched_off"] is False and all(p["mandatory"] for p in got["policy"])


def test_a_health_tools_result_goes_to_the_owners_screen_and_not_to_the_model(memory):
    result = json.dumps({"oura_ring": {"readiness": 81, "hrv": 40}, "phone": {"steps": 8421}})
    for tool in privacygate.HEALTH_TOOLS:
        kept, withheld = privacygate.withhold(tool, result)
        assert withheld and kept == privacygate.WITHHELD and "81" not in kept
    assert privacygate.withhold("weather", "sunny, 21C") == ("sunny, 21C", False)
    card = agent.health_card(result)
    assert card["title"] == "Your health" and {"label": "oura ring · readiness", "value": "81"} in card["rows"]
    assert {"label": "phone · steps", "value": "8,421"} in card["rows"] and "not sent to any AI model" in card["text"]


def test_in_the_chat_a_health_message_is_answered_by_buddy_and_a_health_tools_result_is_withheld(monkeypatch):
    mem = Memory(":memory:")
    privacygate.attach(mem)
    convo = agent.Conversation(mem)
    fake = Scripted([[tool_use("my_health", {"days": 7})], [text("It is on your screen.")]])
    monkeypatch.setattr(agent.client.beta.messages, "stream", fake.stream)
    from buddy import health_data
    monkeypatch.setattr(health_data, "run_my_health", lambda memory_, args: '{"oura_ring": {"readiness": 81, "hrv": 40}}')
    said = []

    async def say(s):
        said.append(s)

    asyncio.run(convo.reply("My HRV was 42 last night, is that bad?", say))
    assert fake.requests == []                                         # nothing was sent to the model
    assert "Privacy Gateway blocked this request" in said[0] and "on your screen now" in said[0]
    assert "42" not in json.dumps(convo.messages, default=str)         # and the words are not kept for later turns
    assert any(c.get("title") == "Your health" for c in convo.cards.cards.values()) if hasattr(convo.cards, "cards") else True

    asyncio.run(convo.reply("Show me my health data", say))            # the model asks for the tool
    assert len(fake.requests) == 2
    second = json.dumps(fake.requests[1]["messages"], default=str)
    assert "Withheld by the Privacy Gateway" in second and "81" not in second and "readiness" not in second
    actions = [e["action"] for e in privacygate.status(mem)["events"]]
    assert "blocked" in actions and "withheld" in actions
    privacygate._memory = None


def test_there_is_one_door_to_each_kind_of_model_and_no_way_to_switch_the_gateway_off():
    """A static check of the source: only config.py makes a Claude client, the free models are only reached through
    free_models._post, and no code path writes a setting that turns the gateway off."""
    root = Path(privacygate.__file__).parent
    makes_client, calls_gate = [], {}
    for path in sorted(root.rglob("*.py")):
        source = path.read_text()
        if re.search(r"\b(?:Async)?Anthropic\(", source) or re.search(r"\(anthropic\.AsyncAnthropic\)", source):
            makes_client.append(path.name)
        calls_gate[path.name] = "privacygate." in source
    assert makes_client == ["config.py"]
    assert all(calls_gate[name] for name in ("config.py", "free_models.py", "maestro.py", "agent.py", "server.py"))
    config_source = (root / "config.py").read_text()
    assert config_source.index("privacygate.sanitize_request") < config_source.index("return await super().request")
    post = (root / "free_models.py").read_text().split("def _post(", 1)[1]
    assert post.index("privacygate.authorize_request") < post.index("urlopen")
    server = (root / "server.py").read_text()
    assert '@app.get("/api/privacy"' in server and not re.search(r'@app\.(post|put|delete|patch)\("/api/privacy', server)
    gate = (root / "privacygate.py").read_text()
    assert "memory.set(" not in gate and "os.getenv" not in gate and "environ" not in gate   # nothing to configure off
