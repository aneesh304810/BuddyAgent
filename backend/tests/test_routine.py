"""Buddy learns the owner's routine from where the phone goes, knows what they're doing now, and greets callers by
name from the phone's contacts (or callers it saved after an important call)."""

import json
from datetime import datetime, timedelta

from buddy import calls, contacts, routine
from buddy.maps import Maps
from buddy.memory import Memory
from buddy.tools import local_now

HOME, WORK, GYM = (40.7400, -74.0300), (40.7130, -74.0060), (40.7300, -74.0500)


def _week_of_history(m: Memory, weeks: int = 4) -> None:
    """Weekdays: home until 7:30, on the move to 8:30, office 8:30-5:30, home from 6:30. Saturdays: gym 9-11."""
    maps = Maps(m)
    maps.name_place("home", *HOME)
    maps.name_place("work", *WORK)
    maps.name_place("gym", *GYM)
    tz = local_now().tzinfo
    start = (local_now() - timedelta(days=7 * weeks)).replace(hour=0, minute=0, second=0, microsecond=0)
    for d in range(7 * weeks):
        day = start + timedelta(days=d)
        for minute in range(0, 24 * 60, 15):
            t = (day + timedelta(minutes=minute)).astimezone(tz)
            if day.weekday() < 5:
                if 450 <= minute < 510:  # 7:30-8:30: a point every 15 minutes, each a bit further along
                    step = (minute - 450) / 60
                    spot = (HOME[0] + (WORK[0] - HOME[0]) * step + 0.004 * (minute % 30 == 15),
                            HOME[1] + (WORK[1] - HOME[1]) * step)
                elif 510 <= minute < 1050:
                    spot = WORK
                elif 1050 <= minute < 1110:
                    spot = ((HOME[0] + WORK[0]) / 2 + 0.01 * (minute % 30 == 15), (HOME[1] + WORK[1]) / 2)
                else:
                    spot = HOME
            elif day.weekday() == 5 and 540 <= minute < 660:
                spot = GYM
            else:
                spot = HOME
            maps.log_location(spot[0], spot[1], at=t)


def test_learns_the_weekly_routine(tmp_path):
    m = Memory(tmp_path / "b.db")
    _week_of_history(m)
    r = routine.learn(m)
    week = {b["what"]: (b["from"], b["to"]) for b in r["weekdays"]}
    assert week["work"] == (510, 1050)  # 8:30 am to 5:30 pm
    assert any(b["what"] == "commuting" for b in r["weekdays"])
    assert {"what": "gym", "from": 540, "to": 660} in r["saturday"]
    text = routine.describe(r)
    assert "Weekdays:" in text and "at the office 8:30 am to 5:30 pm" in text and "at gym 9 am to 11 am" in text


def test_what_theyre_doing_now(tmp_path, monkeypatch):
    m = Memory(tmp_path / "b.db")
    _week_of_history(m)
    routine.save(m, routine.learn(m))
    monday = local_now().replace(tzinfo=None)
    monday = (monday - timedelta(days=monday.weekday())).replace(hour=10, minute=0, second=0, microsecond=0)
    monkeypatch.setattr(routine, "_events_now", lambda memory, now: [])
    monkeypatch.setattr("buddy.maps.Maps.situation", lambda self, now=None: {})
    assert routine.activity_now(m, monday) == {"what": "at the office", "until": "5:30 pm", "source": "routine"}
    monkeypatch.setattr(routine, "_events_now", lambda memory, now: [
        {"title": "Dr. Shah checkup", "start": monday - timedelta(minutes=20), "end": monday + timedelta(minutes=40)}])
    assert routine.activity_now(m, monday) == {"what": "at a doctor's appointment", "until": "10:40 am",
                                               "source": "calendar"}
    monkeypatch.setattr(routine, "_events_now", lambda memory, now: [
        {"title": "Budget review", "start": monday, "end": monday + timedelta(hours=1)}])
    assert routine.activity_now(m, monday)["what"] == "in a meeting"
    monkeypatch.setattr(routine, "_events_now", lambda memory, now: [])
    monkeypatch.setattr("buddy.maps.Maps.situation", lambda self, now=None: {"moving_kmh": 40, "driving": True})
    assert routine.activity_now(m, monday)["what"] == "driving"
    monkeypatch.setattr("buddy.maps.Maps.situation", lambda self, now=None: {"moving_kmh": 20})
    monkeypatch.setattr(routine, "_commute", lambda memory: {"morning": {"path_station": "33rd"}})
    assert routine.activity_now(m, monday)["what"] == "on the train"


def test_routine_tool_and_what_they_told(tmp_path):
    m = Memory(tmp_path / "b.db")
    m.remember("I go to the gym every Tuesday and Thursday at 7 am")
    out = routine.run_tool(m, {"action": "show"})
    assert "not enough location history yet" in out and "gym every Tuesday" in out
    _week_of_history(m)
    assert "at the office" in routine.run_tool(m, {"action": "relearn"})


def test_contacts_sync_and_lookup(tmp_path):
    m = Memory(tmp_path / "b.db")
    n = contacts.ingest(m, {"contacts": [{"name": "Priya Nair", "numbers": ["+1 (201) 555-0111", "201.555.0112"]},
                                         {"name": "", "numbers": ["2015550113"]}, {"name": "Short", "numbers": ["911"]}]})
    assert n == 2
    assert contacts.name_for(m, "12015550111") == ("Priya Nair", "contact")
    assert contacts.name_for(m, "+12015550112") == ("Priya Nair", "contact")
    assert contacts.name_for(m, "2015559999") == ("", "")
    contacts.ingest(m, {"contacts": []})  # a sync replaces the list
    assert contacts.name_for(m, "2015550111") == ("", "")


def test_screened_call_greets_contacts_by_name_and_saves_important_strangers(tmp_path, monkeypatch):
    for k, v in {"BUDDY_OWNER_FULL_NAME": "Sam Rivera", "BUDDY_PLIVO_NUMBER": "+12015550100"}.items():
        monkeypatch.setenv(k, v)
    m = Memory(tmp_path / "b.db")
    contacts.ingest(m, {"contacts": [{"name": "Priya Nair", "numbers": ["2015550111"]}]})
    priya = calls.screen_call(m, {"From": "12015550111", "CallUUID": "c1"})
    assert calls.screen_greeting(None, priya).startswith("Hi Priya, this is Sam's assistant.")
    prompt = calls.system_prompt(m, priya)
    assert "Priya Nair, in Sam's contacts" in prompt and "This caller is someone Buddy knows" in prompt

    stranger = calls.screen_call(m, {"From": "12015550222", "CallUUID": "c2"})
    assert calls.screen_greeting(None, stranger).endswith("Who's calling, please?")
    prompt = calls.system_prompt(m, stranger)
    assert "an unknown number" in prompt and "Ask their name early" in prompt
    assert "Only if the caller were someone Buddy knows" in prompt
    move = calls.parse(json.dumps({"say": "Thanks", "action": "end", "priority": "normal", "caller": "Raj from Acme",
                                   "outcome": "Wants to reschedule Thursday's demo."}))
    calls.screened(m, stranger, move)
    assert contacts.name_for(m, "2015550222") == ("Raj from Acme", "saved")
    again = calls.screen_call(m, {"From": "12015550222", "CallUUID": "c3"})
    assert calls.screen_greeting(None, again).startswith("Hi Raj,")

    spam = calls.screen_call(m, {"From": "12015550333", "CallUUID": "c4"})
    calls.screened(m, spam, calls.parse('{"say": "Bye", "action": "end", "priority": "scam", "caller": "IRS"}'))
    calls.screened(m, spam, calls.parse('{"say": "Bye", "action": "end", "priority": "low", "caller": "Solar Co"}'))
    assert contacts.name_for(m, "2015550333") == ("", "")  # scams and sales aren't saved
