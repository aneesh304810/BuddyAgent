"""Organiser: the user's to-do lists, plus the evening wrap-up.

Tasks live in lists ("today", "shopping", "work"...), can have a due date and a
priority, and are ticked off by voice. The morning briefing mentions what's due;
the evening wrap-up (settings.evening_wrap_at) goes over what got done, what's
still open and what's tomorrow.
"""

import json
from datetime import date, datetime, timedelta, timezone

from .memory import Memory
from .tools import ToolError, local_now, user_tz

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY,
    text TEXT NOT NULL,
    list TEXT NOT NULL DEFAULT 'inbox',
    priority TEXT NOT NULL DEFAULT 'normal',   -- high | normal | low
    due TEXT,                                  -- local date YYYY-MM-DD, or NULL
    created_at TEXT NOT NULL,
    done_at TEXT                               -- UTC ISO when ticked off
);
"""


def _due_date(value: str | None) -> str | None:
    if not value:
        return None
    text = value.strip().lower()
    today = local_now().date()
    if text == "today":
        return today.isoformat()
    if text == "tomorrow":
        return (today + timedelta(days=1)).isoformat()
    try:
        return date.fromisoformat(text[:10]).isoformat()
    except ValueError as e:
        raise ToolError(f"Could not read the date {value!r}; use YYYY-MM-DD, today or tomorrow.") from e


class Organiser:
    def __init__(self, memory: Memory):
        self.memory = memory
        memory.db.executescript(SCHEMA_SQL)

    def add(self, text: str, list_name: str = "inbox", due: str | None = None, priority: str = "normal") -> dict:
        text = text.strip()
        if not text:
            raise ToolError("What's the task?")
        if priority not in ("high", "normal", "low"):
            priority = "normal"
        row = (text, (list_name or "inbox").strip().lower(), priority, _due_date(due),
               datetime.now(timezone.utc).isoformat())
        cur = self.memory.db.execute(
            "INSERT INTO tasks (text, list, priority, due, created_at) VALUES (?, ?, ?, ?, ?)", row)
        self.memory.db.commit()
        return self.get(cur.lastrowid)

    def get(self, task_id: int) -> dict:
        row = self.memory.db.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if not row:
            raise ToolError(f"No task {task_id}.")
        return dict(row)

    def open(self, list_name: str = "", due_by: str | None = None) -> list[dict]:
        sql, args = "SELECT * FROM tasks WHERE done_at IS NULL", []
        if list_name:
            sql += " AND list = ?"
            args.append(list_name.strip().lower())
        if due_by:
            sql += " AND due IS NOT NULL AND due <= ?"
            args.append(due_by)
        sql += (" ORDER BY CASE priority WHEN 'high' THEN 0 WHEN 'normal' THEN 1 ELSE 2 END,"
                " due IS NULL, due, id")
        return [dict(r) for r in self.memory.db.execute(sql, args)]

    def find(self, what: str) -> dict:
        """An open task by id, or by words in it (must be unambiguous)."""
        if str(what).strip().isdigit():
            return self.get(int(what))
        words = str(what).strip().lower()
        matches = [t for t in self.open() if words in t["text"].lower()]
        if len(matches) == 1:
            return matches[0]
        if not matches:
            raise ToolError(f"No open task matches '{what}'.")
        raise ToolError("Which one? " + "; ".join(f"{t['id']}: {t['text']}" for t in matches[:8]))

    def complete(self, what: str) -> dict:
        task = self.find(what)
        self.memory.db.execute("UPDATE tasks SET done_at = ? WHERE id = ?",
                               (datetime.now(timezone.utc).isoformat(), task["id"]))
        self.memory.db.commit()
        return task

    def delete(self, what: str) -> dict:
        task = self.find(what)
        self.memory.db.execute("DELETE FROM tasks WHERE id = ?", (task["id"],))
        self.memory.db.commit()
        return task

    def lists(self) -> dict[str, int]:
        rows = self.memory.db.execute(
            "SELECT list, COUNT(*) AS n FROM tasks WHERE done_at IS NULL GROUP BY list ORDER BY list")
        return {r["list"]: r["n"] for r in rows}

    def done_today(self) -> list[dict]:
        start = datetime.combine(local_now().date(), datetime.min.time(), user_tz()).astimezone(timezone.utc)
        rows = self.memory.db.execute("SELECT * FROM tasks WHERE done_at >= ? ORDER BY done_at",
                                      (start.isoformat(),))
        return [dict(r) for r in rows]

    # --- for the briefings -------------------------------------------------
    def morning_line(self) -> str:
        today = local_now().date().isoformat()
        due = self.open(due_by=today)
        overdue = [t for t in due if t["due"] < today]
        high = [t for t in self.open() if t["priority"] == "high" and t not in due]
        if not due and not high:
            return "nothing due"
        parts = [t["text"] for t in due[:5]]
        line = f"{len(due)} due today" + (f" ({len(overdue)} overdue)" if overdue else "")
        if parts:
            line += ": " + "; ".join(parts)
        if high:
            line += ". Also important: " + "; ".join(t["text"] for t in high[:3])
        return line

    def wrap_up_facts(self) -> str:
        tomorrow = (local_now().date() + timedelta(days=1)).isoformat()
        done = [t["text"] for t in self.done_today()]
        still = [t["text"] for t in self.open(due_by=local_now().date().isoformat())]
        upcoming = [t["text"] for t in self.open() if t["due"] == tomorrow]
        return (f"Done today: {'; '.join(done) or 'nothing ticked off'}. "
                f"Still open from today or earlier: {'; '.join(still) or 'none'}. "
                f"Due tomorrow: {'; '.join(upcoming) or 'nothing'}.")


TASK_TOOLS = [
    {
        "name": "add_task",
        "description": "Add a to-do. `list` groups tasks (inbox, shopping, work, home...; default inbox). "
        "`due`: YYYY-MM-DD, 'today' or 'tomorrow'. For something at a specific time, also set a reminder.",
        "input_schema": {
            "type": "object",
            "properties": {
                "text": {"type": "string"},
                "list": {"type": "string"},
                "due": {"type": "string"},
                "priority": {"type": "string", "enum": ["high", "normal", "low"]},
            },
            "required": ["text"],
            "additionalProperties": False,
        },
    },
    {
        "name": "list_tasks",
        "description": "Open to-dos, most important first, with ids. Filter by `list`, or `due_by` "
        "(YYYY-MM-DD, 'today', 'tomorrow'). Without filters it also returns how many are in each list.",
        "input_schema": {
            "type": "object",
            "properties": {"list": {"type": "string"}, "due_by": {"type": "string"}},
            "additionalProperties": False,
        },
    },
    {
        "name": "complete_task",
        "description": "Tick off a task, by id or by words in it ('milk'). Say what was ticked off.",
        "input_schema": {
            "type": "object",
            "properties": {"task": {"type": "string"}},
            "required": ["task"],
            "additionalProperties": False,
        },
    },
    {
        "name": "delete_task",
        "description": "Remove a task that's no longer needed (not done), by id or words.",
        "input_schema": {
            "type": "object",
            "properties": {"task": {"type": "string"}},
            "required": ["task"],
            "additionalProperties": False,
        },
    },
]
TASK_TOOL_NAMES = {t["name"] for t in TASK_TOOLS}


def _short(t: dict) -> dict:
    return {k: t[k] for k in ("id", "text", "list", "priority", "due") if t.get(k) is not None}


def run_task_tool(organiser: Organiser, name: str, args: dict) -> str:
    if name == "add_task":
        t = organiser.add(args["text"], args.get("list", "inbox"), args.get("due"), args.get("priority", "normal"))
        return f"Added task {t['id']} to {t['list']}" + (f", due {t['due']}." if t["due"] else ".")
    if name == "list_tasks":
        tasks = organiser.open(args.get("list", ""), _due_date(args.get("due_by")))
        result = {"tasks": [_short(t) for t in tasks[:50]]}
        if not args:
            result["lists"] = organiser.lists()
        return json.dumps(result) if tasks else "No open tasks there."
    if name == "complete_task":
        return f"Ticked off: {organiser.complete(args['task'])['text']}."
    if name == "delete_task":
        return f"Removed: {organiser.delete(args['task'])['text']}."
    raise ToolError(f"Unknown tool {name}")
