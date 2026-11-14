"""Signals: reading Reddit (app or feed), tagging, the free model's refinement, counting, retention."""

import asyncio
import io
import json
import urllib.error
from datetime import date, datetime, timedelta

import pytest

from buddy import pricebook, signals
from buddy.connectors.hub import ConnectorHub
from buddy.memory import Memory

DAY = date(2026, 11, 14)


@pytest.fixture
def memory(tmp_path, monkeypatch):
    monkeypatch.delenv("BUDDY_REDDIT_CLIENT_ID", raising=False)
    monkeypatch.delenv("BUDDY_REDDIT_FEEDS", raising=False)
    m = Memory(tmp_path / "buddy.db")
    ConnectorHub(m)
    yield m
    pricebook.close_all()


TITLES = {
    "investing": ["NVDA beats earnings, record high on AI chip demand", "Is $PLTR overvalued? Bubble warning",
                  "VOO vs VTI for a long-term ETF portfolio"],
    "options": ["Selling covered calls on AMD, 30 DTE"],
    "Economics": ["Gold surges as crude oil prices plunge"],
    "buildapcsales": ["[Monitor] LG 27GP850 27in 1440p - $249 (Best Buy)", "[Meta] Weekly discussion thread"],
    "ToolDeals": ["DeWalt 20V drill kit $99 at Home Depot"],
    "awardtravel": ["Hyatt free night certificates: best resort uses", "United business class fares to Tokyo"],
    "BuyItForLife": ["My Sony WH-1000XM5 broke after a year, terrible hinge"],
}


def feed(name):
    entries = "".join(
        f"<entry><title>{t.replace('&', '&amp;')}</title><link href='https://www.reddit.com/r/{name}/comments/"
        f"{name[:3].lower()}{i}/x/'/><updated>2026-11-14T10:00:00+00:00</updated><content type='html'>body</content>"
        f"</entry>" for i, t in enumerate(TITLES.get(name, [])))
    return f"<?xml version='1.0'?><feed xmlns='http://www.w3.org/2005/Atom'>{entries}</feed>".encode()


def fake_fetch(blocked=(), seen=None):
    def fetch(url, data=None, headers=None):
        if seen is not None:
            seen.append((url, headers or {}))
        name = url.split("/r/")[1].split("/")[0]
        if name in blocked or blocked == "all":
            raise urllib.error.HTTPError(url, 403, "Forbidden", {}, io.BytesIO(b"blocked"))
        if "oauth.reddit.com" in url:
            return json.dumps({"data": {"children": [
                {"data": {"id": f"{name[:3].lower()}{i}", "title": t, "selftext": "", "score": 100 - i,
                          "num_comments": 7, "permalink": f"/r/{name}/comments/{name[:3].lower()}{i}/x/"}}
                for i, t in enumerate(TITLES.get(name, []))]}})
        return feed(name)
    return fetch


def run(memory, **kw):
    kw.setdefault("ask", no_model)
    return asyncio.run(signals.run(memory, today=DAY, **kw))


async def no_model(memory, prompt):
    return None


def test_tags_from_code():
    known = {"NVDA", "AMD", "VOO", "VTI", "PLTR", "GLD"}
    t = signals.tag({"title": "NVDA beats earnings, record high", "text": ""}, "investing", "investing", known)
    assert t["tickers"] == ["NVDA"] and t["category"] == "stocks" and t["tone"] > 0
    assert signals.tag({"title": "Is $ZZZQ a fraud? Lawsuit filed", "text": ""}, "stocks", "investing", known)[
        "tickers"] == ["ZZZQ"]  # a cashtag counts even when unknown
    assert signals.tickers_in("ARE YOU ALL IN ON AI NOW", known | {"ALL", "NOW"}) == []  # words, not symbols
    assert signals.tag({"title": "VOO vs VTI", "text": ""}, "investing", "investing", known)["category"] == "etfs"
    assert signals.tag({"title": "Selling puts on AMD", "text": ""}, "stocks", "investing", known)["category"] == "options"
    assert signals.tag({"title": "Gold surges", "text": ""}, "Economics", "investing", known)["category"] == "commodities"
    assert signals.tag({"title": "Hyatt free night", "text": ""}, "awardtravel", "travel", known)["category"] == "hotels"
    assert signals.tag({"title": "United fares to Tokyo", "text": ""}, "awardtravel", "travel", known)[
        "category"] == "flights"
    r = signals.tag({"title": "My Sony WH-1000XM5 headphones broke, terrible", "text": ""}, "BuyItForLife", "reviews", known)
    assert "wh-1000xm5" in r["product"] and r["tone"] < 0 and r["category"] == "electronics"
    assert signals.tag({"title": "DeWalt drill $99", "text": ""}, "ToolDeals", "deals", known)["category"] == "tools"


def test_a_night_on_the_public_feed(memory):
    seen = []
    got = run(memory, fetch=fake_fetch(seen=seen))
    assert got["route"] == "feed" and got["posts"] == 11 and got["new"] == 11 and not got["failed"]
    assert all("oauth" not in u for u, _ in seen) and "Buddy personal reader" in seen[0][1]["User-Agent"]
    assert got["deals_to_price_book"] == 2  # the monitor and the drill; the discussion thread has no price
    deals = pricebook.run(memory, "SELECT title, price, source FROM deals ORDER BY price")
    assert [d["price"] for d in deals] == [99.0, 249.0] and deals[0]["source"] == "reddit"
    top = signals.trending(memory, kind="ticker", today=DAY)
    assert {t["key"] for t in top} >= {"NVDA", "PLTR", "VOO", "VTI", "AMD"}
    nvda = next(t for t in top if t["key"] == "NVDA")
    assert nvda["mentions"] == 1 and nvda["tone"] > 0 and nvda["times_usual"] is None  # no history yet
    assert run(memory, fetch=fake_fetch())["new"] == 0  # the same posts again add nothing
    assert signals.trending(memory, kind="ticker", today=DAY)[0]["mentions"] == 1


def test_the_owners_reddit_app_is_used_when_set(memory, monkeypatch):
    monkeypatch.setattr(signals, "_token", lambda memory: "tok")
    seen = []
    got = run(memory, fetch=fake_fetch(seen=seen))
    assert got["route"] == "app" and all("oauth.reddit.com" in u for u, _ in seen)
    assert seen[0][1]["Authorization"] == "Bearer tok"
    row = pricebook.run(memory, "SELECT score, comments FROM signals WHERE title LIKE 'NVDA%'")[0]
    assert row["score"] == 100 and row["comments"] == 7


def test_one_blocked_community_is_named_and_all_blocked_stops_early(memory):
    got = run(memory, fetch=fake_fetch(blocked={"wallstreetbets"}))
    assert list(got["failed"]) == ["wallstreetbets"] and got["posts"] == 11
    seen = []
    got = run(memory, fetch=fake_fetch(blocked="all", seen=seen))
    assert got["posts"] == 0 and "(stopped)" in got["failed"] and len(seen) == 3
    assert "Reddit refused the public feed" in signals.test(memory, fake_fetch(blocked="all"))
    assert "works through the public feed" in signals.test(memory, fake_fetch())


def test_feeds_can_be_turned_off(memory, monkeypatch):
    monkeypatch.setenv("BUDDY_REDDIT_FEEDS", "0")
    seen = []
    got = run(memory, fetch=fake_fetch(seen=seen))
    assert got["posts"] == 0 and not seen and "turned off" in next(iter(got["failed"].values()))


def test_a_free_model_refines_public_titles_only(memory):
    prompts = []

    async def ask(memory, prompt):
        prompts.append(prompt)
        lines = [ln for ln in prompt.splitlines() if ln[:1].isdigit()]
        tags = [{"i": int(ln.split(".")[0]), "tickers": ["nvda"] if "NVDA" in ln else [], "tone": 5 if "NVDA" in ln
                 else -0.6, "theme": "AI Chips" if "NVDA" in ln else "valuation",
                 "product": "sony wh-1000xm5" if "Sony" in ln else ""} for ln in lines]
        return {"tags": tags + [{"i": 999, "tone": 1}, {"i": "x"}, {"tone": 1}]}  # junk rows are dropped

    memory.db.execute("CREATE TABLE IF NOT EXISTS mkt_watch (symbol TEXT, market TEXT, note TEXT)")
    memory.db.execute("INSERT INTO mkt_watch VALUES ('ZETA', 'us', 'my secret pick')")
    got = run(memory, fetch=fake_fetch(), ask=ask)
    assert got["read_by_free_model"] == 6  # investing and reviews; not deals or travel
    assert "ZETA" not in prompts[0] and "secret" not in prompts[0] and "$249" not in prompts[0]
    row = pricebook.run(memory, "SELECT tone, theme, tagged_by FROM signals WHERE title LIKE 'NVDA%'")[0]
    assert row == {"tone": 1.0, "theme": "ai chips", "tagged_by": "free model"}  # tone clamped
    assert signals.trending(memory, kind="theme", today=DAY)[0]["key"] in ("valuation", "ai chips")
    review = signals.about(memory, "Sony WH-1000XM5", today=DAY)
    assert review["posts"] == 1 and review["tone"] < 0
    assert signals.about(memory, "NVDA", today=DAY)["top"][0]["community"] == "investing"


def test_no_free_model_never_falls_back_to_paid_claude(memory, monkeypatch):
    monkeypatch.delenv("BUDDY_FREE_MODELS", raising=False)
    assert asyncio.run(signals._ask_free(memory, "x")) is None


def test_attention_against_its_own_usual_and_a_year_of_posts(memory):
    signals._ready(memory)
    for i in range(1, 31):  # NVDA: one mention a day for a month, then eight today
        pricebook.run(memory, "INSERT INTO signal_days VALUES (?, 'stocks', 'ticker', 'NVDA', 1, 0.1, 10)",
                      ((DAY - timedelta(days=i)).isoformat(),))
    pricebook.run(memory, "INSERT INTO signal_days VALUES (?, 'stocks', 'ticker', 'NVDA', 8, 0.6, 900)",
                  (DAY.isoformat(),))
    top = signals.trending(memory, "stocks", today=DAY)[0]
    assert top["key"] == "NVDA" and top["times_usual"] == 8.0 and top["days_of_history"] == 30
    old = (DAY - timedelta(days=400)).isoformat()
    pricebook.run(memory, "INSERT INTO signals (id, day, source, title) VALUES ('old', ?, 'reddit', 'x')", (old,))
    pricebook.run(memory, "INSERT INTO signal_days VALUES (?, 'stocks', 'ticker', 'OLD', 3, 0, 0)", (old,))
    assert signals.prune(memory, DAY) == 1
    assert pricebook.run(memory, "SELECT COUNT(*) AS n FROM signal_days WHERE key = 'OLD'")[0]["n"] == 1  # counts stay


def test_communities_and_the_schedule(memory):
    assert signals.communities(memory)["wallstreetbets"]["group"] == "investing"
    assert "r/Bogleheads" in signals.add(memory, "r/Bogleheads", "investing")
    signals.remove(memory, "wallstreetbets")
    signals.switch(memory, "travel", False)
    c = signals.communities(memory)
    assert "Bogleheads" in c and "wallstreetbets" not in c and not c["Flights"]["on"] and c["stocks"]["on"]
    assert signals.due(memory, datetime(2026, 11, 14, 2, 0)) and not signals.due(memory, datetime(2026, 11, 14, 12, 0))
    status = json.loads(asyncio.run(signals.run_tool(memory, {"action": "status"})))
    assert status["raw_posts_kept_days"] == 365 and status["communities"]["Flights"] == "travel (off)"
