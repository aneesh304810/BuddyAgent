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

        def places(self, query, country):
            assert query == "ceviche in Miraflores, Lima" and country == "PE"
            return [{"title": "Punto Azul", "rating": 4.6, "ratingCount": 5321, "priceLevel": "$$",
                     "category": "Seafood restaurant", "address": "Calle San Martin 595"}]

    out = asyncio.run(run_taste_tool(taste, FakeSocial(), "find_places",
                                     {"what": "ceviche", "where": "Miraflores, Lima", "country": "PE"}))
    data = json.loads(out.split("\n", 1)[1])
    assert data["places"][0]["title"] == "Punto Azul"
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
    assert found == [{"title": "Aire Ancient Baths", "rating": 4.8, "ratingCount": 900, "priceLevel": "$$$",
                      "address": "Miami"}]
