"""The research agent's loop, against a scripted fake Claude."""

import asyncio
import json
from types import SimpleNamespace

import pytest

from buddy import research
from buddy.jobs import JobContext, JobFailed, JobRunner
from buddy.memory import Memory

REPORT = (
    "Miraflores is the best fit: safe, walkable, about $900 a month.\n\n"
    "| Area | Rent |\n| --- | --- |\n| Miraflores | $900 |\n\n"
    "## Sources\n- [Numbeo Lima](https://www.numbeo.com/lima)\n- [Lima guide](https://example.org/lima)"
)


def usage(searches=0):
    return SimpleNamespace(input_tokens=1000, output_tokens=500,
                           server_tool_use=SimpleNamespace(web_search_requests=searches))


def msg(stop, *content, searches=0):
    return SimpleNamespace(stop_reason=stop, content=list(content), model="claude-opus-5-5",
                           usage=usage(searches))


def text(t):
    return SimpleNamespace(type="text", text=t)


def tool_use(name, args, id_="t1"):
    return SimpleNamespace(type="tool_use", name=name, input=args, id=id_)


class FakeClaude:
    def __init__(self, steps, summary=None):
        self.steps = list(steps)
        self.summary = summary
        self.stream_calls = []
        self.create_calls = []

    def stream(self, **kw):
        self.stream_calls.append({**kw, "messages": list(kw["messages"])})
        response = self.steps.pop(0)

        class S:
            async def __aenter__(s):
                return s

            async def __aexit__(s, *a):
                return False

            async def get_final_message(s):
                return response
        return S()

    async def create(self, **kw):
        self.create_calls.append(kw)
        if self.summary is None:
            raise RuntimeError("summary call fails")
        return msg("end_turn", text(json.dumps(self.summary)))


def run(fake, params, monkeypatch, memory=None):
    monkeypatch.setattr(research.client.beta.messages, "stream", fake.stream)
    monkeypatch.setattr(research.client.beta.messages, "create", fake.create)
    mem = memory or Memory(":memory:")
    runner = JobRunner(mem, lambda job: None)
    runner.register("research", research.run_research)
    job_id = runner.submit("research", params["question"], params)
    job = runner.get(job_id)
    ctx = JobContext(mem, job)
    return asyncio.run(research.run_research(job, ctx)), ctx, mem


def test_web_research_resumes_pauses_and_summarizes(monkeypatch):
    fake = FakeClaude(
        [msg("pause_turn", text("Searching…"), searches=3), msg("end_turn", text(REPORT), searches=2)],
        summary={"spoken_summary": "Miraflores is your best bet. Rent is about nine hundred a month.",
                 "key_findings": ["Miraflores: about $900"], "sources": [{"title": "Numbeo Lima", "url": "https://www.numbeo.com/lima"}]},
    )
    result, ctx, _ = run(fake, {"question": "Where to stay a month in Lima?", "depth": "quick", "mode": "web"}, monkeypatch)

    assert result.summary.startswith("Miraflores is your best bet")
    assert result.report == REPORT
    assert result.findings == ["Miraflores: about $900"]
    # The paused turn was sent back as-is, with no extra "continue" message.
    second = fake.stream_calls[1]["messages"]
    assert [m["role"] for m in second] == ["user", "assistant"]
    tools = {t["name"]: t for t in fake.stream_calls[0]["tools"]}
    assert tools["web_search"]["type"] == "web_search_20260209"
    assert tools["web_fetch"]["type"] == "web_fetch_20260209"
    assert "code_execution" not in tools  # the web tools bring their own
    assert fake.stream_calls[0]["output_config"] == {"effort": "medium"}
    assert fake.stream_calls[0]["fallbacks"] == "default"
    # 5 searches at a cent each are counted on top of tokens.
    assert ctx.cost > 0.05


def test_data_mode_uses_code_execution_and_masks_data(monkeypatch):
    fake = FakeClaude([msg("end_turn", text("Spending rose 12%."))], summary=None)
    result, _, _ = run(fake, {
        "question": "How did my spending change?", "depth": "deep", "mode": "data",
        "data": "Jan 1200\nFeb 1344\ncard 4111 1111 1111 1111",
    }, monkeypatch)
    call = fake.stream_calls[0]
    assert [t["name"] for t in call["tools"]] == ["code_execution", "recall_memory"]
    assert call["tools"][0]["type"] == "code_execution_20260521"
    assert call["output_config"] == {"effort": "high"}
    assert "4111 1111" not in call["messages"][0]["content"]
    # The summary call failed: Buddy still gets a plain-text summary.
    assert result.summary == "Spending rose 12%."


def test_recall_memory_answers_from_buddys_memory(monkeypatch):
    mem = Memory(":memory:")
    mem.remember("Aneesh's monthly rent budget is $1000.")
    fake = FakeClaude([
        msg("tool_use", tool_use("recall_memory", {"query": "rent budget"})),
        msg("end_turn", text(REPORT)),
    ])
    run(fake, {"question": "Lima stay within my budget", "depth": "quick", "mode": "web"}, monkeypatch, mem)
    tool_result = fake.stream_calls[1]["messages"][2]["content"][0]
    assert tool_result["type"] == "tool_result" and "$1000" in tool_result["content"]


def test_refusal_fails_the_job(monkeypatch):
    fake = FakeClaude([msg("refusal")])
    with pytest.raises(JobFailed, match="declined"):
        run(fake, {"question": "x", "depth": "quick", "mode": "web"}, monkeypatch)


def test_cost_cap_stops_and_keeps_what_was_found(monkeypatch):
    monkeypatch.setattr(research, "settings", type("S", (), {
        "research_max_usd": 0.01, "model": "claude-opus-5-5", "user_name": "Aneesh", "assistant_name": "Buddy",
    })())
    fake = FakeClaude([msg("pause_turn", text("Partial: Miraflores looks good."), searches=5)])
    result, _, _ = run(fake, {"question": "x", "depth": "deep", "mode": "web"}, monkeypatch)
    assert "per-job limit" in result.report
    assert len(fake.stream_calls) == 1


def test_sources_fallback_extracts_links():
    assert research._links(REPORT) == [
        {"title": "Numbeo Lima", "url": "https://www.numbeo.com/lima"},
        {"title": "Lima guide", "url": "https://example.org/lima"},
    ]
