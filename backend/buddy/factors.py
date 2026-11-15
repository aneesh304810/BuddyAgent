"""The shared signals library: facts any of Buddy's prediction engines can look up when it makes a call.

Buddy has three engines that learn from how their own calls turned out (modeltrain.py trains and tests a model for
each once it has enough judged calls): investing (signals and themes), travel (flight and hotel book-or-wait
calls) and shopping (buy-or-wait calls). Each engine keeps its own reasons. This library adds what the others
already know, so a call is saved with more to learn from:

  investing   what the Intelligence Platform holds on the technologies whose filings name the company: how many
              records in 90 days and how fast that is growing, how many kinds of evidence, the stage, good and
              bad headlines in 14 days (the technology's and the company's own), and evidence patterns that
              fired in the last 30 days
  travel      the season for the month of travel and for now (government price data for air fares and hotels),
              oil's move over 30 days (fuel feeds into fares), how many flight or hotel deals reached the inbox
              and feeds in 30 days against the 30 before, deals that name this route or place, days until travel
  shopping    deals for this item in 90 days from the inbox, from Reddit and from the feeds, the category's
              season now and the gain from its best month ahead

Every factor is a number, named f_..., worked out in code from what Buddy already stores (no AI, and no new
outside call except oil, once a day, when its free key is set). A factor that can't be worked out is left out, and
the trainer treats a missing factor as zero. Factors never decide a call themselves: they are saved with it, and
a model uses them only after it has passed its test on calls it never saw.

Only public data is read here: evidence, headlines, prices, deals. Nothing about the owner goes anywhere.
"""

import json
import logging
import re
from datetime import date, timedelta

from .memory import Memory
from .tools import local_now

log = logging.getLogger(__name__)

OIL_KEY = "factors:oil"
WORD = re.compile(r"[a-z0-9]{3,}")
STOP = {"the", "and", "for", "with", "from", "new", "inch", "pack", "black", "white", "night", "nights", "hotel",
        "flight", "flights", "round", "trip", "one", "way"}
WHAT = {
    "investing": ["Intelligence evidence for the company's technologies (records, growth, kinds, stage)",
                  "Good and bad headlines in 14 days, for the technology and the company",
                  "Evidence patterns that fired in the last 30 days",
                  "Already in each signal: Reddit mentions and tone, price trend, insider trades, analysts"],
    "travel": ["The season for the month of travel and for now (government price data)",
               "Oil's move over 30 days", "Flight or hotel deals in your inbox and the feeds, 30 days against the 30 before",
               "Deals that name this route or place", "Days until travel"],
    "shopping": ["Deals for this item in 90 days: from your inbox, from Reddit, from the feeds",
                 "The category's season now and the gain from its best month ahead",
                 "Already in each call: the item's price history, the sales calendar, a newer model coming"],
}


def _words(text: str) -> list[str]:
    return [w for w in dict.fromkeys(WORD.findall(str(text or "").lower())) if w not in STOP][:4]


def _run(memory: Memory, sql: str, params=()) -> list[dict]:
    from . import pricebook  # noqa: PLC0415
    return pricebook.run(memory, sql, params)


def _safe(fn, *args) -> dict:
    try:
        return {k: round(float(v), 4) for k, v in (fn(*args) or {}).items() if isinstance(v, (int, float))}
    except Exception:
        log.info("factors: %s unavailable", getattr(fn, "__name__", "a factor"), exc_info=True)
        return {}


# --- investing ----------------------------------------------------------------------------------------------
def _evidence(memory: Memory, symbol: str, today: date) -> dict:
    from . import bip, themes  # noqa: PLC0415
    rows = themes._rows(memory, today)
    techs = [tid for tid in bip.TECHS if any(c["symbol"] == symbol for c in themes.tickers(
        [r for r in rows if tid in r["techs"]], tid))]
    if not techs:
        return {}
    mine = [r for r in rows if any(t in r["techs"] for t in techs)]
    day = lambda n: (today - timedelta(days=n)).isoformat()  # noqa: E731
    recent, before = [r for r in mine if r["day"] >= day(90)], [r for r in mine if day(180) <= r["day"] < day(90)]
    news = [r for r in mine if r["kind"] in themes.NEWS_KINDS and r["day"] >= day(14)]
    own = _run(memory, "SELECT t.tone FROM theme_news n JOIN theme_tone t ON t.key = n.key WHERE n.symbol = ? AND "
                       "n.day >= ?", (symbol, day(14)))
    kinds: dict[str, int] = {}
    for r in recent:
        kinds[r["kind"]] = kinds.get(r["kind"], 0) + 1
    stage = max((bip.STAGES.index(bip.maturity(kinds, 1, bip.TECHS[t]["established"])["stage"]) for t in techs), default=0)
    fired = _run(memory, "SELECT COUNT(*) AS n FROM theme_events WHERE day >= ? AND symbols LIKE ?",
                 (day(30), f'%"{symbol}"%'))[0]["n"]
    return {"f_evidence_90": len(recent), "f_evidence_growth": (len(recent) / len(before) - 1) if before else 0.0,
            "f_evidence_kinds": len(kinds), "f_stage": stage, "f_themes": len(techs),
            "f_good_news_14": sum(1 for r in news if r["tone"] == "good") + sum(1 for r in own if r["tone"] == "good"),
            "f_bad_news_14": sum(1 for r in news if r["tone"] == "bad") + sum(1 for r in own if r["tone"] == "bad"),
            "f_patterns_30": fired}


def investing(memory: Memory, symbol: str, today: date | None = None) -> dict:
    return _safe(_evidence, memory, str(symbol or "").upper(), today or local_now().date())


# --- seasons, oil, deals ------------------------------------------------------------------------------------
def _season(memory: Memory, category: str, months: list[int]) -> list[float | None]:
    from . import seasons  # noqa: PLC0415
    got = seasons.get(memory, category)
    return [got["by_month"].get(m) if got else None for m in months]


def oil(memory: Memory, today: date, env=None, fetch=None) -> float | None:
    """Oil's change over about 30 days, looked up once a day (needs the free EIA key; None without it)."""
    try:
        kept = json.loads(memory.get(OIL_KEY) or "{}")
    except ValueError:
        kept = {}
    if kept.get("day") == today.isoformat():
        return kept.get("change")
    change = None
    try:
        from . import market_school  # noqa: PLC0415
        rows = market_school.eia(memory, market_school.EIA_SERIES["oil"], 60, env, fetch)  # newest first
        if len(rows) >= 15:
            now, then = rows[0][1], rows[min(len(rows) - 1, 21)][1]  # about 21 trading days back
            change = round(now / then - 1, 4) if then else None
    except Exception as e:
        log.info("factors: oil unavailable (%s)", type(e).__name__)
    memory.set(OIL_KEY, json.dumps({"day": today.isoformat(), "change": change}))
    return change


def _deals(memory: Memory, domain: str, item: str, today: date, days: int) -> dict:
    since, before = (today - timedelta(days=days)).isoformat(), (today - timedelta(days=2 * days)).isoformat()
    words = _words(item)
    rows = _run(memory, "SELECT seen_on, source, lower(title) AS title FROM deals WHERE COALESCE(domain, 'product') = ? "
                        "AND seen_on >= ?", (domain, before))
    now = [r for r in rows if str(r["seen_on"]) >= since]
    named = [r for r in now if words and all(w in r["title"] for w in words[:2])]
    by = lambda rows_, word: sum(1 for r in rows_ if word in str(r["source"]).lower())  # noqa: E731
    return {"all_now": len(now), "all_before": len(rows) - len(now), "named": len(named),
            "inbox": by(named, "email") + by(named, "notification"), "reddit": by(named, "reddit")}


# --- travel -------------------------------------------------------------------------------------------------
def _travel(memory: Memory, domain: str, item: str, travel_day: date | None, today: date, env=None, fetch=None) -> dict:
    out: dict = {}
    category = "airline fares" if domain == "flight" else "hotels"
    months = [today.month] + ([travel_day.month] if travel_day else [])
    season = _season(memory, category, months)
    if season[0] is not None:
        out["f_season_now"] = season[0]
    if travel_day:
        out["f_days_out"] = (travel_day - today).days
        if season[-1] is not None:
            out["f_season_travel"] = season[-1]
    if domain == "flight":
        change = oil(memory, today, env, fetch)
        if change is not None:
            out["f_oil_30"] = change
    deals = _deals(memory, domain, item, today, 30)
    out.update({"f_deals_30": deals["all_now"], "f_deals_named_30": deals["named"],
                "f_deals_growth": (deals["all_now"] / deals["all_before"] - 1) if deals["all_before"] else 0.0})
    return out


def travel(memory: Memory, domain: str, item: str, travel_day: date | None = None, today: date | None = None,
           env=None, fetch=None) -> dict:
    return _safe(_travel, memory, domain, item, travel_day, today or local_now().date(), env, fetch)


# --- shopping -----------------------------------------------------------------------------------------------
def _shopping(memory: Memory, what: str, kind: str, today: date) -> dict:
    from . import seasons  # noqa: PLC0415
    deals = _deals(memory, "product", what, today, 90)
    out = {"f_deals_named_90": deals["named"], "f_inbox_deals_90": deals["inbox"], "f_reddit_deals_90": deals["reddit"]}
    look = seasons.outlook(memory, what, kind, today)
    if look:
        out.update({"f_season_now": look["now_vs_trend_pct"], "f_season_gain": look["gain_pct"]})
    return out


def shopping(memory: Memory, what: str, kind: str = "", today: date | None = None) -> dict:
    return _safe(_shopping, memory, what, kind, today or local_now().date())


# --- the three engines at a glance (the Predictions tab) ------------------------------------------------------
ENGINES = (  # engine, title, the question it answers, its models in modeltrain (domain names)
    ("investing", "Investing", "Will this beat the market? Signals over weeks, theme baskets over 30 and 90 days.",
     ("investing", "theme")),
    ("travel", "Flights and hotels", "Book now or wait: will the price come down 8% or more before the trip?",
     ("flight", "hotel")),
    ("shopping", "Shopping", "Buy now or wait: will the price come down 8% or more within your patience?",
     ("product",)),
)


def overview(memory: Memory) -> dict:
    """Each engine: what it asks, what it learns from, how many judged calls it has, and whether a tested model
    is in use."""
    from . import modeltrain, themes  # noqa: PLC0415
    status = modeltrain.status(memory)
    watched = {}
    try:
        watched["travel"] = memory.db.execute("SELECT COUNT(*) FROM travel_saved").fetchone()[0]
    except Exception:
        watched["travel"] = 0
    try:
        open_calls = {r["domain"]: r["n"] for r in _run(
            memory, "SELECT COALESCE(domain, 'product') AS domain, COUNT(*) AS n FROM predictions WHERE outcome = '' "
                    "GROUP BY 1")}
    except Exception:
        open_calls = {}
    engines = []
    for key, title, asks, domains in ENGINES:
        models = []
        for d in domains:
            s = status["domains"][modeltrain.NAMES[d]]
            last = s["last_run"] or {}
            models.append({"domain": d, "name": modeltrain.NAMES[d], "judged": s["rows"], "needed": s["needed"],
                           "of_each_outcome": s["of_each_outcome"], "enough": s["enough"],
                           "in_use": s["model_in_use"], "last_run": last.get("on"), "last_status": last.get("status"),
                           "tested_on": last.get("tested_on"), "right_overall": last.get("right_overall"),
                           "right_when_liked": last.get("right_when_liked"), "strongest": last.get("strongest"),
                           "checks": last.get("checks"), "why": last.get("why"), "brier": last.get("brier"),
                           "brier_of_the_average": last.get("brier_of_the_average"),
                           "calibration": last.get("calibration"), "by_market": last.get("by_market"),
                           "simple_rule_accuracy": last.get("simple_rule_accuracy"), "live": s.get("live")})
        engines.append({"id": key, "title": title, "asks": asks, "learns_from": WHAT[key], "models": models,
                        "open_calls": sum(open_calls.get(d, 0) for d in domains),
                        "watched": watched.get(key)})
    return {"engines": engines, "history": themes.backfill_status(memory), "running": running(memory),
            "hindsight": themes.HINDSIGHT, "note": status["note"] + " A model's odds "
            "are one more opinion with a record behind it, never a promise. Buddy never buys, books or trades."}


# --- is it really running? ----------------------------------------------------------------------------------
def _age(stamp: str, now) -> float | None:
    from datetime import datetime, timezone  # noqa: PLC0415
    try:
        then = datetime.fromisoformat(str(stamp))
    except ValueError:
        return None
    if then.tzinfo is None:
        then = then.replace(tzinfo=timezone.utc)
    return (now - then).total_seconds() / 3600


def running(memory: Memory, now=None) -> list[dict]:
    """What has actually happened on this server, as opposed to what the code can do: each line is ok, late or
    never, with the facts behind it. Read from what the jobs themselves wrote down."""
    from datetime import datetime, timezone  # noqa: PLC0415
    from . import bip, bip_sources, modeltrain, themes  # noqa: PLC0415
    now = now or datetime.now(timezone.utc)
    out = []

    def line(what: str, state: str, detail: str) -> None:
        out.append({"what": what, "state": state, "detail": detail})

    def count(sql: str, params=()) -> dict:
        try:
            return _run(memory, sql, params)[0]
        except Exception:
            return {}
    try:
        last = json.loads(memory.get(bip.LAST_KEY) or "{}")
    except ValueError:
        last = {}
    hours = _age(last.get("at", ""), now)
    if hours is None:
        line("Nightly evidence collection", "never", "has not run on this server yet")
    else:
        failed = ", ".join(last.get("failed") or {}) or "none"
        line("Nightly evidence collection", "ok" if hours <= 36 else "late",
             f"last ran {hours:.0f} hours ago: {last.get('new', 0)} new records from {last.get('asked', 0)} questions; "
             f"sources that failed: {failed}")
    try:
        sources = bip_sources.status(memory)
        working = sum(1 for s in sources if s["state"] == "working")
        failing = [s["title"] for s in sources if s["state"] == "failing"]
        line("Evidence sources", "ok" if working and not failing else ("never" if not working else "late"),
             f"{working} of {len(sources)} working" + (f"; failing: {', '.join(failing[:4])}" if failing else ""))
    except Exception:
        line("Evidence sources", "never", "no status yet")
    themes._ready(memory)
    tone = count("SELECT COUNT(*) AS n, MAX(made) AS last FROM theme_tone")
    line("Headline labels", "ok" if tone.get("n") else "never",
         f"{tone.get('n', 0)} headlines labelled" + (f", the latest on {tone['last']}" if tone.get("last") else ""))
    news = count("SELECT COUNT(*) AS n, MAX(seen) AS last FROM theme_news")
    line("News about companies", "ok" if news.get("n") else "never",
         f"{news.get('n', 0)} headlines kept" + (f", the latest on {news['last']}" if news.get("last") else ""))
    events = count("SELECT COUNT(*) AS n, SUM(CASE WHEN backfilled THEN 1 ELSE 0 END) AS old, MAX(day) AS last, "
                   "SUM(CASE WHEN r90 IS NOT NULL THEN 1 ELSE 0 END) AS done FROM theme_events")
    line("Evidence events and practice baskets", "ok" if events.get("n") else "never",
         f"{events.get('n', 0)} events ({int(events.get('old') or 0)} from loaded history), "
         f"{int(events.get('done') or 0)} with a 90-day result" + (f"; the latest on {events['last']}" if events.get("last") else ""))
    history = themes.backfill_status(memory)
    line("History for the theme model", {"done": "ok", "collecting": "late", "failed": "late"}.get(history["state"], "never"),
         {"done": f"loaded: {history.get('records', 0)} records, {history.get('events', 0)} events",
          "collecting": f"loading now, reached {history.get('reached', 'the start')}",
          "failed": f"the last load failed: {history.get('why', '')}"}.get(history["state"], "not loaded"))
    week = memory.get(modeltrain.WEEK_KEY)
    trained = count("SELECT COUNT(*) AS n, MAX(trained_on) AS last FROM models")
    line("Weekly model training", "ok" if week else "never",
         (f"last checked in week {week}; " if week else "has not run yet; ")
         + (f"{trained.get('n', 0)} training runs, the latest on {trained['last']}" if trained.get("last") else "no model trained yet"))
    calls = count("SELECT COUNT(*) AS n, SUM(CASE WHEN outcome IN ('right', 'wrong') THEN 1 ELSE 0 END) AS judged, "
                  "MAX(made_on) AS last FROM predictions")
    line("Buy-or-wait calls (shopping, flights, hotels)", "ok" if calls.get("n") else "never",
         f"{calls.get('n', 0)} calls made, {int(calls.get('judged') or 0)} judged" + (f"; the latest on {calls['last']}" if calls.get("last") else ""))
    return out
