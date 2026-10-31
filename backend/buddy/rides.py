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
import re
import secrets
import time
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
    "and the like; places lists them. compare: Buddy opens Uber, then Lyft, reads the prices each shows \
"
    "(every ride type, minutes away) and tells the user which is cheaper, about 30-40 seconds later; the phone must "
    "be unlocked. prices: the last comparison read. status: where the booked ride is (driver, car, plate, minutes away), \
"
    "from the Uber / Lyft notifications on the phone.",
    "input_schema": {"type": "object", "properties": {
        "action": {"type": "string", "enum": ["open", "compare", "prices", "status", "save_place", "places"]},
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
    if action == "prices":
        quote = last_quotes(rides.memory)
        if not quote or not quote.get("apps"):
            return "No recent price comparison: use compare."
        return json.dumps({"to": quote["to"], "apps": quote["apps"]})
    if action == "status":
        ride = RideFollower(rides.memory).active()
        if not ride:
            return ("No ride in progress that I can see. I follow rides through the Uber and Lyft notifications, "
                    "so Buddy needs Notification access (Settings) and the app's notifications on.")
        return describe(ride) + " " + json.dumps({k: v for k, v in ride.items() if k in (
            "app", "stage", "driver", "car", "plate", "eta_min", "fare", "currency")})
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
    compare = action == "compare"
    app = "both" if compare else (args.get("app") or "uber")
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
    where = f"{drop['name']}" + (f" ({drop['address']})" if drop["address"] else "")
    if compare:
        compare_id = secrets.token_hex(4)
        rides.memory.set(QUOTES_KEY, json.dumps({"id": compare_id, "to": drop["name"], "links": links, "apps": {},
                                                  "at": time.time()}))
        await open_links(links, compare=compare_id)
        return (f"Reading the prices now: Uber opens, then Lyft, with drop-off {where}; Buddy comes back with both "
                "in about 30-40 seconds and says which is cheaper. Tell them in a few words and that the phone must "
                "stay unlocked; don't guess any prices.")
    await open_links(links)
    first = links[0]["label"].split()[0]
    then = " When they come back to Buddy, Lyft opens with the same trip, so they can compare." if app == "both" else ""
    return (f"Opening {first} on the phone: drop-off {where}, pickup "
            f"{pickup['name'] if pickup else 'their current location'}.{then} If the screen is off, it waits as a "
            "notification. They see the price in the app and tap Confirm; you haven't booked anything. Check the "
            "drop-off is the right place.")


# --- following a booked ride through Uber's and Lyft's own notifications --------------------
# Neither app lets others see a trip, but both post notifications as it goes: driver found (name,
# car, plate, minutes away), arriving, here, on the trip, done (fare). Buddy reads those (with the
# user's Notification access), keeps the latest state, and speaks the moments that matter.

RIDE_APPS = {"com.ubercab": "Uber", "me.lyft.android": "Lyft"}
ACTIVE_KEY = "rides:active"
STAGES = ("searching", "driver_found", "arriving", "arrived", "on_trip", "finished", "cancelled")
SPOKEN = {"driver_found", "arriving", "arrived", "finished", "cancelled"}
_STAGE_RULES = [  # first match wins
    ("cancelled", r"\bcancel"),
    ("finished", r"thanks for riding|trip (?:is )?(?:complete|ended|finished)|you(?:'ve| have) arrived|rate your "
                 r"(?:trip|ride|driver)|receipt|how was your (?:trip|ride)|tip your driver|your (?:trip|ride) total"),
    ("searching", r"looking for|finding (?:you )?a|matching you|connecting you|requesting"),
    ("on_trip", r"heading to|on (?:your|the) (?:way|trip) to|en route to|arriving at \d|drop-?off at|you'll arrive|"
                r"\beta\b.*\d{1,2}:\d{2}|trip in progress|enjoy your (?:trip|ride)"),
    ("arrived", r"\b(?:has|is) (?:arrived|here)\b|driver is here|\bis outside\b|waiting (?:for you|outside)|"
                r"meet (?:\w+ )?at (?:the )?pickup|at (?:the )?pickup"),
    ("arriving", r"arriving (?:now|soon|in [1-2] min)|almost there|\b[1-2] min(?:ute)?s? away|about to arrive"),
    ("driver_found", r"on (?:the|his|her|their) way|is coming|accepted|confirmed|will (?:arrive|pick you up)|"
                     r"\d+ min(?:ute)?s? away|driver found|matched with"),
]
_COLOURS = r"(?i:white|black|silver|gr[ae]y|blue|red|green|gold|beige|brown|maroon|orange|yellow)"


def _plate(segment: str) -> bool:
    s = segment.replace(" ", "").replace("-", "")
    return 4 <= len(s) <= 8 and s.isalnum() and s.upper() == s and any(c.isdigit() for c in s) \
        and any(c.isalpha() for c in s)


def parse_ride_notification(title: str, text: str) -> dict:
    """What one Uber / Lyft notification says: stage, and any of driver, car, plate, eta_min, fare, currency."""
    import re

    both = f"{title} . {text}"
    low = both.lower()
    out: dict = {"stage": next((stage for stage, rule in _STAGE_RULES if re.search(rule, low)), "")}
    if m := re.search(r"(\d{1,2})\s*min", low):
        out["eta_min"] = int(m.group(1))
    for seg in re.split(r"\s*[·•|•(),]\s*|\s+-\s+", both):
        seg = seg.strip(" .")
        if not out.get("plate") and _plate(seg):
            out["plate"] = seg
    if m := re.search(rf"(?:in an? |driving an? )?\b({_COLOURS}\s+)?([A-Z][a-z]+(?:-[A-Z][a-z]+)? [A-Z0-9][\w-]+)"
                      r"(?=\s*[·•|(•]|\s*$| with plate| \()", both):
        candidate = (m.group(1) or "") + m.group(2)
        if not re.match(r"(?:Your|Meet|Thanks|Rate|Heading|Arriving|Driver)\b", m.group(2)):
            out["car"] = candidate.strip()
    if m := re.search(r"(?:your driver,? |meet |driver )([A-Z][a-z]+)\b|^([A-Z][a-z]+) (?:is|has|will)\b", both):
        name = m.group(1) or m.group(2)
        if name not in ("Your", "The", "Uber", "Lyft", "Driver"):
            out["driver"] = name
    if out["stage"] == "finished" and (m := re.search(r"(R\$|S/|US\$|[$€£₹])\s?(\d{1,4}(?:[.,]\d{2})?)", both)):
        out["fare"] = float(m.group(2).replace(",", "."))
        out["currency"] = {"$": "USD", "US$": "USD", "R$": "BRL", "S/": "PEN", "€": "EUR", "£": "GBP",
                           "₹": "INR"}[m.group(1)]
    return out


class RideFollower:
    """The latest state of the user's booked ride, from the ride apps' notifications."""

    def __init__(self, memory: Memory, record_fare=None):
        self.memory = memory
        self.record_fare = record_fare  # (app, fare, currency) -> None: into spending

    @staticmethod
    def is_ride(msg: dict) -> bool:
        return str(msg.get("app", "")) in RIDE_APPS

    def active(self) -> dict | None:
        try:
            ride = json.loads(self.memory.get(ACTIVE_KEY) or "null")
        except json.JSONDecodeError:
            return None
        if not ride:
            return None
        import time

        done = ride.get("stage") in ("finished", "cancelled")
        return ride if time.time() - ride.get("updated", 0) < (20 * 60 if done else 3 * 3600) else None

    def update(self, msg: dict) -> tuple[str | None, dict | None]:
        """(what to say or None, status card or None) for one ride-app notification."""
        import time

        app = RIDE_APPS[str(msg["app"])]
        seen = parse_ride_notification(str(msg.get("title", "")), str(msg.get("text", "")))
        stage = seen.pop("stage")
        ride = self.active()
        if ride and (ride["app"] != app or ride["stage"] in ("finished", "cancelled")):
            ride = None if stage in ("searching", "driver_found") else ride  # a new trip
        if not stage and not ride:
            return None, None  # a promotion or anything else from the app
        ride = ride or {"app": app, "stage": "", "started": time.time()}
        before = ride["stage"]
        # Notifications can arrive out of order: never step back (except a new search after a cancel).
        if stage and (not before or STAGES.index(stage) >= STAGES.index(before)):
            ride["stage"] = stage
        ride.update({k: v for k, v in seen.items() if v not in (None, "")})
        ride["updated"] = time.time()
        if ride["stage"] in ("arrived", "finished", "cancelled") or (ride["stage"] == "on_trip" and "eta_min" not in seen):
            ride.pop("eta_min", None)  # minutes to the pickup mean nothing now
        self.memory.set(ACTIVE_KEY, json.dumps(ride))
        changed = ride["stage"] != before
        if ride["stage"] == "finished" and ride.get("fare") and not ride.get("fare_noted") and self.record_fare:
            ride["fare_noted"] = True  # the receipt often comes a minute after "trip done"
            self.memory.set(ACTIVE_KEY, json.dumps(ride))
            self.record_fare(app, ride["fare"], ride.get("currency", "USD"))
            if not changed:
                return f"{app} trip: {ride['fare']:.2f} {ride.get('currency', '')}.".replace(" .", "."), status_card(ride)
        say = describe(ride) if changed and ride["stage"] in SPOKEN else None
        return say, (status_card(ride) if changed or seen else None)


def _car_bits(ride: dict) -> str:
    return ", ".join(x for x in (ride.get("car"), _spell_plate(ride.get("plate", ""))) if x)


def _spell_plate(plate: str) -> str:
    return f"plate {plate}" if plate else ""


def describe(ride: dict) -> str:
    app, stage = ride["app"], ride["stage"]
    car = _car_bits(ride)
    eta = f", {ride['eta_min']} minute{'s' if ride['eta_min'] != 1 else ''} away" if ride.get("eta_min") else ""
    if stage == "driver_found":
        who = ride.get("driver", "")
        return f"Your {app}'s on the way" + (f": {who}" if who else "") + (f", {car}" if car else "") + f"{eta}."
    if stage == "arriving":
        return f"Your {app} is almost there{eta}" + (f": {car}." if car else ".")
    if stage == "arrived":
        return f"Your {app} is here" + (f": {car}." if car else ".")
    if stage == "finished":
        fare = f": {ride['fare']:.2f} {ride.get('currency', '')}".rstrip() if ride.get("fare") else ""
        return f"{app} trip done{fare}."
    if stage == "cancelled":
        return f"Your {app} was cancelled."
    if stage == "on_trip":
        return f"On your {app} trip" + (f", about {ride['eta_min']} minutes to go." if ride.get("eta_min") else ".")
    return f"Looking for your {app}."


LABELS = {"searching": "finding a driver", "driver_found": "driver on the way", "arriving": "almost there",
          "arrived": "driver is here", "on_trip": "on the trip", "finished": "trip done", "cancelled": "cancelled"}


def status_card(ride: dict) -> dict:
    eta = f"{ride['eta_min']} min away" if ride.get("eta_min") and ride["stage"] in ("driver_found", "arriving") \
        else (f"about {ride['eta_min']} min to go" if ride.get("eta_min") and ride["stage"] == "on_trip" else "")
    fare = f"{ride['fare']:.2f} {ride.get('currency', '')}".strip() if ride.get("fare") else ""
    return {"kind": "ride_status", "title": f"{ride['app']} · {LABELS.get(ride['stage'], 'ride')}",
            "subtitle": " · ".join(x for x in (ride.get("driver"), ride.get("car"), ride.get("plate")) if x),
            "price": fare, "text": eta}


# --- comparing prices by reading the Uber and Lyft screens -----------------------------------
# The phone's ride reader (Android Accessibility, Uber and Lyft only) opens each app with the trip
# filled in and sends the text on screen with where each piece is. Here that becomes ride types,
# prices and minutes away, so fixing the reading when an app changes its screen is a server update.

QUOTES_KEY = "rides:quotes"
PRICE_RE = re.compile(r"(US\$|R\$|S/|[$€£₹])\s?(\d{1,4}(?:[.,]\d{2})?)")
ETA_RE = re.compile(r"(\d{1,2})\s*min", re.I)
_NOT_A_RIDE = re.compile(r"min\b|away|\d{1,2}:\d{2}|%|\b(?:am|pm)\b|off\b|promo|pay|cash|visa|mastercard|amex|"
                         r"choose|confirm|request|schedule|pickup|drop|where to|save|cheaper|faster|ride options|"
                         r"seats?\b|\bper\b", re.I)
CURRENCIES = {"$": "USD", "US$": "USD", "R$": "BRL", "S/": "PEN", "€": "EUR", "£": "GBP", "₹": "INR"}


def _price(text: str) -> tuple[float, str] | None:
    m = PRICE_RE.search(text)
    return (float(m.group(2).replace(",", ".")), CURRENCIES[m.group(1)]) if m else None


def parse_ride_screen(nodes: list[dict]) -> list[dict]:
    """Ride options on an Uber / Lyft screen: [{ride, price, value, currency, eta_min}], cheapest first."""
    nodes = [n for n in nodes if str(n.get("t", "")).strip()]
    options: dict[str, dict] = {}
    for n in nodes:
        text = str(n["t"])
        found = _price(text)
        if not found:
            continue
        # A whole row read as one ("UberX, 4 min away, $23.40")?
        parts = [p.strip() for p in re.split(r"[,•·|\n]", text) if p.strip()]
        name = next((p for p in parts if not PRICE_RE.search(p) and not _NOT_A_RIDE.search(p)
                     and re.search(r"[A-Za-z]", p) and len(p) <= 30), "")
        top, bottom, left = n.get("y", 0), n.get("b", 0), n.get("x", 0)
        row = [m for m in nodes if m is not n and m.get("y", 0) < bottom + 25 and m.get("b", 0) > top - 70
               and m.get("x", 0) < left]
        if not name:
            names = sorted((m for m in row if not PRICE_RE.search(str(m["t"])) and not _NOT_A_RIDE.search(str(m["t"]))
                            and re.search(r"[A-Za-z]", str(m["t"])) and len(str(m["t"])) <= 30),
                           key=lambda m: (m.get("y", 0), m.get("x", 0)))
            name = str(names[0]["t"]).strip() if names else ""
        if not name:
            continue
        eta = ETA_RE.search(text) or next((e for e in (ETA_RE.search(str(m["t"])) for m in row) if e), None)
        value, currency = found
        old = options.get(name.lower())
        if old and old["value"] <= value:  # a crossed-out old price or a repeat: keep the lower
            continue
        options[name.lower()] = {"ride": name, "price": PRICE_RE.search(text).group(0).replace(" ", ""),
                                 "value": value, "currency": currency,
                                 "eta_min": int(eta.group(1)) if eta else None}
    return sorted(options.values(), key=lambda o: o["value"])[:8]


def last_quotes(memory: Memory) -> dict | None:
    try:
        quote = json.loads(memory.get(QUOTES_KEY) or "null")
    except json.JSONDecodeError:
        return None
    return quote if quote and time.time() - quote.get("at", 0) < 30 * 60 else None


def take_screen(memory: Memory, msg: dict) -> tuple[str | None, list[dict]]:
    """One app's screen from the phone. When the last one is in: (what to say, cards)."""
    quote = last_quotes(memory)
    if not quote or quote["id"] != msg.get("compare"):
        return None, []
    if msg.get("error") == "reader_off":
        return ("To compare prices I need Read Uber and Lyft prices switched on: Buddy Settings, then turn on "
                "Buddy ride prices in Accessibility. I've opened Uber for now; Lyft opens when you come back."), []
    app = str(msg.get("app", ""))[:10]
    if app in ("Uber", "Lyft"):
        quote["apps"][app] = parse_ride_screen(msg.get("nodes") or [])
        log.info("ride prices read from %s: %s", app, [(o["ride"], o["price"]) for o in quote["apps"][app]])
        memory.set(QUOTES_KEY, json.dumps(quote))
    if not msg.get("last"):
        return None, []
    return compare_text(quote), compare_cards(quote)


def _option(o: dict) -> str:
    return f"{o['ride']} at {o['price']}" + (f", {o['eta_min']} min away" if o.get("eta_min") else "")


def compare_text(quote: dict) -> str:
    best = {app: opts[0] for app, opts in quote["apps"].items() if opts}
    missed = [app for app in ("Uber", "Lyft") if not quote["apps"].get(app)]
    if not best:
        return ("I couldn't read either price. The app may have needed a tap, a sign-in or a pickup confirmation; "
                "the screen has to stay unlocked while I read.")
    if len(best) == 1:
        (app, o), = best.items()
        return f"{app}'s cheapest is {_option(o)}. I couldn't read {missed[0]}'s prices."
    uber, lyft = best["Uber"], best["Lyft"]
    if uber["currency"] == lyft["currency"] and abs(uber["value"] - lyft["value"]) >= 0.01:
        cheap, other = ("Uber", "Lyft") if uber["value"] < lyft["value"] else ("Lyft", "Uber")
        diff = abs(uber["value"] - lyft["value"])
        return (f"{cheap} is cheaper by {diff:.2f}: {_option(best[cheap])}. {other}'s cheapest is "
                f"{_option(best[other])}. Want me to open {cheap}?")
    return f"About the same: Uber {_option(uber)}; Lyft {_option(lyft)}. Want me to open one?"


def compare_cards(quote: dict) -> list[dict]:
    links = {link["label"].split()[0]: link for link in quote.get("links", [])}
    out = []
    for app, opts in quote["apps"].items():
        if not opts or app not in links:
            continue
        out.append({"kind": "ride", "title": f"{app} to {quote['to']}", "price": opts[0]["price"],
                    "subtitle": f"{app} · cheapest {opts[0]['ride']}",
                    "rows": [{"label": o["ride"], "value": o["price"] + (f" · {o['eta_min']} min" if o.get("eta_min")
                                                                         else "")} for o in opts[:4]],
                    "url": links[app]["url"], "app_url": links[app]["app"], "text": "Tap to open and book"})
    return sorted(out, key=lambda c: next(o["value"] for o in quote["apps"][c["title"].split()[0]]))
