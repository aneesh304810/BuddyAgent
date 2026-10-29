"""Step 3 of the escalation ladder: ask the expert.

Everyday chat runs on a quick, cheap model. When it judges its own answer
wouldn't be good enough (high stakes, hard reasoning, unsure, or the user
pushes back), it hands the question to a stronger model thinking harder,
with web search and page reading. The expert's answer comes back for Buddy
to say in its own voice. Each call is counted in the daily allowance, has a
daily limit, and asks first if it would ever cost more than the approval
threshold.
"""

import logging

import anthropic

from . import usage
from .agent import fallback_kwargs
from .config import claude_client, settings
from .memory import Memory
from .privacy import mask
from .tools import ToolError, local_now

log = logging.getLogger(__name__)

client = claude_client()

ESTIMATE_USD = 0.15  # a typical expert answer with a couple of searches
MAX_STEPS = 4        # resumes of the server-side search loop

EXPERT_TOOL = {
    "name": "ask_expert",
    "description": (
        "Hand a question to a stronger model that thinks harder and can search and read "
        "web pages (about 5 to 25 cents, a few seconds). Use it when your own answer would "
        "not be good enough: high stakes (money decisions, health, legal, tax, travel "
        "bookings, contracts), multi-step reasoning or calculations, conflicting or unclear "
        "information, you're not confident, or the user says your answer was wrong or not "
        "enough. Not for small talk or simple facts. Put everything the expert needs in "
        "`context`: it can't see the conversation or memory."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "question": {"type": "string"},
            "context": {"type": "string", "description": "Relevant facts about the user and the conversation"},
            "why": {"type": "string", "description": "Why your own answer isn't enough"},
        },
        "required": ["question", "context", "why"],
        "additionalProperties": False,
    },
}

SYSTEM = """\
You are the expert {assistant} consults when a question needs more care than a \
quick answer. {assistant} is {user}'s personal voice assistant and will say your \
answer aloud in its own words. Today is {today}.

- Get it right. Think it through; do calculations step by step; check current \
facts with web search and read the pages that matter.
- Answer in a few short paragraphs of plain text, no Markdown tables: the answer \
first, then the key numbers or reasons, then any important caveat.
- End with one line: "Confidence: high", "Confidence: medium" or "Confidence: low", \
and if low, what would settle it (for example a deep research report, or a \
professional for medical, legal or tax decisions).
- Never invent facts, prices or sources."""


def _today_key() -> str:
    return f"expert:count:{local_now().date().isoformat()}"


async def ask_expert(memory: Memory, args: dict) -> str:
    count = int(memory.get(_today_key()) or 0)
    if count >= settings.expert_daily_limit:
        raise ToolError(
            f"Today's limit of {settings.expert_daily_limit} expert questions is reached "
            "(BUDDY_EXPERT_DAILY_LIMIT). Answer as well as you can, and say so."
        )
    memory.set(_today_key(), str(count + 1))
    question = mask(str(args.get("question", "")).strip())
    if not question:
        raise ToolError("The question is empty.")
    content = f"Question: {question}\n\nContext: {mask(str(args.get('context', '')))}"
    messages: list[dict] = [{"role": "user", "content": content}]
    system = SYSTEM.format(
        assistant=settings.assistant_name, user=settings.user_name,
        today=local_now().strftime("%A %d %B %Y"),
    )
    tools = [
        {"type": "web_search_20260209", "name": "web_search", "max_uses": 4},
        {"type": "web_fetch_20260209", "name": "web_fetch", "max_uses": 4},
    ]
    cost = 0.0
    response = None
    for _ in range(MAX_STEPS):
        async with client.beta.messages.stream(
            model=settings.expert_model,
            max_tokens=16_000,
            system=system,
            tools=tools,
            messages=messages,
            output_config={"effort": settings.expert_effort},
            **fallback_kwargs(settings.expert_model),
        ) as stream:
            response = await stream.get_final_message()
        cost += usage.record(memory, response.model, response.usage, purpose="expert")
        if response.stop_reason == "refusal":
            raise ToolError("The expert declined this question.")
        messages.append({"role": "assistant", "content": response.content})
        if response.stop_reason != "pause_turn":
            break
    answer = "\n".join(b.text for b in response.content if b.type == "text").strip()
    if not answer:
        raise ToolError("The expert couldn't finish an answer.")
    log.info("expert answer cost $%.3f", cost)
    return f"{answer}\n\n(Expert answer, cost ${cost:.2f}.)"
