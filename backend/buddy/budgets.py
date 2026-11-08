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

Alerts go out once each, per month, when spend passes 50%, 80% and 100% of either budget, and when the month's
forecast heads over the overall budget.
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


def pay_per_use(memory: Memory) -> float:
    """This month's spend on pay-per-use services: Claude, other AI models, priced outside APIs."""
    total = usage.month_to_date(memory)
    for sql in ("SELECT COALESCE(SUM(cost_usd), 0) AS s FROM model_calls WHERE at >= ? AND provider != 'claude'",
                "SELECT COALESCE(SUM(cost_usd), 0) AS s FROM api_calls WHERE at >= ?"):
        try:
            total += float(memory.db.execute(sql, (_month_start(),)).fetchone()["s"] or 0)
        except Exception:
            pass
    return round(total, 4)


def committed(memory: Memory) -> float:
    """The month's committed costs in full (server, plans, fixed), as the last cost report found them."""
    return _num(memory.get(COMMITTED_KEY), 0.0)


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
        if cap and usage.month_to_date(memory) >= cap:
            return True
    except Exception:
        return False
    return all_in_stopped(memory)


def check_claude() -> None:
    """Called before every Claude request (config.claude_client)."""
    if claude_stopped():
        raise BudgetStop("Claude is stopped for the month: a budget is reached.")


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
    m = SET.search(text or "")
    if not m:
        return ""
    which = "claude" if m.group(1).lower() in ("claude", "ai") else "all_in"
    return set_budget(memory, which, float(m.group(2)))


def status(memory: Memory, finops_report: dict | None = None) -> dict:
    claude = round(usage.month_to_date(memory), 2)
    cap, budget = claude_cap(memory), all_in_budget(memory)
    fixed, variable = committed(memory), pay_per_use(memory)
    out = {"claude": {"spent_usd": claude, "cutoff_usd": cap or None, "stopped": claude_stopped(memory),
                      "used_pct": round(claude / cap * 100) if cap else None},
           "all_in": {"budget_usd": budget or None, "committed_usd": round(fixed, 2),
                      "pay_per_use_usd": round(variable, 2), "counted_usd": round(fixed + variable, 2),
                      "left_usd": round(max(budget - fixed - variable, 0), 2) if budget else None,
                      "stopped": all_in_stopped(memory),
                      "counted_pct": round((fixed + variable) / budget * 100) if budget else None}}
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


BUDGET_TOOL = {
    "name": "budgets",
    "description": "The user's monthly budgets: the Claude cutoff (Claude stops for the month when reached; default "
    "$20) and the overall budget, a switch for everything Buddy costs (when committed costs plus pay-per-use reach it, "
    "Claude and every paid service stop until the 1st), with alerts at 50%, 80% and 100%. status: both, spent and "
    "forecast. set (which: claude | all_in, usd): lowering one is done at once; raising one, or turning it off, needs "
    "the user's yes. 0 turns one off.",
    "input_schema": {"type": "object", "properties": {
        "action": {"type": "string", "enum": ["status", "set"]},
        "which": {"type": "string", "enum": ["claude", "all_in"]}, "usd": {"type": "number"}},
        "required": ["action"], "additionalProperties": False},
}


async def run_tool(memory: Memory, args: dict, finops=None, thrift=None) -> str:
    import json  # noqa: PLC0415

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
