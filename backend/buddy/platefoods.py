"""Foods on one plate, recognised on the phone from the foods the owner taught (Scan food → Dish): the first time a
taught food is added to a meal, the food and amount the owner picked (USDA's numbers for it) are kept as its usual,
so the next plate with it is added in one tap ("Add all"). A food without a usual yet is shown for the owner to pick
once. Code only: the names come from the phone (never a picture), and nothing here is sent to any AI model.

  remember(memory, name, meal)   -> the usual for a taught food (what, calories, protein and the rest per that amount)
  add_plate(memory, names, slot) -> {added: [...], unknown: [...], eaten}: each known food into the meal for the time
  foods(memory) / forget(memory, name)
"""

from __future__ import annotations

from datetime import datetime, timezone

from . import coach
from .memory import Memory
from .tools import ToolError, local_now

SCHEMA = """
CREATE TABLE IF NOT EXISTS plate_foods (
    name TEXT PRIMARY KEY COLLATE NOCASE,   -- the taught food's name, as the phone sends it
    what TEXT NOT NULL,                     -- the meal line it adds ("Pita bread, white (1 × 1 pita, 60 g)")
    calories REAL, protein_g REAL, carbs_g REAL, fat_g REAL, fiber_g REAL, sodium_mg REAL, sat_fat_g REAL,
    sugar_g REAL, added_sugar_g REAL,
    updated_at TEXT NOT NULL
);
"""
NUMBERS = ("calories", "protein_g", *coach.EXTRA)
MAX_PLATE = 8


def _ready(memory: Memory) -> None:
    memory.db.executescript(SCHEMA)


def _name(name) -> str:
    return " ".join(str(name or "").split())[:60]


def _num(v):
    try:
        return None if v in (None, "") else round(float(v), 1)
    except (TypeError, ValueError):
        return None


def remember(memory: Memory, name: str, meal: dict) -> None:
    """The food and amount the owner just added for a taught food: its usual from now on (the latest wins)."""
    name, what = _name(name), " ".join(str(meal.get("what") or "").split())[:300]
    if not name or not what:
        return
    _ready(memory)
    values = [_num(meal.get(k)) for k in NUMBERS]
    memory.db.execute(f"INSERT INTO plate_foods (name, what, {', '.join(NUMBERS)}, updated_at) "  # nosec B608
                      f"VALUES (?, ?{', ?' * len(NUMBERS)}, ?) ON CONFLICT(name) DO UPDATE SET what = excluded.what, "
                      + ", ".join(f"{k} = excluded.{k}" for k in NUMBERS) + ", updated_at = excluded.updated_at",
                      (name, what, *values, datetime.now(timezone.utc).isoformat(timespec="seconds")))  # fixed columns
    memory.db.commit()


def usual(memory: Memory, name: str) -> dict | None:
    _ready(memory)
    row = memory.db.execute("SELECT * FROM plate_foods WHERE name = ?", (_name(name),)).fetchone()
    return dict(row) if row else None


def add_plate(memory: Memory, names, slot: str = "", now: datetime | None = None) -> dict:
    """Each food on the plate with a usual goes into the meal for the time (or the slot given), once; the rest come
    back as unknown, for the owner to pick a food and amount for (then it is remembered)."""
    clean, seen = [], set()
    for n in names if isinstance(names, list) else []:
        n = _name(n)
        if n and n.lower() not in seen:
            seen.add(n.lower())
            clean.append(n)
    if not clean:
        raise ToolError("Which foods were on the plate?")
    clean = clean[:MAX_PLATE]
    now = now or local_now()
    added, unknown = [], []
    for n in clean:
        got = usual(memory, n)
        if not got:
            unknown.append(n)
            continue
        coach.add_meal(memory, got["what"], got["calories"], got["protein_g"], now=now,
                       extra={k: got[k] for k in coach.EXTRA}, slot=slot)
        added.append(n)
    return {"added": added, "unknown": unknown, "eaten": coach.eaten(memory, now.date()),
            "slot": slot if slot in coach.SLOTS else coach.slot_for(now)}


def foods(memory: Memory) -> list[dict]:
    _ready(memory)
    return [dict(r) for r in memory.db.execute("SELECT name, what, calories, protein_g, updated_at FROM plate_foods "
                                               "ORDER BY name")]


def forget(memory: Memory, name: str) -> None:
    _ready(memory)
    memory.db.execute("DELETE FROM plate_foods WHERE name = ?", (_name(name),))
    memory.db.commit()
