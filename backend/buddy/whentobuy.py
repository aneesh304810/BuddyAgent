"""When to buy: buy now, or wait, with the chance of a better price, the price worth waiting for and roughly when.

Worked out in code (no AI) from:
  the price history   every price Buddy has seen for the item (price watches, note_price, Keepa's 90-day average
                      or the lowest price when Claude passes them in)
  the sales calendar  the US sales that matter for this kind of thing (Black Friday and Cyber Monday, Prime Day and
                      Prime Big Deal Days, Presidents', Memorial, July 4th and Labor Day for appliances and mattresses,
                      back to school for laptops, end-of-season clearance for clothes, the Super Bowl for TVs...),
                      with the rough discount each usually brings. These ranges are rules of thumb from past years,
                      not promises: the track record below shows how well they hold for this owner.
  product cycles      a new model due soon usually pushes the old one down
  the owner's profile how long they'll wait and the discount that makes them buy (shopping_profile)

Every call is kept as a prediction. When its date passes, Buddy checks what really happened (the prices it saw
afterwards, or what the owner paid in the order email) and keeps a track record: how often "wait" found a better
price, and how often "buy now" was right. Flights and hotels are left to the travel tools, which know the route's
own patterns.
"""

import json
import re
from datetime import date, datetime, timedelta, timezone

from .memory import Memory
from .tools import ToolError, local_now

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS buy_predictions (
    id INTEGER PRIMARY KEY,
    what TEXT NOT NULL,
    kind TEXT NOT NULL,
    made_on TEXT NOT NULL,
    price REAL NOT NULL,
    decision TEXT NOT NULL,          -- buy_now | wait
    chance REAL NOT NULL,            -- of a price at or under `target` by `by_date`
    target REAL NOT NULL,            -- the price worth waiting for
    event TEXT NOT NULL DEFAULT '',  -- the sale or launch it expects
    by_date TEXT NOT NULL,
    outcome TEXT NOT NULL DEFAULT '',  -- '' open | right | wrong | unknown
    best_seen REAL,                  -- the lowest price seen (or paid) after the call, until by_date
    settled_on TEXT
);
"""

DAYS = 86400


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    """The n-th weekday (0 = Monday) of a month; n = -1 for the last."""
    if n > 0:
        d = date(year, month, 1)
        d += timedelta(days=(weekday - d.weekday()) % 7)
        return d + timedelta(weeks=n - 1)
    d = date(year + (month == 12), month % 12 + 1, 1) - timedelta(days=1)
    return d - timedelta(days=(d.weekday() - weekday) % 7)


def calendar(year: int) -> list[dict]:
    """The year's sales: name, first and last day, the kinds they matter for, the usual discount range (%)."""
    thanksgiving = _nth_weekday(year, 11, 3, 4)
    july_mid = _nth_weekday(year, 7, 1, 2)       # Prime Day: usually the second week of July
    october = _nth_weekday(year, 10, 1, 2)       # Prime Big Deal Days: usually the second week of October
    super_bowl = _nth_weekday(year, 2, 6, 2)
    ev = [
        ("After-Christmas sales", date(year, 12, 26), date(year, 12, 31), "electronics clothing home", (15, 40)),
        ("Winter clearance", date(year, 1, 2), date(year, 1, 31), "clothing", (40, 70)),
        ("Super Bowl TV deals", super_bowl - timedelta(days=14), super_bowl, "tv", (15, 30)),
        ("Presidents' Day sales", _nth_weekday(year, 2, 0, 3) - timedelta(days=4), _nth_weekday(year, 2, 0, 3),
         "appliance mattress home", (15, 30)),
        ("Memorial Day sales", _nth_weekday(year, 5, 0, -1) - timedelta(days=4), _nth_weekday(year, 5, 0, -1),
         "appliance mattress home grill", (15, 35)),
        ("Prime Day", july_mid, july_mid + timedelta(days=1), "electronics home", (15, 30)),
        ("July 4th sales", date(year, 7, 1), date(year, 7, 4), "appliance mattress home grill", (15, 30)),
        ("Back-to-school sales", date(year, 7, 20), date(year, 8, 31), "laptop electronics clothing", (10, 20)),
        ("Summer clearance", date(year, 7, 15), date(year, 8, 31), "clothing", (40, 70)),
        ("Labor Day sales", _nth_weekday(year, 9, 0, 1) - timedelta(days=4), _nth_weekday(year, 9, 0, 1),
         "appliance mattress home", (15, 35)),
        ("Prime Big Deal Days", october, october + timedelta(days=1), "electronics home", (10, 25)),
        ("Black Friday to Cyber Monday", thanksgiving + timedelta(days=1), thanksgiving + timedelta(days=4),
         "electronics home clothing appliance tv laptop mattress", (20, 40)),
    ]
    return [{"name": n, "start": s, "end": e, "for": set(f.split()), "discount": d} for n, s, e, f, d in ev]


TAGS = {"tv": r"\btv\b|television|oled|qled", "laptop": r"laptop|macbook|chromebook|notebook",
        "appliance": r"washer|dryer|dishwasher|fridge|refrigerator|oven|range|microwave|vacuum|appliance",
        "mattress": r"mattress", "grill": r"grill|smoker"}


def tags_for(what: str, kind: str) -> set[str]:
    tags = {kind} if kind else set()
    tags |= {t for t, rx in TAGS.items() if re.search(rx, what or "", re.I)}
    return tags


def next_sales(what: str, kind: str, today: date, within_days: int) -> list[dict]:
    """The sales that matter for this item, starting within `within_days` (or running now)."""
    tags = tags_for(what, kind)
    out = []
    for year in (today.year, today.year + 1):
        for e in calendar(year):
            if e["for"] & tags and e["end"] >= today and (e["start"] - today).days <= within_days:
                out.append(e)
    return sorted(out, key=lambda e: e["start"])


def _ready(memory: Memory) -> None:
    memory.db.executescript(SCHEMA_SQL)


def _history(memory: Memory, what: str, days: int = 365) -> list[float]:
    try:
        from .prices import _key  # noqa: PLC0415
        since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        return [r["price"] for r in memory.db.execute(
            "SELECT price FROM price_seen WHERE what = ? AND seen_at >= ? ORDER BY seen_at", (_key(what), since))]
    except Exception:
        return []


def _patience(memory: Memory, kind: str) -> tuple[int, float | None]:
    """(days the owner will wait, the discount % that makes them buy), from their profile; defaults if unknown."""
    from .shopper import KINDS, profile  # noqa: PLC0415
    texts, target = [], None
    for k in ("general", kind) if kind in KINDS else ("general",):
        prof, _ = profile(memory, k)
        texts.append(str(prof.get("patience", "")))
        target = prof.get("target_discount_pct", target)
    text = " ".join(texts).lower()
    days = 45
    if re.search(r"month|patient|wait for (a|the) (sale|deal)|black friday|holiday", text):
        days = 120
    if re.search(r"week", text):
        days = 30
    if re.search(r"right away|immediately|now|don'?t wait|never wait", text):
        days = 7
    m = re.search(r"(\d+)\s*(day|week|month)", text)
    if m:
        days = int(m.group(1)) * {"day": 1, "week": 7, "month": 30}[m.group(2)]
    return max(3, min(days, 240)), (float(target) if target else None)


def advise(memory: Memory, what: str, price: float, kind: str = "", lowest: float | None = None,
           average: float | None = None, new_model: str = "", today: date | None = None, save: bool = True) -> dict:
    """Buy now or wait for `what` at `price`: the chance of a better price within the owner's patience, the
    price worth waiting for, the sale or launch to wait for, and why."""
    what = " ".join(str(what or "").split())[:200]
    if not what or not price or price <= 0:
        raise ToolError("Give the item and its price now.")
    kind = kind if kind in ("electronics", "home", "clothing", "other") else ""
    if kind == "" and re.search(r"flight|hotel|airbnb|fare", what, re.I):
        raise ToolError("Flights and hotels: use the travel tools, which know the route's own price patterns.")
    today = today or local_now().date()
    days, target_pct = _patience(memory, kind)
    hist = _history(memory, what)
    low = min([p for p in [lowest, *hist] if p] or [0]) or None
    avg = average or (sum(hist) / len(hist) if len(hist) >= 3 else None)
    reasons, chance, expected_drop = [], 0.15, 0.0

    sales = next_sales(what, kind or "electronics", today, days)
    event = ""
    by_date = today + timedelta(days=days)
    if sales:
        s = sales[0]
        lo, hi = s["discount"]
        running = s["start"] <= today
        event = s["name"]
        by_date = s["end"]
        if running:
            reasons.append(f"{s['name']} is on now (usually {lo}–{hi}% off for this kind of thing)")
        else:
            wait_days = (s["start"] - today).days
            reasons.append(f"{s['name']} starts in {wait_days} days ({s['start']:%b %d}); it usually takes "
                           f"{lo}–{hi}% off this kind of thing")
            chance += 0.45 if wait_days <= days else 0.2
            expected_drop = max(expected_drop, (lo + hi) / 2 / 100)
    if new_model:
        try:
            launch = date.fromisoformat(new_model[:10])
            if 0 <= (launch - today).days <= days + 30:
                reasons.append(f"a new model is due around {launch:%b %d}: the current one usually drops 15–30% "
                               "after the launch")
                chance += 0.25
                expected_drop = max(expected_drop, 0.2)
                if launch + timedelta(days=21) > by_date:
                    by_date, event = launch + timedelta(days=21), event or "the new model's launch"
        except ValueError:
            pass
    if low:
        gap = (price - low) / price
        if gap <= 0.03:
            reasons.append(f"${price:,.2f} is at or near the lowest price seen (${low:,.2f})")
            chance -= 0.2
        elif gap >= 0.10:
            reasons.append(f"it has been ${low:,.2f} before, {gap:.0%} lower than now")
            chance += 0.15
            expected_drop = max(expected_drop, gap * 0.8)
    if avg:
        if price <= avg * 0.9:
            reasons.append(f"${price:,.2f} is already well under its usual ${avg:,.2f}")
            chance -= 0.15
        elif price >= avg * 1.05:
            reasons.append(f"${price:,.2f} is above its usual ${avg:,.2f}")
            chance += 0.1
    if not (hist or lowest or average):
        reasons.append("Buddy has no price history for it yet (a price watch would start one)")
    chance = round(max(0.05, min(chance, 0.85)), 2)
    want = target_pct / 100 if target_pct else max(expected_drop, 0.1)
    target = round(price * (1 - want), 2)
    decision = "wait" if chance >= 0.5 and expected_drop >= min(want, 0.1) else "buy_now"
    if target_pct and expected_drop and expected_drop < target_pct / 100 * 0.6:
        reasons.append(f"the usual drop is smaller than the {target_pct:g}% you wait for")
    out = {"what": what, "price_now": price, "decision": decision, "chance_of_better_price": chance,
           "price_worth_waiting_for": target, "expect_around": round(price * (1 - expected_drop), 2)
           if expected_drop else None, "wait_until": by_date.isoformat(), "for": event, "your_patience_days": days,
           "reasons": reasons,
           "note": "Rough odds from the sales calendar, product cycles and the prices Buddy has seen, not a promise. "
                   "Offer a price watch at the target so Buddy sees what happens. Buddy never buys."}
    if save:
        _ready(memory)
        out["prediction_id"] = memory.db.execute(
            "INSERT INTO buy_predictions (what, kind, made_on, price, decision, chance, target, event, by_date) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (what, kind or "other", today.isoformat(), price, decision, chance, target, event,
             by_date.isoformat())).lastrowid
        memory.db.commit()
    return out


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]{3,}", (text or "").lower())} - {"the", "and", "for", "with", "new"}


def settle(memory: Memory, today: date | None = None) -> int:
    """Close the predictions whose date has passed: right or wrong from the prices seen or paid since."""
    _ready(memory)
    today = today or local_now().date()
    done = 0
    for p in memory.db.execute("SELECT * FROM buy_predictions WHERE outcome = '' AND by_date < ?",
                               (today.isoformat(),)).fetchall():
        seen = []
        try:
            from .prices import _key  # noqa: PLC0415
            seen += [r["price"] for r in memory.db.execute(
                "SELECT price FROM price_seen WHERE what = ? AND seen_at > ? AND seen_at <= ?",
                (_key(p["what"]), p["made_on"], p["by_date"] + "T23:59:59"))]
        except Exception:
            pass
        try:  # what they paid, from an order email for the same thing
            want = _words(p["what"])
            for o in memory.db.execute("SELECT item, amount FROM shop_purchases WHERE event = 'order' AND day > ? "
                                       "AND day <= ? AND amount IS NOT NULL", (p["made_on"], p["by_date"])):
                got = _words(o["item"])
                if want and len(want & got) >= max(1, min(2, len(want))):
                    seen.append(o["amount"])
        except Exception:
            pass
        if not seen:
            outcome, best = "unknown", None
        else:
            best = min(seen)
            if p["decision"] == "wait":
                # right when waiting paid off: the target reached, or at least 10% saved
                outcome = "right" if best <= max(p["target"] * 1.03, p["price"] * 0.9) else "wrong"
            else:
                outcome = "wrong" if best <= p["price"] * 0.92 else "right"
        memory.db.execute("UPDATE buy_predictions SET outcome = ?, best_seen = ?, settled_on = ? WHERE id = ?",
                          (outcome, best, today.isoformat(), p["id"]))
        done += 1
    memory.db.commit()
    return done


def track_record(memory: Memory) -> dict:
    _ready(memory)
    rows = [dict(r) for r in memory.db.execute("SELECT * FROM buy_predictions ORDER BY id DESC")]
    judged = [r for r in rows if r["outcome"] in ("right", "wrong")]

    def rate(sub):
        return f"{sum(r['outcome'] == 'right' for r in sub)} of {len(sub)}" if sub else "none judged yet"
    return {"calls": len(rows), "open": sum(r["outcome"] == "" for r in rows),
            "no_data": sum(r["outcome"] == "unknown" for r in rows),
            "right_overall": rate(judged),
            "wait_calls_right": rate([r for r in judged if r["decision"] == "wait"]),
            "buy_now_calls_right": rate([r for r in judged if r["decision"] == "buy_now"]),
            "recent": [f"{r['made_on']}: {r['what']} at ${r['price']:,.2f}: "
                       f"{'wait for ' + format(r['target'], ',.2f') if r['decision'] == 'wait' else 'buy now'}"
                       + (f" → {r['outcome']}" + (f" (best seen ${r['best_seen']:,.2f})" if r["best_seen"] else "")
                          if r["outcome"] else f" (open until {r['by_date']})") for r in rows[:6]],
            "note": "A call counts as judged only when Buddy saw a price afterwards (a price watch or the order "
                    "email). 'unknown' means it never saw one."}


TOOL = {
    "name": "when_to_buy",
    "description": (
        "Buy now or wait, for a product at today's price (not flights or hotels: the travel tools do those). "
        "advise: what, price (now), kind (electronics, home, clothing, other), optionally lowest and average "
        "(e.g. from the amazon tool's Keepa 90-day average) and new_model (YYYY-MM-DD if a newer model is due). "
        "Gives the decision, the chance of a better price within the owner's patience, the price worth waiting "
        "for, the sale or launch to wait for and until when, and why; it's kept as a prediction and checked later. "
        "After a 'wait', offer watch_price at that target (it asks first). Use it after a shop_for report, or "
        "whenever they ask 'should I buy this now?'. track_record: how Buddy's past calls turned out. "
        "calendar: the coming sales for a kind of thing. Rough odds, not promises; Buddy never buys."),
    "input_schema": {"type": "object", "properties": {
        "action": {"type": "string", "enum": ["advise", "track_record", "calendar"]},
        "what": {"type": "string"}, "price": {"type": "number"},
        "kind": {"type": "string", "enum": ["", "electronics", "home", "clothing", "other"]},
        "lowest": {"type": "number"}, "average": {"type": "number"}, "new_model": {"type": "string"}},
        "required": ["action"], "additionalProperties": False},
}


def run_tool(memory: Memory, args: dict) -> str:
    action = args.get("action")
    if action == "advise":
        return json.dumps(advise(memory, args.get("what", ""), float(args.get("price") or 0), args.get("kind", ""),
                                 args.get("lowest"), args.get("average"), args.get("new_model", "")),
                          ensure_ascii=False)
    if action == "track_record":
        settle(memory)
        return json.dumps(track_record(memory), ensure_ascii=False)
    if action == "calendar":
        today = local_now().date()
        sales = next_sales(args.get("what", ""), args.get("kind") or "electronics", today, 365)
        return json.dumps([{"sale": s["name"], "from": s["start"].isoformat(), "to": s["end"].isoformat(),
                            "usual_discount": f"{s['discount'][0]}–{s['discount'][1]}%"} for s in sales[:8]])
    raise ToolError("Use advise, track_record or calendar.")
