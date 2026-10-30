"""The code sandbox: what the sandbox service allows, and the coding agent driving it."""

import asyncio
import importlib.util
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from buddy import codejob, sandbox
from buddy.jobs import JobContext, JobRunner
from buddy.memory import Memory

SPEC = importlib.util.spec_from_file_location(
    "sandboxd", Path(__file__).resolve().parents[2] / "deploy" / "sandbox" / "sandboxd.py")


@pytest.fixture
def sbx(tmp_path, monkeypatch):
    mod = importlib.util.module_from_spec(SPEC)
    SPEC.loader.exec_module(mod)
    monkeypatch.setattr(mod, "ROOT", tmp_path / "ws")
    (tmp_path / "ws").mkdir()
    ran = []

    def fake_run(args, timeout, stdin=None):
        ran.append((args, stdin))
        return {"exit_code": 0, "output": "ok"}
    monkeypatch.setattr(mod, "_run", fake_run)
    monkeypatch.setattr(mod, "_size_mb", lambda ws: 1)
    mod.ran = ran
    return mod


def test_containers_are_locked_down(sbx):
    args = sbx.docker_run(Path("/x/ws"), ["true"])
    joined = " ".join(args)
    for flag in ("--cap-drop ALL", "no-new-privileges", "--read-only", "--pids-limit", "--memory",
                 "--network buddy-sandbox", "dst=/work"):
        assert flag in joined
    assert "docker.sock" not in joined and "/etc/buddy" not in joined
    assert "--network none" in " ".join(sbx.docker_run(Path("/x/ws"), ["true"], network=False))


def test_names_paths_and_repos_are_checked(sbx):
    for bad in ("../etc", "A", "", "x" * 50, "a/b"):
        with pytest.raises(sbx.Refused):
            sbx.workspace(bad)
    for bad in ("/etc/passwd", "../x", "a/../../b"):
        with pytest.raises(sbx.Refused):
            sbx.safe_path(bad)
    assert sbx.safe_path("src/app.py") == "/work/src/app.py"
    sbx.handle({"op": "create", "workspace": "app"})
    with pytest.raises(sbx.Refused):
        sbx.handle({"op": "clone", "workspace": "app", "repo": "https://evil.example/x/y"})
    with pytest.raises(sbx.Refused):
        sbx.handle({"op": "run", "workspace": "nope", "command": "ls"})


def test_token_only_for_clone_and_push_and_never_echoed(sbx, monkeypatch):
    monkeypatch.setattr(sbx, "TOKEN", "github_pat_SECRET")
    sbx.handle({"op": "create", "workspace": "app"})
    sbx.handle({"op": "run", "workspace": "app", "command": "pytest"})
    run_args = " ".join(sbx.ran[-1][0])
    assert "SECRET" not in run_args  # code never sees the token
    monkeypatch.setattr(sbx, "_run", lambda a, t, s=None: (sbx.ran.append((a, s)) or
                                                          {"exit_code": 0, "output": "auth github_pat_SECRET ok"}))
    out = sbx.handle({"op": "clone", "workspace": "app", "repo": "https://github.com/me/app"})
    assert "SECRET" not in out["output"] and "--env AUTH=x-access-token:github_pat_SECRET@" in " ".join(sbx.ran[-1][0])


def test_push_only_to_buddy_branches_with_a_clean_git_config(sbx, monkeypatch, tmp_path):
    sbx.handle({"op": "create", "workspace": "app"})
    with pytest.raises(sbx.Refused):
        sbx.handle({"op": "push", "workspace": "app", "branch": "main"})
    with pytest.raises(sbx.Refused):  # no token set
        sbx.handle({"op": "push", "workspace": "app", "branch": "buddy/fix"})
    monkeypatch.setattr(sbx, "TOKEN", "github_pat_SECRET")
    git = tmp_path / "ws" / "app" / ".git"
    git.mkdir()
    (git / "config").write_text('[url "https://evil.example/"]\n\tinsteadOf = https://github.com/\n')
    monkeypatch.setattr(sbx, "_run", lambda a, t, s=None: {"exit_code": 0, "output": "https://github.com/me/app.git"})
    out = sbx.handle({"op": "push", "workspace": "app", "branch": "buddy/fix"})
    assert "evil" not in (git / "config").read_text() and out["url"].endswith("/me/app/tree/buddy/fix")


def test_workspace_names_from_repos_and_tasks():
    assert sandbox.workspace_name("https://github.com/me/My_App.git") == "my-app"
    assert sandbox.workspace_name("write a script that renames photos") == "write-a-script-that"


def test_coding_agent_works_through_the_sandbox_and_reports(monkeypatch):
    calls = []

    async def fake_call(op, socket_path=None, timeout=1900, **args):
        calls.append((op, args))
        if op == "list":
            return {"exit_code": 0, "output": "" if len([c for c in calls if c[0] == "list"]) == 1 else "app.py"}
        if op == "diff":
            return {"exit_code": 0, "output": " M app.py\n---\n app.py | 2 +-\n-bug\n+fix"}
        return {"exit_code": 0, "output": "1 passed"}
    monkeypatch.setattr(sandbox, "call", fake_call)
    script = [
        [SimpleNamespace(type="tool_use", name="run_command", input={"command": "pytest -q"}, id="a")],
        [SimpleNamespace(type="tool_use", name="finish", input={"summary": "Fixed the bug.", "checked": "1 passed"},
                         id="b")],
    ]

    class Stream:
        def __init__(self, content):
            self.content = content

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get_final_message(self):
            return SimpleNamespace(content=self.content, stop_reason="tool_use", model="claude-sonnet-5-5",
                                   usage=SimpleNamespace(input_tokens=10, output_tokens=5))
    monkeypatch.setattr(codejob.client.beta.messages, "stream", lambda **kw: Stream(script.pop(0)))
    mem = Memory(":memory:")
    JobRunner(mem, None)  # creates the jobs table
    mem.db.execute("INSERT INTO jobs (id, kind, title, params, status, created_at) VALUES (1, 'code', 't', '{}', "
                   "'running', '2026')")
    job = {"id": 1, "title": "Fix the bug", "params": {"task": "fix it", "repo": "https://github.com/me/app",
                                                       "workspace": "app"}}
    result = asyncio.run(codejob.run_code_task(job, JobContext(mem, job)))
    ops = [c[0] for c in calls]
    assert ops[:3] == ["create", "list", "clone"] and "run" in ops and ops[-1] == "diff"
    assert result.summary == "Fixed the bug." and result.findings == ["changed:yes"]
    assert "```diff" in result.report and "pytest -q" in result.report


def test_buddy_talks_to_the_service_over_its_socket(sbx, tmp_path, monkeypatch):
    sock = str(tmp_path / "s.sock")
    monkeypatch.setattr(sbx, "SOCKET", sock)

    async def body():
        server = asyncio.create_task(sbx.serve())
        for _ in range(50):
            if os.path.exists(sock):
                break
            await asyncio.sleep(0.02)
        assert sandbox.available(sock)
        assert (await sandbox.call("create", socket_path=sock, workspace="demo"))["ok"]
        status = await sandbox.call("status", socket_path=sock)
        assert [w["name"] for w in status["workspaces"]] == ["demo"] and status["token"] is False
        with pytest.raises(sandbox.SandboxError, match="buddy/"):
            await sandbox.call("push", socket_path=sock, workspace="demo", branch="main")
        server.cancel()
    asyncio.run(body())
    with pytest.raises(sandbox.SandboxError, match="isn't running"):
        asyncio.run(sandbox.call("status", socket_path=str(tmp_path / "missing.sock")))
