"""Attention: which of Buddy's own messages deserve to interrupt, which can wait quietly, and which are noise,
learned from the owner's thumbs.

Every message Buddy sends on its own (an alert, a reminder, a signal, a digest) has a topic ("investing:live",
"deals:watched", "reminder"...), a category for grouping (Investing, Deals, Travel, Email, Reminders, Health...),
and one of four levels:

  alert   pops up and is spoken               (something to act on now)
  notify  a silent notification, not spoken   (worth knowing today)
  quiet   in the chat and the Alerts page only, no notification
  muted   kept in the Alerts page only        (the owner has shown they don't want it)

Each topic starts at a sensible level. The owner's thumbs on a message move its topic: mostly thumbs-down moves
it one level quieter (two when it's nearly all down), mostly thumbs-up one level louder. It takes three ratings
on a topic in the last 90 days before anything changes, so one tap never silences a topic. The owner can also
set a level outright ("make fund filings quiet"), which wins over what was learned.

Some things are never quietened below a silent notification, whatever the thumbs say, because missing one costs
more than seeing one: reminders, approvals, rides, urgent email, bills, security and calls.

Everything sent (or held back) is kept 60 days for the Alerts page. It's the owner's own record: on their server.
"""

import json
import re
import uuid
from datetime import datetime, timedelta, timezone

from .memory import Memory
from .tools import ToolError

LEVELS = ("muted", "quiet", "notify", "alert")
OVERRIDE_KEY = "attention:levels"
KEEP_DAYS, LEARN_DAYS, MIN_RATINGS = 60, 90, 3
SCHEMA = """
CREATE TABLE IF NOT EXISTS alerts (
    id TEXT PRIMARY KEY, at TEXT NOT NULL, kind TEXT NOT NULL, topic TEXT NOT NULL, category TEXT NOT NULL,
    level TEXT NOT NULL, text TEXT NOT NULL, display TEXT DEFAULT '', rating INTEGER
);
CREATE INDEX IF NOT EXISTS alerts_topic ON alerts (topic, at);
"""
DEFAULT = {  # topic: starting level (anything not listed starts at alert, as before)
    "investing:live": "alert", "investing:ideas": "alert", "investing:signals": "notify",
    "investing:scan": "notify", "investing:funds": "quiet", "investing:school": "notify",
    "deals:watched": "alert", "deals:daily": "notify", "deals:weekly": "quiet",
}
PROTECTED = re.compile(r"remind|approval|ride|email|mail|bill|security|fraud|call|alarm|timer|safety")
CATEGORIES = (  # (label, what the topic or kind contains), first match wins
    ("Investing", r"invest|market|stock|signal|fund|school"), ("Deals", r"deal|price|shop"),
    ("Travel", r"travel|trip|flight|hotel|ride|commute|transit"), ("Email", r"mail"),
    ("Reminders", r"remind|alarm|timer|calendar|promise"), ("Health", r"health|sleep|oura|habit"),
    ("Money", r"money|bill|bank|budget|card|spend"), ("Tasks", r"job|task|report|research"),
    ("Messages", r"notification|message|chat|call"), ("Briefing", r"brief|morning|digest"),
    ("Home", r"home|thermostat|package|weather"),
)
TITLES = {"investing:live": "live price triggers", "investing:signals": "buy and sell signals",
          "investing:funds": "fund filings", "investing:scan": "the daily market scan",
          "investing:ideas": "trade ideas triggering", "investing:school": "Market school lessons",
          "deals:watched": "deals you asked to watch for", "deals:daily": "daily deal picks",
          "deals:weekly": "the weekly deals outlook"}


def _ready(memory: Memory) -> None:
    memory.db.executescript(SCHEMA)


def topic_of(kind: str, topic: str = "", text: str = "") -> str:
    """The topic of a message: the caller's, else worked out from the kind and how the text starts."""
    if topic:
        return topic
    head = (text or "").lstrip()[:2]
    if kind == "money":
        for mark, t in (("⚡", "investing:live"), ("📈", "investing:signals"), ("📉", "investing:signals"),
                        ("⚠", "investing:signals"), ("🏦", "investing:funds"), ("🔔", "deals:watched")):
            if head.startswith(mark):
                return t
    return kind or "other"


def category_of(topic: str) -> str:
    for label, rx in CATEGORIES:
        if re.search(rx, topic, re.I):
            return label
    return "Other"


def ratings(memory: Memory, topic: str, now: datetime | None = None) -> tuple[int, int]:
    """(thumbs up, thumbs down) on a topic's messages in the last 90 days."""
    _ready(memory)
    since = ((now or datetime.now(timezone.utc)) - timedelta(days=LEARN_DAYS)).isoformat()
    r = memory.db.execute("SELECT COALESCE(SUM(rating = 1), 0) AS up, COALESCE(SUM(rating = -1), 0) AS down "
                          "FROM alerts WHERE topic = ? AND at >= ?", (topic, since)).fetchone()
    return int(r["up"]), int(r["down"])


def level(memory: Memory, topic: str, now: datetime | None = None) -> dict:
    """{"level", "why"} for a topic now: the owner's setting, else the starting level moved by their thumbs."""
    start = DEFAULT.get(topic, "alert")
    floor = LEVELS.index("notify") if PROTECTED.search(topic) else 0
    chosen = json.loads(memory.get(OVERRIDE_KEY) or "{}").get(topic)
    if chosen in LEVELS:
        return {"level": LEVELS[max(LEVELS.index(chosen), floor)], "why": "you set it"}
    up, down = ratings(memory, topic, now)
    n, i, why = up + down, LEVELS.index(start), "the starting level"
    if n >= MIN_RATINGS:
        share = down / n
        if share >= 0.85 and n >= 6:
            i, why = i - 2, f"{down} of {n} thumbs down: two levels quieter"
        elif share >= 0.7:
            i, why = i - 1, f"{down} of {n} thumbs down: one level quieter"
        elif share <= 0.3:
            i, why = i + 1, f"{up} of {n} thumbs up: one level louder"
    i = max(floor, min(i, len(LEVELS) - 1))
    if floor and i == floor and LEVELS.index(start) > floor and why.endswith("quieter"):
        why += " (never fully silenced: missing one costs too much)"
    return {"level": LEVELS[i], "why": why}


def plan(memory: Memory, kind: str, text: str, display: str = "", topic: str = "",
         now: datetime | None = None) -> dict:
    """Decide how a message goes out and keep it: {"id", "topic", "category", "level"}."""
    _ready(memory)
    now = now or datetime.now(timezone.utc)
    topic = topic_of(kind, topic, text)
    got = level(memory, topic, now)
    out = {"id": uuid.uuid4().hex[:12], "topic": topic, "category": category_of(topic), "level": got["level"]}
    memory.db.execute("INSERT INTO alerts (id, at, kind, topic, category, level, text, display) VALUES "
                      "(?, ?, ?, ?, ?, ?, ?, ?)", (out["id"], now.isoformat(timespec="seconds"), kind, topic,
                                                   out["category"], out["level"], text[:4000], display[:6000]))
    memory.db.execute("DELETE FROM alerts WHERE at < ?", ((now - timedelta(days=max(KEEP_DAYS, LEARN_DAYS))).isoformat(),))
    memory.db.commit()
    return out


def rate(memory: Memory, alert_id: str, up: bool | None) -> dict:
    """The owner's thumb on a message (None takes it back). Returns the topic's level after it."""
    _ready(memory)
    row = memory.db.execute("SELECT topic FROM alerts WHERE id = ?", (str(alert_id)[:32],)).fetchone()
    if not row:
        raise ToolError("That message is too old to rate.")
    memory.db.execute("UPDATE alerts SET rating = ? WHERE id = ?",
                      (None if up is None else 1 if up else -1, str(alert_id)[:32]))
    memory.db.commit()
    return {"topic": row["topic"], **level(memory, row["topic"])}


def set_level(memory: Memory, topic: str, new: str) -> str:
    topic = find_topic(memory, topic)
    saved = json.loads(memory.get(OVERRIDE_KEY) or "{}")
    if new in ("learn", "auto", ""):
        saved.pop(topic, None)
        memory.set(OVERRIDE_KEY, json.dumps(saved))
        return f"{title(topic).capitalize()} now follow your thumbs again ({level(memory, topic)['level']})."
    if new not in LEVELS:
        raise ToolError(f"Levels: {', '.join(reversed(LEVELS))}, or 'learn' to go back to learning from thumbs.")
    saved[topic] = new
    memory.set(OVERRIDE_KEY, json.dumps(saved))
    got = level(memory, topic)["level"]
    return (f"{title(topic).capitalize()} are now {got}."
            + (" That's the quietest this kind can go: missing one costs too much." if got != new else ""))


def title(topic: str) -> str:
    return TITLES.get(topic) or topic.replace(":", " ").replace("_", " ") + " messages"


def find_topic(memory: Memory, words: str) -> str:
    """The topic the owner means, from its name or how they'd say it."""
    _ready(memory)
    known = set(DEFAULT) | {r["topic"] for r in memory.db.execute("SELECT DISTINCT topic FROM alerts")}
    w = (words or "").strip().lower()
    if w in known:
        return w
    parts = [p for p in re.findall(r"[a-z]+", w) if p not in ("the", "my", "alerts", "alert", "messages", "and")]
    hits = [t for t in known if parts and all(p.rstrip("s") in (t + " " + title(t)).lower() for p in parts)]
    if len(hits) != 1:
        raise ToolError("Which kind of message? " + ", ".join(sorted(f"{title(t)} ({t})" for t in known)))
    return hits[0]


def topics(memory: Memory) -> list[dict]:
    """Every topic seen or known: its category, level, why, and the thumbs so far."""
    _ready(memory)
    known = set(DEFAULT) | {r["topic"] for r in memory.db.execute("SELECT DISTINCT topic FROM alerts")}
    out = []
    for t in sorted(known):
        up, down = ratings(memory, t)
        out.append({"topic": t, "name": title(t), "category": category_of(t), **level(memory, t),
                    "thumbs_up": up, "thumbs_down": down})
    return out


def inbox(memory: Memory, days: int = 14, now: datetime | None = None) -> dict:
    """The Alerts page: messages of the last `days`, newest first, grouped by category."""
    _ready(memory)
    since = ((now or datetime.now(timezone.utc)) - timedelta(days=days)).isoformat()
    groups: dict[str, list] = {}
    for r in memory.db.execute("SELECT id, at, topic, category, level, text, display, rating FROM alerts "
                               "WHERE at >= ? ORDER BY at DESC LIMIT 400", (since,)):
        groups.setdefault(r["category"], []).append(
            {"id": r["id"], "at": r["at"], "topic": r["topic"], "name": title(r["topic"]), "level": r["level"],
             "text": r["display"] or r["text"], "rating": r["rating"]})
    return {"groups": [{"category": c, "count": len(v), "alerts": v} for c, v in
                       sorted(groups.items(), key=lambda kv: kv[1][0]["at"], reverse=True)],
            "topics": topics(memory), "levels": list(reversed(LEVELS))}


TOOL = {
    "name": "alert_settings",
    "description": (
        "How loud each kind of message Buddy sends on its own is, and what it has learned from the owner's "
        "thumbs. Levels: alert (pops up and is spoken), notify (silent notification), quiet (chat and Alerts "
        "page only), muted (Alerts page only). show: every kind with its level, why, and the thumbs so far. set "
        "(topic, level): set one outright, e.g. 'make fund filings quiet', 'mute the daily deal picks'; level "
        "'learn' goes back to learning from thumbs. Reminders, approvals, rides, urgent email, bills, security "
        "and calls can't go below notify. Use it when the owner says a kind of message is too much, not "
        "important, or should always pop up."),
    "input_schema": {"type": "object", "properties": {
        "action": {"type": "string", "enum": ["show", "set"]}, "topic": {"type": "string"},
        "level": {"type": "string", "enum": [*LEVELS, "learn"]}},
        "required": ["action"], "additionalProperties": False},
}


def run_tool(memory: Memory, args: dict) -> str:
    if args.get("action") == "set":
        return set_level(memory, args.get("topic", ""), args.get("level", ""))
    return json.dumps({"kinds": topics(memory),
                       "note": "Thumbs on a message move its kind after three ratings; a level you set wins."})
