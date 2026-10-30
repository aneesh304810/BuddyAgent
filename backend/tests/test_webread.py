"""Buddy reads web pages itself: main text only, digested by a free model, never private addresses."""

import asyncio
import json

import pytest

from buddy import free_models as fm
from buddy import webread
from buddy.memory import Memory

ENV = {"BUDDY_FREE_MODELS": "1", "BUDDY_GEMINI_KEY": "g"}
PAGE = """<html><head><title>Lima flights</title><script>var x = 1;</script></head><body>
<nav>Home | Deals | Login</nav><header>Big banner</header>
<article><h1>Cheapest flights to Lima</h1><p>Miami to Lima from $289 return on LATAM in March.</p>
<table><tr><th>Airline</th><th>Price</th></tr><tr><td>LATAM</td><td>$289</td></tr></table>
<p>""" + "Prices change daily and include taxes. " * 20 + """</p></article>
<footer>Copyright</footer></body></html>"""


class Free:
    """A stand-in for the free models: answers with `reply`, and records what it was given."""

    def __init__(self, reply="- LATAM Miami-Lima $289 return (March)", enabled=True):
        self.reply, self.on, self.seen = reply, enabled, []

    def enabled(self):
        return ["gemini"] if self.on else []

    async def run(self, kind, text, instruction="", system=None, purpose="", claude_fallback=True, check_text=True):
        self.seen.append((text, instruction, check_text))
        if isinstance(self.reply, Exception):
            raise self.reply
        return {"answer": self.reply}


def reader(free=None, page=PAGE, memory=None):
    return webread.PageReader(memory or Memory(":memory:"), free or Free(), fetcher=lambda url: (url, page))


def test_extract_keeps_main_text_and_tables_and_drops_menus_and_scripts():
    title, text = webread.extract(PAGE)
    assert title == "Lima flights"
    assert "$289 return on LATAM" in text and "LATAM | $289" in text
    for junk in ("var x", "Login", "Big banner", "Copyright"):
        assert junk not in text


def test_free_model_digest_replaces_the_page():
    free = Free()
    out = asyncio.run(reader(free).read(["https://example.com/lima"], "cheapest Miami to Lima flight"))
    assert out.startswith(webread.UNTRUSTED)
    assert "LATAM Miami-Lima $289" in out and "Prices change daily" not in out
    text, instruction, check_text = free.seen[0]
    assert "Cheapest flights to Lima" in text and "Miami to Lima" in instruction
    assert check_text is False  # the page is public; only the question is checked for personal details


def test_unsure_free_model_means_claude_reads_the_trimmed_page():
    out = asyncio.run(reader(Free(fm.NeedsClaude("not sure"))).read(["https://example.com/a"], "price?"))
    assert "Miami to Lima from $289" in out and "Facts pulled out" not in out


def test_nothing_relevant_is_said_plainly():
    out = asyncio.run(reader(Free(webread.NOTHING)).read(["https://example.com/a"], "Tokyo weather"))
    assert "Nothing on this page answers" in out


def test_personal_questions_never_reach_the_free_model():
    free = Free()
    asyncio.run(reader(free).read(["https://example.com/a"], "flights for jane.doe@example.com"))
    assert free.seen == []


def test_full_and_no_free_models_return_page_text():
    for r, full in ((reader(Free()), True), (reader(Free(enabled=False)), False)):
        out = asyncio.run(r.read(["https://example.com/a"], "price", full=full))
        assert "LATAM | $289" in out


def test_at_most_three_pages_and_failures_are_explained():
    def fetcher(url):
        if "bad" in url:
            raise webread.PageError("The site refused (HTTP 403); try web_fetch for this page.")
        return url, PAGE
    r = webread.PageReader(Memory(":memory:"), Free(), fetcher=fetcher)
    out = asyncio.run(r.read([f"https://example.com/{i}" for i in range(5)] + ["https://bad.example"], "q"))
    assert out.count("LATAM Miami-Lima") == 3 and "bad.example" not in out
    out = asyncio.run(r.read(["https://bad.example"], "q"))
    assert "Couldn't read it" in out and "web_fetch" in out


def test_javascript_only_page_points_to_web_fetch():
    out = asyncio.run(reader(page="<html><body><div id=app></div></body></html>").read(["https://x.example"], "q"))
    assert "JavaScript" in out


@pytest.mark.parametrize("url", ["https://localhost/", "https://127.0.0.1/admin", "https://169.254.169.254/latest",
                                 "https://10.0.0.5/", "https://[::1]/", "file:///etc/passwd", "ftp://example.com"])
def test_private_addresses_and_other_schemes_are_refused(url):
    with pytest.raises(webread.PageError):
        webread._check_public(url)


def test_counts_feed_the_insights_web_section():
    memory = Memory(":memory:")
    asyncio.run(reader(memory=memory).read(["https://example.com/a", "https://example.com/b"], "price"))
    web = webread.web_overview(memory)
    assert web["pages_read"] == 2 and web["digested"] == 2 and web["tokens_saved"] > 100


def test_google_search_with_read_top_reads_the_best_results():
    found = webread.UNTRUSTED + json.dumps({"results": [{"title": "A", "link": "https://a.example/1"},
                                                        {"title": "B", "link": "https://b.example/2"}]})
    asked = {}

    async def fake_social_tool(social, name, args):
        asked.update(args)
        return found
    r = reader()
    out = asyncio.run(webread.google_and_read(None, r, {"query": "Lima flights", "read_top": 1}, fake_social_tool))
    assert "read_top" not in asked  # Serper never gets it
    assert "https://a.example/1" in out and out.count("LATAM Miami-Lima") == 1
    plain = asyncio.run(webread.google_and_read(None, r, {"query": "Lima flights"}, fake_social_tool))
    assert plain == found
