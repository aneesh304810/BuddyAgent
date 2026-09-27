"""The conversation loop: user speech in, Buddy's spoken sentences out.

Replies are streamed sentence by sentence so the phone can start speaking
before Claude has finished writing, which is what makes it feel live.
"""

import json
import logging
import re
from collections.abc import Awaitable, Callable

import anthropic

from .config import settings
from .memory import Memory
from .persona import build_system_prompt
from .tools import TOOLS, ToolError, local_now, run_tool

log = logging.getLogger(__name__)

FALLBACK_BETA = "server-side-fallback-2026-07-01"
MAX_TOOL_ROUNDS = 8
SENTENCE_END = re.compile(r"(?<=[.!?])\s+")

Say = Callable[[str], Awaitable[None]]

client = anthropic.AsyncAnthropic()


def _request_kwargs(system: str) -> dict:
    return dict(
        model=settings.model,
        max_tokens=4096,
        system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
        output_config={"effort": settings.effort},
        # On a safety decline, the API retries on Anthropic's recommended model.
        betas=[FALLBACK_BETA],
        fallbacks="default",
    )


class Conversation:
    """One live conversation with the user (one per connected phone)."""

    def __init__(self, memory: Memory):
        self.memory = memory
        self.messages: list[dict] = []
        # Frozen for the whole session so the cached prompt prefix stays valid.
        self.system = build_system_prompt(memory.recall(""), memory.open_goals())

    async def reply(self, user_text: str, say: Say) -> None:
        """Answer one user utterance, calling `say` with each sentence to speak.

        If the task running this is cancelled (the user interrupted), the history
        keeps what was already spoken so Buddy knows where it was cut off.
        """
        stamp = local_now().strftime("%A %d %B %Y, %I:%M %p")
        self.messages.append({"role": "user", "content": f"({stamp}) {user_text}"})
        spoken: list[str] = []

        async def speak(sentence: str) -> None:
            spoken.append(sentence)
            await say(sentence)

        try:
            for _ in range(MAX_TOOL_ROUNDS):
                response = await self._stream_step(speak)
                if response.stop_reason == "refusal":
                    await speak("Hmm, that's one I can't help with. Anything else?")
                    self.messages.append({"role": "assistant", "content": " ".join(spoken)})
                    return
                self.messages.append({"role": "assistant", "content": response.content})
                spoken.clear()
                if response.stop_reason == "pause_turn":
                    continue  # server-side web search wants another round
                tool_uses = [b for b in response.content if b.type == "tool_use"]
                if response.stop_reason != "tool_use" or not tool_uses:
                    return
                self.messages.append(
                    {"role": "user", "content": [self._run(b) for b in tool_uses]}
                )
            await speak("Sorry, I got a bit tangled up there. Can you say that again?")
        except BaseException:
            # Interrupted or failed mid-step: record what the user actually heard.
            if spoken:
                self.messages.append(
                    {"role": "assistant", "content": " ".join(spoken) + " —"}
                )
            raise

    async def _stream_step(self, speak: Say):
        buffer = ""
        async with client.beta.messages.stream(
            **_request_kwargs(self.system), tools=TOOLS, messages=self.messages
        ) as stream:
            async for event in stream:
                if event.type != "text":
                    continue
                buffer += event.text
                *complete, buffer = SENTENCE_END.split(buffer)
                for sentence in complete:
                    if sentence.strip():
                        await speak(sentence.strip())
            response = await stream.get_final_message()
        if buffer.strip() and response.stop_reason != "refusal":
            await speak(buffer.strip())
        return response

    def _run(self, block) -> dict:
        args = block.input if isinstance(block.input, dict) else json.loads(block.input)
        try:
            result, is_error = run_tool(self.memory, block.name, args), False
        except (ToolError, KeyError, TypeError, ValueError) as e:
            result, is_error = str(e) or type(e).__name__, True
        log.info("tool %s(%s) -> %s", block.name, args, result)
        return {
            "type": "tool_result",
            "tool_use_id": block.id,
            "content": result,
            "is_error": is_error,
        }

    def note_proactive(self, text: str) -> None:
        """Record something Buddy said unprompted, so the user can reply to it."""
        if self.messages and self.messages[-1]["role"] == "assistant":
            self.messages.append({"role": "assistant", "content": text})


async def compose(system: str, instruction: str) -> str:
    """One-shot generation with no tools, for briefings and nudges."""
    response = await client.beta.messages.create(
        **_request_kwargs(system),
        messages=[{"role": "user", "content": instruction}],
    )
    if response.stop_reason == "refusal":
        return ""
    return " ".join(b.text for b in response.content if b.type == "text").strip()
