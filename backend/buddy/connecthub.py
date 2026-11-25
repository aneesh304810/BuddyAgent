"""Buddy Connect's connector hub: what every connector shares beyond its calls. Its sync history, two more states,
the terms its data comes under, and syncing, deleting or disconnecting it on its own.

  sync history   each time a source brought the owner's data in (the Oura Ring, the phone's Health Connect, and
                 later lab results and other devices): when, what kind of sync (the first one, which reads the
                 history; a later one, which reads what's new; one the phone sent; one the owner asked for), how
                 many records came and how many were new, how far it reached, and the error if it failed. The last
                 50 per source, a year at most. No health value is in it: counts, dates and errors only.
  expired        the service no longer accepts Buddy's sign-in (a refresh refused): the owner connects it again.
                 Set by the source's own code; cleared when the sign-in works again.
  needs approval a service that opens its data only to developers it has approved (catalog "approval" says what
                 is needed), while it isn't set up. Never shown as connected.
  terms          what a source's data may be used for, where Buddy knows it (catalog "terms"); "" when unknown.
  sources        the connectors that bring the owner's own data in (SOURCES), each with its module's sync, delete
                 and disconnect. Deleting takes out what the source gave Buddy, its normalized health records and
                 its sync history; disconnecting also stops more coming.

A source module implements, as it can: sync_now(memory) -> str, forget(memory), disconnect(memory) -> str.
"""

import importlib
import json
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from .memory import Memory

KEEP_ROWS, KEEP_DAYS = 50, 365
KINDS = ("initial", "incremental", "phone", "manual", "upload")
EXPIRED_PREFIX = "connect:expired:"
SOURCES = {  # connector -> the module that brings its data in, and what it can do
    "oura": {"module": "oura", "group": "Health", "sync": True, "disconnect": True,
             "stop": "Disconnect here; to remove Buddy's access at Oura too, remove it under the Oura app's "
                     "authorized apps."},
    "health_connect": {"module": "health_data", "group": "Health", "sync": False, "disconnect": False,
                       "stop": "The phone sends this. To stop it, turn off health data in the Buddy app's settings, "
                               "or remove Buddy's access in Health Connect's own settings.",
                       "fetch": "The phone sends this: open the Buddy app to send the latest."},
    "lab_upload": {"module": "labs", "group": "Health", "sync": False, "disconnect": False,
                   "stop": "Nothing comes in unless you upload a report.",
                   "fetch": "Upload a report on the Health page's Labs tab."},
}
SCHEMA = """
CREATE TABLE IF NOT EXISTS connect_syncs (       -- each sync of a source of the owner's data: counts, never values
    id INTEGER PRIMARY KEY, connector TEXT NOT NULL, kind TEXT NOT NULL, started_at TEXT NOT NULL,
    finished_at TEXT, ok INTEGER, records INTEGER NOT NULL DEFAULT 0, new_records INTEGER NOT NULL DEFAULT 0,
    reached TEXT NOT NULL DEFAULT '', error TEXT NOT NULL DEFAULT ''
);
"""


def _ready(memory: Memory) -> None:
    memory.db.executescript(SCHEMA)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def start(memory: Memory, connector: str, kind: str) -> int:
    _ready(memory)
    cur = memory.db.execute("INSERT INTO connect_syncs (connector, kind, started_at) VALUES (?, ?, ?)",
                            (connector, kind if kind in KINDS else "manual", _now()))
    memory.db.commit()
    return int(cur.lastrowid)


def finish(memory: Memory, sync_id: int, ok: bool, records: int = 0, new: int = 0, reached: str = "",
           error: str = "") -> None:
    memory.db.execute("UPDATE connect_syncs SET finished_at = ?, ok = ?, records = ?, new_records = ?, reached = ?, "
                      "error = ? WHERE id = ?", (_now(), int(ok), int(records), int(new), str(reached)[:40],
                                                  str(error)[:300], sync_id))
    row = memory.db.execute("SELECT connector FROM connect_syncs WHERE id = ?", (sync_id,)).fetchone()
    if row:  # the last KEEP_ROWS per source, none older than KEEP_DAYS
        memory.db.execute("DELETE FROM connect_syncs WHERE connector = ? AND (id NOT IN (SELECT id FROM connect_syncs "
                          "WHERE connector = ? ORDER BY id DESC LIMIT ?) OR started_at < ?)",
                          (row["connector"], row["connector"], KEEP_ROWS,
                           (datetime.now(timezone.utc) - timedelta(days=KEEP_DAYS)).isoformat()))
    memory.db.commit()


@contextmanager
def syncing(memory: Memory, connector: str, kind: str):
    """Record one sync: fill the yielded dict's records, new and reached; a failure is kept with its error."""
    got = {"id": start(memory, connector, kind), "records": 0, "new": 0, "reached": ""}
    try:
        yield got
    except Exception as e:
        finish(memory, got["id"], False, got["records"], got["new"], got["reached"], f"{type(e).__name__}: {e}")
        raise
    finish(memory, got["id"], True, got["records"], got["new"], got["reached"])


def history(memory: Memory, connector: str, n: int = 10) -> list[dict]:
    _ready(memory)
    return [dict(r) for r in memory.db.execute(
        "SELECT id, kind, started_at, finished_at, ok, records, new_records, reached, error FROM connect_syncs "
        "WHERE connector = ? ORDER BY id DESC LIMIT ?", (connector, n))]


def last(memory: Memory, connector: str) -> dict:
    """The newest finished sync, and the newest one that worked."""
    rows = [r for r in history(memory, connector, KEEP_ROWS) if r["finished_at"]]
    good = next((r for r in rows if r["ok"]), None)
    return {"last": rows[0] if rows else None, "last_ok": good}


def set_expired(memory: Memory, connector: str, why: str = "") -> None:
    """The service refused Buddy's sign-in ("" clears it, once it works again)."""
    memory.set(EXPIRED_PREFIX + connector, json.dumps({"why": why[:200], "at": _now()}) if why else "")


def expired(memory: Memory, connector: str) -> str:
    """Why the sign-in expired, or "" while it works."""
    try:
        return (json.loads(memory.get(EXPIRED_PREFIX + connector) or "{}") or {}).get("why", "")
    except ValueError:
        return ""


def _module(connector: str):
    return importlib.import_module(f"buddy.{SOURCES[connector]['module']}")


def _known(connector: str) -> None:
    if connector not in SOURCES:
        raise KeyError(connector)


def sync_now(memory: Memory, connector: str) -> str:
    """The owner asked: sync this source now (only those Buddy reads itself; the phone sends its own)."""
    _known(connector)
    if not SOURCES[connector]["sync"]:
        return SOURCES[connector]["fetch"]
    return _module(connector).sync_now(memory)


def forget(memory: Memory, connector: str) -> str:
    """Delete everything this source gave Buddy: its own data, its health records and its sync history."""
    _known(connector)
    from . import healthrecords  # noqa: PLC0415
    _module(connector).forget(memory)
    n = healthrecords.forget(memory, connector)
    _ready(memory)
    memory.db.execute("DELETE FROM connect_syncs WHERE connector = ?", (connector,))
    memory.db.commit()
    from .gateway import CATALOG  # noqa: PLC0415
    title = (CATALOG.get(connector) or {}).get("title", connector)
    return f"Deleted what {title} gave Buddy ({n} health records and its sync history)."


def disconnect(memory: Memory, connector: str) -> str:
    """Stop the source sending more, then delete what it gave (the owner confirms on the page first)."""
    _known(connector)
    if not SOURCES[connector]["disconnect"]:
        return SOURCES[connector]["stop"]
    said = _module(connector).disconnect(memory)
    set_expired(memory, connector, "")
    return said + " " + forget(memory, connector)
