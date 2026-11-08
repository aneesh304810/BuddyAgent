"""Budgets: a hard monthly stop for Claude, an overall budget for everything Buddy costs, and alerts.

Claude cutoff (default $20 a month, BUDDY_CLAUDE_KILL_USD, or set from the app or by voice): once Claude's spend this
month reaches it, every Claude call is refused before it leaves the server: chat, briefings, scans, research,
everything. Free models and code-only work carry on. Only the user raises it, in their own words, read in code
("set my Claude budget to 30") or from the app's Insights screen; Claude can never raise it.

Overall budget, the whole-Buddy switch (BUDDY_ALLIN_BUDGET_USD, or set the same ways): everything Buddy costs. The
month's committed costs (the server, plans, per-bank prices, fixed costs: owed whatever Buddy does, so counted in full
from the 1st) plus what Buddy has spent on pay-per-use services (Claude, other AI models, web search, flight and travel
prices, FlightAware, Groq speech) must stay under it. When they reach it, every paid call stops until the 1st: Claude
(config.claude_client) and every paid outside service (an audit hook on urllib refuses PAID_HOSTS; Groq speech falls
back to the local Whisper). Free services, reminders, the bank feed and the screens keep working.

Alerts go out once each, per month, when spend passes 50%, 80% and 100% of either budget.

Where it's going (predict): the month's end from the last 3 days' pace (it reacts to a sudden storm of calls, where a
month average would hide it), the day a budget would be reached, and what's driving it. When a forecast passes a
budget, Buddy says so and holds: background paid work (briefings, scans, research, the brain's reading, paid searches)
waits for the user's approval; their own chat carries on. "Yes" approves this pace (asked again only if the forecast
grows another 20%); "no" keeps the hold (saver) for the month. The answer is read in code, or given in Insights.
"""

import logging
import os
import re
import sys
import time
import urllib.parse
from datetime import datetime, timezone

from . import usage
from .memory import Memory
from .tools import ToolError, local_now

log = logging.getLogger(__name__)

CLAUDE_KEY, ALL_IN_KEY, COMMITTED_KEY = "budget:claude_kill_usd", "budget:all_in_usd", "budget:committed_usd"
# Pay-per-use services stopped by the whole-Buddy switch (free ones, the bank feed and Claude's own host are not here:
# Claude is stopped in its client).
PAID_HOSTS = {"google.serper.dev", "serpapi.com", "api.duffel.com", "aeroapi.flightaware.com", "api.groq.com"}
_stop_cache: dict[int, tuple[float, bool]] = {}
SINCE_KEY = "budget:since"
HOLD_KEY, ASKED_KEY, APPROVED_KEY = "budget:hold", "budget:pace_asked", "budget:pace_approved"
PACE_DAYS, REASK_GROWTH, MIN_PACE_USD = 3, 1.2, 0.5
STEPS = (50, 80, 100)
_memory: Memory | None = None  # the server's memory, so the Claude client can check the cutoff


class BudgetStop(RuntimeError):
    """A Claude call refused: this month's Claude cutoff is reached."""


_hooked = False


def attach(memory: Memory | None) -> None:
    global _memory, _hooked
    _memory = memory
    _stop_cache.clear()
    if memory is not None and not _hooked:
        sys.addaudithook(_audit)
        _hooked = True


def _audit(event: str, args) -> None:
    """Every urllib request: a paid host is refused once the overall budget is reached."""
    if event != "urllib.Request" or _memory is None:
        return
    host = urllib.parse.urlsplit(str(args[0])).hostname or ""
    if host in PAID_HOSTS and all_in_stopped(_memory):
        raise BudgetStop(f"{host}: paid services are stopped, this month's ${all_in_budget(_memory):.0f} budget is "
                         "reached.")
    if host in PAID_HOSTS and not _foreground() and held(_memory):
        raise BudgetStop(f"{host}: background paid work is paused until you approve this month's pace.")


def _num(value, default: float) -> float:
    try:
        return max(float(value), 0.0)
    except (TypeError, ValueError):
        return default


def claude_cap(memory: Memory) -> float:
    """The Claude cutoff in USD for a month; 0 = off."""
    return _num(memory.get(CLAUDE_KEY), _num(os.getenv("BUDDY_CLAUDE_KILL_USD", "20"), 20.0))


def all_in_budget(memory: Memory) -> float:
    return _num(memory.get(ALL_IN_KEY), _num(os.getenv("BUDDY_ALLIN_BUDGET_USD", "0"), 0.0))


def _month_start() -> str:
    first = local_now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return first.astimezone(timezone.utc).isoformat()


def counting_from(memory: Memory) -> str:
    """When the budgets started counting this month: '' = the 1st, or the moment the user started fresh."""
    since = memory.get(SINCE_KEY) or ""
    return since if since >= _month_start() else ""


def start_fresh(memory: Memory) -> str:
    """Let this month's spending so far go: the budgets count from now (and from the 1st again next month)."""
    memory.set(SINCE_KEY, datetime.now(timezone.utc).isoformat())
    month = local_now().strftime("%Y-%m")
    for key in (HOLD_KEY, ASKED_KEY, APPROVED_KEY, "budget:pace_asked_at", f"budget:told:claude:{month}",
                f"budget:told:all_in:{month}"):
        memory.set(key, "")
    _stop_cache.clear()
    return ("Done: this month's spending so far is let go. The budgets count from now (the committed costs only for "
            "the days left), and from the 1st again next month.")


def claude_spent(memory: Memory) -> float:
    """Claude's spend this month, from the fresh start if there was one."""
    since = counting_from(memory)
    if not since:
        return usage.month_to_date(memory)
    try:
        return float(memory.db.execute("SELECT COALESCE(SUM(cost_usd), 0) FROM model_calls WHERE provider = 'claude' "
                                       "AND at >= ?", (since,)).fetchone()[0] or 0)
    except Exception:
        return 0.0


def pay_per_use(memory: Memory) -> float:
    """This month's spend on pay-per-use services: Claude, other AI models, priced outside APIs."""
    total = claude_spent(memory)
    since = counting_from(memory) or _month_start()
    for sql in ("SELECT COALESCE(SUM(cost_usd), 0) AS s FROM model_calls WHERE at >= ? AND provider != 'claude'",
                "SELECT COALESCE(SUM(cost_usd), 0) AS s FROM api_calls WHERE at >= ?"):
        try:
            total += float(memory.db.execute(sql, (since,)).fetchone()["s"] or 0)
        except Exception:
            pass
    return round(total, 4)


def committed(memory: Memory) -> float:
    """The month's committed costs (server, plans, fixed), as the last cost report found them: in full, or for the
    days left after a fresh start."""
    full = _num(memory.get(COMMITTED_KEY), 0.0)
    since = counting_from(memory)
    if not since or not full:
        return full
    import calendar  # noqa: PLC0415
    now = local_now()
    days = calendar.monthrange(now.year, now.month)[1]
    start = datetime.fromisoformat(since).astimezone(now.tzinfo)
    used = (start - start.replace(day=1, hour=0, minute=0, second=0, microsecond=0)).total_seconds() / 86400
    return round(full * max(days - used, 0) / days, 2)


def save_committed(memory: Memory, lines: list) -> float:
    """From finops.report: every line that is owed for the month whatever Buddy does."""
    total = round(sum(float(getattr(l, "forecast", 0) or 0) for l in lines
                      if getattr(l, "source", "") in ("live", "plan price", "fixed", "your price")
                      and getattr(l, "provider", "") != "Claude"), 2)
    memory.set(COMMITTED_KEY, str(total))
    _stop_cache.clear()
    return total


def all_in_stopped(memory: Memory | None = None) -> bool:
    """The whole-Buddy switch: committed costs plus pay-per-use spend have reached the overall budget."""
    memory = memory or _memory
    if memory is None:
        return False
    hit = _stop_cache.get(id(memory))
    if hit and time.monotonic() - hit[0] < 20:
        return hit[1]
    try:
        budget = all_in_budget(memory)
        stopped = bool(budget) and committed(memory) + pay_per_use(memory) >= budget
    except Exception:
        stopped = False
    _stop_cache[id(memory)] = (time.monotonic(), stopped)
    return stopped


def claude_stopped(memory: Memory | None = None) -> bool:
    """Claude may not be called: its own cutoff, or the whole-Buddy switch."""
    memory = memory or _memory
    if memory is None:
        return False
    try:
        cap = claude_cap(memory)
        if cap and claude_spent(memory) >= cap:
            return True
    except Exception:
        return False
    return all_in_stopped(memory)


def _foreground() -> bool:
    """Inside the user's own turn (chat or talk), or work it started."""
    return bool(usage.current_turn.get())


def held(memory: Memory | None = None) -> bool:
    """Background paid work is waiting for the user's approval of the forecast pace."""
    memory = memory or _memory
    if memory is None:
        return False
    try:
        return (memory.get(HOLD_KEY) or "") == local_now().strftime("%Y-%m")
    except Exception:
        return False


def check_claude() -> None:
    """Called before every Claude request (config.claude_client)."""
    if claude_stopped():
        raise BudgetStop("Claude is stopped for the month: a budget is reached.")
    if held() and not _foreground():
        raise BudgetStop("Background AI work is paused until you approve this month's pace.")


def set_budget(memory: Memory, which: str, usd: float) -> str:
    usd = round(_num(usd, -1), 2)
    if usd < 0 or usd > 10_000:
        raise ToolError("Give an amount in dollars, like 20 (0 turns it off).")
    key = CLAUDE_KEY if which == "claude" else ALL_IN_KEY
    memory.set(key, str(usd))
    memory.set(f"budget:told:{which}:{local_now():%Y-%m}", "")  # alerts start again against the new amount
    _stop_cache.clear()
    name = "Claude cutoff" if which == "claude" else "overall monthly budget"
    if not usd:
        return f"The {name} is off."
    if which == "claude":
        return f"The {name} is now ${usd:.0f} a month. Claude stops for the rest of the month when it's reached."
    fixed = committed(memory)
    out = (f"The {name} is now ${usd:.0f} a month: Buddy won't cost more. When it's reached, Claude and every paid "
           "service stop until the 1st; I'll tell you at 50%, 80% and 100%.")
    if fixed:
        out += (f" ${fixed:.2f} of it is already committed this month (server, plans, fixed costs), so "
                f"${max(usd - fixed, 0):.2f} is left for pay-per-use." if fixed < usd else
                f" Your committed costs (${fixed:.2f}: server, plans, fixed) already reach it, so paid services are "
                "stopped now. Those committed costs can't be switched off from Buddy.")
    return out


SET = re.compile(r"(?i)\b(?:set|raise|increase|change|make|lower|reduce|put)\s+(?:my\s+|the\s+)?"
                 r"(claude|ai|overall|total|all[- ]in|expense|monthly)\s+(?:monthly\s+)?(?:budget|cutoff|cap|limit)"
                 r"(?:\s+(?:to|at))?\s+\$?(\d+(?:\.\d+)?)")


def command(memory: Memory, text: str) -> str:
    """A budget change in the user's own words, done in code (no Claude): '' when the text isn't one."""
    if FRESH.search(text or ""):
        return start_fresh(memory)
    if PACE.search(text or ""):
        return approve_pace(memory, True)
    if SAVER.search(text or ""):
        return approve_pace(memory, False)
    m = SET.search(text or "")
    if not m:
        return ""
    which = "claude" if m.group(1).lower() in ("claude", "ai") else "all_in"
    return set_budget(memory, which, float(m.group(2)))


def status(memory: Memory, finops_report: dict | None = None) -> dict:
    claude = round(claude_spent(memory), 2)
    cap, budget = claude_cap(memory), all_in_budget(memory)
    fixed, variable = committed(memory), pay_per_use(memory)
    out = {"claude": {"spent_usd": claude, "cutoff_usd": cap or None, "stopped": claude_stopped(memory),
                      "used_pct": round(claude / cap * 100) if cap else None},
           "all_in": {"budget_usd": budget or None, "committed_usd": round(fixed, 2),
                      "pay_per_use_usd": round(variable, 2), "counted_usd": round(fixed + variable, 2),
                      "left_usd": round(max(budget - fixed - variable, 0), 2) if budget else None,
                      "stopped": all_in_stopped(memory),
                      "counted_pct": round((fixed + variable) / budget * 100) if budget else None},
           "held": held(memory), "prediction": predict(memory), "counting_from": counting_from(memory) or None}
    if finops_report:
        mtd, forecast = finops_report.get("month_to_date_usd", 0.0), finops_report.get("forecast_usd", 0.0)
        out["all_in"].update({"spent_usd": mtd, "forecast_usd": forecast,
                              "used_pct": round(mtd / budget * 100) if budget else None,
                              "heading_over": bool(budget) and forecast > budget})
    return out


def stop_message(memory: Memory, which: str = "") -> str:
    if which == "all_in" or (not which and all_in_stopped(memory)):
        return (f"Buddy has reached this month's ${all_in_budget(memory):.0f} budget, so Claude and every paid service "
                "are stopped until the 1st. Reminders, alerts and your screens still work. To go on, say \"set my "
                "overall budget to\" an amount, or change it in Insights.")
    return (f"Claude has reached this month's ${claude_cap(memory):.0f} cutoff, so it's stopped until the 1st. "
            "Reminders, alerts and your screens still work. To go on, say \"set my Claude budget to\" an amount, or "
            "change it in Insights.")


def _once(memory: Memory, which: str, mark: str) -> bool:
    key = f"budget:told:{which}:{local_now():%Y-%m}"
    told = (memory.get(key) or "").split(",")
    if mark in told:
        return False
    memory.set(key, ",".join([t for t in told if t] + [mark]))
    return True


def alerts(memory: Memory, finops_report: dict | None = None) -> list[str]:
    """The budget alerts due now (each said once a month). Code only, no AI."""
    out = []
    s = status(memory, finops_report)
    c = s["claude"]
    if c["cutoff_usd"]:
        for step in STEPS:
            if c["used_pct"] >= step and _once(memory, "claude", str(step)):
                out = [m for m in out if not m.startswith("Claude")]  # the highest step only
                out.append(stop_message(memory, "claude") if step == 100 else
                           f"Claude has used {step}% of this month's ${c['cutoff_usd']:.0f} cutoff "
                           f"(${c['spent_usd']:.2f}). At 100% it stops until the 1st.")
    a = s["all_in"]
    if a.get("budget_usd"):
        said = []
        for step in STEPS:
            if a["counted_pct"] >= step and _once(memory, "all_in", str(step)):
                said = [stop_message(memory, "all_in") if step == 100 else
                        f"Buddy has used {step}% of this month's ${a['budget_usd']:.0f} budget "
                        f"(${a['committed_usd']:.2f} committed + ${a['pay_per_use_usd']:.2f} pay-per-use). At 100% "
                        "Claude and every paid service stop until the 1st."]
        if a.get("heading_over") and a["counted_pct"] < 100 and _once(memory, "all_in", "forecast"):
            said.append(f"At this pace the month will cost about ${a['forecast_usd']:.0f}, over your "
                        f"${a['budget_usd']:.0f} budget. Ask me what's costing most.")
        out += said
    return out


# where it's going --------------------------------------------------------------------------------------------
def _since(days: float) -> str:
    from datetime import timedelta  # noqa: PLC0415
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


def _sum(memory: Memory, sql: str, args: tuple) -> float:
    try:
        return float(memory.db.execute(sql, args).fetchone()[0] or 0)
    except Exception:
        return 0.0


def drivers(memory: Memory, days: float = PACE_DAYS, top: int = 3) -> list[dict]:
    """What the pay-per-use money went on lately, biggest first."""
    since = _since(days)
    rows = []
    try:
        rows = [dict(r) for r in memory.db.execute(
            "SELECT purpose AS what, provider, COUNT(*) AS calls, SUM(cost_usd) AS usd FROM model_calls WHERE at >= ? "
            "AND cost_usd > 0 GROUP BY purpose, provider", (since,))]
        rows += [dict(r) for r in memory.db.execute(
            "SELECT provider AS what, provider, COUNT(*) AS calls, SUM(cost_usd) AS usd FROM api_calls WHERE at >= ? "
            "AND cost_usd > 0 GROUP BY provider", (since,))]
    except Exception:
        pass
    rows.sort(key=lambda r: -(r["usd"] or 0))
    return [{"what": r["what"], "provider": r["provider"], "calls": r["calls"], "usd": round(r["usd"], 2)}
            for r in rows[:top]]


def predict(memory: Memory) -> dict:
    """The month's end at the recent pace, for each budget that's set."""
    import calendar  # noqa: PLC0415
    from datetime import timedelta  # noqa: PLC0415
    now = local_now()
    days_in = calendar.monthrange(now.year, now.month)[1]
    elapsed = max((now - now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)).total_seconds() / 86400, 0.25)
    left = max(days_in - elapsed, 0.0)
    window = min(PACE_DAYS, elapsed)
    claude_rate = _sum(memory, "SELECT SUM(cost_usd) FROM model_calls WHERE provider = 'claude' AND at >= ?",
                       (_since(window),)) / window
    other_rate = (_sum(memory, "SELECT SUM(cost_usd) FROM model_calls WHERE provider != 'claude' AND at >= ?",
                       (_since(window),)) + _sum(memory, "SELECT SUM(cost_usd) FROM api_calls WHERE at >= ?",
                                                 (_since(window),))) / window
    claude_now, ppu_now, fixed = claude_spent(memory), pay_per_use(memory), committed(memory)
    out = {"daily_pace_usd": round(claude_rate + other_rate, 2), "days_left": round(left, 1),
           "drivers": drivers(memory), "limits": []}

    def limit(which: str, cap: float, spent: float, rate: float) -> None:
        if not cap:
            return
        forecast = spent + rate * left
        reach = None
        if spent < cap and rate > 0 and forecast > cap:
            reach = (now + timedelta(days=(cap - spent) / rate)).strftime("%a %b %-d")
        out["limits"].append({"which": which, "cap_usd": round(cap, 2), "spent_usd": round(spent, 2),
                              "forecast_usd": round(forecast, 2), "over": forecast > cap, "reach_on": reach})
    limit("claude", claude_cap(memory), claude_now, claude_rate)
    limit("all_in", all_in_budget(memory), fixed + ppu_now, claude_rate + other_rate)
    return out


NAMES = {"briefing": "briefings and nudges", "chat": "chat", "research": "research", "listen": "Listen-in",
         "think": "Listen-in thinking", "expert": "the expert model", "lifelog": "the life log", "library": "the library",
         "deals": "deal checks", "serper": "web search", "serpapi": "flight and hotel prices", "duffel": "flight offers",
         "flightaware": "FlightAware", "speech": "speech to text"}


def _driver_text(d: list[dict]) -> str:
    return ", ".join(f"{NAMES.get(x['what'], x['what'].replace('_', ' '))} ${x['usd']:.2f} ({x['calls']} calls)"
                     for x in d)


def pace_check(memory: Memory) -> str:
    """Every few minutes: when a forecast passes a budget, hold background paid work and ask (once per growth step).
    Returns the question to send, or ''."""
    month = local_now().strftime("%Y-%m")
    p = predict(memory)
    over = [l for l in p["limits"] if l["over"] and l["spent_usd"] < l["cap_usd"]]
    if not over or p["daily_pace_usd"] * PACE_DAYS < MIN_PACE_USD:
        return ""
    worst = max(over, key=lambda l: l["forecast_usd"] / l["cap_usd"])
    approved = _num((memory.get(APPROVED_KEY) or ":").split(":")[-1], 0.0) \
        if (memory.get(APPROVED_KEY) or "").startswith(month + ":") else 0.0
    if approved and worst["forecast_usd"] < approved * REASK_GROWTH:
        return ""  # this pace is approved
    asked = memory.get(ASKED_KEY) or ""
    last = _num(asked.split(":")[-1], 0.0) if asked.startswith(month + ":") else 0.0
    memory.set(HOLD_KEY, month)
    if last and worst["forecast_usd"] < last * REASK_GROWTH:
        return ""  # already asked about this pace; the hold stays until they answer
    memory.set(ASKED_KEY, f"{month}:{worst['forecast_usd']:.2f}")
    memory.set("budget:pace_asked_at", datetime.now(timezone.utc).isoformat())
    name = "Claude cutoff" if worst["which"] == "claude" else "budget"
    text = (f"Heads up: at this pace {'Claude' if worst['which'] == 'claude' else 'Buddy'} will cost about ${worst['forecast_usd']:.0f} this month, over your "
            f"${worst['cap_usd']:.0f} {name}" + (f" (you'd reach it around {worst['reach_on']})" if worst["reach_on"]
                                                  else "") + ". ")
    if p["drivers"]:
        text += f"The last {PACE_DAYS} days went mostly on {_driver_text(p['drivers'])}. "
    return text + ("I've paused background paid work (briefings, scans, research, paid searches); your chat still "
                   "works. Keep going at this pace? Say \"approve the pace\" or tap Approve in Insights; "
                   "\"stay in saver\" keeps it paused.")


def just_asked(memory: Memory, minutes: int = 30) -> bool:
    """The pace question went out lately, so a plain yes or no is its answer."""
    at = memory.get("budget:pace_asked_at") or ""
    try:
        return (datetime.now(timezone.utc) - datetime.fromisoformat(at)).total_seconds() < minutes * 60
    except ValueError:
        return False


def not_delivered(memory: Memory) -> None:
    """The question couldn't be sent: ask again next time (the hold stays)."""
    memory.set(ASKED_KEY, "")
    memory.set("budget:pace_asked_at", "")


def approve_pace(memory: Memory, yes: bool) -> str:
    month = local_now().strftime("%Y-%m")
    if yes:
        p = predict(memory)
        top = max([l["forecast_usd"] for l in p["limits"]] or [0.0])
        memory.set(APPROVED_KEY, f"{month}:{top:.2f}")
        memory.set(HOLD_KEY, "")
        memory.set("budget:pace_asked_at", "")
        return (f"Approved: background work is back on, at a pace of about ${top:.0f} this month. I'll ask again if it "
                "climbs another 20%. The budgets' hard stops still apply.")
    memory.set(HOLD_KEY, month)
    memory.set("budget:pace_asked_at", "")
    return ("Staying in saver: background paid work stays paused this month (reminders in plain words, the bank feed "
            "and your screens carry on; chat works). Say \"approve the pace\" anytime to resume.")


FRESH = re.compile(r"(?i)\b(start (?:my |the )?budgets? (?:fresh|from now|again)|count (?:my |the )?budgets? from now|"
                   r"let (?:this month'?s )?(?:spending|overspend) go)\b")
PACE = re.compile(r"(?i)\b(approve (?:the |this )?(?:pace|spending|cost)|keep (?:going|spending) at this pace|"
                  r"resume background(?: work)?)\b")
SAVER = re.compile(r"(?i)\b(stay (?:in|on) saver|saver mode|keep (?:it |background (?:work )?)?paused|"
                   r"pause background(?: work)?)\b")


BUDGET_TOOL = {
    "name": "budgets",
    "description": "The user's monthly budgets: the Claude cutoff (Claude stops for the month when reached; default "
    "$20) and the overall budget, a switch for everything Buddy costs (when committed costs plus pay-per-use reach it, "
    "Claude and every paid service stop until the 1st), with alerts at 50%, 80% and 100%. status: both, spent and "
    "forecast. predict: where the month is heading at the last 3 days' pace, the day a budget would be reached, and "
    "what's driving it (if background work is paused waiting for approval, only the user's own words approve it: "
    "\"approve the pace\" or Insights). set (which: claude | all_in, usd): lowering one is done at once; raising one, or turning it off, needs "
    "the user's yes. 0 turns one off.",
    "input_schema": {"type": "object", "properties": {
        "action": {"type": "string", "enum": ["status", "predict", "set"]},
        "which": {"type": "string", "enum": ["claude", "all_in"]}, "usd": {"type": "number"}},
        "required": ["action"], "additionalProperties": False},
}


async def run_tool(memory: Memory, args: dict, finops=None, thrift=None) -> str:
    import json  # noqa: PLC0415

    if args.get("action") == "predict":
        return json.dumps(predict(memory))
    if args.get("action") == "set":
        which = args.get("which") or "claude"
        usd = _num(args.get("usd"), -1)
        now = claude_cap(memory) if which == "claude" else all_in_budget(memory)
        raising = now > 0 and (not usd or usd > now)
        if raising:  # Claude can never approve more spending: the user's own yes
            if thrift is None:
                raise ToolError("Only the user can raise it: say \"set my Claude budget to\" an amount.")

            async def run() -> str:
                return set_budget(memory, which, usd)
            name = "Claude cutoff" if which == "claude" else "overall budget"
            return thrift.ask_action(f"Raise the {name} to ${usd:.0f} a month?" if usd else f"Turn the {name} off?",
                                     run)
        return set_budget(memory, which, usd)
    report = None
    if finops is not None:
        try:
            report = await finops.report()
        except Exception:
            log.info("budgets: costs unavailable")
    return json.dumps(status(memory, report))
