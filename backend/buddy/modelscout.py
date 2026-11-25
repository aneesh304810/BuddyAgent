"""The model scout: every day it reads what each free AI provider publishes, checks that the model Buddy uses
there still answers, and switches to a working one when it doesn't.

Free tiers change without notice: a model is retired, renamed, slowed down or cut off, and a provider that
worked yesterday times out today. Without this, Buddy kept calling the dead model, then rested the provider.

  Trusted sources   only each provider's own official model list, read with the key the owner gave Buddy for
                    that provider: Google (Gemini), Groq, Mistral, Cerebras and GitHub Models. No third-party
                    lists, no downloads, nothing run on the server. (OpenRouter's free models have their own
                    daily catalog, openrouter_catalog.py, built the same way from OpenRouter's own list.)
  Every day         for each provider with a key:
                      1. read its model list; note what is new and what is gone since yesterday
                      2. ask the model in use one tiny question ("reply with the word ready")
                      3. if it doesn't answer (an error, a timeout, an empty reply), try up to three other chat
                         models from that provider's list, best first, and switch to the first that answers
                    A provider that is only at its free limit is left alone: its model is fine, the quota is
                    not, and the other providers cover for it until it resets.
  Sooner            when a provider has just been rested for failing, the scout looks at it on the next check
                    instead of waiting for tomorrow.
  Choosing          "best first" is the provider's models ranked by: the ones Buddy prefers there, then the
                    family that suits a free helper (small and fast general chat models), stable before
                    preview, newer before older. Speech, image, embedding and safety models are never picked.
  By hand           a model the owner set (BUDDY_GEMINI_MODEL and the like) is checked but never changed;
                    "use <model> on <provider>" pins one from chat.

The test question contains nothing personal, and free models still only ever get general content. A switch is
told once; everything else is in the tool's status.
"""

import json
import logging
import re
import time
import urllib.request
from datetime import datetime, timezone

from .free_models import MODEL_PREFS, PROVIDERS, SKIP_MODEL, RateLimited
from .memory import Memory
from .tools import ToolError, local_now, require_https

log = logging.getLogger(__name__)

LAST_KEY, DAY_KEY, SOON_KEY = "modelscout:last", "modelscout:day", "modelscout:soon"
SCOUTED = ("gemini", "groq", "mistral", "cerebras", "github")  # OpenRouter and DeepSeek: openrouter_catalog.py
LISTS = {"github": "https://models.github.ai/catalog/models"}  # the others: <base>/models
CANDIDATES, PROBE_TIMEOUT = 3, 15
PROBE = ("You are being checked. Follow the instruction exactly.", "Reply with the single word: ready")
NOT_CHAT = re.compile(r"(?i)whisper|tts|speech|guard|embed|audio|vision-only|moderation|playai|orpheus|compound|"
                      r"imagen|image|veo|video|aqa|learnlm|robotics|computer-use|realtime|live|transcribe|rerank|"
                      r"ocr|native-audio|dall-e|sora|search-preview|deep-research")
# What suits a free helper at each provider, best first (matched anywhere in the model's name).
FAMILIES = {
    "gemini": [r"flash-latest$", r"flash$", r"flash(?!-lite)", r"flash-lite"],
    "groq": [r"gpt-oss-120b", r"llama-3\.3-70b", r"llama-4", r"qwen", r"gpt-oss-20b", r"llama.*instant"],
    "mistral": [r"mistral-small", r"mistral-medium", r"open-mistral", r"ministral"],
    "cerebras": [r"gpt-oss-120b", r"llama-3\.3-70b", r"qwen", r"llama"],
    "github": [r"openai/gpt-[\d.]+-mini", r"openai/gpt-4o-mini", r"openai/gpt-[\d.]+-nano", r"meta/.*llama",
               r"mistral-ai/.*small", r"microsoft/phi"],
}
UNSTABLE = re.compile(r"(?i)preview|exp\b|experimental|beta|-\d{4}$|-\d{2}-\d{2}$")


def _version(model: str) -> float:
    found = re.findall(r"(\d+(?:\.\d+)?)", model.split("/")[-1])
    return float(found[0]) if found else 0.0


def rank(provider: str, ids: list[str], skip: str = "") -> list[str]:
    """A provider's chat models, best first for a free helper."""
    usable = [i for i in dict.fromkeys(ids) if i and i != skip and not SKIP_MODEL.search(i) and not NOT_CHAT.search(i)]
    prefs, families = MODEL_PREFS.get(provider, []), FAMILIES.get(provider, [])

    def key(model: str) -> tuple:
        preferred = prefs.index(model) if model in prefs else len(prefs)
        family = next((n for n, pattern in enumerate(families) if re.search(pattern, model, re.I)), len(families))
        return (preferred, family, bool(UNSTABLE.search(model)), -_version(model), model)
    return sorted(usable, key=key)


def list_models(provider: str, key: str, fetch=None) -> list[str]:
    """The model names a provider's own API lists for this key."""
    url = LISTS.get(provider) or f"{PROVIDERS[provider]['base']}/models"
    headers = {"Authorization": f"Bearer {key}", "User-Agent": "Buddy/1.0", **PROVIDERS[provider].get("headers", {})}
    if fetch is not None:
        raw = fetch(url, headers)
    else:
        request = urllib.request.Request(require_https(url), headers=headers)
        with urllib.request.urlopen(request, timeout=20) as r:  # nosec B310: the provider's own https API
            raw = r.read()
    data = json.loads(raw)
    rows = data if isinstance(data, list) else (data.get("data") or data.get("models") or [])
    out = []
    for m in rows:
        name = str((m.get("id") or m.get("name") or "") if isinstance(m, dict) else m)
        out.append(name.removeprefix("models/"))
    return [m for m in out if m]


def probe(free, provider: str, model: str) -> tuple[str, str]:
    """Ask one model the test question. ("ok", "") | ("limit", why) | ("paused", why) | ("fail", why)."""
    from .budgets import BudgetStop  # noqa: PLC0415
    try:
        answer, _ = free._call_model(provider, model, PROBE[0], PROBE[1], PROBE_TIMEOUT, 20)
    except RateLimited:
        return "limit", "at its free limit"
    except BudgetStop as e:
        return "paused", str(e)[:120]
    except Exception as e:
        code = getattr(e, "code", None)
        return "fail", (f"HTTP {code}" if code else type(e).__name__) + (f": {str(e)[:80]}" if not code and str(e) else "")
    return ("ok", "") if answer.strip() else ("fail", "an empty reply")


def _pinned(free, provider: str) -> str:
    return (free.env.get(PROVIDERS[provider]["model_setting"]) or "").strip()


def scout(memory: Memory, free, provider: str, fetch=None) -> dict:
    """Check one provider and switch its model if the one in use doesn't answer."""
    key = (free.env.get(PROVIDERS[provider]["key"]) or "").strip()
    out = {"provider": provider, "model": free.model_of(provider), "state": "", "detail": "", "new": [], "gone": [],
           "switched_from": ""}
    try:
        ids = list_models(provider, key, fetch)
    except Exception as e:
        ids = []
        out["detail"] = f"its model list couldn't be read ({getattr(e, 'code', None) or type(e).__name__})"
    if ids:
        before = json.loads(memory.get(f"modelscout:catalog:{provider}") or "[]")
        if before:
            chat = set(rank(provider, ids))
            out["new"] = sorted(chat - set(before))[:12]
            out["gone"] = sorted(set(before) - set(ids))[:12]
        memory.set(f"modelscout:catalog:{provider}", json.dumps(sorted(set(ids))[:400]))
    state, why = probe(free, provider, out["model"])
    if state == "ok":
        out["state"] = "working"
        _clear(memory, provider)
        return out
    if state in ("limit", "paused"):
        out["state"], out["detail"] = ("at its free limit" if state == "limit" else "paused by your budget"), why
        return out
    if _pinned(free, provider):
        out["state"] = "not answering"
        out["detail"] = (f"{out['model']} was set by hand ({PROVIDERS[provider]['model_setting']}) and didn't answer "
                         f"({why}); I don't change a model you chose")
        return out
    tried = []
    for candidate in rank(provider, ids, skip=out["model"])[:CANDIDATES]:
        got, reason = probe(free, provider, candidate)
        if got == "ok":
            memory.set(f"free:model:{provider}", json.dumps({
                "model": candidate, "replaced": PROVIDERS[provider]["model"], "at": time.strftime("%Y-%m-%d"),
                "why": f"{out['model']} didn't answer ({why})"}))
            _clear(memory, provider)
            log.warning("model scout: %s: %s didn't answer (%s); switched to %s", provider, out["model"], why, candidate)
            out.update(state="switched", switched_from=out["model"], model=candidate,
                       detail=f"{out['model']} didn't answer ({why})")
            return out
        tried.append(f"{candidate}: {reason}")
        if got in ("limit", "paused"):  # the provider itself is out of quota or paused: more tries won't help
            break
    out["state"] = "not answering"
    out["detail"] = (f"{out['model']} didn't answer ({why})"
                     + ("; also tried " + "; ".join(tried) if tried else "; no other model to try"))
    return out


def _clear(memory: Memory, provider: str) -> None:
    """A provider with a working model is back in rotation at once."""
    for key in (f"free:rest:{provider}", f"free:fails:{provider}", f"free:limits:{provider}"):
        if memory.get(key) not in (None, "", "0"):
            memory.set(key, "" if "rest" in key else "0")
    try:
        from . import gateway  # noqa: PLC0415
        from .free_models import CONNECT_NAMES  # noqa: PLC0415
        gateway.report(memory, CONNECT_NAMES.get(provider, provider), True)
    except Exception:
        log.debug("model scout: status report failed", exc_info=True)


def run(memory: Memory, free=None, providers=None, fetch=None) -> dict:
    """Scout every provider that has a key (or the ones named). Returns {"providers": [...], "at"}."""
    from .free_models import FreeModels  # noqa: PLC0415
    free = free or FreeModels(memory)
    from .free_models import TEXT_OFF  # noqa: PLC0415  (Groq hears speech only; GitHub Models is not used)
    wanted = [p for p in (providers or SCOUTED) if p in SCOUTED and p not in TEXT_OFF
              and (free.env.get(PROVIDERS[p]["key"]) or "").strip()]
    rows = []
    for provider in wanted:
        try:
            rows.append(scout(memory, free, provider, fetch))
        except Exception as e:  # one provider's trouble doesn't stop the others
            log.warning("model scout: %s failed: %s", provider, type(e).__name__, exc_info=True)
            rows.append({"provider": provider, "model": "", "state": "not checked", "detail": type(e).__name__,
                         "new": [], "gone": [], "switched_from": ""})
    last = json.loads(memory.get(LAST_KEY) or "{}")
    kept = {r["provider"]: r for r in last.get("providers") or []}
    kept.update({r["provider"]: r for r in rows})
    out = {"at": datetime.now(timezone.utc).isoformat(), "providers": list(kept.values())}
    memory.set(LAST_KEY, json.dumps(out))
    return {"at": out["at"], "providers": rows}


def due(memory: Memory, now: datetime | None = None) -> list[str] | None:
    """None when nothing is due; [] for the daily look at every provider; else the providers to look at now
    (ones that were just rested for failing)."""
    now = now or local_now()
    if now.hour >= 5 and memory.get(DAY_KEY) != now.date().isoformat():
        return []
    soon = [p for p in json.loads(memory.get(SOON_KEY) or "[]") if p in SCOUTED]
    return soon or None


def ask_soon(memory: Memory, provider: str) -> None:
    """A provider just failed enough to be rested: have the scout look at it on the next check."""
    if memory is None or provider not in SCOUTED:
        return
    try:
        soon = set(json.loads(memory.get(SOON_KEY) or "[]"))
        if provider not in soon:
            memory.set(SOON_KEY, json.dumps(sorted(soon | {provider})))
    except Exception:
        log.debug("model scout: couldn't queue %s", provider, exc_info=True)


def mark_done(memory: Memory, daily: bool, now: datetime | None = None) -> None:
    if daily:
        memory.set(DAY_KEY, (now or local_now()).date().isoformat())
    memory.set(SOON_KEY, "[]")


def message(result: dict) -> str:
    """What to tell the owner: only switches and providers that stay broken. '' when there is nothing."""
    name = {"gemini": "Gemini", "groq": "Groq", "mistral": "Mistral", "cerebras": "Cerebras", "github": "GitHub Models"}
    lines = [f"- **{name[r['provider']]}**: {r['switched_from']} stopped answering, so I switched to {r['model']}."
             for r in result["providers"] if r["state"] == "switched"]
    lines += [f"- **{name[r['provider']]}**: {r['detail']}." for r in result["providers"] if r["state"] == "not answering"]
    if not lines:
        return ""
    switched = sum(1 for r in result["providers"] if r["state"] == "switched")
    spoken = ("Free AI models: " + (f"I switched {switched} to a working model" if switched else "one isn't answering")
              + ". Details are on screen.")
    return f"{spoken} || " + "\n".join(["**🧭 Free AI models checked**", "", *lines, "",
                                        "Claude covers anything the free models can't."])


def status(memory: Memory) -> dict:
    last = json.loads(memory.get(LAST_KEY) or "{}")
    return {"last_checked": last.get("at", ""), "providers": last.get("providers") or [],
            "trusted_sources": "each provider's own model list, read with your key for it: Google (Gemini), Groq, "
                               "Mistral, Cerebras, GitHub Models; OpenRouter's free models have their own daily catalog",
            "note": "A provider at its free limit is left alone: its model is fine, the quota resets by itself. "
                    "A model you set by hand is checked but never changed."}


def use(memory: Memory, free, provider: str, model: str) -> str:
    """The owner picks a model for a provider. It must answer the test question first."""
    provider, model = str(provider).lower().strip(), str(model).strip()
    if provider not in SCOUTED:
        raise ToolError(f"Which provider? One of: {', '.join(SCOUTED)}.")
    if not model or len(model) > 120:
        raise ToolError("Which model? Use a name from that provider's list (model_scout status shows it).")
    state, why = probe(free, provider, model)
    if state != "ok":
        return f"{model} on {provider} didn't pass the test ({why or state}), so I left the current model in place."
    memory.set(f"free:model:{provider}", json.dumps({"model": model, "replaced": PROVIDERS[provider]["model"],
                                                      "at": time.strftime("%Y-%m-%d"), "why": "chosen by the owner"}))
    _clear(memory, provider)
    return f"{provider} now uses {model}. It answered the test question."


TOOL = {
    "name": "model_scout",
    "description":
        "Keeps the free AI models working. Every day it reads each free provider's own model list (Gemini, "
        "Groq, Mistral, Cerebras, GitHub Models), checks the model in use still answers, and switches to a "
        "working one when it doesn't. status: each provider's model, whether it answered, what was switched "
        "and why, and what the provider added or removed. scan (provider optional): check now. use (provider, "
        "model): pin a model after it passes the test. Use it for 'why is Gemini failing', 'are the free "
        "models working', 'what new free models are there', 'fix the free models'. A provider at its free "
        "limit isn't broken: say so. This never sends anything personal: only a one-word test question.",
    "input_schema": {"type": "object", "properties": {
        "action": {"type": "string", "enum": ["status", "scan", "use"]},
        "provider": {"type": "string", "enum": list(SCOUTED)}, "model": {"type": "string"}},
        "required": ["action"], "additionalProperties": False},
}


async def run_tool(memory: Memory, args: dict, free=None) -> str:
    import asyncio  # noqa: PLC0415

    from .free_models import FreeModels  # noqa: PLC0415
    free = free or FreeModels(memory)
    action = args.get("action")
    if action == "scan":
        got = await asyncio.to_thread(run, memory, free, [args["provider"]] if args.get("provider") else None)
        if not got["providers"]:
            return "No free AI provider has a key set, so there is nothing to check."
        return json.dumps(got, ensure_ascii=False)
    if action == "use":
        return await asyncio.to_thread(use, memory, free, args.get("provider") or "", args.get("model") or "")
    got = status(memory)
    return json.dumps(got, ensure_ascii=False) if got["providers"] else (
        "The scout hasn't run yet. Say 'check the free models' to run it now.")
