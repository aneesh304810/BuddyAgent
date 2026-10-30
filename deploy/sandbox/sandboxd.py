#!/usr/bin/env python3
"""The sandbox service: runs code for Buddy in throwaway containers, away from Buddy itself.

Runs as its own user (buddy-sandbox), the only one allowed to use Docker. Buddy talks to it
over a local socket (/run/buddy-sandbox/sandbox.sock, group buddy) with one JSON request per
connection, and can ask for only these things:

  create  {workspace}                         a new, empty workspace
  clone   {workspace, repo, branch?}          git clone (a GitHub token, if set, is used here only)
  run     {workspace, command, timeout?}      a shell command in the workspace
  write   {workspace, path, content}          write a file
  read    {workspace, path}                   read a file (first 200 KB)
  list    {workspace, path?}                  list files
  diff    {workspace}                         what changed since the clone
  push    {workspace, branch}                 push to a buddy/... branch (Buddy asks the user first)
  delete  {workspace}
  status  {}                                  workspaces and disk use

Every command, file read and write runs inside a container from the buddy-sandbox image:
non-root user, no Linux capabilities, read-only system, CPU, memory, process and time limits,
on a network that can reach the internet but not this server, the tailnet or the cloud
metadata service (deploy/sandbox.sh sets those firewall rules). Only the workspace folder
is mounted. The GitHub token never enters a container that runs code: it's used by clone
and push only, with the repo's own git config and hooks ignored, so code in the repo can't
redirect or steal it.
"""

import asyncio
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(os.getenv("SANDBOX_ROOT", "/var/lib/buddy-sandbox/ws"))
SOCKET = os.getenv("SANDBOX_SOCKET", "/run/buddy-sandbox/sandbox.sock")
IMAGE = os.getenv("SANDBOX_IMAGE", "buddy-sandbox:latest")
NETWORK = os.getenv("SANDBOX_NETWORK", "buddy-sandbox")
TOKEN = os.getenv("SANDBOX_GITHUB_TOKEN", "")
MAX_WORKSPACES = int(os.getenv("SANDBOX_MAX_WORKSPACES", "10"))
MAX_WORKSPACE_MB = int(os.getenv("SANDBOX_MAX_WORKSPACE_MB", "4000"))
MAX_TIMEOUT = 1800
MAX_OUTPUT = 60_000
MAX_READ = 200_000
# Containers run as this service's own user, so it can tidy the files they make.
UID = os.getuid()
CLIENT_GROUP = os.getenv("SANDBOX_CLIENT_GROUP", "buddy")

NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,40}$")
REPO = re.compile(r"^https://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(\.git)?/?$")
BRANCH = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,80}$")


class Refused(Exception):
    pass


def workspace(name: str) -> Path:
    if not NAME.match(name or ""):
        raise Refused("workspace names are lowercase letters, digits and dashes")
    return ROOT / name


def safe_path(path: str) -> str:
    """A path inside the workspace, as the container sees it (/work/...)."""
    path = (path or ".").strip()
    if path.startswith("/") or "\x00" in path or any(part == ".." for part in Path(path).parts):
        raise Refused("paths are relative to the workspace, without ..")
    return str(Path("/work") / path)


def docker_run(ws: Path, command: list[str], *, network: bool = True, env: dict | None = None,
               memory: str = "2g", cpus: str = "1.5", stdin: bool = False) -> list[str]:
    """The one way a container is started: hardened, limited, only the workspace mounted."""
    args = ["docker", "run", "--rm", "--init",
            "--user", f"{UID}:{UID}",
            "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
            "--read-only", "--tmpfs", "/tmp:rw,exec,size=1g",  # nosec B108: the container's own /tmp "--tmpfs", f"/home/dev:rw,exec,size=1g,uid={UID}",
            "--memory", memory, "--memory-swap", memory, "--cpus", cpus, "--pids-limit", "512",
            "--network", NETWORK if network else "none",
            "--mount", f"type=bind,src={ws},dst=/work",
            "--workdir", "/work"]
    if stdin:
        args.append("-i")
    for key, value in (env or {}).items():
        args += ["--env", f"{key}={value}"]
    return args + [IMAGE] + command


def _run(args: list[str], timeout: int, stdin: bytes | None = None) -> dict:
    try:
        done = subprocess.run(args, input=stdin, capture_output=True, timeout=timeout + 30)
    except subprocess.TimeoutExpired:
        return {"exit_code": 124, "output": f"Stopped after {timeout} seconds."}
    out = (done.stdout or b"") + (b"\n" + done.stderr if done.stderr else b"")
    text = out.decode("utf-8", "replace")
    if len(text) > MAX_OUTPUT:
        text = text[:MAX_OUTPUT // 2] + f"\n[... {len(text) - MAX_OUTPUT} characters cut ...]\n" + text[-MAX_OUTPUT // 2:]
    return {"exit_code": done.returncode, "output": text}


def _size_mb(ws: Path) -> int:
    done = subprocess.run(["du", "-sm", str(ws)], capture_output=True, text=True)
    return int((done.stdout.split() or ["0"])[0])


def _clean_git_config(ws: Path, owner: str, repo: str) -> None:
    """Before a push: the repo's own config could redirect it (url.insteadOf) and steal the token."""
    config = ws / ".git" / "config"
    if config.is_symlink() or not (ws / ".git").is_dir() or (ws / ".git").is_symlink():
        raise Refused("the workspace's .git folder isn't a normal folder")
    config.write_text("[core]\n\trepositoryformatversion = 0\n\tbare = false\n"
                      f'[remote "origin"]\n\turl = https://github.com/{owner}/{repo}.git\n')


def handle(req: dict) -> dict:
    op = req.get("op", "")
    if op == "status":
        spaces = sorted(p.name for p in ROOT.iterdir() if p.is_dir()) if ROOT.exists() else []
        return {"workspaces": [{"name": n, "mb": _size_mb(ROOT / n)} for n in spaces], "token": bool(TOKEN)}
    ws = workspace(req.get("workspace", ""))

    if op == "create":
        if ws.exists():
            return {"ok": True, "existed": True}
        if ROOT.exists() and sum(1 for p in ROOT.iterdir() if p.is_dir()) >= MAX_WORKSPACES:
            raise Refused(f"at most {MAX_WORKSPACES} workspaces: delete one first")
        ws.mkdir(parents=True, mode=0o700)
        return {"ok": True}
    if not ws.exists():
        raise Refused("no such workspace")
    if op == "delete":
        _run(docker_run(ws, ["sh", "-c", "rm -rf /work/* /work/.[!.]* /work/..?* 2>/dev/null; true"],
                        network=False), 120)
        shutil.rmtree(ws, ignore_errors=True)
        return {"ok": True}
    if op in ("run", "clone", "write") and _size_mb(ws) > MAX_WORKSPACE_MB:
        raise Refused(f"the workspace is over {MAX_WORKSPACE_MB} MB: clean it up or delete it")

    if op == "run":
        command = str(req.get("command", ""))
        if not command.strip() or len(command) > 20_000:
            raise Refused("the command is empty or too long")
        timeout = max(5, min(int(req.get("timeout") or 600), MAX_TIMEOUT))
        return _run(docker_run(ws, ["timeout", "--kill-after=10", str(timeout), "bash", "-lc", command],
                               network=req.get("network", True) is not False), timeout)
    if op == "write":
        path = safe_path(req.get("path", ""))
        content = str(req.get("content", "")).encode()
        if len(content) > 2_000_000:
            raise Refused("files are at most 2 MB")
        script = 'mkdir -p "$(dirname "$1")" && cat > "$1"'
        return _run(docker_run(ws, ["sh", "-c", script, "sh", path], network=False, stdin=True), 60, content)
    if op == "read":
        path = safe_path(req.get("path", ""))
        return _run(docker_run(ws, ["head", "-c", str(MAX_READ), path], network=False), 60)
    if op == "list":
        path = safe_path(req.get("path", "."))
        return _run(docker_run(ws, ["sh", "-c", 'cd "$1" && git ls-files 2>/dev/null | head -500 || '
                                     'find . -maxdepth 3 -not -path "*/.git*" | head -500', "sh", path],
                               network=False), 60)
    if op == "diff":
        return _run(docker_run(ws, ["sh", "-c", "git -c core.hooksPath=/dev/null add -A --intent-to-add >/dev/null 2>&1; "
                                     "git status --short && echo --- && git -c core.pager=cat diff HEAD --stat "
                                     "&& git -c core.pager=cat diff HEAD"], network=False), 120)
    if op == "clone":
        match = REPO.match(str(req.get("repo", "")))
        if not match:
            raise Refused("only https://github.com/owner/repo links")
        owner, repo = match.group(1), match.group(2)
        branch = str(req.get("branch") or "")
        if branch and not BRANCH.match(branch):
            raise Refused("that branch name isn't allowed")
        if any(ws.iterdir()):
            raise Refused("clone into an empty workspace")
        auth = f"x-access-token:{TOKEN}@" if TOKEN else ""
        script = ('git -c core.hooksPath=/dev/null clone --depth 50 ${BRANCH:+--branch "$BRANCH"} '
                  '"https://${AUTH}github.com/$OWNER/$REPO.git" /work '
                  '&& git -C /work remote set-url origin "https://github.com/$OWNER/$REPO.git" '
                  '&& git -C /work config user.name Buddy && git -C /work config user.email buddy@localhost')
        result = _run(docker_run(ws, ["sh", "-c", script], env={"AUTH": auth, "OWNER": owner, "REPO": repo,
                                                                 "BRANCH": branch}), 600)
        result["output"] = result["output"].replace(TOKEN, "***") if TOKEN else result["output"]
        return result
    if op == "push":
        branch = str(req.get("branch", ""))
        if not branch.startswith("buddy/") or not BRANCH.match(branch):
            raise Refused("Buddy pushes only to branches named buddy/...")
        if not TOKEN:
            raise Refused("no GitHub token is set for the sandbox (deploy/sandbox.sh token)")
        url = _run(docker_run(ws, ["git", "config", "--get", "remote.origin.url"], network=False), 30)["output"]
        match = REPO.match(url.strip().splitlines()[0] if url.strip() else "")
        if not match:
            raise Refused("the workspace isn't a GitHub clone")
        owner, repo = match.group(1), match.group(2)
        _clean_git_config(ws, owner, repo)
        script = ('git -c core.hooksPath=/dev/null -c user.name=Buddy -c user.email=buddy@localhost add -A && '
                  '(git -c core.hooksPath=/dev/null -c user.name=Buddy -c user.email=buddy@localhost '
                  'commit -q -m "$MESSAGE" || true) && '
                  'git -c core.hooksPath=/dev/null -c credential.helper= push '
                  '"https://x-access-token:${TOKEN}@github.com/$OWNER/$REPO.git" "HEAD:refs/heads/$BRANCH"')
        message = str(req.get("message") or "Changes from Buddy")[:500]
        result = _run(docker_run(ws, ["sh", "-c", script], env={"TOKEN": TOKEN, "OWNER": owner, "REPO": repo,
                                                                 "BRANCH": branch, "MESSAGE": message}), 300)
        result["output"] = result["output"].replace(TOKEN, "***")
        result["url"] = f"https://github.com/{owner}/{repo}/tree/{branch}"
        return result
    raise Refused(f"unknown op {op!r}")


async def serve() -> None:
    ROOT.mkdir(parents=True, exist_ok=True)

    async def client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            line = await asyncio.wait_for(reader.readline(), timeout=30)
            req = json.loads(line or b"{}")
            try:
                reply = await asyncio.to_thread(handle, req)
            except Refused as e:
                reply = {"error": str(e)}
            except Exception as e:  # never leak a traceback with paths into Buddy
                reply = {"error": f"the sandbox failed ({type(e).__name__})"}
            writer.write(json.dumps(reply).encode() + b"\n")
            await writer.drain()
        except Exception:
            pass
        finally:
            writer.close()

    if os.path.exists(SOCKET):
        os.unlink(SOCKET)
    server = await asyncio.start_unix_server(client, path=SOCKET, limit=4_000_000)
    os.chmod(SOCKET, 0o660)  # nosec B103: only this service and the buddy group (Buddy itself) can connect
    try:
        import grp

        os.chown(SOCKET, -1, grp.getgrnam(CLIENT_GROUP).gr_gid)
    except (KeyError, PermissionError):
        pass
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    if "--check" in sys.argv:
        print(json.dumps(handle({"op": "status"})))
    else:
        asyncio.run(serve())
