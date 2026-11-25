"""Topic news from the news services the owner connected, for when Google News (Serper) can't answer.

  NewsCatcher   BUDDY_NEWSCATCHER_KEY
  NewsData.io   BUDDY_NEWSDATA_KEY
  Perigon       BUDDY_PERIGON_KEY
  Marketaux     BUDDY_MARKETAUX_KEY
  GDELT         no key: the last resort, always there

They are tried in that order and the first one that returns headlines answers. Every call goes through Buddy Connect
(gateway.py: the key, rate limits, logging, clear errors). Each one gets the search words only: the same words
Google News would have been sent, with no account, location or other detail added here.
The result says which service answered and why each earlier one didn't, so a reply can say where the news came from.
Everything that comes back is other people's writing: information, never instructions.

A service that answers that it is out of credits, or refuses its key, is left out for an hour (Serper included), so
one that has run out is not asked first on every search. These services have small monthly allowances, and the news
fact check (newsdesk.py) draws on the same ones: every call made here is one it cannot make later that month.
"""

import logging
import re
import time
import urllib.parse
from datetime import date, timedelta

log = logging.getLogger("buddy.newsfeed")

# (catalog name, title, setting with its key; "" for a service that needs none)
SOURCES = (("newscatcher", "NewsCatcher", "BUDDY_NEWSCATCHER_KEY"), ("newsdata", "NewsData.io", "BUDDY_NEWSDATA_KEY"),
           ("perigon", "Perigon", "BUDDY_PERIGON_KEY"), ("marketaux", "Marketaux", "BUDDY_MARKETAUX_KEY"),
           ("gdelt", "GDELT", ""))
TITLES = {name: title for name, title, _ in SOURCES}

# A service out of credits (or refusing its key): skip it for a while instead of failing every news call first.
PAUSE_SECONDS = 60 * 60
_out_until: dict[str, float] = {}
OUT_OF_CREDITS = re.compile(r"credit|quota|insufficient|payment|limit exceeded|unauthori[sz]ed|invalid api key", re.I)
SKIPPED = "said it was out of credits or refused its key within the last hour, so it was skipped"


def connected(env=None) -> list[tuple]:
    """The news services with a key set (GDELT, which needs none, is not counted)."""
    from . import gateway  # noqa: PLC0415
    return [s for s in SOURCES if s[2] and gateway._secret(env, s[2])]


def has_news(env=None) -> bool:
    return bool(connected(env))


def paused(name: str) -> bool:
    return _out_until.get(name, 0.0) > time.time()


def note_failure(name: str, error: Exception) -> None:
    """After a service itself answers that it is out of credits or refuses the key, skip it for an hour.

    Only the service's own answer counts ("... said HTTP 400: Not enough credits"). Buddy Connect's own refusals (its
    per-minute limit, a service the owner switched off) and a service that could not be reached pause nothing: the
    first passes within a minute, the second is the owner's choice, the third may work on the next try."""
    if "said HTTP" not in str(error):
        return
    status = getattr(error, "status", 0) or 0
    if status in (401, 402, 403, 429) or OUT_OF_CREDITS.search(str(error)):
        _out_until[name] = time.time() + PAUSE_SECONDS
    if name == "gdelt" and status == 429:  # the intelligence sources ask it too: they leave it alone as well
        from . import bip_sources  # noqa: PLC0415
        bip_sources.rest("gdelt")


def serper_paused() -> bool:
    return paused("serper")


def note_serper_failure(error: Exception) -> None:
    note_failure("serper", error)


def _gdelt_turn() -> bool:
    """True when GDELT may be asked now (and counts this as the asking); False within five seconds of the last
    asking by anyone on this server (the intelligence sources ask it too, on the same clock: bip_sources.turn),
    and while it is resting after saying "too many requests"."""
    from . import bip_sources  # noqa: PLC0415
    return bip_sources.turn("gdelt")


def reset() -> None:
    from . import bip_sources  # noqa: PLC0415
    _out_until.clear()
    bip_sources._last.pop("gdelt", None)
    bip_sources._rest.clear()
    bip_sources._strikes.clear()


def _plain(query: str) -> str:
    return " ".join(str(query or "").replace('"', " ").split())[:100]


def newscatcher_path(q: str, days: int, num: int) -> str:
    """A search of NewsCatcher's News API: the best `num` recent articles for plain words, the last `days` days
    (it keeps 30)."""
    return "/news-api/search?" + urllib.parse.urlencode(
        {"q": q, "limit": max(1, min(int(num), 100)), "search_window": f"{max(1, min(int(days), 30))}d",
         "languages": "en", "sort_by": "published_at"})


def newscatcher_rows(got) -> list[tuple]:
    """(title, outlet, link, date) from a NewsCatcher answer: the News API's "results", or the v3 API's "articles"."""
    if not isinstance(got, dict):
        return []
    items = got.get("results") if isinstance(got.get("results"), list) else got.get("articles") or []
    rows = []
    for a in items:
        if not isinstance(a, dict):
            continue
        source = a.get("source")
        outlet = (source.get("domain") or source.get("name")) if isinstance(source, dict) else source
        rows.append((a.get("title"), outlet or a.get("name_source") or a.get("domain_url") or a.get("clean_url"),
                     a.get("url") or a.get("link"), a.get("published_at") or a.get("published_date")))
    return rows


def _ask(memory, name: str, query: str, days: int, num: int, env=None, fetch=None) -> list[dict]:
    """One service's recent articles for a query: [{"title", "source", "date", "link"}]."""
    from . import gateway  # noqa: PLC0415
    q = _plain(query)
    since = (date.today() - timedelta(days=days)).isoformat()
    common = {"env": env, "fetch": fetch, "purpose": "news search"}
    if name == "newscatcher":
        rows = newscatcher_rows(gateway.call(memory, name, newscatcher_path(q, days, num), **common) or {})
    elif name == "newsdata":
        got = gateway.call(memory, name, "/api/1/latest?" + urllib.parse.urlencode(
            {"q": q, "language": "en", "apikey": gateway._secret(env, "BUDDY_NEWSDATA_KEY")}), **common) or {}
        rows = [(a.get("title"), a.get("source_name") or a.get("source_id") or a.get("source_url"), a.get("link"),
                 a.get("pubDate")) for a in got.get("results") or [] if isinstance(a, dict)]
    elif name == "perigon":
        got = gateway.call(memory, name, "/v1/all?" + urllib.parse.urlencode(
            {"q": q, "from": since, "sortBy": "date", "size": num}), **common) or {}
        rows = [(a.get("title"), (a.get("source") or {}).get("domain") if isinstance(a.get("source"), dict)
                 else a.get("source"), a.get("url"), a.get("pubDate")) for a in got.get("articles") or [] if isinstance(a, dict)]
    elif name == "marketaux":
        got = gateway.call(memory, name, "/v1/news/all?" + urllib.parse.urlencode(
            {"search": q, "language": "en", "published_after": since, "limit": min(num, 3),
             "api_token": gateway._secret(env, "BUDDY_MARKETAUX_KEY")}), **common) or {}
        rows = [(a.get("title"), a.get("source"), a.get("url"), a.get("published_at"))
                for a in got.get("data") or [] if isinstance(a, dict)]
    elif name == "gdelt":
        gq = " ".join(re.sub(r"[^\w\s&.-]", " ", q).split())
        if re.search(r"\sOR\s", gq):  # GDELT wants OR'd words in parentheses
            gq = f"({gq})"
        got = gateway.call(memory, name, "/api/v2/doc/doc?" + urllib.parse.urlencode(
            {"query": f"{gq} sourcelang:english", "mode": "artlist", "format": "json", "maxrecords": num,
             "sort": "datedesc", "timespan": f"{days}d"}), **common) or {}
        rows = [(a.get("title"), a.get("domain"), a.get("url"),
                 f"{str(a.get('seendate') or '')[:4]}-{str(a.get('seendate') or '')[4:6]}-{str(a.get('seendate') or '')[6:8]}"
                 if a.get("seendate") else "") for a in (got.get("articles") if isinstance(got, dict) else None) or []
                if isinstance(a, dict)]
    else:
        raise ValueError(name)
    return [{"title": " ".join(str(t).split())[:300], "source": str(s or ""), "date": str(d or "")[:10],
             "link": str(u or "")} for t, s, u, d in rows if t][:num]


def search(query: str, memory=None, env=None, fetch=None, days: int = 3, num: int = 10, keyless: bool = True) -> dict:
    """Headlines for a topic from the first connected news service that has some.

    {"news": [...], "answered_by": "NewsCatcher" or "", "service": its catalog name, "tried": [{"source", "error"}]}.
    Never raises: when nothing answers, "news" is empty and "tried" says why each one didn't."""
    from . import gateway  # noqa: PLC0415
    if not _plain(query):
        return {"news": [], "answered_by": "", "service": "", "tried": []}
    days, num = max(1, min(int(days or 3), 30)), max(1, min(int(num or 10), 25))
    tried = []
    for name, title, key in SOURCES:
        if key and not gateway._secret(env, key):
            continue
        if not key and not keyless:
            continue
        if paused(name):
            tried.append({"source": title, "error": SKIPPED})
            continue
        if name == "gdelt" and not _gdelt_turn():
            tried.append({"source": title, "error": "asked a moment ago; it allows one search every five seconds"})
            continue
        try:
            got = _ask(memory, name, query, days, num, env, fetch)
        except Exception as e:  # one service failing is a reason to try the next, not to stop
            note_failure(name, e)
            reason = str(e) if isinstance(e, gateway.ConnectError) else f"{type(e).__name__}: {e}"
            tried.append({"source": title, "error": " ".join(reason.split())[:200]})
            log.info("news via %s failed: %s", name, type(e).__name__)
            continue
        if got:
            return {"news": got, "answered_by": title, "service": name, "tried": tried}
        tried.append({"source": title, "error": "no headlines for this search"})
    return {"news": [], "answered_by": "", "service": "", "tried": tried}
