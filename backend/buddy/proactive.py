"""Buddy speaking up on its own: due reminders, the morning briefing and the evening wrap-up."""

import asyncio
import json
import logging
import os
from pathlib import Path
import time
from datetime import datetime, timezone
from collections.abc import Awaitable, Callable

from . import advisor, benefits, finance, review, health, thinking, travel
from .agent import compose
from .config import settings
from .memory import Memory
from .connectors.hub import agenda, calendar_connected
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
DAILY_DEALS = os.getenv("BUDDY_DAILY_DEALS", "1") not in ("", "0")  # the morning deals collector (dealcollector.py)
MARKET_SCHOOL = os.getenv("BUDDY_MARKET_SCHOOL", "1") not in ("", "0")  # the weekday morning lesson (market_school.py)
MARKET_SCAN = os.getenv("BUDDY_MARKET_SCAN", "1") not in ("", "0")  # the daily trade-idea scan (markets.py)
CARD_LOOKUP = os.getenv("BUDDY_CARD_LOOKUP", "1") not in ("", "0")  # published card benefits, from issuer sites
SOC_MONTHLY = os.getenv("BUDDY_SOC_MONTHLY", "1") not in ("", "0")

CHECK_EVERY_SECONDS = 15

# Delivers a nudge to connected phones; returns False when none are listening.
Deliver = Callable[[str, str], Awaitable[bool]]


REMINDER_PLAIN_WORDS = 5  # a reminder this short is said as it is: no AI call


async def reminder_nudge(memory: Memory, reminder: dict) -> str:
    """A due reminder in one friendly sentence: plain words for short ones, else the quick model with a small prompt
    (no memory recall, no tools): a reminder doesn't need the main model or the whole brain."""
    plain = f"Hey, reminder: {reminder['text']}."
    if len(reminder["text"].split()) <= REMINDER_PLAIN_WORDS:
        return plain
    text = await compose(
        "You are Buddy, the user's personal assistant and friend. Warm, brief, spoken English.",
        "A reminder the user set has just come due. Tell them in one short, natural "
        f"spoken sentence, as their friend would. Reminder: {reminder['text']}",
        memory, label="reminder", model=settings.fast_model,
    )
    return text or plain


def _spoken_system(memory: Memory) -> str:
    """A small prompt for background writing (briefing, wrap-up): who Buddy is, how it talks, a few facts and the
    goals. Not the full chat prompt: its ~30 KB of tool rules are useless here and were most of the briefing's cost."""
    facts = "; ".join(memory.recall("", limit=12))
    goals = "; ".join(g["text"] for g in memory.open_goals()[:6])
    return (f"You are {settings.assistant_name}, {settings.user_name}'s personal assistant and friend. Warm, brief, "
            "natural spoken English: no markdown, lists or emoji; it is read aloud. Use only the facts given; never "
            "invent events, numbers or news. "
            + (f"About {settings.user_name}: {facts}. " if facts else "")
            + (f"Their goals: {goals}." if goals else ""))


def _life_line(memory: Memory) -> str:
    """Appointments, school and travel dates Buddy found in the owner's email, for this week."""
    try:
        from . import lifemail  # noqa: PLC0415
        line = lifemail.briefing_line(memory)
        return f"{line} (mention today's and tomorrow's) " if line else ""
    except Exception:
        log.info("briefing: life memory failed", exc_info=True)
        return ""


async def morning_briefing(memory: Memory) -> str:
    system = _spoken_system(memory)
    reminders = [
        f"{r['text']} at {format_local(r['due_at'])}" for r in memory.upcoming_reminders()
    ]
    from . import nest  # noqa: PLC0415
    home = await asyncio.to_thread(nest.status_line, memory)  # '' without a Nest
    from . import coach, oura  # noqa: PLC0415
    await asyncio.to_thread(oura.sync_if_due, memory)  # last night's sleep and readiness
    try:
        body = await asyncio.to_thread(coach.morning_line, memory)  # '' without a health plan
    except Exception:
        body = ""
    try:  # the news on their interests, the finance headlines and newly listed events (daybrief.py)
        from . import daybrief  # noqa: PLC0415
        world = await asyncio.to_thread(daybrief.facts, memory)
    except Exception:
        log.exception("briefing news failed")
        world = {"say": "", "shown": ""}
    spoken = await compose(
        system,
        f"It's {local_now().strftime('%A %d %B, %I:%M %p')}. Give the user a short, upbeat "
        "morning briefing, about four spoken sentences: greet them, mention what's "
        "coming up today (calendar, tasks due, any urgent email), nudge them on one of their goals, and end with yesterday's AI "
        "cost in a few words (say if we're running ahead of budget). "
        f"Upcoming reminders: {'; '.join(reminders) or 'none'}. "
        f"Today's calendar: {'; '.join(e['when'] + ' ' + e['title'] for e in agenda(memory, 1)) or _no_events(memory)}. "
        f"Tasks: {Organiser(memory).morning_line()}. "
        f"Bills: {Money(memory).morning_line()}. "
        f"{('Home thermostat (mention only if notable): ' + home + '. ') if home else ''}"
        f"{('Their health plan (one short line on how recovered they are and what that means today): ' + body + '. ') if body else ''}"
        f"Email: {MailManager(memory, None).briefing_line()}. {_life_line(memory)}"
        f"Spending: {Thrift(memory).summary()}. "
        f"Buddy's own health check (mention in a few words only if there's something): "
        f"{health.morning_line(memory) or 'nothing'}. "
        f"{thinking.morning_note(memory)}"
        + (f" They asked for these in every briefing, say them briefly: {topics}" if (topics := await _topics_line(memory))
           else "")
        + (" Then, in up to three more short sentences, the most notable news on their interests, the main finance "
           "headline and any new event, from these only (skip a part that has nothing, never add news of your "
           f"own): {world['say']}" if world["say"] else ""),
        memory, label="briefing", model=settings.fast_model,  # quick model: a few spoken sentences from given facts
    )
    # spoken || shown: the same words on screen, with the headlines, links and events listed under them
    return f"{spoken} || {spoken}\n\n{world['shown']}" if spoken and world["shown"] else spoken


def _no_events(memory: Memory) -> str:
    """What to tell the model when the calendar has nothing: empty, or no calendar source at all."""
    return "nothing" if calendar_connected(memory) else "not connected (no iCal link and no phone calendar shared)"


async def evening_wrap(memory: Memory) -> str:
    system = _spoken_system(memory)
    today = local_now().date()
    tomorrow = [e["when"] + " " + e["title"] for e in agenda(memory, 2)
                if e.get("start_at") and datetime.fromisoformat(e["start_at"]).astimezone(user_tz()).date() > today]
    return await compose(
        system,
        f"It's {local_now().strftime('%A evening')}. Give the user a short, warm evening wrap-up, "
        "about three spoken sentences: what they got done (celebrate it), what's still open "
        "(suggest moving or dropping it, don't nag), and the first thing tomorrow. "
        f"{Organiser(memory).wrap_up_facts()} "
        f"Tomorrow's calendar: {'; '.join(tomorrow) or _no_events(memory)}. "
        f"Email still worth a look: {MailManager(memory, None).briefing_line()}. "
        f"Spent today: {Money(memory).today_line()} (mention only if notable).",
        memory, label="evening_wrap", model=settings.fast_model,
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
    day = f"briefing_topics:{local_now().date().isoformat()}"
    if int(memory.get(day) or 0) >= 2:  # looked up with the briefing, at most twice a day whatever happens
        return ""
    memory.set(day, str(int(memory.get(day) or 0) + 1))
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


WRITE_TRIES_PER_DAY, WRITE_TRY_GAP_MINUTES = 3, 10


async def _written_once(memory: Memory, key: str, write, plain: str = "") -> str:
    """Text an AI wrote for a nudge, kept until it's delivered: written once, not again on every 15-second pass
    while the phone isn't connected (that loop once cost about $30 a week). With Claude stopped by the monthly
    cutoff (budgets.py): `plain`, written in code ('' skips the nudge).

    An empty answer or an error is not retried on the next pass: at most 3 tries a day per nudge, 10 minutes apart,
    then `plain` (one warning in the log)."""
    saved = memory.get(f"nudge_text:{key}")
    if saved:
        return saved
    from . import budgets  # noqa: PLC0415
    if budgets.claude_stopped(memory):
        return plain
    now = datetime.now(timezone.utc)
    day, tries, last = (memory.get(f"nudge_try:{key}") or "||").split("|")[:3]
    tries = int(tries or 0) if day == now.date().isoformat() else 0
    if tries >= WRITE_TRIES_PER_DAY:
        return plain
    if last and (now - datetime.fromisoformat(last)).total_seconds() < WRITE_TRY_GAP_MINUTES * 60:
        return plain
    memory.set(f"nudge_try:{key}", f"{now.date().isoformat()}|{tries + 1}|{now.isoformat()}")
    try:
        text = await write()
    except budgets.BudgetStop:
        return plain
    except Exception:
        log.warning("writing %s failed (try %d of %d)", key, tries + 1, WRITE_TRIES_PER_DAY, exc_info=True)
        text = ""
    if not text and tries + 1 >= WRITE_TRIES_PER_DAY:
        log.warning("writing %s: no text after %d tries today; using plain words", key, WRITE_TRIES_PER_DAY)
    if text:
        memory.set(f"nudge_text:{key}", text)
    return text or plain


def _sent(memory: Memory, key: str) -> None:
    memory.set(f"nudge_text:{key}", "")
    memory.set(f"nudge_try:{key}", "")  # delivered: a later nudge with this key starts fresh


async def run(memory: Memory, deliver: Deliver, connected=None) -> None:
    """Loop forever, pushing due nudges. Undelivered ones wait for a connection.
    connected(): is a phone listening? Nothing is written by an AI while none is (it would only be rewritten)."""
    while True:
        try:
            online = connected() if connected else True
            for reminder in memory.due_reminders() if online else []:
                key = f"reminder:{reminder['id']}"
                text = await _written_once(memory, key, lambda r=reminder: reminder_nudge(memory, r),
                                           plain=f"Reminder: {reminder['text']}")
                if text and await deliver("reminder", text):
                    memory.mark_delivered(reminder["id"])
                    _sent(memory, key)
            if online and briefing_due(memory):
                key = f"briefing:{local_now().date().isoformat()}"
                text = await _written_once(memory, key, lambda: morning_briefing(memory))
                if text and await deliver("briefing", text):
                    memory.set("last_briefing", local_now().date().isoformat())
                    thinking.taken(memory)
                    _sent(memory, key)
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
                try:  # saved searches: each re-run live once a day, an alert on a deal (travel_saved.py)
                    from .travel_saved import SavedSearches  # noqa: PLC0415
                    saved = SavedSearches(memory, trips)
                    for alert in await saved.check():
                        if await deliver("travel", alert["text"]):
                            saved.told(alert)
                except Exception:
                    log.exception("saved travel searches failed")
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
            try:  # how Buddy thinks (reasoning.py): its self-knowledge, follow-ups on advice, new sources to suggest
                from . import reasoning  # noqa: PLC0415
                from .brain import Brain  # noqa: PLC0415
                thinker = reasoning.Reasoning(memory)
                if memory.get("reasoning:self_checked") != local_now().strftime("%Y-%m-%dT%H"):
                    memory.set("reasoning:self_checked", local_now().strftime("%Y-%m-%dT%H"))  # hourly: code changed?
                    reasoning.refresh_self_knowledge(Brain(memory))  # on the loop: it writes, and the DB is shared
                if not in_quiet_hours() and local_now().hour >= 10:
                    for f in thinker.follow_ups():
                        if await deliver("followup", f["text"]):
                            thinker.asked(f["id"])
                    if local_now().weekday() == 6 and local_now().hour >= 11:  # Sundays: sources to add
                        idea = await thinker.weekly_suggestion()
                        if idea:
                            await deliver("followup", idea)
            except Exception:
                log.exception("reasoning upkeep failed")
            try:  # the Buddy Intelligence Platform: its nightly collection, convergences and formations
                from . import bip  # noqa: PLC0415
                if bip.due(memory):
                    bip.mark_done(memory)
                    found = await bip.run(memory)
                    log.info("intelligence: %d new records, %d convergences, failed: %s", found["new"],
                             found["convergences"], ", ".join(found["failed"]) or "none")
                    text = bip.message(found)
                    if text and not in_quiet_hours():
                        await deliver("news", text, topic="news:intelligence")
                    from . import bip_links  # noqa: PLC0415  who funds, supplies or partners with whom
                    try:
                        linked = await bip_links.nightly(memory)
                        log.info("links: %d official, %d headlines read, %d companies named, %d relationships",
                                 linked["official"], linked["read"], linked["companies"], linked["links"])
                    except Exception:
                        log.exception("reading relationships failed")
                    from . import themes  # noqa: PLC0415  the evidence joined to the market, with practice money
                    moved = await themes.nightly(memory)
                    log.info("themes: %d new events, %d results filled in, %d company headlines, %d labelled",
                             len(moved["new"]), moved["measured"], moved["company_news"], moved["labelled"])
                    try:
                        from . import watchpicks  # noqa: PLC0415  the recommended watchlist, from the evidence
                        picked = await asyncio.to_thread(watchpicks.refresh, memory)
                        log.info("watchlist picks: %d added, %d returned, %d archived", len(picked["added"]),
                                 len(picked["returned"]), len(picked["archived"]))
                    except Exception:
                        log.exception("refreshing the recommended watchlist failed")
                    text = themes.message(moved, memory)
                    if text and not in_quiet_hours():
                        await deliver("news", text, topic="news:themes")
            except Exception:
                log.exception("intelligence platform failed")
            try:  # technology that matters for Buddy: sorted and verified once a night, shown in Buddy News
                from . import techradar  # noqa: PLC0415
                if techradar.due(memory):
                    techradar.mark_done(memory)
                    found = await techradar.run(memory)
                    log.info("tech radar: read %d, kept %d, new %d", found["read"], found["kept"], len(found["new"]))
                    text = techradar.message(found)
                    if text and not in_quiet_hours():
                        await deliver("news", text, topic="news:tech")
            except Exception:
                log.exception("tech radar failed")
            try:  # the model scout: daily, and sooner for a free provider that was just rested for failing
                from . import modelscout  # noqa: PLC0415
                wanted = modelscout.due(memory)
                if wanted is not None:
                    modelscout.mark_done(memory, daily=not wanted)
                    found = await asyncio.to_thread(modelscout.run, memory, None, wanted or None)
                    text = modelscout.message(found)
                    if text:
                        await deliver("health", text)
            except Exception:
                log.exception("model scout failed")
            try:  # the threat watch: once a day, what is newly published against what this server runs
                from . import securitywatch  # noqa: PLC0415
                if securitywatch.due(memory):
                    memory.set(securitywatch.DAY_KEY, local_now().date().isoformat())
                    got = await asyncio.to_thread(securitywatch.scan, memory)
                    log.info("threat watch: %d new, %d fixed", len(got["new"]), len(got["fixed"]))
                    for text in securitywatch.alerts(memory):
                        await deliver("security", text)
            except Exception:
                log.exception("threat watch failed")
            try:  # life memory: appointments, school, home, family and travel documents in the owner's email
                from . import lifemail  # noqa: PLC0415
                for text in await lifemail.scan(memory):
                    await deliver("email", text)
            except Exception:
                log.exception("life memory scan failed")
            try:  # the brain connects: plans in new emails, notifications and uploads; upcoming flights tracked
                from .connect import Connect  # noqa: PLC0415
                for text in await Connect(memory).scan():
                    await deliver("travel", text)
                from .connect import Moments  # noqa: PLC0415  Buddy speaks first: plans, where you are, the time
                from .maps import Maps  # noqa: PLC0415
                atlas = Maps(memory)
                if memory.get("maps:learned") != local_now().date().isoformat():
                    memory.set("maps:learned", local_now().date().isoformat())  # home, work: once a day
                    atlas.learn_places()  # on the loop: it writes to the shared DB
                    from . import commute  # noqa: PLC0415  the weekday routine, from where the phone goes
                    said = await asyncio.to_thread(commute.relearn, memory)
                    if said:
                        await deliver("travel", said)
                    from . import routine  # noqa: PLC0415  the whole week's routine (quietly)
                    try:
                        await asyncio.to_thread(routine.relearn, memory)
                    except Exception:
                        log.exception("routine learning failed")
                hold = await asyncio.to_thread(atlas.hold_non_urgent)  # driving or in a meeting: only urgent ones now
                extra = []
                try:  # packages: shipping emails and notifications (text rules), 17TRACK live status every 2 hours
                    from .packages import Packages  # noqa: PLC0415
                    parcels = Packages(memory)
                    parcels.scan()
                    if parcels.key and memory.get("packages:refreshed") != local_now().strftime("%Y-%m-%dT%H"):
                        memory.set("packages:refreshed", local_now().strftime("%Y-%m-%dT%H"))
                        parcels.refresh()
                    away = atlas.situation().get("place") not in ("home", "")
                    for item in parcels.news(away_from_home=away):
                        if (item["urgent"] or (not in_quiet_hours() and not hold)) and \
                                await deliver("travel", item["text"]):
                            parcels.told(item)
                except Exception:
                    log.exception("packages failed")
                try:  # the morning weather heads-up, only when rain or snow is likely where the user is
                    now_l = local_now()
                    if 6 <= now_l.hour < 10 and memory.get("weather:told") != now_l.date().isoformat():
                        from .weather import Weather  # noqa: PLC0415
                        fc = await asyncio.to_thread(Weather(memory).here, 1)
                        memory.set("weather:told", now_l.date().isoformat())  # one look a morning, said or not
                        today = (fc or {}).get("days", [{}])[0] if fc else {}
                        if today and (today.get("wet") or (today.get("rain_chance") or 0) >= 60):
                            when = Weather.wet_from(fc, today["day"])
                            extra.append({"key": f"wx:{today['day']}", "urgent": False,
                                          "text": f"{today['conditions'].capitalize()} likely today"
                                                  f" ({today.get('rain_chance') or '?'}%{', ' + when if when else ''}), "
                                                  f"{today['high']}/{today['low']}{fc['unit']}. Take an umbrella."})
                except Exception:
                    log.exception("weather heads-up failed")
                moments = Moments(memory)
                from . import commute  # noqa: PLC0415
                heads = await asyncio.to_thread(commute.nudges, memory)  # before the usual leave: trains, alerts
                from . import nest  # noqa: PLC0415  after leaving: the house still heating? offer eco
                heads += await asyncio.to_thread(nest.nudges, memory)
                for m in await asyncio.to_thread(moments.now) + await asyncio.to_thread(atlas.nudges) + heads + extra:
                    if (m["urgent"] or (not in_quiet_hours() and not hold)) and not moments._said(m["key"]) \
                            and await deliver("travel", m["text"]):
                        moments.told(m)
            except Exception:
                log.exception("connecting plans failed")
            try:  # the health plan: the ring's data every few hours, and its nudges (coach.py), each said once
                from . import coach, oura  # noqa: PLC0415
                await asyncio.to_thread(oura.sync_if_due, memory)
                said_key = "coach:said"
                said = set(json.loads(memory.get(said_key) or "[]"))
                busy = None
                for m in await asyncio.to_thread(coach.nudges, memory):
                    if m["key"] in said or in_quiet_hours():
                        continue
                    if busy is None:  # driving or in a meeting: these wait
                        from .maps import Maps  # noqa: PLC0415
                        busy = await asyncio.to_thread(Maps(memory).hold_non_urgent)
                    if not busy and await deliver("coach", m["text"]):
                        said.add(m["key"])
                        memory.set(said_key, json.dumps(sorted(said)[-60:]))
                if local_now().hour == 3:
                    await asyncio.to_thread(coach.prune, memory)
            except Exception:
                log.exception("health plan nudges failed")
            try:  # flights: the user's tracked flights, every 15 minutes from 4 hours before take-off to landing
                from .flights import Flights  # noqa: PLC0415
                fl = Flights(memory)
                if fl.due():  # their own flight: quiet hours don't hold these back
                    for alert in await asyncio.to_thread(fl.check):
                        await deliver("travel", alert["text"])
            except Exception:
                log.exception("flight status failed")
            try:  # transit: the lines the user rides, checked every few minutes while they ride them (transit.py)
                from .transit import Transit  # noqa: PLC0415
                commute = Transit(memory)
                if commute.due():  # their own commute times, so quiet hours don't hold these back
                    for alert in await asyncio.to_thread(commute.check):
                        if await deliver("travel", alert["text"]):
                            commute.told(alert)
            except Exception:
                log.exception("transit failed")
            try:  # shopping: order and return emails into the lasting purchase history (code only), hourly
                from . import shopper  # noqa: PLC0415
                hour = local_now().strftime("%Y-%m-%dT%H")
                if memory.get("shop:scan_hour") != hour:
                    memory.set("shop:scan_hour", hour)
                    await asyncio.to_thread(shopper.scan_orders, memory)
                    from . import dealnotif  # noqa: PLC0415  deal notifications from shopping and travel apps
                    await asyncio.to_thread(dealnotif.scan, memory)
                    from . import whentobuy  # noqa: PLC0415  buy-or-wait calls whose date passed: right or wrong
                    await asyncio.to_thread(whentobuy.settle, memory)
                if not in_quiet_hours():  # "how's the new X?": one purchase a day, two to six weeks after
                    due_item = await asyncio.to_thread(shopper.checkin_due, memory)
                    if due_item and await deliver("followup", shopper.checkin_text(due_item)):
                        shopper.asked(memory, due_item["id"])
            except Exception:
                log.exception("purchase history failed")
            try:  # nightly (01:00): the deals inbox read into the price book, by code (no AI)
                from . import dealmail  # noqa: PLC0415
                if (dealmail.configured() or dealmail.accounts()) and dealmail.due(memory):
                    memory.set(dealmail.DAY_KEY, local_now().date().isoformat())
                    log.info("deals inbox: %s", await asyncio.to_thread(dealmail.run, memory))
                from . import mailorganizer  # noqa: PLC0415  after the deals reader has read the marketing mail
                if mailorganizer.due(memory):
                    memory.set(mailorganizer.DAY_KEY, local_now().date().isoformat())
                    log.info("email organizer: %s", (await asyncio.to_thread(mailorganizer.nightly, memory))["moved"])
            except Exception:
                log.exception("deals inbox failed")
            try:  # nightly: patterns learned in the sandbox, raw prices over 90 days (already learned) deleted,
                # and a copy of the price book for the backup
                from . import patterns, seasons, signals  # noqa: PLC0415
                if signals.due(memory):  # nightly: what Reddit says about deals, travel, products and markets
                    memory.set(signals.DAY_KEY, local_now().date().isoformat())
                    got = await signals.run(memory)
                    log.info("signals: %s posts (%s new) by %s; failed: %s", got["posts"], got["new"], got["route"],
                             len(got["failed"]))
                if seasons.due(memory):  # monthly: seasonal patterns by category
                    memory.set(seasons.MONTH_KEY, local_now().strftime("%Y-%m"))
                    log.info("seasonal prices: %s", await asyncio.to_thread(seasons.refresh, memory))
                if patterns.due(memory):
                    memory.set(patterns.DAY_KEY, local_now().date().isoformat())
                    status = await patterns.learn(memory)
                    log.info("price patterns: %s", status)
                    from . import pricebook  # noqa: PLC0415
                    from . import livewatch  # noqa: PLC0415
                    await asyncio.to_thread(livewatch.prune, memory)
                    gone = await asyncio.to_thread(patterns.prune_raw, memory)
                    if gone:
                        log.info("price book: %d raw prices over a year old deleted (already learned)", gone)
                    if not pricebook.path_for(memory).startswith(":memory:"):
                        await asyncio.to_thread(pricebook.snapshot, memory,
                                                str(Path(pricebook.path_for(memory)).with_name("pricebook-backup")))
            except Exception:
                log.exception("price patterns failed")
            try:  # the daily deals collector: feeds, price checks for wanted things, matches told (code only)
                from . import dealcollector  # noqa: PLC0415
                if DAILY_DEALS and dealcollector.due(memory) and not in_quiet_hours():
                    memory.set(dealcollector.DONE_KEY, local_now().date().isoformat())
                    result = await asyncio.to_thread(dealcollector.run, memory)
                    dealcollector.save_report(memory, result)
                    dealcollector.prune(memory)
                    for text, links in dealcollector.messages(result):
                        await deliver("money", text, cards=links, topic="deals:daily")
                from . import modeltrain  # noqa: PLC0415  weekly: train a model for any domain with enough data
                if modeltrain.due(memory):
                    y, wk, _ = local_now().isocalendar()
                    memory.set(modeltrain.WEEK_KEY, f"{y}-{wk}")
                    for text in await modeltrain.weekly(memory):
                        await deliver("money", text, topic="investing:model")
                from . import funds  # noqa: PLC0415  new 13F filings from the funds followed, checked weekly
                if funds.due(memory) and os.getenv("BUDDY_SEC_CONTACT") and not in_quiet_hours():
                    y, wk, _ = local_now().isocalendar()
                    memory.set(funds.WEEK_KEY, f"{y}-{wk}")
                    note = await asyncio.to_thread(funds.weekly, memory)
                    if note:
                        await deliver("money", note, topic="investing:funds")
                from . import recommend  # noqa: PLC0415  investing signals, weekday mornings
                if recommend.due(memory) and not in_quiet_hours():
                    memory.set(recommend.DAY_KEY, local_now().date().isoformat())
                    for text in await asyncio.to_thread(recommend.run, memory):
                        await deliver("money", text, topic="investing:signals")
                from . import benchmarks, newssent, researchdesk  # noqa: PLC0415  what feeds the strategy lab
                try:
                    if newssent.due(memory):  # how the news on today's companies reads, before the signals
                        memory.set(newssent.DAY_KEY, local_now().date().isoformat())
                        got = await asyncio.to_thread(newssent.refresh, memory)
                        log.info("news sentiment: asked %d, kept %d, failed %s", got["asked"], got["kept"], got["failed"])
                    from . import newsdesk  # noqa: PLC0415  stories behind the trades; unclear ones are noted
                    if newsdesk.due(memory):
                        memory.set(newsdesk.DAY_KEY, local_now().date().isoformat())
                        got = await asyncio.to_thread(newsdesk.review, memory)
                        log.info("news desk: %d companies, %d stories, %d unclear, %d settled, %d asked, %d would ask",
                                 got["companies"], got["stories"], got["unclear"], got["settled"], got["asked"],
                                 got["would_ask"])
                    from . import fundamentals  # noqa: PLC0415  earnings surprises and ratios, once a week
                    if fundamentals.due(memory):
                        now = local_now()
                        memory.set(fundamentals.WEEK_KEY, f"{now.isocalendar()[0]}-{now.isocalendar()[1]:02d}")
                        from . import stratlab as lab_  # noqa: PLC0415
                        got = await asyncio.to_thread(fundamentals.refresh, memory, lab_.universe(memory))
                        log.info("company figures: asked %d, kept %d, failed %d", got["asked"], got["kept"], got["failed"])
                    if benchmarks.factors_due(memory):
                        memory.set(benchmarks.MONTH_KEY, local_now().date().isoformat()[:7])
                        log.info("factor returns: %d month-values held", await benchmarks.refresh_factors(memory))
                    if researchdesk.due(memory):
                        text = await asyncio.to_thread(researchdesk.weekly, memory)
                        if memory.get(benchmarks.HOLDINGS_ON_KEY) == "1":  # the dated stock lists, once a week
                            for name in benchmarks.HOLDINGS:
                                with contextlib.suppress(Exception):
                                    await benchmarks.fetch_holdings(memory, name)
                        if text and not in_quiet_hours():
                            await deliver("money", text, topic="investing:research")
                except Exception:
                    log.exception("the research and benchmark refresh failed")
                from . import stratlab  # noqa: PLC0415  the strategy lab: rule-based paper trades, weekday mornings
                if stratlab.due(memory):
                    memory.set(stratlab.DAY_KEY, local_now().date().isoformat())
                    try:
                        lab = await asyncio.to_thread(stratlab.run, memory)
                        log.info("strategy lab: %d closed, opened %s", lab["closed"], lab["opened"])
                        if not memory.get(stratlab.REPLAY_KEY):  # the first time: the rules' record on past prices
                            stratlab.replay_in_background(memory)
                        told = await asyncio.to_thread(stratlab.message, memory)
                        if told and not in_quiet_hours():
                            await deliver("money", told, topic="investing:lab")
                    except Exception:
                        log.exception("the strategy lab failed")
                from . import dealforecast  # noqa: PLC0415  deals watched for (found overnight or this morning)
                if not in_quiet_hours() and local_now().hour >= dealcollector.START_HOUR:
                    for text, links in dealforecast.take_pending(memory):
                        await deliver("money", text, cards=links, topic="deals:watched")
                    if dealforecast.weekly_due(memory):
                        y, wk, _ = local_now().isocalendar()
                        memory.set(dealforecast.WEEKLY_KEY, f"{y}-{wk}")
                        note = await asyncio.to_thread(dealforecast.weekly, memory)
                        if note:
                            await deliver("money", note, topic="deals:weekly")
            except Exception:
                log.exception("daily deals failed")
            try:  # the morning tutors: a ten-minute investing lesson and a short English lesson (morning_tutor.py)
                from . import morning_tutor  # noqa: PLC0415
                for kind, make in (("investing", morning_tutor.investing), ("english", morning_tutor.english)):
                    if online and morning_tutor.due(memory, kind) and not in_quiet_hours():
                        morning_tutor.mark(memory, kind)
                        await deliver("lesson", await make(memory), topic=f"tutor:{kind}")
            except Exception:
                log.exception("morning tutors failed")
            try:  # Market school: a short lesson every weekday morning (New York time), unless turned off
                from . import market_school  # noqa: PLC0415
                from . import morning_tutor  # noqa: PLC0415  the ten-minute lesson replaces the short one when on
                if morning_tutor.on(memory, "investing") and market_school.due(memory):
                    memory.set(market_school.DONE_KEY, market_school.school_day())
                elif MARKET_SCHOOL and market_school.due(memory) and not in_quiet_hours():
                    memory.set(market_school.DONE_KEY, market_school.school_day())
                    _, text = await market_school.lesson(memory)
                    await deliver("money", text, topic="investing:school")
            except Exception:
                log.exception("market school failed")
            try:  # markets: the daily scan after the US close (or when asked), and ideas checked in market hours
                from . import markets as mkt  # noqa: PLC0415
                trader = mkt.Markets(memory)
                if memory.get("markets:scan_requested") or (MARKET_SCAN and mkt.scan_due(memory)):
                    asked = bool(memory.get("markets:scan_requested"))
                    memory.set("markets:scan_requested", "")
                    memory.set("markets:scanned", mkt.scan_day())  # New York's date, as scan_due checks
                    _, text = await trader.scan()
                    for alert in await asyncio.to_thread(trader.close_check):
                        text += " " + alert["text"]
                    if asked or not in_quiet_hours():
                        await deliver("money", text, topic="investing:scan")
                if (trader.positions() or memory.get("markets:scanned")) and \
                        memory.get("markets:checked") != local_now().strftime("%Y-%m-%dT%H:") + str(local_now().minute // 15):
                    memory.set("markets:checked", local_now().strftime("%Y-%m-%dT%H:") + str(local_now().minute // 15))
                    for alert in await asyncio.to_thread(trader.check):
                        if not in_quiet_hours():
                            await deliver("money", alert["text"], topic="investing:ideas")
            except Exception:
                log.exception("markets failed")
            try:  # live triggers: once a minute in US market hours, for the symbols being followed (code only)
                from . import livewatch  # noqa: PLC0415
                if livewatch.due(memory):
                    memory.set(livewatch.MINUTE_KEY, datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M"))
                    for text in await asyncio.to_thread(livewatch.check, memory):
                        await deliver("money", text, topic="investing:live")
            except Exception:
                log.exception("live triggers failed")
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
                    from . import subcancel  # noqa: PLC0415
                    try:  # cancellations in progress: confirmed, stopped, or still charging
                        for text in await asyncio.to_thread(subcancel.check, memory):
                            await deliver("money", text)
                        if subcancel.review_due(memory):  # the monthly subscription check
                            subcancel.mark_reviewed(memory)
                            text = await asyncio.to_thread(subcancel.review_message, memory)
                            if text:
                                await deliver("money", text)
                    except Exception:
                        log.exception("subscription cancel check failed")
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
            if online and wrap_up_due(memory):
                key = f"wrap_up:{local_now().date().isoformat()}"
                text = await _written_once(memory, key, lambda: evening_wrap(memory))
                if text and await deliver("wrap_up", text):
                    memory.set("last_wrap_up", local_now().date().isoformat())
                    _sent(memory, key)
                    LifeLog(memory).log_day(day_facts(memory))
        except Exception:
            log.exception("proactive loop error")
        await asyncio.sleep(CHECK_EVERY_SECONDS)
