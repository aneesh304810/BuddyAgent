"""Make Buddy your own, just by saying so.

Two kinds of change:
  - standing instructions in the user's own words ("call me Aneesh", "I prefer Lyft over Uber",
    "always give prices in rupees too"): kept as a short list Buddy follows in every reply
  - settings that change how Buddy works:
      reply_length      short | normal | detailed
      bill_days         how many days before a bill is due the reminders start (1-14, default 3)
      briefing_topics   extra things in the morning briefing ("cricket scores", "weather in Kochi")
      briefing          on | off
They are stored on the server (kv: custom:*) and applied at once; the list is in Settings via
"what have I told you to change?".
"""

import json

from .memory import Memory
from .tools import ToolError

MAX_RULES = 30
SETTINGS = {
    "reply_length": ("short", "normal", "detailed"),
    "bill_days": tuple(range(1, 15)),
    "briefing": ("on", "off"),
}
LENGTH_NOTE = {"short": "Keep every reply to one or two short sentences unless they ask for more.",
               "detailed": "They like fuller answers: give the detail and the reasons, still easy to follow."}


def settings(memory: Memory) -> dict:
    try:
        return json.loads(memory.get("custom:settings") or "{}")
    except ValueError:
        return {}


def rules(memory: Memory) -> list[str]:
    try:
        return list(json.loads(memory.get("custom:rules") or "[]"))
    except ValueError:
        return []


def bill_days(memory: Memory, default: int = 3) -> int:
    try:
        return max(1, min(14, int(settings(memory).get("bill_days", default))))
    except (TypeError, ValueError):
        return default


def briefing_topics(memory: Memory) -> list[str]:
    return [str(t) for t in settings(memory).get("briefing_topics", [])][:6]


def prompt_lines(memory: Memory) -> list[str]:
    """What goes into Buddy's instructions: the user's own rules, then the reply length."""
    out = list(rules(memory))
    note = LENGTH_NOTE.get(settings(memory).get("reply_length", ""))
    if note:
        out.append(note)
    return out


class Customize:
    def __init__(self, memory: Memory):
        self.memory = memory

    def _save(self, s: dict | None = None, r: list[str] | None = None) -> None:
        if s is not None:
            self.memory.set("custom:settings", json.dumps(s))
        if r is not None:
            self.memory.set("custom:rules", json.dumps(r[-MAX_RULES:]))

    def add(self, instruction: str) -> str:
        text = " ".join(str(instruction or "").split())[:200]
        if not text:
            raise ToolError("What should I do differently?")
        r = [x for x in rules(self.memory) if x.lower() != text.lower()]
        self._save(r=r + [text])
        return f"Got it, from now on: {text}"

    def remove(self, what: str) -> str:
        what = str(what or "").lower().strip()
        r = rules(self.memory)
        keep = [x for x in r if what not in x.lower()]
        if len(keep) == len(r):
            raise ToolError("I don't have an instruction like that.")
        self._save(r=keep)
        return f"Removed {len(r) - len(keep)} instruction{'s' if len(r) - len(keep) != 1 else ''}."

    def set(self, setting: str, value) -> str:
        s = settings(self.memory)
        if setting == "briefing_topics":
            topics = value if isinstance(value, list) else [t.strip() for t in str(value or "").split(",")]
            topics = [" ".join(str(t).split())[:60] for t in topics if str(t).strip()][:6]
            s["briefing_topics"] = topics
            self._save(s=s)
            return ("Your morning briefing now includes: " + ", ".join(topics) + ".") if topics else \
                "No extra topics in the briefing."
        if setting == "add_briefing_topic":
            return self.set("briefing_topics", briefing_topics(self.memory) + [str(value)])
        if setting == "remove_briefing_topic":
            return self.set("briefing_topics", [t for t in briefing_topics(self.memory)
                                                if str(value).lower() not in t.lower()])
        allowed = SETTINGS.get(setting)
        if allowed is None:
            raise ToolError(f"Settings: {', '.join(list(SETTINGS) + ['briefing_topics'])}.")
        if setting == "bill_days":
            try:
                value = int(value)
            except (TypeError, ValueError):
                raise ToolError("How many days before a bill is due? 1 to 14.") from None
        if value not in allowed:
            raise ToolError(f"{setting}: one of {', '.join(map(str, allowed))}.")
        s[setting] = value
        self._save(s=s)
        return {"reply_length": f"Replies will be {value} from now on.",
                "bill_days": f"Bill reminders now start {value} day{'s' if value != 1 else ''} before the due date.",
                "briefing": f"Morning briefing {value}."}[setting]

    def overview(self) -> dict:
        s = settings(self.memory)
        return {"instructions": rules(self.memory), "reply_length": s.get("reply_length", "normal"),
                "bill_days": bill_days(self.memory), "briefing": s.get("briefing", "on"),
                "briefing_topics": briefing_topics(self.memory)}

    def reset(self) -> str:
        self._save(s={}, r=[])
        return "Back to my defaults: no standing instructions, normal replies, bill reminders 3 days ahead."


CUSTOMIZE_TOOL = {
    "name": "customize_buddy",
    "description": "The user changes how you work, in their own words. add (instruction): a standing instruction you "
    "follow from now on in every reply (\"call me Aneesh\", \"prefer Lyft\", \"show rupees too\", \"no emoji on screen\"); "
    "write it short, in the third person if needed. remove (what). set (setting, value): reply_length short|normal|"
    "detailed; bill_days 1-14 (bill reminders start this many days before the due date); briefing on|off; "
    "briefing_topics (comma list) or add_briefing_topic / remove_briefing_topic (value) for extra things in the "
    "morning briefing (\"cricket scores\", \"weather in Kochi\"). list: everything they've changed. reset. Several "
    "changes in one sentence: one call each. Confirm briefly what changed. Things you can't change this way (a new "
    "feature) go to buddy_code / the agent builder.",
    "input_schema": {"type": "object", "properties": {
        "action": {"type": "string", "enum": ["add", "remove", "set", "list", "reset"]},
        "instruction": {"type": "string"}, "what": {"type": "string"},
        "setting": {"type": "string", "enum": ["reply_length", "bill_days", "briefing", "briefing_topics",
                                               "add_briefing_topic", "remove_briefing_topic"]},
        "value": {"type": "string"}},
        "required": ["action"], "additionalProperties": False},
}


def run_tool(memory: Memory, args: dict) -> str:
    c = Customize(memory)
    a = args.get("action")
    if a == "add":
        return c.add(str(args.get("instruction") or ""))
    if a == "remove":
        return c.remove(str(args.get("what") or ""))
    if a == "set":
        return c.set(str(args.get("setting") or ""), args.get("value"))
    if a == "reset":
        return c.reset()
    return json.dumps(c.overview(), ensure_ascii=False)
