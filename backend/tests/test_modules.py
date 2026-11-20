"""Modules: the list, the operator's switch, the tools a conversation is offered, and the meter."""

import asyncio
from datetime import datetime, timezone

import pytest

from buddy import agent, modules, usage
from buddy.memory import Memory


def test_unset_means_everything_buddy_has_today_and_nothing_planned():
    on = modules.switched_on({})
    assert on == modules.switched_on({"BUDDY_MODULES": "all"})
    assert {"core", "voice", "money", "investing", "selfcare", "devices", "link"} <= on
    assert not on & {"vision", "integrations", "governance"}  # for glasses, not built yet: listed, never on


def test_the_setting_picks_modules_and_core_is_always_on():
    assert modules.switched_on({"BUDDY_MODULES": "core"}) == {"core"}
    assert modules.switched_on({"BUDDY_MODULES": "voice, Money"}) == {"core", "voice", "money"}
    assert modules.switched_on({"BUDDY_MODULES": "-core"}) == {"core"}  # nothing switches core off
    some = modules.switched_on({"BUDDY_MODULES": "all,-investing,-calls"})
    assert "investing" not in some and "calls" not in some and "money" in some
    assert modules.switched_on({"BUDDY_MODULES": "core,vision,nonsense"}) == {"core"}  # unbuilt or unknown: ignored
    assert modules.switched_on({"BUDDY_MODULES": "core,link"}) == {"core"}  # Link needs Devices: off without it
    assert modules.new_setting("core", "link", True) == "core,devices,link"  # switching it on brings what it needs
    with pytest.raises(ValueError, match="Link needs Devices"):
        modules.new_setting("core,devices,link", "devices", False)


def test_switching_writes_the_setting_and_says_why_when_it_cannot():
    assert modules.new_setting("", "investing", False) == "all,-investing"
    assert modules.new_setting("all,-investing", "investing", True) == "all"
    assert modules.new_setting("core", "money", True) == "core,money"
    assert modules.new_setting("core,voice,money", "voice", False) == "core,money"
    for module, on, why in (("core", False, "always on"), ("vision", True, "not built"), ("nope", True, "No module")):
        with pytest.raises(ValueError, match=why):
            modules.new_setting("", module, on)


def test_every_tool_belongs_to_exactly_one_module():
    """A new tool must be given a module, or switching modules off would silently leave it on."""
    convo = agent.Conversation(Memory(":memory:"))
    names = {t["name"] for t in convo._all_tools()}
    for group in ("JOB_TOOLS", "MAIL_TOOLS", "WATCH_TOOLS", "BUILDER_TOOLS", "CODE_TOOLS", "CONNECTOR_TOOLS",
                  "ORCHESTRATION_TOOLS"):
        names |= {t["name"] for t in getattr(agent, group)}
    for single in ("SPENDING_TOOL", "COST_TOOL", "GENERAL_TOOL", "VERDICT_TOOL", "ROOM_TOOL", "BROWSE_TOOL",
                   "HAND_OVER_TOOL"):
        names.add(getattr(agent, single)["name"])
    names |= {agent.datatools.DATA_TOOL["name"], agent.datatools.TOOLS_ADMIN["name"], "fill_form", "google_search",
              "reddit", "trending", "web_fetch"}
    assert sorted(n for n in names if n not in modules._TOOL_MODULE) == []
    listed = [t for tools in modules.TOOLS.values() for t in tools]
    assert len(listed) == len(set(listed))  # in one module only
    assert set(modules.TOOLS) <= set(modules.BY_ID) and all(modules.BY_ID[m].built for m in modules.TOOLS)
    assert modules.module_of_tool("gmail__read_mail") == "email"      # a specialist agent's tools follow it
    assert modules.module_of_tool("zapier__add_row") == "agents"


def test_a_module_that_is_off_has_no_tools_and_a_call_to_one_is_refused(monkeypatch):
    convo = agent.Conversation(Memory(":memory:"))
    everything = {t["name"] for t in convo._all_tools()}
    assert {"markets", "spending", "phone_call", "modules", "remember_fact"} <= everything

    monkeypatch.setenv("BUDDY_MODULES", "all,-investing,-money")
    left = {t["name"] for t in convo._all_tools()}
    assert not left & {"markets", "paper_portfolio", "spending", "bank_link"}
    assert {"phone_call", "modules", "remember_fact", "weather"} <= left
    said = asyncio.run(convo._dispatch("markets", {"action": "ideas"}))  # asked for anyway: not run
    assert "Investing module is switched off" in said

    monkeypatch.setenv("BUDDY_MODULES", "core")
    core = {t["name"] for t in convo._all_tools()}
    assert {"remember_fact", "set_reminder", "modules", "show_cards"} <= core
    assert not core & {"phone_call", "weather", "make_document", "english_tutor", "buddy_code"}


def test_a_conversation_can_look_at_the_modules_but_not_switch_them(monkeypatch):
    monkeypatch.setenv("BUDDY_MODULES", "all,-investing")
    convo = agent.Conversation(Memory(":memory:"))
    said = asyncio.run(convo._dispatch("modules", {"action": "list"}))
    assert "Investing [investing]: off" in said and "Money [money]: on" in said and "Vision [vision]: not built" in said
    assert "deploy/modules.sh" in said
    assert modules.TOOL["input_schema"]["properties"]["action"]["enum"] == ["list", "statement"]  # no switch here


def test_the_meter_counts_requests_tools_and_minutes_for_the_module_they_belong_to():
    mem = Memory(":memory:")
    usage.log_call(mem, provider="claude", model="claude-haiku-5-5", purpose="chat", cost=0.01)
    usage.log_call(mem, provider="claude", model="claude-haiku-5-5", purpose="markets:take", cost=0.02)
    usage.log_call(mem, provider="claude", model="claude-haiku-5-5", purpose="markets:take", cost=0.03)
    usage.log_call(mem, provider="deepgram", model="flux", purpose="speech", seconds=30, cost=0.004)
    usage.log_tool(mem, "markets", 0.2, True)
    usage.log_tool(mem, "gmail__search_mail", 0.2, True)
    modules.record(mem, "voice", "minute", 0.5)
    modules.record(mem, "voice", "minute", 1.0)

    s = modules.statement(mem)
    by = {m["module"]: {line["unit"]: line for line in m["lines"]} for m in s["modules"]}
    assert by["core"]["request"]["amount"] == 1 and by["core"]["request"]["cost_usd"] == 0.01
    assert by["investing"]["request"]["amount"] == 2 and by["investing"]["request"]["cost_usd"] == 0.05
    assert by["investing"]["use"]["amount"] == 1 and by["email"]["use"]["amount"] == 1
    assert by["voice"]["minute"]["amount"] == 1.5 and by["voice"]["request"]["cost_usd"] == 0.004
    assert by["money"]["day_on"]["amount"] == 1  # on today, though nothing used it
    assert s["priced"] is False and s["charge_usd"] == 0 and s["cost_usd"] == pytest.approx(0.064)
    assert "No price list is set" in modules.statement_text(mem)


def test_a_paid_outside_call_is_counted_and_a_broken_meter_never_breaks_a_call(monkeypatch):
    from buddy import gateway, travel

    mem = Memory(":memory:")
    gateway._table(mem)
    travel.log_call(mem, "serpapi", "/search", True, 0.4, "flights", cost=0.015)
    travel.log_call(mem, "plivo", "call minutes", True, 120, "phone call", cost=0.02)
    lines = {(r["module"], r["unit"]): r["amount"] for r in mem.db.execute("SELECT * FROM module_usage")}
    assert lines[("travel", "outside_call")] == 1 and lines[("calls", "outside_call")] == 1

    monkeypatch.setattr(modules, "record", lambda *a, **k: 1 / 0)
    usage.log_call(mem, provider="claude", model="claude-haiku-5-5", purpose="chat", cost=0.01)  # still logged
    usage.log_tool(mem, "markets", 0.1, True)
    travel.log_call(mem, "serpapi", "/search", True, 0.4, "flights", cost=0.015)
    assert mem.db.execute("SELECT COUNT(*) AS n FROM model_calls").fetchone()["n"] == 1


def test_prices_are_the_operators_table_and_a_month_is_its_own_statement(tmp_path):
    mem = Memory(":memory:")
    november = datetime(2026, 11, 20, tzinfo=timezone.utc)
    modules.record(mem, "voice", "minute", 90, cost=0.7, now=november)
    modules.record(mem, "voice", "request", 40, cost=0.2, now=november)
    modules.record(mem, "voice", "minute", 5, now=datetime(2026, 12, 1, tzinfo=timezone.utc))
    prices = tmp_path / "prices.csv"
    prices.write_text("module,unit,price_usd\nvoice,minute,0.05\nnonsense,minute,9\nvoice,request,free\n")
    env = {"BUDDY_PRICE_LIST": str(prices)}

    s = modules.statement(mem, "2026-11", env)
    voice = next(m for m in s["modules"] if m["module"] == "voice")
    lines = {line["unit"]: line for line in voice["lines"]}
    assert lines["minute"]["amount"] == 90 and lines["minute"]["charge_usd"] == 4.5
    assert lines["request"]["charge_usd"] is None  # no price for this unit: counted, not charged
    assert s["priced"] and s["charge_usd"] == 4.5 and voice["cost_usd"] == 0.9
    assert "charge $4.50" in modules.statement_text(mem, "2026-11", env)
    assert modules.statement(mem, "2026-10", env)["charge_usd"] == 0


def test_the_operators_command_lists_and_switches(capsys):
    assert modules.main(["list", "all,-investing"]) == 0
    assert "Investing [investing]: off" in capsys.readouterr().out
    assert modules.main(["off", "calls", "all,-investing"]) == 0
    assert capsys.readouterr().out.strip() == "all,-calls,-investing"
    assert modules.main(["on", "vision", ""]) == 1
    assert "not built" in capsys.readouterr().err


def test_the_admin_page_shows_modules_and_the_statement(monkeypatch):
    from fastapi.testclient import TestClient

    from buddy import server

    monkeypatch.setattr(server, "authorized", lambda t: t == "s3cret")
    with TestClient(server.app) as c:
        assert c.get("/admin/modules").status_code == 401
        got = c.get("/admin/modules", headers={"Authorization": "Bearer s3cret"}).json()
        assert any(m["module"] == "voice" and m["on"] for m in got["modules"]) and "month" in got["statement"]
        text = c.get("/admin/modules?text=1", headers={"Authorization": "Bearer s3cret"}).text
        assert "Modules on this server" in text
