"""Free-tier AI models for general tasks, chosen by what each is best at.

Rule (checked in code, not left to a model): anything with the user's personal data
or privacy involved stays on Claude. Only general content goes to a free model:
translating a public text, summarizing a public article, rewording, brainstorming,
explaining a concept, a general-knowledge answer. A request that contains masked
numbers, email addresses, phone numbers or anything from the user's memory is refused
and done by Claude instead.

Providers (each needs its own free key; all speak the OpenAI-style chat API):
  Gemini (Google)   long context, multilingual: summaries, translation, explanations
  Groq              very fast open models: quick answers, rewording, classification
  Mistral           strong in European languages: translation, writing, rewording
  Cerebras          very fast open models: quick answers, brainstorming
  OpenRouter        many free models: a general fallback
Free tiers may use prompts to improve their models and have rate limits, so: general
content only, and when a provider is at its limit or fails, the next best one is
tried, then Claude's cheap model. Model names change often: each provider's model is
a setting, checked against the provider's model list.
"""

import json
import logging
import os
import re
import time
import urllib.error
import urllib.request

from . import usage
from .config import claude_client, settings
from .privacy import mask
from .tools import ToolError, require_https

log = logging.getLogger(__name__)
client = claude_client()

PROVIDERS = {
    "gemini": {"base": "https://generativelanguage.googleapis.com/v1beta/openai", "key": "BUDDY_GEMINI_KEY",
               "model_setting": "BUDDY_GEMINI_MODEL", "model": "gemini-flash-latest",
               "good_at": "long context and many languages"},
    "groq": {"base": "https://api.groq.com/openai/v1", "key": "BUDDY_GROQ_KEY",
             "model_setting": "BUDDY_GROQ_MODEL", "model": "llama-3.3-70b-versatile",
             "good_at": "very fast answers"},
    "mistral": {"base": "https://api.mistral.ai/v1", "key": "BUDDY_MISTRAL_KEY",
                "model_setting": "BUDDY_MISTRAL_MODEL", "model": "mistral-small-latest",
                "good_at": "European languages and writing"},
    "cerebras": {"base": "https://api.cerebras.ai/v1", "key": "BUDDY_CEREBRAS_KEY",
                 "model_setting": "BUDDY_CEREBRAS_MODEL", "model": "llama-3.3-70b",
                 "good_at": "very fast answers"},
    "openrouter": {"base": "https://openrouter.ai/api/v1", "key": "BUDDY_OPENROUTER_KEY",
                   "model_setting": "BUDDY_OPENROUTER_MODEL", "model": "openrouter/free",
                   "good_at": "a general fallback"},
}
# Which providers to try first for each kind of task, by what they're best at.
BEST_FOR = {
    "translate": ["gemini", "mistral", "groq", "cerebras", "openrouter"],
    "summarize": ["gemini", "mistral", "groq", "openrouter", "cerebras"],
    "rewrite": ["mistral", "groq", "gemini", "cerebras", "openrouter"],
    "brainstorm": ["gemini", "cerebras", "groq", "mistral", "openrouter"],
    "explain": ["gemini", "groq", "mistral", "cerebras", "openrouter"],
    "quick_answer": ["groq", "cerebras", "gemini", "mistral", "openrouter"],
    "classify": ["groq", "cerebras", "gemini", "mistral", "openrouter"],
}
INSTRUCTIONS = {
    "translate": "Translate the text into {target}. Keep the meaning and tone; output only the translation.",
    "summarize": "Summarize the text in a few short, plain sentences.",
    "rewrite": "Rewrite the text as asked, keeping the meaning. Output only the new text.",
    "brainstorm": "Give a short list of good, varied ideas.",
    "explain": "Explain clearly and simply, in a short paragraph.",
    "quick_answer": "Answer briefly and accurately. If you're not sure, say so.",
    "classify": "Answer with only the label that fits best.",
}
MAX_CHARS = 30_000
# Speed first for these; for the rest, the best-suited model first unless it keeps failing.
SPEED_FIRST = {"quick_answer", "classify", "rewrite"}
TIMEOUT = {"quick_answer": 12, "classify": 10, "rewrite": 15, "brainstorm": 20, "explain": 20,
           "translate": 25, "summarize": 40}
EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
PHONE = re.compile(r"(?:\+?\d[\s().-]?){8,}")


class PersonalData(ToolError):
    pass


def check_general(text: str, memory=None) -> None:
    """Raise PersonalData if the text carries personal details. Free models never get them."""
    reasons = []
    if mask(text) != text:
        reasons.append("card, ID or code numbers")
    if EMAIL.search(text):
        reasons.append("an email address")
    if PHONE.search(text):
        reasons.append("a phone number")
    if memory is not None and _mentions_memory(text, memory):
        reasons.append("details from your memory")
    if reasons:
        raise PersonalData("This contains personal details (" + ", ".join(reasons) + "), so it stays on Claude: "
                           "do it yourself instead of using general_task.")


COMMON = {"the", "and", "for", "with", "that", "this", "from", "have", "has", "was", "are", "my", "his", "her",
          "their", "our", "your", "likes", "loves", "lives", "works", "wants", "is", "in", "on", "at", "to", "of"}


def _mentions_memory(text: str, memory) -> bool:
    """True if the text names something from the user's memory: a name, or any two-word phrase."""
    words_in_text = set(re.findall(r"[\w'-]+", text.lower()))
    lowered = " ".join(re.findall(r"[\w'-]+", text.lower()))
    for fact in memory.recall("", limit=200):
        words = re.findall(r"[\w'-]+", fact)
        for i, word in enumerate(words):
            if i and word[:1].isupper() and len(word) >= 3 and word.lower() in words_in_text:
                return True  # a name or place from memory ("Priya", "Bangalore")
            if i + 1 < len(words):
                a, b = words[i].lower(), words[i + 1].lower()
                if a not in COMMON and b not in COMMON and len(a) >= 3 and len(b) >= 3 and f"{a} {b}" in lowered:
                    return True
    return False


class RateLimited(Exception):
    def __init__(self, wait: float):
        self.wait = wait


class FreeModels:
    def __init__(self, memory, env=None, http=None):
        self.memory = memory
        self.env = os.environ if env is None else env
        self.http = http or _post
        self.cooldown: dict[str, float] = {}
        # Learned as Buddy goes: average answer time (seconds) and recent success rate per provider.
        self.speed: dict[str, float] = {}
        self.success: dict[str, float] = {}

    def order(self, kind: str) -> list[str]:
        """Providers to try, best first: suited to the task, then fast and reliable lately."""
        candidates = [n for n in BEST_FOR[kind] if n in self.enabled() and self.cooldown.get(n, 0) <= time.time()]
        rank = {n: i for i, n in enumerate(candidates)}

        def score(name: str) -> float:
            unreliable = 1 - self.success.get(name, 1.0)  # 0 when it always works
            if kind in SPEED_FIRST:
                return self.speed.get(name, 3.0) + 10 * unreliable + 0.3 * rank[name]
            return rank[name] + 5 * unreliable  # capability first; a flaky provider drops back
        return sorted(candidates, key=score)

    def _learn(self, name: str, seconds: float | None) -> None:
        ok = seconds is not None
        self.success[name] = 0.8 * self.success.get(name, 1.0) + 0.2 * (1.0 if ok else 0.0)
        if ok:
            self.speed[name] = seconds if name not in self.speed else 0.7 * self.speed[name] + 0.3 * seconds

    def enabled(self) -> list[str]:
        if (self.env.get("BUDDY_FREE_MODELS") or "").strip() != "1":
            return []
        return [name for name, p in PROVIDERS.items() if (self.env.get(p["key"]) or "").strip()]

    def model_of(self, name: str) -> str:
        p = PROVIDERS[name]
        return (self.env.get(p["model_setting"]) or p["model"]).strip()

    async def run(self, kind: str, text: str, instruction: str = "", target: str = "English") -> dict:
        import asyncio

        if kind not in BEST_FOR:
            raise ToolError(f"Kinds: {', '.join(BEST_FOR)}.")
        text = text[:MAX_CHARS]
        check_general(text + " " + instruction, self.memory)
        system = INSTRUCTIONS[kind].format(target=target or "English")
        prompt = (instruction.strip() + "\n\n" if instruction.strip() else "") + text
        tried = []
        for name in self.order(kind)[:3]:  # at most three tries, so a slow day never drags on
            started = time.monotonic()
            try:
                answer = await asyncio.wait_for(asyncio.to_thread(self._call, name, system, prompt, TIMEOUT[kind]),
                                                timeout=TIMEOUT[kind] + 2)
                if not answer:
                    raise ValueError("empty answer")
                self._learn(name, time.monotonic() - started)
                usage.count(self.memory, f"free:{name}")
                return {"answer": answer, "model": f"{name}/{self.model_of(name)}", "free": True,
                        "seconds": round(time.monotonic() - started, 1)}
            except RateLimited as e:
                self.cooldown[name] = time.time() + e.wait
                tried.append(f"{name}: at its free limit")
            except Exception as e:
                self._learn(name, None)
                self.cooldown[name] = time.time() + 120
                tried.append(f"{name}: {type(e).__name__}")
                log.info("free model %s failed: %s", name, e)
        answer = await self._claude(system, prompt)  # every free option busy or off: Claude's cheap model
        usage.count(self.memory, "free:fallback_claude")
        return {"answer": answer, "model": settings.fast_model, "free": False, "tried": tried}

    def _call(self, name: str, system: str, prompt: str, timeout: float = 30) -> str:
        p = PROVIDERS[name]
        body = {"model": self.model_of(name), "max_tokens": 1500, "temperature": 0.3,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}]}
        data = self.http(f"{p['base']}/chat/completions", body,
                         {"Authorization": f"Bearer {self.env[p['key']].strip()}"}, timeout)
        return data["choices"][0]["message"]["content"].strip()

    async def _claude(self, system: str, prompt: str) -> str:
        from .agent import fallback_kwargs

        model = settings.fast_model or settings.chat_model
        response = await client.beta.messages.create(model=model, max_tokens=1500, system=system,
                                                     messages=[{"role": "user", "content": prompt}],
                                                     **fallback_kwargs(model))
        if hasattr(response, "usage"):
            usage.record(self.memory, response.model, response.usage)
        return "".join(b.text for b in response.content if b.type == "text").strip()

    def status(self) -> dict:
        from .tools import local_now

        month = local_now().strftime("%Y-%m")
        counts = {r["key"].split(":")[1]: int(float(r["value"])) for r in self.memory.db.execute(
            "SELECT key, value FROM kv WHERE key LIKE 'count:free:%' AND key LIKE ?", (f"%:{month}",))}
        return {"enabled": {n: self.model_of(n) for n in self.enabled()}, "calls_this_month": counts,
                "busy": [n for n, t in self.cooldown.items() if t > time.time()],
                "average_seconds": {n: round(v, 1) for n, v in self.speed.items()},
                "reliability": {n: round(v, 2) for n, v in self.success.items()}}


def _post(url: str, body: dict, headers: dict, timeout: float = 30) -> dict:
    request = urllib.request.Request(require_https(url), data=json.dumps(body).encode(), method="POST",
                                     headers={"Content-Type": "application/json", "User-Agent": "Buddy/1.0", **headers})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # https only  # nosec B310
            return json.loads(response.read())
    except urllib.error.HTTPError as e:
        if e.code == 429:
            raise RateLimited(float(e.headers.get("Retry-After") or 60)) from e
        raise


GENERAL_TOOL = {
    "name": "general_task",
    "description": "Hand a GENERAL task to a free AI model chosen for the job, to save money: translate a public "
    "text, summarize a public article or web page, rewrite or polish general text, brainstorm ideas, explain a "
    "concept, a general-knowledge quick answer, or classify text. NEVER for anything about the user: their "
    "email, money, health, location, contacts, plans, memory or messages stay with you (the tool refuses them "
    "anyway). Check the answer before passing it on.",
    "input_schema": {
        "type": "object",
        "properties": {
            "kind": {"type": "string", "enum": list(BEST_FOR)},
            "text": {"type": "string", "description": "The general content to work on"},
            "instruction": {"type": "string", "description": "What to do, if more than the kind says"},
            "target_language": {"type": "string", "description": "For translate"},
        },
        "required": ["kind", "text"],
        "additionalProperties": False,
    },
}


async def run_general_tool(free: FreeModels, args: dict) -> str:
    try:
        result = await free.run(args["kind"], args.get("text", ""), args.get("instruction", ""),
                                args.get("target_language", "English"))
    except PersonalData as e:
        return str(e)
    return json.dumps(result, ensure_ascii=False)
