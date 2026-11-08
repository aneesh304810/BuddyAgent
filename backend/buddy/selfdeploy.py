"""Self-deploy, Buddy's side: after the owner's yes, ask the server's root-owned deployer to test, merge and deploy
Buddy's own fix (deploy/selfdeploy.sh does the work, and refuses anything touching Buddy's guard rails), then tell the
owner how it went: deployed, refused (and why), or rolled back.

Buddy can only drop a request here; it can't merge, deploy or skip the checks. Off until the owner runs
`sudo bash /opt/buddy/deploy/selfdeploy.sh on`.
"""

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

DIR = Path(os.getenv("BUDDY_DEPLOY_DIR", "/var/lib/buddy/deploy"))
FLAG = Path(os.getenv("BUDDY_SELFDEPLOY_FLAG", "/etc/buddy/selfdeploy-on"))
BRANCH = re.compile(r"^buddy/[A-Za-z0-9._-]{1,80}$")


def enabled() -> bool:
    return FLAG.exists() and DIR.is_dir()


def is_self(job: dict) -> bool:
    """A code task on Buddy's own repo."""
    from .selfcode import REPO  # noqa: PLC0415
    repo = str((job.get("params") or {}).get("repo") or "").rstrip("/").removesuffix(".git")
    return bool(repo) and repo.lower() == REPO.rstrip("/").lower()


def request(branch: str, job_id: int | None = None) -> str:
    if not BRANCH.match(branch):
        return "That isn't a buddy/... branch, so I can't deploy it."
    if not enabled():
        return "Self-deploy is off: sudo bash /opt/buddy/deploy/selfdeploy.sh on"
    tmp = DIR / "request.json.tmp"
    tmp.write_text(json.dumps({"branch": branch, "job": job_id, "at": datetime.now(timezone.utc).isoformat()}))
    tmp.replace(DIR / "request.json")  # the watcher starts on the finished file
    return (f"Asked the server to deploy {branch}: it runs the tests again, merges on GitHub, restarts me and checks "
            "I'm healthy (rolling back if not). I'll tell you how it went in a few minutes.")


def take_result() -> str:
    """The deployer's latest result as a message for the owner, once ('' when there's none)."""
    path = DIR / "result.json"
    try:
        r = json.loads(path.read_text())
        path.unlink()
    except (FileNotFoundError, ValueError, OSError):
        return ""
    head = {"ok": "Deployed my fix", "refused": "I didn't deploy my fix", "rolled_back": "My fix is rolled back",
            "failed": "The deploy failed"}.get(r.get("status"), "Deploy")
    return f"{head}: {r.get('message', '')}".strip()


def branch_from(push_reply: str) -> str:
    m = re.search(r"\b(buddy/[A-Za-z0-9._-]+)", push_reply or "")
    return m.group(1).rstrip(".") if m else ""


# review first, then deploy on the owner's word -------------------------------------------------------------------
READY_KEY = "selfdeploy:ready"


def mark_ready(memory, job: dict) -> None:
    """A finished fix waiting for the owner's review (newest last, the last 5)."""
    items = [i for i in ready(memory) if i["id"] != job["id"]]
    items.append({"id": job["id"], "title": job.get("title", ""), "at": datetime.now(timezone.utc).isoformat()})
    memory.set(READY_KEY, json.dumps(items[-5:]))


def ready(memory) -> list[dict]:
    try:
        return json.loads(memory.get(READY_KEY) or "[]")
    except ValueError:
        return []


def _done(memory, job_id: int) -> None:
    memory.set(READY_KEY, json.dumps([i for i in ready(memory) if i["id"] != job_id]))


def review_text(job: dict, limit: int = 3500) -> str:
    """What the fix does, for the owner to review: the task agent's own report (what's new, files, tests, how to
    test it), not cut to the usual one-paragraph summary."""
    report = str(job.get("report") or "")
    m = re.search(r"## What I did\n(.*?)(?=\n## |\Z)", report, re.S)
    text = (m.group(1) if m else str(job.get("summary") or "")).strip()
    return text if len(text) <= limit else text[:limit - 1] + "…"


def ship(memory, jobs, thrift, push, job_id: int | None = None) -> str:
    """The owner asked to deploy a reviewed fix: one more yes (read in code), then push, and the server tests, merges
    and deploys it. `push(job)` is codejob.push."""
    waiting = ready(memory)
    if not waiting:
        return "There's no finished fix waiting for review."
    pick = next((i for i in waiting if i["id"] == job_id), None) if job_id else waiting[-1]
    if pick is None:
        return f"Fix {job_id} isn't waiting for review. Waiting: " + "; ".join(f"{i['id']}: {i['title']}" for i in waiting)
    job = jobs.get(pick["id"]) if jobs else None
    if not job:
        _done(memory, pick["id"])
        return "That fix is gone (its job was cleaned up)."
    on = enabled()

    async def go() -> str:
        pushed = await push(job)
        _done(memory, job["id"])
        branch = branch_from(pushed)
        if on and branch:
            return f"{pushed} {request(branch, job['id'])}"
        return pushed + ("" if on else " Self-deploy is off, so merge it on GitHub and run update.sh.")
    question = (f"Push, merge and deploy \"{pick['title'][:80]}\"? The server re-runs the tests first, refuses anything "
                "touching my guard rails, and rolls back if I don't come up healthy." if on else
                f"Push \"{pick['title'][:80]}\" to a buddy branch on GitHub for you to merge?")
    if thrift is None:
        return "I need your yes for that, and I can't ask right now."
    return thrift.ask_action(question, go)
