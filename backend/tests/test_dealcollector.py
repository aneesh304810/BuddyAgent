"""The daily deals collector: feeds (RSS and Atom), robots rules, matches, price checks and the report."""

import io
import json
import urllib.error

import pytest

from buddy import dealcollector as dc
from buddy import deals as price_watch_mod  # noqa: F401  creates price_watches when used
from buddy import shopper
from buddy.connectors.hub import ConnectorHub
from buddy.memory import Memory

RSS = b"""<?xml version="1.0"?><rss version="2.0"><channel>
<item><title>Sony WH-1000XM6 Wireless Noise Cancelling Headphones $299.99 at Best Buy</title>
<link>https://slickdeals.net/f/1</link><pubDate>Fri, 13 Nov 2026 10:00:00 GMT</pubDate>
<description>&lt;b&gt;Hot deal&lt;/b&gt;</description></item>
<item><title>Ninja Air Fryer 4qt $59.99 at Amazon</title><link>https://slickdeals.net/f/2</link></item>
<item><title>Beats Studio Pro $169 at Target</title><link>https://slickdeals.net/f/3</link></item>
</channel></rss>"""
ATOM = b"""<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom">
<entry><title>[Monitor] LG 27GP850 27in 1440p 180Hz $249 (Newegg)</title>
<link href="https://www.reddit.com/r/buildapcsales/comments/x/"/><updated>2026-11-13T09:00:00+00:00</updated></entry>
</feed>"""


@pytest.fixture
def memory(tmp_path):
    m = Memory(tmp_path / "d.db")
    ConnectorHub(m)
    m.set(dc.FEEDS_KEY, json.dumps([
        {"name": "Slickdeals front page", "service": "slickdeals", "url": "https://slickdeals.net/rss"},
        {"name": "r/buildapcsales", "service": "reddit", "url": "https://www.reddit.com/r/buildapcsales/new/.rss"},
        {"name": "Blocked deals", "service": "deal_feed", "url": "https://blocked.example/feed"}]))
    return m


def fetch_for(seen, shopping=None, bestbuy=None):
    def fetch(url, data=None, headers=None, *rest):
        seen.append((url, headers or {}))
        if url.endswith("/robots.txt"):
            if "blocked.example" in url:
                return b"User-agent: *\nDisallow: /"
            return b"User-agent: *\nDisallow: /forum/private\n"
        if "slickdeals.net/rss" in url:
            return RSS
        if "reddit.com" in url:
            return ATOM
        if "serper.dev" in url:
            return json.dumps({"shopping": shopping or []}).encode()
        if "api.bestbuy.com" in url:
            return json.dumps({"products": bestbuy or []}).encode()
        raise AssertionError(url)
    return fetch


def test_feeds_read_once_robots_respected_prices_and_stores(memory):
    seen = []
    new, status = dc.read_feeds(memory, fetch_for(seen))
    assert len(new) == 4
    sony = next(d for d in new if "Sony" in d["title"])
    assert sony["price"] == 299.99 and sony["store"] == "Best Buy" and sony["kind"] == "electronics"
    assert sony["posted"] == "2026-11-13"
    lg = next(d for d in new if "LG" in d["title"])
    assert lg["price"] == 249 and lg["store"] == "Newegg"
    assert any("Blocked deals: skipped" in s for s in status)
    assert not any("blocked.example/feed" in u for u, _ in seen)  # never fetched past its robots rules
    assert all(h.get("User-Agent") == dc.AGENT for _, h in seen)
    again, _ = dc.read_feeds(memory, fetch_for([]))
    assert again == []  # each deal kept once


def test_matches_your_list_and_picks_by_profile(memory, monkeypatch):
    shopper.considering(memory, "Sony WH-1000XM6 headphones", "electronics")
    shopper.save(memory, "electronics", {"trusted_brands": ["LG"], "avoid_brands": ["Beats"]}, ["brands"])
    monkeypatch.setenv("BUDDY_SERPER_KEY", "")
    result = dc.run(memory, env={}, fetch=fetch_for([]))
    assert [m["title"][:4] for m in result["matches"]] == ["Sony"]
    picks = [d["title"] for d in result["for_you"]]
    assert any("LG" in p for p in picks) and not any("Beats" in p for p in picks)
    msgs = dc.messages(result)
    assert msgs and "Deal on something you want" in msgs[0][0] and msgs[0][1][0]["url"] == "https://slickdeals.net/f/1"
    dc.save_report(memory, result)
    rep = memory.db.execute("SELECT report FROM jobs WHERE kind = 'daily_deals'").fetchone()["report"]
    assert "## On your list" in rep and "Sources today" in rep and "never buys" in rep
    assert dc.search(memory, "LG monitor 1440p")[0]["store"] == "Newegg"


def test_price_checks_record_history_and_tell_new_lows(memory):
    from buddy import prices
    shopper.considering(memory, "Dyson V15 vacuum", "home")
    prices.record(memory, "Dyson V15 vacuum", 649.0, "USD")
    env = {"BUDDY_SERPER_KEY": "s", "BUDDY_BESTBUY_KEY": "b"}
    shopping = [{"title": "Dyson V15 Detect Cordless Vacuum", "price": "$569.99", "source": "Walmart",
                 "link": "https://walmart.example/x"},
                {"title": "Shark vacuum", "price": "$199.00", "source": "Target", "link": "https://t.example"}]
    bestbuy = [{"name": "Dyson V15 Detect vacuum", "salePrice": 599.99, "regularPrice": 749.99, "onSale": True,
                "url": "https://bestbuy.example/d"}]
    seen = []
    news = dc.check_prices(memory, env, fetch_for(seen, shopping, bestbuy))
    assert news[0]["price"] == 569.99 and news[0]["store"] == "Walmart" and "lowest yet" in news[0]["why"]
    from buddy import pricebook
    rows = [r["price"] for r in pricebook.run(memory, "SELECT price FROM observations WHERE item = 'dyson v15 vacuum'")]
    assert sorted(rows) == [569.99, 599.99, 649.0]  # the Shark didn't count: not the same thing
    bb = next(u for u, _ in seen if "api.bestbuy.com" in u)
    assert "search=Dyson" in bb and "apiKey=b" in bb


def test_add_and_remove_feeds(memory):
    assert "Added" in dc.add_feed(memory, "https://www.bensbargains.com/rss/", "Ben's Bargains")
    assert any(f["name"] == "Ben's Bargains" for f in dc.feeds(memory))
    with pytest.raises(Exception):
        dc.add_feed(memory, "not a url")
    assert "Removed 1" in dc.remove_feed(memory, "ben's")


def test_due_once_a_day_after_nine(memory):
    from datetime import datetime
    assert not dc.due(memory, datetime(2026, 11, 14, 8, 30))
    assert dc.due(memory, datetime(2026, 11, 14, 9, 0))
    memory.set(dc.DONE_KEY, "2026-11-14")
    assert not dc.due(memory, datetime(2026, 11, 14, 15, 0))


def test_catalog_follows_what_keeps_showing_up(memory):
    from buddy import pricebook
    for day in ("2026-11-01", "2026-11-08"):
        for model, title in (("sony wh-1000xm6", "Sony WH-1000XM6 Headphones"), ("dewalt dcd999", "DeWalt DCD999 Drill")):
            pricebook.run(memory, "INSERT INTO deals (seen_on, source, title, url, price, store, kind, model) VALUES "
                                  "(CURRENT_DATE - 3, 'x', ?, ?, 99, 'Shop', 'electronics', ?)",
                          (title, f"https://x/{model}/{day}", model))
    pricebook.run(memory, "UPDATE deals SET seen_on = CURRENT_DATE - 9 WHERE url LIKE '%2026-11-01'")
    pricebook.run(memory, "INSERT INTO deals (seen_on, source, title, url, price, store, kind, model) VALUES "
                          "(CURRENT_DATE, 'x', 'One-off Thing X100', 'https://x/once', 5, 'Shop', 'other', 'thing x100')")
    assert [c["model"] for c in dc.catalog(memory)] == ["dewalt dcd999", "sony wh-1000xm6"]  # seen on 2 days
    shopping = [{"title": "Sony WH-1000XM6 Wireless", "price": "$310.00", "source": "Target", "link": "https://t/x"},
                {"title": "DeWalt DCD999 20V Drill", "price": "$179.00", "source": "Lowe's", "link": "https://l/x"}]
    env = {"BUDDY_SERPER_KEY": "s"}
    assert dc.check_catalog(memory, env, fetch_for([], shopping), budget=1) == 1  # today's share, in rotation
    assert dc.check_catalog(memory, env, fetch_for([], shopping), budget=1) == 1
    assert pricebook.lowest(memory, "sony wh-1000xm6") == 310.0 and pricebook.lowest(memory, "dewalt dcd999") == 179.0
    assert dc.check_catalog(memory, {}, fetch_for([])) == 0  # no search key: nothing spent


def test_a_paused_feed_is_tried_again_and_the_page_can_say_why_it_is_empty(memory):
    name = dc.feeds(memory)[0]["name"]
    assert dc.page_status(memory)["last_run"] == ""                     # nothing read yet: the page says so
    memory.set("dealfeed:failures", json.dumps({name: dc.FAIL_DAYS}))  # it failed three days running
    seen = []
    new, status = dc.read_feeds(memory, fetch_for(seen))
    paused = next(s for s in status if s.startswith(name))
    assert "paused after failing 3 days in a row" in paused and "tries it again every 7 days" in paused
    assert json.loads(memory.get("dealfeed:failures"))[name] == dc.FAIL_DAYS + 1   # each paused day is counted
    # the day its turn comes it is read again, and a good read clears the count for good
    memory.set("dealfeed:failures", json.dumps({name: dc.FAIL_DAYS + dc.RETRY_EVERY - 1}))
    new, status = dc.read_feeds(memory, fetch_for(seen))
    assert next(s for s in status if s.startswith(name)).endswith(" new")
    assert name not in json.loads(memory.get("dealfeed:failures"))

    dc.run(memory, fetch=fetch_for([]))
    page = dc.page_status(memory)
    assert page["last_run"] and page["off"] is False and any(s.startswith(name) for s in page["feeds"])
    memory.set(dc.OFF_KEY, "1")
    assert dc.page_status(memory)["off"] is True


def test_the_feeds_are_read_again_every_few_hours_and_the_days_counts_add_up(memory, monkeypatch):
    from datetime import datetime, timedelta
    from buddy.tools import user_tz
    day = datetime(2026, 11, 27, 9, 30, tzinfo=user_tz())
    monkeypatch.setattr(dc, "local_now", lambda: day)
    assert dc.read_every_hours({}) == 3.0 and dc.read_every_hours({"BUDDY_DEAL_FEEDS_EVERY_HOURS": "6"}) == 6.0
    assert not dc.later_due(memory, day)                       # the morning run is due: it reads the feeds itself
    result = dc.run(memory, fetch=fetch_for([]))
    memory.set(dc.DONE_KEY, day.date().isoformat())
    assert result["new"] == 4
    first = json.loads(memory.get(dc.STATUS_KEY))
    assert first["reads"] == 1 and first["new_deals"] == 4 and "Slickdeals front page: 3 new" in first["feeds"]
    assert not dc.later_due(memory, day + timedelta(hours=2))  # too soon
    assert dc.later_due(memory, day + timedelta(hours=3))
    assert not dc.later_due(memory, day.replace(hour=23, minute=30))                 # not at night
    assert not dc.later_due(memory, day + timedelta(hours=3), env={"BUDDY_DEAL_FEEDS_EVERY_HOURS": "24"})  # mornings only
    memory.set(dc.OFF_KEY, "1")
    assert not dc.later_due(memory, day + timedelta(hours=3))  # switched off is off
    memory.set(dc.OFF_KEY, "")

    more = RSS.replace(b"https://slickdeals.net/f/1", b"https://slickdeals.net/f/11").replace(b"WH-1000XM6", b"WH-1000XM5")

    def afternoon(url, data=None, headers=None, *rest):       # one feed has a new deal, another stops answering
        if "slickdeals.net/rss" in url:
            return more
        if "reddit.com" in url and not url.endswith("/robots.txt"):
            raise urllib.error.HTTPError(url, 503, "Unavailable", {}, io.BytesIO(b""))
        return fetch_for([])(url, data, headers)
    monkeypatch.setattr(dc, "local_now", lambda: day + timedelta(hours=3))
    got = dc.read_later(memory, afternoon)
    assert got["new"] == 1
    status = json.loads(memory.get(dc.STATUS_KEY))
    assert status["reads"] == 2 and status["new_deals"] == 5 and status["last_read"] == "12:30"
    assert "Slickdeals front page: 4 new" in status["feeds"]   # the day's total, not the last read's
    assert "r/buildapcsales: 1 new" in status["feeds"]         # it answered this morning: one bad afternoon is not news
    assert status["matches"] == first["matches"] and "catalog" in status          # the morning's own numbers are kept
    assert dc._failures(memory).get("r/buildapcsales", 0) == 0                     # a later read never pauses a feed
    page = dc.page_status(memory)
    assert page["reads"] == 2 and page["last_read"] == "12:30" and page["every_hours"] == 3.0
    assert not dc.later_due(memory, day + timedelta(hours=4)) and dc.later_due(memory, day + timedelta(hours=6))


def test_a_read_before_the_morning_run_is_counted_in_the_days_status(memory, monkeypatch):
    from datetime import datetime
    from buddy.tools import user_tz
    early = datetime(2026, 11, 27, 7, 5, tzinfo=user_tz())
    monkeypatch.setattr(dc, "local_now", lambda: early)
    assert dc.later_due(memory, early)                         # before the morning run, and never read: read now
    assert dc.read_later(memory, fetch_for([]))["new"] == 4
    monkeypatch.setattr(dc, "local_now", lambda: early.replace(hour=9, minute=10))
    dc.run(memory, fetch=fetch_for([]))                        # nothing new by then
    status = json.loads(memory.get(dc.STATUS_KEY))
    assert status["reads"] == 2 and status["new_deals"] == 4 and "Slickdeals front page: 3 new" in status["feeds"]
