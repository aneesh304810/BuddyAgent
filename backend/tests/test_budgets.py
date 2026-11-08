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
    m.set(budgets.COMMITTED_KEY, "15")
    spend(m, 11)  # 15 + 11 = 26: 52% of 50
    said = budgets.alerts(m, {"month_to_date_usd": 20.0, "forecast_usd": 70.0})
    assert any("Claude has used 50%" in s for s in said)
    assert any("used 50% of this month's $50 budget" in s for s in said)
    assert any("over your $50 budget" in s for s in said)
    assert budgets.alerts(m, {"month_to_date_usd": 20.0, "forecast_usd": 70.0}) == []  # once each
    spend(m, 25)  # 15 + 36 >= 50, and Claude's 36 is past its 20
    budgets._stop_cache.clear()
    said = budgets.alerts(m, {"month_to_date_usd": 40.0, "forecast_usd": 70.0})
    assert len([s for s in said if s.startswith("Claude")]) == 1 and "cutoff" in said[0]
    assert any("$50 budget, so Claude and every paid service are stopped" in s for s in said)


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


def test_the_whole_buddy_switch_stops_claude_and_paid_services_but_not_free_ones(monkeypatch):
    import urllib.request
    from buddy.config import claude_client
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    m = Memory(":memory:")
    budgets.attach(m)
    try:
        budgets.set_budget(m, "all_in", 30)
        m.set(budgets.COMMITTED_KEY, "24")  # the server, owed whatever Buddy does
        spend(m, 3)
        assert not budgets.all_in_stopped(m)
        spend(m, 3.5)  # 24 + 6.5 >= 30
        budgets._stop_cache.clear()
        assert budgets.all_in_stopped(m) and budgets.claude_stopped(m)
        with pytest.raises(budgets.BudgetStop):
            urllib.request.urlopen(urllib.request.Request("https://google.serper.dev/search"), timeout=1)
        with pytest.raises(budgets.BudgetStop):
            asyncio.run(claude_client().messages.create(model="claude-haiku-5-5", max_tokens=5,
                                                        messages=[{"role": "user", "content": "hi"}]))
        with pytest.raises(Exception) as free:  # a free host is not refused by the switch (offline here)
            urllib.request.urlopen(urllib.request.Request("https://127.0.0.1:9/"), timeout=1)
        assert not isinstance(free.value, budgets.BudgetStop)
        assert "every paid service are stopped" in budgets.stop_message(m)
    finally:
        budgets.attach(None)


def test_raising_the_overall_budget_needs_the_users_yes():
    m = Memory(":memory:")
    budgets.set_budget(m, "all_in", 30)
    asked = []

    class Thrift:
        def ask_action(self, q, run):
            asked.append(q)
            return q
    asyncio.run(budgets.run_tool(m, {"action": "set", "which": "all_in", "usd": 0}, None, Thrift()))
    assert asked == ["Turn the overall budget off?"] and budgets.all_in_budget(m) == 30
