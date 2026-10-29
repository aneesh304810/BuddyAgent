"""Email connector: every Gmail (or other IMAP) account you set up, read-only.

Mailboxes are opened read-only and messages are fetched with PEEK, so nothing
is marked as read, moved or deleted. For Gmail: turn on 2-Step Verification,
create an App Password (myaccount.google.com → Security → App passwords), and
set BUDDY_EMAIL_ADDRESS and BUDDY_EMAIL_APP_PASSWORD; more accounts use
BUDDY_EMAIL_2_ADDRESS, BUDDY_EMAIL_2_APP_PASSWORD and so on (see email_accounts).
"""

import asyncio
import email
import email.policy
import imaplib
import logging
import re
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

from ..email_accounts import EmailAccount, email_accounts
from .base import Connector, Record

log = logging.getLogger(__name__)

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


def to_record(raw: bytes, folder: str, account: str = "") -> Record | None:
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
        body=f"From: {sender}\n" + (f"To: {account}\n" if account else "") + _text_of(message),
        start_at=sent,
        data={"from": sender, "folder": folder, "account": account},
    )


class EmailConnector(Connector):
    name = "email"
    title = "Email"
    description = "Recent emails from all your accounts (read-only): bills, bookings, alerts, people."
    required = {
        "BUDDY_EMAIL_ADDRESS": "your email address",
        "BUDDY_EMAIL_APP_PASSWORD": "an app password (not your real password)",
    }
    every_minutes = 15
    snapshot = False
    keep_days = 30

    def missing(self) -> list[str]:
        return [] if email_accounts(self.env) else list(self.required)

    async def fetch(self) -> list[Record]:
        return await asyncio.to_thread(self._fetch)

    def _fetch(self) -> list[Record]:
        records: list[Record] = []
        errors: list[Exception] = []
        for account in email_accounts(self.env):
            try:
                records += self._fetch_account(account)
            except Exception as e:  # one broken account doesn't stop the others
                log.warning("email %s: %s", account.address, e)
                errors.append(e)
        if errors and not records:
            raise errors[0]
        return records

    def _fetch_account(self, account: EmailAccount) -> list[Record]:
        since = (datetime.now(timezone.utc) - timedelta(days=DAYS_PER_SYNC)).strftime("%d-%b-%Y")
        records: list[Record] = []
        imap = imaplib.IMAP4_SSL(account.imap_host, timeout=60)
        try:
            imap.login(account.address, account.password)
            imap.select(f'"{account.folder}"', readonly=True)  # read-only: nothing changes in the mailbox
            _, found = imap.search(None, f"(SINCE {since})")
            ids = (found[0] or b"").split()[-MAX_MESSAGES:]
            for msg_id in ids:
                _, parts = imap.fetch(msg_id, "(BODY.PEEK[])")  # PEEK: stays unread
                raw = next((p[1] for p in parts if isinstance(p, tuple)), None)
                if raw:
                    record = to_record(raw, account.folder, account.address)
                    if record:
                        records.append(record)
        finally:
            try:
                imap.logout()
            except Exception:
                pass
        return records
