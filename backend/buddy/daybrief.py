"""News and events for the morning briefing: what's new on the owner's interests, the main finance headlines, and
events that have newly appeared near them for those interests. Facts gathered in code; the briefing's writer may
only use these (it never invents news).

  Interests   the topics the owner follows on the News page (trends.py): the latest saved digest for each, when
              it is no more than two days old: its headline and top stories, with links
  Finance     last night's market headlines (Finnhub, from signals.py) and the symbols getting unusual attention
  Events      Ticketmaster, searched by each interest in the owner's city once a day; only events not shown
              before are listed, so the briefing says what is new, not the same concert every morning

  Learning    the briefing follows what the owner talks about. Each morning Buddy counts, in their own sentences
              to it over the last 14 days, the stock symbols and the subjects they brought up (from a list of
              everyday subjects: sports, films, music, AI, property, travel deals and so on). A symbol asked
              about on two or more days gets its own headlines in the finance part. A subject raised on three or
              more days that isn't followed yet is added to the news (one new subject a week, three at most),
              and the briefing says so. A subject Buddy added that hasn't come up for 30 days is dropped, also
              said. Subjects the owner added themselves are never touched. The part of the briefing they talk
              about most comes first. The counting is done in code on the owner's server: nothing is sent out.

Only the interest words and the owner's city go to Ticketmaster, never their position.
"""

import json
import logging
from datetime import datetime, timedelta, timezone

from .memory import Memory
from .tools import local_now

log = logging.getLogger(__name__)

SEEN_KEY, DAY_KEY = "daybrief:events_seen", "daybrief:events_day"
MAX_TOPICS = 4
FOCUS_KEY, LEARN_DAY_KEY = "daybrief:focus", "daybrief:learned_day"
WINDOW_DAYS, ADD_AFTER_DAYS, DROP_AFTER_DAYS, MAX_AUTO, TICKER_DAYS = 14, 3, 30, 3, 2
SUBJECTS = {  # what Buddy can notice the owner talking about: name -> words that mean it
    "Cricket": r"cricket|ipl\b|test match", "Soccer": r"soccer|premier league|champions league|fifa|world cup",
    "NFL": r"\bnfl\b|super bowl", "NBA": r"\bnba\b|basketball", "Tennis": r"tennis|wimbledon|us open",
    "Formula 1": r"formula ?1|\bf1\b|grand prix", "Movies": r"movies?\b|films?\b|box office|netflix",
    "Music and concerts": r"concerts?\b|album|tour dates|spotify", "AI": r"\bai\b|artificial intelligence|llm|chatgpt|claude",
    "Smart glasses": r"smart ?glass|\bhalo\b|ray-?ban meta", "Crypto": r"crypto|bitcoin|ethereum",
    "Real estate": r"real estate|mortgage|house prices|rent prices", "Gold and commodities": r"\bgold\b|silver|commodit|crude oil",
    "Electric cars": r"electric (?:car|vehicle)|\bevs?\b|tesla", "Startups": r"startups?\b|venture capital|funding round",
    "Travel deals": r"travel deals?|cheap flights?|fare deals?|points and miles", "Credit cards": r"credit cards?|card rewards|sign-?up bonus",
    "Taxes": r"\btax(?:es)?\b|irs\b|401k|roth", "Fitness": r"workout|fitness|running|marathon",
    "Cooking": r"recipes?\b|cooking", "Restaurants": r"restaurants?\b|michelin", "Gaming": r"video games?|playstation|xbox|nintendo",
    "Photography": r"photography|camera lens", "Space": r"spacex|nasa|rocket launch", "Interest rates": r"interest rates?|the fed\b|rate cut",
    "India": r"\bindia\b|kerala|mumbai|delhi", "Peru": r"\bperu\b|lima\b|cusco",
    "Jobs and careers": r"job market|layoffs?|salary|interview prep", "Parenting": r"parenting|school admission|kids? activities",
}
PARTS = {"news": r"news|headline|latest on|what happened", "finance": r"stock|market|invest|etf|signal|portfolio|earnings",
         "events": r"event|concert|show\b|tickets?|game tonight|things to do"}


def interests(memory: Memory) -> list[dict]:
    try:
        from .trends import Trends  # noqa: PLC0415
        return Trends(memory).topics()
    except Exception:
        log.info("daybrief: no interests", exc_info=True)
        return []


def news(memory: Memory, now: datetime | None = None) -> list[dict]:
    """[{topic, headline, stories: [{title, url}]}] from each interest's latest digest (two days old at most)."""
    now = now or datetime.now(timezone.utc)
    out = []
    for t in interests(memory):
        try:
            b = json.loads(memory.get(f"trends:brief:{t['name']}") or "null")
        except ValueError:
            b = None
        if not b or not b.get("at") or now - datetime.fromisoformat(b["at"]) > timedelta(days=2):
            continue
        stories = [{"title": str(s.get("title", ""))[:160], "url": s.get("url") or ""}
                   for s in (b.get("stories") or [])[:2] if s.get("title")]
        if b.get("headline") or stories:
            out.append({"topic": t["name"], "headline": str(b.get("headline") or "")[:300], "stories": stories})
    return out[:6]


def finance(memory: Memory) -> dict:
    """{"headlines": [{title, url}], "attention": ["NVDA (8 posts, 4x usual)"]} from last night's read."""
    out = {"headlines": [], "attention": []}
    try:
        from . import pricebook, signals  # noqa: PLC0415
        signals._ready(memory)
        day = pricebook.run(memory, "SELECT MAX(day) AS d FROM signals WHERE source = 'finnhub'")[0]["d"]
        if day and (local_now().date() - day).days <= 2:
            out["headlines"] = [{"title": r["title"][:160], "url": r["url"]} for r in pricebook.run(
                memory, "SELECT title, url FROM signals WHERE source = 'finnhub' AND day = ? ORDER BY place LIMIT 3",
                (day,))]
        out["attention"] = [f"{t['key']} ({t['mentions']} posts" + (f", {t['times_usual']:g}x usual)" if t["times_usual"]
                                                                   else ")")
                            for t in signals.trending(memory, kind="ticker", days=1, limit=4) if t["mentions"] >= 3]
    except Exception:
        log.info("daybrief: finance news failed", exc_info=True)
    return out


def new_events(memory: Memory, env=None, fetch=None, today: str = "") -> list[dict]:
    """Events for the owner's interests in their city that weren't shown before. Looked up once a day."""
    from . import events  # noqa: PLC0415
    today = today or local_now().date().isoformat()
    if memory.get(DAY_KEY) == today:
        return json.loads(memory.get(DAY_KEY + ":found") or "[]")
    memory.set(DAY_KEY, today)
    seen = set(json.loads(memory.get(SEEN_KEY) or "[]"))
    found = []
    where = events.city(memory)
    for t in interests(memory)[:MAX_TOPICS] if where else []:
        try:
            got = events.search_events(memory, t["name"], where, 45, env, fetch)
        except Exception as e:
            log.info("daybrief: events for %s: %s", t["name"], e)
            continue
        for e in got.get("events") or []:
            key = f"{e['name']}|{e['date']}"
            if key not in seen and e.get("name"):
                seen.add(key)
                found.append({**e, "interest": t["name"]})
    memory.set(SEEN_KEY, json.dumps(sorted(seen)[-400:]))
    memory.set(DAY_KEY + ":found", json.dumps(found[:5]))
    return found[:5]


# --- learning what the owner cares about, from what they say ---------------------------------------------------
def said(memory: Memory, days: int = WINDOW_DAYS, now: datetime | None = None) -> list[tuple[str, str]]:
    """[(day, sentence)]: the owner's own sentences to Buddy (kept 30 days for the English tutor)."""
    from . import tutor  # noqa: PLC0415
    memory.db.execute(tutor.SCHEMA)
    since = ((now or datetime.now(timezone.utc)) - timedelta(days=days)).isoformat()
    return [(r["at"][:10], r["text"]) for r in memory.db.execute(
        "SELECT at, text FROM utterances WHERE at >= ? AND via != 'room' ORDER BY at", (since,))]


def notice(memory: Memory, now: datetime | None = None) -> dict:
    """What they've been bringing up: {"subjects": {name: days}, "tickers": {symbol: days}, "parts": {part: n}}."""
    import re  # noqa: PLC0415
    from . import signals  # noqa: PLC0415
    known = signals._universe(memory)
    subjects, tickers, parts = {}, {}, {}
    for day, text in said(memory, now=now):
        for name, rx in SUBJECTS.items():
            if re.search(rx, text, re.I):
                subjects.setdefault(name, set()).add(day)
        for sym in signals.tickers_in(text, known):
            tickers.setdefault(sym, set()).add(day)
        for part, rx in PARTS.items():
            if re.search(rx, text, re.I):
                parts[part] = parts.get(part, 0) + 1
    return {"subjects": {k: len(v) for k, v in subjects.items()}, "tickers": {k: len(v) for k, v in tickers.items()},
            "parts": parts}


def learn(memory: Memory, now: datetime | None = None) -> list[str]:
    """Once a day: update what the briefing follows from what the owner has been saying. Returns what changed,
    in plain words, for the briefing to say."""
    from .trends import Trends  # noqa: PLC0415
    now = now or datetime.now(timezone.utc)
    today = now.date().isoformat()
    if memory.get(LEARN_DAY_KEY) == today:
        return []
    memory.set(LEARN_DAY_KEY, today)
    seen = notice(memory, now)
    focus = json.loads(memory.get(FOCUS_KEY) or "{}")
    auto = focus.get("auto") or {}  # subjects Buddy added itself: {name: {"added", "last_seen"}}
    trends, changed = Trends(memory), []
    followed = {t["name"].lower() for t in trends.topics()}
    for name in list(auto):
        if seen["subjects"].get(name):
            auto[name]["last_seen"] = today
        elif (now.date() - datetime.fromisoformat(auto[name]["last_seen"]).date()).days >= DROP_AFTER_DAYS:
            trends.unwatch(name)
            del auto[name]
            changed.append(f"I've stopped following {name} in your news: you haven't brought it up for a month")
    added_this_week = any((now.date() - datetime.fromisoformat(a["added"]).date()).days < 7 for a in auto.values())
    ready = sorted(((d, n) for n, d in seen["subjects"].items() if d >= ADD_AFTER_DAYS and n.lower() not in followed),
                   reverse=True)
    if ready and not added_this_week and len(auto) < MAX_AUTO:
        days, name = ready[0]
        trends.watch(name)
        auto[name] = {"added": today, "last_seen": today}
        changed.append(f"I've added {name} to your news because you brought it up on {days} days these two weeks "
                       f"(say 'stop following {name}' to drop it)")
    order = sorted(PARTS, key=lambda p: -seen["parts"].get(p, 0))
    memory.set(FOCUS_KEY, json.dumps({
        "auto": auto, "order": order, "on": today,
        "tickers": [s for s, d in sorted(seen["tickers"].items(), key=lambda kv: -kv[1]) if d >= TICKER_DAYS][:3]}))
    return changed


def focus(memory: Memory) -> dict:
    try:
        return json.loads(memory.get(FOCUS_KEY) or "{}")
    except ValueError:
        return {}


def asked_about(memory: Memory, env=None, fetch=None) -> list[dict]:
    """The latest headline for each symbol the owner keeps asking about (Finnhub, when its key is set)."""
    out = []
    try:
        from . import finnhub  # noqa: PLC0415
        if not finnhub.configured(env):
            return out
        for sym in focus(memory).get("tickers") or []:
            got = finnhub.company_news(memory, sym, 2, env, fetch)
            if got:
                out.append({"symbol": sym, "title": got[0]["title"], "url": got[0]["url"]})
    except Exception:
        log.info("daybrief: headlines for asked-about symbols failed", exc_info=True)
    return out


def facts(memory: Memory, env=None, fetch=None) -> dict:
    """{"say": the facts for the briefing's writer, "shown": the same as a formatted section for the screen}."""
    try:
        changed = learn(memory)
    except Exception:
        log.info("daybrief: learning failed", exc_info=True)
        changed = []
    n, f = news(memory), finance(memory)
    mine = asked_about(memory, env, fetch)
    try:
        ev = new_events(memory, env, fetch)
    except Exception:
        log.info("daybrief: events failed", exc_info=True)
        ev = []
    part: dict[str, tuple[list, list]] = {p: ([], []) for p in PARTS}
    if n:
        say, shown = part["news"]
        say.append("News on their interests: " + " | ".join(
            f"{x['topic']}: {x['headline'] or x['stories'][0]['title']}" for x in n))
        shown.append("**News on your interests**")
        for x in n:
            shown.append(f"- **{x['topic']}**: {x['headline'] or x['stories'][0]['title']}")
            shown += [f"  - {s['title']}" + (f" {s['url']}" if str(s["url"]).startswith("https://") else "")
                      for s in x["stories"]]
    if f["headlines"] or f["attention"] or mine:
        say, shown = part["finance"]
        say.append("Finance: " + " | ".join(h["title"] for h in f["headlines"])
                   + ("; on the symbols they keep asking about: " + " | ".join(f"{m['symbol']}: {m['title']}"
                                                                              for m in mine) if mine else "")
                   + (f". Getting unusual attention on Reddit (attention, not advice): {', '.join(f['attention'])}"
                      if f["attention"] else ""))
        shown.append("**Finance**")
        shown += [f"- **{m['symbol']}** (you've been asking about it): {m['title']}"
                  + (f" {m['url']}" if str(m["url"]).startswith("https://") else "") for m in mine]
        shown += [f"- {h['title']}" + (f" {h['url']}" if str(h["url"]).startswith("https://") else "")
                  for h in f["headlines"]]
        if f["attention"]:
            shown.append(f"- Most talked about last night: {', '.join(f['attention'])} (attention, not advice)")
    if ev:
        say, shown = part["events"]
        say.append("New events near them for their interests: " + " | ".join(
            f"{e['name']} on {e['date']} at {e['venue']}" for e in ev[:3]))
        shown.append("**New events near you**")
        shown += [f"- {e['name']} · {e['date']}" + (f" {e['time']}" if e["time"] else "")
                  + (f" · {e['venue']}" if e["venue"] else "") + (f" · {e['price']}" if e["price"] else "")
                  + (f" {e['url']}" if str(e["url"]).startswith("https://") else "") for e in ev]
    order = [p for p in (focus(memory).get("order") or []) if p in part] or list(PARTS)
    say = [s for p in order for s in part[p][0]]  # what they talk about most comes first
    shown = [s for p in order for s in part[p][1]]
    if changed:
        say.append("Tell them what changed in their briefing: " + "; ".join(changed))
        shown += ["**What I changed**", *[f"- {c}" for c in changed]]
    return {"say": " ".join(say), "shown": "\n".join(shown)}
