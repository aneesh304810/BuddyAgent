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
        got = [ws.receive_json() for _ in range(4)]
    assert [m["type"] for m in got] == ["say", "say", "say", "done"]
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

    async def fake_reply(self, text, say):
        await say("Shall I send Priya the email saying Friday works?")
        server.registry.pending["abc123"] = PendingAction("abc123", "gmail", "send_mail", {}, 1, 0.0)

    monkeypatch.setattr(agent.Conversation, "reply", fake_reply)
    try:
        with TestClient(server.app) as c, c.websocket_connect("/ws") as ws:
            ws.send_json({"type": "user", "text": "reply to Priya"})
            got = [ws.receive_json() for _ in range(3)]
    finally:
        server.registry.pending.pop("abc123", None)
    assert [m["type"] for m in got] == ["say", "approval", "done"]
    assert got[1]["question"] == "Shall I send Priya the email saying Friday works?"


def test_whisper_on_the_server(monkeypatch):
    import base64
    from buddy import whisper

    async def fake_transcribe(pcm, language=""):
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

    async def missing(pcm, language=""):
        raise whisper.WhisperUnavailable("Whisper isn't installed on the server yet: run deploy/whisper.sh")

    monkeypatch.setattr(whisper, "transcribe", missing)
    with TestClient(server.app) as c, c.websocket_connect("/ws") as ws:
        ws.send_json({"type": "audio", "pcm": base64.b64encode(b"\x00\x00").decode()})
        got = ws.receive_json()
    assert got["type"] == "error" and "whisper.sh" in got["text"]
