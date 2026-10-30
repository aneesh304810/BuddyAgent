"""Taste profile and personal place recommendations."""

import asyncio
import json
from datetime import timedelta

from buddy.lifelog import LifeLog
from buddy.memory import Memory
from buddy.money import Money
from buddy.social import Social
from buddy.taste import Taste, category_of, run_taste_tool
from buddy.tools import local_now


def test_categories_from_words():
    assert category_of("quiet rooftop cocktail bar") == "nightlife"
    assert category_of("deep tissue massage") == "spa"
    assert category_of("boutique hotel near the beach") == "hotels"
    assert category_of("best ceviche") == "food"
    assert category_of("something fun") == "general"


def test_likes_dislikes_and_changing_your_mind():
    taste = Taste(Memory(":memory:"))
    taste.update("nightlife", like="rooftop bars")
    taste.update("nightlife", like="live jazz", dislike="loud clubs", budget="mid-range")
    t = taste.update("nightlife", like="Loud clubs")  # changed their mind
    assert t["likes"] == ["rooftop bars", "live jazz", "Loud clubs"] and t["dislikes"] == []
    assert taste.update("nightlife", forget="live jazz")["likes"] == ["rooftop bars", "Loud clubs"]
    out = asyncio.run(run_taste_tool(taste, None, "my_taste", {}))
    assert json.loads(out)[0]["budget"] == "mid-range"


def test_find_places_brings_maps_taste_and_history():
    memory = Memory(":memory:")
    taste = Taste(memory)
    taste.update("food", like="seafood", dislike="chains", budget="mid-range")
    money = Money(memory)
    for _ in range(3):
        money.add_transaction(38, "PEN", "La Mar Cebichería", "food")
    log = LifeLog(memory)
    day = (local_now().date() - timedelta(days=10)).isoformat()
    log.add(day, "place", "Isolina Taberna", "test", "1", place="Barranco, Lima")

    class FakeSocial:
        def has_google(self):
            return True

        def has_reddit(self):
            return True

        def places(self, query, country):
            assert query == "ceviche in Miraflores, Lima" and country == "PE"
            return [{"title": "New Spot", "rating": 5.0, "ratingCount": 4, "priceLevel": "$$"},
                    {"title": "Punto Azul", "rating": 4.6, "ratingCount": 5321, "priceLevel": "$$",
                     "category": "Seafood restaurant", "address": "Calle San Martin 595"}]

        def reddit(self, action, query, sort="relevance", period="all", limit=5):
            if "Punto Azul" not in query:
                return []
            return [{"title": "Punto Azul is worth the queue", "subreddit": "r/PERU", "score": 88,
                     "text": "Go before 1pm, huge portions."},
                    {"title": "Unrelated ceviche thread", "subreddit": "r/food", "score": 3, "text": "x"}]

        def google(self, query, country="US", num=8):
            return {"results": [{"link": "https://www.tripadvisor.com/punto-azul", "snippet": "4.5 of 5, 3,100 reviews"},
                                {"link": "https://random-blog.example/x", "snippet": "nope"}]}

    out = asyncio.run(run_taste_tool(taste, FakeSocial(), "find_places",
                                     {"what": "ceviche", "where": "Miraflores, Lima", "country": "PE"}))
    data = json.loads(out.split("\n", 1)[1])
    assert data["places"][0]["title"] == "Punto Azul"  # 4.6 from 5,321 beats 5.0 from 4
    other = data["places"][0]["other_reviews"]
    assert other["reddit"] == [{"title": "Punto Azul is worth the queue", "subreddit": "r/PERU", "score": 88,
                                "says": "Go before 1pm, huge portions."}]
    assert other["review_sites"] == [{"site": "tripadvisor.com", "says": "4.5 of 5, 3,100 reviews"}]
    assert data["your_taste"]["likes"] == ["seafood"] and data["your_taste"]["dislikes"] == ["chains"]
    assert data["your_history"]["you_spend_at"][0]["merchant"] == "La Mar Cebichería"
    assert data["your_history"]["places_you_go"][0]["title"] == "Isolina Taberna"


def test_without_maps_it_says_what_to_use():
    out = asyncio.run(run_taste_tool(Taste(Memory(":memory:")), Social(env={}), "find_places",
                                     {"what": "spa", "where": "Brickell, Miami"}))
    assert "web_search" in out and '"category": "spa"' in out


def test_places_parsing():
    def web(url, data=None, headers=None):
        assert url == "https://google.serper.dev/places"
        return json.dumps({"places": [{"title": "Aire Ancient Baths", "rating": 4.8, "ratingCount": 900,
                                       "priceLevel": "$$$", "address": "Miami", "cid": "123", "latitude": 1}]}).encode()
    found = Social(env={"BUDDY_SERPER_KEY": "k"}, http=web).places("spa in Miami")
    # The map fields are kept for the chat card; find_places strips them before Claude sees them.
    assert found == [{"title": "Aire Ancient Baths", "rating": 4.8, "ratingCount": 900, "priceLevel": "$$$",
                      "address": "Miami", "cid": "123", "latitude": 1}]


def test_location_near_me_and_staleness(monkeypatch):
    from buddy import location as loc_mod
    from buddy.location import Location

    memory = Memory(":memory:")
    lookups = []

    def reverse(lat, lng):
        lookups.append((lat, lng))
        return {"area": "Miraflores", "city": "Lima", "country": "PE"}

    here = Location(memory, reverse=reverse)
    assert here.label() == ""
    here.update(-12.1211, -77.0297, 30)
    assert here.label() == "Miraflores, Lima"

    class FakeSocial:
        def has_google(self):
            return True

        def has_reddit(self):
            return False

        def places(self, query, country):
            assert query == "spa in Miraflores, Lima" and country == "PE"
            return []

    out = asyncio.run(run_taste_tool(Taste(memory), FakeSocial(), "find_places", {"what": "spa near me"}, here))
    assert '"where": "Miraflores, Lima"' in out and "tip expected" in out  # the spa catches to check
    stale = json.loads(memory.get("location:current"))
    stale["at"] = "2020-01-01T00:00:00+00:00"
    memory.set("location:current", json.dumps(stale))
    assert here.current() is None
    out = asyncio.run(run_taste_tool(Taste(memory), FakeSocial(), "find_places", {"what": "spa"}, here))
    assert "Share my location" in out
    try:
        here.update(200, 0)
        raise AssertionError("bad coordinates accepted")
    except ValueError:
        pass


def test_catches_and_too_good_ratings():
    from buddy.taste import _review_check

    class FakeSocial:
        def has_reddit(self):
            return False

        def has_google(self):
            return True

        def google(self, query, country="US", num=8):
            if "scam" in query:
                return {"results": [{"title": "Sky Bar Lima - cover charge", "link": "https://www.reddit.com/r/lima/x",
                                     "snippet": "Sky Bar adds a 40 sol cover after 10pm and a 15% service charge"}]}
            return {"results": []}

    found = _review_check(FakeSocial(), {"title": "Sky Bar", "rating": 4.9, "ratingCount": 12}, "Miraflores, Lima", "PE")
    assert found["complaints_and_catches"][0]["says"].startswith("Sky Bar adds a 40 sol cover")
    assert "few reviews" in found["caution"]
