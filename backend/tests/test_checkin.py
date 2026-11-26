"""The evening check-in: asked on the first chat after 6 pm, answered in code, kept in the journal, never sent to a
model."""

import asyncio
import json
from datetime import datetime

import pytest

from buddy import agent, checkin, moodlog, privacygate
from buddy.memory import Memory
from tests.test_conversation import Scripted, text

EVENING = datetime(2026, 11, 26, 19, 30)


@pytest.fixture
def m(monkeypatch):
    monkeypatch.setattr(checkin, "local_now", lambda: EVENING)
    monkeypatch.setattr(moodlog, "local_now", lambda: EVENING)
    return Memory(":memory:")


def test_the_answers_are_read_by_code(m):
    assert checkin.scales("4, 2, 3") == {"mood": 4, "stress": 2, "energy": 3}
    assert checkin.scales("stress 5 and mood 2") == {"stress": 5, "mood": 2}
    assert checkin.scales("4 out of 5") == {"mood": 4}
    assert checkin.scales("I'm pretty good") == {"mood": 4} and checkin.scales("fine, thanks") == {"mood": 3}
    assert checkin.scales("ok thanks for the weather") is None and checkin.scales("7, 8") is None
    assert checkin.people("not much, I texted my brother") == "a little"
    assert checkin.people("I talked to no one") == "no" and checkin.people("yes, called my mother") == "yes"
    assert checkin.rested("3") == 3 and checkin.rested("exhausted") == 1 and checkin.rested("10") is None


def test_a_whole_check_in_goes_into_the_journal_and_reads_the_day_back(m):
    assert checkin.due(m)
    assert checkin.start(m).startswith("Evening check-in")
    assert not checkin.due(m)  # asked once a day
    assert checkin.answer(m, "4 2 3") == checkin.QUESTIONS["people"]
    assert checkin.answer(m, "a little") == checkin.QUESTIONS["rested"]
    assert checkin.answer(m, "9") == checkin.AGAIN["rested"]  # a number, not 1 to 5: asked once more
    assert checkin.answer(m, "3") == checkin.QUESTIONS["note"]
    said = checkin.answer(m, "Busy week at work")
    assert said.startswith("Noted in your journal: mood 4, stress 2, energy 3.") and "no AI model" in said
    e = moodlog.entries(m)[0]
    assert (e["mood"], e["stress"], e["energy"], e["people"], e["rested"], e["note"], e["via"]) == \
        (4, 2, 3, "a little", 3, "Busy week at work", "checkin")
    assert checkin.answer(m, "4 2 3") is None  # it's over: a normal message again


def test_skip_a_hard_day_and_messages_that_are_not_answers(m):
    checkin.start(m)
    checkin.answer(m, "2")
    assert checkin.answer(m, "no, I was alone all day") == checkin.QUESTIONS["rested"]
    said = checkin.answer(m, "skip")
    assert said.startswith("Okay, I'll leave it there; I kept what you told me.")
    e = moodlog.entries(m)[0]
    assert e["mood"] == 2 and e["people"] == "no"

    other = Memory(":memory:")
    checkin.start(other)
    assert checkin.answer(other, "what's the weather tomorrow?") is None  # not an answer: goes on as a message
    assert moodlog.entries(other) == [] and checkin.answer(other, "3") is None

    third = Memory(":memory:")
    checkin.start(third)
    assert checkin.answer(third, "I feel anxious about work").startswith("Noted. how are your mood")
    checkin.answer(third, "2, 4, 2")
    checkin.answer(third, "no")
    checkin.answer(third, "2")
    said = checkin.answer(third, "no")
    assert "988" in said and moodlog.entries(third)[0]["note"] == "I feel anxious about work"


def test_check_in_on_request_and_the_switch(m, monkeypatch):
    monkeypatch.setattr(checkin, "local_now", lambda: datetime(2026, 11, 26, 9, 0))
    assert not checkin.due(m)  # mornings: not offered
    assert checkin.answer(m, "check in").startswith("Evening check-in")  # but asked for, any time
    checkin.switch(m, False)
    monkeypatch.setattr(checkin, "local_now", lambda: datetime(2026, 11, 27, 20, 0))
    assert not checkin.due(m) and moodlog.view(m)["checkin_on"] is False
    checkin.switch(m, True)
    assert checkin.due(m)


def test_in_the_chat_the_answers_reach_no_model(m, monkeypatch):
    privacygate.attach(m)
    convo = agent.Conversation(m)
    fake = Scripted([[text("Tomorrow will be sunny, 18 degrees.")]])
    monkeypatch.setattr(agent.client.beta.messages, "stream", fake.stream)
    said = []

    async def say(s):
        said.append(s)
    asyncio.run(convo.reply("What's the weather tomorrow", say))
    assert len(fake.requests) == 1 and said[-1] == checkin.QUESTIONS["scales"]  # the first chat after 6 pm
    for answer in ("3, 4, 2", "yes", "4", "My knee hurts again"):
        asyncio.run(convo.reply(answer, say))
    assert len(fake.requests) == 1  # no answer went to a model
    kept = json.dumps(convo.messages, default=str)
    assert "knee" not in kept and "read by Buddy's code" in kept
    assert moodlog.entries(m)[0]["note"] == "My knee hurts again"
    privacygate._memory = None


def test_no_check_in_when_buddy_just_asked_something(m, monkeypatch):
    convo = agent.Conversation(m)
    fake = Scripted([[text("Shall I book the 7 pm table?")]])
    monkeypatch.setattr(agent.client.beta.messages, "stream", fake.stream)
    said = []

    async def say(s):
        said.append(s)
    asyncio.run(convo.reply("Find me a table for dinner", say))
    assert checkin.QUESTIONS["scales"] not in said and checkin.due(m)  # it waits for the next chat
