"""The Health page (web/health.html): the owner's own numbers, shown to the owner, worked out by rules.

Since the Privacy Gateway, no AI model is given health information, so Buddy no longer talks about it. This page
is where the owner sees it instead, and where the health plan is set up and changed (a form, in place of the
conversation that used to do it).

  view     today's readings with what they mean for the day (coach.body: fixed rules), each measure over the
           last weeks against the owner's own usual (the same bands healthranges uses), the plan's habits today,
           how each habit went over 7 and 30 days, and food against the plan's targets.
  act      tick a habit off, note a meal (the owner types the calories and protein if they want them counted:
           nothing estimates them), save or forget the plan, read the ring now.

Nothing here calls a model or leaves this server, apart from reading the ring from Oura when the owner presses
"Read the ring now". It is not medical advice.
"""

from datetime import datetime, timedelta
from statistics import median, pstdev

from . import coach, health_data, healthranges, oura
from .memory import Memory
from .tools import ToolError, local_now

LOOK_BACK, MIN_DAYS, MAX_DAYS = 60, 14, 90
# field -> (name on the page, unit, which way is better: "up", "down", or "" when neither is)
MEASURES = {
    "sleep_minutes": ("Sleep", "h", "up"), "sleep_score": ("Sleep score", "", "up"),
    "readiness": ("Readiness", "", "up"), "hrv": ("Heart rate variability", "ms", "up"),
    "lowest_hr": ("Lowest night heart rate", "bpm", "down"), "resting_hr": ("Resting heart rate", "bpm", "down"),
    "temp_deviation": ("Body temperature against normal", "°C", ""), "steps": ("Steps", "", "up"),
    "active_minutes": ("Active minutes", "min", "up"), "activity_score": ("Activity score", "", "up"),
    "deep_minutes": ("Deep sleep", "min", "up"), "rem_minutes": ("REM sleep", "min", "up"),
    "stress_minutes": ("Time under stress", "min", "down"),
}
NOTE = ("Worked out by fixed rules on this server from your ring and phone. No AI model is given any of it. "
        "It is not medical advice.")


def _value(field: str, v):
    if not isinstance(v, (int, float)) or isinstance(v, bool):
        return None
    return round(v / 60, 1) if field == "sleep_minutes" else round(v, 1) if field == "temp_deviation" else round(v)


def measures(data: dict[str, dict], days: int) -> list[dict]:
    """Each measure with readings: the days to draw, the latest, the week's average, the owner's usual and where
    the latest and the week stand against it."""
    order = sorted(data)
    out = []
    for field, (label, unit, better) in MEASURES.items():
        series = [(d, _value(field, data[d].get(field))) for d in order]
        seen = [(d, v) for d, v in series if v is not None]
        if not seen:
            continue
        week = [v for _, v in series[-7:] if v is not None]
        before = [v for _, v in series[:-7] if v is not None]
        usual = spread = None
        if len(before) >= healthranges.MIN_BASELINE:
            usual, spread = median(before), pstdev(before)
        band = (lambda v: healthranges._band(v, usual, spread)) if usual is not None else (lambda v: "")
        out.append({"key": field, "label": label, "unit": unit, "better": better,
                    "latest": seen[-1][1], "latest_day": seen[-1][0],
                    "week": round(sum(week) / len(week), 1) if week else None,
                    "usual": round(usual, 1) if usual is not None else None,
                    "latest_band": band(seen[-1][1]), "week_band": band(sum(week) / len(week)) if week else "",
                    "days_read": len(seen), "series": [{"d": d, "v": v} for d, v in series[-days:]]})
    return out


def view(memory: Memory, days: int = 30, now: datetime | None = None, env=None) -> dict:
    now = now or local_now()
    days = max(MIN_DAYS, min(int(days or 30), MAX_DAYS))
    data = coach._data(memory, max(LOOK_BACK, days + 7))
    plan = coach.plan(memory)
    today = coach.today(memory, now)
    recent = [dict(r) for r in memory.db.execute(
        "SELECT day, at, what, calories, protein_g FROM coach_meals WHERE day >= ? ORDER BY day DESC, at DESC LIMIT 30",
        ((now.date() - timedelta(days=6)).isoformat(),))]
    diet = plan.get("diet") or {}
    eaten = today["eaten"]

    def left(key):
        target = coach._num(diet.get(key))
        return None if target is None else round(target - (eaten.get(key) or 0))
    return {
        "day": now.date().isoformat(), "days": days, "has_data": bool(data),
        "sources": {"ring": {"connected": oura.linked(memory, env), "read": memory.get(oura.SYNC_KEY) or ""},
                    "phone": {"read": memory.get(health_data.SYNC_KEY) or ""}},
        "today": {"readings": {k: v for k, v in today["body"].items() if k != "advice"},
                  "means": today["body"].get("advice") or [], "habits": today["habits"]},
        "measures": measures(data, days),
        "plan": plan or None, "metrics": coach.METRICS,
        "progress": {"week": coach.progress(memory, 7, now)["habits"], "month": coach.progress(memory, 30, now)["habits"],
                     "start": plan.get("start") or {}, "since": plan.get("created_at", "")} if plan else None,
        "food": {"eaten": eaten, "targets": {k: diet.get(k) for k in ("calories", "protein_g") if diet.get(k)},
                 "left": {"calories": left("calories"), "protein_g": left("protein_g")}, "recent": recent,
                 "note": "Calories and protein are counted only when you type them or add a food you looked up "
                         "(USDA's numbers for the amount you pick): no AI estimates them."},
        "privacy": {"ranges": healthranges.on(env),
                    "note": "Ranges are on: Buddy may be told where a measure stands against your usual (never the "
                            "numbers) so it can talk about it." if healthranges.on(env) else
                            "No AI model is given your health information, so Buddy does not discuss it in "
                            "conversation. This page is where you see it."},
        "note": NOTE,
    }


def act(memory: Memory, body: dict, env=None, fetch=None) -> dict:
    """One thing the owner did on the page. Raises ToolError with a plain sentence when it cannot be done."""
    action = str(body.get("action") or "")
    if action == "habit":
        return {"ok": True, **coach.log_habit(memory, body.get("habit"), body.get("value"), body.get("note") or "",
                                              str(body.get("day") or ""))}
    if action == "meal":
        got = coach.add_meal(memory, body.get("what"), body.get("calories"), body.get("protein_g"))
        return {"ok": True, "calories": got["calories"], "protein_g": got["protein_g"]}
    if action == "plan":
        saved = coach.set_plan(memory, body.get("plan") or {})
        return {"ok": True, "habits": len(saved["habits"])}
    if action == "forget":
        coach.forget(memory)
        return {"ok": True}
    if action == "read":
        if not oura.linked(memory, env):
            raise ToolError(oura.NOT_LINKED)
        return {"ok": True, "days": oura.sync(memory, env=env, fetch=fetch)}
    raise ToolError("Unknown action.")
