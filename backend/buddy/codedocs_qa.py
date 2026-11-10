"""Questions and answers about Buddy's design, each with a diagram, written from the generated documentation.

For every component (codedocs.COMPONENTS), a model reads that component's documentation and writes the questions
the owner is likely to ask ("How does Buddy Connect keep my data private?"), a short answer, and the answer's steps,
which the visual page draws as an animated diagram. The documentation is Buddy's code, which holds no personal
data, so the free models write them; Claude's quick model only when none of them can. Answers are kept and
rewritten only when that component's documentation changes.
"""

import hashlib
import json
import logging
import re

from . import codedocs

log = logging.getLogger("buddy.codedocs_qa")
KEY = "codedocs:qa"
PER_COMPONENT = 3

ASK = """You're writing the "Questions and answers" page of Buddy's technical documentation for Buddy's owner,
who is technical but didn't write this part. From ONLY the documentation below (don't invent anything it doesn't
say), write the {n} questions the owner would most likely ask about this component, each with a clear answer of 2 to
4 sentences, and the answer's steps for a diagram (3 to 7 steps, in order; each step a short title of at most 22
characters and a detail of at most 34 characters).

Reply with JSON only: [{{"question": "...", "answer": "...", "steps": [["title", "detail"], ...]}}, ...]

Component: {name}
{doc}"""


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def parse(text: str) -> list[dict]:
    """The model's JSON, cleaned: only well-formed items, with short steps."""
    m = re.search(r"\[.*\]", text or "", re.S)
    try:
        got = json.loads(m.group(0)) if m else []
    except json.JSONDecodeError:
        return []
    out = []
    for item in got if isinstance(got, list) else []:
        if not isinstance(item, dict):
            continue
        q, a = str(item.get("question") or "").strip(), str(item.get("answer") or "").strip()
        steps = [[str(s[0])[:24], str(s[1])[:36] if len(s) > 1 else ""] for s in item.get("steps") or []
                 if isinstance(s, (list, tuple)) and s][:7]
        if q and a:
            out.append({"question": q[:200], "answer": a[:900], "steps": steps if len(steps) >= 2 else []})
    return out[:PER_COMPONENT + 1]


async def _write(memory, free, name: str, doc: str) -> list[dict]:
    prompt = ASK.format(n=PER_COMPONENT, name=name, doc=doc[:9000])
    try:
        got = await free.run("explain", "", instruction=prompt, purpose="codedocs_qa",
                             system="You write accurate technical documentation. Reply with JSON only.")
        items = parse(got.get("answer", ""))
        if items:
            return items
    except Exception as e:  # e.g. a free model refused or all were busy: Claude's quick model instead
        log.info("free models couldn't write Q&A for %s (%s)", name, type(e).__name__)
    try:
        return parse(await free._claude("You write accurate technical documentation. Reply with JSON only.", prompt,
                                        "explain"))
    except Exception as e:
        log.warning("no Q&A for %s (%s)", name, type(e).__name__)
        return []


async def generate(memory, free=None) -> list[dict]:
    """Every component's questions and answers: kept ones reused, changed components rewritten."""
    import asyncio  # noqa: PLC0415
    if free is None:
        from .free_models import FreeModels  # noqa: PLC0415
        free = FreeModels(memory)
    modules = await asyncio.to_thread(codedocs.build_modules)
    try:
        saved = json.loads(memory.get(KEY) or "{}")
    except ValueError:
        saved = {}
    out, keep = [], {}
    for c in codedocs.components(modules):
        doc = codedocs._component_text(c)
        h = _hash(doc)
        old = saved.get(c["name"])
        if old and old.get("hash") == h and old.get("items"):
            items = old["items"]
        else:
            items = await _write(memory, free, c["name"], doc)
        if items:
            keep[c["name"]] = {"hash": h, "items": items}
        out += [{"component": c["name"], **i} for i in items]
    memory.set(KEY, json.dumps(keep))
    return out


def saved(memory) -> list[dict]:
    """The questions and answers written last time (none until the first export)."""
    try:
        data = json.loads(memory.get(KEY) or "{}") if memory is not None else {}
    except ValueError:
        return []
    return [{"component": name, **i} for name, v in data.items() for i in v.get("items", [])]


def search(memory, query: str, limit: int = 4) -> str:
    words = {w for w in re.findall(r"[a-z0-9]{3,}", (query or "").lower())}
    scored = []
    for qa in saved(memory):
        text = (qa["question"] + " " + qa["answer"] + " " + qa["component"]).lower()
        score = sum(1 for w in words if w in text)
        if score:
            scored.append((score, qa))
    scored.sort(key=lambda x: -x[0])
    return "\n\n".join(f"Q ({qa['component']}): {qa['question']}\nA: {qa['answer']}\nSteps: "
                       + " → ".join(s[0] for s in qa["steps"]) for _s, qa in scored[:limit])
