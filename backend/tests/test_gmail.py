"""Gmail agent: search, read, draft, send and clean up, against a fake Gmail."""

import asyncio
import json

import pytest

from agents.gmail import mailbox as mb
from agents.gmail import server as gmail_server
from buddy.agents import AgentRegistry, AgentSpec, ApprovalNeeded
from buddy.memory import Memory


def raw_mail(gid, sender, subject, body="Hello", unsubscribe=""):
    extra = f"List-Unsubscribe: {unsubscribe}\r\n" if unsubscribe else ""
    return (f"Message-ID: <m{gid}@x.com>\r\nFrom: {sender}\r\nTo: me@gmail.com\r\nSubject: {subject}\r\n"
            f"Date: Fri, 16 Oct 2026 09:00:00 -0400\r\n{extra}Content-Type: text/plain\r\n\r\n{body}\r\n").encode()


class FakeGmail:
    """Just enough of Gmail's IMAP: labels, flags, X-GM-RAW search, MOVE, APPEND."""

    def __init__(self):
        self.mail = {}  # uid -> dict
        self.drafts = []
        self.selected = None
        self.literal = None
        add = self.add
        add(1, 1001, "Chase <alerts@chase.com>", "Payment due", "Pay $412 on card 4111 1111 1111 1111")
        add(2, 1002, "Shop <deals@shop.com>", "50% off", unsub="<https://shop.com/unsub>")
        add(3, 1003, "Shop <deals@shop.com>", "Last chance", unsub="<https://shop.com/unsub>")
        add(4, 1004, "Priya <priya@work.com>", "Lunch?", "Lunch Friday?", seen=True)

    def add(self, uid, gid, sender, subject, body="Hello", unsub="", seen=False):
        self.mail[uid] = {"gid": gid, "raw": raw_mail(gid, sender, subject, body, unsub),
                          "from": sender.lower(), "labels": {"\\Inbox"}, "seen": seen, "trash": False}

    # imaplib.IMAP4_SSL surface
    def __call__(self, host, timeout=None):
        return self

    def login(self, user, password):
        if password != "apppass":
            raise mb.imaplib.IMAP4.error("bad login")

    def list(self):
        return "OK", [b'(\\HasNoChildren \\All) "/" "[Gmail]/All Mail"',
                      b'(\\HasNoChildren \\Trash) "/" "[Gmail]/Trash"',
                      b'(\\HasNoChildren \\Drafts) "/" "[Gmail]/Drafts"']

    def select(self, folder, readonly=False):
        self.selected = (folder, readonly)

    def logout(self):
        pass

    def append(self, folder, flags, when, raw):
        self.drafts.append((folder, flags, raw))

    def _match(self, query):
        out = []
        for uid, m in sorted(self.mail.items()):
            if m["trash"]:
                continue
            ok = True
            for term in query.split():
                if term.startswith("from:"):
                    ok &= term[5:].lower() in m["from"]
                elif term == "in:inbox":
                    ok &= "\\Inbox" in m["labels"]
                elif term == "is:unread":
                    ok &= not m["seen"]
            if ok:
                out.append(uid)
        return out

    def uid(self, command, *args):
        if command == "SEARCH":
            if args[0] == "X-GM-MSGID":
                found = [u for u, m in self.mail.items() if str(m["gid"]) == args[1]]
            elif args[0] == "CHARSET":
                found = self._match(self.literal.decode())
            else:
                found = self._match(args[1].strip('"'))
            return "OK", [" ".join(map(str, found)).encode()]
        uids = [int(u) for u in args[0].split(",")]
        if command == "FETCH":
            data = []
            for u in uids:
                m = self.mail[u]
                flags = "\\Seen" if m["seen"] else ""
                body = m["raw"] if "BODY.PEEK[]" in args[1] else m["raw"].split(b"\r\n\r\n")[0] + b"\r\n\r\n"
                data += [(f"{u} (UID {u} X-GM-MSGID {m['gid']} FLAGS ({flags}) BODY[] {{{len(body)}}}".encode(), body), b")"]
            return "OK", data
        if command == "STORE":
            for u in uids:
                if args[1] == "-X-GM-LABELS":
                    self.mail[u]["labels"].discard("\\Inbox")
                elif args[1] == "+X-GM-LABELS":
                    self.mail[u]["labels"].add(args[2].strip("()").strip('"'))
                elif args[1] == "+FLAGS":
                    self.mail[u]["seen"] = True
            return "OK", []
        if command == "MOVE":
            for u in uids:
                self.mail[u]["trash"] = True
            return "OK", []
        raise AssertionError(command)


class FakeSMTP:
    sent = []

    def __init__(self, host, port, timeout=None):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def login(self, user, password):
        assert password == "apppass"

    def send_message(self, msg):
        FakeSMTP.sent.append(msg)


@pytest.fixture
def gmail():
    fake = FakeGmail()
    FakeSMTP.sent = []
    return fake, mb.Mailbox("me@gmail.com", "apppass", imap_factory=fake, smtp_factory=FakeSMTP)


def test_search_and_read_keep_mail_unread(gmail):
    fake, box = gmail
    found = box.search("from:shop in:inbox")
    assert found["total_matches"] == 2 and [m["subject"] for m in found["messages"]] == ["Last chance", "50% off"]
    assert fake.selected == ('"[Gmail]/All Mail"', True)  # read-only
    message = box.read("1001")
    assert "Pay $412" in message["text"] and not fake.mail[1]["seen"]


def test_inbox_summary_finds_the_noisy_senders(gmail):
    _, box = gmail
    summary = box.inbox_summary(30)
    top = summary["top_senders"][0]
    assert top == {"sender": "deals@shop.com", "messages": 2, "unread": 2, "newsletter": True}
    assert summary["inbox_messages"] == 4 and summary["unread"] == 3


def test_cleanup_archive_trash_mark_read_label(gmail):
    fake, box = gmail
    assert box.archive("from:shop")["matched"] == 2
    assert "\\Inbox" not in fake.mail[2]["labels"] and "\\Inbox" in fake.mail[1]["labels"]
    assert box.mark_read(ids=["1001"])["matched"] == 1 and fake.mail[1]["seen"]
    box.label("Bills", ids=["1001"])
    assert "Bills" in fake.mail[1]["labels"]
    assert box.trash("from:shop")["matched"] == 2 and fake.mail[3]["trash"]
    assert fake.selected[1] is False  # changes need a writable mailbox
    with pytest.raises(mb.MailError):
        box.archive()  # nothing said: nothing touched


def test_unsubscribe_links(gmail):
    _, box = gmail
    assert box.unsubscribe_links("deals@shop.com")["unsubscribe"] == ["<https://shop.com/unsub>"]


def test_draft_and_reply_threading(gmail):
    fake, box = gmail
    box.draft("priya@work.com", "", "Friday works!", reply_to_id="1004")
    folder, flags, raw = fake.drafts[0]
    assert folder == '"[Gmail]/Drafts"' and b"Subject: Re: Lunch?" in raw and b"In-Reply-To: <m1004@x.com>" in raw
    box.send("priya@work.com", "", "See you then", reply_to_id="1004")
    msg = FakeSMTP.sent[0]
    assert msg["Subject"] == "Re: Lunch?" and msg["References"] == "<m1004@x.com>"
    with pytest.raises(mb.MailError):
        box.send("not-an-address", "x", "y")


def test_bad_app_password_is_a_clear_error():
    with pytest.raises(mb.MailError, match="app password"):
        mb.Mailbox("me@gmail.com", "wrong", imap_factory=FakeGmail()).search("x")


def test_agent_reads_freely_but_asks_before_sending_or_cleaning(gmail, monkeypatch):
    fake, box = gmail
    monkeypatch.setattr(gmail_server, "_box", lambda: box)

    async def body():
        reg = AgentRegistry(Memory(":memory:"), [AgentSpec(
            name="gmail", server=gmail_server.server, approval_required=["*"],
            no_approval_for=["search_mail", "read_mail", "inbox_summary", "unsubscribe_links", "create_draft"],
        )])
        await reg.start()
        try:
            out = json.loads(await reg.call("gmail__read_mail", {"message_id": "1001"}, 1))
            assert "4111 1111" not in out["text"]  # masked before Claude sees it
            await reg.call("gmail__create_draft", {"to": "a@b.com", "subject": "Hi", "body": "Hey"}, 1)
            for tool, args in [("gmail__send_mail", {"to": "a@b.com", "subject": "Hi", "body": "Hey"}),
                               ("gmail__trash_mail", {"query": "from:shop"}),
                               ("gmail__archive_mail", {"query": "from:shop"})]:
                with pytest.raises(ApprovalNeeded):
                    await reg.call(tool, args, 1)
            assert not FakeSMTP.sent and not fake.mail[2]["trash"]  # nothing happened without a yes
            action_id = next(iter(reg.pending))
            await reg.confirm(action_id, 2)  # the user said yes in a later message
        finally:
            await reg.close()

    asyncio.run(body())
    assert len(fake.drafts) == 1
    assert FakeSMTP.sent or fake.mail[2]["trash"] or "\\Inbox" not in fake.mail[2]["labels"]


def test_agent_process_gets_only_the_settings_it_is_given(monkeypatch):
    import sys
    from pathlib import Path

    monkeypatch.setenv("BUDDY_EMAIL_ADDRESS", "")  # not configured: the agent says so clearly
    monkeypatch.setenv("SOME_OTHER_SECRET", "nope")

    async def body():
        reg = AgentRegistry(Memory(":memory:"), [AgentSpec(
            name="gmail", command=[sys.executable, "-m", "agents.gmail.server"],
            env_from=["BUDDY_EMAIL_ADDRESS", "BUDDY_EMAIL_APP_PASSWORD"],
            env={"PYTHONPATH": str(Path(__file__).resolve().parents[1])},
        )])
        await reg.start()
        try:
            assert "gmail__search_mail" in reg.tools and "gmail__send_mail" in reg.tools
            return await reg.call("gmail__search_mail", {"query": "in:inbox"}, 1)
        finally:
            await reg.close()

    assert "Email isn't set up" in asyncio.run(body())
