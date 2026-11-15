"""The email organizer: what stays, what moves where, preview before any change, undo, teaching, the nightly run."""

import asyncio
import json
import re
from email.message import EmailMessage

import pytest

from buddy import mailorganizer as mo
from buddy.connectors.hub import ConnectorHub
from buddy.memory import Memory

ENV = {"BUDDY_EMAIL_ADDRESS": "me@gmail.com", "BUDDY_EMAIL_APP_PASSWORD": "x"}


@pytest.fixture
def memory(tmp_path):
    m = Memory(tmp_path / "buddy.db")
    ConnectorHub(m)
    return m


def mail(sender, subject, bulk=True, mid=None):
    m = EmailMessage()
    m["From"], m["Subject"] = sender, subject
    m["Message-ID"] = mid or f"<{abs(hash((sender, subject)))}@example.com>"
    if bulk:
        m["List-Unsubscribe"] = "<https://example.com/u>"
    m.set_content("body that must never be read")
    return m


INBOX = [
    mail("Mom <mom@family.example>", "50% off at the sale! Come Sunday", bulk=False),          # a person: stays
    mail("Dr. Rao <office@clinic.example>", "Your results", bulk=False),
    mail("Best Buy <deals@bestbuy.example>", "Black Friday: 40% off TVs"),                     # promotion
    mail("Old Navy <oldnavy@email.oldnavy.example>", "Last chance: $20 off your order of $50"),  # 'order' -> stays? no
    mail("The Verge <newsletter@verge.example>", "Weekly digest: top stories"),                # newsletter
    mail("GitHub <notifications@github.example>", "[repo] Run failed: Security checks"),       # notification
    mail("LinkedIn <updates@linkedin.example>", "You appeared in 9 searches"),                 # notification
    mail("Chase <alerts@chase.example>", "Your statement is ready"),                           # matters: stays
    mail("Amazon <ship@amazon.example>", "Your package was delivered"),                        # matters: stays
    mail("Google <no-reply@accounts.google.example>", "Security alert: new sign-in"),          # matters: stays
]


class Server:
    """An IMAP account with folders: enough of search, fetch, create, MOVE and select to organize and undo."""

    def __init__(self, inbox, move_ok=True):
        self.folders = {"INBOX": {str(i + 1): m for i, m in enumerate(inbox)}}
        self.next, self.at, self.readonly, self.move_ok, self.body_fetched = len(inbox) + 1, "INBOX", True, move_ok, False

    def select(self, folder, readonly=False):
        self.at, self.readonly = folder.strip('"'), readonly
        return "OK", [b"1"]

    def create(self, folder):
        self.folders.setdefault(folder.strip('"'), {})
        return "OK", [b""]

    def uid(self, cmd, *args):
        box = self.folders[self.at]
        if cmd == "search":
            if "HEADER" in args:
                want = args[-1].strip('"')
                return "OK", [b" ".join(u.encode() for u, m in box.items() if m["Message-ID"] == want)]
            return "OK", [b" ".join(u.encode() for u in box)]
        if cmd == "fetch":
            self.body_fetched |= "HEADER.FIELDS" not in args[1]
            return "OK", [x for u in args[0].split(",") for x in
                          ((f"1 (UID {u} BODY[HEADER] {{9}}".encode(), box[u].as_bytes()), b")")]
        assert not self.readonly, "a change was tried on a mailbox opened read-only"
        uids, target = args[0].split(","), args[1].strip('"') if len(args) > 1 else ""
        if cmd == "MOVE":
            if not self.move_ok:
                return "NO", [b"unsupported"]
            for u in uids:
                self.folders[target][str(self.next)] = box.pop(u)
                self.next += 1
            return "OK", [b""]
        if cmd == "COPY":
            for u in uids:
                self.folders[target][str(self.next)] = box[u]
                self.next += 1
            return "OK", [b""]
        if cmd == "STORE":
            self.deleted = uids
            return "OK", [b""]
        raise AssertionError(cmd)

    def expunge(self):
        for u in getattr(self, "deleted", []):
            self.folders[self.at].pop(u, None)
        return "OK", [b""]

    def logout(self):
        pass

    def subjects(self, folder):
        return sorted(str(m["Subject"]) for m in self.folders.get(folder, {}).values())


def test_what_stays_and_where_the_rest_goes():
    def where(i):
        return mo.judge(INBOX[i])[0]
    assert where(0) == "inbox" and where(1) == "inbox"                    # a person, whatever the subject says
    assert where(2) == "promotions" and where(4) == "newsletters"
    assert where(5) == "notifications" and where(6) == "notifications"
    assert [where(i) for i in (7, 8, 9)] == ["inbox"] * 3                 # statements, deliveries, security alerts
    assert where(3) == "inbox"   # "your order": when in doubt a message stays; the owner can teach it otherwise
    taught = {"oldnavy.example": "promotions", "github.example": "keep"}
    assert mo.judge(INBOX[3], taught)[0] == "promotions" and mo.judge(INBOX[5], taught) == (
        "inbox", "you said to keep this sender")
    assert mo.judge(mail("Chase <offers@chase.example>", "Earn 60,000 points with a new card"))[0] == "promotions"


def test_a_preview_changes_nothing_and_says_what_would_move(memory):
    server = Server(INBOX)
    got = mo.preview(memory, ENV, connect=lambda a: server)["accounts"]["me@gmail.com"]
    assert got["looked_at"] == 10 and got["stay_in_inbox"] == 6
    assert got["would_move"]["Buddy Promotions"]["emails"] == 1
    assert got["would_move"]["Buddy Notifications"]["top_senders"] == ["GitHub (1)", "LinkedIn (1)"]
    assert len(server.folders) == 1 and len(server.folders["INBOX"]) == 10 and server.readonly is True
    assert server.body_fetched is False                                   # headers only: no body was ever read


def test_organizing_moves_only_bulk_mail_and_undo_puts_it_back(memory):
    server = Server(INBOX)
    got = mo.organize(memory, ENV, connect=lambda a: server, run="r1")
    assert got["moved"]["me@gmail.com"] == {"Buddy Promotions": 1, "Buddy Newsletters": 1, "Buddy Notifications": 2}
    left = server.subjects("INBOX")
    assert len(left) == 6 and "50% off at the sale! Come Sunday" in left and "Your statement is ready" in left
    assert server.subjects("Buddy Promotions") == ["Black Friday: 40% off TVs"]
    assert mo.status(memory)["moved_so_far"]["Buddy Notifications"] == 2
    assert mo.organize(memory, ENV, connect=lambda a: server)["moved"]["me@gmail.com"] == "nothing to move"
    back = mo.undo(memory, ENV, connect=lambda a: server)
    assert back == {"run": "r1", "put_back_in_the_inbox": 4} and len(server.folders["INBOX"]) == 10
    with pytest.raises(Exception, match="nothing to undo"):
        mo.undo(memory, ENV, connect=lambda a: server)


def test_a_server_without_move_copies_then_removes(memory):
    server = Server(INBOX, move_ok=False)
    mo.organize(memory, ENV, connect=lambda a: server)
    assert len(server.folders["INBOX"]) == 6 and len(server.folders["Buddy Notifications"]) == 2


def test_teaching_senders_and_the_rules_around_changing_mail(memory, monkeypatch, tmp_path):
    assert "always stays in your inbox" in mo.teach(memory, "GitHub <notifications@github.example>", "keep")
    assert "always goes to Buddy Promotions" in mo.teach(memory, "oldnavy.example", "promotions")
    server = Server(INBOX)
    mo.organize(memory, ENV, connect=lambda a: server)
    assert "[repo] Run failed: Security checks" in server.subjects("INBOX")
    assert len(server.folders["Buddy Promotions"]) == 2
    mo.teach(memory, "oldnavy.example", "forget")
    assert "oldnavy.example" not in mo.senders(memory)
    with pytest.raises(Exception, match="Which sender"):
        mo.teach(memory, "everyone", "keep")

    (tmp_path / "two").mkdir()
    fresh = Memory(tmp_path / "two" / "second.db")
    ConnectorHub(fresh)
    with pytest.raises(Exception, match="Preview first"):                 # no preview: nothing may move
        asyncio.run(mo.run_tool(fresh, {"action": "organize"}))
    with pytest.raises(Exception, match="Organize once by hand first"):   # never automatic before a first look
        asyncio.run(mo.run_tool(fresh, {"action": "auto", "on": True}))
    assert "every night" in asyncio.run(mo.run_tool(memory, {"action": "auto", "on": True}))
    from datetime import datetime
    assert mo.due(memory, datetime(2026, 11, 16, 2, 0)) and not mo.due(memory, datetime(2026, 11, 16, 14, 0))
    assert not mo.due(fresh, datetime(2026, 11, 16, 2, 0))


def test_the_owner_is_asked_before_anything_moves(memory, monkeypatch):
    monkeypatch.setattr(mo, "preview", lambda memory, env, which, days: {"accounts": {}})
    memory.set(mo.PREVIEW_KEY, json.dumps({"at": __import__("datetime").datetime.now(
        __import__("datetime").timezone.utc).isoformat()}))
    asked = {}

    class Thrift:
        def ask_action(self, question, run, **kw):
            asked["q"] = question
            return "APPROVAL NEEDED"

    out = asyncio.run(mo.run_tool(memory, {"action": "organize", "days": 14}, Thrift()))
    assert out == "APPROVAL NEEDED" and "last 14 days" in asked["q"] and "Nothing is deleted" in asked["q"]
    assert re.search(r"undo", asked["q"])
