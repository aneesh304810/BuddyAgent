"""Groceries: the shopping list, ticked off as you scan, and My kitchen, what you bought and are going to eat. Code only.

  to_buy(memory)                    -> the shopping list: the organiser's "shopping" list, the same one Buddy fills
                                       when you say "add eggs to my shopping list"
  keep(memory, scan_id, anyway)     -> a scanned product into My kitchen, ticking off the shopping-list item it
                                       matches. One that's "Better to skip" for the owner waits for "anyway", with a
                                       better one they scanned before when there is one
  ate(memory, item_id)              -> one serving into the meal for the time of day (scanmeal.eat); the product stays
                                       until the owner taps Finished (a pack lasts many meals)
  note_eaten(memory, scan_id)       -> a scan noted as a meal at home that's in the kitchen keeps the kitchen in step
  from_kitchen(memory, left, keys)  -> kitchen foods whose serving fits what's left today, for the next-meal hint

A scan matches a list item when every word of the item (a plural s aside) is in the product's name or brand:
"greek yogurt" ticks off "Fage Total 0% Greek Yogurt". The kitchen keeps each product's label as it was scanned, so
deleting it from "Products you checked" doesn't empty the kitchen. Kept on the server until finished or deleted. No
AI model is given the kitchen (health information); the shopping list is the organiser's, which the conversation
already reads.
"""

from __future__ import annotations

import json
import re
from datetime import datetime

from .memory import Memory
from .tools import ToolError, local_now

SCHEMA = """
CREATE TABLE IF NOT EXISTS kitchen (
    id INTEGER PRIMARY KEY,
    scan_id INTEGER NOT NULL DEFAULT 0,  -- food_scans.id when it was scanned; 0 when ticked off by hand
    name TEXT NOT NULL,
    brand TEXT NOT NULL DEFAULT '',
    barcode TEXT NOT NULL DEFAULT '',
    food TEXT NOT NULL DEFAULT '{}',     -- the label as scanned (foodverdict.KEEP); {} when ticked off by hand
    packs INTEGER NOT NULL DEFAULT 1,
    eaten INTEGER NOT NULL DEFAULT 0,
    added_at TEXT NOT NULL,
    last_eaten TEXT
);
"""
SHOP_LIST = "shopping"
STOP = {"the", "and", "with", "of", "a", "an", "in", "organic", "original", "natural", "classic", "style", "pack",
        "family", "size", "value", "fresh", "plain", "free", "low", "fat", "less", "no", "added", "brand"}
NOTE = "Kept on your server until you tap Finished or delete it. No AI model sees what's in your kitchen."


def _ready(memory: Memory) -> None:
    memory.db.executescript(SCHEMA)


def _words(text: str) -> set[str]:
    out = set()
    for w in re.findall(r"[a-z0-9]+", str(text or "").lower()):
        if len(w) > 3 and w.endswith("s") and not w.endswith("ss"):
            w = w[:-1]
        if w not in STOP and len(w) > 1:
            out.add(w)
    return out


# --- the shopping list (the organiser's "shopping" list) --------------------------------------------------------
def _org(memory: Memory):
    from .organiser import Organiser  # noqa: PLC0415
    return Organiser(memory)


def to_buy(memory: Memory) -> list[dict]:
    return [{"id": t["id"], "text": t["text"]} for t in _org(memory).open(SHOP_LIST)]


def add_buy(memory: Memory, text: str) -> list[dict]:
    text = " ".join(str(text or "").split())[:120]
    if not text:
        raise ToolError("What to buy?")
    _org(memory).add(text, SHOP_LIST)
    return to_buy(memory)


def remove_buy(memory: Memory, task_id: int) -> list[dict]:
    if int(task_id) not in {t["id"] for t in to_buy(memory)}:
        raise ToolError("That isn't on the shopping list any more.")
    _org(memory).delete(str(int(task_id)))
    return to_buy(memory)


def _match(memory: Memory, name: str, brand: str = "") -> dict | None:
    have = _words(f"{name} {brand}")
    for t in to_buy(memory):
        want = _words(t["text"])
        if want and want <= have:
            return t
    return None


# --- My kitchen ------------------------------------------------------------------------------------------------
def _scan(memory: Memory, scan_id: int) -> dict:
    from . import foodverdict  # noqa: PLC0415
    foodverdict._ready(memory)
    row = memory.db.execute("SELECT * FROM food_scans WHERE id = ?", (int(scan_id),)).fetchone()
    if not row:
        raise ToolError("That scan isn't there any more: scan it again.")
    return dict(row)


def better_than(memory: Memory, scan_id: int) -> dict | None:
    """A product the owner scanned before that's better for them and of the same kind (a word of the name in
    common), the best first; None when there isn't one."""
    from . import foodverdict  # noqa: PLC0415
    every = foodverdict.scans(memory, 300)
    me = next((s for s in every if s["id"] == int(scan_id)), None)
    if not me:
        return None
    words = _words(me["name"])
    same = [s for s in every if s["id"] != me["id"] and foodverdict.RANK[s["verdict"]] > foodverdict.RANK[me["verdict"]]
            and _words(s["name"]) & words]
    if not same:
        return None
    best = max(same, key=lambda s: s["score"])
    return {"id": best["id"], "name": best["name"], "brand": best.get("brand") or "", "headline": best["headline"],
            "verdict": best["verdict"]}


def kitchen(memory: Memory) -> list[dict]:
    """What's in the kitchen, newest first, each judged again with today's numbers."""
    from . import foodverdict  # noqa: PLC0415
    _ready(memory)
    out = []
    for r in memory.db.execute("SELECT * FROM kitchen ORDER BY added_at DESC, id DESC"):
        item = {k: r[k] for k in ("id", "scan_id", "name", "brand", "packs", "eaten", "added_at", "last_eaten")}
        food = json.loads(r["food"] or "{}")
        if food:
            try:
                got = foodverdict.judge(memory, food)
                item |= {"verdict": got["verdict"], "headline": got["headline"], "serving": got["serving"]["label"],
                         "per_serving": got["per_serving"]}
            except ToolError:
                pass
        out.append(item)
    return out


def keep(memory: Memory, scan_id: int, anyway: bool = False) -> dict:
    """A scanned product into My kitchen; ticks off the shopping-list item it matches."""
    from . import foodverdict  # noqa: PLC0415
    _ready(memory)
    row = _scan(memory, scan_id)
    food = json.loads(row["food"])
    got = foodverdict.judge(memory, food)
    if got["verdict"] == "skip" and not anyway:
        return {"added": False, "warn": {"headline": got["headline"],
                                         "why": [c["text"] for c in got["concerns"] if c["serious"]][:3],
                                         "better": better_than(memory, scan_id)}}
    barcode = str(row["barcode"] or "")
    same = memory.db.execute(
        "SELECT id FROM kitchen WHERE (barcode != '' AND barcode = ?) OR (barcode = '' AND lower(name) = lower(?))",
        (barcode, row["name"])).fetchone()
    now = local_now().isoformat(timespec="seconds")
    if same:
        memory.db.execute("UPDATE kitchen SET packs = packs + 1, added_at = ?, scan_id = ?, food = ? WHERE id = ?",
                          (now, int(scan_id), json.dumps(food)[:20000], same["id"]))
    else:
        memory.db.execute("INSERT INTO kitchen (scan_id, name, brand, barcode, food, added_at) VALUES (?, ?, ?, ?, ?, ?)",
                          (int(scan_id), row["name"], row["brand"] or "", barcode, json.dumps(food)[:20000], now))
    memory.db.commit()
    ticked = _match(memory, row["name"], row["brand"] or "")
    if ticked:
        _org(memory).complete(str(ticked["id"]))
    return {"added": True, "already": bool(same), "name": row["name"], "ticked": ticked["text"] if ticked else "",
            "to_buy": to_buy(memory), "kitchen": kitchen(memory)}


def bought(memory: Memory, task_id: int) -> dict:
    """Tick a shopping-list item off by hand: it goes into the kitchen by its name (no label, so no numbers)."""
    item = next((t for t in to_buy(memory) if t["id"] == int(task_id)), None)
    if not item:
        raise ToolError("That isn't on the shopping list any more.")
    _org(memory).complete(str(item["id"]))
    _ready(memory)
    memory.db.execute("INSERT INTO kitchen (name, added_at) VALUES (?, ?)",
                      (item["text"], local_now().isoformat(timespec="seconds")))
    memory.db.commit()
    return {"to_buy": to_buy(memory), "kitchen": kitchen(memory)}


def ate(memory: Memory, item_id: int, now: datetime | None = None) -> dict:
    """One serving of a kitchen product into the meal for the time of day; the product stays until finished."""
    from . import coach, scanmeal  # noqa: PLC0415
    _ready(memory)
    row = memory.db.execute("SELECT * FROM kitchen WHERE id = ?", (int(item_id),)).fetchone()
    if not row:
        raise ToolError("That isn't in your kitchen any more.")
    now = now or local_now()
    food = json.loads(row["food"] or "{}")
    if food:
        out = scanmeal.eat(memory, row["scan_id"], 1, now=now, food=food | {"_name": row["name"]})
    else:  # ticked off by hand: the meal by its name, without numbers
        coach.add_meal(memory, row["name"], now=now)
        out = {"what": row["name"], "slot": coach.slot_for(now), "ontrack": scanmeal.ontrack(memory, now)}
    memory.db.execute("UPDATE kitchen SET eaten = eaten + 1, last_eaten = ? WHERE id = ?",
                      (now.isoformat(timespec="seconds"), int(item_id)))
    memory.db.commit()
    return out | {"kitchen": kitchen(memory)}


def note_eaten(memory: Memory, scan_id: int) -> str:
    """A scan noted as a meal: when that product is in the kitchen, count it there too. Its name, or ''."""
    _ready(memory)
    try:
        barcode = str(_scan(memory, scan_id)["barcode"] or "")
    except ToolError:
        return ""
    row = memory.db.execute("SELECT id, name FROM kitchen WHERE scan_id = ? OR (barcode != '' AND barcode = ?)",
                            (int(scan_id), barcode)).fetchone()
    if not row:
        return ""
    memory.db.execute("UPDATE kitchen SET eaten = eaten + 1, last_eaten = ? WHERE id = ?",
                      (local_now().isoformat(timespec="seconds"), row["id"]))
    memory.db.commit()
    return row["name"]


def finish(memory: Memory, item_id: int) -> list[dict]:
    """Finished (or bought by mistake): off the kitchen."""
    _ready(memory)
    if not memory.db.execute("DELETE FROM kitchen WHERE id = ?", (int(item_id or 0),)).rowcount:
        raise ToolError("That isn't in your kitchen any more.")
    memory.db.commit()
    return kitchen(memory)


def forget(memory: Memory) -> None:
    """The whole kitchen (the shopping list is the organiser's, deleted there)."""
    _ready(memory)
    memory.db.execute("DELETE FROM kitchen")
    memory.db.commit()


def from_kitchen(memory: Memory, left: dict, keys: list[str], limit: int = 3) -> list[dict]:
    """Kitchen foods whose serving fits what's left today on what's strained, the better ones for the owner first."""
    from . import foodverdict  # noqa: PLC0415
    fits = []
    for item in kitchen(memory):
        per = item.get("per_serving")
        if not per or not keys:
            continue
        if all((per.get(k) or 0) <= (left.get(k) if left.get(k) is not None else 0) for k in keys):
            load = sum((per.get(k) or 0) / left[k] for k in keys if left.get(k))
            fits.append((-foodverdict.RANK[item["verdict"]], load, item))
    fits.sort(key=lambda x: (x[0], x[1]))
    return [{"id": i["id"], "name": i["name"], "serving": i["serving"], "verdict": i["verdict"],
             "numbers": {k: i["per_serving"].get(k) for k in ("kcal", "fat_g", "sat_fat_g", "sodium_mg")}}
            for _, _, i in fits[:limit]]
