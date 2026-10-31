"""Rides: Uber and Lyft open on the phone with the trip filled in; Buddy never books."""

import asyncio
import json
import urllib.parse

import pytest

from buddy import cards, rides
from buddy.memory import Memory
from buddy.tools import ToolError

AIRPORT = {"title": "Miami International Airport", "address": "2100 NW 42nd Ave, Miami, FL",
           "latitude": 25.7959, "longitude": -80.287}


class Social:
    def __init__(self, found=(AIRPORT,)):
        self.found, self.asked = list(found), []

    def has_google(self):
        return True

    def places(self, query, country="US", num=12):
        self.asked.append(query)
        return self.found


class Here:
    def current(self):
        return {"lat": 25.76, "lng": -80.19, "area": "Brickell", "city": "Miami", "country": "US"}


def make(social=None, osm=lambda q, near: []):
    return rides.Rides(Memory(":memory:"), social or Social(), Here(), search_osm=osm)


def params(url):
    return dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))


def run(r, args):
    sent = []

    async def open_links(links):
        sent.append(links)
    box = cards.CardBox()
    cards.current.set(box)
    out = asyncio.run(rides.run_ride_tool(r, args, open_links))
    return out, sent, box


def test_uber_opens_with_the_drop_off_and_pickup_here():
    social = Social()
    out, sent, box = run(make(social), {"action": "open", "app": "uber", "to": "the airport"})
    assert social.asked == ["the airport near Brickell, Miami"]  # found near the user
    (link,) = sent[0]
    q = params(link["url"])
    assert link["url"].startswith("https://m.uber.com/ul/?") and link["app"].startswith("uber://?")
    assert q["pickup"] == "my_location" and q["dropoff[latitude]"] == "25.7959"
    assert q["dropoff[nickname]"] == "Miami International Airport"
    assert "haven't booked" in out and "Miami International Airport" in out
    assert [c["kind"] for c in box.picked] == ["ride"] and box.picked[0]["title"] == "Uber to Miami International Airport"


def test_compare_opens_uber_then_lyft_with_the_same_trip():
    out, sent, box = run(make(), {"action": "open", "app": "both", "to": "MIA", "ride_type": "xl"})
    uber, lyft = sent[0]
    assert lyft["url"].startswith("https://lyft.com/ride?") and lyft["app"].startswith("lyft://ridetype?")
    q = params(lyft["url"])
    assert q["id"] == "lyft_xl" and q["destination[longitude]"] == "-80.287" and "pickup[latitude]" not in q
    assert "Lyft opens" in out and len(box.picked) == 2


def test_saved_places_and_a_named_pickup():
    r = make()
    asyncio.run(rides.run_ride_tool(r, {"action": "save_place", "name": "Home", "to": "123 Main St"}, None))
    r.social = Social([{"title": "Dadeland Mall", "address": "7535 N Kendall Dr", "latitude": 25.69,
                        "longitude": -80.31}])
    _, sent, _ = run(r, {"action": "open", "app": "lyft", "to": "home", "from": "Dadeland Mall"})
    q = params(sent[0][0]["url"])
    assert q["destination[latitude]"] == "25.7959"  # home was saved from the first lookup
    assert q["pickup[latitude]"] == "25.69"
    assert "home" in json.loads(asyncio.run(rides.run_ride_tool(r, {"action": "places"}, None)))


def test_openstreetmap_when_google_finds_nothing_and_clear_errors():
    osm = lambda q, near: [{"title": "Kennedy Park", "address": "Coconut Grove", "latitude": 25.73,  # noqa: E731
                            "longitude": -80.23}]
    _, sent, _ = run(make(Social([]), osm), {"action": "open", "app": "uber", "to": "Kennedy Park"})
    assert params(sent[0][0]["url"])["dropoff[nickname]"] == "Kennedy Park"
    with pytest.raises(ToolError, match="couldn't find"):
        run(make(Social([])), {"action": "open", "app": "uber", "to": "Nowhere"})
    with pytest.raises(ToolError, match="isn't connected"):
        asyncio.run(rides.run_ride_tool(make(), {"action": "open", "to": "MIA"}, None))
