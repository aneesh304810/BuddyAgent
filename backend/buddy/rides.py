"""Rides: Buddy opens Uber or Lyft on the phone with the drop-off already filled in.

Uber and Lyft don't let personal apps see prices or book rides through their APIs, so Buddy does
the part it can: finds the place, builds the apps' own ride links, and the phone opens them. The
user sees the real price in the app and taps Confirm; nothing is booked or paid by Buddy.

  "Uber to the airport"       -> Uber opens, pickup here, drop-off Miami International
  "compare Uber and Lyft"     -> Uber opens; coming back to Buddy opens Lyft with the same trip
  "Lyft home"                 -> saved places: "save home as 123 Main St" once

The pickup is the phone's location unless the user names another one. Places are found with
Google Maps (Serper) near where the user is, or OpenStreetMap when there's no Serper key.
"""

import json
import logging
import urllib.parse
import urllib.request

from . import cards
from .location import Location
from .memory import Memory
from .tools import ToolError, require_https

log = logging.getLogger(__name__)

PLACES_KEY = "rides:places"
LYFT_TYPES = {"standard": "lyft", "xl": "lyft_xl", "lux": "lyft_lux", "lux_black": "lyft_luxsuv"}


def _nominatim(query: str, near: dict | None) -> list[dict]:
    params = {"format": "jsonv2", "q": query, "limit": 1, "accept-language": "en"}
    if near:  # prefer matches around the user (about 50 km)
        lat, lng = near["lat"], near["lng"]
        params["viewbox"] = f"{lng - 0.5},{lat + 0.5},{lng + 0.5},{lat - 0.5}"
    url = "https://nominatim.openstreetmap.org/search?" + urllib.parse.urlencode(params)
    request = urllib.request.Request(require_https(url), headers={"User-Agent": "Buddy personal assistant/1.0"})
    with urllib.request.urlopen(request, timeout=15) as response:  # https only  # nosec B310
        found = json.loads(response.read())
    return [{"title": f.get("name") or f.get("display_name", "").split(",")[0], "address": f.get("display_name", ""),
             "latitude": float(f["lat"]), "longitude": float(f["lon"])} for f in found]


class Rides:
    def __init__(self, memory: Memory, social=None, location: Location | None = None, search_osm=_nominatim):
        self.memory = memory
        self.social = social
        self.location = location or Location(memory)
        self.search_osm = search_osm

    # --- saved places ("home", "work") -----------------------------------------
    def saved(self) -> dict:
        try:
            return json.loads(self.memory.get(PLACES_KEY) or "{}")
        except json.JSONDecodeError:
            return {}

    def save_place(self, name: str, address: str) -> dict:
        place = self.find(address)
        places = self.saved()
        places[name.strip().lower()] = place
        self.memory.set(PLACES_KEY, json.dumps(places))
        return place

    # --- finding a place -------------------------------------------------------
    def find(self, query: str) -> dict:
        query = str(query or "").strip()
        if not query:
            raise ToolError("Where to?")
        saved = self.saved().get(query.lower().removeprefix("my ").strip())
        if saved:
            return saved
        here = self.location.current()
        near = ", ".join(p for p in ((here or {}).get("area"), (here or {}).get("city")) if p)
        found: list[dict] = []
        if self.social is not None and self.social.has_google():
            try:
                found = self.social.places(f"{query} near {near}" if near else query,
                                           (here or {}).get("country") or "US", num=1)
            except Exception as e:  # Serper down: try OpenStreetMap
                log.info("places lookup failed: %s", e)
        found = [f for f in found if f.get("latitude") is not None]
        if not found:
            try:
                found = self.search_osm(query, here)
            except Exception as e:
                log.info("OpenStreetMap lookup failed: %s", e)
        if not found:
            raise ToolError(f"I couldn't find '{query}' on the map. Try the name with the city, or the address.")
        f = found[0]
        return {"name": f.get("title") or query, "address": f.get("address", ""),
                "lat": round(float(f["latitude"]), 6), "lng": round(float(f["longitude"]), 6)}


def uber_links(drop: dict, pickup: dict | None = None) -> tuple[str, str]:
    """(app link, web link) for Uber with the trip filled in."""
    q = [("action", "setPickup")]
    if pickup:
        q += [("pickup[latitude]", pickup["lat"]), ("pickup[longitude]", pickup["lng"]),
              ("pickup[nickname]", pickup["name"]), ("pickup[formatted_address]", pickup["address"])]
    else:
        q.append(("pickup", "my_location"))
    q += [("dropoff[latitude]", drop["lat"]), ("dropoff[longitude]", drop["lng"]),
          ("dropoff[nickname]", drop["name"]), ("dropoff[formatted_address]", drop["address"])]
    query = urllib.parse.urlencode(q)
    return f"uber://?{query}", f"https://m.uber.com/ul/?{query}"


def lyft_links(drop: dict, pickup: dict | None = None, ride_type: str = "standard") -> tuple[str, str]:
    """(app link, web link) for Lyft; without a pickup Lyft uses where the phone is."""
    q = [("id", LYFT_TYPES.get(ride_type, "lyft"))]
    if pickup:
        q += [("pickup[latitude]", pickup["lat"]), ("pickup[longitude]", pickup["lng"])]
    q += [("destination[latitude]", drop["lat"]), ("destination[longitude]", drop["lng"])]
    query = urllib.parse.urlencode(q)
    return f"lyft://ridetype?{query}", f"https://lyft.com/ride?{query}"


RIDE_TOOL = {
    "name": "get_ride",
    "description": "Open Uber and/or Lyft on the user's phone with the trip filled in (pickup: where they are, "
    "unless they name another place; drop-off: a place name, address, or a saved place like 'home'). You can't "
    "see prices or book: the user sees the real price in the app and taps Confirm. app 'both' = compare: Uber "
    "opens first, and Lyft opens when they come back to Buddy. For a ride later (Reserve), open it and tell them "
    "to tap the schedule (clock) option, or offer a reminder before the time. save_place keeps 'home', 'work' "
    "and the like; places lists them.",
    "input_schema": {"type": "object", "properties": {
        "action": {"type": "string", "enum": ["open", "save_place", "places"]},
        "app": {"type": "string", "enum": ["uber", "lyft", "both"]},
        "to": {"type": "string", "description": "Drop-off, or the address to save"},
        "from": {"type": "string", "description": "Pickup, only if not where they are now"},
        "ride_type": {"type": "string", "enum": ["standard", "xl", "lux", "lux_black"],
                      "description": "Lyft only; in Uber they pick it in the app"},
        "name": {"type": "string", "description": "save_place: home, work, gym..."}},
        "required": ["action"], "additionalProperties": False},
}


async def run_ride_tool(rides: Rides, args: dict, open_links) -> str:
    """open_links: async (links: list[dict]) -> None, sends them to the phone (None when the app isn't connected)."""
    import asyncio

    action = args.get("action")
    if action == "places":
        places = rides.saved()
        return json.dumps({k: v["address"] or v["name"] for k, v in places.items()}) if places else \
            "No saved places yet: say 'save home as' and the address."
    if action == "save_place":
        name = str(args.get("name") or "").strip()
        if not name:
            raise ToolError("Save it as what? (home, work...)")
        place = await asyncio.to_thread(rides.save_place, name, str(args.get("to", "")))
        return f"Saved {name}: {place['name']}, {place['address']}."
    if open_links is None:
        raise ToolError("The phone isn't connected, so I can't open Uber or Lyft on it right now.")
    drop = await asyncio.to_thread(rides.find, str(args.get("to", "")))
    pickup = await asyncio.to_thread(rides.find, str(args["from"])) if args.get("from") else None
    app = args.get("app") or "uber"
    links, shown = [], []
    for name in (["uber", "lyft"] if app == "both" else [app]):
        app_url, web_url = uber_links(drop, pickup) if name == "uber" else \
            lyft_links(drop, pickup, str(args.get("ride_type") or "standard"))
        label = f"{name.title()} to {drop['name']}"
        links.append({"app": app_url, "url": web_url, "label": label})
        shown.append(cards.offer({"kind": "ride", "title": label, "subtitle": f"{name.title()} · from "
                     + (pickup["name"] if pickup else "where you are"),
                     "address": drop["address"], "lat": drop["lat"], "lng": drop["lng"], "url": web_url,
                     "app_url": app_url, "text": "Tap to open with the trip filled in"}))
    box = cards.current.get()
    if box is not None:  # always shown: the user may need to tap them
        box.pick([{"id": c} for c in shown if c])
    await open_links(links)
    where = f"{drop['name']}" + (f" ({drop['address']})" if drop["address"] else "")
    first = links[0]["label"].split()[0]
    then = " When they come back to Buddy, Lyft opens with the same trip, so they can compare." if app == "both" else ""
    return (f"Opening {first} on the phone: drop-off {where}, pickup "
            f"{pickup['name'] if pickup else 'their current location'}.{then} If the screen is off, it waits as a "
            "notification. They see the price in the app and tap Confirm; you haven't booked anything. Check the "
            "drop-off is the right place.")
