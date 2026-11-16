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
  5  The paid slot  a story that is both unclear and matters is a candidate for a paid check. No provider is built
                    in: until you choose one and set its key, every candidate is written down as "would have
                    asked", so you can see how many paid calls a month this would make before paying for anything.
                    With a provider set, calls stop at the month's limit (30 unless you change it), and each is
                    logged with why it was made and whether it confirmed, contradicted or added nothing.

No AI model is used here and nothing about you goes anywhere: a paid check would send a company's name and the
headline. The good-or-bad labels come from themes.py and are sometimes wrong, which is one reason a story with a
single weak source is treated as unclear.
"""

import json
import logging
import re
from datetime import date, timedelta

from .memory import Memory
from .tools import ToolError, local_now

log = logging.getLogger("buddy.newsdesk")

CAP_KEY, DAY_KEY, PROVIDER_KEY = "newsdesk:paid_cap", "newsdesk:day", "BUDDY_PAID_NEWS_KEY"
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


def provider(env=None) -> bool:
    from . import gateway  # noqa: PLC0415
    return bool(gateway._secret(env, PROVIDER_KEY))


def paid_check(memory: Memory, symbol: str, story: dict, env=None, fetch=None) -> str:
    """Ask the paid source about one story. No provider is wired in yet: each has its own format, and it is added
    here once the owner has chosen one."""
    raise ToolError("No paid news source has been chosen yet.")


def review(memory: Memory, today: date | None = None, env=None, ask=None) -> dict:
    """The daily pass: for each company Buddy is acting on, find stories that are unclear, and either ask the paid
    source (when one is set and the month's limit allows) or write the story down as one it would have asked about."""
    today = today or local_now().date()
    _ready(memory)
    month = today.isoformat()[:7]
    used = _run(memory, "SELECT COUNT(*) AS n FROM news_checks WHERE asked AND CAST(day AS VARCHAR) LIKE ?", (month + "%",))[0]["n"]
    out = {"companies": 0, "stories": 0, "unclear": 0, "asked": 0, "would_ask": 0}
    for symbol, matters in acting_on(memory).items():
        got = stories(memory, symbol, today=today)
        out["companies"] += 1
        out["stories"] += len(got)
        for s in got:
            if not s["unclear"] or _run(memory, "SELECT 1 FROM news_checks WHERE symbol = ? AND story = ?", (symbol, s["title"][:240])):
                continue
            out["unclear"] += 1
            why = f"{matters}; " + "; ".join(s["why_unclear"])
            asked, result = False, "not asked: no paid news source is set up"
            if provider(env) and used >= cap(memory):
                result = f"not asked: the month's limit of {cap(memory)} paid checks is reached"
            elif provider(env):
                try:
                    result, asked = (ask or paid_check)(memory, symbol, s), True
                    used += 1
                except Exception as e:
                    result = f"not asked: {type(e).__name__}: {str(e)[:120]}"
            out["asked" if asked else "would_ask"] += 1
            _run(memory, "INSERT OR IGNORE INTO news_checks (day, symbol, story, why, asked, result) VALUES (?, ?, ?, ?, ?, ?)",
                 (today.isoformat(), symbol, s["title"][:240], why[:300], asked, str(result)[:300]))
    return out


def due(memory: Memory, now=None) -> bool:
    now = now or local_now()
    return now.hour >= 7 and memory.get(DAY_KEY) != now.date().isoformat()


def view(memory: Memory, today: date | None = None) -> dict:
    """For the Trading tab: the stories behind the companies Buddy is acting on, and the paid slot's record."""
    today = today or local_now().date()
    _ready(memory)
    companies = []
    for symbol, matters in list(acting_on(memory).items())[:40]:
        got = stories(memory, symbol, today=today)
        if got:
            companies.append({"symbol": symbol, "matters": matters, "stories": got[:5],
                              "unclear": sum(1 for s in got if s["unclear"])})
    month = today.isoformat()[:7]
    checks = _run(memory, "SELECT CAST(day AS VARCHAR) AS day, symbol, story, why, asked, result FROM news_checks WHERE "
                          "CAST(day AS VARCHAR) LIKE ? ORDER BY id DESC LIMIT 40", (month + "%",))
    return {"companies": sorted(companies, key=lambda c: -c["unclear"]),
            "paid": {"set_up": provider(), "cap": cap(memory), "asked_this_month": sum(1 for c in checks if c["asked"]),
                     "would_have_asked_this_month": sum(1 for c in checks if not c["asked"]), "checks": checks[:15],
                     "key": PROVIDER_KEY},
            "note": "A story is several outlets reporting the same event. Its rating is its best source's: an established "
                    "outlet, a press release or specialist site, or an outlet Buddy has not rated. A paid check is only "
                    "considered for a story that is unclear and about a company Buddy is acting on."}
