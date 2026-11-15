"""Buddy Maestro's spending view: what Buddy's AI use costs, which part of Buddy spends it, and every step down
to the single request.

Built from the log of model requests (model_calls: one row per request, kept 90 days; previews are short and
masked). Nothing here calls a model.

  Summary     spending in the period, this month so far against last month, where this month is heading at the
              current daily rate, requests, the cost of an average request, how many failed, the share that a
              free model answered
  By day      spending and requests for each day of the period
  Components  the parts of Buddy (COMPONENTS): the conversation, voice, email and memory, money, investing,
              shopping, travel and places, intelligence, health, research, learning, running Buddy. Every
              purpose a request is logged with belongs to one component, by the rules below; a purpose no rule
              knows is shown under "Other" by its own name, so nothing is hidden
  Drill down  a component -> its purposes -> one purpose's models, days and latest requests
  By provider and by model, for the whole period or for the part drilled into

  Since reset "Reset now" marks this moment. "Since reset" then shows only what was spent after it: useful after
              changing a model or a setting, to see what it costs from here. Nothing is deleted and the budget
              limits are not touched; it only changes where this view starts counting. If the budgets were
              started fresh more recently (budgets.py), that moment is used instead.

The month-end figure is this month's spending so far divided by the days elapsed, times the days in the
month. It is arithmetic on the pace so far, not a prediction of what you will do.
"""

import calendar
import re
from datetime import datetime, timedelta, timezone

from .memory import Memory
from .tools import ToolError

COMPONENTS = (  # id, title, what it covers, the purposes that belong to it
    ("conversation", "The conversation", "Talking with you: replies, the expert step-up, thinking, quick free answers",
     r"chat|chat_free|expert|think|reminder|general:.*|free:.*|small_talk"),
    ("voice", "Voice and calls", "Hearing you, listen-in, deciding if speech was meant for Buddy, phone calls",
     r"speech|listen.*|addressing|phone_call|voice.*"),
    ("email", "Email and memory", "Sorting email, life memory, documents, the overnight memory tidy-up, briefings",
     r"mail.*|lifelog|lifemail|life.*|library|brain:.*|briefing|evening_wrap|organis.*"),
    ("money", "Money", "Bank categories, statements, cards and credit, the money review",
     r"bankfeed|finance:.*|money.*|credit.*|card.*|subscription.*|budget.*"),
    ("investing", "Investing", "Market scans and takes, signals, funds, the investor lenses, market school",
     r"market.*|signals.*|funds|lenses|invest.*|paper.*"),
    ("shopping", "Shopping and deals", "Price watches, deal reading, shopping research, seasons",
     r"deals?.*|shopping|seasons|shop.*|whentobuy"),
    ("travel", "Travel and places", "Trips, transit, dining, events, weather, the home",
     r"travel.*|transit|dining|events|weather|home|flight.*|hotel.*|commute"),
    ("intelligence", "Intelligence", "Buddy Intelligence: labels, write-ups, companies and relationships; news and trends",
     r"intelligence|maestro|techradar|trends|news.*"),
    ("health", "Health", "The daily health report and health questions", r"health.*"),
    ("research", "Research and the web", "Research reports and reading web pages", r"research|web_digest|web.*"),
    ("learning", "Learning", "English review and the morning tutor", r"tutor|morning_tutor|vocabulary"),
    ("running", "Running Buddy", "Its own code, self-checks, documentation answers, security, building agents",
     r"code|selfcheck|codedocs.*|security|build_agent|diagnos.*|modelscout"),
)
_RULES = [(cid, re.compile(f"^(?:{pattern})$")) for cid, _t, _w, pattern in COMPONENTS]
OTHER = ("other", "Other", "Purposes no rule above knows yet, listed by their own names")
PERIODS = (7, 30, 90)
RESET_KEY = "maestro:spend_since"


def reset_at(memory: Memory) -> str:
    """The moment 'since reset' counts from: the later of this view's own reset and the budgets' fresh start."""
    from . import budgets  # noqa: PLC0415
    return max(memory.get(RESET_KEY) or "", budgets.last_reset(memory) or "")


def reset(memory: Memory, now: datetime | None = None) -> str:
    """Mark now as the start of 'since reset'. Deletes nothing and changes no limit."""
    now = now or datetime.now(timezone.utc)
    memory.set(RESET_KEY, now.isoformat())
    return "Reset. \"Since reset\" now counts from this moment. Nothing was deleted and your budget limits are unchanged."


def clear_reset(memory: Memory) -> str:
    memory.set(RESET_KEY, "")
    return "The reset point is cleared."


def component_of(purpose: str) -> str:
    return next((cid for cid, rule in _RULES if rule.match(str(purpose or ""))), "other")


def _name(purpose: str) -> str:
    from .insights import purpose_name  # noqa: PLC0415
    return purpose_name(purpose)


def _sum(rows: list[dict]) -> dict:
    n = len(rows)
    usd = sum(r["cost_usd"] for r in rows)
    times = sorted(r["seconds"] for r in rows if r["seconds"] is not None and r["ok"])
    return {"usd": round(usd, 4), "requests": n, "failed": sum(1 for r in rows if not r["ok"]),
            "per_request": round(usd / n, 5) if n else 0.0,
            "tokens_in": sum(r["input_tokens"] + r["cache_read_tokens"] + r["cache_write_tokens"] for r in rows),
            "tokens_out": sum(r["output_tokens"] for r in rows),
            "median_seconds": round(times[len(times) // 2], 2) if times else None}


def _groups(rows: list[dict], key, total: float) -> list[dict]:
    found: dict[str, list[dict]] = {}
    for r in rows:
        found.setdefault(key(r), []).append(r)
    out = [{"name": name, **_sum(rs), "share": round(sum(r["cost_usd"] for r in rs) / total, 4) if total else 0.0}
           for name, rs in found.items()]
    return sorted(out, key=lambda g: (-g["usd"], -g["requests"], g["name"]))


def _month(memory: Memory, now: datetime) -> dict:
    """This month so far, last month, and where this month is heading at the pace so far."""
    def total(prefix: str) -> float:
        row = memory.db.execute("SELECT COALESCE(SUM(cost_usd), 0) AS t FROM usage WHERE day LIKE ?", (prefix + "-%",)).fetchone()
        return float(row["t"])
    last = (now.replace(day=1) - timedelta(days=1))
    spent, days_in = total(f"{now:%Y-%m}"), calendar.monthrange(now.year, now.month)[1]
    return {"month": f"{now:%Y-%m}", "usd": round(spent, 2), "last_month_usd": round(total(f"{last:%Y-%m}"), 2),
            "heading_for_usd": round(spent / now.day * days_in, 2), "day": now.day, "days_in_month": days_in}


def view(memory: Memory, days: int = 30, component: str = "", purpose: str = "", now: datetime | None = None,
         since_reset: bool = False) -> dict:
    """The spending view for a period (or since the last reset), optionally drilled into one component or one
    purpose."""
    now = now or datetime.now(timezone.utc)
    days = days if days in PERIODS else 30
    marker = reset_at(memory)
    since_reset = bool(since_reset and marker)
    if since_reset:  # as many whole days as have passed, for the chart (the log keeps 90)
        days = max(1, min(90, (now.date() - datetime.fromisoformat(marker).astimezone(timezone.utc).date()).days + 1))
    known = {c[0]: c for c in (*COMPONENTS, OTHER)}
    if component and component not in known:
        raise ToolError("Components are: " + ", ".join(known))
    since = marker if since_reset else (now - timedelta(days=days)).isoformat()
    every = [dict(r) for r in memory.db.execute(
        "SELECT id, at, provider, model, purpose, input_tokens, output_tokens, cache_read_tokens, cache_write_tokens, "
        "cost_usd, seconds, ok, stop, request FROM model_calls WHERE at >= ? ORDER BY id DESC", (since,))]
    for r in every:
        r["component"] = component_of(r["purpose"])
    total = sum(r["cost_usd"] for r in every)
    if purpose:
        component = component or component_of(purpose)
    rows = [r for r in every if (not component or r["component"] == component) and (not purpose or r["purpose"] == purpose)]
    scoped = sum(r["cost_usd"] for r in rows)
    per_day: dict[str, dict] = {}
    for n in range(days - 1, -1, -1):
        day = (now - timedelta(days=n)).strftime("%Y-%m-%d")
        per_day[day] = {"day": day, "usd": 0.0, "requests": 0}
    for r in rows:
        if r["at"][:10] in per_day:
            per_day[r["at"][:10]]["usd"] += r["cost_usd"]
            per_day[r["at"][:10]]["requests"] += 1
    out = {
        "days": days, "periods": list(PERIODS), "month": _month(memory, now),
        "reset_at": marker, "since_reset": since_reset,
        "totals": {**_sum(every), "free_share": round(sum(1 for r in every if r["provider"] != "claude") / len(every), 3)
                   if every else 0.0},
        "scope": {"component": component, "purpose": purpose,
                  "title": _name(purpose) if purpose else known[component][1] if component else "All of Buddy",
                  "covers": "" if purpose else known[component][2] if component else "",
                  **_sum(rows), "share": round(scoped / total, 4) if total else 0.0},
        "by_day": [{**d, "usd": round(d["usd"], 4)} for d in per_day.values()],
        "by_provider": _groups(rows, lambda r: r["provider"], scoped),
        "by_model": _groups(rows, lambda r: r["model"], scoped)[:10],
    }
    if not component:
        groups = {g["name"]: g for g in _groups(every, lambda r: r["component"], total)}
        out["components"] = [{"id": cid, "title": title, "covers": covers, **{k: v for k, v in groups[cid].items() if k != "name"}}
                             for cid, title, covers, *_ in (*COMPONENTS, OTHER) if cid in groups]
        out["components"].sort(key=lambda c: (-c["usd"], -c["requests"]))
    else:
        out["purposes"] = [{"purpose": g["name"], **{**g, "name": _name(g["name"])}}
                           for g in _groups([r for r in every if r["component"] == component], lambda r: r["purpose"],
                                            sum(r["cost_usd"] for r in every if r["component"] == component))]
    if purpose:
        out["requests"] = [{"at": r["at"], "model": r["model"], "provider": r["provider"], "usd": round(r["cost_usd"], 5),
                            "seconds": r["seconds"], "ok": bool(r["ok"]), "stop": r["stop"],
                            "tokens_in": r["input_tokens"] + r["cache_read_tokens"] + r["cache_write_tokens"],
                            "tokens_out": r["output_tokens"], "preview": (r["request"] or "")[:160]} for r in rows[:30]]
    return out
