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
  outputs {workspace}                         files the task left in output/ (names and sizes)
  fetch   {workspace, path}                   one of those files, base64 (at most 20 MB)
  push    {workspace, branch}                 push to a buddy/... branch (Buddy asks the user first)
  write_b64 {workspace, path, content_b64}    write a binary file (a PDF, a spreadsheet; at most 15 MB)
  save_skill {workspace, folder, name, replace?}  keep a tool the task wrote, for later tasks (checked and
                                              tested first; saved tools are mounted read-only at /buddy-custom)
  skills  {}                                  the saved tools
  delete_skill {name}
  delete  {workspace}
  status  {}                                  workspaces and disk use
  browser {session?, cmd}                     Buddy's browser (browser/driver.py): one headless Chromium per
                                              session, in the same hardened container; cmd is one driver command
                                              (open, click, type, read, screenshot...); a new session starts when
                                              none is given; idle ones close after 15 minutes; at most 2 at once
  browser_close {session}

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
import secrets
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(os.getenv("SANDBOX_ROOT", "/var/lib/buddy-sandbox/ws"))
SOCKET = os.getenv("SANDBOX_SOCKET", "/run/buddy-sandbox/sandbox.sock")
IMAGE = os.getenv("SANDBOX_IMAGE", "buddy-sandbox:latest")
NETWORK = os.getenv("SANDBOX_NETWORK", "buddy-sandbox")
TOKEN = os.getenv("SANDBOX_GITHUB_TOKEN", "")
# The library of every tool on GitHub: built-in ones (publish_tools) and each one Buddy saves
# (save_skill) arrive there as buddy/... branches for the owner to merge. Empty = off.
SELF_REPO = os.getenv("SANDBOX_SELF_REPO", "aneesh304810/BuddyAgent")  # the only repo Claude fix issues go to
TOOLS_REPO = os.getenv("SANDBOX_TOOLS_REPO", "https://github.com/aneesh304810/Buddy-sandbox-tools")
MAX_WORKSPACES = int(os.getenv("SANDBOX_MAX_WORKSPACES", "10"))
MAX_WORKSPACE_MB = int(os.getenv("SANDBOX_MAX_WORKSPACE_MB", "4000"))
MAX_TIMEOUT = 1800
MAX_OUTPUT = 60_000
MAX_READ = 200_000
# Containers run as this service's own user, so it can tidy the files they make.
UID = os.getuid()
LIBRARY = Path(os.getenv("SANDBOX_LIBRARY", "/usr/local/lib/buddy-sandbox/library"))
TOOL_ENV = {"PYTHONUSERBASE": "/work/.tools/py", "PIP_USER": "1", "NPM_CONFIG_PREFIX": "/work/.tools/npm",
            "PATH": "/work/.tools/bin:/work/.tools/py/bin:/work/.tools/npm/bin:/usr/local/bin:/usr/bin:/bin",
            "PIP_DISABLE_PIP_VERSION_CHECK": "1", "BUDDY_LIBRARY": "/buddy"}
AUDIT = Path(os.getenv("SANDBOX_AUDIT", "/var/lib/buddy-sandbox/commands.log"))
# Tools Buddy made during tasks and chose to keep: data tools, so offline code only.
CUSTOM = Path(os.getenv("SANDBOX_CUSTOM", "/var/lib/buddy-sandbox/skills"))
SKILL_FILES = {".py", ".md", ".txt", ".json", ".csv", ".yaml", ".yml"}
SKILL_MAX_BYTES, SKILL_MAX_FILES = 300_000, 12
# A saved tool processes data it's given; it must not reach the network, start programs or load code.
UNSAFE_CODE = re.compile(
    r"^\s*(?:import|from)\s+(?:subprocess|socket|ctypes|requests|urllib|http|httpx|ftplib|smtplib|telnetlib|"
    r"paramiko|asyncio\.subprocess|multiprocessing|pty|webbrowser)\b|\bos\.(?:system|popen|exec\w*|spawn\w*|fork)\b|"
    r"\b(?:eval|exec|compile|__import__)\s*\(", re.M)
# Downloads piped straight into a shell run unknown code unseen; installs go through the registry.
PIPE_TO_SHELL = re.compile(r"(curl|wget)\b[^|;&]*\|\s*(sudo\s+)?(ba|z|da)?sh\b|\bbash\s+<\s*\(\s*(curl|wget)")
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


def _cpus(wanted: str) -> str:
    """Docker refuses more CPUs than the machine has (a 1-CPU droplet): ask for at most that many."""
    have = os.cpu_count() or 1
    return f"{min(float(wanted), float(have)):g}"


def docker_run(ws: Path, command: list[str], *, network: bool = True, env: dict | None = None,
               memory: str = "2g", cpus: str = "1.5", stdin: bool = False) -> list[str]:
    """The one way a container is started: hardened, limited, only the workspace mounted."""
    args = ["docker", "run", "--rm", "--init",
            "--user", f"{UID}:{UID}",
            "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
            "--read-only",
            "--tmpfs", "/tmp:rw,exec,size=1g",  # nosec B108: the container's own /tmp
            "--tmpfs", f"/home/dev:rw,exec,size=1g,uid={UID}",
            "--memory", memory, "--memory-swap", memory, "--cpus", _cpus(cpus), "--pids-limit", "512",
            "--network", NETWORK if network else "none",
            "--mount", f"type=bind,src={ws},dst=/work",
            "--workdir", "/work"]
    if LIBRARY.is_dir():  # Buddy's tool registry, install guard and skills, read-only
        args += ["--mount", f"type=bind,src={LIBRARY},dst=/buddy,readonly"]
    if CUSTOM.is_dir():  # tools Buddy made earlier, read-only
        args += ["--mount", f"type=bind,src={CUSTOM},dst=/buddy-custom,readonly"]
    # Tools installed during a task live in the workspace, so they last for the whole task.
    env = {**TOOL_ENV, **(env or {})}
    if stdin:
        args.append("-i")
    for key, value in env.items():
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


def _audit(workspace: str, command: str) -> None:
    """Every command run, for review (the service's own folder, not visible to containers)."""
    try:
        with AUDIT.open("a") as f:
            f.write(json.dumps({"at": __import__("datetime").datetime.now().isoformat(timespec="seconds"),
                                "workspace": workspace, "command": command[:500]}) + "\n")
    except OSError:
        pass


def skill_meta(text: str) -> tuple[dict, str]:
    """The SKILL.md front matter (simple 'key: value' lines, tags as [a, b]) and the guide after it."""
    match = re.match(r"(?s)^---\n(.*?)\n---\n?(.*)$", text)
    if not match:
        return {}, text
    meta = {}
    for line in match.group(1).splitlines():
        key, sep, value = line.partition(":")
        if sep:
            value = value.strip()
            if value.startswith("[") and value.endswith("]"):
                meta[key.strip()] = [t.strip().strip("'\"") for t in value[1:-1].split(",") if t.strip()]
            else:
                meta[key.strip()] = value[1:-1] if len(value) > 1 and value[0] == value[-1] and value[0] in "'\"" else value
    return meta, match.group(2).strip()


def list_skills() -> list[dict]:
    out = []
    for doc in sorted(CUSTOM.glob("*/SKILL.md")) if CUSTOM.is_dir() else []:
        meta, guide = skill_meta(doc.read_text(errors="replace"))
        out.append({"name": doc.parent.name, "description": meta.get("description", ""), "tags": meta.get("tags", []),
                    "run": meta.get("run", ""), "guide": guide[:900], "custom": True})
    return out


def save_skill(ws: Path, req: dict) -> dict:
    name = str(req.get("name", ""))
    if not NAME.match(name):
        raise Refused("tool names are lowercase letters, digits and dashes")
    folder = safe_path(str(req.get("folder", "")))
    src = (ws / folder.removeprefix("/work/").lstrip("/")).resolve()
    if not src.is_dir() or ws.resolve() not in src.parents:
        raise Refused("give the tool's folder in the workspace")
    files = [f for f in src.rglob("*") if f.is_file() or f.is_symlink()]
    if any(f.is_symlink() for f in files) or len(files) > SKILL_MAX_FILES:
        raise Refused(f"a tool is at most {SKILL_MAX_FILES} plain files (no links)")
    if any(f.suffix.lower() not in SKILL_FILES for f in files) or sum(f.stat().st_size for f in files) > SKILL_MAX_BYTES:
        raise Refused("a tool is Python scripts plus SKILL.md and small text/JSON/CSV samples, 300 KB at most")
    if not (src / "SKILL.md").is_file():
        raise Refused("the folder needs a SKILL.md")
    meta, _ = skill_meta((src / "SKILL.md").read_text(errors="replace"))
    missing = [k for k in ("description", "run", "test") if not meta.get(k)]
    if missing:
        raise Refused(f"SKILL.md front matter needs: {', '.join(missing)}")
    if f"/buddy-custom/{name}/" not in meta["run"]:
        raise Refused(f"run must call the script from /buddy-custom/{name}/")
    for f in files:
        if f.suffix == ".py" and UNSAFE_CODE.search(f.read_text(errors="replace")):
            raise Refused(f"{f.name} uses the network, starts programs or loads code: saved tools work offline on "
                          "the data they're given (standard library, pandas, openpyxl, pypdf)")
    rel = str(src.relative_to(ws.resolve()))
    test = _run(docker_run(ws, ["timeout", "--kill-after=5", "120", "bash", "-lc", f'cd "/work/{rel}" && {meta["test"]}'],
                           network=False), 130)
    if test["exit_code"] != 0:
        raise Refused(f"its test failed (exit {test['exit_code']}): {test['output'][-1500:]}")
    dest = CUSTOM / name
    if dest.exists() and not req.get("replace"):
        raise Refused(f"there's already a tool called {name}: pick another name, or replace it")
    CUSTOM.mkdir(parents=True, exist_ok=True)
    staging = CUSTOM / f".{name}.new"
    shutil.rmtree(staging, ignore_errors=True)
    shutil.copytree(src, staging)
    for f in staging.rglob("*"):
        f.chmod(0o755 if f.is_dir() else 0o644)
    shutil.rmtree(dest, ignore_errors=True)
    staging.rename(dest)
    _audit(ws.name, f"saved tool {name}")
    result = {"ok": True, "saved": name, "test_output": test["output"][-500:]}
    if TOOLS_REPO and TOKEN and req.get("publish", True):  # also to the tools library on GitHub, for review
        try:
            result["published"] = publish_tools([name], "custom")
        except Exception as e:  # saved either way; say why it isn't on GitHub
            result["publish_error"] = str(e)[:300]
    return result


TOOLS_README = """# Buddy's tools

Data tools Buddy runs in its code sandbox (offline scripts that print a small JSON result).

- `tools/built-in/`: the tools that ship with Buddy (from the BuddyAgent repo, deploy/sandbox/skills)
- `tools/buddy-made/`: tools Buddy wrote during tasks, tested and saved

Each folder has a SKILL.md (what it does, how it's run and tested) and its scripts. New tools arrive
as buddy/... branches: review and merge them here.
"""


def publish_tools(names, source: str) -> dict:
    """Push tools to TOOLS_REPO on a new buddy/... branch: built-in ("library") or Buddy-made ("custom")."""
    if not TOOLS_REPO or not REPO.match(TOOLS_REPO):
        raise Refused("no tools repo is set (SANDBOX_TOOLS_REPO)")
    if not TOKEN:
        raise Refused("no GitHub token is set for the sandbox (deploy/sandbox.sh token)")
    base, folder = (LIBRARY / "skills", "built-in") if source == "library" else (CUSTOM, "buddy-made")
    available = sorted(p.name for p in base.iterdir() if p.is_dir() and not p.name.startswith(".")) \
        if base.is_dir() else []
    chosen = available if names in ("all", ["all"], None, []) else [str(n) for n in names]
    unknown = [n for n in chosen if not NAME.match(n) or n not in available]
    if unknown or not chosen:
        raise Refused("no such tool: " + (", ".join(unknown) or "none to publish"))
    ws_name = f"tools-{secrets.token_hex(3)}"
    handle({"op": "create", "workspace": ws_name})
    try:
        cloned = handle({"op": "clone", "workspace": ws_name, "repo": TOOLS_REPO})
        if cloned.get("exit_code") != 0:
            raise Refused("couldn't clone the tools repo (does the token cover it?): " + cloned["output"][-300:])
        ws = workspace(ws_name)
        if not (ws / "README.md").exists():
            (ws / "README.md").write_text(TOOLS_README)
        for name in chosen:
            dest = ws / "tools" / folder / name
            shutil.rmtree(dest, ignore_errors=True)
            shutil.copytree(base / name, dest, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        stamp = time.strftime("%Y%m%d-%H%M")
        branch = f"buddy/tool-{chosen[0]}-{stamp}" if len(chosen) == 1 else f"buddy/tools-{folder}-{stamp}"
        what = chosen[0] if len(chosen) == 1 else f"{len(chosen)} {folder} tools"
        pushed = handle({"op": "push", "workspace": ws_name, "branch": branch, "message": f"Add {what}"})
        if pushed.get("exit_code") != 0:
            raise Refused("the push didn't work: " + pushed["output"][-300:])
        _audit(ws_name, f"published {what} to {TOOLS_REPO} ({branch})")
        return {"ok": True, "branch": branch, "url": pushed.get("url", ""), "tools": chosen}
    finally:
        handle({"op": "delete", "workspace": ws_name})


# --- Buddy's browser ---------------------------------------------------------------------------------------------
import select
import threading

BROWSER_IDLE, BROWSER_MAX, BROWSER_STEP = 15 * 60, 2, 60
_browsers: dict[str, dict] = {}
_browser_lock = threading.Lock()


def _browser_reap() -> None:
    now = time.time()
    for sid, b in list(_browsers.items()):
        if now - b["last"] > BROWSER_IDLE or b["proc"].poll() is not None:
            _browser_close(sid)


def _browser_close(sid: str) -> None:
    b = _browsers.pop(sid, None)
    if not b:
        return
    try:
        b["proc"].stdin.write('{"op": "close"}\n')
        b["proc"].stdin.flush()
        b["proc"].wait(timeout=10)
    except Exception:
        b["proc"].kill()
    shutil.rmtree(b["ws"], ignore_errors=True)


def _browser_line(b: dict, timeout: float) -> dict:
    ready, _, _ = select.select([b["proc"].stdout], [], [], timeout)
    if not ready:
        raise Refused("the page took too long")
    line = b["proc"].stdout.readline()
    if not line:
        raise Refused("the browser stopped")
    return json.loads(line)


def browser(req: dict) -> dict:
    with _browser_lock:
        _browser_reap()
        sid = str(req.get("session") or "")
        if sid and sid not in _browsers:
            raise Refused("that browser session has closed (idle too long); start again")
        if not sid:
            if len(_browsers) >= BROWSER_MAX:
                raise Refused("two browser tasks are already running; try again in a few minutes")
            sid = secrets.token_hex(6)
            ws = ROOT / f"browse-{sid}"
            ws.mkdir(parents=True, exist_ok=True)
            proc = subprocess.Popen(docker_run(ws, ["python3", "/opt/buddy-browser/driver.py"], memory="1500m",
                                               stdin=True), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                    stderr=subprocess.DEVNULL, text=True, bufsize=1)
            b = {"proc": proc, "ws": ws, "last": time.time()}
            _browsers[sid] = b
            try:
                _browser_line(b, 90)  # {"ready": true}
            except Exception:
                _browser_close(sid)
                raise
            _audit(f"browse-{sid}", "browser started")
        b = _browsers[sid]
        b["last"] = time.time()
        cmd = req.get("cmd") or {"op": "read"}
        if cmd.get("op") == "open":
            _audit(f"browse-{sid}", f"open {str(cmd.get('url', ''))[:200]}")
        b["proc"].stdin.write(json.dumps(cmd) + "\n")
        b["proc"].stdin.flush()
        reply = _browser_line(b, BROWSER_STEP)
        return {"session": sid, **reply}


def _github(method: str, path: str, body: dict | None = None) -> tuple[int, object]:
    """One GitHub API call on Buddy's own repo, with the sandbox's token (which never leaves this service)."""
    import urllib.error
    import urllib.request
    req = urllib.request.Request(f"https://api.github.com/repos/{SELF_REPO}{path}", method=method,
                                 data=json.dumps(body).encode() if body is not None else None, headers={
                                     "Authorization": f"Bearer {TOKEN}", "Accept": "application/vnd.github+json",
                                     "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "Buddy-sandbox"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:  # nosec B310: fixed https host
            return r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        return e.code, {"message": e.read(300).decode("utf-8", "replace")}


CLAUDE_LABEL = "claude-fix"
BUDDY_MARK = "_Sent by Buddy after the owner's yes._"  # in every issue Buddy opens: how Buddy knows its own issues


def claude_issue(req: dict) -> dict:
    """Open an issue on Buddy's own repo asking Claude Code (GitHub Action) to fix something. Only Buddy's repo,
    always labelled, always addressed to @claude; Buddy asks the owner first."""
    if not TOKEN:
        raise Refused("no GitHub token is set for the sandbox (deploy/sandbox.sh token)")
    title = " ".join(str(req.get("title") or "").split())[:120]
    brief = str(req.get("body") or "").strip()[:20000]
    if not title or len(brief) < 20:
        raise Refused("a fix needs a title and a brief")
    body = f"@claude please fix this in Buddy's code.\n\n{brief}\n\n{BUDDY_MARK}"
    # The label must exist first: GitHub quietly drops a label it doesn't know, and the workflow then skips the issue.
    _github("POST", "/labels", {"name": CLAUDE_LABEL, "color": "d97757",
                                "description": "Claude Code fixes this (sent by Buddy)"})  # 422 = already there
    code, got = _github("POST", "/issues", {"title": title, "body": body, "labels": [CLAUDE_LABEL]})
    if code != 201:
        hint = " (the sandbox's GitHub token needs Issues: read and write)" if code in (403, 404) else ""
        raise Refused(f"GitHub didn't open the issue (HTTP {code}){hint}")
    if CLAUDE_LABEL not in [lb.get("name") for lb in got.get("labels", [])]:
        # Only a marker: the workflow starts on the owner's issue with @claude. A token that may open issues but not
        # label them (HTTP 403) still works.
        _github("POST", f"/issues/{got['number']}/labels", {"labels": [CLAUDE_LABEL]})
    return {"number": got["number"], "url": got["html_url"]}


def claude_issue_status(req: dict) -> dict:
    """What Claude has done on one of these issues: its latest comments, and the branch it pushed."""
    if not TOKEN:
        raise Refused("no GitHub token is set for the sandbox")
    try:
        number = int(req.get("number"))
    except (TypeError, ValueError):
        raise Refused("which issue?") from None
    code, issue = _github("GET", f"/issues/{number}")
    if code != 200 or not (CLAUDE_LABEL in [lb.get("name") for lb in issue.get("labels", [])]
                           or BUDDY_MARK in str(issue.get("body") or "")):
        raise Refused("that isn't one of Buddy's Claude fix issues")
    _, comments = _github("GET", f"/issues/{number}/comments?per_page=50")
    # Claude's comments: from the Claude app (claude[bot]), or a cloud session writing as the owner (its report and
    # progress lines say so). The owner's own "@claude please start" is not one.
    by_claude = [c for c in comments or [] if "claude" in str(c.get("user", {}).get("login", "")).lower()
                 or re.match(r"\s*(\*\*)?claude code\b", str(c.get("body", "")), re.IGNORECASE)
                 or re.search(r"what's new", str(c.get("body", "")), re.IGNORECASE)]
    _, branches = _github("GET", "/branches?per_page=100")
    prefix = f"buddy/claude-issue-{number}-"
    mine = sorted(b["name"] for b in branches or [] if str(b.get("name", "")).startswith(prefix))
    return {"state": issue.get("state"), "url": issue.get("html_url"), "branch": mine[-1] if mine else "",
            "comments": [{"at": c.get("updated_at"), "body": str(c.get("body", ""))[:6000]} for c in by_claude[-3:]]}


def handle(req: dict) -> dict:
    op = req.get("op", "")
    if op == "claude_issue":
        return claude_issue(req)
    if op == "claude_issue_status":
        return claude_issue_status(req)
    if op == "browser":
        return browser(req)
    if op == "browser_close":
        with _browser_lock:
            _browser_close(str(req.get("session", "")))
        return {"ok": True}
    if op == "publish_tools":
        return publish_tools(req.get("names") or "all", str(req.get("source") or "library"))
    if op == "status":
        spaces = sorted(p.name for p in ROOT.iterdir() if p.is_dir()) if ROOT.exists() else []
        return {"workspaces": [{"name": n, "mb": _size_mb(ROOT / n)} for n in spaces], "token": bool(TOKEN)}
    if op == "skills":
        return {"skills": list_skills()}
    if op == "delete_skill":
        name = str(req.get("name", ""))
        if not NAME.match(name) or not (CUSTOM / name).is_dir():
            raise Refused("no saved tool with that name")
        shutil.rmtree(CUSTOM / name)
        return {"ok": True}
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
    if op in ("run", "clone", "write", "write_b64") and _size_mb(ws) > MAX_WORKSPACE_MB:
        raise Refused(f"the workspace is over {MAX_WORKSPACE_MB} MB: clean it up or delete it")

    if op == "run":
        command = str(req.get("command", ""))
        if not command.strip() or len(command) > 20_000:
            raise Refused("the command is empty or too long")
        timeout = max(5, min(int(req.get("timeout") or 600), MAX_TIMEOUT))
        if PIPE_TO_SHELL.search(command):
            raise Refused("piping a download into a shell isn't allowed: install tools with install_tool "
                          "(Buddy's registry) or pip / npm (checked by the install guard)")
        _audit(ws.name, command)
        return _run(docker_run(ws, ["timeout", "--kill-after=10", str(timeout), "bash", "-lc", command],
                               network=req.get("network", True) is not False), timeout)
    if op == "write":
        path = safe_path(req.get("path", ""))
        content = str(req.get("content", "")).encode()
        if len(content) > 2_000_000:
            raise Refused("files are at most 2 MB")
        script = 'mkdir -p "$(dirname "$1")" && cat > "$1"'
        return _run(docker_run(ws, ["sh", "-c", script, "sh", path], network=False, stdin=True), 60, content)
    if op == "write_b64":
        import base64
        import binascii

        path = safe_path(req.get("path", ""))
        try:
            content = base64.b64decode(str(req.get("content_b64", "")), validate=True)
        except (binascii.Error, ValueError) as e:
            raise Refused("that isn't base64") from e
        if len(content) > 15_000_000:
            raise Refused("files are at most 15 MB")
        script = 'mkdir -p "$(dirname "$1")" && cat > "$1"'
        return _run(docker_run(ws, ["sh", "-c", script, "sh", path], network=False, stdin=True), 120, content)
    if op == "save_skill":
        return save_skill(ws, req)
    if op == "read":
        path = safe_path(req.get("path", ""))
        return _run(docker_run(ws, ["head", "-c", str(MAX_READ), path], network=False), 60)
    if op == "list":
        path = safe_path(req.get("path", "."))
        return _run(docker_run(ws, ["sh", "-c", 'cd "$1" && git ls-files 2>/dev/null | head -500 || '
                                     'find . -maxdepth 3 -not -path "*/.git*" | head -500', "sh", path],
                               network=False), 60)
    if op == "outputs":
        r = _run(docker_run(ws, ["sh", "-c", "find /work/output -maxdepth 2 -type f -printf '%P\\t%s\\n' 2>/dev/null "
                                     "| head -50"], network=False), 60)
        files = []
        for line in r["output"].splitlines():
            name, _, size = line.partition("\t")
            if name and size.isdigit():
                files.append({"path": name, "size": int(size)})
        return {"files": files}
    if op == "fetch":
        path = safe_path("output/" + str(req.get("path", "")))
        script = 'test -f "$1" && [ "$(stat -c %s "$1")" -le 20000000 ] && base64 -w0 "$1"'
        try:
            done = subprocess.run(docker_run(ws, ["sh", "-c", script, "sh", path], network=False),
                                  capture_output=True, timeout=120)
        except subprocess.TimeoutExpired as e:
            raise Refused("reading that file took too long") from e
        if done.returncode != 0:
            raise Refused("no such file in output/, or it's over 20 MB")
        return {"base64": done.stdout.decode("ascii", "replace")}
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
