"""Restaurants: find a table and book it, always after the owner's yes.

Finding: Google Places (the official API, Buddy Connect "google_places") when its key is set, else the Google Maps
results Buddy already gets through Serper. Either way only the search words and a neighbourhood or city go out
("Italian in Chelsea, New York"), never the owner's exact position.

Booking, three ways, the owner picks:
  link   booking pages with the party size, date and time filled in where the site takes them: OpenTable, Resy, the
         restaurant's own site, and Google Maps (its "Reserve a table" for places that take bookings through Google).
         The owner taps the final button there: Buddy never confirms or pays on its own.
  call   Buddy's phone agent calls the restaurant (calls.py, kind reservation) after the owner's yes; it shares only
         the owner's name and callback number, never a card number (if they need a card to hold the table, Buddy
         says the owner will book online or call back).
OpenTable, Resy and Yelp only let approved business partners book through their systems, so links are the way in.
"""

import json
import urllib.parse
from datetime import datetime

from . import gateway
from .memory import Memory
from .tools import ToolError, local_now

FIELDS = ("places.id,places.displayName,places.formattedAddress,places.rating,places.userRatingCount,"
          "places.priceLevel,places.primaryTypeDisplayName,places.currentOpeningHours.openNow,places.websiteUri,"
          "places.nationalPhoneNumber,places.internationalPhoneNumber,places.googleMapsUri,places.reservable,"
          "places.servesVegetarianFood,places.editorialSummary")
PRICE = {"PRICE_LEVEL_INEXPENSIVE": "$", "PRICE_LEVEL_MODERATE": "$$", "PRICE_LEVEL_EXPENSIVE": "$$$",
         "PRICE_LEVEL_VERY_EXPENSIVE": "$$$$", "PRICE_LEVEL_FREE": "free"}
LAST_KEY = "dining:last"  # the last search's places, so "book the second one" works


def area(memory: Memory) -> str:
    """Where to search: the neighbourhood and city the phone last shared (never coordinates), or ''."""
    try:
        from .location import Location  # noqa: PLC0415
        return Location(memory).label()
    except Exception:
        return ""


def _places_api(memory, text: str, env=None, fetch=None) -> list[dict]:
    got = gateway.call(memory, "google_places", "/v1/places:searchText",
                       json_body={"textQuery": text, "maxResultCount": 8, "includedType": "restaurant"},
                       headers={"X-Goog-FieldMask": FIELDS}, env=env, fetch=fetch, purpose="dining") or {}
    out = []
    for p in got.get("places") or []:
        out.append({
            "name": (p.get("displayName") or {}).get("text", ""), "address": p.get("formattedAddress", ""),
            "rating": p.get("rating"), "reviews": p.get("userRatingCount"), "price": PRICE.get(p.get("priceLevel"), ""),
            "kind": (p.get("primaryTypeDisplayName") or {}).get("text", ""),
            "open_now": (p.get("currentOpeningHours") or {}).get("openNow"),
            "phone": p.get("internationalPhoneNumber") or p.get("nationalPhoneNumber") or "",
            "website": p.get("websiteUri", ""), "maps": p.get("googleMapsUri", ""),
            "takes_reservations": p.get("reservable"), "vegetarian": p.get("servesVegetarianFood"),
            "about": (p.get("editorialSummary") or {}).get("text", ""), "id": p.get("id", "")})
    return out


def _serper(memory, text: str, env=None) -> list[dict]:
    from .social import Social  # noqa: PLC0415
    out = []
    for p in Social(env=env).places(text, num=8):
        out.append({"name": p.get("title", ""), "address": p.get("address", ""), "rating": p.get("rating"),
                    "reviews": p.get("ratingCount"), "price": p.get("priceLevel", ""), "kind": p.get("category", ""),
                    "phone": p.get("phoneNumber", ""), "website": p.get("website", ""),
                    "maps": f"https://www.google.com/maps/place/?q=place_id:{p['cid']}" if p.get("cid") else "",
                    "takes_reservations": None, "id": ""})
    return out


def search(memory: Memory, what: str, where: str = "", env=None, fetch=None) -> dict:
    """Restaurants for `what` (a cuisine, dish, vibe or name) in `where` (default: the owner's neighbourhood)."""
    where = (where or "").strip() or area(memory)
    what = " ".join(str(what or "restaurants").split())[:80]
    text = f"{what} in {where}" if where else what
    if gateway.configured("google_places", env):
        found, source = _places_api(memory, text, env, fetch), "Google Places"
    else:
        found, source = _serper(memory, text, env), "Google Maps (via Serper)"
    found = [p for p in found if p["name"]][:8]
    memory.set(LAST_KEY, json.dumps(found))
    return {"searched": text, "source": source, "places": found,
            "note": "" if where else "No area known: ask where, or turn on location sharing in the app."}


def _when(text: str) -> datetime:
    try:
        return datetime.fromisoformat(str(text).strip())
    except ValueError as e:
        raise ToolError("Give the time as YYYY-MM-DDTHH:MM, e.g. 2026-11-14T19:30.") from e


def _pick(memory: Memory, which) -> dict:
    """A place from the last search: by its number (1-based) or by name."""
    try:
        last = json.loads(memory.get(LAST_KEY) or "[]")
    except ValueError:
        last = []
    if isinstance(which, int) or str(which).strip().isdigit():
        i = int(which) - 1
        if 0 <= i < len(last):
            return last[i]
    name = str(which or "").strip().lower()
    hit = next((p for p in last if name and (name in p["name"].lower() or p["name"].lower() in name)), None)
    if not hit:
        raise ToolError("Search first, then say which restaurant (its number or name).")
    return hit


def booking_links(place: dict, party: int, when: datetime) -> dict:
    """Pages to book on, with the details filled in where the site takes them in its address."""
    name = place["name"]
    city = (place.get("address") or "").split(",")[-2].strip() if "," in (place.get("address") or "") else ""
    links = {
        "opentable": "https://www.opentable.com/s?" + urllib.parse.urlencode(
            {"term": f"{name} {city}".strip(), "covers": party, "dateTime": when.strftime("%Y-%m-%dT%H:%M")}),
        "resy": "https://www.google.com/search?" + urllib.parse.urlencode({"q": f"{name} {city} resy.com"}),
    }
    if place.get("website"):
        links["restaurant_site"] = place["website"]
    if place.get("maps"):
        links["google_maps"] = place["maps"]  # "Reserve a table" there when the place books through Google
    return links


def book(memory: Memory, which, party: int, when_text: str) -> dict:
    place = _pick(memory, which)
    when = _when(when_text)
    if when.tzinfo is None and when < local_now().replace(tzinfo=None):
        raise ToolError("That time has already passed.")
    party = max(1, min(int(party or 2), 20))
    links = booking_links(place, party, when)
    label = f"{party} people · {when.strftime('%a %d %b, %I:%M %p')}"
    from . import cards  # noqa: PLC0415  tappable in the app: the owner books and pays (if asked) on the page
    shown = [cards.offer({"kind": "link", "title": f"Book {place['name']} on OpenTable", "subtitle": label,
                          "text": "Pick the time and confirm there", "url": links["opentable"]})]
    for key, title in (("restaurant_site", "the restaurant's site"), ("google_maps", "Google Maps (Reserve a table)")):
        if links.get(key):
            shown.append(cards.offer({"kind": "link", "title": f"{place['name']}: {title}", "subtitle": label,
                                      "url": links[key]}))
    return {"restaurant": place["name"], "party": party, "when": when.strftime("%A %d %B, %I:%M %p"),
            "phone": place.get("phone", ""), "links": links, "cards": [c for c in shown if c],
            "takes_reservations": place.get("takes_reservations"),
            "next": "Show the cards (show_cards; the owner taps one and confirms there), or offer to call them: phone_call with "
                    "kind reservation, the number above, and the goal 'a table for N on <day> at <time>, flexible "
                    "within 30 minutes'."}


TOOL = {
    "name": "restaurants",
    "description": (
        "Find restaurants and help book a table. search: what (cuisine, dish, vibe or a name) and optionally where "
        "(default: the owner's neighbourhood from the phone; only that area is searched, never their exact "
        "position); gives rating, reviews, price, open now, phone, website and whether they take reservations. "
        "Pick the best 3 for the owner's taste (find_places has their taste and reviews). book: which (number or "
        "name from the last search), party, when (YYYY-MM-DDTHH:MM); gives booking links (OpenTable, Resy, the "
        "restaurant's site, Google Maps) with the details filled in, and the phone number. The owner taps the final "
        "button on the site; or, after the owner says so, call the restaurant with phone_call kind reservation. "
        "Never book or confirm on your own."),
    "input_schema": {"type": "object", "properties": {
        "action": {"type": "string", "enum": ["search", "book"]},
        "what": {"type": "string"}, "where": {"type": "string"},
        "which": {"type": "string", "description": "book: the restaurant's number in the last search, or its name"},
        "party": {"type": "integer"}, "when": {"type": "string", "description": "YYYY-MM-DDTHH:MM, local time"}},
        "required": ["action"], "additionalProperties": False},
}


def run_tool(memory: Memory, args: dict) -> str:
    if args.get("action") == "search":
        return json.dumps(search(memory, args.get("what", ""), args.get("where", "")), ensure_ascii=False)
    if args.get("action") == "book":
        return json.dumps(book(memory, args.get("which", "1"), args.get("party") or 2, args.get("when", "")),
                          ensure_ascii=False)
    raise ToolError("Use search or book.")


def check(memory=None, env=None, fetch=None) -> None:
    """Buddy Connect's Test: one small search."""
    _places_api(memory, "pizza in New York", env, fetch)

