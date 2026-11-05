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
