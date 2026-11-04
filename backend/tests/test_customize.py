"""The user changes how Buddy works by telling it."""

import asyncio
import json

from buddy import customize
from buddy.agent import Conversation
from buddy.memory import Memory
from buddy.money import Money
from buddy.persona import build_system_prompt


def test_standing_instructions_and_settings_are_kept_and_reach_the_prompt():
    mem = Memory(":memory:")
    c = customize.Customize(mem)
    assert "from now on: Call him Aneesh" in c.add("Call him Aneesh")
    c.add("Prefer Lyft over Uber")
    assert "short" in c.set("reply_length", "short")
    assert "5 days" in c.set("bill_days", "5")
    assert "cricket scores" in c.set("add_briefing_topic", "cricket scores")
    prompt = build_system_prompt([], [], [], customize.prompt_lines(mem))
    assert "- Call him Aneesh" in prompt and "- Prefer Lyft over Uber" in prompt and "one or two short sentences" in prompt
    assert customize.bill_days(mem) == 5 and customize.briefing_topics(mem) == ["cricket scores"]
    c.remove("lyft")
    assert customize.rules(mem) == ["Call him Aneesh"]
    over = json.loads(customize.run_tool(mem, {"action": "list"}))
    assert over["reply_length"] == "short" and over["bill_days"] == 5


def test_bad_values_are_explained():
    mem = Memory(":memory:")
    c = customize.Customize(mem)
    for setting, value in (("bill_days", "40"), ("reply_length", "huge"), ("nonsense", "x")):
        try:
            c.set(setting, value)
        except Exception as e:
            assert str(e)
        else:
            raise AssertionError(setting)


def test_bill_reminders_start_when_the_user_asked():
    from buddy.tools import local_now
    from datetime import timedelta
    mem = Memory(":memory:")
    money = Money(mem)
    money.add_bill("Electric", 80, "USD", (local_now().date() + timedelta(days=5)).isoformat())
    assert money.bills_to_remind() == []           # default: 3 days ahead
    customize.Customize(mem).set("bill_days", 5)
    assert [b["biller"] for b in money.bills_to_remind()] == ["Electric"]


def test_a_change_applies_from_the_next_reply_in_the_same_conversation():
    mem = Memory(":memory:")
    convo = Conversation(mem)
    assert "customize_buddy" in [t["name"] for t in convo.tools()]
    asyncio.run(convo._dispatch("customize_buddy", {"action": "add", "instruction": "Show prices in rupees too"}))
    assert "Show prices in rupees too" in convo.system


def test_the_briefing_can_be_turned_off():
    from buddy import proactive
    mem = Memory(":memory:")
    customize.Customize(mem).set("briefing", "off")
    assert proactive.briefing_due(mem) is False
