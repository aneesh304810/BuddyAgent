"""Entertainment: Ticketmaster events with their ticket pages as cards to tap, movie showtime links (AMC, Fandango),
only the city sent out, and keys never kept in the call log."""

import json
import urllib.parse

import pytest

from buddy import cards, dining, events, gateway
from buddy.memory import Memory
from buddy.tools import ToolError

ENV = {"BUDDY_TICKETMASTER_KEY": "tm-secret"}
TM = {"_embedded": {"events": [
    {"name": "Hamilton", "url": "https://www.ticketmaster.com/event/abc",
     "dates": {"start": {"localDate": "2030-05-02", "localTime": "19:00:00"}},
     "priceRanges": [{"min": 89, "max": 349, "currency": "USD"}],
     "classifications": [{"segment": {"name": "Arts & Theatre"}}],
     "_embedded": {"venues": [{"name": "Richard Rodgers Theatre", "city": {"name": "New York"}}]}}]}}


@pytest.fixture(autouse=True)
def _fresh_limits():
    gateway._recent.clear()


def test_events_send_only_the_city_and_keep_the_ticket_page(tmp_path, monkeypatch):
    m = Memory(tmp_path / "b.db")
    monkeypatch.setattr(dining, "area", lambda memory: "West Village, New York")
    sent = []

    def fetch(url, data=None, headers=None):
        sent.append(url)
        return json.dumps(TM).encode()
    got = events.search_events(m, "hamilton", env=ENV, fetch=fetch)
    q = urllib.parse.parse_qs(urllib.parse.urlparse(sent[0]).query)
    assert q["city"] == ["New York"] and q["keyword"] == ["hamilton"] and "latlong" not in q
    ev = got["events"][0]
    assert ev["venue"] == "Richard Rodgers Theatre" and ev["time"] == "19:00" and ev["price"] == "USD 89–349"
    logged = [r["endpoint"] for r in m.db.execute("SELECT endpoint FROM api_calls")]
    assert logged and "tm-secret" not in " ".join(logged)  # the key never lands in the call log


def test_tickets_become_a_card_the_owner_taps_to_pay(tmp_path, monkeypatch):
    m = Memory(tmp_path / "b.db")
    monkeypatch.setattr(dining, "area", lambda memory: "New York")
    events.search_events(m, "hamilton", env=ENV, fetch=lambda u, d=None, h=None: json.dumps(TM).encode())
    box = cards.CardBox()
    token = cards.current.set(box)
    try:
        got = events.tickets(m, "1")
    finally:
        cards.current.reset(token)
    assert got["cards"] and box.cards[got["cards"][0]]["url"] == "https://www.ticketmaster.com/event/abc"
    assert "Never buy" in got["next"] and "stubhub" in got["links"]
    with pytest.raises(ToolError):
        events.tickets(m, "Wicked")


def test_movies_give_showtime_links_for_amc_and_fandango(tmp_path, monkeypatch):
    m = Memory(tmp_path / "b.db")
    monkeypatch.setattr(dining, "area", lambda memory: "Union City, Weehawken")
    got = events.movies(m, "Dune Part Three", day="Saturday")
    assert "Dune+Part+Three+showtimes+Weehawken+Saturday" in got["links"]["showtimes"]
    assert "AMC" in urllib.parse.unquote_plus(got["links"]["amc"]) and "Fandango" in got["links"]["fandango"]


def test_without_a_key_events_say_how_to_set_it_up(tmp_path):
    got = events.search_events(Memory(tmp_path / "b.db"), "jazz", where="Boston", env={})
    assert "BUDDY_TICKETMASTER_KEY" in got["note"] and got["events"] == []


def test_keys_in_addresses_are_redacted():
    assert gateway._redact("/x.json?apikey=abc&size=1") == "/x.json?apikey=~&size=1"
    assert gateway._redact("/product?key=SECRET&asin=B0") == "/product?key=~&asin=B0"


def test_restaurant_booking_links_are_cards(tmp_path, monkeypatch):
    m = Memory(tmp_path / "b.db")
    m.set(dining.LAST_KEY, json.dumps([{"name": "L'Artusi", "address": "228 W 10th St, New York, NY 10014, USA",
                                        "website": "https://lartusi.com", "maps": "https://maps.google.com/?cid=2"}]))
    box = cards.CardBox()
    token = cards.current.set(box)
    try:
        got = dining.book(m, "1", 2, "2030-05-02T19:30")
    finally:
        cards.current.reset(token)
    titles = [box.cards[c]["title"] for c in got["cards"]]
    assert titles[0] == "Book L'Artusi on OpenTable" and len(titles) == 3
