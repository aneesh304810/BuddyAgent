"""Buddy speaking up on its own: due reminders, the morning briefing and the evening wrap-up."""

import asyncio
import logging
from datetime import datetime
from collections.abc import Awaitable, Callable

from .agent import compose
from .config import settings
from .memory import Memory
from .persona import build_system_prompt
from .connectors import agenda
from .mailbrain import MailManager
from .organiser import Organiser
from .thrift import Thrift
from .tools import format_local, local_now, user_tz

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
        "coming up today (calendar, tasks due, any urgent email), nudge them on one of their goals, and end with yesterday's AI "
        "cost in a few words (say if we're running ahead of budget). "
        f"Upcoming reminders: {'; '.join(reminders) or 'none'}. "
        f"Today's calendar: {'; '.join(e['when'] + ' ' + e['title'] for e in agenda(memory, 1)) or 'nothing or not connected'}. "
        f"Tasks: {Organiser(memory).morning_line()}. "
        f"Email: {MailManager(memory, None).briefing_line()}. "
        f"Spending: {Thrift(memory).summary()}",
        memory,
    )


async def evening_wrap(memory: Memory) -> str:
    system = build_system_prompt(memory.recall(""), memory.open_goals())
    today = local_now().date()
    tomorrow = [e["when"] + " " + e["title"] for e in agenda(memory, 2)
                if e.get("start_at") and datetime.fromisoformat(e["start_at"]).astimezone(user_tz()).date() > today]
    return await compose(
        system,
        f"It's {local_now().strftime('%A evening')}. Give the user a short, warm evening wrap-up, "
        "about three spoken sentences: what they got done (celebrate it), what's still open "
        "(suggest moving or dropping it, don't nag), and the first thing tomorrow. "
        f"{Organiser(memory).wrap_up_facts()} "
        f"Tomorrow's calendar: {'; '.join(tomorrow) or 'nothing or not connected'}. "
        f"Email still worth a look: {MailManager(memory, None).briefing_line()}.",
        memory,
    )


def _due(memory: Memory, at: str, key: str) -> bool:
    if not at:
        return False
    now = local_now()
    hour, minute = map(int, at.split(":"))
    if (now.hour, now.minute) < (hour, minute):
        return False
    return memory.get(key) != now.date().isoformat()


def briefing_due(memory: Memory) -> bool:
    return _due(memory, settings.morning_brief_at, "last_briefing")


def wrap_up_due(memory: Memory) -> bool:
    return _due(memory, settings.evening_wrap_at, "last_wrap_up")


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
            if wrap_up_due(memory):
                text = await evening_wrap(memory)
                if text and await deliver("wrap_up", text):
                    memory.set("last_wrap_up", local_now().date().isoformat())
        except Exception:
            log.exception("proactive loop error")
        await asyncio.sleep(CHECK_EVERY_SECONDS)
