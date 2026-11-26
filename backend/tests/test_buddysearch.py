"""Buddy Search: what's asked picks the providers; they run together; results come back in one form, merged,
ranked and kept only as each provider's terms allow; the owner's own things never leave the server."""

import asyncio
import json
import urllib.parse

import pytest
from fastapi.testclient import TestClient

from buddy import buddysearch as bs
from buddy import gateway
from buddy.buddysearch import Result
from buddy.memory import Memory
from buddy.tools import ToolError

KEYS = {"BUDDY_BRAVE_KEY": "b", "BUDDY_TAVILY_KEY": "t", "BUDDY_GOOGLE_PLACES_KEY": "g", "BUDDY_SERPER_KEY": "s"}


@pytest.mark.parametrize("q,engines,area", [
    ("best italian restaurant in hoboken", ["local"], "hoboken"),
    ("coffee shops near Journal Square, Jersey City", ["local"], "Journal Square, Jersey City"),
    ("What is happening in AI?", ["news", "web"], ""),
    ("Find cheapest iPhone 16", ["products"], ""),
    ("Research longevity science", ["research", "web"], ""),
    ("find the etihad booking in my email", ["internal"], ""),
    ("how tall is mount everest", ["web"], ""),
])
def test_what_is_asked_picks_the_engines(q, engines, area):
    got = bs.understand(q)
    assert got["engines"] == engines and got["area"] == area


def test_the_same_page_or_place_is_one_thing():
    assert bs.canonical("http://www.Example.com/a/?utm_source=x&id=3#top") == "https://example.com/a?id=3"
    assert bs.canonical("javascript:alert(1)") == ""
    a = Result("Luca's Pizza", engine="local", kind="place", extra={"lat": 40.7441, "lon": -74.0324})
    b = Result("Luca’s pizza", engine="local", kind="place", extra={"lat": 40.74412, "lon": -74.03238})
    assert bs.identity(a) != bs.identity(Result("Other", kind="place", extra={"lat": 40.7441, "lon": -74.0324}))
    assert bs.identity(Result("X", url="https://pubmed.ncbi.nlm.nih.gov/5/", kind="paper")) == "pmid:5"
    assert bs.identity(a) == bs.identity(Result("Luca's Pizza", kind="place", extra={"lat": 40.74409, "lon": -74.03241}))
    assert b.title  # (a different apostrophe is a different name: kept apart rather than wrongly merged)


def test_products_cheapest_offer_first():
    got = bs.rank([[Result("Page", "https://p.example", engine="products", provider="brave", source="Brave Search")],
                   [Result("Phone", "https://s1.example", kind="product", provider="shopping", source="Store A",
                           extra={"price": 799.0}),
                    Result("Phone", "https://s2.example", kind="product", provider="shopping", source="Store B",
                           extra={"price": 729.99})]], "products")
    assert [r["extra"].get("price") for r in got] == [729.99, 799.0, None]


def test_ranking_merges_rewards_corroboration_and_official_sources():
    brave = [Result("A", "https://a.com/x", provider="brave", source="Brave Search"),
             Result("Gov", "https://www.cdc.gov/page", provider="brave", source="Brave Search")]
    tavily = [Result("A again", "https://www.a.com/x/?utm_source=t", snippet="a longer snippet here", provider="tavily",
                     source="Tavily"), Result("C", "https://c.com", provider="tavily", source="Tavily")]
    got = bs.rank([brave, tavily], "web")
    assert got[0]["url"] == "https://a.com/x" and got[0]["found_by"] == ["Brave Search", "Tavily"]
    assert got[0]["corroborated"] and got[0]["snippet"] == "a longer snippet here"
    gov = next(r for r in got if r["site"] == "cdc.gov")
    c = next(r for r in got if r["site"] == "c.com")
    assert gov.get("official") and gov["score"] > c["score"]


class FakeWorld:
    """Fake providers through Buddy Connect, recording what each was sent."""

    def __init__(self, brave_ok=True):
        self.sent, self.brave_ok = [], brave_ok

    def call(self, memory, name, path="", *, data=None, json_body=None, headers=None, raw=False, fetch=None, env=None,
             purpose="", personal=False, method=""):
        self.sent.append((name, path, json_body, data))
        if name == "brave":
            if not self.brave_ok:
                raise gateway.ConnectError("brave", "Brave can't be reached right now.")
            if "/news/" in path:
                return {"results": [{"title": "AI news", "url": "https://news.example/ai", "description": "d",
                                     "page_age": "2026-11-25T10:00:00"}]}
            return {"web": {"results": [{"title": "Page one", "url": "https://one.example/p", "description": "first"},
                                        {"title": "NIH page", "url": "https://www.nih.gov/x", "description": "gov"}]}}
        if name == "tavily":
            return {"results": [{"title": "Page one", "url": "https://one.example/p?utm_campaign=z", "content": "more",
                                 "score": 0.9}]}
        if name == "serper":
            return {"organic": [{"title": "From Google", "link": "https://g.example/", "snippet": "s"}]}
        if name == "google_places":
            return {"places": [{"id": "ChIJabc", "displayName": {"text": "Luca Pizza"}, "formattedAddress":
                                "100 Washington St, Hoboken", "rating": 4.6, "userRatingCount": 300,
                                "googleMapsUri": "https://maps.google.com/?cid=1",
                                "location": {"latitude": 40.7441, "longitude": -74.0324}}]}
        if name == "nominatim":
            return [{"boundingbox": ["40.73", "40.76", "-74.05", "-74.02"], "name": "Hoboken"}]
        if name == "overpass":
            return {"elements": [{"type": "node", "id": 7, "lat": 40.74411, "lon": -74.03242,
                                  "tags": {"name": "Luca Pizza", "cuisine": "italian;pizza", "opening_hours": "Mo-Su 11-22",
                                           "addr:housenumber": "100", "addr:street": "Washington St"}},
                                 {"type": "node", "id": 8, "lat": 40.75, "lon": -74.03,
                                  "tags": {"name": "Casa Roma", "cuisine": "italian"}}]}
        raise AssertionError(name)


@pytest.fixture
def world(monkeypatch):
    w = FakeWorld()
    monkeypatch.setattr(gateway, "call", w.call)
    monkeypatch.setattr(bs, "_news", lambda m, q, env=None, fetch=None: [])
    monkeypatch.setattr(bs, "_shopping", lambda m, q, env=None, fetch=None: [])
    monkeypatch.setattr(bs, "_papers", lambda m, q, env=None, fetch=None: [])
    return w


def test_places_from_google_and_openstreetmap_merged_and_kept_by_their_terms(tmp_path, world):
    m = Memory(tmp_path / "b.db")
    got = asyncio.run(bs.search(m, "italian restaurant in hoboken", env=KEYS, fetch=lambda *a, **k: None))
    places = got["results"]["local"]
    luca = places[0]
    assert luca["title"] == "Luca Pizza" and luca["found_by"] == ["Google Places", "OpenStreetMap"]
    assert luca["extra"]["rating"] == 4.6 and luca["extra"]["hours"] == "Mo-Su 11-22"  # each filled the other's gap
    assert [p["title"] for p in places] == ["Luca Pizza", "Casa Roma"]
    assert got["attribution"] == ["© OpenStreetMap contributors, ODbL"]
    # the provider was sent the words and the area name, never a position
    places_call = next(s for s in world.sent if s[0] == "google_places")
    assert places_call[2]["textQuery"] == "italian restaurant in hoboken"
    overpass = urllib.parse.parse_qs(next(s for s in world.sent if s[0] == "overpass")[3].decode())["data"][0]
    assert '["amenity"="restaurant"]["cuisine"~"italian",i]' in overpass
    kept = {r["id"]: dict(r) for r in m.db.execute("SELECT * FROM search_kept")}
    assert kept["google_places:ChIJabc"]["title"] == "" and kept["google_places:ChIJabc"]["detail"] == "{}"
    osm = kept["osm:node/7"]
    assert osm["title"] == "Luca Pizza" and json.loads(osm["detail"])["hours"] == "Mo-Su 11-22"
    assert "rating" not in json.loads(osm["detail"])  # Google's rating is never kept under OpenStreetMap's licence


def test_web_runs_providers_together_and_falls_back(tmp_path, world, monkeypatch):
    m = Memory(tmp_path / "b.db")
    got = asyncio.run(bs.search(m, "how tall is mount everest", env=KEYS))
    web = got["results"]["web"]
    assert web[0]["url"] == "https://one.example/p" and web[0]["corroborated"]
    assert {p["provider"] for p in got["providers"]} == {"brave", "tavily"}  # Serper held back as the fallback
    kept = {r["id"]: dict(r) for r in m.db.execute("SELECT * FROM search_kept WHERE provider = 'brave'")}
    assert all(json.loads(r["detail"]) == {} and r["url"] for r in kept.values())  # Brave: links only
    # Brave down and Tavily without a key: Google through Serper answers
    world.brave_ok = False
    got = asyncio.run(bs.search(m, "how tall is k2", env={k: v for k, v in KEYS.items() if k != "BUDDY_TAVILY_KEY"}))
    assert [r["title"] for r in got["results"]["web"]] == ["From Google"]
    assert next(p for p in got["providers"] if p["provider"] == "brave")["error"].startswith("Brave")


def test_daily_limits_and_the_cache(tmp_path, world, monkeypatch):
    m = Memory(tmp_path / "b.db")
    env = KEYS | {"BUDDY_SEARCH_CAP_BRAVE": "1"}
    asyncio.run(bs.search(m, "first question", env=env))
    got = asyncio.run(bs.search(m, "second question", env=env))
    assert next(p for p in got["providers"] if p["provider"] == "brave")["error"] == "today's limit reached"
    n = len(world.sent)
    again = asyncio.run(bs.search(m, "first question", env=env))
    assert all(p["cached"] for p in again["providers"] if p["ok"]) and len(world.sent) == n  # no new call


def test_the_owners_own_things_never_leave_or_get_copied(tmp_path, world, monkeypatch):
    m = Memory(tmp_path / "b.db")
    monkeypatch.setattr(bs, "_internal", lambda memory, q: [Result("Etihad booking EY101", snippet="Your booking",
                                                                    engine="internal", kind="message",
                                                                    provider="internal", source="Your email")])
    got = asyncio.run(bs.search(m, "find the etihad booking in my email", env=KEYS))
    assert got["engines"] == ["internal"] and got["results"]["internal"][0]["title"] == "Etihad booking EY101"
    assert world.sent == []  # nothing went to any provider
    assert m.db.execute("SELECT COUNT(*) FROM search_kept").fetchone()[0] == 0
    assert m.db.execute("SELECT COUNT(*) FROM search_cache").fetchone()[0] == 0


def test_the_summary_uses_claude_only_with_the_owners_things(tmp_path, monkeypatch):
    from buddy import maestro
    m = Memory(tmp_path / "b.db")
    seen = {}

    async def ask(memory, job, prompt, system=None, fetch=None, env=None, personal=False):
        seen.update(job=job, personal=personal, prompt=prompt)
        return {"answer": "It is 8,849 m [1].", "sources": [1, 9]}
    monkeypatch.setattr(maestro, "ask", ask)
    found = {"query": "how tall", "engines": ["web"], "results": {"web": [
        {"title": "Everest", "url": "https://e.example", "source": "Brave Search", "site": "e.example",
         "snippet": "8,849 m"}]}}
    s = asyncio.run(bs.summarise(m, found))
    assert s["answer"].startswith("It is") and [c["n"] for c in s["sources"]] == [1] and seen["personal"] is False
    assert seen["job"] == "summarise_search" and "[1] Everest (Brave Search, e.example): 8,849 m" in seen["prompt"]
    asyncio.run(bs.summarise(m, found | {"engines": ["internal"]}))
    assert seen["personal"] is True


def test_status_history_endpoints_and_the_tool(tmp_path, world, monkeypatch):
    from buddy import server
    m = Memory(tmp_path / "b.db")
    monkeypatch.setattr(server, "memory", m)
    monkeypatch.setattr(server, "authorized", lambda t: t == "s3cret")
    for k, v in KEYS.items():
        monkeypatch.setenv(k, v)
    client, auth = TestClient(server.app), {"Authorization": "Bearer s3cret"}
    got = client.get("/api/search?q=how+tall+is+mount+everest", headers=auth).json()
    assert got["results"]["web"][0]["found_by"] == ["Brave Search", "Tavily"]
    assert client.get("/api/search?q=", headers=auth).status_code == 400
    assert client.get("/api/search?q=x&engines=nope", headers=auth).status_code == 400
    st = client.get("/api/search/status", headers=auth).json()
    brave = next(p for p in st["providers"] if p["provider"] == "brave")
    assert brave["ready"] and brave["today"] == 1 and brave["daily_limit"] == 40 and brave["keeps"] == "links"
    assert next(p for p in st["providers"] if p["provider"] == "google_places")["keeps"] == "id"
    assert st["history"][0]["query"] == "how tall is mount everest"
    out = json.loads(asyncio.run(bs.run_tool(m, {"query": "pizza in hoboken"})))
    assert out["engines"] == ["local"] and out["results"]["local"][0]["title"] == "Luca Pizza"
    gone = client.post("/api/search", json={"action": "forget"}, headers=auth).json()
    assert gone["deleted"] >= 2 and gone["history"] == [] and gone["kept"] == 0
    with pytest.raises(ToolError, match="What should I search for"):
        asyncio.run(bs.search(m, "  "))
