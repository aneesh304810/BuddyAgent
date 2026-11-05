"""Credit score and report watch: read from monitoring emails and notifications, alerts once, never acts."""

import asyncio
import json

import pytest

from buddy import credit
from buddy.connectors.base import Connector, Record
from buddy.connectors.hub import ConnectorHub
from buddy.credit import Credit
from buddy.memory import Memory
from buddy.tools import ToolError


class Fake(Connector):
    name, title, description = "fake", "Fake", "test"
    required = {"FAKE_KEY": "a key"}

    def __init__(self, env=None):
        super().__init__(env)
        self.items = []

    async def fetch(self):
        return self.items


def synced(mem, records):
    from datetime import datetime, timezone
    hub = ConnectorHub(mem, env={"FAKE_KEY": "x"}, registry={"fake": Fake})
    hub.connectors["fake"].items = records
    asyncio.run(hub.sync("fake"))
    return datetime.now(timezone.utc)


def rec(i, kind, title, body, data):
    from datetime import datetime, timezone
    return Record(external_id=str(i), kind=kind, title=title, body=body, start_at=datetime.now(timezone.utc), data=data)


def test_monitoring_emails_and_credit_karma_notifications_are_read_once():
    mem = Memory(":memory:")
    synced(mem, [
        rec(1, "email", "Your credit score changed", "Your Experian FICO 8 score is 781, down 22 points.",
            {"from": "Chase Credit Journey <no-reply@chase.com>"}),
        rec(2, "notification", "Credit Karma: New hard inquiry", "Capital One checked your TransUnion report",
            {"app_name": "Credit Karma"}),
        rec(3, "email", "Your weekly grocery deals", "Save on apples", {"from": "Shop <a@shop.com>"})])
    asked = []

    async def ask(memory, prompt):
        asked.append(prompt)
        if "no-reply@chase.com" in prompt:
            return {"credit_email": True, "score": 781, "bureau": "Experian", "model": "FICO 8", "as_of": "2026-11-04",
                    "service": "Chase Credit Journey", "events": []}
        return {"credit_email": True, "score": None, "bureau": "TransUnion", "service": "Credit Karma",
                "events": [{"kind": "hard_inquiry", "day": "2026-11-03", "lender": "Capital One", "detail": "card"}]}
    c = Credit(mem, ask=ask)
    c.add_score(803, "Experian", "FICO 8", "2026-10-04")
    asyncio.run(c.scan_emails())
    assert len(asked) == 2 and not any("grocery" in p for p in asked)          # the shop email isn't read
    asyncio.run(c.scan_emails())
    assert len(asked) == 2                                                      # each read once
    assert c.latest("Experian")["score"] == 781
    alerts = c.alerts()
    texts = " ".join(a["text"] for a in alerts)
    assert "new hard inquiry from Capital One on your TransUnion report" in texts and "Was this you?" in texts
    assert "Credit Karma notification" in texts and "experian.com/freeze" in texts and "dropped 22 points to 781" in texts
    for a in alerts:
        c.told(a)
    assert c.alerts() == []


def test_the_user_answers_was_this_you():
    mem = Memory(":memory:")
    c = Credit(mem)
    c.add_event("hard_inquiry", "2026-11-03", "Capital One", "TransUnion", "", "Credit Karma notification")
    ev = c.events()[0]
    assert "was you" in credit.run_tool(mem, {"action": "it_was_me", "event_id": ev["id"]})
    out = credit.run_tool(mem, {"action": "not_me", "event_id": ev["id"]})
    assert "transunion.com/credit-freeze" in out and "identitytheft.gov" in out
    assert c.events()[0]["status"] == "not_mine"


def test_scores_are_checked_and_shown_on_the_dashboard():
    mem = Memory(":memory:")
    with pytest.raises(ToolError):
        credit.run_tool(mem, {"action": "add_score", "score": 1200})
    assert "Saved: 782 on Experian" in credit.run_tool(mem, {"action": "add_score", "score": 782, "bureau": "experian"})
    dash = json.loads(credit.run_tool(mem, {"action": "overview"}))
    assert dash["latest"][0]["score"] == 782 and dash["latest"][0]["bureau"] == "Experian"
    from buddy import review
    assert review.dashboard(mem)["credit"]["latest"][0]["score"] == 782


def test_a_new_card_at_a_connected_bank_is_a_new_account_but_the_first_look_only_learns():
    from buddy.plaid import Plaid
    mem = Memory(":memory:")
    Plaid(mem, env={})
    mem.db.execute("INSERT INTO plaid_items (item_id, access_token, institution, added_at, env) VALUES "
                   "('i1', 't', 'Chase', '2026-01-01', 'production')")
    mem.db.execute("INSERT INTO plaid_accounts (account_id, item_id, name, mask, type) VALUES ('a1','i1','Freedom','3073','credit')")
    c = Credit(mem)
    c.watch_connected_cards()
    assert c.events() == []
    mem.db.execute("INSERT INTO plaid_accounts (account_id, item_id, name, mask, type) VALUES ('a2','i1','Sapphire','9911','credit')")
    c.watch_connected_cards()
    ev = c.events()
    assert len(ev) == 1 and ev[0]["kind"] == "new_account" and "9911" in ev[0]["detail"]


def test_a_teaser_email_asks_to_open_the_app_and_read_my_credit_reads_it():
    mem = Memory(":memory:")
    synced(mem, [rec(1, "email", "Something changed on your credit report", "Log in to Credit Karma to see what changed.",
                     {"from": "Credit Karma <notifications@creditkarma.com>"})])

    async def ask(memory, prompt):
        if "credit app" in prompt and "Screen:" in prompt:
            assert "TransUnion 742" in prompt
            return {"scores": [{"score": 742, "bureau": "TransUnion", "model": "VantageScore 3.0"}],
                    "events": [{"kind": "hard_inquiry", "day": "2026-11-02", "lender": "Discover", "bureau": "TransUnion"}]}
        return {"credit_email": True, "service": "Credit Karma", "teaser": True,
                "teaser_says": "something changed on your TransUnion report", "events": []}
    c = Credit(mem, ask=ask)
    asyncio.run(c.scan_emails())
    alerts = c.alerts()
    assert len(alerts) == 1 and 'say "Buddy, read my credit"' in alerts[0]["text"]
    c.told(alerts[0])

    sent = []

    async def phone(msg):
        sent.append(msg)
        return {"app": "com.creditkarma.mobile", "can_scroll": False,
                "nodes": [{"kind": "text", "t": "TransUnion 742"}, {"kind": "text", "t": "Hard inquiry: Discover"}]}
    out = asyncio.run(c.read_screen(phone))
    assert "Saved: 742 on TransUnion" in out and "1 new item" in out
    assert all(m["action"] in ("read", "scroll") for m in sent)       # never a fill or a tap
    texts = " ".join(a["text"] for a in c.alerts())
    assert "new hard inquiry from Discover" in texts and "Credit Karma app" in texts


def test_read_my_credit_with_another_app_in_front_says_what_to_open():
    async def phone(msg):
        return {"error": "not_browser", "app": "com.whatsapp"}
    out = asyncio.run(Credit(Memory(":memory:")).read_screen(phone))
    assert "Open your credit app" in out and "com.whatsapp" in out


REPORT = {
    "bureaus": ["TransUnion"], "scores": [{"score": 742, "bureau": "TransUnion", "model": "VantageScore 3.0"}],
    "factors": [{"name": "Card use", "value": "34%", "rating": "Fair"}],
    "accounts": [
        {"lender": "American Express", "type": "credit card", "open": True, "opened": "2019-04", "limit": 20000,
         "balance": 7400, "on_time": "100%", "status": "current"},
        {"lender": "Chase", "type": "credit card", "open": True, "opened": "2012-08", "limit": 10000, "balance": 900,
         "on_time": "99%", "late": "1 x 30 days, Mar 2024", "status": "current"},
        {"lender": "Chase", "type": "mortgage", "open": True, "opened": "2021-06", "limit": None, "balance": 410000,
         "on_time": "100%", "status": "current"}],
    "inquiries": [{"lender": "Discover", "day": "2026-09-14", "bureau": "TransUnion"},
                  {"lender": "Capital One", "day": "2026-03-02", "bureau": "TransUnion"}],
    "negatives": []}


def test_a_detailed_report_from_the_credit_app_lands_in_reports():
    mem = Memory(":memory:")
    sent = []

    async def phone(msg):
        sent.append(msg)
        return {"app": "com.creditkarma.mobile", "can_scroll": len(sent) < 4,
                "nodes": [{"kind": "text", "t": f"TransUnion report screen {len(sent)} American Express 7,400"}]}

    async def ask(memory, prompt):
        assert "screen 1" in prompt and "screen 4" in prompt   # every screen read, scrolling down
        return REPORT
    out = asyncio.run(Credit(mem).full_report(phone, ask_report=ask))
    assert "742" in out and "cards at 28% of limits" in out and "2 hard inquiries" in out
    assert all(m["action"] in ("read", "scroll") for m in sent)
    job = mem.db.execute("SELECT kind, title, report FROM jobs").fetchone()
    assert job["kind"] == "credit_report"
    report = job["report"]
    assert "| American Express | credit card | 2019-04 | $20,000 | $7,400 | 37% |" in report
    assert "| Discover | 2026-09-14 | TransUnion | 2028-09-14 |" in report
    assert "American Express: 37% used" in report and "goodwill letter" in report and "2012-08" in report
    kinds = {e["kind"] for e in Credit(mem).events()}
    assert kinds == {"hard_inquiry"}


def test_model_answers_are_read_whole_for_every_kind_of_reply():
    from buddy.benefits import last_json
    assert last_json('Here it is:\n{"credit_email": true, "events": [{"kind": "hard_inquiry"}]}')["credit_email"] is True
    assert last_json('{"ideas": [{"symbol": "NVDA"}]}')["ideas"][0]["symbol"] == "NVDA"
    assert last_json('{"links": [{"n": 0, "credit_id": 3}]}')["links"][0]["credit_id"] == 3
    assert last_json("no json here") is None


def test_read_my_credit_from_inside_buddy_asks_to_switch_and_waits_for_the_app():
    screens = [{"error": "not_browser", "app": "app.buddy"}, {"error": "not_browser", "app": "app.buddy"},
               {"app": "com.creditkarma.mobile", "can_scroll": False, "nodes": [{"kind": "text", "t": "TransUnion 742"}]}]
    sent, said = [], []

    async def phone(msg):
        sent.append(msg)
        return screens.pop(0) if screens else {"app": "com.creditkarma.mobile", "nodes": []}

    async def say(text):
        said.append(text)

    async def no_wait(_):
        return None
    wrapped = asyncio.run(credit.wait_for_credit_app(phone, say, sleep=no_wait))
    assert said and "Open Credit Karma" in said[0]
    first = asyncio.run(wrapped({"type": "form", "action": "read"}))
    assert first["app"] == "com.creditkarma.mobile" and len(sent) == 3   # waited, then read once


def test_buddys_own_background_messages_are_marked_real_in_the_conversation():
    from buddy.agent import Conversation
    convo = Conversation.__new__(Conversation)
    convo.messages = []
    convo.note_proactive("Market scan: 8 ideas worth watching: MSFT ...", "money")
    assert "Never call it made up" in convo.messages[0]["content"] and "market scan" in convo.messages[0]["content"]
