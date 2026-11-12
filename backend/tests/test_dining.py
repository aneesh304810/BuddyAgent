"""Restaurants: found by cuisine in the owner's neighbourhood (never their position), booked through links with the
details filled in or by a reservation call after a yes."""

import json
import urllib.parse

import pytest

from buddy import calls, dining, gateway
from buddy.memory import Memory
from buddy.tools import ToolError

ENV = {"BUDDY_GOOGLE_PLACES_KEY": "k"}
PLACES = {"places": [
    {"id": "p1", "displayName": {"text": "Via Carota"}, "formattedAddress": "51 Grove St, New York, NY 10014, USA",
     "rating": 4.6, "userRatingCount": 3100, "priceLevel": "PRICE_LEVEL_EXPENSIVE",
     "primaryTypeDisplayName": {"text": "Italian Restaurant"}, "currentOpeningHours": {"openNow": True},
     "websiteUri": "https://viacarota.com", "internationalPhoneNumber": "+1 212-255-1962",
     "googleMapsUri": "https://maps.google.com/?cid=1", "reservable": False},
    {"id": "p2", "displayName": {"text": "L'Artusi"}, "formattedAddress": "228 W 10th St, New York, NY 10014, USA",
     "rating": 4.7, "userRatingCount": 2500, "priceLevel": "PRICE_LEVEL_EXPENSIVE", "reservable": True,
     "internationalPhoneNumber": "+1 212-255-5757"}]}


@pytest.fixture(autouse=True)
def _fresh_limits():
    gateway._recent.clear()


def test_search_uses_the_neighbourhood_never_the_position(tmp_path, monkeypatch):
    m = Memory(tmp_path / "b.db")
    monkeypatch.setattr(dining, "area", lambda memory: "West Village, New York")
    sent = []

    def fetch(url, data=None, headers=None):
        sent.append((url, json.loads(data), headers))
        return json.dumps(PLACES).encode()
    got = dining.search(m, "italian", env=ENV, fetch=fetch)
    url, body, headers = sent[0]
    assert url == "https://places.googleapis.com/v1/places:searchText"
    assert body["textQuery"] == "italian in West Village, New York" and "locationBias" not in body
    assert headers["X-Goog-Api-Key"] == "k" and "places.reservable" in headers["X-Goog-FieldMask"]
    first = got["places"][0]
    assert first["name"] == "Via Carota" and first["price"] == "$$$" and first["open_now"] is True
    assert got["places"][1]["takes_reservations"] is True and got["source"] == "Google Places"


def test_book_gives_links_with_the_details_filled_in(tmp_path, monkeypatch):
    m = Memory(tmp_path / "b.db")
    monkeypatch.setattr(dining, "area", lambda memory: "West Village, New York")
    dining.search(m, "italian", env=ENV, fetch=lambda u, d=None, h=None: json.dumps(PLACES).encode())
    got = dining.book(m, "2", 4, "2030-05-02T19:30")
    assert got["restaurant"] == "L'Artusi" and got["party"] == 4 and got["phone"] == "+1 212-255-5757"
    ot = urllib.parse.parse_qs(urllib.parse.urlparse(got["links"]["opentable"]).query)
    assert ot["covers"] == ["4"] and ot["dateTime"] == ["2030-05-02T19:30"] and "L'Artusi" in ot["term"][0]
    assert "phone_call with kind reservation" in got["next"]
    assert dining.book(m, "via carota", 2, "2030-05-02T19:30")["links"]["restaurant_site"] == "https://viacarota.com"
    with pytest.raises(ToolError):
        dining.book(m, "2", 2, "tomorrow at 7")
    with pytest.raises(ToolError):
        dining.book(m, "Nobu", 2, "2030-05-02T19:30")  # not in the last search


def test_reservation_calls_share_only_name_and_callback(tmp_path, monkeypatch):
    m = Memory(tmp_path / "b.db")
    c = calls.plan(m, "L'Artusi", "+12122555757", "reservation", "a table for 4 on Friday at 7:30 pm, under Sam")
    prompt = calls.system_prompt(m, c, {"BUDDY_OWNER_FULL_NAME": "Sam Rivera", "BUDDY_OWNER_PHONE": "+12015550199",
                                        "BUDDY_OWNER_DOB": "January 2, 1990", "BUDDY_OWNER_INSURANCE": "Aetna W1"})
    assert "Book the table" in prompt and "never give one" in prompt
    assert "January 2, 1990" not in prompt and "Aetna" not in prompt  # appointment details stay out


def test_google_places_is_in_buddy_connect():
    e = gateway.CATALOG["google_places"]
    assert e["group"] == "Food & dining" and e["privacy"] == "public" and "Food & dining" in gateway.GROUPS
    assert gateway.setup_hint("google_places") == "sudo bash /opt/buddy/deploy/set.sh BUDDY_GOOGLE_PLACES_KEY"
