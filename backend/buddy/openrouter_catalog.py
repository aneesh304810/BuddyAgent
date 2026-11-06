"""Which free OpenRouter models to use for each kind of task, chosen from OpenRouter's live list once a day.

Free models on OpenRouter come and go every few weeks, so model names aren't fixed in Buddy. Once a day:
  1. read the public model list (openrouter.ai/api/v1/models, no key needed)
  2. keep only free ones (an id ending in ":free" and a zero price) that take and give text
  3. score each for every task Buddy gives free models, from what the list says about it:
       code       coding models (coder, codestral, devstral, deepseek, north code, laguna...)
       long       long summaries of public pages: the biggest context
       reasoning  explanations: big models and reasoning ones (r1, think, nemotron ultra...)
       writing    rewording, ideas, decks, translation: big general chat models
       fast       quick answers, small talk, sorting: small and quick models
     plus how well each model has done for that task lately (answers Claude used or fixed, minus ones it redid or
     that failed the checks), so a model that keeps getting a task wrong drops down
  4. keep the best three per task (kv openrouter:picks); a model that errors (gone, no longer free) is set aside
     for three days and the next one is used.
A model name set by hand (BUDDY_OPENROUTER_MODEL, BUDDY_DEEPSEEK_MODEL) always wins. Turn on OpenRouter's privacy
setting that keeps prompts away from providers that train on them: requests to those then fail and the next
model is tried.
"""

import json
import logging
import math
import re
import time
from datetime import datetime, timedelta, timezone

log = logging.getLogger(__name__)

URL = "https://openrouter.ai/api/v1/models"
KIND_TASK = {"code": "code", "summarize": "long", "explain": "reasoning", "brainstorm": "writing", "rewrite": "writing",
             "translate": "writing", "quick_answer": "fast", "classify": "fast", "small_talk": "fast"}
CODE = re.compile(r"(?i)coder|codestral|devstral|code|deepseek|laguna|starcoder|swe")
REASON = re.compile(r"(?i)\br1\b|reason|think|qwq|ultra|o\d-|nemotron")
FAST = re.compile(r"(?i)mini|small|lite|flash|lightning|tiny|nano|\b[1-9]b\b|haiku")
BAD_DAYS = 3


def _params(text: str) -> float:
    """Billions of parameters from the name ('70b', '550b-a55b' -> 550), 0 when it doesn't say."""
    found = re.findall(r"(\d+(?:\.\d+)?)\s*b\b", text.lower())
    return max((float(x) for x in found), default=0.0)


def free_models(catalog: dict) -> list[dict]:
    out = []
    for m in catalog.get("data", []):
        mid = str(m.get("id") or "")
        price = m.get("pricing") or {}
        try:
            zero = float(price.get("prompt") or 0) == 0 and float(price.get("completion") or 0) == 0
        except (TypeError, ValueError):
            zero = False
        arch = m.get("architecture") or {}
        outs = arch.get("output_modalities") or ["text"]
        if not (mid.endswith(":free") and zero and "text" in outs):
            continue
        text = f"{mid} {m.get('name', '')}"
        out.append({"id": mid, "name": m.get("name", mid), "context": int(m.get("context_length") or 0),
                    "params": _params(text), "text": text, "description": str(m.get("description") or "")[:300]})
    return out


def score(m: dict, task: str, accuracy: dict | None = None) -> float:
    size = math.log1p(m["params"] or 30)          # unknown size: treat as mid-sized
    ctx = math.log1p(m["context"] / 1000)
    s = {"code": 1.0 * size + (6 if CODE.search(m["text"]) else 0),
         "long": 3.0 * ctx + 0.5 * size,
         "reasoning": 1.5 * size + (3 if REASON.search(m["text"]) else 0) - (3 if CODE.search(m["text"]) else 0),
         "writing": 1.5 * size + 0.3 * ctx - (4 if CODE.search(m["text"]) else 0),
         "fast": (5 if FAST.search(m["text"]) else 0) - 0.6 * size + (1 if m["params"] and m["params"] <= 32 else 0),
         }[task]
    good = (accuracy or {}).get(m["id"])
    if good is not None:
        s += 8 * (good - 0.7)  # usable 70% of the time is neutral; better rises, worse sinks
    return round(s, 3)


def accuracy(memory, days: int = 14) -> dict[str, float]:
    """Share of each OpenRouter model's answers Claude found usable lately (with at least 5 judged)."""
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    try:
        rows = memory.db.execute(
            "SELECT model, SUM(verdict IN ('used', 'fixed')) AS good, "
            "SUM(verdict IN ('used', 'fixed', 'redone', 'failed_check')) AS judged FROM model_calls "
            "WHERE provider IN ('openrouter', 'deepseek') AND at >= ? GROUP BY model", (since,)).fetchall()
    except Exception:
        return {}
    return {r["model"]: r["good"] / r["judged"] for r in rows if (r["judged"] or 0) >= 5}


def refresh(memory, fetch=None) -> dict:
    """Read the live list and save the best free models per task. Returns the picks."""
    if fetch is None:
        from .markets import _fetch as fetch  # noqa: PLC0415  (https only, Buddy's user agent)
    raw = fetch(URL, {})
    catalog = raw if isinstance(raw, dict) else json.loads(raw or b"{}")
    models = free_models(catalog)
    if not models:
        return picks(memory)  # keep yesterday's rather than nothing
    acc = accuracy(memory)
    bad = set_aside(memory)
    chosen = {}
    for task in ("code", "long", "reasoning", "writing", "fast"):
        ranked = sorted((m for m in models if m["id"] not in bad), key=lambda m: -score(m, task, acc))
        chosen[task] = [m["id"] for m in ranked[:3]]
    memory.set("openrouter:picks", json.dumps({"at": datetime.now(timezone.utc).isoformat(), "picks": chosen,
                                               "free_count": len(models)}))
    return chosen


def picks(memory) -> dict:
    try:
        return json.loads(memory.get("openrouter:picks") or "{}").get("picks") or {}
    except ValueError:
        return {}


def due(memory) -> bool:
    try:
        at = json.loads(memory.get("openrouter:picks") or "{}").get("at")
    except ValueError:
        at = None
    return not at or datetime.now(timezone.utc) - datetime.fromisoformat(at) > timedelta(hours=24)


def set_aside(memory) -> set[str]:
    try:
        bad = json.loads(memory.get("openrouter:bad") or "{}")
    except ValueError:
        bad = {}
    return {m for m, until in bad.items() if until > time.time()}


def mark_bad(memory, model_id: str) -> None:
    """A model that errors (gone, no longer free, refused by the privacy setting): skipped for three days."""
    try:
        bad = json.loads(memory.get("openrouter:bad") or "{}")
    except ValueError:
        bad = {}
    bad = {m: u for m, u in bad.items() if u > time.time()}
    bad[model_id] = time.time() + BAD_DAYS * 86400
    memory.set("openrouter:bad", json.dumps(bad))


def models_for(memory, kind: str) -> list[str]:
    """The free models to try for this kind of task, best first (set-aside ones left out)."""
    task = KIND_TASK.get(kind, "writing")
    bad = set_aside(memory)
    return [m for m in picks(memory).get(task, []) if m not in bad]
