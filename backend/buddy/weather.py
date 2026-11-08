"""Weather from Open-Meteo (open-meteo.com): free, no key, worldwide.

forecast(place or coordinates): now, today and the next days (high, low, chance of rain, conditions), in the
user's units. Used for "will it rain tomorrow?", the morning heads-up when rain or snow is likely (said only when
it matters), and the evening before a flight: the weather where the user is going.
Only coordinates (rounded to about 1 km) or a place name go to Open-Meteo, nothing about the user.
"""

import json
import logging
import os
import time
import urllib.parse
from datetime import datetime, timezone

from .memory import Memory
from .tools import ToolError, require_https

log = logging.getLogger(__name__)

FORECAST = "https://api.open-meteo.com/v1/forecast?"
GEOCODE = "https://geocoding-api.open-meteo.com/v1/search?"
CACHE_SECONDS = 30 * 60
WMO = {0: "clear", 1: "mostly clear", 2: "partly cloudy", 3: "cloudy", 45: "fog", 48: "freezing fog",
       51: "light drizzle", 53: "drizzle", 55: "heavy drizzle", 56: "freezing drizzle", 57: "freezing drizzle",
       61: "light rain", 63: "rain", 65: "heavy rain", 66: "freezing rain", 67: "freezing rain", 71: "light snow",
       73: "snow", 75: "heavy snow", 77: "snow grains", 80: "rain showers", 81: "rain showers",
       82: "violent rain showers", 85: "snow showers", 86: "heavy snow showers", 95: "thunderstorms",
       96: "thunderstorms with hail", 99: "thunderstorms with hail"}
WET = {51, 53, 55, 56, 57, 61, 63, 65, 66, 67, 71, 73, 75, 77, 80, 81, 82, 85, 86, 95, 96, 99}
_cache: dict[str, tuple[float, dict]] = {}


def _fetch(url: str) -> bytes:
    import urllib.request  # noqa: PLC0415
    req = urllib.request.Request(require_https(url), headers={"User-Agent": "Buddy/1.0 (personal assistant)"})
    with urllib.request.urlopen(req, timeout=20) as r:  # https only  # nosec B310
        return r.read(2_000_000)


class Weather:
    def __init__(self, memory: Memory, fetch=None):
        self.memory = memory
        self.fetch = fetch or _fetch

    @property
    def units(self) -> str:
        return "celsius" if (os.getenv("BUDDY_TEMP_UNIT") or "").lower().startswith("c") else "fahrenheit"

    def _get(self, url: str) -> dict:
        hit = _cache.get(url)
        if hit and time.monotonic() - hit[0] < CACHE_SECONDS:
            return hit[1]
        from .travel import log_call  # noqa: PLC0415
        started = time.monotonic()
        try:
            got = json.loads(self.fetch(url) or b"{}")
            log_call(self.memory, "openmeteo", url.split("?")[0][-30:], True, time.monotonic() - started, "weather",
                     cost=0.0)
        except Exception as e:
            log_call(self.memory, "openmeteo", url.split("?")[0][-30:], False, time.monotonic() - started, "weather",
                     type(e).__name__, cost=0.0)
            raise ToolError("The weather service didn't answer; try again in a minute.") from e
        _cache[url] = (time.monotonic(), got)
        return got

    def place(self, name: str) -> dict:
        """'Lima' -> its coordinates and name (Open-Meteo geocoding)."""
        got = self._get(GEOCODE + urllib.parse.urlencode({"name": name.split(",")[0].strip(), "count": 1}))
        r = (got.get("results") or [None])[0]
        if not r:
            raise ToolError(f"I couldn't find a place called {name}.")
        return {"lat": r["latitude"], "lng": r["longitude"],
                "name": ", ".join(x for x in (r.get("name"), r.get("admin1"), r.get("country")) if x)}

    def forecast(self, lat: float, lng: float, days: int = 3, name: str = "") -> dict:
        q = {"latitude": round(lat, 2), "longitude": round(lng, 2), "timezone": "auto",
             "forecast_days": max(1, min(int(days), 7)), "temperature_unit": self.units,
             "current": "temperature_2m,apparent_temperature,weather_code,precipitation",
             "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max",
             "hourly": "precipitation_probability"}
        got = self._get(FORECAST + urllib.parse.urlencode(q))
        unit = "°F" if self.units == "fahrenheit" else "°C"
        d = got.get("daily") or {}
        days_out = []
        for i, day in enumerate(d.get("time") or []):
            code = (d.get("weather_code") or [None])[i]
            days_out.append({"day": day, "conditions": WMO.get(code, "mixed"), "wet": code in WET,
                             "high": round((d.get("temperature_2m_max") or [0])[i]),
                             "low": round((d.get("temperature_2m_min") or [0])[i]),
                             "rain_chance": (d.get("precipitation_probability_max") or [None])[i]})
        cur = got.get("current") or {}
        h = got.get("hourly") or {}
        hours = list(zip(h.get("time") or [], h.get("precipitation_probability") or []))
        return {"place": name, "unit": unit, "timezone": got.get("timezone", ""),
                "now": {"temp": round(cur["temperature_2m"]) if "temperature_2m" in cur else None,
                        "feels": round(cur["apparent_temperature"]) if "apparent_temperature" in cur else None,
                        "conditions": WMO.get(cur.get("weather_code"), "")},
                "days": days_out, "hours": hours, "source": "Open-Meteo"}

    def here(self, days: int = 3) -> dict | None:
        from .location import Location  # noqa: PLC0415
        loc = Location(self.memory).current()
        if not loc:
            return None
        return self.forecast(loc["lat"], loc["lng"], days, ", ".join(x for x in (loc.get("area"), loc.get("city")) if x))

    @staticmethod
    def wet_from(fc: dict, day: str) -> str:
        """'from 3 PM' when the chance of rain first reaches 50% on that day, or ''."""
        for t, p in fc.get("hours") or []:
            if t.startswith(day) and (p or 0) >= 50:
                return datetime.fromisoformat(t).strftime("from %-I %p")
        return ""

    @staticmethod
    def describe(fc: dict, n: int = 3) -> str:
        u = fc["unit"]
        parts = []
        if fc["now"]["temp"] is not None:
            parts.append(f"Now {fc['now']['temp']}{u}, {fc['now']['conditions']}")
        for d in fc["days"][:n]:
            rain = f", {d['rain_chance']}% chance of rain" if d["rain_chance"] is not None else ""
            parts.append(f"{d['day']}: {d['conditions']}, {d['high']}/{d['low']}{u}{rain}")
        return (f"{fc['place']}: " if fc["place"] else "") + "; ".join(parts) + " (Open-Meteo)."


WEATHER_TOOL = {
    "name": "weather",
    "description": "Weather from Open-Meteo (free): now and up to 7 days (conditions, high/low, chance of rain by day "
    "and hour). place: a city or area (omit for where the user is now); days. For trips use the destination.",
    "input_schema": {"type": "object", "properties": {"place": {"type": "string"}, "days": {"type": "integer"}},
                     "additionalProperties": False},
}


def run_tool(memory: Memory, args: dict) -> str:
    w = Weather(memory)
    days = int(args.get("days") or 3)
    if args.get("place"):
        p = w.place(str(args["place"]))
        fc = w.forecast(p["lat"], p["lng"], days, p["name"])
    else:
        fc = w.here(days)
        if fc is None:
            return "I don't know where you are: say a place, or turn on Settings -> Share my location."
    return json.dumps({"summary": Weather.describe(fc, days), **{k: v for k, v in fc.items() if k != "hours"}},
                      ensure_ascii=False)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
