"""Buddy speaking up on its own: due reminders and the morning briefing."""

import asyncio
import logging
from collections.abc import Awaitable, Callable

from .agent import compose
from .config import settings
from .memory import Memory
from .persona import build_system_prompt
from .thrift import Thrift
from .tools import format_local, local_now

log = logging.getLogger(__name__)

CHECK_EVERY_SECONDS = 15

# Delivers a nudge to connected phones; returns False when none are listening.
Deliver = Callable[[str, str], Awaitable[bool]]


async def reminder_nudge(memory: Memory, reminder: dict) -> str:
    system = build_system_prompt(memory.recall(""), memory.open_goals())
    text = await compose(
        system,
        "A reminder the user set has just come due. Tell them in one short, natural "
        f"spoken sentence, as their friend would. Reminder: {reminder['text']}",
        memory,
    )
    return text or f"Hey, reminder: {reminder['text']}."


async def morning_briefing(memory: Memory) -> str:
    system = build_system_prompt(memory.recall(""), memory.open_goals())
    reminders = [
        f"{r['text']} at {format_local(r['due_at'])}" for r in memory.upcoming_reminders()
    ]
    return await compose(
        system,
        f"It's {local_now().strftime('%A %d %B, %I:%M %p')}. Give the user a short, upbeat "
        "morning briefing, about four spoken sentences: greet them, mention what's "
        "coming up today, nudge them on one of their goals, and end with yesterday's AI "
        "cost in a few words (say if we're running ahead of budget). "
        f"Upcoming reminders: {'; '.join(reminders) or 'none'}. "
        f"Spending: {Thrift(memory).summary()}",
        memory,
    )


def briefing_due(memory: Memory) -> bool:
    if not settings.morning_brief_at:
        return False
    now = local_now()
    hour, minute = map(int, settings.morning_brief_at.split(":"))
    if (now.hour, now.minute) < (hour, minute):
        return False
    return memory.get("last_briefing") != now.date().isoformat()


async def run(memory: Memory, deliver: Deliver) -> None:
    """Loop forever, pushing due nudges. Undelivered ones wait for a connection."""
    while True:
        try:
            for reminder in memory.due_reminders():
                if await deliver("reminder", await reminder_nudge(memory, reminder)):
                    memory.mark_delivered(reminder["id"])
            if briefing_due(memory):
                text = await morning_briefing(memory)
                if text and await deliver("briefing", text):
                    memory.set("last_briefing", local_now().date().isoformat())
        except Exception:
            log.exception("proactive loop error")
        await asyncio.sleep(CHECK_EVERY_SECONDS)
