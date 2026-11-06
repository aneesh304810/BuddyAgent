"""Buddy speaking up on its own: due reminders, the morning briefing and the evening wrap-up."""

import asyncio
import json
import logging
import os
import time
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
CREDIT_DEALS = os.getenv("BUDDY_CREDIT_DEALS", "1") not in ("", "0")  # deal search before a credit runs out
TREND_DIGEST = os.getenv("BUDDY_TRENDS", "1") not in ("", "0")  # morning trend digest and spike alerts (trends.py)
MARKET_SCAN = os.getenv("BUDDY_MARKET_SCAN", "1") not in ("", "0")  # the daily trade-idea scan (markets.py)
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
        f"{thinking.morning_note(memory)}"
        + (f" They asked for these in every briefing, say them briefly: {topics}" if (topics := await _topics_line(memory))
           else ""),
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
    from .customize import settings as custom  # "stop the morning briefing"

    if custom(memory).get("briefing") == "off":
        return False
    return _due(memory, settings.morning_brief_at, "last_briefing")


async def _topics_line(memory: Memory) -> str:
    """The extra things the user asked for in the briefing ("cricket scores"), looked up now (a few cents)."""
    from .customize import briefing_topics

    topics = briefing_topics(memory)
    if not topics:
        return ""
    from .agent import fallback_kwargs
    from .config import claude_client

    client = claude_client()
    started = time.monotonic()
    try:
        response = await client.beta.messages.create(
            model=settings.fast_model, max_tokens=700,
            messages=[{"role": "user", "content": f"Today is {local_now():%A %d %B %Y}. In one short line each, the latest "
                       f"on: {'; '.join(topics)}. Facts only, from today's or yesterday's news or results."}],
            tools=[{"type": "web_search_20260209", "name": "web_search", "max_uses": min(4, len(topics) + 1)}],
            output_config={"effort": "low"}, **fallback_kwargs(settings.fast_model))
    except Exception:
        log.exception("briefing topics lookup failed")
        return ""
    if hasattr(response, "usage"):
        from . import usage
        usage.record(memory, response.model, response.usage, purpose="briefing", response=response, started=started)
    return " ".join(b.text for b in response.content if getattr(b, "type", "") == "text").strip()[:900]


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
            if CARD_LOOKUP and memory.get("benefits:check_new") and not in_quiet_hours():  # a card just connected
                memory.set("benefits:check_new", "")
                try:
                    perks = benefits.Benefits(memory)
                    if not memory.get("benefits:setup_done"):
                        job_id, text = await perks.setup()
                        await deliver("money", text, **({"job_id": job_id} if job_id else {}))
                    else:
                        for text in await perks.offer_new_cards(per_day=10):
                            await deliver("money", text)
                except Exception:
                    log.exception("new card benefits lookup failed")
            if CARD_LOOKUP and memory.get("benefits:setup_requested"):  # "set up my card benefits": now
                fresh = memory.get("benefits:setup_requested") == "fresh"
                memory.set("benefits:setup_requested", "")
                try:
                    job_id, text = await benefits.Benefits(memory).setup(fresh)
                    await deliver("money", text, **({"job_id": job_id} if job_id else {}))
                except Exception:
                    log.exception("card benefits setup failed")
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
            try:  # credit monitoring emails and notifications, and new cards at connected banks: read as they come
                from .credit import Credit  # noqa: PLC0415
                watch = Credit(memory)
                await watch.scan_emails()
                watch.watch_connected_cards()
            except Exception:
                log.exception("credit watch failed")
                watch = None
            try:  # today's best free OpenRouter models per task (free models come and go)
                from . import openrouter_catalog  # noqa: PLC0415
                if os.getenv("BUDDY_OPENROUTER_KEY") and openrouter_catalog.due(memory) and \
                        memory.get("openrouter:tried") != local_now().strftime("%Y-%m-%dT%H"):
                    memory.set("openrouter:tried", local_now().strftime("%Y-%m-%dT%H"))  # at most once an hour on errors
                    await asyncio.to_thread(openrouter_catalog.refresh, memory)
            except Exception:
                log.exception("OpenRouter model list failed")
            try:  # trends: the morning digest, and a message when a watched topic suddenly takes off
                from . import trends as trd  # noqa: PLC0415
                if memory.get("trends:digest_requested") or (TREND_DIGEST and trd.digest_due(memory)
                                                               and not in_quiet_hours()):
                    asked = bool(memory.get("trends:digest_requested"))
                    memory.set("trends:digest_requested", "")
                    memory.set("trends:digest", local_now().date().isoformat())
                    _, text = await trd.Trends(memory).digest()
                    if asked or not in_quiet_hours():
                        await deliver("money", text)
                slot = local_now().strftime("%Y-%m-%d:") + str(local_now().hour // 3)
                if TREND_DIGEST and memory.get("trends:spike_check") != slot and memory.get("trends:digest"):
                    memory.set("trends:spike_check", slot)  # every three hours
                    watcher = trd.Trends(memory)
                    for topic in watcher.topics():
                        await asyncio.to_thread(watcher.collect, topic)
                        hot = watcher.spike(topic["name"])
                        mark = f"trends:spiked:{topic['name']}:{local_now().date().isoformat()}"
                        if hot and not memory.get(mark) and not in_quiet_hours():
                            memory.set(mark, "1")
                            b = await watcher.brief(topic)
                            lead = (b.get("summary") or {}).get("headline") or (b["stories"][0]["title"] if b["stories"] else "")
                            await deliver("money", f"{topic['name']} is suddenly everywhere: {hot['today']} mentions today, "
                                                   f"about {hot['usual']} on a usual day. {lead} Ask me for the details.")
            except Exception:
                log.exception("trends failed")
            try:  # markets: the daily scan after the US close (or when asked), and ideas checked in market hours
                from . import markets as mkt  # noqa: PLC0415
                trader = mkt.Markets(memory)
                if memory.get("markets:scan_requested") or (MARKET_SCAN and mkt.scan_due(memory)):
                    asked = bool(memory.get("markets:scan_requested"))
                    memory.set("markets:scan_requested", "")
                    memory.set("markets:scanned", local_now().date().isoformat())
                    _, text = await trader.scan()
                    for alert in await asyncio.to_thread(trader.close_check):
                        text += " " + alert["text"]
                    if asked or not in_quiet_hours():
                        await deliver("money", text)
                if (trader.positions() or memory.get("markets:scanned")) and \
                        memory.get("markets:checked") != local_now().strftime("%Y-%m-%dT%H:") + str(local_now().minute // 15):
                    memory.set("markets:checked", local_now().strftime("%Y-%m-%dT%H:") + str(local_now().minute // 15))
                    for alert in await asyncio.to_thread(trader.check):
                        if not in_quiet_hours():
                            await deliver("money", alert["text"])
            except Exception:
                log.exception("markets failed")
            if not in_quiet_hours():
                if watch is not None:
                    for alert in watch.alerts():  # new hard inquiries, accounts, late payments; a score drop
                        if await deliver("money", alert["text"]):
                            watch.told(alert)
                fin = finance.Finance(memory)
                for alert in fin.alerts():
                    if await deliver("money", alert["text"]):
                        fin.told(alert)
                if local_now().hour >= 9:  # card credits running out: in the morning, each step once
                    perks = benefits.Benefits(memory)
                    if CREDIT_DEALS:  # deals for credits ending within a month, found before the 1-month reminder
                        try:
                            await perks.prepare_deals()
                        except Exception:
                            log.exception("credit deal search failed")
                    if memory.get("benefits:linked") != today:  # statement credits no wording explains: once a day
                        memory.set("benefits:linked", today)
                        try:
                            await perks.link_credits()
                        except Exception:
                            log.exception("statement credit linking failed")
                    for alert in perks.bundle(perks.alerts()):
                        if await deliver("money", alert["text"]):
                            perks.told(alert)
                    from .cancelling import Cancelling  # noqa: PLC0415
                    trials = Cancelling(memory)
                    for alert in trials.trial_alerts():  # free trials about to start charging
                        if await deliver("money", alert["text"]):
                            trials.told_trial(alert)
                    if CARD_LOOKUP and not memory.get("benefits:setup_done") \
                            and memory.get("benefits:setup_tried") != today and perks.known_cards():
                        memory.set("benefits:setup_tried", today)  # the first time: every card at once
                        try:
                            job_id, text = await perks.setup()
                            await deliver("money", text, **({"job_id": job_id} if job_id else {}))
                        except Exception:
                            log.exception("card benefits setup failed")
                    elif CARD_LOOKUP and memory.get("benefits:cards_checked") != today:
                        memory.set("benefits:cards_checked", today)
                        try:  # then only new cards, and the yearly refresh
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
