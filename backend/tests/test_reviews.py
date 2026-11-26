"""Reviews of one result, on tap: Google's own reviews shown word for word (never cached, kept or summarised),
what Reddit, review sites and complaint threads say, warning signs by code, pros and cons that cite their sources,
the owner's own verdicts, and places ranked by the review-count-aware rating."""

import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from buddy import buddysearch as bs
from buddy import gateway, maestro
from buddy import reviews as rv
from buddy.buddysearch import Result
from buddy.memory import Memory
from buddy.tools import ToolError

KEYS = {"BUDDY_GOOGLE_PLACES_KEY": "g", "BUDDY_SERPER_KEY": "s"}


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


class FakeGoogle:
    def __init__(self):
        self.sent = []

    def call(self, memory, name, path="", *, headers=None, env=None, fetch=None, purpose="", **_):
        self.sent.append((name, path, headers))
        if name == "google_places" and path == "/v1/places/ChIJabcdefghij":
            return {"rating": 4.6, "userRatingCount": 300, "googleMapsUri": "https://maps.google.com/?cid=1",
                    "reviews": [{"rating": 5, "relativePublishTimeDescription": "a week ago",
                                 "text": {"text": "SECRET-GOOGLE-WORDS lovely pizza"},
                                 "authorAttribution": {"displayName": "Ann", "uri": "https://maps.google.com/u/1"}}]}
        raise AssertionError((name, path))


@pytest.fixture
def fakes(monkeypatch):
    g, seen = FakeGoogle(), {}
    monkeypatch.setattr(gateway, "call", g.call)

    async def ask(memory, job, prompt, system=None, fetch=None, env=None, personal=False):
        seen.update(job=job, prompt=prompt, personal=personal)
        return {"pros": [{"text": "Great crust", "sources": [1]}, {"text": "Made up", "sources": [99]}],
                "cons": [{"text": "A fee on the bill", "sources": [1, 4]}], "catches": []}
    monkeypatch.setattr(maestro, "ask", ask)
    return g, seen


def _owner_bought(m):
    from buddy import shopper
    shopper._ready(m)
    m.db.execute("INSERT INTO shop_purchases (id, day, merchant, item, amount, kind) VALUES "
                 "(1, '2026-10-02', 'Amazon', 'Sony WH-1000XM5 headphones', 329, 'electronics')")
    m.db.execute("INSERT INTO shop_reviews (purchase_id, asked_on, verdict, note) VALUES (1, '2026-10-20', 'regret', "
                 "'ear cups cracked')")
    m.db.commit()


def test_a_place_google_shown_not_kept_or_summarised(tmp_path, fakes):
    g, seen = fakes
    m = Memory(tmp_path / "b.db")
    social = FakeSocial()
    got = asyncio.run(rv.for_result(m, "place", "Luca Pizza", "Hoboken", "ChIJabcdefghij", env=KEYS, social=social))
    assert got["google"]["reviews"][0] == {"author": "Ann", "author_url": "https://maps.google.com/u/1", "rating": 5,
                                           "when": "a week ago", "text": "SECRET-GOOGLE-WORDS lovely pizza", "url": ""}
    assert got["google"]["attribution"] == "Reviews from Google"
    assert g.sent[0][2]["X-Goog-FieldMask"].endswith("reviews")
    froms = [(it["n"], it["type"], it["from"]) for it in got["items"]]
    assert froms == [(1, "site", "tripadvisor.com"), (2, "site", "yelp.com"), (3, "reddit", "r/hoboken"),
                     (4, "complaint", "forum.example")]  # the blog and the unrelated threads left out
    # only the name and the area went out
    assert social.asked[0] == "Luca Pizza Hoboken reviews" and social.asked[-1] == 'reddit:"Luca Pizza" Hoboken'
    # the summary: public snippets only, never Google's reviews; a health-like snippet left out; made-up sources dropped
    assert seen["job"] == "summarise_reviews" and "SECRET-GOOGLE-WORDS" not in seen["prompt"]
    assert "diabetes" not in seen["prompt"] and "[3]" not in seen["prompt"] and "[1] (tripadvisor.com)" in seen["prompt"]
    assert got["summary"] == {"pros": [{"text": "Great crust", "sources": [1]}],
                              "cons": [{"text": "A fee on the bill", "sources": [1, 4]}], "catches": []}
    signs = {w["sign"] for w in got["warnings"]}
    assert signs == {"disagree", "complaint"}  # Google 4.6 vs Tripadvisor 3.5; the fee in two sources
    # tapping again: the public part from the hour's cache, Google's reviews asked again (never cached)
    n = len(social.asked)
    again = asyncio.run(rv.for_result(m, "place", "Luca Pizza", "Hoboken", "ChIJabcdefghij", env=KEYS, social=social,
                                      summary=False))
    assert again["cached"] and len(social.asked) == n and len(g.sent) == 2 and again["summary"] is None
    cached = " ".join(r["payload"] for r in m.db.execute("SELECT payload FROM search_cache"))
    assert "SECRET-GOOGLE-WORDS" not in cached and m.db.execute("SELECT COUNT(*) FROM search_kept").fetchone()[0] == 0
    assert bs._used(m, "google_places") == 2 and bs._used(m, "serper") == 3
    with pytest.raises(ToolError):
        asyncio.run(rv.for_result(m, "place", "  "))


def test_a_product_through_brave_with_the_owners_own_verdict(tmp_path, fakes, monkeypatch):
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
    assert "regret" not in fakes[1]["prompt"] and "Amazon" not in fakes[1]["prompt"]  # own verdicts never sent
    assert bs._used(m, "brave") == 2 and got["google"] is None


def test_without_search_keys_and_the_endpoint_and_tool(tmp_path, fakes, monkeypatch):
    from buddy import server
    m = Memory(tmp_path / "b.db")
    monkeypatch.setattr(server, "memory", m)
    monkeypatch.setattr(server, "authorized", lambda t: t == "s3cret")
    for k in ("BUDDY_SERPER_KEY", "BUDDY_BRAVE_KEY", "BUDDY_GOOGLE_PLACES_KEY", "BUDDY_REDDIT_CLIENT_ID"):
        monkeypatch.delenv(k, raising=False)
    client, auth = TestClient(server.app), {"Authorization": "Bearer s3cret"}
    got = client.get("/api/search/reviews?kind=place&title=Luca+Pizza&area=Hoboken&rating=4.9&reviews=8",
                     headers=auth).json()
    assert got["items"] == [] and "Serper or Brave key" in got["note"]
    assert got["warnings"][0]["sign"] == "few_reviews" and got["warnings"][0]["text"].startswith("The listing gives 4.9")
    assert got["summary"] is None and got["summary_note"] == "Too little to sum up."
    assert client.get("/api/search/reviews?title=", headers=auth).status_code == 400
    out = json.loads(asyncio.run(rv.run_tool(m, {"name": "Luca Pizza", "kind": "place", "rating": 4.9, "reviews": 8})))
    assert "google" not in out and out["warnings"][0]["sign"] == "few_reviews"
    assert rv.TOOL["name"] == "check_reviews"
