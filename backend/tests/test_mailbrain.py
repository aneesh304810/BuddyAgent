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


def add_triage(memory, n, sender, done="", priority="low"):
    now = datetime.now(timezone.utc).isoformat()
    memory.db.execute(
        "INSERT INTO mail_triage (record_id, account, sender, subject, priority, reason, done, received_at, sorted_at) "
        "VALUES (?, 'me@gmail.com', ?, 'Hi', ?, 'A promotion.', ?, ?, ?)", (n, sender, priority, done, now, now))


def test_weekly_label_suggestions_skip_covered_and_acted_senders(setup):
    memory, manager, told, box = setup
    n = 0
    for sender, count, done in [("Temu <deals@temu.com>", 12, ""), ("Uber <receipts@uber.com>", 9, ""),
                                ("Shop <news@shop.com>", 10, "labelled Shop"), ("Rare <hi@rare.com>", 3, "")]:
        for _ in range(count):
            n += 1
            add_triage(memory, n, sender, done if n % 2 else "")
    manager.add_rule("Uber receipts", "label", "Travel")  # already covered by a rule
    items = manager.label_suggestions()
    assert [i["sender"] for i in items] == ["temu.com"]  # shop was acted on, rare is too few
    assert items[0]["label"] == "Temu" and items[0]["emails"] == 12 and items[0]["mostly_low"]
    assert "label emails from temu.com as Temu" in items[0]["say"]


def test_weekly_suggestions_ask_once_and_batch_yes_creates_all_rules(setup):
    memory, manager, told, box = setup
    n = 0
    for sender in ("Temu <deals@temu.com>", "Shein <hi@shein.com>", "Wish <x@wish.com>"):
        for _ in range(8):
            n += 1
            add_triage(memory, n, sender)
    thrift = Thrift(memory)
    asked = []

    async def suggest(items):  # what the server does: queue one approval per rule, ask once
        for item in items:
            async def add(item=item):
                manager.add_rule(item["rule"], "label", item["label"])
                return f"Label rule set: {item['label']}."
            thrift.ask_action(f"Label {item['sender']}?", add, group="weekly_labels", item=item["say"],
                              plural="email label rules")
        asked.append(thrift.question())
        return True

    manager.suggest = suggest
    assert manager.rules() == []
    assert asyncio.run(manager.weekly_suggestions()) == 3
    assert asyncio.run(manager.weekly_suggestions()) == 0  # once a week only
    assert len(asked) == 1 and "these 3 email label rules" in asked[0] and "One yes does them all" in asked[0]
    assert manager.rules() == []  # nothing created before the yes
    said, _ = asyncio.run(thrift.resolve("yes"))
    assert sorted(r["label"] for r in manager.rules()) == ["Shein", "Temu", "Wish"]
    assert said and "Temu" in said
    memory.set("mail:suggest_week", "old")  # next week: the same senders are not offered again
    assert manager.label_suggestions() == []


def test_weekly_suggestions_retry_when_nobody_listening(setup):
    memory, manager, told, box = setup
    for n in range(8):
        add_triage(memory, n + 1, "Temu <deals@temu.com>")
    calls = []

    async def suggest(items):
        calls.append(items)
        return len(calls) > 1  # first time the phone isn't connected

    manager.suggest = suggest
    assert asyncio.run(manager.weekly_suggestions()) == 0
    assert asyncio.run(manager.weekly_suggestions()) == 1
    assert len(calls) == 2
