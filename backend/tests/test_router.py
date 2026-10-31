"""The router: the cheapest Claude model that does each turn well."""

import dataclasses

from buddy import router as rt
from buddy.config import settings


def use(monkeypatch, **changes):
    monkeypatch.setattr(rt, "settings", dataclasses.replace(settings, **changes))


def test_small_talk_and_reminders_go_to_the_quick_model(monkeypatch):
    use(monkeypatch, router_enabled=True, fast_model="haiku", chat_model="sonnet")
    r = rt.Router()
    assert r.pick("hey buddy, how are you") == "haiku"
    assert rt.Router().pick("remind me at 6 to call mum") == "haiku"
    assert rt.Router().pick("thanks") == "haiku"


def test_real_work_goes_to_the_main_model(monkeypatch):
    use(monkeypatch, router_enabled=True, fast_model="haiku", chat_model="sonnet")
    assert rt.Router().pick("check my email for anything urgent") == "sonnet"
    assert rt.Router().pick("find a quiet spa near me") == "sonnet"
    long = "so I was thinking about what we talked about the other day and whether it makes sense to go ahead"
    assert rt.Router().pick(long) == "sonnet"


def test_short_follow_up_keeps_the_main_model(monkeypatch):
    use(monkeypatch, router_enabled=True, fast_model="haiku", chat_model="sonnet")
    r = rt.Router()
    assert r.pick("compare flights to Lima next week") == "sonnet"
    assert r.pick("and the second one?") == "sonnet"


def test_hand_over_switches_to_main(monkeypatch):
    use(monkeypatch, router_enabled=True, fast_model="haiku", chat_model="sonnet")
    r = rt.Router()
    assert r.pick("ok") == "haiku"
    assert r.handed_over() == "sonnet"
    assert r.pick("and?") == "sonnet"  # treated as a follow-up to the main model


def test_router_off_always_uses_main(monkeypatch):
    use(monkeypatch, router_enabled=False, fast_model="haiku", chat_model="sonnet")
    assert rt.Router().pick("hi") == "sonnet"


def test_simple_general_questions_can_go_free():
    assert rt.free_kind("what is a black hole") == "explain"
    assert rt.free_kind("how do you say thank you in portuguese") == "translate"
    for personal_or_current in ("what is my balance", "who is the president", "what is the weather today",
                                "explain my bill", "what is the price of bitcoin", "book a table"):
        assert rt.free_kind(personal_or_current) == ""


def test_small_talk_on_its_own_goes_to_a_free_model():
    from buddy.router import free_kind

    for t in ("Thanks Buddy.", "Good night, bro!", "tell me a joke", "How are you doing today?", "Hello."):
        assert free_kind(t) == "small_talk", t
    for t in ("yes", "ok", "no", "sure", "Thanks, and book it", "Thanks, what about tomorrow?"):
        assert free_kind(t) != "small_talk", t  # answers and requests stay on Claude


def test_closers_use_the_small_model_even_right_after_a_big_question(monkeypatch):
    from dataclasses import replace
    from buddy import router as r

    monkeypatch.setattr(r, "settings", replace(r.settings, router_enabled=True, fast_model="claude-haiku-5-5",
                                               chat_model="claude-sonnet-5-5"))
    picker = r.Router()
    assert picker.pick("Compare flights from Miami to Lima next week") == "claude-sonnet-5-5"
    assert picker.pick("Thank you.") == "claude-haiku-5-5"  # no need for the big model to say thanks back
    assert picker.pick("and the second one?") == "claude-sonnet-5-5"  # a real follow-up keeps the context model
