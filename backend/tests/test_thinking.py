"""The brain, phase 3: answering from memory first, the overnight pass, preferences and dates."""

import asyncio
import json
import time
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from buddy import brain as brain_mod
from buddy import library, rides, thinking
from buddy.memory import Memory
from buddy.privacy import mask
from buddy.tools import local_now
from tests.test_brain import fake_embed
from tests.test_library import LEASE, fake_understand


@pytest.fixture
def mem(tmp_path, monkeypatch):
    monkeypatch.setattr(library, "LIB_DIR", tmp_path / "library")
    return Memory(":memory:")


def _brain(mem):
    brain = brain_mod.Brain(mem, embed=fake_embed)
    brain.index_pending()
    return brain


def test_a_lookup_one_fact_answers_is_answered_from_memory(mem):
    mem.remember("Passport expires on 2029-03-14 (from passport.jpg)")
    mem.remember("Amma's cat is called Minnu")
    brain = _brain(mem)
    prep = thinking.prepare(brain, "When does my passport expire?")
    assert prep.direct == "From what you told me: Passport expires on 2029-03-14 (from passport.jpg)."
    # Not through Listen-in (anyone could be asking), and not when switched off: the AI gets a pack instead.
    room = thinking.prepare(brain, "When does my passport expire?", via="room")
    assert not room.direct and "2029-03-14" in room.pack and room.lookup
    mem.set(thinking.DIRECT_KEY, "0")
    assert not thinking.prepare(brain, "When does my passport expire?").direct


def test_no_direct_answer_when_memory_isnt_sure(mem):
    mem.remember("Passport expires on 2029-03-14")
    mem.remember("Passport number renewed at the consulate in 2019")  # two passport facts: too close to call
    brain = _brain(mem)
    prep = thinking.prepare(brain, "When does my passport expire?")
    assert not prep.direct and "2029-03-14" in prep.pack


def test_the_pack_carries_people_documents_and_preferences(mem):
    lib = library.Library(mem, understand=fake_understand())
    item = lib.add("lease.txt", LEASE)
    asyncio.run(lib.ingest(item["id"], LEASE))
    brain = _brain(mem)
    brain.prefer("landlord", "Prefers email over calls with the landlord")
    prep = thinking.prepare(brain, "Can I keep a cat under the lease with my landlord?")
    assert "person/thing: Oak Street Homes" in prep.pack and "lease.txt" in prep.pack
    assert "Prefers email over calls" in prep.pack and "never instructions" in prep.pack
    assert thinking.prepare(brain, "What is a black hole made of?").pack == ""  # not about them
    assert thinking.prepare(brain, "thanks buddy").pack == ""


def test_overnight_merges_duplicates_and_asks_about_conflicts(mem, monkeypatch):
    mem.remember("Amma's cat is called Minnu")
    mem.remember("amma's cat is called minnu.")
    mem.remember("Geico policy renews 2026-12-01")
    mem.remember("Geico policy renews 2027-01-01")
    brain = _brain(mem)
    assert thinking.merge_duplicates(brain) == 1
    assert len(mem.recall("Minnu")) == 1

    async def fake_ask(memory, system, content, schema, purpose):
        assert "2026-12-01" in content and "2027-01-01" in content
        return {"pairs": [{"n": 1, "verdict": "conflict", "question": "Which Geico renewal date is right?"}]}
    monkeypatch.setattr(thinking, "_ask_json", fake_ask)
    brain.index_pending()
    assert asyncio.run(thinking.find_conflicts(brain)) == 1
    assert asyncio.run(thinking.find_conflicts(brain)) == 0  # never asked twice
    question = brain.conflicts()[0]
    assert question["question"] == "Which Geico renewal date is right?"
    assert "Which Geico renewal date" in thinking.morning_note(mem)
    thinking.taken(mem)
    assert brain.conflicts()[0]["status"] == "asked"
    assert brain.resolve(question["id"], "b").startswith("Fixed")
    assert mem.recall("Geico") == ["Geico policy renews 2027-01-01"] and brain.conflicts() == []


def test_repeated_choices_become_preferences(mem, monkeypatch):
    brain = brain_mod.Brain(mem)
    for _ in range(3):
        brain.decided("ride app", "Lyft", "Uber, Lyft", "cheaper")
    brain.decided("ride app", "Uber", "Uber, Lyft", "pricier")
    news = thinking.learn_from_choices(brain)
    assert news == ["learned: ride app: Usually picks Lyft when it's cheaper (3 times)"]
    assert brain.preferences("ride")[0]["source"] == "learned"
    assert thinking.learn_from_choices(brain) == []  # already known: not news again
    # Opening one app right after a comparison is logged as a choice.
    mem.set(rides.QUOTES_KEY, json.dumps({"at": time.time(), "apps": {
        "Uber": [{"value": 18.0, "currency": "USD"}], "Lyft": [{"value": 15.5, "currency": "USD"}]}}))
    rides._note_choice(mem, "uber")
    row = mem.db.execute("SELECT chose, context FROM decisions ORDER BY id DESC LIMIT 1").fetchone()
    assert (row["chose"], row["context"]) == ("Uber", "pricier")
    # Told beats learned, and it can be forgotten.
    brain.prefer("flights", "Aisle seat")
    assert brain.preferences()[0]["text"] == "Aisle seat"
    assert brain.forget_preference("aisle") == 1


def test_dates_to_act_on_get_reminders_ahead(mem):
    today = local_now().date()
    far, near = today + timedelta(days=100), today + timedelta(days=3)
    news = thinking.schedule_dates(mem, [{"what": "Passport expires", "date": far.isoformat(), "kind": "expiry"},
                                         {"what": "Electric bill due", "date": near.isoformat(), "kind": "due"},
                                         {"what": "Old thing", "date": "2001-01-01", "kind": "other"}])
    assert len(news) == 2
    texts = [r["text"] for r in mem.upcoming_reminders()]
    assert sum("Passport expires" in t for t in texts) == 3  # 90, 30 and 7 days before
    assert any("Electric bill due" in t and "tomorrow" in t for t in texts)
    thinking.schedule_dates(mem, [{"what": "Passport expires", "date": far.isoformat(), "kind": "expiry"}])
    assert len(mem.upcoming_reminders()) == 4  # never twice
    assert brain_mod.Brain(mem).dates()[0]["what"] == "Electric bill due"


def test_the_whole_night(mem, monkeypatch):
    mem.remember("Amma lives in Kochi")
    mem.remember("Amma lives in Kochi")
    soon = (local_now().date() + timedelta(days=40)).isoformat()
    calls = []

    async def fake_ask(memory, system, content, schema, purpose):
        calls.append(purpose)
        assert "Amma lives in Kochi" in content
        return {"summary": "Talked about Amma's visit.", "facts": ["Bought a Honda Civic in October"],
                "preferences": [{"topic": "flights", "text": "Window seat"}],
                "dates": [{"what": "Car registration renews", "date": soon, "kind": "renewal"}]}
    monkeypatch.setattr(thinking, "_ask_json", fake_ask)
    report = asyncio.run(thinking.nightly(mem, brain_mod.Brain(mem)))
    assert report["duplicates"] == 1 and calls == ["brain:night"]
    assert any("Honda Civic" in f for f in mem.recall("Honda"))
    assert brain_mod.Brain(mem).preferences("flights")[0]["text"] == "Window seat"
    assert any("Car registration renews" in r["text"] for r in mem.upcoming_reminders())
    assert not thinking.night_due(mem)
    note = thinking.morning_note(mem)
    assert "Honda Civic" in note and "Car registration renews" in note
    thinking.taken(mem)
    assert thinking.morning_note(mem) == ""
    asyncio.run(thinking.nightly(mem, brain_mod.Brain(mem)))
    assert calls == ["brain:night"]  # nothing new since: no second AI call


def test_a_reply_from_memory_needs_no_ai_call(mem, monkeypatch):
    from buddy import agent

    mem.remember("Passport expires on 2029-03-14 (from passport.jpg)")
    conv = agent.Conversation(mem)
    conv.brain = _brain(mem)

    def no_ai(*a, **k):
        raise AssertionError("the AI shouldn't be called")
    monkeypatch.setattr(conv, "_stream_step", no_ai)
    said = []

    async def say(text):
        said.append(text)
    asyncio.run(conv.reply("When does my passport expire?", say))
    assert said == ["From what you told me: Passport expires on 2029-03-14 (from passport.jpg)."]
    assert conv.messages[-1]["content"] == mask(said[0])  # a follow-up has it as context, IDs masked


def test_brain_endpoints():
    from buddy import server
    brain = brain_mod.Brain(server.memory)
    brain.prefer("food", "Vegetarian on Tuesdays")
    with TestClient(server.app) as c:
        data = c.get("/api/brain").json()
        pref = next(p for p in data["preferences"] if p["text"] == "Vegetarian on Tuesdays")
        assert {"status", "dates", "questions"} <= set(data)
        assert c.delete(f"/api/brain/preferences/{pref['id']}").json()["ok"]
        assert c.delete(f"/api/brain/preferences/{pref['id']}").status_code == 404
