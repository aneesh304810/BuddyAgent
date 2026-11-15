"""Email attachments, read: the PDFs, pictures and documents attached to the owner's life email are opened,
understood and remembered, instead of only their file names being seen.

  Which ones      attachments on email that life memory kept (lifemail.py): a travel authorisation, a school's
                  weekly letter, an insurer's receipt, an invitation that is only a picture. Never attachments
                  on marketing mail. PDFs, pictures, Word and Excel files and text, up to 10 MB each and four
                  per email. A picture inside the email body counts only when the email has almost no text
                  (an invitation sent as an image), not logos and signatures.
  How             the email is fetched again from the owner's own mailbox, read-only, by its Message-ID; each
                  attachment goes into the library (library.py), which reads it (Claude reads scans and
                  pictures), summarises it, indexes it for search and keeps the original encrypted.
  Private ones    documents from travel, official, health, tax, insurance and money email go into the vault:
                  their summary is known, the full text opens only after the owner's yes.

Read by Claude only, kept in the encrypted library. The owner can open or delete each one there.
"""

import email
import email.policy
import imaplib
import json
import logging
import re
from datetime import datetime, timezone

from .memory import Memory

log = logging.getLogger(__name__)

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS mail_files (
    id INTEGER PRIMARY KEY,
    record_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    item_id TEXT NOT NULL DEFAULT '',     -- library_items.id
    status TEXT NOT NULL DEFAULT 'read',  -- read | unreadable
    summary TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    UNIQUE (record_id, name)
);
"""
READABLE = {"pdf", "png", "jpg", "jpeg", "webp", "gif", "docx", "xlsx", "xls", "csv", "txt", "html", "htm"}
IMAGES = {"png", "jpg", "jpeg", "webp", "gif"}
MAX_BYTES, MAX_PER_EMAIL, INLINE_MIN = 10_000_000, 4, 40_000
VAULT = {"travel", "official", "health", "tax", "insurance", "money"}


def _ext(name: str) -> str:
    return name.rsplit(".", 1)[-1].lower() if "." in name else ""


def _body_words(message) -> int:
    part = message.get_body(preferencelist=("plain", "html"))
    try:
        text = part.get_content() if part is not None else ""
    except (LookupError, ValueError):
        text = ""
    return len(re.sub(r"(?s)<[^>]+>", " ", text).split())


def parts(message) -> list[dict]:
    """The readable attachments of one message: [{name, data, inline}]."""
    image_only = _body_words(message) < 40
    out, n = [], 0
    for part in message.walk():
        if part.is_multipart():
            continue
        kind = part.get_content_type()
        name = part.get_filename() or ""
        inline = str(part.get("Content-Disposition", "")).lower().startswith("inline") or not name
        if not name and kind.startswith("image/"):
            n += 1
            name = f"picture-{n}.{kind.split('/')[-1].replace('jpeg', 'jpg')}"
        if _ext(name) not in READABLE or kind in ("text/plain", "text/html") and not part.get_filename():
            continue
        try:
            data = part.get_payload(decode=True) or b""
        except Exception:
            continue
        if not data or len(data) > MAX_BYTES:
            continue
        if inline and _ext(name) in IMAGES and (not image_only or len(data) < INLINE_MIN):
            continue  # a logo, a signature, a tracking pixel
        out.append({"name": name[:120], "data": data, "inline": inline})
    return out[:MAX_PER_EMAIL]


def names(message) -> list[str]:
    """Attachment names, for the stored email record (no content)."""
    try:
        return [p["name"] for p in parts(message)]
    except Exception:
        return []


def _imap(account):
    imap = imaplib.IMAP4_SSL(account.imap_host, timeout=60)
    imap.login(account.address, account.password)
    return imap


def fetch_message(account, message_id: str, connect=None):
    """The whole message with this Message-ID from the owner's mailbox (read-only), or None."""
    wanted = re.sub(r'[\r\n"\\]', "", str(message_id)).strip()
    if not wanted:
        return None
    imap = (connect or _imap)(account)
    try:
        folders = [account.folder] + (['"[Gmail]/All Mail"'] if getattr(account, "is_gmail", False) else [])
        for folder in folders:
            try:
                ok, _ = imap.select(folder, readonly=True)
                if ok != "OK":
                    continue
                _, found = imap.search(None, "HEADER", "Message-ID", f'"{wanted}"')
                ids = (found[0] or b"").split()
                if not ids:
                    continue
                _, got = imap.fetch(ids[-1], "(BODY.PEEK[])")
                raw = next((p[1] for p in got if isinstance(p, tuple)), None)
                if raw:
                    return email.message_from_bytes(raw, policy=email.policy.default)
            except imaplib.IMAP4.error as e:
                log.info("mailfiles: %s in %s", type(e).__name__, folder)
        return None
    finally:
        try:
            imap.logout()
        except Exception:
            log.debug("imap logout failed", exc_info=True)


def known(memory: Memory, record_id: int) -> list[dict]:
    memory.db.executescript(SCHEMA_SQL)
    return [dict(r) for r in memory.db.execute("SELECT name, item_id, status, summary FROM mail_files WHERE "
                                               "record_id = ? ORDER BY id", (record_id,))]


async def read(memory: Memory, record_id: int, vault: bool = False, env=None, connect=None, library=None) -> list[dict]:
    """Open the attachments of one stored email and read them into the library. Returns
    [{name, item_id, title, summary, important}] (already-read ones included); [] when there are none."""
    import asyncio  # noqa: PLC0415

    from .email_accounts import email_accounts, pick_account  # noqa: PLC0415
    from .library import Library  # noqa: PLC0415
    memory.db.executescript(SCHEMA_SQL)
    have = known(memory, record_id)
    if have:
        return [{"name": h["name"], "item_id": h["item_id"], "title": h["name"], "summary": h["summary"],
                 "important": []} for h in have if h["status"] == "read"]
    row = memory.db.execute("SELECT title, external_id, data FROM records WHERE id = ? AND kind = 'email'",
                            (record_id,)).fetchone()
    if row is None:
        return []
    meta = json.loads(row["data"] or "{}")
    try:
        account = pick_account(email_accounts(env), meta.get("account") or "")
    except LookupError:
        return []
    message = await asyncio.to_thread(fetch_message, account, row["external_id"], connect)
    if message is None:
        return []
    lib = library or Library(memory)
    sender = str(meta.get("from") or "").split("<")[0].strip()[:60]
    out = []
    for p in parts(message):
        now = datetime.now(timezone.utc).isoformat()
        try:
            item = lib.add(p["name"], p["data"], note=f"Attached to the email \"{row['title'][:120]}\" from {sender}",
                           vault=vault)
            if not item.get("duplicate") or item.get("status") == "reading":
                item = await lib.ingest(item["id"], p["data"])
            summary = (item.get("summary") or "")[:600]
            ok = item.get("status") == "ready"
            memory.db.execute("INSERT OR IGNORE INTO mail_files (record_id, name, item_id, status, summary, "
                              "created_at) VALUES (?, ?, ?, ?, ?, ?)",
                              (record_id, p["name"], item["id"], "read" if ok else "unreadable", summary, now))
            if ok:
                out.append({"name": p["name"], "item_id": item["id"], "title": item.get("title") or p["name"],
                            "summary": summary, "important": item.get("important") or []})
        except Exception as e:
            log.warning("mailfiles: couldn't read %s: %s", p["name"], type(e).__name__)
            memory.db.execute("INSERT OR IGNORE INTO mail_files (record_id, name, status, created_at) VALUES "
                              "(?, ?, 'unreadable', ?)", (record_id, p["name"], now))
    memory.db.commit()
    return out
