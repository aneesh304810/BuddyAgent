"""News desk: turns the headlines Buddy collects into stories, says how far each can be trusted, and asks a paid
news source only when a story that matters is still unclear.

  1  Stories        the same event reported by several outlets is one story, not several. Headlines about a company
                    within three days of each other that share most of their meaningful words are grouped.
  2  Source rating  every outlet is rated by a list kept in this file: established news outlets and regulators (3),
                    press-release wires and specialist sites (2: first-hand but written by the company, or narrow),
                    anything else (1). A story takes its best source's rating.
  3  Unclear?       a story is unclear when only unrated outlets carry it, when its outlets disagree on whether it
                    is good or bad, or when it has no good-or-bad label at all.
  4  Matters?       only for a company Buddy is acting on: an open paper trade (the Signals book or the strategy
                    lab), the recommended watchlist, or your own watchlist.
  5  A free model   a story that is both unclear and matters is first put to the free AI model used for labelling
                    headlines. If it can settle the story from the headlines alone, nothing else is asked.
  6  Fact check     only a story the free model could not settle goes to a news service (NewsData.io, Perigon,
                    NewsCatcher: whichever have a key, tried in that order) to see who else reports it. With no
                    key set, the story is written down as "would have asked". Calls stop at the month's limit (30
                    unless you change it), and each is logged with why it was made and what came back.

Nothing about you goes anywhere: the free model sees public headlines, and a fact check sends a company's name.
The good-or-bad labels come from themes.py and are sometimes wrong, which is one reason a story with a single weak
source is treated as unclear.
"""

import hashlib
import logging
import re
import urllib.parse
from datetime import date, timedelta

from .memory import Memory
from .tools import ToolError, local_now

log = logging.getLogger("buddy.newsdesk")

CAP_KEY, DAY_KEY = "newsdesk:paid_cap", "newsdesk:day"
PROVIDERS = (("newsdata", "NewsData.io", "BUDDY_NEWSDATA_KEY"), ("perigon", "Perigon", "BUDDY_PERIGON_KEY"),
             ("newscatcher", "NewsCatcher", "BUDDY_NEWSCATCHER_KEY"))
SETTLED = "settled by the free model"
JUDGE = """These are news headlines about {company} that seem to describe one event. Outlets: {outlets}.
{headlines}
From the headlines alone, is it clear what happened and whether it is good or bad for the company's business?
Answer "clear": false if the event could be a rumour, the headlines conflict, or you cannot tell.
Reply with JSON only: {{"clear": true or false, "tone": "good", "bad" or "neutral", "reason": "one short sentence"}}"""
DEFAULT_CAP, SAME_STORY, WINDOW_DAYS, LOOK_BACK = 30, 0.5, 3, 14
ESTABLISHED = {  # rated 3: established news outlets, wire services and regulators
    "reuters.com", "apnews.com", "bloomberg.com", "wsj.com", "ft.com", "nytimes.com", "washingtonpost.com", "cnbc.com",
    "bbc.com", "bbc.co.uk", "economist.com", "barrons.com", "marketwatch.com", "forbes.com", "fortune.com",
    "theguardian.com", "npr.org", "axios.com", "politico.com", "sec.gov", "federalreserve.gov", "energy.gov",
    "nasa.gov", "nih.gov", "fda.gov", "nature.com", "science.org", "technologyreview.com", "spectrum.ieee.org",
    "arstechnica.com", "theverge.com", "techcrunch.com", "wired.com", "nikkei.com", "asia.nikkei.com",
}
FIRST_HAND = {  # rated 2: press-release wires (the company's own words) and specialist or aggregator sites
    "businesswire.com", "prnewswire.com", "globenewswire.com", "accesswire.com", "newswire.com", "finance.yahoo.com",
    "seekingalpha.com", "fool.com", "investopedia.com", "benzinga.com", "thestreet.com", "investing.com",
    "zacks.com", "morningstar.com", "spacenews.com", "utilitydive.com", "world-nuclear-news.org", "fiercebiotech.com",
    "statnews.com", "electrek.co", "cnet.com", "zdnet.com", "venturebeat.com",
}
RATING_WORDS = {3: "an established outlet", 2: "a press release or specialist site", 1: "an outlet Buddy has not rated"}
STOP = set("a an the and or of for to in on at by with from as is are was be its it this that after amid over new says "
           "said will up down shares stock stocks inc corp co ltd company".split())
SCHEMA = ["CREATE SEQUENCE IF NOT EXISTS news_check_id",
          """CREATE TABLE IF NOT EXISTS news_checks (id BIGINT PRIMARY KEY DEFAULT nextval('news_check_id'), day DATE,
    symbol VARCHAR, story VARCHAR, why VARCHAR, asked BOOLEAN, result VARCHAR, UNIQUE (symbol, story))"""]


def _run(memory: Memory, sql: str, params: tuple = ()) -> list[dict]:
    from . import pricebook  # noqa: PLC0415
    return pricebook.run(memory, sql, params)


def _ready(memory: Memory) -> None:
    for statement in SCHEMA:
        _run(memory, statement)


def rating(outlet: str) -> int:
    """3, 2 or 1 for an outlet's site name (with or without www, and for its sub-sites)."""
    host = re.sub(r"^(?:https?://)?(?:www\.)?", "", str(outlet or "").lower()).split("/")[0]
    for level, names in ((3, ESTABLISHED), (2, FIRST_HAND)):
        if any(host == n or host.endswith("." + n) for n in names):
            return level
    return 1


def _words(title: str, company: str = "") -> set:
    skip = STOP | set(re.findall(r"[a-z0-9]+", company.lower()))
    return {w for w in re.findall(r"[a-z0-9]+", title.lower()) if w not in skip and len(w) > 2}


def group(rows: list[dict], company: str = "") -> list[dict]:
    """Headlines -> stories. rows: {"title", "day", "outlet", "tone", "url"}. Newest story first."""
    stories: list[dict] = []
    for r in sorted(rows, key=lambda r: r["day"]):
        words = _words(r["title"], company)
        home = None
        for s in stories:
            near = abs((date.fromisoformat(r["day"]) - date.fromisoformat(s["last_day"])).days) <= WINDOW_DAYS
            shared = len(words & s["words"]) / max(1, min(len(words), len(s["words"])))
            if near and words and shared >= SAME_STORY:
                home = s
                break
        if home is None:
            home = {"title": r["title"], "day": r["day"], "last_day": r["day"], "words": set(words), "headlines": []}
            stories.append(home)
        home["headlines"].append(r)
        home["words"] |= words
        home["last_day"] = max(home["last_day"], r["day"])
    out = []
    for s in stories:
        outlets = list(dict.fromkeys(str(h.get("outlet") or "") for h in s["headlines"] if h.get("outlet")))
        tones = [h["tone"] for h in s["headlines"] if h.get("tone") in ("good", "bad", "neutral")]
        sides = {t for t in tones if t != "neutral"}
        best = max((rating(o) for o in outlets), default=1)
        tone = ("mixed" if len(sides) > 1 else next(iter(sides)) if sides else "neutral" if tones else "")
        why = []
        if best == 1:
            why.append("only outlets Buddy has not rated carry it")
        if tone == "mixed":
            why.append("its outlets disagree on whether it is good or bad")
        if not tones:
            why.append("it has no good-or-bad label yet")
        out.append({"title": s["title"], "day": s["day"], "reports": len(s["headlines"]), "outlets": outlets[:6],
                    "titles": list(dict.fromkeys(h["title"] for h in s["headlines"]))[:6],
                    "rating": best, "rated": RATING_WORDS[best], "tone": tone, "unclear": bool(why), "why_unclear": why,
                    "url": next((h["url"] for h in s["headlines"] if h.get("url")), "")})
    return sorted(out, key=lambda s: s["day"], reverse=True)


def stories(memory: Memory, symbol: str, days: int = LOOK_BACK, today: date | None = None) -> list[dict]:
    """A company's stories from the headlines found by its name (themes.company_news), newest first."""
    today = today or local_now().date()
    try:
        rows = _run(memory, "SELECT n.title, CAST(n.day AS VARCHAR) AS day, n.outlet, n.url, n.company, t.tone FROM "
                            "theme_news n LEFT JOIN theme_tone t ON t.key = n.key WHERE n.symbol = ? AND n.day >= ?",
                    (symbol.upper(), (today - timedelta(days=days)).isoformat()))
    except Exception:
        return []
    return group(rows, rows[0]["company"] if rows else "")


def counts(memory: Memory, symbol: str, days: int = LOOK_BACK, today: date | None = None) -> dict:
    """Good and bad STORIES (not headlines) for a company. Every story counts, whatever its source rating: the
    list of rated outlets is short, so dropping unrated ones would throw away most small-company news. The rating
    marks a story as unclear instead."""
    got = stories(memory, symbol, days, today)
    return {"good": sum(1 for s in got if s["tone"] == "good"), "bad": sum(1 for s in got if s["tone"] == "bad"),
            "stories": len(got)}


def acting_on(memory: Memory) -> dict[str, str]:
    """{symbol: why it matters}: open paper trades first, then the recommended watchlist, then your own."""
    out: dict[str, str] = {}
    for sql, why in (("SELECT DISTINCT symbol FROM lab_trades WHERE status = 'open' AND NOT COALESCE(backfilled, FALSE)",
                      "an open strategy-lab trade"),
                     ("SELECT DISTINCT symbol FROM sim_trades WHERE status = 'open'", "an open Signals trade")):
        try:
            for r in _run(memory, sql):
                out.setdefault(r["symbol"], why)
        except Exception:
            pass
    try:
        from . import watchpicks  # noqa: PLC0415
        for s in sorted(watchpicks.symbols(memory)):
            out.setdefault(s, "on the recommended watchlist")
    except Exception:
        pass
    try:
        for r in memory.db.execute("SELECT symbol FROM mkt_watch WHERE market = 'us'"):
            out.setdefault(r["symbol"], "on your watchlist")
    except Exception:
        pass
    return out


# --- the paid slot ----------------------------------------------------------------------------------------------
def cap(memory: Memory) -> int:
    try:
        return max(0, int(memory.get(CAP_KEY) or DEFAULT_CAP))
    except ValueError:
        return DEFAULT_CAP


def set_cap(memory: Memory, calls: int) -> int:
    memory.set(CAP_KEY, str(max(0, min(int(calls), 1000))))
    return cap(memory)


def configured(env=None) -> list[tuple]:
    """The news services that have a key, in the order they are tried."""
    from . import gateway  # noqa: PLC0415
    return [p for p in PROVIDERS if gateway._secret(env, p[2])]


def provider(env=None) -> bool:
    return bool(configured(env))


def _company(memory: Memory, symbol: str) -> str:
    try:
        got = _run(memory, "SELECT company FROM theme_news WHERE symbol = ? AND company <> '' LIMIT 1", (symbol.upper(),))
    except Exception:
        got = []
    name = re.sub(r"\b(inc|corp|corporation|co|ltd|plc|holdings|group|company)\b\.?", "", got[0]["company"] if got else symbol,
                  flags=re.I)
    return " ".join(name.replace(",", " ").split()) or symbol


def _judge(memory: Memory, company: str, story: dict) -> dict | None:
    """The free model's reading of one story: {"clear", "tone", "reason"}, or None when it gave no usable answer.
    Public headlines only, sent as the 'label' job (free models unless the owner changed it)."""
    import asyncio  # noqa: PLC0415

    from . import maestro  # noqa: PLC0415
    prompt = JUDGE.format(company=company, outlets=", ".join(story.get("outlets") or []) or "unknown",
                          headlines="\n".join("- " + " ".join(t.split())[:200] for t in story.get("titles") or [story["title"]]))
    got = asyncio.run(maestro.ask(memory, "label", prompt))
    return got if isinstance(got, dict) and isinstance(got.get("clear"), bool) else None


def _search(memory: Memory, name: str, company: str, today: date, env=None, fetch=None) -> list[dict]:
    """Recent articles naming the company at one service: [{"title", "outlet", "url", "day"}]."""
    from . import gateway  # noqa: PLC0415
    q = f'"{company}"'
    if name == "newsdata":
        key = gateway._secret(env, "BUDDY_NEWSDATA_KEY")
        got = gateway.call(memory, name, "/api/1/latest?" + urllib.parse.urlencode({"q": q, "language": "en", "apikey": key}),
                           env=env, fetch=fetch, purpose="news fact check") or {}
        rows = [(a.get("title"), a.get("source_url") or a.get("source_id"), a.get("link"), a.get("pubDate"))
                for a in got.get("results") or [] if isinstance(a, dict)]
    elif name == "perigon":
        got = gateway.call(memory, name, "/v1/all?" + urllib.parse.urlencode(
            {"q": q, "from": (today - timedelta(days=7)).isoformat(), "sortBy": "date", "size": 25}),
            env=env, fetch=fetch, purpose="news fact check") or {}
        rows = [(a.get("title"), (a.get("source") or {}).get("domain") if isinstance(a.get("source"), dict) else a.get("source"),
                 a.get("url"), a.get("pubDate")) for a in got.get("articles") or [] if isinstance(a, dict)]
    else:
        got = gateway.call(memory, name, "/api/search?" + urllib.parse.urlencode(
            {"q": q, "lang": "en", "from_": "7 days ago", "page_size": 25}), env=env, fetch=fetch,
            purpose="news fact check") or {}
        rows = [(a.get("title"), a.get("domain_url") or a.get("clean_url") or a.get("name_source"), a.get("link"),
                 a.get("published_date")) for a in got.get("articles") or [] if isinstance(a, dict)]
    return [{"title": str(t), "outlet": re.sub(r"^(?:https?://)?(?:www\.)?", "", str(o or "").lower()).split("/")[0],
             "url": str(u or ""), "day": str(d or "")[:10]} for t, o, u, d in rows if t]


def paid_check(memory: Memory, symbol: str, story: dict, env=None, fetch=None, today: date | None = None) -> str:
    """Fact-check one story: who else reports it? Tries each news service that has a key until one answers.
    Reports from rated outlets are kept with the company's headlines, so the story's rating rises by itself."""
    today = today or local_now().date()
    company = _company(memory, symbol)
    words, failed = _words(story["title"], company), []
    for name, title, _ in configured(env):
        try:
            found = _search(memory, name, company, today, env, fetch)
        except Exception as e:
            failed.append(f"{title}: {str(e)[:60] or type(e).__name__}")
            continue
        same = [a for a in found if words and len(words & _words(a["title"], company)) / max(1, min(
            len(words), len(_words(a["title"], company)) or 1)) >= SAME_STORY and a["outlet"] not in (story.get("outlets") or [])]
        rated = sorted((a for a in same if rating(a["outlet"]) > 1), key=lambda a: -rating(a["outlet"]))
        for a in rated[:5]:
            key = "check:" + hashlib.sha256((symbol + a["url"] + a["title"]).encode()).hexdigest()[:24]
            day = a["day"] if re.fullmatch(r"\d{4}-\d\d-\d\d", a["day"]) else today.isoformat()
            _run(memory, "INSERT OR IGNORE INTO theme_news (key, symbol, company, title, url, day, outlet, seen) VALUES "
                         "(?, ?, ?, ?, ?, ?, ?, ?)", (key, symbol.upper(), company, a["title"][:300], a["url"][:500], day,
                                                    a["outlet"], today.isoformat()))
        if rated:
            return (f"confirmed: also reported by {', '.join(dict.fromkeys(a['outlet'] for a in rated[:3]))} "
                    f"({RATING_WORDS[rating(rated[0]['outlet'])]}), found through {title}")
        if same:
            return (f"not confirmed: {len(same)} more report{'s' if len(same) != 1 else ''}, all at outlets Buddy has "
                    f"not rated, found through {title}")
        return f"not confirmed: no other report among {len(found)} recent articles at {title}"
    raise ToolError("; ".join(failed) or "No news service has a key yet.")


def review(memory: Memory, today: date | None = None, env=None, ask=None, judge=None) -> dict:
    """The daily pass, for each company Buddy is acting on. A story that is unclear goes first to the free model;
    only one it cannot settle goes to a news service for a fact check (when one has a key and the month's limit
    allows), or is written down as one it would have asked about."""
    today = today or local_now().date()
    _ready(memory)
    month = today.isoformat()[:7]
    used = _run(memory, "SELECT COUNT(*) AS n FROM news_checks WHERE asked AND CAST(day AS VARCHAR) LIKE ?", (month + "%",))[0]["n"]
    out = {"companies": 0, "stories": 0, "unclear": 0, "settled": 0, "asked": 0, "would_ask": 0}
    for symbol, matters in acting_on(memory).items():
        got = stories(memory, symbol, today=today)
        out["companies"] += 1
        out["stories"] += len(got)
        for s in got:
            if not s["unclear"] or _run(memory, "SELECT 1 FROM news_checks WHERE symbol = ? AND story = ?", (symbol, s["title"][:240])):
                continue
            out["unclear"] += 1
            why = f"{matters}; " + "; ".join(s["why_unclear"])
            try:
                verdict = (judge or _judge)(memory, _company(memory, symbol), s)
            except Exception as e:
                log.info("news desk: the free model failed: %s", type(e).__name__)
                verdict = None
            asked, result = False, ""
            if verdict and verdict.get("clear"):
                result = f"{SETTLED}: {verdict.get('tone') or 'neutral'}; {str(verdict.get('reason') or '')[:160]}"
                out["settled"] += 1
            else:
                said = (f"the free model could not tell ({str(verdict.get('reason') or '')[:100]})" if verdict
                        else "the free model gave no answer")
                why = f"{why}; {said}"
                if not provider(env):
                    result = "not asked: no news service has a key"
                elif used >= cap(memory):
                    result = f"not asked: the month's limit of {cap(memory)} paid checks is reached"
                else:
                    try:
                        result = ask(memory, symbol, s) if ask else paid_check(memory, symbol, s, env, today=today)
                        asked, used = True, used + 1
                    except Exception as e:
                        result = f"not asked: {type(e).__name__}: {str(e)[:120]}"
                out["asked" if asked else "would_ask"] += 1
            _run(memory, "INSERT OR IGNORE INTO news_checks (day, symbol, story, why, asked, result) VALUES (?, ?, ?, ?, ?, ?)",
                 (today.isoformat(), symbol, s["title"][:240], why[:400], asked, str(result)[:300]))
    return out


def due(memory: Memory, now=None) -> bool:
    now = now or local_now()
    return now.hour >= 7 and memory.get(DAY_KEY) != now.date().isoformat()


def view(memory: Memory, today: date | None = None) -> dict:
    """For the Trading tab: the stories behind the companies Buddy is acting on, and the paid slot's record."""
    today = today or local_now().date()
    _ready(memory)
    companies = []
    seen = {(r["symbol"], r["story"]): r["result"] for r in _run(memory, "SELECT symbol, story, result FROM news_checks")}
    for symbol, matters in list(acting_on(memory).items())[:40]:
        got = stories(memory, symbol, today=today)
        for s in got:
            s["check"] = seen.get((symbol, s["title"][:240]), "")
        if got:
            companies.append({"symbol": symbol, "matters": matters, "stories": got[:5],
                              "unclear": sum(1 for s in got if s["unclear"])})
    month = today.isoformat()[:7]
    checks = _run(memory, "SELECT CAST(day AS VARCHAR) AS day, symbol, story, why, asked, result FROM news_checks WHERE "
                          "CAST(day AS VARCHAR) LIKE ? ORDER BY id DESC LIMIT 40", (month + "%",))
    return {"companies": sorted(companies, key=lambda c: -c["unclear"]),
            "paid": {"set_up": provider(), "cap": cap(memory), "asked_this_month": sum(1 for c in checks if c["asked"]),
                     "settled_this_month": sum(1 for c in checks if str(c["result"]).startswith(SETTLED)),
                     "would_have_asked_this_month": sum(1 for c in checks if not c["asked"] and not str(
                         c["result"]).startswith(SETTLED)), "checks": checks[:15],
                     "sources": [{"title": t, "key": k, "set_up": (n, t, k) in configured()} for n, t, k in PROVIDERS]},
            "note": "A story is several outlets reporting the same event. Its rating is its best source's: an established "
                    "outlet, a press release or specialist site, or an outlet Buddy has not rated. A story that is unclear, about "
                    "a company Buddy is acting on, goes first to the free AI model; only one it cannot settle is "
                    "fact-checked with a news service."}
