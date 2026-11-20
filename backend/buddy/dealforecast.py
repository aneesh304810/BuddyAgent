"""Why deals happen, when the next one is likely, and an alert when it comes.

  why       every deal gets a reason from its own words, its date and what Buddy knows (code, no AI): a holiday
            sale (Black Friday, Prime Day...), clearance or end of season, a new model coming or just out,
            a flash or limited-time deal, a coupon or promo code, a member or loyalty sale, the store's regular
            weekly sale, or just a price drop
  rhythm    the nightly learning in the sandbox (sandbox_jobs/price_patterns.py) finds, from the deals and prices:
            per item, the dates and depths of its deals (so "every ~45 days, usually 20% off" survives the 90-day
            deletion of raw data); per store and kind, how often deals come, on which weekday, how deep, and why
  forecast  the next likely deal for an item: its own rhythm (next = last deal + its usual gap), the store's
            rhythm for that kind, the coming sales (with the discounts really seen in past years) and its life
            cycle (a successor due, end of season); each with a date window, the expected price and the reason
  watch     "watch for it": an alert at a target price (the expected deal price unless the owner names one). The
            deals collector (mornings), the deals inbox (nights) and the daily price checks look for it; a match
            is told in the morning, outside quiet hours, with the reason and how it compares with the lowest seen
  weekly    on Monday mornings: the deals worth watching for in the next month, for what the owner wants and
            the kinds they like, with one line each on why

Buddy never buys: an alert says the deal is there, the owner decides.
"""

import json
import re
from datetime import date, datetime, timedelta, timezone

from .memory import Memory
from .tools import ToolError, local_now

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS deal_alerts (
    id INTEGER PRIMARY KEY,
    what TEXT NOT NULL,
    model TEXT NOT NULL,             -- the model key matched against deals and prices
    kind TEXT NOT NULL,
    target REAL,                     -- tell at or under this price ('' = any real deal)
    why TEXT NOT NULL DEFAULT '',    -- the forecast it came from
    until TEXT NOT NULL,             -- stops after this day
    created TEXT NOT NULL,
    last_told TEXT NOT NULL DEFAULT '',
    stopped INTEGER NOT NULL DEFAULT 0
);
"""
PENDING_KEY, WEEKLY_KEY = "dealalerts:pending", "dealalerts:weekly"

REASONS = (
    ("clearance", r"clearance|end of season|end-of-season|closeout|last chance|final sale|while supplies last|"
                  r"overstock|we made too much|outlet"),
    ("new model", r"previous[- ]gen|last[- ]gen|older model|\(\d{4}\)|new model|newly released|just launched|launch"),
    ("flash deal", r"flash|lightning|today only|24 hours|one day only|limited[- ]time|doorbuster|deal of the day|"
                   r"ends tonight|hours left"),
    ("coupon", r"coupon|promo code|use code|with code|code [A-Z0-9]{4,}|clip|extra \d+% off"),
    ("member sale", r"member|rewards|loyalty|prime exclusive|my best buy|circle|bonvoy|honors|plus members"),
    ("weekly sale", r"weekly ad|this week'?s|weekly deals|weekend sale|weekend deals"),
)
WORDS_SKIP = {"the", "and", "for", "with", "new", "free", "shipping", "off", "deal", "sale", "pack", "now", "only"}


def _ready(memory: Memory) -> None:
    memory.db.executescript(SCHEMA_SQL)


def reason_of(title: str, subject: str = "", day: date | None = None, kind: str = "") -> str:
    """Why this deal is happening, from its words and date (holiday sales by the calendar)."""
    text = f"{title} {subject}"
    from .whentobuy import calendar  # noqa: PLC0415
    day = day or local_now().date()
    for e in calendar(day.year):
        if e["start"] - timedelta(days=7) <= day <= e["end"] + timedelta(days=2) and (not kind or kind in e["for"]
                                                                                     or kind == "other"):
            return f"holiday sale: {e['name']}"
    if re.search(r"black friday|cyber monday|prime day|prime big deal|presidents|memorial day|labor day|"
                 r"4th of july|fourth of july|back to school|boxing day|after christmas|super bowl", text, re.I):
        return "holiday sale"
    for name, rx in REASONS:
        if re.search(rx, text, re.I):
            return name
    return "price drop"


def model_key(title: str) -> str:
    """The product, as the same words across stores and emails: the brand and the model codes ("sony wh-1000xm6"),
    else the first words."""
    words = re.findall(r"[A-Za-z0-9][A-Za-z0-9-]*", title or "")
    if not words:
        return ""
    codes = [w.lower() for w in words if re.search(r"\d", w) and re.search(r"[A-Za-z]", w) and len(w) >= 3]
    brand = words[0].lower() if words[0][:1].isupper() and not re.search(r"\d", words[0]) else ""
    if codes:
        return " ".join(([brand] if brand and brand not in codes else []) + codes[:2])
    return " ".join(w.lower() for w in words if w.lower() not in WORDS_SKIP)[:60].rsplit(" ", 1)[0] \
        if len(words) > 6 else " ".join(w.lower() for w in words if w.lower() not in WORDS_SKIP)


def _same(a: str, b: str) -> bool:
    wa, wb = set(a.split()), set(b.split())
    return bool(wa) and (wa <= wb or wb <= wa or len(wa & wb) >= max(2, min(len(wa), len(wb))))


# --- forecasting --------------------------------------------------------------------------------------------------
def forecast(memory: Memory, what: str, kind: str = "", price: float | None = None,
             today: date | None = None) -> dict:
    """The next likely deals for an item, soonest first: when, at about what price, and why."""
    from . import patterns, whentobuy  # noqa: PLC0415
    from .shopper import kind_of  # noqa: PLC0415
    today = today or local_now().date()
    kind = kind or kind_of(what)
    model = model_key(what)
    learned = patterns.get(memory)
    out = []
    # 1. the item's own rhythm
    item = next((v | {"key": k} for k, v in (learned.get("item_deals") or {}).items() if _same(model, k)), None)
    if item and len(item.get("dates", [])) >= 2:
        dates = sorted(date.fromisoformat(d) for d in item["dates"])
        gaps = sorted((b - a).days for a, b in zip(dates, dates[1:]) if (b - a).days >= 3)
        if gaps:
            gap = gaps[len(gaps) // 2]
            nxt = dates[-1] + timedelta(days=gap)
            while nxt < today:
                nxt += timedelta(days=gap)
            low = min(item.get("prices") or [0]) or None
            depth = sorted(item.get("depths") or [0])[len(item.get("depths") or [0]) // 2]
            out.append({"when": nxt.isoformat(), "window": [(nxt - timedelta(days=max(3, gap // 5))).isoformat(),
                                                            (nxt + timedelta(days=max(3, gap // 5))).isoformat()],
                        "expect_price": low, "depth": round(depth, 2) if depth else None,
                        "why": f"it's been on deal {len(dates)} times, about every {gap} days (last {dates[-1]:%b %d})"
                               + (f", usually around ${low:,.0f}" if low else ""),
                        "confidence": "medium" if len(dates) >= 3 else "low", "source": "its own deal history"})
    # 2. the coming sales, with what they really took off this kind
    seen = (learned.get("event_discounts") or {}).get(kind) or {}
    for e in whentobuy.next_sales(what, kind if kind != "other" else "electronics", today, 120)[:3]:
        real = seen.get(e["name"])
        lo, hi = (round(real["p25"] * 100), round(real["p75"] * 100)) if real and real.get("items", 0) >= 5 \
            else e["discount"]
        mid = (lo + hi) / 2 / 100
        out.append({"when": e["start"].isoformat(), "window": [e["start"].isoformat(), e["end"].isoformat()],
                    "expect_price": round(price * (1 - mid), 2) if price else None, "depth": round(mid, 2),
                    "why": f"{e['name']}: usually {lo}–{hi}% off {kind} "
                           + (f"(seen on {real['items']} items in your price book)" if real and real.get("items", 0) >= 5
                              else "(a rule of thumb until Buddy has seen one)"),
                    "confidence": "medium" if real else "low", "source": "sales calendar"})
    # 3. the product's life cycle: a successor coming pushes it down
    life = whentobuy.lifecycle(memory, what, price or 1.0, kind, today, today + timedelta(days=120))
    if life["stage"] in ("replaced",) or any("new model is due" in r for r in life["reasons"]):
        out.append({"when": today.isoformat(), "window": [today.isoformat(), (today + timedelta(days=45)).isoformat()],
                    "expect_price": round(price * 0.8, 2) if price else None, "depth": 0.2,
                    "why": life["reasons"][0] if life["reasons"] else "a newer model is out or coming",
                    "confidence": "medium", "source": "life cycle"})
    try:  # 4. the season for its category, from government price data
        from . import seasons  # noqa: PLC0415
        s = seasons.outlook(memory, what, kind, today, 180)
    except Exception:
        s = None
    if s and s["better_month_ahead"]:
        m = seasons.MONTHS.index(s["better_month_ahead"]) + 1
        first = date(today.year + (m < today.month), m, 1)
        out.append({"when": first.isoformat(), "window": [first.isoformat(), (first + timedelta(days=27)).isoformat()],
                    "expect_price": round(price * (1 - s["gain_pct"] / 100), 2) if price else None,
                    "depth": round(s["gain_pct"] / 100, 3), "why": seasons.reason(s), "confidence": "medium",
                    "source": "season (government data)"})
    out.sort(key=lambda f: f["when"])
    rhythm = store_rhythm(memory, kind)
    return {"what": what, "model": model, "kind": kind, "next_deals": out[:5], "how_deals_happen": rhythm,
            "note": "Learned from the deals and prices Buddy has seen (more history, better forecasts); rough, "
                    "not promises. Offer 'watch for it' to get an alert when one comes."}


def store_rhythm(memory: Memory, kind: str) -> dict:
    """How deals happen for a kind of product: why (the reasons' shares) and the stores' rhythm."""
    from . import patterns  # noqa: PLC0415
    learned = patterns.get(memory)
    reasons = (learned.get("deal_reasons") or {}).get(kind) or {}
    stores = {s: v for s, v in (learned.get("store_rhythm") or {}).items() if v.get("kind") == kind}
    top = sorted(stores.items(), key=lambda kv: -kv[1].get("deals", 0))[:4]
    return {"why_deals_happen": reasons,
            "stores": [{"store": s.split("|")[0], "deals": v["deals"], "every_days": v.get("every_days"),
                        "usual_weekday": v.get("weekday"), "usual_depth": v.get("depth")} for s, v in top]}


MONTHS = ("", "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def company(memory: Memory, name: str, today: date | None = None) -> dict:
    """A company's deal playbook (a store or a brand): how often it runs deals, on which weekday, how deep, why,
    in which months, on what; and when its next deal day is likely."""
    from . import patterns  # noqa: PLC0415
    today = today or local_now().date()
    want = (name or "").strip().lower()
    found = [v for k, v in (patterns.get(memory).get("companies") or {}).items()
             if want and (want in k.split(":", 1)[1] or k.split(":", 1)[1] in want)]
    if not found:
        return {"company": name, "note": "No pattern learned for it yet: Buddy needs to see at least 4 of its deals "
                                         "on 2 different days (feeds, the deals inbox, app notifications)."}
    out = []
    for v in sorted(found, key=lambda v: -v["deals"]):
        total = sum(v["reasons"].values()) or 1
        last = date.fromisoformat(v["deal_days"][-1])
        gap = max(1, round(v.get("every_days") or 7))
        nxt = last + timedelta(days=gap)
        while nxt < today:
            nxt += timedelta(days=gap)
        busy = sorted(v["months"].items(), key=lambda kv: -kv[1])[:3]
        out.append({"company": v["name"], "as": v["role"], "deals_seen": v["deals"], "deal_days_seen": len(v["deal_days"]),
                    "runs_deals_every_days": v.get("every_days"), "usual_weekday": v.get("weekday"),
                    "usual_depth": v.get("depth"),
                    "why": {r: round(n / total, 2) for r, n in sorted(v["reasons"].items(), key=lambda kv: -kv[1])},
                    "busiest_months": [MONTHS[int(m)] for m, _ in busy], "mostly": list(v["kinds"]),
                    "next_deal_day_likely": nxt.isoformat(), "last_deal_day": last.isoformat()})
    return {"company": name, "playbooks": out,
            "note": "Learned from the deals Buddy has seen; more history makes it sharper. Rough, not a promise."}


# Days in the season that are not in the buy-or-wait calendar (whentobuy.calendar), because Buddy has no discount
# numbers for them and does not make any up. They are shown on the Predicted list as dates, and decide nothing.
def season(year: int) -> list[dict]:
    from .whentobuy import _nth_weekday  # noqa: PLC0415
    thanksgiving = _nth_weekday(year, 11, 3, 4)
    christmas = date(year, 12, 25)
    saturday = christmas - timedelta(days=(christmas.weekday() - 5) % 7 or 7)  # the last Saturday before the 25th
    return [
        {"name": "Travel Tuesday", "day": thanksgiving + timedelta(days=5), "for": "flights, hotels and cruises",
         "why": "the Tuesday after Thanksgiving: airlines, hotels and cruise lines run one-day sales. How big varies; "
                "Buddy has no numbers of its own for it yet"},
        {"name": "Green Monday", "day": _nth_weekday(year, 12, 0, 2), "for": "online stores",
         "why": "the second Monday of December: online stores run a last round of sales in time for delivery by "
                "Christmas. Buddy has no numbers of its own for it yet"},
        {"name": "Super Saturday", "day": saturday, "for": "stores",
         "why": "the last Saturday before Christmas: stores cut prices for last-minute shopping. Buddy has no "
                "numbers of its own for it yet"},
    ]


MAX_PRODUCTS, MAX_BRANDS = 40, 20  # how many product and brand predictions the list carries (the surest first)


def _next(dates: list, today: date, least_gap: int = 3) -> tuple[int, date] | None:
    """A rhythm from the days something was on deal: the usual gap between them and the next day it points to.
    None with fewer than two days, or when they sit closer together than `least_gap` days (one sale seen twice)."""
    dates = sorted(set(dates))
    gaps = sorted((b - a).days for a, b in zip(dates, dates[1:]) if (b - a).days >= least_gap)
    if len(dates) < 2 or not gaps:
        return None
    gap = gaps[len(gaps) // 2]
    nxt = dates[-1] + timedelta(days=gap)
    while nxt < today:
        nxt += timedelta(days=gap)
    return gap, nxt


def _product_label(product: str) -> str:
    """ "sony wh-1000xm5" as people write it: "Sony WH-1000XM5"."""
    return " ".join(w.upper() if (re.search(r"\d", w) and re.search(r"[a-z]", w)) or len(w) <= 2 else w[:1].upper() + w[1:]
                    for w in str(product or "").split())


def from_book(memory: Memory, today: date, days: int = 45) -> dict:
    """Deal rhythms worked out here from the deals in the price book, with no sandbox: for each product on deal on
    two or more days (at least 3 days apart), its next likely deal day and the lowest deal price seen; and the same
    for a brand within a category ("Dewalt: power tool") when its deals do not come nearly every day. The nightly
    learning finds the same rhythms and keeps them after the raw deals are a year old; this is what the list shows
    until then, and whenever the learning has not run.

    Returns {"products": [...], "brands": [...], "steady": [brands with deals most days], "first_day": date|None}."""
    from . import pricebook  # noqa: PLC0415
    until = today + timedelta(days=days)
    pricebook.identify(memory)
    rows = pricebook.run(memory, "SELECT product, COALESCE(brand, '') AS brand, COALESCE(category, '') AS category, "
                                 "seen_on, MIN(price) AS price, COUNT(*) AS deals FROM deals WHERE "
                                 "COALESCE(domain, 'product') = 'product' AND product IS NOT NULL AND product <> '' AND "
                                 "seen_on >= ? GROUP BY 1, 2, 3, 4 ORDER BY seen_on",
                         ((today - timedelta(days=365)).isoformat(),))
    by_product: dict = {}
    by_brand: dict = {}
    first = None
    for r in rows:
        day = date.fromisoformat(str(r["seen_on"]))
        first = first or day
        p = by_product.setdefault(r["product"], {"days": [], "prices": [], "brand": r["brand"], "category": r["category"]})
        p["days"].append(day)
        if r["price"]:
            p["prices"].append(r["price"])
        if r["brand"]:
            b = by_brand.setdefault((r["brand"], r["category"]), {"days": set(), "products": set(), "deals": 0})
            b["days"].add(day)
            b["products"].add(r["product"])
            b["deals"] += int(r["deals"])
    products = []
    for name, v in by_product.items():
        rhythm = _next(v["days"], today)
        if not rhythm or rhythm[1] > until:
            continue
        seen = len(set(v["days"]))
        products.append({"when": rhythm[1].isoformat(), "what": _product_label(name), "type": "product",
                         "expect_price": min(v["prices"]) if v["prices"] else None,
                         "why": f"on deal on {seen} days, about every {rhythm[0]} days",
                         "confidence": "medium" if seen >= 3 else "low", "brand": v["brand"], "category": v["category"],
                         "_seen": seen})
    brands, steady = [], []
    for (brand, category), v in by_brand.items():
        if len(v["days"]) < 3 or len(v["products"]) < 2:
            continue
        rhythm = _next(sorted(v["days"]), today, least_gap=1)
        name = pricebook.group_label("product", "brand", brand)
        if not rhythm:
            continue
        if rhythm[0] < 5:  # deals most days: nothing to wait for, so it is not a prediction
            steady.append((len(v["days"]), f"{name} ({category})" if category else name))
            continue
        if rhythm[1] <= until:
            brands.append({"when": rhythm[1].isoformat(), "what": f"{name}: {category}" if category else name,
                           "type": "brand", "expect_price": None,
                           "why": f"deals on {len(v['days'])} days across {len(v['products'])} products, about every "
                                  f"{rhythm[0]} days",
                           "confidence": "medium" if len(v["days"]) >= 5 else "low", "brand": brand, "category": category,
                           "_seen": len(v["days"])})
    for found in (products, brands):
        found.sort(key=lambda p: -p["_seen"])
    return {"products": [{k: v for k, v in p.items() if k != "_seen"} for p in products[:MAX_PRODUCTS]],
            "brands": [{k: v for k, v in p.items() if k != "_seen"} for p in brands[:MAX_BRANDS]],
            "steady": [name for _, name in sorted(steady, reverse=True)[:12]], "first_day": first}


def basis(memory: Memory, today: date | None = None) -> dict:
    """What the Predicted list rests on, in numbers: how many deals of each kind the price book holds and over how
    many days, how many products, routes and hotel places were on deal on two or more days, whether the nightly
    learning has run, and the error when the price book could not be read. So a short list can say why it is short."""
    from . import patterns, pricebook  # noqa: PLC0415
    out = {"kinds": {}, "repeat": {}, "steady": [], "learning": "", "learned_on": memory.get(patterns.LEARNED_KEY) or "",
           "error": ""}
    try:
        for r in pricebook.run(memory, "SELECT COALESCE(domain, 'product') AS domain, COUNT(*) AS n, MIN(seen_on) AS "
                                       "first, MAX(seen_on) AS last, COUNT(DISTINCT seen_on) AS days FROM deals GROUP BY 1"):
            out["kinds"][r["domain"]] = {"deals": int(r["n"]), "days": int(r["days"]), "first": str(r["first"]),
                                         "last": str(r["last"])}
        for r in pricebook.run(memory, "SELECT domain, COUNT(*) AS n FROM (SELECT COALESCE(domain, 'product') AS domain, "
                                       "CASE WHEN COALESCE(domain, 'product') = 'product' THEN product ELSE item END AS "
                                       "name FROM deals GROUP BY 1, 2 HAVING COUNT(DISTINCT seen_on) >= 2 AND "
                                       "name IS NOT NULL AND name <> '') GROUP BY 1"):
            out["repeat"][r["domain"]] = int(r["n"])
    except Exception as e:
        out["error"] = f"The price book could not be read just now ({type(e).__name__}), so only the sales calendar is shown."
    try:
        out["learning"] = str((json.loads(memory.get(patterns.STATUS_KEY) or "{}") or {}).get("status", ""))[:200]
    except ValueError:
        pass
    return out


def predicted_page(memory: Memory, days: int = 45, today: date | None = None) -> dict:
    """The Deals page's Predicted tab: the predictions, and what they rest on."""
    today = today or local_now().date()
    found = basis(memory, today)
    try:
        found["steady"] = from_book(memory, today, days)["steady"]
    except Exception:
        pass
    return {"predictions": predictions(memory, days, today), "basis": found, "days": days}


def predictions(memory: Memory, days: int = 45, today: date | None = None) -> list[dict]:
    """Predicted deals, up front: every product with a deal rhythm (2+ deals seen), every brand's and company's
    next deal day, and the coming sales, soonest first. The rhythms come from the deals in the price book
    (from_book) and from what the nightly learning has kept of older ones."""
    from . import patterns, whentobuy  # noqa: PLC0415
    today = today or local_now().date()
    until = today + timedelta(days=days)
    learned = patterns.get(memory)
    out = []
    try:
        book = from_book(memory, today, days)
    except Exception:  # the price book may be new, or not readable just now: the calendar still shows
        import logging  # noqa: PLC0415
        logging.getLogger(__name__).info("deal rhythms from the price book failed", exc_info=True)
        book = {"products": [], "brands": [], "first_day": None}
    out += book["products"] + book["brands"]
    for model, v in (learned.get("item_deals") or {}).items():
        dates = sorted(date.fromisoformat(d) for d in v.get("dates", []))
        if book["first_day"] and dates and dates[-1] >= book["first_day"]:
            continue  # its deals are still in the price book: from_book has it, under its product's name
        gaps = sorted((b - a).days for a, b in zip(dates, dates[1:]) if (b - a).days >= 3)
        if len(dates) < 2 or not gaps:
            continue
        gap = gaps[len(gaps) // 2]
        nxt = dates[-1] + timedelta(days=gap)
        while nxt < today:
            nxt += timedelta(days=gap)
        if nxt <= until:
            low = min(p for p in v.get("prices", []) if p) if any(v.get("prices", [])) else None
            out.append({"when": nxt.isoformat(), "what": model, "type": "product", "expect_price": low,
                        "why": f"on deal {len(dates)} times, about every {gap} days"
                               + (f" (at {', '.join(v.get('stores', [])[:2])})" if v.get("stores") else ""),
                        "confidence": "medium" if len(dates) >= 3 else "low"})
    for v in (learned.get("companies") or {}).values():
        if len(v.get("deal_days", [])) < 3:
            continue
        gap = max(1, round(v.get("every_days") or 7))
        nxt = date.fromisoformat(v["deal_days"][-1]) + timedelta(days=gap)
        while nxt < today:
            nxt += timedelta(days=gap)
        if nxt <= until and gap >= 5:  # daily dealers aren't news
            out.append({"when": nxt.isoformat(), "what": f"{v['name']} ({v['role']})", "type": "company",
                        "expect_price": None,
                        "why": f"runs deals about every {gap} days, usually on {v.get('weekday', 'the same weekday')}"
                               + (f", around {v['depth']:.0%} off" if v.get("depth") else ""),
                        "confidence": "medium" if len(v["deal_days"]) >= 5 else "low"})
    seen = set()
    for kind in ("electronics", "home", "clothing"):
        for e in whentobuy.next_sales("drill tv laptop mattress grill mower" if kind == "home" else "", kind, today, days):
            if e["name"] not in seen:
                seen.add(e["name"])
                out.append({"when": max(e["start"], today).isoformat(), "what": e["name"], "type": "sale",
                            "expect_price": None, "why": f"usually {e['discount'][0]}–{e['discount'][1]}% off "
                                                         f"({', '.join(sorted(e['for']))})", "confidence": "calendar"})
    for year in (today.year, today.year + 1):
        for e in season(year):
            if today <= e["day"] <= until:
                out.append({"when": e["day"].isoformat(), "what": f"{e['name']} ({e['for']})", "type": "sale",
                            "expect_price": None, "why": e["why"], "confidence": "calendar"})
    try:
        out += travel(memory, learned, today, days)
    except Exception:  # the price book may be empty or new: the product predictions still show
        import logging  # noqa: PLC0415
        logging.getLogger(__name__).info("travel predictions failed", exc_info=True)
    return sorted(out, key=lambda p: p["when"])[:150]


MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
          "November", "December")
WINDOW_DAYS = {"0-7": (0, 7), "8-21": (8, 21), "22-60": (22, 60), "61-120": (61, 120), "121+": (121, 200)}
WINDOW_WORDS = {"0-7": "in the last week before departure", "8-21": "1 to 3 weeks before departure",
                "22-60": "3 weeks to 2 months before departure", "61-120": "2 to 4 months before departure",
                "121+": "more than 4 months before departure"}


def _name(domain: str, item: str) -> str:
    item = str(item)
    if domain == "flight":
        return "Flight " + (item.upper().replace("-", " to ") if re.fullmatch(r"[a-z]{3}-[a-z]{3}", item.lower()) else item)
    return "Hotels in " + item.replace("hotels:", "").title() if item.startswith("hotels:") else "Hotel: " + item.title()


def travel(memory: Memory, learned: dict, today: date, days: int = 45) -> list[dict]:
    """Flight and hotel predictions: the trips Buddy made a book-or-wait call on, the routes and places whose
    deals keep coming back, and what the learned price patterns say about when each is cheapest."""
    from . import pricebook  # noqa: PLC0415
    out, until = [], today + timedelta(days=days)
    # 1. open book-now-or-wait calls: a trip with a date
    seen_calls = set()
    try:
        calls = pricebook.run(memory, "SELECT COALESCE(domain, 'product') AS domain, item, price, decision, chance, "
                                      "target, by_date, travel_date FROM predictions WHERE outcome = '' AND domain IN "
                                      "('flight', 'hotel') AND by_date >= ? ORDER BY id DESC", (today.isoformat(),))
    except Exception:
        calls = []
    for c in calls:
        key = (c["domain"], c["item"], str(c["travel_date"] or ""))
        if key in seen_calls:
            continue
        seen_calls.add(key)
        trip = f", travelling {date.fromisoformat(str(c['travel_date'])):%b %d}" if c["travel_date"] else ""
        wait = c["decision"] == "wait"
        out.append({"when": str(c["by_date"]) if wait else today.isoformat(), "type": c["domain"],
                    "what": _name(c["domain"], c["item"]) + trip, "expect_price": c["target"] if wait else c["price"],
                    "why": (f"my call: wait. About a {c['chance']:.0%} chance it drops to the target by "
                            f"{date.fromisoformat(str(c['by_date'])):%b %d}; it was {c['price']:,.0f} when I looked"
                            if wait else f"my call: book now at about {c['price']:,.0f}. Only a {c['chance']:.0%} "
                                         "chance of a better price before the trip"),
                    "confidence": "medium" if abs(c["chance"] - 0.5) >= 0.2 else "low"})
    # 2. routes and places whose deals keep coming back (the deal feeds and the deals inbox)
    try:
        rows = pricebook.run(memory, "SELECT domain, item, seen_on, MIN(price) AS price FROM deals WHERE domain IN "
                                     "('flight', 'hotel') AND item != '' AND seen_on >= ? GROUP BY domain, item, seen_on "
                                     "ORDER BY seen_on", ((today - timedelta(days=365)).isoformat(),))
    except Exception:
        rows = []
    by: dict = {}
    for r in rows:
        by.setdefault((r["domain"], r["item"]), []).append((date.fromisoformat(str(r["seen_on"])), r["price"]))
    for (domain, item), seen in by.items():
        dates = [d for d, _ in seen]
        gaps = sorted((b - a).days for a, b in zip(dates, dates[1:]) if (b - a).days >= 3)
        prices = [p for _, p in seen if p]
        if len(dates) < 2 or not gaps:
            continue
        gap = gaps[len(gaps) // 2]
        nxt = dates[-1] + timedelta(days=gap)
        while nxt < today:
            nxt += timedelta(days=gap)
        if nxt <= until:
            out.append({"when": nxt.isoformat(), "type": domain, "what": _name(domain, item),
                        "expect_price": min(prices) if prices else None,
                        "why": f"on deal {len(dates)} times in your deal emails and feeds, about every {gap} days"
                               + (f"; lowest seen {min(prices):,.0f}" if prices else ""),
                        "confidence": "medium" if len(dates) >= 4 else "low"})
    # 3. what the learned price patterns say: the cheapest time to book a route, the cheapest month for a place
    for route, v in (learned.get("flights") or {}).items():
        window, ratios = v.get("cheapest_window") or "", v.get("by_days_before") or {}
        if window not in WINDOW_WORDS or len(ratios) < 2:
            continue
        saving = 1 - ratios.get(window, 1.0)
        out.append({"when": today.isoformat(), "type": "flight", "what": _name("flight", route) + " (any date)",
                    "expect_price": round(v["median"] * ratios[window], 0) if v.get("median") else None,
                    "why": f"usually cheapest {WINDOW_WORDS[window]}"
                           + (f", about {saving:.0%} under its usual fare" if saving >= 0.03 else "")
                           + f" (from {v.get('prices', 0)} prices seen)",
                    "confidence": "medium" if v.get("prices", 0) >= 60 else "low"})
    for place, v in (learned.get("hotels") or {}).items():
        months = {int(k): r for k, r in (v.get("by_month") or {}).items() if str(k).isdigit()}
        if len(months) < 2:
            continue
        best = min(months, key=months.get)
        start = date(today.year + (1 if best < today.month else 0), best, 1)
        extra = v.get("weekend_premium")
        out.append({"when": max(start, today).isoformat(), "type": "hotel", "what": _name("hotel", place),
                    "expect_price": round(v["median"] * months[best], 0) if v.get("median") else None,
                    "why": f"cheapest month is {MONTHS[best - 1]}, about {1 - months[best]:.0%} under the usual rate"
                           + (f"; Friday and Saturday nights cost about {extra:.0%} more" if extra and extra >= 0.05 else "")
                           + f" (from {v.get('prices', 0)} prices seen)",
                    "confidence": "medium" if v.get("prices", 0) >= 60 else "low"})
    return out


# --- alerts -------------------------------------------------------------------------------------------------------
def watch(memory: Memory, what: str, kind: str = "", target: float | None = None, price: float | None = None) -> dict:
    _ready(memory)
    what = " ".join(str(what or "").split())[:160]
    if not what:
        raise ToolError("What should I watch for?")
    f = forecast(memory, what, kind, price)
    soon = f["next_deals"][0] if f["next_deals"] else None
    if target is None and soon and soon.get("expect_price"):
        target = soon["expect_price"]
    until = max([date.fromisoformat(n["window"][1]) for n in f["next_deals"]] + [local_now().date()]) + timedelta(days=30)
    memory.db.execute("INSERT INTO deal_alerts (what, model, kind, target, why, until, created) VALUES (?, ?, ?, ?, ?, "
                      "?, ?)", (what, f["model"], f["kind"], target, soon["why"] if soon else "", until.isoformat(),
                                local_now().date().isoformat()))
    memory.db.commit()
    return {"watching": what, "target": target, "until": until.isoformat(),
            "expect": soon, "note": "I'll tell you in the morning when a deal or price for it shows up"
                                    + (f" at or under ${target:,.2f}" if target else "") + "."}


def alerts(memory: Memory) -> list[dict]:
    _ready(memory)
    return [dict(r) for r in memory.db.execute("SELECT id, what, target, until, why, last_told FROM deal_alerts "
                                               "WHERE stopped = 0 AND until >= ? ORDER BY id",
                                               (local_now().date().isoformat(),))]


def stop(memory: Memory, which: str) -> str:
    _ready(memory)
    rows = [a for a in alerts(memory) if str(which).strip() == str(a["id"]) or
            (which and which.lower() in a["what"].lower())]
    for a in rows:
        memory.db.execute("UPDATE deal_alerts SET stopped = 1 WHERE id = ?", (a["id"],))
    memory.db.commit()
    return f"Stopped {len(rows)} alert{'s' if len(rows) != 1 else ''}." if rows else "No alert by that name."


def check(memory: Memory, found: list[dict]) -> int:
    """Match new deals and prices (title, price, store, url, source) against the alerts; matches wait for the
    morning (kept in memory) and each alert tells at most once a day. Returns how many matched."""
    _ready(memory)
    today = local_now().date().isoformat()
    pending = json.loads(memory.get(PENDING_KEY) or "[]")
    told = 0
    for a in alerts(memory):
        if a["last_told"] == today:
            continue
        best = None
        for d in found:
            if not d.get("price") or not _same(model_key(a["what"]), model_key(d.get("title") or d.get("item") or "")):
                continue
            if a["target"] and d["price"] > a["target"] * 1.01:
                continue
            if best is None or d["price"] < best["price"]:
                best = d
        if best:
            from . import pricebook  # noqa: PLC0415
            low = pricebook.lowest(memory, best.get("item") or best["title"])
            pending.append({"alert": a["id"], "what": a["what"], "title": best["title"], "price": best["price"],
                            "store": best.get("store", ""), "url": best.get("url", ""), "source": best.get("source", ""),
                            "reason": best.get("reason") or reason_of(best["title"]), "lowest": low})
            memory.db.execute("UPDATE deal_alerts SET last_told = ? WHERE id = ?", (today, a["id"]))
            told += 1
    memory.db.commit()
    memory.set(PENDING_KEY, json.dumps(pending[-20:]))
    return told


def take_pending(memory: Memory) -> list[tuple[str, list[dict]]]:
    """The waiting alert messages (text, link cards), cleared once taken."""
    pending = json.loads(memory.get(PENDING_KEY) or "[]")
    memory.set(PENDING_KEY, "[]")
    out = []
    from . import recommend  # noqa: PLC0415  the owner's switches per category
    for p in pending:
        if not recommend.enabled(memory, recommend.category_of_item(p["title"])):
            continue
        cmp = (" — the lowest Buddy has seen" if p["lowest"] and p["price"] <= p["lowest"] * 1.001
               else f" (lowest seen ${p['lowest']:,.2f})" if p["lowest"] else "")
        text = (f"🔔 The deal you were watching for is here: {p['title']} for ${p['price']:,.2f}"
                + (f" at {p['store']}" if p["store"] else "") + f"{cmp}. Why: {p['reason']}. "
                "Ask me 'should I buy it now?' if you want a second opinion.")
        try:  # a card offer at that store, from the owner's card emails (dealmail.py)
            from . import dealmail  # noqa: PLC0415
            mine = dealmail.offers(memory, p["store"]) if p.get("store") else []
        except Exception:
            mine = []
        if mine:
            text += " You also have a card offer there: " + "; ".join(o["offer"] for o in mine[:2]) + "."
        cards = [{"kind": "link", "title": p["title"][:80], "subtitle": p["store"] or p["source"], "url": p["url"]}] \
            if str(p.get("url", "")).startswith("https://") else []
        out.append((text, cards))
    return out


def weekly_due(memory: Memory, now: datetime | None = None) -> bool:
    now = now or local_now()
    year, week, _ = now.isocalendar()
    return now.weekday() == 0 and now.hour >= 10 and memory.get(WEEKLY_KEY) != f"{year}-{week}"


def weekly(memory: Memory, today: date | None = None) -> str:
    """The deals worth watching for in the next 30 days, for what the owner wants and the kinds they like."""
    from . import dealcollector, whentobuy  # noqa: PLC0415
    today = today or local_now().date()
    lines = []
    for w in dealcollector.wanted(memory)[:6]:
        f = forecast(memory, w["name"], w.get("kind") or "", w.get("price"), today)
        soon = [n for n in f["next_deals"] if n["when"] <= (today + timedelta(days=30)).isoformat()]
        if soon:
            lines.append(f"{w['name']}: likely around {date.fromisoformat(soon[0]['when']):%b %d} — {soon[0]['why']}")
    kinds = dealcollector.likes(memory)["kinds"] or {"electronics"}
    events = {}
    for k in sorted(kinds):
        for e in whentobuy.next_sales("", k, today, 30):
            events.setdefault(e["name"], (e, set()))[1].add(k)
    for name, (e, ks) in events.items():
        lines.append(f"{name} ({e['start']:%b %d}–{e['end']:%b %d}): worth watching for {', '.join(sorted(ks))}")
    if not lines:
        return ""
    return ("📅 Deals to watch for this month: " + "; ".join(lines[:5])
            + ". Say 'watch for it' on any of them and I'll alert you when it comes.")


TOOL = {
    "name": "deal_forecast",
    "description": (
        "Why deals happen and when the next one is likely, with alerts. forecast: what (and kind, price now): the "
        "next likely deals soonest first (its own deal rhythm, the coming sales with the discounts really seen, its "
        "life cycle), each with a date window, expected price and why; plus how deals happen for that kind (the "
        "reasons' shares, the stores' rhythm: how often, which weekday, how deep). why: just the how-deals-happen "
        "part for a kind. watch: what, kind, target (optional; default the expected deal price): an alert, told in "
        "the morning when a matching deal or price appears in the feeds, the deals inbox or the daily price checks. "
        "alerts: the alerts set. stop: which (number or name). upcoming: the deals worth watching for in the next "
        "month. company: a store's or brand's deal playbook (company: 'Best Buy', 'Sony', 'Home Depot'): how "
        "often it runs deals, which weekday, how deep, why, its busiest months, and its next likely deal day. "
        "predictions: every predicted deal for the next days (default 45), up front: products with a deal rhythm, "
        "companies' next deal days, and the coming sales (also on the Deals page's Predicted tab). "
        "Rough forecasts from what Buddy has seen, not promises; Buddy never buys."),
    "input_schema": {"type": "object", "properties": {
        "action": {"type": "string", "enum": ["forecast", "why", "watch", "alerts", "stop", "upcoming", "company",
                                              "predictions"]},
        "company": {"type": "string"}, "days": {"type": "integer"},
        "what": {"type": "string"}, "kind": {"type": "string", "enum": ["", "electronics", "home", "clothing", "other"]},
        "price": {"type": "number"}, "target": {"type": "number"}, "which": {"type": "string"}},
        "required": ["action"], "additionalProperties": False},
}


def run_tool(memory: Memory, args: dict) -> str:
    action = args.get("action")
    if action == "forecast":
        return json.dumps(forecast(memory, args.get("what", ""), args.get("kind", ""), args.get("price")),
                          ensure_ascii=False)
    if action == "why":
        return json.dumps(store_rhythm(memory, args.get("kind") or "electronics"), ensure_ascii=False)
    if action == "watch":
        return json.dumps(watch(memory, args.get("what", ""), args.get("kind", ""), args.get("target"),
                                args.get("price")), ensure_ascii=False)
    if action == "alerts":
        return json.dumps(alerts(memory), ensure_ascii=False)
    if action == "stop":
        return stop(memory, str(args.get("which", "")))
    if action == "company":
        return json.dumps(company(memory, args.get("company") or args.get("what", "")), ensure_ascii=False)
    if action == "predictions":
        return json.dumps(predictions(memory, int(args.get("days") or 45)), ensure_ascii=False)
    if action == "upcoming":
        return weekly(memory) or "Nothing worth watching for in the next month yet."
    raise ToolError("Use forecast, why, watch, alerts, stop, upcoming, company or predictions.")
