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


# --- old promotions are archived ----------------------------------------------------------------------------------
from datetime import date, datetime, timedelta, timezone  # noqa: E402
from email.utils import format_datetime  # noqa: E402

TODAY = date(2026, 11, 21)


def dated(sender, subject, days_old, bulk=True):
    m = mail(sender, subject, bulk=bulk)
    m["Date"] = format_datetime(datetime(2026, 11, 21, 12, tzinfo=timezone.utc) - timedelta(days=days_old))
    return m


class Dated(Server):
    """The same account with dates, stars and an archive: Gmail's All Mail, or a folder the server marks."""

    def __init__(self, inbox, boxes=(b'(\\HasNoChildren) "/" "INBOX"', b'(\\All \\HasNoChildren) "/" "[Gmail]/All Mail"'),
                 starred=(), header_search=True):
        super().__init__(inbox)
        self.boxes, self.starred, self.header_search, self.asked = list(boxes), set(starred), header_search, []
        for raw in boxes:
            name = raw.decode().rsplit('" ', 1)[-1].strip('"')
            self.folders.setdefault(name, {})

    def list(self):
        return "OK", self.boxes

    def select(self, folder, readonly=False):
        if folder.strip('"') not in self.folders:
            return "NO", [b"no such folder"]
        return super().select(folder, readonly)

    def uid(self, cmd, *args):
        if cmd == "search" and "HEADER" not in args:
            said = args[1]
            self.asked.append(said)
            if "HEADER" in said and not self.header_search:
                return "BAD", [b"cannot search headers"]
            when = {k: datetime.strptime(v, "%d-%b-%Y").date() for k, v in re.findall(r"(SINCE|BEFORE) (\S+)", said)}
            got = []
            for u, m in self.folders[self.at].items():
                day = datetime.strptime(str(m["Date"])[5:16], "%d %b %Y").date()
                if when.get("SINCE", date.min) <= day < when.get("BEFORE", date.max) and not (
                        "UNFLAGGED" in said and str(m["Subject"]) in self.starred) and not (
                        "HEADER List-Unsubscribe" in said and not m["List-Unsubscribe"]):
                    got.append(u.encode())
            return "OK", [b" ".join(got)]
        return super().uid(cmd, *args)


OLD = [
    dated("Best Buy <deals@bestbuy.example>", "Black Friday: 40% off TVs", 15),               # an old promotion: goes
    dated("Costco <costco@online.costco.example>", "Member-only savings: $300 off", 12),      # goes
    dated("Target <offers@target.example>", "Starred sale: 20% off toys", 20),                # starred: stays
    dated("Kohl's <kohls@email.kohls.example>", "Today only: 30% off", 3),                    # 3 days old: stays
    dated("Mom <mom@family.example>", "50% off at the sale! Come Sunday", 30, bulk=False),    # a person: stays
    dated("The Verge <newsletter@verge.example>", "Weekly digest: top stories", 30),          # a newsletter: stays
    dated("Chase <alerts@chase.example>", "Your statement is ready", 30),                     # matters: stays
    dated("Amazon <ship@amazon.example>", "Your order of $50 has shipped", 30),               # an order: stays
]


@pytest.fixture
def clock(monkeypatch):
    monkeypatch.setattr(mo, "local_now", lambda: datetime(2026, 11, 21, 3, 0, tzinfo=timezone.utc))


def test_old_promotions_are_previewed_then_archived_and_undo_puts_them_back(memory, clock):
    server = Dated(OLD, starred={"Starred sale: 20% off toys"})
    seen = mo.archive_preview(memory, ENV, days=10, connect=lambda a: server)
    acct = seen["accounts"]["me@gmail.com"]
    assert acct["would_archive"] == 2 and acct["found_in"] == {"INBOX": 2} and acct["to"] == "[Gmail]/All Mail"
    assert len(server.folders["INBOX"]) == 8 and server.readonly                 # a preview changes nothing
    assert all("UNFLAGGED" in q and "BEFORE 11-Nov-2026" in q for q in server.asked)
    got = mo.archive_old(memory, ENV, days=10, connect=lambda a: server, run="a1")
    assert got["archived"]["me@gmail.com"] == {"archived": 2, "from": {"INBOX": 2}, "to": "[Gmail]/All Mail"}
    assert server.subjects("[Gmail]/All Mail") == ["Black Friday: 40% off TVs", "Member-only savings: $300 off"]
    left = server.subjects("INBOX")
    assert len(left) == 6 and "Starred sale: 20% off toys" in left and "Today only: 30% off" in left
    assert "Weekly digest: top stories" in left and "Your order of $50 has shipped" in left
    assert not server.body_fetched and not hasattr(server, "deleted")            # headers only; nothing ever deleted
    assert mo.archive_old(memory, ENV, days=10, connect=lambda a: server)["archived"]["me@gmail.com"] == "nothing to archive"
    back = mo.undo(memory, ENV, connect=lambda a: server)
    assert back == {"run": "a1", "put_back_in_the_inbox": 2} and len(server.folders["INBOX"]) == 8
    assert len(server.folders["[Gmail]/All Mail"]) == 2                          # copied out of All Mail, never removed


def test_promotions_the_organizer_filed_are_archived_from_their_folder_too(memory, clock):
    server = Dated(OLD, boxes=(b'(\\HasNoChildren) "/" INBOX', b'(\\HasNoChildren \\Archive) "/" Archive'),
                   header_search=False)
    server.folders["Buddy Promotions"] = {"90": dated("Old Navy <oldnavy@email.oldnavy.example>", "Clearance: 60% off", 40),
                                          "91": dated("Old Navy <oldnavy@email.oldnavy.example>", "New sale: 20% off", 2)}
    got = mo.archive_old(memory, ENV, days=10, connect=lambda a: server, run="a2")
    assert got["archived"]["me@gmail.com"] == {"archived": 4, "from": {"INBOX": 3, "Buddy Promotions": 1}, "to": "Archive"}
    assert server.subjects("Buddy Promotions") == ["New sale: 20% off"]          # a young one stays where it is
    assert mo.undo(memory, ENV, connect=lambda a: server)["put_back_in_the_inbox"] == 4
    assert server.folders["Archive"] == {}                                       # out of a real folder it is moved back
    teach = mo.teach(memory, "bestbuy.example", "keep")
    assert "inbox" in teach.lower() or "keep" in teach.lower()
    again = mo.archive_old(memory, ENV, days=10, connect=lambda a: server)["archived"]["me@gmail.com"]
    assert again["archived"] == 3 and "Black Friday: 40% off TVs" in server.subjects("INBOX")   # a kept sender stays


def test_an_account_with_no_archive_is_left_alone_and_a_refused_move_deletes_nothing(memory, clock):
    bare = Dated(OLD, boxes=(b'(\\HasNoChildren) "/" "INBOX"',))
    assert mo.archive_preview(memory, ENV, connect=lambda a: bare)["accounts"]["me@gmail.com"]["would_archive"] == 0
    said = mo.archive_old(memory, ENV, connect=lambda a: bare)["archived"]["me@gmail.com"]
    assert said == "no archive was found in this account, so nothing was moved" and len(bare.folders["INBOX"]) == 8
    stuck = Dated(OLD)
    stuck.move_ok = False
    said = mo.archive_old(memory, ENV, connect=lambda a: stuck)["archived"]["me@gmail.com"]
    assert "would not move" in said and len(stuck.folders["INBOX"]) == 8 and not hasattr(stuck, "deleted")
    named = Dated(OLD, boxes=(b'(\\HasNoChildren) "/" "INBOX"', b'(\\HasNoChildren) "/" "Archive"'))
    assert mo.archive_box(named) == ("Archive", "archive")                       # a folder simply called Archive


def test_archiving_needs_a_preview_a_yes_and_one_run_by_hand_before_the_nightly_one(memory, clock, monkeypatch):
    server = Dated(OLD)
    monkeypatch.setattr(mo, "_connect", lambda a: server)
    from buddy.email_accounts import email_accounts
    mine = email_accounts(ENV)
    monkeypatch.setattr(mo, "_accounts", lambda env=None, which="": mine)

    def tool(**args):
        return asyncio.run(mo.run_tool(memory, args))
    with pytest.raises(Exception, match="archive_preview first"):
        tool(action="archive")
    with pytest.raises(Exception, match="Archive once by hand first"):
        tool(action="archive_auto", on=True)
    assert not mo.archive_due(memory)
    assert json.loads(tool(action="archive_preview"))["older_than_days"] == 10    # ten days unless the owner says
    asked = []

    class Thrift:
        def ask_action(self, question, go, **more):
            asked.append(question)
            return "asked"
    assert asyncio.run(mo.run_tool(memory, {"action": "archive"}, Thrift())) == "asked"
    assert "older than 10 days" in asked[0] and len(server.folders["INBOX"]) == 8   # asked first: nothing moved yet
    assert json.loads(tool(action="archive"))["archived"]["me@gmail.com"]["archived"] == 3   # nothing is starred here
    assert tool(action="archive_auto", on=True, days=10) == "Promotions older than 10 days will be archived every night."
    assert mo.archive_days(memory) == 10 and mo.archive_due(memory) and mo.status(memory)["archives_promotions_older_than_days"] == 10
    assert mo.archive_nightly(memory, ENV, connect=lambda a: server)["older_than_days"] == 10
    assert tool(action="archive_auto", on=False) == "I'll only archive old promotions when you ask."
    assert not mo.archive_due(memory)
