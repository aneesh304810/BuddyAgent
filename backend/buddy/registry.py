"""Buddy's tool and skill registry, kept in the git repo (deploy/sandbox/registry and skills).

The task agent searches it with find_tools: vetted tools (pinned versions, checked downloads)
and skills (tested scripts with a short guide). The same files are mounted read-only into the
sandbox at /buddy, where install_tool and the pip / npm guard use them.
"""

import json
import re
from functools import lru_cache
from pathlib import Path

import yaml

LIBRARY = Path(__file__).resolve().parents[2] / "deploy" / "sandbox"


@lru_cache(maxsize=1)
def _load(root: str = str(LIBRARY)) -> tuple[list[dict], list[dict]]:
    base = Path(root)
    try:
        tools = (yaml.safe_load((base / "registry" / "tools.yaml").read_text()) or {}).get("tools", [])
    except (OSError, yaml.YAMLError):
        tools = []
    skills = []
    for doc in sorted((base / "skills").glob("*/SKILL.md")):
        text = doc.read_text()
        meta = {}
        match = re.match(r"(?s)^---\n(.*?)\n---\n(.*)$", text)
        if match:
            try:
                meta = yaml.safe_load(match.group(1)) or {}
            except yaml.YAMLError:
                continue  # a broken guide: leave it out rather than hide every skill
            body = match.group(2)
        else:
            body = text
        skills.append({"name": meta.get("name", doc.parent.name), "description": meta.get("description", ""),
                       "tags": meta.get("tags", []), "guide": body.strip(), "path": f"/buddy/skills/{doc.parent.name}/",
                       "data": bool(meta.get("data")), "run": meta.get("run", "")})
    return tools, skills


def search(query: str, limit: int = 8, root: str | None = None) -> dict:
    """Tools and skills matching the words of a task, best first."""
    tools, skills = _load(root or str(LIBRARY))
    words = {w for w in re.findall(r"[a-z0-9]+", query.lower()) if len(w) > 2}

    def score(item: dict, text: str) -> int:
        tags = " ".join(item.get("tags", [])).lower()
        return sum(3 * (w in tags) + (w in text.lower()) + 2 * (w in item["name"].lower()) for w in words)

    ranked_skills = sorted(((score(s, s["description"] + " " + s["guide"]), s) for s in skills), key=lambda x: -x[0])
    ranked_tools = sorted(((score(t, t.get("purpose", "") + " " + t.get("usage", "")), t) for t in tools),
                          key=lambda x: -x[0])
    return {
        "skills": [{"name": s["name"], "what": s["description"], "how": s["guide"][:900], "folder": s["path"]}
                   for n, s in ranked_skills if n > 0][:3],
        "tools": [{"name": t["name"], "what": t.get("purpose", ""), "kind": t.get("kind"),
                   "ready": t.get("kind") == "builtin", "usage": t.get("usage", ""),
                   **({"careful": True} if t.get("risk") == "medium" else {})}
                  for n, t in ranked_tools if n > 0][:limit],
        "note": "Built-in tools are ready. Others: install_tool NAME. Not listed: pip or npm install it (the guard "
                "allows established packages, refuses new or lookalike ones). Skills: read the guide, run the script.",
    }


def catalogue(root: str | None = None) -> str:
    """One line per tool and skill, for the task agent's first message."""
    tools, skills = _load(root or str(LIBRARY))
    return json.dumps({"skills": [s["name"] for s in skills],
                       "tools_ready": [t["name"] for t in tools if t.get("kind") == "builtin"],
                       "tools_to_install": [t["name"] for t in tools if t.get("kind") != "builtin"]})
