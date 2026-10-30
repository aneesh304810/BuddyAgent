"""Which Claude model answers each turn: the cheapest one that does it well.

  Haiku 5.5   small talk, reminders, tasks, quick facts (about 20x cheaper than Sonnet)
  Sonnet 5.5  everything else: email, money, places, planning, follow-ups to those
  Opus 5.5    hard questions, through the ask_expert step-up (unchanged)

Rules decide (no extra model call, no delay). Haiku gets a hand_over tool: when a
question turns out to need more, it passes it to Sonnet in the same turn.
"""

import re
import time

from .config import settings

SIMPLE = re.compile(
    r"\b(hi|hello|hey|thanks|thank you|good (morning|night|evening|afternoon)|how are you|what'?s up|"
    r"lol|haha|joke|bye|see you|remind me|reminder|add .{1,40} to (my )?(list|shopping|tasks?)|"
    r"what'?s on my list|my (to-?do|list)|tick off|done with|cancel (the )?reminder|what time|what day|"
    r"today'?s date|remember (that|my)|where('?s| is) my|yes|no|okay|ok|sure|cool|nice|great)\b",
    re.IGNORECASE,
)
NEEDS_SONNET = re.compile(
    r"\b(e-?mail|mail|inbox|money|spend|spent|bill|bank|card|budget|invest|tax|legal|lawyer|doctor|health|"
    r"medical|contract|flight|hotel|book|booking|deal|price|cheap|compare|plan|research|why|explain|analy|"
    r"strategy|restaurant|spa|bar|club|near me|place|agent|build|translate|write|draft|reply|summar|cost|"
    r"trip|travel|news|weather|search|google|reddit|find)\b",
    re.IGNORECASE,
)
FOLLOW_UP_SECONDS = 120

HAND_OVER_TOOL = {
    "name": "hand_over",
    "description": "Pass this question to the main model when it needs more than a quick reply: several "
    "steps, careful reasoning, email, money, planning, places, writing, or anything you're not sure about. "
    "Call it before answering, and say nothing else.",
    "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
}


class Router:
    def __init__(self):
        self.last_model = ""
        self.last_at = 0.0

    def pick(self, text: str) -> str:
        model = self._pick(text)
        self.last_model, self.last_at = model, time.monotonic()
        return model

    def _pick(self, text: str) -> str:
        if not settings.router_enabled or not settings.fast_model:
            return settings.chat_model
        words = len(text.split())
        recent_sonnet = (self.last_model == settings.chat_model
                         and time.monotonic() - self.last_at < FOLLOW_UP_SECONDS)
        if NEEDS_SONNET.search(text) or words > 18:
            return settings.chat_model
        if recent_sonnet and words <= 8:
            return settings.chat_model  # "and tomorrow?", "the second one": keep the same brain
        if SIMPLE.search(text) or words <= 4:
            return settings.fast_model
        return settings.chat_model

    def handed_over(self) -> str:
        self.last_model = settings.chat_model
        return settings.chat_model
