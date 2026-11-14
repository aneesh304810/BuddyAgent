"""Check direct: the hotel's own price against booking sites, the call, the owner's notes and the record."""

import json

import pytest

from buddy import hoteldirect as hd
from buddy.memory import Memory
from buddy.tools import ToolError


def offer(name, sellers, nights=2):
    return {"name": name, "nights": nights, "currency": "USD",
            "sellers": [{"source": s, "per_night": p} for s, p in sellers]}


@pytest.fixture
def memory(tmp_path):
    return Memory(tmp_path / "h.db")


def test_chains_and_direct_seller():
    assert hd.chain_of("Courtyard by Marriott Lima Miraflores") == "Marriott"
    assert hd.chain_of("Hampton Inn & Suites Miami") == "Hilton" and hd.chain_of("Casa Andina Premium") == ""


def test_direct_same_or_cheaper_or_close_means_book_direct(memory):
    same = hd.compare(memory, offer("Courtyard by Marriott Lima", [("Booking.com", 150), ("Marriott.com", 150)]), "Lima")
    assert same["verdict"] == "book direct" and same["direct_per_night"] == 150 and same["best_site"] == "Booking.com"
    assert "Marriott Bonvoy" in same["why"] and "site%3Amarriott.com" in same["check_direct_link"]
    close = hd.compare(memory, offer("Hilton Lima Miraflores", [("Expedia", 200), ("Hilton", 208)]))
    assert close["verdict"] == "book direct" and "only USD 16 more" in close["why"]


def test_big_gap_says_site_unless_member_rate_and_rule_can_change(memory):
    far = hd.compare(memory, offer("Hyatt Centric Lima", [("Agoda", 160), ("Hyatt.com", 200)]))
    assert far["verdict"].startswith("booking site") and "best-rate guarantee" in far["why"]
    assert "within 30%" in hd.run_tool(memory, {"action": "rule", "within_pct": 30})
    assert hd.compare(memory, offer("Hyatt Centric Lima", [("Agoda", 160), ("Hyatt.com", 200)]))["verdict"] == "book direct"


def test_no_direct_price_says_check_and_independent_hotels(memory):
    none = hd.compare(memory, offer("Sheraton Lima", [("Booking.com", 140), ("Expedia", 150)]))
    assert none["verdict"] == "check direct" and "Marriott Bonvoy member" in none["why"] and none["best_site"] == "Booking.com"
    indie = hd.compare(memory, offer("Casa Andina Premium Miraflores", [("Booking.com", 120),
                                                                        ("Casa Andina Official Site", 118)]))
    assert indie["chain"] == "" and indie["verdict"] == "book direct" and indie["direct_per_night"] == 118


def test_record_notes_and_stats(memory):
    for name, site, direct in (("Courtyard Lima", 150, 150), ("Sheraton Cusco", 180, 171), ("Westin Lima", 220, 260)):
        c = hd.compare(memory, offer(name, [("Booking.com", site), ("Marriott.com", direct)]))
        hd.record(memory, c, "Peru")
        hd.record(memory, c, "Peru")  # once a day per hotel
    s = hd.stats(memory)["Marriott"]
    assert s["checks"] == 3 and s["direct_wins_pct"] == 67
    said = hd.run_tool(memory, {"action": "note", "hotel": "Westin Lima", "direct": 205})
    assert "15 less than Booking.com" in said  # the booking-site price from the last search
    again = hd.compare(memory, offer("JW Marriott Lima", [("Expedia", 300), ("Marriott.com", 300)]))
    assert "in your 4 Marriott checks" in again["your_record"]
    assert "Marriott" in json.loads(hd.run_tool(memory, {"action": "stats"}))
    with pytest.raises(ToolError):
        hd.note(memory, "", 0)
