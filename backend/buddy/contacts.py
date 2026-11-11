"""Who's calling: the owner's phone contacts (names and numbers only, synced by the app with the owner's permission),
and callers Buddy saved itself after an important call from a number that wasn't in the contacts.

Kept only on this server and never sent to free models or written to the logs. A sync replaces the whole contact
list (so a contact the owner deleted on the phone goes here too); saved callers stay until forgotten ("forget the
caller 201..."). Turning "Share contacts" off in the app deletes the synced list here (forget_all).

Lookups: by number (name_for: who's calling) and by name (find: "reply to Priya", "what's Priya's number").
"""

import re
from datetime import datetime, timezone

from .memory import Memory

SCHEMA = """
CREATE TABLE IF NOT EXISTS phone_contacts (number TEXT PRIMARY KEY, name TEXT NOT NULL, synced_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS saved_callers (number TEXT PRIMARY KEY, name TEXT NOT NULL, note TEXT NOT NULL DEFAULT '',
                                          saved_at TEXT NOT NULL);
"""
MAX_CONTACTS = 5000


def _ready(memory: Memory) -> None:
    memory.db.executescript(SCHEMA)


def key(number: str) -> str:
    """The last 10 digits: the same person whether written +1 (201) 555-0123 or 2015550123."""
    return re.sub(r"\D", "", number or "")[-10:]


def ingest(memory: Memory, body: dict) -> int:
    """The app's sync: {"contacts": [{"name": "Priya", "numbers": ["+1 201-555-0123", ...]}]}. Replaces the list."""
    _ready(memory)
    rows = {}
    for c in (body or {}).get("contacts", [])[:MAX_CONTACTS]:
        name = " ".join(str(c.get("name") or "").split())[:80]
        for n in c.get("numbers") or []:
            k = key(str(n))
            if name and len(k) >= 7:
                rows[k] = name
    now = datetime.now(timezone.utc).isoformat()
    memory.db.execute("DELETE FROM phone_contacts")
    memory.db.executemany("INSERT INTO phone_contacts (number, name, synced_at) VALUES (?, ?, ?)",
                          [(k, n, now) for k, n in rows.items()])
    memory.db.commit()
    return len(rows)


def name_for(memory: Memory, number: str) -> tuple[str, str]:
    """(name, "contact" | "saved") for a number, or ("", "")."""
    _ready(memory)
    k = key(number)
    if len(k) < 7:
        return "", ""
    row = memory.db.execute("SELECT name FROM phone_contacts WHERE number = ?", (k,)).fetchone()
    if row:
        return row["name"], "contact"
    row = memory.db.execute("SELECT name FROM saved_callers WHERE number = ?", (k,)).fetchone()
    return (row["name"], "saved") if row else ("", "")


def save_caller(memory: Memory, number: str, name: str, note: str = "") -> bool:
    """Remember an important caller who isn't in the contacts (not for contacts: their phone name wins)."""
    _ready(memory)
    k, name = key(number), " ".join(str(name or "").split())[:80]
    if len(k) < 7 or not name or name_for(memory, number)[1] == "contact":
        return False
    memory.db.execute("INSERT OR REPLACE INTO saved_callers (number, name, note, saved_at) VALUES (?, ?, ?, ?)",
                      (k, name, note[:200], datetime.now(timezone.utc).isoformat()))
    memory.db.commit()
    return True


def forget_caller(memory: Memory, number: str) -> bool:
    _ready(memory)
    n = memory.db.execute("DELETE FROM saved_callers WHERE number = ?", (key(number),)).rowcount
    memory.db.commit()
    return bool(n)


def count(memory: Memory) -> tuple[int, int]:
    _ready(memory)
    return (memory.db.execute("SELECT COUNT(*) FROM phone_contacts").fetchone()[0],
            memory.db.execute("SELECT COUNT(*) FROM saved_callers").fetchone()[0])


def find(memory: Memory, query: str, limit: int = 5) -> list[dict]:
    """Contacts matching a name or a number: [{"name", "numbers": [...], "how": "contact" | "saved"}], best first.
    A name matches exactly, then by first name, then as part of the name (case doesn't matter)."""
    _ready(memory)
    query = " ".join(str(query or "").split())[:80]
    if not query:
        return []
    if re.fullmatch(r"[\d\s()+.-]{7,}", query):  # a phone number
        name, how = name_for(memory, query)
        return [{"name": name, "numbers": numbers_for(memory, name, how), "how": how}] if name else []
    q = query.lower()
    rows = [(r["name"], r["number"], "contact") for r in memory.db.execute(
        "SELECT name, number FROM phone_contacts ORDER BY name")]
    rows += [(r["name"], r["number"], "saved") for r in memory.db.execute(
        "SELECT name, number FROM saved_callers ORDER BY name")]
    people: dict[tuple, list[str]] = {}
    for name, number, how in rows:
        people.setdefault((name, how), []).append(number)

    def rank(name: str) -> int:
        n = name.lower()
        if n == q:
            return 0
        if n.split()[0] == q or n.startswith(q + " "):
            return 1
        if all(w in n.split() for w in q.split()):
            return 2
        return 3 if q in n else 9
    hits = sorted(((rank(name), how != "contact", name, how) for name, how in people), key=lambda t: t[:3])
    return [{"name": name, "numbers": people[(name, how)], "how": how}
            for r, _, name, how in hits if r < 9][:limit]


def numbers_for(memory: Memory, name: str, how: str = "contact") -> list[str]:
    _ready(memory)
    table = "phone_contacts" if how == "contact" else "saved_callers"
    return [r["number"] for r in memory.db.execute(f"SELECT number FROM {table} WHERE name = ?", (name,))]


def forget_all(memory: Memory, saved_too: bool = False) -> int:
    """Delete the synced phone contacts (and Buddy's saved callers too, if asked). Returns how many numbers went."""
    _ready(memory)
    n = memory.db.execute("DELETE FROM phone_contacts").rowcount
    if saved_too:
        n += memory.db.execute("DELETE FROM saved_callers").rowcount
    memory.db.commit()
    return n
