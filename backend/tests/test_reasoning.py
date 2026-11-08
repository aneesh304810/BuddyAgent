"""How Buddy thinks: sources, plan, fact-check, decisions and outcomes, growing knowledge, new sources."""

import asyncio
from datetime import datetime, timedelta, timezone

from buddy import reasoning
from buddy.brain import Brain
from buddy.memory import Memory
from buddy.reasoning import Reasoning


def brain():
    return Brain(Memory(":memory:"), embed=lambda texts: None)


def test_where_to_look_and_the_plan():
    r = Reasoning(Memory(":memory:"))
    ids = [s["id"] for s in r.pick("should I pay my Chase bill from checking or savings before my trip?")]
    assert {"bills", "bank"} <= set(ids)
    note = r.plan_note("should I pay my Chase bill from checking or savings before my trip?")
    assert "Look in:" in note and "ONE recommendation" in note and "Check before you say it" in note
    assert r.plan_note("hi there") == ""


def test_architecture_questions_prefer_the_live_code():
    r = Reasoning(Memory(":memory:"))
    note = r.plan_note("explain buddy architecture in detail")
    assert "buddy_itself" in note and "Uploaded documents can be out of date" in note


def test_no_source_yet_is_a_gap_and_a_suggestion():
    r = Reasoning(Memory(":memory:"))
    note = r.plan_note("will it rain in Lima tomorrow?")
    assert "No source connected for weather" in note and "Open-Meteo" in note
    r.plan_note("weather this weekend?")
    idea = asyncio.run(r.weekly_suggestion(ask=lambda m, e: None))
    assert "weather (2 times): Open-Meteo" in idea
    assert asyncio.run(r.weekly_suggestion()) == ""  # once a week


def test_fact_check_finds_a_wrong_number():
    r = Reasoning(Memory(":memory:"))
    evidence = '{"bills": [{"biller": "Chase", "amount": 412.0, "due_date": "2026-11-12"}]}'
    assert r.unsupported_numbers("Your Chase bill is $412 due Nov 12.", evidence) == []
    assert r.unsupported_numbers("Your Chase bill is $421 due Nov 12.", evidence) == ["$421"]

    async def ask(memory, q, a, ev, doubtful):
        return {"issues": [{"claim": "$421", "correct": "$412", "source": "bills"}]}
    fix = asyncio.run(r.verify("chase bill?", "Your Chase bill is $421.", evidence, ask=ask))
    assert fix == "Fact check: $421 should be $412 (bills)."
    assert asyncio.run(r.verify("chase bill?", "Your Chase bill is $412.", evidence, ask=ask)) == ""


def test_decisions_are_followed_up_and_remembered():
    m = Memory(":memory:")
    r = Reasoning(m)
    did = r.log_decision("Should I book the refundable fare to Lima or the cheaper one?",
                         "Book the refundable fare: it's $60 more and your plans may change.")
    m.db.execute("UPDATE reasoning_decisions SET at = ?", ((datetime.now(timezone.utc) - timedelta(days=4)).isoformat(),))
    ups = r.follow_ups()
    assert ups and "How did it turn out?" in ups[0]["text"]
    r.asked(ups[0]["id"])
    assert r.follow_ups() == []  # one a day
    assert "decision_outcome with id" in r.awaiting_note()
    r.outcome(did, "The flight was cancelled and I got a full refund in 5 days.")
    past = r.related_decisions("Should I book a refundable fare to Cusco?")
    assert past and "full refund" in past[0]


def test_knowledge_grows_from_what_buddy_finds():
    b = brain()

    async def ask(memory, q, a, ev):
        return {"facts": [{"title": "Hilton Miraflores booking", "text": "Nov 10-14, confirmation H778899.",
                           "source": "Hilton email", "lasting": True},
                          {"title": "LIM taxi price", "text": "About 60 soles to Miraflores.", "source": "web",
                           "lasting": False}]}
    ids = asyncio.run(reasoning.learn(b, "where am I staying in Lima?", "You're at the Hilton Miraflores " * 4,
                                      "x" * 300, ["search_my_apps"], ask=ask))
    assert len(ids) == 2
    hits = b.recall("hilton miraflores booking")
    assert hits and hits[0]["source"] == "note" and "H778899" in hits[0]["text"]
    expiring = [n for n in b.notes() if n["title"] == "LIM taxi price"][0]
    assert expiring["expires"] is not None
    assert asyncio.run(reasoning.learn(b, "hi", "hello", "", [], ask=ask)) == []  # nothing looked up: nothing kept


def test_self_knowledge_from_the_code_is_rebuilt_only_on_change():
    b = brain()
    assert reasoning.refresh_self_knowledge(b) > 50
    assert reasoning.refresh_self_knowledge(b) == 0
    overview = [n for n in b.notes("self", 500) if n["title"].startswith("Buddy's architecture")]
    assert overview and "transit.py" in " ".join(n["text"] for n in overview)
