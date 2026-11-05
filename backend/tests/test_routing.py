"""Smart model routing: task, difficulty and mode decide; decisions are logged; weak tasks move up."""

from datetime import datetime, timezone

from buddy import routing
from buddy.config import settings
from buddy.customize import Customize
from buddy.memory import Memory
from buddy.router import Router


def test_tasks_go_to_the_model_that_does_them_well():
    r = Router()
    d = r.decide("remind me at 6 to call mum")
    assert (d.task, d.tier) == ("reminder", "fast")
    d = Router().decide("how much did I spend on dining last month")
    assert (d.task, d.tier) == ("money", "main")
    d = Router().decide("thanks")
    assert (d.task, d.model) == ("closer", settings.fast_model)


def test_hard_high_stakes_questions_get_the_expert_hint():
    d = Router().decide("Should I sell my NVDA calls before earnings or roll them? Compare the trade-offs step by step.")
    assert d.task == "investing" and d.tier == "main" and d.expert_hint
    assert not Router().decide("what's my credit score").expert_hint


def test_modes_change_the_routing():
    mem = Memory(":memory:")
    assert "Saver mode" in Customize(mem).set("routing", "saver")
    d = Router().decide("check my email", mem)
    assert d.tier == "fast" and "saver mode" in d.reason
    Customize(mem).set("routing", "best")
    assert Router().decide("hello there my friend how was your day", mem).tier == "main"


def test_decisions_are_logged_and_a_task_the_quick_model_keeps_missing_moves_up():
    mem = Memory(":memory:")
    now = datetime.now(timezone.utc).isoformat()
    for i in range(8):
        d = routing.Decision(settings.fast_model, "fast", "reminder", "reminder: the quick model")
        routing.log(mem, f"t{i}", d)
        mem.db.execute("INSERT INTO turns (turn, at, via, first_model, final_model, total_seconds, rounds, tools, sentences, "
                       "filler, outcome) VALUES (?, ?, 'text', ?, ?, 1.0, 1, '', 1, 0, 'ok')",
                       (f"t{i}", now, settings.fast_model, settings.chat_model if i < 4 else settings.fast_model))
    assert routing.note_correction(mem, "t7", "No, that's wrong")
    mem.db.commit()
    d = Router().decide("add eggs to my shopping list", mem)
    assert d.tier == "main" and "getting reminder wrong lately" in d.reason
    view = routing.overview(mem)
    row = next(t for t in view["tasks"] if t["task"] == "reminder")
    assert row["fast"] == 8 and row["missed_on_fast"] == 5 and view["moved_up"] == ["reminder"]
