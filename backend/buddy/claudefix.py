"""Hand a code fix to Claude Code: Buddy opens a GitHub issue on its own repo, the Claude GitHub Action fixes it on a
buddy/claude-... branch (on the owner's Claude subscription), and Buddy tells the owner when it's ready to review.
Deploying it goes through self-deploy as usual: tests again, guard rails refused, merge, deploy, roll back if sick.

Every step that acts needs the owner's yes, read in code (thrift). The brief is code instructions only: never the
owner's personal data (it becomes issue text on GitHub). The GitHub token stays in the sandbox service.
"""

import json
import re
from datetime import datetime, timezone

from . import sandbox, selfdeploy

PENDING_KEY = "claudefix:pending"
CHECK_EVERY_S = 180
GIVE_UP_HOURS = 3
PER_DAY = 5  # issues a day, at most: each run uses the owner's subscription

TOOL = {
    "name": "claude_code_fix",
    "description": "Hand a fix to Buddy's own code to Claude Code (the GitHub Action on Buddy's repo, run on the "
    "owner's Claude subscription, not Buddy's API budget): use it when the user asks to send a fix to Claude, or for "
    "a bigger change to Buddy itself. send: opens a GitHub issue with the brief (asks the user first); Buddy says "
    "when Claude has pushed a branch. status: what Claude has done. deploy: test, merge and deploy Claude's branch "
    "through self-deploy (asks the user first). The brief is code instructions only: what's wrong, where, what "
    "done looks like; never the user's personal data, since it becomes issue text on GitHub.",
    "input_schema": {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["send", "status", "deploy"]},
            "title": {"type": "string", "description": "send: a short title"},
            "brief": {"type": "string", "description": "send: what to fix, where, and how to know it's done"},
            "number": {"type": "integer", "description": "status/deploy: the issue number (default: the latest)"},
        },
        "required": ["action"],
        "additionalProperties": False,
    },
}

# What must never go into an issue: the brief is about code, and GitHub is outside Buddy's privacy boundary.
PERSONAL = re.compile(r"\b(\d{3}-\d{2}-\d{4}|\d{13,19}|password|passcode|pin code|account number|routing number|"
                      r"diagnos|pathology|lab result|prescription|salary)\b", re.IGNORECASE)


def pending(memory) -> list[dict]:
    try:
        return json.loads(memory.get(PENDING_KEY) or "[]")
    except ValueError:
        return []


def _save(memory, items: list[dict]) -> None:
    memory.set(PENDING_KEY, json.dumps(items[-10:]))


def _sent_today(memory) -> int:
    today = datetime.now(timezone.utc).date().isoformat()
    return sum(1 for i in pending(memory) if i.get("at", "").startswith(today))


def _pick(memory, number) -> dict | None:
    items = pending(memory)
    if number:
        return next((i for i in items if i["number"] == int(number)), None)
    return items[-1] if items else None


def finished(comments: list[dict]) -> str:
    """Claude's closing report, once it's done ('' while it's still working)."""
    for c in reversed(comments or []):
        body = c.get("body", "")
        if re.search(r"what's new|files changed|claude finished|\*\*claude finished", body, re.IGNORECASE):
            return body
    return ""


def _short(report: str, limit: int = 1500) -> str:
    text = re.sub(r"<[^>]+>|\[([^\]]*)\]\([^)]*\)", r"\1", report)  # no html or link clutter
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text if len(text) <= limit else text[:limit - 1] + "…"


async def run_tool(memory, thrift, args: dict) -> str:
    action = args.get("action")
    if action == "send":
        title, brief = (args.get("title") or "").strip(), (args.get("brief") or "").strip()
        if not title or len(brief) < 20:
            return "I need a short title and a brief: what's wrong, where, and how to know it's fixed."
        if PERSONAL.search(f"{title} {brief}"):
            return ("That brief has personal details in it, and it would become issue text on GitHub. Rewrite it as "
                    "code instructions only.")
        if _sent_today(memory) >= PER_DAY:
            return f"I've sent {PER_DAY} fixes to Claude today already; let's wait for those."

        async def go() -> str:
            try:
                got = await sandbox.call("claude_issue", title=title, body=brief, timeout=60)
            except sandbox.SandboxError as e:
                return f"I couldn't open the issue: {e}"
            items = pending(memory)
            items.append({"number": got["number"], "url": got["url"], "title": title,
                          "at": datetime.now(timezone.utc).isoformat(), "told": False, "checked": 0})
            _save(memory, items)
            return (f"Sent to Claude Code as issue #{got['number']} ({got['url']}). It usually takes 5 to 15 minutes; "
                    "I'll tell you when the branch is ready to review.")
        if thrift is None:
            return "I need your yes for that, and I can't ask right now."
        return thrift.ask_action(f"Send this fix to Claude Code as a GitHub issue: \"{title[:100]}\"? It runs on your "
                                 "Claude subscription and only pushes a branch; nothing is deployed without you.", go)

    item = _pick(memory, args.get("number"))
    if not item and args.get("number"):
        # Not in my list (e.g. the send looked failed but GitHub did open it): look it up. The sandbox only answers
        # for issues Buddy opened (they carry Buddy's line), so this can't reach anyone else's issue.
        item = {"number": int(args["number"]), "url": "", "title": f"issue #{args['number']}",
                "at": datetime.now(timezone.utc).isoformat(), "told": True, "checked": 0}
    if not item:
        return "There's no fix sent to Claude Code yet."
    try:
        st = await sandbox.call("claude_issue_status", number=item["number"], timeout=60)
    except sandbox.SandboxError as e:
        return f"I couldn't check issue #{item['number']}: {e}"
    report = finished(st.get("comments", []))
    if action == "status":
        if report:
            return f"Claude finished issue #{item['number']} (branch {st.get('branch') or 'not found'}):\n{_short(report)}"
        latest = (st.get("comments") or [{}])[-1].get("body", "")
        return (f"Issue #{item['number']} ({st.get('url')}): Claude is still on it."
                + (f" Latest: {_short(latest, 400)}" if latest else " No comment from Claude on it yet. I can't see "
                   "GitHub's Actions runs, so I don't know why: the Actions tab shows whether a run started, was "
                   "skipped or failed."))
    if action == "deploy":
        branch = st.get("branch", "")
        if not branch or not report:
            return f"Claude hasn't pushed a finished branch for issue #{item['number']} yet."
        if thrift is None:
            return "I need your yes for that, and I can't ask right now."

        async def ship() -> str:
            return selfdeploy.request(branch)
        return thrift.ask_action(f"Test, merge and deploy Claude's fix for issue #{item['number']} ({branch})? The "
                                 "server re-runs the tests first, refuses anything touching my guard rails, and rolls "
                                 "back if I don't come up healthy.", ship)
    return "Use send, status or deploy."


async def check(memory, deliver, now: float) -> None:
    """Every few minutes: tell the owner once when Claude has finished a fix (or gave no result in 3 hours)."""
    items = pending(memory)
    changed = False
    for item in items:
        if item.get("told") or now - item.get("checked", 0) < CHECK_EVERY_S:
            continue
        item["checked"] = now
        changed = True
        age_h = (datetime.now(timezone.utc) - datetime.fromisoformat(item["at"])).total_seconds() / 3600
        try:
            st = await sandbox.call("claude_issue_status", number=item["number"], timeout=60)
        except sandbox.SandboxError:
            continue
        report = finished(st.get("comments", []))
        failed = next((c["body"] for c in reversed(st.get("comments") or [])
                       if re.search(r"encountered an error|couldn't finish", c.get("body", ""), re.IGNORECASE)), "")
        if failed and not report:
            link = re.search(r"\((https://github\.com/[^)]+/actions/runs/\d+)\)", failed)
            if await deliver("job_done", f"Claude Code stopped with an error on issue #{item['number']} "
                             f"(\"{item['title'][:60]}\"). The run's log says why: {link.group(1) if link else item['url']}. "
                             "Tell me what to change and I'll send it again."):
                item["told"] = True
        elif report and st.get("branch"):
            if await deliver("job_done", f"Claude Code finished \"{item['title'][:80]}\" (issue #{item['number']}, "
                             f"branch {st['branch']}).\n{_short(report)}\nReview it, then say \"deploy Claude's fix\" "
                             "to test, merge and deploy it."):
                item["told"] = True
        elif age_h > GIVE_UP_HOURS:
            if await deliver("job_done", f"Claude Code hasn't finished issue #{item['number']} in {GIVE_UP_HOURS} "
                             f"hours. Have a look: {item['url']} (and the Actions tab)."):
                item["told"] = True
    if changed:
        _save(memory, items)
