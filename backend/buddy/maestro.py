"""Buddy Maestro: the one gateway every AI model call in Buddy is meant to go through.

Buddy uses several AI providers. This is the place that knows them all, decides which may see what, picks one
for each job, counts what it cost, and lets a new provider be added later without changing any code.

  Providers (PROVIDERS)
      Claude      the only provider that may see anything about the owner. Three tiers: cheap, mid, strongest.
      OpenAI      optional, paid; public records only. Off until the owner sets its key, model id and price.
      Mistral, Cerebras, Google, NVIDIA   the free pool for public work, tried in the order each is best at.
      OpenRouter  the fallback only: used when none of the pool answers.
      Ollama      a place kept for a local model on a machine the owner controls. Not set up.
      Groq        speech to text only; not used for text.
      Added       any provider that speaks the common chat format can be added from the page: a name, its
                  address, a model id and its price. Its key is entered with the hidden prompt, never in chat.
  The privacy rule (may_see, in one place)
      Anything about the owner goes to Claude and nowhere else. No fallback, no added provider and no setting
      changes that. If Claude cannot answer, a personal task waits; it is never sent elsewhere. Text that is
      meant to be public is checked for personal details before it leaves (free_models.check_general), and if
      it has any it goes to Claude instead.
  Jobs and choices
      Each job (JOBS) has its own model choice, so the cheapest model that does a job well can do it:
      the free pool, one named pool provider, a Claude tier, OpenAI, or an added provider.
  Limit     a monthly spending limit in dollars for the paid choices (CAP_KEY, $5 unless changed). At the limit
            a job goes back to the free pool until the month ends. Spending is counted per job.
  Test      a fixed set of headlines with the right label beside each (TEST). Any choice can be run on it to see
            how often it agrees, how long it took and what it cost, before a job is switched to it. The set was
            written for this purpose, not taken from the news, so treat a result as a guide.

No model id or price for OpenAI or an added provider is built in, because they change and Buddy cannot check
them: the owner enters them from the provider's own page.
"""

import json
import logging
import time
from datetime import datetime, timezone

from . import gateway
from .memory import Memory
from .tools import ToolError

log = logging.getLogger(__name__)

JOBS = {  # name -> (title, what it does, in use today?)
    "label": ("Label headlines", "good, bad or neutral for a technology or company; up to 120 a night", True),
    "write": ("Write up a convergence", "what two technologies add together, from record titles; about 5 a night", True),
    "extract": ("Find the companies in a record", "the companies a headline names and the relationships it states; up to 100 a night", True),
    "analyse": ("Analyse a company or theme", "compares the evidence for and against; on request", False),
    "deep": ("Hard research questions", "long questions that need the strongest model; on request", False),
    "summarise_search": ("Summarise search results", "a short answer, with numbered sources, from public search "
                         "results (Buddy Search); on request. Your own files and messages: Claude only", True),
    "summarise_reviews": ("Sum up reviews", "pros, cons and catches, each with its sources, from public review "
                          "snippets (Reddit, review sites, complaint threads) when you tap Reviews; never Google's "
                          "reviews or your own verdicts", True),
}
CLAUDE = {"claude:fast": ("fast_model", "Claude, cheap model"), "claude:chat": ("chat_model", "Claude, mid model"),
          "claude:deep": ("model", "Claude, strongest model")}
OPENAI = {"openai:cheap": ("BUDDY_OPENAI_CHEAP", "OpenAI, volume model"),
          "openai:analysis": ("BUDDY_OPENAI_ANALYSIS", "OpenAI, analysis model")}
POOL = ("mistral", "cerebras", "gemini", "nvidia")  # the free pool's primaries (free_models.py), each a choice too
ADDED_KEY = "maestro:providers"
PROVIDERS = (  # id, title, role, may it see anything about the owner?, how it is paid for
    ("claude", "Claude (Anthropic)", "Everything about you, and the main assistant", True, "paid per use"),
    ("openai", "OpenAI", "Optional: public records only, for jobs you assign to it", False, "paid per use"),
    ("mistral", "Mistral", "Free pool: public work; writing and European languages", False, "free tier"),
    ("cerebras", "Cerebras", "Free pool: public work; the fastest answers", False, "free tier"),
    ("gemini", "Google (Gemini)", "Free pool: public work; long texts and many languages", False, "free tier"),
    ("nvidia", "NVIDIA", "Free pool: public work; open models it hosts", False, "free tier"),
    ("openrouter", "OpenRouter", "Fallback only: used when none of the pool answers", False, "free models"),
    ("ollama", "Ollama (local)", "A place kept for a model on a machine you control. Not set up", False, "your own machine"),
    ("groq", "Groq", "Speech to text only; not used for text", False, "paid per use"),
)
RULE = ("Anything about you goes to Claude and nowhere else. No fallback, no added provider and no setting changes "
        "that. If Claude cannot answer, a personal task waits.")
CAP_KEY, CAP = "intel:cap", 5.0
TEST_KEY = "intel:test"


def _env(env=None):
    import os  # noqa: PLC0415
    return os.environ if env is None else env


def openai_setting(choice: str, env=None) -> dict | None:
    """The model id and price the owner set for an OpenAI choice, or None when any of the three is missing."""
    prefix = OPENAI[choice][0]
    env = _env(env)
    model, price = (env.get(f"{prefix}_MODEL") or "").strip(), (env.get(f"{prefix}_PRICE") or "").strip()
    try:
        price_in, price_out = (float(x) for x in price.split(","))
    except ValueError:
        return None
    if not model or not (env.get("BUDDY_OPENAI_KEY") or "").strip() or price_in < 0 or price_out < 0:
        return None
    return {"model": model, "in": price_in, "out": price_out}


def check_public(text: str, memory: Memory | None = None) -> None:
    """The one check every part of Buddy calls before text goes to a model other than Claude. Raises
    free_models.PersonalData when the text holds anything about the owner (an email address, a phone number, a
    secret, something from memory), so the caller keeps it on Claude."""
    from .free_models import check_general  # noqa: PLC0415  (the detector itself)
    check_general(text, memory)


def may_see(provider: str, personal: bool) -> bool:
    """The privacy rule, in one place: only Claude may see anything about the owner."""
    return not personal or provider == "claude" or provider.startswith("claude:")


def added(memory: Memory) -> list[dict]:
    """The providers the owner added from the page."""
    try:
        got = json.loads(memory.get(ADDED_KEY) or "[]")
    except ValueError:
        got = []
    return [g for g in got if isinstance(g, dict) and g.get("name")]


def key_name(name: str) -> str:
    return f"BUDDY_AI_{name.upper()}_KEY"


def add_provider(memory: Memory, name: str, base: str, model: str, price_in=0, price_out=0, title: str = "") -> str:
    """Add a provider that speaks the common chat format (POST {base}/chat/completions). Public work only."""
    import re  # noqa: PLC0415
    import urllib.parse  # noqa: PLC0415
    name = str(name or "").strip().lower()
    if not re.fullmatch(r"[a-z][a-z0-9]{1,19}", name):
        raise ToolError("Give it a short name: letters and numbers only, starting with a letter.")
    if name in {p[0] for p in PROVIDERS} or name in ("free", "pool", "added", "deepseek", "github"):
        raise ToolError(f"{name} is already one of Buddy's providers.")
    url = urllib.parse.urlsplit(str(base or "").strip().rstrip("/"))
    host = url.hostname or ""
    if url.scheme != "https" or "." not in host or re.fullmatch(r"[\d.]+", host) or host.endswith((".local", ".internal")):
        raise ToolError("Its address must start with https:// and be a public host name.")
    model = " ".join(str(model or "").split())
    if not model or len(model) > 120:
        raise ToolError("Give the model's id, as the provider's own page writes it.")
    try:
        cost = (float(price_in), float(price_out))
    except (TypeError, ValueError):
        raise ToolError("Prices are numbers: dollars per million tokens in, and out (0 and 0 if it is free).") from None
    if min(cost) < 0:
        raise ToolError("A price can't be negative.")
    kept = [g for g in added(memory) if g["name"] != name]
    if len(kept) >= 10:
        raise ToolError("Ten added providers is the limit. Remove one first.")
    kept.append({"name": name, "title": (" ".join(str(title or "").split()) or name.title())[:40],
                 "base": urllib.parse.urlunsplit(url), "model": model, "in": cost[0], "out": cost[1]})
    memory.set(ADDED_KEY, json.dumps(kept))
    return (f"Added {kept[-1]['title']}. Enter its key with: sudo bash /opt/buddy/deploy/set.sh {key_name(name)} . "
            "It is for public work only, and is used only for the jobs you assign to it.")


def remove_provider(memory: Memory, name: str) -> str:
    kept = [g for g in added(memory) if g["name"] != str(name).strip().lower()]
    if len(kept) == len(added(memory)):
        raise ToolError("No added provider has that name.")
    memory.set(ADDED_KEY, json.dumps(kept))
    for job in JOBS:  # a job that used it goes back to the free pool
        if choice_for(memory, job) == f"added:{str(name).strip().lower()}":
            memory.set(f"intel:model:{job}", "free")
    return "Removed. Any job that used it is back on the free pool."


def choices(env=None, memory: Memory | None = None) -> list[dict]:
    from .config import settings  # noqa: PLC0415
    from .free_models import PROVIDERS as pool_providers  # noqa: PLC0415
    out = [{"id": "free", "title": "The free pool, then Claude's cheap model", "ready": True, "paid": False}]
    for name in POOL:
        key = pool_providers[name]["key"]
        ready = bool((_env(env).get(key) or "").strip())
        out.append({"id": f"pool:{name}", "title": f"{next(t for i, t, *_ in PROVIDERS if i == name)} only (free)",
                    "ready": ready, "paid": False, "setup": "" if ready else f"Set {key} with sudo bash "
                    "/opt/buddy/deploy/set.sh NAME"})
    for g in added(memory) if memory is not None else []:
        ready = bool((_env(env).get(key_name(g["name"])) or "").strip())
        out.append({"id": f"added:{g['name']}", "title": f"{g['title']} ({g['model']})", "ready": ready,
                    "paid": bool(g["in"] or g["out"]), "added": True,
                    "setup": "" if ready else f"Set {key_name(g['name'])} with sudo bash /opt/buddy/deploy/set.sh NAME"})
    out += [{"id": c, "title": f"{title} ({getattr(settings, attr)})", "ready": True, "paid": True}
            for c, (attr, title) in CLAUDE.items()]
    for c, (prefix, title) in OPENAI.items():
        got = openai_setting(c, env)
        out.append({"id": c, "title": f"{title} ({got['model']})" if got else title, "ready": bool(got), "paid": True,
                    "setup": "" if got else f"Set BUDDY_OPENAI_KEY, {prefix}_MODEL and {prefix}_PRICE with "
                                            "sudo bash /opt/buddy/deploy/set.sh NAME"})
    return out


def choice_for(memory: Memory, job: str) -> str:
    return memory.get(f"intel:model:{job}") or "free"


def set_choice(memory: Memory, job: str, choice: str, env=None) -> str:
    if job not in JOBS:
        raise ToolError("Jobs are: " + ", ".join(JOBS))
    found = next((c for c in choices(env, memory) if c["id"] == choice), None)
    if not found:
        raise ToolError("Choices are: " + ", ".join(c["id"] for c in choices(env, memory)))
    if not found["ready"]:
        raise ToolError(found["setup"])
    memory.set(f"intel:model:{job}", choice)
    return f"{JOBS[job][0]} now uses: {found['title']}."


# --- spending ------------------------------------------------------------------------------------------------
def _month(now=None) -> str:
    return f"{(now or datetime.now(timezone.utc)):%Y-%m}"


def spending(memory: Memory, now=None) -> dict:
    try:
        jobs = json.loads(memory.get(f"intel:spend:{_month(now)}") or "{}")
    except ValueError:
        jobs = {}
    return {"month": _month(now), "jobs": jobs, "total": round(sum(j["usd"] for j in jobs.values()), 4), "cap": cap(memory)}


def cap(memory: Memory) -> float:
    try:
        return max(0.0, float(memory.get(CAP_KEY) or CAP))
    except ValueError:
        return CAP


def set_cap(memory: Memory, dollars) -> str:
    try:
        value = float(dollars)
    except (TypeError, ValueError):
        raise ToolError("Give the monthly limit as a number of dollars.") from None
    if not 0 <= value <= 500:
        raise ToolError("The monthly limit must be between $0 and $500.")
    memory.set(CAP_KEY, str(value))
    return f"The Intelligence Platform's paid models stop at ${value:,.2f} a month, then use the free choice."


def _spent(memory: Memory, job: str, usd: float, tokens_in: int, tokens_out: int, now=None) -> None:
    got = spending(memory, now)["jobs"]
    row = got.setdefault(job, {"calls": 0, "in": 0, "out": 0, "usd": 0.0})
    row["calls"] += 1
    row["in"] += int(tokens_in)
    row["out"] += int(tokens_out)
    row["usd"] = round(row["usd"] + usd, 6)
    memory.set(f"intel:spend:{_month(now)}", json.dumps(got))


# --- asking --------------------------------------------------------------------------------------------------
async def _claude(memory: Memory, choice: str, prompt: str, system: str) -> dict:
    from . import usage  # noqa: PLC0415
    from .agent import fallback_kwargs  # noqa: PLC0415
    from .config import claude_client, settings  # noqa: PLC0415
    model = getattr(settings, CLAUDE[choice][0])
    started = time.monotonic()
    response = await claude_client().beta.messages.create(
        model=model, max_tokens=3_000, system=system, messages=[{"role": "user", "content": prompt}],
        **fallback_kwargs(model))
    usage.record(memory, response.model, response.usage, purpose="intelligence", response=response, started=started)
    return {"text": "\n".join(b.text for b in response.content if getattr(b, "type", "") == "text"),
            "usd": usage.cost_usd(response.model, response.usage), "in": response.usage.input_tokens,
            "out": response.usage.output_tokens}


def _openai(memory: Memory, choice: str, prompt: str, system: str, fetch=None, env=None) -> dict:
    setting = openai_setting(choice, env)
    if not setting:
        raise ToolError(f"{OPENAI[choice][1]} isn't set up.")
    got = gateway.call(memory, "openai", "/v1/chat/completions", json_body={
        "model": setting["model"], "messages": [{"role": "system", "content": system},
                                                {"role": "user", "content": prompt}]},
        fetch=fetch, env=env, purpose="intelligence")
    used = (got or {}).get("usage") or {}
    tokens_in, tokens_out = int(used.get("prompt_tokens") or 0), int(used.get("completion_tokens") or 0)
    return {"text": str((((got or {}).get("choices") or [{}])[0].get("message") or {}).get("content") or ""),
            "usd": (tokens_in * setting["in"] + tokens_out * setting["out"]) / 1_000_000, "in": tokens_in,
            "out": tokens_out}


def _added(memory: Memory, choice: str, prompt: str, system: str, fetch=None, env=None) -> dict:
    """An added provider: the common chat format, through Buddy Connect so it is logged and can be switched off."""
    g = next((x for x in added(memory) if f"added:{x['name']}" == choice), None)
    key = (_env(env).get(key_name(g["name"])) or "").strip() if g else ""
    if not g or not key:
        raise ToolError("That provider isn't set up.")
    body = json.dumps({"model": g["model"], "messages": [{"role": "system", "content": system},
                                                         {"role": "user", "content": prompt}]}).encode()
    got = gateway.call_url(memory, "added_ai", [g["base"] + "/chat/completions"], data=body, fetch=fetch,
                           headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                           purpose="maestro")
    used = (got or {}).get("usage") or {}
    tokens_in, tokens_out = int(used.get("prompt_tokens") or 0), int(used.get("completion_tokens") or 0)
    return {"text": str((((got or {}).get("choices") or [{}])[0].get("message") or {}).get("content") or ""),
            "usd": (tokens_in * g["in"] + tokens_out * g["out"]) / 1_000_000, "in": tokens_in, "out": tokens_out}


def _pool_one(memory: Memory, name: str, prompt: str, system: str) -> dict:
    """One named provider of the free pool, and no other."""
    from .free_models import FreeModels  # noqa: PLC0415
    free = FreeModels(memory)
    if name not in free.enabled():
        raise ToolError(f"{name} isn't set up or is switched off.")
    text, _ = free._call(name, system, prompt)
    return {"text": text, "usd": 0.0, "in": 0, "out": 0}


async def _free(memory: Memory, prompt: str, system: str) -> dict:
    """Free models first; the cheap Claude model if none answers (its small cost is counted too)."""
    from .free_models import FreeModels, NeedsClaude  # noqa: PLC0415
    free = FreeModels(memory)
    if free.enabled():
        try:
            got = await free.run("explain", prompt, system=system, purpose="intelligence", claude_fallback=False,
                                 check_text=False)
            if (got.get("answer") or "").strip():
                return {"text": got["answer"], "usd": 0.0, "in": 0, "out": 0}
        except NeedsClaude:
            log.info("intelligence models: no free model answered")
        except Exception:
            log.info("intelligence models: the free model failed", exc_info=True)
    return await _claude(memory, "claude:fast", prompt, system)


async def ask_with(memory: Memory, choice: str, prompt: str, system: str, fetch=None, env=None) -> dict:
    """One answer from one choice: {text, usd, in, out, seconds}. Raises when the choice fails."""
    import asyncio  # noqa: PLC0415
    from . import privacygate  # noqa: PLC0415
    if choice in CLAUDE:  # health never goes, even to Claude; the client cuts it too, this refuses early
        if privacygate.health(prompt + "\n" + system):
            privacygate.authorize("claude", prompt + "\n" + system, "Maestro", memory)
    else:  # every other model: public text only, and a refusal is final (no other model is tried)
        privacygate.authorize(choice, prompt + "\n" + system, "Maestro", memory)
    started = time.monotonic()
    if choice in OPENAI:
        got = await asyncio.to_thread(_openai, memory, choice, prompt, system, fetch, env)
    elif choice.startswith("added:"):
        got = await asyncio.to_thread(_added, memory, choice, prompt, system, fetch, env)
    elif choice.startswith("pool:"):
        got = await asyncio.to_thread(_pool_one, memory, choice.split(":", 1)[1], prompt, system)
    elif choice in CLAUDE:
        got = await _claude(memory, choice, prompt, system)
    else:
        got = await _free(memory, prompt, system)
    return {**got, "seconds": round(time.monotonic() - started, 2)}


SYSTEM = "You work from public records only. Answer with the one line of JSON asked for, nothing else."


async def ask(memory: Memory, job: str, prompt: str, system: str = SYSTEM, fetch=None, env=None,
              personal: bool = False) -> dict | None:
    """The JSON answer for a job, from the model chosen for it. A paid choice that has reached the month's limit,
    or that fails, falls back to the free choice. personal=True: Claude only, whatever the job's choice."""
    from .benefits import last_json  # noqa: PLC0415
    choice = choice_for(memory, job)
    if personal or not _public(prompt, memory):  # the privacy rule: nothing about the owner leaves Claude
        try:
            got = await ask_with(memory, choice if may_see(choice, True) else "claude:chat", prompt, system)
        except Exception as e:
            log.info("maestro: Claude could not answer a personal %s (%s); it is not sent elsewhere", job, type(e).__name__)
            return None
        _spent(memory, job, got["usd"], got["in"], got["out"])
        return last_json(got["text"])
    if choice != "free" and spending(memory)["total"] >= cap(memory):
        log.info("intelligence models: the month's limit is reached; %s uses the free choice", job)
        choice = "free"
    from . import privacygate  # noqa: PLC0415
    for attempt in dict.fromkeys((choice, "free")):
        try:
            got = await ask_with(memory, attempt, prompt, system, fetch, env)
        except privacygate.PrivacyBlocked as e:  # a privacy refusal is final: no other model is tried
            log.info("intelligence models: %s was refused by the Privacy Gateway for %s (%s)", attempt, job, e.kind)
            return None
        except Exception as e:
            log.info("intelligence models: %s failed for %s (%s)", attempt, job, type(e).__name__)
            continue
        if got["usd"]:
            _spent(memory, job, got["usd"], got["in"], got["out"])
        parsed = last_json(got["text"])
        if parsed:
            return parsed
    return None


def _public(text: str, memory: Memory) -> bool:
    """Does this text hold nothing about the owner? (Emails, phone numbers, secrets, things from memory.)"""
    from .free_models import PersonalData  # noqa: PLC0415
    try:
        check_public(text, memory)
        return True
    except PersonalData:
        return False


# --- the test ------------------------------------------------------------------------------------------------
TEST = (  # (subject, headline, the right label). Written for this test; not taken from the news.
    ("Small modular reactors", "Utility signs binding order for four small modular reactors", "good"),
    ("Small modular reactors", "Regulator approves first small modular reactor design", "good"),
    ("Small modular reactors", "Small modular reactor project cancelled after costs double", "bad"),
    ("Small modular reactors", "Safety review of small modular reactor delayed by two years", "bad"),
    ("Small modular reactors", "Conference on small modular reactors opens in Vienna", "neutral"),
    ("Small modular reactors", "What is a small modular reactor? An explainer", "neutral"),
    ("Fusion energy", "Fusion startup sustains plasma for record 20 minutes", "good"),
    ("Fusion energy", "Fusion company lays off half its staff as funding dries up", "bad"),
    ("Fusion energy", "Lawmakers hold hearing on fusion energy regulation", "neutral"),
    ("Fusion energy", "Government awards $400 million to fusion pilot plant", "good"),
    ("Solid-state batteries", "Automaker drops solid-state battery plan, citing durability failures", "bad"),
    ("Solid-state batteries", "Solid-state battery maker begins shipping cells to three carmakers", "good"),
    ("Solid-state batteries", "Solid-state battery cells recalled after fire in testing", "bad"),
    ("Solid-state batteries", "Researchers publish review of solid-state battery chemistries", "neutral"),
    ("AI data centers", "Chipmaker's data center revenue triples on AI demand", "good"),
    ("AI data centers", "County blocks new AI data center over water use", "bad"),
    ("AI data centers", "Cloud provider cancels leases on planned AI data centers", "bad"),
    ("AI data centers", "AI data center operators meet for annual trade show", "neutral"),
    ("AI data centers", "Utility approves grid connection for 1-gigawatt AI campus", "good"),
    ("Humanoid robots", "Carmaker to deploy 1,000 humanoid robots on assembly lines", "good"),
    ("Humanoid robots", "Humanoid robot maker misses delivery target for third quarter", "bad"),
    ("Humanoid robots", "Humanoid robot injures worker during factory trial", "bad"),
    ("Humanoid robots", "A short history of humanoid robots", "neutral"),
    ("Gene editing", "FDA approves first gene editing therapy for sickle cell disease", "good"),
    ("Gene editing", "Gene editing trial halted after patient death", "bad"),
    ("Gene editing", "Gene editing therapy shows lasting benefit at two years", "good"),
    ("Gene editing", "Ethics panel publishes guidance on gene editing", "neutral"),
    ("Quantum computing", "Quantum computer runs error-corrected algorithm for first time", "good"),
    ("Quantum computing", "Quantum computing firm restates revenue, shares halted", "bad"),
    ("Quantum computing", "University launches quantum computing degree", "neutral"),
    ("Quantum computing", "Bank signs five-year contract for quantum computing access", "good"),
    ("Post-quantum cryptography", "Flaw found in widely used post-quantum encryption library", "bad"),
    ("Post-quantum cryptography", "Browser makers switch on post-quantum encryption by default", "good"),
    ("Post-quantum cryptography", "Standards body schedules meeting on post-quantum signatures", "neutral"),
    ("Oklo Inc.", "Oklo wins Air Force contract to power Alaska base", "good"),
    ("Oklo Inc.", "Oklo licence application rejected by regulator", "bad"),
    ("Oklo Inc.", "Oklo to present at investor conference next week", "neutral"),
    ("NuScale Power", "NuScale customer withdraws from flagship project", "bad"),
    ("NuScale Power", "NuScale receives order for six reactor modules", "good"),
    ("NuScale Power", "NuScale names new board member", "neutral"),
    ("Rocket Lab", "Rocket Lab launch fails, satellite lost", "bad"),
    ("Rocket Lab", "Rocket Lab wins $500 million defence launch contract", "good"),
    ("Rocket Lab", "Rocket Lab schedules next launch window for March", "neutral"),
    ("Satellite internet", "Satellite internet service passes ten million subscribers", "good"),
    ("Satellite internet", "Regulator fines satellite internet operator over interference", "bad"),
    ("Satellite internet", "How satellite internet works", "neutral"),
    ("Carbon capture", "Carbon capture plant shut after capturing a tenth of its target", "bad"),
    ("Carbon capture", "Steelmaker commits to carbon capture at three plants", "good"),
)


async def test(memory: Memory, choice: str, ask_one=None, fetch=None, env=None, batch: int = 16) -> dict:
    """Run one choice on the test headlines: how many it got right, how long it took, what it cost."""
    from .benefits import last_json  # noqa: PLC0415
    from .themes import LABEL, TONES  # noqa: PLC0415
    found = next((c for c in choices(env, memory) if c["id"] == choice), None)
    if not found or not found["ready"]:
        raise ToolError(found["setup"] if found else "Choices are: " + ", ".join(c["id"] for c in choices(env, memory)))
    right = answered = 0
    usd = seconds = 0.0
    wrong = []
    for i in range(0, len(TEST), batch):
        part = TEST[i:i + batch]
        items = "\n".join(f"{n}. [{subject}] {title}" for n, (subject, title, _) in enumerate(part, 1))
        try:
            got = await (ask_one or ask_with)(memory, choice, LABEL.format(items=items), SYSTEM, fetch, env)
        except Exception as e:
            log.info("intelligence models: test batch failed on %s (%s)", choice, type(e).__name__)
            continue
        usd, seconds = usd + got["usd"], seconds + got.get("seconds", 0)
        labels = (last_json(got["text"]) or {}).get("labels")
        for n, (subject, title, expected) in enumerate(part, 1):
            said = str((labels or {}).get(str(n)) or "").strip().lower() if isinstance(labels, dict) else ""
            if said in TONES:
                answered += 1
                right += said == expected
                if said != expected and len(wrong) < 8:
                    wrong.append({"headline": title, "said": said, "right": expected})
    if usd:
        _spent(memory, "test", usd, 0, 0)
    out = {"choice": choice, "title": found["title"], "headlines": len(TEST), "answered": answered, "right": right,
           "share": round(right / len(TEST), 2), "usd": round(usd, 4), "seconds": round(seconds, 1), "wrong": wrong,
           "at": datetime.now(timezone.utc).isoformat()}
    try:
        kept = json.loads(memory.get(TEST_KEY) or "{}")
    except ValueError:
        kept = {}
    kept[choice] = out
    memory.set(TEST_KEY, json.dumps(kept))
    return out


def page(memory: Memory, env=None) -> dict:
    try:
        tests = json.loads(memory.get(TEST_KEY) or "{}")
    except ValueError:
        tests = {}
    return {"jobs": [{"id": j, "title": title, "does": does, "in_use": used, "choice": choice_for(memory, j)}
                     for j, (title, does, used) in JOBS.items()],
            "choices": choices(env, memory), "spending": spending(memory), "tests": list(tests.values()),
            "providers": providers(memory, env), "rule": RULE, "added": added(memory),
            "everything": everything(memory),
            "setup": setup(memory, env), "finder_tasks": [{"id": k, "title": v[0]} for k, v in TASKS.items()],
            "catalog_at": catalog(memory)["at"], "catalog_size": len(catalog(memory)["models"]),
            "test_size": len(TEST),
            "note": "Only public records go to these models. A job on a paid model goes back to the free choice when "
                    "the month's limit is reached. The test headlines were written for the test, not taken from the "
                    "news, so use a result as a guide."}


def everything(memory: Memory, days: int = 30) -> dict:
    """All of Buddy's AI use, not only the jobs routed here: the main conversation and every other feature report
    in from the log of model requests (usage.py, insights.py). Spending this month, and where it went."""
    try:
        from . import insights, usage  # noqa: PLC0415
        got = insights.report(memory, days=days, recent=0)
        top = lambda rows: [{"name": r["name"], "requests": r["requests"], "usd": r["cost_usd"]}  # noqa: E731
                            for r in sorted(rows, key=lambda r: -r["cost_usd"])[:8]]
        totals = got["totals"]
        return {"month_usd": round(usage.month_to_date(memory), 2), "days": got["days"], "usd": totals["cost_usd"],
                "requests": totals["requests"], "claude_requests": totals["claude_requests"],
                "free_requests": totals["free_requests"], "saved_by_router_usd": totals["saved_by_router_usd"],
                "saved_by_free_usd": totals["saved_by_free_usd"],
                "by_purpose": [{**r, "name": insights.purpose_name(r["name"])} for r in top(got["by_purpose"])],
                "by_model": top(got["by_model"])}
    except Exception:
        log.info("maestro: the request log is unavailable", exc_info=True)
        return {}


def providers(memory: Memory, env=None) -> list[dict]:
    """Every provider: its role, whether it may see anything about the owner, whether it is set up, and how it
    has been doing (from Buddy Connect's record of its calls)."""
    from .free_models import PROVIDERS as pool_providers  # noqa: PLC0415
    env_ = _env(env)
    connect = {"gemini": "gemini", "openai": "openai", "claude": "claude"}
    try:
        rows = {r["name"]: r for r in gateway.status(memory, env)}
    except Exception:
        log.debug("maestro: no Connect status", exc_info=True)
        rows = {}
    out = []
    for name, title, role, personal, paid in PROVIDERS:
        if name == "claude":
            ready = True
        elif name == "openai":
            ready = any(openai_setting(c, env) for c in OPENAI)
        elif name == "ollama":
            ready = False
        elif name == "groq":
            ready = bool((env_.get("BUDDY_GROQ_KEY") or "").strip())
        else:
            ready = bool((env_.get(pool_providers[name]["key"]) or "").strip())
        row = {"id": name, "title": title, "role": role, "may_see_personal": personal, "paid": paid, "set_up": ready,
               "off": False, "state": "", "detail": ""}
        seen = rows.get(connect.get(name, name))
        if seen:  # how its calls have gone, from Buddy Connect's record
            row["off"] = gateway.is_off(memory, connect.get(name, name))
            row["state"] = gateway.state_of(seen)
            row["detail"] = str(seen.get("last_error") or "")[:120]
        out.append(row)
    for g in added(memory):
        out.append({"id": f"added:{g['name']}", "title": g["title"], "role": f"Added by you: {g['model']} at {g['base']}",
                    "may_see_personal": False, "paid": "paid per use" if g["in"] or g["out"] else "free",
                    "set_up": bool((env_.get(key_name(g["name"])) or "").strip()), "off": False, "state": "",
                    "detail": "", "added": g["name"]})
    return out


# --- the model finder: what exists, what it costs, and what suits a task --------------------------------------
CATALOG_KEY, CATALOG_URL = "maestro:catalog", "https://openrouter.ai/api/v1/models"
MAKERS = {  # the maker's prefix in the public list -> (name, how Buddy can use its models)
    "anthropic": ("Anthropic", "claude"), "openai": ("OpenAI", "openai"), "mistralai": ("Mistral", "mistral"),
    "google": ("Google", "gemini"), "nvidia": ("NVIDIA", "nvidia"), "meta-llama": ("Meta (Llama)", ""),
    "deepseek": ("DeepSeek", ""), "qwen": ("Alibaba (Qwen)", ""), "x-ai": ("xAI", ""), "cohere": ("Cohere", ""),
    "perplexity": ("Perplexity", ""), "moonshotai": ("Moonshot (Kimi)", ""), "microsoft": ("Microsoft", ""),
    "amazon": ("Amazon", ""),
}
TASKS = {  # task -> (title, what matters for it, most it should cost per million tokens in, least context, name hints)
    "sort": ("Sort and label", "Labelling headlines, tagging, yes-or-no questions. Thousands of small calls, so price "
             "and speed matter most; a small model is enough. Check it with the 48-headline test.", 0.5, 8_000, "fast"),
    "extract": ("Pull out names and facts", "Finding companies, dates and relationships in a text. It must follow "
                "the format exactly and not add anything; a small-to-mid model usually does.", 1.5, 16_000, ""),
    "summarise": ("Summarise long texts", "Long articles, filings and papers. The whole text must fit, so a large "
                  "context matters more than cleverness.", 3.0, 200_000, ""),
    "analyse": ("Analyse and write", "Comparing evidence, writing a briefing or a report. Needs a mid model: cheap "
                "ones miss nuance and repeat themselves.", 5.0, 32_000, ""),
    "code": ("Write and fix code", "Small, contained code questions. Models tuned for code do better; anything "
             "across several files needs a strong model.", 5.0, 32_000, "code"),
    "reason": ("Hard questions", "Multi-step reasoning, strategy, research questions. Use a strong model and expect "
               "to pay for it; use it rarely.", 1e9, 64_000, "reason"),
    "personal": ("Anything about you", "Your email, money, family, health. Claude only; no other model is offered.",
                 1e9, 0, ""),
}
SETUP = {  # provider -> (setting to enter, where its key page is, what the free tier is)
    "mistral": ("BUDDY_MISTRAL_KEY", "https://console.mistral.ai/api-keys", "A free tier with a monthly limit."),
    "cerebras": ("BUDDY_CEREBRAS_KEY", "https://cloud.cerebras.ai", "A free tier with a daily limit."),
    "gemini": ("BUDDY_GEMINI_KEY", "https://aistudio.google.com/apikey", "A free tier with a daily limit."),
    "nvidia": ("BUDDY_NVIDIA_KEY", "https://build.nvidia.com", "Free credits for the models it hosts."),
    "openrouter": ("BUDDY_OPENROUTER_KEY", "https://openrouter.ai/keys", "Free models, with a daily limit."),
    "openai": ("BUDDY_OPENAI_KEY", "https://platform.openai.com/api-keys", "Paid. No free tier for the API."),
    "groq": ("BUDDY_GROQ_KEY", "https://console.groq.com/keys", "Used for speech to text only."),
}


def refresh_catalog(memory: Memory, fetch=None) -> dict:
    """Read the public list of models and prices (no key needed; nothing about the owner is sent)."""
    if fetch is None:
        from .markets import _fetch as fetch  # noqa: PLC0415  (https only, Buddy's user agent)
    raw = fetch(CATALOG_URL, {})
    data = (raw if isinstance(raw, dict) else json.loads(raw or b"{}")).get("data") or []
    models = []
    for m in data:
        mid = str(m.get("id") or "")
        price = m.get("pricing") or {}
        try:
            cost_in, cost_out = float(price.get("prompt") or 0) * 1e6, float(price.get("completion") or 0) * 1e6
        except (TypeError, ValueError):
            continue
        outs = (m.get("architecture") or {}).get("output_modalities") or ["text"]
        if "/" not in mid or "text" not in outs or cost_in < 0 or cost_out < 0:
            continue
        models.append({"id": mid[:100], "name": str(m.get("name") or mid)[:80], "maker": mid.split("/")[0],
                       "in": round(cost_in, 4), "out": round(cost_out, 4), "context": int(m.get("context_length") or 0),
                       "free": cost_in == 0 and cost_out == 0})
    if not models:
        return catalog(memory)  # keep the last list rather than nothing
    out = {"at": datetime.now(timezone.utc).isoformat(), "models": models[:800]}
    memory.set(CATALOG_KEY, json.dumps(out))
    return out


def catalog(memory: Memory) -> dict:
    try:
        return json.loads(memory.get(CATALOG_KEY) or "{}") or {"at": "", "models": []}
    except ValueError:
        return {"at": "", "models": []}


def _how(model: dict, env=None, memory: Memory | None = None) -> dict:
    """How Buddy can use a model: straight from its maker (if that provider is in the lineup), or through the
    fallback provider."""
    name, provider = MAKERS.get(model["maker"], (model["maker"].replace("-", " ").title(), ""))
    if provider == "claude":
        return {"maker": name, "via": "Claude, already set up", "ready": True}
    if provider:
        ready = next((p["set_up"] for p in providers(memory, env) if p["id"] == provider), False) if memory else False
        return {"maker": name, "via": f"{name} directly" + ("" if ready else " (needs its key)"), "ready": ready}
    return {"maker": name, "via": "through OpenRouter, or add its maker as a provider", "ready": False}


def find(memory: Memory, task: str = "", query: str = "", limit: int = 12, env=None) -> dict:
    """Models that suit a task (by price ceiling, context and what they are tuned for), or that match the words
    searched for, cheapest first. Price is not quality: the test and the job's own results decide."""
    from . import openrouter_catalog as hints  # noqa: PLC0415
    from .config import settings  # noqa: PLC0415
    got = catalog(memory)
    models, words = got["models"], [w for w in str(query or "").lower().split() if w][:4]
    note = ""
    if task == "personal":
        return {"task": task, "title": TASKS[task][0], "advice": TASKS[task][1], "at": got["at"], "models": [],
                "claude": [settings.fast_model, settings.chat_model, settings.model], "note": RULE}
    if task in TASKS:
        title, advice, ceiling, context, hint = TASKS[task]
        pattern = {"fast": hints.FAST, "code": hints.CODE, "reason": hints.REASON}.get(hint)
        fit = [m for m in models if m["in"] <= ceiling and m["context"] >= context
               and (pattern is None or pattern.search(m["id"] + " " + m["name"]))]
        if task == "reason":  # the strongest first: here the dearest in each maker's range is the best guide there is
            fit.sort(key=lambda m: -m["in"])
            note = "Shown dearest first: for hard questions a maker's top model is usually its dearest."
        else:
            fit.sort(key=lambda m: (not m["free"], m["in"] + m["out"]))
            note = "Shown cheapest first. Price is not quality: run the test before giving a model a job."
        models = fit
    else:
        title, advice = "Search", ""
        models = sorted(models, key=lambda m: (m["in"] + m["out"], m["id"]))
    if words:
        models = [m for m in models if all(w in (m["id"] + " " + m["name"]).lower() for w in words)]
    return {"task": task, "title": title, "advice": advice, "at": got["at"], "found": len(models), "note": note,
            "models": [{**m, **_how(m, env, memory)} for m in models[:max(1, min(limit, 40))]],
            "tasks": [{"id": k, "title": v[0]} for k, v in TASKS.items()]}


def setup(memory: Memory, env=None) -> list[dict]:
    """For each provider that needs a key: where to get it, the command to enter it, and whether it is set."""
    rows = {p["id"]: p for p in providers(memory, env)}
    out = [{"id": name, "title": rows[name]["title"], "role": rows[name]["role"], "page": page_url, "free": free,
            "command": f"sudo bash /opt/buddy/deploy/set.sh {setting}", "set_up": rows[name]["set_up"]}
           for name, (setting, page_url, free) in SETUP.items()]
    out += [{"id": f"added:{g['name']}", "title": g["title"], "role": "Added by you", "page": "", "free": "",
             "command": f"sudo bash /opt/buddy/deploy/set.sh {key_name(g['name'])}",
             "set_up": bool((_env(env).get(key_name(g["name"])) or "").strip())} for g in added(memory)]
    return out


def check_key(memory: Memory, provider: str, env=None, fetch=None) -> dict:
    """Ask a provider to list its models with the key that is set: proof the key works. The key is never shown."""
    from . import modelscout  # noqa: PLC0415
    from .free_models import PROVIDERS as pool_providers  # noqa: PLC0415
    env_ = _env(env)
    try:
        if provider.startswith("added:"):
            g = next((x for x in added(memory) if f"added:{x['name']}" == provider), None)
            key = (env_.get(key_name(g["name"])) or "").strip() if g else ""
            if not g or not key:
                raise ToolError("Its key isn't set yet.")
            got = gateway.call_url(memory, "added_ai", [g["base"] + "/models"], fetch=fetch,
                                   headers={"Authorization": f"Bearer {key}"}, purpose="maestro")
            ids = [str(m.get("id") or "") for m in (got or {}).get("data") or [] if isinstance(m, dict)]
        elif provider == "openai":
            got = gateway.call(memory, "openai", "/v1/models", fetch=fetch, env=env, purpose="maestro")
            ids = [str(m.get("id") or "") for m in (got or {}).get("data") or [] if isinstance(m, dict)]
        elif provider in pool_providers:
            key = (env_.get(pool_providers[provider]["key"]) or "").strip()
            if not key:
                raise ToolError("Its key isn't set yet.")
            ids = modelscout.list_models(provider, key, fetch)
        else:
            raise ToolError("That provider has no key to check.")
    except ToolError as e:
        return {"provider": provider, "ok": False, "says": str(e)}
    except Exception as e:
        return {"provider": provider, "ok": False, "says": f"{type(e).__name__}: {str(e)[:160]}"}
    ids = [i for i in ids if i]
    return {"provider": provider, "ok": bool(ids), "models": len(ids), "some": sorted(ids)[:8],
            "says": f"The key works: it lists {len(ids)} models." if ids else "It answered, but listed no models."}


TOOL = {
    "name": "maestro",
    "description":
        "Buddy Maestro: the AI providers Buddy can use and which one does each job. show: every provider (its "
        "role, whether it may see the owner's data, whether it is set up and working), the model chosen for each "
        "job, this month's spending and limit, and test results. set_model (job, choice), set_limit (dollars a "
        "month), test_model (choice: scores it on 48 labelled headlines), add_provider (name, base address, "
        "model id, price_in, price_out per million tokens), remove_provider (name): only when the owner asks. "
        "spending (days 7, 30 or 90; optionally component, then purpose): what Buddy's AI use cost, which part of "
        "Buddy spent it, this month against last month and where it is heading, down to single requests; since_reset: only what was spent "
        "after the last reset. reset_spending: mark now as the start of 'since reset' (deletes nothing, changes "
        "no limit), only when the owner asks. "
        "find_models (task and/or query): models that suit a task with their current prices per million tokens, "
        "from a public price list; say prices are from that list on its date and that price is not quality. setup: "
        "where to get each provider's key and the command to enter it. check_key (name): whether a key works. "
        "Use it for 'which models are you using', 'what are you spending on AI', 'add a model', 'which model is "
        "best for X', 'how much does X cost'. Buddy cannot sign up to a provider or create a key: the owner does "
        "that on the provider's own page, and enters the key with the command. Only Claude may "
        "see anything about the owner, and nothing here can change that. Never ask for a key in chat: after "
        "add_provider, give the set.sh command it returns.",
    "input_schema": {"type": "object", "properties": {
        "action": {"type": "string", "enum": ["show", "set_model", "set_limit", "test_model", "add_provider",
                                              "remove_provider", "find_models", "setup", "check_key", "spending",
                                              "reset_spending"]},
        "since_reset": {"type": "boolean"},
        "component": {"type": "string"}, "purpose": {"type": "string"}, "days": {"type": "integer"},
        "task": {"type": "string", "enum": list(TASKS)}, "query": {"type": "string"},
        "job": {"type": "string"}, "choice": {"type": "string"}, "dollars": {"type": "number"},
        "name": {"type": "string"}, "base": {"type": "string"}, "model": {"type": "string"},
        "price_in": {"type": "number"}, "price_out": {"type": "number"}},
        "required": ["action"], "additionalProperties": False},
}


async def run_tool(memory: Memory, args: dict) -> str:
    action = args.get("action")
    if action == "set_model":
        return set_choice(memory, str(args.get("job") or ""), str(args.get("choice") or ""))
    if action == "set_limit":
        return set_cap(memory, args.get("dollars"))
    if action == "test_model":
        return json.dumps(await test(memory, str(args.get("choice") or "free")), ensure_ascii=False)
    if action == "add_provider":
        return add_provider(memory, args.get("name"), args.get("base"), args.get("model"), args.get("price_in") or 0,
                            args.get("price_out") or 0)
    if action == "remove_provider":
        return remove_provider(memory, str(args.get("name") or ""))
    if action == "spending":
        from . import maestro_usage  # noqa: PLC0415
        got = maestro_usage.view(memory, int(args.get("days") or 30), str(args.get("component") or ""),
                                 str(args.get("purpose") or ""), None, bool(args.get("since_reset")))
        got.pop("by_day", None)
        return json.dumps(got, ensure_ascii=False)
    if action == "reset_spending":
        from . import maestro_usage  # noqa: PLC0415
        return maestro_usage.reset(memory)
    if action == "find_models":
        import asyncio  # noqa: PLC0415
        if not catalog(memory)["models"]:
            await asyncio.to_thread(refresh_catalog, memory)
        return json.dumps(find(memory, str(args.get("task") or ""), str(args.get("query") or "")), ensure_ascii=False)
    if action == "setup":
        return json.dumps(setup(memory), ensure_ascii=False)
    if action == "check_key":
        import asyncio  # noqa: PLC0415
        return json.dumps(await asyncio.to_thread(check_key, memory, str(args.get("name") or "")), ensure_ascii=False)
    got = page(memory)
    return json.dumps({k: got[k] for k in ("rule", "providers", "jobs", "spending", "tests", "added")}, ensure_ascii=False)
