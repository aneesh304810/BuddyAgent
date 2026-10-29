"""FinOps: the all-in monthly cost of running Buddy, against fake provider APIs."""

import asyncio
import json
from datetime import datetime, timezone
from types import SimpleNamespace

from buddy import finops as fo
from buddy import usage
from buddy.agent import Conversation
from buddy.jobs import JobRunner
from buddy.memory import Memory
from buddy.tools import local_now


def fake_usage(inp=1_000_000, out=0, searches=0):
    return SimpleNamespace(input_tokens=inp, output_tokens=out, cache_read_input_tokens=0,
                           cache_creation_input_tokens=0,
                           server_tool_use=SimpleNamespace(web_search_requests=searches))


class FakeAPIs:
    def __init__(self, fail=False):
        self.calls = []
        self.fail = fail

    def __call__(self, url, headers):
        self.calls.append((url, headers))
        if self.fail:
            raise OSError("down")
        if "digitalocean" in url:
            assert headers["Authorization"] == "Bearer do-token"
            return {"month_to_date_usage": "7.50", "account_balance": "-4.70"}
        assert headers["x-api-key"] == "admin-key"
        if "page=p2" in url:
            return {"data": [{"results": [{"amount": "250.5"}]}], "has_more": False}
        return {"data": [{"results": [{"amount": "1000"}, {"amount": "49.5"}]}],
                "has_more": True, "next_page": "p2"}


def test_fixed_costs_parse_and_forgive_typos():
    assert fo._parse_fixed("Backblaze=0.10, Domain=$1.25, oops, Twilio=abc") == [
        ("Backblaze", 0.1), ("Domain", 1.25)]


def test_defaults_without_any_keys_show_only_what_buddy_meters():
    memory = Memory(":memory:")
    usage.record(memory, "claude-sonnet-5-5", fake_usage(searches=3), purpose="")
    report = asyncio.run(fo.FinOps(memory, env={}).report())
    lines = {l["provider"]: l for l in report["lines"]}
    assert set(lines) == {"Claude"}  # no server price, Zapier unused: no lines
    assert lines["Claude"]["source"] == "buddy meter" and lines["Claude"]["month_to_date"] == 2.03
    assert report["claude_by_purpose"]["web_searches"] == {"count": 3, "usd_included_above": 0.03}
    assert report["all_in_budget_usd"] is None and not report["over_budget"]


def test_live_bills_from_digitalocean_and_anthropic():
    apis = FakeAPIs()
    env = {"BUDDY_DO_TOKEN": "do-token", "BUDDY_ANTHROPIC_ADMIN_KEY": "admin-key"}
    report = asyncio.run(fo.FinOps(Memory(":memory:"), env=env, fetch=apis).report())
    lines = {l["provider"]: l for l in report["lines"]}
    assert lines["DigitalOcean"] == {**lines["DigitalOcean"], "month_to_date": 7.5, "source": "live"}
    assert "$4.70 of credit left" in lines["DigitalOcean"]["note"]
    assert lines["Claude"]["month_to_date"] == 13.0 and lines["Claude"]["source"] == "live"  # 1300 cents
    assert sum("anthropic" in url for url, _ in apis.calls) == 2  # followed the next page


def test_provider_outage_falls_back_instead_of_failing():
    env = {"BUDDY_DO_TOKEN": "do-token", "BUDDY_ANTHROPIC_ADMIN_KEY": "admin-key"}
    env["BUDDY_COST_SERVER_USD"] = "12"
    report = asyncio.run(fo.FinOps(Memory(":memory:"), env=env, fetch=FakeAPIs(fail=True)).report())
    lines = {l["provider"]: l for l in report["lines"]}
    assert lines["DigitalOcean"]["source"] == "plan price" and "unavailable" in lines["DigitalOcean"]["note"]
    assert lines["Claude"]["source"] == "buddy meter" and "unavailable" in lines["Claude"]["note"]


def test_zapier_tasks_fixed_costs_budget_and_cache():
    memory = Memory(":memory:")
    now = datetime.now(timezone.utc).isoformat()
    for status in ("ok", "ok", "ok", "error"):
        memory.db.execute("INSERT INTO audit (ts, agent, tool, args, status, detail) VALUES (?, 'zapier', 't', '{}', ?, '')",
                          (now, status))
    env = {"BUDDY_ZAPIER_PLAN_USD": "20", "BUDDY_ZAPIER_TASKS_PER_MONTH": "750",
           "BUDDY_FIXED_COSTS": "Backblaze=1, Twilio=2", "BUDDY_ALLIN_BUDGET_USD": "30",
           "BUDDY_COST_SERVER_USD": "12"}  # another host, no DigitalOcean token
    ops = fo.FinOps(memory, env=env)
    report = asyncio.run(ops.report())
    lines = {l["provider"]: l for l in report["lines"]}
    assert lines["Server"]["forecast"] == 12 and lines["Server"]["source"] == "plan price"
    assert lines["Zapier"]["forecast"] == 20 and "about 6 of 750 tasks" in lines["Zapier"]["note"]
    assert lines["Backblaze"]["source"] == "fixed" and lines["Twilio"]["forecast"] == 2
    assert report["forecast_usd"] == 35.0 and report["over_budget"]  # 0 + 12 + 20 + 1 + 2
    assert "over your all-in budget" in ops.spoken_summary(report)
    ops.env = {}
    assert asyncio.run(ops.report()) is report  # cached for 30 minutes
    assert asyncio.run(ops.report(fresh=True)) is not report


def test_claude_split_by_purpose():
    memory = Memory(":memory:")
    usage.record(memory, "claude-haiku-5-5", fake_usage(inp=10_000_000), purpose="listen")  # $1
    usage.record(memory, "claude-opus-5-5", fake_usage(inp=500_000), purpose="expert")  # $2
    usage.record(memory, "claude-sonnet-5-5", fake_usage(inp=2_000_000))  # $4: chat and research
    JobRunner(memory, None)  # creates the jobs table
    memory.db.execute("INSERT INTO jobs (kind, title, params, status, created_at, cost_usd) "
                      "VALUES ('research', 'x', '{}', 'done', ?, 1.5)", (datetime.now(timezone.utc).isoformat(),))
    split = fo.FinOps(memory, env={}).claude_by_purpose()
    assert split["listen"] == 1.0 and split["expert"] == 2.0 and split["research"] == 1.5
    assert split["chat_and_other"] == 2.5  # 7 metered, minus the named ones


def test_forecast_projects_month_to_date():
    fraction, days = fo._month_progress()
    assert 0 < fraction <= 1 and days >= 28
    assert abs(fo._project(10) - round(10 / fraction, 2)) < 0.01


def test_buddy_can_answer_what_it_costs():
    memory = Memory(":memory:")
    convo = Conversation(memory, finops=fo.FinOps(memory, env={"BUDDY_FIXED_COSTS": "Domain=1"}))
    assert "cost_report" in [t["name"] for t in convo.tools()]
    report = json.loads(asyncio.run(convo._dispatch("cost_report", {})))
    assert report["month"] == local_now().strftime("%B %Y")
    assert [l["provider"] for l in report["lines"]] == ["Claude", "Domain"]


def test_any_host_by_name():
    env = {"BUDDY_SERVER_PROVIDER": "Hetzner", "BUDDY_COST_SERVER_USD": "5"}
    line = asyncio.run(fo.FinOps(Memory(":memory:"), env=env).server())
    assert line.provider == "Hetzner" and line.forecast == 5


def test_agents_this_month():
    memory = Memory(":memory:")
    now = datetime.now(timezone.utc).isoformat()
    for agent, status in [("zapier", "ok"), ("zapier", "approved"), ("gmail", "ok"), ("gmail", "blocked")]:
        memory.db.execute("INSERT INTO audit (ts, agent, tool, args, status, detail) VALUES (?, ?, 't', '{}', ?, '')",
                          (now, agent, status))
    agents = {a["agent"]: a for a in fo.FinOps(memory, env={}).by_agent()}
    assert agents["zapier"]["calls_this_month"] == 2 and "4 Zapier tasks" in agents["zapier"]["note"]
    assert agents["gmail"]["calls_this_month"] == 1 and agents["gmail"]["external_cost_usd"] == 0
