"""The conversation loop: user speech in, Buddy's spoken sentences out.

Replies are streamed sentence by sentence so the phone can start speaking
before Claude has finished writing, which is what makes it feel live.
"""

import json
import logging
import re
from collections.abc import Awaitable, Callable

import anthropic

from . import usage
from .agents import AgentRegistry, ApprovalNeeded
from .config import settings
from .jobs import JOB_TOOL_NAMES, JOB_TOOLS, JobRunner, run_job_tool
from .connectors import CONNECTOR_TOOL_NAMES, CONNECTOR_TOOLS, ConnectorHub, run_connector_tool
from .finops import COST_TOOL, FinOps
from .thrift import SPENDING_TOOL, Thrift
from .memory import Memory
from .persona import build_system_prompt
from .privacy import mask
from .tools import CLIENT_TOOLS, ORCHESTRATION_TOOLS, WEB_SEARCH, ToolError, local_now, run_tool

log = logging.getLogger(__name__)

FALLBACK_BETA = "server-side-fallback-2026-07-01"
MAX_TOOL_ROUNDS = 8
SENTENCE_END = re.compile(r"(?<=[.!?])\s+")

Say = Callable[[str], Awaitable[None]]

client = anthropic.AsyncAnthropic()


def fallback_kwargs(model: str) -> dict:
    """On a safety decline, the API retries on Anthropic's recommended model.

    Haiku has no server-side fallback, so it gets none.
    """
    if model.startswith("claude-haiku"):
        return {}
    return {"betas": [FALLBACK_BETA], "fallbacks": "default"}


def _request_kwargs(system: str) -> dict:
    return dict(
        model=settings.chat_model,
        max_tokens=4096,
        system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
        output_config={"effort": settings.effort},
        **fallback_kwargs(settings.chat_model),
    )


def _expert_tool() -> dict:
    from .expert import EXPERT_TOOL
    return EXPERT_TOOL


class Conversation:
    """One live conversation with the user (one per connected phone)."""

    def __init__(
        self,
        memory: Memory,
        registry: AgentRegistry | None = None,
        jobs: JobRunner | None = None,
        thrift: Thrift | None = None,
        hub: ConnectorHub | None = None,
        finops: FinOps | None = None,
    ):
        self.memory = memory
        self.registry = registry
        self.jobs = jobs
        self.thrift = thrift
        self.hub = hub
        self.finops = finops
        self.messages: list[dict] = []
        self.turn = 0  # counts user messages; approvals need a newer one
        # Frozen for the whole session so the cached prompt prefix stays valid.
        self.system = build_system_prompt(memory.recall(""), memory.open_goals())

    async def reply(self, user_text: str, say: Say) -> None:
        """Answer one user utterance, calling `say` with each sentence to speak.

        If the task running this is cancelled (the user interrupted), the history
        keeps what was already spoken so Buddy knows where it was cut off.
        """
        self.turn += 1
        if self.thrift:
            # Money questions are answered in code, at no cost: Claude never approves spending.
            said, resume = await self.thrift.resolve(user_text)
            if said:
                await say(said)
                self._note_exchange(user_text, said)
                if not resume:
                    return
                user_text = resume  # approved: now answer what they asked before
            question = self.thrift.gate_chat(user_text)
            if question:
                await say(question)
                self._note_exchange(user_text, question)
                return
        elif usage.month_to_date(self.memory) >= settings.monthly_budget_usd:
            await say("Heads up, we've hit this month's AI budget, so I'm pausing until you raise it.")
            return

        stamp = local_now().strftime("%A %d %B %Y, %I:%M %p")
        # Sensitive numbers never leave the server.
        self.messages.append({"role": "user", "content": f"({stamp}) {mask(user_text)}"})
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
                results = [await self._run(b) for b in tool_uses]
                self.messages.append({"role": "user", "content": results})
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
            **_request_kwargs(self.system), tools=self.tools(), messages=self.messages
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
        usage.record(self.memory, response.model, response.usage)
        if buffer.strip() and response.stop_reason != "refusal":
            await speak(buffer.strip())
        return response

    def _note_exchange(self, user_text: str, answer: str) -> None:
        """Keep an exchange handled without Claude in the history, for context."""
        stamp = local_now().strftime("%A %d %B %Y, %I:%M %p")
        self.messages.append({"role": "user", "content": f"({stamp}) {mask(user_text)}"})
        self.messages.append({"role": "assistant", "content": answer})

    def tools(self) -> list[dict]:
        agent_tools = self.registry.tool_definitions() if self.registry else []
        orchestration = ORCHESTRATION_TOOLS if self.registry else []
        job_tools = JOB_TOOLS if self.jobs else []
        spending = [SPENDING_TOOL] if self.thrift else []
        spending += [COST_TOOL] if self.finops else []
        spending += CONNECTOR_TOOLS if self.hub else []
        return (
            CLIENT_TOOLS + orchestration + job_tools + spending + [_expert_tool()]
            + agent_tools + [WEB_SEARCH]
        )

    async def _run(self, block) -> dict:
        args = block.input if isinstance(block.input, dict) else json.loads(block.input)
        try:
            result, is_error = await self._dispatch(block.name, args), False
        except ApprovalNeeded as e:
            result, is_error = str(e), False
        except (ToolError, KeyError, TypeError, ValueError) as e:
            result, is_error = str(e) or type(e).__name__, True
        log.info("tool %s -> %s", block.name, "error" if is_error else "ok")
        return {
            "type": "tool_result",
            "tool_use_id": block.id,
            "content": result,
            "is_error": is_error,
        }

    async def _dispatch(self, name: str, args: dict) -> str:
        if self.jobs and name in JOB_TOOL_NAMES:
            return run_job_tool(self.jobs, name, args, self.thrift)
        if self.hub and name in CONNECTOR_TOOL_NAMES:
            return await run_connector_tool(self.hub, name, args)
        if self.finops and name == "cost_report":
            return json.dumps(await self.finops.report(bool(args.get("fresh"))))
        if self.thrift and name == "spending_status":
            return json.dumps(self.thrift.status())
        if name == "ask_expert":
            from . import expert  # imported here: expert uses this module's helpers
            if self.thrift and expert.ESTIMATE_USD > settings.approve_over_usd:
                async def run() -> str:
                    return await expert.ask_expert(self.memory, args)
                return self.thrift.ask_action(
                    f"This needs a closer look from the expert model, about "
                    f"{round(expert.ESTIMATE_USD * 100)} cents. Go ahead?", run,
                )
            return await expert.ask_expert(self.memory, args)
        if self.registry:
            if name == "confirm_action":
                return await self.registry.confirm(args["id"], self.turn)
            if name == "list_agents":
                return json.dumps(
                    [{k: a[k] for k in ("name", "description", "enabled", "tools")}
                     for a in self.registry.status()]
                )
            if name == "pause_agent":
                self.registry.set_enabled(args["name"], False)
                return f"The {args['name']} agent is now off."
            if name in self.registry.tools:
                return await self.registry.call(name, args, self.turn)
        return run_tool(self.memory, name, args)

    def note_proactive(self, text: str) -> None:
        """Record something Buddy said unprompted, so the user can reply to it."""
        if self.messages and self.messages[-1]["role"] == "assistant":
            self.messages.append({"role": "assistant", "content": text})


async def compose(system: str, instruction: str, memory: Memory | None = None) -> str:
    """One-shot generation with no tools, for briefings and nudges."""
    response = await client.beta.messages.create(
        **_request_kwargs(system),
        messages=[{"role": "user", "content": instruction}],
    )
    if memory is not None:
        usage.record(memory, response.model, response.usage)
    if response.stop_reason == "refusal":
        return ""
    return " ".join(b.text for b in response.content if b.type == "text").strip()
