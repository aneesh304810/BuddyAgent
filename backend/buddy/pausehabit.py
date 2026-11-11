"""How the owner pauses and interrupts while speaking, learned per user and kept as a setting.

A sentence cut off by a pause is held back for a moment so the rest can join it. How long to hold is learned:
each time the rest of a sentence arrives, the gap is folded into a running average; interrupting Buddy mid-reply
is counted too. Stored under the setting key "pause_habit" (JSON), so it can be read back and shown.
"""

import json

KEY = "pause_habit"
MIN_WAIT, MAX_WAIT = 1.5, 8.0
MARGIN = 1.5  # wait this many times the usual pause, so a slightly longer one still joins
ALPHA = 0.3   # how fast new pauses move the average


def load(memory) -> dict:
    try:
        data = json.loads(memory.get(KEY) or "{}")
    except (ValueError, TypeError):
        data = {}
    return {"avg_gap": float(data.get("avg_gap") or 0), "merged": int(data.get("merged") or 0),
            "interrupts": int(data.get("interrupts") or 0)}


def _save(memory, data: dict) -> None:
    memory.set(KEY, json.dumps(data))


def record_gap(memory, seconds: float) -> dict:
    """The rest of a cut-off sentence came `seconds` after the first part."""
    if not 0 < seconds <= MAX_WAIT * 2:
        return load(memory)
    d = load(memory)
    d["avg_gap"] = round(seconds if not d["avg_gap"] else d["avg_gap"] * (1 - ALPHA) + seconds * ALPHA, 2)
    d["merged"] += 1
    _save(memory, d)
    return d


def record_interrupt(memory) -> dict:
    d = load(memory)
    d["interrupts"] += 1
    _save(memory, d)
    return d


def wait_seconds(memory, base: float) -> float:
    """How long to hold a cut-off sentence: never less than `base`, longer for someone who pauses longer
    (and a bit longer again for someone who often interrupts)."""
    d = load(memory)
    if not d["avg_gap"]:
        return base
    wait = d["avg_gap"] * MARGIN + min(d["interrupts"], 10) * 0.1
    return round(min(max(base, wait), max(base, MAX_WAIT)), 2)
