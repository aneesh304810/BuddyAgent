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
  people / set_who                  -> who it's for: the owner, the household they name once (e.g. a wife and a son),
                                       or everyone; the kitchen groups by person
  set_expires / use_soon            -> the date on the pack (read on the phone, or picked) and what to use in the next
                                       two days, for Home's Today strip, Health → Today and the evening check-in
  spend(memory)                     -> grocery spending by month and store, from the card and bank transactions Buddy
                                       files as groceries (money.py) and the order emails of grocery stores (shopper.py)
  deals_for(memory, name)           -> lower prices seen lately for a product: the store emails Buddy reads (weekly
                                       ads, coupons: dealmail.py) and the prices of orders, from the price book, each
                                       with the store's own link (clip a coupon there: Buddy can't clip or buy)

A scan matches a list item when every word of the item (a plural s aside) is in the product's name or brand:
"greek yogurt" ticks off "Fage Total 0% Greek Yogurt". The kitchen keeps each product's label as it was scanned, so
deleting it from "Products you checked" doesn't empty the kitchen. Kept on the server until finished or deleted. No
AI model is given the kitchen (health information); the shopping list is the organiser's, which the conversation
already reads.
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta, timezone

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
    last_eaten TEXT,
    who TEXT NOT NULL DEFAULT '',        -- '' everyone, 'me', or a name from the household
    expires TEXT,                        -- YYYY-MM-DD, the date on the pack
    expires_how TEXT NOT NULL DEFAULT '' -- read (the phone read it) | set (picked)
);
CREATE TABLE IF NOT EXISTS buy_for (
    task_id INTEGER PRIMARY KEY,         -- organiser tasks.id on the shopping list
    who TEXT NOT NULL
);
"""
ADDED = {"who": "TEXT NOT NULL DEFAULT ''", "expires": "TEXT", "expires_how": "TEXT NOT NULL DEFAULT ''"}
PEOPLE_KEY = "groceries:people"
SOON_DAYS = 2
GROCERS = ("shoprite", "acme", "walmart", "stop & shop", "stop and shop", "whole foods", "trader joe", "costco",
           "aldi", "wegmans", "kroger", "target", "patel brothers", "h mart", "lidl", "sprouts", "safeway", "instacart",
           "shop rite", "foodtown", "key food", "giant", "food lion", "publix", "bj's")
SHOP_LIST = "shopping"
STOP = {"the", "and", "with", "of", "a", "an", "in", "organic", "original", "natural", "classic", "style", "pack",
        "family", "size", "value", "fresh", "plain", "free", "low", "fat", "less", "no", "added", "brand"}
NOTE = "Kept on your server until you tap Finished or delete it. No AI model sees what's in your kitchen."


def _ready(memory: Memory) -> None:
    memory.db.executescript(SCHEMA)
    have = {r[1] for r in memory.db.execute("PRAGMA table_info(kitchen)")}
    for name, kind in ADDED.items():  # kitchens made before these columns existed
        if name not in have:
            memory.db.execute(f"ALTER TABLE kitchen ADD COLUMN {name} {kind}")  # nosec B608 - fixed names
    memory.db.commit()


# --- who it's for ------------------------------------------------------------------------------------------------
def people(memory: Memory) -> list[str]:
    try:
        return [str(p) for p in json.loads(memory.get(PEOPLE_KEY) or "[]")][:8]
    except ValueError:
        return []


def set_people(memory: Memory, names: list) -> list[str]:
    """The household the owner shops for (the owner is always 'me'), e.g. their wife and son, by first name."""
    out = []
    for n in names or []:
        n = " ".join(str(n or "").split())[:30]
        if n and n.lower() not in {o.lower() for o in out} and n.lower() not in ("me", "everyone"):
            out.append(n)
    memory.set(PEOPLE_KEY, json.dumps(out[:8]))
    return out[:8]


def _who(memory: Memory, who) -> str:
    who = " ".join(str(who or "").split())
    if who.lower() in ("", "everyone", "all", "home"):
        return ""
    if who.lower() == "me":
        return "me"
    match = next((p for p in people(memory) if p.lower() == who.lower()), None)
    if not match:
        raise ToolError(f"Who's {who}? Add them to the people you shop for first.")
    return match


def set_who(memory: Memory, item_id: int, who) -> list[dict]:
    _ready(memory)
    if not memory.db.execute("UPDATE kitchen SET who = ? WHERE id = ?", (_who(memory, who), int(item_id))).rowcount:
        raise ToolError("That isn't in your kitchen any more.")
    memory.db.commit()
    return kitchen(memory)


# --- the date on the pack ---------------------------------------------------------------------------------------
def set_expires(memory: Memory, item_id: int, day: str = "", days: int | None = None, how: str = "set") -> list[dict]:
    """The date on the pack: a date (YYYY-MM-DD, as the phone read it or the owner picked it), or days from today."""
    today = local_now().date()
    if days is not None:
        when = today + timedelta(days=int(days))
    else:
        try:
            when = date.fromisoformat(str(day or "")[:10])
        except ValueError:
            raise ToolError("Which date? Like 2026-12-05.") from None
    if not today - timedelta(days=60) <= when <= today + timedelta(days=1100):
        raise ToolError("That date doesn't look right for food: check it.")
    _ready(memory)
    if not memory.db.execute("UPDATE kitchen SET expires = ?, expires_how = ? WHERE id = ?",
                             (when.isoformat(), "read" if how == "read" else "set", int(item_id))).rowcount:
        raise ToolError("That isn't in your kitchen any more.")
    memory.db.commit()
    return kitchen(memory)


def _when(days_left: int) -> str:
    if days_left < -1:
        return f"expired {-days_left} days ago"
    return {-1: "expired yesterday", 0: "use today", 1: "use by tomorrow"}.get(days_left, f"use within {days_left} days")


def use_soon(memory: Memory, now: datetime | None = None, within: int = SOON_DAYS) -> list[dict]:
    """Kitchen products whose date is within the next two days, or past, soonest first."""
    today = (now or local_now()).date()
    out = []
    for k in kitchen(memory):
        if not k.get("expires"):
            continue
        left = (date.fromisoformat(k["expires"]) - today).days
        if left <= within:
            out.append(k | {"days_left": left, "when": _when(left)})
    return sorted(out, key=lambda k: k["days_left"])


def _label(k: dict) -> str:
    who = k.get("who") or ""
    return k["name"] + ((" (yours)" if who == "me" else f" ({who}'s)") if who else "")


def soon_line(memory: Memory, now: datetime | None = None) -> str:
    """For the evening check-in: what to use up, in one line (code only), or ''."""
    soon = use_soon(memory, now)
    if not soon:
        return ""
    bits = [f"{_label(k)}: {k['when']}" for k in soon[:4]]
    more = f" and {len(soon) - 4} more" if len(soon) > 4 else ""
    return "From your kitchen, " + "; ".join(bits) + more + "."


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
    _ready(memory)
    tags = {r["task_id"]: r["who"] for r in memory.db.execute("SELECT * FROM buy_for")}
    return [{"id": t["id"], "text": t["text"], "who": tags.get(t["id"], "")} for t in _org(memory).open(SHOP_LIST)]


def add_buy(memory: Memory, text: str, who: str = "") -> list[dict]:
    text = " ".join(str(text or "").split())[:120]
    if not text:
        raise ToolError("What to buy?")
    who = _who(memory, who)
    task = _org(memory).add(text, SHOP_LIST)
    if who:
        _ready(memory)
        memory.db.execute("INSERT OR REPLACE INTO buy_for (task_id, who) VALUES (?, ?)", (task["id"], who))
        memory.db.commit()
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
        item = {k: r[k] for k in ("id", "scan_id", "name", "brand", "packs", "eaten", "added_at", "last_eaten", "who",
                                  "expires", "expires_how")}
        food = json.loads(r["food"] or "{}")
        item["matched"] = food.get("matched", "")  # added by name: the USDA food it was matched to
        if food:
            try:
                got = foodverdict.judge(memory, food)
                item |= {"verdict": got["verdict"], "headline": got["headline"], "serving": got["serving"]["label"],
                         "per_serving": got["per_serving"]}
            except ToolError:
                pass
        out.append(item)
    return out


def keep(memory: Memory, scan_id: int, anyway: bool = False, who: str | None = None, expires: str = "") -> dict:
    """A scanned product into My kitchen; ticks off the shopping-list item it matches. who: whom it's for (else the
    list item's, else everyone). expires: the date on the pack as the phone's camera read it in the same scan; with
    one more pack of a product already in the kitchen, the sooner date stays."""
    from . import foodverdict  # noqa: PLC0415
    _ready(memory)
    chosen = _who(memory, who) if who not in (None, "") else None
    row = _scan(memory, scan_id)
    food = json.loads(row["food"])
    got = foodverdict.judge(memory, food)
    if got["verdict"] == "skip" and not anyway:
        return {"added": False, "warn": {"headline": got["headline"],
                                         "why": [c["text"] for c in got["concerns"] if c["serious"]][:3],
                                         "better": better_than(memory, scan_id)}}
    barcode = str(row["barcode"] or "")
    same = memory.db.execute(
        "SELECT id, expires FROM kitchen WHERE (barcode != '' AND barcode = ?) OR (barcode = '' AND lower(name) = lower(?))",
        (barcode, row["name"])).fetchone()
    now = local_now().isoformat(timespec="seconds")
    ticked = _match(memory, row["name"], row["brand"] or "")
    who = chosen if chosen is not None else (ticked or {}).get("who", "")
    if same:
        item_id = same["id"]
        memory.db.execute("UPDATE kitchen SET packs = packs + 1, added_at = ?, scan_id = ?, food = ?, who = ? WHERE id = ?",
                          (now, int(scan_id), json.dumps(food)[:20000], who, item_id))
    else:
        item_id = memory.db.execute(
            "INSERT INTO kitchen (scan_id, name, brand, barcode, food, added_at, who) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (int(scan_id), row["name"], row["brand"] or "", barcode, json.dumps(food)[:20000], now, who)).lastrowid
    memory.db.commit()
    dated = ""
    if expires and not (same and same["expires"] and same["expires"] <= str(expires)[:10]):
        try:
            set_expires(memory, int(item_id), str(expires), how="read")
            dated = str(expires)[:10]
        except (ToolError, ValueError):
            pass  # a date that doesn't look right for food: left for the owner to read again or pick
    if not dated and same and same["expires"]:
        dated = same["expires"]
    if ticked:
        _org(memory).complete(str(ticked["id"]))
    return {"added": True, "already": bool(same), "name": row["name"], "item_id": int(item_id), "who": who,
            "expires": dated,
            "ticked": ticked["text"] if ticked else "", "to_buy": to_buy(memory), "kitchen": kitchen(memory)}


def bought(memory: Memory, task_id: int) -> dict:
    """Tick a shopping-list item off by hand: it goes into the kitchen by its name (no label, so no numbers)."""
    item = next((t for t in to_buy(memory) if t["id"] == int(task_id)), None)
    if not item:
        raise ToolError("That isn't on the shopping list any more.")
    _org(memory).complete(str(item["id"]))
    _ready(memory)
    memory.db.execute("INSERT INTO kitchen (name, added_at, who) VALUES (?, ?, ?)",
                      (item["text"], local_now().isoformat(timespec="seconds"), item.get("who", "")))
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


def _in_kitchen(memory: Memory, scan_id: int):
    _ready(memory)
    try:
        barcode = str(_scan(memory, scan_id)["barcode"] or "")
    except ToolError:
        return None
    return memory.db.execute("SELECT id, name FROM kitchen WHERE scan_id = ? OR (barcode != '' AND barcode = ?)",
                             (int(scan_id), barcode)).fetchone()


def in_kitchen(memory: Memory, scan_id: int) -> str:
    """The kitchen product a scan is (its name), or '': at home only those count as eaten (scanmeal.where)."""
    row = _in_kitchen(memory, scan_id)
    return row["name"] if row else ""


def note_eaten(memory: Memory, scan_id: int) -> str:
    """A scan noted as a meal: when that product is in the kitchen, count it there too. Its name, or ''."""
    row = _in_kitchen(memory, scan_id)
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


# --- spending -----------------------------------------------------------------------------------------------------
def _grocer(name: str) -> bool:
    low = str(name or "").lower()
    return any(g in low for g in GROCERS)


def spend(memory: Memory, months: int = 3, now: datetime | None = None) -> dict:
    """Grocery spending by month and store: the card and bank transactions filed as groceries, and grocery stores'
    order emails (e-receipts) with what each order was."""
    from .money import Money  # noqa: PLC0415
    from . import shopper  # noqa: PLC0415
    Money(memory)
    today = (now or local_now()).date()
    first = today.replace(day=1)
    for _ in range(max(0, months - 1)):
        first = (first - timedelta(days=1)).replace(day=1)
    rows = memory.db.execute(
        "SELECT substr(day, 1, 7) AS month, merchant, SUM(amount) AS total, COUNT(*) AS n FROM transactions "
        "WHERE category = 'groceries' AND day >= ? GROUP BY month, merchant ORDER BY month DESC, total DESC",
        (first.isoformat(),)).fetchall()
    by_month: dict[str, dict] = {}
    for r in rows:
        m = by_month.setdefault(r["month"], {"month": r["month"], "total": 0.0, "stores": []})
        m["total"] = round(m["total"] + (r["total"] or 0), 2)
        m["stores"].append({"store": r["merchant"], "total": round(r["total"] or 0, 2), "visits": r["n"]})
    shopper._ready(memory)
    receipts = [dict(r) for r in memory.db.execute(
        "SELECT day, merchant, item, amount FROM shop_purchases WHERE event = 'order' AND day >= ? "
        "ORDER BY day DESC LIMIT 200", (first.isoformat(),))]
    receipts = [r for r in receipts if _grocer(r["merchant"])][:30]
    return {"months": list(by_month.values()), "receipts": receipts,
            "note": "From your card and bank transactions Buddy files as groceries, and order emails from grocery "
                    "stores. Kept on your server."}


# --- lower prices seen lately -----------------------------------------------------------------------------------
def _seen(memory: Memory, days: int = 14) -> list[dict]:
    from . import pricebook  # noqa: PLC0415
    since = datetime.now(timezone.utc) - timedelta(days=days)
    try:
        return pricebook.run(memory, "SELECT item, price, regular_price, seller, url, source, seen_at FROM observations "
                                     "WHERE domain = 'product' AND seen_at >= ? ORDER BY seen_at DESC LIMIT 4000",
                             (since.replace(tzinfo=None),))
    except Exception:  # noqa: BLE001 - no price book yet: no deals
        return []


def _fits(want: set[str], last: str, have: set[str]) -> bool:
    if not want:
        return False
    if len(want) <= 2:
        return want <= have
    return last in have and len(want & have) >= 2


def deals_for(memory: Memory, name: str, brand: str = "", seen: list[dict] | None = None, limit: int = 3) -> list[dict]:
    """The lowest prices seen lately for a product, one per store, cheapest first."""
    words = [w for w in re.findall(r"[a-z0-9]+", str(name or "").lower())]
    want = _words(name) - _words(brand)
    last = next((w for w in reversed([x[:-1] if len(x) > 3 and x.endswith("s") else x for x in words])
                 if w in want), "")
    best: dict[str, dict] = {}
    for r in seen if seen is not None else _seen(memory):
        if not _fits(want, last, _words(r["item"])):
            continue
        store = (r.get("seller") or r.get("source") or "").strip() or "a store"
        if store.lower() not in best or r["price"] < best[store.lower()]["price"]:
            best[store.lower()] = {"item": r["item"], "price": round(float(r["price"]), 2), "store": store,
                                   "regular": r.get("regular_price"), "link": r.get("url") or "",
                                   "from": r.get("source") or "", "seen": str(r.get("seen_at") or "")[:10]}
    return sorted(best.values(), key=lambda d: d["price"])[:limit]


def lists(memory: Memory, now: datetime | None = None) -> dict:
    """Everything the Groceries page shows."""
    seen = _seen(memory)
    buy = [t | {"deals": deals_for(memory, t["text"], seen=seen)} for t in to_buy(memory)]
    return {"people": people(memory), "to_buy": buy, "kitchen": kitchen(memory), "use_soon": use_soon(memory, now),
            "spend": spend(memory, now=now), "note": NOTE}


# --- groceries said or typed, without a scan ----------------------------------------------------------------------
COUNT_WORDS = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
               "eight": 8, "nine": 9, "ten": 10, "twelve": 12, "a dozen": 12, "dozen": 12, "one dozen": 12,
               "half a dozen": 6, "two dozen": 24, "three dozen": 36}
WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
MONTHS = ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec")
UNITS = r"(?:packs?|packets?|bags?|boxes?|cartons?|bottles?|cans?|jars?|tubs?|bunch(?:es)?|heads?|loaves|loaf|" \
        r"lbs?|pounds?|kg|dozen)"


def _day(text: str, today: date) -> date | None:
    """'friday', 'tomorrow', 'dec 5', '12/5', '2026-12-05' as a date on or after today."""
    t = " ".join(str(text or "").lower().replace(",", " ").split())
    if t in ("today", "tonight"):
        return today
    if t == "tomorrow":
        return today + timedelta(days=1)
    for i, name in enumerate(WEEKDAYS):
        if t in (name, name[:3]):
            return today + timedelta(days=(i - today.weekday()) % 7)
    try:
        return date.fromisoformat(t[:10])
    except ValueError:
        pass
    m = re.fullmatch(r"(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?", t)
    if m:
        month, day_, year = int(m.group(1)), int(m.group(2)), m.group(3)
    else:
        m = re.fullmatch(r"([a-z]{3})[a-z]*\.? (\d{1,2})(?: (\d{4}))?", t)
        if not m or m.group(1) not in MONTHS:
            return None
        month, day_, year = MONTHS.index(m.group(1)) + 1, int(m.group(2)), m.group(3)
    try:
        when = date(int(year) + (2000 if year and len(year) == 2 else 0) if year else today.year, month, day_)
    except ValueError:
        return None
    return when if year or when >= today - timedelta(days=30) else when.replace(year=when.year + 1)


def parse_items(text: str, household: list[str], today: date | None = None) -> list[dict]:
    """What the owner typed: "eggs x12, 2 dozen eggs, milk use by Friday, spinach for Ishaan, chicken use within 2
    days" -> [{name, count, who, use_by, within_days}]. Commas (or new lines) between items; "and" before the last."""
    today = today or local_now().date()
    parts = [p for p in re.split(r"[,;\n]+", str(text or "")) if p.strip()]
    if parts:
        parts = parts[:-1] + re.split(r"\s+and\s+", parts[-1])
    names = {p.lower(): p for p in household} | {"me": "me", "everyone": "everyone", "all": "everyone"}
    out = []
    for raw in parts[:30]:
        s = " ".join(raw.split())
        item: dict = {}
        m = re.search(r"\bfor\s+(\w+)\s*$", s, re.I) or re.search(r"\bfor\s+(\w+)\b", s, re.I)
        if m and m.group(1).lower() in names:
            item["who"] = names[m.group(1).lower()]
            s = (s[:m.start()] + s[m.end():]).strip()
        m = re.search(r"\b(?:use\s+)?within\s+(\d+)\s+days?\b|\bin\s+(\d+)\s+days?\b", s, re.I)
        if m:
            item["within_days"] = int(m.group(1) or m.group(2))
            s = (s[:m.start()] + s[m.end():]).strip()
        m = re.search(r"\b(?:use|best|sell|good)?\s*(?:by|before|until)\s+([a-z0-9/\- ]+?)\s*$", s, re.I)
        if m and (when := _day(m.group(1), today)):
            item["use_by"] = when.isoformat()
            s = s[:m.start()].strip()
        m = re.search(r"\s*(?:x|×)\s*(\d+)\s*$|\s*\((\d+)\)\s*$", s, re.I)
        if m:
            item["count"] = int(m.group(1) or m.group(2))
            s = s[:m.start()].strip()
        m = re.match(r"^(\d+|an?|one|two|three|four|five|half an?)\s+dozen\s+(?:of\s+)?", s, re.I)
        if m and len(s) > m.end():  # "2 dozen eggs", "half a dozen eggs"
            word = m.group(1).lower()
            item.setdefault("count", 6 if word.startswith("half") else 12 * (int(word) if word.isdigit()
                                                                              else COUNT_WORDS.get(word, 1)))
            s = s[m.end():]
        m = re.match(r"^(\d+|half a dozen|(?:one|two|three) dozen|a dozen|dozen|an?|one|two|three|four|five|six|seven|"
                     rf"eight|nine|ten|twelve)\s+(?:{UNITS}\s+(?:of\s+)?)?", s, re.I)
        if m and len(s) > m.end():
            word = m.group(1).lower()
            item.setdefault("count", int(word) if word.isdigit() else COUNT_WORDS.get(word, 1))
            s = s[m.end():]
        s = re.sub(rf"^(?:{UNITS}\s+of\s+)", "", s, flags=re.I).strip(" .-")
        if len(s) >= 2:
            out.append({"name": s[:60], **item})
    return out


def _usda(memory: Memory, name: str, env=None, fetch=None) -> dict:
    """USDA's closest basic food for a name ('eggs' -> 'Egg, whole, raw, fresh'), or {} (no key, no match, offline).
    Only the name goes to USDA."""
    from . import foodfacts, foodverdict  # noqa: PLC0415
    try:
        found = foodfacts.foods(memory, name, env=env, fetch=fetch)
    except (ToolError, OSError, ValueError):
        return {}
    pick = next((f for f in found if (f.get("per100") or {}).get("kcal")), None)
    if not pick:
        return {}
    return {k: pick.get(k) for k in foodverdict.KEEP if pick.get(k) not in (None, "", [])} | {"matched": pick["name"]}


def add_by_name(memory: Memory, items: list[dict], env=None, fetch=None) -> dict:
    """Groceries the owner says or types they bought, into My kitchen without a scan: each matched to USDA's closest
    basic food (so Ate it counts it and the verdict shows), how many, whom it's for, the use-by date; a shopping-list
    item it matches is ticked off. One already in the kitchen gets more packs (the sooner date stays)."""
    _ready(memory)
    today = local_now().date()
    added, ticked, notes = [], [], []
    for it in (items or [])[:30]:
        name = " ".join(str(it.get("name") or "").split())[:60]
        if len(name) < 2:
            continue
        name = name[0].upper() + name[1:]
        try:
            count = max(1, min(int(it.get("count") or 1), 99))
        except (TypeError, ValueError):
            count = 1
        try:
            chosen = _who(memory, it.get("who")) if it.get("who") not in (None, "") else None
        except ToolError:
            chosen = None
            notes.append(f"Who's {it.get('who')}? Add them under Who you shop for; {name} is for everyone for now.")
        expires = ""
        if it.get("use_by"):
            try:
                expires = date.fromisoformat(str(it["use_by"])[:10]).isoformat()
            except ValueError:
                expires = ""
        elif str(it.get("within_days") or "").isdigit():
            expires = (today + timedelta(days=min(int(it["within_days"]), 1100))).isoformat()
        food = _usda(memory, name, env, fetch)
        if not food:
            notes.append(f"No USDA match for {name}: kept by name, without numbers.")
        tick = _match(memory, name)
        who = chosen if chosen is not None else (tick or {}).get("who", "")
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        same = memory.db.execute("SELECT id, expires FROM kitchen WHERE barcode = '' AND lower(name) = lower(?)",
                                 (name,)).fetchone()
        if same:
            item_id = same["id"]
            memory.db.execute("UPDATE kitchen SET packs = packs + ?, added_at = ?, who = ? WHERE id = ?",
                              (count, now, who, item_id))
            if food:
                memory.db.execute("UPDATE kitchen SET food = ? WHERE id = ?", (json.dumps(food)[:20000], item_id))
        else:
            item_id = memory.db.execute(
                "INSERT INTO kitchen (scan_id, name, food, packs, added_at, who) VALUES (0, ?, ?, ?, ?, ?)",
                (name, json.dumps(food)[:20000] if food else "{}", count, now, who)).lastrowid
        memory.db.commit()
        if expires and not (same and same["expires"] and same["expires"] <= expires):
            try:
                set_expires(memory, int(item_id), expires, how="set")
            except ToolError:
                notes.append(f"The date for {name} doesn't look right for food: pick it on Groceries.")
                expires = ""
        if tick:
            _org(memory).complete(str(tick["id"]))
            ticked.append(tick["text"])
        added.append({"id": int(item_id), "name": name, "count": count, "who": who, "use_by": expires,
                      "matched": food.get("matched", "")})
    return {"added": added, "ticked": ticked, "notes": notes, "to_buy": to_buy(memory), "kitchen": kitchen(memory)}


def refood(memory: Memory, item_id: int, food: dict) -> list[dict]:
    """The owner picked the right USDA food for a kitchen item added by name ("not that milk, 2% milk")."""
    from . import foodverdict  # noqa: PLC0415
    _ready(memory)
    if not isinstance(food, dict) or not (food.get("per100") or {}).get("kcal"):
        raise ToolError("Pick a food with numbers.")
    slim = {k: food.get(k) for k in foodverdict.KEEP if food.get(k) not in (None, "", [])}
    slim["matched"] = " ".join(str(food.get("name") or "").split())[:120]
    if not memory.db.execute("UPDATE kitchen SET food = ? WHERE id = ?", (json.dumps(slim)[:20000], int(item_id))).rowcount:
        raise ToolError("That isn't in your kitchen any more.")
    memory.db.commit()
    return kitchen(memory)


def said(result: dict) -> str:
    """The read-back: only what was added, in the owner's own words (no verdicts or numbers: those stay off AI)."""
    def one(a):
        bits = [a["name"].lower() + (f" ({a['count']})" if a["count"] > 1 else "")]
        if a["who"] and a["who"] != "me":
            bits.append(f"for {a['who']}")
        if a["use_by"]:
            bits.append(f"use by {date.fromisoformat(a['use_by']).strftime('%a %b %d')}")
        return " ".join(bits)
    if not result["added"]:
        return "Nothing added: say what you bought, like \"eggs, milk and spinach for my kitchen\"."
    out = "Added to your kitchen: " + ", ".join(one(a) for a in result["added"]) + "."
    if result["ticked"]:
        out += " Ticked off your list: " + ", ".join(result["ticked"]) + "."
    return " ".join([out] + result["notes"][:3])


TOOL = {
    "name": "my_kitchen",
    "description": "Groceries the user bought, into My kitchen (Food -> Groceries). When they say they bought or are "
    "buying groceries for their kitchen ('I'm buying eggs, milk and spinach for my kitchen', 'got two dozen eggs and "
    "chicken, use within 2 days', 'yogurt for Ishaan'), call add at once with every item and read back the result "
    "(no need to ask first; each has a remove button on the page). Each item: name (as they said it), count (packs "
    "or pieces, when said), who (a household member's name, 'me' or 'everyone', when said), use_by (YYYY-MM-DD, when "
    "they gave a date or day) or within_days ('use within 2 days'). Shopping-list items they match are ticked off. "
    "Nutrition comes from USDA's closest basic food on the server; you never see the kitchen, its verdicts or "
    "numbers, so for those point them to Food -> Groceries. Not for things still to buy (that's the shopping list: "
    "add_task with list shopping).",
    "input_schema": {"type": "object", "properties": {
        "action": {"type": "string", "enum": ["add"]},
        "items": {"type": "array", "items": {"type": "object", "properties": {
            "name": {"type": "string"}, "count": {"type": "integer"}, "who": {"type": "string"},
            "use_by": {"type": "string"}, "within_days": {"type": "integer"}},
            "required": ["name"], "additionalProperties": False}}},
        "required": ["action", "items"], "additionalProperties": False},
}


def run_tool(memory: Memory, args: dict) -> str:
    if args.get("action") != "add":
        raise ToolError("Use add.")
    items = args.get("items") if isinstance(args.get("items"), list) else []
    return said(add_by_name(memory, [i for i in items if isinstance(i, dict)]))
