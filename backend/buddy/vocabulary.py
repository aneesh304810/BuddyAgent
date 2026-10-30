"""Words Buddy should hear right: names, places and terms from your life, and words you corrected.

Speech recognition stumbles on names (Miraflores, Priya, Nubank) and on fast, accented or mixed
speech. This list primes it: Whisper on the server gets it as a prompt, and the phone's
recogniser as biasing words (Android 13+). It grows by itself: names in Buddy's memory, and
every time you correct a misheard word ("no, I said Barranco"), Claude records it with learn_word.
"""

import json
import os
import re

from .config import settings
from .memory import Memory

KEY = "vocabulary"
MAX_WORDS = 60
COMMON = {"The", "This", "That", "They", "There", "When", "What", "Where", "Which", "Who", "And", "But", "For",
          "With", "From", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday",
          "January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
          "November", "December", "English", "User", "Buddy"}


def learned(memory: Memory) -> list[str]:
    try:
        return json.loads(memory.get(KEY) or "[]")
    except json.JSONDecodeError:
        return []


def learn(memory: Memory, word: str) -> list[str]:
    word = re.sub(r"\s+", " ", word).strip()[:40]
    words = [w for w in learned(memory) if w.lower() != word.lower()]
    if word:
        words.insert(0, word)
    memory.set(KEY, json.dumps(words[:MAX_WORDS]))
    return words


def words(memory: Memory) -> list[str]:
    """Learned words first, then settings, then proper names from memory: most useful first."""
    out: list[str] = []
    extra = [w.strip() for w in (os.environ.get("BUDDY_VOCABULARY") or "").split(",") if w.strip()]
    names = []
    for fact in memory.recall("", limit=200):
        names += [w for w in re.findall(r"\b[A-Z][a-zA-Z'’-]{2,}\b", fact) if w not in COMMON]
    for w in learned(memory) + extra + [settings.user_name, settings.assistant_name] + names:
        if w and w.lower() not in {x.lower() for x in out}:
            out.append(w)
    return out[:MAX_WORDS]


def whisper_prompt(memory: Memory) -> str:
    """A short prompt in the user's voice: Whisper copies its spellings and style."""
    found = words(memory)
    if not found:
        return ""
    return f"{settings.user_name} talking to {settings.assistant_name}. Names: " + ", ".join(found[:40]) + "."


LEARN_TOOL = {
    "name": "learn_word",
    "description": "Speech recognition misheard a word (the user corrects you, or a name came out garbled and "
    "you worked out what they meant): save the right spelling so it's recognised next time. Names, places, "
    "brands, terms.",
    "input_schema": {"type": "object", "properties": {
        "word": {"type": "string", "description": "The right spelling, e.g. Miraflores"},
        "heard_as": {"type": "string", "description": "What recognition wrote, if known"}},
        "required": ["word"], "additionalProperties": False},
}
