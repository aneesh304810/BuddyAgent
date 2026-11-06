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


def _safe(base):
    """The connection class Buddy uses: executescript (which commits and runs several statements) is held under one
    lock, so two threads never interleave on the shared connection; and a script of only "CREATE ... IF NOT EXISTS"
    statements runs once per connection, not every time a module starts up."""
    class SafeConnection(base):
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
