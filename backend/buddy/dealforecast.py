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
    out.sort(key=lambda f: f["when"])
    rhythm = store_rhythm(memory, kind)
    return {"what": what, "model": model, "kind": kind, "next_deals": out[:4], "how_deals_happen": rhythm,
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


def predictions(memory: Memory, days: int = 45, today: date | None = None) -> list[dict]:
    """Predicted deals, up front: every product with a deal rhythm (2+ deals seen), every company's next deal
    day, and the coming sales, soonest first."""
    from . import patterns, whentobuy  # noqa: PLC0415
    today = today or local_now().date()
    until = today + timedelta(days=days)
    learned = patterns.get(memory)
    out = []
    for model, v in (learned.get("item_deals") or {}).items():
        dates = sorted(date.fromisoformat(d) for d in v.get("dates", []))
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
    return sorted(out, key=lambda p: p["when"])[:60]


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
    for p in pending:
        cmp = (" — the lowest Buddy has seen" if p["lowest"] and p["price"] <= p["lowest"] * 1.001
               else f" (lowest seen ${p['lowest']:,.2f})" if p["lowest"] else "")
        text = (f"🔔 The deal you were watching for is here: {p['title']} for ${p['price']:,.2f}"
                + (f" at {p['store']}" if p["store"] else "") + f"{cmp}. Why: {p['reason']}. "
                "Ask me 'should I buy it now?' if you want a second opinion.")
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
