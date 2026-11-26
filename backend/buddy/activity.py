"""The week's activity targets, from the Oura Ring first (and the phone's Health Connect for what it lacks), and what
is still missing, said in the morning briefing, the evening check-in and on the Health page (never a notification).

The targets (the owner can change them):
- cardio: 150 minutes a week of moderate activity, a vigorous minute counted as two (Physical Activity Guidelines
  for Americans, 2nd edition: 150 to 300 moderate, or 75 to 150 vigorous). Oura's medium-activity time is moderate
  (3 to 6 METs) and its high-activity time vigorous (6 METs or more), the guideline's own split; without the ring
  that day, the phone's active minutes count as moderate;
- strength: 2 sessions a week (the guideline: muscle-strengthening on 2 or more days), from workouts the ring or the
  phone recorded as strength training, or the owner's own "I did strength today";
- steps: 8,000 a day; sleep: 7 hours a night (adults 7 or more).
Code only: no AI model sees any of it; the briefing's line is added by code after the model's part.
"""

from __future__ import annotations

import json
import math
import re
from datetime import date, timedelta

from .memory import Memory
from .tools import ToolError, local_now

TARGETS_KEY = "activity:targets"
DEFAULTS = {"cardio": 150, "strength": 2, "steps": 8000, "sleep_h": 7.0}
LIMITS = {"cardio": (30, 600), "strength": (0, 7), "steps": (1000, 30000), "sleep_h": (5.0, 10.0)}
STRENGTH = re.compile(r"strength|weight|resistance|lifting|functional|crossfit|kettlebell|calisthenics|bodyweight",
                      re.I)
SCHEMA = """
CREATE TABLE IF NOT EXISTS strength_log (         -- strength sessions the owner noted (the ring may miss them)
    day TEXT PRIMARY KEY, at TEXT NOT NULL
);
"""
GUIDE = "Physical Activity Guidelines for Americans, 2nd edition"


def targets(memory: Memory) -> dict:
    try:
        got = json.loads(memory.get(TARGETS_KEY) or "{}")
    except ValueError:
        got = {}
    out = dict(DEFAULTS)
    for k, (low, high) in LIMITS.items():
        v = got.get(k) if isinstance(got, dict) else None
        if isinstance(v, (int, float)) and not isinstance(v, bool) and low <= v <= high:
            out[k] = v
    return out


def set_targets(memory: Memory, values: dict) -> dict:
    out = targets(memory)
    for k, (low, high) in LIMITS.items():
        if values.get(k) in (None, ""):
            continue
        try:
            v = float(values[k])
        except (TypeError, ValueError):
            raise ToolError(f"Type a number for {k}.") from None
        if not low <= v <= high:
            raise ToolError(f"{k}: between {low:g} and {high:g}.")
        out[k] = v if k == "sleep_h" else int(v)
    memory.set(TARGETS_KEY, json.dumps(out))
    return out


def log_strength(memory: Memory, day: str | None = None) -> None:
    memory.db.executescript(SCHEMA)
    memory.db.execute("INSERT OR IGNORE INTO strength_log (day, at) VALUES (?, ?)",
                      (day or local_now().date().isoformat(), local_now().isoformat(timespec="minutes")))
    memory.db.commit()


def unlog_strength(memory: Memory, day: str | None = None) -> None:
    memory.db.executescript(SCHEMA)
    memory.db.execute("DELETE FROM strength_log WHERE day = ?", (day or local_now().date().isoformat(),))
    memory.db.commit()


def days(memory: Memory, n: int = 14) -> dict[str, dict]:
    """day -> moderate and vigorous minutes, cardio (vigorous double), strength sessions, steps, sleep hours."""
    from . import coach, health_data, oura  # noqa: PLC0415
    memory.db.executescript(SCHEMA)
    out: dict[str, dict] = {}
    merged = coach._data(memory, n)
    for day, d in merged.items():
        out[day] = {"steps": d.get("steps"), "sleep_h": round(d["sleep_minutes"] / 60, 1) if d.get("sleep_minutes")
                    else None, "moderate": None, "vigorous": None, "strength": 0, "readiness": d.get("readiness"),
                    "hrv": d.get("hrv")}
    try:
        for r in health_data.days(memory, n):
            o = out.setdefault(r["day"], {"steps": r.get("steps"), "sleep_h": None, "strength": 0})
            if r.get("active_minutes") is not None:
                o["moderate"], o["vigorous"], o["from"] = r["active_minutes"], 0, "the phone's active minutes"
            o["strength"] = max(o.get("strength") or 0, sum(1 for w in r.get("workouts") or []
                                                            if STRENGTH.search(str(w.get("type") or ""))))
    except Exception:  # noqa: BLE001  no phone data
        pass
    try:
        for r in oura.days(memory, n):
            o = out.setdefault(r["day"], {"steps": r.get("steps"), "sleep_h": None, "strength": 0})
            if r.get("moderate_minutes") is not None or r.get("vigorous_minutes") is not None:
                o["moderate"], o["vigorous"] = r.get("moderate_minutes") or 0, r.get("vigorous_minutes") or 0
                o["from"] = "the Oura Ring"
            o["strength"] = max(o.get("strength") or 0, sum(1 for w in r.get("workouts") or []
                                                            if STRENGTH.search(str(w.get("type") or ""))))
    except Exception:  # noqa: BLE001  no ring
        pass
    since = (local_now().date() - timedelta(days=n)).isoformat()
    for r in memory.db.execute("SELECT day FROM strength_log WHERE day >= ?", (since,)):
        o = out.setdefault(r["day"], {"steps": None, "sleep_h": None, "strength": 0})
        o["strength"], o["noted"] = max(o.get("strength") or 0, 1), True
    for o in out.values():
        o.setdefault("moderate", None)
        o.setdefault("vigorous", None)
        o["cardio"] = None if o["moderate"] is None else (o["moderate"] or 0) + 2 * (o["vigorous"] or 0)
    return out


def week(memory: Memory, today: date | None = None) -> dict:
    """This week (Monday on) against the targets: what's done, what's left, and how to catch up."""
    today = today or local_now().date()
    t = targets(memory)
    monday = today - timedelta(days=today.weekday())
    data = days(memory, 14)
    span = [(monday + timedelta(days=i)).isoformat() for i in range(today.weekday() + 1)]
    left_days = 7 - today.weekday()  # today included
    this = [data.get(d) or {} for d in span]
    past = this[:-1]  # the days already over
    out: dict = {"targets": t, "from": monday.isoformat(), "days_left": left_days, "items": [], "source": GUIDE}
    has_any = any(x.get("cardio") is not None or x.get("steps") for x in this)
    out["has_data"] = has_any
    # cardio
    cardio = sum(x.get("cardio") or 0 for x in this)
    vig = sum(x.get("vigorous") or 0 for x in this)
    need = max(0, t["cardio"] - cardio)
    item = {"key": "cardio", "label": "Cardio", "done": cardio, "goal": t["cardio"], "unit": "minutes",
            "detail": f"{vig} vigorous, counted double" if vig else "moderate minutes; vigorous ones count double"}
    if need == 0:
        item |= {"status": "met", "say": f"Cardio done for the week: {cardio} of {t['cardio']} minutes."}
    else:
        per_day = math.ceil(need / left_days)
        behind = cardio < t["cardio"] * today.weekday() / 7 - 10
        item |= {"status": "behind" if behind else "on_track",
                 "say": f"{cardio} of {t['cardio']} cardio minutes this week; about {per_day} minutes of brisk walking "
                        f"a day for the next {left_days} day{'s' if left_days != 1 else ''} gets you there (or "
                        f"{math.ceil(per_day / 2)} minutes running or fast cycling)."}
    out["items"].append(item)
    # strength
    done = sum(min(x.get("strength") or 0, 1) for x in this)
    need = max(0, int(t["strength"]) - done)
    item = {"key": "strength", "label": "Strength", "done": done, "goal": int(t["strength"]), "unit": "sessions",
            "noted_today": bool((data.get(today.isoformat()) or {}).get("noted"))}
    if need == 0:
        item |= {"status": "met", "say": f"Strength done: {done} of {int(t['strength'])} sessions."}
    else:
        item |= {"status": "behind" if need > left_days - 1 else "on_track",
                 "say": f"Strength {done} of {int(t['strength'])} this week: {need} more session{'s' if need != 1 else ''} "
                        f"in the next {left_days} day{'s' if left_days != 1 else ''}, with a rest day between."}
    out["items"].append(item)
    # steps
    today_steps = (data.get(today.isoformat()) or {}).get("steps")
    met_days = sum(1 for x in past if (x.get("steps") or 0) >= t["steps"])
    counted = sum(1 for x in past if x.get("steps"))
    yesterday = (data.get((today - timedelta(days=1)).isoformat()) or {}).get("steps")
    item = {"key": "steps", "label": "Steps", "done": today_steps or 0, "goal": t["steps"], "unit": "steps today",
            "met_days": met_days, "counted_days": counted, "yesterday": yesterday}
    item["status"] = "met" if (today_steps or 0) >= t["steps"] else "open"
    item["say"] = (f"{t['steps'] - (today_steps or 0):,} steps to go today" if item["status"] == "open" else
                   f"Steps done today: {today_steps:,}") + (
        f"; {met_days} of {counted} days this week at {t['steps']:,} or more." if counted else ".")
    out["items"].append(item)
    # sleep
    nights = [x.get("sleep_h") for x in this if x.get("sleep_h")]
    good = sum(1 for h in nights if h >= t["sleep_h"])
    last = (data.get(today.isoformat()) or {}).get("sleep_h")
    item = {"key": "sleep", "label": "Sleep", "done": good, "goal": len(nights), "unit": "nights",
            "last_night": last, "target_h": t["sleep_h"],
            "status": "met" if nights and good == len(nights) else "open" if nights else "no_data",
            "say": (f"Last night {last} hours; " if last else "") + (
                f"{good} of {len(nights)} nights this week at {t['sleep_h']:g} hours or more." if nights else
                "No sleep readings this week.")}
    out["items"].append(item)
    # an easy day?
    d = data.get(today.isoformat()) or {}
    hrvs = [x.get("hrv") for k, x in data.items() if k < today.isoformat() and x.get("hrv")]
    usual = sum(hrvs) / len(hrvs) if hrvs else None
    if (d.get("readiness") is not None and d["readiness"] < 70) or (d.get("hrv") and usual and d["hrv"] < 0.8 * usual):
        out["easy"] = ("Go easy today (readiness or HRV is low): an easy walk counts toward cardio; save intervals "
                       "and heavy lifting for a better day.")
    out["note"] = ("From your ring and phone, worked out by Buddy's code against the " + GUIDE + ". No AI model "
                   "sees it.")
    return out


def morning_line(memory: Memory) -> str:
    """For the morning briefing (added by code, after the model's part): what's missing this week, in one breath."""
    try:
        w = week(memory)
    except Exception:  # noqa: BLE001
        return ""
    if not w["has_data"]:
        return ""
    open_items = [i for i in w["items"] if i["status"] in ("behind", "on_track") and i["key"] in ("cardio", "strength")]
    bits = [i["say"] for i in open_items]
    steps = next(i for i in w["items"] if i["key"] == "steps")
    if steps.get("yesterday") is not None and steps["yesterday"] < w["targets"]["steps"]:
        bits.append(f"Yesterday {steps['yesterday']:,} steps, under your {w['targets']['steps']:,}.")
    sleep = next(i for i in w["items"] if i["key"] == "sleep")
    if sleep.get("last_night") and sleep["last_night"] < w["targets"]["sleep_h"]:
        bits.append(f"You slept {sleep['last_night']} hours, under your {w['targets']['sleep_h']:g}.")
    if w.get("easy"):
        bits.append(w["easy"])
    if not bits:
        return "This week's activity targets are on track."
    return "Your week so far: " + " ".join(bits)


def evening_line(memory: Memory) -> str:
    """For the evening check-in: what's still open today, if anything."""
    try:
        w = week(memory)
    except Exception:  # noqa: BLE001
        return ""
    if not w["has_data"]:
        return ""
    bits = []
    steps = next(i for i in w["items"] if i["key"] == "steps")
    if steps["status"] == "open" and steps["done"]:
        bits.append(f"{steps['goal'] - steps['done']:,} steps to go today")
    cardio = next(i for i in w["items"] if i["key"] == "cardio")
    if cardio["status"] == "behind":
        bits.append(f"{cardio['goal'] - cardio['done']} cardio minutes left this week")
    strength = next(i for i in w["items"] if i["key"] == "strength")
    if strength["status"] == "behind":
        bits.append(f"{strength['goal'] - strength['done']} strength session{'s' if strength['goal'] - strength['done'] != 1 else ''} left this week")
    return ("Still open: " + "; ".join(bits) + ".") if bits else ""


def daily(memory: Memory, n: int = 730) -> dict[str, dict]:
    """For Buddy's own model: each day's cardio minutes and strength session."""
    return {d: {"cardio_minutes": x["cardio"], "strength": min(x.get("strength") or 0, 1)}
            for d, x in days(memory, n).items() if x.get("cardio") is not None or x.get("strength")}
