"""The escalation ladder: ask the expert, and offer a deeper look when unsure."""

import asyncio
import dataclasses
from types import SimpleNamespace

import pytest

from buddy import agent, expert, server
from buddy.memory import Memory
from buddy.thrift import Thrift
from buddy.tools import ToolError


def msg(stop, text=""):
    return SimpleNamespace(stop_reason=stop, model="claude-opus-5-5",
                           content=[SimpleNamespace(type="text", text=text)] if text else [],
                           usage=SimpleNamespace(input_tokens=2000, output_tokens=800,
                                                 server_tool_use=SimpleNamespace(web_search_requests=2)))


class FakeExpert:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def stream(self, **kw):
        self.calls.append({**kw, "messages": list(kw["messages"])})
        response = self.responses.pop(0)

        class S:
            async def __aenter__(s):
                return s

            async def __aexit__(s, *a):
                return False

            async def get_final_message(s):
                return response
        return S()


ARGS = {"question": "Should I pay my 4111 1111 1111 1111 card in full or keep the cash?",
        "context": "Card APR 24%, savings at 4%.", "why": "money decision"}


def test_expert_answers_with_opus_thinking_harder(monkeypatch):
    fake = FakeExpert(msg("pause_turn"), msg("end_turn", "Pay it in full: 24% beats 4%.\nConfidence: high"))
    monkeypatch.setattr(expert.client.beta.messages, "stream", fake.stream)
    mem = Memory(":memory:")
    answer = asyncio.run(expert.ask_expert(mem, ARGS))
    assert answer.startswith("Pay it in full") and "Confidence: high" in answer and "cost $" in answer
    call = fake.calls[0]
    assert call["model"] == "claude-opus-5-5" and call["output_config"] == {"effort": "high"}
    assert {t["name"] for t in call["tools"]} == {"web_search", "web_fetch"}
    assert "4111 1111" not in call["messages"][0]["content"]  # masked
    assert len(fake.calls) == 2  # resumed after the pause
    assert Thrift(mem).status()["today_usd"] > 0  # counted against the allowance


def test_expert_daily_limit(monkeypatch):
    monkeypatch.setattr(expert, "settings", dataclasses.replace(expert.settings, expert_daily_limit=1))
    fake = FakeExpert(msg("end_turn", "Answer.\nConfidence: medium"))
    monkeypatch.setattr(expert.client.beta.messages, "stream", fake.stream)
    mem = Memory(":memory:")
    asyncio.run(expert.ask_expert(mem, ARGS))
    with pytest.raises(ToolError, match="limit"):
        asyncio.run(expert.ask_expert(mem, ARGS))


def test_expert_asks_first_when_over_the_approval_threshold(monkeypatch):
    monkeypatch.setattr(agent, "settings", dataclasses.replace(agent.settings, approve_over_usd=0.10))
    mem = Memory(":memory:")
    thrift = Thrift(mem)
    convo = agent.Conversation(mem, thrift=thrift)
    out = asyncio.run(convo._dispatch("ask_expert", ARGS))
    assert out.startswith("APPROVAL NEEDED") and "15 cents" in out


def test_chat_offers_the_expert_tool():
    names = {t["name"] for t in agent.Conversation(Memory(":memory:")).tools()}
    assert "ask_expert" in names


def test_low_confidence_quick_research_offers_a_deep_report(monkeypatch):
    sent = []

    async def deliver(kind, text, **extra):
        sent.append(text)
        return True

    monkeypatch.setattr(server, "deliver", deliver)
    job_id = server.jobs.submit("research", "eSIM for Brazil", {"question": "eSIM for Brazil", "depth": "quick", "mode": "web"})
    server.jobs._finish(job_id, "done", summary="Airalo looks cheapest.", report="r", findings="[]",
                        sources="[]", confidence="low", delivered=1)
    before = len(server.jobs.list())
    asyncio.run(server.job_finished(server.jobs.get(job_id)))
    assert "dig deeper" in sent[0] and "1 to 3 dollars" in sent[0]
    said, _ = asyncio.run(server.thrift.resolve("yes"))
    assert "deep report" in said
    newest = server.jobs.get(server.jobs.list()[0]["id"])
    assert len(server.jobs.list()) == before + 1 and newest["params"]["depth"] == "deep"
    server.jobs.cancel(newest["id"])  # don't leave a queued job for other tests


def test_confident_research_offers_nothing(monkeypatch):
    sent = []

    async def deliver(kind, text, **extra):
        sent.append(text)
        return True

    monkeypatch.setattr(server, "deliver", deliver)
    job_id = server.jobs.submit("research", "rent", {"question": "rent", "depth": "quick", "mode": "web"})
    server.jobs._finish(job_id, "done", summary="About $900.", report="r", findings="[]",
                        sources="[]", confidence="high", delivered=1)
    asyncio.run(server.job_finished(server.jobs.get(job_id)))
    assert "dig deeper" not in sent[0]
