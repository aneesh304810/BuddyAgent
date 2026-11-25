"""The email map (who writes, in which category, what the owner chose) and payments with people from email."""

import asyncio
import json
from datetime import date, datetime, timezone
from email.message import EmailMessage
from types import SimpleNamespace

import pytest

from buddy import dealmail, mailbrain, mailmap, mailorganizer, paymail
from buddy.connectors.hub import ConnectorHub
from buddy.memory import Memory

ENV = {"BUDDY_EMAIL_ADDRESS": "me@gmail.com", "BUDDY_EMAIL_APP_PASSWORD": "x",
       "BUDDY_DEALS_EMAIL_ADDRESS": "deals@gmail.com", "BUDDY_DEALS_EMAIL_APP_PASSWORD": "z"}


@pytest.fixture
def memory():
    m = Memory(":memory:")
    ConnectorHub(m)  # the email records table
    return m


# --- categories, by rules on the sender and the subject ------------------------------------------------------------
@pytest.mark.parametrize("address,name,subject,bulk,want", [
    ("venmo@venmo.com", "Venmo", "You paid Peiji Shi $38.00", False, "payments"),
    ("no.reply.alerts@chase.com", "Chase", "You sent $500.00 to NEHA S with Zelle", False, "payments"),
    ("AmericanExpress@welcome.aexp.com", "Amex Offers from American Express", "Aneesh, your new Amex Offers are inside!",
     True, "card_offers"),
    ("onlinestatements@americanexpress.com", "American Express", "Your statement is ready", True, "bank"),
    ("no-reply@accounts.google.com", "Google", "Security alert: new sign-in on Windows", True, "security"),
    ("cvs@pharmacy.cvs.com", "CVS Pharmacy", "Your prescription is ready", True, "health"),
    ("deltaairlines@t.delta.com", "Delta Air Lines", "Your trip to Lima", True, "travel"),
    ("shipment-tracking@amazon.com", "Amazon.com", "Your package was delivered", False, "orders"),
    ("verizon@email.verizon.com", "Verizon", "Your bill is ready", True, "bills"),
    ("notification@facebookmail.com", "Facebook", "Priya commented on your post", True, "social"),
    ("deals@shop.example", "Shop", "50% off everything this weekend", True, "deals"),
    ("hello@substack.example", "Weekly Digest", "This week's top stories", True, "newsletters"),
    ("priya.s@gmail.com", "Priya", "Dinner Sunday?", False, "people"),
    ("raj@acme-corp.example", "Raj Mehta", "Quick question", False, "people"),
    ("noreply@portal.example", "Portal", "Your account was updated", False, "other"),
])
def test_each_sender_lands_in_its_category(address, name, subject, bulk, want):
    assert mailmap.category_of(address, name, subject, bulk) == want


# --- the scan: headers only, read-only -------------------------------------------------------------------------------
def head(sender, subject, when, bulk=True):
    m = EmailMessage()
    m["From"], m["Subject"], m["Date"] = sender, subject, when
    if bulk:
        m["List-Unsubscribe"] = "<https://example.com/unsub>"
    m.set_content("The body, which the scan never asks for.")
    return m


class HeaderBox:
    """An inbox that hands out headers in batches, and fails the test if a body is asked for."""

    def __init__(self, messages):
        self.messages, self.readonly, self.asked = messages, None, []

    def select(self, box, readonly=False):
        self.readonly = readonly
        return "OK", [str(len(self.messages)).encode()]

    def uid(self, cmd, *args):
        if cmd == "search":
            if args[1].startswith("UID "):
                start = int(args[1].split()[1].split(":")[0])
                return "OK", [" ".join(str(i + 1) for i in range(len(self.messages)) if i + 1 >= start).encode()]
            return "OK", [" ".join(str(i + 1) for i in range(len(self.messages))).encode()]
        assert cmd == "fetch" and "HEADER.FIELDS" in args[1] and "PEEK" in args[1], args  # never a body
        self.asked.append(args[0])
        out = []
        for uid in args[0].split(","):
            out += [(f"{uid} (UID {uid} BODY[HEADER.FIELDS] {{1}}".encode(), bytes(self.messages[int(uid) - 1])), b")"]
        return "OK", out

    def logout(self):
        pass


MAIL = [
    head("Venmo <venmo@venmo.com>", "You paid Peiji Shi $38.00", "Mon, 23 Nov 2026 10:00:00 +0000", bulk=False),
    head("Venmo <venmo@venmo.com>", "Neha paid you $20.00", "Tue, 24 Nov 2026 10:00:00 +0000", bulk=False),
    head("Shop <deals@shop.example>", "50% off everything", "Tue, 24 Nov 2026 09:00:00 +0000"),
    head("Priya <priya.s@gmail.com>", "Dinner Sunday?", "Sun, 22 Nov 2026 18:00:00 +0000", bulk=False),
    head("CVS Pharmacy <cvs@pharmacy.cvs.com>", "Your prescription for Lipitor is ready", "Sat, 21 Nov 2026 08:00:00 +0000"),
]


def test_the_scan_reads_headers_only_and_keeps_subjects_only_where_they_are_not_personal(memory):
    box = HeaderBox(MAIL)
    got = mailmap.scan(memory, ENV, connect=lambda account: box, today=date(2026, 11, 25))
    assert got == {"senders": 4, "emails": 5, "accounts": 1, "failed": []} and box.readonly is True
    view = mailmap.view(memory)
    cats = {c["key"]: c for c in view["categories"]}
    assert set(cats) == {"payments", "deals", "people", "health"}
    venmo = cats["payments"]["senders"][0]
    assert (venmo["address"], venmo["emails"], venmo["subjects"]) == ("venmo@venmo.com", 2, [])  # not bulk: no subject
    assert cats["deals"]["senders"][0]["subjects"] == ["50% off everything"]
    assert cats["health"]["senders"][0]["subjects"] == [] and "Lipitor" not in json.dumps(view)  # health keeps none
    assert cats["people"]["senders"][0]["subjects"] == [] and "Dinner" not in json.dumps(view)
    assert view["new"] == [] and view["scanned_at"]


def test_the_nightly_look_adds_new_senders_as_new(memory):
    assert mailmap.nightly(memory, ENV, connect=lambda account: HeaderBox(MAIL)) == ""  # nothing before the first scan
    mailmap.scan(memory, ENV, connect=lambda account: HeaderBox(MAIL), today=date(2026, 11, 25))
    more = MAIL + [head("Chase <no.reply.alerts@chase.com>", "You sent $500.00 to NEHA S with Zelle",
                        "Wed, 25 Nov 2026 07:00:00 +0000", bulk=False)]
    said = mailmap.nightly(memory, ENV, connect=lambda account: HeaderBox(more))
    assert said == "email map: 1 new emails, 1 new senders"
    view = mailmap.view(memory)
    assert [n["address"] for n in view["new"]] == ["no.reply.alerts@chase.com"] and view["new"][0]["title"] == \
        "Payments to people"
    assert mailmap.due(memory, datetime(2026, 11, 26, 2, 0)) and not mailmap.due(memory, datetime(2026, 11, 26, 9, 0))


def test_a_scan_without_accounts_says_so(memory):
    with pytest.raises(Exception, match="No email account"):
        mailmap.scan(memory, {})


# --- the owner's choices -------------------------------------------------------------------------------------------
def test_a_senders_choice_wins_over_its_categorys(memory):
    mailmap.scan(memory, ENV, connect=lambda account: HeaderBox(MAIL), today=date(2026, 11, 25))
    mailmap.choose(memory, "category:payments", importance="urgent")
    assert mailmap.importance(memory, "Venmo <venmo@venmo.com>") == "urgent"
    mailmap.choose(memory, "sender:venmo@venmo.com", importance="low")
    assert mailmap.importance(memory, "venmo@venmo.com") == "low"
    assert mailmap.importance(memory, "someone@nowhere.example") == ""          # Buddy decides
    assert mailmap.use_on(memory, "venmo@venmo.com", "payments")                  # on until switched off
    mailmap.choose(memory, "category:payments", use="payments", on=False)
    assert not mailmap.use_on(memory, "venmo@venmo.com", "payments")
    mailmap.choose(memory, "sender:deals@shop.example", category="people")
    assert {c["key"] for c in mailmap.view(memory)["categories"]} >= {"people"}
    assert [s["address"] for c in mailmap.view(memory)["categories"] if c["key"] == "people"
            for s in c["senders"]] == ["deals@shop.example", "priya.s@gmail.com"]
    with pytest.raises(Exception, match="Importance"):
        mailmap.choose(memory, "category:payments", importance="very")
    with pytest.raises(Exception, match="category"):
        mailmap.choose(memory, "category:nonsense", importance="low")


def test_keep_in_the_inbox_teaches_the_organizer(memory):
    mailmap.scan(memory, ENV, connect=lambda account: HeaderBox(MAIL), today=date(2026, 11, 25))
    mailmap.choose(memory, "category:deals", use="keep", on=True)
    assert mailorganizer.senders(memory)["deals@shop.example"] == "keep"
    mailmap.choose(memory, "category:deals", use="keep", on=False)
    assert "deals@shop.example" not in mailorganizer.senders(memory)


def test_an_offers_reader_switched_off_for_a_sender_takes_nothing_from_it(memory):
    amex = EmailMessage()
    amex["From"], amex["Subject"], amex["List-Unsubscribe"] = "American Express <offers@americanexpress.example>", "x", "<u>"
    amex.set_content("Spend $250 or more, get $50 back at Best Buy. Expires 12/31")

    class Box:
        def select(self, box, readonly=False):
            return "OK", [b"1"]

        def uid(self, cmd, *args):
            if cmd == "search":
                return "OK", [b"1"]
            return "OK", [(b"x", amex.as_bytes())]

        def logout(self):
            pass
    mailmap.choose(memory, "sender:offers@americanexpress.example", use="offers", on=False)
    dealmail.run_all(memory, ENV, lambda account: Box())
    assert dealmail.offers(memory) == []
    mailmap.choose(memory, "sender:offers@americanexpress.example", use="offers", on=True)
    memory.set(f"{dealmail.LAST_KEY}:me@gmail.com", "")
    dealmail.run_all(memory, ENV, lambda account: Box())
    assert [o["merchant"] for o in dealmail.offers(memory)] == ["Best Buy"]


# --- the email manager uses the owner's importance --------------------------------------------------------------------
def test_the_email_manager_uses_the_chosen_importance(memory, monkeypatch):
    from buddy.budgets import BudgetStop
    told = []

    async def notify(kind, text, **_):
        told.append((kind, text))
        return True
    monkeypatch.setattr(mailbrain, "in_quiet_hours", lambda: False)
    manager = mailbrain.MailManager(memory, notify, mailbox_factory=lambda account, env: None)
    mailmap._ready(memory)
    memory.db.execute("INSERT INTO mail_senders (address, category) VALUES ('venmo@venmo.com', 'payments'), "
                      "('alerts@chase.com', 'bank')")
    mailmap.choose(memory, "category:payments", importance="urgent")
    mailmap.choose(memory, "sender:alerts@chase.com", importance="ignore")
    now = datetime.now(timezone.utc).isoformat()
    for n, (sender, subject, body) in enumerate([
            ("Venmo <venmo@venmo.com>", "You paid Peiji Shi $38.00",
             "From: Venmo <venmo@venmo.com>\nYou paid Peiji Shi\n$38.00\nLokal\nSee transaction"),
            ("Chase <alerts@chase.com>", "Payment due tomorrow", "From: Chase\nPay now")]):
        memory.db.execute("INSERT INTO records (source, external_id, kind, title, body, start_at, data, synced_at) "
                          "VALUES ('email', ?, 'email', ?, ?, ?, ?, ?)",
                          (f"<m{n}@x>", subject, body, now, json.dumps({"from": sender, "account": "me@gmail.com"}), now))

    class Paused:  # background AI work is paused: plain rules, and the owner's choices
        beta = SimpleNamespace(messages=SimpleNamespace(create=None))

    async def refuse(**kw):
        raise BudgetStop("Background AI work is paused until you approve this month's pace.")
    Paused.beta.messages.create = refuse
    monkeypatch.setattr(mailbrain, "client", Paused)
    assert asyncio.run(manager.sort_new()) == 2
    got = {i["subject"]: i["priority"] for i in manager.important(include_low=True)}
    assert got == {"You paid Peiji Shi $38.00": "urgent", "Payment due tomorrow": "low"}  # "ignore" is low
    assert told and "Peiji" in told[0][1] and "Payment due" not in told[0][1]
    pay = paymail.payments(memory)["payments"]  # read while Claude was paused
    assert [(p["person"], p["amount"], p["note"], p["direction"]) for p in pay] == [("Peiji Shi", 38.0, "Lokal", "out")]


# --- payments with people ------------------------------------------------------------------------------------------
@pytest.mark.parametrize("sender,subject,body,want", [
    ("Venmo <venmo@venmo.com>", "You paid Peiji Shi $38.00", "You paid Peiji Shi\n$38.00\nLokal\nSee transaction",
     ("Venmo", "out", "Peiji Shi", 38.0, "Lokal")),
    ("Venmo <venmo@venmo.com>", "Neha Sareen paid you $1,200.00", "", ("Venmo", "in", "Neha Sareen", 1200.0, "")),
    ("Venmo <venmo@venmo.com>", "You completed Raj's $15.50 charge request", "", ("Venmo", "out", "Raj", 15.5, "")),
    ("Chase <no.reply.alerts@chase.com>", "You sent $500.00 to NEHA S", "Your Zelle® payment was sent",
     ("Zelle", "out", "Neha S", 500.0, "")),
    ("Bank of America <onlinebanking@ealerts.bankofamerica.com>", "You received $75.00 from JOHN D with Zelle", "",
     ("Zelle", "in", "John D", 75.0, "")),
    ("Cash App <cash@square.com>", "You sent $25 to Maria", "", ("Cash App", "out", "Maria", 25.0, "")),
    ("PayPal <service@paypal.com>", "Mark Lee sent you $40.00 USD", "", ("PayPal", "in", "Mark Lee", 40.0, "")),
])
def test_payment_emails_are_read(sender, subject, body, want):
    got = paymail.payment_in(sender, subject, body)
    assert (got["app"], got["direction"], got["person"], got["amount"], got["note"]) == want


@pytest.mark.parametrize("sender,subject", [
    ("Chase <no.reply.alerts@chase.com>", "You sent $500.00 to NEHA S"),           # a bank, but no word of Zelle
    ("Shop <deals@shop.example>", "You paid less: $38.00 off"),                     # not a payment app
    ("Venmo <venmo@venmo.com>", "Your Venmo statement is ready"),                   # an app, not a payment
    ("Friend <a@gmail.com>", "You paid Peiji Shi $38.00"),                          # a forward from a person
])
def test_other_emails_are_not_payments(sender, subject):
    assert paymail.payment_in(sender, subject, "") is None


def test_payments_are_kept_once_and_found_in_older_email(memory):
    now = datetime.now(timezone.utc).isoformat()
    rows = []
    for n, subject in enumerate(["You paid Peiji Shi $38.00", "You paid Peiji Shi $38.00", "Neha paid you $20.00"]):
        memory.db.execute("INSERT INTO records (source, external_id, kind, title, body, start_at, data, synced_at) "
                          "VALUES ('email', ?, 'email', ?, '', ?, ?, ?)",
                          (f"<p{n}@x>", subject, now, json.dumps({"from": "Venmo <venmo@venmo.com>"}), now))
    assert paymail.backfill(memory) == 2 and paymail.backfill(memory) == 0   # the same payment in two inboxes: once
    got = paymail.payments(memory)
    assert (got["paid"], got["received"]) == (38.0, 20.0)
    assert [p["person"] for p in got["people"]] == ["Peiji Shi", "Neha"]
    assert rows == []


# --- the page's endpoints ------------------------------------------------------------------------------------------
def test_the_email_page_and_the_payments_are_for_the_owner_only(monkeypatch):
    from fastapi.testclient import TestClient

    from buddy import server
    monkeypatch.setattr(server, "authorized", lambda t: t == "s3cret")
    ok = {"Authorization": "Bearer s3cret"}
    mailmap.forget(server.memory)
    with TestClient(server.app) as client:
        assert client.get("/api/mail/map").status_code == 401
        assert client.get("/api/money/payments").status_code == 401
        view = client.get("/api/mail/map", headers=ok).json()
        assert view["categories"] == [] and view["importance"]["urgent"] == "Tell me now"
        assert client.post("/api/mail/map/choose", json={"target": "category:payments", "importance": "urgent"},
                           headers=ok).json()["importance"] == "urgent"
        assert client.post("/api/mail/map/choose", json={"target": "category:nope"}, headers=ok).status_code == 400
        assert client.get("/api/money/payments", headers=ok).json()["days"] == 90
    mailmap.forget(server.memory)
