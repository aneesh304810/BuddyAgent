"""News desk: headlines become stories, sources are rated, and a paid check is only considered for a story that
is unclear and about a company Buddy is acting on."""

import json
from datetime import date

import pytest

from buddy import bip, factors, newsdesk, pricebook, stratlab, themes
from buddy.connectors.hub import ConnectorHub
from buddy.memory import Memory

DAY = date(2026, 11, 16)


@pytest.fixture
def memory(tmp_path):
    m = Memory(tmp_path / "buddy.db")
    ConnectorHub(m)
    yield m
    pricebook.close_all()


def headline(memory, symbol, title, day, outlet, tone="", n=[0]):
    n[0] += 1
    themes._ready(memory)
    bip._run(memory, "INSERT INTO theme_news (key, symbol, company, title, url, day, outlet, seen) VALUES (?, ?, 'Oklo Inc', ?, ?, ?, ?, ?)",
             (f"n{n[0]}", symbol, title, f"https://{outlet}/a{n[0]}", day, outlet, day))
    if tone:
        bip._run(memory, "INSERT INTO theme_tone (key, tone, made) VALUES (?, ?, ?)", (f"n{n[0]}", tone, day))


def test_outlets_are_rated_and_the_same_event_is_one_story(memory):
    assert newsdesk.rating("www.reuters.com") == 3 and newsdesk.rating("https://markets.ft.com/x") == 3
    assert newsdesk.rating("prnewswire.com") == 2 and newsdesk.rating("some-stock-blog.example") == 1
    headline(memory, "OKLO", "Oklo wins Air Force contract for Alaska microreactor", "2026-11-10", "reuters.com", "good")
    headline(memory, "OKLO", "Oklo Inc awarded Air Force microreactor contract in Alaska", "2026-11-11", "stocktips.example", "good")
    headline(memory, "OKLO", "Air Force contract for Alaska microreactor goes to Oklo", "2026-11-11", "prnewswire.com", "good")
    headline(memory, "OKLO", "Oklo delays first reactor after regulator questions", "2026-11-14", "hotstocks.example", "bad")
    headline(memory, "OKLO", "Oklo chief speaks at energy conference", "2026-11-15", "cnbc.com")
    got = newsdesk.stories(memory, "oklo", today=DAY)
    assert [s["reports"] for s in got] == [1, 1, 3]          # five headlines, three stories
    contract, delay, talk = got[2], got[1], got[0]
    assert contract["rating"] == 3 and contract["tone"] == "good" and not contract["unclear"]
    assert delay["rating"] == 1 and delay["why_unclear"] == ["only outlets Buddy has not rated carry it"]
    assert talk["why_unclear"] == ["it has no good-or-bad label yet"]
    assert newsdesk.counts(memory, "OKLO", today=DAY) == {"good": 1, "bad": 1, "stories": 3}  # five headlines count as three
    mixed = newsdesk.group([{"title": "Oklo reactor plan approved", "day": "2026-11-10", "outlet": "reuters.com", "tone": "good", "url": ""},
                            {"title": "Oklo reactor plan approved with conditions", "day": "2026-11-10", "outlet": "ft.com", "tone": "bad", "url": ""}])
    assert mixed[0]["tone"] == "mixed" and "disagree" in mixed[0]["why_unclear"][0]


UNSURE = lambda m, company, story: {"clear": False, "tone": "neutral", "reason": "could be a rumour"}  # noqa: E731


def lab_trade(memory):
    stratlab._ready(memory)
    pricebook.run(memory, "INSERT INTO lab_trades (strategy, symbol, opened, entry, stop, target, shares, hold_days, why, features, "
                          "spy_entry) VALUES ('trend', 'OKLO', '2026-11-12', 100, 93, 114, 10, 28, 'x', '{}', 400)")


def test_only_unclear_stories_on_companies_buddy_acts_on_are_candidates_and_the_limit_holds(memory):
    headline(memory, "OKLO", "Oklo delays first reactor after regulator questions", "2026-11-14", "hotstocks.example", "bad")
    headline(memory, "OKLO", "Oklo names a new finance chief", "2026-11-13", "smallcaps.example", "neutral")
    headline(memory, "ELSE", "Elsewhere Corp rumoured to be for sale", "2026-11-14", "rumours.example", "good")
    assert newsdesk.review(memory, DAY, env={}, judge=UNSURE)["companies"] == 0   # Buddy is acting on neither
    lab_trade(memory)
    assert newsdesk.acting_on(memory) == {"OKLO": "an open strategy-lab trade"}
    got = newsdesk.review(memory, DAY, env={}, judge=UNSURE)
    assert got == {"companies": 1, "stories": 2, "unclear": 2, "settled": 0, "asked": 0, "would_ask": 2}
    assert newsdesk.review(memory, DAY, env={}, judge=UNSURE)["unclear"] == 0     # each story is considered once
    paid = newsdesk.view(memory, DAY)["paid"]
    assert paid["set_up"] is False and paid["would_have_asked_this_month"] == 2 and paid["asked_this_month"] == 0
    assert "an open strategy-lab trade" in paid["checks"][0]["why"] and "could be a rumour" in paid["checks"][0]["why"]
    assert "no news service has a key" in paid["checks"][0]["result"]
    headline(memory, "OKLO", "Oklo sued by a supplier over payments", "2026-11-15", "courtwatch.example", "bad")
    headline(memory, "OKLO", "Oklo plant site floods in storm", "2026-11-15", "localnews.example", "bad")
    asked = []
    newsdesk.set_cap(memory, 1)
    got = newsdesk.review(memory, DAY, env={"BUDDY_PERIGON_KEY": "k"}, judge=UNSURE,
                          ask=lambda m, s, story: asked.append(story["title"]) or "confirmed by two established outlets")
    assert got["asked"] == 1 and got["would_ask"] == 1 and len(asked) == 1     # the month's limit of one is kept
    results = [c["result"] for c in newsdesk.view(memory, DAY)["paid"]["checks"]]
    assert "confirmed by two established outlets" in results and any("limit of 1 paid checks is reached" in r for r in results)
    with pytest.raises(newsdesk.ToolError):
        newsdesk.paid_check(memory, "OKLO", {"title": "x", "outlets": []}, env={})   # no service has a key


def test_the_free_model_goes_first_and_a_news_service_is_asked_only_when_it_cannot_tell(memory):
    headline(memory, "OKLO", "Oklo names a new finance chief", "2026-11-13", "smallcaps.example", "neutral")
    headline(memory, "OKLO", "Oklo delays first reactor after regulator questions", "2026-11-14", "hotstocks.example", "bad")
    lab_trade(memory)
    calls = []

    def fetch(url, data=None, headers=None):
        calls.append((url, headers or {}))
        if "newsdata.io" in url:
            raise OSError("down")                  # the first service fails: the next one is tried
        return json.dumps({"articles": [
            {"title": "Oklo first reactor delayed as regulator asks questions", "url": "https://www.reuters.com/a",
             "source": {"domain": "reuters.com"}, "pubDate": "2026-11-15T10:00:00Z"},
            {"title": "Oklo opens a new office", "url": "https://x.example/b", "source": {"domain": "x.example"},
             "pubDate": "2026-11-15T10:00:00Z"}]}).encode()

    def judge(m, company, story):
        assert company == "Oklo" and story["titles"]
        return ({"clear": True, "tone": "neutral", "reason": "a routine appointment"} if "finance chief" in story["title"]
                else {"clear": False, "tone": "bad", "reason": "one unknown outlet"})

    env = {"BUDDY_NEWSDATA_KEY": "nd", "BUDDY_PERIGON_KEY": "pg"}
    real = newsdesk.paid_check
    got = newsdesk.review(memory, DAY, env=env, judge=judge, ask=lambda m, s, story: real(m, s, story, env, fetch, DAY))
    assert got == {"companies": 1, "stories": 2, "unclear": 2, "settled": 1, "asked": 1, "would_ask": 0}
    perigon = [c for c in calls if "goperigon" in c[0]]
    assert len(perigon) == 1 and perigon[0][1].get("x-api-key") == "pg" and "%22Oklo%22" in perigon[0][0]
    assert "delays" not in perigon[0][0]           # only the company's name is sent
    view = newsdesk.view(memory, DAY)
    by = {c["story"]: c["result"] for c in view["paid"]["checks"]}
    assert by["Oklo names a new finance chief"].startswith("settled by the free model: neutral; a routine appointment")
    assert by["Oklo delays first reactor after regulator questions"].startswith("confirmed: also reported by reuters.com")
    assert "Perigon" in by["Oklo delays first reactor after regulator questions"]
    assert view["paid"]["settled_this_month"] == 1 and view["paid"]["asked_this_month"] == 1
    delay = next(s for s in newsdesk.stories(memory, "OKLO", today=DAY) if "delays" in s["title"])
    assert delay["rating"] == 3 and delay["reports"] == 2     # the established report is kept, so the rating rises


def test_a_trades_news_figures_count_stories_not_headlines(memory):
    bip._ready(memory)
    bip._run(memory, "INSERT INTO bip_signals (key, kind, source, title, url, day, who, amount, techs, sectors, seen) VALUES "
                     "('f1', 'filing', 'test', 'filing', 'https://example.org/x', '2026-11-10', 'Oklo Inc.  (OKLO)', NULL, ?, ?, '2026-11-10')",
             ('["smr"]', f'["{bip.TECHS["smr"]["sector"]}"]'))
    for outlet in ("reuters.com", "cnbc.com", "bloomberg.com", "wsj.com"):
        headline(memory, "OKLO", "Oklo wins Air Force contract for Alaska microreactor", "2026-11-12", outlet, "good")
    got = factors.investing(memory, "OKLO", DAY)
    assert got["f_good_news_14"] == 1.0   # four outlets, one event
