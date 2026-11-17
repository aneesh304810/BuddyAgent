"""The daily strategy pass: queued papers are read into rules and tested, failures are removed, passes wait for the
owner, a rule failing live is paused, and each new rule gets a second reading."""

import asyncio
from datetime import date

import pytest

from buddy import pricebook, researchdesk, stratauto, stratlab, stratrepo, stratrules
from buddy.connectors.hub import ConnectorHub
from buddy.memory import Memory
from tests.test_stratrepo import trades

RULES = {
    "Momentum A": {"name": "Momentum A", "summary": "Buys six-month winners.", "universe": "stocks",
                   "buy": [{"field": "ret_126", "op": ">", "value": 0}], "hold_days": 20, "needs": []},
    "Noise C": {"name": "Noise C", "summary": "Buys after a fall.", "universe": "stocks",
                "buy": [{"field": "ret_5", "op": "<", "value": 0}], "hold_days": 10, "needs": []},
    "Value B": {"name": "Value B", "summary": "Buys cheap companies.", "universe": "stocks",
                "buy": [{"field": "pe", "op": "<", "value": 12}], "hold_days": 60, "needs": []},
}


@pytest.fixture
def memory(tmp_path):
    m = Memory(tmp_path / "buddy.db")
    ConnectorHub(m)
    yield m
    pricebook.close_all()


async def ask(m, prompt):
    return next((rule for name, rule in RULES.items() if name in prompt), None)


async def reader(name, data):
    return {"text": data.decode() * 30, "where": "test", "error": ""}


def fetch(url, data=None, headers=None):
    return b"A paper that describes a trading strategy in enough words to be read. "


def queue(memory, *titles):
    researchdesk._ready(memory)
    for i, title in enumerate(titles):
        researchdesk._keep(memory, "arXiv", f"{title}: a study", f"https://arxiv.org/abs/2501.0{i:04d}", f"2025-0{i + 1}-01",
                           f"{title} tested on daily prices.", "arXiv")


def fake_replay(memory, data=None, keys=None, proposed=False):
    assert proposed and keys
    for key in keys:
        if key == "u_momentum_a":
            trades(memory, key, [0.04, 0.03, -0.01, 0.05] * 10, market=0.005)
        if key == "u_noise_c":
            trades(memory, key, [-0.02, 0.01, -0.03, 0.0] * 10, market=0.005)


def test_queued_papers_are_read_tested_and_only_what_passed_is_kept(memory):
    queue(memory, "Old D", "Value B", "Noise C", "Momentum A")            # the newest three are read; the oldest waits
    got = asyncio.run(stratauto.daily(memory, ask, reader, fetch, replay=fake_replay))
    assert got["read"] == 3 and got["no_rule"] == 0 and sorted(got["rules"]) == ["Momentum A", "Noise C", "Value B"]
    assert [p["name"] for p in got["passed"]] == ["Momentum A"] and got["passed"][0]["trades"] == 40
    assert got["kept_untested"] == ["Value B"]                             # company figures: no history to test on
    assert [r["name"] for r in got["removed"]] == ["Noise C"] and "did not beat the market" in got["removed"][0]["why"]
    held = {r["key"]: r["status"] for r in stratrules.rules(memory) if r["key"] in ("u_momentum_a", "u_noise_c", "u_value_b")}
    assert held == {"u_momentum_a": "proposed", "u_value_b": "proposed"}  # nothing was approved; the failure is gone
    assert pricebook.run(memory, "SELECT 1 FROM lab_trades WHERE strategy = 'u_noise_c'") == []
    notes = {q["title"][:7]: q["note"] for q in researchdesk.queue(memory, "read")}
    assert notes["Noise C"].startswith("Read into a rule, tested, and removed:")
    assert [q["title"] for q in researchdesk.queue(memory, "new")] == ["Old D: a study"]
    said = stratauto.message(got)
    assert "Read 3 papers" in said and "✅ Passed on past prices: Momentum A" in said and "Waiting for your approval" in said
    assert "Kept untested: Value B" in said and "Nothing trades until you approve it" in said
    assert stratauto.last(memory)["read"] == 3 and stratrepo.view(memory)["auto"]["last"]["day"] == got["day"]


def test_a_paper_with_no_rule_is_counted_and_nothing_is_said_when_nothing_happened(memory):
    queue(memory, "Unreadable Z")
    got = asyncio.run(stratauto.daily(memory, ask, reader, fetch, replay=fake_replay))
    assert got["read"] == 1 and got["no_rule"] == 1 and got["rules"] == [] and stratauto.message(got) == ""
    empty = asyncio.run(stratauto.daily(memory, ask, reader, fetch, replay=fake_replay))
    assert empty["read"] == 0 and stratauto.message(empty) == ""
    now = date(2026, 11, 18)
    from datetime import datetime
    assert not stratauto.due(memory, datetime(2026, 11, 18, 8, 0)) and stratauto.due(memory, datetime(2026, 11, 18, 9, 5))
    memory.set(stratauto.DAY_KEY, now.isoformat())
    assert not stratauto.due(memory, datetime(2026, 11, 18, 15, 0))


def test_a_rule_of_the_owners_that_fails_live_is_paused_and_a_built_in_one_is_reported(memory):
    asyncio.run(stratrules.propose(memory, "paper.pdf", "Buy six-month winners and hold twenty days. " * 5, ask=lambda m, p: ask(m, "Momentum A")))
    stratrules.decide(memory, "u_momentum_a", "approve")
    trades(memory, "u_momentum_a", [0.04] * 40, market=0.0)
    trades(memory, "u_momentum_a", [-0.02] * 20, market=0.0, backfilled=False, start=date(2026, 6, 1), every=3)
    trades(memory, "dip", [-0.02] * 20, market=0.0, backfilled=False, start=date(2026, 6, 1), every=3)
    trades(memory, "trend", [-0.02] * 12, market=0.0, backfilled=False, start=date(2026, 6, 1), every=3)   # failing, but under 20
    got = stratauto.retire(memory)
    assert [p["name"] for p in got["paused"]] == ["Momentum A"] and "Losing to the market live" in got["paused"][0]["why"]
    assert [p["name"] for p in got["failing_built_in"]] == ["Buy the dip"]
    assert stratrules.one(memory, "u_momentum_a")["status"] == "paused"
    assert "u_momentum_a" not in stratlab.rules_in_use(memory)             # a paused rule opens no more trades
    assert stratauto.retire(memory)["paused"] == []                        # already paused: not paused twice
    said = stratauto.message({"read": 0, "rules": [], "removed": [], "passed": [], "kept_untested": [], **got})
    assert "⏸ Paused: Momentum A" in said and "⚠️ Failing live: Buy the dip (built in, so not paused)" in said


def test_a_new_rule_gets_a_second_reading_that_is_shown_and_changes_nothing(memory):
    rule = asyncio.run(stratrules.propose(memory, "paper.pdf", "Buy six-month winners and hold twenty days. " * 5,
                                          ask=lambda m, p: ask(m, "Momentum A")))
    assert rule["critique"] is None
    seen = []

    async def critic(m, prompt):
        seen.append(prompt)
        return {"faithful": False, "issues": ["The paper skips the most recent month; the rule does not."] * 5,
                "missing": ["a volatility filter"], "ignore": "extra"}

    said = asyncio.run(stratrules.critique(memory, "u_momentum_a", "The paper text.", critic))
    assert said == {"faithful": False, "issues": ["The paper skips the most recent month; the rule does not."] * 3,
                    "missing": ["a volatility filter"]}
    assert "untrusted text" in seen[0] and "The paper text." in seen[0] and "ret_126 > 0" in seen[0]
    after = stratrules.one(memory, "u_momentum_a")
    assert after["critique"] == said and after["rule"] == rule["rule"] and after["status"] == "proposed"

    async def useless(m, prompt):
        return {"verdict": "fine"}
    assert asyncio.run(stratrules.critique(memory, "u_momentum_a", "x", useless)) is None
    assert stratrules.one(memory, "u_momentum_a")["critique"] == said     # an unusable answer does not erase the last one


def test_a_proposed_rule_can_be_tested_without_being_switched_on(memory):
    asyncio.run(stratrules.propose(memory, "paper.pdf", "Buy six-month winners and hold twenty days. " * 5, ask=lambda m, p: ask(m, "Momentum A")))
    assert "u_momentum_a" not in stratlab.rules_in_use(memory)
    assert stratlab.rules_in_use(memory, proposed=True)["u_momentum_a"]["name"] == "Momentum A"
    stratrules.decide(memory, "u_momentum_a", "pause")
    assert "u_momentum_a" not in stratlab.rules_in_use(memory, proposed=True)   # paused is not proposed
