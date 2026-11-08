"""What was said earlier, searchable: Claude gets only the latest turns, and looks older ones up when it needs them.

Every plain message of the chat (the owner's words and Buddy's replies, and what Buddy said on its own) is kept here
on the server, in SQLite with a full-text index (FTS5, ranked with BM25: the same kind of word search Claude uses to
find its tools). Tool results are not kept: they can be fetched again from their tool. Nothing leaves the server
except the few lines a search returns, and those go only to Claude (never a free model). Kept 30 days.
"""

import re
import sqlite3
from datetime import datetime, timedelta, timezone

KEEP_DAYS = 30
MAX_CHARS = 2000  # per message: a long report is kept in full enough to find it

SCHEMA = """
CREATE TABLE IF NOT EXISTS convo_log (
    id INTEGER PRIMARY KEY,
    at TEXT NOT NULL,          -- ISO-8601, UTC
    role TEXT NOT NULL,        -- user | assistant
    text TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS convo_log_at ON convo_log (at);
CREATE VIRTUAL TABLE IF NOT EXISTS convo_fts USING fts5(text, content='convo_log', content_rowid='id',
    tokenize='porter unicode61');
"""

TOOL = {
    "name": "recall_conversation",
    "description": "Search what was said earlier in your chats with the user (their words, your replies, and "
    "reports or messages you sent on your own), beyond the few latest turns you can see. Use it when they refer to "
    "something said before that isn't in view ('the report you sent', 'what we discussed yesterday', 'that "
    "restaurant you mentioned'). It matches words (any word form: report/reports), not meaning, so pass the "
    "key words plus related words and synonyms the earlier chat may have used (for 'car trouble': car brakes "
    "noise dealer repair service). Nothing found: try once more with other words. Optionally how many days back.",
    "input_schema": {
        "type": "object",
        "properties": {"query": {"type": "string"},
                       "when": {"type": "string", "description": "A time if one was named: 'yesterday', 'this "
                                "morning', 'last night', 'on Tuesday', 'last week', '3 days ago'."},
                       "days": {"type": "integer", "description": "How far back, in days (default 7, at most 30)."}},
        "required": ["query"],
        "additionalProperties": False,
    },
}


def _ready(db: sqlite3.Connection) -> None:
    old = db.execute("SELECT sql FROM sqlite_master WHERE name = 'convo_fts'").fetchone()
    if old and "porter" not in old[0]:  # built before word forms matched: rebuild it from the log
        db.execute("DROP TABLE convo_fts")
        db.executescript(SCHEMA)
        db.execute("INSERT INTO convo_fts (convo_fts) VALUES ('rebuild')")
        db.commit()
        return
    db.executescript(SCHEMA)


AUTO_MARK = "(Found automatically in earlier chats"   # the lookup before a reply (auto_context)
EARLIER_MARK = "(Earlier in this chat, no longer shown"  # the topic line once old turns drop out
MARKS = (AUTO_MARK, EARLIER_MARK)


def text_of(message: dict) -> str:
    """The words of a plain message; '' for tool calls and tool results (and Buddy's own added notes)."""
    content = message.get("content")
    if isinstance(content, str):
        return content
    parts = []
    for block in content or []:
        kind = block.get("type") if isinstance(block, dict) else getattr(block, "type", None)
        if kind in ("tool_use", "tool_result", "server_tool_use", "web_search_tool_result"):
            continue
        if kind == "text":
            text = block.get("text", "") if isinstance(block, dict) else getattr(block, "text", "") or ""
            if not text.startswith(MARKS):
                parts.append(text)
    return " ".join(p for p in parts if p)


def log(memory, role: str, text: str) -> None:
    text = " ".join((text or "").split())
    if not text or role not in ("user", "assistant"):
        return
    if role == "user" and text.startswith("(Buddy spoke up on its own"):
        return  # the note before a background message: the message itself is what's worth finding
    db = memory.db
    _ready(db)
    cur = db.execute("INSERT INTO convo_log (at, role, text) VALUES (?, ?, ?)",
                     (datetime.now(timezone.utc).isoformat(timespec="seconds"), role, text[:MAX_CHARS]))
    db.execute("INSERT INTO convo_fts (rowid, text) VALUES (?, ?)", (cur.lastrowid, text[:MAX_CHARS]))
    db.commit()


STOP = set("""a an the and or but if so to of in on at by for from with about as is are was were be been being
do does did done have has had i me my mine you your yours we us our he she it its they them their this that these
those what which who whom when where why how there here then than just also too very can could would should will
shall may might must not no yes ok okay please tell say said says told ask asked again earlier before ago remind
what's that's it's i'm you're don't didn't any some all more most get got give gave let lets like know think one
today yesterday tonight morning afternoon evening night week month last this next days day time
monday tuesday wednesday thursday friday saturday sunday""".split())


def _query(words: str) -> str:
    """Free text to an FTS5 query: any of the meaningful words, so 'weekly money report' finds 'your weekly
    report' (common words like 'what' or 'you' would match everything, so they're left out)."""
    terms = [t for t in re.findall(r"\w+", words.lower()) if len(t) > 1 and t not in STOP][:12]
    return " OR ".join(f'"{t}"' for t in terms)


WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
PARTS = {"morning": (5, 12), "afternoon": (12, 17), "evening": (17, 24), "night": (18, 29)}


def time_range(text: str, now: datetime | None = None) -> tuple[datetime, datetime] | None:
    """'yesterday', 'this morning', 'last night', 'on Tuesday', 'last week', '3 days ago'... as a time range
    (the user's time zone, returned in UTC); None when the words name no time."""
    from .tools import user_tz
    tz = user_tz()
    now = (now or datetime.now(timezone.utc)).astimezone(tz)
    t = text.lower()
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)

    def day(start: datetime, part: str = "") -> tuple[datetime, datetime]:
        if part in PARTS:
            a, b = PARTS[part]
            return start + timedelta(hours=a), start + timedelta(hours=b)
        return start, start + timedelta(days=1)

    part = next((p for p in PARTS if re.search(rf"\b{p}\b", t)), "")
    found = None
    if re.search(r"\blast night\b", t):
        found = day(midnight - timedelta(days=1), "night")
    elif re.search(r"\bday before yesterday\b", t):
        found = day(midnight - timedelta(days=2), part)
    elif re.search(r"\byesterday\b", t):
        found = day(midnight - timedelta(days=1), part)
    elif (m := re.search(r"\b(\d{1,2}) days? ago\b", t)):
        found = day(midnight - timedelta(days=int(m.group(1))), part)
    elif re.search(r"\b(today|this (morning|afternoon|evening)|tonight)\b", t):
        found = day(midnight, part or ("night" if "tonight" in t else ""))
    elif re.search(r"\blast week\b", t):
        monday = midnight - timedelta(days=now.weekday())
        found = (monday - timedelta(days=7), monday)
    elif re.search(r"\bthis week\b", t):
        found = (midnight - timedelta(days=now.weekday()), now + timedelta(minutes=1))
    elif re.search(r"\blast month\b", t):
        first = midnight.replace(day=1)
        found = ((first - timedelta(days=1)).replace(day=1), first)
    elif re.search(r"\bthis month\b", t):
        found = (midnight.replace(day=1), now + timedelta(minutes=1))
    elif (m := re.search(r"\b(" + "|".join(WEEKDAYS) + r")\b", t)):
        back = (now.weekday() - WEEKDAYS.index(m.group(1))) % 7  # the latest one: today, or up to 6 days ago
        found = day(midnight - timedelta(days=back), part)
    if not found:
        return None
    return found[0].astimezone(timezone.utc), min(found[1], now + timedelta(minutes=1)).astimezone(timezone.utc)


RECENCY_DAYS = 14  # a match this many days old counts half as much as the same match today


def search(memory, query: str, days: int = 7, limit: int = 6, around: int = 1,
           start: datetime | None = None, end: datetime | None = None) -> list[dict]:
    """The best matching lines, newer ones first when equally good, each with the line before and after it.
    start/end narrow it to a time ('yesterday'); otherwise the last `days` days."""
    db = memory.db
    _ready(db)
    now = datetime.now(timezone.utc)
    since = (start or now - timedelta(days=max(1, min(int(days or 7), KEEP_DAYS)))).isoformat()
    until = (end or now + timedelta(minutes=1)).isoformat()
    q = _query(query)
    if not q:  # no keywords: the latest things said
        rows = db.execute("SELECT id, at, role, text FROM convo_log WHERE at >= ? AND at < ? ORDER BY id DESC "
                          "LIMIT ?", (since, until, limit)).fetchall()
        return [{"id": r[0], "at": r[1], "role": r[2], "text": r[3], "hit": True} for r in rows]
    rows = db.execute(
        "SELECT l.id, l.at, bm25(convo_fts) FROM convo_fts JOIN convo_log l ON l.id = convo_fts.rowid "
        "WHERE convo_fts MATCH ? AND l.at >= ? AND l.at < ? ORDER BY bm25(convo_fts) LIMIT ?",
        (q, since, until, limit * 4)).fetchall()

    def score(row):  # bm25: lower is better; age pulls it toward 0
        age = max(0.0, (now - _time(row[1])).total_seconds() / 86400)
        return row[2] / (1 + age / RECENCY_DAYS)
    hits = [r[0] for r in sorted(rows, key=score)[:limit]]
    if not hits:
        return []
    wanted = {i + d for i in hits for d in range(-around, around + 1)}
    marks = ",".join("?" * len(wanted))
    near = db.execute(f"SELECT id, at, role, text FROM convo_log WHERE id IN ({marks}) ORDER BY id",
                      tuple(wanted)).fetchall()
    rank = {i: n for n, i in enumerate(hits, 1)}  # 1 = best match; 0 = a line around a match
    when = {r[0]: _time(r[1]) for r in near}

    def same_talk(i):  # a line around a match counts only from the same conversation (within 2 hours of it)
        return i in rank or any(h in when and abs((when[i] - when[h]).total_seconds()) <= 7200
                                for h in hits if abs(h - i) <= around)
    return [{"id": r[0], "at": r[1], "role": r[2], "text": r[3], "hit": r[0] in rank, "rank": rank.get(r[0], 0)}
            for r in near if same_talk(r[0])]


def _time(at: str) -> datetime:
    t = datetime.fromisoformat(at)
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def recent(memory, hours: float = 12, limit: int = 20) -> list[dict]:
    """The latest lines of the chat, oldest first, as Claude messages: to carry on after Buddy restarted."""
    db = memory.db
    _ready(db)
    since = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    rows = db.execute("SELECT role, text FROM convo_log WHERE at >= ? ORDER BY id DESC LIMIT ?",
                      (since, limit)).fetchall()[::-1]
    out = [{"role": role, "content": text} for role, text in rows]
    if out and out[0]["role"] == "assistant":  # it opens with something Buddy sent on its own
        out.insert(0, {"role": "user", "content": "(Buddy spoke up on its own; no question asked.)"})
    return out


def _format(found: list[dict]) -> list[str]:
    from .tools import user_tz
    lines, last = [], None
    for f in sorted(found, key=lambda f: f["id"]):
        if last is not None and f["id"] != last + 1:
            lines.append("  ...")
        last = f["id"]
        when = datetime.fromisoformat(f["at"]).astimezone(user_tz()).strftime("%a %d %b %I:%M %p")
        who = "User" if f["role"] == "user" else "You (Buddy)"
        lines.append(f"{'>' if f['hit'] else ' '} [{when}] {who}: {f['text'][:600 if f['hit'] else 300]}")
    return lines


def run_tool(memory, args: dict) -> str:
    query = args.get("query", "")
    span = time_range(f"{args.get('when', '')} {query}")
    found = search(memory, query, args.get("days") or 7, start=span[0] if span else None,
                   end=span[1] if span else None)
    if not found:
        return ("Nothing about that in the chats I kept (30 days"
                f"{', in that time' if span else ''}). Try other or related words once, else ask the user, or check "
                "Reports and list_jobs.")
    return ("Earlier in your chats (lines marked > matched; the others are what was said around them):\n"
            + "\n".join(_format(found)))


POINTS_BACK = re.compile(
    r"\b(earlier|yesterday|last (night|week|time|month)|the other day|\d+ days? ago|"
    r"you (said|told|sent|mentioned|suggested|recommended|promised|found|showed)|"
    r"we (talked|discussed|spoke|said|decided|agreed|planned)|i (said|told you|mentioned|asked you)|"
    r"(that|this|the) (report|message|plan|idea|link|place|restaurant|email|list|summary|reminder|thing) "
    r"(you|we|i)\b|(this|that) report|remind me what|what (was|were) (it|that|those|the)|"
    r"(on|last) (monday|tuesday|wednesday|thursday|friday|saturday|sunday)|this (morning|afternoon))",
    re.IGNORECASE)


def auto_context(memory, text: str, in_view: set[str] = frozenset(), limit: int = 3) -> str:
    """When the user's words point back ('you said', 'yesterday', 'that report'), the best earlier lines, found
    here on the server before Claude is asked: no extra round to Claude to search. '' when nothing points back,
    nothing matches, or what matches is already in view."""
    if not POINTS_BACK.search(text or "") or not _query(text):
        return ""
    span = time_range(text)
    found = search(memory, text, KEEP_DAYS, limit, 1, start=span[0] if span else None,
                   end=span[1] if span else None)
    if not [f for f in found if f["hit"] and " ".join(f["text"].split()) not in in_view]:
        return ""
    return (f"{AUTO_MARK}, for context; lines marked > matched; use recall_conversation to look further:\n"
            + "\n".join(_format(found)) + ")")


def topic(text: str, words: int = 8) -> str:
    """A few words that name what a message was about, for the 'earlier in this chat' line."""
    text = " ".join(text.split())
    if len(text.split()) < 3:
        return ""  # "ok", "thanks"
    cut = " ".join(text.split()[:words]).rstrip(",.;:!?")
    return cut + ("..." if len(text.split()) > words else "")


def forget(memory, text: str) -> int:
    """Delete chat lines containing this text (the user asked Buddy to forget it)."""
    text = (text or "").strip()
    if len(text) < 3:
        return 0
    db = memory.db
    _ready(db)
    rows = db.execute("SELECT id, text FROM convo_log WHERE text LIKE ? ESCAPE '\\'",
                      ("%" + text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%",)).fetchall()
    for rowid, said in rows:
        db.execute("INSERT INTO convo_fts (convo_fts, rowid, text) VALUES ('delete', ?, ?)", (rowid, said))
        db.execute("DELETE FROM convo_log WHERE id = ?", (rowid,))
    db.commit()
    return len(rows)


def prune(memory, days: int = KEEP_DAYS) -> int:
    db = memory.db
    _ready(db)
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    old = db.execute("SELECT id, text FROM convo_log WHERE at < ?", (cutoff,)).fetchall()
    for rowid, text in old:
        db.execute("INSERT INTO convo_fts (convo_fts, rowid, text) VALUES ('delete', ?, ?)", (rowid, text))
    db.execute("DELETE FROM convo_log WHERE at < ?", (cutoff,))
    db.commit()
    return len(old)
