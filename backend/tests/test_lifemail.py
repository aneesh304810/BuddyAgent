"""Life memory from email: what is read, what is kept, the circle of people, reminders and recall."""

import asyncio
import json
from datetime import datetime, timedelta, timezone

import pytest

from buddy import lifemail
from buddy.connectors.hub import ConnectorHub
from buddy.memory import Memory
from buddy.tools import ToolError, local_now


@pytest.fixture
def memory(tmp_path):
    m = Memory(tmp_path / "buddy.db")
    ConnectorHub(m)
    return m


def mail(memory, n, sender, subject, body="", days_ago=0):
    when = (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()
    cur = memory.db.execute(
        "INSERT INTO records (source, external_id, kind, title, body, start_at, data, synced_at) VALUES "
        "('email', ?, 'email', ?, ?, ?, ?, ?)", (f"m{n}", subject, body, when, json.dumps({"from": sender}), when))
    memory.db.commit()
    return cur.lastrowid


def day(offset):
    return (local_now().date() + timedelta(days=offset)).isoformat()


def answers(*items):
    async def extract(memory, emails):
        subjects = {e["subject"]: n for n, e in enumerate(emails, 1)}
        return {"items": [{**i, "n": subjects[i["subject"]]} for i in items if i["subject"] in subjects]}
    return extract


def item(subject, category, kind, title, **more):
    return {"subject": subject, "keep": True, "category": category, "kind": kind, "title": title, **more}


def test_only_life_email_is_read_not_marketing_or_low_mail(memory):
    mail(memory, 1, "MyChart <noreply@epic.example>", "You have an upcoming visit", "arrival 9:15")
    mail(memory, 2, "Uber <uber@uber.example>", "Your next find, delivered", "Shop now. Unsubscribe")
    mail(memory, 3, "Alibaba <a@alibaba.example>", "Fear of customs?", "Let suppliers handle duties")
    low = mail(memory, 4, "School <news@school.example>", "Weekly backpack and updates", "Dear families")
    memory.db.executescript(__import__("buddy.mailbrain", fromlist=["x"]).SCHEMA_SQL)
    memory.db.execute("INSERT INTO mail_triage (record_id, account, sender, subject, priority, reason, sorted_at) "
                      "VALUES (?, '', '', '', 'low', '', '')", (low,))
    got = lifemail.candidates(memory)
    assert [e["subject"] for e in got] == ["You have an upcoming visit"]
    assert lifemail.candidates(memory) == []  # read once


def test_mail_from_a_confirmed_person_is_always_read(memory):
    lifemail.teach(memory, "Neha Sareen", "wife")
    memory.db.execute("UPDATE mail_circle SET senders = ?", (json.dumps(["neha@example.com"]),))
    mail(memory, 1, "Neha Sareen <neha@example.com>", "dinner saturday", "can you book the place")
    assert [e["from"] for e in lifemail.candidates(memory)] == ["Neha Sareen"]


def test_an_appointment_is_said_once_and_gets_reminders(memory):
    mail(memory, 1, "MyChart <noreply@epic.example>", "You have an upcoming visit", "with Optum, arrival 9:15")
    told = asyncio.run(lifemail.scan(memory, answers(item(
        "You have an upcoming visit", "health", "appointment", "Visit at Optum", date=day(3), time="09:30",
        org="Optum", remember="Visit at Optum; arrive 15 minutes early."))))
    assert len(told) == 1 and "Visit at Optum" in told[0] and "at 09:30" in told[0] and "two hours ahead" in told[0]
    reminders = [r["text"] for r in memory.upcoming_reminders()]
    assert any(r.startswith("Tomorrow at 09:30: Visit at Optum") for r in reminders)
    assert any(r.startswith("In two hours (09:30)") for r in reminders)
    assert "Visit at Optum" in lifemail.briefing_line(memory)
    mail(memory, 2, "MyChart <noreply@epic.example>", "You have an upcoming visit", "reminder: with Optum")
    again = asyncio.run(lifemail.scan(memory, answers(item(
        "You have an upcoming visit", "health", "appointment", "Visit at Optum", date=day(3), time="09:30"))))
    assert again == [] and len(memory.upcoming_reminders()) == 2  # the same visit isn't kept or announced twice


def test_documents_are_kept_with_their_reference_and_can_be_found(memory):
    mail(memory, 1, "Neha Sareen <neha@example.com>", "Fwd: Your eTA for Kenya - ISHAAN NAIR", "Forwarded message")
    told = asyncio.run(lifemail.scan(memory, answers(item(
        "Fwd: Your eTA for Kenya - ISHAAN NAIR", "travel", "document", "Kenya eTA for Ishaan Nair",
        who="Ishaan Nair", reference="9RW5XGM542", expires=day(80),
        remember="Ishaan's Kenya travel authorisation was approved.",
        people=[{"name": "Neha Sareen", "kind": "person", "guess": "forwards the family's travel documents"},
                {"name": "Ishaan Nair", "kind": "person", "guess": "a traveller on the same trip: likely family"}]))))
    assert told == []  # kept quietly: nothing to do today
    found = lifemail.show(memory, query="kenya ishaan")["entries"][0]
    assert found["reference"] == "9RW5XGM542" and found["who"] == "Ishaan Nair" and found["expires"] == day(80)
    assert any("ends on" in r["text"] and "in 30 days" in r["text"] for r in memory.upcoming_reminders())
    from buddy.brain import Brain
    notes = Brain(memory, embed=lambda t: None).notes("life")
    assert "9RW5XGM542" in notes[0]["text"] and notes[0]["title"] == "Travel: Kenya eTA for Ishaan Nair"


def test_people_are_guessed_asked_once_and_confirmed_only_by_the_owner(memory):
    for n in (1, 2):
        mail(memory, n, "Neha Sareen <neha@example.com>", f"Fwd: eTA {n}", "Forwarded message")
    extract = answers(*[item(f"Fwd: eTA {n}", "travel", "document", f"eTA {n}", people=[
        {"name": "Neha Sareen", "kind": "person", "guess": "forwards the family's travel documents: likely spouse"}])
        for n in (1, 2)])
    told = asyncio.run(lifemail.scan(memory, extract))
    assert len(told) == 1 and "Neha Sareen keeps coming up" in told[0] and "Who are they to you?" in told[0]
    who = lifemail.show(memory)["circle"][0]
    assert who["confirmed"] is False and "not confirmed" in who["relation"]  # a guess, shown as one
    assert lifemail.questions(memory) == []  # asked once
    assert "your wife" in lifemail.teach(memory, "Neha", "wife")
    who = lifemail.show(memory)["circle"][0]
    assert who == {"name": "Neha Sareen", "kind": "person", "relation": "wife", "confirmed": True}
    assert "neha@example.com" in lifemail._known_senders(memory)  # her mail is now always read
    from buddy.brain import Brain
    assert Brain(memory, embed=lambda t: None).person("Neha Sareen")["relation"] == "wife"


def test_a_delivery_is_said_and_marketing_the_model_rejects_is_dropped(memory):
    mail(memory, 1, "Nine on the Hudson <n@building.example>", "You have a delivery at the front desk")
    mail(memory, 2, "Global Tree <s@gt.example>", "FollowUp Regarding Your Immigration Enquiry", "I am Sridhar")
    told = asyncio.run(lifemail.scan(memory, answers(
        item("You have a delivery at the front desk", "home", "notice", "A delivery is waiting at the front desk",
             org="Nine on the Hudson"),
        {"subject": "FollowUp Regarding Your Immigration Enquiry", "keep": False, "category": "other",
         "kind": "update", "title": "sales follow-up"})))
    assert told == ["From your building: A delivery is waiting at the front desk."]
    assert len(lifemail.show(memory)["entries"]) == 1


def test_catch_up_reads_older_mail_without_announcing_it_and_forget_deletes(memory):
    mail(memory, 1, "School <o@school.example>", "Virtual Lunch & Learn - Kindergarten", "Dear parents", days_ago=5)
    lifemail.candidates(memory)  # already passed over once
    extract = answers(item("Virtual Lunch & Learn - Kindergarten", "school", "update", "Lunch & Learn on literacy",
                           date=day(4), org="Stevens Cooperative School", remember="Parents are invited."))
    assert asyncio.run(lifemail.scan(memory, extract, since_days=30)) == []
    assert lifemail.show(memory, category="school")["entries"][0]["org"] == "Stevens Cooperative School"
    assert asyncio.run(lifemail.scan(memory, extract, since_days=30)) == []  # not read twice
    assert len(lifemail.show(memory)["entries"]) == 1
    assert lifemail.forget(memory, "lunch learn") == "Forgot 1 entry."
    with pytest.raises(ToolError):
        lifemail.forget(memory, "")


def test_bad_model_output_is_ignored(memory):
    mail(memory, 1, "MyChart <noreply@epic.example>", "You have an upcoming visit")

    async def extract(memory, emails):
        return {"items": [{"n": 9, "keep": True, "category": "health", "kind": "appointment", "title": "x"},
                          {"n": 1, "keep": True, "category": "secrets", "kind": "appointment", "title": "x"},
                          {"n": 1, "keep": True, "category": "health", "kind": "appointment", "title": "Visit",
                           "date": "soon", "time": "25:99"}]}
    assert asyncio.run(lifemail.scan(memory, extract)) == []  # no real date: kept, not announced, no reminder
    assert len(lifemail.show(memory)["entries"]) == 1 and memory.upcoming_reminders() == []


def test_the_tool_shows_and_teaches(memory):
    out = asyncio.run(lifemail.run_tool(memory, {"action": "teach", "name": "Nine on the Hudson",
                                                 "relation": "building", "kind": "organisation"}))
    assert "Nine on the Hudson is your building" in out
    shown = json.loads(asyncio.run(lifemail.run_tool(memory, {"action": "show"})))
    assert shown["circle"][0]["kind"] == "organisation" and shown["entries"] == []
    assert "won't ask" in asyncio.run(lifemail.run_tool(memory, {"action": "ignore", "name": "nine on"}))


# --- attachments, trips, calendar, routines, providers, replies, payments -----------------------------------
from email.message import EmailMessage  # noqa: E402

from buddy import mailfiles  # noqa: E402


def with_files(*files, body="Please see attached.", subject="Fwd: Your eTA"):
    m = EmailMessage()
    m["From"], m["To"], m["Subject"], m["Message-ID"] = "Neha <neha@example.com>", "me@example.com", subject, "<a1@x>"
    m.set_content(body)
    for name, data, kind, inline in files:
        main, sub = kind.split("/")
        m.add_attachment(data, maintype=main, subtype=sub, filename=name,
                         disposition="inline" if inline else "attachment")
    return m


def test_attachments_are_picked_and_logos_are_not():
    pdf, logo, big = b"%PDF-1.4 " + b"x" * 500, b"\x89PNG" + b"x" * 900, b"\x89PNG" + b"x" * 60_000
    got = mailfiles.parts(with_files(("TA-78GDEK622.pdf", pdf, "application/pdf", False),
                                     ("logo.png", logo, "image/png", True),
                                     ("run.exe", b"MZ" * 50, "application/octet-stream", False)))
    assert [p["name"] for p in got] == ["TA-78GDEK622.pdf"]
    # an invitation sent as one picture: the email has no words, so the picture is the message
    party = mailfiles.parts(with_files(("party.png", big, "image/png", True), ("pixel.png", logo, "image/png", True),
                                       body="", subject="Holiday party"))
    assert [p["name"] for p in party] == ["party.png"]
    assert mailfiles.names(with_files(("a.pdf", pdf, "application/pdf", False))) == ["a.pdf"]


class Imap:
    """Answers a Message-ID search with one stored message."""

    def __init__(self, message, log):
        self.message, self.log = message, log

    def select(self, folder, readonly=False):
        self.log.append(("select", folder, readonly))
        return "OK", [b"1"]

    def search(self, charset, *criteria):
        self.log.append(("search", criteria))
        return "OK", [b"7"]

    def fetch(self, num, what):
        return "OK", [(b"7 (BODY[] {1}", self.message.as_bytes()), b")"]

    def logout(self):
        pass


def test_an_attachment_is_fetched_read_only_read_into_the_library_and_kept_once(memory, monkeypatch):
    monkeypatch.setenv("BUDDY_EMAIL_ADDRESS", "me@example.com")
    monkeypatch.setenv("BUDDY_EMAIL_APP_PASSWORD", "app pass")
    rid = mail(memory, 1, "Neha <neha@example.com>", "Fwd: Your eTA", "see attached")
    memory.db.execute("UPDATE records SET external_id = '<a1@x>', data = ? WHERE id = ?",
                      (json.dumps({"from": "Neha <neha@example.com>", "account": "me@example.com"}), rid))
    message = with_files(("TA-78GDEK622.pdf", b"%PDF-1.4 " + b"x" * 500, "application/pdf", False))
    log = []

    async def understand(row, sample, image, pdf):
        return {"title": "Kenya eTA", "doc_type": "travel authorisation", "summary": "Approved eTA, valid to March.",
                "facts": [{"fact": "The eTA is valid for 90 days", "important": True}], "entities": [],
                "text": "ELECTRONIC TRAVEL AUTHORISATION approved"}
    from buddy import library
    from buddy.library import Library
    monkeypatch.setattr(library, "_pdf_text", lambda data: "")  # a scanned PDF: Claude reads the pages
    lib = Library(memory, understand=understand)
    got = asyncio.run(mailfiles.read(memory, rid, vault=True, connect=lambda account: Imap(message, log),
                                     library=lib))
    assert got[0]["title"] == "Kenya eTA" and got[0]["important"] == ["The eTA is valid for 90 days"]
    assert ("select", "INBOX", True) in log and log[1] == ("search", ("HEADER", "Message-ID", '"<a1@x>"'))
    item = memory.db.execute("SELECT vault, status, note FROM library_items").fetchone()
    assert item["vault"] == 1 and item["status"] == "ready" and "Fwd: Your eTA" in item["note"]
    again = asyncio.run(mailfiles.read(memory, rid, connect=lambda account: 1 / 0, library=lib))
    assert again[0]["summary"] == "Approved eTA, valid to March."  # remembered: the mailbox isn't asked twice


def test_what_the_attachment_says_is_added_to_the_entry_and_fills_in_the_date(memory):
    mail(memory, 1, "Nine on the Hudson <n@building.example>", "Notification: Holiday party", "resident event")
    asyncio.run(lifemail.scan(memory, answers(item("Notification: Holiday party", "event", "document",
                                                   "Holiday party", org="Nine on the Hudson"))))
    entry = memory.db.execute("SELECT * FROM mail_life").fetchone()
    assert entry["files"] == "none" and not entry["dated"]  # no mailbox in this test: nothing could be opened
    memory.db.execute("UPDATE mail_life SET files = 'pending'")

    async def reader(memory, record_id, vault=False):
        return [{"name": "party.png", "item_id": "abc", "title": "Holiday party invitation",
                 "summary": "Residents' holiday party in the lounge.", "important": []}]

    async def extract(memory, emails):
        assert "The attachment says" in emails[0]["text"]
        return {"items": [{"n": 1, "keep": True, "category": "event", "kind": "document", "title": "Holiday party",
                           "date": day(10), "time": "18:00"}]}
    orig = lifemail._extract
    lifemail._extract = extract
    try:
        told = asyncio.run(lifemail.read_files(memory, reader=reader))
    finally:
        lifemail._extract = orig
    assert "I read the attachment on \"Holiday party\"" in told[0] and "Shall I add it to your calendar?" in told[0]
    entry = lifemail.show(memory)["entries"][0]
    assert entry["day"] == day(10) and entry["time"] == "18:00" and "Residents' holiday party" in entry["remember"]
    assert entry["attachments_read"] == ["Holiday party invitation (library id abc)"]


def test_trips_group_documents_by_place_and_say_what_is_missing(memory):
    for n, who in enumerate(("Neha Sareen", "Ishaan Nair", "Aneesh Nair"), 1):
        mail(memory, n, "Neha <neha@example.com>", f"Fwd: eTA {who}", "Forwarded message")
    mail(memory, 9, "Clinic <c@clinic.example>", "Fwd: yellow fever certificate Neha", "Forwarded message")
    asyncio.run(lifemail.scan(memory, answers(
        *[item(f"Fwd: eTA {who}", "travel", "document", f"Kenya eTA for {who}", who=who, place="Kenya",
               reference="9RW5XGM542") for who in ("Neha Sareen", "Ishaan Nair", "Aneesh Nair")],
        item("Fwd: yellow fever certificate Neha", "travel", "document", "Yellow fever certificate for Neha Sareen",
             who="Neha Sareen", place="Kenya"))))
    trip = lifemail.trips(memory)[0]
    assert trip["place"] == "Kenya" and trip["travellers"] == ["Aneesh Nair", "Ishaan Nair", "Neha Sareen"]
    assert len(trip["documents"]) == 4 and trip["documents"][0]["reference"] == "9RW5XGM542"
    assert "Ishaan Nair has fewer documents than the others" in trip["to_check"]
    assert any("no flight" in t for t in trip["to_check"]) and any("no hotel" in t for t in trip["to_check"])
    assert "Kenya" in asyncio.run(lifemail.run_tool(memory, {"action": "trips"}))


def event_on_calendar(memory, title, offset, hour):
    tz = local_now().tzinfo
    at = datetime.combine(local_now().date() + timedelta(days=offset), datetime.min.time(), tz).replace(hour=hour)
    memory.db.execute("INSERT INTO records (source, external_id, kind, title, body, start_at, data, synced_at) VALUES "
                      "('calendar', ?, 'event', ?, '', ?, '{}', '')", (title, title, at.astimezone(timezone.utc).isoformat()))


def test_a_dated_school_event_offers_the_calendar_and_flags_a_clash(memory):
    event_on_calendar(memory, "Team offsite", 4, 12)
    mail(memory, 1, "Stevens <s@school.example>", "Virtual Lunch & Learn - Kindergarten", "Dear parents")
    told = asyncio.run(lifemail.scan(memory, answers(item(
        "Virtual Lunch & Learn - Kindergarten", "school", "update", "Lunch & Learn on literacy", date=day(4),
        time="12:30", org="Stevens Cooperative School"))))
    assert told[0].startswith("From Stevens Cooperative School: Lunch & Learn on literacy")
    assert 'clashes with "Team offsite"' in told[0] and "Shall I add it to your calendar?" in told[0]
    event_on_calendar(memory, "Optum visit", 6, 9)
    mail(memory, 2, "MyChart <m@epic.example>", "You have an upcoming visit", "Optum")
    told = asyncio.run(lifemail.scan(memory, answers(item(
        "You have an upcoming visit", "health", "appointment", "Visit at Optum", date=day(6), time="09:30"))))
    assert "already on your calendar" in told[0] and "Shall I add" not in told[0]


def test_childcare_times_become_a_routine_and_an_odd_day_is_flagged(memory):
    for n in range(1, 8):
        mail(memory, n, "Procare <p@procare.example>", f"Daily summary {n} - Ishaan Nair", "Sign-Ins", days_ago=8 - n)
    summaries = [item(f"Daily summary {n} - Ishaan Nair", "childcare", "update", f"Daily summary {n}",
                      who="Ishaan Nair", org="Apple Montessori", sign_in="08:30",
                      sign_out="15:05" if n == 7 else "17:30") for n in range(1, 8)]
    assert asyncio.run(lifemail.scan(memory, answers(*summaries))) == []  # a daily summary is kept quietly
    got = lifemail.routines(memory)[0]
    assert got["who"] == "Ishaan Nair" and got["usual_drop_off"] == "08:30" and got["usual_pick_up"] == "17:30"
    assert len(got["unusual"]) == 1 and "sign out at 15:05 (usually 17:30)" in got["unusual"][0]


def test_the_provider_book_keeps_what_the_organisations_email_gave(memory):
    mail(memory, 1, "Optum <o@optum.example>", "Your visit appointment details", "Call us at 201-555-0100")
    asyncio.run(lifemail.scan(memory, answers(item(
        "Your visit appointment details", "health", "update", "Visit details", org="Optum Hoboken",
        contact={"phone": "201-555-0100", "address": "1 River St, Hoboken NJ", "website": ""}))))
    assert lifemail.providers(memory) == [{"name": "Optum Hoboken", "what": "", "phone": "201-555-0100",
                                           "address": "1 River St, Hoboken NJ"}]


def test_a_question_from_a_confirmed_person_offers_a_reply_and_a_strangers_does_not(memory):
    lifemail.teach(memory, "Neha Sareen", "wife")
    memory.db.execute("UPDATE mail_circle SET senders = ?", (json.dumps(["neha@example.com"]),))
    mail(memory, 1, "Neha Sareen <neha@example.com>", "saturday", "can you book the dentist for Ishaan")
    mail(memory, 2, "Abass <a@recruit.example>", "Fwd: opportunity", "can you send your resume")
    told = asyncio.run(lifemail.scan(memory, answers(
        item("saturday", "family", "update", "Neha asks about the dentist", asks="Can you book the dentist for Ishaan?"),
        item("Fwd: opportunity", "work", "update", "Recruiter outreach", asks="Can you send your resume?"))))
    assert told == ["Neha Sareen asks: Can you book the dentist for Ishaan? Shall I draft a reply?"]


def test_a_payment_email_settles_the_open_bill(memory):
    from buddy.money import Money
    money = Money(memory)
    money.add_bill("American Express", 389.44, "USD", day(5), source="t:amex")
    mail(memory, 1, "American Express <a@amex.example>", "We processed your payment", "Thank you")
    asyncio.run(lifemail.scan(memory, answers(item("We processed your payment", "money", "payment",
                                                   "Amex payment processed", amount=389.44, payee="American Express"))))
    assert money.open_bills(days=30) == []
    entry = lifemail.show(memory)["entries"][0]
    assert entry["amount"] == 389.44 and "settled the open American Express bill" in entry["remember"]
