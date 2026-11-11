"""Where the user is, when they choose to share it from the Buddy app.

The phone sends its location when Buddy is opened or spoken to (Settings → Share my
location). It's kept in the encrypted database, only the latest one, and treated as
stale after MAX_AGE_HOURS. Coordinates are turned into a neighbourhood and city with
OpenStreetMap's free geocoder (cached), so "a spa near me" becomes "a spa in
Miraflores, Lima".

With the GPS fix the phone shares, Buddy also works out:
  street        the street address (house number and road, from the same OpenStreetMap lookup)
  nearby        shops, cafes and restaurants within about NEARBY_M metres (Google Maps places via Serper,
                through Buddy Connect so the key stays on the server; skipped when BUDDY_SERPER_KEY isn't set)
  current_shop  the place the coordinates are inside, if any: the shop/amenity OpenStreetMap puts the point in,
                else the nearest listed place within INSIDE_M metres. Remembered (name and spot only) so the
                learned routine (routine.py) can name exact places.
When nothing matches, neighbourhood and city are still there, as before.
"""

import json
import logging
import math
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

from .memory import Memory
from .tools import require_https

log = logging.getLogger(__name__)
MAX_AGE_HOURS = 3
NEARBY_M = 50          # "nearby": places within this many metres
INSIDE_M = 25          # "current shop": the nearest place within this, when OpenStreetMap doesn't say
NEARBY_KEEP = 8
SHOPS_KEY = "location:shops"   # [{"name", "lat", "lng"}]: places the owner has been in, for the routine
SHOPS_KEEP = 300
INSIDE_KINDS = {"shop", "amenity", "leisure", "tourism", "craft", "office", "healthcare"}
_names: dict[tuple, dict] = {}
_near: dict[tuple, list] = {}


def _reverse(lat: float, lng: float) -> dict:
    key = (round(lat, 4), round(lng, 4))  # about 10 m: street-level, one lookup per spot
    if key in _names:
        return _names[key]
    url = "https://nominatim.openstreetmap.org/reverse?" + urllib.parse.urlencode(
        {"format": "jsonv2", "lat": lat, "lon": lng, "zoom": 18, "accept-language": "en"})
    request = urllib.request.Request(require_https(url), headers={"User-Agent": "Buddy personal assistant/1.0"})
    with urllib.request.urlopen(request, timeout=15) as response:  # https only  # nosec B310
        found = json.loads(response.read())
    place = parse_reverse(found)
    _names[key] = place
    return place


def parse_reverse(found: dict) -> dict:
    """OpenStreetMap's reverse answer -> area, city, country, state, county, street and (when the point is in a
    shop, cafe or other amenity) inside: {"name", "kind"}."""
    address = found.get("address") or {}
    road = address.get("road") or address.get("pedestrian") or address.get("footway") or ""
    street = " ".join(p for p in (address.get("house_number") or "", road) if p)
    place = {
        "area": address.get("suburb") or address.get("neighbourhood") or address.get("quarter")
        or address.get("city_district") or "",
        "city": address.get("city") or address.get("town") or address.get("village") or address.get("county") or "",
        "country": (address.get("country_code") or "").upper(),
        "state": address.get("state") or "",   # which local transit applies (New Jersey or New York)
        "county": address.get("county") or "",
        "street": street,
    }
    if found.get("category") in INSIDE_KINDS and found.get("name"):
        place["inside"] = {"name": found["name"], "kind": str(found.get("type") or found["category"]).replace("_", " ")}
    return place


def metres(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lng1, lat2, lng2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lng2 - lng1) / 2) ** 2
    return 2 * 6_371_000 * math.asin(math.sqrt(min(1.0, h)))


def _nearby(lat: float, lng: float, memory: Memory | None = None, env=None) -> list[dict]:
    """Shops, cafes and restaurants Google Maps lists around the point (Serper /places, key kept server-side by
    Buddy Connect). [] when Serper isn't set up. Cached per ~10 m spot."""
    from . import gateway  # noqa: PLC0415
    if not gateway.configured("serper", env):
        return []
    key = (round(lat, 4), round(lng, 4))
    if key in _near:
        return _near[key]
    data = gateway.call(memory, "serper", "/places", env=env, purpose="location:nearby",
                        json_body={"q": "shops cafes restaurants", "ll": f"@{lat:.6f},{lng:.6f},19z"})
    out = [{"name": p["title"], "category": p.get("category") or "", "address": p.get("address") or "",
            "lat": float(p["latitude"]), "lng": float(p["longitude"])}
           for p in (data.get("places") or []) if p.get("title") and p.get("latitude") is not None
           and p.get("longitude") is not None]
    _near[key] = out
    return out


def around(lat: float, lng: float, place: dict, found: list[dict]) -> tuple[list[dict], dict | None]:
    """The listed places within NEARBY_M metres (closest first) and the one the point is in, if any."""
    near = []
    for p in found:
        d = metres((lat, lng), (p["lat"], p["lng"]))
        if d <= NEARBY_M:
            near.append({"name": p["name"], "category": p.get("category") or "", "address": p.get("address") or "",
                         "distance_m": round(d), "lat": p["lat"], "lng": p["lng"]})
    near.sort(key=lambda p: p["distance_m"])
    inside = place.get("inside")
    if inside:
        match = next((p for p in near if p["name"].lower() == inside["name"].lower()), None)
        shop = {"name": inside["name"], "category": (match or {}).get("category") or inside.get("kind", ""),
                "lat": (match or {}).get("lat", lat), "lng": (match or {}).get("lng", lng)}
    elif near and near[0]["distance_m"] <= INSIDE_M:
        shop = {k: near[0][k] for k in ("name", "category", "lat", "lng")}
    else:
        shop = None
    return near[:NEARBY_KEEP], shop


class Location:
    def __init__(self, memory: Memory, reverse=_reverse, nearby=None):
        self.memory = memory
        self.reverse = reverse
        self.nearby = nearby or (lambda lat, lng: _nearby(lat, lng, memory))

    def update(self, lat: float, lng: float, accuracy: float | None = None) -> dict:
        if not (-90 <= lat <= 90 and -180 <= lng <= 180):
            raise ValueError("bad coordinates")
        try:
            place = self.reverse(lat, lng)
        except Exception as e:  # no name: coordinates still work
            log.info("reverse geocoding failed: %s", e)
            place = {"area": "", "city": "", "country": ""}
        place = dict(place)
        try:
            found = self.nearby(lat, lng) or []
        except Exception as e:  # no places: street, neighbourhood and city still work
            log.info("nearby places lookup failed: %s", e)
            found = []
        near, shop = around(lat, lng, place, found)
        place.pop("inside", None)
        now = {"lat": round(lat, 5), "lng": round(lng, 5), "accuracy_m": accuracy, **place,
               "street": place.get("street", ""),
               "nearby": [{k: p[k] for k in ("name", "category", "address", "distance_m")} for p in near],
               "current_shop": shop["name"] if shop else "",
               "current_shop_category": shop["category"] if shop else "",
               "at": datetime.now(timezone.utc).isoformat()}
        if shop:
            self._remember_shop(shop)
        self.memory.set("location:current", json.dumps(now))
        try:  # the history the places-and-routines map learns from (maps.py), kept 60 days
            from .maps import Maps
            Maps(self.memory).log_location(lat, lng)
        except Exception:
            log.info("location history not kept", exc_info=True)
        return now

    def current(self) -> dict | None:
        raw = self.memory.get("location:current")
        if not raw:
            return None
        here = json.loads(raw)
        if datetime.fromisoformat(here["at"]) < datetime.now(timezone.utc) - timedelta(hours=MAX_AGE_HOURS):
            return None
        return here

    def label(self) -> str:
        here = self.current()
        if not here:
            return ""
        return ", ".join(p for p in (here.get("area"), here.get("city")) if p) or f"{here['lat']},{here['lng']}"

    def forget(self) -> None:
        self.memory.set("location:current", "")

    # places the owner has been in (name and spot only), so the learned routine can name them -------------------
    def _remember_shop(self, shop: dict) -> None:
        try:
            shops = [s for s in shops_seen(self.memory)
                     if not (s["name"] == shop["name"] and metres((s["lat"], s["lng"]), (shop["lat"], shop["lng"])) < 30)]
            shops.append({"name": shop["name"], "lat": round(shop["lat"], 5), "lng": round(shop["lng"], 5)})
            self.memory.set(SHOPS_KEY, json.dumps(shops[-SHOPS_KEEP:]))
        except Exception:
            log.info("shop not remembered", exc_info=True)


def shops_seen(memory: Memory) -> list[dict]:
    try:
        return json.loads(memory.get(SHOPS_KEY) or "[]")
    except ValueError:
        return []


def shop_at(memory: Memory, lat: float, lng: float, shops: list[dict] | None = None) -> str:
    """The remembered shop the point is in (within INSIDE_M metres), or ''."""
    best, best_d = "", INSIDE_M + 0.001
    for s in shops if shops is not None else shops_seen(memory):
        d = metres((lat, lng), (s["lat"], s["lng"]))
        if d < best_d:
            best, best_d = s["name"], d
    return best
