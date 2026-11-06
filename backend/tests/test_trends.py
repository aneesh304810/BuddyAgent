"""Trends: sources collected, stories grouped across sources and ranked by momentum, a digest, spike alerts."""

import asyncio
import json
import time

from buddy import trends as tr
from buddy.memory import Memory
from buddy.trends import Trends, rank

NOW = time.time()
ARXIV = b"""<feed xmlns="http://www.w3.org/2005/Atom"><entry><id>https://arxiv.org/abs/2611.01234</id>
<title>Agents that plan: a new benchmark for AI agents</title><summary>We study LLM agents.</summary></entry>
<entry><id>https://arxiv.org/abs/2611.09999</id><title>Protein folding kinetics</title><summary>Biology.</summary></entry></feed>"""


class Web:
    def __init__(self):
        self.urls = []

    def __call__(self, url, headers=None, data=None):
        self.urls.append(url)
        if "hn.algolia.com" in url:
            return {"hits": [{"title": "OpenAI launches new AI agents platform", "points": 900, "num_comments": 400,
                              "created_at_i": NOW - 3600 * 5, "objectID": "1", "url": "https://openai.com/x"},
                             {"title": "Show HN: my weekend project", "points": 40, "num_comments": 5,
                              "created_at_i": NOW - 3600 * 20, "objectID": "2"}]}
        if "google.serper.dev/news" in url:
            assert json.loads(data)["tbs"] == "qdr:d"
            return {"news": [{"title": "OpenAI unveils AI agents platform for business", "link": "https://news.x/1",
                              "source": "Reuters", "date": "2 hours ago"}]}
        if "serpapi.com" in url:
            return {"related_queries": {"rising": [{"query": "openai agents", "value": "Breakout"}]}}
        if "arxiv.org" in url:
            return ARXIV
        if "api.github.com" in url:
            return {"items": [{"full_name": "acme/agentkit", "description": "Build AI agents", "stargazers_count": 3500,
                               "html_url": "https://github.com/acme/agentkit", "created_at": "2026-11-03T00:00:00Z"}]}
        if "reddit.com" in url:
            return {"data": {"children": [{"data": {"title": "OpenAI's new agents platform is wild", "subreddit": "OpenAI",
                                                    "score": 2500, "num_comments": 600, "created_utc": NOW - 3600 * 4,
                                                    "permalink": "/r/OpenAI/x"}}]}}
        if "stocktwits" in url:
            return {"symbols": [{"symbol": "NVDA", "title": "NVIDIA", "watchlist_count": 900000},
                                {"symbol": "BTC.X", "title": "Bitcoin", "watchlist_count": 800000}]}
        return {}


ENV = {"BUDDY_SERPER_KEY": "k", "BUDDY_SERPAPI_KEY": "k"}


def test_the_same_story_across_sources_is_one_story_and_ranks_first():
    t = Trends(Memory(":memory:"), env=ENV, fetch=Web())
    data = t.collect({"name": "AI agents", "kind": "ai", "query": "AI agents"})
    top = data["stories"][0]
    assert "agents platform" in top["title"].lower() and len(top["sources"]) >= 3
    assert {"Hacker News", "News", "Reddit"} <= set(top["sources"])
    assert data["rising"][0]["query"] == "openai agents"
    assert any("Protein" in s["title"] for s in data["stories"]) is False      # off-topic paper dropped
    assert any("agentkit" in s["title"] for s in data["stories"])


def test_a_briefing_and_a_digest_in_reports():
    mem = Memory(":memory:")
    asked = []

    async def summarize(memory, prompt):
        asked.append(prompt)
        return {"headline": "OpenAI's agents platform dominates the day.", "stories": [{"n": 0, "take": "Big launch."}],
                "rising": "Searches for 'openai agents' broke out.", "debate": "Hype versus real enterprise use."}
    t = Trends(mem, env=ENV, fetch=Web(), summarize=summarize)
    mem.set("trends:defaults_set", "1")
    t.watch("AI agents")
    job, summary = asyncio.run(t.digest())
    assert "OpenAI's agents platform dominates the day." in summary
    report = mem.db.execute("SELECT report FROM jobs WHERE id = ?", (job,)).fetchone()[0]
    for bit in ("## AI agents", "Big launch.", "Rising searches:** openai agents (Breakout)", "Where people disagree",
                "Sources:"):
        assert bit in report, bit


def test_stocktwits_splits_stocks_and_crypto_and_kinds_are_guessed():
    t = Trends(Memory(":memory:"), env={}, fetch=Web())
    assert [x["symbol"] for x in t.stocktwits("markets")] == ["NVDA"]
    assert [x["symbol"] for x in t.stocktwits("crypto")] == ["BTC.X"]
    assert Trends.kind_of("AI agents") == "ai" and Trends.kind_of("Nifty midcaps") == "india"
    assert Trends.kind_of("Nvidia earnings") == "markets" and Trends.kind_of("Kerala real estate") == "general"


def test_a_spike_is_three_times_the_usual_day():
    mem = Memory(":memory:")
    t = Trends(mem, env={}, fetch=Web())
    for i, n in enumerate([5, 6, 4, 5]):
        mem.db.execute("INSERT INTO trend_counts (topic, day, mentions) VALUES ('AI', ?, ?)", (f"2026-10-0{i + 1}", n))
    mem.db.execute("INSERT INTO trend_counts (topic, day, mentions) VALUES ('AI', '2026-10-09', 30)")
    assert t.spike("AI") == {"today": 30, "usual": 5.0}


def test_default_topics_and_the_tool():
    mem = Memory(":memory:")
    names = [x["name"] for x in Trends(mem, env={}, fetch=Web()).topics()]
    assert names == ["AI", "US markets", "Crypto", "Indian stocks"]
    assert "Stopped watching crypto" in asyncio.run(tr.run_tool(mem, {"action": "unwatch", "topic": "crypto"}))
    assert "Reports" in asyncio.run(tr.run_tool(mem, {"action": "digest"}))


def test_the_news_tab_keeps_the_latest_briefing_per_topic_and_learns_from_likes():
    mem = Memory(":memory:")

    async def summarize(memory, prompt):
        return {"headline": "Agents everywhere.", "stories": [{"n": 0, "take": "The big one."}]}
    t = Trends(mem, env=ENV, fetch=Web(), summarize=summarize)
    mem.set("trends:defaults_set", "1")
    t.watch("AI agents")
    asyncio.run(t.brief(t.topics()[0]))
    feed = t.feed()
    f = feed["feed"][0]
    assert f["headline"] == "Agents everywhere." and f["stories"][0]["take"] == "The big one." and f["at"]
    assert "AI agents" not in feed["suggestions"] and "Nvidia" in feed["suggestions"]

    # likes: agent platform stories; dislikes: weekend side projects
    for i in range(3):
        t.feedback("AI agents", f"OpenAI agents platform update {i}", f"https://x/{i}", ["Hacker News"], True)
    for i in range(3):
        t.feedback("AI agents", f"Show HN: my weekend project {i}", f"https://y/{i}", ["Hacker News"], False)
    taste = t.taste()
    assert taste["words"]["agents"] > 1 and taste["words"]["weekend"] < -1
    ranked = t.personalize([
        {"title": "Show HN: another weekend project", "sources": ["Hacker News"], "momentum": 9.0},
        {"title": "Anthropic ships agents platform tools", "sources": ["News"], "momentum": 5.0}])
    assert [s["title"][:9] for s in ranked] == ["Anthropic"]          # the kind they keep disliking is dropped
    assert "agents" in ranked[0]["because"]
    assert t.feed()["feed"][0]["stories"] is not None
    assert t.feedback("AI agents", "x", "https://x/0", [], None) == "Cleared."


def test_after_a_few_ratings_buddy_remembers_the_news_taste():
    from buddy.brain import Brain
    mem = Memory(":memory:")
    t = Trends(mem, env={}, fetch=Web())
    for i in range(5):
        t.feedback("AI", f"Databricks agents launch {i}", f"https://z/{i}", ["News"], True)
    prefs = Brain(mem).preferences("news")
    assert prefs and "databricks" in prefs[0]["text"].lower()


def test_without_a_reddit_app_blocked_reddit_is_found_through_google():
    import urllib.error

    class Blocked(Web):
        def __call__(self, url, headers=None, data=None):
            if "reddit.com" in url and "serper" not in url:
                raise urllib.error.HTTPError(url, 403, "Blocked", {}, None)
            if "google.serper.dev/search" in url:
                assert b"site:reddit.com" in data
                return {"organic": [{"title": "OpenAI agents platform thoughts? : r/MachineLearning",
                                     "link": "https://www.reddit.com/r/MachineLearning/comments/abc"}]}
            return super().__call__(url, headers, data)
    t = Trends(Memory(":memory:"), env=ENV, fetch=Blocked())
    got = t.reddit("AI agents", "ai", 1)
    assert got and got[0]["source"] == "Reddit (via Google)" and "r/MachineLearning" in got[0]["title"]
