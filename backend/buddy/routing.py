"""Smart model routing: each message goes to the model that does that kind of task well, at the lowest cost.

  1. Signals, by code (no model call, under a millisecond): the task (one of TASKS: reminders, quick facts,
     email, money, credit, markets, travel, places, writing, code, research, health / legal / tax, planning...),
     how hard it looks (length, several parts, "compare", "step by step", "strategy"), whether it's personal
     (then it never goes to a free model; that guard also runs again in free_models), and whether it follows
     a bigger question a moment ago (same model, so a follow-up keeps its context).
  2. Policy: each task has a tier (fast = Haiku, main = Sonnet); hard questions go up a tier; high-stakes ones
     (health, legal, tax, investing) with a hard question get a hint to step up to the expert (Opus, ask_expert).
     The user picks the mode in their own words ("save money on AI", "always use the best model"):
       saver     quick model unless the task needs tools or depth; it hands over when it must
       balanced  the default
       best      the main model for everything but greetings and closers
  3. Learning: every decision is logged (route_log) with its task and reason. A task where the quick model
     often fails (handed over, an error, a thumbs down, or "that's wrong" right after) over the last 14 days
     is moved to the main model automatically; it's tried on the quick model again after 14 days.
  0. Free models first (free_kind): small talk, and simple, general, timeless requests with nothing personal and no
     tool needed (a fact, an explanation, a translation, a poem or caption, ideas, rewording, a short coding
     how-to). Anything personal, current, needing Buddy's tools, high stakes or hard goes to a paid model. A free
     model that isn't sure hands it back; hand-backs, thumbs down and "that's wrong" count as misses, and a kind the
     free models keep missing goes to Claude for two weeks.
"""

import json
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from .config import settings

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS route_log (
    turn TEXT PRIMARY KEY,
    at TEXT NOT NULL,
    task TEXT NOT NULL,
    tier TEXT NOT NULL,          -- fast | main
    model TEXT NOT NULL,
    reason TEXT NOT NULL DEFAULT '',
    signals TEXT NOT NULL DEFAULT '{}',
    corrected INTEGER NOT NULL DEFAULT 0   -- the next message said it was wrong
);
CREATE INDEX IF NOT EXISTS route_log_at ON route_log (at);
"""
# (task, words that show it, tier, high stakes)
TASKS = [
    ("closer", None, "fast", False),
    ("reminder", r"remind me|reminder|alarm|timer|to-?do|my list|shopping list|tick off|add .{1,40} to (my )?(list|tasks?)",
     "fast", False),
    ("memory", r"remember (that|my)|where('?s| is) my|what did i (say|tell)|do you remember", "fast", False),
    ("quick_fact", r"^(what time|what day|today'?s date|what'?s the date|how old|when is)", "fast", False),
    ("health_legal_tax", r"\b(doctor|medical|symptom|medicine|dose|diagnos|lawyer|legal|contract|lawsuit|tax(es)?|irs|visa"
     r"|immigration)\b", "main", True),
    ("investing", r"\b(invest|stock|shares|etf|crypto|bitcoin|portfolio|trade|trading|breakout|options?|calls?|puts?)\b",
     "main", True),
    ("credit", r"\b(credit (score|report|karma)|hard inquir|fico|experian|transunion|equifax)\b", "main", False),
    ("money", r"\b(money|spend|spent|bill|bank|card|budget|payment|salary|savings?|loan|mortgage|subscription|refund)\b",
     "main", False),
    ("email", r"\b(e-?mail|mail|inbox|reply to|draft)\b", "main", False),
    ("travel", r"\b(flight|hotel|trip|travel|airport|seat|booking|visa)\b", "main", False),
    ("places", r"\b(restaurant|cafe|bar|spa|near me|nearby|place|uber|lyft|ride)\b", "main", False),
    ("code", r"\b(code|script|bug|error|python|javascript|sql|api|deploy|build an? (app|agent|tool))\b", "main", False),
    ("writing", r"\b(write|rewrite|draft|essay|letter|post|caption|summar|translate)\b", "main", False),
    ("research", r"\b(research|compare|pros and cons|review|best .{1,30} for|which is better|news|search|google|reddit)\b",
     "main", False),
    ("planning", r"\b(plan|schedule|itinerary|strategy|organi[sz]e|week|calendar)\b", "main", False),
]
TASK_RE = [(name, re.compile(rx, re.I) if rx else None, tier, stakes) for name, rx, tier, stakes in TASKS]
HARD = re.compile(r"(?i)\b(step by step|in detail|deep dive|compare|pros and cons|trade-?offs?|strategy|analy[sz]e|"
                  r"why|explain how|what should i|should i|optimi[sz]e|forecast|scenario|plan out)\b")
PERSONAL = re.compile(r"(?i)\b(my|me|mine|i|i'm|we|our|us)\b")
CORRECTION = re.compile(r"(?i)^(no[,.!]?\s|nope|that'?s (wrong|not (right|it|what))|not what i (asked|meant)|wrong|"
                        r"try again|you (got|misunderstood)|that'?s incorrect)")
MODES = ("saver", "balanced", "best")
LEARN_DAYS, MIN_SAMPLES, FAIL_RATE = 14, 6, 0.3
FOLLOW_UP_SECONDS = 120


@dataclass
class Decision:
    model: str
    tier: str
    task: str
    reason: str
    signals: dict = field(default_factory=dict)
    expert_hint: bool = False


def task_of(text: str, closer: bool = False) -> tuple[str, str, bool]:
    if closer:
        return "closer", "fast", False
    for name, rx, tier, stakes in TASK_RE:
        if rx is not None and rx.search(text):
            return name, tier, stakes
    return "chat", "", False


def difficulty(text: str) -> int:
    """0 easy .. 3 hard, from the message alone."""
    words = len(text.split())
    score = 0
    score += 1 if words > 18 else 0
    score += 1 if words > 45 else 0
    score += min(2, len(HARD.findall(text)))  # "compare ... step by step ... trade-offs" counts more than one
    score += 1 if text.count("?") >= 2 or re.search(r"(?i)\b(and also|then|as well as|plus)\b", text) else 0
    return min(3, score)


class Learner:
    """Which tasks the quick model keeps getting wrong lately (cached for ten minutes)."""

    def __init__(self):
        self._at, self._weak = None, set()

    def weak(self, memory, tier: str = "fast") -> set[str]:
        """Tasks this tier (fast = Haiku, free = the free models) got wrong too often in the last two weeks."""
        if memory is None:
            return set()
        cached = self._weak if isinstance(self._weak, dict) else {}
        if self._at is not None and time.monotonic() - self._at < 600 and tier in cached:
            return cached[tier]
        since = (datetime.now(timezone.utc) - timedelta(days=LEARN_DAYS)).isoformat()
        weak = set()
        try:
            rows = memory.db.execute(
                "SELECT r.task, COUNT(*) AS n, SUM(CASE WHEN r.corrected = 1 OR t.final_model != t.first_model OR "
                "t.outcome NOT IN ('ok', 'interrupted') OR (SELECT MIN(rating) FROM model_calls m WHERE m.turn = r.turn) "
                "= -1 THEN 1 ELSE 0 END) AS bad FROM route_log r LEFT JOIN turns t ON t.turn = r.turn "
                "WHERE r.at >= ? AND r.tier = ? GROUP BY r.task", (since, tier)).fetchall()
            for r in rows:
                if r["n"] >= MIN_SAMPLES and (r["bad"] or 0) / r["n"] >= FAIL_RATE and r["task"] not in ("closer",):
                    weak.add(r["task"])
        except Exception:
            weak = set()
        cached[tier] = weak
        self._at, self._weak = time.monotonic(), cached
        return weak


# --- the free models: simple, general requests, before any paid model --------------------------------------
FREE_WRITE = re.compile(r"(?i)^(write|compose|make up|give me) (a |an |me a |me an )?(short |funny |little )?(poem|haiku|"
                        r"limerick|joke|riddle|story|toast|caption|slogan|tagline|tweet|quote|rhyme)\b")
FREE_IDEAS = re.compile(r"(?i)^(give me|suggest|list|brainstorm|any|some)\b.{0,25}\b(ideas?|names?|suggestions|tips|"
                        r"ways|examples|words|synonyms|questions)\b")
FREE_REWORD = re.compile(r"(?i)^(rephrase|reword|rewrite|paraphrase|make (this|it) (sound|more)|fix the grammar|proofread|"
                         r"shorten|simplify)\b")
FREE_CODE = re.compile(r"(?i)\b(how (do|can) (i|you)|what'?s the (syntax|command)|regex for|one-?liner|example of)\b.{0,60}"
                       r"\b(python|javascript|typescript|sql|bash|shell|regex|css|html|java|kotlin|go|rust|excel|"
                       r"git|linux|json)\b")
FREE_FACT = re.compile(r"(?i)^(who|what|when|where|which|how (many|much|far|long|big|tall|old)|is it true|can you tell me)\b")
PERSONAL_REF = re.compile(r"(?i)\b(my|mine|our|ours|we|us|i'm|i've|i'd|i was|i am|i have|i had|i did|i spent|i bought|"
                          r"i owe|i need to|me and)\b")
CURRENT = re.compile(r"(?i)\b(today|tonight|tomorrow|yesterday|right now|current(ly)?|latest|recent|news|this (week|month|"
                     r"year)|price|weather|score|stock|near|nearby|open now)\b")
NEEDS_TOOLS = {"reminder", "memory", "money", "credit", "investing", "email", "travel", "places", "planning", "research",
               "health_legal_tax", "quick_fact"}


def free_kind(text: str, memory=None, learner: "Learner | None" = None) -> tuple[str, str]:
    """(free model kind, why) when a free model can answer the whole message, else ('', why not). Only general,
    timeless, simple requests with nothing personal: those need Claude (personal data stays on Claude) or Buddy's
    tools (email, money, reminders...). Hard ones go to a paid model; the free models' guard checks again."""
    from .router import NOT_FOR_FREE, free_kind as basic  # noqa: PLC0415
    t = text.strip()
    m = mode(memory)
    kind = basic(t)
    if kind == "small_talk":
        return kind, "small talk"
    if m == "best":
        return "", "best mode: paid models only"
    task, _, stakes = task_of(t)
    hard = difficulty(t)
    if stakes or task in NEEDS_TOOLS:
        return "", f"{task.replace('_', ' ')} needs Buddy's tools or care"
    if not kind and (PERSONAL_REF.search(t) or CURRENT.search(t)):
        return "", "personal or current"  # "how do I", "give me" and "tell me" are fine; "my", "we", "today" aren't
    if kind and NOT_FOR_FREE.search(t):
        return "", "personal, current or needs a tool"
    if hard > (2 if m == "saver" else 1) or len(t.split()) > (80 if m == "saver" else 50):
        return "", "too involved for a free model"
    if not kind:
        kind = ("rewrite" if FREE_REWORD.search(t) else "brainstorm" if FREE_WRITE.search(t) or FREE_IDEAS.search(t)
                else "code" if FREE_CODE.search(t) else "quick_answer" if FREE_FACT.search(t) and len(t.split()) <= 20
                else "")
    if not kind:
        return "", "not a kind of request the free models take"
    if f"free:{kind}" in (learner or Learner()).weak(memory, "free"):
        return "", f"the free models have been getting {kind.replace('_', ' ')} wrong lately"
    return kind, f"simple and general ({kind.replace('_', ' ')}): a free model"


def log_free(memory, turn: str, kind: str, model: str, handed_back: bool = False) -> None:
    """A free model answered (or handed it back to Claude, which counts as a miss for that kind)."""
    log(memory, f"{turn}:free" if handed_back else turn,
        Decision(model, "free", f"free:{kind}", "handed back to Claude" if handed_back else f"free model ({kind})"))
    if handed_back and memory is not None:
        try:
            memory.db.execute("UPDATE route_log SET corrected = 1 WHERE turn = ?", (f"{turn}:free",))
            memory.db.commit()
        except Exception:
            pass


def mode(memory) -> str:
    if memory is None:
        return "balanced"
    try:
        m = json.loads(memory.get("custom:settings") or "{}").get("routing", "balanced")
    except ValueError:
        m = "balanced"
    return m if m in MODES else "balanced"


def decide(text: str, *, closer: bool, simple: bool, recent_main: bool, memory=None, learner: Learner | None = None,
           via: str = "") -> Decision:
    fast, main = settings.fast_model, settings.chat_model
    task, tier, stakes = task_of(text, closer)
    hard = difficulty(text)
    words = len(text.split())
    personal = bool(PERSONAL.search(text))
    m = mode(memory)
    signals = {"words": words, "hard": hard, "personal": personal, "mode": m, "via": via}
    if not settings.router_enabled or not fast:
        return Decision(main, "main", task, "routing off", signals)
    if closer:
        return Decision(fast, "fast", task, "a closer: the quick model", signals)
    weak = (learner or Learner()).weak(memory, "fast")

    def done(t: str, why: str) -> Decision:
        hint = stakes and hard >= 2 and t == "main"
        return Decision(fast if t == "fast" else main, t, task, why, signals, hint)

    if m == "best":
        return done("main", "best mode: the main model")
    if task in weak and tier != "main":
        return done("main", f"the quick model has been getting {task.replace('_', ' ')} wrong lately")
    if recent_main and words <= 8 and task in ("chat", "quick_fact", "memory"):
        return done("main", "a follow-up to the last question: same model")
    if tier == "main":
        if m == "saver" and hard == 0 and words <= 12 and not stakes:
            return done("fast", f"saver mode: a short {task.replace('_', ' ')} question (hands over if needed)")
        return done("main", f"{task.replace('_', ' ')} needs the main model" + (" (high stakes)" if stakes else ""))
    if hard >= 2 or words > 18:
        return done("main", "a longer or harder question")
    if tier == "fast" or simple or words <= 4:
        return done("fast", f"{task.replace('_', ' ') if task != 'chat' else 'short chat'}: the quick model")
    if m == "saver" and hard == 0:
        return done("fast", "saver mode: the quick model (hands over if needed)")
    return done("main", "general question: the main model")


def log(memory, turn: str, d: Decision) -> None:
    if memory is None or not turn:
        return
    try:
        if not getattr(memory, "_route_log_ready", False):  # the table once per process, not every turn
            memory.db.executescript(SCHEMA_SQL)
            memory._route_log_ready = True
        memory.db.execute("INSERT OR REPLACE INTO route_log (turn, at, task, tier, model, reason, signals) VALUES "
                          "(?, ?, ?, ?, ?, ?, ?)", (turn, datetime.now(timezone.utc).isoformat(), d.task, d.tier, d.model,
                                                    d.reason[:120], json.dumps(d.signals)))
        memory.db.execute("DELETE FROM route_log WHERE at < ?",
                          ((datetime.now(timezone.utc) - timedelta(days=90)).isoformat(),))
        memory.db.commit()
    except Exception:
        pass


def note_correction(memory, previous_turn: str, text: str) -> bool:
    """'No, that's wrong' right after a reply: the previous routing decision counts as a miss."""
    if memory is None or not previous_turn or not CORRECTION.search(text.strip()):
        return False
    try:
        memory.db.execute("UPDATE route_log SET corrected = 1 WHERE turn = ?", (previous_turn,))
        memory.db.commit()
        return True
    except Exception:
        return False


def overview(memory, days: int = 30) -> dict:
    """How messages were routed lately: per task, the share on each model, misses, cost, and what was learned."""
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    memory.db.executescript(SCHEMA_SQL)
    out = []
    try:
        rows = memory.db.execute(
            "SELECT r.task, r.tier, COUNT(*) AS n, SUM(CASE WHEN r.corrected = 1 OR t.final_model != t.first_model OR "
            "t.outcome NOT IN ('ok', 'interrupted') THEN 1 ELSE 0 END) AS missed, "
            "SUM((SELECT COALESCE(SUM(cost_usd), 0) FROM model_calls m WHERE m.turn = r.turn)) AS cost "
            "FROM route_log r LEFT JOIN turns t ON t.turn = r.turn WHERE r.at >= ? GROUP BY r.task, r.tier", (since,)).fetchall()
    except Exception:
        rows = memory.db.execute("SELECT task, tier, COUNT(*) AS n, SUM(corrected) AS missed, 0 AS cost FROM route_log "
                                 "WHERE at >= ? GROUP BY task, tier", (since,)).fetchall()
    tasks: dict[str, dict] = {}
    for r in rows:
        t = tasks.setdefault(r["task"], {"task": r["task"], "free": 0, "fast": 0, "main": 0, "missed_on_fast": 0,
                                         "missed_on_free": 0, "cost_usd": 0.0})
        t[r["tier"]] = r["n"]
        if r["tier"] in ("fast", "free"):
            t[f"missed_on_{r['tier']}"] = r["missed"] or 0
        t["cost_usd"] = round(t["cost_usd"] + (r["cost"] or 0), 4)
    out = sorted(tasks.values(), key=lambda t: -(t["fast"] + t["main"]))
    return {"mode": mode(memory), "days": days, "tasks": out,
            "moved_up": sorted(Learner().weak(memory, "fast")),
            "kept_off_free": sorted(Learner().weak(memory, "free")),
            "models": {"free": "free models (Gemini, Groq...) when set up", "fast": settings.fast_model,
                       "main": settings.chat_model, "expert": "ask_expert (Opus)"}}


ROUTING_TOOL = {
    "name": "model_routing",
    "description": "How Buddy picks a model for each message: 'which model answers what?', 'how much does each kind "
    "of question cost?', 'why did you use the quick model?'. Returns per task (reminders, email, money, investing...) "
    "how many went to the quick and the main model, how many the quick model missed, the cost, the current mode, and "
    "tasks moved up to the main model because the quick one kept missing. To change the mode use customize_buddy "
    "set routing saver|balanced|best.",
    "input_schema": {"type": "object", "properties": {"days": {"type": "integer", "minimum": 1, "maximum": 90}},
                     "additionalProperties": False},
}


# --- things to make (an image, a deck, a document) on a general topic: a free model, no Claude -----------------
MAKE = re.compile(r"(?i)^(?:please\s+)?(?:can you\s+)?(?:make|create|build|generate|draw|design|produce|prepare|give me)\s+"
                  r"(?:me\s+)?(?:an?\s+|some\s+|the\s+)?(?:\d+[- ]slide\s+)?(?P<what>image|picture|illustration|logo|drawing|"
                  r"poster|artwork|presentation|deck|slide ?deck|slides|ppt|pptx|power ?point|word doc(?:ument)?|docx|"
                  r"document)\b[^.?!]{0,40}?\b(?:about|on|of|for|showing|explaining)\s+(?P<topic>.{3,200})$")


def free_make(text: str, memory=None) -> tuple[str, str]:
    """('image' | 'pptx' | 'docx', topic) when a free model can make it: a general topic with nothing personal and
    nothing current. Else ('', '')."""
    t = text.strip().rstrip(".!")
    m = MAKE.search(t)
    if not m or mode(memory) == "best":
        return "", ""
    topic = m.group("topic").strip()
    if PERSONAL_REF.search(topic) or CURRENT.search(topic) or task_of(topic)[2]:
        return "", ""  # about them, about now, or high stakes: Claude makes it (with Buddy's tools and care)
    what = m.group("what").lower()
    kind = "image" if what in ("image", "picture", "illustration", "logo", "drawing", "poster", "artwork") else \
        "docx" if "doc" in what else "pptx"
    if f"free:{kind}" in Learner().weak(memory, "free"):
        return "", ""
    return kind, topic
