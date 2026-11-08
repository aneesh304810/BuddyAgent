"""Listen-in mode: Buddy hears the people around the user and whispers when it matters.

It whispers for three reasons:
  - translation: someone speaks a language the user doesn't understand (anything
    outside settings.known_languages); Buddy gives just the gist in English
  - about_you:   in such a language, people are talking about the user, mocking
    them, or planning something that concerns them
  - correction:  someone (in any language) states something technically wrong
    with confidence; Buddy says what is actually correct

Speech is analysed live and kept only in memory, in a short rolling window.
Nothing said by other people is written to disk.

To keep always-on listening affordable, speech is gathered into batches
(settings.listen_batch_seconds) and a cheap model (settings.listen_triage_model)
first decides whether a batch could need a whisper. Only then does the listen
model read it closely. In a room speaking your languages, most batches stop at
the cheap first pass.
"""

import asyncio
import json
import logging
import os
import time
from collections import deque
from collections.abc import Awaitable, Callable

from . import usage
from .agent import client, fallback_kwargs
from .config import settings
from .memory import Memory
from . import transcripts
from .privacy import mask
from .thrift import Thrift

log = logging.getLogger(__name__)

WINDOW_SECONDS = 120
# What was said in the room stays in memory (never on disk) this long, so you can ask "what are
# they talking about?" or "what did he just say?". Cleared when Listen-in stops.
ROOM_MEMORY_SECONDS = int(os.getenv("BUDDY_ROOM_MEMORY_MINUTES", "10")) * 60
MAX_SEGMENTS = 300

AMBIENT_PROMPT = """\
You are {assistant}, {user}'s AI companion, quietly listening through {user}'s \
earbuds to the conversation around them. You receive a live speech-recognition \
transcript. It cannot tell speakers apart, may include {user}'s own words, and \
has recognition errors. Speech may be in any language, possibly transcribed in \
Latin script or mixed.

{user} understands these languages perfectly: {known}. (The [xx] tag on each line is speech \
recognition's guess at the language and is often wrong: judge the language from the words. A tag \
ending in "?" means recognition was unsure of that line: its words may be garbled or invented.) Never translate or \
summarise speech in those languages; {user} heard it already.

Decide whether to whisper something short into {user}'s ear right now. Whisper \
only when it is genuinely useful; staying quiet is the normal case. Reasons:

- "translation": people are talking in a language {user} does NOT understand. \
Give the gist of the conversation in English: what it's about and the main \
point, in one or two short sentences, naming the language ("In Spanish: they're \
planning dinner; they want to go at eight and are asking if you're coming."). \
Use the earlier lines for context. Skip bare greetings and small talk. Be honest: \
never build a gist from unsure [xx?] lines or text that reads as garbled; if foreign \
talk is going on but the words are unclear, either stay quiet or whisper only that \
("People nearby are talking in Spanish; I couldn't make out what.") once, not repeatedly.
- "about_you": in a language {user} does NOT understand, people are talking \
about {user}, making fun of them, insulting them, or planning something that \
affects them. Say plainly what was said and the tone, without exaggerating. \
If it's only a mild joke, say so.
- "correction": someone, in any language, confidently states something \
technically wrong (engineering, software, science, finance, numbers). Say what \
is actually correct in one or two sentences, so {user} can respond. Don't \
nitpick opinions, jokes, or harmless simplifications.

Rules:
- Only react to the NEW lines; earlier lines are context. Never repeat \
something already in "Already whispered".
- Be honest about uncertainty ("I think they said...") when the transcript is \
garbled. Never invent what people said.
- The whisper is spoken aloud in {user}'s ear, so no markdown; keep it under \
thirty-five words.

Names and details that refer to {user}:
{about}
"""

TRIAGE_PROMPT = """\
You screen a live transcript of the conversation around {user} for their AI \
companion. Two jobs, for the NEW lines only.

1. attention: true if any NEW line
- is in a language other than these, which {user} understands: {known} (judge the language from \
the words themselves: the [xx] tags are speech recognition's guesses and are often wrong)
- mentions {user} or the details below, or seems to talk about someone present
Otherwise false. When unsure, true.

2. claims: factual claims someone states as true that could be wrong or made up: science, health, \
technology, money and markets, history, law, statistics, numbers, how things work, general knowledge. \
Rewrite each as one standalone, general statement in English that can be checked on its own: no names, \
no "he/she/I/you", nothing about anyone's private life ("Vaccines cause autism.", "Python lists are \
immutable.", "The S&P 500 returned 30% last year."). Skip opinions, jokes, plans, feelings, personal \
stories and anything you can't state without someone's personal details. At most 3; [] when none.

3. plans: concrete plans for {user} or their family with a day or time, said in the NEW lines ("soccer \
at 10", "dentist on Thursday at 4", "pick up Amma from the station at 6"). Each as {{"what": short, \
"when": as said}}. Skip vague ones ("sometime", "later") and other people's plans that don't involve \
{user}'s family. [] when none.

Details that refer to {user}:
{about}
"""

TRIAGE_SCHEMA = {
    "type": "object",
    "properties": {"attention": {"type": "boolean"},
                   "claims": {"type": "array", "items": {"type": "string"}},
                   "plans": {"type": "array", "items": {"type": "object", "properties": {
                       "what": {"type": "string"}, "when": {"type": "string"}},
                       "required": ["what", "when"], "additionalProperties": False}}},
    "required": ["attention", "claims", "plans"],
    "additionalProperties": False,
}

FACT_PROMPT = """You fact-check general claims overheard in a conversation, for a person who wants to know \
whether what they're hearing is true and how to question it politely. For each claim give:
- verdict: "true", "false" (wrong), "misleading" (partly true, or missing what matters) or "unsure" (you \
can't tell, it depends, or it's a matter of opinion)
- confidence: "high" only when you are sure from well-established facts; else "medium" or "low"
- correct: what is actually true, in one short sentence with a number or fact where it helps
- ask: one short, polite question the person could ask to test the claim ("Where did you see that? I \
thought...", "Is that nominal or after inflation?"), never rude
Be strict: don't call something false because of wording, rounding or a harmless simplification. Answer \
with JSON only: {"checks": [{"claim": "...", "verdict": "...", "confidence": "...", "correct": "...", \
"ask": "..."}]}"""
FACT_GAP_SECONDS = 45     # at most one fact-check whisper this often: never a running commentary
PLAN_GAP_SECONDS = 120    # at most one "want a reminder?" this often

SCHEMA = {
    "type": "object",
    "properties": {
        "whisper": {"type": "boolean"},
        "kind": {"type": "string", "enum": ["none", "translation", "about_you", "correction"]},
        "message": {"type": "string"},
    },
    "required": ["whisper", "kind", "message"],
    "additionalProperties": False,
}

THINK_PROMPT = """\
You are {assistant}, {user}'s AI companion, listening through their earbuds. \
{user} just said "let me think": they are buying a few seconds to answer \
something they were asked. You receive the live transcript of the last couple \
of minutes (no speaker labels, recognition errors possible, any language).

Find the most recent question, problem or request put to {user} and whisper a \
ready-to-use answer they can say in their own voice:
- Lead with the answer in one sentence, then at most two short supporting \
points. Under 45 words, plain speech, no lists or markdown.
- Phrase it so {user} can say it directly ("I'd say...", "The main reason is...").
- Maths, numbers or technical problems: give the result and the one key step.
- Opinion or judgement questions: give a balanced, sensible position.
- Questions about {user}'s own life or work: use only what you know below; if \
you don't know, give a structure instead ("Mention a project where you...").
- Answer in English unless the question was in one of {user}'s languages and \
an answer in that language clearly fits better.
- If you can't find a question, set found to false.

What you know about {user}:
{facts}
"""

THINK_SCHEMA = {
    "type": "object",
    "properties": {"found": {"type": "boolean"}, "answer": {"type": "string"}},
    "required": ["found", "answer"],
    "additionalProperties": False,
}


def is_think_cue(text: str) -> bool:
    """Did the user just say "let me think" (or another configured cue)?"""
    spoken = " ".join(text.lower().replace(",", " ").replace(".", " ").split())
    return any(p.strip() and p.strip() in spoken for p in settings.think_phrases.lower().split(","))


Whisper = Callable[[str, str], Awaitable[None]]



FOREIGN_BATCH_SECONDS = 5


def whisper_languages() -> list[str]:
    from .whisper import known_languages

    return known_languages()

class AmbientListener:
    def __init__(self, memory: Memory, whisper: Whisper, thrift: Thrift | None = None):
        self.memory = memory
        self.whisper = whisper
        self.thrift = thrift
        about = [f"- Their name is {settings.user_name}."]
        about += [f"- {f}" for f in memory.recall("name nickname called family team work")]
        self.system = AMBIENT_PROMPT.format(
            assistant=settings.assistant_name,
            user=settings.user_name,
            known=settings.known_languages,
            about="\n".join(about),
        )
        self.triage_system = TRIAGE_PROMPT.format(
            user=settings.user_name, known=settings.known_languages, about="\n".join(about),
        )
        facts = [f"- {mask(f)}" for f in memory.recall("")] or ["- (nothing yet)"]
        self.think_system = THINK_PROMPT.format(
            assistant=settings.assistant_name, user=settings.user_name, facts="\n".join(facts),
        )
        self._thinking: asyncio.Task | None = None
        self.segments: deque[tuple[float, str, str]] = deque(maxlen=MAX_SEGMENTS)
        self.seen = 0            # segments already analysed
        self.whispered: deque[str] = deque(maxlen=8)
        self.checked: deque[str] = deque(maxlen=50)  # claims already fact-checked this session
        self._fact_at = 0.0
        self._plan_at = 0.0
        self.offered: deque[str] = deque(maxlen=30)  # plans already offered as reminders
        self._facts: asyncio.Task | None = None
        self._running: asyncio.Task | None = None
        self._again = False

    def hear(self, text: str, lang: str, unclear: bool = False) -> None:
        """Add a transcribed segment and analyse it (coalescing bursts of speech). unclear: speech
        recognition wasn't sure (TV, distance, a language it barely knows): tagged [xx?]."""
        if len(self.segments) == self.segments.maxlen:
            self.seen = max(0, self.seen - 1)  # the oldest segment is about to drop
        self.segments.append((time.monotonic(), f"{lang or '??'}?" if unclear else lang, mask(text)))
        transcripts.keep(self.memory, text, lang)  # only if you switched room transcripts on (2 days)
        if self._running and not self._running.done():
            self._again = True
        else:
            self._running = asyncio.create_task(self._analyse_loop())

    def think(self) -> None:
        """The user said "let me think": whisper an answer now, skipping the batch wait."""
        if self._thinking and not self._thinking.done():
            return  # already on it
        self._thinking = asyncio.create_task(self._answer_for_user())

    async def _answer_for_user(self) -> None:
        try:
            if self.thrift and self.thrift.month_spent() >= self.thrift.month_budget():
                await self.whisper("budget", "Sorry, this month's AI budget is used up.")
                return
            cutoff = time.monotonic() - WINDOW_SECONDS
            transcript = "\n".join(f"[{lang}] {text}" for t, lang, text in self.segments if t >= cutoff)
            if not transcript:
                return
            started = time.monotonic()
            response = await client.beta.messages.create(
                model=settings.think_model,
                max_tokens=2048,
                system=[{"type": "text", "text": self.think_system, "cache_control": {"type": "ephemeral"}}],
                output_config={"effort": "low", "format": {"type": "json_schema", "schema": THINK_SCHEMA}},
                **fallback_kwargs(settings.think_model),
                messages=[{"role": "user", "content": f"Transcript, oldest first:\n{transcript}"}],
            )
            if hasattr(response, "usage"):
                usage.record(self.memory, response.model, response.usage, purpose="think", response=response, started=started)
            if response.stop_reason in ("refusal", "max_tokens"):
                return
            verdict = json.loads(next((b.text for b in response.content if b.type == "text"), "{}"))
            if verdict.get("found") and verdict.get("answer", "").strip():
                self.whispered.append(verdict["answer"])
                await self.whisper("answer", verdict["answer"].strip())
            else:
                await self.whisper("answer", "I didn't catch a question there.")
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("let-me-think answer failed")

    def recent(self, seconds: float | None = None) -> list[str]:
        """The room's recent conversation, oldest first, with how long ago ("2m ago [es] ...")."""
        now = time.monotonic()
        limit = min(seconds or ROOM_MEMORY_SECONDS, ROOM_MEMORY_SECONDS)
        lines = []
        for t, lang, text in self.segments:
            ago = now - t
            if ago <= limit:
                when = f"{int(ago)}s ago" if ago < 90 else f"{int(ago // 60)}m ago"
                lines.append(f"{when}{f' [{lang}]' if lang else ''} {text}")
        return lines

    def stop(self) -> None:
        if self._thinking:
            self._thinking.cancel()
        if self._running:
            self._running.cancel()
        self.segments.clear()

    async def _analyse_loop(self) -> None:
        while True:
            self._again = False
            await self._wait_for_batch()
            try:
                await self._analyse()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("ambient analysis failed")
            if not self._again:
                return

    async def _wait_for_batch(self) -> None:
        """Let speech gather until the oldest unchecked phrase is a batch old."""
        if settings.listen_batch_seconds <= 0 or self.seen >= len(self.segments):
            return
        oldest_new = self.segments[self.seen][0]
        batch = settings.listen_batch_seconds
        # Speech that looks foreign: the gist matters now, not in 20 seconds.
        known = set(whisper_languages())
        if any(lang and lang.split("-")[0] not in known for _, lang, _ in list(self.segments)[self.seen:]):
            batch = min(batch, FOREIGN_BATCH_SECONDS)
        delay = batch - (time.monotonic() - oldest_new)
        if delay > 0:
            await asyncio.sleep(delay)

    async def _needs_attention(self, earlier: str, new: str) -> bool:
        """Cheap first pass. Any failure errs on the side of a close look."""
        attention, claims, plans = await self._triage(earlier, new)
        if claims:
            self._check_claims(claims)
        if plans:
            await self._offer_reminder(plans)
        return attention

    async def _offer_reminder(self, plans: list[dict]) -> None:
        """A plan heard in the room ("soccer at 10"): offer a reminder; "Hey Buddy, remind me" sets it."""
        if time.monotonic() - self._plan_at < PLAN_GAP_SECONDS:
            return
        for p in plans:
            key = f"{p.get('what', '')} {p.get('when', '')}".strip().lower()
            if not key or key in self.offered:
                continue
            self.offered.append(key)
            self._plan_at = time.monotonic()
            words = (f"Sounds like a plan: {p['what']}, {p['when']}. Want a reminder? Say \"Hey Buddy, remind me\".")
            self.whispered.append(words)
            await self.whisper("plan", words)
            return

    def _check_claims(self, claims: list[str]) -> None:
        fresh = [c.strip() for c in claims if c.strip() and c.strip().lower() not in self.checked][:3]
        if not fresh or (self._facts and not self._facts.done()):
            return
        self.checked.extend(c.lower() for c in fresh)
        self._facts = asyncio.create_task(self._fact_check(fresh))

    async def _fact_check(self, claims: list[str]) -> None:
        """General claims (no names, nothing private) checked by two free models; a whisper only when both say a
        claim is wrong, so a free model's mistake doesn't put words in your mouth. Claude's quick model only when
        no free model can (or a claim still carries personal details)."""
        try:
            checks = await fact_check(self.memory, claims)
            if time.monotonic() - self._fact_at < FACT_GAP_SECONDS:
                return
            for c in checks:
                if c.get("verdict") in ("false", "misleading") and c.get("agreed") and c.get("correct"):
                    words = ("Not quite. " if c["verdict"] == "false" else "Partly true. ") + c["correct"].strip()
                    if c.get("ask"):
                        words += f" You could ask: {c['ask'].strip()}"
                    self._fact_at = time.monotonic()
                    self.whispered.append(words)
                    await self.whisper("correction", words)
                    return  # one at a time
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("listen-in fact check failed")

    async def _triage(self, earlier: str, new: str) -> tuple[bool, list[str], list[dict]]:
        if not settings.listen_triage_model:
            return True, [], []
        try:
            started = time.monotonic()
            response = await client.beta.messages.create(
                model=settings.listen_triage_model,
                max_tokens=1500,  # room for brief thinking plus the answer
                system=[{"type": "text", "text": self.triage_system, "cache_control": {"type": "ephemeral"}}],
                output_config={
                    "effort": "low",
                    "format": {"type": "json_schema", "schema": TRIAGE_SCHEMA},
                },
                **fallback_kwargs(settings.listen_triage_model),
                messages=[{"role": "user", "content": f"Earlier lines:\n{earlier or '(none)'}\n\nNEW lines:\n{new}"}],
            )
            if hasattr(response, "usage"):
                usage.record(self.memory, response.model, response.usage, purpose="listen", response=response, started=started)
            if response.stop_reason in ("refusal", "max_tokens"):
                return True, [], []
            text = next((b.text for b in response.content if b.type == "text"), "")
            got = json.loads(text)
            plans = [p for p in got.get("plans") or [] if isinstance(p, dict) and p.get("what") and p.get("when")]
            return bool(got["attention"]), [str(c) for c in got.get("claims") or [] if str(c).strip()][:3], plans[:2]
        except Exception:
            log.exception("listen-in triage failed; checking closely instead")
            return True, [], []

    async def _analyse(self) -> None:
        cutoff = time.monotonic() - WINDOW_SECONDS
        while self.segments and self.segments[0][0] < cutoff:
            self.segments.popleft()
            self.seen = max(0, self.seen - 1)
        lines = list(self.segments)
        if not lines or self.seen >= len(lines):
            return
        earlier = "\n".join(f"[{lang}] {text}" for _, lang, text in lines[: self.seen])
        new = "\n".join(f"[{lang}] {text}" for _, lang, text in lines[self.seen :])
        self.seen = len(lines)

        if self.thrift:
            allowed, ask = self.thrift.listen_allowed()
            if not allowed:
                if ask:
                    await self.whisper("budget", ask)
                return
        if not await self._needs_attention(earlier, new):
            return
        started = time.monotonic()
        response = await client.beta.messages.create(
            model=settings.listen_model,
            max_tokens=1024,
            system=[{"type": "text", "text": self.system, "cache_control": {"type": "ephemeral"}}],
            output_config={
                "effort": "low",
                "format": {"type": "json_schema", "schema": SCHEMA},
            },
            **fallback_kwargs(settings.listen_model),
            messages=[{
                "role": "user",
                "content": f"Earlier lines:\n{earlier or '(none)'}\n\nNEW lines:\n{new}\n\n"
                f"Already whispered:\n{chr(10).join(self.whispered) or '(nothing)'}\n\n"
                f"Fact-checked separately, so no correction about these:\n{chr(10).join(self.checked) or '(none)'}",
            }],
        )
        if hasattr(response, "usage"):
            usage.record(self.memory, response.model, response.usage, purpose="listen", response=response, started=started)
        if response.stop_reason in ("refusal", "max_tokens"):
            return
        text = next((b.text for b in response.content if b.type == "text"), "")
        verdict = json.loads(text)
        if verdict["whisper"] and verdict["kind"] != "none" and verdict["message"].strip():
            self.whispered.append(verdict["message"])
            await self.whisper(verdict["kind"], verdict["message"].strip())


# fact checks on free models ---------------------------------------------------------------------------------------
def _parse_checks(text: str) -> list[dict]:
    from .benefits import last_json  # noqa: PLC0415
    got = last_json(text or "") or {}
    return [c for c in got.get("checks") or [] if isinstance(c, dict)]


async def fact_check(memory: Memory, claims: list[str]) -> list[dict]:
    """Each claim's verdict. A false or misleading verdict is "agreed" only when a second, independent free model
    says the same (or, with no second one, the first is highly confident). Free models get the general claims only;
    claims that still look personal, or no working free model, go to Claude's quick model instead."""
    from .free_models import FreeModels, PersonalData  # noqa: PLC0415
    prompt = json.dumps({"claims": claims}, ensure_ascii=False)
    free = FreeModels(memory)
    first: list[dict] = []
    used = ""
    if free.enabled():
        try:
            got = await free.run("explain", prompt, system=FACT_PROMPT, purpose="listen:factcheck",
                                 claude_fallback=False)
            first, used = _parse_checks(got["answer"]), got["model"].split("/", 1)[0]
        except PersonalData:
            first = []
        except Exception:
            log.info("free fact check failed", exc_info=True)
    if not first:
        return [{**c, "agreed": c.get("confidence") == "high"} for c in await _claude_checks(memory, prompt)]
    doubtful = [c for c in first if c.get("verdict") in ("false", "misleading")]
    if not doubtful:
        return first
    second = await _second_opinion(free, used, json.dumps({"claims": [c.get("claim", "") for c in doubtful]}))
    for c in first:
        if c not in doubtful:
            continue
        other = next((o for o in second if str(o.get("claim", "")).strip().lower() ==
                      str(c.get("claim", "")).strip().lower()), None)
        if other is not None:
            c["agreed"] = other.get("verdict") in ("false", "misleading")
        else:
            c["agreed"] = c.get("confidence") == "high" and not second
    return first


async def _second_opinion(free, used: str, prompt: str) -> list[dict]:
    """The same claims to a different free provider, for an independent answer."""
    for name in free.order("explain"):
        if name == used:
            continue
        try:
            answer, _ = await asyncio.wait_for(asyncio.to_thread(free._call, name, FACT_PROMPT, prompt, 25, 1500), 27)
            usage.log_call(free.memory, provider=name, model=free._last_model or free.model_of(name),
                           purpose="listen:factcheck", stop="free (second opinion)")
            return _parse_checks(answer)
        except Exception:
            continue
    return []


async def _claude_checks(memory: Memory, prompt: str) -> list[dict]:
    started = time.monotonic()
    model = settings.fast_model or settings.listen_triage_model
    response = await client.beta.messages.create(
        model=model, max_tokens=1500, system=FACT_PROMPT, **fallback_kwargs(model),
        messages=[{"role": "user", "content": prompt}])
    if hasattr(response, "usage"):
        usage.record(memory, response.model, response.usage, purpose="listen", response=response, started=started)
    return _parse_checks(" ".join(b.text for b in response.content if getattr(b, "type", "") == "text"))
