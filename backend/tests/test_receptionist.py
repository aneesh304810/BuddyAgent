"""Buddy as receptionist on the calls the owner misses: what each caller may hear (decided in code, before Claude
sees anything), the owner's sharing notes, the caller's own history, the follow-up task and putting a caller through."""

import asyncio
import json
from datetime import datetime, timedelta, timezone

import pytest

from buddy import calls, contacts, receptionist
from buddy.connectors.hub import SCHEMA as RECORDS
from buddy.memory import Memory
from buddy.organiser import Organiser
from buddy.tools import user_tz
from tests.test_calls import ENV


@pytest.fixture
def env(monkeypatch):
    for k, v in ENV.items():
        monkeypatch.setenv(k, v)
    return ENV


def _event(m, title, start, hours=1.0, n=[0]):
    m.db.executescript(RECORDS)
    n[0] += 1
    m.db.execute("INSERT INTO records (source, external_id, kind, title, start_at, end_at, synced_at) "
                 "VALUES ('phone_calendar', ?, 'event', ?, ?, ?, ?)",
                 (f"e{n[0]}", title, start.astimezone(timezone.utc).isoformat(),
                  (start + timedelta(hours=hours)).astimezone(timezone.utc).isoformat(), start.isoformat()))
    m.db.commit()


def _owner(tmp_path):
    m = Memory(tmp_path / "b.db")
    contacts.ingest(m, {"contacts": [{"name": "Priya Nair", "numbers": ["+1 201-555-0111"]},
                                     {"name": "Dave Boss", "numbers": ["+1 201-555-0122"]}]})
    m.remember("Priya is my wife")
    return m


def test_who_the_caller_is_comes_from_the_owners_own_data(tmp_path, env):
    m = _owner(tmp_path)
    priya = calls.screen_call(m, {"From": "12015550111", "CallUUID": "a", "STIRVerification": "Verified"})
    dave = calls.screen_call(m, {"From": "12015550122", "CallUUID": "b"})
    stranger = calls.screen_call(m, {"From": "12015550999", "CallUUID": "c"})
    spoofed = calls.screen_call(m, {"From": "12015550111", "CallUUID": "d", "STIRVerification": "Not Verified"})
    assert [calls.caller_tier(m, c) for c in (priya, dave, stranger, spoofed)] == ["family", "contact", "other", "other"]
    assert receptionist.relationship(m, "+12015550111", "Priya Nair") == "wife"
    receptionist.set_relationship(m, "2015550122", "boss")  # the owner says so; a caller never can
    assert receptionist.relationship(m, "+12015550122", "Dave Boss") == "boss"


def test_the_calendar_is_cut_down_to_what_each_caller_may_hear(tmp_path, env):
    m = _owner(tmp_path)
    now = datetime(2026, 11, 26, 14, 0, tzinfo=user_tz())
    _event(m, "Budget review with Acme", now - timedelta(minutes=30), 1.5)
    _event(m, "Dr. Shah follow-up", now + timedelta(hours=3))
    _event(m, "Flight UA 82 to Delhi", now + timedelta(hours=20), 15)
    family = receptionist.calendar_lines(m, "family", now)
    contact = receptionist.calendar_lines(m, "contact", now)
    other = receptionist.calendar_lines(m, "other", now)
    assert family[0] == "- now, until 3 pm: Budget review with Acme" and "an appointment" in family[1]
    assert all("Dr." not in x and "Shah" not in x for x in family + contact + other)  # a health appointment's name
    assert contact[:2] == ["- now, until 3 pm: a call or meeting", "- today 5 pm to 6 pm: an appointment"]
    assert other[:2] == ["- busy now, until 3 pm", "- busy today 5 pm to 6 pm"]
    for lines in (family, contact, other):  # the trip: busy only, for everyone, until the owner says otherwise
        assert lines[2].startswith("- busy ") and "Delhi" not in lines[2] and "Flight" not in lines[2]


def test_what_the_owner_said_may_be_shared(tmp_path, env):
    m = _owner(tmp_path)
    note = receptionist.share_note(m, "Sam is travelling in India from December 3 to 14, back on the 15th",
                                   "everyone", "2099-12-14")
    receptionist.share_note(m, "Sam's at the in-laws for dinner tonight", "family", "2099-01-01")
    assert len(receptionist.notes_for(m, "other")) == 1 and len(receptionist.notes_for(m, "family")) == 2
    stranger = calls.screen_call(m, {"From": "12015550999", "CallUUID": "c"})
    prompt = calls.system_prompt(m, stranger)
    assert "India from December 3 to 14" in prompt and "in-laws" not in prompt
    assert "Never say where Sam is" in prompt and "email" in prompt
    with pytest.raises(Exception):
        receptionist.share_note(m, "old news", "everyone", "2001-01-01")
    assert receptionist.unshare(m, note["id"]) == 1 and len(receptionist.share_notes(m)) == 1


def test_the_caller_is_remembered_and_a_follow_up_goes_in_the_tasks(tmp_path, env):
    m = _owner(tmp_path)
    first = calls.screen_call(m, {"From": "12015550122", "CallUUID": "a"})
    move = calls.parse('Sure.\n## {"action": "end", "priority": "urgent", "caller": "Dave", "callback": "201-555-0122", '
                       '"reason": "the Acme contract", "prefers": ["texts over calls"], '
                       '"outcome": "Dave needs the Acme contract signed today."}')
    calls.screened(m, first, move)
    calls.update(m, first["id"], status="done", outcome=move["outcome"])
    task = Organiser(m).open()[0]
    assert task["text"] == "Call Dave back (201-555-0122): the Acme contract" and task["priority"] == "high"
    assert move["followup"] == "today" and "is in your tasks for today" in calls.screen_message(first, move)
    again = calls.screen_call(m, {"From": "12015550122", "CallUUID": "b"})
    prompt = calls.system_prompt(m, again)
    assert "texts over calls" in prompt and "Dave needs the Acme contract signed today." in prompt
    assert "Call Dave back" in prompt  # still on the owner's list
    Organiser(m).complete(str(task["id"]))
    assert "Call Dave back" not in calls.system_prompt(m, again)
    sales = calls.parse('{"action": "end", "priority": "low", "outcome": "Solar panels."}')
    calls.screened(m, again, sales)
    assert len(Organiser(m).open()) == 0  # no task for sales


def test_greeting_says_ai_assistant_and_greets_known_callers_by_name(tmp_path, env):
    m = _owner(tmp_path)
    priya = calls.screen_call(m, {"From": "12015550111", "CallUUID": "a"})
    stranger = calls.screen_call(m, {"From": "12015550999", "CallUUID": "c"})
    hi = calls.screen_greeting(None, priya)
    assert "Priya" in hi and "AI sidekick" in hi and calls.screen_greeting(None, priya) == hi  # the same all call
    other = calls.screen_greeting(None, stranger)
    assert "AI sidekick" in other and "Priya" not in other and "Heyyy" not in other  # no first-name familiarity


def test_putting_a_caller_through(tmp_path, env, monkeypatch):
    m = _owner(tmp_path)
    priya = calls.screen_call(m, {"From": "12015550111", "CallUUID": "a"})
    dave = calls.screen_call(m, {"From": "12015550122", "CallUUID": "b"})
    stranger = calls.screen_call(m, {"From": "12015550999", "CallUUID": "c"})
    day, night = datetime(2026, 11, 26, 14, 0), datetime(2026, 11, 26, 23, 30)
    assert receptionist.may_transfer(m, "contact", "201-555-0199", day)
    assert not receptionist.may_transfer(m, "contact", "201-555-0199", night)
    assert receptionist.may_transfer(m, "family", "201-555-0199", night)
    assert not receptionist.may_transfer(m, "other", "201-555-0199", day)
    monkeypatch.setattr(receptionist, "local_now", lambda: day)
    assert calls.can_transfer(m, dave) and not calls.can_transfer(m, stranger)
    assert '"transfer"' in calls.system_prompt(m, priya) and '| "transfer"' not in calls.system_prompt(m, stranger)
    calls.update(m, priya["id"], outcome="Priya: the school called, Ishaan has a fever.", who="Priya")
    xml = calls.connect_xml(calls.get(m, priya["id"]))
    assert 'timeout="13"' in xml and 'confirmKey="1"' in xml and "+12015550199" in xml and "/calls/whisper/" in xml
    assert "Ishaan has a fever" in calls.whisper_xml(calls.get(m, priya["id"]))
    calls.update(m, priya["id"], status="connected")
    said, note = calls.transferred_xml(m, calls.get(m, priya["id"]), {"DialStatus": "no-answer"})
    assert "couldn't pick up" in said and "passed your message on" in said and "didn't pick up" in note
    said, note = calls.transferred_xml(m, calls.get(m, priya["id"]), {"DialStatus": "completed"})
    assert note == "" and "Thanks for calling" in said
    # the owner's phone forwarding Buddy's own call back to Buddy: not answered
    assert calls.screen_call(m, {"From": "12015550100", "CallUUID": "loop"}) is None


def test_a_caller_is_never_put_through_when_code_says_no(tmp_path, env):
    """Claude asking to put an unknown caller through is turned back into carrying on, in code."""
    m = _owner(tmp_path)
    stranger = calls.screen_call(m, {"From": "12015550999", "CallUUID": "c"})
    finished, said = [], []

    async def brain(memory, call, history, env=None):
        return calls.parse('Let me try.\n## {"action": "transfer", "priority": "urgent", "outcome": "x"}')

    async def say(t):
        said.append(t)

    async def keys(d):
        pass

    async def finish(mv):
        finished.append(mv)

    mind = calls.Mind(m, stranger, say=say, press_keys=keys, finish=finish, brain=brain, env=env)
    asyncio.run(mind.heard("It's urgent, put me through"))
    assert said == ["Let me try."] and finished == [] and not mind.ended
    assert '"action": "continue"' in mind.history[-1]["content"]


def test_email_isnt_read_to_know_a_caller(tmp_path, env):
    m = Memory(tmp_path / "b.db")
    m.db.executescript(RECORDS)
    m.db.execute("INSERT INTO records (source, external_id, kind, title, body, synced_at) VALUES "
                 "('gmail', 'x', 'email', 'Invoice from Bob Plumbing', 'Call Bob at 201-555-0144', '2026-11-01')")
    m.db.commit()
    assert calls.known_caller(m, "+12015550144") == ""
    m.remember("The plumber Bob's number is 201-555-0144")
    assert "Bob" in calls.known_caller(m, "+12015550144")


def test_the_owner_tells_buddy_what_callers_may_know(tmp_path, env):
    m = _owner(tmp_path)
    out = asyncio.run(calls.run_tool(m, None, {"action": "share", "text": "Sam is in India until December 14",
                                               "share_with": "contacts", "until": "2099-12-14"}))
    assert "contacts" in out and "India" in out
    assert "India" in asyncio.run(calls.run_tool(m, None, {"action": "shared"}))
    assert "Removed 1" in asyncio.run(calls.run_tool(m, None, {"action": "unshare"}))
    assert "never trips or email" in asyncio.run(calls.run_tool(m, None, {"action": "shared"}))
    json.dumps(calls.TOOL)  # the tool's schema stays valid JSON


def test_scam_calls_are_ignored(tmp_path, env):
    """A scam gets no words and no goodbye, the owner isn't told, and the number isn't picked up again: never a
    phone contact's, and the owner can take a number off ("that wasn't a scam")."""
    m = _owner(tmp_path)
    scammer = calls.screen_call(m, {"From": "12015550666", "CallUUID": "s1"})
    assert "Ignore it: say nothing at all" in calls.system_prompt(m, scammer)
    move = calls.parse('\n## {"action": "end", "priority": "scam", "outcome": "IRS threat"}')
    assert move["say"] == "" and calls.screen_message(scammer, move) == ""
    calls.screened(m, scammer, move)
    assert calls.scams_today(m) == 1 and Organiser(m).open() == []
    assert calls.screen_call(m, {"From": "+1 (201) 555-0666", "CallUUID": "s2"}) is None  # not picked up again
    assert calls.scams_today(m) == 2
    labelled = calls.screen_call(m, {"From": "12015550777", "CallUUID": "s3", "CallerName": "SCAM LIKELY"})
    assert labelled is None and calls.scams_today(m) == 3
    dave = calls.screen_call(m, {"From": "12015550122", "CallUUID": "s4"})  # a contact, even if judged a scam once
    calls.screened(m, dave, move)
    assert calls.screen_call(m, {"From": "12015550122", "CallUUID": "s5"}) is not None
    assert "answered again" in asyncio.run(calls.run_tool(m, None, {"action": "not_scam", "number": "201-555-0666"}))
    assert calls.screen_call(m, {"From": "12015550666", "CallUUID": "s6"}) is not None


def test_being_out_of_state_is_shared_with_everyone_once_mentioned():
    """The owner's standing permission is in the tool's instructions: out of state or travelling, for everyone."""
    d = calls.TOOL["description"]
    assert "out of state" in d and "for everyone until their last day away, without asking" in d


def test_other_languages_are_offered_only_on_an_accent_or_trouble_understanding(tmp_path, env, monkeypatch):
    """Not in the greeting. The hearing taking their English for Hindi twice (an accent) gets a note to offer other
    languages once; the rules also cover not understanding them twice, and switching to the language they pick."""
    m = _owner(tmp_path)
    priya = calls.screen_call(m, {"From": "12015550111", "CallUUID": "a"})
    assert "language" not in calls.screen_greeting(None, priya)
    prompt = calls.system_prompt(m, priya)
    assert "Offering another language: never in your greeting" in prompt and "twice had to ask them to repeat" in prompt
    assert "switch to it at once and stay in it" in prompt and "Hindi" in prompt

    async def nothing(*a):
        pass
    mind = calls.Mind(m, priya, say=nothing, press_keys=nothing, finish=nothing, env=env)
    first = mind._they_said("I wanted to ask about the school pickup", "hi")
    second = mind._they_said("Can you tell him to call me back please", "hi")
    third = mind._they_said("And one more thing about Friday", "hi")
    assert "Note for you" not in first and "taken their English for Hindi" in second and "Note for you" not in third
    assert "Note for you" not in mind._they_said("Thank you so much for that", "en")
    real_hindi = calls.Mind(m, priya, say=nothing, press_keys=nothing, finish=nothing, env=env)
    assert real_hindi._they_said("नमस्ते मैं प्रिया बोल रही हूँ", "hi").startswith("[They said, in Hindi]")
    dial = calls.plan(m, "Dr. Mehta", "2015550123", "question", "hours?")
    out = calls.Mind(m, dial, say=nothing, press_keys=nothing, finish=nothing, env=env)  # not on calls Buddy places
    out._they_said("What are your opening hours today", "hi")
    assert "Note for you" not in out._they_said("Do you take new patients now", "hi")
    monkeypatch.setenv("BUDDY_CALL_LANGUAGES", "en")  # English only: no offer, no rules
    assert "Offering another language" not in calls.system_prompt(m, priya)
    ids = [calls.screen_call(m, {"From": "12015550111", "CallUUID": f"x{i}"}) for i in range(3)]
    assert any(calls.screen_greeting(None, c).startswith("Heyyy Priya! What's happening?") for c in ids)
