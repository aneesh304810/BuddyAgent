"""Database connection: encrypted with SQLCipher whenever BUDDY_DB_KEY is set."""

import logging
import os
import sqlite3
from pathlib import Path

log = logging.getLogger(__name__)


def connect(path: Path | str):
    key = os.getenv("BUDDY_DB_KEY", "")
    if not key:
        if str(path) != ":memory:":
            log.warning("BUDDY_DB_KEY is not set: the database is NOT encrypted (dev only)")
        conn = sqlite3.connect(str(path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    import sqlcipher3  # only needed when encryption is on

    conn = sqlcipher3.connect(str(path), check_same_thread=False)
    # The key is a random hex string from setup; pass it as a raw key, not a passphrase.
    if not all(c in "0123456789abcdefABCDEF" for c in key) or len(key) != 64:
        raise ValueError("BUDDY_DB_KEY must be 64 hex characters (openssl rand -hex 32)")
    conn.execute(f"PRAGMA key = \"x'{key}'\"")
    conn.execute("SELECT count(*) FROM sqlite_master")  # fails fast on a wrong key
    conn.row_factory = sqlcipher3.Row
    return conn
