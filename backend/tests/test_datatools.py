"""Data tools: code in the sandbox does the mechanical work; Buddy keeps the tools it makes."""

import asyncio
import base64
import importlib.util
import json
from pathlib import Path

import pytest

from buddy import datatools, sandbox
from buddy.memory import Memory
from buddy.tools import ToolError

SPEC = importlib.util.spec_from_file_location(
    "sandboxd_dt", Path(__file__).resolve().parents[2] / "deploy" / "sandbox" / "sandboxd.py")


def test_builtin_data_tools_are_listed():
    names = {t["name"] for t in datatools.builtin_tools()}
    assert {"spending-summary", "pdf-extract", "table-query", "receipts-extract", "doc-snippets",
            "compare-offers"} <= names
    assert all("{input}" in t["run"] for t in datatools.builtin_tools())


def test_process_data_runs_offline_in_a_throwaway_workspace(monkeypatch):
    mem = Memory(":memory:")
    mem.db.execute("CREATE TABLE IF NOT EXISTS transactions (id INTEGER PRIMARY KEY, day TEXT, amount REAL, "
                   "currency TEXT, merchant TEXT, category TEXT, card TEXT)")
    from buddy.tools import local_now
    today = local_now().date().isoformat()
    mem.db.execute("INSERT INTO transactions (day, amount, currency, merchant, category) VALUES (?, 42.5, 'USD', "
                   "'Whole Foods', 'Groceries')", (today,))
    calls = []

    async def fake_call(op, **kw):
        calls.append((op, kw))
        if op == "skills":
            return {"skills": []}
        if op == "run":
            return {"exit_code": 0, "output": json.dumps({"total_spent": 42.5})}
        return {"ok": True}

    monkeypatch.setattr(sandbox, "call", fake_call)
    out = asyncio.run(datatools.run_data_tool(mem, {"tool": "spending-summary", "source": {"kind": "transactions",
                                                                                          "days": 30}}))
    assert out.startswith(datatools.UNTRUSTED) and '"total_spent": 42.5' in out
    ops = [op for op, _ in calls]
    assert ops == ["skills", "create", "write_b64", "run", "delete"]
    run = dict(calls)["run"]
    assert run["network"] is False and "/work/input.csv" in run["command"]
    written = base64.b64decode(dict(calls)["write_b64"]["content_b64"]).decode()
    assert "Whole Foods,-42.5,USD" in written  # spending handed over bank style (negative)


def test_unknown_tool_and_a_failing_tool_are_explained(monkeypatch):
    async def fake_call(op, **kw):
        if op == "skills":
            return {"skills": []}
        if op == "run":
            return {"exit_code": 1, "output": "Traceback ... KeyError: 'amount'"}
        return {"ok": True}

    monkeypatch.setattr(sandbox, "call", fake_call)
    mem = Memory(":memory:")
    with pytest.raises(ToolError, match="Data tools:"):
        asyncio.run(datatools.run_data_tool(mem, {"tool": "nope", "source": {"kind": "text", "text": "x"}}))
    with pytest.raises(ToolError, match="failed"):
        asyncio.run(datatools.run_data_tool(mem, {"tool": "table-query", "source": {"kind": "text", "text": "a,b\n1,2",
                                                                                   "ext": "csv"}}))


def test_args_are_quoted_so_they_cant_add_commands(monkeypatch):
    seen = {}

    async def fake_call(op, **kw):
        if op == "skills":
            return {"skills": []}
        if op == "run":
            seen.update(kw)
            return {"exit_code": 0, "output": "{}"}
        return {"ok": True}

    monkeypatch.setattr(sandbox, "call", fake_call)
    asyncio.run(datatools.run_data_tool(Memory(":memory:"), {
        "tool": "doc-snippets", "source": {"kind": "text", "text": "hello"}, "args": "--question 'fees; rm -rf /'"}))
    assert "'fees; rm -rf /'" in seen["command"]


@pytest.fixture
def daemon(tmp_path, monkeypatch):
    mod = importlib.util.module_from_spec(SPEC)
    SPEC.loader.exec_module(mod)
    monkeypatch.setattr(mod, "ROOT", tmp_path / "ws")
    monkeypatch.setattr(mod, "CUSTOM", tmp_path / "skills")
    (tmp_path / "ws" / "job").mkdir(parents=True)
    tests = []

    def fake_run(args, timeout, stdin=None):
        tests.append(args)
        return {"exit_code": 0, "output": "1 passed"}

    monkeypatch.setattr(mod, "_run", fake_run)
    mod.tests = tests
    return mod


def write_tool(ws: Path, script: str, front: str = "") -> None:
    folder = ws / "tools" / "bank-clean"
    folder.mkdir(parents=True)
    (folder / "clean.py").write_text(script)
    (folder / "sample.csv").write_text("a,b\n1,2\n")
    (folder / "SKILL.md").write_text(front or (
        '---\nname: bank-clean\ndescription: "Messy bank export -> clean CSV"\ntags: [bank, csv]\ndata: true\n'
        'run: "python /buddy-custom/bank-clean/clean.py {input} {args}"\ntest: "python clean.py sample.csv"\n---\n'
        "# bank-clean\nUsage..."))


def test_a_tool_buddy_wrote_is_tested_saved_and_listed(daemon, tmp_path):
    write_tool(tmp_path / "ws" / "job", "import pandas as pd\nprint(pd.read_csv('sample.csv').shape)\n")
    out = daemon.handle({"op": "save_skill", "workspace": "job", "folder": "tools/bank-clean", "name": "bank-clean"})
    assert out["saved"] == "bank-clean" and (tmp_path / "skills" / "bank-clean" / "clean.py").exists()
    assert "--network" in daemon.tests[-1] and "none" in daemon.tests[-1]  # its test ran offline
    listed = daemon.handle({"op": "skills"})["skills"]
    assert listed[0]["name"] == "bank-clean" and listed[0]["run"].startswith("python /buddy-custom/bank-clean/")
    with pytest.raises(daemon.Refused, match="already"):
        daemon.handle({"op": "save_skill", "workspace": "job", "folder": "tools/bank-clean", "name": "bank-clean"})
    assert daemon.handle({"op": "delete_skill", "name": "bank-clean"})["ok"]
    assert daemon.handle({"op": "skills"})["skills"] == []


@pytest.mark.parametrize("script", ["import subprocess\nsubprocess.run(['ls'])\n", "import requests\n",
                                    "import os\nos.system('curl x')\n", "eval('1+1')\n", "from urllib import request\n"])
def test_tools_that_reach_out_or_run_programs_are_refused(daemon, tmp_path, script):
    write_tool(tmp_path / "ws" / "job", script)
    with pytest.raises(daemon.Refused, match="offline"):
        daemon.handle({"op": "save_skill", "workspace": "job", "folder": "tools/bank-clean", "name": "bank-clean"})


def test_a_tool_whose_test_fails_is_not_saved(daemon, tmp_path, monkeypatch):
    write_tool(tmp_path / "ws" / "job", "print('hi')\n")
    monkeypatch.setattr(daemon, "_run", lambda args, timeout, stdin=None: {"exit_code": 1, "output": "AssertionError"})
    with pytest.raises(daemon.Refused, match="test failed"):
        daemon.handle({"op": "save_skill", "workspace": "job", "folder": "tools/bank-clean", "name": "bank-clean"})
    assert not (tmp_path / "skills" / "bank-clean").exists()


def test_saved_tools_are_mounted_read_only(daemon, tmp_path):
    (tmp_path / "skills").mkdir()
    args = daemon.docker_run(tmp_path / "ws" / "job", ["true"])
    assert f"type=bind,src={tmp_path / 'skills'},dst=/buddy-custom,readonly" in args
