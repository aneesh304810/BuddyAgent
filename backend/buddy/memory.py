"""Long-term memory: facts about the user, reminders, and goals, stored in SQLite."""

from datetime import datetime, timezone
from pathlib import Path

from .db import connect

SCHEMA = """
CREATE TABLE IF NOT EXISTS facts (
    id INTEGER PRIMARY KEY,
    text TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS reminders (
    id INTEGER PRIMARY KEY,
    text TEXT NOT NULL,
    due_at TEXT NOT NULL,          -- ISO-8601, UTC
    delivered INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS goals (
    id INTEGER PRIMARY KEY,
    text TEXT NOT NULL,
    done INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audit (
    id INTEGER PRIMARY KEY,
    ts TEXT NOT NULL,
    agent TEXT NOT NULL,
    tool TEXT NOT NULL,
    args TEXT NOT NULL,             -- masked
    status TEXT NOT NULL,           -- ok | error | blocked | approval_requested | approved
    detail TEXT
);
CREATE TABLE IF NOT EXISTS usage (
    day TEXT NOT NULL,              -- YYYY-MM-DD, UTC
    model TEXT NOT NULL,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    cache_read_tokens INTEGER NOT NULL DEFAULT 0,
    cache_write_tokens INTEGER NOT NULL DEFAULT 0,
    cost_usd REAL NOT NULL DEFAULT 0,
    PRIMARY KEY (day, model)
);
CREATE TABLE IF NOT EXISTS kv (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Memory:
    def __init__(self, path: Path | str):
        self.db = connect(path)
        self.db.executescript(SCHEMA)

    # --- facts -----------------------------------------------------------
    def remember(self, text: str) -> int:
        cur = self.db.execute(
            "INSERT INTO facts (text, created_at) VALUES (?, ?)", (text, _now())
        )
        self.db.commit()
        return cur.lastrowid

    def recall(self, query: str = "", limit: int = 20) -> list[str]:
        """Return facts matching any word in the query (all facts if empty)."""
        words = [w for w in query.lower().split() if len(w) > 2]
        if not words:
            rows = self.db.execute(
                "SELECT text FROM facts ORDER BY id DESC LIMIT ?", (limit,)
            )
        else:
            clause = " OR ".join("lower(text) LIKE ?" for _ in words)
            rows = self.db.execute(
                f"SELECT text FROM facts WHERE {clause} ORDER BY id DESC LIMIT ?",
                [f"%{w}%" for w in words] + [limit],
            )
        return [r["text"] for r in rows]

    def forget(self, query: str) -> int:
        cur = self.db.execute(
            "DELETE FROM facts WHERE lower(text) LIKE ?", (f"%{query.lower()}%",)
        )
        self.db.commit()
        return cur.rowcount

    # --- reminders -------------------------------------------------------
    def add_reminder(self, text: str, due_at: datetime) -> int:
        due_utc = due_at.astimezone(timezone.utc).isoformat()
        cur = self.db.execute(
            "INSERT INTO reminders (text, due_at) VALUES (?, ?)", (text, due_utc)
        )
        self.db.commit()
        return cur.lastrowid

    def upcoming_reminders(self) -> list[dict]:
        rows = self.db.execute(
            "SELECT id, text, due_at FROM reminders WHERE delivered = 0 ORDER BY due_at"
        )
        return [dict(r) for r in rows]

    def due_reminders(self, now: datetime | None = None) -> list[dict]:
        now_iso = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat()
        rows = self.db.execute(
            "SELECT id, text, due_at FROM reminders WHERE delivered = 0 AND due_at <= ? "
            "ORDER BY due_at",
            (now_iso,),
        )
        return [dict(r) for r in rows]

    def mark_delivered(self, reminder_id: int) -> None:
        self.db.execute("UPDATE reminders SET delivered = 1 WHERE id = ?", (reminder_id,))
        self.db.commit()

    def cancel_reminder(self, reminder_id: int) -> bool:
        cur = self.db.execute(
            "DELETE FROM reminders WHERE id = ? AND delivered = 0", (reminder_id,)
        )
        self.db.commit()
        return cur.rowcount > 0

    # --- goals -----------------------------------------------------------
    def add_goal(self, text: str) -> int:
        cur = self.db.execute(
            "INSERT INTO goals (text, created_at) VALUES (?, ?)", (text, _now())
        )
        self.db.commit()
        return cur.lastrowid

    def open_goals(self) -> list[dict]:
        rows = self.db.execute("SELECT id, text FROM goals WHERE done = 0 ORDER BY id")
        return [dict(r) for r in rows]

    def complete_goal(self, goal_id: int) -> bool:
        cur = self.db.execute("UPDATE goals SET done = 1 WHERE id = ?", (goal_id,))
        self.db.commit()
        return cur.rowcount > 0

    # --- small key/value state (e.g. last briefing date) -----------------
    def get(self, key: str) -> str | None:
        row = self.db.execute("SELECT value FROM kv WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None

    def set(self, key: str, value: str) -> None:
        self.db.execute(
            "INSERT INTO kv (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        self.db.commit()
