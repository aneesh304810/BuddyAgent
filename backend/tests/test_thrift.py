"""Thrift: allowances, and approvals that only the user's own words can give."""

import asyncio
from types import SimpleNamespace

import pytest

from buddy import agent, usage
from buddy.jobs import JobRunner, run_job_tool
from buddy.memory import Memory
from buddy.thrift import Thrift, answer_of


@pytest.mark.parametrize("text,expected", [
    ("yes", True), ("Yeah go ahead", True), ("ok", True), ("Buddy, yes", True),
    ("haan", True), ("theek hai", True), ("sí", True), ("dale", True), ("sim", True),
    ("no problem", True), ("no", False), ("not now", False), ("nahi", False),
    ("não", False), ("yes, but actually no", False),
    ("what's the weather in Lima tomorrow?", None), ("", None),
])
def test_reads_yes_and_no(text, expected):
    assert answer_of(text) is expected


def spend(memory, usd, model="claude-sonnet-5-5"):
    """Pretend Buddy already spent `usd` today (and this month)."""
    tokens = int(usd / 10 * 1_000_000)  # Sonnet output is $10 per million
    usage.record(memory, model, SimpleNamespace(input_tokens=0, output_tokens=tokens))


class FakeClaude:
    def __init__(self):
        self.requests = []

    def stream(self, **kw):
        self.requests.append({**kw, "messages": list(kw["messages"])})

        class S:
            async def __aenter__(s):
                return s

            async def __aexit__(s, *a):
                return False

            def __aiter__(s):
                async def gen():
                    yield SimpleNamespace(type="text", text="Sunny in Lima.")
                return gen()

            async def get_final_message(s):
                return SimpleNamespace(stop_reason="end_turn", model="claude-sonnet-5-5",
                                       content=[SimpleNamespace(type="text", text="Sunny in Lima.")],
                                       usage=SimpleNamespace(input_tokens=1, output_tokens=1))
        return S()


def talk(convo, text):
    said = []

    async def say(sentence):
        said.append(sentence)

    asyncio.run(convo.reply(text, say))
    return " ".join(said)


def setup(monkeypatch):
    mem = Memory(":memory:")
    thrift = Thrift(mem)
    fake = FakeClaude()
    monkeypatch.setattr(agent.client.beta.messages, "stream", fake.stream)
    return mem, thrift, agent.Conversation(mem, thrift=thrift), fake


def test_monthly_budget_asks_then_resumes_on_yes(monkeypatch):
    mem, thrift, convo, fake = setup(monkeypatch)
    spend(mem, thrift.month_budget())
    out = talk(convo, "weather in Lima tomorrow?")
    assert "used this month's $40 budget" in out and "$10" in out
    assert fake.requests == []  # asking costs nothing
    out = talk(convo, "yes")
    assert "budget is now $50" in out and "Sunny in Lima." in out
    assert thrift.month_budget() == 50
    # Claude answered the original question, not "yes".
    assert "weather in Lima" in fake.requests[0]["messages"][-1]["content"]


def test_no_holds_off(monkeypatch):
    mem, thrift, convo, fake = setup(monkeypatch)
    spend(mem, thrift.month_budget())
    talk(convo, "weather?")
    assert "hold off" in talk(convo, "no")
    assert thrift.month_budget() == 40 and fake.requests == []


def test_hard_cap_cannot_be_approved_away(monkeypatch):
    mem, thrift, convo, fake = setup(monkeypatch)
    mem.set(f"extra:month:{thrift._month()}", "40")  # already at 2x the budget
    spend(mem, 80)
    out = talk(convo, "weather?")
    assert "hard cap of $80" in out
    assert "budget is now" not in talk(convo, "yes")
    assert fake.requests == []


def test_daily_allowance_asks_and_each_yes_adds_a_day(monkeypatch):
    mem, thrift, convo, fake = setup(monkeypatch)
    base = thrift.base_daily()
    spend(mem, base + 0.01)
    assert "daily allowance" in talk(convo, "weather?")
    out = talk(convo, "sure")
    assert "Sunny in Lima." in out
    assert thrift.daily_allowance() == pytest.approx(base * 2)


def test_an_unrelated_reply_is_not_a_yes(monkeypatch):
    mem, thrift, convo, fake = setup(monkeypatch)
    spend(mem, thrift.base_daily() + 0.01)
    talk(convo, "weather?")
    out = talk(convo, "what's on my calendar today?")
    assert "daily allowance" in out  # still asking; nothing approved
    assert thrift.daily_allowance() == pytest.approx(thrift.base_daily())


def test_deep_research_waits_for_the_users_yes(monkeypatch):
    mem, thrift, convo, fake = setup(monkeypatch)
    runner = JobRunner(mem, lambda job: None)
    runner.register("research", lambda job, ctx: None)
    thrift.turn = 1  # inside a user turn, as when Claude calls the tool
    out = run_job_tool(runner, "start_research",
                       {"question": "best laptop under $1500", "depth": "deep", "mode": "web"}, thrift)
    assert out.startswith("APPROVAL NEEDED") and "1 to 3 dollars" in out
    assert runner.list() == []
    said, resume = asyncio.run(thrift.resolve("yes please do"))
    assert "started" in said and resume is None
    assert len(runner.list()) == 1


def test_quick_research_under_the_threshold_just_starts(monkeypatch):
    mem, thrift, _, _ = setup(monkeypatch)
    runner = JobRunner(mem, lambda job: None)
    runner.register("research", lambda job, ctx: None)
    out = run_job_tool(runner, "start_research",
                       {"question": "eSIM for Brazil", "depth": "quick", "mode": "web"}, thrift)
    assert "started" in out and len(runner.list()) == 1


def test_approval_needs_a_later_message():
    thrift = Thrift(Memory(":memory:"))

    async def run():
        return "ran"

    thrift.turn = 3
    thrift.ask_action("Spend two dollars?", run)
    thrift.turn = 2  # a message counted before the question was asked
    assert asyncio.run(thrift.resolve("yes")) == (None, None)  # turn 3: same turn, refused
    assert asyncio.run(thrift.resolve("yes")) == ("ran", None)  # turn 4: approved


def test_listen_in_allowance_asks_once():
    mem = Memory(":memory:")
    thrift = Thrift(mem)
    assert thrift.listen_allowed() == (True, None)
    usage.record(mem, "claude-opus-5-5", SimpleNamespace(input_tokens=0, output_tokens=30_000), purpose="listen")
    allowed, ask = thrift.listen_allowed()
    assert not allowed and "Buddy, yes" in ask
    assert thrift.listen_allowed() == (False, None)  # asked already; stays quiet
    said, _ = asyncio.run(thrift.resolve("Buddy, yes"))
    assert "keep listening" in said
    assert thrift.listen_allowed() == (True, None)


def test_status_and_summary():
    mem = Memory(":memory:")
    thrift = Thrift(mem)
    spend(mem, 0.62)
    status = thrift.status()
    assert status["today_usd"] == 0.62 and status["month_budget_usd"] == 40
    assert "today so far $0.62" in thrift.summary()
