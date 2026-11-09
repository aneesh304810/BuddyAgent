"""Handing a fix to Claude Code through a GitHub issue: asks first, never personal data, tells once when done."""

import asyncio

from buddy import claudefix, sandbox, selfdeploy
from buddy.memory import Memory


class Thrift:
    def __init__(self):
        self.asked = []

    def ask_action(self, question, run, **kw):
        self.asked.append((question, run))
        return f"APPROVAL NEEDED: {question}"


def _sandbox(monkeypatch, status=None):
    calls = []

    async def call(op, **kw):
        calls.append((op, kw))
        if op == "claude_issue":
            return {"number": 12, "url": "https://github.com/o/r/issues/12"}
        return status or {"state": "open", "url": "u", "branch": "", "comments": []}
    monkeypatch.setattr(sandbox, "call", call)
    return calls


def test_send_asks_first_then_opens_the_issue(monkeypatch, tmp_path):
    m, t = Memory(tmp_path / "b.db"), Thrift()
    calls = _sandbox(monkeypatch)
    said = asyncio.run(claudefix.run_tool(m, t, {"action": "send", "title": "Fix the briefing loop",
                                                 "brief": "proactive.py rewrites the briefing every minute; write it once."}))
    assert said.startswith("APPROVAL NEEDED") and not calls  # nothing sent before the yes
    done = asyncio.run(t.asked[0][1]())
    assert "issue #12" in done and calls[0][0] == "claude_issue"
    assert claudefix.pending(m)[0]["number"] == 12


def test_personal_details_never_go_to_github(monkeypatch, tmp_path):
    m, t = Memory(tmp_path / "b.db"), Thrift()
    _sandbox(monkeypatch)
    said = asyncio.run(claudefix.run_tool(m, t, {"action": "send", "title": "Fix vault search",
                                                 "brief": "searching mom's pathology report returns nothing at all"}))
    assert "personal details" in said and not t.asked


def test_tells_once_when_claude_is_done_and_deploys_after_a_yes(monkeypatch, tmp_path):
    m, t = Memory(tmp_path / "b.db"), Thrift()
    claudefix._save(m, [{"number": 12, "url": "u", "title": "Fix it", "at": "2026-11-09T10:00:00+00:00",
                         "told": False, "checked": 0}])
    report = {"body": "**Claude finished** ... ## What's new\nThe briefing is written once.\n## Tests\n740 passed"}
    _sandbox(monkeypatch, {"state": "open", "url": "u", "branch": "buddy/claude-issue-12-20261109-1000",
                           "comments": [report]})
    told = []

    async def deliver(kind, text, **kw):
        told.append(text)
        return True
    asyncio.run(claudefix.check(m, deliver, 1000.0))
    asyncio.run(claudefix.check(m, deliver, 2000.0))
    assert len(told) == 1 and "buddy/claude-issue-12" in told[0] and "deploy Claude's fix" in told[0]
    monkeypatch.setattr(selfdeploy, "request", lambda branch, job_id=None: f"requested {branch}")
    said = asyncio.run(claudefix.run_tool(m, t, {"action": "deploy"}))
    assert said.startswith("APPROVAL NEEDED")
    assert asyncio.run(t.asked[-1][1]()) == "requested buddy/claude-issue-12-20261109-1000"


def test_no_deploy_before_claude_has_finished(monkeypatch, tmp_path):
    m, t = Memory(tmp_path / "b.db"), Thrift()
    claudefix._save(m, [{"number": 12, "url": "u", "title": "Fix it", "at": "2026-11-09T10:00:00+00:00",
                         "told": False, "checked": 0}])
    _sandbox(monkeypatch, {"state": "open", "url": "u", "branch": "", "comments": [{"body": "Claude is working…"}]})
    assert "hasn't pushed" in asyncio.run(claudefix.run_tool(m, t, {"action": "deploy"})) and not t.asked
    assert "still on it" in asyncio.run(claudefix.run_tool(m, t, {"action": "status"}))


def test_says_when_claude_stopped_with_an_error(monkeypatch, tmp_path):
    m = Memory(tmp_path / "b.db")
    claudefix._save(m, [{"number": 26, "url": "u", "title": "Add test", "at": "2026-11-09T10:00:00+00:00",
                         "told": False, "checked": 0}])
    _sandbox(monkeypatch, {"state": "open", "url": "u", "branch": "", "comments": [{"body": (
        "**Claude encountered an error after 2s** —— [View job](https://github.com/o/r/actions/runs/42)")}]})
    told = []

    async def deliver(kind, text, **kw):
        told.append(text)
        return True
    asyncio.run(claudefix.check(m, deliver, 1000.0))
    asyncio.run(claudefix.check(m, deliver, 2000.0))
    assert len(told) == 1 and "actions/runs/42" in told[0] and "send it again" in told[0]


def test_an_issue_not_in_my_list_is_looked_up_by_number(monkeypatch, tmp_path):
    """Issue #27 was opened but the send looked failed (label 403), so Buddy had no record and couldn't deploy it."""
    m, t = Memory(tmp_path / "b.db"), Thrift()
    calls = _sandbox(monkeypatch, {"state": "open", "url": "u", "branch": "buddy/claude-issue-27-20261109-2151",
                                   "comments": [{"body": "Claude Code finished\\n## What's new\\nCalendar check fixed."}]})
    monkeypatch.setattr(selfdeploy, "request", lambda branch, job_id=None: f"requested {branch}")
    assert "finished issue #27" in asyncio.run(claudefix.run_tool(m, t, {"action": "status", "number": 27}))
    assert asyncio.run(claudefix.run_tool(m, t, {"action": "deploy", "number": 27})).startswith("APPROVAL NEEDED")
    assert asyncio.run(t.asked[-1][1]()) == "requested buddy/claude-issue-27-20261109-2151"
    assert calls[0] == ("claude_issue_status", {"number": 27, "timeout": 60})
