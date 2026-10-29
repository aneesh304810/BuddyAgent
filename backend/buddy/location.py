"""Where the user is, when they choose to share it from the Buddy app.

The phone sends its location when Buddy is opened or spoken to (Settings → Share my
location). It's kept in the encrypted database, only the latest one, and treated as
stale after MAX_AGE_HOURS. Coordinates are turned into a neighbourhood and city with
OpenStreetMap's free geocoder (cached), so "a spa near me" becomes "a spa in
Miraflores, Lima".
"""

import json
import logging
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

from .memory import Memory
from .tools import require_https

log = logging.getLogger(__name__)
MAX_AGE_HOURS = 3
_names: dict[tuple, dict] = {}


def _reverse(lat: float, lng: float) -> dict:
    key = (round(lat, 3), round(lng, 3))  # about 100 m: one lookup per neighbourhood
    if key in _names:
        return _names[key]
    url = "https://nominatim.openstreetmap.org/reverse?" + urllib.parse.urlencode(
        {"format": "jsonv2", "lat": lat, "lon": lng, "zoom": 16, "accept-language": "en"})
    request = urllib.request.Request(require_https(url), headers={"User-Agent": "Buddy personal assistant/1.0"})
    with urllib.request.urlopen(request, timeout=15) as response:  # https only  # nosec B310
        address = json.loads(response.read()).get("address", {})
    place = {
        "area": address.get("suburb") or address.get("neighbourhood") or address.get("quarter")
        or address.get("city_district") or "",
        "city": address.get("city") or address.get("town") or address.get("village") or address.get("county") or "",
        "country": (address.get("country_code") or "").upper(),
    }
    _names[key] = place
    return place


class Location:
    def __init__(self, memory: Memory, reverse=_reverse):
        self.memory = memory
        self.reverse = reverse

    def update(self, lat: float, lng: float, accuracy: float | None = None) -> dict:
        if not (-90 <= lat <= 90 and -180 <= lng <= 180):
            raise ValueError("bad coordinates")
        try:
            place = self.reverse(lat, lng)
        except Exception as e:  # no name: coordinates still work
            log.info("reverse geocoding failed: %s", e)
            place = {"area": "", "city": "", "country": ""}
        now = {"lat": round(lat, 5), "lng": round(lng, 5), "accuracy_m": accuracy, **place,
               "at": datetime.now(timezone.utc).isoformat()}
        self.memory.set("location:current", json.dumps(now))
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
