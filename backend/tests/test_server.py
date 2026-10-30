"""End-to-end over the WebSocket, with Claude replaced by a fake."""

import asyncio
from types import SimpleNamespace

from fastapi.testclient import TestClient

from buddy import agent, ambient, server


class FakeStream:
    def __init__(self, text):
        self.text = text

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def __aiter__(self):
        async def gen():
            for word in self.text.split(" "):
                yield SimpleNamespace(type="text", text=word + " ")
                await asyncio.sleep(0)
        return gen()

    async def get_final_message(self):
        return SimpleNamespace(
            stop_reason="end_turn",
            model="claude-opus-5",
            usage=SimpleNamespace(input_tokens=10, output_tokens=5),
            content=[SimpleNamespace(type="text", text=self.text)],
        )


def test_reply_streams_sentences(monkeypatch):
    monkeypatch.setattr(
        agent.client.beta.messages, "stream",
        lambda **kw: FakeStream("Hey Aneesh! Sure thing. You've got this."),
    )
    with TestClient(server.app) as c, c.websocket_connect("/ws") as ws:
        ws.send_json({"type": "user", "text": "hi buddy"})
        got = [ws.receive_json() for _ in range(5)]
    assert [m["type"] for m in got] == ["thinking", "say", "say", "say", "done"]
    got = got[1:]
    assert got[0]["text"] == "Hey Aneesh!"


def test_listen_in_whispers(monkeypatch):
    async def fake_create(**kw):
        return SimpleNamespace(
            stop_reason="end_turn",
            model="claude-opus-5",
            usage=SimpleNamespace(input_tokens=10, output_tokens=5),
            content=[SimpleNamespace(
                type="text",
                text='{"whisper": true, "kind": "about_you", '
                     '"message": "They are joking that you are always late."}',
            )],
        )

    monkeypatch.setattr(ambient.client.beta.messages, "create", fake_create)
    with TestClient(server.app) as c, c.websocket_connect("/ws") as ws:
        ws.send_json({"type": "ambient", "text": "Aneesh hamesha late aata hai", "lang": "hi-IN"})
        msg = ws.receive_json()
    assert msg == {"type": "whisper", "kind": "about_you",
                   "text": "They are joking that you are always late."}


def test_addressed_to_buddy():
    assert server.addressed_to_buddy("Buddy, what did he say?") == "what did he say?"
    assert server.addressed_to_buddy("hey buddy translate that") == "translate that"
    assert server.addressed_to_buddy("Budo, Buddy, why is the allowance 516?") == "why is the allowance 516?"
    assert server.addressed_to_buddy("Okay buddy, what did she say?") == "what did she say?"
    assert server.addressed_to_buddy("hey body what's the score") == "what's the score"
    assert server.addressed_to_buddy("My body hurts after the game") is None  # "body" alone isn't Buddy
    assert server.addressed_to_buddy("my buddy is late") is None


def test_rejects_bad_token(monkeypatch):
    monkeypatch.setattr(server, "authorized", lambda t: False)
    with TestClient(server.app) as c:
        try:
            with c.websocket_connect("/ws") as ws:
                ws.receive_json()
        except Exception as e:  # WebSocketDisconnect with 4401
            assert getattr(e, "code", None) == 4401


def test_job_reports_need_the_token(monkeypatch):
    monkeypatch.setattr(server, "authorized", lambda t: t == "s3cret")
    job_id = server.jobs.submit("research", "rent in Lima", {"question": "rent in Lima"})
    server.jobs._finish(job_id, "done", summary="Miraflores.", report="# Report", findings="[]", sources="[]")
    with TestClient(server.app) as client:
        assert client.get(f"/api/jobs/{job_id}").status_code == 401
        headers = {"Authorization": "Bearer s3cret"}
        job = client.get(f"/api/jobs/{job_id}", headers=headers).json()
        assert job["summary"] == "Miraflores." and job["report"] == "# Report"
        assert any(j["id"] == job_id for j in client.get("/api/jobs", headers=headers).json())
        assert client.get("/api/jobs/99999", headers=headers).status_code == 404


def test_conversation_offers_job_tools():
    names = {t["name"] for t in agent.Conversation(server.memory, server.registry, server.jobs).tools()}
    assert {"start_research", "list_jobs", "job_report", "cancel_job"} <= names


def test_asking_for_a_yes_shows_approval_buttons(monkeypatch):
    from buddy.agents import PendingAction

    async def fake_reply(self, text, say, via="user"):
        await say("Shall I send Priya the email saying Friday works?")
        server.registry.pending["abc123"] = PendingAction("abc123", "gmail", "send_mail", {}, 1, 0.0)

    monkeypatch.setattr(agent.Conversation, "reply", fake_reply)
    try:
        with TestClient(server.app) as c, c.websocket_connect("/ws") as ws:
            ws.send_json({"type": "user", "text": "reply to Priya"})
            assert ws.receive_json() == {"type": "thinking"}
            got = [ws.receive_json() for _ in range(3)]
    finally:
        server.registry.pending.pop("abc123", None)
    assert [m["type"] for m in got] == ["say", "approval", "done"]
    assert got[1]["question"] == "Shall I send Priya the email saying Friday works?"


def test_whisper_on_the_server(monkeypatch):
    import base64
    from buddy import whisper

    async def fake_transcribe(pcm, language="", prompt="", **kwargs):
        assert pcm == b"\x01\x00" * 8000 and language == "auto"
        return "hola, ¿dónde está la estación?", "es"

    monkeypatch.setattr(whisper, "transcribe", fake_transcribe)
    audio = base64.b64encode(b"\x01\x00" * 8000).decode()
    with TestClient(server.app) as c, c.websocket_connect("/ws") as ws:
        ws.send_json({"type": "audio", "pcm": audio, "lang": "auto"})
        got = ws.receive_json()
    assert got == {"type": "heard", "text": "hola, ¿dónde está la estación?", "lang": "es", "id": None}


def test_whisper_not_installed_says_how(monkeypatch):
    import base64
    from buddy import whisper

    async def missing(pcm, language="", prompt="", **kwargs):
        raise whisper.WhisperUnavailable("Whisper isn't installed on the server yet: run deploy/whisper.sh")

    monkeypatch.setattr(whisper, "transcribe", missing)
    with TestClient(server.app) as c, c.websocket_connect("/ws") as ws:
        ws.send_json({"type": "audio", "pcm": base64.b64encode(b"\x00\x00").decode()})
        got = ws.receive_json()
    assert got["type"] == "error" and "whisper.sh" in got["text"]


def test_no_token_means_closed_unless_developing(monkeypatch):
    from dataclasses import replace

    monkeypatch.setattr(server, "settings", replace(server.settings, access_token="", dev_mode=False))
    assert not server.authorized(None) and not server.authorized("anything")
    monkeypatch.setattr(server, "settings", replace(server.settings, access_token="s3cret"))
    assert server.authorized("s3cret") and not server.authorized("s3cre")


def test_api_docs_are_not_public():
    with TestClient(server.app) as c:
        assert c.get("/docs").status_code in (404, 405) or "swagger" not in c.get("/docs").text.lower()
        assert "openapi" not in c.get("/openapi.json").text.lower()


def test_yes_heard_in_listen_in_cannot_approve():
    import asyncio as aio
    from buddy.memory import Memory
    from buddy.thrift import Thrift

    thrift = Thrift(Memory(":memory:"))
    ran = []

    async def act():
        ran.append(1)
        return "Sent."

    thrift.turn = 1
    thrift.ask_action("Send the email to Priya?", act)
    said, _ = aio.run(thrift.resolve("yes, send it", trusted=False))
    assert "tap Approve" in said and not ran and thrift.pending is not None
    said, _ = aio.run(thrift.resolve("yes", trusted=True))
    assert said == "Sent." and ran == [1]


def test_slow_answers_get_a_quick_filler(monkeypatch):
    class SlowStream(FakeStream):
        def __aiter__(self):
            async def gen():
                await asyncio.sleep(0.3)  # e.g. a web search before the first word
                yield SimpleNamespace(type="text", text="Found it. ")
            return gen()

    monkeypatch.setattr(agent, "FILLER_AFTER_SECONDS", 0.05)
    monkeypatch.setattr(agent.client.beta.messages, "stream", lambda **kw: SlowStream("Found it."))
    with TestClient(server.app) as c, c.websocket_connect("/ws") as ws:
        ws.send_json({"type": "user", "text": "best spa near me"})
        assert ws.receive_json() == {"type": "thinking"}
        got = [ws.receive_json() for _ in range(3)]
    assert got[0]["type"] == "say" and got[0]["text"] in agent.FILLERS
    assert got[1] == {"type": "say", "text": "Found it."} and got[2]["type"] == "done"


def test_whispers_become_context_for_follow_ups():
    from buddy.memory import Memory

    convo = agent.Conversation(Memory(":memory:"))
    convo.note_proactive("Canberra is the capital of Australia.")  # nothing said before: still kept
    assert [m["role"] for m in convo.messages] == ["user", "assistant"]
    assert convo.messages[1]["content"] == "Canberra is the capital of Australia."
    convo.messages.append({"role": "user", "content": "a question being answered"})
    convo.note_proactive("A nudge")  # mid-turn: not inserted
    assert convo.messages[-1]["content"] == "a question being answered"


def test_trace_shows_both_directions(monkeypatch, caplog):
    import logging

    monkeypatch.setattr(agent.client.beta.messages, "stream", lambda **kw: FakeStream("Hi there."))
    with TestClient(server.app) as c:
        assert c.post("/admin/trace?on=true").json() == {"tracing": True}
        with caplog.at_level(logging.INFO, logger="buddy.trace"), c.websocket_connect("/ws") as ws:
            ws.send_json({"type": "user", "text": "hey buddy my card is 4111 1111 1111 1111", "via": "room"})
            for _ in range(3):
                ws.receive_json()
        c.post("/admin/trace?on=false")
    lines = [r.getMessage() for r in caplog.records if r.name == "buddy.trace"]
    assert any(line.startswith("← user") and "room" in line for line in lines)
    assert any(line.startswith("→ thinking") for line in lines) and any("→ say" in line for line in lines)
    assert not any("4111 1111 1111 1111" in line for line in lines)  # masked in the log too


def test_tools_load_on_demand_and_history_is_trimmed():
    from buddy.memory import Memory

    convo = agent.Conversation(Memory(":memory:"))
    tools = convo.tools()
    assert tools[0]["type"] == "tool_search_tool_bm25_20251119"
    loaded = {t["name"] for t in tools if not t.get("defer_loading")}
    assert loaded >= {"tool_search_tool_bm25", "remember_fact", "set_reminder", "add_task", "web_search"}
    assert len(loaded) <= 9 and len(tools) > 20  # everything is there, few are loaded
    assert next(t for t in tools if t["name"] == "add_expense")["defer_loading"] is True

    convo.messages = []
    for i in range(30):
        convo.messages += [{"role": "user", "content": f"q{i}"},
                           {"role": "assistant", "content": [{"type": "tool_use", "id": f"t{i}"}]},
                           {"role": "user", "content": [{"type": "tool_result", "tool_use_id": f"t{i}"}]}]
    convo._trim_history()
    assert len(convo.messages) <= agent.KEEP_HISTORY
    assert convo.messages[0]["role"] == "user" and isinstance(convo.messages[0]["content"], str)
    assert agent._request_kwargs("x")["cache_control"] == {"type": "ephemeral"}


def test_a_sentence_cut_off_by_a_pause_is_put_back_together(monkeypatch):
    import base64
    from buddy import whisper

    heard = iter(["Why can't we use...", "the free model for the news?"])

    async def fake_transcribe(pcm, language="", prompt="", **kwargs):
        return next(heard), "en"

    asked = []

    def fake_stream(**kw):
        asked.append(kw["messages"][-1]["content"])
        return FakeStream("Because the news is live.")

    monkeypatch.setattr(whisper, "transcribe", fake_transcribe)
    monkeypatch.setattr(agent.client.beta.messages, "stream", fake_stream)
    monkeypatch.setattr(server, "FRAGMENT_WAIT_SECONDS", 30)
    audio = base64.b64encode(b"\x01\x00" * 800).decode()
    with TestClient(server.app) as c, c.websocket_connect("/ws") as ws:
        ws.send_json({"type": "audio", "pcm": audio, "lang": "en-US", "mode": "talk"})
        first = ws.receive_json()["text"]
        ws.send_json({"type": "user", "text": first})  # the phone sends what it heard: no answer yet
        ws.send_json({"type": "audio", "pcm": audio, "lang": "en-US", "mode": "talk"})
        rest = ws.receive_json()
        assert rest["type"] == "heard"  # not "thinking": the half sentence wasn't answered
        ws.send_json({"type": "user", "text": rest["text"]})
        got = [ws.receive_json() for _ in range(3)]
    assert [m["type"] for m in got] == ["thinking", "say", "done"]
    assert "Why can't we use the free model for the news?" in str(asked[-1])


def test_sleep_until_hey_buddy_switches_the_app_to_listen_in(monkeypatch):
    steps = iter([
        SimpleNamespace(stop_reason="tool_use", model="claude-opus-5", usage=SimpleNamespace(input_tokens=10, output_tokens=5),
                        content=[SimpleNamespace(type="tool_use", id="m1", name="switch_mode", input={"mode": "listen_in"})]),
        SimpleNamespace(stop_reason="end_turn", model="claude-opus-5", usage=SimpleNamespace(input_tokens=10, output_tokens=5),
                        content=[SimpleNamespace(type="text", text="Going quiet.")]),
    ])

    class Step(FakeStream):
        def __init__(self):
            self.final = next(steps)
            super().__init__(" ".join(b.text for b in self.final.content if b.type == "text"))

        def __aiter__(self):
            return super().__aiter__() if self.text else self._none()

        async def _none(self):
            return
            yield

        async def get_final_message(self):
            return self.final

    monkeypatch.setattr(agent.client.beta.messages, "stream", lambda **kw: Step())
    with TestClient(server.app) as c, c.websocket_connect("/ws") as ws:
        ws.send_json({"type": "user", "text": "You can sleep now, until I say hey Buddy."})
        got = []
        while not got or got[-1]["type"] != "done":
            got.append(ws.receive_json())
    assert {"type": "mode", "mode": "listen"} in got
    assert any(m["type"] == "say" and m["text"] == "Going quiet." for m in got)


def test_unsure_talk_clips_are_not_answered(monkeypatch):
    import base64
    from buddy import whisper

    clips = iter([("It's a good idea. It's an equity.", -1.1), ("What's the weather?", -0.2)])

    async def fake_transcribe(pcm, language="", prompt="", meta=None, **kwargs):
        text, score = next(clips)
        meta["confidence"] = score
        return text, "en"

    monkeypatch.setattr(whisper, "transcribe", fake_transcribe)
    audio = base64.b64encode(b"\x01\x00" * 16000).decode()
    with TestClient(server.app) as c, c.websocket_connect("/ws") as ws:
        ws.send_json({"type": "audio", "pcm": audio, "lang": "en-US", "mode": "talk"})
        ws.send_json({"type": "audio", "pcm": audio, "lang": "en-US", "mode": "talk"})
        got = ws.receive_json()
    assert got["type"] == "heard" and got["text"] == "What's the weather?"  # the TV line never came back
