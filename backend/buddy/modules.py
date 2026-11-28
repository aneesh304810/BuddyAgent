"""Modules: every function of Buddy belongs to one, each with a switch and a meter (docs/PLATFORM.md).

  core      always on: the brain, Maestro, Buddy Connect, memory, security and privacy, costs, the server and app
  module    a part Buddy already has (Voice, Money, Travel...): on or off for this server
  wearable  the new parts for glasses. Devices and Link are built (glasslink.py). The rest (Vision, Voice on a
            device, Integrations...) are listed here and cannot be on until they are

THE SWITCH belongs to whoever runs the server, not to the person talking to Buddy: it is the setting BUDDY_MODULES
in the server's root-only settings (deploy/modules.sh changes it). In a conversation the modules can be listed,
never switched.

  unset, or "all"       everything Buddy has today is on: the owner's own server is unchanged
  "core"                core only: where a new customer's server starts
  "all,-investing"      everything but one
  "core,voice,money"    just those

A module that is off does not exist for the conversation: its tools are not offered to Claude (allowed), and a
call to one is refused (refusal). The background loops that belong to one module (email, price watches, bank
feeds, the tutors) do not start. Not gated yet: the app's pages, and the jobs inside the loop that speaks up on its
own (proactive.py).

THE METER is one line a day for each module and unit (the table module_usage): how many, and what it cost to run.
It is written as things happen, at the places Buddy already logs its work:

  request        a model request made for the module, with its cost            (usage.log_call)
  use            a tool of the module was run                                   (usage.log_tool)
  outside_call   a paid outside service was called for the module, with its cost (travel.log_call)
  minute         speech heard (Voice) or time on the line (Phone calls)
  day_on         the module was on that day
  device_day     a pair of glasses was connected that day, once for each device (mark); a statement counts the
                 different devices of the month as active_device
  ...            whatever a module records itself with record(): image, action, seat_month

Lines are kept for three years. A wrong or failing meter never breaks a reply: every hook swallows its errors.

THE PRICE LIST is the operator's table, kept out of the code: a CSV file named by BUDDY_PRICE_LIST with the columns
module,unit,price_usd. Without it a statement shows counts and running cost and no charge. Charging (an invoice,
a card) is not built.
"""

import csv
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

KEEP_DAYS = 3 * 366
DAY_KEY = "modules:day"  # the last day the day_on lines were written


@dataclass(frozen=True)
class Module:
    id: str
    title: str
    kind: str                      # core | module | wearable
    sits_with: str                 # the component it belongs to (docs/architecture)
    meter: tuple[str, ...]         # the units it is counted in
    needs: tuple[str, ...] = ()    # modules that must be on for it to be on
    built: bool = True             # False: listed in the plan, nothing on the server to switch on
    owner_only: bool = False       # runs on the owner's own server; not something a customer is offered
    note: str = ""


EVERYDAY = ("request", "use", "outside_call", "day_on")
MODULES = (
    Module("core", "Core", "core", "The brain, Maestro, Buddy Connect, Memory, Security and privacy, Insights and "
           "costs, the server and the app", EVERYDAY),
    # --- the parts Buddy already has ---------------------------------------------------------------------------
    Module("voice", "Voice: Talk mode and Listen-in", "module", "Voice", ("minute",) + EVERYDAY),
    Module("calls", "Phone calls", "module", "Phone calls", ("minute",) + EVERYDAY),
    Module("briefing", "Briefing, reminders and alerts", "module", "Speaking up on its own", EVERYDAY,
           note="Its jobs inside the proactive loop are not gated yet."),
    Module("learning", "Learning", "module", "Learning", EVERYDAY),
    Module("money", "Money", "module", "Money", EVERYDAY),
    Module("shopping", "Shopping and deals", "module", "Shopping and deals", EVERYDAY),
    Module("pricebook", "Price book and trained models", "module", "The price book", EVERYDAY),
    Module("investing", "Investing", "module", "Investing", EVERYDAY),
    Module("travel", "Travel and getting around", "module", "Travel and getting around", EVERYDAY),
    Module("goingout", "Going out", "module", "Going out", EVERYDAY),
    Module("news", "News, trends and research", "module", "News, trends and research", EVERYDAY),
    Module("email", "Email, messages and apps", "module", "Email, messages and apps", EVERYDAY),
    Module("home_health", "Home and health", "module", "Home and health", EVERYDAY,
           note="Health information still goes to no AI model."),
    Module("making", "Making and doing things", "module", "Making and doing things", EVERYDAY),
    Module("agents", "Agents and skills", "module", "Agents and skills", EVERYDAY),
    Module("intelligence", "Intelligence Platform", "module", "Buddy Intelligence Platform", EVERYDAY),
    Module("selfcare", "Fixing and explaining itself", "module", "Fixing and explaining itself", EVERYDAY,
           owner_only=True),
    # --- for glasses: the plan, not built ----------------------------------------------------------------------
    Module("devices", "Devices", "wearable", "Wearables (new)", ("device_day", "active_device"),
           note="Glasses connect and say what they can do. Not yet run with real glasses."),
    Module("link", "Link", "wearable", "The server and the app", ("use",), needs=("devices",),
           note="Joins a device to Buddy's brain: show, ask, choose. Counted with Devices."),
    Module("device_voice", "Voice on a device", "wearable", "Voice", ("minute", "character"),
           needs=("devices", "link"), built=False),
    Module("vision", "Vision", "wearable", "New", ("image",), needs=("devices", "link"), built=False),
    Module("agent", "Agent", "wearable", "The brain, Maestro", ("request",), needs=("link",), built=False,
           note="Reasoning and actions for a wearer."),
    Module("knowledge", "Knowledge", "wearable", "Memory, Intelligence Platform", ("document", "lookup"), built=False,
           note="A customer's own memory and research."),
    Module("integrations", "Integrations", "wearable", "Buddy Connect, Agents", ("system_month", "action"),
           built=False, note="One adapter for each business system."),
    Module("governance", "Governance", "wearable", "Security and privacy", ("seat_month",), built=False,
           note="Roles, policy and the audit log."),
    Module("studio", "Studio and SDK", "wearable", "Wearables (new)", (), built=False,
           note="Runs in a browser, in the simulator. Nothing on the server to switch."),
)
BY_ID = {m.id: m for m in MODULES}

# Every tool belongs to one module (a test fails when a new tool is in none).
TOOLS = {
    "core": ("remember_fact", "forget_fact", "set_reminder", "list_reminders", "cancel_reminder", "add_goal",
             "list_goals", "complete_goal", "reply_style", "switch_mode", "customize_buddy", "decision_outcome",
             "budgets", "ask_expert", "confirm_action", "list_agents", "pause_agent", "spending_status", "hand_over",
             "connections", "model_advice", "model_scout", "maestro", "model_routing", "general_task",
             "free_answer_verdict", "rate_answer", "cost_watch", "cost_report", "life_log", "my_taste", "where_am_i",
             "library", "brain", "recall_conversation", "recall_memory", "my_routine", "security_report",
             "security_watch", "show_cards", "add_task", "list_tasks", "complete_task", "delete_task", "web_search",
             "web_fetch", "read_pages", "google_search", "list_jobs", "job_report", "cancel_job", "health_report",
             "modules", "tool_search_tool_bm25"),
    "voice": ("room_conversation", "room_transcripts", "voice_match", "learn_word"),
    "calls": ("phone_call",),
    "briefing": ("alert_settings",),
    "learning": ("english_tutor", "market_school", "morning_tutor"),
    "money": ("spending", "add_expense", "bills", "spending_limit", "account_balances", "finance", "refunds",
              "money_advisor",
              "card_benefits", "bank_link", "merchants", "credit", "cancel_subscription"),
    "shopping": ("note_price", "amazon_price", "shopping_profile", "when_to_buy", "daily_deals", "deal_forecast",
                 "seasonal_prices", "watch_price", "price_watches", "find_deals", "shop_for"),
    "pricebook": ("price_book",),
    "investing": ("markets", "community_signals", "recommendations", "investor_checks", "fund_holdings",
                  "futures_positioning",
                  "investment_journal", "paper_portfolio", "day_trade_practice", "investor_library"),
    "travel": ("get_ride", "pick_seat", "travel", "transit", "flight_status", "buddy_maps", "weather", "hotel_direct",
               "travel_timing", "commute"),
    "goingout": ("restaurants", "entertainment", "find_places"),
    "news": ("trending", "trends", "tech_radar", "start_research", "reddit", "search_everywhere", "check_reviews"),
    "email": ("packages", "messages", "email_organizer", "life_memory", "important_email", "add_email_rule",
              "email_rules", "search_my_apps", "agenda", "app_connections", "calendar_add"),
    "home_health": ("my_health", "health_info", "health_plan", "health_sync", "thermostat", "my_kitchen"),
    "making": ("make_diagram", "make_chart", "make_document", "make_image", "edit_document", "browse", "process_data",
               "buddy_tools", "fill_form"),
    "agents": ("build_agent", "connect_mcp_server", "built_agents"),
    "intelligence": ("intelligence",),
    "selfcare": ("buddy_code", "claude_code_fix", "self_check", "start_task", "code_workspaces"),
    "link": ("glasses_describe", "glasses_show", "glasses_ask", "glasses_choose"),
}
_TOOL_MODULE = {tool: module for module, tools in TOOLS.items() for tool in tools}
AGENT_MODULE = {"gmail": "email"}  # a specialist agent's tools (gmail__read_mail) belong to its module

# What a model request or a paid outside call was for (its logged purpose) -> the module. First match wins;
# anything no rule knows is core.
_PURPOSES = [(module, re.compile(f"^(?:{pattern})$", re.IGNORECASE)) for module, pattern in (
    ("calls", r"phone.*|choosing a voice"),
    ("voice", r"speech.*|listen.*|addressing|voice.*"),
    ("briefing", r"briefing|evening_wrap"),
    ("learning", r"market_school|morning_tutor|tutor|vocabulary"),
    ("pricebook", r"pricebook.*|price_book.*"),
    ("money", r"bankfeed|finance:.*|money.*|credit.*|card.*|subscription.*|budget.*"),
    ("shopping", r"deals?.*|shopping|seasons|shop.*|whentobuy|price"),
    ("investing", r"market.*|signals.*|funds|lenses|invest.*|paper.*|day-trade"),
    ("travel", r"travel.*|transit|weather|flight.*|hotel.*|commute"),
    ("goingout", r"dining|events"),
    ("intelligence", r"intelligence"),
    ("news", r"news.*|trends|research|web_digest|web.*|techradar|search"),
    ("email", r"mail.*|lifemail|organis.*"),
    ("home_health", r"health.*|home"),
    ("agents", r"build_agent"),
    ("selfcare", r"code|codedocs.*|selfcheck|diagnos.*"),
)]


def _setting(name: str, env=None) -> str:
    return ((env if env is not None else os.environ).get(name) or "").strip()


# --- the switch -----------------------------------------------------------------------------------------------
def _everything() -> set[str]:
    """Everything Buddy has today: core and every built module."""
    return {m.id for m in MODULES if m.built}


def _read(setting: str) -> set[str]:
    on = {"core"}
    for item in (x.strip().lower() for x in (setting or "all").split(",")):
        if item == "all":
            on |= _everything()
        elif item.startswith("-"):
            on.discard(item[1:])
        elif item in BY_ID and BY_ID[item].built:
            on.add(item)
    on.add("core")
    while True:  # a module whose needs are off is off too
        short = {m for m in on if any(n not in on for n in BY_ID[m].needs)}
        if not short:
            return on
        on -= short


def switched_on(env=None) -> frozenset[str]:
    """The modules that are on for this server."""
    return frozenset(_read(_setting("BUDDY_MODULES", env)))


def is_on(module: str, env=None) -> bool:
    return module in switched_on(env)


def new_setting(current: str, module: str, on: bool) -> str:
    """BUDDY_MODULES after switching one module. Raises ValueError, in words for the operator, when it can't be."""
    module = (module or "").strip().lower()
    m = BY_ID.get(module)
    if m is None:
        raise ValueError(f"No module called '{module}'. The modules: {', '.join(x.id for x in MODULES)}.")
    if module == "core":
        raise ValueError("Core is always on.")
    if not m.built:
        raise ValueError(f"{m.title} is not built yet, so there is nothing to switch on.")
    now = _read(current)
    if on:
        missing = [n for n in m.needs if not BY_ID[n].built]
        if missing:
            raise ValueError(f"{m.title} needs {', '.join(BY_ID[n].title for n in missing)}, which is not built yet.")
        now |= {module, *m.needs}
    else:
        needed_by = [x.title for x in MODULES if module in x.needs and x.id in now]
        if needed_by:
            raise ValueError(f"{', '.join(needed_by)} needs {m.title}. Switch that off first.")
        now.discard(module)
    everything = _everything()
    if "all" in [x.strip().lower() for x in (current or "all").split(",")] or now == everything:
        off = [x.id for x in MODULES if x.id in everything and x.id not in now]  # "all" also covers modules added later
        return ",".join(["all"] + [f"-{x}" for x in off])
    return ",".join(x.id for x in MODULES if x.id in now)


# --- which module a thing belongs to --------------------------------------------------------------------------
def module_of_tool(tool: str) -> str:
    if tool in _TOOL_MODULE:
        return _TOOL_MODULE[tool]
    if "__" in tool:  # a specialist agent's tool
        return AGENT_MODULE.get(tool.split("__", 1)[0], "agents")
    return "core"


def module_of_purpose(purpose: str) -> str:
    purpose = str(purpose or "")
    return next((module for module, rule in _PURPOSES if rule.match(purpose)), "core")


def allowed(tools: list[dict], env=None) -> list[dict]:
    """The tools Claude is offered: those of the modules that are on."""
    on = switched_on(env)
    return [t for t in tools if module_of_tool(str(t.get("name", ""))) in on]


def refusal(tool: str, env=None) -> str:
    """"" when the tool's module is on; otherwise what to tell Claude instead of running it."""
    module = module_of_tool(tool)
    if module in switched_on(env):
        return ""
    return (f"Not available: the {BY_ID[module].title} module is switched off on this server. Tell the user so in "
            "one plain sentence; don't try to do the same thing another way.")


# --- the meter ------------------------------------------------------------------------------------------------
def _day(now: datetime | None = None) -> str:
    return (now or datetime.now(timezone.utc)).date().isoformat()


def record(memory, module: str, unit: str, amount: float = 1.0, cost: float = 0.0, who: str = "",
           now: datetime | None = None, env=None) -> None:
    """Add to today's line for this module and unit. `who` is a device or a person, where there is one."""
    if memory is None or module not in BY_ID or not unit or amount is None:
        return
    day = _day(now)
    _mark_day(memory, day, env)
    memory.db.execute(
        """INSERT INTO module_usage (day, module, unit, who, amount, cost_usd) VALUES (?, ?, ?, ?, ?, ?)
           ON CONFLICT (day, module, unit, who) DO UPDATE SET amount = amount + excluded.amount,
               cost_usd = cost_usd + excluded.cost_usd""",
        (day, module, unit, who[:80], float(amount), float(cost or 0.0)))
    memory.db.commit()


def mark(memory, module: str, unit: str, who: str, now: datetime | None = None, env=None) -> None:
    """Count `who` once today for this module and unit, however often it is marked (an active device)."""
    if memory is None or module not in BY_ID or not unit or not who:
        return
    day = _day(now)
    _mark_day(memory, day, env)
    memory.db.execute("INSERT OR IGNORE INTO module_usage (day, module, unit, who, amount, cost_usd) "
                      "VALUES (?, ?, ?, ?, 1, 0)", (day, module, unit, who[:80]))
    memory.db.commit()


def _mark_day(memory, day: str, env=None) -> None:
    """Once a day: a day_on line for every module that is on, and lines past three years dropped."""
    if memory.get(DAY_KEY) == day:
        return
    memory.set(DAY_KEY, day)
    for module in switched_on(env):
        memory.db.execute("INSERT OR IGNORE INTO module_usage (day, module, unit, who, amount, cost_usd) "
                          "VALUES (?, ?, 'day_on', '', 1, 0)", (day, module))
    cutoff = (datetime.fromisoformat(day) - timedelta(days=KEEP_DAYS)).date().isoformat()
    memory.db.execute("DELETE FROM module_usage WHERE day < ?", (cutoff,))
    memory.db.commit()


def note_request(memory, purpose: str, cost: float = 0.0) -> None:
    """A model request was made (called by usage.log_call)."""
    record(memory, module_of_purpose(purpose), "request", 1, cost)


def note_use(memory, tool: str) -> None:
    """A tool was run (called by usage.log_tool)."""
    record(memory, module_of_tool(tool), "use", 1)


def note_outside(memory, purpose: str, cost: float = 0.0) -> None:
    """A paid outside service was called (called by travel.log_call)."""
    record(memory, module_of_purpose(purpose), "outside_call", 1, cost)


# --- prices and the statement ---------------------------------------------------------------------------------
def price_list(env=None) -> dict[tuple[str, str], float]:
    """{(module, unit): price in USD} from the operator's CSV (BUDDY_PRICE_LIST), or {} when there is none."""
    path = _setting("BUDDY_PRICE_LIST", env)
    if not path or not os.path.isfile(path):
        return {}
    prices = {}
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            module, unit = (row.get("module") or "").strip().lower(), (row.get("unit") or "").strip().lower()
            try:
                price = float(row.get("price_usd") or "")
            except ValueError:
                continue
            if module in BY_ID and unit and price >= 0:
                prices[(module, unit)] = price
    return prices


def statement(memory, month: str = "", env=None) -> dict:
    """What each module was used for in a month (YYYY-MM, UTC; default this month), what that cost to run, and
    what it comes to at the operator's prices. Units without a price are counted and not charged."""
    month = month if re.fullmatch(r"\d{4}-\d{2}", month or "") else _day()[:7]
    rows = memory.db.execute(
        "SELECT module, unit, SUM(amount) AS amount, SUM(cost_usd) AS cost FROM module_usage "
        "WHERE day LIKE ? GROUP BY module, unit", (month + "-%",)).fetchall()
    prices, on = price_list(env), switched_on(env)
    found: dict[str, list[dict]] = {}
    active = memory.db.execute(  # the different devices of the month: what "a price for each active device" counts
        "SELECT module, COUNT(DISTINCT who) AS amount, 0.0 AS cost FROM module_usage "
        "WHERE day LIKE ? AND unit = 'device_day' AND who != '' GROUP BY module", (month + "-%",)).fetchall()
    rows = list(rows) + [{"module": r["module"], "unit": "active_device", "amount": r["amount"], "cost": 0.0} for r in active]
    for r in rows:
        price = prices.get((r["module"], r["unit"]))
        found.setdefault(r["module"], []).append({
            "unit": r["unit"], "amount": round(r["amount"], 2), "cost_usd": round(r["cost"], 4),
            "price_usd": price, "charge_usd": None if price is None else round(price * r["amount"], 2)})
    out = []
    for m in MODULES:
        lines = sorted(found.get(m.id, []), key=lambda line: line["unit"])
        if not lines and m.id not in on:
            continue
        out.append({"module": m.id, "title": m.title, "on": m.id in on, "lines": lines,
                    "cost_usd": round(sum(line["cost_usd"] for line in lines), 4),
                    "charge_usd": round(sum(line["charge_usd"] or 0.0 for line in lines), 2)})
    return {"month": month, "priced": bool(prices), "modules": out,
            "cost_usd": round(sum(m["cost_usd"] for m in out), 4),
            "charge_usd": round(sum(m["charge_usd"] for m in out), 2)}


def overview(env=None) -> list[dict]:
    on = switched_on(env)
    return [{"module": m.id, "title": m.title, "kind": m.kind, "on": m.id in on, "built": m.built,
             "sits_with": m.sits_with, "meter": list(m.meter), "needs": list(m.needs), "note": m.note}
            for m in MODULES]


# --- in a conversation: look, don't switch --------------------------------------------------------------------
TOOL = {
    "name": "modules",
    "description": "Buddy's modules on this server: which are on, and what each was used for in a month and what "
    "that cost to run (\"which modules are on?\", \"what did each module cost this month?\", \"is investing "
    "switched on?\"). Actions: list; statement (month: YYYY-MM, default this month). Modules are switched by whoever "
    "runs the server (deploy/modules.sh), never from a conversation: say so if asked to switch one.",
    "input_schema": {"type": "object", "properties": {
        "action": {"type": "string", "enum": ["list", "statement"]},
        "month": {"type": "string", "description": "statement: YYYY-MM"}},
        "required": ["action"], "additionalProperties": False},
}


def list_text(env=None) -> str:
    rows = overview(env)
    lines = ["Modules on this server (switched by whoever runs it: sudo bash /opt/buddy/deploy/modules.sh):"]
    for kind, heading in (("core", "Always on"), ("module", "Parts Buddy has"), ("wearable", "For glasses (planned)")):
        lines.append(f"{heading}:")
        for r in (r for r in rows if r["kind"] == kind):
            state = "on" if r["on"] else "not built" if not r["built"] else "off"
            lines.append(f"- {r['title']} [{r['module']}]: {state}" + (f". {r['note']}" if r["note"] else ""))
    return "\n".join(lines)


def statement_text(memory, month: str = "", env=None) -> str:
    s = statement(memory, month, env)
    if not any(m["lines"] for m in s["modules"]):
        return f"Nothing counted for {s['month']} yet. Counting started when this was installed."
    lines = [f"Modules in {s['month']} (UTC days). Running cost: ${s['cost_usd']:.2f}."
             + (f" At the price list: ${s['charge_usd']:.2f}." if s["priced"] else
                " No price list is set, so nothing is charged: these are counts and running costs.")]
    for m in s["modules"]:
        if not m["lines"]:
            continue
        parts = [f"{line['amount']:g} {line['unit']}" for line in m["lines"]]
        tail = f"; cost ${m['cost_usd']:.2f}" + (f"; charge ${m['charge_usd']:.2f}" if s["priced"] else "")
        lines.append(f"- {m['title']}{'' if m['on'] else ' (off now)'}: {', '.join(parts)}{tail}")
    return "\n".join(lines)


def run_tool(memory, args: dict, env=None) -> str:
    if args.get("action") == "statement":
        return statement_text(memory, str(args.get("month") or ""), env)
    return list_text(env)


# --- for deploy/modules.sh ------------------------------------------------------------------------------------
def main(argv: list[str]) -> int:
    """python -m buddy.modules list <current setting> | on <id> <current> | off <id> <current>.
    `list` prints the modules; on/off print the new value of BUDDY_MODULES, or an explanation and exit 1."""
    if len(argv) >= 1 and argv[0] == "list":
        print(list_text({"BUDDY_MODULES": argv[1] if len(argv) > 1 else ""}))
        return 0
    if len(argv) >= 2 and argv[0] in ("on", "off"):
        try:
            print(new_setting(argv[2] if len(argv) > 2 else "", argv[1], argv[0] == "on"))
            return 0
        except ValueError as e:
            print(e, file=sys.stderr)
            return 1
    print("Usage: list [current] | on <module> [current] | off <module> [current]", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
