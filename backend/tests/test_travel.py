"""Travel: several sources, one comparison on the true total, with the catches flagged (fake APIs here)."""

import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from buddy import insights, library, travel
from buddy.memory import Memory

ENV = {"BUDDY_DUFFEL_TOKEN": "duffel_live_x", "BUDDY_SERPAPI_KEY": "serp", "BUDDY_TRAVELPAYOUTS_TOKEN": "tp"}


def seg(carrier, code, number, frm, to, dep, arr, checked=0, carry=1):
    return {"marketing_carrier": {"name": carrier, "iata_code": code}, "marketing_carrier_flight_number": number,
            "origin": {"iata_code": frm}, "destination": {"iata_code": to}, "departing_at": dep, "arriving_at": arr,
            "passengers": [{"baggages": [{"type": "checked", "quantity": checked},
                                         {"type": "carry_on", "quantity": carry}]}]}


DUFFEL = {"data": {"offers": [
    {"id": "off_basic", "total_amount": "289.00", "total_currency": "USD", "base_amount": "240.00", "tax_amount": "49.00",
     "owner": {"name": "American Airlines"}, "expires_at": "2026-12-01T10:00:00Z",
     "conditions": {"refund_before_departure": {"allowed": False}, "change_before_departure": {"allowed": False}},
     "slices": [{"origin": {"iata_code": "MIA"}, "destination": {"iata_code": "LIM"}, "duration": "PT5H40M",
                 "fare_brand_name": "Basic Economy",
                 "segments": [seg("American Airlines", "AA", "917", "MIA", "LIM", "2026-12-10T08:00:00",
                                  "2026-12-10T13:40:00", checked=0, carry=0)]}]},
    {"id": "off_main", "total_amount": "348.00", "total_currency": "USD",
     "owner": {"name": "LATAM"}, "conditions": {"refund_before_departure": {"allowed": True, "penalty_amount": "100",
                                                                          "penalty_currency": "USD"},
                                               "change_before_departure": {"allowed": True, "penalty_amount": "0"}},
     "slices": [{"origin": {"iata_code": "MIA"}, "destination": {"iata_code": "LIM"}, "duration": "PT9H10M",
                 "fare_brand_name": "Standard",
                 "segments": [seg("LATAM", "LA", "2463", "MIA", "BOG", "2026-12-10T09:00:00", "2026-12-10T12:00:00", 1),
                              seg("LATAM", "LA", "2513", "BOG", "LIM", "2026-12-10T12:40:00", "2026-12-10T18:10:00", 1)]}]},
]}}

GOOGLE_FLIGHTS = {
    "search_metadata": {"google_flights_url": "https://www.google.com/travel/flights?tfs=abc"},
    "best_flights": [{"price": 289, "total_duration": 340, "extensions": ["Checked baggage for a fee"],
                      "flights": [{"airline": "American", "flight_number": "AA 917",
                                   "departure_airport": {"id": "MIA", "time": "2026-12-10 08:00"},
                                   "arrival_airport": {"id": "LIM", "time": "2026-12-10 13:40"}}], "layovers": []}],
    "other_flights": [{"price": 199, "total_duration": 1090,
                       "flights": [{"airline": "Spirit", "flight_number": "NK 501", "often_delayed_by_over_30_min": True,
                                    "departure_airport": {"id": "MIA", "time": "2026-12-10 23:30"},
                                    "arrival_airport": {"id": "SJO", "time": "2026-12-11 01:10"}},
                                   {"airline": "JetSMART", "flight_number": "JA 88",
                                    "departure_airport": {"id": "SJO", "time": "2026-12-11 09:00"},
                                    "arrival_airport": {"id": "LIM", "time": "2026-12-11 13:40"}}],
                       "layovers": [{"id": "SJO", "duration": 470, "overnight": True}],
                       "extensions": ["Separate tickets booked together"]}],
    "price_insights": {"lowest_price": 199, "price_level": "low", "typical_price_range": [260, 420],
                       "price_history": [[1761955200, 330], [1762041600, 318], [1762128000, 305]]},
}

GOOGLE_HOTELS = {"search_metadata": {"google_hotels_url": "https://www.google.com/travel/hotels/x"}, "properties": [
    {"name": "Casa Andina Miraflores", "type": "hotel", "link": "https://casa-andina.example/booking",
     "rate_per_night": {"extracted_lowest": 138, "extracted_before_taxes_fees": 117},
     "total_rate": {"extracted_lowest": 414}, "extracted_hotel_class": 4, "overall_rating": 4.5, "reviews": 2100,
     "amenities": ["Free Wi-Fi", "Free breakfast", "Free cancellation"], "excluded_amenities": [],
     "prices": [{"source": "Booking.com", "rate_per_night": {"extracted_lowest": 138}},
                {"source": "Expedia", "rate_per_night": {"extracted_lowest": 131}}]},
    {"name": "Ocean Resort Lima", "type": "resort", "link": "https://ocean.example",
     "rate_per_night": {"extracted_lowest": 119}, "total_rate": {"extracted_lowest": 357}, "overall_rating": 3.9,
     "reviews": 300, "amenities": ["Pool"], "excluded_amenities": ["Parking ($)", "No free Wi-Fi"]},
    {"name": "Barranco Loft", "type": "vacation rental", "rate_per_night": {"extracted_lowest": 95},
     "total_rate": {"extracted_lowest": 285}, "overall_rating": 4.7, "reviews": 80, "amenities": []},
]}

TP = {"data": [{"price": 150, "airline": "AV", "departure_at": "2026-12-08T06:00:00-05:00", "transfers": 1,
                "duration": 600, "link": "/search/MIA0812LIM1?t=x"},
               {"price": 245, "airline": "LA", "departure_at": "2026-12-15T09:00:00-05:00", "transfers": 0,
                "duration": 340, "link": "/search/MIA1512LIM1?t=y"}]}


class FakeHTTP:
    def __init__(self):
        self.calls = []

    def __call__(self, method, url, headers=None, body=None, timeout=60):
        self.calls.append((method, url, headers, body))
        if "api.duffel.com/air/offer_requests" in url:
            assert headers["Authorization"] == "Bearer duffel_live_x" and headers["Duffel-Version"] == "v2"
            return DUFFEL
        if "api.duffel.com/air/offers/off_main" in url:
            return {"data": {"total_amount": "355.00", "total_currency": "USD", "expires_at": "x",
                             "available_services": [{"type": "baggage", "total_amount": "40"}]}}
        if "serpapi.com/search.json" in url and "google_flights" in url:
            return GOOGLE_FLIGHTS
        if "serpapi.com/search.json" in url and "google_hotels" in url:
            return GOOGLE_HOTELS
        if "serpapi.com/account.json" in url:
            return {"plan_name": "Free", "plan_searches_left": 240, "searches_per_month": 250}
        if "api.travelpayouts.com" in url:
            assert "token=tp" in url
            return TP
        raise AssertionError(url)


@pytest.fixture
def mem(tmp_path, monkeypatch):
    monkeypatch.setattr(library, "LIB_DIR", tmp_path / "library")
    return Memory(":memory:")


def make(mem):
    fake = FakeHTTP()
    return travel.Travel(mem, env=ENV, http=fake), fake


def test_flights_compared_on_the_true_total_with_the_catches(mem):
    t, fake = make(mem)
    t.set_profile(checked_bags=1, carry_on=True)
    result = asyncio.run(t.flights("mia", "LIM", "2026-12-10"))
    offers = result["offers"]
    by_flight = {"".join(f for leg in o["legs"] for f in leg["flights"]): o for o in offers}
    basic = by_flight["AA917"]
    assert basic["source"] == "duffel" and basic["also"][0]["source"] == "google"  # same flight, shown once
    assert basic["true_total"] == 289 + 35 + 45  # checked bag and carry-on not included: estimates added
    texts = " ".join(f["text"] for f in basic["flags"])
    assert "Basic economy" in texts and "No refund" in texts and "Changes not allowed" in texts
    latam = by_flight["LA2463LA2513"]
    assert latam["true_total"] == 348 and any("1 checked bag included" in f["text"] for f in latam["flags"])
    assert any("Refundable for a 100 USD fee" in f["text"] for f in latam["flags"])
    spirit = by_flight["NK501JA88"]
    flags = " ".join(f["text"] for f in spirit["flags"])
    assert "Separate tickets" in flags and "Overnight" in flags and "often delayed" in flags
    assert spirit["true_total"] > spirit["price"]  # low-cost: bags extra
    assert "cheapest" in min(offers, key=lambda o: o["true_total"])["tags"]
    assert offers[0]["tags"][0] == "best value"
    assert result["google_price_insight"]["level"] == "low"
    assert result["cheapest_dates"][0]["price"] == 150 and result["cheapest_dates"][0]["link"].startswith("https://www.aviasales.com/")
    assert "kayak.com/flights/MIA-LIM/2026-12-10" in result["links"]["kayak"]
    assert t.search(result["search_id"])["results"]["offers"][0]["id"] == offers[0]["id"]
    assert not result["problems"]
    advice = " ".join(result["advice"])
    assert "Google rates today's prices low" in advice
    assert "Flexible? Flying on 2026-12-08" in advice  # Travelpayouts saw it cheaper two days earlier
    serp = next(c for c in fake.calls if "google_flights" in c[1])
    assert "departure_id=MIA%2CFLL%2CPBI" in serp[1]  # nearby airports searched together


def test_a_failing_source_doesnt_stop_the_others(mem):
    t, fake = make(mem)

    def flaky(method, url, headers=None, body=None, timeout=60):
        if "duffel" in url:
            raise travel.SourceError(401, "invalid token")
        return fake(method, url, headers, body)
    t.duffel.http = flaky
    result = asyncio.run(t.flights("MIA", "LIM", "2026-12-10"))
    assert result["offers"] and result["problems"][0].startswith("duffel: 401")
    calls = {r["provider"]: r for r in mem.db.execute("SELECT provider, ok FROM api_calls")}
    assert calls["duffel"]["ok"] == 0 and calls["serpapi"]["ok"] == 1


def test_duffel_test_mode_is_said_plainly(mem):
    fake = FakeHTTP()
    t = travel.Travel(mem, env={"BUDDY_DUFFEL_TOKEN": "duffel_test_x"}, http=lambda m, u, h=None, b=None, timeout=60: DUFFEL)
    result = asyncio.run(t.flights("MIA", "LIM", "2026-12-10"))
    assert "test mode" in result["note"] and all(o["test"] for o in result["offers"])
    assert fake.calls == []


def test_hotels_flag_fees_cancellation_and_rentals(mem, monkeypatch):
    t, fake = make(mem)
    result = asyncio.run(t.hotels("Miraflores, Lima", "2026-12-10", "2026-12-13", 2, check_reviews=False))
    hotels = {o["name"]: o for o in result["offers"]}
    casa = hotels["Casa Andina Miraflores"]
    text = " ".join(f["text"] for f in casa["flags"])
    assert "Taxes and fees add 63 USD" in text and "Free cancellation" in text and "Breakfast included" in text
    assert "Cheapest seller: Expedia" in text
    resort = " ".join(f["text"] for f in hotels["Ocean Resort Lima"]["flags"])
    assert "resort fee" in resort and "Parking costs extra" in resort and "Cancellation terms not shown" in resort
    assert "cleaning fee" in " ".join(f["text"] for f in hotels["Barranco Loft"]["flags"])
    assert any("booking sites differ" in a for a in result["advice"])
    assert casa["link"] == "https://casa-andina.example/booking"
    assert {"best value", "cheapest", "best rated"} <= {t for o in result["offers"] for t in o["tags"]}


def test_reviews_mentioning_surprise_fees_are_flagged(mem, monkeypatch):
    from buddy import social
    t, fake = make(mem)
    monkeypatch.setattr(social.Social, "has_google", lambda self: True)
    monkeypatch.setattr(social.Social, "google", lambda self, q, news=False, country="US", num=10: {"results": [
        {"title": "Ocean Resort Lima reviews", "snippet": "Ocean Resort was fine but they charged a $35 resort fee per night at check-in."}]})
    result = asyncio.run(t.hotels("Miraflores, Lima", "2026-12-10", "2026-12-13", 2))
    ocean = next(o for o in result["offers"] if o["name"] == "Ocean Resort Lima")
    assert any("Reviews mention" in f["text"] and "resort fee" in f["text"] for f in ocean["flags"])
    assert mem.db.execute("SELECT COUNT(*) FROM api_calls WHERE provider = 'serper'").fetchone()[0] >= 1


def test_price_history_says_below_the_usual(mem):
    t, fake = make(mem)
    for price in (400, 420, 380, 410):
        mem.db.execute("INSERT INTO travel_prices (route, day, source, price, currency, seen_at) VALUES "
                       "('MIA-LIM', '2026-12-10', 'best', ?, 'USD', '2026-10-01T00:00:00+00:00')", (price,))
    mem.db.execute("UPDATE travel_prices SET seen_at = datetime('now')")
    result = asyncio.run(t.flights("MIA", "LIM", "2026-12-10"))
    assert result["history"]["usual"] == 405 and result["history"]["vs_usual_percent"] < 0


def test_refresh_rechecks_a_duffel_price(mem):
    t, fake = make(mem)
    result = asyncio.run(t.flights("MIA", "LIM", "2026-12-10"))
    got = asyncio.run(t.refresh(result["search_id"], "off_main"))
    assert got["changed"] and got["price"] == 355 and got["was"] == 348 and got["bag_price"] == 40


def test_bad_input_is_explained(mem):
    t, _ = make(mem)
    with pytest.raises(Exception, match="airport code"):
        asyncio.run(t.flights("Miami", "LIM", "2026-12-10"))
    with pytest.raises(Exception, match="date"):
        asyncio.run(t.flights("MIA", "LIM", "10/12"))
    with pytest.raises(Exception, match="Check-out"):
        asyncio.run(t.hotels("Lima", "2026-12-10", "2026-12-09"))
    with pytest.raises(Exception, match="No travel source"):
        asyncio.run(travel.Travel(mem, env={}).flights("MIA", "LIM", "2026-12-10"))


def test_tool_answer_and_insights_costs(mem, monkeypatch):
    t, fake = make(mem)
    monkeypatch.setenv("BUDDY_SERPAPI_COST_PER_SEARCH", "0.025")
    out = json.loads(asyncio.run(travel.run_tool(t, {"action": "flights", "origin": "MIA", "destination": "LIM",
                                                     "depart": "2026-12-10"})))
    assert out["offers"] and "Travel screen" in out["app"] and "flags" in out["offers"][0]
    report = insights.report(mem, days=1)
    serp = next(p for p in report["apis"]["providers"] if p["provider"] == "serpapi")
    assert serp["calls"] == 1 and serp["cost_usd"] == 0.025 and serp["endpoints"] == {"google_flights": 1}


def test_travel_endpoints(monkeypatch):
    from buddy import server
    fake = FakeHTTP()
    real = travel.Travel.__init__

    def init(self, memory, env=None, http=None):
        real(self, memory, env=ENV, http=fake)
    monkeypatch.setattr(travel.Travel, "__init__", init)
    with TestClient(server.app) as c:
        assert c.post("/api/travel/profile", json={"checked_bags": 1}).json()["checked_bags"] == 1
        found = c.post("/api/travel/search", json={"kind": "flights", "origin": "MIA", "destination": "LIM",
                                                   "depart": "2026-12-10"}).json()
        assert found["kind"] == "flights" and found["results"]["offers"]
        assert c.post("/api/travel/search", json={"origin": "Miami"}).status_code == 400
        overview = c.get("/api/travel").json()
        assert overview["sources"]["duffel"] and overview["searches"][0]["id"] == found["id"]
        assert c.get(f"/api/travel/searches/{found['id']}").json()["title"].startswith("MIA → LIM")
        hotels = c.post("/api/travel/search", json={"kind": "hotels", "place": "Lima", "check_in": "2026-12-10",
                                                    "check_out": "2026-12-12"}).json()
        assert hotels["results"]["offers"][0]["kind"] == "hotel"


def test_buddy_learns_what_you_pay_for(mem):
    t, fake = make(mem)
    t.set_profile(checked_bags=1)
    for _ in range(2):
        result = asyncio.run(t.flights("MIA", "LIM", "2026-12-10"))
        nonstop = next(o for o in result["offers"] if o["stops"] == 0)
        got = t.chose(result["search_id"], nonstop["id"])
        assert any("per hour saved" in x for x in got["learned"])
    learned = t.learned()
    assert learned["choices"] == 2 and learned["hour_value"] and learned["nonstop_share"] == 1.0
    assert "American Airlines" in learned["airlines"]
    again = asyncio.run(t.flights("MIA", "LIM", "2026-12-10"))
    assert any("you usually pay about" in a for a in again["advice"])
    row = mem.db.execute("SELECT topic FROM decisions ORDER BY id DESC LIMIT 1").fetchone()
    assert row["topic"] == "flights"


def test_every_price_and_googles_history_are_kept(mem):
    t, fake = make(mem)
    asyncio.run(t.flights("MIA", "LIM", "2026-12-10"))
    sources = {r["source"] for r in mem.db.execute("SELECT source FROM travel_prices WHERE route = 'MIA-LIM'")}
    assert any(s.startswith("duffel:") for s in sources) and "best" in sources
    # Google's history points are a year old: already rolled into patterns, raw rows gone (90 days)
    assert mem.db.execute("SELECT COUNT(*) FROM travel_prices WHERE source = 'google_history'").fetchone()[0] == 0
    assert mem.db.execute("SELECT SUM(n) FROM travel_patterns WHERE route = 'MIA-LIM'").fetchone()[0] == 3


def test_patterns_answer_good_price_when_to_book_and_cheaper_days(mem):
    from datetime import date, datetime, timedelta, timezone
    t, fake = make(mem)
    now = datetime.now(timezone.utc)
    rows = []
    for week in range(30):  # 30 weeks of old prices: cheap on Tuesdays and when booked 22-60 days ahead
        travel_day = date(2026, 12, 1) + timedelta(days=week % 14)
        for lead, price in ((10, 420), (40, 300), (90, 360)):
            seen = datetime.combine(travel_day - timedelta(days=lead), datetime.min.time(), timezone.utc)
            p = price * (0.85 if travel_day.weekday() == 1 else 1.0)
            rows.append(("MIA-LIM", travel_day.isoformat(), "google:x", p, "USD", (min(seen, now - timedelta(days=120))).isoformat()))
    mem.db.executemany("INSERT INTO travel_prices (route, day, source, price, currency, seen_at) VALUES (?,?,?,?,?,?)", rows)
    mem.db.commit()
    assert t.roll_up() == 90 and mem.db.execute("SELECT COUNT(*) FROM travel_prices").fetchone()[0] == 0
    p = t.pattern("MIA-LIM", "2026-12-10", "USD", 290)
    assert p["seen"] == 90 and p["cheaper_than_percent"] >= 70 and p["for"] == "December"
    assert p["by_weekday"]["Tuesdays"] < p["by_weekday"]["Fridays"]
    advice = " ".join(travel.pattern_advice(p, 290, "USD", "2026-12-10"))
    assert "a good price" in advice and "Tuesdays have been" in advice
    assert mem.db.execute("SELECT COUNT(*) FROM travel_patterns").fetchone()[0] <= 5 * 7  # tiny: no raw rows kept
    out = json.loads(asyncio.run(travel.run_tool(t, {"action": "price_pattern", "origin": "MIA", "destination": "LIM",
                                                     "depart": "2026-12-10"})))
    assert out["seen"] == 90


def test_travelpayouts_fares_feed_the_patterns(mem):
    t, fake = make(mem)
    asyncio.run(t.flights("MIA", "LIM", "2026-12-10"))
    rows = {(r["day"], r["price"]) for r in mem.db.execute(
        "SELECT day, price FROM travel_prices WHERE source = 'travelpayouts'")}
    assert ("2026-12-08", 150.0) in rows and ("2026-12-15", 245.0) in rows  # each on its own travel date


def test_route_watching_alerts_once_per_drop(mem):
    t, fake = make(mem)
    w = t.watch("mia", "lim", "2026-12", target=200)
    assert w["origin"] == "MIA" and w["target"] == 200
    alerts = t.check_watches()
    assert len(alerts) == 1 and "150 USD" in alerts[0]["text"] and "target" in alerts[0]["text"]
    assert alerts[0]["link"].startswith("https://www.aviasales.com/")
    t.told(alerts[0])
    assert t.check_watches() == []  # same fare: not said again
    TP["data"][0]["price"] = 120  # a further drop is news
    try:
        again = t.check_watches()
        assert again and "a new low (was 150)" in again[0]["text"]
    finally:
        TP["data"][0]["price"] = 150
    watched = t.watches()[0]
    assert watched["best"] == 120 and watched["best_day"] == "2026-12-08"
    assert mem.db.execute("SELECT COUNT(*) FROM travel_prices WHERE source = 'travelpayouts'").fetchone()[0] >= 4
    assert "Stopped watching MIA → LIM" in t.unwatch("MIA-LIM") and t.watches() == []


def test_past_months_stop_being_watched_and_bad_input(mem):
    t, fake = make(mem)
    mem.db.execute("INSERT INTO travel_watches (origin, destination, month, currency, created_at) VALUES "
                   "('MIA', 'LIM', '2020-01', 'USD', '2020-01-01')")
    mem.db.commit()
    t.check_watches()
    assert t.watches() == []
    with pytest.raises(Exception, match="YYYY-MM"):
        t.watch("MIA", "LIM", "December")
    with pytest.raises(Exception, match="Travelpayouts"):
        travel.Travel(mem, env={}).watch("MIA", "LIM", "2026-12")


def test_watch_endpoints_and_tool(monkeypatch):
    from buddy import server
    fake = FakeHTTP()
    real = travel.Travel.__init__

    def init(self, memory, env=None, http=None):
        real(self, memory, env=ENV, http=fake)
    monkeypatch.setattr(travel.Travel, "__init__", init)
    with TestClient(server.app) as c:
        w = c.post("/api/travel/watches", json={"origin": "MIA", "destination": "LIM", "month": "2026-12",
                                                 "target": 250}).json()
        assert any(x["id"] == w["id"] for x in c.get("/api/travel/watches").json())
        assert "watches" in c.get("/api/travel").json()
        assert c.post("/api/travel/watches", json={"origin": "MIA", "destination": "LIM", "month": "Dec"}).status_code == 400
        assert "Stopped" in c.delete(f"/api/travel/watches/{w['id']}").json()["result"]
    out = asyncio.run(travel.run_tool(travel.Travel(server.memory), {"action": "watch", "origin": "MIA",
                                                                    "destination": "LIM", "month": "2026-12"}))
    assert "Checked every morning" in out
    travel.Travel(server.memory).unwatch("MIA-LIM")
