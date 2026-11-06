"""Flight status for the user's own flights, from AirLabs (airlabs.co), checked in the background.

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
    def __init__(self, memory: Memory, env=None, fetch=None):
        self.memory = memory
        self.env = os.environ if env is None else env
        self.fetch = fetch or _fetch
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
    def lookup(self, flight: str, day: str = "") -> dict:
        """The flight on that date (or the closest one): schedule, status, gates, delays, position."""
        flight = normal_flight(flight)
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
        status["missing"] = [label for key, label in (("status", "status"), ("dep_gate", "gate"),
                                                       ("dep_delayed", "delay"), ("dep_estimated", "updated times"))
                             if key not in out]
        return status

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
        if not self.key:
            extra = (" To start checking it needs a free AirLabs key: sign up at airlabs.co, then sudo bash "
                     "/opt/buddy/deploy/set.sh BUDDY_AIRLABS_KEY.")
        else:
            try:  # first look now, so the user hears the schedule straight away
                status = self.lookup(flight, day)
                self._save(flight, day, status, self._baseline(status))
                extra = " " + self.describe(status)
                if status["missing"]:
                    extra += (f" (Your AirLabs plan doesn't give {', '.join(status['missing'])}; I'll still tell you "
                              "about departure, landing and anything it does report.)")
            except ToolError as e:
                extra = f" {e}"
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
        if dep is None:  # not looked up yet: check on its day
            return local_now().date().isoformat() >= w["day"]
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
    "description": "Live status of the user's flights (AirLabs). status (flight like 'AA 1234', day?): on time or "
    "delayed, gates, terminals, departed/landed, bag carousel. track (flight, day YYYY-MM-DD, note?): Buddy checks it "
    "every 15 minutes from 4 hours before departure until it lands and messages on delays, gate or terminal changes, "
    "cancellation, diversion, take-off and landing. When a booking or check-in email or the user mentions a flight, "
    "offer to track it (find the flight number and date in the email). untrack (flight, day?). list. Read-only: never "
    "change bookings.",
    "input_schema": {"type": "object", "properties": {
        "action": {"type": "string", "enum": ["status", "track", "untrack", "list"]},
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
    if a == "list":
        items = f.tracked()
        return json.dumps([{k: x[k] for k in ("flight", "day", "note", "summary")} for x in items]) if items else \
            "No flights tracked."
    s = f.lookup(args.get("flight", ""), (args.get("day") or "")[:10])
    return json.dumps({"summary": f.describe(s), **s}, ensure_ascii=False)
