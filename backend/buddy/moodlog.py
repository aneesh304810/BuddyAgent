"""The owner's mood and stress journal: how they feel, in their own words, kept on their server only.

Each entry: mood, stress and energy from 1 to 5 (1 low, 5 high), what was going on (fixed tags: work, family, sleep,
exercise, illness, travel, money, alcohol, social, alone time), and a short note of their own. Several a day are
fine; a day is the average of its entries. The days feed the personal model (healthmodel.py) as factors: a stressful
day, then the next morning. No AI model is given any of it, the notes least of all (the Privacy Gateway's health
rule); kept as long as the other health readings (two years), and deleted entry by entry or all at once.
"""

import json
import re
from datetime import datetime, timedelta, timezone

from .memory import Memory
from .tools import ToolError, local_now

TAGS = ("work", "family", "sleep", "exercise", "illness", "travel", "money", "alcohol", "social", "alone time")
SCALES = ("mood", "stress", "energy")
SCHEMA = """
CREATE TABLE IF NOT EXISTS mood_log (             -- the owner's own entries; never given to an AI model
    id INTEGER PRIMARY KEY, day TEXT NOT NULL, at TEXT NOT NULL, mood INTEGER, stress INTEGER, energy INTEGER,
    tags TEXT NOT NULL DEFAULT '[]', note TEXT NOT NULL DEFAULT ''
);
"""


def _ready(memory: Memory) -> None:
    memory.db.executescript(SCHEMA)


def _scale(v) -> int | None:
    try:
        n = int(v)
    except (TypeError, ValueError):
        return None
    return n if 1 <= n <= 5 else None


def add(memory: Memory, mood=None, stress=None, energy=None, tags=None, note: str = "", day: str = "") -> dict:
    values = {"mood": _scale(mood), "stress": _scale(stress), "energy": _scale(energy)}
    if all(v is None for v in values.values()):
        raise ToolError("Pick at least one of mood, stress or energy (1 to 5).")
    day = day if re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(day or "")) else local_now().date().isoformat()
    if day > local_now().date().isoformat():
        raise ToolError("That day hasn't come yet.")
    kept_tags = [t for t in (tags or []) if t in TAGS][:len(TAGS)]
    _ready(memory)
    cur = memory.db.execute("INSERT INTO mood_log (day, at, mood, stress, energy, tags, note) VALUES (?, ?, ?, ?, ?, ?, ?)",
                            (day, datetime.now(timezone.utc).isoformat(timespec="seconds"), values["mood"],
                             values["stress"], values["energy"], json.dumps(kept_tags),
                             " ".join(str(note or "").split())[:500]))
    memory.db.commit()
    prune(memory)
    return {"id": int(cur.lastrowid), "day": day, **values, "tags": kept_tags}


def entries(memory: Memory, days: int = 30) -> list[dict]:
    _ready(memory)
    since = (local_now().date() - timedelta(days=days - 1)).isoformat()
    rows = [dict(r) for r in memory.db.execute("SELECT * FROM mood_log WHERE day >= ? ORDER BY day DESC, id DESC",
                                               (since,))]
    for r in rows:
        r["tags"] = json.loads(r["tags"] or "[]")
    return rows


def daily(memory: Memory, days: int = 730) -> dict[str, dict]:
    """day -> the day's average mood, stress and energy (for the page and the personal model)."""
    _ready(memory)
    since = (local_now().date() - timedelta(days=days - 1)).isoformat()
    out = {}
    for r in memory.db.execute("SELECT day, AVG(mood) AS mood, AVG(stress) AS stress, AVG(energy) AS energy, "
                               "COUNT(*) AS n FROM mood_log WHERE day >= ? GROUP BY day", (since,)):
        out[r["day"]] = {k: (round(r[k], 2) if r[k] is not None else None) for k in SCALES} | {"entries": r["n"]}
    return out


def view(memory: Memory, days: int = 30) -> dict:
    got = entries(memory, days)
    by_day = daily(memory, days)

    def avg(k, n):
        recent = [v[k] for d, v in by_day.items() if v.get(k) is not None
                  and d >= (local_now().date() - timedelta(days=n - 1)).isoformat()]
        return round(sum(recent) / len(recent), 1) if recent else None
    tag_count: dict[str, int] = {}
    for e in got:
        for t in e["tags"]:
            tag_count[t] = tag_count.get(t, 0) + 1
    return {"entries": got[:60], "days": [{"day": d, **v} for d, v in sorted(by_day.items(), reverse=True)],
            "week": {k: avg(k, 7) for k in SCALES}, "month": {k: avg(k, 30) for k in SCALES},
            "tags": sorted(tag_count.items(), key=lambda x: -x[1]), "choices": list(TAGS),
            "today": local_now().date().isoformat(),
            "note": "Kept on your server only; no AI model is given any of it. Not a diagnosis: if you feel low for "
                    "a long time, or unsafe, talk to a doctor or call or text 988 (the US Suicide & Crisis Lifeline)."}


def delete(memory: Memory, entry_id: int) -> str:
    _ready(memory)
    n = memory.db.execute("DELETE FROM mood_log WHERE id = ?", (int(entry_id),)).rowcount
    memory.db.commit()
    return "Deleted." if n else "That entry wasn't found."


def forget(memory: Memory) -> str:
    _ready(memory)
    n = memory.db.execute("DELETE FROM mood_log").rowcount
    memory.db.commit()
    return f"Deleted all {n} entries."


def prune(memory: Memory) -> int:
    from .healthrecords import keep_days  # noqa: PLC0415  as long as the other health readings
    _ready(memory)
    n = memory.db.execute("DELETE FROM mood_log WHERE day < ?",
                          ((local_now().date() - timedelta(days=keep_days())).isoformat(),)).rowcount
    memory.db.commit()
    return n
