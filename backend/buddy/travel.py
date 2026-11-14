"""Travel: flights and hotels from several sources, compared on what you'd really pay, with the catches.

Sources (each an adapter returning the same Offer shape; the rest of Buddy never knows which one):
  duffel         live airline offers you could book (later), with bag allowance and change / refund rules
                 for each fare                                       BUDDY_DUFFEL_TOKEN
  google         Google Flights and Google Hotels through SerpApi: the market benchmark, Google's
                 low / typical / high price rating, and a link to book from     BUDDY_SERPAPI_KEY
  travelpayouts  the cheapest fares Aviasales travellers found lately, for cheapest dates
                                                                     BUDDY_TRAVELPAYOUTS_TOKEN

What Buddy adds on top (all in code, on the server):
  true total     fare + the bags you travel with + seat + payment fees, or room + taxes + fees; a fee
                 the source doesn't price is estimated and marked as an estimate
  caveats        basic economy, no carry-on, bags for a fee, no refunds or a change fee, tight or
                 overnight layovers, changing airports, often delayed, long trips, red-eyes; for hotels
                 taxes and fees on top, resort and cleaning fees, no free cancellation, parking, Wi-Fi,
                 vacation-rental rules, and what reviews say about surprise fees
  ranking        best value (true total, plus time and stops weighed in), cheapest, fastest; the same
                 flight from two sources shown once, with both prices
  history        every search's prices are kept, so "18% below the usual for this route" is possible
  links          each offer opens where it can be booked (Google Flights, the hotel, Kayak, Aviasales);
                 Buddy never books or pays by itself

Every call to an outside API is logged (api_calls) with its cost, for Insights.
"""

import asyncio
import json
import logging
import os
import re
import secrets
import statistics
import threading
import time
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone

from .memory import Memory
from .tools import ToolError, require_https

log = logging.getLogger(__name__)

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS travel_searches (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,                -- flights | hotels
    query TEXT NOT NULL,               -- JSON
    title TEXT NOT NULL,
    results TEXT NOT NULL,             -- JSON: offers, insights, problems
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS travel_prices (  -- every price seen, for "below the usual"
    route TEXT NOT NULL,               -- MIA-LIM, or hotels:<place>
    day TEXT NOT NULL,                 -- travel date (or check-in)
    source TEXT NOT NULL,
    price REAL NOT NULL,
    currency TEXT NOT NULL,
    seen_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS travel_prices_route ON travel_prices (route, currency);
CREATE TABLE IF NOT EXISTS travel_watches (  -- routes checked daily for a cheap fare (Travelpayouts, free)
    id INTEGER PRIMARY KEY,
    origin TEXT NOT NULL,
    destination TEXT NOT NULL,
    month TEXT NOT NULL,               -- YYYY-MM to fly in
    return_month TEXT NOT NULL DEFAULT '',  -- '' = one way
    target REAL,                       -- tell me at or under this
    currency TEXT NOT NULL,
    best REAL,                         -- lowest fare seen since watching
    best_day TEXT,
    best_link TEXT NOT NULL DEFAULT '',
    alerted REAL,                      -- the fare last told, so a drop is said once
    checked_at TEXT,
    created_at TEXT NOT NULL,
    UNIQUE (origin, destination, month, return_month)
);
CREATE TABLE IF NOT EXISTS travel_patterns (  -- what prices are like, without keeping every price
    route TEXT NOT NULL,
    month INTEGER NOT NULL,            -- travel month 1-12
    lead TEXT NOT NULL,                -- how far ahead it was priced: 0-7, 8-21, 22-60, 61-120, 120+
    weekday INTEGER NOT NULL,          -- travel weekday, 0 = Monday
    currency TEXT NOT NULL,
    n REAL NOT NULL,                   -- weighted count (old prices fade: half weight after a year)
    mean REAL NOT NULL,
    m2 REAL NOT NULL,                  -- for the spread (Welford)
    low REAL NOT NULL,
    high REAL NOT NULL,
    hist TEXT NOT NULL,                -- {band: weight}, bands 10% apart, for "cheaper than X% of prices"
    updated_at TEXT NOT NULL,
    PRIMARY KEY (route, month, lead, weekday, currency)
);
CREATE TABLE IF NOT EXISTS api_calls (      -- outside APIs (not AI models): for Insights
    id INTEGER PRIMARY KEY,
    at TEXT NOT NULL,
    provider TEXT NOT NULL,            -- duffel | serpapi | travelpayouts | serper ...
    endpoint TEXT NOT NULL,
    purpose TEXT NOT NULL DEFAULT '',
    ok INTEGER NOT NULL DEFAULT 1,
    seconds REAL,
    cost_usd REAL NOT NULL DEFAULT 0,
    error TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS api_calls_at ON api_calls (at);
"""

PROFILE_KEY = "travel:profile"
LEARNED_KEY = "travel:learned"
RAW_DAYS = 90                      # raw prices kept this long, then rolled into travel_patterns
HALF_LIFE_DAYS = 365               # a pattern's old prices count half after this long
LEADS = ((0, 7, "0-7"), (8, 21, "8-21"), (22, 60, "22-60"), (61, 120, "61-120"), (121, 10 ** 5, "120+"))
WEEKDAYS = ("Mondays", "Tuesdays", "Wednesdays", "Thursdays", "Fridays", "Saturdays", "Sundays")
MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
          "November", "December")     # what the user's own choices say they value
DEFAULT_PROFILE = {"checked_bags": 0, "carry_on": True, "seat": "any", "cabin": "economy", "max_stops": 2,
                   "free_cancellation": False, "currency": "USD", "nearby_airports": True, "flexible_days": 3}
# What a fee the source doesn't price usually costs (USD, per flight direction): marked "estimate".
CHECKED_BAG_ESTIMATE = 35.0
CARRY_ON_ESTIMATE = 45.0          # on airlines that charge for it (basic economy, ultra low-cost)
SEAT_ESTIMATE = 20.0
HOUR_VALUE = 15.0                 # best value: each hour longer than the fastest counts as this much
STOP_VALUE = 25.0
ULCC = ("spirit", "frontier", "allegiant", "volaris", "viva", "jetsmart", "sky airline", "flair", "ryanair",
        "wizz", "easyjet", "indigo", "akasa", "air india express", "spicejet", "flynas", "flydubai", "air arabia")
# Airports that serve the same place: searched together on Google when nearby_airports is on.
METRO = [("MIA", "FLL", "PBI"), ("JFK", "LGA", "EWR"), ("LAX", "BUR", "LGB", "SNA", "ONT"), ("SFO", "OAK", "SJC"),
         ("ORD", "MDW"), ("IAD", "DCA", "BWI"), ("DFW", "DAL"), ("IAH", "HOU"), ("BOS", "PVD", "MHT"),
         ("MCO", "SFB"), ("TPA", "PIE"), ("LHR", "LGW", "STN", "LTN", "LCY"), ("CDG", "ORY"), ("FCO", "CIA"),
         ("MXP", "LIN", "BGY"), ("NRT", "HND"), ("ICN", "GMP"), ("DXB", "DWC", "SHJ"), ("BOM", "NMI"),
         ("DEL",), ("GRU", "CGH", "VCP"), ("GIG", "SDU"), ("EZE", "AEP"), ("SCL",), ("LIM",), ("BOG",),
         ("COK",), ("TRV",), ("BLR",), ("MAA",)]
COST_DEFAULTS = {"duffel": "BUDDY_DUFFEL_COST_PER_SEARCH", "serpapi": "BUDDY_SERPAPI_COST_PER_SEARCH",
                 "travelpayouts": "", "serper": ""}
COST_FALLBACK = {"duffel": 0.0, "serpapi": 0.0, "travelpayouts": 0.0, "serper": 0.001}


# --- outside calls ---------------------------------------------------------------------------------------
def api_cost(provider: str) -> float:
    key = COST_DEFAULTS.get(provider, "")
    try:
        return float(os.environ.get(key) or COST_FALLBACK.get(provider, 0.0)) if key else COST_FALLBACK.get(provider, 0.0)
    except ValueError:
        return COST_FALLBACK.get(provider, 0.0)


def log_call(memory: Memory, provider: str, endpoint: str, ok: bool, seconds: float, purpose: str = "",
             error: str = "", cost: float | None = None) -> None:
    try:
        with _db_lock:
            _log(memory, provider, endpoint, ok, seconds, purpose, error, cost)
    except Exception:
        log.exception("couldn't log the API call")


_db_lock = threading.Lock()


def _log(memory, provider, endpoint, ok, seconds, purpose, error, cost) -> None:
    """Searches run in parallel threads on one connection: the lock keeps their writes apart."""
    memory.db.execute("INSERT INTO api_calls (at, provider, endpoint, purpose, ok, seconds, cost_usd, error) "
                      "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                      (datetime.now(timezone.utc).isoformat(), provider, endpoint[:60], purpose[:40], int(ok),
                       round(seconds, 2), api_cost(provider) if cost is None else cost, error[:200]))
    memory.db.execute("DELETE FROM api_calls WHERE at < ?",
                      ((datetime.now(timezone.utc) - timedelta(days=400)).isoformat(),))
    memory.db.commit()


def _http(method: str, url: str, headers: dict | None = None, body: dict | None = None, timeout: int = 60) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(require_https(url), data=data, method=method,
                                 headers={"Accept": "application/json", **({"Content-Type": "application/json"}
                                                                           if data else {}), **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:  # https only  # nosec B310
            return json.loads(r.read(30_000_000) or b"{}")
    except urllib.error.HTTPError as e:
        try:
            detail = json.loads(e.read(200_000))
        except Exception:
            detail = {}
        raise SourceError(e.code, _error_text(detail) or str(e)) from e


def _error_text(detail: dict) -> str:
    if isinstance(detail.get("errors"), list) and detail["errors"]:
        return "; ".join(str(x.get("message") or x.get("title") or x) for x in detail["errors"])[:200]
    return str(detail.get("error") or detail.get("message") or "")[:200]


class SourceError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(f"{status}: {message}")
        self.status, self.message = status, message


# --- small helpers --------------------------------------------------------------------------------------
def _minutes(text) -> int:
    """'PT11H25M' (Duffel) or 685 (minutes) -> minutes."""
    if isinstance(text, (int, float)):
        return int(text)
    m = re.match(r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?", str(text or ""))
    return (int(m.group(1) or 0) * 1440 + int(m.group(2) or 0) * 60 + int(m.group(3) or 0)) if m else 0


def _hm(minutes: int) -> str:
    return f"{minutes // 60}h {minutes % 60:02d}m" if minutes else ""


def _iata(code: str) -> str:
    code = str(code or "").strip().upper()
    if not re.fullmatch(r"[A-Z]{3}", code):
        raise ToolError(f"'{code}' isn't an airport code: use three letters, like MIA or LIM.")
    return code


def _day(value: str) -> str:
    try:
        return date.fromisoformat(str(value)[:10]).isoformat()
    except ValueError:
        raise ToolError(f"'{value}' isn't a date: use YYYY-MM-DD.") from None


def _flag(level: str, text: str) -> dict:
    return {"level": level, "text": text}  # level: warn (costs or risks you), info, good


def nearby(code: str) -> list[str]:
    return list(next((m for m in METRO if code in m), (code,)))


def kayak_link(origin: str, destination: str, depart: str, ret: str = "", adults: int = 1) -> str:
    trip = f"{origin}-{destination}/{depart}" + (f"/{ret}" if ret else "")
    return f"https://www.kayak.com/flights/{trip}/{adults}adults?sort=price_a"


def google_flights_link(origin: str, destination: str, depart: str, ret: str = "") -> str:
    q = f"Flights from {origin} to {destination} on {depart}" + (f" returning {ret}" if ret else " one way")
    return "https://www.google.com/travel/flights?" + urllib.parse.urlencode({"q": q, "hl": "en"})


# --- adapters -------------------------------------------------------------------------------------------
class Duffel:
    name = "duffel"
    base = "https://api.duffel.com"

    def __init__(self, memory: Memory, env, http):
        self.memory, self.env, self.http = memory, env, http

    def configured(self) -> bool:
        return bool((self.env.get("BUDDY_DUFFEL_TOKEN") or "").strip())

    @property
    def test_mode(self) -> bool:
        return (self.env.get("BUDDY_DUFFEL_TOKEN") or "").strip().startswith("duffel_test")

    def _call(self, method: str, path: str, body: dict | None = None) -> dict:
        started = time.monotonic()
        try:
            data = self.http(method, self.base + path, {
                "Authorization": f"Bearer {(self.env.get('BUDDY_DUFFEL_TOKEN') or '').strip()}",
                "Duffel-Version": self.env.get("BUDDY_DUFFEL_VERSION") or "v2"}, body)
            log_call(self.memory, "duffel", path.split("?")[0], True, time.monotonic() - started, "travel",
                     cost=api_cost("duffel") if "offer_requests" in path else 0.0)
            return data
        except Exception as e:
            log_call(self.memory, "duffel", path.split("?")[0], False, time.monotonic() - started, "travel", str(e),
                     cost=0.0)
            raise

    def flights(self, q: dict, profile: dict) -> list[dict]:
        slices = [{"origin": q["origin"], "destination": q["destination"], "departure_date": q["depart"]}]
        if q.get("return"):
            slices.append({"origin": q["destination"], "destination": q["origin"], "departure_date": q["return"]})
        body = {"data": {"slices": slices, "passengers": [{"type": "adult"}] * q["adults"],
                         "cabin_class": profile.get("cabin") or "economy",
                         "max_connections": min(int(profile.get("max_stops", 2)), 2)}}
        data = self._call("POST", "/air/offer_requests?return_offers=true&supplier_timeout=20000", body)
        offers = (data.get("data") or {}).get("offers") or []
        return [self._offer(o, q) for o in sorted(offers, key=lambda o: float(o.get("total_amount") or 0))[:30]]

    def _offer(self, o: dict, q: dict) -> dict:
        legs, bags_in, fare_brand = [], {"checked": 0, "carry_on": 0}, ""
        for s in o.get("slices") or []:
            segs = s.get("segments") or []
            fare_brand = fare_brand or (s.get("fare_brand_name") or "")
            for seg in segs:
                for p in (seg.get("passengers") or [])[:1]:
                    for b in p.get("baggages") or []:
                        kind = "checked" if b.get("type") == "checked" else "carry_on"
                        bags_in[kind] = max(bags_in[kind], int(b.get("quantity") or 0))
            legs.append({
                "from": (s.get("origin") or {}).get("iata_code", ""), "to": (s.get("destination") or {}).get("iata_code", ""),
                "depart": (segs[0].get("departing_at") if segs else "") or "",
                "arrive": (segs[-1].get("arriving_at") if segs else "") or "",
                "minutes": _minutes(s.get("duration")),
                "stops": max(0, len(segs) - 1),
                "carriers": sorted({(g.get("marketing_carrier") or {}).get("name", "") for g in segs} - {""}),
                "flights": [f"{(g.get('marketing_carrier') or {}).get('iata_code', '')}"
                            f"{g.get('marketing_carrier_flight_number', '')}" for g in segs],
                "layovers": [{"airport": (a.get("destination") or {}).get("iata_code", ""),
                              "change_airport": (a.get("destination") or {}).get("iata_code") !=
                              (b.get("origin") or {}).get("iata_code"),
                              "minutes": _gap(a.get("arriving_at"), b.get("departing_at"))}
                             for a, b in zip(segs, segs[1:])],
            })
        cond = o.get("conditions") or {}
        return {"source": "duffel", "kind": "flight", "id": o.get("id", ""), "price": float(o.get("total_amount") or 0),
                "currency": o.get("total_currency") or "USD", "base": _num(o.get("base_amount")),
                "taxes": _num(o.get("tax_amount")), "airline": (o.get("owner") or {}).get("name", ""),
                "legs": legs, "bags_included": bags_in, "fare_brand": fare_brand,
                "refund": _condition(cond.get("refund_before_departure")),
                "change": _condition(cond.get("change_before_departure")),
                "expires_at": o.get("expires_at") or "", "instant_payment":
                    bool((o.get("payment_requirements") or {}).get("requires_instant_payment")),
                "bookable": True, "test": self.test_mode,
                "link": google_flights_link(q["origin"], q["destination"], q["depart"], q.get("return", ""))}

    def refresh(self, offer_id: str) -> dict:
        data = self._call("GET", f"/air/offers/{urllib.parse.quote(offer_id)}?return_available_services=true")
        o = data.get("data") or {}
        bag_prices = [float(s.get("total_amount") or 0) for s in o.get("available_services") or []
                      if s.get("type") == "baggage"]
        return {"price": float(o.get("total_amount") or 0), "currency": o.get("total_currency") or "",
                "expires_at": o.get("expires_at") or "", "bag_price": min(bag_prices) if bag_prices else None}


def _gap(a: str | None, b: str | None) -> int:
    try:
        return int((datetime.fromisoformat(str(b)) - datetime.fromisoformat(str(a))).total_seconds() // 60)
    except (TypeError, ValueError):
        return 0


def _num(v) -> float | None:
    try:
        return round(float(v), 2)
    except (TypeError, ValueError):
        return None


def _condition(c) -> dict:
    if c is None:
        return {"known": False}
    return {"known": True, "allowed": bool(c.get("allowed")), "penalty": _num(c.get("penalty_amount")),
            "currency": c.get("penalty_currency") or ""}


class Google:
    """Google Flights and Google Hotels through SerpApi."""
    name = "google"
    base = "https://serpapi.com/search.json"

    def __init__(self, memory: Memory, env, http):
        self.memory, self.env, self.http = memory, env, http

    def configured(self) -> bool:
        return bool((self.env.get("BUDDY_SERPAPI_KEY") or "").strip())

    def _search(self, params: dict) -> dict:
        started = time.monotonic()
        url = self.base + "?" + urllib.parse.urlencode({**params, "api_key": (self.env.get("BUDDY_SERPAPI_KEY") or "").strip()})
        try:
            data = self.http("GET", url, None, None)
            if data.get("error"):
                raise SourceError(200, str(data["error"]))
            log_call(self.memory, "serpapi", params.get("engine", ""), True, time.monotonic() - started, "travel")
            return data
        except Exception as e:
            log_call(self.memory, "serpapi", params.get("engine", ""), False, time.monotonic() - started, "travel",
                     str(e), cost=0.0)
            raise

    def account(self) -> dict | None:
        """Searches left this month (SerpApi doesn't count this call)."""
        if not self.configured():
            return None
        try:
            cached = json.loads(self.memory.get("serpapi:account") or "null")
            if cached and cached["at"] > time.time() - 900:
                return cached["data"]
        except (json.JSONDecodeError, KeyError, TypeError):
            pass
        try:
            data = self.http("GET", "https://serpapi.com/account.json?" + urllib.parse.urlencode(
                {"api_key": (self.env.get("BUDDY_SERPAPI_KEY") or "").strip()}), None, None, 15)
            info = {k: data.get(k) for k in ("plan_name", "searches_per_month", "plan_searches_left",
                                             "this_month_usage", "total_searches_left")}
            self.memory.set("serpapi:account", json.dumps({"at": time.time(), "data": info}))
            return info
        except Exception:
            return None

    def flights(self, q: dict, profile: dict) -> tuple[list[dict], dict]:
        near = profile.get("nearby_airports", True)
        params = {"engine": "google_flights",
                  "departure_id": ",".join(nearby(q["origin"])) if near else q["origin"],
                  "arrival_id": ",".join(nearby(q["destination"])) if near else q["destination"],
                  "outbound_date": q["depart"], "type": 1 if q.get("return") else 2, "adults": q["adults"],
                  "currency": q["currency"], "hl": "en",
                  "travel_class": {"economy": 1, "premium_economy": 2, "business": 3, "first": 4}.get(
                      profile.get("cabin") or "economy", 1)}
        if q.get("return"):
            params["return_date"] = q["return"]
        data = self._search(params)
        link = (data.get("search_metadata") or {}).get("google_flights_url") or google_flights_link(
            q["origin"], q["destination"], q["depart"], q.get("return", ""))
        offers = []
        for group, best in (("best_flights", True), ("other_flights", False)):
            for f in data.get(group) or []:
                offers.append(self._flight(f, q, link, best))
        pi = data.get("price_insights") or {}
        insight = {"lowest": pi.get("lowest_price"), "level": pi.get("price_level"),
                   "typical": pi.get("typical_price_range"),
                   "history": [[int(t), float(p)] for t, p in (pi.get("price_history") or []) if p]} if pi else {}
        return offers[:30], insight

    def _flight(self, f: dict, q: dict, link: str, best: bool) -> dict:
        segs = f.get("flights") or []
        notes = [str(x) for x in f.get("extensions") or []]
        for s in segs:
            notes += [str(x) for x in s.get("extensions") or []]
        lay = f.get("layovers") or []
        leg = {"from": ((segs[0].get("departure_airport") or {}).get("id") if segs else q["origin"]) or q["origin"],
               "to": ((segs[-1].get("arrival_airport") or {}).get("id") if segs else q["destination"]) or q["destination"],
               "depart": (segs[0].get("departure_airport") or {}).get("time", "") if segs else "",
               "arrive": (segs[-1].get("arrival_airport") or {}).get("time", "") if segs else "",
               "minutes": int(f.get("total_duration") or 0), "stops": len(lay),
               "carriers": sorted({s.get("airline", "") for s in segs} - {""}),
               "flights": [str(s.get("flight_number", "")).replace(" ", "") for s in segs],
               "layovers": [{"airport": l.get("id", ""), "minutes": int(l.get("duration") or 0),
                             "overnight": bool(l.get("overnight")), "change_airport": False} for l in lay],
               "often_delayed": any(s.get("often_delayed_by_over_30_min") for s in segs),
               "overnight": any(s.get("overnight") for s in segs)}
        return {"source": "google", "kind": "flight", "id": "g" + secrets.token_hex(4), "price": float(f.get("price") or 0),
                "currency": q["currency"], "airline": ", ".join(leg["carriers"]), "legs": [leg], "notes": notes,
                "bags_included": None, "fare_brand": "", "refund": {"known": False}, "change": {"known": False},
                "google_pick": best, "bookable": False, "link": link,
                "round_trip_price": bool(q.get("return"))}

    def hotels(self, q: dict, profile: dict) -> tuple[list[dict], dict]:
        params = {"engine": "google_hotels", "q": q["place"], "check_in_date": q["check_in"],
                  "check_out_date": q["check_out"], "adults": q["adults"], "currency": q["currency"], "hl": "en",
                  "gl": "us"}
        if profile.get("free_cancellation"):
            params["free_cancellation"] = "true"
        data = self._search(params)
        link = (data.get("search_metadata") or {}).get("google_hotels_url") or ""
        nights = max(1, (date.fromisoformat(q["check_out"]) - date.fromisoformat(q["check_in"])).days)
        out = []
        for p in (data.get("properties") or [])[:25]:
            rate, total = p.get("rate_per_night") or {}, p.get("total_rate") or {}
            per_night = _num(rate.get("extracted_lowest"))
            before = _num(rate.get("extracted_before_taxes_fees"))
            stay = _num(total.get("extracted_lowest")) or (per_night * nights if per_night else None)
            if not stay:
                continue
            out.append({"source": "google", "kind": "hotel", "id": "h" + secrets.token_hex(4), "name": p.get("name", ""),
                        "type": p.get("type", "hotel"), "price": stay, "per_night": per_night,
                        "per_night_before_fees": before, "nights": nights, "currency": q["currency"],
                        "stars": p.get("extracted_hotel_class"), "rating": p.get("overall_rating"),
                        "reviews": p.get("reviews"), "amenities": p.get("amenities") or [],
                        "excluded": p.get("excluded_amenities") or [], "essential": p.get("essential_info") or [],
                        "deal": p.get("deal_description") or p.get("deal") or "",
                        "check_in_time": p.get("check_in_time", ""), "check_out_time": p.get("check_out_time", ""),
                        "sellers": [{"source": s.get("source", ""), "per_night": _num((s.get("rate_per_night") or {})
                                                                                      .get("extracted_lowest"))}
                                    for s in (p.get("prices") or [])[:6]],
                        "link": p.get("link") or link, "google_link": link, "bookable": False})
        return out, {}


class Travelpayouts:
    name = "travelpayouts"

    def __init__(self, memory: Memory, env, http):
        self.memory, self.env, self.http = memory, env, http

    def configured(self) -> bool:
        return bool((self.env.get("BUDDY_TRAVELPAYOUTS_TOKEN") or "").strip())

    def cheapest(self, origin: str, destination: str, month: str, currency: str = "USD", one_way: bool = True,
                 return_month: str = "") -> list[dict]:
        params = {"origin": origin, "destination": destination, "departure_at": month, "currency": currency.lower(),
                  "sorting": "price", "unique": "false", "limit": 30, "page": 1, "one_way": str(one_way).lower(),
                  "token": (self.env.get("BUDDY_TRAVELPAYOUTS_TOKEN") or "").strip()}
        if return_month:
            params["return_at"] = return_month
        started = time.monotonic()
        try:
            data = self.http("GET", "https://api.travelpayouts.com/aviasales/v3/prices_for_dates?"
                             + urllib.parse.urlencode(params), None, None)
            log_call(self.memory, "travelpayouts", "prices_for_dates", True, time.monotonic() - started, "travel")
        except Exception as e:
            log_call(self.memory, "travelpayouts", "prices_for_dates", False, time.monotonic() - started, "travel", str(e))
            raise
        out = []
        for r in data.get("data") or []:
            out.append({"source": "travelpayouts", "kind": "fare", "price": float(r.get("price") or 0),
                        "currency": currency.upper(), "airline": r.get("airline", ""),
                        "depart": str(r.get("departure_at", ""))[:10], "return": str(r.get("return_at") or "")[:10],
                        "stops": int(r.get("transfers") or 0), "minutes": int(r.get("duration") or 0),
                        "link": ("https://www.aviasales.com" + r["link"]) if str(r.get("link", "")).startswith("/") else "",
                        "found": "found by travellers in the last few days; check the live price"})
        return out


# --- price patterns ------------------------------------------------------------------------------------
import math as _math


def _band(price: float) -> int:
    return int(_math.floor(_math.log(max(price, 1.0)) / _math.log(1.1)))


def _lead(days: int) -> str:
    return next(name for lo, hi, name in LEADS if lo <= max(days, 0) <= hi)


class Summary:
    """Count, mean, spread, low, high and a banded histogram, mergeable (so raw prices can be dropped)."""

    def __init__(self, n=0.0, mean=0.0, m2=0.0, low=float("inf"), high=0.0, hist=None):
        self.n, self.mean, self.m2, self.low, self.high = n, mean, m2, low, high
        self.hist = dict(hist or {})

    def add(self, price: float, weight: float = 1.0) -> None:
        self.merge(Summary(weight, price, 0.0, price, price, {str(_band(price)): weight}))

    def merge(self, o: "Summary") -> None:
        if o.n <= 0:
            return
        n = self.n + o.n
        delta = o.mean - self.mean
        self.mean += delta * o.n / n
        self.m2 += o.m2 + delta * delta * self.n * o.n / n
        self.n = n
        self.low, self.high = min(self.low, o.low), max(self.high, o.high)
        for k, v in o.hist.items():
            self.hist[k] = self.hist.get(k, 0.0) + v

    def fade(self, factor: float) -> None:
        self.n *= factor
        self.m2 *= factor
        self.hist = {k: v * factor for k, v in self.hist.items() if v * factor > 1e-3}

    def share_below(self, price: float) -> float | None:
        """Share of prices seen that were higher than this one (0-1)."""
        total = sum(self.hist.values())
        if total <= 0:
            return None
        b = _band(price)
        above = sum(v for k, v in self.hist.items() if int(k) > b) + 0.5 * self.hist.get(str(b), 0.0)
        return above / total

    def quantile(self, q: float) -> float | None:
        total = sum(self.hist.values())
        if total <= 0:
            return None
        acc = 0.0
        for k in sorted(self.hist, key=int):
            acc += self.hist[k]
            if acc >= q * total:
                return round(1.1 ** (int(k) + 0.5), 2)
        return round(self.high, 2)


# --- the engine -----------------------------------------------------------------------------------------
class Travel:
    def __init__(self, memory: Memory, env=None, http=None):
        self.memory = memory
        memory.db.executescript(SCHEMA_SQL)
        env = os.environ if env is None else env
        http = http or _http
        self.duffel, self.google, self.tp = Duffel(memory, env, http), Google(memory, env, http), \
            Travelpayouts(memory, env, http)

    # profile: how the user travels (asked once, kept)
    def profile(self) -> dict:
        try:
            return {**DEFAULT_PROFILE, **json.loads(self.memory.get(PROFILE_KEY) or "{}")}
        except json.JSONDecodeError:
            return dict(DEFAULT_PROFILE)

    def set_profile(self, **values) -> dict:
        p = self.profile()
        for k, v in values.items():
            if k in DEFAULT_PROFILE and v is not None and v != "":
                p[k] = type(DEFAULT_PROFILE[k])(v) if not isinstance(DEFAULT_PROFILE[k], bool) else \
                    (v if isinstance(v, bool) else str(v).lower() in ("1", "true", "yes"))
        self.memory.set(PROFILE_KEY, json.dumps(p))
        return p

    def sources(self) -> dict:
        return {"duffel": self.duffel.configured(), "duffel_test_mode": self.duffel.configured() and self.duffel.test_mode,
                "google": self.google.configured(), "travelpayouts": self.tp.configured()}

    # --- flights ---
    async def flights(self, origin: str, destination: str, depart: str, ret: str = "", adults: int = 1) -> dict:
        q = {"origin": _iata(origin), "destination": _iata(destination), "depart": _day(depart),
             "return": _day(ret) if ret else "", "adults": max(1, min(int(adults or 1), 9))}
        profile = self.profile()
        q["currency"] = profile["currency"]
        if not any((self.duffel.configured(), self.google.configured(), self.tp.configured())):
            raise ToolError("No travel source is set up: add BUDDY_DUFFEL_TOKEN, BUDDY_SERPAPI_KEY or "
                            "BUDDY_TRAVELPAYOUTS_TOKEN on the server (set.sh).")
        jobs, names = [], []
        if self.duffel.configured():
            jobs.append(asyncio.to_thread(self.duffel.flights, q, profile))
            names.append("duffel")
        if self.google.configured():
            jobs.append(asyncio.to_thread(self.google.flights, q, profile))
            names.append("google")
        if self.tp.configured():
            jobs.append(asyncio.to_thread(self.tp.cheapest, q["origin"], q["destination"], q["depart"][:7],
                                          q["currency"], not q["return"], q["return"][:7]))
            names.append("travelpayouts")
        results = await asyncio.gather(*jobs, return_exceptions=True)
        offers, problems, insight, dates = [], [], {}, []
        for name, got in zip(names, results):
            if isinstance(got, Exception):
                problems.append(f"{name}: {got}"[:200])
                continue
            if name == "google":
                offers += got[0]
                insight = got[1]
            elif name == "travelpayouts":
                dates = got
            else:
                offers += got
        offers = [self._price_flight(o, profile, q) for o in offers if o["price"] > 0]
        offers = _merge_same_flights(offers)
        learned = self.learned()
        ranked = _rank(offers, hour_value=learned["hour_value"])
        route = f"{q['origin']}-{q['destination']}"
        history = self._history(route, q["depart"], q["currency"],
                                [o["true_total"] for o in ranked if o["source"] != "duffel" or not o.get("test")])
        self._keep(route, q["depart"], q["currency"], ranked, insight.get("history") or [], "price", dates)
        insight.pop("history", None)
        cheapest_dates = sorted(dates, key=lambda d: d["price"])[:8]
        advice = flight_advice(ranked, q, insight, history, dates, profile)
        result_pattern: dict = {}
        pick = min((o for o in ranked if not o.get("test")), key=lambda o: o["price"], default=None)
        if pick:  # patterns compare fares before extras, the way every source reports them
            result_pattern = self.pattern(route, q["depart"], q["currency"], pick["price"])
            advice[:0] = pattern_advice(result_pattern, pick["price"], q["currency"], q["depart"], " (fare before extras)")
        if learned["hour_value"]:
            advice.append(f"Ranked with what your past choices show: you usually pay about "
                          f"{learned['hour_value']:.0f} {q['currency']} to save an hour.")
        if learned["airlines"]:
            fav = next(iter(learned["airlines"]))
            mine = [o for o in ranked if fav in (o.get("airline") or "")]
            if mine and mine[0] is not ranked[0]:
                advice.append(f"You often fly {fav}: their best here is {_money(mine[0]['true_total'], q['currency'])}.")
        result = {"offers": ranked, "advice": advice, "google_price_insight": insight, "history": history,
                  "pattern": result_pattern,
                  "cheapest_dates": cheapest_dates, "problems": problems, "profile": profile,
                  "links": {"google": google_flights_link(q["origin"], q["destination"], q["depart"], q["return"]),
                            "kayak": kayak_link(q["origin"], q["destination"], q["depart"], q["return"], q["adults"])},
                  "note": _test_note(ranked)}
        title = f"{q['origin']} → {q['destination']}, {q['depart']}" + (f" – {q['return']}" if q["return"] else "")
        result["search_id"] = self._save("flights", q, title, result)
        return result

    def _price_flight(self, o: dict, profile: dict, q: dict) -> dict:
        directions = 2 if q.get("return") else 1
        want_checked = int(profile.get("checked_bags") or 0)
        extras, flags = [], []
        notes = " ".join(o.get("notes") or []).lower()
        carriers = " ".join(c.lower() for leg in o["legs"] for c in leg.get("carriers", []))
        ulcc = any(x in carriers for x in ULCC)
        basic = bool(re.search(r"basic|light|saver|lite|economy minimum|zero", (o.get("fare_brand") or "").lower())) or \
            "basic economy" in notes
        # bags
        bags = o.get("bags_included")
        if bags is not None:  # Duffel tells us exactly
            missing = max(0, want_checked - bags["checked"])
            if missing:
                extras.append(("checked bags (estimate)", CHECKED_BAG_ESTIMATE * missing * directions * q["adults"], True))
                flags.append(_flag("warn", f"Checked bag not included: about {CHECKED_BAG_ESTIMATE:.0f} {o['currency']} "
                                           f"each way per bag (estimate, added)."))
            if profile.get("carry_on") and bags["carry_on"] == 0:
                extras.append(("carry-on (estimate)", CARRY_ON_ESTIMATE * directions * q["adults"], True))
                flags.append(_flag("warn", "No carry-on bag included, only a small personal item (estimate added)."))
            if bags["checked"]:
                flags.append(_flag("good", f"{bags['checked']} checked bag included."))
        else:  # Google: what its notes say
            if want_checked and re.search(r"checked bag\w* for a fee|checked baggage for a fee|1st checked bag", notes):
                extras.append(("checked bags (estimate)", CHECKED_BAG_ESTIMATE * want_checked * directions * q["adults"], True))
                flags.append(_flag("warn", "Checked bags cost extra on this fare (estimate added)."))
            if re.search(r"carry-on bag\w* not included|no carry-on|carry-on bag\w* for a fee", notes) or \
                    (profile.get("carry_on") and ulcc):
                extras.append(("carry-on (estimate)", CARRY_ON_ESTIMATE * directions * q["adults"], True))
                flags.append(_flag("warn", "Carry-on bag not included (low-cost airline or basic fare): estimate added."))
            elif want_checked and ulcc and not any("checked" in e[0] for e in extras):
                extras.append(("checked bags (estimate)", CHECKED_BAG_ESTIMATE * want_checked * directions * q["adults"], True))
                flags.append(_flag("warn", "Low-cost airline: bags are extra (estimate added)."))
        if basic:
            flags.append(_flag("warn", "Basic economy: usually no seat choice, boards last, and no changes or refunds."))
            if profile.get("seat") not in ("any", "", None):
                extras.append(("seat selection (estimate)", SEAT_ESTIMATE * directions * q["adults"], True))
        # change and refund
        if o["refund"].get("known"):
            if not o["refund"]["allowed"]:
                flags.append(_flag("warn", "No refund if you cancel."))
            elif o["refund"].get("penalty"):
                flags.append(_flag("info", f"Refundable for a {o['refund']['penalty']:.0f} {o['refund']['currency']} fee."))
            else:
                flags.append(_flag("good", "Refundable without a fee."))
        if o["change"].get("known"):
            if not o["change"]["allowed"]:
                flags.append(_flag("warn", "Changes not allowed."))
            elif o["change"].get("penalty"):
                flags.append(_flag("info", f"Changes cost {o['change']['penalty']:.0f} {o['change']['currency']} plus any fare difference."))
            else:
                flags.append(_flag("good", "Free changes (you pay any fare difference)."))
        elif o["source"] == "google":
            flags.append(_flag("info", "Change and refund rules aren't shown here: check before you book."))
        # the journey itself
        for leg in o["legs"]:
            for lay in leg.get("layovers") or []:
                intl = leg["from"][:1] != leg["to"][:1]
                if lay.get("change_airport"):
                    flags.append(_flag("warn", f"You change airports at {lay['airport']}: allow time and transport."))
                if lay.get("minutes") and lay["minutes"] < (90 if intl else 50):
                    flags.append(_flag("warn", f"Tight connection: {lay['minutes']} minutes at {lay['airport']}."))
                if lay.get("overnight") or (lay.get("minutes") or 0) >= 480:
                    flags.append(_flag("warn", f"Overnight or very long layover at {lay['airport']} ({_hm(lay['minutes'])})."))
            if leg.get("often_delayed"):
                flags.append(_flag("info", "One of these flights is often delayed by 30+ minutes."))
            if leg.get("overnight") or re.search(r"T0[0-4]:|^\S+ 0[0-4]:", str(leg.get("depart", ""))):
                flags.append(_flag("info", "Red-eye or very early departure."))
        if "separate tickets" in notes or "self transfer" in notes or "self-transfer" in notes:
            flags.append(_flag("warn", "Separate tickets (self-transfer): you re-check bags, and a missed connection isn't covered."))
        if o.get("instant_payment"):
            flags.append(_flag("info", "Must be paid immediately (no hold)."))
        if o.get("round_trip_price"):
            flags.append(_flag("info", "Price is for the round trip."))
        extra_total = round(sum(v for _, v, _ in extras), 2)
        o["parts"] = {"price": o["price"], **{k: round(v, 2) for k, v, _ in extras}}
        o["true_total"] = round(o["price"] + extra_total, 2)
        o["estimated"] = any(est for _, _, est in extras)
        o["flags"] = _dedupe(flags)
        o["minutes"] = sum(leg.get("minutes") or 0 for leg in o["legs"])
        o["stops"] = sum(leg.get("stops") or 0 for leg in o["legs"])
        o["title"] = f"{o['airline'] or 'Flight'} · " + (" + ".join(
            f"{_time(leg.get('depart'))}→{_time(leg.get('arrive'))}" for leg in o["legs"]))
        o["summary"] = " · ".join(x for x in (
            "nonstop" if o["stops"] == 0 else f"{o['stops']} stop{'s' if o['stops'] > 1 else ''}", _hm(o["minutes"]),
            o.get("fare_brand") or "") if x)
        return o

    # --- hotels ---
    async def hotels(self, place: str, check_in: str, check_out: str, adults: int = 2, check_reviews: bool = True) -> dict:
        if not self.google.configured():
            raise ToolError("Hotel search needs SerpApi (Google Hotels): add BUDDY_SERPAPI_KEY on the server.")
        place = " ".join(str(place).split())[:120]
        if not place:
            raise ToolError("Where? A city or area, like 'Miraflores, Lima'.")
        q = {"place": place, "check_in": _day(check_in), "check_out": _day(check_out),
             "adults": max(1, min(int(adults or 2), 9))}
        if q["check_out"] <= q["check_in"]:
            raise ToolError("Check-out must be after check-in.")
        profile = self.profile()
        q["currency"] = profile["currency"]
        offers, _ = await asyncio.to_thread(self.google.hotels, q, profile)
        offers = [self._price_hotel(o) for o in offers]
        ranked = _rank(offers, hotel=True)
        if check_reviews:
            await self._review_checks(ranked[:3], place)
        history = self._history(f"hotels:{place.lower()}", q["check_in"], q["currency"],
                                [o["true_total"] / o["nights"] for o in ranked])
        self._keep(f"hotels:{place.lower()}", q["check_in"], q["currency"],
                   [{**o, "true_total": o["true_total"] / o["nights"]} for o in ranked], [], "true_total")
        advice = hotel_advice(ranked, history)
        pat = {}
        if ranked:
            cheap = min(ranked, key=lambda o: o["true_total"])
            pat = self.pattern(f"hotels:{place.lower()}", q["check_in"], q["currency"], cheap["true_total"] / cheap["nights"])
            advice[:0] = pattern_advice(pat, cheap["true_total"] / cheap["nights"], q["currency"], q["check_in"], " a night")
        result = {"offers": ranked, "advice": advice, "pattern": pat, "history": history, "problems": [],
                  "profile": profile, "links": {"google": ranked[0]["google_link"] if ranked else ""}}
        result["search_id"] = self._save("hotels", q, f"{place}, {q['check_in']} – {q['check_out']}", result)
        return result

    def _price_hotel(self, o: dict) -> dict:
        flags = []
        text = " ".join(o["amenities"] + o["excluded"] + o["essential"] + [o["deal"], o["type"]]).lower()
        if o["per_night"] and o["per_night_before_fees"] and o["per_night"] > o["per_night_before_fees"]:
            fees = round((o["per_night"] - o["per_night_before_fees"]) * o["nights"], 2)
            flags.append(_flag("info", f"Taxes and fees add {fees:.0f} {o['currency']} for the stay (included in the total)."))
        if "free cancellation" in text:
            flags.append(_flag("good", "Free cancellation offered (check the deadline before booking)."))
        else:
            flags.append(_flag("warn", "Cancellation terms not shown: the cheapest rate is often non-refundable."))
        if "resort" in text:
            flags.append(_flag("warn", "Resorts often add a daily resort fee at the property: check it's in the total."))
        if o["type"].lower() in ("vacation rental", "apartment", "house") or "vacation rental" in text:
            flags.append(_flag("warn", "Vacation rental: check cleaning fee, deposit, house rules and check-out chores."))
        for word, msg in (("parking ($)", "Parking costs extra."), ("paid parking", "Parking costs extra."),
                          ("no free wi-fi", "Wi-Fi isn't free."), ("wi-fi ($)", "Wi-Fi isn't free."),
                          ("no air conditioning", "No air conditioning."), ("breakfast ($)", "Breakfast costs extra.")):
            if word in text:
                flags.append(_flag("info", msg))
        if "free breakfast" in text or "breakfast included" in text:
            flags.append(_flag("good", "Breakfast included."))
        if o.get("deal"):
            flags.append(_flag("good", str(o["deal"])[:80]))
        sellers = [s for s in o["sellers"] if s.get("per_night")]
        if len(sellers) > 1:
            cheapest = min(sellers, key=lambda s: s["per_night"])
            flags.append(_flag("info", f"Cheapest seller: {cheapest['source']} at {cheapest['per_night']:.0f} a night."))
        o["true_total"] = o["price"]
        o["estimated"] = False
        o["parts"] = {"stay (taxes included)": o["price"]}
        o["flags"] = _dedupe(flags)
        stars = f"{int(o['stars'])}★" if o.get("stars") else ""
        rating = f"{o['rating']} ({o['reviews']} reviews)" if o.get("rating") else ""
        o["title"] = o["name"]
        o["summary"] = " · ".join(x for x in (stars, rating, f"{o['per_night']:.0f}/night" if o.get("per_night") else "") if x)
        return o

    async def _review_checks(self, offers: list[dict], place: str) -> None:
        """What reviews say about surprise fees, for the top few (Google search through Serper)."""
        from .social import Social

        social = Social()
        if not social.has_google():
            return

        async def one(o):
            started = time.monotonic()
            try:
                hits = (await asyncio.to_thread(
                    social.google, f'"{o["name"]}" {place} resort fee OR "cleaning fee" OR "hidden fee" OR '
                    f'"parking fee" OR deposit review', False, "US", 5)).get("results", [])
                log_call(self.memory, "serper", "search", True, time.monotonic() - started, "travel:reviews")
            except Exception as e:
                log_call(self.memory, "serper", "search", False, time.monotonic() - started, "travel:reviews", str(e))
                return
            for h in hits[:5]:
                snippet = str(h.get("snippet") or h.get("text") or "")
                m = re.search(r"[^.]*\b(resort fee|cleaning fee|hidden fee|parking fee|deposit)[^.]*\.?", snippet, re.I)
                if m and o["name"].split()[0].lower() in (snippet + str(h.get("title", ""))).lower():
                    o["flags"].append(_flag("warn", f"Reviews mention: \"{m.group(0).strip()[:140]}\""))
                    break
        await asyncio.gather(*(one(o) for o in offers))

    # --- after a search ---
    async def refresh(self, search_id: str, offer_id: str) -> dict:
        """Check an offer's price again before you act on it (Duffel offers; others: open the link)."""
        found = self.search(search_id)
        offer = next((o for o in found["results"]["offers"] if o["id"] == offer_id), None)
        if offer is None:
            raise ToolError("That offer isn't in this search any more: search again.")
        if offer["source"] != "duffel":
            return {"id": offer_id, "price": offer["price"], "note": "Open the link to see the live price and book."}
        got = await asyncio.to_thread(self.duffel.refresh, offer_id)
        changed = abs(got["price"] - offer["price"]) >= 0.01
        return {"id": offer_id, "price": got["price"], "currency": got["currency"], "was": offer["price"],
                "changed": changed, "expires_at": got["expires_at"], "bag_price": got["bag_price"]}

    def cheapest_dates(self, origin: str, destination: str, month: str) -> list[dict]:
        if not self.tp.configured():
            raise ToolError("Cheapest dates need Travelpayouts: add BUDDY_TRAVELPAYOUTS_TOKEN on the server.")
        if not re.fullmatch(r"\d{4}-\d{2}", str(month)):
            raise ToolError("Month as YYYY-MM.")
        return sorted(self.tp.cheapest(_iata(origin), _iata(destination), month, self.profile()["currency"]),
                      key=lambda d: d["price"])[:15]

    # --- route watching (Travelpayouts: free, so it can run every day) ---
    def watch(self, origin: str, destination: str, month: str, target: float | None = None,
              return_month: str = "") -> dict:
        if not self.tp.configured():
            raise ToolError("Route watching uses Travelpayouts: add BUDDY_TRAVELPAYOUTS_TOKEN on the server.")
        for m in filter(None, (month, return_month)):
            if not re.fullmatch(r"\d{4}-\d{2}", str(m)):
                raise ToolError("Months as YYYY-MM, like 2026-12.")
        o, d = _iata(origin), _iata(destination)
        self.memory.db.execute(
            "INSERT INTO travel_watches (origin, destination, month, return_month, target, currency, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(origin, destination, month, return_month) DO UPDATE SET "
            "target = excluded.target", (o, d, month, return_month or "", float(target) if target else None,
                                         self.profile()["currency"], datetime.now(timezone.utc).isoformat()))
        self.memory.db.commit()
        return next(w for w in self.watches() if (w["origin"], w["destination"], w["month"]) == (o, d, month))

    def watches(self) -> list[dict]:
        return [dict(r) for r in self.memory.db.execute("SELECT * FROM travel_watches ORDER BY month, id")]

    def unwatch(self, what) -> str:
        what = str(what).strip().upper()
        rows = [w for w in self.watches() if str(w["id"]) == what or f"{w['origin']}-{w['destination']}" == what
                or f"{w['origin']}{w['destination']}" == what.replace(" ", "")]
        if not rows:
            raise ToolError("No watched route like that.")
        for w in rows:
            self.memory.db.execute("DELETE FROM travel_watches WHERE id = ?", (w["id"],))
        self.memory.db.commit()
        return f"Stopped watching {rows[0]['origin']} → {rows[0]['destination']}."

    def check_watches(self) -> list[dict]:
        """Each watched route once: the cheapest fares found lately go into the patterns, and a fare at the
        target, well under the usual, or a new low gives an alert (once per drop). Months already past end."""
        alerts = []
        today = date.today().isoformat()[:7]
        for w in self.watches():
            if w["month"] < today:
                self.memory.db.execute("DELETE FROM travel_watches WHERE id = ?", (w["id"],))
                continue
            try:
                fares = self.tp.cheapest(w["origin"], w["destination"], w["month"], w["currency"],
                                         not w["return_month"], w["return_month"])
            except Exception as e:
                log.warning("watch %s-%s failed: %s", w["origin"], w["destination"], e)
                continue
            route = f"{w['origin']}-{w['destination']}"
            self._keep(route, f"{w['month']}-15", w["currency"], [], [], "price", fares)
            fares = [f for f in fares if f["price"] > 0]
            now = datetime.now(timezone.utc).isoformat()
            if not fares:
                self.memory.db.execute("UPDATE travel_watches SET checked_at = ? WHERE id = ?", (now, w["id"]))
                continue
            low = min(fares, key=lambda f: f["price"])
            pat = self.pattern(route, low["depart"], w["currency"], low["price"])
            reasons = []
            if w["target"] and low["price"] <= w["target"]:
                reasons.append(f"at or under your {w['target']:,.0f} target")
            if pat.get("seen", 0) >= 8 and pat.get("cheaper_than_percent", 0) >= 85:
                reasons.append(f"cheaper than {pat['cheaper_than_percent']}% of fares seen")
            if w["best"] and low["price"] < w["best"] * 0.95:
                reasons.append(f"a new low (was {w['best']:,.0f})")
            told_before = w["alerted"] is not None and low["price"] >= w["alerted"] * 0.97
            if reasons and not told_before:
                stops = "nonstop" if low["stops"] == 0 else f"{low['stops']} stop{'s' if low['stops'] > 1 else ''}"
                alerts.append({"watch": w["id"], "price": low["price"], "link": low["link"], "text": (
                    f"Cheap fare: {w['origin']} → {w['destination']} on {low['depart']} for about "
                    f"{low['price']:,.0f} {w['currency']} ({low['airline']}, {stops}), {', '.join(reasons)}. "
                    "It's a fare travellers found lately: say 'check it live' and I'll search it properly.")})
            best = min(low["price"], w["best"] or low["price"])
            self.memory.db.execute(
                "UPDATE travel_watches SET best = ?, best_day = CASE WHEN ? THEN ? ELSE best_day END, "
                "best_link = CASE WHEN ? THEN ? ELSE best_link END, checked_at = ? WHERE id = ?",
                (best, best == low["price"], low["depart"], best == low["price"], low["link"], now, w["id"]))
        self.memory.db.commit()
        return alerts

    def told(self, alert: dict) -> None:
        self.memory.db.execute("UPDATE travel_watches SET alerted = ? WHERE id = ?", (alert["price"], alert["watch"]))
        self.memory.db.commit()

    # --- learning from what the user picks ---
    def learned(self) -> dict:
        try:
            data = json.loads(self.memory.get(LEARNED_KEY) or "{}")
        except json.JSONDecodeError:
            data = {}
        hours = data.get("hour_values") or []
        refunds = data.get("refund_premiums") or []
        return {"choices": data.get("choices", 0), "hotel_choices": data.get("hotel_choices", 0),
                "hour_value": round(statistics.median(hours), 2) if len(hours) >= 2 else None,
                "pays_for_refundable": round(statistics.median(refunds), 2) if len(refunds) >= 2 else None,
                "nonstop_share": round(data.get("nonstop", 0) / data["choices"], 2) if data.get("choices") else None,
                "airlines": dict(sorted((data.get("airlines") or {}).items(), key=lambda kv: -kv[1])[:5]),
                "rating_premium": round(statistics.median(data["rating_premiums"]), 2)
                if len(data.get("rating_premiums") or []) >= 2 else None}

    def chose(self, search_id: str, offer_id: str, how: str = "opened") -> dict:
        """The user opened an offer to book it (or said they booked it): learn what they traded for what."""
        found = self.search(search_id)
        offers = found["results"]["offers"]
        pick = next((o for o in offers if o["id"] == offer_id), None)
        if pick is None:
            raise ToolError("That offer isn't in this search.")
        try:
            data = json.loads(self.memory.get(LEARNED_KEY) or "{}")
        except json.JSONDecodeError:
            data = {}
        cheap = min(offers, key=lambda o: o["true_total"])
        extra = pick["true_total"] - cheap["true_total"]
        lesson = []
        if found["kind"] == "flights":
            data["choices"] = data.get("choices", 0) + 1
            if pick["stops"] == 0:
                data["nonstop"] = data.get("nonstop", 0) + 1
            for a in (pick.get("airline") or "").split(", "):
                if a:
                    data.setdefault("airlines", {})[a] = data.get("airlines", {}).get(a, 0) + 1
            saved_h = (cheap["minutes"] - pick["minutes"]) / 60 if cheap["minutes"] and pick["minutes"] else 0
            if extra > 0 and saved_h >= 0.5:
                value = max(5.0, min(150.0, extra / saved_h))
                data.setdefault("hour_values", []).append(round(value, 2))
                lesson.append(f"paid about {value:.0f} {pick['currency']} per hour saved")
            if extra > 0 and pick["refund"].get("allowed") and not cheap["refund"].get("allowed"):
                data.setdefault("refund_premiums", []).append(round(extra, 2))
                lesson.append(f"paid {extra:.0f} more for a refundable fare")
            if extra <= 0:
                lesson.append("took the cheapest")
        else:
            data["hotel_choices"] = data.get("hotel_choices", 0) + 1
            if extra > 0 and (pick.get("rating") or 0) > (cheap.get("rating") or 0):
                data.setdefault("rating_premiums", []).append(round(extra / max(1, pick.get("nights") or 1), 2))
                lesson.append(f"paid {extra / max(1, pick.get('nights') or 1):.0f} a night more for a better-rated place")
        for k in ("hour_values", "refund_premiums", "rating_premiums"):
            data[k] = (data.get(k) or [])[-25:]  # recent choices count, old habits fade
        self.memory.set(LEARNED_KEY, json.dumps(data))
        try:
            from .brain import Brain

            Brain(self.memory).decided(found["kind"], pick.get("airline") or pick.get("name") or "",
                                       f"{len(offers)} options", "cheapest" if extra <= 0 else "paid more")
        except Exception:
            log.exception("couldn't log the travel choice")
        return {"learned": lesson, "now": self.learned(), "how": how}

    # --- storage ---
    def _save(self, kind: str, q: dict, title: str, result: dict) -> str:
        sid = secrets.token_hex(5)
        self.memory.db.execute("INSERT INTO travel_searches (id, kind, query, title, results, created_at) VALUES "
                               "(?, ?, ?, ?, ?, ?)", (sid, kind, json.dumps(q), title, json.dumps(result),
                                                      datetime.now(timezone.utc).isoformat()))
        self.memory.db.execute("DELETE FROM travel_searches WHERE created_at < ?",
                               ((datetime.now(timezone.utc) - timedelta(days=90)).isoformat(),))
        self.memory.db.commit()
        return sid

    def searches(self, limit: int = 30) -> list[dict]:
        return [{"id": r["id"], "kind": r["kind"], "title": r["title"], "created_at": r["created_at"],
                 "offers": len(json.loads(r["results"]).get("offers", []))}
                for r in self.memory.db.execute("SELECT * FROM travel_searches ORDER BY created_at DESC LIMIT ?", (limit,))]

    def search(self, search_id: str) -> dict:
        r = self.memory.db.execute("SELECT * FROM travel_searches WHERE id = ?", (search_id,)).fetchone()
        if r is None:
            raise ToolError("No such search.")
        return {"id": r["id"], "kind": r["kind"], "title": r["title"], "query": json.loads(r["query"]),
                "created_at": r["created_at"], "results": json.loads(r["results"])}

    def _keep(self, route: str, day: str, currency: str, offers: list[dict], google_history: list,
              field: str = "price", cached: list[dict] | None = None) -> None:
        """Every price seen (not just the best), plus Google's recent price graph: data for spotting patterns
        (how far ahead, which weekday, which season) and, later, predicting prices."""
        now = datetime.now(timezone.utc).isoformat()
        rows = [(route, day, f"{o['source']}:{(o.get('airline') or o.get('name') or '')[:40]}", round(o[field], 2),
                 currency, now) for o in offers[:25] if not o.get("test")]
        for d in cached or []:  # Travelpayouts: fares travellers found in the last two days, for their own dates
            if d.get("depart") and d.get("price"):
                rows.append((route, d["depart"], "travelpayouts", round(d["price"], 2), currency, now))
        for ts, price in google_history[-90:]:
            seen = datetime.fromtimestamp(ts / 1000 if ts > 10 ** 11 else ts, timezone.utc).isoformat()
            rows.append((route, day, "google_history", round(price, 2), currency, seen))
        if rows:
            self.memory.db.executemany("INSERT INTO travel_prices (route, day, source, price, currency, seen_at) "
                                       "VALUES (?, ?, ?, ?, ?, ?)", rows)
            _to_pricebook(self.memory, rows)
        self.memory.db.commit()
        self.roll_up()

    # --- patterns ---
    def roll_up(self) -> int:
        """Raw prices older than 90 days go into the patterns, then are deleted (the privacy policy says 90 days)."""
        db = self.memory.db
        cutoff = (datetime.now(timezone.utc) - timedelta(days=RAW_DAYS)).isoformat()
        rows = db.execute("SELECT route, day, source, price, currency, seen_at FROM travel_prices WHERE seen_at < ? "
                          "AND source != 'best'", (cutoff,)).fetchall()
        groups: dict[tuple, Summary] = {}
        for r in rows:
            key = self._pattern_key(r["route"], r["day"], r["seen_at"], r["currency"])
            if key:
                groups.setdefault(key, Summary()).add(r["price"])
        now = datetime.now(timezone.utc)
        for key, new in groups.items():
            old = db.execute("SELECT * FROM travel_patterns WHERE route = ? AND month = ? AND lead = ? AND weekday = ? "
                             "AND currency = ?", key).fetchone()
            summary = new
            if old:
                summary = Summary(old["n"], old["mean"], old["m2"], old["low"], old["high"], json.loads(old["hist"]))
                age = (now - datetime.fromisoformat(old["updated_at"])).days
                summary.fade(0.5 ** (age / HALF_LIFE_DAYS))
                summary.merge(new)
            db.execute("INSERT OR REPLACE INTO travel_patterns (route, month, lead, weekday, currency, n, mean, m2, low, "
                       "high, hist, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                       (*key, round(summary.n, 4), round(summary.mean, 2), round(summary.m2, 2), round(summary.low, 2),
                        round(summary.high, 2), json.dumps({k: round(v, 4) for k, v in summary.hist.items()}),
                        now.isoformat()))
        db.execute("DELETE FROM travel_prices WHERE seen_at < ?", (cutoff,))
        db.commit()
        return len(rows)

    @staticmethod
    def _pattern_key(route: str, day: str, seen_at: str, currency: str) -> tuple | None:
        try:
            travel = date.fromisoformat(day[:10])
            seen = datetime.fromisoformat(seen_at).date()
        except ValueError:
            return None
        return route, travel.month, _lead((travel - seen).days), travel.weekday(), currency

    def pattern(self, route: str, travel_day: str, currency: str, price: float | None = None) -> dict:
        """What prices on this route are like: the patterns plus the last 90 days of raw prices."""
        db = self.memory.db
        cells: list[tuple[tuple, Summary]] = []
        for p in db.execute("SELECT * FROM travel_patterns WHERE route = ? AND currency = ?", (route, currency)):
            cells.append(((p["month"], p["lead"], p["weekday"]),
                          Summary(p["n"], p["mean"], p["m2"], p["low"], p["high"], json.loads(p["hist"]))))
        for r in db.execute("SELECT day, price, seen_at FROM travel_prices WHERE route = ? AND currency = ? AND "
                            "source != 'best'", (route, currency)):
            key = self._pattern_key(route, r["day"], r["seen_at"], currency)
            if key:
                one = Summary()
                one.add(r["price"])
                cells.append(((key[1], key[2], key[3]), one))
        if not cells:
            return {"seen": 0}
        try:
            month = date.fromisoformat(travel_day[:10]).month
        except ValueError:
            month = 0

        def combine(pick) -> Summary:
            total = Summary()
            for k, sm in cells:
                if pick(k):
                    total.merge(sm)
            return total
        this_month = combine(lambda k: k[0] == month)
        base = this_month if this_month.n >= 8 else combine(lambda k: True)
        out = {"seen": round(base.n), "for": MONTHS[month - 1] if base is this_month and month else "all months",
               "typical": [base.quantile(0.25), base.quantile(0.75)], "low": round(base.low, 2),
               "average": round(base.mean, 2)}
        if price:
            share = base.share_below(price)
            if share is not None:
                out["cheaper_than_percent"] = round(100 * share)
        by_lead = {name: combine(lambda k, name=name: k[1] == name and (k[0] == month or base is not this_month))
                   for _, _, name in LEADS}
        leads = {k: round(v.mean, 2) for k, v in by_lead.items() if v.n >= 3}
        if len(leads) >= 2:
            out["by_lead"] = leads
            out["best_lead"] = min(leads, key=leads.get)
        days = {WEEKDAYS[i]: combine(lambda k, i=i: k[2] == i) for i in range(7)}
        days = {k: round(v.mean, 2) for k, v in days.items() if v.n >= 3}
        if len(days) >= 2:
            out["by_weekday"] = days
        months = {MONTHS[m - 1]: combine(lambda k, m=m: k[0] == m) for m in range(1, 13)}
        months = {k: round(v.mean, 2) for k, v in months.items() if v.n >= 3}
        if len(months) >= 2:
            out["by_month"] = months
        return out

    def _history(self, route: str, day: str, currency: str, prices: list[float]) -> dict:
        db = self.memory.db
        now = datetime.now(timezone.utc).isoformat()
        past = [r["price"] for r in db.execute(
            "SELECT price FROM travel_prices WHERE route = ? AND currency = ? AND seen_at >= ? "
            "AND source IN ('best', 'google_history')",
            (route, currency, (datetime.now(timezone.utc) - timedelta(days=365)).isoformat()))]
        best = min(prices) if prices else None
        if best:
            db.execute("INSERT INTO travel_prices (route, day, source, price, currency, seen_at) VALUES (?,?,?,?,?,?)",
                       (route, day, "best", round(best, 2), currency, now))
            _to_pricebook(self.memory, [(route, day, "best", round(best, 2), currency, now)])
            db.commit()
        if not best or len(past) < 3:
            return {"seen_before": len(past)}
        usual = statistics.median(past)
        return {"seen_before": len(past), "usual": round(usual, 2),
                "vs_usual_percent": round(100 * (best / usual - 1)), "lowest_seen": round(min(past), 2)}


def _money(v: float, cur: str) -> str:
    return f"{v:,.0f} {cur}"


def pattern_advice(p: dict, price: float, cur: str, travel_day: str, what: str = "") -> list[str]:
    """Plain-language lessons from the price patterns, once there's enough data."""
    if p.get("seen", 0) < 8:
        return []
    out = []
    if "cheaper_than_percent" in p:
        pct = p["cheaper_than_percent"]
        verdict = "a good price" if pct >= 70 else "an average price" if pct >= 35 else "on the high side"
        out.append(f"{_money(price, cur)}{what} is {verdict}: cheaper than {pct}% of prices Buddy has seen for "
                   f"{p['for']} (typical {_money(p['typical'][0] or 0, cur)}–{_money(p['typical'][1] or 0, cur)}).")
    if p.get("best_lead"):
        try:
            lead_now = (date.fromisoformat(travel_day[:10]) - date.today()).days
        except ValueError:
            lead_now = -1
        best = p["best_lead"]
        if lead_now >= 0 and _lead(lead_now) != best:
            out.append(f"On this route prices have been lowest when booked {best} days ahead; you're {lead_now} days out.")
    days = p.get("by_weekday") or {}
    if len(days) >= 3:
        lo, hi = min(days, key=days.get), max(days, key=days.get)
        if days[hi] and days[lo] < days[hi] * 0.92:
            out.append(f"{lo} have been about {round(100 * (1 - days[lo] / days[hi]))}% cheaper than {hi}.")
    months = p.get("by_month") or {}
    if len(months) >= 3:
        lo = min(months, key=months.get)
        try:
            mine = MONTHS[date.fromisoformat(travel_day[:10]).month - 1]
        except ValueError:
            mine = ""
        if mine in months and lo != mine and months[lo] < months[mine] * 0.9:
            out.append(f"{lo} has been about {round(100 * (1 - months[lo] / months[mine]))}% cheaper than {mine}.")
    return out


def flight_advice(offers: list[dict], q: dict, insight: dict, history: dict, dates: list[dict],
                  profile: dict) -> list[str]:
    """The trade-offs a good travel agent would point out, worked out from the numbers."""
    if not offers:
        return []
    real = [o for o in offers if not o.get("test")] or offers
    cur = real[0]["currency"]
    out = []
    best = next(o for o in real if "best value" in o["tags"]) if any("best value" in o["tags"] for o in real) else real[0]
    cheap = min(real, key=lambda o: o["true_total"])
    headline = min(real, key=lambda o: o["price"])
    if headline is not cheap and headline["true_total"] > cheap["true_total"]:
        out.append(f"Headline trap: the lowest price shown ({_money(headline['price'], cur)}, {headline['airline']}) is "
                   f"really {_money(headline['true_total'], cur)} once your bags are added; {cheap['airline']} at "
                   f"{_money(cheap['true_total'], cur)} is cheaper in the end.")
    if best is not cheap:
        diff = best["true_total"] - cheap["true_total"]
        gains = []
        if best["minutes"] and cheap["minutes"] and cheap["minutes"] - best["minutes"] >= 60:
            gains.append(f"{_hm(cheap['minutes'] - best['minutes'])} shorter")
        if best["stops"] < cheap["stops"]:
            gains.append("fewer stops")
        warn_gap = sum(f["level"] == "warn" for f in cheap["flags"]) - sum(f["level"] == "warn" for f in best["flags"])
        if warn_gap > 0:
            gains.append(f"{warn_gap} fewer catches")
        out.append(f"Best value is {best['airline']} for {_money(best['true_total'], cur)}: {_money(diff, cur)} more than "
                   f"the cheapest, but {', '.join(gains) or 'a better trip overall'}.")
    refundable = [o for o in real if o["refund"].get("known") and o["refund"].get("allowed")]
    if refundable and not (cheap["refund"].get("known") and cheap["refund"].get("allowed")):
        r = min(refundable, key=lambda o: o["true_total"])
        out.append(f"Flexibility costs {_money(r['true_total'] - cheap['true_total'], cur)}: the cheapest refundable fare "
                   f"is {r['airline']} at {_money(r['true_total'], cur)}. Worth it if your plans might change.")
    nonstop = [o for o in real if o["stops"] == 0]
    if nonstop and cheap["stops"] > 0:
        n = min(nonstop, key=lambda o: o["true_total"])
        saved = cheap["minutes"] - n["minutes"]
        out.append(f"Nonstop: {n['airline']} for {_money(n['true_total'] - cheap['true_total'], cur)} more"
                   + (f", saving {_hm(saved)}." if saved > 0 else "."))
    origins = {leg["from"] for leg in cheap["legs"][:1]}
    dests = {leg["to"] for leg in cheap["legs"][:1]}
    if origins - {q["origin"]} or dests - {q["destination"]}:
        out.append(f"The cheapest uses a nearby airport ({'/'.join(sorted(origins))} → {'/'.join(sorted(dests))}): "
                   "count the ride there.")
    window = int(profile.get("flexible_days") or 0)
    if window and dates:
        target = date.fromisoformat(q["depart"])
        near = [d for d in dates if d["depart"] and d["depart"] != q["depart"]
                and abs((date.fromisoformat(d["depart"]) - target).days) <= window]
        if near:
            d = min(near, key=lambda d: d["price"])
            if d["price"] < cheap["price"] * 0.9:
                out.append(f"Flexible? Flying on {d['depart']} was seen for about {_money(d['price'], d['currency'])} "
                           f"({_money(cheap['price'] - d['price'], cur)} less, before bags); check it live.")
    for o in real[:5]:
        for a in o.get("also") or []:
            if a["price"] < o["price"] - 5:
                out.append(f"The same {o['airline']} flights are {_money(o['price'] - a['price'], cur)} cheaper on "
                           f"{a['source']}.")
    if insight.get("level"):
        rng = insight.get("typical") or []
        out.append(f"Google rates today's prices {insight['level']}" + (f" (typical {rng[0]}–{rng[1]} {cur})."
                                                                       if len(rng) == 2 else "."))
    if history.get("usual"):
        pct = history["vs_usual_percent"]
        out.append(f"Compared with what Buddy has seen for this route: {abs(pct)}% {'below' if pct < 0 else 'above'} "
                   f"the usual {_money(history['usual'], cur)}.")
    if any(o.get("estimated") for o in real[:5]):
        out.append("Bag and seat fees marked as estimates: the airline's site shows the exact amount at checkout.")
    return out[:8]


def hotel_advice(offers: list[dict], history: dict) -> list[str]:
    if not offers:
        return []
    cur = offers[0]["currency"]
    out = []
    cheap = min(offers, key=lambda o: o["true_total"])
    best = offers[0]
    top = max(offers, key=lambda o: float(o.get("rating") or 0))
    if best is not cheap:
        out.append(f"Best value is {best['name']} at {_money(best['true_total'], cur)} for the stay: "
                   f"{_money(best['true_total'] - cheap['true_total'], cur)} more than {cheap['name']}, with "
                   f"{best.get('rating') or '?'} vs {cheap.get('rating') or '?'} rating and fewer catches.")
    if top is not best and top.get("rating"):
        out.append(f"Best rated: {top['name']} ({top['rating']}) at {_money(top['true_total'], cur)}.")
    free = [o for o in offers if any("Free cancellation" in f["text"] for f in o["flags"])]
    if free and cheap not in free:
        f = min(free, key=lambda o: o["true_total"])
        out.append(f"Free cancellation costs {_money(f['true_total'] - cheap['true_total'], cur)} more ({f['name']}).")
    for o in offers[:5]:
        sellers = [x for x in o.get("sellers") or [] if x.get("per_night")]
        if len(sellers) > 1:
            lo, hi = min(s["per_night"] for s in sellers), max(s["per_night"] for s in sellers)
            if hi - lo >= 5:
                out.append(f"{o['name']}: booking sites differ by {_money((hi - lo) * o['nights'], cur)} for the stay; "
                           "book with the cheapest one that shows the same room and cancellation terms.")
    if history.get("usual"):
        pct = history["vs_usual_percent"]
        out.append(f"{abs(pct)}% {'below' if pct < 0 else 'above'} what Buddy has seen here before.")
    return out[:6]


def _time(value) -> str:
    s = str(value or "")
    m = re.search(r"(\d{2}:\d{2})", s)
    return m.group(1) if m else s[-5:]


def _dedupe(flags: list[dict]) -> list[dict]:
    seen, out = set(), []
    for f in flags:
        if f["text"] not in seen:
            seen.add(f["text"])
            out.append(f)
    order = {"warn": 0, "info": 1, "good": 2}
    return sorted(out, key=lambda f: order.get(f["level"], 1))


def _merge_same_flights(offers: list[dict]) -> list[dict]:
    """The same flights from two sources: one entry, with the other source's price alongside. Duffel's entry
    leads when it has the flight, because it knows the exact bags and rules (Google's notes are partial)."""
    groups: dict[str, list[dict]] = {}
    out = []
    for o in offers:
        key = "|".join(f for leg in o["legs"] for f in leg.get("flights", []) if f)
        if not key:
            out.append(o)
            continue
        groups.setdefault(key, []).append(o)
    for same in groups.values():
        lead = next((o for o in same if o["source"] == "duffel"), min(same, key=lambda o: o["true_total"]))
        others = [o for o in same if o is not lead]
        if others:
            lead["also"] = [{"source": o["source"], "price": o["price"], "true_total": o["true_total"],
                             "link": o["link"]} for o in others]
            extra = [f for o in others for f in o["flags"] if f["level"] == "warn" and "estimate" not in f["text"]]
            lead["flags"] = _dedupe(lead["flags"] + extra)
            if lead["source"] == "duffel":
                lead["link"] = next((o["link"] for o in others if o["source"] == "google"), lead["link"])
        out.append(lead)
    return out


def _rank(offers: list[dict], hotel: bool = False, hour_value: float | None = None) -> list[dict]:
    if not offers:
        return []
    if hotel:
        def value(o):
            rating = float(o.get("rating") or 3.5)
            return o["true_total"] * (1 + max(0.0, 4.5 - rating) * 0.12) + 15 * sum(f["level"] == "warn" for f in o["flags"])
    else:
        fastest = min((o["minutes"] for o in offers if o["minutes"]), default=0)

        per_hour = hour_value or HOUR_VALUE  # learned from the user's own choices once there are a few

        def value(o):
            return o["true_total"] + per_hour * max(0, o["minutes"] - fastest) / 60 + STOP_VALUE * o["stops"]
    ranked = sorted(offers, key=value)
    cheapest = min(offers, key=lambda o: o["true_total"])
    for i, o in enumerate(ranked):
        o["rank"] = i + 1
        o["tags"] = []
    ranked[0]["tags"].append("best value")
    cheapest["tags"].append("cheapest")
    if not hotel:
        quick = min(offers, key=lambda o: (o["minutes"] or 10 ** 6, o["true_total"]))
        quick["tags"].append("fastest")
    else:
        top = max(offers, key=lambda o: (float(o.get("rating") or 0), -o["true_total"]))
        top["tags"].append("best rated")
    return ranked


def _test_note(offers: list[dict]) -> str:
    return ("Duffel is in test mode: its offers are a pretend airline with made-up prices, so compare on Google's."
            if any(o.get("test") for o in offers) else "")


def api_overview(memory: Memory, since: str) -> dict:
    """For Insights: calls to outside APIs, failures, time and cost, by provider, plus SerpApi's quota."""
    memory.db.executescript(SCHEMA_SQL)
    rows = [dict(r) for r in memory.db.execute("SELECT * FROM api_calls WHERE at >= ?", (since,))]
    groups: dict[str, list[dict]] = {}
    for r in rows:
        groups.setdefault(r["provider"], []).append(r)
    providers = []
    for name, rs in groups.items():
        endpoints: dict[str, int] = {}
        for r in rs:
            endpoints[r["endpoint"]] = endpoints.get(r["endpoint"], 0) + 1
        times = sorted(r["seconds"] for r in rs if r["seconds"] is not None)
        errors = [r["error"] for r in rs if not r["ok"] and r["error"]]
        providers.append({"provider": name, "calls": len(rs), "failed": sum(1 for r in rs if not r["ok"]),
                          "cost_usd": round(sum(r["cost_usd"] for r in rs), 4), "endpoints": endpoints,
                          "median_seconds": times[len(times) // 2] if times else None,
                          "last_error": errors[-1] if errors else ""})
    month = local_month_start()
    month_cost = memory.db.execute("SELECT COALESCE(SUM(cost_usd), 0) FROM api_calls WHERE at >= ?", (month,)).fetchone()[0]
    return {"providers": sorted(providers, key=lambda p: -p["calls"]), "cost_usd": round(sum(p["cost_usd"] for p in providers), 4),
            "month_cost_usd": round(month_cost, 4)}


def local_month_start() -> str:
    from .tools import local_now

    first = local_now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return first.astimezone(timezone.utc).isoformat()


def offer_card(o: dict) -> dict:
    warn = [f["text"] for f in o["flags"] if f["level"] == "warn"]
    price = f"{o['currency']} {o['true_total']:,.0f}" + ("*" if o.get("estimated") else "")
    return {"kind": "deal", "title": o["title"], "subtitle": " · ".join(x for x in (
        ", ".join(o.get("tags") or []), o.get("summary", ""), {"duffel": "Duffel", "google": "Google"}.get(o["source"], o["source"]))
        if x), "price": price, "text": ("⚠ " + warn[0]) if warn else (o["flags"][0]["text"] if o["flags"] else ""),
        "url": o.get("link", "")}


TRAVEL_TOOL = {
    "name": "travel",
    "description": "Flights and hotels from Duffel (live airline offers with bag and change rules), Google Flights / "
    "Google Hotels (market prices) and Travelpayouts (cheapest dates), compared on the TRUE TOTAL for how the user "
    "travels (bags, seat), with caveats flagged: basic economy, bags or carry-on extra, no refund, change fees, tight "
    "or overnight layovers, airport changes, self-transfer, taxes and fees, resort or cleaning fees, no free "
    "cancellation, what reviews say about surprise fees. flights (origin, destination as airport codes, depart, "
    "optional return, adults), hotels (place, check_in, check_out, adults), cheapest_dates (origin, destination, "
    "month YYYY-MM), refresh (search_id, offer_id: re-check a price), profile (show) / set_profile (checked_bags, "
    "carry_on, seat, cabin, max_stops, free_cancellation, currency, nearby_airports, flexible_days), chose (search_id, "
    "offer_id: the user booked or picked this one; Buddy learns what they trade for what), price_pattern (origin + "
    "destination, or place for hotels, and a date: what prices are usually like, when to book, cheaper weekdays and "
    "months, from Buddy's own records), watch (origin, destination, month YYYY-MM, optional return_month and target "
    "price: checked every day, Buddy speaks up when the fare hits the target, drops well below the usual or hits a "
    "new low), watches (list), unwatch (what = route like MIA-LIM or its id). save_search (search_id, optional target): "
    "'save this search' / 'track this': the exact search (dates, route or hotel) is re-run live every morning and "
    "Buddy alerts on a deal (target hit, 5%+ drop since saved, a new low); use this, not watch_price, for flights and "
    "hotels already searched. saved_searches (list), unsave_search (saved_id). Results show in the app's Travel screen with links "
    "to book; you never book. Think like an experienced travel agent who works for the user: use 'advice' (the trade-offs "
    "worked out: headline traps, what flexibility or a nonstop costs, nearby airports, cheaper nearby dates, same "
    "flight cheaper elsewhere, how today's price compares) to recommend ONE option clearly with the reason, then the "
    "cheaper alternative and what they'd give up. If the answer depends on something you don't know (could plans "
    "change? is the bag needed?), ask. Ask the user's travel style once (bags, seat) if the profile is still the default.",
    "input_schema": {"type": "object", "properties": {
        "action": {"type": "string", "enum": ["flights", "hotels", "cheapest_dates", "refresh", "profile", "set_profile",
                                              "chose", "price_pattern", "watch", "watches", "unwatch", "save_search",
                                              "saved_searches", "unsave_search"]},
        "saved_id": {"type": "integer"},
        "origin": {"type": "string"}, "destination": {"type": "string"}, "depart": {"type": "string"},
        "target": {"type": "number"}, "return_month": {"type": "string"}, "what": {"type": "string"},
        "return": {"type": "string"}, "adults": {"type": "integer"}, "place": {"type": "string"},
        "check_in": {"type": "string"}, "check_out": {"type": "string"}, "month": {"type": "string"},
        "search_id": {"type": "string"}, "offer_id": {"type": "string"},
        "checked_bags": {"type": "integer"}, "carry_on": {"type": "boolean"}, "seat": {"type": "string"},
        "cabin": {"type": "string", "enum": ["economy", "premium_economy", "business", "first"]},
        "max_stops": {"type": "integer"}, "free_cancellation": {"type": "boolean"}, "currency": {"type": "string"}},
        "required": ["action"], "additionalProperties": False},
}


def _for_claude(result: dict, top: int = 6) -> dict:
    keep = ("id", "source", "title", "summary", "price", "true_total", "currency", "estimated", "parts", "tags", "flags",
            "also", "bookable", "test")
    return {"search_id": result["search_id"], "advice": result.get("advice", []),
            "offers": [{k: o[k] for k in keep if k in o} for o in result["offers"][:top]],
            "google_price_insight": result.get("google_price_insight"), "history": result.get("history"),
            "cheapest_dates": result.get("cheapest_dates", [])[:5], "problems": result.get("problems"),
            "note": result.get("note", ""), "profile": result.get("profile"),
            "app": "All results, links to book and side-by-side compare are in the app's Travel screen."}


async def run_tool(travel: Travel, args: dict) -> str:
    from . import cards

    a = args.get("action")
    if a == "profile":
        return json.dumps({"profile": travel.profile(), "learned": travel.learned(), "sources": travel.sources()})
    if a == "watch":
        w = travel.watch(args.get("origin", ""), args.get("destination", ""), str(args.get("month", "")),
                         args.get("target"), str(args.get("return_month") or ""))
        return json.dumps({"watching": w, "say": "Checked every morning; I'll speak up when it's a good fare."})
    if a == "watches":
        return json.dumps(travel.watches()) if travel.watches() else "No routes watched yet."
    if a in ("save_search", "saved_searches", "unsave_search"):
        from .travel_saved import SavedSearches  # noqa: PLC0415
        saved = SavedSearches(travel.memory, travel)
        if a == "save_search":
            return saved.save(str(args.get("search_id") or ""), args.get("target"))["text"]
        if a == "unsave_search":
            return saved.remove(int(args.get("saved_id") or 0))
        return json.dumps(saved.saved(), default=str) if saved.saved() else "No saved searches yet."
    if a == "unwatch":
        return travel.unwatch(args.get("what") or "")
    if a == "price_pattern":
        route = f"hotels:{str(args.get('place', '')).strip().lower()}" if args.get("place") else \
            f"{_iata(args.get('origin', ''))}-{_iata(args.get('destination', ''))}"
        day = str(args.get("depart") or args.get("check_in") or date.today().isoformat())
        return json.dumps(travel.pattern(route, day, travel.profile()["currency"]))
    if a == "chose":
        return json.dumps(travel.chose(str(args.get("search_id", "")), str(args.get("offer_id", "")), "told"))
    if a == "set_profile":
        return json.dumps(travel.set_profile(**{k: args.get(k) for k in DEFAULT_PROFILE}))
    if a == "cheapest_dates":
        return json.dumps(await asyncio.to_thread(travel.cheapest_dates, args.get("origin", ""),
                                                  args.get("destination", ""), args.get("month", "")))
    if a == "refresh":
        return json.dumps(await travel.refresh(str(args.get("search_id", "")), str(args.get("offer_id", ""))))
    if a == "flights":
        result = await travel.flights(args.get("origin", ""), args.get("destination", ""), args.get("depart", ""),
                                      args.get("return", "") or "", args.get("adults") or 1)
    elif a == "hotels":
        result = await travel.hotels(args.get("place", ""), args.get("check_in", ""), args.get("check_out", ""),
                                     args.get("adults") or 2)
    else:
        raise ToolError("Unknown travel action.")
    box = cards.current.get()
    if box is not None:
        box.pick([{"id": c} for c in (cards.offer(offer_card(o)) for o in result["offers"][:4]) if c])
    if not result["offers"]:
        return json.dumps({"offers": [], "problems": result["problems"],
                           "say": "Nothing found: check the airport codes and dates, or try other dates."})
    return json.dumps(_for_claude(result), ensure_ascii=False)


def _to_pricebook(memory, rows) -> None:
    """Fares and hotel rates also go into the price book (pricebook.py), Buddy's market-price database."""
    try:
        from . import pricebook  # noqa: PLC0415
        for route, day, source, price, currency, seen in rows:
            hotel = str(route).startswith("hotels:")
            seen_at = datetime.fromisoformat(seen) if isinstance(seen, str) else seen
            pricebook.observe(memory, "hotel" if hotel else "flight", route, price, currency=currency,
                              source=source, travel_date=day, seen_at=seen_at)
    except Exception:
        log.warning("price book: travel prices not copied", exc_info=True)
