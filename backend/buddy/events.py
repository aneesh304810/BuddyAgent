"""Entertainment: events and movies, with links to buy tickets in the browser or the app. Buddy never pays.

Events (concerts, sports, theatre, comedy): Ticketmaster's Discovery API (Buddy Connect "ticketmaster", a free key).
Each event comes with its own Ticketmaster page, sent to the Buddy app as a card: the owner taps it and buys there (in
the browser or the Ticketmaster app). Other sellers (SeatGeek, StubHub) are offered as searches for the same event.
Movies (AMC, Regal, Fandango): no public API, so showtime links: Google's showtimes for the area, and searches that
land on the AMC and Fandango pages for that film. Only the search words and the owner's city go out, never their
position.
"""

import json
import urllib.parse
from datetime import datetime, timedelta, timezone

from . import cards, gateway
from .memory import Memory
from .tools import ToolError, local_now

LAST_KEY = "events:last"


def city(memory: Memory) -> str:
    """The owner's city from the phone's last shared place ('West Village, New York' -> 'New York'), or ''."""
    from .dining import area  # noqa: PLC0415
    label = area(memory)
    return label.split(",")[-1].strip() if label else ""


def _google(q: str) -> str:
    return "https://www.google.com/search?" + urllib.parse.urlencode({"q": q})


def search_events(memory: Memory, what: str = "", where: str = "", days: int = 30, env=None, fetch=None) -> dict:
    where = (where or "").strip() or city(memory)
    if not gateway.configured("ticketmaster", env):
        return {"events": [], "note": "Ticketmaster isn't set up: sudo bash /opt/buddy/deploy/set.sh "
                                      "BUDDY_TICKETMASTER_KEY (free at developer.ticketmaster.com). Meanwhile: "
                                      + _google(f"{what} events {where}".strip())}
    start = datetime.now(timezone.utc).replace(microsecond=0)
    q = {"apikey": gateway._secret(env, "BUDDY_TICKETMASTER_KEY"), "size": 10, "sort": "date,asc",
         "startDateTime": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
         "endDateTime": (start + timedelta(days=max(1, min(int(days or 30), 180)))).strftime("%Y-%m-%dT%H:%M:%SZ")}
    if what:
        q["keyword"] = " ".join(str(what).split())[:80]
    if where:
        q["city"] = where
    got = gateway.call(memory, "ticketmaster", "/discovery/v2/events.json?" + urllib.parse.urlencode(q), env=env,
                       fetch=fetch, purpose="events") or {}
    out = []
    for e in (got.get("_embedded") or {}).get("events") or []:
        venue = ((e.get("_embedded") or {}).get("venues") or [{}])[0]
        start_ = (e.get("dates") or {}).get("start") or {}
        price = (e.get("priceRanges") or [{}])[0]
        out.append({"name": e.get("name", ""), "date": start_.get("localDate", ""),
                    "time": (start_.get("localTime") or "")[:5], "venue": venue.get("name", ""),
                    "city": (venue.get("city") or {}).get("name", ""),
                    "kind": ((e.get("classifications") or [{}])[0].get("segment") or {}).get("name", ""),
                    "price": (f"{price.get('currency', '')} {price.get('min')}–{price.get('max')}".strip()
                              if price.get("min") is not None else ""),
                    "url": e.get("url", "")})
    memory.set(LAST_KEY, json.dumps(out))
    return {"searched": {"what": what, "city": where}, "events": out}


def tickets(memory: Memory, which) -> dict:
    """The ticket links for one event from the last search, offered to the app as cards to tap."""
    try:
        last = json.loads(memory.get(LAST_KEY) or "[]")
    except ValueError:
        last = []
    ev = None
    if str(which).strip().isdigit() and 0 < int(which) <= len(last):
        ev = last[int(which) - 1]
    else:
        ev = next((e for e in last if str(which).lower() in e["name"].lower()), None)
    if not ev:
        raise ToolError("Search events first, then say which one (its number or name).")
    when = f"{ev['date']} {ev['time']}".strip()
    links = {"ticketmaster": ev["url"],
             "seatgeek": _google(f"{ev['name']} {ev['venue']} {ev['date']} seatgeek"),
             "stubhub": _google(f"{ev['name']} {ev['venue']} {ev['date']} stubhub")}
    shown = [cards.offer({"kind": "link", "title": f"Tickets: {ev['name']}", "subtitle": f"{ev['venue']} · {when}",
                          "text": "Ticketmaster: pick seats and pay there" + (f" ({ev['price']})" if ev["price"] else ""),
                          "url": ev["url"]}) if ev["url"] else None]
    return {"event": ev, "links": links, "cards": [c for c in shown if c],
            "next": "Show the card (show_cards): the owner taps it and buys in the browser or the Ticketmaster app. "
                    "Never buy for them."}


def movies(memory: Memory, film: str, where: str = "", day: str = "") -> dict:
    """Showtime links for a film near the owner: Google's showtimes, AMC and Fandango, offered as cards."""
    film = " ".join(str(film or "").split())[:80]
    if not film:
        raise ToolError("Which film?")
    where = (where or "").strip() or city(memory)
    when = day or local_now().strftime("%A")
    links = {"showtimes": _google(f"{film} showtimes {where} {when}".strip()),
             "amc": _google(f"{film} AMC {where} tickets".strip()),
             "fandango": _google(f"{film} Fandango {where} tickets".strip())}
    ids = [cards.offer({"kind": "link", "title": f"{film}: showtimes", "subtitle": f"{where} · {when}".strip(" ·"),
                        "text": "Google's showtimes for cinemas nearby; tap a time to buy", "url": links["showtimes"]}),
           cards.offer({"kind": "link", "title": f"{film} at AMC", "subtitle": where,
                        "text": "AMC's page for the film: seats and payment in the browser or the AMC app",
                        "url": links["amc"]})]
    return {"film": film, "where": where, "links": links, "cards": [i for i in ids if i],
            "note": "AMC, Regal and Fandango have no public booking API: the owner picks a time and pays on their "
                    "page or app."}


TOOL = {
    "name": "entertainment",
    "description": (
        "Concerts, sports, theatre, comedy and movies, with links to buy tickets. events: what (artist, team, show or "
        "kind, may be empty for 'what's on') and where (default: the owner's city), days ahead (default 30). "
        "tickets: which (number or name from the last events search) gives the Ticketmaster page as a card (plus "
        "SeatGeek and StubHub searches). movies: film, where, day gives showtime links (Google, AMC, Fandango) as "
        "cards. Show the cards with show_cards: the owner taps one and pays in the browser or the app. Never buy or "
        "pay for them."),
    "input_schema": {"type": "object", "properties": {
        "action": {"type": "string", "enum": ["events", "tickets", "movies"]},
        "what": {"type": "string"}, "where": {"type": "string"}, "days": {"type": "integer"},
        "which": {"type": "string"}, "film": {"type": "string"}, "day": {"type": "string"}},
        "required": ["action"], "additionalProperties": False},
}


def run_tool(memory: Memory, args: dict) -> str:
    action = args.get("action")
    if action == "events":
        return json.dumps(search_events(memory, args.get("what", ""), args.get("where", ""), args.get("days") or 30),
                          ensure_ascii=False)
    if action == "tickets":
        return json.dumps(tickets(memory, args.get("which", "1")), ensure_ascii=False)
    if action == "movies":
        return json.dumps(movies(memory, args.get("film", ""), args.get("where", ""), args.get("day", "")),
                          ensure_ascii=False)
    raise ToolError("Use events, tickets or movies.")


def check(memory=None, env=None, fetch=None) -> None:
    """Buddy Connect's Test: one small search."""
    q = urllib.parse.urlencode({"apikey": gateway._secret(env, "BUDDY_TICKETMASTER_KEY"), "size": 1,
                                "keyword": "concert"})
    gateway.call(memory, "ticketmaster", "/discovery/v2/events.json?" + q, env=env, fetch=fetch, purpose="health")
