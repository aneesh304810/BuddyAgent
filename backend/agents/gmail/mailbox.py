"""Gmail over IMAP and SMTP with an app password: search, read, draft, send, clean up.

No Google Cloud project needed: the same app password as the email connector.
Messages are identified by Gmail's own message id (X-GM-MSGID), which stays the
same whatever label or folder a message is in. Nothing is ever deleted for good:
"trash" moves mail to Gmail's Trash, where it can be restored for 30 days.
"""

import email
import email.policy
import imaplib
import re
import smtplib
import time
from collections import Counter
from email.message import EmailMessage
from email.utils import formataddr, getaddresses, make_msgid, parseaddr

MAX_RESULTS = 50
MAX_BULK = 500
MAX_TEXT = 6000
_LIST_LINE = re.compile(rb'\((?P<flags>[^)]*)\) "(?P<sep>[^"]*)" (?P<name>.+)$')
_TAGS = re.compile(r"<[^>]+>")


class MailError(Exception):
    """Something the user should hear about (bad query, nothing found, login failed)."""


def quote(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def text_of(message) -> str:
    part = message.get_body(preferencelist=("plain", "html"))
    if part is None:
        return ""
    try:
        text = part.get_content()
    except (LookupError, ValueError):
        return ""
    if part.get_content_type() == "text/html":
        text = _TAGS.sub(" ", re.sub(r"(?is)<(script|style).*?</\1>", " ", text))
    return " ".join(text.split())


class Mailbox:
    def __init__(self, address: str, app_password: str, imap_host: str = "imap.gmail.com",
                 smtp_host: str = "smtp.gmail.com", smtp_port: int = 465,
                 imap_factory=imaplib.IMAP4_SSL, smtp_factory=smtplib.SMTP_SSL):
        if not address or not app_password:
            raise MailError("Email isn't set up: BUDDY_EMAIL_ADDRESS and BUDDY_EMAIL_APP_PASSWORD are needed.")
        self.address = address
        self.password = app_password
        self.imap_host, self.smtp_host, self.smtp_port = imap_host, smtp_host, smtp_port
        self.imap_factory, self.smtp_factory = imap_factory, smtp_factory

    # --- connection --------------------------------------------------------
    def _open(self, readonly: bool):
        imap = self.imap_factory(self.imap_host, timeout=60)
        try:
            imap.login(self.address, self.password)
        except imaplib.IMAP4.error as e:
            raise MailError("Gmail refused the login. Check the app password.") from e
        folders = self._special_folders(imap)
        imap.select(quote(folders["all"]), readonly=readonly)
        return imap, folders

    @staticmethod
    def _special_folders(imap) -> dict[str, str]:
        """Gmail's special folders, found by their role so non-English accounts work too."""
        found = {"all": "[Gmail]/All Mail", "trash": "[Gmail]/Trash", "drafts": "[Gmail]/Drafts"}
        roles = {b"\\All": "all", b"\\Trash": "trash", b"\\Drafts": "drafts"}
        _, lines = imap.list()
        for line in lines or []:
            match = _LIST_LINE.match(line if isinstance(line, bytes) else line.encode())
            if not match:
                continue
            name = match.group("name").decode().strip().strip('"')
            for flag, role in roles.items():
                if flag in match.group("flags").split():
                    found[role] = name
        return found

    @staticmethod
    def _close(imap) -> None:
        try:
            imap.logout()
        except Exception:
            pass

    # --- finding -----------------------------------------------------------
    @staticmethod
    def _search(imap, query: str) -> list[bytes]:
        """UIDs matching a Gmail search ("from:chase newer_than:7d"), oldest first."""
        if query.isascii():
            status, data = imap.uid("SEARCH", "X-GM-RAW", quote(query))
        else:
            imap.literal = query.encode("utf-8")
            status, data = imap.uid("SEARCH", "CHARSET", "UTF-8", "X-GM-RAW")
        if status != "OK":
            raise MailError(f"Gmail couldn't run the search {query!r}.")
        return (data[0] or b"").split()

    @staticmethod
    def _uids_for_ids(imap, ids: list[str]) -> list[bytes]:
        uids: list[bytes] = []
        for gid in ids:
            if not str(gid).isdigit():
                raise MailError(f"{gid!r} isn't a message id from search_mail.")
            _, data = imap.uid("SEARCH", "X-GM-MSGID", str(gid))
            uids += (data[0] or b"").split()
        return uids

    @staticmethod
    def _headers(imap, uids: list[bytes]) -> list[dict]:
        if not uids:
            return []
        _, data = imap.uid(
            "FETCH", b",".join(uids).decode(),
            "(X-GM-MSGID FLAGS BODY.PEEK[HEADER.FIELDS (FROM TO SUBJECT DATE LIST-UNSUBSCRIBE)])",
        )
        out = []
        for part in data:
            if not isinstance(part, tuple):
                continue
            meta, raw = part[0].decode(errors="replace"), part[1]
            gid = re.search(r"X-GM-MSGID (\d+)", meta)
            headers = email.message_from_bytes(raw, policy=email.policy.default)
            out.append({
                "id": gid.group(1) if gid else "",
                "from": str(headers.get("From", "")),
                "to": str(headers.get("To", "")),
                "subject": str(headers.get("Subject", "")),
                "date": str(headers.get("Date", "")),
                "unread": "\\Seen" not in meta,
                "unsubscribe": str(headers.get("List-Unsubscribe", "")),
            })
        return out

    def search(self, query: str, limit: int = 10) -> dict:
        imap, _ = self._open(readonly=True)
        try:
            uids = self._search(imap, query)
            newest = list(reversed(uids))[: max(1, min(limit, MAX_RESULTS))]
            messages = self._headers(imap, newest)
        finally:
            self._close(imap)
        for m in messages:
            m.pop("unsubscribe", None)
        return {"query": query, "total_matches": len(uids), "messages": messages}

    def read(self, message_id: str) -> dict:
        imap, _ = self._open(readonly=True)
        try:
            uids = self._uids_for_ids(imap, [message_id])
            if not uids:
                raise MailError("That message wasn't found.")
            _, data = imap.uid("FETCH", uids[0].decode(), "(BODY.PEEK[])")  # PEEK: stays unread
            raw = next(p[1] for p in data if isinstance(p, tuple))
        finally:
            self._close(imap)
        message = email.message_from_bytes(raw, policy=email.policy.default)
        attachments = [p.get_filename() for p in message.iter_attachments() if p.get_filename()]
        return {
            "id": message_id,
            "from": str(message.get("From", "")),
            "to": str(message.get("To", "")),
            "cc": str(message.get("Cc", "")),
            "subject": str(message.get("Subject", "")),
            "date": str(message.get("Date", "")),
            "text": text_of(message)[:MAX_TEXT],
            "attachments": attachments,
        }

    def inbox_summary(self, days: int = 30) -> dict:
        """Who fills the inbox: the starting point for a cleanup."""
        imap, _ = self._open(readonly=True)
        try:
            uids = self._search(imap, f"in:inbox newer_than:{max(1, min(days, 365))}d")
            recent = list(reversed(uids))[:2000]
            headers: list[dict] = []
            for i in range(0, len(recent), 200):
                headers += self._headers(imap, recent[i:i + 200])
        finally:
            self._close(imap)
        senders = Counter()
        unread = Counter()
        newsletters = set()
        for h in headers:
            sender = parseaddr(h["from"])[1].lower() or h["from"]
            senders[sender] += 1
            unread[sender] += h["unread"]
            if h["unsubscribe"]:
                newsletters.add(sender)
        return {
            "days": days,
            "inbox_messages": len(uids),
            "unread": sum(unread.values()),
            "top_senders": [
                {"sender": s, "messages": n, "unread": unread[s], "newsletter": s in newsletters}
                for s, n in senders.most_common(15)
            ],
        }

    def unsubscribe_links(self, sender: str) -> dict:
        imap, _ = self._open(readonly=True)
        try:
            uids = self._search(imap, f"from:{sender}")
            headers = self._headers(imap, list(reversed(uids))[:5])
        finally:
            self._close(imap)
        links = sorted({h["unsubscribe"] for h in headers if h["unsubscribe"]})
        return {"sender": sender, "unsubscribe": links or ["No unsubscribe link found."]}

    # --- cleaning up -------------------------------------------------------
    def _bulk(self, query: str, ids: list[str], limit: int, action) -> dict:
        if not query and not ids:
            raise MailError("Say which emails: a Gmail search or message ids.")
        imap, folders = self._open(readonly=False)
        try:
            uids = self._uids_for_ids(imap, ids) if ids else self._search(imap, query)
            uids = list(reversed(uids))[: max(1, min(limit, MAX_BULK))]
            if uids:
                action(imap, b",".join(uids).decode(), folders)
        finally:
            self._close(imap)
        return {"matched": len(uids), "query": query, "ids": ids}

    def archive(self, query: str = "", ids: list[str] | None = None, limit: int = MAX_BULK) -> dict:
        return self._bulk(query, ids or [], limit,
                          lambda imap, uids, _: imap.uid("STORE", uids, "-X-GM-LABELS", "(\\Inbox)"))

    def trash(self, query: str = "", ids: list[str] | None = None, limit: int = MAX_BULK) -> dict:
        return self._bulk(query, ids or [], limit,
                          lambda imap, uids, f: imap.uid("MOVE", uids, quote(f["trash"])))

    def mark_read(self, query: str = "", ids: list[str] | None = None, limit: int = MAX_BULK) -> dict:
        return self._bulk(query, ids or [], limit,
                          lambda imap, uids, _: imap.uid("STORE", uids, "+FLAGS", "(\\Seen)"))

    def label(self, label: str, query: str = "", ids: list[str] | None = None, limit: int = MAX_BULK) -> dict:
        if not label.strip():
            raise MailError("Which label?")
        return self._bulk(query, ids or [], limit,
                          lambda imap, uids, _: imap.uid("STORE", uids, "+X-GM-LABELS", f"({quote(label)})"))

    # --- writing -----------------------------------------------------------
    def _compose(self, to: str, subject: str, body: str, cc: str = "", reply_to_id: str = "") -> EmailMessage:
        if not getaddresses([to]) or "@" not in to:
            raise MailError(f"{to!r} doesn't look like an email address.")
        msg = EmailMessage()
        msg["From"] = formataddr(("", self.address))
        msg["To"] = to
        if cc:
            msg["Cc"] = cc
        msg["Message-ID"] = make_msgid(domain=self.address.split("@")[-1])
        if reply_to_id:
            original = self._original_headers(reply_to_id)
            msg["In-Reply-To"] = original["message_id"]
            msg["References"] = (original["references"] + " " + original["message_id"]).strip()
            if not subject:
                subject = original["subject"] if original["subject"].lower().startswith("re:") \
                    else f"Re: {original['subject']}"
        msg["Subject"] = subject or "(no subject)"
        msg.set_content(body)
        return msg

    def _original_headers(self, message_id: str) -> dict:
        imap, _ = self._open(readonly=True)
        try:
            uids = self._uids_for_ids(imap, [message_id])
            if not uids:
                raise MailError("The email to reply to wasn't found.")
            _, data = imap.uid("FETCH", uids[0].decode(),
                               "(BODY.PEEK[HEADER.FIELDS (MESSAGE-ID REFERENCES SUBJECT)])")
            raw = next(p[1] for p in data if isinstance(p, tuple))
        finally:
            self._close(imap)
        h = email.message_from_bytes(raw, policy=email.policy.default)
        return {"message_id": str(h.get("Message-ID", "")), "references": str(h.get("References", "")),
                "subject": str(h.get("Subject", ""))}

    def draft(self, to: str, subject: str, body: str, cc: str = "", reply_to_id: str = "") -> dict:
        msg = self._compose(to, subject, body, cc, reply_to_id)
        imap, folders = self._open(readonly=False)
        try:
            imap.append(quote(folders["drafts"]), "(\\Draft)",
                        imaplib.Time2Internaldate(time.time()), msg.as_bytes())
        finally:
            self._close(imap)
        return {"draft_saved": True, "to": to, "subject": msg["Subject"]}

    def send(self, to: str, subject: str, body: str, cc: str = "", reply_to_id: str = "") -> dict:
        msg = self._compose(to, subject, body, cc, reply_to_id)
        with self.smtp_factory(self.smtp_host, self.smtp_port, timeout=60) as smtp:
            smtp.login(self.address, self.password)
            smtp.send_message(msg)  # Gmail keeps a copy in Sent by itself
        return {"sent": True, "to": to, "subject": msg["Subject"]}
