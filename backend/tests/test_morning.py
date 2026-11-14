"""The morning lessons (investing, English) and the briefing's news, events and learning from what is said."""

import asyncio
import json
from datetime import date, datetime, timedelta, timezone

import pytest

from buddy import daybrief, morning_tutor as mt, pricebook, simbook, tutor
from buddy.connectors.hub import ConnectorHub
from buddy.memory import Memory
from buddy.trends import Trends

DAY = date(2026, 11, 17)  # a Tuesday
NOW = datetime(2026, 11, 17, 13, 0, tzinfo=timezone.utc)


@pytest.fixture
def memory(tmp_path, monkeypatch):
    for k in ("BUDDY_FINNHUB_KEY", "BUDDY_TICKETMASTER_KEY", "BUDDY_FREE_MODELS"):
        monkeypatch.delenv(k, raising=False)
    m = Memory(tmp_path / "buddy.db")
    ConnectorHub(m)
    m.set("trends:defaults_set", "1")
    yield m
    pricebook.close_all()


MARKET = {"indexes": {"S&P 500": {"day": 0.8, "week": 1.5, "month": 3.0, "ytd": 12.0}}, "sectors_best": [("technology", 1.9)],
          "sectors_worst": [("energy", -0.7)], "commodities": {}, "bonds": {}, "macro": {}, "themes": [],
          "concept": {"key": "diversify", "title": "Diversification", "text": "Spread money over many things."}}


def say(memory, text, days_ago=0, via="typed"):
    memory.db.execute(tutor.SCHEMA)
    memory.db.execute("INSERT INTO utterances (at, text, via) VALUES (?, ?, ?)",
                      ((NOW - timedelta(days=days_ago)).isoformat(), text, via))
    memory.db.commit()


# --- the investing lesson -------------------------------------------------------------------------------------
def test_the_investing_lesson_from_numbers_alone_has_every_section_and_the_arithmetic(memory):
    simbook.open_from(memory, [{"key": "NVDA", "category": "stocks", "action": "buy", "price": 100.0,
                                "facts": {"parts": {"tone": 1, "trend": 1}}}], DAY - timedelta(days=1), 500.0)
    f = mt.invest_facts(memory, DAY, lambda m: MARKET)
    opened = f["buddy"]["paper_trades_opened"][0]
    assert opened["symbol"] == "NVDA" and opened["money_at_risk"] == 1000.0 and f["principle"]["question"].endswith("?")
    lesson = mt.invest_plain(f)
    for section in ("Today's markets", "The idea of the day", "What Buddy did and how", "A lesson from the masters",
                    "Check yourself"):
        assert f"**{section}**" in lesson
    assert "each share risks $7.00" in lesson and "143 shares" in lesson and "Diversification" in lesson
    quiet = mt.invest_plain(mt.invest_facts(memory, DAY + timedelta(days=9), lambda m: MARKET))
    assert "Not trading is a decision too" in quiet


def test_the_lesson_is_delivered_spoken_and_shown_kept_in_reports_and_marks_the_idea_taught(memory):
    async def writer(memory, f):
        return "**Today's markets**\\nStocks rose.\\n**Check yourself**\\n- Q: Why?\\n- A: Because.", "a test"

    text = asyncio.run(mt.investing(memory, DAY, lambda m: MARKET, writer))
    spoken, shown = text.split(" || ")
    assert spoken.startswith("🎓 Your investing lesson is ready") and "Tap Listen" in spoken
    assert shown.startswith("**🎓 Investing lesson · Tuesday**") and "Education, not advice" in shown
    row = memory.db.execute("SELECT title, report FROM jobs WHERE kind = 'market_school'").fetchone()
    assert row["title"] == "Investing lesson, Nov 17, 2026" and "Stocks rose." in row["report"]
    assert "diversify" in json.loads(memory.get("school:taught"))


def test_a_models_lesson_is_used_only_when_whole_else_the_numbers_teach(memory):
    assert not mt._whole("**Today's markets** short")
    text = asyncio.run(mt.investing(memory, DAY, lambda m: MARKET))      # no free model, no Claude key in tests
    assert "written by Buddy's numbers (no model was available)" in text and "**What Buddy did and how**" in text


# --- the English lesson ---------------------------------------------------------------------------------------
def test_the_english_lesson_uses_the_owners_sentences_and_only_claude_reads_them(memory):
    for i in range(6):
        say(memory, f"I want that you check the price number {i}", days_ago=1)
    got = {}

    async def writer(memory, said, unclear):
        got["said"] = said
        return {"corrections": [{"you_said": "I want that you check", "better": "I want you to check",
                                 "why": "After want, use the person then 'to'."}],
                "grammar": {"title": "Want + person + to", "rule": "Say want you to.", "examples": ["I want you to go."]},
                "pronunciation": [{"word": "comfortable", "say_it": "COM-fuh-tuh-bul", "tip": "Three beats."}],
                "upgrade": {"instead_of": "do the needful", "say": "take care of it", "why": "More common."},
                "practice": "I want you to feel comfortable."}

    text = asyncio.run(mt.english(memory, DAY, writer))
    spoken, shown = text.split(" || ")
    assert len(got["said"]) == 6 and "1 fixes from what you said" in spoken
    assert "Better: **I want you to check**" in shown and "**comfortable**: COM-fuh-tuh-bul" in shown
    assert "only Claude read them" in shown and "Say this to me" in shown
    assert memory.db.execute("SELECT COUNT(*) AS n FROM jobs WHERE kind = 'tutor'").fetchone()["n"] == 1


def test_too_few_sentences_gives_a_general_tip_without_any_model(memory):
    say(memory, "hello")

    async def never(memory, said, unclear):
        raise AssertionError("no model should be asked")

    text = asyncio.run(mt.english(memory, DAY, never))
    assert "A general tip today: there weren't enough of your sentences" in text and "**Grammar: " in text


def test_switches_times_and_the_morning_schedule(memory):
    at8, at7 = datetime(2026, 11, 17, 8, 5), datetime(2026, 11, 17, 7, 5)
    assert mt.due(memory, "investing", at8) and not mt.due(memory, "english", at7)
    asyncio.run(mt.run_tool(memory, {"action": "time", "which": "english", "hour": 7}))
    assert mt.due(memory, "english", at7)
    mt.mark(memory, "english", at7)
    assert not mt.due(memory, "english", at8) and mt.due(memory, "english", at8 + timedelta(days=1))
    assert "are off" in asyncio.run(mt.run_tool(memory, {"action": "off"}))
    assert not mt.due(memory, "investing", at8)
    status = json.loads(asyncio.run(mt.run_tool(memory, {"action": "status"})))
    assert status["english"] == {"on": False, "at": "7:00", "last": "2026-11-17"}


# --- the briefing: news, finance, events, and learning from what is said --------------------------------------
def test_the_briefing_gets_news_finance_and_only_new_events(memory, monkeypatch):
    Trends(memory).watch("AI agents")
    memory.set("trends:brief:AI agents", json.dumps({
        "topic": "AI agents", "at": datetime.now(timezone.utc).isoformat(), "headline": "Agents got cheaper",
        "stories": [{"title": "A new agent framework", "url": "https://example.com/a"}]}))
    from buddy import events, signals
    signals._ready(memory)
    pricebook.run(memory, "INSERT INTO signals (id, day, source, title, url, place) VALUES ('f1', ?, 'finnhub', "
                          "'Fed holds rates', 'https://example.com/fed', 1)", (date.today().isoformat(),))
    monkeypatch.setattr(events, "city", lambda memory: "New York")
    shows = [{"name": "AI Summit", "date": "2026-12-01", "time": "09:00", "venue": "Javits", "price": "", "url":
              "https://example.com/e"}]
    monkeypatch.setattr(events, "search_events", lambda memory, what, where, days, env=None, fetch=None: {"events": shows})
    got = daybrief.facts(memory)
    assert "AI agents: Agents got cheaper" in got["say"] and "Fed holds rates" in got["say"] and "AI Summit" in got["say"]
    assert "**News on your interests**" in got["shown"] and "https://example.com/fed" in got["shown"]
    assert "**New events near you**" in got["shown"]
    assert daybrief.new_events(memory, today="2030-01-02") == []        # the same event again isn't new


def test_the_briefing_learns_what_the_owner_keeps_bringing_up(memory):
    for d in (1, 3, 5):
        say(memory, "any cricket news today, did we win", days_ago=d)
        say(memory, "what is NVDA doing", days_ago=d)
    say(memory, "tell me about tennis", days_ago=2)                       # one day only: not enough
    say(memory, "they were talking about bitcoin all night", days_ago=1, via="room")   # other people: not counted
    seen = daybrief.notice(memory, NOW)
    assert seen["subjects"]["Cricket"] == 3 and seen["tickers"] == {"NVDA": 3} and "Crypto" not in seen["subjects"]
    changed = daybrief.learn(memory, NOW)
    assert len(changed) == 1 and changed[0].startswith("I've added Cricket to your news because you brought it up on 3 days")
    assert "Cricket" in {t["name"] for t in Trends(memory).topics()} and daybrief.focus(memory)["tickers"] == ["NVDA"]
    assert daybrief.learn(memory, NOW) == []                              # once a day
    assert daybrief.learn(memory, NOW + timedelta(days=1)) == []          # nothing more: one new subject a week at most
    later = NOW + timedelta(days=40)                                      # cricket not mentioned for over a month
    dropped = daybrief.learn(memory, later)
    assert dropped == ["I've stopped following Cricket in your news: you haven't brought it up for a month"]
    assert "Cricket" not in {t["name"] for t in Trends(memory).topics()}


def test_a_subject_the_owner_added_themselves_is_never_dropped(memory):
    Trends(memory).watch("Cricket")
    assert daybrief.learn(memory, NOW + timedelta(days=90)) == []
    assert "Cricket" in {t["name"] for t in Trends(memory).topics()}
