"""Which AI model does each job of the Intelligence Platform, what it has cost, and a test to compare them.

Most of the platform uses no AI at all: tagging, stages, convergences, the patterns and the practice scoring are
code. The jobs that do use a model (JOBS) each have their own setting, so the cheapest model that does a job
well can be used for it and a dearer one kept for the few jobs that need it.

  Choices   free           Buddy's free models first, the cheap Claude model if none answers (the default)
            claude:fast    Claude's cheap model      claude:chat   its mid model      claude:deep   its strongest
            openai:cheap   an OpenAI model for volume work       openai:analysis   one for deeper analysis
            The OpenAI choices are there only when the owner has set its key (BUDDY_OPENAI_KEY), the model's id
            and its price (BUDDY_OPENAI_CHEAP_MODEL and _PRICE, BUDDY_OPENAI_ANALYSIS_MODEL and _PRICE; a price
            is "input,output" in US dollars per million tokens, from OpenAI's own pricing page). No id or price
            is built in, because they change and Buddy cannot check them.
  Limit     a monthly spending limit in dollars for the paid choices (CAP_KEY, $5 unless changed). At the limit
            a job goes back to the free choice until the month ends. Spending is counted per job.
  Test      a fixed set of headlines with the right label beside each (TEST). Any choice can be run on it to see
            how often it agrees, how long it took and what it cost, before a job is switched to it. The set was
            written for this purpose, not taken from the news, so treat a result as a guide and look at the
            labels on the Themes tab as well.

Only public records go through here (headlines, titles of papers and filings). Nothing about the owner does,
so a provider other than Claude is allowed for these jobs and for nothing else in Buddy.
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
}
CLAUDE = {"claude:fast": ("fast_model", "Claude, cheap model"), "claude:chat": ("chat_model", "Claude, mid model"),
          "claude:deep": ("model", "Claude, strongest model")}
OPENAI = {"openai:cheap": ("BUDDY_OPENAI_CHEAP", "OpenAI, volume model"),
          "openai:analysis": ("BUDDY_OPENAI_ANALYSIS", "OpenAI, analysis model")}
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


def choices(env=None) -> list[dict]:
    from .config import settings  # noqa: PLC0415
    out = [{"id": "free", "title": "Free models, then Claude's cheap model", "ready": True, "paid": False}]
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
    found = next((c for c in choices(env) if c["id"] == choice), None)
    if not found:
        raise ToolError("Choices are: " + ", ".join(c["id"] for c in choices(env)))
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
    started = time.monotonic()
    if choice in OPENAI:
        got = await asyncio.to_thread(_openai, memory, choice, prompt, system, fetch, env)
    elif choice in CLAUDE:
        got = await _claude(memory, choice, prompt, system)
    else:
        got = await _free(memory, prompt, system)
    return {**got, "seconds": round(time.monotonic() - started, 2)}


SYSTEM = "You work from public records only. Answer with the one line of JSON asked for, nothing else."


async def ask(memory: Memory, job: str, prompt: str, system: str = SYSTEM, fetch=None, env=None) -> dict | None:
    """The JSON answer for a job, from the model chosen for it. A paid choice that has reached the month's limit,
    or that fails, falls back to the free choice."""
    from .benefits import last_json  # noqa: PLC0415
    choice = choice_for(memory, job)
    if choice != "free" and spending(memory)["total"] >= cap(memory):
        log.info("intelligence models: the month's limit is reached; %s uses the free choice", job)
        choice = "free"
    for attempt in dict.fromkeys((choice, "free")):
        try:
            got = await ask_with(memory, attempt, prompt, system, fetch, env)
        except Exception as e:
            log.info("intelligence models: %s failed for %s (%s)", attempt, job, type(e).__name__)
            continue
        if got["usd"]:
            _spent(memory, job, got["usd"], got["in"], got["out"])
        parsed = last_json(got["text"])
        if parsed:
            return parsed
    return None


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
    found = next((c for c in choices(env) if c["id"] == choice), None)
    if not found or not found["ready"]:
        raise ToolError(found["setup"] if found else "Choices are: " + ", ".join(c["id"] for c in choices(env)))
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
            "choices": choices(env), "spending": spending(memory), "tests": list(tests.values()),
            "test_size": len(TEST),
            "note": "Only public records go to these models. A job on a paid model goes back to the free choice when "
                    "the month's limit is reached. The test headlines were written for the test, not taken from the "
                    "news, so use a result as a guide."}
