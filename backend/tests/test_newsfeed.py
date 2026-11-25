"""News from the owner's connected news services: the order they are tried in, the fallback when Google News (Serper)
is out of credits, trends' failure reasons, and a newly listed stock in markets analyze."""

import asyncio
import io
import json
import time
import urllib.error
from datetime import date, timedelta

import pytest

from buddy import gateway, newsfeed
from buddy import social as social_module
from buddy.markets import Markets
from buddy.memory import Memory
from buddy.social import Social, run_social_tool, social_tools
from buddy.tools import ToolError
from buddy.trends import Trends

CATCHER = {"count": 1, "results": [{"title": "Anthropic files for IPO", "url": "https://reuters.com/a",
                                     "source": {"domain": "reuters.com"}, "published_at": "2026-11-19T08:00:00Z",
                                     "story": {"id": "s1"}}]}


def credits_error(url):
    return urllib.error.HTTPError(url, 400, "Bad Request", {}, io.BytesIO(b'{"message": "Not enough credits"}'))


class Web:
    """Serper out of credits; NewsCatcher, NewsData and GDELT as each test sets them."""

    def __init__(self, catcher=CATCHER, newsdata=None, gdelt=None):
        self.calls, self.catcher, self.newsdata, self.gdelt = [], catcher, newsdata, gdelt

    def __call__(self, url, data=None, headers=None):
        self.calls.append((url, headers or {}))
        if "google.serper.dev" in url:
            raise credits_error(url)
        if "newscatcherapi.com" in url:
            assert (headers or {}).get("x-api-key") == "nc" and "/news-api/search?" in url and "search_window=" in url
            if isinstance(self.catcher, Exception):
                raise self.catcher
            return json.dumps(self.catcher).encode()
        if "newsdata.io" in url:
            return json.dumps(self.newsdata or {"results": []}).encode()
        if "gdeltproject.org" in url:
            return json.dumps(self.gdelt or {"articles": []}).encode()
        raise AssertionError(url)


def test_a_serper_credit_error_falls_back_to_a_connected_news_source_in_the_same_call():
    web = Web()
    social = Social(env={"BUDDY_SERPER_KEY": "sk", "BUDDY_NEWSCATCHER_KEY": "nc"}, http=web)
    out = asyncio.run(run_social_tool(social, "google_search", {"query": "Anthropic IPO", "news": True}))
    data = json.loads(out.split("\n", 1)[1])
    assert data["answered_by"] == "NewsCatcher" and data["news"][0]["title"] == "Anthropic files for IPO"
    assert "Not enough credits" in data["fallback_reason"] and "Serper" in data["fallback_reason"]
    assert [c[0].split("/")[2] for c in web.calls] == ["google.serper.dev", "api.newscatcherapi.com"]
    # out of credits: the next news search goes straight to the news services, without asking Serper first
    asyncio.run(run_social_tool(social, "google_search", {"query": "OpenAI IPO", "news": True}))
    assert sum("serper" in c[0] for c in web.calls) == 1
    assert "skipped" in social.google("Nvidia", news=True)["fallback_reason"]


def test_a_failed_web_search_is_not_turned_into_news():
    social = Social(env={"BUDDY_SERPER_KEY": "sk", "BUDDY_NEWSCATCHER_KEY": "nc"}, http=Web())
    try:
        asyncio.run(run_social_tool(social, "google_search", {"query": "weather in Lima"}))
        raise AssertionError("expected the Serper error")
    except Exception as e:
        assert "Not enough credits" in str(e)


def test_news_tools_use_connected_sources_without_serper():
    web = Web()
    social = Social(env={"BUDDY_NEWSCATCHER_KEY": "nc"}, http=web)
    assert "google_search" in {t["name"] for t in social_tools(social)}
    data = json.loads(asyncio.run(run_social_tool(social, "google_search", {"query": "Anthropic", "news": True}))
                      .split("\n", 1)[1])
    assert data["answered_by"] == "NewsCatcher" and "isn't connected" in data["fallback_reason"]
    assert not any("serper" in c[0] for c in web.calls)


def test_each_source_is_tried_in_order_and_says_why_it_did_not_answer():
    web = Web(catcher=urllib.error.HTTPError("u", 429, "Too Many", {}, io.BytesIO(b'{"message": "quota used"}')),
              newsdata={"results": []},
              gdelt={"articles": [{"title": "Anthropic weighs listing", "domain": "ft.com", "url": "https://ft.com/x",
                                   "seendate": "20261119T080000Z"}]})
    got = newsfeed.search("Anthropic IPO", env={"BUDDY_NEWSCATCHER_KEY": "nc", "BUDDY_NEWSDATA_KEY": "nd"}, fetch=web)
    assert got["answered_by"] == "GDELT" and got["news"][0]["date"] == "2026-11-19"
    assert [t["source"] for t in got["tried"]] == ["NewsCatcher", "NewsData.io"]
    assert "429" in got["tried"][0]["error"] and "no headlines" in got["tried"][1]["error"]
    assert got["service"] == "gdelt"
    assert newsfeed.search("", fetch=web) == {"news": [], "answered_by": "", "service": "", "tried": []}


def test_a_news_service_that_is_out_of_credits_is_left_out_for_an_hour_and_gdelt_keeps_its_pace():
    quota = urllib.error.HTTPError("u", 429, "Too Many", {}, io.BytesIO(b'{"message": "monthly quota used"}'))
    web = Web(catcher=quota, newsdata={"results": [{"title": "Anthropic sets a price range", "source_name": "CNBC",
                                                    "link": "https://cnbc.com/a", "pubDate": "2026-11-19 09:00:00"}]})
    env = {"BUDDY_NEWSCATCHER_KEY": "nc", "BUDDY_NEWSDATA_KEY": "nd"}
    first = newsfeed.search("Anthropic IPO", env=env, fetch=web)
    assert first["answered_by"] == "NewsData.io" and first["service"] == "newsdata"
    again = newsfeed.search("OpenAI IPO", env=env, fetch=web)
    assert again["answered_by"] == "NewsData.io" and "skipped" in again["tried"][0]["error"]
    assert sum("newscatcherapi" in c[0] for c in web.calls) == 1   # not asked first again, and failing, every time
    newsfeed._out_until["newscatcher"] = time.time() - 1            # the hour has passed: it is asked again
    newsfeed.search("xAI IPO", env=env, fetch=web)
    assert sum("newscatcherapi" in c[0] for c in web.calls) == 2

    # nothing else connected: GDELT, which asks for one search every five seconds, is not asked twice in a row
    web = Web(gdelt={"articles": []})
    assert newsfeed.search("first", env={}, fetch=web)["tried"][0]["error"] == "no headlines for this search"
    assert "five seconds" in newsfeed.search("second", env={}, fetch=web)["tried"][0]["error"]
    assert sum("gdeltproject" in c[0] for c in web.calls) == 1


def test_buddys_own_limits_and_an_unreachable_service_pause_nothing():
    """Only the service's own answer (out of credits, key refused) leaves it out for an hour."""
    for error in (gateway.ConnectError("serper", "Serper: too many calls this minute; try again shortly.", 429),
                  gateway.ConnectError("serper", "Serper is disconnected (you switched it off on the Security page).", 403),
                  gateway.ConnectError("serper", "Serper can't be reached right now.", 0, "timed out"),
                  gateway.ConnectError("serper", "Serper said HTTP 500: upstream error", 500, "upstream error")):
        newsfeed.note_serper_failure(error)
        assert not newsfeed.serper_paused(), str(error)
    for error in (gateway.ConnectError("serper", "Serper said HTTP 400: Not enough credits", 400, "Not enough credits"),
                  gateway.ConnectError("serper", "Serper said HTTP 403", 403)):
        newsfeed.reset()
        newsfeed.note_serper_failure(error)
        assert newsfeed.serper_paused(), str(error)


def test_web_search_failing_does_not_pause_news_and_the_answering_service_is_logged():
    social_module._cache.clear()
    logged = []
    social_module.METER = lambda provider, endpoint: logged.append((provider, endpoint))
    try:
        social = Social(env={"BUDDY_SERPER_KEY": "sk", "BUDDY_NEWSCATCHER_KEY": "nc"}, http=Web())
        for i in range(social_module.FAIL_LIMIT):   # Serper out of credits: three web searches fail
            with pytest.raises(ToolError, match="Not enough credits"):
                asyncio.run(run_social_tool(social, "google_search", {"query": f"weather in Lima {i}"}))
        with pytest.raises(ToolError, match="pausing"):   # web search is paused, as before
            asyncio.run(run_social_tool(social, "google_search", {"query": "weather in Quito"}))
        out = asyncio.run(run_social_tool(social, "google_search", {"query": "Anthropic listing", "news": True}))
        assert json.loads(out.split("\n", 1)[1])["answered_by"] == "NewsCatcher"   # news still answers
        assert logged == [("newscatcher", "news")]
    finally:
        social_module.METER = None
        social_module._cache.clear()


class TrendsWeb:
    """Trends' fetch: Serper out of credits, Alpaca news unreachable, NewsCatcher has the story."""

    def __init__(self):
        self.urls = []

    def __call__(self, url, headers=None, data=None):
        self.urls.append(url)
        if "google.serper.dev" in url:
            raise credits_error(url)
        if "data.alpaca.markets/v1beta1/news" in url:
            raise urllib.error.URLError("connection refused")
        if "newscatcherapi.com" in url:
            return CATCHER
        return {}


def test_trends_now_falls_back_and_shows_why_sources_failed():
    env = {"BUDDY_SERPER_KEY": "sk", "BUDDY_NEWSCATCHER_KEY": "nc", "BUDDY_ALPACA_KEY_ID": "k", "BUDDY_ALPACA_SECRET": "s"}
    t = Trends(Memory(":memory:"), env=env, fetch=TrendsWeb())
    data = t.collect({"name": "Anthropic IPO", "kind": "markets", "query": "Anthropic IPO"})
    assert any(s["title"] == "Anthropic files for IPO" for s in data["stories"])
    assert "News (NewsCatcher)" in data["sources"]
    assert any("Not enough credits" in e for e in data["errors"])
    assert any(e.startswith("alpaca news:") and "can't be reached" in e for e in data["errors"])


def test_when_no_news_service_has_the_story_each_is_asked_once():
    class Quiet(TrendsWeb):
        def __call__(self, url, headers=None, data=None):
            if "newscatcherapi.com" in url:
                self.urls.append(url)
                return {"articles": []}
            return super().__call__(url, headers, data)

    web = Quiet()
    env = {"BUDDY_SERPER_KEY": "sk", "BUDDY_NEWSCATCHER_KEY": "nc", "BUDDY_ALPACA_KEY_ID": "k", "BUDDY_ALPACA_SECRET": "s"}
    data = Trends(Memory(":memory:"), env=env, fetch=web).collect(
        {"name": "Anthropic IPO", "kind": "markets", "query": "Anthropic IPO"})
    assert sum("newscatcherapi.com" in u for u in web.urls) == 1 and sum("gdeltproject.org" in u for u in web.urls) == 1
    assert data["errors"].count("news: NewsCatcher: no headlines for this search") == 1
    assert any(e.startswith("alpaca news:") for e in data["errors"])


def bars(n):
    start = date(2026, 11, 1)
    return [{"t": (start + timedelta(days=i)).isoformat() + "T05:00:00Z", "o": 20 + i, "h": 21 + i, "l": 19 + i,
             "c": 20 + i, "v": 1_000_000} for i in range(n)]


def test_a_newly_listed_stock_gets_a_clear_message_not_an_error():
    def fetch(url, headers=None, data=None):
        if "/v2/stocks/bars" in url:
            return {"bars": {"ANT": bars(5)}}
        return {}

    env = {"BUDDY_ALPACA_KEY_ID": "k", "BUDDY_ALPACA_SECRET": "s"}
    got = Markets(Memory(":memory:"), env=env, fetch=fetch).analyze("ANT")
    assert got["status"] == "new_listing" and got["trading_days"] == 5 and got["close"] == 24
    assert "newly listed" in got["message"] and "60 days" in got["message"]
    assert got["first_day"] == "2026-11-01" and got["change_since_first_day"] == 20.0

    none = Markets(Memory(":memory:"), env=env, fetch=lambda url, headers=None, data=None: {}).analyze("ANT")
    assert none["status"] == "no_price_history" and "symbol is wrong" in none["message"]
