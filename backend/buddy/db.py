"""Database connection: encrypted with SQLCipher whenever BUDDY_DB_KEY is set."""

import logging
import os
import re
import threading
import sqlite3
from pathlib import Path

log = logging.getLogger(__name__)


_SCRIPT_LOCK = threading.RLock()
_SCHEMA_ONLY = re.compile(r"(?is)^(\s*(--[^\n]*\n\s*)*(create\s+(table|index|unique\s+index)\s+if\s+not\s+exists\s"
                          r"[^;]*;\s*(--[^\n]*)?)+)\s*$")


class Done:
    """A statement that has finished: its rows are already read, so nothing is left open on the shared connection
    for another thread to trip over. It answers what callers use on a cursor: looping over it, fetchone, fetchall,
    fetchmany, rowcount, lastrowid and description."""
    __slots__ = ("_rows", "_at", "rowcount", "lastrowid", "description")

    def __init__(self, cursor):
        self.description = cursor.description
        self._rows = cursor.fetchall() if cursor.description is not None else []
        self._at = 0
        self.rowcount, self.lastrowid = cursor.rowcount, cursor.lastrowid

    def __iter__(self):
        return self

    def __next__(self):
        if self._at >= len(self._rows):
            raise StopIteration
        self._at += 1
        return self._rows[self._at - 1]

    def fetchone(self):
        return next(self, None)

    def fetchall(self):
        rest, self._at = self._rows[self._at:], len(self._rows)
        return rest

    def fetchmany(self, size: int = 1):
        rest = self._rows[self._at:self._at + size]
        self._at += len(rest)
        return rest

    def close(self):
        pass


def _safe(base):
    """The connection class Buddy uses. One connection is shared by the server's threads (the chat, the background
    loop, work handed to worker threads), and SQLite's Python driver is not safe for two threads to use one
    connection at the same moment: it surfaced as rare "bad parameter or other API misuse" and "cannot commit:
    SQL statements in progress" errors. So every statement and every commit runs under one lock, and a statement's
    rows are read to the end before the lock is released (Done), leaving nothing half-finished on the connection.
    A script of only "CREATE ... IF NOT EXISTS" statements also runs once per connection, not every time a module
    starts up."""
    class SafeConnection(base):
        def execute(self, sql, parameters=()):
            with _SCRIPT_LOCK:
                return Done(super().execute(sql, parameters))

        def executemany(self, sql, parameters):
            with _SCRIPT_LOCK:
                return Done(super().executemany(sql, parameters))

        def commit(self):
            with _SCRIPT_LOCK:
                return super().commit()

        def rollback(self):
            with _SCRIPT_LOCK:
                return super().rollback()

        def executescript(self, script):
            with _SCRIPT_LOCK:
                schema = bool(_SCHEMA_ONLY.match(script or ""))
                done = self.__dict__.setdefault("_schemas_done", set()) if hasattr(self, "__dict__") else set()
                key = hash(script)
                if schema and key in done:
                    return None
                out = super().executescript(script)
                if schema:
                    done.add(key)
                return out
    return SafeConnection


def connect(path: Path | str):
    key = os.getenv("BUDDY_DB_KEY", "")
    if not key:
        if str(path) != ":memory:":
            log.warning("BUDDY_DB_KEY is not set: the database is NOT encrypted (dev only)")
        conn = sqlite3.connect(str(path), check_same_thread=False, factory=_safe(sqlite3.Connection))
        conn.execute("PRAGMA secure_delete = ON")  # deleted data is overwritten, not just marked free
        conn.row_factory = sqlite3.Row
        return conn

    import sqlcipher3  # only needed when encryption is on

    conn = sqlcipher3.connect(str(path), check_same_thread=False, factory=_safe(sqlcipher3.Connection))
    # The key is a random hex string from setup; pass it as a raw key, not a passphrase.
    if not all(c in "0123456789abcdefABCDEF" for c in key) or len(key) != 64:
        raise ValueError("BUDDY_DB_KEY must be 64 hex characters (openssl rand -hex 32)")
    conn.execute(f"PRAGMA key = \"x'{key}'\"")
    conn.execute("SELECT count(*) FROM sqlite_master")  # fails fast on a wrong key
    conn.execute("PRAGMA secure_delete = ON")  # deleted data is overwritten, not just marked free
    conn.row_factory = sqlcipher3.Row
    return conn
