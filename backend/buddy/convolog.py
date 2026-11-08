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


def text_of(message: dict) -> str:
    """The words of a plain message; '' for tool calls and tool results."""
    content = message.get("content")
    if isinstance(content, str):
        return content
    parts = []
    for block in content or []:
        kind = block.get("type") if isinstance(block, dict) else getattr(block, "type", None)
        if kind in ("tool_use", "tool_result", "server_tool_use", "web_search_tool_result"):
            continue
        if kind == "text":
            parts.append(block.get("text", "") if isinstance(block, dict) else getattr(block, "text", "") or "")
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


def _query(words: str) -> str:
    """Free text to an FTS5 query: any of the words, so 'weekly money report' finds 'your weekly report'."""
    terms = [t for t in re.findall(r"\w+", words.lower()) if len(t) > 1][:12]
    return " OR ".join(f'"{t}"' for t in terms)


RECENCY_DAYS = 14  # a match this many days old counts half as much as the same match today


def search(memory, query: str, days: int = 7, limit: int = 6, around: int = 1) -> list[dict]:
    """The best matching lines, newer ones first when equally good, each with the line before and after it."""
    db = memory.db
    _ready(db)
    now = datetime.now(timezone.utc)
    since = (now - timedelta(days=max(1, min(int(days or 7), KEEP_DAYS)))).isoformat()
    q = _query(query)
    if not q:  # no keywords: the latest things said
        rows = db.execute("SELECT id, at, role, text FROM convo_log WHERE at >= ? ORDER BY id DESC LIMIT ?",
                          (since, limit)).fetchall()
        return [{"id": r[0], "at": r[1], "role": r[2], "text": r[3], "hit": True} for r in rows]
    rows = db.execute(
        "SELECT l.id, l.at, bm25(convo_fts) FROM convo_fts JOIN convo_log l ON l.id = convo_fts.rowid "
        "WHERE convo_fts MATCH ? AND l.at >= ? ORDER BY bm25(convo_fts) LIMIT ?", (q, since, limit * 4)).fetchall()

    def score(row):  # bm25: lower is better; age pulls it toward 0
        at = datetime.fromisoformat(row[1])
        at = at if at.tzinfo else at.replace(tzinfo=timezone.utc)
        age = max(0.0, (now - at).total_seconds() / 86400)
        return row[2] / (1 + age / RECENCY_DAYS)
    hits = [r[0] for r in sorted(rows, key=score)[:limit]]
    if not hits:
        return []
    wanted = {i + d for i in hits for d in range(-around, around + 1)}
    marks = ",".join("?" * len(wanted))
    near = db.execute(f"SELECT id, at, role, text FROM convo_log WHERE id IN ({marks}) AND at >= ? ORDER BY id",
                      (*wanted, since)).fetchall()
    rank = {i: n for n, i in enumerate(hits, 1)}  # 1 = best match; 0 = a line around a match
    return [{"id": r[0], "at": r[1], "role": r[2], "text": r[3], "hit": r[0] in rank, "rank": rank.get(r[0], 0)}
            for r in near]


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


def run_tool(memory, args: dict) -> str:
    from .tools import user_tz
    found = search(memory, args.get("query", ""), args.get("days") or 7)
    if not found:
        return ("Nothing about that in the chats I kept (30 days). Try other or related words once, else ask the "
                "user, or check Reports and list_jobs.")
    lines, last = [], None
    for f in sorted(found, key=lambda f: f["id"]):
        if last is not None and f["id"] != last + 1:
            lines.append("  ...")
        last = f["id"]
        when = datetime.fromisoformat(f["at"]).astimezone(user_tz()).strftime("%a %d %b %I:%M %p")
        who = "User" if f["role"] == "user" else "You (Buddy)"
        lines.append(f"{'>' if f['hit'] else ' '} [{when}] {who}: {f['text'][:600 if f['hit'] else 300]}")
    return ("Earlier in your chats (lines marked > matched; the others are what was said around them):\n"
            + "\n".join(lines))


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
