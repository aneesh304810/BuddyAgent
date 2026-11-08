"""Replies through the phone's notification (with a yes), the user's own auto-reply rules, chats into memory."""

import asyncio
import json
from datetime import datetime, timezone

import pytest

from buddy import messaging
from buddy.connectors.hub import ConnectorHub
from buddy.connectors.phone import NotificationsConnector
from buddy.memory import Memory
from buddy.messaging import Messages
from buddy.tools import ToolError


def note(memory, title, text, app="com.whatsapp", can_reply=True):
    msg = {"device": "Fold", "app": app, "app_name": "WhatsApp", "title": title, "text": text,
           "posted_at": datetime.now(timezone.utc).isoformat(), "key": title + text, "can_reply": can_reply}
    ConnectorHub(memory).ingest("notifications", [NotificationsConnector.to_record(msg)])
    return msg


class Thrift:
    def __init__(self):
        self.asked = []

    def ask_action(self, question, run):
        self.asked.append((question, run))
        return f"APPROVAL NEEDED: {question}"


def test_reply_needs_a_yes_then_goes_through_the_phone():
    m = Memory(":memory:")
    note(m, "Neha", "are you coming tonight?")
    note(m, "Family: Amma", "dinner at 8")
    chats = Messages(m).recent_chats()
    assert {c["chat"]: c["group"] for c in chats} == {"Neha": False, "Family: Amma": True}
    sent = []

    async def phone(message):
        sent.append(message)
        return {"ok": True}
    thrift = Thrift()
    out = asyncio.run(messaging.run_tool(m, {"action": "reply", "to": "neha", "text": "Yes, 10 min late!"}, phone, thrift))
    assert out.startswith("APPROVAL NEEDED") and sent == []  # nothing sent before the yes
    assert thrift.asked[0][0] == 'Send to Neha on WhatsApp: "Yes, 10 min late!"?'
    assert asyncio.run(thrift.asked[0][1]()) == "Sent to Neha on WhatsApp."
    assert sent == [{"type": "reply", "app": "com.whatsapp", "chat": "Neha", "text": "Yes, 10 min late!"}]
    with pytest.raises(ToolError):
        Messages(m).find_chat("Ravi")  # no recent notification: can't reply


def test_auto_reply_only_by_the_users_rules():
    m = Memory(":memory:")
    msgs = Messages(m)
    msg = note(m, "Neha", "where are you?")
    driving = {"driving": True, "in_meeting": "", "quiet": False}
    assert msgs.auto_reply_for(msg, driving) == ""  # no rule: nothing
    msgs.add_rule("Neha", "driving", "Hi {name}, I'm driving, will call you soon.")
    text = msgs.auto_reply_for(msg, driving)
    assert text == "Hi Neha, I'm driving, will call you soon. (sent by Buddy, my assistant)"
    assert msgs.auto_reply_for(msg, {"driving": False}) == ""  # not driving: no reply
    msgs.log_auto("Neha", text)
    assert msgs.auto_reply_for(msg, driving) == ""  # once every 2 hours
    group = note(m, "Family: Amma", "where are you?")
    msgs.add_rule("*people", "always", "Busy, back soon.")
    assert msgs.auto_reply_for(group, driving) == ""  # never to groups
    stranger = note(m, "+1 201 555 0100", "hello")
    assert msgs.auto_reply_for(stranger, driving) == ""  # not someone the user knows
    with pytest.raises(ToolError):
        msgs.add_rule("Neha", "sometimes", "x" * 10)


def test_chat_messages_become_memories():
    from buddy.connect import Connect
    m = Memory(":memory:")
    note(m, "Neha", "Rohan starts at Hoboken Elementary in January, so we're moving to Jersey City next month")

    async def extract(memory, content):
        assert "a message from WhatsApp" in content
        return {"plans": [{"kind": "fact", "date": "2026-11-08", "title": "Neha's son Rohan starts at Hoboken "
                           "Elementary in January; they're moving to Jersey City", "who": "Neha"}]}
    asyncio.run(Connect(m).scan(extract=extract))
    from buddy.brain import Brain
    notes = Brain(m, embed=lambda t: None).notes("learned")
    assert notes and notes[0]["title"] == "About Neha" and "Rohan" in notes[0]["text"]
