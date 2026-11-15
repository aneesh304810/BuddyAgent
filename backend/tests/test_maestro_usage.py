"""Buddy Maestro's spending view: totals, the month's pace, which part of Buddy spends, and the drill-down."""

from datetime import datetime, timedelta, timezone

import pytest

from buddy import maestro_usage as mu
from buddy.memory import Memory
from buddy.tools import ToolError

NOW = datetime(2026, 11, 16, 12, tzinfo=timezone.utc)


@pytest.fixture
def memory():
    return Memory(":memory:")


def call(memory, purpose, cost, days_ago=1, provider="claude", model="claude-sonnet-5-5", ok=1, request=""):
    memory.db.execute(
        "INSERT INTO model_calls (at, provider, model, purpose, input_tokens, output_tokens, cost_usd, seconds, ok, "
        "stop, request) VALUES (?, ?, ?, ?, 1000, 100, ?, 1.5, ?, ?, ?)",
        ((NOW - timedelta(days=days_ago)).isoformat(), provider, model, purpose, cost, ok, "" if ok else "overloaded", request))


def test_every_purpose_buddy_logs_belongs_to_a_component():
    known = {"chat": "conversation", "expert": "conversation", "general:translate": "conversation", "free:make": "conversation",
             "speech": "voice", "listen:factcheck": "voice", "phone_call": "voice", "mail": "email", "brain:night": "email",
             "library": "email", "bankfeed": "money", "finance:statement": "money", "credit_report": "money",
             "card_lookup": "money", "market_scan": "investing", "markets:take": "investing", "signals:tag": "investing",
             "funds": "investing", "deals": "shopping", "seasons": "shopping", "transit": "travel", "dining": "travel",
             "intelligence": "intelligence", "maestro": "intelligence", "techradar": "intelligence", "health_digest": "health",
             "research": "research", "web_digest": "research", "morning_tutor": "learning", "selfcheck": "running",
             "security": "running", "codedocs_qa": "running", "something_new": "other"}
    for purpose, component in known.items():
        assert mu.component_of(purpose) == component, purpose
    assert mu.component_of("chatter") == "other"  # a whole-name match, not a prefix


def test_the_summary_adds_up_and_the_month_shows_its_pace(memory):
    call(memory, "chat", 0.40)
    call(memory, "chat", 0.20, ok=0)
    call(memory, "mail", 0.10, model="claude-haiku-5-5")
    call(memory, "general:translate", 0.0, provider="cerebras", model="llama")
    call(memory, "chat", 9.0, days_ago=45)  # outside 30 days
    memory.db.execute("INSERT INTO usage (day, model, cost_usd) VALUES ('2026-11-10', 'm', 8.0)")
    memory.db.execute("INSERT INTO usage (day, model, cost_usd) VALUES ('2026-10-20', 'm', 12.0)")
    got = mu.view(memory, 30, now=NOW)
    assert got["totals"]["usd"] == 0.7 and got["totals"]["requests"] == 4 and got["totals"]["failed"] == 1
    assert got["totals"]["free_share"] == 0.25 and got["totals"]["per_request"] == 0.175
    assert got["month"] == {"month": "2026-11", "usd": 8.0, "last_month_usd": 12.0, "heading_for_usd": 15.0,
                            "day": 16, "days_in_month": 30}
    parts = {c["id"]: c for c in got["components"]}
    assert list(parts) == ["conversation", "email"] and parts["conversation"]["usd"] == 0.6
    assert parts["conversation"]["requests"] == 3 and parts["conversation"]["share"] == round(0.6 / 0.7, 4)
    assert round(sum(c["share"] for c in got["components"]), 2) == 1.0
    assert len(got["by_day"]) == 30 and round(sum(d["usd"] for d in got["by_day"]), 4) == 0.7
    assert [g["name"] for g in got["by_provider"]] == ["claude", "cerebras"]
    assert mu.view(memory, 90, now=NOW)["totals"]["usd"] == 9.7 and mu.view(memory, 13, now=NOW)["days"] == 30


def test_drilling_down_goes_from_a_component_to_a_purpose_to_its_requests(memory):
    call(memory, "chat", 0.40, request="what is on my calendar")
    call(memory, "expert", 0.90, model="claude-opus-5-5")
    call(memory, "mail", 0.10)
    part = mu.view(memory, 30, "conversation", now=NOW)
    assert part["scope"]["title"] == "The conversation" and part["scope"]["usd"] == 1.3 and "components" not in part
    assert [(p["purpose"], p["name"], p["usd"]) for p in part["purposes"]] == [("expert", "Expert", 0.9),
                                                                              ("chat", "Talking with you", 0.4)]
    assert part["purposes"][0]["share"] == round(0.9 / 1.3, 4) and "requests" not in part
    assert [g["name"] for g in part["by_model"]] == ["claude-opus-5-5", "claude-sonnet-5-5"]
    one = mu.view(memory, 30, purpose="chat", now=NOW)
    assert one["scope"]["component"] == "conversation" and one["scope"]["title"] == "Talking with you"
    assert len(one["requests"]) == 1 and one["requests"][0]["preview"] == "what is on my calendar"
    assert one["requests"][0]["usd"] == 0.4 and one["requests"][0]["tokens_in"] == 1000
    assert [p["purpose"] for p in one["purposes"]] == ["expert", "chat"]  # its siblings stay in view
    with pytest.raises(ToolError):
        mu.view(memory, 30, "nope", now=NOW)
    empty = mu.view(Memory(":memory:"), 7, now=NOW)
    assert empty["components"] == [] and empty["totals"]["requests"] == 0 and empty["month"]["heading_for_usd"] == 0.0


def test_the_endpoint_needs_the_owner_and_the_chat_tool_reports_spending(memory, monkeypatch):
    import asyncio
    import json

    from fastapi.testclient import TestClient

    from buddy import maestro, server
    call(memory, "chat", 0.40, days_ago=0)
    monkeypatch.setattr(server, "memory", memory)
    monkeypatch.setattr(server, "authorized", lambda t: t == "owner-token")
    client = TestClient(server.app)
    assert client.get("/api/maestro/usage").status_code == 401
    ok = {"Authorization": "Bearer owner-token"}
    assert "components" in client.get("/api/maestro/usage?days=7", headers=ok).json()
    assert client.get("/api/maestro/usage?component=nope", headers=ok).status_code == 400
    out = json.loads(asyncio.run(maestro.run_tool(memory, {"action": "spending", "days": 7})))
    assert "month" in out and "by_day" not in out
