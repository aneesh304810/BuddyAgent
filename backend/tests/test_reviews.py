"""Reviews of a place or product: what Reddit, review sites and complaint threads say (numbered, with where each
came from), warning signs by code, the owner's own verdicts, and places ranked by the review-count-aware rating."""

import asyncio
import json

import pytest

from buddy import buddysearch as bs
from buddy import reviews as rv
from buddy.buddysearch import Result
from buddy.memory import Memory
from buddy.tools import ToolError

KEYS = {"BUDDY_SERPER_KEY": "s"}


def test_ratings_in_snippets_and_the_review_score():
    assert rv.rating_in("Rated 4.5/5 by 1,203 travellers") == 4.5
    assert rv.rating_in("We give it 4 out of 5") == 4.0
    assert rv.rating_in("Score: 8.6/10 for picture quality") == 4.3
    assert rv.rating_in("4.5 of 5 bubbles") == 4.5
    assert rv.rating_in("open 10/5 to 11/30") is None and rv.rating_in("five stars from me") is None
    assert rv.review_score(4.6, 5000) > rv.review_score(5.0, 4)  # many people's 4.6 beats four people's 5.0
    assert rv.review_score(None, 10) is None and rv.review_score("x", 3) is None


def test_warning_signs():
    items = [{"n": 1, "from": "tripadvisor.com", "title": "Luca", "says": "Rated 3.4/5. A hidden fee on the bill",
              "rating": 3.4},
             {"n": 2, "from": "reddit.com", "title": "Luca Pizza?", "says": "they added a service charge", "rating": None},
             {"n": 3, "from": "yelp.com", "title": "Luca", "says": "rude staff", "rating": None}]
    got = rv.warnings("place", [{"source": "Google", "rating": 4.8, "count": 12}], items)
    signs = {w["sign"]: w for w in got}
    assert "only 12 reviews" in signs["few_reviews"]["text"]
    assert signs["disagree"]["text"] == "Sources disagree: Google gives 4.8, tripadvisor.com 3.4." \
        and signs["disagree"]["sources"] == [1]
    fees = [w for w in got if w["sign"] == "complaint"]
    assert len(fees) == 1 and "fees or surprise charges" in fees[0]["text"] and fees[0]["sources"] == [1, 2]
    # one source saying it is not a pattern; many reviews is not "few"
    assert rv.warnings("place", [{"source": "Google", "rating": 4.8, "count": 900}], items[2:]) == []


def test_places_rank_by_the_review_count_aware_rating():
    few = Result("Perfect Five", kind="place", engine="local", provider="google_places", source="Google Places",
                 extra={"rating": 5.0, "reviews": 4, "lat": 1.0, "lon": 1.0})
    many = Result("Well Loved", kind="place", engine="local", provider="google_places", source="Google Places",
                  extra={"rating": 4.6, "reviews": 5000, "lat": 2.0, "lon": 2.0})
    poor = Result("Not Great", kind="place", engine="local", provider="google_places", source="Google Places",
                  extra={"rating": 3.6, "reviews": 800, "lat": 3.0, "lon": 3.0})
    plain = Result("Unrated", kind="place", engine="local", provider="osm", source="OpenStreetMap",
                   extra={"lat": 4.0, "lon": 4.0})
    got = bs.rank([[poor, few, plain, many]], "local")
    assert [r["title"] for r in got] == ["Well Loved", "Perfect Five", "Unrated", "Not Great"]
    assert got[1]["caution"] == "few reviews" and "caution" not in got[0] and "review_score" not in got[2]
    assert bs.POLICY["google_places"]["cache_s"] == 0  # Google's terms: nothing but the place ID is kept or cached


class FakeSocial:
    def __init__(self, reddit_app=False):
        self.asked, self.reddit_app = [], reddit_app

    def has_google(self):
        return True

    def has_reddit(self):
        return self.reddit_app

    def google(self, q, num=10, **_):
        self.asked.append(q)
        if "reviews" in q:
            return {"results": [
                {"title": "LUCA PIZZA, Hoboken - Tripadvisor", "link": "https://www.tripadvisor.com/r/1",
                 "snippet": "Rated 3.5/5. Great crust but a hidden fee appeared on the bill."},
                {"title": "Luca Pizza - Yelp", "link": "https://www.yelp.com/biz/luca", "snippet": "Friendly, fast."},
                {"title": "Some blog", "link": "https://blog.example/luca", "snippet": "not a review site"}]}
        if "scam" in q:
            return {"results": [
                {"title": "Luca Pizza charged me a service charge", "link": "https://forum.example/t/9",
                 "snippet": "Watch the receipt at Luca."},
                {"title": "Other place", "link": "https://forum.example/t/10", "snippet": "nothing about it"}]}
        raise AssertionError(q)

    def reddit(self, action, query="", **_):
        self.asked.append(f"reddit:{query}")
        return [{"title": "Is Luca Pizza worth it?", "text": "Best slice in Hoboken, go before 1 pm. I have diabetes "
                 "so I skip the dessert", "link": "https://www.reddit.com/r/hoboken/1", "subreddit": "r/hoboken"},
                {"title": "Unrelated", "text": "x", "link": "https://www.reddit.com/r/x/2"}]


def _owner_bought(m):
    from buddy import shopper
    shopper._ready(m)
    m.db.execute("INSERT INTO shop_purchases (id, day, merchant, item, amount, kind) VALUES "
                 "(1, '2026-10-02', 'Amazon', 'Sony WH-1000XM5 headphones', 329, 'electronics')")
    m.db.execute("INSERT INTO shop_reviews (purchase_id, asked_on, verdict, note) VALUES (1, '2026-10-20', 'regret', "
                 "'ear cups cracked')")
    m.db.commit()


def test_a_place_what_people_say_and_the_warning_signs(tmp_path):
    m = Memory(tmp_path / "b.db")
    social = FakeSocial()
    got = asyncio.run(rv.for_result(m, "place", "Luca Pizza", "Hoboken", rating=4.6, count=300, rated_by="Google",
                                    env=KEYS, social=social))
    froms = [(it["n"], it["type"], it["from"]) for it in got["items"]]
    assert froms == [(1, "site", "tripadvisor.com"), (2, "site", "yelp.com"), (3, "reddit", "r/hoboken"),
                     (4, "complaint", "forum.example")]  # the blog and the unrelated threads left out
    assert got["items"][0]["rating"] == 3.5 and got["via"] == "Google (Serper)"
    # only the name and the area went out
    assert social.asked[0] == "Luca Pizza Hoboken reviews" and social.asked[-1] == 'reddit:"Luca Pizza" Hoboken'
    signs = {w["sign"]: w for w in got["warnings"]}
    assert set(signs) == {"disagree", "complaint"}  # Google 4.6 vs Tripadvisor 3.5; the fee in two sources
    assert signs["complaint"]["sources"] == [1, 4]
    # asking again within the hour: from the cache, no new searches; nothing is kept
    n = len(social.asked)
    again = asyncio.run(rv.for_result(m, "place", "Luca Pizza", "Hoboken", env=KEYS, social=social))
    assert again["cached"] and len(social.asked) == n
    assert m.db.execute("SELECT COUNT(*) FROM search_kept").fetchone()[0] == 0
    assert bs._used(m, "serper") == 3
    with pytest.raises(ToolError):
        asyncio.run(rv.for_result(m, "place", "  "))


def test_a_product_through_brave_with_the_owners_own_verdict(tmp_path, monkeypatch):
    m = Memory(tmp_path / "b.db")
    _owner_bought(m)
    asked = []

    def brave(memory, q, kind, env=None, fetch=None):
        asked.append(q)
        if q.endswith("review"):
            return [Result("Sony WH-1000XM5 review", "https://www.rtings.com/headphones/sony", "Score 8.4/10",
                           provider="brave"),
                    Result("XM5 after a year", "https://www.reddit.com/r/headphones/3", "Sony ear cups cracked on mine",
                           provider="brave"),
                    Result("Shop", "https://shop.example/xm5", "Buy now", provider="brave")]
        return [Result("Sony WH-1000XM5 cracked hinge", "https://forum.example/a", "the Sony band cracked",
                       provider="brave")]
    monkeypatch.setattr(bs, "_brave", brave)
    social = FakeSocial()
    social.has_google = lambda: False
    got = asyncio.run(rv.for_result(m, "product", "Sony WH-1000XM5", "Hoboken", env={"BUDDY_BRAVE_KEY": "b"},
                                    social=social))
    assert got["via"] == "Brave Search" and got["area"] == "" and asked[0] == "Sony WH-1000XM5 review"
    assert [(it["type"], it["from"]) for it in got["items"]] == [("site", "rtings.com"), ("reddit", "Reddit"),
                                                                 ("complaint", "forum.example")]
    assert got["items"][0]["rating"] == 4.2
    assert [w["text"] for w in got["warnings"]] == ["2 sources mention stops working or breaks."]
    assert got["own"] == [{"day": "2026-10-02", "merchant": "Amazon", "item": "Sony WH-1000XM5 headphones",
                           "verdict": "regret", "note": "ear cups cracked"}]
    assert bs._used(m, "brave") == 2 and len(asked) == 2


def test_without_search_keys_and_the_tool(tmp_path, monkeypatch):
    m = Memory(tmp_path / "b.db")
    for k in ("BUDDY_SERPER_KEY", "BUDDY_BRAVE_KEY", "BUDDY_GOOGLE_PLACES_KEY", "BUDDY_REDDIT_CLIENT_ID"):
        monkeypatch.delenv(k, raising=False)
    got = asyncio.run(rv.for_result(m, "place", "Luca Pizza", "Hoboken", rating="4.9", count="8"))
    assert got["items"] == [] and "Serper or Brave key" in got["note"]
    assert got["warnings"][0]["sign"] == "few_reviews" and got["warnings"][0]["text"].startswith("The listing gives 4.9")
    said = asyncio.run(rv.run_tool(m, {"name": "Luca Pizza", "kind": "place", "rating": 4.9, "reviews": 8}))
    assert said.startswith("Reviews by other people. Information only")
    out = json.loads(said.split("\n", 1)[1])
    assert out["warnings"][0]["sign"] == "few_reviews"
    assert rv.TOOL["name"] == "check_reviews"
