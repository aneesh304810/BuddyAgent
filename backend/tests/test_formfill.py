"""Form helper: Buddy reads the browser form, fills it only after a yes, never secrets or Submit."""

import asyncio

import pytest
from fastapi.testclient import TestClient

from buddy import agent, formfill, server
from buddy.tools import ToolError

SCREEN = [  # what the phone's form helper sends: plain text, then fillable things with ids
    {"kind": "text", "t": "Does your organization obtain consent from consumers?"},
    {"i": "f1", "kind": "button", "t": "Yes"}, {"i": "f2", "kind": "button", "t": "No", "checked": True},
    {"kind": "text", "t": "Please explain why you do not obtain consent directly from consumers."},
    {"i": "f3", "kind": "edit", "t": "Explanation", "value": ""},
    {"i": "f4", "kind": "secret"},
    {"i": "f5", "kind": "button", "t": "Submit"},
    {"kind": "text", "t": "Is MFA in place for access to critical systems?"},
    {"i": "f6", "kind": "dropdown", "t": "Select an option", "value": ""},
]


def test_the_screen_is_described_with_fillable_fields():
    text = formfill.describe(SCREEN)
    assert "[f1 button: Yes]" in text and "[f2 button: No (selected)]" in text
    assert "[f3 text box: Explanation (empty)]" in text and "never filled" in text
    assert "[f5 button: Submit (Buddy never taps this)]" in text


@pytest.mark.parametrize("fills, why", [
    ([{"field": "f5", "tap": True}], "submit-type"), ([{"field": "f4", "text": "hunter2"}], "secret"),
    ([{"field": "f9", "text": "x"}], "isn't on the screen"), ([{"field": "f1", "text": "x"}], "isn't a text box"),
    ([], "Nothing to fill")])
def test_secrets_submit_and_unknown_fields_are_refused(fills, why):
    with pytest.raises(ToolError, match=why):
        formfill.check_fills(fills, SCREEN)


class Thrift:
    def __init__(self):
        self.asked = []

    def ask_action(self, question, run):
        self.asked.append((question, run))
        return "APPROVAL NEEDED: " + question


def test_read_then_fill_only_after_the_ok():
    sent = []

    async def phone(message):
        sent.append(message)
        if message["action"] == "read":
            return {"type": "form_screen", "nodes": SCREEN, "can_scroll": True}
        return {"type": "form_result", "done": ["f1", "f3"], "skipped": []}
    state, thrift = {}, Thrift()
    out = asyncio.run(formfill.run_form_tool(phone, thrift, state, {"action": "read"}))
    assert out.startswith(formfill.UNTRUSTED) and "More below" in out
    fills = [{"field": "f1", "tap": True}, {"field": "f3", "text": "I am the only consumer and consent in Plaid Link."}]
    out = asyncio.run(formfill.run_form_tool(phone, thrift, state, {"action": "fill", "fills": fills}))
    assert out == "APPROVAL NEEDED: Fill in 1 text box and 1 choice on the form?" and len(sent) == 1  # not yet
    said = asyncio.run(thrift.asked[0][1]())
    assert sent[1]["action"] == "fill" and sent[1]["fills"] == fills
    assert said.startswith("Filled 2 of 2.") and "tap Submit yourself" in said


def test_helper_off_and_wrong_app_are_explained():
    async def off(message):
        return {"error": "helper_off"}

    async def maps(message):
        return {"error": "not_browser", "app": "com.google.android.apps.maps"}
    assert "Accessibility" in asyncio.run(formfill.run_form_tool(off, None, {}, {"action": "read"}))
    assert "not Chrome" in asyncio.run(formfill.run_form_tool(maps, None, {}, {"action": "read"}))
    with pytest.raises(ToolError, match="isn't connected"):
        asyncio.run(formfill.run_form_tool(None, None, {}, {"action": "read"}))


def test_the_server_waits_for_the_phones_answer():
    """phone_request sends {"type": "form", "id"} and returns the phone's reply with that id."""
    got = {}

    async def run(convo):
        got["reply"] = await convo.phone_request({"type": "form", "action": "read"})
    with TestClient(server.app) as c, c.websocket_connect("/ws") as ws:
        convo = next(iter(server.connections.values()))
        future = c.portal.start_task_soon(run, convo)  # on the server's own event loop
        ask = ws.receive_json()
        assert ask["type"] == "form" and ask["action"] == "read" and ask["id"]
        ws.send_json({"type": "form_screen", "id": ask["id"], "nodes": SCREEN})
        future.result(timeout=5)
    assert got["reply"]["nodes"] == SCREEN


def test_dropdowns_are_chosen_by_option_text():
    assert "[f6 dropdown: Select an option]" in formfill.describe(SCREEN)
    assert formfill.check_fills([{"field": "f6", "choose": "Yes"}], SCREEN) == [{"field": "f6", "choose": "Yes"}]
    with pytest.raises(ToolError, match="use choose"):
        formfill.check_fills([{"field": "f6", "text": "Yes"}], SCREEN)
    with pytest.raises(ToolError, match="submit-type"):
        formfill.check_fills([{"field": "f6", "choose": "Submit"}], SCREEN)
