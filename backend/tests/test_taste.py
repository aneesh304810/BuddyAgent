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


def places_search(monkeypatch, places, ok=True):
    """Buddy Search answering with these places (Google Places results, ranked as the real search ranks them)."""
    from buddy import buddysearch as bs
    asked = []

    async def search(memory, query, engines=None, area="", **_):
        asked.append((query, engines, area))
        rs = [bs.Result(p["title"], url="", kind="place", engine="local", provider="google_places",
                        source="Google Places", extra={k: v for k, v in p.items() if k != "title"}) for p in places]
        local = bs.rank([rs], "local") if rs else []
        return {"query": query, "engines": ["local"], "area": area, "results": {"local": local}, "attribution": [],
                "providers": [{"provider": "google_places", "title": "Google Places", "ok": ok, "n": len(local),
                               "error": "" if ok else "Google Places can't be reached right now."}]}
    monkeypatch.setattr(bs, "search", search)
    return asked


class ReviewSocial:
    """Serper and Reddit for the review checks of the top places."""

    def __init__(self):
        self.asked = []

    def has_google(self):
        return True

    def has_reddit(self):
        return True

    def reddit(self, action, query="", **_):
        self.asked.append(query)
        if "Punto Azul" not in query:
            return []
        return [{"title": "Punto Azul is worth the queue", "subreddit": "r/PERU", "score": 88,
                 "text": "Go before 1pm, huge portions.", "link": "https://www.reddit.com/r/PERU/1"},
                {"title": "Unrelated ceviche thread", "subreddit": "r/food", "text": "x", "link": "https://r.example"}]

    def google(self, query, num=10, **_):
        self.asked.append(query)
        if "Punto Azul" in query and query.endswith("reviews"):
            return {"results": [{"title": "Punto Azul - Tripadvisor", "link": "https://www.tripadvisor.com/punto-azul",
                                 "snippet": "4.5 of 5 bubbles, 3,100 reviews"},
                                {"title": "Some blog", "link": "https://random-blog.example/x", "snippet": "nope"}]}
        return {"results": []}


CEVICHE = [
    {"title": "New Spot", "rating": 5.0, "reviews": 4, "price_level": "$$", "category": "Peruvian restaurant"},
    {"title": "Punto Azul", "rating": 4.6, "reviews": 5321, "price_level": "$$", "category": "Seafood restaurant",
     "address": "Calle San Martin 595", "open_now": True, "lat": -12.1225, "lon": -77.0301},
    {"title": "Fancy Fish", "rating": 4.7, "reviews": 3000, "price_level": "$$$$", "category": "Seafood restaurant"},
    {"title": "Quick Ceviche", "rating": 4.5, "reviews": 2000, "price_level": "$", "category": "Fast food restaurant"},
    {"title": "Isolina Taberna", "rating": 4.4, "reviews": 800, "price_level": "$$", "category": "Peruvian restaurant"},
    {"title": "Corner Cevicheria", "category": "Restaurant"},
]


def test_find_places_reviews_first_then_taste_and_history(monkeypatch):
    memory = Memory(":memory:")
    taste = Taste(memory)
    taste.update("food", like="seafood", dislike="fast food", budget="mid-range")
    money = Money(memory)
    for _ in range(3):
        money.add_transaction(38, "PEN", "La Mar Cebichería", "food")
    log = LifeLog(memory)
    day = (local_now().date() - timedelta(days=10)).isoformat()
    log.add(day, "place", "Isolina Taberna", "test", "1", place="Barranco, Lima")
    asked, social = places_search(monkeypatch, CEVICHE), ReviewSocial()
    out = asyncio.run(run_taste_tool(taste, social, "find_places",
                                     {"what": "ceviche", "where": "Miraflores, Lima", "country": "PE"}))
    data = json.loads(out.split("\n", 1)[1])
    assert asked == [("ceviche", ["local"], "Miraflores, Lima")]
    names = [p["title"] for p in data["places"]]
    # reviews first (5.0 from 4 people is not the best), then taste among the top six: seafood liked, over budget
    # and fast food pushed down, a place they go to nudged up; an unrated place last
    assert names == ["Punto Azul", "Isolina Taberna", "Fancy Fish", "New Spot", "Quick Ceviche", "Corner Cevicheria"]
    top = data["places"][0]
    assert top["pick"] and top["taste_fit"] == ["you like seafood"] and top["open_now"] is True
    assert data["places"][1]["taste_fit"] == ["you've been there"]
    assert data["places"][2]["taste_fit"] == ["you like seafood", "above your mid-range budget"]
    assert [p.get("pick", False) for p in data["places"]] == [True, True, True, False, False, False]
    other = top["other_reviews"]
    assert other["reddit"] == [{"title": "Punto Azul is worth the queue", "subreddit": "PERU",
                                "says": "Go before 1pm, huge portions."}]  # links stay in the card, not the prompt
    assert other["review_sites"] == [{"site": "tripadvisor.com", "says": "4.5 of 5 bubbles, 3,100 reviews"}]
    assert '"Punto Azul" Miraflores, Lima' in social.asked and "Punto Azul Miraflores, Lima reviews" in social.asked
    assert not any("New Spot" in q for q in social.asked)  # only the top three had their reviews checked
    assert data["places"][3]["caution"] == "few reviews"
    assert data["your_taste"]["likes"] == ["seafood"] and data["your_taste"]["dislikes"] == ["fast food"]
    assert data["your_history"]["you_spend_at"][0]["merchant"] == "La Mar Cebichería"
    assert "reviews first" in data["how_ranked"]


def test_when_no_place_search_answers_it_says_what_to_use(monkeypatch):
    places_search(monkeypatch, [], ok=False)
    out = asyncio.run(run_taste_tool(Taste(Memory(":memory:")), Social(env={}), "find_places",
                                     {"what": "spa", "where": "Brickell, Miami"}))
    assert "web_search" in out and '"category": "spa"' in out and "can't be reached" in out


def test_places_parsing():
    def web(url, data=None, headers=None):
        assert url == "https://google.serper.dev/places"
        return json.dumps({"places": [{"title": "Aire Ancient Baths", "rating": 4.8, "ratingCount": 900,
                                       "priceLevel": "$$$", "address": "Miami", "cid": "123", "latitude": 1}]}).encode()
    found = Social(env={"BUDDY_SERPER_KEY": "k"}, http=web).places("spa in Miami")
    # The map fields are kept for the chat card; find_places strips them before Claude sees them.
    assert found == [{"title": "Aire Ancient Baths", "rating": 4.8, "ratingCount": 900, "priceLevel": "$$$",
                      "address": "Miami", "cid": "123", "latitude": 1}]


def test_location_near_me_distance_and_staleness(monkeypatch):
    from buddy import dining
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
    asked = places_search(monkeypatch, CEVICHE[1:2])
    social = ReviewSocial()
    social.has_google = lambda: False
    social.has_reddit = lambda: False
    out = asyncio.run(run_taste_tool(Taste(memory), social, "find_places", {"what": "spa near me"}, here))
    assert asked[-1][2] == "Miraflores, Lima" and '"where": "Miraflores, Lima"' in out
    assert "tip expected" in out  # the spa catches to check
    assert '"distance": "0.2 km"' in out  # worked out here, from the phone's position
    assert "-12.12" not in str(asked) and not social.asked  # the position never went to a search
    # a position with no neighbourhood name: never searched with the coordinates
    blank = Location(Memory(":memory:"), reverse=lambda lat, lng: {"country": "PE"})
    blank.update(-12.1211, -77.0297, 30)
    out = asyncio.run(run_taste_tool(Taste(blank.memory), social, "find_places", {"what": "spa"}, blank))
    assert "ask them for the area" in out and len(asked) == 1
    assert dining.area(blank.memory) == ""
    stale = json.loads(memory.get("location:current"))
    stale["at"] = "2020-01-01T00:00:00+00:00"
    memory.set("location:current", json.dumps(stale))
    assert here.current() is None
    out = asyncio.run(run_taste_tool(Taste(memory), social, "find_places", {"what": "spa"}, here))
    assert "Share my location" in out
    try:
        here.update(200, 0)
        raise AssertionError("bad coordinates accepted")
    except ValueError:
        pass
