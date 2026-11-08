"""How Buddy thinks: know where to look, plan and gather, fact-check, decide like an advisor, learn from results.

1. Source map (SOURCES): every source Buddy has, what it answers, the tool that reads it, and whether it's set up.
   pick(question) ranks the ones that matter for a question, so Buddy looks in the right places first ("my flight"
   -> plans, flight_status, email) instead of saying "I don't know".
2. Plan, then gather: for an involved question (two or more sources, or a decision) the memory pack carries a short
   plan: which tools, called together in one step where they don't depend on each other.
3. Fact-check (verify): after a reply that used tools, the numbers and dates in it are checked against what the
   tools actually returned; any that aren't there go to Claude's quick model with the tool results, and a wrong
   one gets a "Fact check:" correction right after. Web facts need two sources or are labelled as one source.
4. Decide like an advisor: a question that asks for a decision gets a structure (2-3 real options, what matters to
   the user from their saved preferences, trade-offs, ONE recommendation and why, what would change it), plus past
   related decisions and how they turned out. High stakes: a second opinion (ask_expert) and never acting without
   the user's yes.
5. Learn from results: each recommendation is logged; a few days later Buddy asks once how it went, and the outcome
   (decision_outcome tool) is used the next time a similar decision comes up.

Adapting: a question in an area with no source connected (weather, packages, health data...) is a gap. In the
moment Buddy says what source would answer it (CATALOG: free/official ones first) and how to set it up; gaps are
counted, and once a week the most-asked missing areas become a suggestion. Areas the catalog doesn't know are
proposed by Claude's quick model from the gap topics.
"""

import json
import logging
import os
import re
from datetime import datetime, timedelta, timezone

from .memory import Memory

log = logging.getLogger(__name__)

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS reasoning_decisions (
    id INTEGER PRIMARY KEY,
    at TEXT NOT NULL,
    question TEXT NOT NULL,
    advice TEXT NOT NULL,             -- what Buddy recommended (the start of its answer)
    topic TEXT NOT NULL DEFAULT '',
    asked_at TEXT,                    -- when Buddy asked how it went
    outcome TEXT NOT NULL DEFAULT '', -- what the user said happened
    outcome_at TEXT
);
CREATE TABLE IF NOT EXISTS reasoning_gaps (
    area TEXT PRIMARY KEY,            -- catalog area, or 'other:<words>'
    asks INTEGER NOT NULL DEFAULT 0,
    example TEXT NOT NULL DEFAULT '',
    last_at TEXT NOT NULL,
    suggested_at TEXT
);
"""


def _env(*keys: str):
    return lambda memory: all((os.getenv(k) or "").strip() for k in keys)


def _table(sql: str):
    def check(memory) -> bool:
        try:
            return memory.db.execute(sql).fetchone() is not None
        except Exception:
            return False
    return check


def _always(memory) -> bool:
    return True


# id: (what it answers, tools, question words, set up?)
SOURCES = {
    "memory": ("facts you told Buddy, people, preferences, lessons", ["recall_memory", "brain"],
               r"\b(my|remember|told you|who is|what is my|preference)\b", _always),
    "library": ("documents, photos and statements you uploaded", ["library", "process_data"],
                r"\b(document|file|upload|pdf|report|statement|receipt|scan|photo|policy|contract|lab|medical)\b",
                _table("SELECT 1 FROM library_items LIMIT 1")),
    "email": ("bookings, bills, receipts, messages in your inboxes", ["search_my_apps", "important_email"],
              r"\b(email|mail|inbox|sent me|confirmation|receipt|booking|invoice|order|newsletter)\b",
              _table("SELECT 1 FROM records WHERE kind = 'email' LIMIT 1")),
    "notifications": ("what your phone's apps told you (banks, airlines, rides, chats)", ["search_my_apps"],
                      r"\b(notification|app said|alert|message from|whatsapp|text)\b",
                      _table("SELECT 1 FROM records WHERE kind = 'notification' LIMIT 1")),
    "calendar": ("your events, when you're free", ["agenda"],
                 r"\b(calendar|meeting|appointment|schedule|free|busy|agenda|event|tomorrow|today|this week)\b",
                 _table("SELECT 1 FROM records WHERE kind = 'event' LIMIT 1")),
    "plans": ("upcoming flights, hotels and reservations found in email and uploads", ["flight_status", "travel"],
              r"\b(trip|flight|hotel|stay|reservation|itinerary|travel|vacation|check-?in)\b",
              _table("SELECT 1 FROM brain_plans LIMIT 1")),
    "bank": ("balances, transactions, payments, transfers (Plaid)", ["account_balances", "spending", "finance"],
             r"\b(balance|bank|checking|savings|spent|spend|transaction|transfer|paid|payment|net worth|account)\b",
             _table("SELECT 1 FROM plaid_items LIMIT 1")),
    "bills": ("bills due, from email, notifications, statements and card due dates", ["bills"],
              r"\b(bills?|due|owe|statement|minimum|autopay|rent|utility)\b", _always),
    "cards": ("card benefits, credits used and unused, best card per purchase", ["card_benefits"],
              r"\b(card|credit card|benefit|credit|points|miles|cashback|reward|annual fee)\b", _always),
    "credit": ("credit scores and credit report changes", ["credit"],
               r"\b(credit score|fico|credit report|inquiry|experian|equifax|transunion|credit karma)\b", _always),
    "flights": ("live flight status: gates, delays, landing (AirLabs, airline apps, FlightAware)", ["flight_status"],
                r"\b(flight|gate|boarding|delayed?|landed|departure|arrival|terminal)\b",
                lambda m: bool((os.getenv("BUDDY_AIRLABS_KEY") or os.getenv("BUDDY_AEROAPI_KEY") or "").strip())
                or True),  # airline messages work with no key
    "transit": ("MTA, NJ Transit and PATH alerts and next trains", ["transit"],
                r"\b(train|subway|path|light rail|nj transit|mta|bus|lirr|commute|line)\b", _always),
    "travel_search": ("flight and hotel prices (Duffel, Google Flights / Hotels, Travelpayouts)", ["travel"],
                      r"\b(cheap|fare|ticket|book|price|hotel|flights? to|fly to)\b",
                      lambda m: bool((os.getenv("BUDDY_SERPAPI_KEY") or os.getenv("BUDDY_DUFFEL_TOKEN") or "").strip())),
    "markets": ("stock and crypto prices, chart setups, chatter", ["markets"],
                r"\b(stock|share|market|crypto|bitcoin|nvda|invest|trade|ticker|etf|nifty)\b", _always),
    "news": ("what's trending on a topic: news, Hacker News, Reddit, arXiv...", ["trends", "trending"],
             r"\b(news|trending|latest|what's new|happening|announced|launch)\b", _always),
    "places": ("places near you, reviews, open now", ["find_places", "where_am_i"],
               r"\b(near|nearby|restaurant|cafe|open now|place|where can i|closest)\b", _always),
    "health": ("health reports from what you shared", ["health_report"],
               r"\b(health|sleep|steps|weight|blood|heart|doctor|medicine|lab)\b", _always),
    "tasks": ("your tasks, goals and reminders", ["list_tasks", "list_goals", "list_reminders"],
              r"\b(task|todo|to-do|goal|remind|reminder)\b", _always),
    "buddy_itself": ("how Buddy itself works now: its live code, docs and website (newer than any upload)",
                     ["buddy_code", "recall_memory"],
                     r"\b(buddy|your (code|architecture|design|setup|features?)|architecture|how do you work|how does buddy|"
                     r"module|which model|your tools)\b", _always),
    "web": ("public facts, prices, how-tos (web search)", ["google_search", "read_pages"],
            r"\b(what is|how (do|to|does)|why|who|price of|compare|best|review|latest)\b", _always),
}

# Areas Buddy has no source for yet, and the source that would answer them (free or official first).
CATALOG = {
    "weather": (r"\b(weather|rain|forecast|temperature|snow|umbrella|humid)\b",
                "Open-Meteo (free, no key) for forecasts where you are and at your trip's destination"),
    "packages": (r"\b(package|parcel|delivery|tracking number|shipment|ups|fedex|usps|dhl|amazon order)\b",
                 "17TRACK or AfterShip (free tiers) for package tracking; Buddy already reads shipping emails"),
    "fitness": (r"\b(steps|workout|heart rate|sleep score|calories burned|fitness)\b",
                "Google Health Connect on the phone (steps, sleep, heart rate), read by the Buddy app"),
    "traffic": (r"\b(traffic|commute time|drive time|how long to drive|eta)\b",
                "Google Routes API or OpenRouteService (free tier) for live drive times"),
    "restaurants_booking": (r"\b(reservation at|book a table|resy|opentable|table for)\b",
                            "Resy or OpenTable links (booking stays with you; Buddy finds times)"),
    "sports": (r"\b(score|game tonight|match|nba|nfl|cricket|premier league|ipl)\b",
               "API-Sports or TheSportsDB (free tiers) for scores and schedules"),
    "utilities": (r"\b(electric|electricity|gas bill|water bill|pse&g|con ed|utility usage)\b",
                  "Your utility's account emails (already read) or Arcadia for usage data"),
    "home_value": (r"\b(home value|house price|zillow|property value|mortgage rate)\b",
                   "Zillow / Redfin estimates (no official free API; Buddy can read their public pages)"),
    "currency": (r"\b(exchange rate|convert .* to (inr|usd|eur)|forex|rupee rate)\b",
                 "Frankfurter (free, no key, European Central Bank rates)"),
}

DECISION = re.compile(r"(?i)\b(should i|which (one|is better|card|flight|hotel|option)|better (to|option)|worth it|"
                      r"vs\.?|versus|or should|recommend|what would you (do|pick|choose)|best (option|way|card|time)|"
                      r"decide|pick between|choose between)\b")
HIGH_STAKES = re.compile(r"(?i)\b(invest|buy|sell|loan|mortgage|refinance|credit|debt|insurance|surgery|medicine|"
                         r"doctor|lawyer|legal|tax|contract|visa|immigration|book|cancel|transfer|\$\d{3,})\b")
NUMBER = re.compile(r"(?<![\w.])(?:[$₹€£]\s?)?\d[\d,]*(?:\.\d+)?(?:\s?%|\s?(?:am|pm|AM|PM))?")
FOLLOW_UP_DAYS = 3


class Reasoning:
    def __init__(self, memory: Memory):
        self.memory = memory
        memory.db.executescript(SCHEMA_SQL)

    # 1. where to look --------------------------------------------------------------------------------------
    def pick(self, question: str) -> list[dict]:
        q = question.lower()
        out = []
        for sid, (what, tools, words, ready) in SOURCES.items():
            hits = len(re.findall(words, q))
            if hits:
                try:
                    ok = bool(ready(self.memory))
                except Exception:
                    ok = False
                out.append({"id": sid, "what": what, "tools": tools, "ready": ok, "score": hits})
        return sorted(out, key=lambda s: (-s["score"], s["id"]))[:5]

    def gaps_for(self, question: str) -> list[dict]:
        q = question.lower()
        return [{"area": area, "suggest": how} for area, (words, how) in CATALOG.items() if re.search(words, q)]

    def note_gap(self, area: str, example: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        self.memory.db.execute(
            "INSERT INTO reasoning_gaps (area, asks, example, last_at) VALUES (?, 1, ?, ?) ON CONFLICT (area) DO "
            "UPDATE SET asks = asks + 1, example = excluded.example, last_at = excluded.last_at",
            (area[:60], example[:160], now))
        self.memory.db.commit()

    # 2 + 4: the plan that rides with the question ------------------------------------------------------------
    def plan_note(self, question: str) -> str:
        """A short thinking plan for involved questions; '' for simple ones."""
        sources = self.pick(question)
        gaps = self.gaps_for(question)
        decision = bool(DECISION.search(question))
        aging = any(s["id"] in ("library", "buddy_itself") for s in sources)  # documents vs what's running now
        if len(sources) < 2 and not decision and not gaps and not aging:
            return ""
        lines = []
        ready = [s for s in sources if s["ready"]]
        if ready:
            lines.append("Look in: " + "; ".join(f"{s['id']} ({s['what']}) via {'/'.join(s['tools'])}" for s in ready)
                         + ". Call the ones that don't depend on each other together in one step; don't guess what a "
                         "tool can tell you.")
        missing = [s for s in sources if not s["ready"]]
        if missing:
            lines.append("Not set up (say so if it matters): " + ", ".join(s["id"] for s in missing) + ".")
        for g in gaps:
            self.note_gap(g["area"], question)
            lines.append(f"No source connected for {g['area'].replace('_', ' ')}: answer as well as you can (web), "
                         f"and suggest connecting {g['suggest']}.")
        if any(s["id"] in ("library", "buddy_itself") for s in sources):
            lines.append("Uploaded documents can be out of date. When a live source covers the same thing (Buddy's "
                         "code and docs, the bank, the calendar, email), trust the newer one, combine them, and say "
                         "what changed since the document (with its date).")
        lines.append("Check before you say it: numbers, dates and times come from tool results, not memory; a fact "
                     "from the web needs two sources or say it's one source; say where each answer came from.")
        if decision:
            lines.append("This is a decision: give 2-3 real options, what matters to the user (their saved "
                         "preferences), the trade-offs, then ONE recommendation and why, and what would change it."
                         + (" High stakes: get a second opinion with ask_expert first, and never act without the "
                            "user's yes." if HIGH_STAKES.search(question) else ""))
            past = self.related_decisions(question)
            if past:
                lines.append("Past related decisions and how they went: " + " | ".join(past))
        return "\n\n(How to think about this, from Buddy's source map:\n- " + "\n- ".join(lines) + ")"

    # 3. fact-check ---------------------------------------------------------------------------------------------
    @staticmethod
    def unsupported_numbers(answer: str, evidence: str) -> list[str]:
        """Numbers in the answer that appear nowhere in what the tools returned (or the memory pack)."""
        ev = re.sub(r"[,\s$₹€£]", "", evidence.lower())
        out = []
        for m in NUMBER.finditer(answer):
            raw = m.group(0).strip()
            core = re.sub(r"[,\s$₹€£%]|am|pm", "", raw.lower())
            if not core or len(re.sub(r"\D", "", core)) < 2:
                continue  # single digits ("2 options") aren't claims worth checking
            variants = {core, core.rstrip("0").rstrip(".") if "." in core else core, core.split(".")[0]}
            if not any(v and v in ev for v in variants):
                out.append(raw)
        return list(dict.fromkeys(out))[:8]

    async def verify(self, question: str, answer: str, evidence: str, ask=None) -> str:
        """'' when the reply holds up; else a short correction to say ('Fact check: ...')."""
        if not evidence.strip() or not answer.strip():
            return ""
        doubtful = self.unsupported_numbers(answer, evidence)
        if not doubtful:
            return ""
        got = await (ask or _ask_verifier)(self.memory, question, answer, evidence[-12000:], doubtful)
        issues = [i for i in (got or {}).get("issues") or [] if i.get("correct")]
        if not issues:
            return ""
        fixes = "; ".join(f"{i['claim']} should be {i['correct']}" + (f" ({i['source']})" if i.get("source") else "")
                          for i in issues[:3])
        return f"Fact check: {fixes}."

    # 5. decisions and how they went -----------------------------------------------------------------------------
    def log_decision(self, question: str, answer: str) -> int | None:
        if not DECISION.search(question) or len(answer) < 40:
            return None
        topic = " ".join(sorted(set(w for w in re.findall(r"[a-z]{4,}", question.lower())
                                    if w not in {"should", "which", "better", "would", "what", "worth"}))[:6])
        cur = self.memory.db.execute(
            "INSERT INTO reasoning_decisions (at, question, advice, topic) VALUES (?, ?, ?, ?)",
            (datetime.now(timezone.utc).isoformat(), question[:300], answer[:400], topic))
        self.memory.db.commit()
        return cur.lastrowid

    def related_decisions(self, question: str, k: int = 3) -> list[str]:
        words = set(re.findall(r"[a-z]{4,}", question.lower()))
        rows = self.memory.db.execute("SELECT * FROM reasoning_decisions ORDER BY id DESC LIMIT 200").fetchall()
        scored = [(len(words & set(r["topic"].split())), r) for r in rows]
        out = []
        for score, r in sorted(scored, key=lambda x: -x[0]):
            if score < 2 or len(out) >= k:
                continue
            out.append(f"{r['at'][:10]} asked \"{r['question'][:80]}\", advised \"{r['advice'][:120]}\""
                       + (f", outcome: {r['outcome'][:120]}" if r["outcome"] else ", outcome not known yet"))
        return out

    def follow_ups(self, now: datetime | None = None) -> list[dict]:
        """Decisions from a few days ago: ask once how they went (one a day at most)."""
        now = now or datetime.now(timezone.utc)
        if self.memory.get("reasoning:asked_day") == now.date().isoformat():
            return []
        before = (now - timedelta(days=FOLLOW_UP_DAYS)).isoformat()
        r = self.memory.db.execute(
            "SELECT * FROM reasoning_decisions WHERE asked_at IS NULL AND outcome = '' AND at <= ? AND at >= ? "
            "ORDER BY at DESC LIMIT 1", (before, (now - timedelta(days=21)).isoformat())).fetchone()
        if r is None:
            return []
        return [{"id": r["id"], "text": f"A few days ago you asked \"{r['question'][:90]}\" and I suggested: "
                 f"{r['advice'][:140].rstrip('.')}. How did it turn out? Telling me helps me advise better."}]

    def asked(self, decision_id: int, now: datetime | None = None) -> None:
        now = now or datetime.now(timezone.utc)
        self.memory.db.execute("UPDATE reasoning_decisions SET asked_at = ? WHERE id = ?", (now.isoformat(), decision_id))
        self.memory.set("reasoning:asked_day", now.date().isoformat())
        self.memory.set("reasoning:awaiting", str(decision_id))
        self.memory.db.commit()

    def outcome(self, decision_id: int, text: str) -> str:
        n = self.memory.db.execute("UPDATE reasoning_decisions SET outcome = ?, outcome_at = ? WHERE id = ?",
                                   (text.strip()[:400], datetime.now(timezone.utc).isoformat(), int(decision_id))).rowcount
        self.memory.set("reasoning:awaiting", "")
        self.memory.db.commit()
        return "Thanks, I'll use that next time." if n else "I couldn't find that decision."

    def awaiting_note(self) -> str:
        did = self.memory.get("reasoning:awaiting") or ""
        if not did:
            return ""
        return (f"\n\n(You asked the user how decision #{did} turned out. If this message answers it, call "
                f"decision_outcome with id {did} and what happened.)")

    # adapting: suggest new sources from the gaps -----------------------------------------------------------
    async def weekly_suggestion(self, ask=None) -> str:
        week = datetime.now(timezone.utc).strftime("%G-W%V")
        if self.memory.get("reasoning:suggested_week") == week:
            return ""
        rows = [dict(r) for r in self.memory.db.execute(
            "SELECT * FROM reasoning_gaps WHERE asks >= 2 AND (suggested_at IS NULL OR suggested_at < ?) "
            "ORDER BY asks DESC LIMIT 3", ((datetime.now(timezone.utc) - timedelta(days=30)).isoformat(),))]
        self.memory.set("reasoning:suggested_week", week)
        if not rows:
            return ""
        parts = []
        for r in rows:
            if r["area"] in CATALOG:
                parts.append(f"{r['area'].replace('_', ' ')} ({r['asks']} times): {CATALOG[r['area']][1]}")
            else:
                idea = await (ask or _ask_source_idea)(self.memory, r["example"])
                if idea:
                    parts.append(f"questions like \"{r['example'][:60]}\" ({r['asks']} times): {idea}")
            self.memory.db.execute("UPDATE reasoning_gaps SET suggested_at = ? WHERE area = ?",
                                   (datetime.now(timezone.utc).isoformat(), r["area"]))
        self.memory.db.commit()
        if not parts:
            return ""
        return ("You've asked about things I have no source for. I could connect: " + " | ".join(parts) +
                ". Want me to set any of these up?")

    def note_unanswered(self, question: str) -> None:
        """A reply that ended in 'I don't know' with no catalog area: an 'other' gap, for the weekly suggestion."""
        if self.gaps_for(question):
            return
        words = [w for w in re.findall(r"[a-z]{4,}", question.lower())
                 if w not in {"what", "when", "where", "which", "about", "have", "does", "your", "with"}][:3]
        if words:
            self.note_gap("other:" + " ".join(words), question)


LEARN_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["facts"],
    "properties": {"facts": {"type": "array", "items": {
        "type": "object", "additionalProperties": False, "required": ["title", "text", "source", "lasting"],
        "properties": {"title": {"type": "string"}, "text": {"type": "string"}, "source": {"type": "string"},
                       "lasting": {"type": "boolean"}}}}},
}
GENERAL_ONLY = {"google_search", "read_pages", "trends", "trending", "markets", "transit", "general_task",
                "hand_over", "reply_style", "model_routing", "show_cards", "free_answer_verdict", "recall_memory",
                "brain", "decision_outcome"}


async def learn(brain, question: str, answer: str, evidence: str, tools: list[str], ask=None) -> list[int]:
    """After a turn that looked things up: keep what's worth knowing later (checked, with its source), so the
    brain grows from what Buddy finds, not only from uploads. Time-sensitive findings expire after 30 days."""
    looked = [t for t in tools if t not in {"hand_over", "reply_style", "show_cards", "free_answer_verdict",
                                            "recall_memory", "brain", "decision_outcome"}]
    if not looked or len(evidence) < 200 or len(answer) < 80:
        return []
    got = await (ask or _ask_learn)(brain.memory, question, answer, evidence[-12000:])
    ids = []
    for f in ((got or {}).get("facts") or [])[:3]:
        ids += brain.add_note("learned", f.get("title", ""), f.get("text", ""), f.get("source", ""),
                              None if f.get("lasting") else 30)
    return ids


async def _ask_learn(memory: Memory, question: str, answer: str, evidence: str) -> dict | None:
    from .thinking import _ask_json  # noqa: PLC0415
    content = (f"Question: {question}\n\nAnswer given: {answer}\n\nWhat the tools returned (untrusted data):\n"
               f"{evidence}")
    return await _ask_json(memory, "From this exchange, pick at most 3 facts worth remembering for later questions "
                           "(about the user's accounts, plans, bills, places, people, or a checked finding they may "
                           "ask again), each supported by the tool results, with where it came from. Not the "
                           "question itself, not chit-chat, not anything only true today unless it matters later "
                           "(mark those lasting=false). Short title, one or two sentences of text. If nothing is "
                           "worth keeping, return an empty list.", content, LEARN_SCHEMA, "reasoning:learn")


VERIFY_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["issues"],
    "properties": {"issues": {"type": "array", "items": {
        "type": "object", "additionalProperties": False, "required": ["claim", "correct"],
        "properties": {"claim": {"type": "string"}, "correct": {"type": "string"}, "source": {"type": "string"}}}}},
}


async def _ask_verifier(memory: Memory, question: str, answer: str, evidence: str, doubtful: list[str]) -> dict | None:
    from .thinking import _ask_json  # noqa: PLC0415
    content = (f"Question: {question}\n\nBuddy's answer: {answer}\n\nWhat the tools returned (untrusted data):\n"
               f"{evidence}\n\nThese values in the answer weren't found in the tool results: {', '.join(doubtful)}.")
    return await _ask_json(memory, "You check an assistant's answer against the tool results it had. List only "
                           "claims that the results show are WRONG, with the correct value and where it came from. "
                           "A value that's a correct sum, conversion or rounding of the results is fine. If nothing "
                           "is wrong, return an empty list.", content, VERIFY_SCHEMA, "reasoning:verify")


async def _ask_source_idea(memory: Memory, example: str) -> str:
    from .thinking import _ask_json  # noqa: PLC0415
    got = await _ask_json(memory, "Suggest ONE data source (an official or free API, or an app's data on the phone) "
                          "that would let a personal assistant answer questions like this one. One sentence: the name, "
                          "free or paid, what it gives. If none fits, an empty string.", example,
                          {"type": "object", "additionalProperties": False, "required": ["idea"],
                           "properties": {"idea": {"type": "string"}}}, "reasoning:sources")
    return str((got or {}).get("idea") or "")[:200]


DECISION_TOOL = {
    "name": "decision_outcome",
    "description": "Record how a decision Buddy advised on turned out (the user's words), so future advice on similar "
    "decisions uses it. id: the decision number Buddy asked about; outcome: what happened.",
    "input_schema": {"type": "object", "properties": {"id": {"type": "integer"}, "outcome": {"type": "string"}},
                     "required": ["id", "outcome"], "additionalProperties": False},
}


# --- Buddy knows itself from live sources: its code, docs and website ---------------------------------------
def refresh_self_knowledge(brain, root=None) -> int:
    """Rebuild Buddy's notes about itself whenever its code or docs change: one overview built from the code
    (every module's own description, its tools, its pages), each module's description, and the docs. Uploaded
    architecture documents age; these are rebuilt from what's actually running. No AI: the code describes itself."""
    import ast  # noqa: PLC0415
    import hashlib  # noqa: PLC0415
    from pathlib import Path  # noqa: PLC0415
    pkg = Path(__file__).resolve().parent
    repo = Path(root) if root else pkg.parents[1]
    files = sorted(pkg.glob("*.py")) + sorted(pkg.glob("connectors/*.py"))
    docs = sorted((repo / "docs").glob("*.md")) if (repo / "docs").exists() else []
    web = sorted((repo / "web").glob("*.html")) if (repo / "web").exists() else []
    stamp = hashlib.sha1("".join(f"{p.name}:{p.stat().st_mtime_ns}:{p.stat().st_size}"
                                 for p in files + docs + web).encode()).hexdigest()[:16]
    if brain.memory.get("reasoning:self_stamp") == stamp:
        return 0
    brain.memory.db.execute("DELETE FROM brain_notes WHERE kind = 'self'")
    modules, tools = [], []
    for p in files:
        try:
            tree = ast.parse(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        doc = (ast.get_docstring(tree) or "").strip()
        name = str(p.relative_to(pkg))
        for node in ast.walk(tree):  # tools the model can call: {"name": "...", "description": "..."}
            if isinstance(node, ast.Dict):
                keys = [k.value for k in node.keys if isinstance(k, ast.Constant)]
                if "name" in keys and "input_schema" in keys:
                    v = node.values[keys.index("name")]
                    if isinstance(v, ast.Constant) and isinstance(v.value, str):
                        tools.append(f"{v.value} ({name})")
        if doc:
            modules.append((name, doc))
            brain.add_note("self", f"Buddy's code: {name}", doc[:4000], f"backend/buddy/{name} (live code)")
    when = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    overview = (f"Buddy's architecture, built from the running code on {when} ({len(files)} server modules, "
                f"{len(tools)} tools, {len(web)} web pages, {len(docs)} docs).\n\n"
                "Server modules (each one's own description, first line):\n" +
                "\n".join(f"- {n}: {d.splitlines()[0][:160]}" for n, d in modules) +
                "\n\nTools the AI can use: " + ", ".join(sorted(tools)) +
                "\n\nWeb pages: " + ", ".join(p.name for p in web) +
                "\n\nDocs: " + ", ".join(p.name for p in docs))
    brain.add_note("self", "Buddy's architecture (current, from the code)", overview, "built from the live code")
    for p in docs:
        try:
            brain.add_note("self", f"Buddy's docs: {p.name}", p.read_text(encoding="utf-8")[:12000],
                           f"docs/{p.name} (updated {datetime.fromtimestamp(p.stat().st_mtime, timezone.utc):%Y-%m-%d})")
        except Exception:
            continue
    brain.memory.set("reasoning:self_stamp", stamp)
    return len(modules)
