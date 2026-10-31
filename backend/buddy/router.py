"""Which Claude model answers each turn: the cheapest one that does it well.

  (free)      small talk on its own ("thanks", "good night", "tell me a joke"), with free models on
  Haiku 5.5   short chat, closers even mid-topic, reminders, tasks, quick facts (about 20x cheaper)
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

# Simple, general, timeless questions a free model answers as well as Claude: sent straight to
# one (no Claude call at all) when free models are on. Anything personal, current, local or
# needing a tool is left out here; the free-model guard refuses personal details again, and a
# free model that isn't sure hands it back.
FREE_TRANSLATE = re.compile(r"(?i)^(how do (you|i) say|translate|what('s| is| does) .{1,60} (mean )?in "
                            r"(spanish|portuguese|english|hindi|french|italian|german|malayalam|tamil|japanese))\b")
FREE_EXPLAIN = re.compile(r"(?i)^(what is an?|what is the (meaning|definition|difference)|what's an?|what are|"
                          r"what does .{1,40} mean|define|meaning of|explain|how does|how do .{1,30} work|why is the|"
                          r"why do|who was|who invented|who wrote|tell me about the history of|how many .{1,30} in an?)\b")
NOT_FOR_FREE = re.compile(
    r"(?i)\b(my|me|mine|i|i'm|we|our|us|you|your|buddy|remind|email|mail|calendar|today|tonight|tomorrow|"
    r"yesterday|now|current|currently|latest|recent|news|this (week|month|year)|price|cost|cheap|deal|weather|"
    r"near|nearby|open|book|buy|order|stock|score|schedule|bill|bank|spend|money|flight|hotel|restaurant|"
    r"search|google|reddit|code|script|bug|health|doctor|medical|legal|lawyer|tax|invest)\b")


# The whole message is small talk: a free model can reply ("thanks Buddy", "good night", "tell me a joke").
# Not "yes", "ok", "no" or "sure": those usually answer something Buddy asked, so Claude takes them.
SMALL_TALK = re.compile(
    r"(?i)^(?:(?:hey|hi|hello|yo)(?:\s+there)?|good (?:morning|afternoon|evening|night)|thanks?(?: a lot| so much)?|"
    r"thank you(?: so much| very much)?|cheers|bye(?: bye)?|see you(?: later| soon| tomorrow)?|talk (?:to you )?later|"
    r"take care|how are you(?: doing)?(?: today)?|how'?s it going|what'?s up|tell me a joke|another (?:one|joke)|"
    r"lol|haha+|ha ha|nice one|awesome|cool|great|perfect|love it|you'?re (?:the best|awesome|funny))"
    r"(?:[\s,]+(?:buddy|bro|machaa|aliya|man|mate))?[\s.!?]*$")
# Closers never need the bigger model, even right after a big question.
CLOSER = re.compile(
    r"(?i)^(?:ok(?:ay)?[\s,]+)?(?:thanks?(?: a lot| so much)?|thank you(?: so much| very much)?|cheers|bye(?: bye)?|"
    r"good night|see you(?: later| soon| tomorrow)?|talk (?:to you )?later|take care|got it|perfect|great|cool|nice|"
    r"awesome)(?:[\s,]+(?:buddy|bro|machaa|aliya|man))?[\s.!?]*$")


def free_kind(text: str) -> str:
    """'small_talk', 'translate' or 'explain' when a free model can take the whole turn, else ''."""
    text = text.strip()
    if SMALL_TALK.match(text):
        return "small_talk"
    if len(text.split()) > 25:
        return ""
    if FREE_TRANSLATE.search(text):
        return "translate"  # the phrase itself can be anything; the privacy guard still checks it
    if FREE_EXPLAIN.search(text) and not NOT_FOR_FREE.search(text):
        return "explain"
    return ""

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
        if not CLOSER.match(text.strip()):  # a "thanks" mid-topic doesn't end the topic for what follows
            self.last_model, self.last_at = model, time.monotonic()
        return model

    def _pick(self, text: str) -> str:
        if not settings.router_enabled or not settings.fast_model:
            return settings.chat_model
        words = len(text.split())
        recent_sonnet = (self.last_model == settings.chat_model
                         and time.monotonic() - self.last_at < FOLLOW_UP_SECONDS)
        if CLOSER.match(text.strip()):
            return settings.fast_model  # "thanks", "bye", "got it": no need for the big model, even mid-topic
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
