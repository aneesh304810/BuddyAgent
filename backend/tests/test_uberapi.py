"""Uber Guest Rides API: real fares, booking only after a yes, the trip followed to the end.
Responses are shaped like the examples in Uber's Guest Rides Postman collection."""

import asyncio
import json

import httpx
import pytest

from buddy import rides, uberapi
from buddy.memory import Memory
from tests.test_rides import Here, Social

KEYS = {"BUDDY_UBER_CLIENT_ID": "id", "BUDDY_UBER_CLIENT_SECRET": "secret", "BUDDY_UBER_ORG_UUID": "org-1",
        "BUDDY_UBER_PHONE": "+13055550100"}
ESTIMATES = {"product_estimates": [
    {"product": {"product_id": "comfort", "display_name": "Comfort", "capacity": 4},
     "estimate_info": {"pickup_estimate": 3, "fare": {"fare_id": "f2", "value": 29.1, "currency_code": "USD",
                                                       "display": "$29.10"}}},
    {"product": {"product_id": "uberx", "display_name": "UberX", "capacity": 4},
     "estimate_info": {"pickup_estimate": 5, "fare": {"fare_id": "f1", "value": 23.4, "currency_code": "USD",
                                                       "display": "$23.40"}}}]}


class FakeUber:
    def __init__(self, statuses=("accepted", "arriving", "completed")):
        self.calls, self.statuses = [], list(statuses)

    def handler(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content or b"{}") if request.headers.get("content-type", "").startswith(
            "application/json") else request.content.decode()
        self.calls.append((request.method, request.url.path, body, dict(request.headers)))
        if request.url.path == "/oauth/v2/token":
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 2592000, "scope": "guests.trips"})
        if request.url.path.endswith("/trips/estimates"):
            return httpx.Response(200, json=ESTIMATES)
        if request.method == "POST" and request.url.path.endswith("/trips"):
            return httpx.Response(200, json={"request_id": "r1", "status": "processing"})
        if request.method == "GET" and request.url.path.endswith("/trips/r1"):
            status = self.statuses.pop(0) if len(self.statuses) > 1 else self.statuses[0]
            trip = {"request_id": "r1", "status": status, "driver": {"name": "Carlos"},
                    "vehicle": {"make": "Toyota", "model": "Prius", "license_plate": "7PXK191",
                                "vehicle_color_name": "Gray"}}
            if status == "completed":
                trip["client_fare"] = "$23.40"
            return httpx.Response(200, json=trip)
        return httpx.Response(404, json={"message": "not found"})

    def client(self):
        return httpx.AsyncClient(transport=httpx.MockTransport(self.handler))


class Thrift:
    def __init__(self):
        self.asked = []

    def ask_action(self, question, run):
        self.asked.append((question, run))
        return "APPROVAL NEEDED: " + question


@pytest.fixture
def uber(monkeypatch):
    for k, v in KEYS.items():
        monkeypatch.setenv(k, v)
    uberapi._token.clear()
    fake = FakeUber()
    real_call = uberapi._call
    monkeypatch.setattr(uberapi, "_call", lambda m, p, b=None, http=None: real_call(m, p, b, fake.client))
    return fake


def test_off_without_keys_points_to_the_apps(monkeypatch):
    for k in KEYS:
        monkeypatch.delenv(k, raising=False)
    out = asyncio.run(rides.run_ride_tool(rides.Rides(Memory(":memory:"), Social(), Here()),
                                          {"action": "prices", "to": "MIA"}, None))
    assert "isn't connected" in out and "app both" in out


def test_prices_then_book_only_after_yes_then_follow_the_trip(uber, monkeypatch):
    mem = Memory(":memory:")
    r = rides.Rides(mem, Social(), Here())
    out = asyncio.run(rides.run_ride_tool(r, {"action": "prices", "to": "the airport"}, None))
    assert out.index("UberX") < out.index("Comfort") and "$23.40" in out  # cheapest first
    token_call, estimate_call = uber.calls[0], uber.calls[1]
    assert "scope=guests.trips" in token_call[2] and "grant_type=client_credentials" in token_call[2]
    assert estimate_call[3]["x-uber-organizationuuid"] == "org-1" and estimate_call[3]["authorization"] == "Bearer tok"
    assert estimate_call[2]["dropoff"] == {"latitude": 25.7959, "longitude": -80.287}

    thrift = Thrift()
    out = asyncio.run(rides.run_ride_tool(r, {"action": "book", "ride": "uberx"}, None, thrift))
    assert out.startswith("APPROVAL NEEDED") and not any(c[1].endswith("/trips") for c in uber.calls)  # not yet
    question, go = thrift.asked[0]
    assert question == ("Book an UberX to Miami International Airport for $23.40, about 5 minutes away? "
                        "It goes on your Uber for Business card.")
    said, fares = [], []

    async def notify(kind, text, **extra):
        said.append(text)
    monkeypatch.setattr(uberapi, "notify", notify)
    real_watch = uberapi.watch
    started = []
    monkeypatch.setattr(uberapi, "watch", lambda *a, **k: started.append(a) or asyncio.sleep(0))

    async def yes():
        return await go()
    assert asyncio.run(yes()).startswith("Booked: UberX")
    booked = [c for c in uber.calls if c[0] == "POST" and c[1].endswith("/trips")][0][2]
    assert booked["product_id"] == "uberx" and booked["fare_id"] == "f1"
    assert booked["guest"]["phone_number"] == "+13055550100"
    assert uberapi.active_trip(mem)["request_id"] == "r1" and started

    asyncio.run(real_watch(mem, "r1", lambda *a: fares.append(a), every=0))
    assert said == ["Your Uber's on the way: Carlos, Gray Toyota Prius, plate 7PXK191.",
                    "Your Uber is here: Gray Toyota Prius, plate 7PXK191.", "Uber trip done: 23.40 USD."]
    assert fares == [("Uber", 23.4, "USD")] and uberapi.active_trip(mem) is None


def test_booking_needs_fresh_prices_and_a_known_ride(uber):
    r = rides.Rides(Memory(":memory:"), Social(), Here())
    with pytest.raises(rides.ToolError, match="fresh prices"):
        asyncio.run(rides.run_ride_tool(r, {"action": "book"}, None, Thrift()))
    asyncio.run(rides.run_ride_tool(r, {"action": "prices", "to": "MIA"}, None))
    with pytest.raises(rides.ToolError, match="Which one"):
        asyncio.run(rides.run_ride_tool(r, {"action": "book", "ride": "helicopter"}, None, Thrift()))


def test_missing_scope_is_explained(monkeypatch):
    for k, v in KEYS.items():
        monkeypatch.setenv(k, v)
    uberapi._token.clear()
    deny = lambda req: httpx.Response(400, json={"error": "invalid_scope"})  # noqa: E731
    with pytest.raises(rides.ToolError, match="guests.trips permission"):
        asyncio.run(uberapi._call("GET", "/trips", http=lambda: httpx.AsyncClient(transport=httpx.MockTransport(deny))))
