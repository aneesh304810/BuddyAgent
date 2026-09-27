"""Listen-in mode: Buddy hears the people around the user and whispers when it matters.

It whispers for three reasons:
  - translation: someone speaks Hindi, Malayalam, Marathi, Tamil or another
    language, and the user would want the gist in English
  - about_you:   people are talking about the user, mocking them, or planning
    something that concerns them
  - correction:  someone states something technically wrong with confidence;
    Buddy says what is actually correct

Speech is analysed live and kept only in memory, in a short rolling window.
Nothing said by other people is written to disk.
"""

import asyncio
import json
import logging
import time
from collections import deque
from collections.abc import Awaitable, Callable

from .agent import FALLBACK_BETA, client
from .config import settings
from .memory import Memory

log = logging.getLogger(__name__)

WINDOW_SECONDS = 120
MAX_SEGMENTS = 30

AMBIENT_PROMPT = """\
You are {assistant}, {user}'s AI companion, quietly listening through {user}'s \
earbuds to the conversation around them. You receive a live speech-recognition \
transcript. It cannot tell speakers apart, may include {user}'s own words, and \
has recognition errors. Speech may be in English, Hindi, Malayalam, Marathi, \
Tamil or another language, possibly transcribed in Latin script or mixed.

Decide whether to whisper something short into {user}'s ear right now. Whisper \
only when it is genuinely useful; staying quiet is the normal case. Reasons:

- "translation": someone said something meaningful in a language other than \
English. Give the gist in natural English, in one or two sentences. Skip small \
talk that doesn't matter.
- "about_you": people are talking about {user}, making fun of them, insulting \
them, or planning something that affects them. Say plainly what was said and \
the tone, without exaggerating. If it's only a mild joke, say so.
- "correction": someone confidently states something technically wrong \
(engineering, software, science, finance, numbers). Say what is actually \
correct in one or two sentences, so {user} can respond. Don't nitpick opinions, \
jokes, or harmless simplifications.

Rules:
- Only react to the NEW lines; earlier lines are context. Never repeat \
something already in "Already whispered".
- Be honest about uncertainty ("I think they said...") when the transcript is \
garbled. Never invent what people said.
- The whisper is spoken aloud in {user}'s ear, so no markdown; keep it under \
thirty words.

Names and details that refer to {user}:
{about}
"""

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

Whisper = Callable[[str, str], Awaitable[None]]


class AmbientListener:
    def __init__(self, memory: Memory, whisper: Whisper):
        self.whisper = whisper
        about = [f"- Their name is {settings.user_name}."]
        about += [f"- {f}" for f in memory.recall("name nickname called family team work")]
        self.system = AMBIENT_PROMPT.format(
            assistant=settings.assistant_name, user=settings.user_name, about="\n".join(about)
        )
        self.segments: deque[tuple[float, str, str]] = deque(maxlen=MAX_SEGMENTS)
        self.seen = 0            # segments already analysed
        self.whispered: deque[str] = deque(maxlen=8)
        self._running: asyncio.Task | None = None
        self._again = False

    def hear(self, text: str, lang: str) -> None:
        """Add a transcribed segment and analyse it (coalescing bursts of speech)."""
        if len(self.segments) == self.segments.maxlen:
            self.seen = max(0, self.seen - 1)  # the oldest segment is about to drop
        self.segments.append((time.monotonic(), lang, text))
        if self._running and not self._running.done():
            self._again = True
        else:
            self._running = asyncio.create_task(self._analyse_loop())

    def stop(self) -> None:
        if self._running:
            self._running.cancel()
        self.segments.clear()

    async def _analyse_loop(self) -> None:
        while True:
            self._again = False
            try:
                await self._analyse()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("ambient analysis failed")
            if not self._again:
                return

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

        response = await client.beta.messages.create(
            model=settings.model,
            max_tokens=1024,
            system=[{"type": "text", "text": self.system, "cache_control": {"type": "ephemeral"}}],
            output_config={
                "effort": "low",
                "format": {"type": "json_schema", "schema": SCHEMA},
            },
            betas=[FALLBACK_BETA],
            fallbacks="default",
            messages=[{
                "role": "user",
                "content": f"Earlier lines:\n{earlier or '(none)'}\n\nNEW lines:\n{new}\n\n"
                f"Already whispered:\n{chr(10).join(self.whispered) or '(nothing)'}",
            }],
        )
        if response.stop_reason in ("refusal", "max_tokens"):
            return
        text = next((b.text for b in response.content if b.type == "text"), "")
        verdict = json.loads(text)
        if verdict["whisper"] and verdict["kind"] != "none" and verdict["message"].strip():
            self.whispered.append(verdict["message"])
            await self.whisper(verdict["kind"], verdict["message"].strip())
