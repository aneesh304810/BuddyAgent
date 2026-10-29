"""Email manager: sorting by priority and rules, urgent alerts, auto-organising."""

import asyncio
import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from buddy import mailbrain
from buddy.connectors.hub import ConnectorHub
from buddy.memory import Memory
from buddy.thrift import Thrift


class FakeClaude:
    def __init__(self, verdicts):
        self.verdicts = verdicts
        self.calls = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self.create))

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        usage = SimpleNamespace(input_tokens=1000, output_tokens=100, cache_read_input_tokens=0,
                                cache_creation_input_tokens=0, server_tool_use=None)
        return SimpleNamespace(model="claude-haiku-5-5", usage=usage, stop_reason="end_turn",
                               content=[SimpleNamespace(type="text", text=json.dumps({"emails": self.verdicts(kwargs)}))])


class FakeBox:
    def __init__(self):
        self.done = []

    def label(self, label, query):
        self.done.append(("label", label, query))

    def archive(self, query):
        self.done.append(("archive", query))

    def mark_read(self, query):
        self.done.append(("mark_read", query))


def add_email(memory, n, sender, subject, account="me@gmail.com"):
    now = datetime.now(timezone.utc).isoformat()
    memory.db.execute(
        "INSERT INTO records (source, external_id, kind, title, body, start_at, data, synced_at) "
        "VALUES ('email', ?, 'email', ?, ?, ?, ?, ?)",
        (f"<m{n}@x.com>", subject, f"From: {sender}\nHello", now,
         json.dumps({"from": sender, "account": account}), now),
    )


@pytest.fixture
def setup(monkeypatch):
    memory = Memory(":memory:")
    ConnectorHub(memory)  # creates the records table
    told, box = [], FakeBox()

    async def notify(kind, text, **_):
        told.append(text)
        return True

    monkeypatch.setattr(mailbrain, "in_quiet_hours", lambda: False)
    manager = mailbrain.MailManager(memory, notify, mailbox_factory=lambda account, env: box)
    return memory, manager, told, box


def test_sorts_tells_urgent_and_applies_rules(setup, monkeypatch):
    memory, manager, told, box = setup
    travel = manager.add_rule("Uber receipts", "label", "Travel")["id"]
    add_email(memory, 1, "Chase <alerts@chase.com>", "Payment due Friday")
    add_email(memory, 2, "Uber <receipts@uber.com>", "Your trip")
    add_email(memory, 3, "Shop <deals@shop.com>", "50% off")
    ids = [r["id"] for r in memory.db.execute("SELECT id FROM records ORDER BY id")]
    fake = FakeClaude(lambda kw: [
        {"id": ids[0], "priority": "urgent", "reason": "Chase payment of $412 due Friday.", "rules": []},
        {"id": ids[1], "priority": "normal", "reason": "Uber trip receipt.", "rules": [travel]},
        {"id": ids[2], "priority": "low", "reason": "A promotion.", "rules": []},
    ])
    monkeypatch.setattr(mailbrain, "client", fake)

    assert asyncio.run(manager.sort_new()) == 3
    assert told == ["Important email: Chase payment of $412 due Friday."]
    assert box.done == [("label", "Travel", "rfc822msgid:m2@x.com")]
    important = manager.important()
    assert [i["priority"] for i in important] == ["urgent", "normal"]  # low stays out
    assert important[1]["done"] == "labelled Travel"
    assert "untrusted" in fake.calls[0]["messages"][0]["content"]
    assert "1 urgent" in manager.briefing_line()
    assert asyncio.run(manager.sort_new()) == 0  # nothing new: no Claude call
    assert len(fake.calls) == 1


def test_user_rule_overrides_priority(setup, monkeypatch):
    memory, manager, told, _ = setup
    rule = manager.add_rule("Anything from my mom", "urgent")["id"]
    add_email(memory, 1, "Mom <mom@gmail.com>", "Call me")
    rid = memory.db.execute("SELECT id FROM records").fetchone()["id"]
    monkeypatch.setattr(mailbrain, "client", FakeClaude(lambda kw: [
        {"id": rid, "priority": "normal", "reason": "Mom asks you to call.", "rules": [rule]}]))
    asyncio.run(manager.sort_new())
    assert told == ["Important email: Mom asks you to call."]


def test_quiet_hours_hold_alerts(setup, monkeypatch):
    memory, manager, told, _ = setup
    monkeypatch.setattr(mailbrain, "in_quiet_hours", lambda: True)
    add_email(memory, 1, "Bank", "Fraud alert")
    rid = memory.db.execute("SELECT id FROM records").fetchone()["id"]
    monkeypatch.setattr(mailbrain, "client", FakeClaude(lambda kw: [
        {"id": rid, "priority": "urgent", "reason": "Fraud alert on your card.", "rules": []}]))
    asyncio.run(manager.sort_new())
    assert told == [] and manager.important()[0]["priority"] == "urgent"  # in the morning briefing instead


def test_rules_that_change_mail_ask_first(setup):
    memory, manager, _, _ = setup
    thrift = Thrift(memory)
    out = asyncio.run(mailbrain.run_mail_tool(manager, "add_email_rule",
                                              {"rule": "LinkedIn notifications", "action": "archive"}, thrift))
    assert out.startswith("APPROVAL NEEDED") and manager.rules() == []
    out = asyncio.run(mailbrain.run_mail_tool(manager, "add_email_rule",
                                              {"rule": "Chase payments", "action": "urgent"}, thrift))
    assert "saved" in out and len(manager.rules()) == 1
    rule_id = manager.rules()[0]["id"]
    assert asyncio.run(mailbrain.run_mail_tool(manager, "email_rules", {"remove_id": rule_id})) == "Removed."
    with pytest.raises(Exception):
        manager.add_rule("x", "label")  # a label rule needs the label
