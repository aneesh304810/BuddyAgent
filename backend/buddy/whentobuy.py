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
import logging
import re
from datetime import date, datetime, timedelta, timezone

from .memory import Memory
from .tools import ToolError, local_now

log = logging.getLogger(__name__)



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
        ("Spring Black Friday (Home Depot, Lowe's)", date(year, 4, 3), date(year, 4, 27),
         "tools garden grill home", (15, 35)),
        ("Father's Day tool deals", _nth_weekday(year, 6, 6, 3) - timedelta(days=14), _nth_weekday(year, 6, 6, 3),
         "tools grill", (15, 30)),
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
         "electronics home clothing appliance tv laptop mattress tools", (20, 40)),
    ]
    return [{"name": n, "start": s, "end": e, "for": set(f.split()), "discount": d} for n, s, e, f, d in ev]


TAGS = {"tv": r"\btv\b|television|oled|qled", "laptop": r"laptop|macbook|chromebook|notebook",
        "appliance": r"washer|dryer|dishwasher|fridge|refrigerator|oven|range|microwave|vacuum|appliance",
        "mattress": r"mattress", "grill": r"grill|smoker",
        "tools": r"drill|saw|sander|grinder|impact|wrench|tool|compressor|nailer|ladder|generator|dewalt|milwaukee|"
                 r"makita|ryobi|ridgid|kobalt|craftsman",
        "garden": r"mower|trimmer|blower|pressure washer|garden|patio|mulch|deck|fence"}


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
    from . import pricebook  # noqa: PLC0415  predictions live in the price book
    pricebook.conn(memory)


def _history(memory: Memory, what: str, days: int = 365) -> list[float]:
    from . import pricebook  # noqa: PLC0415
    return [r["price"] for r in pricebook.history(memory, what, "product", days)]


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


# --- product life cycles: new launches and old models ----------------------------------------------------------
AGE_BUCKETS = ((0, 60), (60, 180), (180, 365), (365, 730), (730, 100000))  # days since launch


def note_launch(memory: Memory, item: str, launch_date: str = "", launch_price=None, brand: str = "",
                kind: str = "", successor: str = "", successor_date: str = "", source: str = "") -> dict:
    """Keep a product's life cycle in the price book: launch date and price, and its successor (out or expected)."""
    from . import pricebook  # noqa: PLC0415
    item = pricebook.key(item)
    if not item:
        raise ToolError("Which product?")
    have = (pricebook.run(memory, "SELECT * FROM launches WHERE item = ?", (item,)) or [{}])[0]
    row = {"item": item, "brand": brand or have.get("brand") or "", "kind": kind or have.get("kind") or "other",
           "launch_date": pricebook._date(launch_date) or have.get("launch_date"),
           "launch_price": float(launch_price) if launch_price else have.get("launch_price"),
           "successor": successor or have.get("successor") or "",
           "successor_date": pricebook._date(successor_date) or have.get("successor_date"),
           "source": source or have.get("source") or "", "noted_on": local_now().date().isoformat()}
    pricebook.run(memory, "INSERT OR REPLACE INTO launches VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                  tuple(row[k] for k in ("item", "brand", "kind", "launch_date", "launch_price", "successor",
                                         "successor_date", "source", "noted_on")))
    return {k: str(v) if isinstance(v, date) else v for k, v in row.items()}


def _bucket(days: int) -> int:
    return next(i for i, (lo, hi) in enumerate(AGE_BUCKETS) if lo <= days < hi)


def curve(memory: Memory, kind: str) -> dict:
    """What the price book shows for this kind of product: the median price as a share of the launch price, by
    age since launch. Only ages backed by at least 3 different products count."""
    from statistics import median  # noqa: PLC0415
    from . import pricebook  # noqa: PLC0415
    rows = pricebook.run(memory, "SELECT o.item, o.price, CAST(o.seen_at AS DATE) - l.launch_date AS age, "
                                 "l.launch_price FROM observations o JOIN launches l ON l.item = o.item "
                                 "WHERE o.domain = 'product' AND l.kind = ? AND l.launch_price > 0 "
                                 "AND l.launch_date IS NOT NULL", (kind or "other",))
    by: dict[int, dict] = {}
    for r in rows:
        if r["age"] is None or r["age"] < 0:
            continue
        b = by.setdefault(_bucket(int(r["age"])), {"ratios": [], "items": set()})
        b["ratios"].append(r["price"] / r["launch_price"])
        b["items"].add(r["item"])
    return {i: {"share_of_launch_price": round(median(b["ratios"]), 3), "products": len(b["items"])}
            for i, b in sorted(by.items()) if len(b["items"]) >= 3}


def lifecycle(memory: Memory, what: str, price: float, kind: str, today: date, until: date,
              launch_date: str = "", launch_price=None, successor_date: str = "") -> dict:
    """Where the product is in its life (just launched, mature, old, replaced) and what that means for its price:
    {chance, drop, reasons, stage}. Rules of thumb, sharpened by the price book's own curve once it has data."""
    from . import pricebook  # noqa: PLC0415
    known = (pricebook.run(memory, "SELECT * FROM launches WHERE item = ?", (pricebook.key(what),)) or [{}])[0]
    launch = pricebook._date(launch_date) or (str(known["launch_date"]) if known.get("launch_date") else None)
    lprice = float(launch_price) if launch_price else known.get("launch_price")
    succ = pricebook._date(successor_date) or (str(known["successor_date"]) if known.get("successor_date") else None)
    out = {"chance": 0.0, "drop": 0.0, "reasons": [], "stage": ""}
    if succ and date.fromisoformat(succ) <= today:
        out["stage"] = "replaced"
        out["reasons"].append(f"its successor is already out ({succ}): the old model usually sells 15–30% off while "
                              "stock lasts, and the best colours and sizes go first")
        out["chance"] += 0.2
        out["drop"] = 0.2
    if launch:
        age = (today - date.fromisoformat(launch)).days
        if age < 60 and out["stage"] != "replaced":
            out["stage"] = "just launched"
            out["reasons"].append(f"it launched only {age} days ago: new products rarely drop much before their "
                                  "first big sale, and that first discount is usually small (around 10–15%)")
            out["chance"] -= 0.1
            out["drop"] = -0.15  # a cap: the first discount on a new product is small
        elif age >= 365 and not out["stage"]:
            out["stage"] = "mature"
            out["reasons"].append(f"it's been out {age // 30} months: older products get the deepest sale discounts")
            out["chance"] += 0.05
        elif not out["stage"]:
            out["stage"] = "early"
        if lprice:
            share = price / lprice
            if share <= 0.85:
                out["reasons"].append(f"${price:,.2f} is already {1 - share:.0%} under its launch price of "
                                      f"${lprice:,.2f}")
            bucket = _bucket(max(0, (until - date.fromisoformat(launch)).days))
            from . import patterns  # noqa: PLC0415
            later = ((patterns.get(memory).get("lifecycle") or {}).get(kind) or {}).get(str(bucket)) \
                or curve(memory, kind).get(bucket)
            if later:
                expect = lprice * later["share_of_launch_price"]
                months = (until - date.fromisoformat(launch)).days // 30
                if expect < price * 0.97:
                    out["reasons"].append(
                        f"in your price book, {kind} products this age ({later['products']} of them) sold for about "
                        f"{later['share_of_launch_price']:.0%} of launch price around month {months}: about "
                        f"${expect:,.0f} for this one")
                    out["chance"] += 0.1
                    out["drop"] = max(out["drop"], 1 - expect / price)
                else:
                    out["reasons"].append(f"in your price book, {kind} products this age didn't usually get cheaper "
                                          "than this")
                    out["chance"] -= 0.1
    return out


def advise(memory: Memory, what: str, price: float, kind: str = "", lowest: float | None = None,
           average: float | None = None, new_model: str = "", today: date | None = None, save: bool = True,
           launch_date: str = "", launch_price=None, successor_date: str = "") -> dict:
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
    parts, learned_w = {}, weights(memory, "product")

    def bump(name: str, delta: float) -> float:
        """A reason's push on the chance, times what that reason has earned from past calls (1.0 until learned);
        remembered with the call so it can be judged later."""
        parts[name] = round(parts.get(name, 0) + delta, 3)
        return delta * float((learned_w.get(name) or {}).get("weight", 1.0))

    sales = next_sales(what, kind or "electronics", today, days)
    event = ""
    by_date = today + timedelta(days=days)
    from . import patterns  # noqa: PLC0415  what the sandbox learned from real prices, preferred when enough items
    learned = patterns.get(memory)
    if sales:
        s = sales[0]
        lo, hi = s["discount"]
        seen = ((learned.get("event_discounts") or {}).get(kind or "electronics") or {}).get(s["name"])
        if seen and seen.get("items", 0) >= 5:
            lo, hi = round(max(seen["p25"], 0) * 100), round(max(seen["p75"], 0) * 100)
            s = {**s, "discount": (lo, hi), "learned": f"learned from {seen['items']} items in your price book"
                                                         f" ({', '.join(map(str, seen.get('years', [])))})"}
        running = s["start"] <= today
        event = s["name"]
        by_date = s["end"]
        if running:
            reasons.append(f"{s['name']} is on now (usually {lo}–{hi}% off for this kind of thing)")
        else:
            wait_days = (s["start"] - today).days
            reasons.append(f"{s['name']} starts in {wait_days} days ({s['start']:%b %d}); it usually takes "
                           f"{lo}–{hi}% off this kind of thing"
                           + (f" ({s['learned']})" if s.get("learned") else " (a rule of thumb until Buddy has "
                                                                             "learned it from real prices)"))
            chance += bump("sale_ahead", 0.45 if wait_days <= days else 0.2)
            expected_drop = max(expected_drop, (lo + hi) / 2 / 100)
    if new_model:
        try:
            launch = date.fromisoformat(new_model[:10])
            if 0 <= (launch - today).days <= days + 30:
                reasons.append(f"a new model is due around {launch:%b %d}: the current one usually drops 15–30% "
                               "after the launch")
                chance += bump("new_model", 0.25)
                expected_drop = max(expected_drop, 0.2)
                if launch + timedelta(days=21) > by_date:
                    by_date, event = launch + timedelta(days=21), event or "the new model's launch"
        except ValueError:
            pass
    life = lifecycle(memory, what, price, kind or "other", today, by_date, launch_date, launch_price,
                     successor_date)
    reasons += life["reasons"]
    chance += bump("life_cycle", life["chance"]) if life["chance"] else 0
    if life["drop"] < 0:  # just launched: the first discount is small
        expected_drop = min(expected_drop, -life["drop"]) if expected_drop else 0.0
    else:
        expected_drop = max(expected_drop, life["drop"])
    if low:
        gap = (price - low) / price
        if gap <= 0.03:
            reasons.append(f"${price:,.2f} is at or near the lowest price seen (${low:,.2f})")
            chance += bump("near_lowest", -0.2)
        elif gap >= 0.10:
            reasons.append(f"it has been ${low:,.2f} before, {gap:.0%} lower than now")
            chance += bump("was_lower", 0.15)
            expected_drop = max(expected_drop, gap * 0.8)
    if avg:
        if price <= avg * 0.9:
            reasons.append(f"${price:,.2f} is already well under its usual ${avg:,.2f}")
            chance += bump("under_usual", -0.15)
        elif price >= avg * 1.05:
            reasons.append(f"${price:,.2f} is above its usual ${avg:,.2f}")
            chance += bump("above_usual", 0.1)
    try:  # the season, from ten years of government price data for the category (seasons.py)
        from . import seasons  # noqa: PLC0415
        season = seasons.outlook(memory, what, kind, today, days)
    except Exception:
        season = None
    if season:
        reasons.append(seasons.reason(season))
        if season["better_month_ahead"]:
            chance += bump("season", 0.1)
            expected_drop = max(expected_drop, season["gain_pct"] / 100)
        elif season["now_vs_trend_pct"] <= -0.5 and not sales:
            chance += bump("season", -0.05)
    odds = (learned.get("drop_odds") or {}).get(kind or "electronics") or {}
    horizon = next((h for h in ("14", "30", "60") if int(h) >= days), "60") if odds else ""
    if horizon in odds and odds[horizon].get("items", 0) >= 5:
        real = odds[horizon]["chance"]
        chance = (chance + real) / 2
        reasons.append(f"in your price book, {kind or 'electronics'} prices fell 10% or more within {horizon} days "
                       f"{real:.0%} of the time ({odds[horizon]['items']} items)")
    from . import factors  # noqa: PLC0415  what Buddy's other sources know, kept with the call to learn from
    extra = factors.shopping(memory, what, kind, today, price)
    if not (hist or lowest or average):
        # Never seen before: judge it by how far the same brand's products of this kind (else the whole category)
        # have fallen below their usual price. A new reason, so it earns its weight from past calls like the rest.
        name, dip, n = next(((label, extra[f"f_{k}_dip"], extra[f"f_{k}_products"]) for k, label in
                             (("peers", "this brand's products of this kind"), ("group", "products of this kind"))
                             if extra.get(f"f_{k}_products", 0) >= 3), ("", 0.0, 0))
        if name:
            reasons.append(f"Buddy has not seen this one before, but {name} have dipped {dip:.0%} below their usual "
                           f"price on average ({int(n)} products in the price book)")
            if dip >= 0.15:
                chance += bump("peers", 0.1)
                expected_drop = max(expected_drop, dip * 0.6)
            elif dip <= 0.05:
                chance += bump("peers", -0.05)
        else:
            reasons.append("Buddy has no price history for it yet (a price watch would start one)")
    cal = (learned.get("calibration") or {}).get("wait") or {}
    if cal.get("calls", 0) >= 10 and cal["right"] < cal["odds_given"] - 0.15:
        chance -= 0.1
        reasons.append(f"my past 'wait' calls were right {cal['right']:.0%} of the time over {cal['calls']} calls, "
                       "so I'm more careful")
    try:  # a trained model, when one has passed its test on calls it never saw (modeltrain.py)
        from . import modeltrain  # noqa: PLC0415
        m = modeltrain.predict(memory, "product", features_of(parts, kind, extra))
    except Exception:
        m = None
    if m:
        chance = (chance + m["p"]) / 2
        reasons.append(f"my trained model puts the chance of a lower price at {m['p']:.0%} (on {m['tested_on']} "
                       f"calls it had never seen, the ones it liked were right {m['right_when_liked']:.0%} of the time)")
    chance = round(max(0.05, min(chance, 0.85)), 2)
    want = target_pct / 100 if target_pct else max(expected_drop, 0.1)
    target = round(price * (1 - want), 2)
    decision = "wait" if chance >= 0.5 and expected_drop >= min(want, 0.1) else "buy_now"
    if target_pct and expected_drop and expected_drop < target_pct / 100 * 0.6:
        reasons.append(f"the usual drop is smaller than the {target_pct:g}% you wait for")
    out = {"what": what, "price_now": price, "decision": decision, "chance_of_better_price": chance,
           "price_worth_waiting_for": target, "expect_around": round(price * (1 - expected_drop), 2)
           if expected_drop else None, "wait_until": by_date.isoformat(), "for": event, "your_patience_days": days,
           "life_stage": life["stage"],
           "reasons": reasons,
           "note": "Rough odds from the sales calendar, product cycles and the prices Buddy has seen, not a promise. "
                   "Offer a price watch at the target so Buddy sees what happens. Buddy never buys."}
    if save:
        from . import pricebook  # noqa: PLC0415
        _ready(memory)
        out["prediction_id"] = pricebook.run(
            memory, "INSERT INTO predictions (item, kind, made_on, price, decision, chance, target, event, by_date, "
                    "parts, factors) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) RETURNING id",
            (what, kind or "other", today.isoformat(), price, decision, chance, target, event,
             by_date.isoformat(), json.dumps(parts), json.dumps(extra)))[0]["id"]
    return out


# --- learning from how the calls turned out ------------------------------------------------------------------
MIN_TO_LEARN = 15
DROPPED = 0.92  # "the price did come down": 8% or more under the price at the call


def _ready(memory: Memory) -> None:
    from . import pricebook  # noqa: PLC0415  it adds this table's newer columns once, when it opens
    pricebook.conn(memory)


def learn(memory: Memory, domain: str = "product") -> dict:
    """Re-weigh each reason from the calls it took part in whose outcome Buddy saw. A reason that pushed towards
    waiting was right when the price then fell 8% or more; one that pushed towards buying now was right when it
    didn't. With 15 such calls behind it, a reason's weight moves between 0.5 and 1.5."""
    from . import pricebook  # noqa: PLC0415
    _ready(memory)
    rows = pricebook.run(memory, "SELECT price, best_seen, parts FROM predictions WHERE COALESCE(domain, 'product') "
                                 "= ? AND outcome IN ('right', 'wrong') AND best_seen IS NOT NULL AND parts IS NOT "
                                 "NULL", (domain,))
    tally: dict[str, list[bool]] = {}
    for r in rows:
        fell = r["best_seen"] <= r["price"] * DROPPED
        for name, delta in (json.loads(r["parts"] or "{}")).items():
            if delta:
                tally.setdefault(name, []).append((delta > 0) == fell)
    out = {}
    for name, hits in sorted(tally.items()):
        n, right = len(hits), sum(hits)
        out[name] = ({"weight": round(max(0.5, min(1.5, 1 + (right / n - 0.5))), 2), "calls": n, "right": right}
                     if n >= MIN_TO_LEARN else {"weight": 1.0, "calls": n, "right": right,
                                                "note": f"needs {MIN_TO_LEARN} judged calls"})
    memory.set(f"whentobuy:weights:{domain}", json.dumps({"on": local_now().date().isoformat(), "parts": out}))
    return out


def features_of(parts: dict, kind: str = "", extra: dict | None = None) -> dict:
    """A call's features for training and for the model's odds: each reason's push, the kind of thing, and the
    shared factors saved with the call (factors.py)."""
    x = {f"reason_{k}": v for k, v in (parts or {}).items()}
    x.update({k: v for k, v in (extra or {}).items() if isinstance(v, (int, float))})
    for k in ("electronics", "home", "clothing"):
        x[f"kind_{k}"] = 1 if kind == k else 0
    return x


def weights(memory: Memory, domain: str = "product") -> dict:
    """{reason: {"weight", "calls", "right"}}: what each reason has earned so far (missing = 1.0)."""
    try:
        return json.loads(memory.get(f"whentobuy:weights:{domain}") or "{}").get("parts") or {}
    except ValueError:
        return {}


HOTEL_EVERY_DAYS, HOTELS_A_DAY = 3, 3
NOT_A_NAME = {"hotel", "hotels", "the", "inn", "resort", "suites", "and", "by", "at", "in"}


def _hotel_rate(offers: list[dict], item: str, place: str) -> float | None:
    """The nightly rate that answers a hotel call: the named hotel's when the call names one, else the place's
    cheapest."""
    named = {w for w in re.findall(r"[a-z0-9]{3,}", item.lower())} - NOT_A_NAME - set(
        re.findall(r"[a-z0-9]{3,}", place.lower()))
    rates = [o["per_night"] for o in offers if o.get("per_night")
             and (not named or named <= set(re.findall(r"[a-z0-9]{3,}", str(o.get("name", "")).lower())))]
    return min(rates) if rates else None


def follow_hotels(memory: Memory, env=None, search=None, today: date | None = None) -> int:
    """Open hotel calls get their rate re-checked every three days (Google Hotels through SerpApi, three searches
    a day at most), so they get an outcome. `search(place, check_in, check_out)` -> offers. Returns the checks."""
    from . import pricebook  # noqa: PLC0415
    _ready(memory)
    seen_at = datetime(today.year, today.month, today.day, 12, tzinfo=timezone.utc) if today else None  # now
    today = today or local_now().date()
    if search is None:
        from .travel import Travel  # noqa: PLC0415
        trips = Travel(memory, env)
        if not trips.google.configured():
            return 0
        search = lambda place, a, b: trips.google.hotels(  # noqa: E731
            {"place": place, "check_in": a, "check_out": b, "adults": 2, "currency": "USD"}, {})[0]
    done = 0
    for p in pricebook.run(memory, "SELECT id, item, event, travel_date, nights FROM predictions WHERE domain = "
                                   "'hotel' AND outcome = '' AND by_date >= ? AND travel_date > ? ORDER BY id",
                           (today.isoformat(), today.isoformat())):
        key = f"whentobuy:hotel_checked:{p['id']}"
        last = memory.get(key)
        if done >= HOTELS_A_DAY or (last and (today - date.fromisoformat(last)).days < HOTEL_EVERY_DAYS):
            continue
        memory.set(key, today.isoformat())
        check_in = date.fromisoformat(str(p["travel_date"]))
        try:
            offers = search(p["event"] or p["item"], check_in.isoformat(),
                            (check_in + timedelta(days=max(1, p["nights"] or 1))).isoformat())
        except Exception as e:
            log.info("following hotel call %s: %s", p["id"], e)
            continue
        done += 1
        rate = _hotel_rate(offers, p["item"], p["event"] or "")
        if rate:
            pricebook.observe(memory, "hotel", p["item"], rate, source="call follow-up", travel_date=check_in,
                              seen_at=seen_at)
    return done


def follow_open(memory: Memory, env=None, fetch=None, budget: int = 15) -> int:
    """So that every call gets an outcome: the products with an open call get a price check today (Google
    Shopping, about $0.001 each, the oldest-checked first), the routes with an open flight call are followed
    through Travelpayouts (free), and open hotel calls are re-checked every three days (follow_hotels). Returns
    how many products were checked."""
    try:
        follow_hotels(memory, env)
    except Exception:
        log.info("following hotel calls failed", exc_info=True)
    from . import dealcollector, gateway, prices, pricebook  # noqa: PLC0415
    _ready(memory)
    today = local_now().date().isoformat()
    open_ = pricebook.run(memory, "SELECT id, COALESCE(domain, 'product') AS domain, item, travel_date FROM "
                                  "predictions WHERE outcome = '' AND by_date >= ? ORDER BY id", (today,))
    for p in open_:
        if p["domain"] == "flight" and re.fullmatch(r"[A-Za-z]{3}-[A-Za-z]{3}", p["item"]) and p["travel_date"]:
            try:
                from .travel import Travel  # noqa: PLC0415
                o, d = p["item"].upper().split("-")
                Travel(memory, env).watch(o, d, str(p["travel_date"])[:7])
            except Exception as e:
                log.info("following %s: %s", p["item"], e)
    products = [p for p in open_ if p["domain"] == "product"]
    if not products or not gateway._secret(env, "BUDDY_SERPER_KEY"):
        return 0
    start = int(memory.get("whentobuy:follow_at") or 0) % len(products)
    todo = (products[start:] + products[:start])[:budget]
    memory.set("whentobuy:follow_at", str((start + len(todo)) % len(products)))
    done = 0
    for p in todo:
        try:
            offers = [o for o in dealcollector.google_shopping(memory, p["item"], env, fetch)
                      if dealcollector._close(p["item"], o["title"])]
        except Exception as e:  # the budget, the network: try again tomorrow
            log.info("following calls stopped: %s", e)
            break
        done += 1
        if offers:
            best = min(offers, key=lambda o: o["price"])
            prices.record(memory, p["item"], best["price"], "USD", best["store"], best["url"], source="call follow-up")
    return done


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]{3,}", (text or "").lower())} - {"the", "and", "for", "with", "new"}


def settle(memory: Memory, today: date | None = None) -> int:
    """Close the predictions whose date has passed: right or wrong from the prices seen or paid since."""
    from . import pricebook  # noqa: PLC0415
    today = today or local_now().date()
    done = 0
    _ready(memory)
    for p in pricebook.run(memory, "SELECT * FROM predictions WHERE outcome = '' AND by_date < ?", (today.isoformat(),)):
        made, until = str(p["made_on"]), str(p["by_date"])
        domain = p.get("domain") or "product"
        same_trip = " AND travel_date = ?" if p.get("travel_date") else ""
        seen = [r["price"] for r in pricebook.run(
            memory, f"SELECT price FROM observations WHERE domain = ? AND item = ? AND seen_at > ? "
                    f"AND seen_at <= ?{same_trip}",
            (domain, pricebook.key(p["item"]), made, until + " 23:59:59",
             *([str(p["travel_date"])] if same_trip else [])))]
        try:  # what they paid, from an order email for the same thing (Buddy's personal database)
            want = _words(p["item"])
            for o in (memory.db.execute("SELECT item, amount FROM shop_purchases WHERE event = 'order' AND day > ? "
                                        "AND day <= ? AND amount IS NOT NULL", (made, until))
                      if domain == "product" else []):
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
        pricebook.run(memory, "UPDATE predictions SET outcome = ?, best_seen = ?, settled_on = ? WHERE id = ?",
                      (outcome, best, today.isoformat(), p["id"]))
        done += 1
    if done:  # what the newly judged calls teach about each reason
        for d in ("product", "flight", "hotel"):
            try:
                learn(memory, d)
            except Exception:
                log.info("learning from %s calls failed", d, exc_info=True)
    return done


def track_record(memory: Memory) -> dict:
    from . import pricebook  # noqa: PLC0415
    rows = [{**r, "made_on": str(r["made_on"]), "by_date": str(r["by_date"]), "what": r["item"]}
            for r in pricebook.run(memory, "SELECT * FROM predictions ORDER BY id DESC")]
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


def open_calls(memory: Memory, today: date | None = None, limit: int = 30) -> dict:
    """Buddy's open buy-now-or-wait calls for products, flights and hotels, newest first, each with what it is
    waiting for, and how the judged calls of its kind have turned out. For the "For you" view."""
    from . import pricebook  # noqa: PLC0415
    today = today or local_now().date()
    _ready(memory)
    rows = pricebook.run(memory, "SELECT id, COALESCE(domain, 'product') AS domain, item, kind, price, decision, chance, "
                                 "target, event, made_on, by_date, travel_date FROM predictions WHERE outcome = '' AND "
                                 "by_date >= ? ORDER BY made_on DESC, id DESC LIMIT ?", (today.isoformat(), limit))
    judged = pricebook.run(memory, "SELECT COALESCE(domain, 'product') AS domain, COUNT(*) AS n, SUM(CASE WHEN outcome = "
                                   "'right' THEN 1 ELSE 0 END) AS good FROM predictions WHERE outcome IN ('right', "
                                   "'wrong') GROUP BY 1")
    record = {r["domain"]: {"judged": int(r["n"]), "right": int(r["good"] or 0)} for r in judged}
    names = {"product": "Product", "flight": "Flight", "hotel": "Hotel"}
    calls = []
    for r in rows:
        try:
            calls.append(_call_card(r, record, names))
        except Exception:  # one odd row must not take the whole view down
            log.warning("a buy-or-wait call could not be shown (id %s)", r.get("id"), exc_info=True)
    return {"calls": calls, "record": record,
            "note": "Rough odds from the prices Buddy has seen, the sales calendar and the season, not a promise. "
                    "Buddy never buys or books."}


def _call_card(r: dict, record: dict, names: dict) -> dict:
    wait = r["decision"] == "wait"
    rec = record.get(r["domain"], {"judged": 0, "right": 0})
    target, price, chance = (v if isinstance(v, (int, float)) and v == v and abs(v) != float("inf") else None
                             for v in (r["target"], r["price"], r["chance"]))
    return ({
            "id": r["id"], "what": r["item"], "kind": names.get(r["domain"], "Product"), "domain": r["domain"],
            "price": price, "call": "Wait" if wait else ("Book now" if r["domain"] != "product" else "Buy now"),
            "wait": wait, "chance": chance,
            "why": ((f"Worth waiting for about ${target:,.0f}" if target is not None else "Worth waiting")
                    + (f", around {r['event']}" if r["event"] else "") + f". Buddy checks again by {r['by_date']}.") if wait
            else "Little chance of a better price soon, by what Buddy has seen.",
            "travel_date": str(r["travel_date"]) if r.get("travel_date") else "",
            "made_on": str(r["made_on"]), "recheck_by": str(r["by_date"]),
            "record": (f"{rec['right']} of {rec['judged']} past {names.get(r['domain'], 'product').lower()} calls were right"
                       if rec["judged"] else "No past calls of this kind judged yet"),
        })


TOOL = {
    "name": "when_to_buy",
    "description": (
        "Buy now or wait, for a product at today's price (not flights or hotels: the travel tools do those). "
        "advise: what, price (now; for an Amazon link or ASIN it's looked up, with Keepa's yearly average when "
        "connected), kind (electronics, home, clothing, other), optionally lowest and average "
        "(e.g. from the amazon tool's Keepa 90-day average) and new_model (YYYY-MM-DD if a newer model is due). "
        "Gives the decision, the chance of a better price within the owner's patience, the price worth waiting "
        "for, the sale or launch to wait for and until when, and why; it's kept as a prediction and checked later. "
        "After a 'wait', offer watch_price at that target (it asks first). Use it after a shop_for report, or "
        "whenever they ask 'should I buy this now?'. track_record: how Buddy's past calls turned out. "
        "calendar: the coming sales for a kind of thing. Product life cycles matter: pass launch_date and "
        "launch_price (MSRP) when known, and successor_date when a newer model is out or expected (research finds "
        "these); a just-launched product rarely drops much before its first big sale, a replaced one drops in "
        "clearance while stock lasts. note_launch: keep a product's launch date, launch price, brand, kind and "
        "successor in the price book. lifecycle: what the price book shows for a kind (price as a share of launch "
        "price by age). Rough odds, not promises; Buddy never buys."),
    "input_schema": {"type": "object", "properties": {
        "action": {"type": "string", "enum": ["advise", "track_record", "calendar", "note_launch", "lifecycle"]},
        "launch_date": {"type": "string"}, "launch_price": {"type": "number"}, "successor_date": {"type": "string"},
        "successor": {"type": "string"}, "brand": {"type": "string"},
        "what": {"type": "string"}, "price": {"type": "number"},
        "kind": {"type": "string", "enum": ["", "electronics", "home", "clothing", "other"]},
        "lowest": {"type": "number"}, "average": {"type": "number"}, "new_model": {"type": "string"}},
        "required": ["action"], "additionalProperties": False},
}


def run_tool(memory: Memory, args: dict) -> str:
    action = args.get("action")
    if action == "advise":
        what, price, average = args.get("what", ""), float(args.get("price") or 0), args.get("average")
        source = ""
        from . import amazon  # noqa: PLC0415  Amazon items: today's price and Keepa's averages, when connected
        if amazon.is_amazon(what) and (not price or not average):
            found = amazon.lookup(memory, what)
            if found:
                price = price or found["price"]
                average = average or found.get("avg365") or found.get("avg90")
                source = found["where"]
        if args.get("launch_date") or args.get("launch_price") or args.get("successor_date"):
            note_launch(memory, what, args.get("launch_date", ""), args.get("launch_price"),
                        kind=args.get("kind", ""), successor_date=args.get("successor_date") or args.get("new_model", ""),
                        source="research")
        got = advise(memory, what, price, args.get("kind", ""), args.get("lowest"), average, args.get("new_model", ""),
                     launch_date=args.get("launch_date", ""), launch_price=args.get("launch_price"),
                     successor_date=args.get("successor_date", ""))
        if source:
            got["price_from"] = source
        return json.dumps(got, ensure_ascii=False)
    if action == "note_launch":
        return json.dumps(note_launch(memory, args.get("what", ""), args.get("launch_date", ""),
                                      args.get("launch_price"), args.get("brand", ""), args.get("kind", ""),
                                      args.get("successor", ""), args.get("successor_date", ""), "owner or research"))
    if action == "lifecycle":
        return json.dumps({"kind": args.get("kind") or "other", "by_age_since_launch": {
            f"{AGE_BUCKETS[i][0]}–{AGE_BUCKETS[i][1] if AGE_BUCKETS[i][1] < 100000 else '+'} days": v
            for i, v in curve(memory, args.get("kind") or "other").items()},
            "note": "From the price book's own prices; ages with fewer than 3 products aren't shown."})
    if action == "track_record":
        settle(memory)
        return json.dumps(track_record(memory), ensure_ascii=False)
    if action == "calendar":
        today = local_now().date()
        sales = next_sales(args.get("what", ""), args.get("kind") or "electronics", today, 365)
        return json.dumps([{"sale": s["name"], "from": s["start"].isoformat(), "to": s["end"].isoformat(),
                            "usual_discount": f"{s['discount'][0]}–{s['discount'][1]}%"} for s in sales[:8]])
    raise ToolError("Use advise, track_record or calendar.")
