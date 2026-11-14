"""Seasonal price patterns from free government data: which months each kind of thing is cheapest.

The US Bureau of Labor Statistics publishes a monthly consumer price index for narrow categories (televisions,
computers, major appliances, furniture, tools, clothing, footwear, airline fares, hotels, car rental...), going
back decades, not seasonally adjusted. FRED (the St. Louis Fed, the free key Market school already uses) serves
them. For each category Buddy takes the last ten years and works out, per calendar month, how far prices usually
sit above or below that year's trend (the index against its centred 12-month average; the median across years).
So "TVs are usually about 4% under their yearly trend in December" is from data, not a rule of thumb.

What it is and isn't: a category's national average price level by month. It says nothing about one product or one
store, and a category index moves less than a single item's sale price does; it shows the season, and Buddy's own
price book shows the item. Refreshed once a month (code only; no AI, no cost). Only series names go to FRED.
"""

import json
import logging
import re
from datetime import date, datetime, timezone
from statistics import median

from .memory import Memory
from .tools import ToolError, local_now

log = logging.getLogger(__name__)

MONTH_KEY, STATUS_KEY = "seasons:month", "seasons:status"
YEARS = 10
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")

# category: (FRED series id: CPI for All Urban Consumers, US city average, not seasonally adjusted; the words that
# put an item in it). A series FRED doesn't have is skipped and named in the status, never guessed around.
SERIES = {
    "televisions": ("CUUR0000SERA01", r"\btv\b|television|oled|qled"),
    "audio equipment": ("CUUR0000SERA05", r"headphone|earbud|speaker|soundbar|airpods"),
    "computers": ("CUUR0000SEEE01", r"laptop|macbook|chromebook|computer|desktop|monitor|tablet|ipad|printer"),
    "major appliances": ("CUUR0000SEHK01", r"washer|dryer|dishwasher|fridge|refrigerator|oven|range|freezer|"
                                           r"microwave|appliance"),
    "furniture and bedding": ("CUUR0000SEHJ", r"sofa|couch|chair|desk|table|mattress|bed\b|dresser|furniture"),
    "tools and outdoor equipment": ("CUUR0000SEHM", r"drill|saw|sander|grinder|wrench|tool|mower|trimmer|blower|"
                                                    r"ladder|generator|pressure washer|dewalt|milwaukee|makita|ryobi"),
    "household furnishings": ("CUUR0000SAH3", r"vacuum|rug|curtain|lamp|cookware|bedding|towel"),
    "clothing": ("CUUR0000SAA", r"shirt|jeans|pants|dress|jacket|coat|hoodie|sweater|suit|clothing|apparel"),
    "footwear": ("CUUR0000SEAE", r"sneaker|shoe|boot|sandal"),
    "toys": ("CUUR0000SERE01", r"\btoys?\b|lego|doll|board game"),
    "sporting goods": ("CUUR0000SERC", r"bike|bicycle|treadmill|golf|tent|kayak|dumbbell|sporting"),
    "airline fares": ("CUUR0000SETG01", r"flight|airfare|fare\b|airline"),
    "hotels": ("CUUR0000SEHB", r"hotel|motel|lodging|resort|per night"),
    "car rental": ("CUUR0000SETA04", r"rental car|car rental|rent a car"),
}
KIND_DEFAULT = {"clothing": "clothing", "home": "household furnishings"}


def _ready(memory: Memory) -> None:
    from . import pricebook  # noqa: PLC0415  kept with the other learned patterns
    pricebook.run(memory, "CREATE TABLE IF NOT EXISTS seasons (category VARCHAR PRIMARY KEY, series VARCHAR, "
                          "data VARCHAR, fitted_on DATE)")


def seasonal(rows: list[tuple[str, float]]) -> dict | None:
    """Per month, the usual % above or below the year's trend, from monthly (date, index) rows in any order.
    Needs three whole years. The trend is the centred 12-month average, so inflation doesn't count as season."""
    rows = sorted((d[:7], v) for d, v in rows if v and v > 0)
    values = [v for _, v in rows]
    if len(rows) < 48:
        return None
    by: dict[int, list[float]] = {}
    for i in range(6, len(rows) - 6):
        trend = (values[i - 6] / 2 + sum(values[i - 5:i + 6]) + values[i + 6] / 2) / 12
        by.setdefault(int(rows[i][0][5:7]), []).append((values[i] / trend - 1) * 100)
    if len(by) < 12 or min(len(v) for v in by.values()) < 3:
        return None
    raw = {m: median(v) for m, v in by.items()}
    mean = sum(raw.values()) / 12
    factor = {m: round(raw[m] - mean, 2) for m in range(1, 13)}  # centred: the year averages to zero
    order = sorted(factor, key=lambda m: factor[m])
    return {"by_month": factor, "cheapest": order[:3], "dearest": order[-3:][::-1],
            "swing_pct": round(factor[order[-1]] - factor[order[0]], 2), "years": min(len(v) for v in by.values()),
            "through": rows[-1][0]}


def refresh(memory: Memory, env=None, fetch=None) -> str:
    """Fetch every category from FRED and keep its seasonal pattern. Returns a status line."""
    from . import market_school, pricebook  # noqa: PLC0415
    _ready(memory)
    done, failed = [], []
    for category, (series, _) in SERIES.items():
        try:
            rows = market_school.fred(memory, series, YEARS * 12 + 13, env, fetch)
            got = seasonal(rows)
        except ToolError as e:
            return _status(memory, str(e))
        except Exception as e:
            log.info("seasons %s: %s", category, e)
            failed.append(f"{category} ({series}: {str(e)[:60]})")
            continue
        if not got:
            failed.append(f"{category} ({series}: not enough monthly data)")
            continue
        pricebook.run(memory, "INSERT OR REPLACE INTO seasons VALUES (?, ?, ?, ?)",
                      (category, series, json.dumps(got), local_now().date().isoformat()))
        done.append(category)
    return _status(memory, f"seasonal patterns for {len(done)} categories"
                           + (f"; not available: {'; '.join(failed)}" if failed else ""))


def _status(memory: Memory, text: str) -> str:
    memory.set(STATUS_KEY, json.dumps({"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "status": text}))
    return text


def due(memory: Memory, now: datetime | None = None) -> bool:
    now = now or local_now()
    return memory.get(MONTH_KEY) != now.strftime("%Y-%m")


def category_of(what: str, kind: str = "") -> str:
    if (what or "").strip().lower() in SERIES:
        return what.strip().lower()
    for category, (_, rx) in SERIES.items():
        if re.search(rx, what or "", re.I):
            return category
    return KIND_DEFAULT.get(kind, "")


def get(memory: Memory, category: str) -> dict | None:
    if not category:
        return None
    try:
        _ready(memory)
        from . import pricebook  # noqa: PLC0415
        rows = pricebook.run(memory, "SELECT data FROM seasons WHERE category = ?", (category,))
        got = json.loads(rows[0]["data"]) if rows else None
    except Exception:
        return None
    if got:
        got["by_month"] = {int(k): v for k, v in got["by_month"].items()}
    return got


def outlook(memory: Memory, what: str, kind: str = "", today: date | None = None, within_days: int = 120) -> dict | None:
    """For an item: its category's season now, and the best month coming within the owner's patience."""
    category = category_of(what, kind)
    s = get(memory, category)
    if not s:
        return None
    today = today or local_now().date()
    now = s["by_month"][today.month]
    ahead = [((today.month - 1 + i) % 12) + 1 for i in range(1, max(1, min(within_days // 30, 11)) + 1)]
    best = min(ahead, key=lambda m: s["by_month"][m]) if ahead else today.month
    gain = round(now - s["by_month"][best], 2)
    return {"category": category, "now_vs_trend_pct": now, "cheapest_months": [MONTHS[m - 1] for m in s["cheapest"]],
            "dearest_months": [MONTHS[m - 1] for m in s["dearest"]], "swing_pct": s["swing_pct"],
            "better_month_ahead": MONTHS[best - 1] if gain >= 1 else "", "gain_pct": gain if gain >= 1 else 0,
            "years": s["years"], "by_month": {MONTHS[m - 1]: v for m, v in sorted(s["by_month"].items())},
            "note": f"US average prices for {category}, from {s['years']} years of government data (through "
                    f"{s['through']}). A category's average moves less than one item's sale price."}


def reason(o: dict) -> str:
    """One sentence for the buy-or-wait reasons."""
    text = (f"government price data ({o['years']} years): {o['category']} are usually cheapest in "
            f"{', '.join(o['cheapest_months'])} and dearest in {', '.join(o['dearest_months'])} "
            f"(about {o['swing_pct']:g}% apart)")
    if o["better_month_ahead"]:
        text += f"; {o['better_month_ahead']} usually runs about {o['gain_pct']:g}% lower than now"
    elif o["now_vs_trend_pct"] <= -0.5:
        text += "; this is one of the cheaper times of year"
    return text


TOOL = {
    "name": "seasonal_prices",
    "description": (
        "Which months a kind of thing is usually cheapest, from about ten years of free US government price data "
        "(BLS consumer price index by category, through FRED): televisions, audio equipment, computers, major "
        "appliances, furniture and bedding, tools and outdoor equipment, household furnishings, clothing, footwear, "
        "toys, sporting goods, airline fares, hotels, car rental. show: what (an item or a category): its cheapest "
        "and dearest months, the % above or below the yearly trend each month, and whether a better month is "
        "coming. list: every category's cheapest months. refresh: fetch the latest data (done monthly anyway). "
        "It's the national average for a category, not one product or store: use it for timing, with the price "
        "book and deal forecasts for the item itself."),
    "input_schema": {"type": "object", "properties": {
        "action": {"type": "string", "enum": ["show", "list", "refresh"]}, "what": {"type": "string"}},
        "required": ["action"], "additionalProperties": False},
}


def run_tool(memory: Memory, args: dict) -> str:
    action = args.get("action")
    if action == "refresh":
        text = refresh(memory)
        memory.set(MONTH_KEY, local_now().strftime("%Y-%m"))
        return text
    if action == "list":
        out = {}
        for c in SERIES:
            s = get(memory, c)
            if s:
                out[c] = {"cheapest": [MONTHS[m - 1] for m in s["cheapest"]],
                          "dearest": [MONTHS[m - 1] for m in s["dearest"]], "swing_pct": s["swing_pct"]}
        return json.dumps(out or {"note": "Not fetched yet: needs the free FRED key (BUDDY_FRED_KEY), then say "
                                          "'refresh the seasonal prices'.", "status": memory.get(STATUS_KEY)})
    if action == "show":
        what = args.get("what", "")
        o = outlook(memory, what)
        if not o:
            return json.dumps({"note": "No seasonal data for that: categories are " + ", ".join(SERIES)
                                       + ". If none are fetched yet, say 'refresh the seasonal prices'.",
                               "status": memory.get(STATUS_KEY)})
        return json.dumps(o)
    raise ToolError("Use show, list or refresh.")
