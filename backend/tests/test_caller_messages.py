"""Messages the owner leaves for one caller: public ones for whoever says they're that person, private ones given
only when code has confirmed the call is from that person's number in the contacts and the carrier verified the
caller ID; given once, the owner told how it was checked, the reply kept as a task, and a lapsed one reported."""

from datetime import date, timedelta

import pytest

from buddy import calls, contacts, receptionist
from buddy.memory import Memory
from buddy.organiser import Organiser
from buddy.tools import ToolError, local_now
from tests.test_calls import ENV

WORDS = "The work is pending; I'll have it done by end of day and send you an email."


@pytest.fixture
def env(monkeypatch):
    for k, v in ENV.items():
        monkeypatch.setenv(k, v)
    return ENV


@pytest.fixture
def m(tmp_path):
    memory = Memory(tmp_path / "b.db")
    contacts.ingest(memory, {"contacts": [{"name": "Kyle Turner", "numbers": ["+1 201-555-0144"]},
                                          {"name": "Kyle Brown", "numbers": ["+1 973-555-0101"]},
                                          {"name": "Dave Boss", "numbers": ["+1 201-555-0122"]}]})
    return memory


def test_a_private_message_needs_a_contact_and_says_which_one(m):
    with pytest.raises(ToolError, match="Which one: Kyle Brown, Kyle Turner"):
        receptionist.leave_message(m, "Kyle", WORDS)
    with pytest.raises(ToolError, match="isn't in your phone's contacts"):
        receptionist.leave_message(m, "Zed", WORDS)
    got = receptionist.leave_message(m, "Kyle Turner", WORDS)
    assert got["private"] and got["numbers"] == 1
    assert got["until"] == (local_now().date() + timedelta(days=7)).isoformat()  # a week when not said
    assert receptionist.leave_message(m, "the plumber", "Come after 3.", private=False)["name"] == "the plumber"


def test_only_kyles_checked_call_carries_the_private_message(m, env):
    receptionist.leave_message(m, "Kyle Turner", WORDS)
    kyle = calls.screen_call(m, {"From": "12015550144", "CallUUID": "a", "STIRVerification": "Verified"})
    spoofed = calls.screen_call(m, {"From": "12015550144", "CallUUID": "b", "STIRVerification": "Not Verified"})
    unchecked = calls.screen_call(m, {"From": "12015550144", "CallUUID": "c"})  # the carrier said nothing
    dave = calls.screen_call(m, {"From": "12015550122", "CallUUID": "d", "STIRVerification": "Verified"})
    prompt = calls.screen_prompt(m, kyle, env)
    assert WORDS in prompt and "carrier verified the caller ID" in prompt
    assert "Greet them warmly by first name, and right after their hello tell them, unasked" in prompt
    assert "Hey Kyle! " in prompt and "asked me to tell you: " + WORDS in prompt
    for other in (spoofed, unchecked, dave):  # the AI on these calls is never given the words
        assert WORDS not in calls.screen_prompt(m, other, env)
        assert '"delivered"' not in calls.screen_prompt(m, other, env)


def test_given_once_with_the_reply_as_a_task_and_the_owner_told(m, env):
    left = receptionist.leave_message(m, "Kyle Turner", WORDS)
    kyle = calls.screen_call(m, {"From": "12015550144", "CallUUID": "a", "STIRVerification": "Verified"})
    calls.screen_prompt(m, kyle, env)
    move = calls.parse('Hey Kyle! Aneesh asked me to pass on a message.\n## {"action": "end", "priority": "normal", '
                       '"caller": "Kyle", "delivered": [' + str(left["id"]) + '], "reply": "Sounds good, thanks", '
                       '"outcome": "Kyle got the message and said thanks."}')
    assert move["delivered"] == [left["id"]] and move["reply"] == "Sounds good, thanks"
    calls.screened(m, kyle, move)
    assert "I gave your message to Kyle Turner (checked by their number in your contacts and the carrier" in \
        move["delivered_note"]
    assert receptionist.messages(m) == []  # given once
    assert any("Kyle Turner replied to your message: Sounds good, thanks" in t["text"] for t in Organiser(m).open())
    assert "I gave your message" in calls.screen_message(kyle, move)


def test_a_message_not_offered_cant_be_marked_given(m, env):
    left = receptionist.leave_message(m, "Kyle Turner", WORDS)
    spoofed = calls.screen_call(m, {"From": "12015550144", "CallUUID": "b", "STIRVerification": "Not Verified"})
    calls.screen_prompt(m, spoofed, env)
    move = {"action": "end", "priority": "normal", "delivered": [left["id"]], "outcome": "x"}
    calls.screened(m, spoofed, move)
    assert len(receptionist.messages(m)) == 1  # still waiting
    assert "the carrier couldn't confirm the caller ID, so I didn't give your private message" in move["delivered_note"]


def test_a_public_message_is_offered_to_anyone_who_says_who_they_are(m, env):
    receptionist.leave_message(m, "Kyle Turner", "Call me after five.", private=False)
    stranger = calls.screen_call(m, {"From": "12015550999", "CallUUID": "z"})
    prompt = calls.screen_prompt(m, stranger, env)
    assert "Call me after five." in prompt and "give it only if the caller says they're Kyle" in prompt
    kyle = calls.screen_call(m, {"From": "12015550144", "CallUUID": "y"})  # his own number: said first, unasked
    assert "this call is from their number in" in calls.screen_prompt(m, kyle, env)


def test_a_lapsed_message_is_reported_and_removed(m):
    receptionist.leave_message(m, "Kyle Turner", WORDS, until=local_now().date().isoformat())
    assert receptionist.lapsed(m, local_now().date()) == []
    said = receptionist.lapsed(m, local_now().date() + timedelta(days=1))
    assert len(said) == 1 and "wasn't given: they didn't call by" in said[0] and receptionist.messages(m) == []


def test_the_tool(m):
    import asyncio
    out = asyncio.run(calls.run_tool(m, None, {"action": "leave_message", "who": "Kyle Turner", "text": WORDS}))
    assert "Private: given only when the call comes from their number" in out
    assert "Kyle Turner" in asyncio.run(calls.run_tool(m, None, {"action": "messages"}))
    assert "Removed 1 message" in asyncio.run(calls.run_tool(m, None, {"action": "cancel_message", "who": "kyle"}))
