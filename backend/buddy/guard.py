"""Server security controls: the access log, the brute-force lockout and the security headers.

Access log: every call to Buddy's API, admin pages, bank pages and app connections, with when,
who (the tailnet login Tailscale passes along, when there is one), the network address, the
method, the path (never the query string, so tokens and search words stay out), the result and
how long it took. Never a message, a body or a key. Kept ACCESS_LOG_DAYS (400) days, in its own
database file, encrypted with the same key as Buddy's memory.

Lockout: LOCKOUT_FAILURES failed sign-ins (a wrong or missing token) from one address within
LOCKOUT_WINDOW seconds block that address for LOCKOUT_SECONDS; each lockout is logged.
"""

import logging
import os
import re
import threading
import time
from collections import deque
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import db
from .config import settings

log = logging.getLogger(__name__)

ACCESS_LOG_DAYS = 400
LOCKOUT_FAILURES = int(os.getenv("BUDDY_LOCKOUT_FAILURES", "10"))
LOCKOUT_WINDOW = 15 * 60
LOCKOUT_SECONDS = 15 * 60
LOGGED_PREFIXES = ("/api/", "/admin/", "/plaid/", "/ws")
SCHEMA = """CREATE TABLE IF NOT EXISTS access_log (
    id INTEGER PRIMARY KEY, at TEXT NOT NULL, kind TEXT NOT NULL, ip TEXT, user TEXT, method TEXT,
    path TEXT, status INTEGER, ms INTEGER);
CREATE INDEX IF NOT EXISTS access_log_at ON access_log(at);"""

# Content Security Policy for Buddy's own pages: only Buddy's scripts run, nothing can frame them.
# The bank-connection pages (/plaid/...) carry their own policy for Plaid's script.
CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
       "img-src 'self' data: blob: https:; media-src 'self' blob: data:; connect-src 'self' {ws}; "
       "font-src 'self' data:; object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'")
HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
    "Strict-Transport-Security": "max-age=31536000",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Permissions-Policy": "camera=(), geolocation=(), payment=(), usb=(), microphone=(self)",
}
HOST = re.compile(r"[A-Za-z0-9.-]{1,253}(:\d{1,5})?")


def security_headers(path: str, host: str, existing) -> dict:
    """The headers to add to a response (those it already has are kept)."""
    out = {k: v for k, v in HEADERS.items() if k.lower() not in existing}
    if not path.startswith("/plaid/") and "content-security-policy" not in existing:
        ws = f"wss://{host} ws://{host}" if HOST.fullmatch(host or "") else ""
        out["Content-Security-Policy"] = CSP.format(ws=ws).replace("'self' ;", "'self';")
    if path.startswith(("/api/", "/admin/")) and "cache-control" not in existing:
        out["Cache-Control"] = "no-store"
    return out


def client(peer: str | None, headers) -> tuple[str, str]:
    """The caller's address and tailnet login. Buddy listens only on 127.0.0.1 behind `tailscale serve`,
    which adds X-Forwarded-For: its last entry is the one Tailscale wrote, so only that one counts."""
    ip = peer or ""
    if ip in ("127.0.0.1", "::1"):
        forwarded = (headers.get("x-forwarded-for") or "").split(",")[-1].strip()
        if forwarded:
            ip = forwarded
    user = headers.get("tailscale-user-login") or ""
    return ip[:64], re.sub(r"[^\w.@+-]", "", user)[:120]


class Guard:
    def __init__(self, path: str | None = None):
        self.path = path if path is not None else (
            ":memory:" if settings.db_path == ":memory:" else str(Path(settings.db_path).parent / "access-log.db"))
        self._db = None
        self._lock = threading.Lock()
        self._fails: dict[str, deque] = {}
        self._locked: dict[str, float] = {}
        self._purged = 0.0

    @property
    def db(self):
        if self._db is None:
            self._db = db.connect(self.path)
            if self.path != ":memory:":
                self._db.execute("PRAGMA journal_mode=WAL")
                self._db.execute("PRAGMA synchronous=NORMAL")
            self._db.executescript(SCHEMA)
        return self._db

    # --- the access log ---------------------------------------------------------------------
    def record(self, kind: str, ip: str, user: str, method: str, path: str, status: int, ms: int = 0) -> None:
        try:
            with self._lock:
                self.db.execute("INSERT INTO access_log (at, kind, ip, user, method, path, status, ms) "
                                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                                (datetime.now(timezone.utc).isoformat(timespec="seconds"), kind, ip, user,
                                 method, path.split("?")[0][:200], status, ms))
                self.db.commit()
                if time.time() - self._purged > 86400:
                    self._purged = time.time()
                    cutoff = (datetime.now(timezone.utc) - timedelta(days=ACCESS_LOG_DAYS)).isoformat()
                    self.db.execute("DELETE FROM access_log WHERE at < ?", (cutoff,))
                    self.db.commit()
        except Exception:
            log.exception("couldn't write the access log")

    def recent(self, limit: int = 200, only_failures: bool = False) -> list[dict]:
        where = "WHERE status IN (401, 403, 429) OR kind = 'lockout'" if only_failures else ""
        with self._lock:
            rows = self.db.execute(f"SELECT at, kind, ip, user, method, path, status, ms FROM access_log {where} "  # nosec B608 (fixed text)
                                   "ORDER BY id DESC LIMIT ?", (min(limit, 2000),)).fetchall()
        return [dict(r) for r in rows]

    def stats(self, days: int = 30) -> dict:
        since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        with self._lock:
            q = lambda sql, *a: self.db.execute(sql, a).fetchone()[0]  # noqa: E731
            return {
                "days": days,
                "entries": q("SELECT count(*) FROM access_log WHERE at >= ?", since),
                "failed_sign_ins": q("SELECT count(*) FROM access_log WHERE at >= ? AND status = 401", since),
                "lockouts": q("SELECT count(*) FROM access_log WHERE at >= ? AND kind = 'lockout'", since),
                "addresses": q("SELECT count(DISTINCT ip) FROM access_log WHERE at >= ?", since),
                "oldest": q("SELECT min(at) FROM access_log") or "",
                "total": q("SELECT count(*) FROM access_log"),
                "keep_days": ACCESS_LOG_DAYS,
            }

    # --- the lockout ------------------------------------------------------------------------
    def locked(self, ip: str) -> int:
        """Seconds left on this address's lockout (0: not locked)."""
        until = self._locked.get(ip, 0)
        if until and until <= time.time():
            self._locked.pop(ip, None)
            return 0
        return int(until - time.time()) if until else 0

    def failed(self, ip: str, user: str = "", path: str = "") -> bool:
        """A failed sign-in; True when it locks the address."""
        now = time.time()
        fails = self._fails.setdefault(ip, deque())
        fails.append(now)
        while fails and fails[0] < now - LOCKOUT_WINDOW:
            fails.popleft()
        if len(fails) >= LOCKOUT_FAILURES and not self.locked(ip):
            self._locked[ip] = now + LOCKOUT_SECONDS
            fails.clear()
            log.warning("locked out %s for %d minutes after %d failed sign-ins", ip, LOCKOUT_SECONDS // 60,
                        LOCKOUT_FAILURES)
            self.record("lockout", ip, user, "", path, 429)
            return True
        return False

    def reset(self) -> None:
        self._fails.clear()
        self._locked.clear()


guard = Guard()


def wants_log(path: str, status: int) -> bool:
    return path.startswith(LOGGED_PREFIXES) or status in (401, 403, 429)
