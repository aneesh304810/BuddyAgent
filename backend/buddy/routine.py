"""The owner's daily routine, learned on its own, and what they're doing right now.

Learning (every night, from the last 6 weeks of the location history the app shares, maps.py): for weekdays,
Saturdays and Sundays, what the owner is usually doing in each half hour: at home, at the office, commuting, at a
place they've named (gym, Trader Joe's...), or out (errands). Half hours become blocks ("weekdays: commuting 7:40 to
8:30, at the office 8:30 to 5:30"). What the owner tells Buddy about their routine ("I go to the gym Tuesdays at 7")
is kept as facts and shown with it; their word beats the learned pattern.

Right now (activity): the calendar first (a meeting, a doctor's appointment), then live location (driving, on the
move, at home, at the office, at a named place, out), then the learned routine. Each with until when, if known.
Phone calls use it: contacts hear what the owner is doing ("on the train, free around 6:15"); unknown callers only
that they're busy. Never an address or exact place.
"""

import json
import re
from collections import Counter
from datetime import datetime, timedelta

from .memory import Memory
from .tools import ToolError, local_now

KEY = "routine:week"
DAYS = 42
SLOT_MIN = 30
MIN_DAYS = 3           # a half hour needs this many days of data before it says anything
MIN_SHARE = 0.5        # ...and the same thing on at least half of them
MOVING_KM = 0.5        # points in one half hour spread wider than this: on the move
APPOINTMENT = re.compile(r"\b(doctor|dr\.?|dentist|dental|clinic|hospital|therapy|therapist|physio|appointment|"
                         r"checkup|check-up|pharmacy|lab|vaccin\w*|eye exam|optometrist|vet)\b", re.I)
TYPES = ("weekdays", "saturday", "sunday")


def _day_type(d) -> str:
    return "weekdays" if d.weekday() < 5 else ("saturday" if d.weekday() == 5 else "sunday")


def _say(minutes: int) -> str:
    h, m = divmod(minutes % (24 * 60), 60)
    return f"{(h % 12) or 12}:{m:02d}{' am' if h < 12 else ' pm'}".replace(":00 ", " ")


def _label(maps, points) -> str:
    from .maps import _km  # noqa: PLC0415
    coords = [(la, ln) for _t, la, ln in points]
    if len(coords) >= 2 and max(_km(coords[0], c) for c in coords[1:]) > MOVING_KM:
        return "commuting"
    names = Counter(maps.place_at(la, ln) for la, ln in coords)
    name, _n = names.most_common(1)[0]
    return name or "out"


def learn(memory: Memory) -> dict:
    """The week's usual blocks from the location history: {"weekdays": [{"from", "to", "what"}], ...}."""
    from .maps import Maps  # noqa: PLC0415
    maps = Maps(memory)
    if not maps.places():
        maps.learn_places()
    by_day: dict = {}
    for t, la, ln in maps._history(DAYS):
        by_day.setdefault(t.date(), {}).setdefault((t.hour * 60 + t.minute) // SLOT_MIN, []).append((t, la, ln))
    votes: dict = {k: {} for k in TYPES}
    for day, slots in by_day.items():
        for slot, points in slots.items():
            votes[_day_type(day)].setdefault(slot, []).append(_label(maps, points))
    out = {"learned_at": local_now().isoformat(timespec="minutes"), "days": len(by_day)}
    for kind in TYPES:
        blocks: list[dict] = []
        for slot in range(24 * 60 // SLOT_MIN):
            seen = votes[kind].get(slot, [])
            what = ""
            if len(seen) >= MIN_DAYS:
                top, n = Counter(seen).most_common(1)[0]
                if n / len(seen) >= MIN_SHARE:
                    what = top
            start = slot * SLOT_MIN
            if what and blocks and blocks[-1]["what"] == what and blocks[-1]["to"] == start:
                blocks[-1]["to"] = start + SLOT_MIN
            elif what:
                blocks.append({"from": start, "to": start + SLOT_MIN, "what": what})
        out[kind] = blocks
    return out


def save(memory: Memory, r: dict) -> None:
    memory.set(KEY, json.dumps(r))


def week(memory: Memory) -> dict:
    try:
        return json.loads(memory.get(KEY) or "{}")
    except ValueError:
        return {}


def relearn(memory: Memory) -> str:
    """Nightly: learn again from the latest history (quietly)."""
    r = learn(memory)
    if any(r.get(k) for k in TYPES):
        save(memory, r)
    return describe(r)


def _what(what: str) -> str:
    return {"home": "at home", "work": "at the office", "commuting": "commuting", "out": "out (errands)"}.get(
        what, f"at {what}")


def describe(r: dict | None) -> str:
    if not r or not any(r.get(k) for k in TYPES):
        return ""
    parts = []
    for kind in TYPES:
        blocks = [b for b in r.get(kind, []) if b["to"] - b["from"] >= SLOT_MIN]
        if blocks:
            parts.append(f"{kind.capitalize()}: " + ", ".join(
                f"{_what(b['what'])} {_say(b['from'])} to {_say(b['to'])}" for b in blocks))
    return "; ".join(parts)


def told(memory: Memory) -> list[str]:
    """What the owner said about their routine (facts with days, times and habits)."""
    try:
        rows = memory.db.execute(
            "SELECT text FROM facts WHERE text LIKE '%every %' OR text LIKE '%usually%' OR text LIKE '%routine%' "
            "OR text LIKE '%gym%' OR text LIKE '%on mondays%' OR text LIKE '%on weekends%' ORDER BY id DESC LIMIT 8")
        return [r["text"] for r in rows]
    except Exception:
        return []


def _events_now(memory: Memory, now: datetime) -> list[dict]:
    try:
        from .connect import Moments  # noqa: PLC0415
        return Moments(memory, geocode=lambda q: None)._events(now.date())
    except Exception:
        return []


def activity_now(memory: Memory, now: datetime | None = None) -> dict:
    """What the owner is doing now: {"what": "in a meeting" | "at a doctor's appointment" | "driving" | "on the move" |
    "at home" | "at the office" | "at the gym" | "out (errands)" | "", "until": "6:15 pm" | "", "source": ...}."""
    now = (now or local_now()).replace(tzinfo=None)
    for e in _events_now(memory, now):
        end = e.get("end") or e["start"] + timedelta(hours=1)
        if e["start"] <= now <= end:
            what = "at a doctor's appointment" if APPOINTMENT.search(e.get("title", "")) else "in a meeting"
            return {"what": what, "until": _say(end.hour * 60 + end.minute), "source": "calendar"}
    r = week(memory)
    minute = now.hour * 60 + now.minute
    block = next((b for b in r.get(_day_type(now), []) if b["from"] <= minute < b["to"]), None)
    try:
        from .maps import Maps  # noqa: PLC0415
        s = Maps(memory).situation(now)
    except Exception:
        s = {}
    if s.get("driving"):
        return {"what": "driving", "until": "", "source": "location"}
    if s.get("moving_kmh"):
        train = "path" in json.dumps(_commute(memory)).lower() or "rail" in json.dumps(_commute(memory)).lower()
        return {"what": "on the train" if train else "on the move", "until": _until_next(r, now, "commuting"),
                "source": "location"}
    if s.get("place"):
        return {"what": _what(s["place"]), "until": "", "source": "location"}
    if block:
        return {"what": _what(block["what"]), "until": _say(block["to"]), "source": "routine"}
    return {"what": "", "until": "", "source": ""}


def _commute(memory: Memory) -> dict:
    try:
        from . import commute  # noqa: PLC0415
        return commute.routine(memory)
    except Exception:
        return {}


def _until_next(r: dict, now: datetime, what: str) -> str:
    minute = now.hour * 60 + now.minute
    for b in r.get(_day_type(now), []):
        if b["what"] == what and b["from"] <= minute < b["to"]:
            return _say(b["to"])
    return ""


def now_line(memory: Memory) -> str:
    a = activity_now(memory)
    if not a["what"]:
        return ""
    return a["what"] + (f" until about {a['until']}" if a["until"] else "")


TOOL = {
    "name": "my_routine",
    "description": "The user's daily routine as Buddy has learned it from their location history (weekdays, "
    "Saturdays, Sundays: home, commute, office, regular places), what they've told Buddy about it, and what they're "
    "doing right now (calendar, then location, then routine). show: all of it. relearn: learn again now. To correct "
    "or add to it, save what they say with remember_fact (their word beats the learned pattern); to name a regular "
    "spot (\"this is my gym\"), use the place-naming tool.",
    "input_schema": {"type": "object", "properties": {"action": {"type": "string", "enum": ["show", "relearn"]}},
                     "required": ["action"], "additionalProperties": False},
}


def run_tool(memory: Memory, args: dict) -> str:
    action = args.get("action") or "show"
    if action not in ("show", "relearn"):
        raise ToolError("Use show or relearn.")
    text = relearn(memory) if action == "relearn" else describe(week(memory))
    r = week(memory)
    lines = [f"Learned routine ({r.get('days', 0)} days of location, {r.get('learned_at', 'not yet')}): "
             + (text or "not enough location history yet (the app shares it; give it a week or two).")]
    if (said := told(memory)):
        lines.append("What they told you: " + "; ".join(said))
    if (now := now_line(memory)):
        lines.append(f"Right now: {now}.")
    return "\n".join(lines)
