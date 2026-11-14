"""Buy and sell signals by category, each with its reasons and Buddy's own record, switched on per category.

  Categories (each on or off): electronics, home, tools, clothing, games, flights, hotels (shopping and travel:
  the deal alerts and buy-or-wait calls Buddy already makes), and etfs, stocks, commodities, options (investing).

  Investing signals, each weekday morning, from everything Buddy has on a symbol:
    attention  it got unusual attention on Reddit last night (signals.py: times its own usual), and the tone
    trend      the price against its 50-day average, and RSI (stretched or not), from daily prices
    facts      Finnhub, when its key is set: earnings coming, insiders buying or selling, analysts' ratings
  -> buy (attention up, tone and trend agree), sell or avoid (they agree the other way), caution (a crowd piling
     into something already stretched: usually late), or nothing. Code only: no AI decides a signal.

  Every signal is written down with the price that day and scored a week later (right: moved 2% or more the
  signal's way; wrong: 2% or more against). Until a category has 20 scored signals it says "unproven", with the
  count so far. Reddit attention is opinion, often late or wrong, and is never the only reason.

Signals only: Buddy never places, changes or cancels a trade, and this is not financial advice.
"""

import json
import logging
from datetime import date, datetime, timedelta

from . import signals
from .memory import Memory
from .tools import ToolError, local_now

log = logging.getLogger(__name__)

SETTINGS_KEY, DAY_KEY, PENDING_KEY = "recommend:categories", "recommend:day", "recommend:pending"
INVESTING = ("etfs", "stocks", "commodities", "options")
SHOPPING = ("electronics", "home", "tools", "clothing", "games", "flights", "hotels")
HOUR = 8
SCORE_AFTER_DAYS = 7
MOVE = 0.02
PROVEN_AFTER = 20
MAX_TOLD = 3
MIN_MENTIONS, SPIKE = 3, 3.0
NOTE = "A signal, not advice: you decide and trade yourself. Buddy never trades."


def _ready(memory: Memory) -> None:
    from . import pricebook  # noqa: PLC0415
    pricebook.run(memory, "CREATE SEQUENCE IF NOT EXISTS recs_id")
    pricebook.run(memory, "CREATE TABLE IF NOT EXISTS recs (id BIGINT PRIMARY KEY DEFAULT nextval('recs_id'), "
                          "day DATE, category VARCHAR, key VARCHAR, action VARCHAR, reasons VARCHAR, facts VARCHAR, "
                          "price_then DOUBLE, status VARCHAR DEFAULT 'open', scored_on DATE, price_after DOUBLE, "
                          "change DOUBLE)")


# --- the switches ---------------------------------------------------------------------------------------------
def categories(memory: Memory) -> dict:
    """{category: on}: everything on until the owner turns one off."""
    saved = json.loads(memory.get(SETTINGS_KEY) or "{}")
    return {c: bool(saved.get(c, True)) for c in signals.CATEGORIES}


def enabled(memory: Memory, category: str) -> bool:
    return categories(memory).get(category, True)


def switch(memory: Memory, which: list[str], on: bool) -> str:
    names = []
    for w in which:
        w = (w or "").strip().lower()
        names += list(INVESTING) if w == "investing" else list(SHOPPING) if w in ("shopping", "deals") \
            else list(signals.CATEGORIES) if w == "all" else [w]
    bad = [n for n in names if n not in signals.CATEGORIES]
    if bad or not names:
        raise ToolError(f"Categories: {', '.join(signals.CATEGORIES)} (or 'investing', 'shopping', 'all').")
    saved = json.loads(memory.get(SETTINGS_KEY) or "{}")
    saved.update({n: on for n in names})
    memory.set(SETTINGS_KEY, json.dumps(saved))
    return f"Buy and sell signals are {'on' if on else 'off'} for {', '.join(dict.fromkeys(names))}."


def category_of_item(title: str) -> str:
    """Which shopping or travel category a deal belongs to (for the switches)."""
    import re  # noqa: PLC0415
    from .shopper import kind_of  # noqa: PLC0415
    t = title or ""
    if signals.HOTEL.search(t) and not re.search(r"\bflight|\bfare", t, re.I):
        return "hotels"
    if re.search(r"\bflights?\b|\bfares?\b|airfare|round[- ]?trip|\b[A-Z]{3}\s?(?:-|to|→)\s?[A-Z]{3}\b", t):
        return "flights"
    if re.search(r"\b(drill|saw|sander|grinder|wrench|impact driver|tool ?(?:kit|set|box)|mower|dewalt|milwaukee|"
                 r"makita|ryobi)\b", t, re.I):
        return "tools"
    if re.search(r"\b(ps5|xbox|nintendo|switch 2|steam|video game|gamepad|controller)\b", t, re.I):
        return "games"
    kind = kind_of(t)
    return {"electronics": "electronics", "clothing": "clothing", "travel": "flights"}.get(kind, "home")


# --- investing signals ----------------------------------------------------------------------------------------
def candidates(memory: Memory, today: date) -> list[dict]:
    """Symbols that got unusual attention last night, in categories that are on."""
    out = []
    for t in signals.trending(memory, kind="ticker", today=today, days=1, limit=40):
        if t["category"] not in INVESTING or not enabled(memory, t["category"]):
            continue
        spike = t["times_usual"]
        if t["mentions"] >= MIN_MENTIONS and (spike is None and t["mentions"] >= 5 or (spike or 0) >= SPIKE):
            out.append(t)
    return out[:12]


def judge(t: dict, bars: list[dict], facts: dict | None = None) -> dict | None:
    """One symbol: the action and its reasons, from attention, trend and facts. None without enough prices."""
    from .markets import rsi, sma  # noqa: PLC0415
    closes = [b["c"] for b in bars or []]
    if len(closes) < 60:
        return None
    price, avg50 = closes[-1], sma(closes, 50)
    strength = (rsi(closes) or [50.0])[-1]
    week = price / closes[-6] - 1
    facts = facts or {}
    score, why = 0.0, []
    times = f"{t['times_usual']:g} times its usual" if t["times_usual"] else "more than Buddy has seen before"
    why.append(f"{t['mentions']} Reddit posts last night ({times}), tone "
               f"{'positive' if t['tone'] > 0.25 else 'negative' if t['tone'] < -0.25 else 'mixed'}")
    score += 1 if t["tone"] > 0.25 else -1 if t["tone"] < -0.25 else 0
    score += 1 if price > avg50 else -1
    why.append(f"price ${price:,.2f} is {'above' if price > avg50 else 'below'} its 50-day average "
               f"(${avg50:,.2f}), {week:+.1%} this week, RSI {strength:.0f}")
    ins = facts.get("insiders")
    if ins is not None and abs(ins) >= 20:
        score += 1 if ins > 0 else -1
        why.append(f"insiders have been net {'buying' if ins > 0 else 'selling'} for three months")
    an = facts.get("analysts")
    if an and an["buy_share"] >= 0.6:
        score += 0.5
        why.append(f"{an['buy']} of {an['buy'] + an['hold'] + an['sell']} analysts rate it buy")
    elif an and an["buy_share"] <= 0.25:
        score -= 0.5
        why.append(f"only {an['buy']} of {an['buy'] + an['hold'] + an['sell']} analysts rate it buy")
    if facts.get("earnings_on"):
        why.append(f"earnings on {facts['earnings_on']}: the price can jump either way that day")
    crowded = strength >= 75 and (t["times_usual"] or SPIKE) >= SPIKE and t["tone"] > 0
    action = "caution" if crowded else "buy" if score >= 2 else "sell" if score <= -2 else "watch"
    if crowded:
        why.append("a crowd piling into something already stretched is usually late")
    return {"key": t["key"], "category": t["category"], "action": action, "price": round(price, 2),
            "reasons": why, "facts": {"score": score, "rsi": round(strength, 1), "tone": t["tone"],
                                      "mentions": t["mentions"], "times_usual": t["times_usual"], **facts}}


def build(memory: Memory, today: date | None = None, data=None, facts_for=None) -> list[dict]:
    """Today's investing signals, written down. `data`: markets.Data (daily prices). `facts_for(symbol)`: Finnhub."""
    from . import finnhub, pricebook  # noqa: PLC0415
    from .markets import Data  # noqa: PLC0415
    today = today or local_now().date()
    _ready(memory)
    cands = candidates(memory, today - timedelta(days=1)) or candidates(memory, today)
    if not cands:
        return []
    data = data or Data(memory)
    bars = data.bars("us", [c["key"] for c in cands], days=200)
    if facts_for is None and finnhub.configured():
        facts_for = lambda s: finnhub.facts(memory, s, today=today)  # noqa: E731
    out = []
    for c in cands:
        try:
            facts = facts_for(c["key"]) if facts_for else {}
        except Exception:
            log.info("recommend: facts for %s failed", c["key"], exc_info=True)
            facts = {}
        j = judge(c, bars.get(c["key"]) or [], facts)
        if not j:
            continue
        if pricebook.run(memory, "SELECT 1 FROM recs WHERE key = ? AND action = ? AND day >= ?",
                         (j["key"], j["action"], (today - timedelta(days=SCORE_AFTER_DAYS)).isoformat())):
            continue  # the same call on the same symbol this week: said once
        pricebook.run(memory, "INSERT INTO recs (day, category, key, action, reasons, facts, price_then) "
                              "VALUES (?, ?, ?, ?, ?, ?, ?)",
                      (today.isoformat(), j["category"], j["key"], j["action"], json.dumps(j["reasons"]),
                       json.dumps(j["facts"], default=str), j["price"]))
        out.append(j)
    return out


def settle(memory: Memory, today: date | None = None, data=None) -> int:
    """Score the signals that are a week old against today's price."""
    from . import pricebook  # noqa: PLC0415
    from .markets import Data  # noqa: PLC0415
    today = today or local_now().date()
    _ready(memory)
    due_ = pricebook.run(memory, "SELECT id, key, action, price_then FROM recs WHERE status = 'open' AND day <= ?",
                         ((today - timedelta(days=SCORE_AFTER_DAYS)).isoformat(),))
    if not due_:
        return 0
    data = data or Data(memory)
    latest = data.latest("us", sorted({r["key"] for r in due_}))
    n = 0
    for r in due_:
        now = latest.get(r["key"])
        if not now or not r["price_then"]:
            continue
        change = now / r["price_then"] - 1
        way = change if r["action"] == "buy" else -change  # sell and caution are right when it falls
        status = "none" if r["action"] == "watch" else "right" if way >= MOVE else "wrong" if way <= -MOVE else "flat"
        pricebook.run(memory, "UPDATE recs SET status = ?, scored_on = ?, price_after = ?, change = ? WHERE id = ?",
                      (status, today.isoformat(), round(now, 2), round(change, 4), r["id"]))
        n += 1
    return n


def track_record(memory: Memory, category: str = "") -> dict:
    from . import pricebook  # noqa: PLC0415
    _ready(memory)
    cat = "AND category = ?" if category else ""
    rows = pricebook.run(memory, f"SELECT status, COUNT(*) AS n FROM recs WHERE action != 'watch' {cat} "
                                 f"GROUP BY status", ((category,) if category else ()))
    by = {r["status"]: int(r["n"]) for r in rows}
    scored = by.get("right", 0) + by.get("wrong", 0) + by.get("flat", 0)
    return {"scored": scored, "right": by.get("right", 0), "wrong": by.get("wrong", 0), "flat": by.get("flat", 0),
            "open": by.get("open", 0), "hit_rate": round(by.get("right", 0) / scored, 2) if scored else None,
            "proven": scored >= PROVEN_AFTER}


def message(j: dict, record: dict) -> str:
    verb = {"buy": "buy signal", "sell": "sell or avoid signal", "caution": "caution: crowded"}[j["action"]]
    icon = {"buy": "📈", "sell": "📉", "caution": "⚠️"}[j["action"]]
    proof = (f"Buddy's {j['category']} signals: {record['right']} right of {record['scored']} scored"
             if record["proven"] else
             f"UNPROVEN: only {record['scored']} of Buddy's {j['category']} signals scored so far"
             + (f" ({record['right']} right)" if record["scored"] else ""))
    return f"{icon} {j['key']} ({j['category']}): {verb}. Why: {'; '.join(j['reasons'])}. {proof}. {NOTE}"


# --- the morning ----------------------------------------------------------------------------------------------
def due(memory: Memory, now: datetime | None = None) -> bool:
    now = now or local_now()
    return now.weekday() < 5 and now.hour >= HOUR and memory.get(DAY_KEY) != now.date().isoformat()


def run(memory: Memory, today: date | None = None, data=None, facts_for=None) -> list[str]:
    """Score last week's signals, make today's, and return the messages worth sending (at most three)."""
    today = today or local_now().date()
    if not any(enabled(memory, c) for c in INVESTING):
        return []
    try:
        settle(memory, today, data)
    except Exception:
        log.warning("recommend: scoring failed", exc_info=True)
    made = [j for j in build(memory, today, data, facts_for) if j["action"] != "watch"]
    made.sort(key=lambda j: -abs(j["facts"]["score"]))
    return [message(j, track_record(memory, j["category"])) for j in made[:MAX_TOLD]]


def recent(memory: Memory, days: int = 14, today: date | None = None) -> list[dict]:
    from . import pricebook  # noqa: PLC0415
    _ready(memory)
    since = ((today or local_now().date()) - timedelta(days=days)).isoformat()
    rows = pricebook.run(memory, "SELECT day, category, key, action, reasons, price_then, status, price_after, change "
                                 "FROM recs WHERE day >= ? AND action != 'watch' ORDER BY day DESC, id DESC", (since,))
    return [{**r, "day": str(r["day"]), "reasons": json.loads(r["reasons"] or "[]")} for r in rows]


def page(memory: Memory) -> dict:
    """For the Deals page's Signals tab: the switches, the recent signals and the record per category."""
    return {"categories": categories(memory), "investing": list(INVESTING), "shopping": list(SHOPPING),
            "signals": recent(memory, 30), "record": {c: track_record(memory, c) for c in INVESTING},
            "trending": signals.trending(memory, kind="ticker", days=1, limit=10), "note": NOTE}


TOOL = {
    "name": "recommendations",
    "description": (
        "Buy and sell signals by category, with reasons and Buddy's own record. Categories: electronics, home, "
        "tools, clothing, games, flights, hotels (deal alerts and buy-or-wait) and etfs, stocks, commodities, "
        "options (investing signals from Reddit attention, price trend and Finnhub facts, made each weekday "
        "morning and scored a week later). show: the recent signals and how each did. settings: which categories "
        "are on. switch (categories: names, or 'investing', 'shopping', 'all'; on): turn them on or off. "
        "record (category?): how many were right. run: make today's signals now. live: the live triggers "
        "(checked every minute in US market hours for the watch list, this week's signals and watched ideas: "
        "breakouts, big days, fast moves): whether they are on, the price source and what is followed. Always say a signal is unproven "
        "when its record says so, that it is not advice, and that the owner decides and trades themselves."),
    "input_schema": {"type": "object", "properties": {
        "action": {"type": "string", "enum": ["show", "settings", "switch", "record", "run", "live"]},
        "categories": {"type": "array", "items": {"type": "string"}}, "on": {"type": "boolean"},
        "category": {"type": "string"}}, "required": ["action"], "additionalProperties": False},
}


def run_tool(memory: Memory, args: dict) -> str:
    a = args.get("action")
    if a == "switch":
        return switch(memory, args.get("categories") or [], bool(args.get("on")))
    if a == "settings":
        return json.dumps(categories(memory))
    if a == "record":
        cats = [args["category"]] if args.get("category") else list(INVESTING)
        return json.dumps({c: track_record(memory, c) for c in cats})
    if a == "live":
        from . import livewatch  # noqa: PLC0415
        return json.dumps(livewatch.status(memory))
    if a == "run":
        told = run(memory)
        return json.dumps({"signals": told or "No new signals: nothing got unusual attention with the trend and "
                                              "tone agreeing.", "note": NOTE})
    return json.dumps({"signals": recent(memory), "record": {c: track_record(memory, c) for c in INVESTING},
                       "note": NOTE}, default=str)
