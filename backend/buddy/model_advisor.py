"""Which model for a task: free when the task holds none of the owner's data and a free model is good enough for it,
otherwise a plainly-stated "no free model is good for this" and which Claude model to use, with its cost.

  1. Data: is any of the owner's data in it? Personal details in the request (free_models.check_general: emails,
     phone numbers, secrets, things from memory) or a repo/workspace that isn't known to be clean -> Claude, always.
     Buddy's own code holds none of the owner's data (keys and the database are never in the repo), so code tasks on
     it may go to a free model. Other clean repos: BUDDY_CLEAN_REPOS (comma-separated URLs).
  2. Size: free models are fine for a small, contained change (a rename, a label, a constant, a one-file fix) and
     unreliable for multi-step work across files (a refactor, a new feature, security, concurrency, the app).
  3. Which free model: the best one for code that can use tools (GitHub Models, Gemini, Mistral, Groq, OpenRouter's
     coding picks), ranked by free_models (suited, fast, reliable lately, Claude's verdicts on its answers), skipping
     ones resting after failures and ones whose recent code tasks mostly failed the tests.
  4. None good enough -> say why, and suggest Claude Haiku (small jobs, a few cents) or Sonnet (bigger, 50 cents to
     a few dollars). The owner's yes starts it.
After a free model's code task the tests decide (codejob): failing tests are reported as "the free model couldn't do
this" with the paid suggestion, never passed off as done.
"""

import json
import os
import re

from .config import settings

TOOL_CAPABLE = ("github", "gemini", "mistral", "deepseek", "openrouter", "groq", "cerebras")
SMALL = re.compile(r"(?i)\b(rename|label|typo|spelling|wording|text|string|message|comment|docstring|log line|"
                   r"constant|default|threshold|limit|timeout|one[- ]line|small|tiny|minor|bump|title|heading|"
                   r"change \S+ to|from \S+ to)\b")
LARGE = re.compile(r"(?i)\b(refactor|redesign|architect|several files|multiple files|across the|new feature|"
                   r"migrat|security|auth|encrypt|schema|concurren|race condition|thread|performance|rewrite|"
                   r"integrat|android|kotlin|deploy|budget|approval|privacy|payment|bank)\b")
PAID = {"haiku": ("Claude Haiku (the quick model)", "about 2 to 10 cents"),
        "sonnet": ("Claude Sonnet (the main model)", "about 50 cents to 3 dollars")}
CODE_TASK_MIN_RUNS, CODE_TASK_MIN_OK = 2, 0.5


def clean_repo(repo: str) -> bool:
    """A repo known to hold none of the owner's data: Buddy's own, or one the owner listed."""
    from .selfcode import REPO  # noqa: PLC0415
    norm = lambda u: str(u or "").strip().rstrip("/").removesuffix(".git").lower()  # noqa: E731
    listed = {norm(u) for u in (os.getenv("BUDDY_CLEAN_REPOS") or "").split(",") if u.strip()}
    return bool(repo) and norm(repo) in {norm(REPO)} | listed


def the_change(task: str) -> str:
    """The owner's own words (a self-fix brief puts them after 'The change: ')."""
    return task.split("The change: ", 1)[-1]


def size(task: str) -> str:
    words = the_change(task)
    if LARGE.search(words) or len(words) > 700:
        return "large"
    return "small" if SMALL.search(words) or len(words) < 220 else "large"


def code_record(memory, provider: str) -> tuple[int, int]:
    """(runs, passed) of this free model's recent code tasks."""
    try:
        r = json.loads(memory.get(f"free:codetask:{provider}") or "{}")
        return int(r.get("runs", 0)), int(r.get("ok", 0))
    except (ValueError, TypeError):
        return 0, 0


def note_code_result(memory, provider: str, ok: bool) -> None:
    runs, good = code_record(memory, provider)
    runs, good = min(runs, 9) + 1, min(good, 9) + (1 if ok else 0)  # the last ~10 tasks count
    memory.set(f"free:codetask:{provider}", json.dumps({"runs": runs, "ok": good}))


def advise_code(memory, task: str, repo: str, free=None) -> dict:
    """{route: free|claude, provider?, model?, paid: haiku|sonnet, why}."""
    from .free_models import FreeModels, PersonalData, check_general  # noqa: PLC0415
    big = size(task) == "large"
    paid = "sonnet" if big else "haiku"
    out = {"route": "claude", "paid": paid, "size": "large" if big else "small"}
    try:
        check_general(the_change(task), memory)
    except PersonalData:
        return {**out, "why": "the request includes your personal details, which only go to Claude"}
    except Exception:
        pass
    if not clean_repo(repo):
        return {**out, "why": ("it works on files that may hold your data (only Buddy's own code, or repos you list "
                               "in BUDDY_CLEAN_REPOS, go to free models)") if repo else
                ("a one-off task can touch your files, so it stays with Claude")}
    if big:
        return {**out, "why": "it's a bigger change across several steps or files, and free models aren't reliable "
                              "at that (they often leave it half done or break the tests)"}
    free = free or FreeModels(memory)
    if not free.enabled():
        return {**out, "why": "no free model is set up (BUDDY_FREE_MODELS and a free key, e.g. GitHub Models or "
                              "Gemini)"}
    resting = [n for n in free.enabled() if free.resting(n)]
    for name in free.order("code"):
        if name not in TOOL_CAPABLE:
            continue
        runs, good = code_record(memory, name)
        if runs >= CODE_TASK_MIN_RUNS and good / runs < CODE_TASK_MIN_OK:
            continue  # its recent code tasks mostly failed the tests
        return {"route": "free", "provider": name, "model": free.model_of(name, "code"), "paid": paid,
                "size": "small", "why": "a small change to code that holds none of your data"}
    why = "none of the free models is good enough right now"
    if resting:
        why += f" ({', '.join(resting)} resting after failures)"
    return {**out, "why": why + "; their recent code tasks failed the tests or they can't use tools"}


def say(advice: dict) -> str:
    """The advice in plain words for the owner."""
    if advice["route"] == "free":
        return (f"I'll use the free model {advice['model']} ({advice['provider']}): {advice['why']}. If it can't "
                f"pass the tests, I'll tell you and suggest {PAID[advice['paid']][0]}.")
    name, cost = PAID[advice["paid"]]
    return f"None of the free models is good for this one: {advice['why']}. I suggest {name}, {cost}."


def paid_model(choice: str) -> str:
    return settings.fast_model if choice == "haiku" else (os.getenv("BUDDY_CODE_MODEL") or settings.chat_model)


ADVICE_TOOL = {
    "name": "model_advice",
    "description": "Which model a task should use, and why: free when it holds none of the user's data and a free "
    "model is good enough; otherwise it says no free model is good for it and suggests Claude Haiku or Sonnet with "
    "the cost. Code tasks on Buddy's own repo hold none of the user's data. Use it when the user asks which model, "
    "or why something costs money.",
    "input_schema": {"type": "object", "properties": {
        "task": {"type": "string"}, "repo": {"type": "string", "description": "for a code task: the repo URL"}},
        "required": ["task"], "additionalProperties": False},
}


def run_tool(memory, args: dict) -> str:
    from .selfcode import REPO  # noqa: PLC0415
    repo = str(args.get("repo") or "")
    if repo.lower() in ("buddy", "self", "yourself"):
        repo = REPO
    advice = advise_code(memory, str(args.get("task") or ""), repo)
    return json.dumps({**advice, "say": say(advice)}, ensure_ascii=False)
