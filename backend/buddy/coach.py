"""The owner's health plan: goals, a few daily habits, food targets, tracking and timely nudges.

Set up in conversation: Buddy asks about the owner's goals, current habits, food (preferences, allergies, what they
avoid), schedule and anything a doctor should weigh in on, proposes a small plan, and saves it only once the owner
agrees (tool health_plan, set_plan). Then:
  tracking   habits are checked automatically from the Oura Ring (oura.py) and the phone's Health Connect data
             (health_data.py): steps, sleep, bedtime, workouts, active minutes. Others (water, no sugar, stretching)
             the owner ticks off by telling Buddy. Meals the owner mentions are noted with Claude's estimate of their
             calories and protein, so "what should I eat?" knows what's left for the day.
  nudges     a morning line (how recovered they are and what that means for today), an evening one when a habit
             is behind ("2,800 steps to go: a 25-minute walk"), a wind-down reminder before the bedtime target, and
             a Sunday review of the week. Plain sentences from the numbers: no model call, nothing sent anywhere.
  guidance   readiness, HRV, temperature and sleep shape the day's advice (a low readiness: an easy day). It is
             coaching, not medical advice: signs worth a doctor's look are said as such.
Personal: kept on this server (90 days of logs), seen only by Claude. "Forget my health plan" deletes it all.
"""

import json
import re
from datetime import date, datetime, timedelta, timezone

from .memory import Memory
from .tools import ToolError, local_now

PLAN_KEY = "coach:plan"
KEEP_DAYS = 90
METRICS = {  # automatic tracking: how a day's value is read and what "done" means
    "steps": "steps that day (Oura, else the phone)",
    "sleep_hours": "hours asleep the night before (Oura, else the phone)",
    "bedtime": "lights out by HH:MM (Oura)",
    "workouts": "workouts a week (Oura or the phone)",
    "active_minutes": "active minutes that day (the phone)",
    "manual": "the owner says when it's done (water, stretching, no sugar...)",
}
SCHEMA = """
CREATE TABLE IF NOT EXISTS coach_log (           -- habits the owner ticked off (manual ones); 90 days
    day TEXT NOT NULL, habit TEXT NOT NULL, value REAL, note TEXT NOT NULL DEFAULT '', at TEXT NOT NULL,
    PRIMARY KEY (day, habit)
);
CREATE TABLE IF NOT EXISTS coach_meals (         -- meals the owner mentioned, with Claude's estimate; 90 days
    id INTEGER PRIMARY KEY, day TEXT NOT NULL, at TEXT NOT NULL, what TEXT NOT NULL,
    calories INTEGER, protein_g INTEGER
);
"""


def _ready(memory: Memory) -> None:
    memory.db.executescript(SCHEMA)


def plan(memory: Memory) -> dict:
    try:
        return json.loads(memory.get(PLAN_KEY) or "{}")
    except ValueError:
        return {}


def _num(v):
    try:
        return None if v in (None, "") else float(v)
    except (TypeError, ValueError):
        return None


def _hhmm(v) -> str:
    m = re.fullmatch(r"\s*(\d{1,2}):?(\d{2})\s*", str(v or ""))
    if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
        return ""
    return f"{int(m.group(1)):02d}:{m.group(2)}"


def set_plan(memory: Memory, args: dict) -> dict:
    """Save the plan the owner agreed to (replacing the old one; the tick-off history stays)."""
    goals = [" ".join(str(g).split())[:160] for g in args.get("goals") or [] if str(g).strip()][:6]
    habits = []
    for i, h in enumerate((args.get("habits") or [])[:8], 1):
        metric = h.get("metric") if h.get("metric") in METRICS else "manual"
        target = _hhmm(h.get("target")) if metric == "bedtime" else _num(h.get("target"))
        if metric != "manual" and not target:
            raise ToolError(f"Habit {h.get('text')!r} needs a target for {metric} ({METRICS[metric]}).")
        habits.append({"id": f"h{i}", "text": " ".join(str(h.get("text") or metric).split())[:120],
                       "metric": metric, "target": target,
                       "days": h.get("days") if h.get("days") in ("daily", "weekdays") else "daily"})
    if not goals or not habits:
        raise ToolError("A plan needs at least one goal and one habit.")
    diet = {k: v for k, v in (args.get("diet") or {}).items()
            if k in ("style", "calories", "protein_g", "avoid", "allergies", "notes") and v not in (None, "", [])}
    old = plan(memory)
    saved = {"goals": goals, "habits": habits, "diet": diet, "why": str(args.get("why") or "")[:300],
             "created_at": old.get("created_at") or local_now().date().isoformat(),
             "updated_at": local_now().date().isoformat(), "start": old.get("start") or _snapshot(memory)}
    memory.set(PLAN_KEY, json.dumps(saved))
    return saved


# --- the day's numbers ---------------------------------------------------------------------------------------
def _data(memory: Memory, n: int) -> dict[str, dict]:
    """day -> the merged numbers (Oura first, the phone's Health Connect for what Oura lacks)."""
    out: dict[str, dict] = {}
    from . import health_data, oura  # noqa: PLC0415
    try:
        for r in health_data.days(memory, n):
            out[r["day"]] = {"steps": r.get("steps"), "sleep_minutes": r.get("sleep_minutes"),
                             "active_minutes": r.get("active_minutes"), "resting_hr": r.get("resting_hr"),
                             "workouts": len(r.get("workouts") or [])}
    except Exception:
        pass
    try:
        for r in oura.days(memory, n):
            d = out.setdefault(r["day"], {})
            for k in ("readiness", "sleep_score", "activity_score", "hrv", "lowest_hr", "temp_deviation",
                      "bedtime", "wake", "deep_minutes", "rem_minutes", "stress_minutes", "resilience"):
                if r.get(k) is not None:
                    d[k] = r[k]
            for k in ("steps", "sleep_minutes"):
                if r.get(k):
                    d[k] = r[k]
            if r.get("workouts"):
                d["workouts"] = max(d.get("workouts") or 0, len(r["workouts"]))
    except Exception:
        pass
    return out


def _snapshot(memory: Memory) -> dict:
    """7-day averages when the plan starts, so progress can be shown against where they began."""
    rows = list(_data(memory, 7).values())

    def avg(k):
        vals = [r[k] for r in rows if isinstance(r.get(k), (int, float))]
        return round(sum(vals) / len(vals), 1) if vals else None
    return {k: avg(k) for k in ("steps", "sleep_minutes", "readiness", "hrv", "resting_hr", "lowest_hr")}


def _bed_ok(bedtime: str, target: str) -> bool:
    """Lights out at or before the target; past midnight counts as late for an evening target."""
    def mins(t):
        h, m = map(int, t.split(":"))
        return h * 60 + m + (24 * 60 if h < 12 else 0)  # 00:30 is later than 23:00
    return mins(bedtime) <= mins(target)


def _logged(memory: Memory, since: str) -> dict[tuple, dict]:
    _ready(memory)
    return {(r["day"], r["habit"]): dict(r) for r in memory.db.execute(
        "SELECT day, habit, value, note FROM coach_log WHERE day >= ?", (since,))}


def _due(habit: dict, day: date) -> bool:
    return habit.get("days") != "weekdays" or day.weekday() < 5


def habit_day(habit: dict, day: date, data: dict[str, dict], logged: dict) -> dict:
    """One habit on one day: its value, whether it's done (None: no data yet)."""
    d = data.get(day.isoformat()) or {}
    metric, target = habit["metric"], habit["target"]
    got = logged.get((day.isoformat(), habit["id"]))
    if metric == "manual":
        return {"value": got["value"] if got else None, "done": True if got else None}
    if got:  # the owner said so (their phone wasn't on them, say): that wins
        return {"value": got["value"], "done": True}
    if metric == "steps":
        v = d.get("steps")
        return {"value": v, "done": None if v is None else v >= target}
    if metric == "sleep_hours":
        v = round(d["sleep_minutes"] / 60, 1) if d.get("sleep_minutes") else None
        return {"value": v, "done": None if v is None else v >= target}
    if metric == "active_minutes":
        v = d.get("active_minutes")
        return {"value": v, "done": None if v is None else v >= target}
    if metric == "bedtime":
        v = d.get("bedtime") or ""  # Oura files the night under the day you wake up
        return {"value": v or None, "done": None if not v else _bed_ok(v, target)}
    if metric == "workouts":  # a weekly count: this week so far
        monday = day - timedelta(days=day.weekday())
        n = sum((data.get((monday + timedelta(days=i)).isoformat()) or {}).get("workouts") or 0
                for i in range((day - monday).days + 1))
        return {"value": n, "done": n >= target}
    return {"value": None, "done": None}


def today(memory: Memory, now: datetime | None = None) -> dict:
    p = plan(memory)
    now = now or local_now()
    data = _data(memory, 8)
    day = now.date()
    logged = _logged(memory, (day - timedelta(days=7)).isoformat())
    habits = [{**h, **habit_day(h, day, data, logged)} for h in p.get("habits", []) if _due(h, day)]
    return {"day": day.isoformat(), "habits": habits, "body": body(data, day), "eaten": eaten(memory, day),
            "diet": p.get("diet") or {}}


def body(data: dict[str, dict], day: date) -> dict:
    """How the owner's body is doing today, against their own week, with what it means for the day."""
    d = data.get(day.isoformat()) or data.get((day - timedelta(days=1)).isoformat()) or {}
    week = [v for k, v in data.items() if k < day.isoformat()]

    def avg(k):
        vals = [r[k] for r in week if isinstance(r.get(k), (int, float))]
        return sum(vals) / len(vals) if vals else None
    out = {k: d[k] for k in ("readiness", "sleep_score", "hrv", "lowest_hr", "temp_deviation", "sleep_minutes",
                             "resilience") if d.get(k) is not None}
    advice = []
    r, hrv, hrv_avg = d.get("readiness"), d.get("hrv"), avg("hrv")
    if r is not None:
        advice.append("well recovered: a good day to push a workout" if r >= 85 else
                      "recovered enough for normal training" if r >= 70 else
                      "low recovery: keep it easy today (a walk, mobility, an early night)")
    if hrv and hrv_avg and hrv < 0.8 * hrv_avg:
        advice.append(f"HRV {hrv} is well under your usual {round(hrv_avg)}: stress, alcohol, late food or a bug "
                      "can do that; go easy")
    if (d.get("temp_deviation") or 0) >= 0.5:
        advice.append("body temperature is up from your normal: rest, fluids, and watch for signs of illness")
    if d.get("sleep_minutes") and d["sleep_minutes"] < 6 * 60:
        advice.append("short sleep: protect tonight's bedtime, and skip caffeine after noon")
    out["advice"] = advice
    return out


# --- food -----------------------------------------------------------------------------------------------------
def add_meal(memory: Memory, what: str, calories=None, protein_g=None, now: datetime | None = None) -> dict:
    _ready(memory)
    now = now or local_now()
    what = " ".join(str(what or "").split())[:200]
    if not what:
        raise ToolError("Say what was eaten.")
    cal, pro = _num(calories), _num(protein_g)
    memory.db.execute("INSERT INTO coach_meals (day, at, what, calories, protein_g) VALUES (?, ?, ?, ?, ?)",
                      (now.date().isoformat(), now.strftime("%H:%M"), what, round(cal) if cal else None,
                       round(pro) if pro else None))
    memory.db.commit()
    return eaten(memory, now.date())


def eaten(memory: Memory, day: date) -> dict:
    _ready(memory)
    rows = [dict(r) for r in memory.db.execute(
        "SELECT at, what, calories, protein_g FROM coach_meals WHERE day = ? ORDER BY at", (day.isoformat(),))]
    return {"meals": rows, "calories": sum(r["calories"] or 0 for r in rows),
            "protein_g": sum(r["protein_g"] or 0 for r in rows)}


def food(memory: Memory, now: datetime | None = None) -> dict:
    """What's left for the day against the plan's targets, the meal coming up, and where the owner is (area only,
    for finding a place to eat nearby)."""
    now = now or local_now()
    p = plan(memory)
    diet = p.get("diet") or {}
    ate = eaten(memory, now.date())
    meal = "breakfast" if now.hour < 11 else "lunch" if now.hour < 15 else "a snack" if now.hour < 17 else "dinner"
    left = {}
    if _num(diet.get("calories")):
        left["calories"] = round(float(diet["calories"]) - ate["calories"])
    if _num(diet.get("protein_g")):
        left["protein_g"] = round(float(diet["protein_g"]) - ate["protein_g"])
    data = _data(memory, 2)
    d = data.get(now.date().isoformat()) or {}
    try:
        from .location import Location  # noqa: PLC0415
        area = Location(memory).label()
    except Exception:
        area = ""
    from . import oura  # noqa: PLC0415
    return {"next_meal": meal, "diet": diet, "eaten_today": ate, "left_today": left,
            "activity_today": {k: d[k] for k in ("steps", "workouts", "activity_score") if d.get(k) is not None},
            "body": oura.profile(memory), "area": area}


# --- progress -------------------------------------------------------------------------------------------------
def progress(memory: Memory, n: int = 7, now: datetime | None = None) -> dict:
    p = plan(memory)
    now = now or local_now()
    data = _data(memory, max(n, 7) + 7)
    logged = _logged(memory, (now.date() - timedelta(days=n + 7)).isoformat())
    out = []
    for h in p.get("habits", []):
        results = []  # newest first: True/False per counted day (or week, for workouts)
        if h["metric"] == "workouts":
            sunday = now.date() + timedelta(days=6 - now.date().weekday())
            for w in range(max(1, n // 7)):
                end = sunday - timedelta(days=7 * w)
                r = habit_day(h, min(end, now.date()), data, logged)
                if end > now.date() and not r["done"]:
                    continue  # this week isn't over
                results.append(bool(r["done"]))
        else:
            for i in range(n):
                day = now.date() - timedelta(days=i)
                r = habit_day(h, day, data, logged) if _due(h, day) else {"done": None}
                if r["done"] is None:
                    continue  # not due, or no data (ring off, today not over): neither done nor missed
                results.append(bool(r["done"]))
        streak = next((i for i, ok in enumerate(results) if not ok), len(results))
        out.append({"habit": h["text"], "done_days": sum(results), "of_days": len(results),
                    "rate": round(100 * sum(results) / len(results)) if results else None, "streak": streak,
                    "per": "week" if h["metric"] == "workouts" else "day"})
    rows = [data[k] for k in sorted(data) if k > (now.date() - timedelta(days=n)).isoformat()]

    def avg(k):
        vals = [r[k] for r in rows if isinstance(r.get(k), (int, float))]
        return round(sum(vals) / len(vals), 1) if vals else None
    return {"days": n, "goals": p.get("goals", []), "habits": out, "since_start": p.get("start") or {},
            "now": {k: avg(k) for k in ("steps", "sleep_minutes", "readiness", "hrv", "resting_hr", "lowest_hr")},
            "plan_since": p.get("created_at", "")}


# --- nudges ---------------------------------------------------------------------------------------------------
def _fmt_steps(n) -> str:
    return f"{int(n):,}"


def nudges(memory: Memory, now: datetime | None = None) -> list[dict]:
    """Messages due now (each said once: the key carries the day). [] without a plan."""
    p = plan(memory)
    if not p.get("habits"):
        return []
    now = now or local_now()
    day = now.date().isoformat()
    t = today(memory, now)
    out = []
    by_metric = {h["metric"]: h for h in t["habits"]}
    if 7 <= now.hour < 11 and t["body"].get("readiness") is not None:
        b = t["body"]
        bits = [f"Readiness {b['readiness']}"]
        if b.get("sleep_minutes"):
            bits.append(f"{b['sleep_minutes'] // 60}h{b['sleep_minutes'] % 60:02d} of sleep")
        line = ", ".join(bits) + ". " + (b["advice"][0][0].upper() + b["advice"][0][1:] + "." if b["advice"] else "")
        bed = by_metric.get("bedtime")
        if bed:
            line += f" Tonight: lights out by {bed['target']}."
        out.append({"key": f"coach:morning:{day}", "text": line.strip(), "urgent": False})
    steps = by_metric.get("steps")
    if steps and 18 <= now.hour < 21 and steps.get("value") is not None and not steps["done"]:
        gap = int(steps["target"] - steps["value"])
        if gap > 300:
            out.append({"key": f"coach:steps:{day}", "urgent": False,
                        "text": f"{_fmt_steps(steps['value'])} steps so far, {_fmt_steps(gap)} to your "
                                f"{_fmt_steps(steps['target'])}: about a {max(5, round(gap / 110 / 5) * 5)}-minute "
                                "walk closes it."})
    bed = by_metric.get("bedtime")
    if bed:
        h, m = map(int, bed["target"].split(":"))
        target = now.replace(hour=h, minute=m, second=0, microsecond=0)
        if h < 12:
            target += timedelta(days=1)
        if timedelta(0) < target - now <= timedelta(minutes=40):
            out.append({"key": f"coach:bed:{day}", "urgent": False,
                        "text": f"Wind-down time: screens away and lights low, for lights out by {bed['target']}."})
    manual = [h for h in t["habits"] if h["metric"] == "manual" and not h["done"]]
    if manual and 20 <= now.hour < 22:
        out.append({"key": f"coach:manual:{day}", "urgent": False,
                    "text": "Still open today: " + "; ".join(h["text"] for h in manual[:3])
                            + ". Tell me when it's done."})
    if now.weekday() == 6 and 18 <= now.hour < 21:
        pr = progress(memory, 7, now)
        rated = [h for h in pr["habits"] if h["rate"] is not None]
        if rated:
            best = max(rated, key=lambda h: h["rate"])
            worst = min(rated, key=lambda h: h["rate"])
            text = (f"Your week: {best['habit']} {best['done_days']} of {best['of_days']} {best['per']}s"
                    + (f"; {worst['habit']} {worst['done_days']} of {worst['of_days']} {worst['per']}s"
                       if worst is not best else "")
                    + ". Want to adjust next week's plan?")
            out.append({"key": f"coach:week:{day}", "text": text, "urgent": False})
    return out


def morning_line(memory: Memory) -> str:
    """For the morning briefing: recovery and today's focus, in a few words ('' without a plan or data)."""
    p = plan(memory)
    if not p.get("habits"):
        return ""
    t = today(memory)
    b = t["body"]
    bits = []
    if b.get("readiness") is not None:
        bits.append(f"readiness {b['readiness']}")
    if b.get("sleep_minutes"):
        bits.append(f"slept {round(b['sleep_minutes'] / 60, 1)} h")
    if b.get("advice"):
        bits.append(b["advice"][0])
    bits.append("today's habits: " + ", ".join(h["text"] for h in t["habits"][:4]))
    return "; ".join(bits)


def forget(memory: Memory) -> None:
    _ready(memory)
    memory.db.execute("DELETE FROM coach_log")
    memory.db.execute("DELETE FROM coach_meals")
    memory.db.commit()
    memory.set(PLAN_KEY, "")


def prune(memory: Memory) -> None:
    _ready(memory)
    cutoff = (local_now().date() - timedelta(days=KEEP_DAYS)).isoformat()
    memory.db.execute("DELETE FROM coach_log WHERE day < ?", (cutoff,))
    memory.db.execute("DELETE FROM coach_meals WHERE day < ?", (cutoff,))
    memory.db.commit()


def log_habit(memory: Memory, which: str, value=None, note: str = "", day: str = "") -> dict:
    """The owner did one (or tells about a day the data missed)."""
    p = plan(memory)
    which = str(which or "").strip().lower()
    habit = next((h for h in p.get("habits", []) if h["id"] == which), None) or next(
        (h for h in p.get("habits", []) if which and (which in h["text"].lower() or h["text"].lower() in which)), None)
    if not habit:
        raise ToolError("No habit like that in the plan: " + "; ".join(f"{h['id']} {h['text']}"
                                                                        for h in p.get("habits", [])))
    day = day if re.fullmatch(r"\d{4}-\d{2}-\d{2}", day or "") else local_now().date().isoformat()
    _ready(memory)
    memory.db.execute("INSERT OR REPLACE INTO coach_log (day, habit, value, note, at) VALUES (?, ?, ?, ?, ?)",
                      (day, habit["id"], _num(value), str(note or "")[:200], datetime.now(timezone.utc).isoformat()))
    memory.db.commit()
    return {"logged": habit["text"], "day": day}


# --- the tool -------------------------------------------------------------------------------------------------
TOOL = {
    "name": "health_plan",
    "description": (
        "The owner's health plan: goals, daily habits, food targets, tracking, progress. Data comes from their "
        "Oura Ring and phone automatically. HOW TO SET IT UP: ask (a few questions at a time) about their goals "
        "(lose fat, more energy, sleep better, run a 10K...), current routine (look at my_health and my_routine "
        "first), food (preferences, allergies, what they avoid, how they cook), time they have, and any condition, "
        "injury or medication. Then propose a small plan: 2-3 goals, 3-5 habits that start near where they are now "
        "(today's average + a step, not a leap), and food targets. Save it with set_plan ONLY after they agree. "
        "metric is one of steps (target: steps a day), sleep_hours (hours), bedtime (target 'HH:MM'), workouts "
        "(a week), active_minutes (a day) or manual (they tell you when done). Use today for 'how am I doing', "
        "progress for the week or month, log when they say they did a habit, meal when they mention eating (estimate "
        "calories and protein_g yourself), food before suggesting what to eat (it gives what's left for the day and "
        "their area; for places nearby, search in that area). Low readiness, low HRV or raised temperature mean an "
        "easier day. This is coaching, not medical advice: suggest a doctor for pain, symptoms, big weight changes "
        "or anything worrying; never advise on medication."),
    "input_schema": {"type": "object", "properties": {
        "action": {"type": "string", "enum": ["show", "set_plan", "today", "progress", "log", "meal", "food",
                                              "forget"]},
        "goals": {"type": "array", "items": {"type": "string"}},
        "why": {"type": "string", "description": "set_plan: what they want it for, in their words"},
        "habits": {"type": "array", "items": {"type": "object", "properties": {
            "text": {"type": "string"}, "metric": {"type": "string", "enum": list(METRICS)},
            "target": {"type": "string", "description": "a number, or HH:MM for bedtime"},
            "days": {"type": "string", "enum": ["daily", "weekdays"]}}, "required": ["text", "metric"]}},
        "diet": {"type": "object", "properties": {
            "style": {"type": "string"}, "calories": {"type": "number"}, "protein_g": {"type": "number"},
            "avoid": {"type": "array", "items": {"type": "string"}},
            "allergies": {"type": "array", "items": {"type": "string"}}, "notes": {"type": "string"}}},
        "habit": {"type": "string", "description": "log: the habit's id or words"},
        "value": {"type": "number"},
        "day": {"type": "string", "description": "log: YYYY-MM-DD, default today"},
        "what": {"type": "string", "description": "meal: what they ate"},
        "calories": {"type": "number"}, "protein_g": {"type": "number"},
        "days_back": {"type": "integer", "description": "progress: 7 or 30"},
    }, "required": ["action"], "additionalProperties": False},
}


def run_tool(memory: Memory, args: dict) -> str:
    action = args.get("action")
    if action == "set_plan":
        return json.dumps({"saved": set_plan(memory, args)})
    if action == "forget":
        forget(memory)
        return "The health plan, the habit log and the meals are deleted."
    p = plan(memory)
    if not p.get("habits") and action != "food":
        from . import oura  # noqa: PLC0415
        return json.dumps({"plan": None, "note": "No health plan yet: ask about their goals and set one up.",
                           "oura_linked": oura.linked(memory), "recent": _snapshot(memory)})
    if action == "show":
        return json.dumps({"plan": p, "metrics": METRICS})
    if action == "today":
        return json.dumps(today(memory))
    if action == "progress":
        return json.dumps(progress(memory, 30 if int(args.get("days_back") or 7) > 7 else 7))
    if action == "log":
        return json.dumps(log_habit(memory, args.get("habit", ""), args.get("value"), day=args.get("day", "")))
    if action == "meal":
        return json.dumps({"eaten_today": add_meal(memory, args.get("what", ""), args.get("calories"),
                                                   args.get("protein_g")), "plan_diet": p.get("diet") or {}})
    if action == "food":
        return json.dumps(food(memory))
    raise ToolError("Use show, set_plan, today, progress, log, meal, food or forget.")
