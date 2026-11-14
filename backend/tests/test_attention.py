"""Attention: topics, categories, starting levels, learning from thumbs, protected kinds, overrides, the inbox."""

import json
from datetime import datetime, timedelta, timezone

import pytest

from buddy import attention as at
from buddy.connectors.hub import ConnectorHub
from buddy.memory import Memory

NOW = datetime(2026, 11, 14, 15, 0, tzinfo=timezone.utc)


@pytest.fixture
def memory(tmp_path):
    m = Memory(tmp_path / "buddy.db")
    ConnectorHub(m)
    return m


def send(memory, text, topic="", kind="money", thumbs=None, when=NOW):
    p = at.plan(memory, kind, text, topic=topic, now=when)
    if thumbs is not None:
        at.rate(memory, p["id"], thumbs)
    return p


def test_topics_categories_and_starting_levels(memory):
    assert send(memory, "🏦 New fund filings")["topic"] == "investing:funds"
    assert send(memory, "⚡ NVDA $140")["level"] == "alert" and send(memory, "📈 NVDA: buy")["level"] == "notify"
    funds = send(memory, "🏦 New fund filings")
    assert (funds["category"], funds["level"]) == ("Investing", "quiet")
    deal = send(memory, "🔔 The deal you were watching for is here")
    assert (deal["topic"], deal["category"], deal["level"]) == ("deals:watched", "Deals", "alert")
    assert send(memory, "Take your pills", kind="reminder")["category"] == "Reminders"
    assert at.plan(memory, "surprise", "z")["level"] == "alert" and at.category_of("surprise") == "Other"
    assert at.topic_of("money", "deals:daily", "📈 x") == "deals:daily"  # the caller's topic wins


def test_one_thumb_changes_nothing_and_three_downs_quieten(memory):
    send(memory, "📈 A: buy", thumbs=False)
    assert at.level(memory, "investing:signals")["level"] == "notify"       # one tap never silences a kind
    send(memory, "📈 B: buy", thumbs=False)
    last = send(memory, "📈 C: buy")
    got = at.rate(memory, last["id"], False)
    assert got["level"] == "quiet" and "3 of 3 thumbs down" in got["why"]
    assert send(memory, "📈 D: buy")["level"] == "quiet"                   # the next one goes out quieter
    for i in range(3):
        send(memory, f"📈 E{i}: buy", thumbs=False)
    assert at.level(memory, "investing:signals")["level"] == "muted"        # nearly all down, six ratings: noise
    at.rate(memory, last["id"], None)                                       # taking a thumb back counts
    assert at.ratings(memory, "investing:signals") == (0, 5)


def test_thumbs_up_make_a_kind_louder_and_mixed_leaves_it(memory):
    for i in range(3):
        send(memory, f"🏦 filing {i}", thumbs=True)
    assert at.level(memory, "investing:funds")["level"] == "notify"         # quiet -> notify
    for up in (True, False, True, False):
        send(memory, "daily picks", "deals:daily", thumbs=up)
    assert at.level(memory, "deals:daily") == {"level": "notify", "why": "the starting level"}
    for i in range(4):
        send(memory, f"⚡ X{i}", thumbs=True)
    assert at.level(memory, "investing:live")["level"] == "alert"           # already the loudest


def test_reminders_are_never_silenced_and_old_thumbs_expire(memory):
    for i in range(8):
        send(memory, f"reminder {i}", kind="reminder", thumbs=False)
    got = at.level(memory, "reminder")
    assert got["level"] == "notify" and "never fully silenced" in got["why"]
    assert "quietest this kind can go" in at.set_level(memory, "reminder", "muted")
    assert at.level(memory, "reminder")["level"] == "notify"
    for i in range(4):
        send(memory, f"old {i}", "deals:daily", thumbs=False, when=NOW - timedelta(days=120))
    assert at.level(memory, "deals:daily", NOW)["level"] == "notify"        # four-month-old thumbs don't count


def test_the_owner_can_set_a_level_by_name_and_go_back_to_learning(memory):
    assert at.set_level(memory, "fund filings", "alert") == "Fund filings are now alert."
    assert at.level(memory, "investing:funds") == {"level": "alert", "why": "you set it"}
    for i in range(4):
        send(memory, f"🏦 {i}", thumbs=False)
    assert at.level(memory, "investing:funds")["level"] == "alert"          # a set level wins over thumbs
    assert "follow your thumbs again (quiet)" not in at.set_level(memory, "investing:funds", "learn")
    assert at.level(memory, "investing:funds")["level"] == "muted"          # quiet, one quieter from 4 downs
    with pytest.raises(Exception, match="Which kind of message"):
        at.set_level(memory, "everything", "quiet")
    with pytest.raises(Exception, match="Levels"):
        at.set_level(memory, "fund filings", "loud")


def test_the_inbox_groups_by_category_newest_first(memory):
    send(memory, "🏦 old filing", when=NOW - timedelta(days=3))
    send(memory, "Take your pills", kind="reminder", when=NOW - timedelta(days=1))
    p = at.plan(memory, "money", "⚡ NVDA", display="**⚡ NVDA**\n- broke out", now=NOW)
    at.rate(memory, p["id"], True)
    box = at.inbox(memory, now=NOW)
    assert [g["category"] for g in box["groups"]] == ["Investing", "Reminders"]
    first = box["groups"][0]["alerts"][0]
    assert first["text"].startswith("**⚡ NVDA**") and first["rating"] == 1 and first["name"] == "live price triggers"
    assert box["groups"][0]["count"] == 2 and box["levels"][0] == "alert"
    shown = json.loads(at.run_tool(memory, {"action": "show"}))
    assert any(k["topic"] == "investing:live" and k["thumbs_up"] == 1 for k in shown["kinds"])
    with pytest.raises(Exception, match="too old"):
        at.rate(memory, "nope", True)
