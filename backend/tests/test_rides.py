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


UBER_SCREEN = [  # text on screen with where it is (top y, bottom b, left x), like Uber's "Choose a ride"
    {"t": "Choose a ride", "x": 40, "y": 900, "r": 600, "b": 960},
    {"t": "UberX", "x": 220, "y": 1000, "r": 400, "b": 1040}, {"t": "4 min away · 9:42 PM", "x": 220, "y": 1045, "r": 600, "b": 1080},
    {"t": "$23.40", "x": 900, "y": 1010, "r": 1040, "b": 1050}, {"t": "$27.00", "x": 900, "y": 1052, "r": 1040, "b": 1085},
    {"t": "Comfort", "x": 220, "y": 1150, "r": 400, "b": 1190}, {"t": "3 min away", "x": 220, "y": 1195, "r": 500, "b": 1230},
    {"t": "$29.10", "x": 900, "y": 1160, "r": 1040, "b": 1200},
    {"t": "UberXL", "x": 220, "y": 1300, "r": 400, "b": 1340}, {"t": "6 min away", "x": 220, "y": 1345, "r": 500, "b": 1380},
    {"t": "$35.80", "x": 900, "y": 1310, "r": 1040, "b": 1350},
    {"t": "Visa •••• 4242", "x": 40, "y": 1700, "r": 500, "b": 1740}, {"t": "Choose UberX", "x": 40, "y": 1800, "r": 1000, "b": 1880},
]
LYFT_SCREEN = [  # Lyft reads each row as one piece of text
    {"t": "Lyft, 6 min away, $21.90", "x": 40, "y": 1000, "r": 1040, "b": 1100},
    {"t": "Lyft XL, 8 min away, $33.20", "x": 40, "y": 1120, "r": 1040, "b": 1220},
    {"t": "Request Lyft", "x": 40, "y": 1800, "r": 1000, "b": 1880},
]


def test_prices_are_read_off_uber_and_lyft_screens():
    uber = rides.parse_ride_screen(UBER_SCREEN)
    assert [(o["ride"], o["price"], o["eta_min"]) for o in uber] == [
        ("UberX", "$23.40", 4), ("Comfort", "$29.10", 3), ("UberXL", "$35.80", 6)]  # the crossed-out $27 is dropped
    lyft = rides.parse_ride_screen(LYFT_SCREEN)
    assert [(o["ride"], o["value"], o["eta_min"]) for o in lyft] == [("Lyft", 21.9, 6), ("Lyft XL", 33.2, 8)]
    assert rides.parse_ride_screen([{"t": "Where to?", "x": 0, "y": 0, "b": 10}]) == []


def test_compare_reads_both_apps_then_says_the_cheaper():
    r = make()
    sent = []

    async def open_links(links, compare=""):
        sent.append((links, compare))
    box = cards.CardBox()
    cards.current.set(box)
    out = asyncio.run(rides.run_ride_tool(r, {"action": "compare", "to": "the airport"}, open_links))
    links, compare_id = sent[0]
    assert compare_id and [l["label"].split()[0] for l in links] == ["Uber", "Lyft"] and "don't guess" in out
    mem = r.memory
    assert rides.take_screen(mem, {"compare": compare_id, "app": "Uber", "nodes": UBER_SCREEN}) == (None, [])
    assert rides.take_screen(mem, {"compare": "old", "app": "Lyft", "nodes": LYFT_SCREEN, "last": True}) == (None, [])
    say, shown = rides.take_screen(mem, {"compare": compare_id, "app": "Lyft", "nodes": LYFT_SCREEN, "last": True})
    assert say == ("Lyft is cheaper by 1.50: Lyft at $21.90, 6 min away. Uber's cheapest is UberX at $23.40, "
                   "4 min away. Want me to open Lyft?")
    assert [c["title"] for c in shown] == ["Lyft to Miami International Airport", "Uber to Miami International Airport"]
    assert shown[1]["rows"][1] == {"label": "Comfort", "value": "$29.10 · 3 min"} and shown[0]["app_url"].startswith("lyft:")
    assert '"Lyft XL"' in asyncio.run(rides.run_ride_tool(r, {"action": "prices"}, None))


def test_compare_explains_a_missed_app_and_a_switched_off_reader():
    r = make()
    sent = []

    async def open_links(links, compare=""):
        sent.append(compare)
    asyncio.run(rides.run_ride_tool(r, {"action": "compare", "to": "MIA"}, open_links))
    rides.take_screen(r.memory, {"compare": sent[0], "app": "Uber", "nodes": UBER_SCREEN})
    say, _ = rides.take_screen(r.memory, {"compare": sent[0], "app": "Lyft", "nodes": [], "last": True})
    assert say == "Uber's cheapest is UberX at $23.40, 4 min away. I couldn't read Lyft's prices."
    say, _ = rides.take_screen(r.memory, {"compare": sent[0], "error": "reader_off", "last": True})
    assert "Accessibility" in say
