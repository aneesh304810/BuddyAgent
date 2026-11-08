"""Buddy's browser: the agent drives the page, stops before a submit, and only the user's yes presses it."""

import asyncio
import json
from types import SimpleNamespace as NS

from buddy import browse
from buddy.memory import Memory

PAGE = {"session": "s1", "title": "Clinic", "url": "https://clinic.example/book",
        "items": ["[1 input text] Full name", "[2 button] Book appointment"], "text": "Book an appointment"}


def tool(name, **args):
    return NS(type="tool_use", name=name, input=args, id=f"t-{name}")


def test_a_form_is_filled_then_waits_for_the_users_yes(monkeypatch):
    calls = []

    async def step(session, cmd):
        calls.append(cmd)
        if cmd.get("op") == "screenshot":
            return {"session": "s1"}
        if cmd.get("op") == "click" and not cmd.get("approved"):
            return {"needs_approval": True, "label": "Book appointment", **PAGE}
        return dict(PAGE)
    monkeypatch.setattr(browse, "step", step)
    script = iter([[tool("my_details", question="name")], [tool("type", ref=1, text="Aneesh Nair")],
                   [tool("click", ref=2)]])

    async def create(**kw):
        return NS(content=next(script), stop_reason="tool_use")
    monkeypatch.setattr(browse.client.beta.messages, "create", create)

    class Ctx:
        memory, cost = Memory(":memory:"), 0.0

        def record(self, r):
            return 0.0
    result = asyncio.run(browse.run_browse({"title": "Book", "params": {"task": "book Dr Lee",
                                                                        "url": "https://clinic.example/book"}}, Ctx()))
    pending = browse.pending_of({"findings": result.findings})
    assert pending == {"session": "s1", "ref": 2, "summary": "Press \"Book appointment\"",
                       "url": "https://clinic.example/book"}
    assert not any(c.get("approved") for c in calls)  # nothing sent without the user

    closed = []

    async def call(op, **kw):
        closed.append(op)
        return {}
    monkeypatch.setattr(browse.sandbox, "call", call)

    async def step2(session, cmd):
        calls.append(cmd)
        return {"session": "s1", "text": "Thanks, you are booked."} if cmd.get("approved") else {}
    monkeypatch.setattr(browse, "step", step2)
    said, _ = asyncio.run(browse.submit(Memory(":memory:"), pending))
    assert said.startswith("Sent. The page now says: Thanks, you are booked.") and closed == ["browser_close"]
    assert calls[-2] == {"op": "click", "ref": 2, "approved": True}


def test_an_old_sandbox_says_to_update(monkeypatch):
    import pytest
    from buddy.jobs import JobFailed

    async def step(session, cmd):
        return {"error": "workspace names are lowercase letters, digits and dashes", "session": ""}
    monkeypatch.setattr(browse, "step", step)

    class Ctx:
        memory, cost = Memory(":memory:"), 0.0
    with pytest.raises(JobFailed, match="update.sh"):
        asyncio.run(browse.run_browse({"title": "x", "params": {"task": "t", "url": ""}}, Ctx()))


def test_news_that_arrives_mid_reply_is_kept_for_the_next_turn():
    from buddy.agent import Conversation
    c = Conversation.__new__(Conversation)
    c.messages = [{"role": "user", "content": "did you fill the form"}]
    c.note_proactive("The browser task didn't finish.", "job_done")
    assert len(c.messages) == 1 and c._held_notes == [("The browser task didn't finish.", "job_done")]


def test_a_task_that_fails_closes_its_browser(monkeypatch):
    import pytest
    closed = []

    async def step(session, cmd):
        return dict(PAGE)
    monkeypatch.setattr(browse, "step", step)

    async def call(op, **kw):
        closed.append((op, kw.get("session")))
        return {}
    monkeypatch.setattr(browse.sandbox, "call", call)

    async def create(**kw):
        raise RuntimeError("Background AI work is paused")
    monkeypatch.setattr(browse.client.beta.messages, "create", create)

    class Ctx:
        memory, cost = Memory(":memory:"), 0.0
    with pytest.raises(RuntimeError):
        asyncio.run(browse.run_browse({"title": "x", "params": {"task": "t", "url": "https://a.example"}}, Ctx()))
    assert closed == [("browser_close", "s1")]
