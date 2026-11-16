"""News desk: headlines become stories, sources are rated, and a paid check is only considered for a story that
is unclear and about a company Buddy is acting on."""

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


def test_only_unclear_stories_on_companies_buddy_acts_on_are_candidates_and_the_limit_holds(memory):
    headline(memory, "OKLO", "Oklo delays first reactor after regulator questions", "2026-11-14", "hotstocks.example", "bad")
    headline(memory, "OKLO", "Oklo names a new finance chief", "2026-11-13", "smallcaps.example", "neutral")
    headline(memory, "ELSE", "Elsewhere Corp rumoured to be for sale", "2026-11-14", "rumours.example", "good")
    assert newsdesk.review(memory, DAY, env={})["companies"] == 0   # Buddy is acting on neither: nothing to check
    stratlab._ready(memory)
    pricebook.run(memory, "INSERT INTO lab_trades (strategy, symbol, opened, entry, stop, target, shares, hold_days, why, features, "
                          "spy_entry) VALUES ('trend', 'OKLO', '2026-11-12', 100, 93, 114, 10, 28, 'x', '{}', 400)")
    assert newsdesk.acting_on(memory) == {"OKLO": "an open strategy-lab trade"}
    got = newsdesk.review(memory, DAY, env={})
    assert got == {"companies": 1, "stories": 2, "unclear": 2, "asked": 0, "would_ask": 2}
    assert newsdesk.review(memory, DAY, env={})["unclear"] == 0     # each story is considered once
    paid = newsdesk.view(memory, DAY)["paid"]
    assert paid["set_up"] is False and paid["would_have_asked_this_month"] == 2 and paid["asked_this_month"] == 0
    assert "an open strategy-lab trade" in paid["checks"][0]["why"] and "no paid news source" in paid["checks"][0]["result"]
    headline(memory, "OKLO", "Oklo sued by a supplier over payments", "2026-11-15", "courtwatch.example", "bad")
    headline(memory, "OKLO", "Oklo plant site floods in storm", "2026-11-15", "localnews.example", "bad")
    asked = []
    newsdesk.set_cap(memory, 1)
    got = newsdesk.review(memory, DAY, env={"BUDDY_PAID_NEWS_KEY": "k"}, ask=lambda m, s, story: asked.append(story["title"]) or "confirmed by two established outlets")
    assert got["asked"] == 1 and got["would_ask"] == 1 and len(asked) == 1     # the month's limit of one is kept
    results = [c["result"] for c in newsdesk.view(memory, DAY)["paid"]["checks"]]
    assert "confirmed by two established outlets" in results and any("limit of 1 paid checks is reached" in r for r in results)
    with pytest.raises(newsdesk.ToolError):
        newsdesk.paid_check(memory, "OKLO", {})   # no provider is wired in until one is chosen


def test_a_trades_news_figures_count_stories_not_headlines(memory):
    bip._ready(memory)
    bip._run(memory, "INSERT INTO bip_signals (key, kind, source, title, url, day, who, amount, techs, sectors, seen) VALUES "
                     "('f1', 'filing', 'test', 'filing', 'https://example.org/x', '2026-11-10', 'Oklo Inc.  (OKLO)', NULL, ?, ?, '2026-11-10')",
             ('["smr"]', f'["{bip.TECHS["smr"]["sector"]}"]'))
    for outlet in ("reuters.com", "cnbc.com", "bloomberg.com", "wsj.com"):
        headline(memory, "OKLO", "Oklo wins Air Force contract for Alaska microreactor", "2026-11-12", outlet, "good")
    got = factors.investing(memory, "OKLO", DAY)
    assert got["f_good_news_14"] == 1.0   # four outlets, one event
