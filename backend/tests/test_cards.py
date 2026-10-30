"""Rich cards: tools offer results, Claude picks by id, the phone gets photos, maps and links."""

import asyncio
import json
from types import SimpleNamespace

from buddy import agent, cards
from buddy.memory import Memory
from buddy.social import run_social_tool
from buddy.taste import Taste, run_taste_tool
from tests.test_conversation import Scripted, text, tool_use


class FakeSocial:
    def has_google(self):
        return True

    def has_reddit(self):
        return False

    def places(self, query, country):
        return [{"title": "Punto Azul", "rating": 4.6, "ratingCount": 5321, "priceLevel": "$$",
                 "category": "Seafood restaurant", "address": "Calle San Martin 595, Lima",
                 "thumbnailUrl": "https://lh5.googleusercontent.com/p/abc", "latitude": -12.12,
                 "longitude": -77.03, "cid": "987", "website": "https://puntoazul.pe"}]

    def reddit(self, *a, **k):
        return []

    def google(self, query, country="US", num=8, news=False):
        if "reviews" in query:
            return {"results": [{"link": "https://www.tripadvisor.com/punto-azul", "snippet": "4.5 of 5"}]}
        return {"results": [{"title": "Long queue", "link": "https://blog.example/punto",
                             "snippet": "Punto Azul has a long queue and cash only"}]}


def test_places_become_cards_and_claude_gets_only_ids():
    box = cards.CardBox()
    token = cards.current.set(box)
    try:
        out = asyncio.run(run_taste_tool(Taste(Memory(":memory:")), FakeSocial(), "find_places",
                                         {"what": "ceviche", "where": "Miraflores, Lima"}))
    finally:
        cards.current.reset(token)
    claude_sees = out.split("\n", 1)[1] if not out.startswith("{") else out
    place = json.loads(claude_sees[claude_sees.index("{"):])["places"][0]
    assert place["card"] == "p1"
    assert not {"thumbnailUrl", "latitude", "cid"} & set(place)  # no photo or map data in the prompt
    assert "googleusercontent" not in out and "maps?cid" not in out
    card = box.cards["p1"]
    assert card["image"].startswith("https://lh5.") and card["lat"] == -12.12
    assert card["maps_url"] == "https://www.google.com/maps?cid=987"
    assert card["reviews"][0]["url"] == "https://www.tripadvisor.com/punto-azul"
    assert card["catches"] and card["catches"][0]["url"] == "https://blog.example/punto"


def test_news_ids_are_fresh_each_reply_even_from_cache():
    class News:
        def has_google(self):
            return True

        def google(self, query, news=False, country="US", num=10):
            return {"news": [{"title": "Sol rises", "source": "Reuters", "link": "https://reuters.com/a",
                              "imageUrl": "https://img.example/a.jpg"}]}
    for _ in range(2):
        box = cards.CardBox()
        token = cards.current.set(box)
        out = asyncio.run(run_social_tool(News(), "google_search", {"query": "peru sol", "news": True}))
        cards.current.reset(token)
        assert '"card": "n1"' in out and "img.example" not in out
        assert box.cards["n1"]["image"] == "https://img.example/a.jpg"


def test_pick_only_from_this_turn_and_web_search_urls():
    box = cards.CardBox()
    box.add(cards.link_card({"title": "Old", "link": "https://a.example"}))
    box.seen_web_results([SimpleNamespace(type="web_search_tool_result", content=[
        SimpleNamespace(url="https://www.airbnb.com/rooms/42", title="Loft in Barranco", page_age="")])])
    said = box.pick([{"id": "https://www.airbnb.com/rooms/42", "note": "quiet, big terrace"},
                     {"id": "https://made-up.example/x"}, {"id": "w1"}])
    assert "made-up.example" in said
    assert [c["kind"] for c in box.picked] == ["stay", "link"]
    assert box.picked[0]["note"] == "quiet, big terrace"


def test_show_cards_ends_the_turn_and_reaches_the_chat(monkeypatch):
    mem = Memory(":memory:")
    convo = agent.Conversation(mem)

    async def find(name, args):
        cards.offer(cards.place_card(FakeSocial().places("", "")[0]))
        return "found p1"

    fake = Scripted([
        [tool_use("lookup", {})],
        [text("Punto Azul is the one."), tool_use("show_cards", {"cards": [{"id": "p1", "note": "best ceviche"}]},
                                                  "t2")],
    ])
    monkeypatch.setattr(agent.client.beta.messages, "stream", fake.stream)
    original = convo._dispatch

    async def dispatch(name, args):
        return await find(name, args) if name == "lookup" else await original(name, args)
    monkeypatch.setattr(convo, "_dispatch", dispatch)

    async def say(s):
        pass
    asyncio.run(convo.reply("best ceviche in Miraflores", say))
    assert len(fake.requests) == 2  # no third round just to show the cards
    assert convo.cards.picked[0]["title"] == "Punto Azul" and convo.cards.picked[0]["note"] == "best ceviche"


def test_cards_reach_the_phone_before_done(monkeypatch):
    from fastapi.testclient import TestClient

    from buddy import server

    async def fake_reply(self, text, say, via="user"):
        self.turn_key = "t9"
        self.cards = cards.CardBox()
        self.cards.add(cards.news_card({"title": "Sol rises", "link": "https://reuters.com/a"}))
        self.cards.pick([{"id": "n1", "note": "today"}])
        await say("Here's the news.")
    monkeypatch.setattr(server.Conversation, "reply", fake_reply)
    with TestClient(server.app) as c, c.websocket_connect("/ws") as ws:
        ws.send_json({"type": "user", "text": "news?"})
        got = [ws.receive_json() for _ in range(4)]
    assert [m["type"] for m in got] == ["thinking", "say", "cards", "done"]
    assert got[2]["cards"][0]["url"] == "https://reuters.com/a" and got[2]["cards"][0]["note"] == "today"
