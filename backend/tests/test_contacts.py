"""The phone's contacts: synced with the owner's permission, looked up by name and number, used by the phone
agent and messages, and deletable."""

import asyncio
import json
import logging
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from buddy import calls, contacts, guard, makers
from buddy.connectors.hub import ConnectorHub
from buddy.connectors.phone import NotificationsConnector
from buddy.memory import Memory
from buddy.messaging import Messages
from buddy.tools import ToolError

BOOK = {"contacts": [{"name": "Priya Sharma", "numbers": ["+1 (201) 555-0123", "201.555.0124"]},
                     {"name": "Mom", "numbers": ["2015550777"]},
                     {"name": "Raj Patel", "numbers": ["+91 98765 43210"]}]}


def note(memory, title, text, app="com.whatsapp"):
    msg = {"device": "Fold", "app": app, "app_name": "WhatsApp", "title": title, "text": text,
           "posted_at": datetime.now(timezone.utc).isoformat(), "key": title + text, "can_reply": True}
    ConnectorHub(memory).ingest("notifications", [NotificationsConnector.to_record(msg)])
    return msg


def test_lookup_by_name_and_by_number():
    m = Memory(":memory:")
    assert contacts.ingest(m, BOOK) == 4
    assert contacts.name_for(m, "+12015550124") == ("Priya Sharma", "contact")
    assert contacts.find(m, "priya")[0] == {"name": "Priya Sharma", "numbers": ["2015550123", "2015550124"],
                                            "how": "contact"}
    assert contacts.find(m, "Priya Sharma")[0]["name"] == "Priya Sharma"
    assert contacts.find(m, "sharma")[0]["name"] == "Priya Sharma"
    assert contacts.find(m, "(201) 555-0777")[0]["name"] == "Mom"
    assert contacts.find(m, "nobody") == [] and contacts.find(m, "") == []
    contacts.save_caller(m, "2015550999", "Dr. Mehta's office")
    assert contacts.find(m, "mehta")[0]["how"] == "saved"


def test_contacts_can_be_deleted():
    m = Memory(":memory:")
    contacts.ingest(m, BOOK)
    contacts.save_caller(m, "2015550999", "Plumber")
    assert contacts.forget_all(m) == 4
    assert contacts.count(m) == (0, 1) and contacts.name_for(m, "2015550123") == ("", "")
    assert contacts.forget_all(m, saved_too=True) == 1 and contacts.count(m) == (0, 0)


def test_screened_call_from_a_contact_is_known_by_name(tmp_path, monkeypatch):
    for k, v in {"BUDDY_OWNER_NAME": "Sam Rivera"}.items():
        monkeypatch.setenv(k, v)
    m = Memory(tmp_path / "b.db")
    contacts.ingest(m, BOOK)
    call = calls.screen_call(m, {"From": "12015550123", "CallUUID": "in-1"})
    assert call["who"] == "Priya Sharma" and call["goal"] == "contact: Priya Sharma"
    assert "Priya Sharma, in" in calls.screen_prompt(m, call) and "contacts" in calls.screen_prompt(m, call)
    stranger = calls.screen_call(m, {"From": "12015550000", "CallUUID": "in-2"})
    assert "not in the contacts" in calls.screen_prompt(m, stranger)


def test_phone_tool_looks_up_a_contact():
    m = Memory(":memory:")
    said = asyncio.run(calls.run_tool(m, None, {"action": "contact", "who": "Priya"}))
    assert "Share contacts" in said  # nothing synced yet
    contacts.ingest(m, BOOK)
    got = json.loads(asyncio.run(calls.run_tool(m, None, {"action": "contact", "who": "Priya"})))
    assert got[0]["name"] == "Priya Sharma"
    assert asyncio.run(calls.run_tool(m, None, {"action": "contact", "who": "Zed"})) == "No one like that in the contacts."


def test_reply_to_a_name_resolves_through_contacts():
    m = Memory(":memory:")
    contacts.ingest(m, BOOK)
    note(m, "+1 201-555-0123", "running late?", app="com.google.android.apps.messaging")  # shows only her number
    note(m, "Raj Patel", "lunch?")
    msgs = Messages(m)
    assert msgs.find_chat("Priya")["chat"] == "+1 201-555-0123"
    assert msgs.find_chat("priya sharma")["chat"] == "+1 201-555-0123"
    assert msgs.find_chat("Raj")["chat"] == "Raj Patel"
    with pytest.raises(ToolError):
        msgs.find_chat("Mom")  # in the contacts but no recent chat to reply through


def test_auto_reply_knows_a_contact_behind_a_number():
    m = Memory(":memory:")
    contacts.ingest(m, BOOK)
    msgs = Messages(m)
    msgs.add_rule("Priya", "driving", "Driving, will call you back, {name}.")
    msg = note(m, "+1 201-555-0123", "call me", app="com.google.android.apps.messaging")
    got = msgs.auto_reply_for(msg, {"driving": True})
    assert got.startswith("Driving, will call you back, Priya.")
    msgs.add_rule("", "meeting", "In a meeting.")  # anyone in the owner's people: contacts count
    assert msgs.auto_reply_for(note(m, "Mom", "hi"), {"in_meeting": True}).startswith("In a meeting.")
    assert msgs.auto_reply_for(note(m, "+1 555 000 1111", "hi"), {"in_meeting": True}) == ""  # unknown number


def test_contacts_api_syncs_looks_up_and_deletes_without_logging(monkeypatch, tmp_path, caplog):
    from buddy import library, server

    mem = Memory(":memory:")
    monkeypatch.setattr(server, "authorized", lambda t: t == "s3cret")
    monkeypatch.setattr(server, "memory", mem)
    monkeypatch.setattr(guard, "guard", guard.Guard(":memory:"))
    monkeypatch.setattr(makers, "FILES_DIR", tmp_path / "files")
    monkeypatch.setattr(library, "LIB_DIR", tmp_path / "library")
    good = {"Authorization": "Bearer s3cret"}
    caplog.set_level(logging.DEBUG)
    with TestClient(server.app) as c:
        assert c.post("/api/contacts/sync", json=BOOK).status_code in (401, 403)
        assert c.post("/api/contacts/sync", json=BOOK, headers=good).json() == {"ok": True, "numbers": 4}
        assert c.post("/api/contacts/lookup", json={"q": "mom"}, headers=good).json()["contacts"][0]["name"] == "Mom"
        assert c.post("/api/contacts/lookup", json={"q": "2015550123"}, headers=good).json()["contacts"][0]["name"] == "Priya Sharma"
        assert c.delete("/api/contacts").status_code in (401, 403)
        assert c.delete("/api/contacts", headers=good).json() == {"ok": True, "deleted": 4}
        assert c.post("/api/contacts/lookup", json={"q": "mom"}, headers=good).json() == {"contacts": []}
    logged = caplog.text
    assert "Priya" not in logged and "5550123" not in logged
