"""Insights: every model request logged, then grouped by model, purpose and day."""

import time
from types import SimpleNamespace

from fastapi.testclient import TestClient

from buddy import insights, server, usage
from buddy.memory import Memory


def fake_usage(inp=1000, out=200, cache=0):
    return SimpleNamespace(input_tokens=inp, output_tokens=out, cache_read_input_tokens=cache,
                           cache_creation_input_tokens=0, server_tool_use=None)


def fake_response(text="Sure, done.", tools=()):
    blocks = [SimpleNamespace(type="text", text=text)] + [SimpleNamespace(type="tool_use", name=t) for t in tools]
    return SimpleNamespace(content=blocks, stop_reason="end_turn")


def test_record_logs_the_call_with_preview_time_and_tools():
    mem = Memory(":memory:")
    started = time.monotonic() - 1.5
    usage.record(mem, "claude-sonnet-5-5", fake_usage(), response=fake_response("Booked.", ["set_reminder"]),
                 started=started, request="remind me, card 4111 1111 1111 1111", label="chat")
    row = dict(mem.db.execute("SELECT * FROM model_calls").fetchone())
    assert row["provider"] == "claude" and row["purpose"] == "chat"
    assert row["seconds"] >= 1.5 and row["tools"] == "set_reminder" and row["reply"] == "Booked."
    assert "4111 1111 1111 1111" not in row["request"]  # previews are masked


def test_report_groups_by_model_purpose_and_day():
    mem = Memory(":memory:")
    usage.record(mem, "claude-haiku-5-5", fake_usage(), response=fake_response(), started=time.monotonic(),
                 label="chat")
    usage.record(mem, "claude-sonnet-5-5", fake_usage(cache=3000), purpose="listen")
    usage.log_call(mem, provider="groq", model="llama", purpose="general:translate", input_tokens=500,
                   output_tokens=100, seconds=0.6, request="hola", reply="hello")
    usage.log_call(mem, provider="gemini", model="flash", purpose="general:translate", ok=False,
                   stop="at its free limit")
    r = insights.report(mem, days=7)
    t = r["totals"]
    assert t["requests"] == 4 and t["failed"] == 1 and t["free_requests"] == 2
    assert t["saved_by_router_usd"] > 0 and t["saved_by_free_usd"] > 0
    assert {g["provider"] for g in r["by_model"]} == {"claude", "groq", "gemini"}
    labels = {g["label"] for g in r["by_purpose"]}
    assert {"Talking with you", "Listen-in", "General task: translate"} <= labels
    assert len(r["by_day"]) == 7 and r["by_day"][-1]["requests"] == 4
    assert r["recent"][0]["provider"] == "gemini"  # newest first
    only = insights.report(mem, purpose="listen")["recent"]
    assert [c["purpose"] for c in only] == ["listen"]


def test_insights_endpoint_needs_the_token(monkeypatch):
    monkeypatch.setattr(server, "authorized", lambda t: t == "s3cret")
    with TestClient(server.app) as client:
        assert client.get("/admin/insights").status_code == 401
        body = client.get("/admin/insights?days=3", headers={"Authorization": "Bearer s3cret"}).json()
        assert body["days"] == 3 and "by_model" in body


def test_accuracy_from_claude_reviews_and_thumbs_from_you():
    mem = Memory(":memory:")
    token = usage.current_turn.set("turn1")
    usage.record(mem, "claude-sonnet-5-5", fake_usage(), label="chat")
    good = usage.log_call(mem, provider="deepseek", model="ds", purpose="general:code", seconds=1)
    fixed = usage.log_call(mem, provider="deepseek", model="ds", purpose="general:code", seconds=1)
    usage.log_call(mem, provider="deepseek", model="ds", purpose="general:code", ok=False, verdict="failed_check")
    usage.current_turn.reset(token)
    assert usage.set_verdict(mem, good, "used")
    assert usage.set_verdict(mem, fixed, "fixed", "off-by-one")
    assert not usage.set_verdict(mem, good, "great")  # only the three verdicts
    assert usage.rate_turn(mem, "turn1", False) == 4
    r = insights.report(mem)
    ds = next(g for g in r["by_model"] if g["provider"] == "deepseek")
    assert (ds["accuracy"], ds["usable"], ds["failed_check"]) == (0.5, 1.0, 1)
    assert r["totals"]["thumbs_down"] == 1 and r["totals"]["satisfaction"] == 0.0  # one reply, one vote
    usage.rate_turn(mem, "turn1", None)
    assert insights.report(mem)["totals"]["satisfaction"] is None


def test_turn_id_reaches_the_phone_and_thumbs_come_back(monkeypatch):
    async def fake_reply(self, text, say, via="user"):
        self.turn_key = "abc123"
        await say("Hi.")
    monkeypatch.setattr(server.Conversation, "reply", fake_reply)
    with TestClient(server.app) as c, c.websocket_connect("/ws") as ws:
        ws.send_json({"type": "user", "text": "hello"})
        got = [ws.receive_json() for _ in range(3)]
        assert got[-1] == {"type": "done", "turn": "abc123"}
        server.memory.db.execute("INSERT INTO model_calls (at, provider, model, purpose, turn) "
                                 "VALUES ('2026-10-30', 'claude', 'm', 'chat', 'abc123')")
        ws.send_json({"type": "rate", "turn": "abc123", "up": True})
        ws.send_json({"type": "interrupt"})
    row = server.memory.db.execute("SELECT rating FROM model_calls WHERE turn = 'abc123'").fetchone()
    assert row["rating"] == 1
