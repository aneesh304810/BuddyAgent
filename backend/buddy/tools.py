"""Tools Buddy can use, and the code that runs them."""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .config import settings
from .memory import Memory

# Anthropic-hosted web search: runs server-side, no local code needed.
def web_tools() -> list[dict]:
    """Chat's web tools; how many uses per answer follows BUDDY_SEARCH_LEVEL."""
    from .config import search_caps

    caps = search_caps()
    tools = [{"type": "web_search_20260209", "name": "web_search", "max_uses": caps["chat_search"]}]
    if caps["chat_fetch"]:
        tools.append({"type": "web_fetch_20260209", "name": "web_fetch", "max_uses": caps["chat_fetch"]})
    return tools


WEB_SEARCH = web_tools()[0]  # kept for older imports

CLIENT_TOOLS = [
    {
        "name": "remember_fact",
        "description": "Save a fact about the user to long-term memory (people, "
        "preferences, plans, where things are). One self-contained sentence.",
        "input_schema": {
            "type": "object",
            "properties": {"fact": {"type": "string"}},
            "required": ["fact"],
            "additionalProperties": False,
        },
    },
    {
        "name": "recall_memory",
        "description": "Search long-term memory for facts about the user. Pass a few "
        "keywords, or an empty string to list recent facts.",
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
            "additionalProperties": False,
        },
    },
    {
        "name": "forget_fact",
        "description": "Delete remembered facts containing this text. Only when the "
        "user asks you to forget something.",
        "input_schema": {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
            "additionalProperties": False,
        },
    },
    {
        "name": "set_reminder",
        "description": "Schedule a spoken reminder. Give either `at` (local date and "
        "time, ISO 8601 like 2026-09-27T18:00) or `in_minutes`.",
        "input_schema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "What to remind them about"},
                "at": {"type": "string"},
                "in_minutes": {"type": "number"},
            },
            "required": ["text"],
            "additionalProperties": False,
        },
    },
    {
        "name": "list_reminders",
        "description": "List reminders that haven't gone off yet, with their ids.",
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "cancel_reminder",
        "description": "Cancel a pending reminder by id (get ids from list_reminders).",
        "input_schema": {
            "type": "object",
            "properties": {"id": {"type": "integer"}},
            "required": ["id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "add_goal",
        "description": "Record a goal the user wants to work toward, so you can guide "
        "and check in on it.",
        "input_schema": {
            "type": "object",
            "properties": {"goal": {"type": "string"}},
            "required": ["goal"],
            "additionalProperties": False,
        },
    },
    {
        "name": "list_goals",
        "description": "List the user's open goals with their ids.",
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "complete_goal",
        "description": "Mark a goal as achieved by id.",
        "input_schema": {
            "type": "object",
            "properties": {"id": {"type": "integer"}},
            "required": ["id"],
            "additionalProperties": False,
        },
    },
]

# Orchestration tools: handled by the conversation loop, not run_tool().
ORCHESTRATION_TOOLS = [
    {
        "name": "confirm_action",
        "description": "Carry out an agent action that needed approval. Call ONLY after the "
        "user clearly said yes in a message that came after you asked them.",
        "input_schema": {
            "type": "object",
            "properties": {"id": {"type": "string", "description": "The action id"}},
            "required": ["id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "list_agents",
        "description": "List Buddy's specialist agents, whether each is on, and its tools.",
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "pause_agent",
        "description": "Switch an agent off immediately (kill switch). Only the user can switch "
        "it back on, from the admin page.",
        "input_schema": {
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
            "additionalProperties": False,
        },
    },
]


_user_tz: ZoneInfo | None = None


def user_tz() -> ZoneInfo:
    """The user's current time zone: the phone's, else the configured default."""
    return _user_tz or settings.timezone


def set_user_tz(name: str) -> bool:
    """Adopt the phone's IANA time zone (e.g. 'America/Sao_Paulo'). False if unknown."""
    global _user_tz
    try:
        _user_tz = ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return False
    return True


def local_now() -> datetime:
    return datetime.now(user_tz())


def format_local(iso_utc: str) -> str:
    dt = datetime.fromisoformat(iso_utc).astimezone(user_tz())
    return dt.strftime("%a %d %b, %I:%M %p")


class ToolError(Exception):
    """A tool failed in a way the model should hear about and recover from."""


def require_https(url: str) -> str:
    """Buddy only fetches https:// links (never file:// or plain http://)."""
    if not url.lower().startswith("https://"):
        raise ToolError("Only https:// links are allowed.")
    return url


def run_tool(memory: Memory, name: str, args: dict) -> str:
    """Execute one client tool and return its result as text for the model."""
    if name == "remember_fact":
        memory.remember(args["fact"])
        return "Saved."
    if name == "recall_memory":
        facts = memory.recall(args.get("query", ""))
        return "\n".join(facts) if facts else "Nothing remembered about that."
    if name == "forget_fact":
        return f"Forgot {memory.forget(args['text'])} fact(s)."
    if name == "set_reminder":
        if args.get("in_minutes") is not None:
            due = local_now() + timedelta(minutes=float(args["in_minutes"]))
        elif args.get("at"):
            try:
                due = datetime.fromisoformat(args["at"])
            except ValueError as e:
                raise ToolError(f"Could not read the time {args['at']!r}.") from e
            if due.tzinfo is None:
                due = due.replace(tzinfo=user_tz())
        else:
            raise ToolError("Give either `at` or `in_minutes`.")
        if due < local_now() - timedelta(minutes=1):
            raise ToolError("That time is in the past.")
        rid = memory.add_reminder(args["text"], due)
        return f"Reminder {rid} set for {due.strftime('%a %d %b, %I:%M %p')}."
    if name == "list_reminders":
        items = memory.upcoming_reminders()
        if not items:
            return "No pending reminders."
        return "\n".join(f"{r['id']}: {r['text']} at {format_local(r['due_at'])}" for r in items)
    if name == "cancel_reminder":
        return "Cancelled." if memory.cancel_reminder(args["id"]) else "No such pending reminder."
    if name == "add_goal":
        return f"Goal {memory.add_goal(args['goal'])} added."
    if name == "list_goals":
        goals = memory.open_goals()
        return "\n".join(f"{g['id']}: {g['text']}" for g in goals) if goals else "No open goals."
    if name == "complete_goal":
        return "Marked done." if memory.complete_goal(args["id"]) else "No such goal."
    raise ToolError(f"Unknown tool {name}.")
