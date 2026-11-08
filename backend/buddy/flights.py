"""Flight status for the user's own flights: AirLabs, the airline's own app and emails, then FlightAware.

Sources, in order: AirLabs (schedule, live position, and more on paid plans); the airline's app notifications and
emails about the flight, read on Buddy's server with text rules (gate, terminal, new departure time, delay,
boarding, cancellation: the airline knows first); FlightAware AeroAPI as the final backup for what's still missing,
rationed (only near departure, every 30 minutes at most, 12 calls a flight, BUDDY_AEROAPI_MONTHLY a month, key
BUDDY_AEROAPI_KEY) because it's paid per query. FlightAware's reply format is from its public documentation and is
checked with a real key on first use.

"Track AA 1234 tomorrow" (or Buddy finds the flight in a booking email and asks). From about 4 hours before the
scheduled departure until it lands, Buddy checks every 15 minutes and messages only when something changes: a
delay (or a bigger one), a gate or terminal change, a cancellation, a diversion, departure, landing (with the bag
carousel when known). Any time: "is my flight on time?".

AirLabs: GET https://airlabs.co/api/v9/schedules?flight_iata=AA1234&api_key=… (status scheduled / active / landed /
cancelled, times, estimated and actual times, gates, terminals, baggage, delays) and /flight?flight_iata=… (the
closest flight, with its live position). Which fields come back depends on the AirLabs plan: per its docs the free
plan has the route, scheduled times and live position, while gates, terminals, delays, estimated times and status
may need a paid plan. Buddy uses whatever the key returns and says what's missing. Key: BUDDY_AIRLABS_KEY.
Only flight numbers go to AirLabs, nothing about the user. Read-only: Buddy never changes a booking.
"""

import json
import logging
import os
import re
import time
import urllib.parse
from datetime import date, datetime, timedelta, timezone

from .memory import Memory
from .tools import ToolError, local_now, require_https

log = logging.getLogger(__name__)

BASE = "https://airlabs.co/api/v9/"
UA = "Buddy/1.0 (personal assistant; read-only)"
BEFORE_HOURS = 4          # start checking this long before departure
AFTER_HOURS = 3           # stop this long after the scheduled arrival if no landing is seen
CHECK_MINUTES = 15
DELAY_STEP = 15           # tell a delay of 15+ minutes, and again when it grows by 15+
AERO = "https://aeroapi.flightaware.com/aeroapi/flights/"
AERO_PER_FLIGHT = 12
AIRLINE_NAMES = {"AA": ["american"], "DL": ["delta"], "UA": ["united"], "WN": ["southwest"], "B6": ["jetblue"],
                 "AS": ["alaska"], "NK": ["spirit"], "F9": ["frontier"], "HA": ["hawaiian"], "G4": ["allegiant"],
                 "SY": ["sun country"], "MX": ["breeze"], "LA": ["latam"], "AV": ["avianca"], "CM": ["copa"],
                 "AC": ["air canada"], "BA": ["british airways"], "LH": ["lufthansa"], "EK": ["emirates"],
                 "QR": ["qatar"], "AI": ["air india"], "EI": ["aer lingus"], "KL": ["klm"], "AF": ["air france"],
                 "TK": ["turkish"], "IB": ["iberia"], "VS": ["virgin atlantic"], "AM": ["aeromexico"],
                 "6E": ["indigo"], "EY": ["etihad"], "SQ": ["singapore airlines"], "CX": ["cathay"]}
AIRLINE_APP = re.compile(r"airline|airways|air lines|\bairlines?\b|flight|aa\.android|delta\.mobile|united\.mobile|"
                         r"southwest|jetblue|alaskaair|latam|avianca|flighty|tripit|flightaware|" +
                         "|".join(n for names in AIRLINE_NAMES.values() for n in names), re.I)
FLIGHT = re.compile(r"^\s*([A-Z0-9]{2})\s*-?\s*(\d{1,4})[A-Z]?\s*$", re.I)

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS flight_watch (
    id INTEGER PRIMARY KEY,
    flight TEXT NOT NULL,             -- AA1234
    day TEXT NOT NULL,                -- departure date (airport local), YYYY-MM-DD
    note TEXT NOT NULL DEFAULT '',    -- "to Lima", "Amma's flight"
    last TEXT NOT NULL DEFAULT '{}',  -- the latest status (JSON)
    told TEXT NOT NULL DEFAULT '{}',  -- what was said already: {"delay": 30, "dep_gate": "D12", ...}
    checked_at TEXT,
    done INTEGER NOT NULL DEFAULT 0,  -- landed, cancelled, or past
    added_at TEXT NOT NULL,
    UNIQUE (flight, day)
);
"""


def _fetch(url: str) -> bytes:
    import urllib.request
    req = urllib.request.Request(require_https(url), headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as r:  # https only  # nosec B310
        return r.read(5_000_000)


def _fetch_with(url: str, headers: dict) -> bytes:
    import urllib.request
    req = urllib.request.Request(require_https(url), headers={"User-Agent": UA, **headers})
    with urllib.request.urlopen(req, timeout=30) as r:  # https only  # nosec B310
        return r.read(5_000_000)


def aero_status(got: dict, flight: str, day: str = "") -> dict:
    """FlightAware AeroAPI /flights/{ident} -> the same fields AirLabs uses (times in UTC and airport local)."""
    from zoneinfo import ZoneInfo  # noqa: PLC0415
    flights = (got or {}).get("flights") or []

    def local(iso: str | None, tz: str | None) -> str:
        if not iso:
            return ""
        t = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        try:
            t = t.astimezone(ZoneInfo(tz)) if tz else t
        except Exception:
            pass
        return t.strftime("%Y-%m-%d %H:%M")

    def utc(iso: str | None) -> str:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M") \
            if iso else ""
    pick = None
    if day:
        for fl in flights:
            tz = (fl.get("origin") or {}).get("timezone")
            if local(fl.get("scheduled_out") or fl.get("scheduled_off"), tz).startswith(day):
                pick = fl
                break
    elif flights:  # no date: the one closest to now (FlightAware lists newest first)
        now = datetime.now(timezone.utc)
        pick = min(flights, key=lambda fl: abs((datetime.fromisoformat(
            (fl.get("scheduled_out") or fl.get("scheduled_off") or now.isoformat()).replace("Z", "+00:00")) - now)
            .total_seconds()))
    if pick is None:
        raise ToolError(f"FlightAware has nothing for {flight}" + (f" on {day}" if day else "") + ".")
    o, d = pick.get("origin") or {}, pick.get("destination") or {}
    otz, dtz = o.get("timezone"), d.get("timezone")
    st = (pick.get("status") or "").lower()
    status = "cancelled" if pick.get("cancelled") else "landed" if (pick.get("actual_on") or pick.get("actual_in") or
                                                                    "arrived" in st or "landed" in st) \
        else "active" if (pick.get("actual_off") or pick.get("actual_out")) else "scheduled"
    out = {"status": status, "dep_iata": o.get("code_iata"), "arr_iata": d.get("code_iata"),
           "dep_gate": pick.get("gate_origin"), "dep_terminal": pick.get("terminal_origin"),
           "arr_gate": pick.get("gate_destination"), "arr_terminal": pick.get("terminal_destination"),
           "arr_baggage": pick.get("baggage_claim"),
           "dep_time": local(pick.get("scheduled_out"), otz), "dep_time_utc": utc(pick.get("scheduled_out")),
           "dep_estimated": local(pick.get("estimated_out"), otz), "dep_estimated_utc": utc(pick.get("estimated_out")),
           "dep_actual": local(pick.get("actual_out"), otz),
           "arr_time": local(pick.get("scheduled_in"), dtz), "arr_time_utc": utc(pick.get("scheduled_in")),
           "arr_estimated": local(pick.get("estimated_in"), dtz), "arr_estimated_utc": utc(pick.get("estimated_in")),
           "arr_actual": local(pick.get("actual_in") or pick.get("actual_on"), dtz),
           "dep_delayed": round((pick.get("departure_delay") or 0) / 60) if pick.get("departure_delay") is not None
           else None}
    if pick.get("diverted"):
        out["diverted"] = True
    return {k: v for k, v in out.items() if v not in (None, "")}


def normal_flight(text: str) -> str:
    m = FLIGHT.match(str(text or "").replace(" ", " "))
    if not m:
        raise ToolError("Which flight? Give the airline code and number, like AA 1234 or B6 23.")
    return f"{m[1].upper()}{int(m[2])}"


def _utc(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.strptime(s[:16], "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _hm(s: str | None) -> str:
    """'2026-11-07 14:05' (airport local) -> '2:05 PM'."""
    if not s:
        return ""
    try:
        return datetime.strptime(s[:16], "%Y-%m-%d %H:%M").strftime("%-I:%M %p")
    except ValueError:
        return s


KEEP = ("flight_iata", "airline_iata", "status", "dep_iata", "dep_terminal", "dep_gate", "dep_time", "dep_time_utc",
        "dep_estimated", "dep_estimated_utc", "dep_actual", "dep_actual_utc", "arr_iata", "arr_terminal", "arr_gate",
        "arr_baggage", "arr_time", "arr_time_utc", "arr_estimated", "arr_estimated_utc", "arr_actual",
        "arr_actual_utc", "dep_delayed", "arr_delayed", "duration", "lat", "lng", "alt", "speed")


class Flights:
    def __init__(self, memory: Memory, env=None, fetch=None, aero_fetch=None):
        self.memory = memory
        self.env = os.environ if env is None else env
        self.fetch = fetch or _fetch
        self.aero_fetch = aero_fetch or _fetch_with
        from .markets import API_CALLS_SQL  # noqa: PLC0415  (Insights counts every API call)
        memory.db.executescript(SCHEMA_SQL + API_CALLS_SQL)

    @property
    def key(self) -> str:
        return (self.env.get("BUDDY_AIRLABS_KEY") or "").strip()

    def _get(self, endpoint: str, **params) -> object:
        if not self.key:
            raise ToolError("Flight status needs a free AirLabs key: sign up at airlabs.co, then run "
                            "sudo bash /opt/buddy/deploy/set.sh BUDDY_AIRLABS_KEY.")
        from .travel import log_call  # noqa: PLC0415
        url = BASE + endpoint + "?" + urllib.parse.urlencode({**params, "api_key": self.key})
        started = time.monotonic()
        try:
            got = json.loads(self.fetch(url) or b"{}")
        except Exception as e:
            log_call(self.memory, "airlabs", endpoint, False, time.monotonic() - started, "flight status",
                     type(e).__name__, cost=0.0)
            raise
        error = (got or {}).get("error") if isinstance(got, dict) else None
        log_call(self.memory, "airlabs", endpoint, not error, time.monotonic() - started, "flight status",
                 (error or {}).get("code", "") if error else "", cost=0.0)
        if error:
            code = error.get("code", "")
            raise ToolError("AirLabs didn't accept the key." if "key" in code else
                            "AirLabs' monthly limit is used up." if "limit" in code else
                            f"AirLabs: {error.get('message') or code}")
        return got.get("response") if isinstance(got, dict) else got

    # --- one flight now -----------------------------------------------------------------------------------
    def lookup(self, flight: str, day: str = "", since: str = "") -> dict:
        """The flight on that date: AirLabs first, then what the airline's app or emails said (their gate, delay
        and cancellation win), then FlightAware for what's still missing (a final backup, rationed)."""
        flight = normal_flight(flight)
        status, problems = {}, []
        if self.key:
            try:
                status = self.airlabs(flight, day)
            except ToolError as e:
                problems.append(str(e))
        heard = self.from_messages(flight, day, status, since)
        if heard:
            status = {**status, **{k: v for k, v in heard.items() if not k.startswith("_")}}
            status.setdefault("flight", flight)
            status["airline_said"] = heard["_said"]
        gaps = [k for k in ("dep_gate", "dep_delayed", "status") if k not in status]
        if gaps and self.aero_key and self._aero_allowed(flight, status):
            try:
                fa = self.aeroapi(flight, day)
                status = {**fa, **{k: v for k, v in status.items() if v not in (None, "") and k != "missing"}}
                status["from_flightaware"] = [k for k in gaps if k in fa]
            except ToolError as e:
                problems.append(str(e))
        if not status:
            raise ToolError(problems[0] if problems else
                            f"Nothing on {flight}" + (f" for {day}" if day else "") + " yet: add an AirLabs or "
                            "FlightAware key, or wait for the airline's app or emails.")
        status["flight"] = flight
        status["missing"] = [label for key, label in (("status", "status"), ("dep_gate", "gate"),
                                                       ("dep_delayed", "delay"), ("dep_estimated", "updated times"))
                             if key not in status]
        return status

    def airlabs(self, flight: str, day: str = "") -> dict:
        """AirLabs: the schedule, and the live position while flying."""
        rows = self._get("schedules", flight_iata=flight) or []
        rows = rows if isinstance(rows, list) else [rows]
        mine = [r for r in rows if not day or (r.get("dep_time") or "").startswith(day)]
        out = dict(mine[0]) if mine else {}
        live = {}
        if not out or out.get("status") in (None, "active", "en-route") or "lat" not in out:
            try:
                live = self._get("flight", flight_iata=flight) or {}
            except ToolError:
                live = {}
            if isinstance(live, dict) and (not day or (live.get("dep_time") or "").startswith(day)):
                out = {**live, **{k: v for k, v in out.items() if v not in (None, "")}}
        if not out:
            raise ToolError(f"AirLabs has nothing for {flight}" + (f" on {day}" if day else "") +
                            ": check the number and date (schedules are known about a day ahead).")
        status = {k: out.get(k) for k in KEEP if out.get(k) not in (None, "")}
        status["flight"] = flight
        if "status" not in status and status.get("lat") is not None:
            status["status"] = "en-route"  # a live position: it's flying
        return status

    # --- what the airline itself said: its app's notifications and its emails -------------------------------
    def from_messages(self, flight: str, day: str, known: dict | None = None, since: str = "") -> dict:
        """Gate, terminal, new departure time, delay, boarding, cancellation from the airline's own app
        notifications and emails about this flight (the 2 days before it), latest first-come wins. Text rules,
        no AI: these are the user's messages, read on Buddy's server only."""
        known = known or {}
        code, number = re.match(r"([A-Z0-9]{2})(\d+)", flight).groups()
        names = AIRLINE_NAMES.get(code, [])
        start = (datetime.fromisoformat(day) - timedelta(days=2)).isoformat() if day else \
            (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
        try:
            rows = self.memory.db.execute(
                "SELECT kind, title, body, data, start_at FROM records WHERE kind IN ('notification', 'email') "
                "AND start_at >= ? ORDER BY start_at LIMIT 2000", (start,)).fetchall()
        except Exception:
            return {}  # no records yet
        exact = re.compile(rf"\b{code}\s?-?0*{number}\b", re.I)
        bare = re.compile(rf"(?:flight|flt|#)\s*0*{number}\b", re.I)
        out, said = {}, []
        for r in rows:
            meta = json.loads(r["data"] or "{}")
            source = f"{meta.get('app_name', '')} {meta.get('app', '')} {meta.get('from', '')}"
            text = f"{r['title'] or ''} {r['body'] or ''}"[:3000]
            from_airline = AIRLINE_APP.search(source) is not None or any(n in source.lower() for n in names)
            if not (exact.search(text) or (from_airline and bare.search(text))):
                continue
            if not from_airline and not exact.search(r["title"] or ""):
                continue  # someone else's message that only mentions the flight
            facts = self._facts(text, day or "", known.get("dep_time", ""))
            if facts:
                out.update(facts)
                label = (meta.get("app_name") or "the airline's app") if r["kind"] == "notification" else "an airline email"
                said.append(label)
        if out:
            out["_said"] = list(dict.fromkeys(said))[-2:]
        return out

    @staticmethod
    def _facts(text: str, day: str, scheduled: str) -> dict:
        t = " ".join(text.split())
        f = {}
        if re.search(r"\bcancel+ed\b", t, re.I) and not re.search(r"not (been )?cancel+ed|if .{0,20}cancel", t, re.I):
            f["status"] = "cancelled"
        gates = re.findall(r"\bgate(?:\s+(?:is\s+)?(?:now|changed to|change(?:d)?:?|has changed to|moved to))?\s*:?\s*"
                           r"([A-Z]{0,2}\d{1,3}[A-Z]?)\b", t, re.I)
        if gates:
            f["dep_gate"] = gates[-1].upper()  # the newest gate named ("from D12 to D20" -> D20)
            m = re.search(r"gate\s+([A-Z]{0,2}\d{1,3}[A-Z]?)\s+to\s+(?:gate\s+)?([A-Z]{0,2}\d{1,3}[A-Z]?)", t, re.I)
            if m:
                f["dep_gate"] = m[2].upper()
        term = re.search(r"\bterminal\s+([A-Z0-9]{1,3})\b", t, re.I)
        if term:
            f["dep_terminal"] = term[1].upper()
        new_time = re.search(r"(?:new departure(?: time)?|now (?:departs?|departing|leaves?|leaving)(?: at)?|"
                             r"departs? (?:at|now at)|estimated departure)\s*(?:is|:)?\s*(\d{1,2}:\d{2}\s*[AP]\.?M\.?|"
                             r"\d{1,2}:\d{2})", t, re.I)
        if new_time and day:
            raw = new_time[1].upper().replace(".", "").replace(" ", "")
            try:
                hm = datetime.strptime(raw, "%I:%M%p" if raw[-1] == "M" else "%H:%M")
                est_t = datetime.strptime(f"{scheduled[:10] if scheduled else day} {hm:%H:%M}", "%Y-%m-%d %H:%M")
                if scheduled:
                    sched = datetime.strptime(scheduled[:16], "%Y-%m-%d %H:%M")
                    if est_t < sched - timedelta(hours=6):
                        est_t += timedelta(days=1)  # pushed past midnight: 11:40 PM -> 12:30 AM the next day
                est = est_t.strftime("%Y-%m-%d %H:%M")
                f["dep_estimated"] = est
                if scheduled:
                    late = int((est_t - sched).total_seconds() // 60)
                    if -60 < late < 24 * 60:
                        f["dep_delayed"] = max(0, late)
            except ValueError:
                pass
        late = re.search(r"delayed (?:by )?(?:about |approximately )?(\d{1,3})\s*(?:min|minutes)", t, re.I) or \
            re.search(r"delayed (?:by )?(?:about )?(\d{1,2})\s*(?:h|hr|hrs|hours?)\b", t, re.I)
        if late:
            n = int(late[1]) * (60 if re.search(r"h|hr|hour", late[0][-6:], re.I) else 1)
            f["dep_delayed"] = n
        elif re.search(r"\bon time\b", t, re.I) and "dep_delayed" not in f and "status" not in f:
            f["dep_delayed"] = 0
        if re.search(r"\bnow boarding\b|\bboarding (?:has )?(?:started|begun|now)\b", t, re.I):
            f["boarding"] = True
        return f

    # --- FlightAware AeroAPI: the final backup, rationed ----------------------------------------------------
    @property
    def aero_key(self) -> str:
        return (self.env.get("BUDDY_AEROAPI_KEY") or "").strip()

    def _aero_allowed(self, flight: str, status: dict) -> bool:
        """Only near departure (from 3 hours before until it lands), at most every 30 minutes per flight, at most
        AERO_PER_FLIGHT calls per flight and BUDDY_AEROAPI_MONTHLY (default 100) a month."""
        dep = _utc(status.get("dep_estimated_utc") or status.get("dep_time_utc"))
        now = datetime.now(timezone.utc)
        if dep is not None and not (dep - timedelta(hours=3) <= now <= dep + timedelta(hours=14)):
            return False
        month = now.strftime("%Y-%m")
        used = int(self.memory.get(f"flights:aero:{month}") or 0)
        cap = int((self.env.get("BUDDY_AEROAPI_MONTHLY") or "100").strip() or 100)
        mine = json.loads(self.memory.get(f"flights:aero:{flight}") or '{"n": 0, "at": ""}')
        if used >= cap or mine["n"] >= AERO_PER_FLIGHT:
            return False
        if mine["at"] and datetime.fromisoformat(mine["at"]) > now - timedelta(minutes=30):
            return False
        return True

    def aeroapi(self, flight: str, day: str = "") -> dict:
        from .travel import log_call  # noqa: PLC0415
        now = datetime.now(timezone.utc)
        month = now.strftime("%Y-%m")
        self.memory.set(f"flights:aero:{month}", str(int(self.memory.get(f"flights:aero:{month}") or 0) + 1))
        mine = json.loads(self.memory.get(f"flights:aero:{flight}") or '{"n": 0, "at": ""}')
        self.memory.set(f"flights:aero:{flight}", json.dumps({"n": mine["n"] + 1, "at": now.isoformat()}))
        base = date.fromisoformat(day) if day else date.today()
        url = AERO + urllib.parse.quote(flight) + "?" + urllib.parse.urlencode({  # just the days around it: one page
            "ident_type": "designator", "start": (base - timedelta(days=1)).isoformat(),
            "end": (base + timedelta(days=2)).isoformat(), "max_pages": 1})
        started = time.monotonic()
        try:
            got = json.loads(self.aero_fetch(url, {"x-apikey": self.aero_key, "Accept": "application/json"}) or b"{}")
            log_call(self.memory, "flightaware", "flights", True, time.monotonic() - started, "flight status", cost=0.0)
        except Exception as e:
            log_call(self.memory, "flightaware", "flights", False, time.monotonic() - started, "flight status",
                     type(e).__name__, cost=0.0)
            code = getattr(e, "code", None)
            raise ToolError("FlightAware didn't accept the key." if code in (401, 403) else
                            f"FlightAware: {type(e).__name__}{' ' + str(code) if code else ''}") from e
        return aero_status(got, flight, day)

    def test(self) -> str:
        """'Test my flight sources': each source once. AirLabs: one schedule lookup (and which fields this plan
        gives); FlightAware: its usage endpoint (checks the key without a flight lookup, and gives this month's cost);
        airline messages: how many arrived lately."""
        lines = []
        if self.key:
            try:
                rows = self._get("schedules", flight_iata="AA100") or []
                row = (rows if isinstance(rows, list) else [rows] or [{}])[0] if rows else {}
                has = [label for key, label in (("dep_gate", "gates"), ("dep_delayed", "delays"),
                                                ("status", "status"), ("dep_estimated", "updated times")) if key in row]
                lacks = [label for label in ("gates", "delays", "status", "updated times") if label not in has]
                lines.append("✓ AirLabs: key works" + (f"; your plan gives {', '.join(has)}" if has else "") +
                             (f"; not {', '.join(lacks)} (the airline's app and emails, or FlightAware, fill those)"
                              if lacks else ""))
            except Exception as e:
                lines.append(f"✗ AirLabs: {e}")
        else:
            lines.append("– AirLabs: no key (sudo bash /opt/buddy/deploy/set.sh BUDDY_AIRLABS_KEY)")
        if self.aero_key:
            try:
                start = date.today().replace(day=1).isoformat()
                got = json.loads(self.aero_fetch(AERO.replace("flights/", "account/usage") + "?start=" + start,
                                                 {"x-apikey": self.aero_key, "Accept": "application/json"}) or b"{}")
                cost = got.get("total_cost")
                lines.append(f"✓ FlightAware: key works; {got.get('total_calls', 0)} calls this month" +
                             (f", ${float(cost):.2f}" if cost is not None else "") +
                             f" (Buddy allows {(self.env.get('BUDDY_AEROAPI_MONTHLY') or '100').strip()} a month)")
            except Exception as e:
                code = getattr(e, "code", None)
                lines.append("✗ FlightAware: the key wasn't accepted" if code in (401, 403) else
                             f"✗ FlightAware: {type(e).__name__}{' ' + str(code) if code else ''}")
        else:
            lines.append("– FlightAware: no key (optional backup: sudo bash /opt/buddy/deploy/set.sh BUDDY_AEROAPI_KEY)")
        try:
            since = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
            n = sum(1 for r in self.memory.db.execute(
                "SELECT data FROM records WHERE kind IN ('notification', 'email') AND start_at >= ?", (since,))
                if AIRLINE_APP.search(" ".join(str(v) for v in json.loads(r["data"] or "{}").values())))
            lines.append(f"✓ Airline app and emails: {n} airline message{'s' if n != 1 else ''} in the last 7 days")
        except Exception:
            lines.append("– Airline app and emails: nothing read yet")
        return "Flight sources:\n" + "\n".join(lines)

    @staticmethod
    def describe(s: dict) -> str:
        f = s.get("flight", "")
        route = f"{s.get('dep_iata', '?')} → {s.get('arr_iata', '?')}"
        st = (s.get("status") or "").replace("active", "in the air").replace("en-route", "in the air")
        parts = [f"{f} {route}"]
        if st == "cancelled":
            return f"{f} {route} is CANCELLED."
        if st == "landed":
            parts.append(f"landed {_hm(s.get('arr_actual') or s.get('arr_estimated') or s.get('arr_time'))}" +
                         (f", bags at carousel {s['arr_baggage']}" if s.get("arr_baggage") else ""))
        else:
            dep = s.get("dep_actual") or s.get("dep_estimated") or s.get("dep_time")
            late = int(s.get("dep_delayed") or 0)
            parts.append(("departed " if s.get("dep_actual") else "departs ") + _hm(dep) +
                         (f" ({late} min late)" if late >= 5 else " (on time)" if "dep_delayed" in s else ""))
            where = " ".join(x for x in ((f"terminal {s['dep_terminal']}" if s.get("dep_terminal") else ""),
                                         (f"gate {s['dep_gate']}" if s.get("dep_gate") else "")) if x)
            if where:
                parts.append(where)
            parts.append(f"arrives {_hm(s.get('arr_estimated') or s.get('arr_time'))}" +
                         (f" at gate {s['arr_gate']}" if s.get("arr_gate") else ""))
            if st:
                parts.append(st)
        return ", ".join(p for p in parts if p) + "."

    # --- tracked flights ----------------------------------------------------------------------------------
    def tracked(self, include_done: bool = False) -> list[dict]:
        rows = self.memory.db.execute("SELECT * FROM flight_watch" + ("" if include_done else " WHERE done = 0") +
                                      " ORDER BY day, id").fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["last"], d["told"] = json.loads(d["last"] or "{}"), json.loads(d["told"] or "{}")
            d["summary"] = self.describe(d["last"]) if d["last"] else f"{d['flight']} on {d['day']}: not checked yet."
            out.append(d)
        return out

    def track(self, flight: str, day: str = "", note: str = "") -> str:
        flight = normal_flight(flight)
        day = (day or local_now().date().isoformat()).strip()[:10]
        try:
            date.fromisoformat(day)
        except ValueError as e:
            raise ToolError("The date looks like 2026-11-07.") from e
        if day < (date.today() - timedelta(days=1)).isoformat():
            raise ToolError("That date has passed.")
        self.memory.db.execute(
            "INSERT INTO flight_watch (flight, day, note, added_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT (flight, day) DO UPDATE SET note = excluded.note, done = 0",
            (flight, day, note.strip()[:80], datetime.now(timezone.utc).isoformat()))
        self.memory.db.commit()
        extra = ""
        if not (self.key or self.aero_key):
            extra = (" With no flight data key I'll go by your airline's app notifications and emails; for the "
                     "schedule and live position add a free AirLabs key (sudo bash /opt/buddy/deploy/set.sh "
                     "BUDDY_AIRLABS_KEY).")
        try:  # first look now, so the user hears the schedule straight away
            status = self.lookup(flight, day)
            self._save(flight, day, status, self._baseline(status))
            extra = " " + self.describe(status)
            if status["missing"]:
                extra += (f" (No {', '.join(status['missing'])} yet: your AirLabs plan may not give them. I'll "
                          "also go by your airline's app notifications and emails, and FlightAware if it's set "
                          "up.)")
        except ToolError as e:
            extra += "" if extra else f" {e}"
        return (f"Tracking {flight} on {day}: from about {BEFORE_HOURS} hours before departure until it lands, I'll "
                f"tell you about delays, gate changes, cancellation and landing.{extra}")

    def untrack(self, flight: str, day: str = "") -> str:
        flight = normal_flight(flight)
        q, args = "DELETE FROM flight_watch WHERE flight = ?", [flight]
        if day:
            q, args = q + " AND day = ?", [flight, day[:10]]
        n = self.memory.db.execute(q, args).rowcount
        self.memory.db.commit()
        return f"Stopped tracking {flight}." if n else f"I wasn't tracking {flight}."

    @staticmethod
    def _baseline(s: dict) -> dict:
        """What counts as already known: the schedule as first seen (so the first check doesn't 'announce' it)."""
        return {"dep_gate": s.get("dep_gate"), "dep_terminal": s.get("dep_terminal"), "arr_iata": s.get("arr_iata"),
                "delay": int(s.get("dep_delayed") or 0) // DELAY_STEP * DELAY_STEP, "status": s.get("status")}

    def _save(self, flight: str, day: str, status: dict, told: dict | None = None, done: bool = False) -> None:
        sets, args = ["last = ?", "checked_at = ?", "done = ?"], [json.dumps(status), datetime.now(timezone.utc).isoformat(),
                                                                 int(done)]
        if told is not None:
            sets.append("told = ?")
            args.append(json.dumps(told))
        self.memory.db.execute(f"UPDATE flight_watch SET {', '.join(sets)} WHERE flight = ? AND day = ?",
                               (*args, flight, day))
        self.memory.db.commit()

    def window(self, w: dict, now: datetime | None = None) -> bool:
        """Within the checking window: from BEFORE_HOURS before departure to AFTER_HOURS after arrival."""
        now = now or datetime.now(timezone.utc)
        last = w["last"]
        dep = _utc(last.get("dep_estimated_utc") or last.get("dep_time_utc"))
        arr = _utc(last.get("arr_estimated_utc") or last.get("arr_time_utc"))
        if dep is None:  # no times known yet: its day, and from 6 PM the evening before (an early flight)
            today = local_now().date()
            return w["day"] <= today.isoformat() or (w["day"] == (today + timedelta(days=1)).isoformat()
                                                    and local_now().hour >= 18)
        return dep - timedelta(hours=BEFORE_HOURS) <= now <= (arr or dep + timedelta(hours=12)) + timedelta(hours=AFTER_HOURS)

    def changes(self, s: dict, told: dict) -> tuple[list[str], dict]:
        """What's new since last told, as sentences, and the updated 'told'."""
        f, out, told = s["flight"], [], dict(told)
        st = s.get("status")
        if st == "cancelled" and told.get("status") != "cancelled":
            out.append(f"{f} to {s.get('arr_iata', '')} has been CANCELLED. Check the airline app for rebooking.")
        if told.get("arr_iata") and s.get("arr_iata") and s["arr_iata"] != told["arr_iata"]:
            out.append(f"{f} is now going to {s['arr_iata']} instead of {told['arr_iata']} (diverted).")
        late = int(s.get("dep_delayed") or 0)
        step = late // DELAY_STEP * DELAY_STEP
        if not s.get("dep_actual") and step >= DELAY_STEP and step > int(told.get("delay") or 0):
            out.append(f"{f} is delayed about {late} minutes: now leaving {_hm(s.get('dep_estimated'))}" +
                       (f" from gate {s['dep_gate']}" if s.get("dep_gate") else "") + ".")
        elif not s.get("dep_actual") and step < int(told.get("delay") or 0) and late < 5:
            out.append(f"{f} is back on time: leaving {_hm(s.get('dep_estimated') or s.get('dep_time'))}.")
        for key, label in (("dep_terminal", "terminal"), ("dep_gate", "gate")):
            if s.get(key) and told.get(key) and s[key] != told[key]:
                out.append(f"{f} {label} change: now {label} {s[key]} (was {told[key]}).")
            elif s.get(key) and not told.get(key) and key == "dep_gate":
                out.append(f"{f} departs from gate {s[key]}" +
                           (f", terminal {s['dep_terminal']}" if s.get("dep_terminal") else "") + ".")
        if (s.get("dep_actual") or st in ("active", "en-route")) and not told.get("departed") and st != "landed":
            out.append(f"{f} has taken off" + (f" at {_hm(s['dep_actual'])}" if s.get("dep_actual") else "") +
                       f"; arriving about {_hm(s.get('arr_estimated') or s.get('arr_time'))}.")
            told["departed"] = True
        if s.get("diverted") and not told.get("diverted"):
            out.append(f"{f} has been diverted. Check the airline app.")
            told["diverted"] = True
        if s.get("boarding") and not told.get("boarding") and not s.get("dep_actual"):
            out.append(f"{f} is boarding" + (f" at gate {s['dep_gate']}" if s.get("dep_gate") else "") + ".")
            told["boarding"] = True
        if (st == "landed" or s.get("arr_actual")) and not told.get("landed"):
            out.append(f"{f} has landed in {s.get('arr_iata', '')}" +
                       (f" at {_hm(s['arr_actual'])}" if s.get("arr_actual") else "") +
                       (f"; gate {s['arr_gate']}" if s.get("arr_gate") else "") +
                       (f", bags at carousel {s['arr_baggage']}" if s.get("arr_baggage") else "") + ".")
            told["landed"] = True
        told.update({"dep_gate": s.get("dep_gate") or told.get("dep_gate"),
                     "dep_terminal": s.get("dep_terminal") or told.get("dep_terminal"),
                     "arr_iata": told.get("arr_iata") or s.get("arr_iata"), "status": st,
                     "delay": step if not s.get("dep_actual") else told.get("delay", 0)})
        return out, told

    def check(self, now: datetime | None = None) -> list[dict]:
        """Every tracked flight in its window: the news to tell (each change once)."""
        alerts = []
        self.memory.db.execute("DELETE FROM flight_watch WHERE day < ?",  # kept 30 days after the flight, then gone
                               ((date.today() - timedelta(days=30)).isoformat(),))
        for w in self.tracked():
            if w["day"] < (date.today() - timedelta(days=2)).isoformat():
                self._save(w["flight"], w["day"], w["last"], done=True)
                continue
            if not self.window(w, now):
                arr = _utc(w["last"].get("arr_estimated_utc") or w["last"].get("arr_time_utc"))
                if arr and (now or datetime.now(timezone.utc)) > arr + timedelta(hours=AFTER_HOURS):
                    self._save(w["flight"], w["day"], w["last"], done=True)  # past: stop
                continue
            try:
                s = self.lookup(w["flight"], w["day"])
            except ToolError as e:
                log.info("flight %s: %s", w["flight"], e)
                continue
            except Exception:
                log.info("flight %s check failed", w["flight"], exc_info=True)
                continue
            told = w["told"] if w["told"] else self._baseline(s)
            news, told = self.changes(s, told)
            done = s.get("status") in ("landed", "cancelled") or bool(s.get("arr_actual"))
            self._save(w["flight"], w["day"], s, told, done)
            for text in news:
                alerts.append({"flight": w["flight"], "text": text + (f" ({w['note']})" if w["note"] else "")})
        return alerts

    def due(self, now: datetime | None = None) -> bool:
        now_l = local_now()
        slot = now_l.strftime("%Y-%m-%dT%H:") + str(now_l.minute // CHECK_MINUTES)
        if self.memory.get("flights:checked") == slot or not any(self.window(w, now) for w in self.tracked()):
            return False
        self.memory.set("flights:checked", slot)
        return True


FLIGHT_TOOL = {
    "name": "flight_status",
    "description": "Live status of the user's flights (status with no flight: every upcoming flight Buddy knows from "
    "tracked flights, emails and uploads; never ask for the number before trying that): AirLabs, then what the airline's own app notifications and "
    "emails said (gate, new time, delay, boarding, cancellation), then FlightAware for what's still missing. status (flight like 'AA 1234', day?): on time or "
    "delayed, gates, terminals, departed/landed, bag carousel. track (flight, day YYYY-MM-DD, note?): Buddy checks it "
    "every 15 minutes from 4 hours before departure until it lands and messages on delays, gate or terminal changes, "
    "cancellation, diversion, take-off and landing. When a booking or check-in email or the user mentions a flight, "
    "offer to track it (find the flight number and date in the email). untrack (flight, day?). list. Read-only: never "
    "change bookings. test: 'test my flight sources' (AirLabs, FlightAware with this month's cost, airline messages); "
    "give the result line by line.",
    "input_schema": {"type": "object", "properties": {
        "action": {"type": "string", "enum": ["status", "track", "untrack", "list", "test"]},
        "flight": {"type": "string"}, "day": {"type": "string"}, "note": {"type": "string"}},
        "required": ["action"], "additionalProperties": False},
}


def run_tool(memory: Memory, args: dict) -> str:
    f = Flights(memory)
    a = args.get("action")
    if a == "track":
        return f.track(args.get("flight", ""), args.get("day") or "", args.get("note") or "")
    if a == "untrack":
        return f.untrack(args.get("flight", ""), args.get("day") or "")
    if a == "test":
        return f.test()
    if a == "list":
        items = f.tracked()
        return json.dumps([{k: x[k] for k in ("flight", "day", "note", "summary")} for x in items]) if items else \
            "No flights tracked."
    if not (args.get("flight") or "").strip():  # "what's my flight status?": the flights Buddy knows about
        known = [x for x in f.tracked()]
        if not known:
            try:
                from .connect import Connect  # noqa: PLC0415
                for p in Connect(memory).upcoming(days=14):
                    if p["kind"] == "flight" and p["details"].get("flight"):
                        known.append({"flight": p["details"]["flight"], "day": p["starts"][:10]})
            except Exception:
                pass
        if not known:
            return ("No upcoming flight found in tracked flights, emails or uploads. Search the user's email (gmail) "
                    "and library for a booking before asking them for the flight number.")
        out = []
        for k in known[:3]:
            try:
                s = f.lookup(k["flight"], k["day"])
                out.append({"summary": f.describe(s), **s})
            except Exception as e:
                out.append({"flight": k["flight"], "day": k["day"], "error": str(e)})
        return json.dumps(out, ensure_ascii=False)
    s = f.lookup(args.get("flight", ""), (args.get("day") or "")[:10])
    return json.dumps({"summary": f.describe(s), **s}, ensure_ascii=False)
