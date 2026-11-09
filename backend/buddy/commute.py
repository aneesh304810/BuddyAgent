"""The user's weekday commute, learned from where the phone goes, and heads-ups before they leave.

From the 30-day location history (maps.py, only while location sharing is on): when they usually leave home and reach
work, when they leave work and get home, and which transit they ride (PATH stations and light rail stops the phone
passes on the way). It's saved as a fact in memory ("what's my commute?"), the lines are watched during those times
(transit.py tells them when a delay starts or clears), and about 15 minutes before they usually leave Buddy says the
next trains and any alerts. Everything stays on Buddy's server; nothing here is sent anywhere but the transit feeds
(which see no personal data). Learned again once a day; "stop commute alerts" turns it off.
"""

import json
import statistics
from datetime import datetime, timedelta

from .memory import Memory
from .tools import ToolError, local_now

KEY = "commute:routine"
OFF_KEY = "commute:off"
AUTO_KEY = "commute:auto_watches"  # lines Buddy started watching for the commute (the user's own watches stay theirs)
FACT = "My weekday commute:"
DAYS = 30
MIN_TRIPS = 3
HEADS_UP = (20, 5)  # minutes before the usual leave time: the window for the heads-up
NEAR_STOP_KM = {"path": 0.3, "light_rail": 0.4}


def _hm(minutes: float) -> str:
    m = int(round(minutes)) % (24 * 60)
    return f"{m // 60:02d}:{m % 60:02d}"


def _min(t: datetime) -> int:
    return t.hour * 60 + t.minute


def _say(hm: str) -> str:
    h, m = map(int, hm.split(":"))
    return datetime(2000, 1, 1, h, m).strftime("%-I:%M %p")


def _trip(pts: list, start_at, end_at, after: int, before: int) -> dict | None:
    """One trip on one day: from the last point at `start_at` to the first point at `end_at` after it."""
    leave = None
    for (t0, at0), (t1, at1) in zip(pts, pts[1:]):
        if not (after <= _min(t1) < before):
            continue
        if leave is None and start_at(at0) and not start_at(at1):
            leave = t1
        elif leave is not None and end_at(at1):
            between = [p for t, p in pts if leave <= t <= t1]
            return {"leave": _min(leave), "arrive": _min(t1), "points": between}
    return None


def learn(memory: Memory) -> dict | None:
    """The weekday routine from the last DAYS days, or None when there isn't enough to go on (home and work unknown,
    or fewer than MIN_TRIPS trips)."""
    from .maps import Maps
    from .transit import LIGHT_RAIL, PATH_COORDS, km
    maps = Maps(memory)
    places = {p["name"]: (p["lat"], p["lng"]) for p in maps.places()}
    home, work = places.get("home"), places.get("work")
    if not home or not work:
        return None
    days: dict = {}
    for t, la, ln in maps._history(DAYS):
        if t.weekday() < 5:
            days.setdefault(t.date(), []).append((t, (la, ln)))

    def at(place):
        return lambda p: km(p, place) <= 0.5

    trips = {"morning": [], "evening": []}
    for pts in days.values():
        pts.sort()
        if (m := _trip(pts, at(home), lambda p: km(p, work) <= 0.4, 5 * 60, 12 * 60)):
            trips["morning"].append(m)
        if (e := _trip(pts, at(work), lambda p: km(p, home) <= 0.4, 14 * 60, 24 * 60)):
            trips["evening"].append(e)
    routine: dict = {}
    for name, found in trips.items():
        if len(found) >= MIN_TRIPS:
            routine[name] = {"leave": _hm(statistics.median(x["leave"] for x in found)),
                             "arrive": _hm(statistics.median(x["arrive"] for x in found)), "trips": len(found)}
    if not routine:
        return None

    # What they ride: stops the phone passed on at least 40% of trips (two at the least).
    every = trips["morning"] + trips["evening"]
    need = max(2, round(0.4 * len(every)))
    seen_lines: dict[str, int] = {}
    path_from = {"morning": {}, "evening": {}}
    for name, found in trips.items():
        for trip in found:
            lines = set()
            first_path = ""
            for p in trip["points"]:
                for code, c in PATH_COORDS.items():
                    if km(p, c) <= NEAR_STOP_KM["path"]:
                        lines.add("PATH")
                        first_path = first_path or code
                for line, stops in LIGHT_RAIL.items():
                    if min(km(p, s) for s in stops) <= NEAR_STOP_KM["light_rail"]:
                        lines.add(line)
            for line in lines:
                seen_lines[line] = seen_lines.get(line, 0) + 1
            if first_path:
                path_from[name][first_path] = path_from[name].get(first_path, 0) + 1
    routine["lines"] = sorted(line for line, n in seen_lines.items() if n >= need)
    for name in ("morning", "evening"):
        if name in routine and "PATH" in routine["lines"] and path_from[name]:
            routine[name]["path_station"] = max(path_from[name], key=path_from[name].get)
    routine["learned"] = local_now().date().isoformat()
    return routine


def routine(memory: Memory) -> dict:
    try:
        return json.loads(memory.get(KEY) or "{}")
    except ValueError:
        return {}


def describe(r: dict) -> str:
    from .transit import PATH_STATIONS
    parts = []
    for name, label in (("morning", ("home", "work")), ("evening", ("work", "home"))):
        if name in r:
            x = r[name]
            parts.append(f"leaves {label[0]} about {_say(x['leave'])} and reaches {label[1]} about {_say(x['arrive'])}"
                         + (f" (PATH from {PATH_STATIONS.get(x['path_station'], x['path_station'])})"
                            if x.get("path_station") else ""))
    rides = ", ".join(r.get("lines") or []) or "no transit line I could recognise"
    return "; ".join(parts) + f"; rides {rides}"


def _watch_times(r: dict) -> list[str]:
    spans = []
    for name in ("morning", "evening"):
        if name in r:
            h, m = map(int, r[name]["leave"].split(":"))
            a, b = map(int, r[name]["arrive"].split(":"))
            spans.append(f"{_hm(h * 60 + m - 45)}-{_hm(a * 60 + b + 15)}")
    return spans


def save(memory: Memory, r: dict) -> str:
    """Store the routine, the fact, and watches on its lines during commute times. Returns a message for the user
    when the routine is new or has changed (leave times by 15+ minutes, or the lines), else ''."""
    old = routine(memory)
    memory.set(KEY, json.dumps(r))
    memory.forget(FACT)
    memory.remember(f"{FACT} {describe(r)} (learned from location history, {r['learned']}).")
    from .transit import Transit
    t = Transit(memory)
    try:
        auto = json.loads(memory.get(AUTO_KEY) or "[]")
    except ValueError:
        auto = []
    mine = {w["line"] for w in t.watches()} - set(auto)  # the user's own watches: never changed here
    for line in set(auto) - set(r.get("lines") or []):
        t.unwatch(line)
    watched = []
    for line in r.get("lines") or []:
        if line in mine:
            continue
        station = ""
        if line == "PATH":
            from .transit import PATH_STATIONS
            code = (r.get("morning") or {}).get("path_station") or (r.get("evening") or {}).get("path_station") or ""
            station = PATH_STATIONS.get(code, "")
        try:
            t.watch(line, station, "weekdays", _watch_times(r))
            watched.append(line)
        except ToolError:
            pass
    memory.set(AUTO_KEY, json.dumps(watched))

    def moved(a, b):
        if not a or not b:
            return a != b
        ah, am = map(int, a["leave"].split(":"))
        bh, bm = map(int, b["leave"].split(":"))
        return abs((ah * 60 + am) - (bh * 60 + bm)) >= 15
    if old and not moved(old.get("morning"), r.get("morning")) and not moved(old.get("evening"), r.get("evening")) \
            and set(old.get("lines") or []) == set(r.get("lines") or []):
        return ""
    return (f"I've {'updated' if old else 'learned'} your weekday commute from where your phone goes: you "
            f"{describe(r)}. I'll give you a heads-up about 15 minutes before you leave, with the next trains and any "
            "alerts, and tell you if a delay starts on the way. Say \"stop commute alerts\" to turn this off.")


def relearn(memory: Memory) -> str:
    """Once a day (and when asked): learn again; a message when it's new or changed."""
    if memory.get(OFF_KEY) == "1":
        return ""
    r = learn(memory)
    return save(memory, r) if r else ""


def nudges(memory: Memory, now: datetime | None = None) -> list[dict]:
    """The heads-up before they usually leave, morning and evening (said once a day each, by key)."""
    if memory.get(OFF_KEY) == "1":
        return []
    r = routine(memory)
    now = now or local_now().replace(tzinfo=None)
    if not r or now.weekday() >= 5:
        return []
    out = []
    for name, place in (("morning", "home"), ("evening", "work")):
        if name not in r:
            continue
        h, m = map(int, r[name]["leave"].split(":"))
        leave = now.replace(hour=h, minute=m, second=0, microsecond=0)
        if not leave - timedelta(minutes=HEADS_UP[0]) <= now <= leave - timedelta(minutes=HEADS_UP[1]):
            continue
        text, urgent = heads_up(memory, r, name, place)
        out.append({"key": f"commute:{name}:{now.date()}", "urgent": urgent, "text": text})
    return out


def heads_up(memory: Memory, r: dict, name: str, place: str) -> tuple[str, bool]:
    from .transit import PATH_STATIONS, Transit
    t = Transit(memory)
    x = r[name]
    parts = [f"Heads-up: you usually leave {place} about {_say(x['leave'])}."]
    if x.get("path_station"):
        try:
            trains = t.path_arrivals(x["path_station"])
        except Exception:
            trains = []
        if trains:
            parts.append(f"PATH from {PATH_STATIONS.get(x['path_station'], x['path_station'])}: " +
                         "; ".join(f"{a['to']} {a['in']}" for a in trains[:3]) + ".")
    bad = []
    for line in r.get("lines") or []:
        try:
            st = t.status(line)
        except Exception:
            continue
        for item in st.get("lines") or []:
            if item.get("ok", True):
                continue  # only what affects the ride: delays, suspensions, shuttles
            for a in (item.get("alerts") or [])[:1]:
                bad.append(f"{item.get('line', line)}: {a['text'][:180]}")
    if bad:
        parts.append("Alerts: " + " ".join(bad[:2]))
    elif r.get("lines"):
        parts.append(f"No alerts on {' or '.join(r['lines'])}.")
    return " ".join(parts), bool(bad)


TOOL = {
    "name": "commute",
    "description": "The user's weekday commute, learned from where their phone goes: when they usually leave home "
    "and reach work, leave work and get home, and the transit they ride. Buddy watches those lines and gives a "
    "heads-up before they leave. show: the routine; relearn: learn it again now; off / on: stop or restart commute "
    "heads-ups ('stop commute alerts').",
    "input_schema": {"type": "object", "properties": {"action": {"type": "string",
                                                                  "enum": ["show", "relearn", "off", "on"]}},
                     "required": ["action"], "additionalProperties": False},
}


def run_tool(memory: Memory, args: dict) -> str:
    action = args.get("action") or "show"
    if action == "off":
        memory.set(OFF_KEY, "1")
        from .transit import Transit
        t = Transit(memory)
        for line in json.loads(memory.get(AUTO_KEY) or "[]"):
            t.unwatch(line)
        memory.set(AUTO_KEY, "[]")
        return "Commute heads-ups are off, and I've stopped watching the lines I'd added for it. Say \"turn commute " \
               "alerts back on\" anytime."
    if action == "on":
        memory.set(OFF_KEY, "")
        action = "relearn"
    if action == "relearn":
        r = learn(memory)
        if not r:
            return ("I can't see a weekday routine yet: I need home and work (learned from where the phone is at night "
                    f"and in working hours) and at least {MIN_TRIPS} trips between them in the last {DAYS} days, with "
                    "location sharing on.")
        said = save(memory, r)
        return said or f"Your commute hasn't changed: you {describe(r)}."
    r = routine(memory)
    if not r:
        return "I haven't learned your commute yet. Say \"learn my commute\" and I'll look at the last 30 days."
    off = " (heads-ups are off)" if memory.get(OFF_KEY) == "1" else ""
    return f"Your weekday commute{off}: you {describe(r)}. Learned {r.get('learned')}."
