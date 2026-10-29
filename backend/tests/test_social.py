"""Reddit, Google (Serper) and Google Trends, against fake services."""

import asyncio
import json

import pytest

from buddy.social import Social, run_social_tool, social_tools
from buddy.tools import ToolError

TRENDS = b"""<?xml version="1.0"?><rss xmlns:ht="https://trends.google.com/trending/rss" version="2.0"><channel>
<item><title>copa libertadores</title><ht:approx_traffic>200K+</ht:approx_traffic><pubDate>Thu, 29 Oct 2026</pubDate>
<ht:news_item><ht:news_item_title>Final tonight in Lima</ht:news_item_title><ht:news_item_url>https://news.example/a</ht:news_item_url>
<ht:news_item_source>El Comercio</ht:news_item_source></ht:news_item></item>
<item><title>diwali sale</title><ht:approx_traffic>50K+</ht:approx_traffic></item></channel></rss>"""


class FakeWeb:
    def __init__(self):
        self.calls = []

    def __call__(self, url, data=None, headers=None):
        self.calls.append((url, data, headers or {}))
        if "trends.google.com" in url:
            return TRENDS
        if "access_token" in url:
            assert headers["Authorization"].startswith("Basic ")
            return b'{"access_token": "tok", "expires_in": 86400}'
        if "oauth.reddit.com/r/travel/search" in url:
            assert headers["Authorization"] == "Bearer tok" and "restrict_sr=1" in url
            return json.dumps({"data": {"children": [{"data": {
                "title": "Cusco in December?", "subreddit_name_prefixed": "r/travel", "score": 120,
                "num_comments": 40, "created_utc": 1793232000, "permalink": "/r/travel/comments/abc/cusco/",
                "selftext": "Rainy season but worth it. Card 4111 1111 1111 1111"}}]}}).encode()
        if "oauth.reddit.com/comments/abc" in url:
            return json.dumps([{}, {"data": {"children": [
                {"kind": "t1", "data": {"author": "maria", "score": 50, "body": "Go! Book Machu Picchu early."}},
                {"kind": "more", "data": {}}]}}]).encode()
        if "google.serper.dev/search" in url:
            assert headers["X-API-KEY"] == "serper-key" and json.loads(data)["gl"] == "pe"
            return json.dumps({"organic": [{"title": "Best ceviche in Lima", "link": "https://x.example",
                                            "snippet": "..."}], "answerBox": {"answer": "La Mar"}}).encode()
        raise AssertionError(url)


def test_trending_is_free_and_always_there():
    web = FakeWeb()
    social = Social(env={}, http=web)
    assert [t["name"] for t in social_tools(social)] == ["trending"]
    out = asyncio.run(run_social_tool(social, "trending", {"country": "peru"}))
    data = json.loads(out.split("\n", 1)[1])
    assert data[0]["search"] == "copa libertadores" and data[0]["approx_searches"] == "200K+"
    assert data[0]["news"][0]["source"] == "El Comercio" and "geo=PE" in web.calls[0][0]


def test_reddit_and_google_when_connected():
    web = FakeWeb()
    social = Social(env={"BUDDY_REDDIT_CLIENT_ID": "id", "BUDDY_REDDIT_SECRET": "sec", "BUDDY_SERPER_KEY": "serper-key"},
                    http=web)
    assert {t["name"] for t in social_tools(social)} == {"trending", "reddit", "google_search"}
    posts = social.reddit("search", "cusco december", "r/travel")
    assert posts[0]["title"] == "Cusco in December?" and "4111 1111" not in posts[0]["text"]
    comments = social.reddit("comments", post=posts[0]["link"])
    assert comments == [{"author": "maria", "score": 50, "text": "Go! Book Machu Picchu early."}]
    social.reddit("search", "x", "travel")
    assert sum("access_token" in c[0] for c in web.calls) == 1  # the token is reused
    g = social.google("best ceviche", country="Peru")
    assert g["answerBox"] == {"answer": "La Mar"} and g["results"][0]["title"] == "Best ceviche in Lima"


def test_answers_are_cached():
    import buddy.social as soc
    soc._cache.clear()
    web = FakeWeb()
    social = Social(env={}, http=web)
    first = asyncio.run(run_social_tool(social, "trending", {"country": "BR"}))
    assert asyncio.run(run_social_tool(social, "trending", {"country": "BR"})) == first
    assert len(web.calls) == 1


def test_not_connected_and_failures_are_clear():
    social = Social(env={}, http=FakeWeb())
    with pytest.raises(ToolError, match="Reddit isn't connected"):
        asyncio.run(run_social_tool(social, "reddit", {"action": "search", "query": "x"}))

    def broken(url, data=None, headers=None):
        raise OSError("down")
    with pytest.raises(ToolError, match="Try web_search"):
        asyncio.run(run_social_tool(Social(env={}, http=broken), "trending", {"country": "MX"}))
