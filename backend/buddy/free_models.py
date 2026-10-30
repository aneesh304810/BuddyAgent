"""Free-tier AI models for general tasks, chosen by what each is best at.

Rule (checked in code, not left to a model): anything with the user's personal data
or privacy involved stays on Claude. Only general content goes to a free model:
translating a public text, summarizing a public article, rewording, brainstorming,
explaining a concept, a general-knowledge answer, a general coding question. A request
that contains masked numbers, email addresses, phone numbers, secrets (API keys,
tokens, passwords, private keys, .env lines), the user's server addresses or anything
from the user's memory is refused and done by Claude instead.

Providers (each needs its own free key; all speak the OpenAI-style chat API):
  Gemini (Google)   long context, multilingual: summaries, translation, explanations
  Groq              very fast open models: quick answers, rewording, classification
  Mistral           strong in European languages: translation, writing, rewording
  Cerebras          very fast open models: quick answers, brainstorming
  DeepSeek          strong, low-cost coding model, through OpenRouter's free models
                    (DeepSeek's own API keeps data in China, so it's not used)
  GitHub Models     OpenAI's GPT models on a free tier: careful writing and code
  OpenRouter        many free models: a general fallback
Free tiers may use prompts to improve their models and have rate limits, so: general
content only, and when a provider is at its limit or fails, the next best one is
tried, then Claude's cheap model. Model names change often: each provider's model is
a setting, checked against the provider's model list.
"""

import ast
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
    # Same OpenRouter key; its own entry so code goes to DeepSeek first and it learns its own speed.
    "deepseek": {"base": "https://openrouter.ai/api/v1", "key": "BUDDY_OPENROUTER_KEY",
                 "model_setting": "BUDDY_DEEPSEEK_MODEL", "model": "deepseek/deepseek-chat-v3-0324:free",
                 "good_at": "code: writing, explaining and fixing it"},
    "github": {"base": "https://models.github.ai/inference", "key": "BUDDY_GITHUB_MODELS_KEY",
               "model_setting": "BUDDY_GITHUB_MODEL", "model": "openai/gpt-4.1-mini",
               "good_at": "OpenAI's GPT models, free with a GitHub account; careful writing and explaining"},
    "openrouter": {"base": "https://openrouter.ai/api/v1", "key": "BUDDY_OPENROUTER_KEY",
                   "model_setting": "BUDDY_OPENROUTER_MODEL", "model": "openrouter/free",
                   "good_at": "a general fallback"},
}
# Which providers to try first for each kind of task, by what they're best at.
BEST_FOR = {
    "translate": ["gemini", "github", "mistral", "groq", "cerebras", "openrouter"],
    "summarize": ["gemini", "github", "mistral", "groq", "openrouter", "cerebras"],
    "rewrite": ["mistral", "github", "groq", "gemini", "cerebras", "openrouter"],
    "brainstorm": ["gemini", "github", "cerebras", "groq", "mistral", "openrouter"],
    "explain": ["gemini", "github", "groq", "mistral", "cerebras", "openrouter"],
    "quick_answer": ["groq", "cerebras", "github", "gemini", "mistral", "openrouter"],
    "classify": ["groq", "cerebras", "github", "gemini", "mistral", "openrouter"],
    "code": ["deepseek", "github", "gemini", "mistral", "groq", "cerebras", "openrouter"],
}
INSTRUCTIONS = {
    "translate": "Translate the text into {target}. Keep the meaning and tone; output only the translation.",
    "summarize": "Summarize the text in a few short, plain sentences.",
    "rewrite": "Rewrite the text as asked, keeping the meaning. Output only the new text.",
    "brainstorm": "Give a short list of good, varied ideas.",
    "explain": "Explain clearly and simply, in a short paragraph.",
    "quick_answer": "Answer briefly and accurately. If you're not sure, say so.",
    "classify": "Answer with only the label that fits best.",
    "code": "You are an expert programmer. Answer with correct, minimal, working code and a short "
    "explanation. Use only real libraries and functions; if you're not sure something exists, say so. "
    "Point out anything risky (deleting files, security).",
}
MAX_CHARS = 30_000
# Speed first for these; for the rest, the best-suited model first unless it keeps failing.
SPEED_FIRST = {"quick_answer", "classify", "rewrite"}
TIMEOUT = {"quick_answer": 12, "classify": 10, "rewrite": 15, "brainstorm": 20, "explain": 20,
           "translate": 25, "summarize": 40, "code": 40}
MAX_TOKENS = {"code": 3000}  # others: 1500
EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
# A + and digits, or the 3-3-4 shape: plain long numbers in code aren't phone numbers.
PHONE = re.compile(r"\+\d[\d\s().-]{7,}\d|\(?\b\d{3}\)?[\s.-]\d{3}[\s.-]\d{4}\b")
SECRETS = [
    re.compile(r"\b(sk|rk|pk)-[A-Za-z0-9_-]{16,}"),                     # OpenAI, Anthropic, Stripe...
    re.compile(r"\b(ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}|github_pat_\w{20,}"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),                                # AWS
    re.compile(r"\bAIza[0-9A-Za-z_-]{30,}"),                             # Google
    re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"),                        # Slack
    re.compile(r"\bdop_v1_[a-f0-9]{20,}"),                               # DigitalOcean
    re.compile(r"\btskey-\w+-\w{10,}"),                                  # Tailscale
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"\beyJ[\w-]{10,}\.eyJ[\w-]{10,}\.[\w-]{10,}"),             # JWT
    re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{16,}", re.IGNORECASE),
    re.compile(r"(?i)\b[\w-]*(password|passwd|pwd|secret|token|api[_-]?key|access[_-]?key|private[_-]?key)[\w-]*"
               r"[\"']?\s*[:=]\s*[\"']?(?!\s*$)(?!(os\.|getenv|env\[|process\.env|\$\{?|<|\*{3}|x{3}|\.\.\.))[^\s\"',;]{6,}"),
    re.compile(r"(?i)\b[a-z][\w-]*\.ts\.net\b"),                         # the user's Tailscale machines
    re.compile(r"(?i)\b(postgres|postgresql|mysql|mongodb(\+srv)?|redis|amqp)://[^\s:/]+:[^\s@/]+@"),  # URL with a password
]


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
    if any(p.search(text) for p in SECRETS):
        reasons.append("a key, password or private address")
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


# Coding work that needs Claude: the hard, risky or system-level kind. A free model gets
# only short, self-contained questions (a snippet, a regex, a one-liner, an error message,
# a small function, a concept), where it's as good and the answer is easy to check.
CLAUDE_CODE = [
    (re.compile(r"(?i)\b(refactor|architect|design (the|a|my)|system design|restructure|rewrite (the|my|this) "
                r"(app|project|codebase|service)|whole (app|project|codebase|repo))"), "a design or refactoring job"),
    (re.compile(r"(?i)\b(multi-?file|several files|across (the )?(files|modules|repo|codebase)|my (repo|codebase|project))"),
     "work across several files"),
    (re.compile(r"(?i)\b(security|secure|vulnerab|exploit|auth(entication|orization)?|oauth|encrypt|decrypt|crypto|"
                r"permission|sandbox|injection|xss|csrf)\b"), "security"),
    (re.compile(r"(?i)\b(production|deploy|deployment|migration|migrate|database schema|backup|restore|"
                r"rm -rf|drop table|delete (all|every)|firewall|iptables|sudo|systemd|cron)\b"),
     "changes to a live system"),
    (re.compile(r"(?i)\b(race condition|deadlock|concurren|memory leak|performance issue|intermittent|flaky)\b"),
     "a hard bug"),
    (re.compile(r"(?i)\b(buddy|mcp server|agent|tool use|your own code|this server|my server|droplet|tailscale)\b"),
     "Buddy's own systems or agents"),
]
MAX_FREE_CODE_LINES = 120
MAX_FREE_CODE_CHARS = 8_000


def code_route(text: str) -> str:
    """'' when a free model can take this coding task, else why Claude should do it."""
    if len(text) > MAX_FREE_CODE_CHARS or text.count("\n") > MAX_FREE_CODE_LINES:
        return "a lot of code"
    for pattern, why in CLAUDE_CODE:
        if pattern.search(text):
            return why
    return ""


def answer_problem(kind: str, answer: str) -> str:
    """'' if the free answer looks usable, else what's wrong (the next model is tried)."""
    lowered = answer.lower()[:300]
    if any(p in lowered for p in ("i can't help", "i cannot help", "i'm unable to", "as an ai")):
        return "declined"
    if kind == "code":
        for block in re.findall(r"```(?:python|py)\s*\n(.*?)```", answer, re.S):
            try:
                ast.parse(block)
            except SyntaxError as e:
                return f"python syntax error (line {e.lineno})"
    return ""


class NeedsClaude(ToolError):
    pass


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
        if kind == "code" and (why := code_route(instruction + "\n" + text)):
            usage.count(self.memory, "free:code_to_claude")
            raise NeedsClaude(f"This coding task needs you, not a free model ({why}): do it yourself, carefully (hand_over first if you have that tool).")
        system = INSTRUCTIONS[kind].format(target=target or "English")
        prompt = (instruction.strip() + "\n\n" if instruction.strip() else "") + text
        tried = []
        for name in self.order(kind)[:3]:  # at most three tries, so a slow day never drags on
            started = time.monotonic()
            try:
                answer, tokens = await asyncio.wait_for(
                    asyncio.to_thread(self._call, name, system, prompt, TIMEOUT[kind], MAX_TOKENS.get(kind, 1500)), timeout=TIMEOUT[kind] + 2)
                if not answer:
                    raise ValueError("empty answer")
                if problem := answer_problem(kind, answer):
                    raise ValueError(problem)
                self._learn(name, time.monotonic() - started)
                usage.count(self.memory, f"free:{name}")
                usage.log_call(self.memory, provider=name, model=self.model_of(name), purpose=f"general:{kind}",
                               input_tokens=tokens.get("prompt_tokens") or 0,
                               output_tokens=tokens.get("completion_tokens") or 0,
                               seconds=time.monotonic() - started, stop="free", request=prompt, reply=answer)
                return {"answer": answer, "model": f"{name}/{self.model_of(name)}", "free": True,
                        "seconds": round(time.monotonic() - started, 1)}
            except RateLimited as e:
                self.cooldown[name] = time.time() + e.wait
                tried.append(f"{name}: at its free limit")
                usage.log_call(self.memory, provider=name, model=self.model_of(name), purpose=f"general:{kind}",
                               seconds=time.monotonic() - started, ok=False, stop="at its free limit",
                               request=prompt)
            except Exception as e:
                self._learn(name, None)
                self.cooldown[name] = time.time() + 120
                tried.append(f"{name}: {type(e).__name__}")
                usage.log_call(self.memory, provider=name, model=self.model_of(name), purpose=f"general:{kind}",
                               seconds=time.monotonic() - started, ok=False, stop=f"error: {type(e).__name__}",
                               request=prompt)
                log.info("free model %s failed: %s", name, e)
        answer = await self._claude(system, prompt, kind)  # every free option busy or off: Claude's cheap model
        usage.count(self.memory, "free:fallback_claude")
        return {"answer": answer, "model": settings.fast_model, "free": False, "tried": tried}

    def _call(self, name: str, system: str, prompt: str, timeout: float = 30,
              max_tokens: int = 1500) -> tuple[str, dict]:
        p = PROVIDERS[name]
        body = {"model": self.model_of(name), "max_tokens": max_tokens, "temperature": 0.3,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}]}
        data = self.http(f"{p['base']}/chat/completions", body,
                         {"Authorization": f"Bearer {self.env[p['key']].strip()}"}, timeout)
        return (data["choices"][0]["message"]["content"] or "").strip(), data.get("usage") or {}

    async def _claude(self, system: str, prompt: str, kind: str = "") -> str:
        from .agent import fallback_kwargs

        model = settings.fast_model or settings.chat_model
        started = time.monotonic()
        response = await client.beta.messages.create(model=model, max_tokens=1500, system=system,
                                                     messages=[{"role": "user", "content": prompt}],
                                                     **fallback_kwargs(model))
        if hasattr(response, "usage"):
            usage.record(self.memory, response.model, response.usage, response=response, started=started,
                         request=prompt, label=f"general:{kind}")
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
    "concept, a general-knowledge quick answer, classify text, or a SMALL self-contained coding question (code: "
    "a snippet, regex, one-liner, an error message, a small function, a concept; DeepSeek first). Hard or "
    "risky code stays with you: refactoring, design, several files, security, live systems, hard bugs, agents, "
    "Buddy itself (the tool refuses those). NEVER for anything about the user: their email, money, health, "
    "location, contacts, plans, memory, messages, keys or servers stay with you (refused anyway). Always "
    "check the answer before passing it on.",
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
    except (PersonalData, NeedsClaude) as e:
        return str(e)
    if result.get("free"):
        result["review"] = ("From a free model: check it before using it. If anything is wrong, missing or "
                            "unsafe, fix it or do the task yourself; never pass on code you wouldn't stand behind.")
    return json.dumps(result, ensure_ascii=False)
