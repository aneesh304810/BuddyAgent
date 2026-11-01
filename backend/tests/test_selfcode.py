"""Buddy reads its own code (never secrets) and fixes itself only through a code task on its repo."""

import asyncio

import pytest

from buddy import selfcode
from buddy.tools import ToolError


@pytest.fixture
def repo(tmp_path, monkeypatch):
    (tmp_path / "backend" / "buddy").mkdir(parents=True)
    (tmp_path / "backend" / "buddy" / "router.py").write_text("def pick(text):\n    return 'haiku' if text else 'sonnet'\n")
    (tmp_path / "backend" / ".env").write_text("ANTHROPIC_API_KEY=sk-secret\n")
    (tmp_path / "backend" / "buddy.db").write_bytes(b"SQLite format 3")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config").write_text("token=pick\n")
    monkeypatch.setattr(selfcode, "ROOT", tmp_path)
    return tmp_path


def test_search_and_read_the_code_but_never_secrets(repo):
    found = asyncio.run(selfcode.run_tool({"action": "search", "query": "pick"}))
    assert "backend/buddy/router.py:1: def pick(text):" in found and ".git" not in found
    assert "sk-secret" not in asyncio.run(selfcode.run_tool({"action": "search", "query": "API_KEY"}))
    out = asyncio.run(selfcode.run_tool({"action": "read", "path": "backend/buddy/router.py"}))
    assert "    2      return 'haiku'" in out
    for bad in ("backend/.env", "backend/buddy.db", "../etc/passwd", ".git/config"):
        with pytest.raises(ToolError):
            selfcode.read(bad)
    assert "backend/buddy/  (1 files)" in selfcode.overview()


def test_fix_is_a_code_task_on_buddys_repo():
    started = []

    async def start_task(args):
        started.append(args)
        return "Shall I start?"
    out = asyncio.run(selfcode.run_tool({"action": "fix", "task": "router.py:2 picks Haiku for every text"}, start_task))
    assert out == "Shall I start?" and started[0]["repo"] == selfcode.REPO and started[0]["branch"] == selfcode.BRANCH
    assert started[0]["task"].endswith("router.py:2 picks Haiku for every text") and "pytest" in started[0]["task"]
    with pytest.raises(ToolError, match="sandbox"):
        asyncio.run(selfcode.run_tool({"action": "fix", "task": "x"}, None))
