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


def note(app, title, text):
    return {"app": app, "title": title, "text": text}


def test_a_ride_is_followed_through_its_notifications():
    fares = []
    f = rides.RideFollower(Memory(":memory:"), record_fare=lambda *a: fares.append(a))
    uber = "com.ubercab"
    assert f.update(note(uber, "Looking for a driver", "Finding you a ride"))[0] is None  # not spoken
    say, card = f.update(note(uber, "Carlos is on the way", "Toyota Camry · ABC1234 · 4 min away"))
    assert say == "Your Uber's on the way: Carlos, Toyota Camry, plate ABC1234, 4 minutes away."
    assert card["title"] == "Uber · driver on the way" and card["subtitle"] == "Carlos · Toyota Camry · ABC1234"
    assert f.update(note(uber, "Carlos is on the way", "Toyota Camry · ABC1234 · 3 min away"))[0] is None  # same stage
    assert f.active()["eta_min"] == 3
    say, _ = f.update(note(uber, "Carlos has arrived", "Meet Carlos at pickup"))
    assert say == "Your Uber is here: Toyota Camry, plate ABC1234."
    f.update(note(uber, "Carlos is on the way", "2 min away"))  # late and out of order: no step back
    assert f.active()["stage"] == "arrived"
    assert f.update(note(uber, "Heading to Miami International Airport", "Arriving at 6:42 PM"))[0] is None
    say, card = f.update(note(uber, "Thanks for riding, Aneesh", "Your trip total was $23.45"))
    assert say == "Uber trip done: 23.45 USD." and card["price"] == "23.45 USD"
    assert fares == [("Uber", 23.45, "USD")]
    f.update(note(uber, "Rate your trip", "Your receipt: $23.45"))
    assert len(fares) == 1  # counted once


def test_promotions_are_ignored_and_status_explains_itself():
    mem = Memory(":memory:")
    f = rides.RideFollower(mem)
    assert f.update(note("me.lyft.android", "50% off your next 3 rides", "Use code RIDE50")) == (None, None)
    assert "Notification access" in asyncio.run(rides.run_ride_tool(rides.Rides(mem, Social(), Here()),
                                                                    {"action": "status"}, None))
    say, _ = f.update(note("me.lyft.android", "Lyft", "Your driver Maria is 5 min away in a silver Honda Civic (7XYZ123)"))
    assert say == "Your Lyft's on the way: Maria, silver Honda Civic, plate 7XYZ123, 5 minutes away."
    out = asyncio.run(rides.run_ride_tool(rides.Rides(mem, Social(), Here()), {"action": "status"}, None))
    assert out.startswith("Your Lyft's on the way") and '"plate": "7XYZ123"' in out
    assert not rides.RideFollower.is_ride({"app": "com.ubercab.eats"})
