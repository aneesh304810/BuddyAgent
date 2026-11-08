"""Budgets: the Claude cutoff stops every Claude call; the overall budget and its alerts."""

import asyncio
from types import SimpleNamespace

import pytest

from buddy import budgets, usage
from buddy.memory import Memory


def spend(m, usd):
    usage.record(m, "claude-sonnet-5-5", SimpleNamespace(input_tokens=int(usd * 500_000), output_tokens=0))


def test_the_claude_cutoff_stops_every_claude_call(monkeypatch):
    from buddy.config import claude_client
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    m = Memory(":memory:")
    budgets.attach(m)
    try:
        budgets.set_budget(m, "claude", 20)
        spend(m, 21)
        assert budgets.claude_stopped(m)

        async def call():
            await claude_client().messages.create(model="claude-haiku-5-5", max_tokens=5,
                                                  messages=[{"role": "user", "content": "hi"}])
        with pytest.raises(budgets.BudgetStop):
            asyncio.run(call())
        assert "Claude has reached this month's $20 cutoff" in budgets.stop_message(m)
    finally:
        budgets.attach(None)


def test_budgets_are_set_in_the_users_own_words():
    m = Memory(":memory:")
    assert "Claude cutoff is now $30" in budgets.command(m, "Buddy, set my Claude budget to $30")
    assert budgets.claude_cap(m) == 30
    assert "overall monthly budget is now $60" in budgets.command(m, "set my overall budget to 60")
    assert budgets.all_in_budget(m) == 60
    assert budgets.command(m, "what's the weather") == ""


def test_alerts_once_each_at_50_80_100_and_heading_over():
    m = Memory(":memory:")
    budgets.set_budget(m, "claude", 20)
    budgets.set_budget(m, "all_in", 50)
    spend(m, 11)
    said = budgets.alerts(m, {"month_to_date_usd": 26.0, "forecast_usd": 70.0})
    assert any("Claude has used 50%" in s for s in said)
    assert any("52% of your $50 budget" in s or "50% of your $50" in s for s in said)
    assert any("over your $50 budget" in s for s in said)
    assert budgets.alerts(m, {"month_to_date_usd": 26.0, "forecast_usd": 70.0}) == []  # once each
    spend(m, 9.5)
    said = budgets.alerts(m, {"month_to_date_usd": 51.0, "forecast_usd": 70.0})
    assert len([s for s in said if s.startswith("Claude")]) == 1 and "cutoff" in said[0]
    assert any("the whole budget" in s for s in said)


def test_claude_cannot_raise_the_cutoff_without_a_yes():
    m = Memory(":memory:")
    budgets.set_budget(m, "claude", 20)
    asked = []

    class Thrift:
        def ask_action(self, q, run):
            asked.append(q)
            return q
    out = asyncio.run(budgets.run_tool(m, {"action": "set", "which": "claude", "usd": 50}, None, Thrift()))
    assert asked and "Raise the Claude cutoff to $50" in out and budgets.claude_cap(m) == 20
    asyncio.run(budgets.run_tool(m, {"action": "set", "which": "claude", "usd": 10}, None, Thrift()))
    assert budgets.claude_cap(m) == 10  # lowering needs no yes


def test_reminders_still_come_in_plain_words_when_claude_is_stopped():
    from buddy import proactive
    m = Memory(":memory:")
    budgets.set_budget(m, "claude", 1)
    spend(m, 2)

    async def write():
        raise AssertionError("no Claude call when stopped")
    assert asyncio.run(proactive._written_once(m, "reminder:1", write, plain="Reminder: call Amma")) == \
        "Reminder: call Amma"
    assert asyncio.run(proactive._written_once(m, "briefing:x", write)) == ""
