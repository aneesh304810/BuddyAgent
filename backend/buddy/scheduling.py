"""Scheduling: the owner's free time, meeting proposals from calls Buddy answered, and adding events to the phone's
calendar after the owner's yes.

Free time: weekdays within working hours (BUDDY_MEETING_HOURS, default 09:00-18:00), the next 7 days, minus calendar
events and the learned commute, from 30 minutes from now. On a call Buddy answers, a caller who wants to meet is
offered two of these times; the one they pick is only pencilled in ("I'll confirm with Aneesh"). The owner is told
and asked; on their yes (thrift.ask_action), the event goes into the phone's calendar (the app writes it, with the
Calendar permission). Buddy never commits the owner's time by itself.
"""

import json
import re
import secrets
from datetime import datetime, timedelta

from .memory import Memory
from .tools import ToolError, local_now

KEY = "calls:proposals"
KEEP_DAYS = 14


def _hours(env=None) -> tuple[int, int]:
    import os  # noqa: PLC0415
    raw = ((env if env is not None else os.environ).get("BUDDY_MEETING_HOURS") or "09:00-18:00").strip()
    m = re.fullmatch(r"(\d{1,2}):(\d\d)\s*-\s*(\d{1,2}):(\d\d)", raw)
    if not m:
        return 9 * 60, 18 * 60
    a, b = int(m[1]) * 60 + int(m[2]), int(m[3]) * 60 + int(m[4])
    return (a, b) if a < b else (9 * 60, 18 * 60)


def _busy(memory: Memory, day) -> list[tuple[datetime, datetime]]:
    out = []
    try:
        from .connect import Moments  # noqa: PLC0415
        for e in Moments(memory, geocode=lambda q: None)._events(day):
            out.append((e["start"], e.get("end") or e["start"] + timedelta(hours=1)))
    except Exception:
        pass
    try:
        from . import routine  # noqa: PLC0415
        kind = routine._day_type(day)
        for b in routine.week(memory).get(kind, []):
            if b["what"] == "commuting":
                base = datetime(day.year, day.month, day.day)
                out.append((base + timedelta(minutes=b["from"]), base + timedelta(minutes=b["to"])))
    except Exception:
        pass
    return out


def _round_up(t: datetime) -> datetime:
    """To the next :00 or :30."""
    if t.minute in (0, 30):
        return t
    return t.replace(minute=30) if t.minute < 30 else t.replace(minute=0) + timedelta(hours=1)


def free_slots(memory: Memory, days: int = 7, minutes: int = 30, now: datetime | None = None, env=None,
               limit: int = 8) -> list[tuple[datetime, datetime]]:
    """Free windows (at least `minutes` long) in working hours over the next `days` weekdays."""
    now = (now or local_now()).replace(tzinfo=None, second=0, microsecond=0)
    start_min, end_min = _hours(env)
    out = []
    for d in range(days + 1):
        day = (now + timedelta(days=d)).date()
        if day.weekday() >= 5:
            continue
        base = datetime(day.year, day.month, day.day)
        cursor = _round_up(max(base + timedelta(minutes=start_min), now + timedelta(minutes=30)))
        end = base + timedelta(minutes=end_min)
        for b0, b1 in sorted(_busy(memory, day)) + [(end, end)]:
            stop = min(b0, end)
            if stop - cursor >= timedelta(minutes=minutes):
                out.append((cursor, stop))
            cursor = max(cursor, b1)
            if cursor >= end:
                break
        if len(out) >= limit:
            break
    return out[:limit]


def _t(t: datetime) -> str:
    return t.strftime("%I:%M %p").lstrip("0").replace(":00 ", " ").lower()


def describe(slots: list[tuple[datetime, datetime]]) -> str:
    return "; ".join(f"{a.strftime('%a %b')} {a.day}: {_t(a)} to {_t(b)}" for a, b in slots) or "no free time found"


def clean_meeting(raw) -> dict | None:
    """A meeting the caller agreed to, from the call's reply: {"title", "start": "YYYY-MM-DD HH:MM", "minutes"}."""
    if not isinstance(raw, dict):
        return None
    try:
        start = datetime.strptime(str(raw.get("start") or "").strip()[:16].replace("T", " "), "%Y-%m-%d %H:%M")
    except ValueError:
        return None
    minutes = int(raw.get("minutes") or 30) if str(raw.get("minutes") or "30").isdigit() else 30
    return {"title": " ".join(str(raw.get("title") or "Meeting").split())[:80], "start": start.isoformat(),
            "minutes": max(10, min(minutes, 240))}


def _all(memory: Memory) -> list[dict]:
    try:
        return json.loads(memory.get(KEY) or "[]")
    except ValueError:
        return []


def propose(memory: Memory, meeting: dict, who: str, number: str) -> dict:
    cutoff = (local_now().replace(tzinfo=None) - timedelta(days=KEEP_DAYS)).isoformat()
    items = [p for p in _all(memory) if p["start"] >= cutoff]
    p = {**meeting, "id": secrets.token_hex(3), "who": who[:80], "number": number, "status": "pending"}
    items.append(p)
    memory.set(KEY, json.dumps(items[-30:]))
    return p


def pending(memory: Memory) -> list[dict]:
    return [p for p in _all(memory) if p["status"] == "pending"]


def mark(memory: Memory, pid: str, status: str) -> None:
    items = _all(memory)
    for p in items:
        if p["id"] == pid:
            p["status"] = status
    memory.set(KEY, json.dumps(items))


def when(p: dict) -> str:
    a = datetime.fromisoformat(p["start"])
    b = a + timedelta(minutes=p["minutes"])
    return f"{a.strftime('%A %b')} {a.day}, {_t(a)} to {_t(b)}"


def proposal_message(p: dict) -> str:
    return (f"📅 {p['who']} would like {when(p)} ({p['title']}). I told them I'd confirm with you. Add it to your "
            f"calendar? Say \"add it to my calendar\".")


TOOL = {
    "name": "calendar_add",
    "description": "Add an event to the user's phone calendar, only after their yes (asked automatically). "
    "proposal: the latest meeting a caller asked for on a call Buddy answered (or `id`), e.g. when the user says "
    "\"add it to my calendar\". event: a new event (title, start 'YYYY-MM-DD HH:MM' in their local time, minutes, "
    "notes). free: the user's free times in working hours over the next week (to plan or suggest times). decline: "
    "drop a pending proposal.",
    "input_schema": {"type": "object", "properties": {
        "action": {"type": "string", "enum": ["proposal", "event", "free", "decline"]},
        "id": {"type": "string"}, "title": {"type": "string"}, "start": {"type": "string"},
        "minutes": {"type": "integer"}, "notes": {"type": "string"}},
        "required": ["action"], "additionalProperties": False},
}


async def run_tool(memory: Memory, thrift, phone_request, args: dict) -> str:
    action = args.get("action")
    if action == "free":
        return "Free (working hours, next week): " + describe(free_slots(memory, minutes=int(args.get("minutes") or 30)))
    if action in ("proposal", "decline"):
        items = pending(memory)
        p = next((x for x in items if x["id"] == args.get("id")), items[-1] if items else None)
        if p is None:
            return "No meeting requests waiting."
        if action == "decline":
            mark(memory, p["id"], "declined")
            return f"Dropped {p['who']}'s request for {when(p)}. Want me to call them back to let them know?"
        event = {"title": f"{p['title']} ({p['who']})", "start": p["start"], "minutes": p["minutes"],
                 "notes": f"Set up by Buddy from {p['who']}'s call ({p['number']})."}
    elif action == "event":
        m = clean_meeting({"title": args.get("title"), "start": args.get("start"), "minutes": args.get("minutes")})
        if m is None:
            raise ToolError("Give the start as YYYY-MM-DD HH:MM (local time).")
        p, event = None, {**m, "notes": str(args.get("notes") or "")[:500]}
    else:
        raise ToolError("Use proposal, event, free or decline.")
    if thrift is None or phone_request is None:
        return "I need your yes and the phone app connected to add it; open the Buddy app and ask again."

    async def go() -> str:
        start = datetime.fromisoformat(event["start"])
        tz = local_now().tzinfo
        start_ms = int(start.replace(tzinfo=tz).timestamp() * 1000)
        try:
            got = await phone_request({"type": "calendar_add", "title": event["title"], "start_ms": start_ms,
                                       "end_ms": start_ms + event["minutes"] * 60_000, "notes": event["notes"]})
        except Exception:
            return "The phone didn't answer (open the Buddy app), so nothing was added."
        if not got.get("ok"):
            why = got.get("error") or "unknown"
            return ("Allow Buddy to change your calendar (Android settings > Apps > Buddy > Permissions > Calendar), "
                    "then ask me again." if why == "permission" else f"The phone couldn't add it ({why}).")
        if p:
            mark(memory, p["id"], "added")
        return f"Added to your calendar: {event['title']}, {when(event)}."
    return thrift.ask_action(f"Add \"{event['title']}\" on {when(event)} to your phone calendar?", go)
