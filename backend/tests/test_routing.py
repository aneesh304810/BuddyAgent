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


def test_simple_general_requests_go_to_a_free_model_and_hard_or_personal_ones_to_a_paid_one():
    mem = Memory(":memory:")
    fk = lambda t: routing.free_kind(t, mem)[0]   # noqa: E731
    assert fk("thanks buddy") == "small_talk"
    assert fk("what is a black hole") == "explain"
    assert fk("write a short poem about the monsoon") == "brainstorm"
    assert fk("give me some names for a golden retriever puppy") == "brainstorm"
    assert fk("how do I reverse a list in python") == "code"
    assert fk("who wrote the Odyssey") == "explain"
    assert fk("how far is the moon from earth") == "quick_answer"
    # personal, needs Buddy's tools, high stakes, current or hard: a paid model
    for text in ("how much did I spend on food", "remind me to call mum", "should I buy NVDA calls",
                 "what's the weather today", "what is my credit score",
                 "compare the trade-offs of Kafka and Pulsar step by step for a bank's event platform and explain why"):
        assert fk(text) == "", text
    Customize(mem).set("routing", "best")
    assert fk("what is a black hole") == "" and fk("thanks") == "small_talk"


def test_a_kind_the_free_models_keep_missing_goes_to_claude():
    mem = Memory(":memory:")
    for i in range(6):
        routing.log_free(mem, f"f{i}", "code", "gemini/x", handed_back=i < 3)
    for i in range(3):
        routing.log_free(mem, f"ok{i}", "code", "gemini/x")
    kind, why = routing.free_kind("how do I reverse a list in python", mem)
    assert kind == "" and "getting code wrong lately" in why
    assert routing.overview(mem)["kept_off_free"] == ["free:code"]


def test_images_decks_and_documents_on_general_topics_are_made_with_free_models():
    mem = Memory(":memory:")
    fm = lambda t: routing.free_make(t, mem)   # noqa: E731
    assert fm("Make a presentation about the history of the Roman Empire") == ("pptx", "the history of the Roman Empire")
    assert fm("create a 10-slide deck on how transformers work") == ("pptx", "how transformers work")
    assert fm("draw an image of a lighthouse at sunset") == ("image", "a lighthouse at sunset")
    assert fm("make a word document explaining photosynthesis")[0] == "docx"
    # about the user, about now, or high stakes: Claude makes it
    assert fm("make a presentation about my spending this year") == ("", "")
    assert fm("make slides about today's market news") == ("", "")
    assert fm("make a deck on whether I should invest in NVDA") == ("", "")
