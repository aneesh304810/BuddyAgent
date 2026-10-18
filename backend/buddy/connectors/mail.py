"""Email connector: Gmail (or any IMAP mailbox), read-only.

The mailbox is opened read-only and messages are fetched with PEEK, so nothing
is marked as read, moved or deleted. For Gmail: turn on 2-Step Verification,
create an App Password (myaccount.google.com → Security → App passwords), and
set BUDDY_EMAIL_ADDRESS and BUDDY_EMAIL_APP_PASSWORD.
"""

import asyncio
import email
import email.policy
import imaplib
import re
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

from .base import Connector, Record

DAYS_PER_SYNC = 2      # each sync looks back this far; known messages are skipped
MAX_MESSAGES = 200
MAX_TEXT = 1500
TAGS = re.compile(r"<[^>]+>")


def _text_of(message) -> str:
    part = message.get_body(preferencelist=("plain", "html"))
    if part is None:
        return ""
    try:
        text = part.get_content()
    except (LookupError, ValueError):
        return ""
    if part.get_content_type() == "text/html":
        text = TAGS.sub(" ", re.sub(r"(?is)<(script|style).*?</\1>", " ", text))
    return " ".join(text.split())[:MAX_TEXT]


def to_record(raw: bytes, folder: str) -> Record | None:
    message = email.message_from_bytes(raw, policy=email.policy.default)
    message_id = str(message.get("Message-ID", "")).strip()
    if not message_id:
        return None
    try:
        sent = parsedate_to_datetime(str(message.get("Date")))
        sent = sent if sent.tzinfo else sent.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        sent = datetime.now(timezone.utc)
    sender = str(message.get("From", ""))
    return Record(
        external_id=message_id,
        kind="email",
        title=str(message.get("Subject", "") or "(no subject)"),
        body=f"From: {sender}\n{_text_of(message)}",
        start_at=sent,
        data={"from": sender, "folder": folder},
    )


class EmailConnector(Connector):
    name = "email"
    title = "Email"
    description = "Recent emails (read-only): bills, bookings, alerts, people."
    required = {
        "BUDDY_EMAIL_ADDRESS": "your email address",
        "BUDDY_EMAIL_APP_PASSWORD": "an app password (not your real password)",
    }
    every_minutes = 15
    snapshot = False
    keep_days = 30

    async def fetch(self) -> list[Record]:
        return await asyncio.to_thread(self._fetch)

    def _fetch(self) -> list[Record]:
        host = self.setting("BUDDY_EMAIL_IMAP_HOST", "imap.gmail.com")
        folder = self.setting("BUDDY_EMAIL_FOLDER", "INBOX")
        since = (datetime.now(timezone.utc) - timedelta(days=DAYS_PER_SYNC)).strftime("%d-%b-%Y")
        records: list[Record] = []
        imap = imaplib.IMAP4_SSL(host, timeout=60)
        try:
            imap.login(self.setting("BUDDY_EMAIL_ADDRESS"), self.setting("BUDDY_EMAIL_APP_PASSWORD"))
            imap.select(f'"{folder}"', readonly=True)  # read-only: nothing changes in the mailbox
            _, found = imap.search(None, f"(SINCE {since})")
            ids = (found[0] or b"").split()[-MAX_MESSAGES:]
            for msg_id in ids:
                _, parts = imap.fetch(msg_id, "(BODY.PEEK[])")  # PEEK: stays unread
                raw = next((p[1] for p in parts if isinstance(p, tuple)), None)
                if raw:
                    record = to_record(raw, folder)
                    if record:
                        records.append(record)
        finally:
            try:
                imap.logout()
            except Exception:
                pass
        return records
