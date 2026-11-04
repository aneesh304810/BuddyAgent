"""Buddy speaking up on its own: due reminders, the morning briefing and the evening wrap-up."""

import asyncio
import json
import logging
import os
from datetime import datetime
from collections.abc import Awaitable, Callable

from . import advisor, benefits, finance, review, health, thinking, travel
from .agent import compose
from .config import settings
from .memory import Memory
from .persona import build_system_prompt
from .connectors import agenda
from .mailbrain import MailManager
from .money import Money, bill_reminder_text
from .lifelog import LifeLog
from .organiser import Organiser
from .connectors.phone import in_quiet_hours
from .thrift import Thrift
from .tools import format_local, local_now, user_tz

log = logging.getLogger(__name__)
CARD_LOOKUP = os.getenv("BUDDY_CARD_LOOKUP", "1") not in ("", "0")  # published card benefits, from issuer sites
SOC_MONTHLY = os.getenv("BUDDY_SOC_MONTHLY", "1") not in ("", "0")

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
        f"Bills: {Money(memory).morning_line()}. "
        f"Email: {MailManager(memory, None).briefing_line()}. "
        f"Spending: {Thrift(memory).summary()}. "
        f"Buddy's own health check (mention in a few words only if there's something): "
        f"{health.morning_line(memory) or 'nothing'}. "
        f"{thinking.morning_note(memory)}",
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
        f"Email still worth a look: {MailManager(memory, None).briefing_line()}. "
        f"Spent today: {Money(memory).today_line()} (mention only if notable).",
        memory,
    )


def day_facts(memory: Memory) -> str:
    """What today held, for the life log's daily entry."""
    events = "; ".join(e["when"] + " " + e["title"] for e in agenda(memory, 1)) or "nothing on the calendar"
    return (f"{Organiser(memory).wrap_up_facts()} Calendar: {events}. "
            f"Spent: {Money(memory).today_line()}. Email: {MailManager(memory, None).briefing_line()}.")


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
                    thinking.taken(memory)
            today = local_now().date().isoformat()
            if memory.get("finance:refreshed") != today:
                await asyncio.to_thread(finance.Finance(memory).refresh)
                memory.set("finance:refreshed", today)
            if memory.get("voiceprint:purge_checked") != today:  # biometric retention schedule
                memory.set("voiceprint:purge_checked", today)
                from . import voiceprint  # noqa: PLC0415
                if voiceprint.purge_unused(memory):
                    log.info("voiceprint unused for 12 months: deleted")
            month_now = local_now().strftime("%Y-%m")
            if SOC_MONTHLY and memory.get("soc:monthly_saved") != month_now:
                memory.set("soc:monthly_saved", month_now)  # once a month, even if it fails: no retry storm
                try:  # SOC 2 readiness evidence, monthly: it lands in Reports (Type II needs controls over time)
                    from . import compliance  # noqa: PLC0415
                    await asyncio.to_thread(compliance.generate, memory)
                except Exception:
                    log.exception("monthly SOC readiness report failed")
            if advisor.monthly_due(memory):
                month = local_now().strftime("%Y-%m")
                adv = advisor.Advisor(memory)
                if memory.get("finance:monthly_saved") != month:
                    job_id, summary = adv.save_month()
                    try:  # the detailed review of last month goes to Reports alongside the check-in
                        await review.Review(memory).save_full("last_month")
                        summary = summary.replace("The full check-in is in Reports.",
                                                  "The check-in and a detailed money review are in Reports.")
                    except Exception:
                        log.exception("monthly money review failed")
                    memory.set("finance:monthly_saved", month)
                    memory.set("finance:monthly_job", json.dumps([job_id, summary]))
                job_id, summary = json.loads(memory.get("finance:monthly_job"))
                if await deliver("money_monthly", summary, job_id=job_id):
                    memory.set("finance:monthly", month)
            if finance.weekly_due(memory):
                fin = finance.Finance(memory)
                if memory.get("finance:weekly_saved") != today:
                    job_id, summary = fin.save_week()
                    memory.set("finance:weekly_saved", today)
                    memory.set("finance:weekly_job", json.dumps([job_id, summary]))
                job_id, summary = json.loads(memory.get("finance:weekly_job"))
                if await deliver("money_weekly", summary, job_id=job_id):
                    memory.set("finance:weekly", today)
            if not in_quiet_hours() and local_now().hour >= 9 and memory.get("travel:watched") != today:
                memory.set("travel:watched", today)  # once a day, in the morning
                trips = travel.Travel(memory)
                for alert in await asyncio.to_thread(trips.check_watches):
                    if await deliver("travel", alert["text"]):
                        trips.told(alert)
            if not in_quiet_hours():
                fin = finance.Finance(memory)
                for alert in fin.alerts():
                    if await deliver("money", alert["text"]):
                        fin.told(alert)
                if local_now().hour >= 9:  # card credits running out: in the morning, each step once
                    perks = benefits.Benefits(memory)
                    for alert in perks.alerts():
                        if await deliver("money", alert["text"]):
                            perks.told(alert)
                    if CARD_LOOKUP and memory.get("benefits:cards_checked") != today:  # new cards: their published credits, offered once
                        memory.set("benefits:cards_checked", today)
                        try:
                            for text in await perks.offer_new_cards() + await perks.recheck():
                                await deliver("money", text)
                        except Exception:
                            log.exception("card benefits lookup failed")
                money = Money(memory)
                for bill in money.bills_to_remind():
                    if await deliver("bill", bill_reminder_text(bill)):
                        money.reminded(bill["id"])
            if wrap_up_due(memory):
                text = await evening_wrap(memory)
                if text and await deliver("wrap_up", text):
                    memory.set("last_wrap_up", local_now().date().isoformat())
                    LifeLog(memory).log_day(day_facts(memory))
        except Exception:
            log.exception("proactive loop error")
        await asyncio.sleep(CHECK_EVERY_SECONDS)
