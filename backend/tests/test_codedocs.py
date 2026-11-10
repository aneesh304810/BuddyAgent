"""Technical documentation generated from Buddy's code, its search, and the visual page."""

import asyncio
import json

import pytest

from buddy import codedocs, codedocs_visual, selfcode
from buddy.memory import Memory

MODULE = '''"""Phone calls for the owner. <script>alert(1)</script>

More detail here."""
from . import gateway, memory
from .tools import ToolError

SCHEMA = "CREATE TABLE IF NOT EXISTS phone_calls (id INTEGER)"
LIMIT = os.getenv("BUDDY_CALLS_LIMIT")


class Mind:
    """The call's conversation."""

    async def heard(self, said: str) -> None:
        """What they said in, Buddy's move out."""


def dial(memory, call: dict) -> str:
    """Ask Plivo to place the call."""


TOOL = {"name": "phone_call", "description": "Call a business for the user.",
        "input_schema": {"type": "object", "properties": {"action": {"type": "string", "enum": ["call", "status"]}}}}
'''
SERVER = '''"""The web server."""
from . import calls


@app.post("/calls/answer/{call_id}", dependencies=[Depends(require_admin)])
async def call_answer(call_id: int):
    """Plivo asks what to do when they pick up."""
'''
KOTLIN = '''package app.buddy
/** The Insights screen: what Buddy costs. */
@Composable
fun Insights() {}
'''
SHELL = '''#!/usr/bin/env bash
# Set up Buddy's phone calls, once.
# It asks for the keys.
set -e
echo $BUDDY_PLIVO_NUMBER
'''
GUIDE = "# Calls\n\nIntro.\n\n## Setup\n\nRun calls.sh.\n\n## Costs\n\nAbout 2 cents a minute.\n"


@pytest.fixture
def repo(tmp_path, monkeypatch):
    (tmp_path / "backend/buddy").mkdir(parents=True)
    (tmp_path / "android/app").mkdir(parents=True)
    (tmp_path / "deploy").mkdir()
    (tmp_path / "docs").mkdir()
    (tmp_path / "backend/buddy/calls.py").write_text(MODULE)
    (tmp_path / "backend/buddy/server.py").write_text(SERVER)
    (tmp_path / "backend/buddy/secrets.env").write_text("BUDDY_KEY=x")  # never read (selfcode skips .env-like files)
    (tmp_path / "android/app/Insights.kt").write_text(KOTLIN)
    (tmp_path / "deploy/calls.sh").write_text(SHELL)
    (tmp_path / "docs/CALLS.md").write_text(GUIDE)
    monkeypatch.setattr(selfcode, "ROOT", tmp_path)
    monkeypatch.setattr(codedocs, "_cache", {"sig": None, "db": None, "pages": {}, "modules": {}})
    return tmp_path


def test_module_pages_describe_design_tools_routes_tables_and_links(repo):
    pages = codedocs.build()
    calls = pages["backend/buddy/calls.py"]["text"]
    assert calls.startswith("Phone calls for the owner.")
    assert "- phone_call (call, status): Call a business for the user." in calls
    assert "Database tables: phone_calls" in calls and "Settings it reads: BUDDY_CALLS_LIMIT" in calls
    assert "Uses: gateway, memory, tools" in calls and "Used by: server" in calls
    assert "async heard(self, said: str) -> None: What they said in" in calls
    assert "dial(memory, call: dict) -> str: Ask Plivo to place the call." in calls
    server = pages["backend/buddy/server.py"]["text"]
    assert "- POST /calls/answer/{call_id} (admin): Plivo asks what to do when they pick up." in server
    assert "Insights screen" in pages["android/app/Insights.kt"]["text"]
    assert "Set up Buddy's phone calls" in pages["deploy/calls.sh"]["text"]
    assert pages["docs/CALLS.md: Costs"]["text"] == "About 2 cents a minute."
    assert "phone_call" in pages["tools"]["text"] and "phone_calls (calls.py)" in pages["tables"]["text"]
    assert "calls.py: Phone calls for the owner." in pages["design"]["text"]
    assert not any("secrets" in k for k in pages)


def test_search_and_pages(repo):
    hits = codedocs.search("how does Buddy place a phone call")
    assert "[backend/buddy/calls.py]" in hits.split("\n\n")[0] and "(Open one with doc" in hits
    assert "costing" not in codedocs.search("cost")  # word forms: "costs" is found by "cost"
    assert "Costs" in codedocs.search("cost")
    assert codedocs.page("calls").startswith("backend/buddy/calls.py (server)")
    assert codedocs.page("tools").startswith("All of Buddy's tools")
    with pytest.raises(Exception):
        codedocs.page("nothing-like-this")
    assert "Buddy is a personal assistant" in codedocs.search("")


def test_rebuilt_when_the_code_changes(repo):
    assert "dial(" in codedocs.page("calls.py")
    import os
    import time
    (repo / "backend/buddy/calls.py").write_text(MODULE.replace("def dial", "def ring"))
    later = time.time() + 5
    os.utime(repo / "backend/buddy/calls.py", (later, later))
    assert "ring(" in codedocs.page("calls.py") and "dial(" not in codedocs.page("calls.py")


def test_components_are_explained_from_their_code(repo):
    (repo / "backend/buddy/gateway.py").write_text('''"""Buddy Connect: every outside API Buddy uses, in one catalog.

call() adds the key, tries the backup host and logs every call."""''')
    (repo / "backend/buddy/free_models.py").write_text('''"""Free-tier AI models for general tasks.

OpenRouter: many free models, a general fallback."""''')
    pages = codedocs.build()
    connect = pages["component:buddy connect"]["text"]
    assert "How it works (from gateway.py):" in connect and "tries the backup host" in connect
    assert "OpenRouter" in pages["component:free ai models"]["text"]
    assert "Phone calls" in pages["component:phone calls"]["text"]
    assert "- Buddy Connect: One catalog" in pages["design"]["text"]
    assert "component:buddy connect" in codedocs.search("buddy connect backup host")
    page = codedocs_visual.html()
    assert "<h2>Components</h2>" in page and "Buddy Connect" in page and "OpenRouter" in page
    assert "Privacy check" in page and "Personal-data check" in page  # their animated diagrams


def test_visual_page_has_diagrams_and_escapes_the_code(repo):
    page = codedocs_visual.html()
    assert page.startswith("<!doctype html>") and page.count("<svg") >= 5
    assert "animateMotion" in page and "@keyframes lit" in page and "prefers-reduced-motion" in page
    assert "<script>alert(1)</script>" not in page and "&lt;script&gt;alert(1)" in page
    assert "calls.py" in page and "A phone call, step by step" in page
    assert page.count("<script>") == 1  # only the page's own search box


def test_buddy_code_tool_reads_and_exports_docs(repo, tmp_path, monkeypatch):
    from buddy import makers
    monkeypatch.setattr(makers, "FILES_DIR", tmp_path / "files")
    assert "Design overview" in asyncio.run(selfcode.run_tool({"action": "design"}))
    assert "[backend/buddy/calls.py]" in asyncio.run(selfcode.run_tool({"action": "docs", "query": "phone call"}))
    assert "phone_calls" in asyncio.run(selfcode.run_tool({"action": "doc", "path": "calls.py"}))
    m = Memory(tmp_path / "b.db")

    async def no_qa(memory, free=None):
        return [{"component": "Phone calls", "question": "How is a call placed?", "answer": "Plivo dials.",
                 "steps": [["Your yes", "Buddy asks"], ["Plivo dials", "Buddy's number"]]}]
    from buddy import codedocs_qa
    monkeypatch.setattr(codedocs_qa, "generate", no_qa)
    got = json.loads(asyncio.run(selfcode.run_tool({"action": "export_docs"}, memory=m)))
    assert got["made"] == "Buddy - how it works.html"
    saved = list((tmp_path / "files").glob("*.html"))
    assert saved and "<svg" in saved[0].read_text() and "Q: How is a call placed?" in saved[0].read_text()
    assert makers.MIME["html"] == "text/html"


class Writer:
    """Stands in for the free models: writes Q&A for each component, counts how often it's asked."""

    def __init__(self, reply=None, fail=False):
        self.asked, self.fail = [], fail
        self.reply = reply or json.dumps([
            {"question": "How does it work? <b>", "answer": "It reads the catalog.",
             "steps": [["Find the service", "in the catalog"], ["Privacy check", "personal data?"],
                       ["Logged", "for Insights"]]},
            {"question": "", "answer": "dropped: no question"}])

    async def run(self, kind, text, instruction="", **kw):
        self.asked.append(instruction)
        if self.fail:
            raise RuntimeError("all busy")
        return {"answer": "Sure! " + self.reply}

    async def _claude(self, system, prompt, kind=""):
        self.asked.append("claude")
        return self.reply


def test_questions_and_answers_are_written_once_and_drawn(repo, tmp_path):
    from buddy import codedocs_qa
    (repo / "backend/buddy/gateway.py").write_text('''"""Buddy Connect: every outside API in one catalog."""''')
    m, w = Memory(tmp_path / "b.db"), Writer()
    qa = asyncio.run(codedocs_qa.generate(m, w))
    names = {q["component"] for q in qa}
    assert {"Buddy Connect", "Phone calls"} <= names and len(qa) == len(names)  # the empty question was dropped
    assert "Buddy Connect: every outside API" in next(a for a in w.asked if "Component: Buddy Connect" in a)
    first = len(w.asked)
    asyncio.run(codedocs_qa.generate(m, w))
    assert len(w.asked) == first  # nothing changed: nothing rewritten
    (repo / "backend/buddy/gateway.py").write_text('''"""Buddy Connect: changed."""''')
    import os
    import time
    os.utime(repo / "backend/buddy/gateway.py", (time.time() + 5, time.time() + 5))
    asyncio.run(codedocs_qa.generate(m, w))
    assert len(w.asked) == first + 1  # only Buddy Connect again
    page = codedocs_visual.html(codedocs_qa.saved(m))
    assert "<h2>Questions and answers</h2>" in page and "Q: How does it work? &lt;b&gt;" in page
    assert "Privacy check" in page
    assert "Q (Buddy Connect): How does it work?" in codedocs_qa.search(m, "how does buddy connect work")


def test_claude_writes_them_when_free_models_cant(repo, tmp_path):
    from buddy import codedocs_qa
    m, w = Memory(tmp_path / "b.db"), Writer(fail=True)
    qa = asyncio.run(codedocs_qa.generate(m, w))
    assert qa and "claude" in w.asked
    assert codedocs_qa.parse("no json here") == [] and codedocs_qa.parse('[{"question": "q", "answer": "a", '
                                                                          '"steps": [["only one", "x"]]}]')[0]["steps"] == []
