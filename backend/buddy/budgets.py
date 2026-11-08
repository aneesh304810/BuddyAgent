"""Budgets: a hard monthly stop for Claude, an overall budget for everything Buddy costs, and alerts.

Claude cutoff (default $20 a month, BUDDY_CLAUDE_KILL_USD, or set from the app or by voice): once Claude's spend this
month reaches it, every Claude call is refused before it leaves the server: chat, briefings, scans, research,
everything. Free models and code-only work carry on. Only the user raises it, in their own words, read in code
("set my Claude budget to 30") or from the app's Insights screen; Claude can never raise it.

Overall budget (BUDDY_ALLIN_BUDGET_USD, or set the same ways): everything Buddy costs (Claude, the server, APIs, free
models, fixed costs, from finops.py). Alerts go out once each, per month, when spend passes 50%, 80% and 100% of
either budget, and when the month's forecast heads over the overall budget.
"""

import logging
import os
import re

from . import usage
from .memory import Memory
from .tools import ToolError, local_now

log = logging.getLogger(__name__)

CLAUDE_KEY, ALL_IN_KEY = "budget:claude_kill_usd", "budget:all_in_usd"
STEPS = (50, 80, 100)
_memory: Memory | None = None  # the server's memory, so the Claude client can check the cutoff


class BudgetStop(RuntimeError):
    """A Claude call refused: this month's Claude cutoff is reached."""


def attach(memory: Memory) -> None:
    global _memory
    _memory = memory


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


def claude_stopped(memory: Memory | None = None) -> bool:
    memory = memory or _memory
    if memory is None:
        return False
    try:
        cap = claude_cap(memory)
        return bool(cap) and usage.month_to_date(memory) >= cap
    except Exception:
        return False


def check_claude() -> None:
    """Called before every Claude request (config.claude_client)."""
    if claude_stopped():
        raise BudgetStop(f"Claude is stopped for the month: the ${claude_cap(_memory):.0f} cutoff is reached.")


def set_budget(memory: Memory, which: str, usd: float) -> str:
    usd = round(_num(usd, -1), 2)
    if usd < 0 or usd > 10_000:
        raise ToolError("Give an amount in dollars, like 20 (0 turns it off).")
    key = CLAUDE_KEY if which == "claude" else ALL_IN_KEY
    memory.set(key, str(usd))
    memory.set(f"budget:told:{which}:{local_now():%Y-%m}", "")  # alerts start again against the new amount
    name = "Claude cutoff" if which == "claude" else "overall monthly budget"
    if not usd:
        return f"The {name} is off."
    return f"The {name} is now ${usd:.0f} a month." + (
        " Claude stops for the rest of the month when it's reached." if which == "claude" else
        " I'll tell you at 50%, 80% and 100%, and if the month is heading over it.")


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
    out = {"claude": {"spent_usd": claude, "cutoff_usd": cap or None, "stopped": bool(cap) and claude >= cap,
                      "used_pct": round(claude / cap * 100) if cap else None},
           "all_in": {"budget_usd": budget or None}}
    if finops_report:
        mtd, forecast = finops_report.get("month_to_date_usd", 0.0), finops_report.get("forecast_usd", 0.0)
        out["all_in"].update({"spent_usd": mtd, "forecast_usd": forecast,
                              "used_pct": round(mtd / budget * 100) if budget else None,
                              "heading_over": bool(budget) and forecast > budget})
    return out


def stop_message(memory: Memory) -> str:
    return (f"Claude has reached this month's ${claude_cap(memory):.0f} cutoff, so it's stopped until the 1st. "
            "Reminders, alerts and your screens still work. To go on, say \"set my Claude budget to\" an amount, or change it "
            "in Insights.")


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
                out.append(stop_message(memory) if step == 100 else
                           f"Claude has used {step}% of this month's ${c['cutoff_usd']:.0f} cutoff "
                           f"(${c['spent_usd']:.2f}). At 100% it stops until the 1st.")
    a = s["all_in"]
    if a.get("budget_usd") and a.get("used_pct") is not None:
        said = []
        for step in STEPS:
            if a["used_pct"] >= step and _once(memory, "all_in", str(step)):
                said = [f"Buddy has cost ${a['spent_usd']:.2f} this month, all in: {step}% of your "
                        f"${a['budget_usd']:.0f} budget" + (", the whole budget." if step == 100 else ".")]
        if a["heading_over"] and a["used_pct"] < 100 and _once(memory, "all_in", "forecast"):
            said.append(f"At this pace the month will cost about ${a['forecast_usd']:.0f}, over your "
                        f"${a['budget_usd']:.0f} budget. Ask me what's costing most.")
        out += said
    return out


BUDGET_TOOL = {
    "name": "budgets",
    "description": "The user's monthly budgets: the Claude cutoff (Claude stops for the month when reached; default "
    "$20) and the overall budget for everything Buddy costs, with alerts at 50%, 80% and 100%. status: both, spent "
    "and forecast. set (which: claude | all_in, usd): lowering, or setting an overall budget, is done at once; "
    "raising the Claude cutoff needs the user's yes. 0 turns one off.",
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
        raising = which == "claude" and (not usd or usd > claude_cap(memory) > 0)
        if raising:  # Claude can never approve more spending: the user's own yes
            if thrift is None:
                raise ToolError("Only the user can raise it: say \"set my Claude budget to\" an amount.")

            async def run() -> str:
                return set_budget(memory, which, usd)
            return thrift.ask_action(f"Raise the Claude cutoff to ${usd:.0f} a month?" if usd else
                                     "Turn the Claude cutoff off?", run)
        return set_budget(memory, which, usd)
    report = None
    if finops is not None:
        try:
            report = await finops.report()
        except Exception:
            log.info("budgets: costs unavailable")
    return json.dumps(status(memory, report))
