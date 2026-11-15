"""Life memory from email: what is read, what is kept, the circle of people, reminders and recall."""

import asyncio
import json
from datetime import datetime, timedelta, timezone

import pytest

from buddy import lifemail
from buddy.connectors.hub import ConnectorHub
from buddy.memory import Memory
from buddy.tools import ToolError, local_now


@pytest.fixture
def memory(tmp_path):
    m = Memory(tmp_path / "buddy.db")
    ConnectorHub(m)
    return m


def mail(memory, n, sender, subject, body="", days_ago=0):
    when = (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()
    cur = memory.db.execute(
        "INSERT INTO records (source, external_id, kind, title, body, start_at, data, synced_at) VALUES "
        "('email', ?, 'email', ?, ?, ?, ?, ?)", (f"m{n}", subject, body, when, json.dumps({"from": sender}), when))
    memory.db.commit()
    return cur.lastrowid


def day(offset):
    return (local_now().date() + timedelta(days=offset)).isoformat()


def answers(*items):
    async def extract(memory, emails):
        subjects = {e["subject"]: n for n, e in enumerate(emails, 1)}
        return {"items": [{**i, "n": subjects[i["subject"]]} for i in items if i["subject"] in subjects]}
    return extract


def item(subject, category, kind, title, **more):
    return {"subject": subject, "keep": True, "category": category, "kind": kind, "title": title, **more}


def test_only_life_email_is_read_not_marketing_or_low_mail(memory):
    mail(memory, 1, "MyChart <noreply@epic.example>", "You have an upcoming visit", "arrival 9:15")
    mail(memory, 2, "Uber <uber@uber.example>", "Your next find, delivered", "Shop now. Unsubscribe")
    mail(memory, 3, "Alibaba <a@alibaba.example>", "Fear of customs?", "Let suppliers handle duties")
    low = mail(memory, 4, "School <news@school.example>", "Weekly backpack and updates", "Dear families")
    memory.db.executescript(__import__("buddy.mailbrain", fromlist=["x"]).SCHEMA_SQL)
    memory.db.execute("INSERT INTO mail_triage (record_id, account, sender, subject, priority, reason, sorted_at) "
                      "VALUES (?, '', '', '', 'low', '', '')", (low,))
    got = lifemail.candidates(memory)
    assert [e["subject"] for e in got] == ["You have an upcoming visit"]
    assert lifemail.candidates(memory) == []  # read once


def test_mail_from_a_confirmed_person_is_always_read(memory):
    lifemail.teach(memory, "Neha Sareen", "wife")
    memory.db.execute("UPDATE mail_circle SET senders = ?", (json.dumps(["neha@example.com"]),))
    mail(memory, 1, "Neha Sareen <neha@example.com>", "dinner saturday", "can you book the place")
    assert [e["from"] for e in lifemail.candidates(memory)] == ["Neha Sareen"]


def test_an_appointment_is_said_once_and_gets_reminders(memory):
    mail(memory, 1, "MyChart <noreply@epic.example>", "You have an upcoming visit", "with Optum, arrival 9:15")
    told = asyncio.run(lifemail.scan(memory, answers(item(
        "You have an upcoming visit", "health", "appointment", "Visit at Optum", date=day(3), time="09:30",
        org="Optum", remember="Visit at Optum; arrive 15 minutes early."))))
    assert len(told) == 1 and "Visit at Optum" in told[0] and "at 09:30" in told[0] and "two hours ahead" in told[0]
    reminders = [r["text"] for r in memory.upcoming_reminders()]
    assert any(r.startswith("Tomorrow at 09:30: Visit at Optum") for r in reminders)
    assert any(r.startswith("In two hours (09:30)") for r in reminders)
    assert "Visit at Optum" in lifemail.briefing_line(memory)
    mail(memory, 2, "MyChart <noreply@epic.example>", "You have an upcoming visit", "reminder: with Optum")
    again = asyncio.run(lifemail.scan(memory, answers(item(
        "You have an upcoming visit", "health", "appointment", "Visit at Optum", date=day(3), time="09:30"))))
    assert again == [] and len(memory.upcoming_reminders()) == 2  # the same visit isn't kept or announced twice


def test_documents_are_kept_with_their_reference_and_can_be_found(memory):
    mail(memory, 1, "Neha Sareen <neha@example.com>", "Fwd: Your eTA for Kenya - ISHAAN NAIR", "Forwarded message")
    told = asyncio.run(lifemail.scan(memory, answers(item(
        "Fwd: Your eTA for Kenya - ISHAAN NAIR", "travel", "document", "Kenya eTA for Ishaan Nair",
        who="Ishaan Nair", reference="9RW5XGM542", expires=day(80),
        remember="Ishaan's Kenya travel authorisation was approved.",
        people=[{"name": "Neha Sareen", "kind": "person", "guess": "forwards the family's travel documents"},
                {"name": "Ishaan Nair", "kind": "person", "guess": "a traveller on the same trip: likely family"}]))))
    assert told == []  # kept quietly: nothing to do today
    found = lifemail.show(memory, query="kenya ishaan")["entries"][0]
    assert found["reference"] == "9RW5XGM542" and found["who"] == "Ishaan Nair" and found["expires"] == day(80)
    assert any("ends on" in r["text"] and "in 30 days" in r["text"] for r in memory.upcoming_reminders())
    from buddy.brain import Brain
    notes = Brain(memory, embed=lambda t: None).notes("life")
    assert "9RW5XGM542" in notes[0]["text"] and notes[0]["title"] == "Travel: Kenya eTA for Ishaan Nair"


def test_people_are_guessed_asked_once_and_confirmed_only_by_the_owner(memory):
    for n in (1, 2):
        mail(memory, n, "Neha Sareen <neha@example.com>", f"Fwd: eTA {n}", "Forwarded message")
    extract = answers(*[item(f"Fwd: eTA {n}", "travel", "document", f"eTA {n}", people=[
        {"name": "Neha Sareen", "kind": "person", "guess": "forwards the family's travel documents: likely spouse"}])
        for n in (1, 2)])
    told = asyncio.run(lifemail.scan(memory, extract))
    assert len(told) == 1 and "Neha Sareen keeps coming up" in told[0] and "Who are they to you?" in told[0]
    who = lifemail.show(memory)["circle"][0]
    assert who["confirmed"] is False and "not confirmed" in who["relation"]  # a guess, shown as one
    assert lifemail.questions(memory) == []  # asked once
    assert "your wife" in lifemail.teach(memory, "Neha", "wife")
    who = lifemail.show(memory)["circle"][0]
    assert who == {"name": "Neha Sareen", "kind": "person", "relation": "wife", "confirmed": True}
    assert "neha@example.com" in lifemail._known_senders(memory)  # her mail is now always read
    from buddy.brain import Brain
    assert Brain(memory, embed=lambda t: None).person("Neha Sareen")["relation"] == "wife"


def test_a_delivery_is_said_and_marketing_the_model_rejects_is_dropped(memory):
    mail(memory, 1, "Nine on the Hudson <n@building.example>", "You have a delivery at the front desk")
    mail(memory, 2, "Global Tree <s@gt.example>", "FollowUp Regarding Your Immigration Enquiry", "I am Sridhar")
    told = asyncio.run(lifemail.scan(memory, answers(
        item("You have a delivery at the front desk", "home", "notice", "A delivery is waiting at the front desk",
             org="Nine on the Hudson"),
        {"subject": "FollowUp Regarding Your Immigration Enquiry", "keep": False, "category": "other",
         "kind": "update", "title": "sales follow-up"})))
    assert told == ["From your building: A delivery is waiting at the front desk."]
    assert len(lifemail.show(memory)["entries"]) == 1


def test_catch_up_reads_older_mail_without_announcing_it_and_forget_deletes(memory):
    mail(memory, 1, "School <o@school.example>", "Virtual Lunch & Learn - Kindergarten", "Dear parents", days_ago=5)
    lifemail.candidates(memory)  # already passed over once
    extract = answers(item("Virtual Lunch & Learn - Kindergarten", "school", "update", "Lunch & Learn on literacy",
                           date=day(4), org="Stevens Cooperative School", remember="Parents are invited."))
    assert asyncio.run(lifemail.scan(memory, extract, since_days=30)) == []
    assert lifemail.show(memory, category="school")["entries"][0]["org"] == "Stevens Cooperative School"
    assert asyncio.run(lifemail.scan(memory, extract, since_days=30)) == []  # not read twice
    assert len(lifemail.show(memory)["entries"]) == 1
    assert lifemail.forget(memory, "lunch learn") == "Forgot 1 entry."
    with pytest.raises(ToolError):
        lifemail.forget(memory, "")


def test_bad_model_output_is_ignored(memory):
    mail(memory, 1, "MyChart <noreply@epic.example>", "You have an upcoming visit")

    async def extract(memory, emails):
        return {"items": [{"n": 9, "keep": True, "category": "health", "kind": "appointment", "title": "x"},
                          {"n": 1, "keep": True, "category": "secrets", "kind": "appointment", "title": "x"},
                          {"n": 1, "keep": True, "category": "health", "kind": "appointment", "title": "Visit",
                           "date": "soon", "time": "25:99"}]}
    assert asyncio.run(lifemail.scan(memory, extract)) == []  # no real date: kept, not announced, no reminder
    assert len(lifemail.show(memory)["entries"]) == 1 and memory.upcoming_reminders() == []


def test_the_tool_shows_and_teaches(memory):
    out = asyncio.run(lifemail.run_tool(memory, {"action": "teach", "name": "Nine on the Hudson",
                                                 "relation": "building", "kind": "organisation"}))
    assert "Nine on the Hudson is your building" in out
    shown = json.loads(asyncio.run(lifemail.run_tool(memory, {"action": "show"})))
    assert shown["circle"][0]["kind"] == "organisation" and shown["entries"] == []
    assert "won't ask" in asyncio.run(lifemail.run_tool(memory, {"action": "ignore", "name": "nine on"}))
