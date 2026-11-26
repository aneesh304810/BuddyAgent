"""A scanned food into the day's meals, by time and place, and whether the day is on track. Code only.

  where(memory)            -> {do: log | verdict | ask, place, why, slot}
  eat(memory, scan_id, n)  -> the meal noted (n label servings) and the day's check
  ontrack(memory)          -> today's fat, saturated fat, sodium, added sugar and ultra-processed foods against the
                              owner's targets, what to change, and a lighter next meal from the day's menus

Eating or shopping: the phone's last shared position (location.py; the app shares it as a scan starts) is compared,
on the server, with home and work (learned by maps.py) and the places the owner marked once: a store (scans there
are shopping: the verdict only) or a place they eat (scans there are meals). Anywhere else, or with no position from
the last 20 minutes, the page asks. The comparison sends the position nowhere (sharing a location names its street
through OpenStreetMap, as location.py always has).

The day's check (dietplan.targets for the numbers):
- saturated fat: at most 10% of calories (Dietary Guidelines 2025-2030)
- fat: at most 35% of calories (the National Academies' range for fat, 20 to 35%)
- sodium: under 2,300 mg (Dietary Guidelines 2025-2030)
- added sugar: at most 10 g in a meal (Dietary Guidelines 2025-2030)
- ultra-processed (NOVA 4) foods scanned and eaten today, two or more
Nine tenths of a limit is "close". Through the day a limit is "ahead of pace" when what's eaten passes the share
of the day's food the meals so far usually carry (dietplan.SHARE: breakfast a quarter, lunch to three fifths, the
snack to seven tenths, then dinner), with a little room. Only meals that came with the number count, and the page
says how many did.
No AI model is given any of it (health information, privacygate.py).
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from .memory import Memory
from .tools import ToolError, local_now

SCHEMA = """
CREATE TABLE IF NOT EXISTS food_places (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    kind TEXT NOT NULL,                -- store (scans are shopping) | eat (scans are meals)
    lat REAL NOT NULL, lng REAL NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS scan_meals (
    meal_id INTEGER PRIMARY KEY,       -- coach_meals.id
    scan_id INTEGER NOT NULL,          -- food_scans.id
    day TEXT NOT NULL,
    servings REAL NOT NULL,
    what TEXT NOT NULL,                -- the meal's words, so a reused meal id never counts as a scan
    nova INTEGER
);
"""
KINDS = {"store": "a store: scans there are shopping", "eat": "a place you eat: scans there are meals"}
NEAR_M = 150          # within this of a marked place (a big store fits)
FRESH_MIN = 20        # a shared position older than this doesn't say where the owner is now
PACE = {"breakfast": 0.25, "lunch": 0.60, "snack": 0.70, "dinner": 1.0}  # dietplan.SHARE, added up through the day
ROOM = 0.10
CLOSE = 0.90          # nine tenths of a limit: say so before it's passed
MEAL_SUGAR_G = 10.0
NEXT = {"breakfast": "lunch", "lunch": "dinner", "snack": "dinner", "dinner": ""}
LETTER = {"breakfast": "b", "lunch": "l", "snack": "s", "dinner": "d"}
WATCH = (  # key, words, unit
    ("sat_fat_g", "Saturated fat", "g"), ("fat_g", "Fat", "g"), ("sodium_mg", "Sodium", "mg"),
)
NOTE = "Worked out by Buddy's code from your plan, your labs and the labels. Not medical advice. No AI model sees it."


def _ready(memory: Memory) -> None:
    memory.db.executescript(SCHEMA)


# --- where the owner is ----------------------------------------------------------------------------------------
def _here(memory: Memory, now_utc: datetime | None = None) -> tuple[dict | None, str]:
    from .location import Location  # noqa: PLC0415
    here = Location(memory).current()
    if not here:
        return None, "Buddy doesn't know where you are now: turn on Settings → Share my location."
    now_utc = now_utc or datetime.now(timezone.utc)
    age = now_utc - datetime.fromisoformat(here["at"])
    if age > timedelta(minutes=FRESH_MIN):
        return None, f"Your phone last shared where it is {int(age.total_seconds() // 60)} minutes ago."
    return here, ""


def places(memory: Memory) -> list[dict]:
    _ready(memory)
    return [dict(r) for r in memory.db.execute("SELECT id, name, kind, created_at FROM food_places ORDER BY name")]


def where(memory: Memory, now: datetime | None = None, now_utc: datetime | None = None) -> dict:
    """Eating or shopping, by where the phone is: log (a meal), verdict (shopping) or ask."""
    from . import coach  # noqa: PLC0415
    from .location import metres  # noqa: PLC0415
    from .maps import Maps  # noqa: PLC0415
    _ready(memory)
    slot = coach.slot_for(now or local_now())
    here, why = _here(memory, now_utc)
    if not here:
        return {"do": "ask", "place": "", "why": why, "slot": slot, "can_remember": False}
    pos = (float(here["lat"]), float(here["lng"]))
    best = None
    for r in memory.db.execute("SELECT * FROM food_places"):
        d = metres(pos, (r["lat"], r["lng"]))
        if d <= NEAR_M and (best is None or d < best[0]):
            best = (d, dict(r))
    if best:
        p = best[1]
        if p["kind"] == "store":
            return {"do": "verdict", "place": p["name"], "slot": slot, "can_remember": False,
                    "why": f"At {p['name']}, one of your stores: shopping, so it isn't added to a meal."}
        return {"do": "log", "place": p["name"], "slot": slot, "can_remember": False,
                "why": f"At {p['name']}, a place you eat."}
    named = Maps(memory).place_at(*pos)
    if named in ("home", "work"):
        return {"do": "log", "place": named, "slot": slot, "can_remember": False, "why": f"At {named}."}
    return {"do": "ask", "place": "", "slot": slot, "can_remember": True,
            "why": "A place Buddy doesn't know yet: say once whether it's a store or a place you eat."}


def remember(memory: Memory, kind: str, name: str = "") -> list[dict]:
    """Mark where the phone is now as a store or a place the owner eats."""
    if kind not in KINDS:
        raise ToolError("A store or a place you eat?")
    here, why = _here(memory)
    if not here:
        raise ToolError(why)
    name = " ".join(str(name or "").split())[:60] or ("My store" if kind == "store" else "A place I eat")
    _ready(memory)
    memory.db.execute("INSERT INTO food_places (name, kind, lat, lng, created_at) VALUES (?, ?, ?, ?, ?)",
                      (name, kind, round(float(here["lat"]), 5), round(float(here["lng"]), 5),
                       local_now().isoformat(timespec="seconds")))
    memory.db.commit()
    return places(memory)


def forget_place(memory: Memory, place_id: int | None = None) -> int:
    _ready(memory)
    cur = memory.db.execute("DELETE FROM food_places WHERE id = ?", (int(place_id),)) if place_id else \
        memory.db.execute("DELETE FROM food_places")
    memory.db.commit()
    return cur.rowcount


# --- a scan into a meal ----------------------------------------------------------------------------------------
def _scan(memory: Memory, scan_id: int) -> dict:
    from . import foodverdict  # noqa: PLC0415
    foodverdict._ready(memory)
    row = memory.db.execute("SELECT * FROM food_scans WHERE id = ?", (int(scan_id),)).fetchone()
    if not row:
        raise ToolError("That scan isn't there any more: scan it again.")
    return json.loads(row["food"]) | {"_name": row["name"]}


def eat(memory: Memory, scan_id: int, servings: float = 1.0, now: datetime | None = None, slot: str = "") -> dict:
    """Note a scanned product as eaten: label servings x the label's numbers, in the meal for the time of day."""
    from . import coach, foodverdict  # noqa: PLC0415
    try:
        servings = float(servings)
    except (TypeError, ValueError):
        servings = 1.0
    if not 0 < servings <= 20:
        raise ToolError("How many servings? Between a quarter and 20.")
    food = _scan(memory, scan_id)
    per100 = food.get("per100") if isinstance(food.get("per100"), dict) else {}
    if not isinstance(per100.get("kcal"), (int, float)):
        raise ToolError("This product has no nutrition numbers, so it can't be counted: note it on Food by hand.")
    grams, label = foodverdict.serving(food)
    grams *= servings
    amt = {k: float(v) * grams / 100 for k, v in per100.items() if isinstance(v, (int, float)) and not isinstance(v, bool)}
    count = f"{servings:g} × " if servings != 1 else ""
    what = f"{food['_name']} ({count}{label}, {grams:g} g)" if count else f"{food['_name']} ({label})"
    now = now or local_now()
    coach.add_meal(memory, what, amt.get("kcal"), amt.get("protein_g"), now=now,
                   extra={k: amt.get(k) for k in coach.EXTRA}, slot=slot)
    meal_id = int(memory.db.execute("SELECT max(id) FROM coach_meals").fetchone()[0])
    _ready(memory)
    memory.db.execute("INSERT OR REPLACE INTO scan_meals (meal_id, scan_id, day, servings, what, nova) "
                      "VALUES (?, ?, ?, ?, ?, ?)",
                      (meal_id, int(scan_id), now.date().isoformat(), servings, " ".join(what.split())[:300],
                       food.get("nova") if isinstance(food.get("nova"), int) else None))
    memory.db.commit()
    got = memory.db.execute("SELECT slot FROM coach_meals WHERE id = ?", (meal_id,)).fetchone()
    return {"meal_id": meal_id, "slot": got["slot"], "what": what, "servings": servings,
            "ontrack": ontrack(memory, now)}


def uneat(memory: Memory, meal_id: int) -> dict:
    """Undo a scan noted as a meal (only meals that came from a scan)."""
    _ready(memory)
    if not memory.db.execute("SELECT 1 FROM scan_meals s JOIN coach_meals m ON m.id = s.meal_id AND m.what = s.what "
                             "WHERE s.meal_id = ?", (int(meal_id),)).fetchone():
        raise ToolError("That meal didn't come from a scan: change it on Food.")
    memory.db.execute("DELETE FROM coach_meals WHERE id = ?", (int(meal_id),))
    memory.db.execute("DELETE FROM scan_meals WHERE meal_id = ?", (int(meal_id),))
    memory.db.commit()
    return {"ok": True, "ontrack": ontrack(memory)}


def recount(memory: Memory, meal_id: int, servings: float) -> dict:
    """Change how many servings a scanned meal was: same product, same meal."""
    _ready(memory)
    row = memory.db.execute("SELECT s.scan_id, m.slot, m.day, m.at FROM scan_meals s JOIN coach_meals m "
                            "ON m.id = s.meal_id AND m.what = s.what WHERE s.meal_id = ?", (int(meal_id),)).fetchone()
    if not row:
        raise ToolError("That meal didn't come from a scan: change it on Food.")
    when = datetime.fromisoformat(f"{row['day']}T{row['at']}")
    uneat(memory, meal_id)
    return eat(memory, row["scan_id"], servings, now=when, slot=row["slot"])


# --- is the day on track ---------------------------------------------------------------------------------------
def _limits(memory: Memory) -> tuple[dict, list[tuple]]:
    from . import dietplan  # noqa: PLC0415
    t = dietplan.targets(memory)
    kcal = t["calories"]["value"]
    out = []
    if t["sat_fat_g"]["value"]:
        out.append(("sat_fat_g", t["sat_fat_g"]["value"], t["sat_fat_g"]["how"]))
    if kcal:
        out.append(("fat_g", round(0.35 * kcal / 9),
                    "at most 35% of calories (the National Academies' range for fat is 20 to 35%)"))
    out.append(("sodium_mg", 2300, t["sodium_mg"]["how"]))
    return t, out


def _pace(memory: Memory, now: datetime, meals: list[dict]) -> tuple[float, str]:
    from . import coach  # noqa: PLC0415
    slot = coach.slot_for(now)
    if now.hour < 4:  # after midnight counts with the evening before
        slot = "dinner"
    done = [PACE[m["slot"]] for m in meals if m["slot"] in PACE]
    return min(1.0, max([PACE[slot], *done])), slot


def _next_meal(memory: Memory, slot: str, left: dict, strained: list[str]) -> dict | None:
    """The lightest of the day's menu options for the next meal, on what is strained."""
    from . import dietplan  # noqa: PLC0415
    letter = LETTER.get(slot)
    if not letter:
        return None
    try:
        first = next((m for m in dietplan.menu(memory)["meals"] if m["meal"] == letter), None)
    except Exception:  # noqa: BLE001 - no menu (no targets yet): no suggestion
        return None
    if not first:
        return None
    options = []
    for k in range(min(int(first.get("options") or 1), 8)):
        meal = next((m for m in dietplan.menu(memory, swap={letter: k})["meals"] if m["meal"] == letter), None)
        if meal:
            options.append(meal)
    if not options:
        return None

    def load(m):  # the worst share of what's left that the meal would use
        return max((m["totals"].get(k, 0) / left[k] if left.get(k) else m["totals"].get(k, 0) * 10)
                   for k in strained) if strained else 0
    best = min(options, key=load)
    return {"slot": slot, "name": best["name"], "items": ", ".join(f"{i['name']} ({i['amount']})" for i in best["items"]),
            "totals": {k: round(best["totals"].get(k, 0), 1) for k in ("kcal", "fat_g", "sat_fat_g", "sodium_mg")},
            "fits": all(best["totals"].get(k, 0) <= left.get(k, 0) for k in strained if left.get(k) is not None),
            "why": "the lightest of your day menu's options for this meal on what's over (USDA's numbers)"}


def ontrack(memory: Memory, now: datetime | None = None) -> dict:
    """Today's food against the owner's limits: what's over or ahead of pace, what to do, a lighter next meal."""
    from . import coach  # noqa: PLC0415
    now = now or local_now()
    ate = coach.eaten(memory, now.date())
    meals = ate["meals"]
    t, limits = _limits(memory)
    pace, slot = _pace(memory, now, meals)
    rows, strained, left = [], [], {}
    for key, limit, how in limits:
        words, unit = next((w, u) for k, w, u in WATCH if k == key)
        got = ate.get(key)
        left[key] = round(max(0.0, limit - (got or 0)), 1 if unit == "g" else 0)
        if got is None:
            continue
        state = ("over" if got > limit else "ahead" if got > limit * min(1.0, pace + ROOM)
                 else "close" if got >= CLOSE * limit else "ok")
        if state != "ok":
            strained.append(key)
        rows.append({"key": key, "what": words, "got": round(got, 1) if unit == "g" else round(got), "limit": limit,
                     "unit": unit, "state": state, "how": how, "counted": ate[key + "_meals"], "of": len(meals)})
    sugary = [m for m in meals if (m.get("added_sugar_g") or 0) > MEAL_SUGAR_G]
    _ready(memory)
    upf = [r["meal_id"] for r in memory.db.execute(
        "SELECT s.meal_id FROM scan_meals s JOIN coach_meals m ON m.id = s.meal_id AND m.what = s.what "
        "WHERE s.day = ? AND s.nova = 4", (now.date().isoformat(),))]
    lines = []
    for r in rows:
        if r["state"] == "over":
            lines.append(f"{r['what']} today {r['got']:g} {r['unit']}, over your {r['limit']:g} {r['unit']}.")
        elif r["state"] == "ahead":
            lines.append(f"{r['what']} today {r['got']:g} of your {r['limit']:g} {r['unit']}: ahead of pace "
                         f"for {'the evening' if pace >= 1 else 'this time of day'}.")
        elif r["state"] == "close":
            lines.append(f"{r['what']} today {r['got']:g} of your {r['limit']:g} {r['unit']}: close to your limit.")
    if sugary:
        lines.append(f"{len(sugary)} meal{'s' if len(sugary) != 1 else ''} today over {MEAL_SUGAR_G:g} g of added "
                     "sugar (the Dietary Guidelines' most for a meal).")
    if len(upf) >= 2:
        lines.append(f"{len(upf)} ultra-processed foods today (NOVA 4): swap one for something whole tomorrow.")
    nxt = NEXT.get(slot, "") if any(m["slot"] == slot for m in meals) else slot
    advice, suggestion = [], None
    if strained and nxt:
        keep = [f"{next(w for k, w, _ in WATCH if k == key).lower()} under {left[key]:g} "
                f"{next(u for k, _, u in WATCH if k == key)}" for key in strained if left[key] > 0]
        if keep:
            advice.append(f"For {nxt} keep " + ", ".join(keep) + ".")
        else:
            advice.append(f"For {nxt}: vegetables, beans or lean protein, nothing fried, little cheese or butter.")
        suggestion = _next_meal(memory, nxt, left, strained)
    elif strained:
        advice.append("Tomorrow's menu (Food → Menu) keeps the day within your numbers.")
    return {"on_track": not lines, "lines": lines, "advice": advice, "next_meal": suggestion, "rows": rows,
            "meals": len(meals), "ultra_processed": len(upf), "sugary_meals": len(sugary), "slot": slot,
            "calories": t["calories"], "note": NOTE}


def evening_line(memory: Memory, now: datetime | None = None) -> str:
    """For the evening check-in: what went over today, in one line (code only), or ''."""
    got = ontrack(memory, now)
    over = [r for r in got["rows"] if r["state"] == "over"]
    if not over and not got["sugary_meals"] and got["ultra_processed"] < 2:
        return ""
    bits = [f"{r['what'].lower()} {r['got']:g} of {r['limit']:g} {r['unit']}" for r in over]
    if got["sugary_meals"]:
        bits.append(f"added sugar in {got['sugary_meals']} meal{'s' if got['sugary_meals'] != 1 else ''}")
    if got["ultra_processed"] >= 2:
        bits.append(f"{got['ultra_processed']} ultra-processed foods")
    return "Food today went over on " + ", ".join(bits) + ". Tomorrow's menu on Health → Food keeps it in range."


def forget(memory: Memory) -> None:
    """Everything this module keeps: marked places and which meals came from scans."""
    _ready(memory)
    memory.db.execute("DELETE FROM food_places")
    memory.db.execute("DELETE FROM scan_meals")
    memory.db.commit()
