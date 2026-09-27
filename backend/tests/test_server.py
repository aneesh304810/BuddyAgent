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
