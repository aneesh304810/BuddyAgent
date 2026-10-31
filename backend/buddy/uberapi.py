"""Uber Guest Rides API: real prices, booking and live trip status, once Uber grants access.

Needs an Uber for Business account and an app with the guests.trips scope (Uber has to grant it),
then on the server:
    sudo bash /opt/buddy/deploy/set.sh BUDDY_UBER_CLIENT_ID
    sudo bash /opt/buddy/deploy/set.sh BUDDY_UBER_CLIENT_SECRET
    sudo bash /opt/buddy/deploy/set.sh BUDDY_UBER_ORG_UUID     (from authorising the app on business.uber.com)
    sudo bash /opt/buddy/deploy/set.sh BUDDY_UBER_PHONE        (your mobile, +1...: Uber texts the trip link)
    BUDDY_UBER_SANDBOX=1 tests against Uber's sandbox (no real cars).

Rides are booked as guest rides on the business account: billed to its card, the trip link
arrives by text, they don't show in the personal Uber app. Booking always asks the user first.
While a booked trip is active Buddy checks it every 30 seconds (the server is private, so no
webhooks) and says the moments that matter: driver assigned, arriving, here, done with the fare.
"""

import asyncio
import json
import logging
import os
import time

import httpx

from .config import settings
from .memory import Memory
from .tools import ToolError

log = logging.getLogger(__name__)

TOKEN_URL = "https://login.uber.com/oauth/v2/token"
ESTIMATES_KEY = "uber:estimates"
TRIP_KEY = "uber:trip"
POLL_SECONDS = 30
DONE = {"completed", "rider_canceled", "driver_canceled", "no_drivers_available", "canceled"}
STAGE = {"processing": "searching", "scheduled": "searching", "accepted": "driver_found", "driver_assigned":
         "driver_found", "arriving": "arrived", "in_progress": "on_trip", "completed": "finished",
         "rider_canceled": "cancelled", "driver_canceled": "cancelled", "canceled": "cancelled",
         "no_drivers_available": "cancelled"}
notify = None  # async (kind, text, **extra) -> bool: set by the server
_token: dict = {}
_watching: set[str] = set()


def enabled() -> bool:
    return all(os.getenv(k) for k in ("BUDDY_UBER_CLIENT_ID", "BUDDY_UBER_CLIENT_SECRET", "BUDDY_UBER_ORG_UUID"))


def _base() -> str:
    return ("https://sandbox-api.uber.com" if os.getenv("BUDDY_UBER_SANDBOX") == "1" else "https://api.uber.com") \
        + "/v1/guests"


async def _access_token(client: httpx.AsyncClient) -> str:
    if _token.get("value") and _token["until"] > time.time() + 60:
        return _token["value"]
    r = await client.post(TOKEN_URL, data={"client_id": os.getenv("BUDDY_UBER_CLIENT_ID"),
                                           "client_secret": os.getenv("BUDDY_UBER_CLIENT_SECRET"),
                                           "grant_type": "client_credentials", "scope": "guests.trips"})
    if r.status_code != 200:
        raise ToolError("Uber refused Buddy's keys" + (": the app doesn't have the guests.trips permission yet."
                                                       if "scope" in r.text else f" (HTTP {r.status_code})."))
    body = r.json()
    _token.update(value=body["access_token"], until=time.time() + int(body.get("expires_in", 3600)))
    return _token["value"]


async def _call(method: str, path: str, body: dict | None = None, http=None) -> dict:
    async with (http() if http else httpx.AsyncClient(timeout=20)) as client:  # http: a client factory (tests)
        headers = {"Authorization": f"Bearer {await _access_token(client)}",
                   "x-uber-organizationuuid": os.getenv("BUDDY_UBER_ORG_UUID", ""), "Content-Type": "application/json"}
        r = await client.request(method, _base() + path, headers=headers, json=body)
    if r.status_code >= 400:
        try:
            why = r.json().get("message") or r.json().get("code") or r.text[:200]
        except ValueError:
            why = r.text[:200]
        raise ToolError(f"Uber said no (HTTP {r.status_code}): {why}")
    return r.json() if r.content else {}


async def estimates(memory: Memory, pickup: dict, drop: dict, http=None) -> list[dict]:
    """Every ride type with its upfront fare and minutes to pickup, cheapest first."""
    out = await _call("POST", "/trips/estimates", {"pickup": {"latitude": pickup["lat"], "longitude": pickup["lng"]},
                                                     "dropoff": {"latitude": drop["lat"], "longitude": drop["lng"]}},
                      http)
    rides = []
    for e in out.get("product_estimates", []):
        product, info = e.get("product", {}), e.get("estimate_info", {})
        fare = info.get("fare") or {}
        rides.append({"ride": product.get("display_name", ""), "product_id": product.get("product_id", ""),
                      "fare_id": fare.get("fare_id") or info.get("fare_id", ""), "price": fare.get("display", ""),
                      "value": fare.get("value"), "currency": fare.get("currency_code", ""),
                      "pickup_min": info.get("pickup_estimate"), "seats": product.get("capacity")})
    rides.sort(key=lambda r: (r["value"] is None, r["value"] or 0))
    memory.set(ESTIMATES_KEY, json.dumps({"pickup": pickup, "drop": drop, "rides": rides, "at": time.time()}))
    return rides


def last_estimates(memory: Memory) -> dict | None:
    try:
        saved = json.loads(memory.get(ESTIMATES_KEY) or "null")
    except json.JSONDecodeError:
        return None
    return saved if saved and time.time() - saved["at"] < 10 * 60 else None  # upfront fares expire


async def book(memory: Memory, choice: dict, pickup: dict, drop: dict, http=None) -> dict:
    phone = os.getenv("BUDDY_UBER_PHONE", "")
    if not phone:
        raise ToolError("Set BUDDY_UBER_PHONE first (sudo bash /opt/buddy/deploy/set.sh BUDDY_UBER_PHONE): "
                        "Uber texts the trip link to it.")
    first, _, last = settings.user_name.partition(" ")
    trip = await _call("POST", "/trips", {
        "guest": {"first_name": first or "Buddy", "last_name": last or "User", "phone_number": phone},
        "start_latitude": pickup["lat"], "start_longitude": pickup["lng"],
        "end_latitude": drop["lat"], "end_longitude": drop["lng"],
        "product_id": choice["product_id"], **({"fare_id": choice["fare_id"]} if choice.get("fare_id") else {}),
        "expense_memo": "Booked by Buddy"}, http)
    saved = {"request_id": trip.get("request_id", ""), "ride": choice["ride"], "price": choice["price"],
             "to": drop.get("name", ""), "status": trip.get("status", "processing"), "at": time.time()}
    memory.set(TRIP_KEY, json.dumps(saved))
    return saved


def active_trip(memory: Memory) -> dict | None:
    try:
        trip = json.loads(memory.get(TRIP_KEY) or "null")
    except json.JSONDecodeError:
        return None
    return trip if trip and trip.get("status") not in DONE and time.time() - trip["at"] < 4 * 3600 else None


def ride_view(trip: dict) -> dict:
    """The trip in the shape the ride follower uses (rides.describe / status_card)."""
    v, d = trip.get("vehicle") or {}, trip.get("driver") or {}
    fare = str(trip.get("client_fare") or "")
    view = {"app": "Uber", "stage": STAGE.get(trip.get("status", ""), "searching"), "driver": d.get("name", ""),
            "car": " ".join(x for x in (v.get("vehicle_color_name"), v.get("make"), v.get("model")) if x),
            "plate": v.get("license_plate", "")}
    if trip.get("pickup", {}).get("eta") is not None:
        view["eta_min"] = int(trip["pickup"]["eta"])
    if fare:
        digits = "".join(c for c in fare if c.isdigit() or c == ".")
        if digits:
            view.update(fare=float(digits), currency="USD" if fare.startswith("$") else "")
    return view


async def watch(memory: Memory, request_id: str, record_fare=None, http=None, every: float = POLL_SECONDS) -> None:
    """Follow a booked trip until it ends, saying each new stage."""
    from .rides import describe, status_card

    if request_id in _watching:
        return
    _watching.add(request_id)
    said = ""
    try:
        for _ in range(int(4 * 3600 / max(every, POLL_SECONDS))):  # four hours at most
            try:
                trip = await _call("GET", f"/trips/{request_id}", http=http)
            except (ToolError, httpx.HTTPError) as e:
                log.info("uber trip check failed: %s", e)
                await asyncio.sleep(every)
                continue
            view = ride_view(trip)
            saved = json.loads(memory.get(TRIP_KEY) or "{}")
            if saved.get("request_id") == request_id:
                memory.set(TRIP_KEY, json.dumps({**saved, "status": trip.get("status", ""), "view": view}))
            if view["stage"] != said and view["stage"] != "searching":
                said = view["stage"]
                if view["stage"] in ("driver_found", "arrived", "finished", "cancelled") and notify:
                    await notify("ride", describe(view), cards=[status_card(view)])
                if view["stage"] == "finished" and view.get("fare") and record_fare:
                    record_fare("Uber", view["fare"], view.get("currency") or "USD")
            if trip.get("status") in DONE:
                return
            await asyncio.sleep(every)
    finally:
        _watching.discard(request_id)


async def cancel(memory: Memory, http=None) -> str:
    trip = active_trip(memory)
    if not trip:
        return "There's no Uber booked through Buddy right now."
    await _call("DELETE", f"/trips/{trip['request_id']}", http=http)
    memory.set(TRIP_KEY, json.dumps({**trip, "status": "rider_canceled"}))
    return f"Cancelled the {trip['ride']} to {trip['to']}. Uber may charge a cancellation fee if the driver was close."
