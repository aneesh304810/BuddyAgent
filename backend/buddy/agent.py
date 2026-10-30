"""The conversation loop: user speech in, Buddy's spoken sentences out.

Replies are streamed sentence by sentence so the phone can start speaking
before Claude has finished writing, which is what makes it feel live.
"""

import asyncio
import contextlib
import json
import logging
import random
import re
import secrets
import time
from collections.abc import Awaitable, Callable

import anthropic

from . import usage
from .agents import AgentRegistry, ApprovalNeeded
from .config import claude_client, settings
from .jobs import JOB_TOOL_NAMES, JOB_TOOLS, JobRunner, run_job_tool
from .connectors import CONNECTOR_TOOL_NAMES, CONNECTOR_TOOLS, ConnectorHub, run_connector_tool
from .bankfeed import BALANCES_TOOL, BankFeed, run_balances_tool
from .free_models import GENERAL_TOOL, UNSURE, VERDICT_TOOL, FreeModels, run_general_tool
from .insights import RATE_TOOL
from .router import HAND_OVER_TOOL, Router, free_kind
from .social import SOCIAL_TOOL_NAMES, Social, run_social_tool, social_tools
from .location import Location
from .taste import TASTE_TOOL_NAMES, TASTE_TOOLS, WHERE_TOOL, Taste, run_taste_tool
from .builder import BUILDER_TOOL_NAMES, BUILDER_TOOLS, AgentBuilder, run_builder_tool
from .deals import WATCH_TOOL_NAMES, WATCH_TOOLS, PriceWatcher, run_watch_tool
from .finops import COST_TOOL, FinOps
from .health import HEALTH_TOOL, run_health_tool
from . import cards, sandbox
from .codejob import CODE_TOOL_NAMES, CODE_TOOLS, run_code_tool
from .makers import MAKER_TOOL_NAMES, MAKER_TOOLS, run_maker_tool
from . import tutor, vocabulary
from .lifelog import LIFELOG_TOOL, LifeLog, run_lifelog_tool
from .mailbrain import MAIL_TOOL_NAMES, MAIL_TOOLS, MailManager, run_mail_tool
from .money import MONEY_TOOL_NAMES, MONEY_TOOLS, Money, run_money_tool
from .organiser import TASK_TOOL_NAMES, TASK_TOOLS, Organiser, run_task_tool
from .thrift import SPENDING_TOOL, Thrift
from .memory import Memory
from .persona import build_system_prompt
from .privacy import mask
from .tools import CLIENT_TOOLS, ORCHESTRATION_TOOLS, ToolError, local_now, run_tool, web_tools

log = logging.getLogger(__name__)

FALLBACK_BETA = "server-side-fallback-2026-07-01"
MAX_TOOL_ROUNDS = 8
SENTENCE_END = re.compile(r"(?<=[.!?])\s+")
FILLER_AFTER_SECONDS = 2.5
# Loaded on every request; everything else is found with tool search when needed.
# Tools that only show or note something: when a response calls nothing else, the turn ends there.
DISPLAY_ONLY = {"free_answer_verdict", "show_cards"}
ALWAYS_LOADED = {"remember_fact", "recall_memory", "set_reminder", "confirm_action", "add_task"}
MAX_HISTORY = 40      # messages; above this, older turns are dropped
KEEP_HISTORY = 24
FILLERS = ("One sec, let me check.", "Give me a moment, looking that up.", "Hang on, checking.",
           "Let me look into that.", "One moment.")

Say = Callable[[str], Awaitable[None]]

client = claude_client()


def fallback_kwargs(model: str) -> dict:
    """On a safety decline, the API retries on Anthropic's recommended model.

    Haiku has no server-side fallback, so it gets none.
    """
    if model.startswith("claude-haiku"):
        return {}
    return {"betas": [FALLBACK_BETA], "fallbacks": "default"}


def _request_kwargs(system: str, model: str | None = None) -> dict:
    model = model or settings.chat_model
    return dict(
        model=model,
        max_tokens=4096,
        system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
        # Also cache the conversation so far: earlier turns are read back at a tenth of the price.
        cache_control={"type": "ephemeral"},
        output_config={"effort": settings.effort},
        **fallback_kwargs(model),
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
        mail: MailManager | None = None,
        watcher: PriceWatcher | None = None,
        builder: AgentBuilder | None = None,
    ):
        self.memory = memory
        self.registry = registry
        self.jobs = jobs
        self.thrift = thrift
        self.hub = hub
        self.finops = finops
        self.mail = mail
        self.watcher = watcher
        self.builder = builder
        self.organiser = Organiser(memory)
        self.money = Money(memory)
        self.lifelog = LifeLog(memory)
        self.bank = BankFeed(memory)
        self.social = Social()
        self.taste = Taste(memory)
        self.location = Location(memory)
        self.router = Router()
        self.free = FreeModels(memory)
        self.model = settings.chat_model  # this turn's model, picked by the router
        self.messages: list[dict] = []
        self.turn = 0  # counts user messages; approvals need a newer one
        self.turn_key = ""  # tags this turn's model calls, for your thumbs rating
        self.previous_turn = ""
        self.cards = cards.CardBox()  # what this reply can show in the chat
        self.via = "user"
        # Frozen for the whole session so the cached prompt prefix stays valid.
        self.system = build_system_prompt(memory.recall(""), memory.open_goals())

    async def reply(self, user_text: str, say: Say, via: str = "user") -> None:
        """Answer one user utterance, calling `say` with each sentence to speak.

        via "room": the words came through Listen-in ("Hey Buddy, ..."), where
        anyone nearby could have said them, so they can't approve anything.
        If the task running this is cancelled (the user interrupted), the history
        keeps what was already spoken so Buddy knows where it was cut off.
        """
        self.turn += 1
        self.via = via
        self.previous_turn, self.turn_key = self.turn_key, secrets.token_hex(6)
        usage.current_turn.set(self.turn_key)
        self.cards = cards.CardBox()
        cards.current.set(self.cards)
        if self.thrift:
            # Money questions are answered in code, at no cost: Claude never approves spending.
            said, resume = await self.thrift.resolve(user_text, trusted=via != "room")
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
        self._trim_history()
        self.turn_question, self._step = mask(user_text), 0
        tutor.log_utterance(self.memory, user_text, via)  # for the English tutor's review
        if await self._free_turn(user_text, say, via):
            return
        self.model = self.router.pick(user_text)
        usage.count(self.memory, "router:quick" if self.model == settings.fast_model else "router:main")
        # Sensitive numbers never leave the server.
        self._add_user_text(f"({stamp}) {mask(user_text)}" + (tutor.LIVE_NOTE if tutor.live(self.memory) else ""))
        spoken: list[str] = []
        said_anything = asyncio.Event()
        # Performance of this turn, for Insights and the daily health check.
        perf = {"started": time.monotonic(), "first": None, "sentences": 0, "rounds": 0, "tools": [],
                "filler": False, "outcome": "ok", "first_model": self.model}

        async def speak(sentence: str) -> None:
            said_anything.set()
            if perf["first"] is None:
                perf["first"] = time.monotonic() - perf["started"]
            perf["sentences"] += 1
            spoken.append(sentence)
            await say(sentence)

        async def filler() -> None:
            # Searching or checking places can take a while: don't leave the user in silence.
            await asyncio.sleep(FILLER_AFTER_SECONDS)
            if not said_anything.is_set():
                perf["filler"] = True
                await say(random.choice(FILLERS))

        waiting = asyncio.create_task(filler())
        try:
            for _ in range(MAX_TOOL_ROUNDS):
                response = await self._stream_step(speak)
                perf["rounds"] += 1
                self.cards.seen_web_results(response.content)
                if response.stop_reason == "refusal":
                    perf["outcome"] = "refusal"
                    await speak("Hmm, that's one I can't help with. Anything else?")
                    self.messages.append({"role": "assistant", "content": " ".join(spoken)})
                    return
                self.messages.append({"role": "assistant", "content": response.content})
                spoken.clear()
                if response.stop_reason == "pause_turn":
                    continue  # server-side web search wants another round
                tool_uses = [b for b in response.content if b.type == "tool_use"]
                perf["tools"] += [b.name for b in tool_uses]
                if response.stop_reason != "tool_use" or not tool_uses:
                    await self._never_silent(perf, speak, response)
                    return
                if all(b.name in DISPLAY_ONLY for b in tool_uses):
                    # Only cards to show or a review note: do it and finish, no extra round (or wait).
                    self.messages.append({"role": "user", "content": [await self._run(b) for b in tool_uses]})
                    await self._never_silent(perf, speak, response)
                    return
                if any(b.name == "hand_over" for b in tool_uses):
                    # The quick model passes a bigger question to the main one, same turn.
                    self.model = self.router.handed_over()
                    usage.count(self.memory, "router:handed_over")
                results = [await self._run(b) for b in tool_uses]
                self.messages.append({"role": "user", "content": results})
            perf["outcome"] = "too_many_steps"
            await speak("Sorry, I got a bit tangled up there. Can you say that again?")
        except BaseException as e:
            perf["outcome"] = "interrupted" if isinstance(e, asyncio.CancelledError) else f"error: {type(e).__name__}"
            # Interrupted or failed mid-step: record what the user actually heard.
            if spoken:
                self.messages.append(
                    {"role": "assistant", "content": " ".join(spoken) + " —"}
                )
            raise
        finally:
            waiting.cancel()
            with contextlib.suppress(Exception):  # metrics never break a reply
                usage.log_turn(self.memory, turn=self.turn_key, via=via, first_model=perf["first_model"],
                               final_model=self.model, first_word=perf["first"],
                               total=time.monotonic() - perf["started"], rounds=perf["rounds"],
                               tools=perf["tools"], sentences=perf["sentences"], filler=perf["filler"],
                               outcome=perf["outcome"])

    async def _never_silent(self, perf: dict, speak: Say, response) -> None:
        """A reply that ended without a word (only a filler, or nothing): say so, and log why."""
        if perf["sentences"]:
            return
        kinds = [getattr(b, "type", "?") + (f":{b.name}" if getattr(b, "name", None) else "") for b in response.content]
        log.warning("reply ended without words: stop=%s content=%s tools=%s", response.stop_reason, kinds, perf["tools"])
        perf["outcome"] = "silent"
        await speak("Sorry, I didn't get an answer together on that one. Can you ask me again, maybe in other words?")

    async def _stream_step(self, speak: Say):
        buffer = ""
        started = time.monotonic()
        async with client.beta.messages.stream(
            **_request_kwargs(self.system, self.model), tools=self.tools(), messages=self.messages
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
        self._step = getattr(self, "_step", 0) + 1
        question = getattr(self, "turn_question", "") or self._last_user_text()
        usage.record(self.memory, response.model, response.usage, response=response, started=started,
                     request=question if self._step == 1 else f"(step {self._step}, after tools) {question}",
                     label="chat")
        if buffer.strip() and response.stop_reason != "refusal":
            await speak(buffer.strip())
        return response

    def _last_user_text(self) -> str:
        """What the user said this turn, or the tool results being answered."""
        content = self.messages[-1]["content"] if self.messages else ""
        if isinstance(content, str):
            return content
        parts = [b.get("text", "") if b.get("type") == "text" else f"[{b.get('type', '')}]"
                 for b in content if isinstance(b, dict)]
        return " ".join(parts)

    def _add_user_text(self, text: str) -> None:
        """A new user message, or added to the last one if a turn ended on tool results."""
        last = self.messages[-1] if self.messages else None
        if last and last["role"] == "user" and isinstance(last["content"], list):
            last["content"] = last["content"] + [{"type": "text", "text": text}]
        else:
            self.messages.append({"role": "user", "content": text})

    def _trim_history(self) -> None:
        """Long talks: keep the latest turns. Done in one jump, so the cache rebuilds only rarely."""
        if len(self.messages) <= MAX_HISTORY:
            return
        keep = self.messages[-KEEP_HISTORY:]
        # Start at a plain user message (never a tool result, whose tool call would be cut off).
        while keep and not (keep[0]["role"] == "user" and isinstance(keep[0]["content"], str)):
            keep = keep[1:]
        self.messages = keep

    def _note_exchange(self, user_text: str, answer: str) -> None:
        """Keep an exchange handled without Claude in the history, for context."""
        stamp = local_now().strftime("%A %d %B %Y, %I:%M %p")
        self._add_user_text(f"({stamp}) {mask(user_text)}")
        self.messages.append({"role": "assistant", "content": answer})

    async def _free_turn(self, user_text: str, say: Say, via: str) -> bool:
        """Simple, general, timeless questions ("what is a black hole", "how do you say thanks in
        Portuguese") go straight to a free model: no Claude call. True if it answered; anything
        personal, or a model that isn't sure, falls through to Claude."""
        kind = free_kind(user_text) if settings.free_direct and self.free.enabled() else ""
        if not kind:
            return False
        started = time.monotonic()
        try:
            result = await self.free.direct(kind, mask(user_text))
        except ToolError:  # personal details, not sure, or every free model busy
            return False
        except Exception:
            log.exception("free turn failed")
            return False
        answer = result["answer"].replace(UNSURE, "").strip()
        first = None
        sentences = [x.strip() for x in SENTENCE_END.split(answer) if x.strip()]
        for sentence in sentences:
            await say(sentence)
            first = first if first is not None else time.monotonic() - started
        self._note_exchange(user_text, answer)
        usage.count(self.memory, "router:free")
        usage.log_turn(self.memory, turn=self.turn_key, via=via, first_model=result["model"],
                       final_model=result["model"], first_word=first, total=time.monotonic() - started, rounds=1,
                       tools=[], sentences=len(sentences), filler=False, outcome="ok")
        return True

    def tools(self) -> list[dict]:
        """Every tool, but only a few load up front: Claude finds the rest with tool search
        when a question needs them, so each request carries a short prefix, not ~60 tools."""
        tools = [{"type": "tool_search_tool_bm25_20251119", "name": "tool_search_tool_bm25"}]
        everything = self._all_tools()
        if self.model == settings.fast_model and self.model != settings.chat_model:
            everything = [HAND_OVER_TOOL] + everything
        for tool in everything:
            if tool.get("type", "custom") != "custom" or tool["name"] in ALWAYS_LOADED | {"hand_over", "general_task"} | DISPLAY_ONLY:
                tools.append(tool)  # server tools (web search) and the everyday ones stay loaded
            else:
                tools.append({**tool, "defer_loading": True})
        return tools

    def _all_tools(self) -> list[dict]:
        agent_tools = self.registry.tool_definitions() if self.registry else []
        orchestration = ORCHESTRATION_TOOLS if self.registry else []
        job_tools = JOB_TOOLS if self.jobs else []
        spending = [SPENDING_TOOL] if self.thrift else []
        spending += [COST_TOOL] if self.finops else []
        spending += MAIL_TOOLS if self.mail else []
        spending += TASK_TOOLS + MONEY_TOOLS + [BALANCES_TOOL, LIFELOG_TOOL]
        spending += WATCH_TOOLS if self.watcher else []
        spending += BUILDER_TOOLS if self.builder and self.jobs else []
        spending += CODE_TOOLS if self.jobs and sandbox.available() else []
        spending += CONNECTOR_TOOLS if self.hub else []
        spending += social_tools(self.social) + TASTE_TOOLS + [WHERE_TOOL]
        spending += [GENERAL_TOOL, VERDICT_TOOL] if self.free.enabled() else []
        spending += [RATE_TOOL, HEALTH_TOOL, cards.SHOW_TOOL, tutor.TUTOR_TOOL, vocabulary.LEARN_TOOL] + MAKER_TOOLS
        return (
            CLIENT_TOOLS + orchestration + job_tools + spending + [_expert_tool()]
            + agent_tools + web_tools()
        )

    async def _run(self, block) -> dict:
        args = block.input if isinstance(block.input, dict) else json.loads(block.input)
        started = time.monotonic()
        try:
            result, is_error = await self._dispatch(block.name, args), False
        except ApprovalNeeded as e:
            result, is_error = str(e), False
        except (ToolError, KeyError, TypeError, ValueError) as e:
            result, is_error = str(e) or type(e).__name__, True
        log.info("tool %s -> %s", block.name, "error" if is_error else "ok")
        with contextlib.suppress(Exception):  # for Insights: which tools and agents, how fast, how often failing
            usage.log_tool(self.memory, block.name, time.monotonic() - started, not is_error,
                           str(result)[:160] if is_error else "")
        return {
            "type": "tool_result",
            "tool_use_id": block.id,
            "content": result,
            "is_error": is_error,
        }

    async def _dispatch(self, name: str, args: dict) -> str:
        if name == "hand_over":
            return "Handed over to the main model: answer the user's question now."
        if name == "general_task":
            return await run_general_tool(self.free, args)
        if name == "free_answer_verdict":
            usage.set_verdict(self.memory, args["call_id"], args["verdict"], args.get("note", ""))
            return "Noted."
        if self.jobs and name in CODE_TOOL_NAMES:
            return await run_code_tool(self.jobs, name, args, self.thrift)
        if name == "english_tutor":
            return tutor.run_tutor_tool(self.memory, self.jobs, args)
        if name == "learn_word":
            vocabulary.learn(self.memory, str(args.get("word", "")))
            return f"Saved: I'll recognise \"{args.get('word')}\" better from now on."
        if name in MAKER_TOOL_NAMES:
            return await run_maker_tool(self.memory, name, args)
        if name == "show_cards":
            return self.cards.pick(args.get("cards") or [])
        if name == "health_report":
            return run_health_tool(self.memory)
        if name == "rate_answer":
            if not self.previous_turn:
                return "There's no earlier answer to rate yet."
            usage.rate_turn(self.memory, self.previous_turn, bool(args["good"]))
            return "Noted, thanks: it's in your Insights."
        if self.jobs and name in JOB_TOOL_NAMES:
            return run_job_tool(self.jobs, name, args, self.thrift)
        if self.hub and name in CONNECTOR_TOOL_NAMES:
            return await run_connector_tool(self.hub, name, args)
        if self.builder and self.jobs and name in BUILDER_TOOL_NAMES:
            return await run_builder_tool(self.builder, self.jobs, name, args, self.thrift)
        if self.watcher and name in WATCH_TOOL_NAMES:
            return await run_watch_tool(self.watcher, name, args, self.thrift)
        if name in TASTE_TOOL_NAMES:
            return await run_taste_tool(self.taste, self.social, name, args, self.location)
        if name == "where_am_i":
            here = self.location.current()
            return json.dumps(here) if here else ("Not shared right now. The user can turn on Share my location "
                                                  "in the Buddy app's settings, or just tell you.")
        if name in SOCIAL_TOOL_NAMES:
            return await run_social_tool(self.social, name, args)
        if name == "account_balances":
            return await run_balances_tool(self.bank, args)
        if name == "life_log":
            return run_lifelog_tool(self.lifelog, args)
        if name in MONEY_TOOL_NAMES:
            return run_money_tool(self.money, name, args)
        if name in TASK_TOOL_NAMES:
            return run_task_tool(self.organiser, name, args)
        if self.mail and name in MAIL_TOOL_NAMES:
            return await run_mail_tool(self.mail, name, args, self.thrift)
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
                if self.via == "room":
                    return ("Not confirmed: that yes came through Listen-in, where someone else in the room "
                            "could have said it. Ask the user to tap Approve or answer in Talk mode.")
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
        """Record something Buddy said unprompted (a whisper, a nudge), so a follow-up
        like "why?" or "tell me more" has it as context."""
        if self.messages and self.messages[-1]["role"] == "user":
            return  # a reply is on its way; don't split the user's turn
        self.messages.append({"role": "user", "content": "(Buddy spoke up on its own; no question asked.)"})
        self.messages.append({"role": "assistant", "content": text})


async def compose(system: str, instruction: str, memory: Memory | None = None, label: str = "briefing") -> str:
    """One-shot generation with no tools, for briefings and nudges."""
    started = time.monotonic()
    response = await client.beta.messages.create(
        **_request_kwargs(system),
        messages=[{"role": "user", "content": instruction}],
    )
    if memory is not None:
        usage.record(memory, response.model, response.usage, response=response, started=started,
                     request=instruction, label=label)
    if response.stop_reason == "refusal":
        return ""
    return " ".join(b.text for b in response.content if b.type == "text").strip()
