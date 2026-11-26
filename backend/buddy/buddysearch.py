"""Buddy Search: one search brain over many providers, so Buddy depends on no single search company.

  search("best italian restaurant in hoboken")
    -> what's asked (code, not a model): places, in "Hoboken"
    -> which providers: Google Places and OpenStreetMap, in parallel
    -> one result format, with where each came from, when, and its licence
    -> duplicates merged (a place in both is one place, "found by 2"), ranked (reciprocal rank fusion, then seen
       in more than one source, official sources, freshness for news)
    -> cached as each provider's terms allow; the search itself kept in the owner's history

Six engines, each with its providers in order (a provider without its key, over its daily limit or failing is
skipped and the next one used):
- web: Brave Search and Tavily; Serper (Google results) when neither answers
- local: Google Places and OpenStreetMap (Nominatim and Overpass); Serper's Google Maps results without Places
- products: Google Shopping (Serper), Best Buy, eBay, and Buddy's own price book; Brave for the web around them
- news: Brave News and the connected news services (newsfeed.py)
- research: Tavily, Europe PMC, OpenAlex, arXiv, and the papers Buddy already keeps (paperlib.py)
- internal: the owner's own documents, facts and notes (brain.py), their connected apps (email, messages,
  calendar) and their paper library; never sent anywhere

Privacy: providers get the search words and, for places, an area name (never a position). A search of the owner's
own things never leaves the server. A summary of web results may use any model in Maestro (public text only, the
Privacy Gateway checks); a summary that includes the owner's own things uses Claude only.

Places are ranked with their reviews too: the rating pulled towards an ordinary 4.2 when few people gave it
(reviews.review_score), so a 4.6 from 5,000 beats a 5.0 from 4. Reviews of one result: reviews.py, on request.

Storage, by each provider's terms (POLICY): a link and title are kept for every result; the rest only where the
terms allow, else held in a short cache. Google Places: only the place ID is kept, nothing cached (Google's terms); Brave: links
unless the owner's plan includes storage rights (BUDDY_BRAVE_STORAGE_RIGHTS=1); OpenStreetMap: kept, with its
attribution (ODbL). The owner's searches are kept until they delete them.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import re
import time
import urllib.parse
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from . import gateway
from .memory import Memory
from .tools import ToolError, local_now

ENGINES = {"web": "Web", "local": "Places", "products": "Products", "news": "News", "research": "Research",
           "internal": "Your own things"}
SCHEMA = """
CREATE TABLE IF NOT EXISTS search_cache (         -- providers' answers, held only as long as their terms allow
    key TEXT PRIMARY KEY, provider TEXT NOT NULL, created REAL NOT NULL, expires REAL NOT NULL, payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS search_kept (          -- what may be kept of each result, by its provider's terms
    id TEXT PRIMARY KEY, provider TEXT NOT NULL, engine TEXT NOT NULL, title TEXT NOT NULL DEFAULT '',
    url TEXT NOT NULL DEFAULT '', source_id TEXT NOT NULL DEFAULT '', detail TEXT NOT NULL DEFAULT '{}',
    licence TEXT NOT NULL DEFAULT '', first_seen TEXT NOT NULL, last_seen TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS search_history (       -- the owner's searches, until they delete them
    id INTEGER PRIMARY KEY, at TEXT NOT NULL, query TEXT NOT NULL, engines TEXT NOT NULL, providers TEXT NOT NULL,
    results INTEGER NOT NULL, ms INTEGER NOT NULL
);
"""
# provider -> what may be kept beyond a short cache, how long the cache is, and the attribution to show
POLICY = {
    "brave": {"keep": "links", "cache_s": 3600, "licence": "Brave Search API terms",
              "why": "Brave's terms: storing results needs a plan with storage rights"},
    "tavily": {"keep": "links", "cache_s": 86400, "licence": "Tavily terms", "why": "Tavily's terms"},
    "serper": {"keep": "links", "cache_s": 3600, "licence": "Google results via Serper",
               "why": "results come from Google; links only"},
    "google_places": {"keep": "id", "cache_s": 0, "licence": "Google Maps Platform terms",
                      "why": "Google's terms: only the place ID may be kept, and nothing else cached"},
    "osm": {"keep": "full", "cache_s": 7 * 86400, "licence": "© OpenStreetMap contributors, ODbL",
            "why": "open data (ODbL): kept with its attribution"},
    "shopping": {"keep": "links", "cache_s": 3600, "licence": "each store's terms",
                 "why": "prices change; links kept, prices in Buddy's own price book"},
    "pricebook": {"keep": "none", "cache_s": 0, "licence": "your own price book", "why": "already yours"},
    "news": {"keep": "links", "cache_s": 1800, "licence": "each publisher's terms", "why": "links only"},
    "papers": {"keep": "full", "cache_s": 86400, "licence": "public scholarly records",
               "why": "public records, also kept in the paper library"},
    "internal": {"keep": "none", "cache_s": 0, "licence": "yours", "why": "your own things: never copied"},
}
DAILY = {"brave": 40, "tavily": 30, "google_places": 25, "serper": 60}  # about a month's free use, spread out
OFFICIAL = re.compile(r"(^|\.)(gov|edu|mil|int)$|(^|\.)(who\.int|nih\.gov|cdc\.gov|fda\.gov|europa\.eu|nature\.com|"
                      r"science\.org|nejm\.org|thelancet\.com|bmj\.com|jamanetwork\.com|ahajournals\.org|arxiv\.org|"
                      r"wikipedia\.org|reuters\.com|apnews\.com)$", re.I)
TRACKING = re.compile(r"^(utm_\w+|fbclid|gclid|mc_cid|mc_eid|ref|ref_src|igshid|si)$", re.I)

# --- what's asked ---------------------------------------------------------------------------------------------
INTERNAL = re.compile(r"\b(my|mine|i (?:saved|got|sent|received|wrote|uploaded))\b.*\b(emails?|mails?|inbox|messages?|"
                      r"texts?|documents?|docs|files?|notes?|papers?|library|calendar|receipts?|pdfs?)\b|"
                      r"\b(in|from) my (emails?|inbox|messages|documents|files|notes|library|calendar)\b", re.I)
LOCAL = re.compile(r"\b(near me|nearby|near here|around here|close by|open now|restaurants?|cafes?|coffee shops?|"
                   r"bars?|pubs?|hotels?|motels?|pharmac(?:y|ies)|hospitals?|clinics?|dentists?|doctors?|gyms?|"
                   r"supermarkets?|grocer(?:y|ies)|bakery|bakeries|atms?|gas stations?|petrol|parking|parks?|"
                   r"museums?|salons?|barbers?|temples?|churches|mosques?|where (?:can|to) (?:i )?(?:eat|buy|get))\b",
                   re.I)
PRODUCTS = re.compile(r"\b(buy|price|prices|cheapest|cheaper|deal|deals|discount|coupon|sale|on sale|cost of|"
                      r"how much (?:is|are|does)|in stock|compare prices|best price)\b|\$\d", re.I)
FOOD = re.compile(r"\b(pizza|sushi|burgers?|tacos?|ramen|pho|biryani|dosa|curry|bbq|barbecue|steak|seafood|brunch|"
                  r"breakfast|lunch|dinner|food|eat|takeout|delivery|vegan|vegetarian|halal|italian|indian|chinese|"
                  r"mexican|thai|japanese|korean|greek|mediterranean|kerala)\b", re.I)
NEAR = re.compile(r"\b(near me|nearby|near here|around here|close by)\b", re.I)
NEWS = re.compile(r"\b(news|latest|headlines?|breaking|today|this week|yesterday|what(?:'s| is) happening|"
                  r"announced|update on)\b", re.I)
RESEARCH = re.compile(r"\b(research|study|studies|evidence|paper|papers|science|scientific|clinical|trial|trials|"
                      r"meta-analysis|systematic review|journal|preprint|longevity)\b", re.I)
IN_AREA = re.compile(r"\b(?:in|near|around)\s+((?!me\b|here\b|my\b|stock\b)[\w.'-]+(?:[ ,]+[\w.'-]+){0,3})\s*\??$", re.I)


def understand(query: str, memory: Memory | None = None) -> dict:
    """What's asked: the engines it needs (in order), the area for places, and the words to search."""
    q = " ".join(str(query or "").split())[:300]
    engines, area, words = [], "", q
    if INTERNAL.search(q):
        engines.append("internal")
    if LOCAL.search(q) or (FOOD.search(q) and (IN_AREA.search(q) or NEAR.search(q))):
        # a place search ("pizza in hoboken", "thai near me"): the area's name comes off before the rest is read
        engines.append("local")
        if (m := IN_AREA.search(q)):
            area, words = m.group(1).strip(" ,"), q[:m.start()].strip()
        else:
            words = re.sub(r"\b(near me|nearby|near here|around here|close by)\b", "", q, flags=re.I).strip()
    if PRODUCTS.search(words):
        engines.append("products")
    if NEWS.search(words):
        engines.append("news")
    if RESEARCH.search(words):
        engines.append("research")
    if "internal" not in engines and not ({"local", "products"} & set(engines)) or not engines:
        engines.append("web")
    if "local" in engines and not area and memory is not None:
        from . import dining  # noqa: PLC0415
        area = dining.area(memory)  # the neighbourhood the phone last shared: never coordinates
    return {"query": q, "engines": list(dict.fromkeys(engines)), "area": area, "words": words or q}


# --- one result format ----------------------------------------------------------------------------------------
@dataclass
class Result:
    title: str
    url: str = ""
    snippet: str = ""
    engine: str = "web"
    kind: str = "page"                    # page, news, place, product, paper, document, fact, message
    provider: str = ""                    # its POLICY key
    source: str = ""                      # who answered, as shown
    published: str = ""
    source_id: str = ""
    extra: dict = field(default_factory=dict)

    def site(self) -> str:
        host = urllib.parse.urlparse(self.url).hostname or ""
        return host[4:] if host.startswith("www.") else host


def canonical(url: str) -> str:
    """The same page however it was linked: https, no www, no tracking parameters, no fragment or end slash."""
    try:
        p = urllib.parse.urlparse(str(url or "").strip())
    except ValueError:
        return ""
    if p.scheme not in ("http", "https") or not p.hostname:
        return ""
    host = p.hostname.lower().removeprefix("www.")
    query = urllib.parse.urlencode([(k, v) for k, v in urllib.parse.parse_qsl(p.query) if not TRACKING.match(k)])
    path = p.path.rstrip("/") or ""
    return f"https://{host}{path}" + (f"?{query}" if query else "")


def _norm_name(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(text or "").lower().replace("&", " and ")).strip()


def identity(r: Result) -> str:
    """What makes two results the same thing: a place's name and street or position, a paper's number, a page."""
    if r.kind == "place":
        lat, lon = r.extra.get("lat"), r.extra.get("lon")
        if isinstance(lat, (int, float)) and isinstance(lon, (int, float)):
            return f"place:{_norm_name(r.title)}@{round(lat, 3)},{round(lon, 3)}"
        street = _norm_name(str(r.extra.get("address") or "").split(",")[0])
        return f"place:{_norm_name(r.title)}|{street}"
    if r.kind == "paper":
        from . import paperlib  # noqa: PLC0415
        k = paperlib.key_for(paperlib.ids(r.url, r.extra.get("pmid", ""), r.extra.get("doi", "")), r.url)
        if k:
            return k
    if r.kind == "product":  # an offer: its own link, else its store and title
        return canonical(r.url) or f"product:{_norm_name(r.extra.get('store') or r.source)}|{_norm_name(r.title)[:80]}"
    return canonical(r.url) or f"{r.kind}:{_norm_name(r.title)[:120]}"


# --- the providers --------------------------------------------------------------------------------------------
def _brave(memory, q: str, kind: str, env=None, fetch=None) -> list[Result]:
    path = "/res/v1/news/search" if kind == "news" else "/res/v1/web/search"
    params = {"q": q, "count": 10} | ({"freshness": "pw"} if kind == "news" else {})
    got = gateway.call(memory, "brave", f"{path}?{urllib.parse.urlencode(params)}", headers={"Accept":
                       "application/json"}, env=env, fetch=fetch, purpose="search") or {}
    rows = got.get("results") if kind == "news" else (got.get("web") or {}).get("results")
    return [Result(title=r.get("title") or "", url=r.get("url") or "", snippet=r.get("description") or "",
                   engine="news" if kind == "news" else "web", kind="news" if kind == "news" else "page",
                   provider="brave", source="Brave News" if kind == "news" else "Brave Search",
                   published=str(r.get("page_age") or r.get("age") or "")[:25])
            for r in rows or [] if r.get("url")]


def _tavily(memory, q: str, kind: str, env=None, fetch=None) -> list[Result]:
    got = gateway.call(memory, "tavily", "/search", json_body={
        "query": q, "max_results": 8, "search_depth": "advanced" if kind == "research" else "basic",
        "topic": "news" if kind == "news" else "general", "include_answer": False}, env=env, fetch=fetch,
        purpose="search") or {}
    engine = kind if kind in ("research", "news") else "web"
    return [Result(title=r.get("title") or "", url=r.get("url") or "", snippet=str(r.get("content") or "")[:600],
                   engine=engine, kind="page", provider="tavily", source="Tavily",
                   published=str(r.get("published_date") or "")[:25], extra={"score": r.get("score")})
            for r in got.get("results") or [] if r.get("url")]


def _serper_web(memory, q: str, kind: str, env=None, fetch=None) -> list[Result]:
    from .social import Social  # noqa: PLC0415
    got = Social(env=env).google(q, num=10)
    return [Result(title=r.get("title") or "", url=r.get("link") or "", snippet=r.get("snippet") or "",
                   engine="web", provider="serper", source="Google (Serper)", published=str(r.get("date") or ""))
            for r in got.get("results") or [] if r.get("link")]


def _places(memory, q: str, area: str, env=None, fetch=None) -> list[Result]:
    """Google Places when its key is set, else Google Maps results through Serper."""
    from . import dining  # noqa: PLC0415
    text = f"{q} in {area}" if area else q
    out = []
    if gateway.configured("google_places", env):
        got = gateway.call(memory, "google_places", "/v1/places:searchText",
                           json_body={"textQuery": text, "maxResultCount": 10},
                           headers={"X-Goog-FieldMask": dining.FIELDS + ",places.location"}, env=env, fetch=fetch,
                           purpose="search") or {}
        for p in got.get("places") or []:
            loc = p.get("location") or {}
            out.append(Result(title=(p.get("displayName") or {}).get("text", ""), url=p.get("googleMapsUri", ""),
                              snippet=(p.get("editorialSummary") or {}).get("text", ""), engine="local", kind="place",
                              provider="google_places", source="Google Places", source_id=p.get("id", ""),
                              extra={"address": p.get("formattedAddress", ""), "rating": p.get("rating"),
                                     "reviews": p.get("userRatingCount"),
                                     "category": (p.get("primaryTypeDisplayName") or {}).get("text", ""),
                                     "open_now": (p.get("currentOpeningHours") or {}).get("openNow"),
                                     "phone": p.get("internationalPhoneNumber") or "", "website": p.get("websiteUri", ""),
                                     "lat": loc.get("latitude"), "lon": loc.get("longitude")}))
        return out
    from .social import Social  # noqa: PLC0415
    for p in Social(env=env).places(text, num=10):
        out.append(Result(title=p.get("title", ""), url=f"https://www.google.com/maps/place/?q=place_id:{p['cid']}"
                          if p.get("cid") else "", engine="local", kind="place", provider="serper",
                          source="Google Maps (Serper)",
                          extra={"address": p.get("address", ""), "rating": p.get("rating"),
                                 "reviews": p.get("ratingCount"), "category": p.get("category", ""),
                                 "phone": p.get("phoneNumber", ""), "website": p.get("website", ""),
                                 "lat": p.get("latitude"), "lon": p.get("longitude")}))
    return out


OSM_KINDS = {  # words -> OpenStreetMap tags (key, value)
    "restaurant": ("amenity", "restaurant"), "cafe": ("amenity", "cafe"), "coffee": ("amenity", "cafe"),
    "bar": ("amenity", "bar"), "pub": ("amenity", "pub"), "hotel": ("tourism", "hotel"), "motel": ("tourism", "motel"),
    "pharmacy": ("amenity", "pharmacy"), "pharmacies": ("amenity", "pharmacy"), "hospital": ("amenity", "hospital"),
    "clinic": ("amenity", "clinic"), "dentist": ("amenity", "dentist"), "doctor": ("amenity", "doctors"),
    "gym": ("leisure", "fitness_centre"), "supermarket": ("shop", "supermarket"), "grocery": ("shop", "supermarket"),
    "bakery": ("shop", "bakery"), "atm": ("amenity", "atm"), "gas": ("amenity", "fuel"), "petrol": ("amenity", "fuel"),
    "parking": ("amenity", "parking"), "park": ("leisure", "park"), "museum": ("tourism", "museum"),
    "salon": ("shop", "hairdresser"), "barber": ("shop", "hairdresser"), "temple": ("amenity", "place_of_worship"),
    "church": ("amenity", "place_of_worship"), "mosque": ("amenity", "place_of_worship"),
}
CUISINES = ("italian", "indian", "chinese", "mexican", "thai", "japanese", "sushi", "pizza", "korean", "vietnamese",
            "greek", "mediterranean", "french", "spanish", "turkish", "lebanese", "american", "burger", "vegan",
            "vegetarian", "seafood", "kerala", "south indian", "ethiopian", "caribbean", "middle eastern")
_last_nominatim = [0.0]


def _nominatim(memory, params: dict, env=None, fetch=None) -> list:
    """Nominatim, at most one request a second, every answer cached a week (its usage policy)."""
    key = "osm:" + json.dumps(params, sort_keys=True)
    if (hit := _cache_get(memory, key)) is not None:
        return hit
    wait = 1.05 - (time.monotonic() - _last_nominatim[0])
    if wait > 0 and fetch is None:
        time.sleep(wait)
    _last_nominatim[0] = time.monotonic()
    got = gateway.call(memory, "nominatim", "/search?" + urllib.parse.urlencode(
        params | {"format": "jsonv2", "addressdetails": 1}), env=env, fetch=fetch, purpose="search") or []
    _cache_put(memory, key, "osm", got)
    return got


def _osm(memory, q: str, area: str, env=None, fetch=None) -> list[Result]:
    """OpenStreetMap: places of a kind inside the area (Overpass), else a name search (Nominatim)."""
    low = q.lower()
    tag = next((OSM_KINDS[w] for w in OSM_KINDS if re.search(rf"\b{w}s?\b", low)), None)
    cuisine = next((c for c in CUISINES if re.search(rf"\b{c}\b", low)), "")
    if cuisine and not tag:
        tag = OSM_KINDS["restaurant"]
    out: list[Result] = []
    if tag and area:
        places = _nominatim(memory, {"q": area, "limit": 1}, env, fetch)
        bbox = (places[0].get("boundingbox") if places else None) or []
        if len(bbox) == 4:
            s, n, w, e = (float(x) for x in bbox)
            if (n - s) * (e - w) <= 0.25:  # a town or neighbourhood, not a whole state
                rule = f'["{tag[0]}"="{tag[1]}"]' + (f'["cuisine"~"{cuisine.replace(" ", "_")}",i]' if cuisine and
                                                     tag[1] == "restaurant" else "")
                ql = f"[out:json][timeout:20];nwr{rule}({s},{w},{n},{e});out center tags 30;"
                key = "overpass:" + ql
                got = _cache_get(memory, key)
                if got is None:
                    got = gateway.call(memory, "overpass", "/api/interpreter", data=urllib.parse.urlencode(
                        {"data": ql}).encode(), headers={"Content-Type": "application/x-www-form-urlencoded"},
                        env=env, fetch=fetch, purpose="search") or {}
                    _cache_put(memory, key, "osm", got)
                for el in (got or {}).get("elements") or []:
                    t = el.get("tags") or {}
                    if not t.get("name"):
                        continue
                    lat = el.get("lat") or (el.get("center") or {}).get("lat")
                    lon = el.get("lon") or (el.get("center") or {}).get("lon")
                    street = " ".join(x for x in (t.get("addr:housenumber"), t.get("addr:street")) if x)
                    out.append(Result(title=t["name"], url=f"https://www.openstreetmap.org/{el.get('type')}/{el.get('id')}",
                                      engine="local", kind="place", provider="osm", source="OpenStreetMap",
                                      source_id=f"{el.get('type')}/{el.get('id')}",
                                      extra={"address": ", ".join(x for x in (street, t.get("addr:city")) if x),
                                             "category": (t.get("cuisine") or tag[1]).replace("_", " ").replace(";", ", "),
                                             "website": t.get("website") or t.get("contact:website") or "",
                                             "phone": t.get("phone") or t.get("contact:phone") or "",
                                             "hours": t.get("opening_hours") or "", "lat": lat, "lon": lon}))
                return out[:20]
    for p in _nominatim(memory, {"q": f"{q} {area}".strip(), "limit": 8}, env, fetch):
        out.append(Result(title=p.get("name") or str(p.get("display_name") or "").split(",")[0],
                          url=f"https://www.openstreetmap.org/{p.get('osm_type')}/{p.get('osm_id')}", engine="local",
                          kind="place", provider="osm", source="OpenStreetMap",
                          source_id=f"{p.get('osm_type')}/{p.get('osm_id')}",
                          extra={"address": p.get("display_name", ""), "category": str(p.get("type") or "").replace("_", " "),
                                 "lat": float(p["lat"]) if p.get("lat") else None,
                                 "lon": float(p["lon"]) if p.get("lon") else None}))
    return out


def _shopping(memory, q: str, env=None, fetch=None) -> list[Result]:
    from . import dealcollector  # noqa: PLC0415
    out = []
    for fn, store_name in ((dealcollector.google_shopping, ""), (dealcollector.bestbuy, "Best Buy"),
                           (dealcollector.ebay, "eBay")):
        try:
            got = fn(memory, q, env=env, fetch=fetch)
        except Exception:  # noqa: BLE001  one store not answering is not the search failing
            continue
        for o in got[:10]:
            out.append(Result(title=o.get("title") or q, url=o.get("url") or "", engine="products", kind="product",
                              provider="shopping", source=o.get("store") or store_name,
                              extra={"price": o.get("price"), "store": o.get("store") or store_name,
                                     "regular": o.get("regular"), "on_sale": o.get("on_sale")}))
    return out


def _pricebook(memory, q: str) -> list[Result]:
    from . import dealcollector  # noqa: PLC0415
    return [Result(title=r["title"], url=r.get("url") or "", engine="products", kind="product", provider="pricebook",
                   source=f"Your price book ({r.get('store')})", published=str(r.get("seen_on") or ""),
                   extra={"price": r.get("price"), "store": r.get("store"), "seen": str(r.get("seen_on") or "")})
            for r in dealcollector.search(memory, q, 30)]


def _news(memory, q: str, env=None, fetch=None) -> list[Result]:
    from . import newsfeed  # noqa: PLC0415
    got = newsfeed.search(q, memory=memory, env=env, fetch=fetch, days=7, num=10)
    return [Result(title=n.get("title") or "", url=n.get("link") or n.get("url") or "", snippet=n.get("snippet") or "",
                   engine="news", kind="news", provider="news", source=n.get("source") or got.get("answered_by", ""),
                   published=str(n.get("date") or n.get("published") or "")[:25])
            for n in got.get("news") or [] if n.get("link") or n.get("url")]


def _papers(memory, q: str, env=None, fetch=None) -> list[Result]:
    from . import bip_sources, paperlib  # noqa: PLC0415  (each also keeps what it finds in the paper library)
    out = []
    for fn, name in ((bip_sources.europepmc, "Europe PMC"), (bip_sources.openalex, "OpenAlex"),
                     (bip_sources.arxiv, "arXiv")):
        try:
            got = fn(memory, q, fetch)
        except Exception:  # noqa: BLE001
            continue
        for r in got[:8]:
            out.append(Result(title=r.get("title") or "", url=r.get("url") or "", snippet=str(r.get("text") or "")[:500],
                              engine="research", kind="paper", provider="papers", source=name,
                              published=str(r.get("day") or ""), extra={"venue": r.get("who") or "",
                                                                         "pmid": r.get("pmid") or "",
                                                                         "doi": r.get("doi") or ""}))
    for p in paperlib.search(memory, q, limit=8)["papers"]:
        out.append(Result(title=p["title"], url=p["url"], snippet=p["abstract"][:500], engine="research", kind="paper",
                          provider="papers", source="Your paper library", published=p["published"],
                          extra={"venue": p["venue"], "pmid": p["pmid"], "doi": p["doi"]}))
    return out


def _internal(memory, q: str) -> list[Result]:
    """The owner's own things, on this server only."""
    from .brain import Brain  # noqa: PLC0415
    from .connectors.hub import ConnectorHub  # noqa: PLC0415
    from .library import Library  # noqa: PLC0415
    out = []
    try:
        for d in Library(memory).search(q, limit=6):
            out.append(Result(title=d.get("title") or d.get("file") or "Document", snippet=str(d.get("text") or "")[:400],
                              engine="internal", kind="document", provider="internal", source="Your library",
                              published=str(d.get("added") or ""), source_id=str(d.get("id") or "")))
    except Exception:  # noqa: BLE001
        pass
    try:
        for f in Brain(memory).recall(q, sources=("fact", "note"), k=6):
            out.append(Result(title=f.get("title") or f["text"][:80], snippet=f["text"][:400], engine="internal",
                              kind="fact", provider="internal", source="What Buddy remembers",
                              published=f.get("saved", "")))
    except Exception:  # noqa: BLE001
        pass
    try:
        for r in ConnectorHub(memory).search(q, days=365, limit=8):
            out.append(Result(title=r.get("title") or "", snippet=str(r.get("body") or r.get("text") or "")[:300],
                              engine="internal", kind="message", provider="internal",
                              source=f"Your {r.get('source') or 'apps'}", published=str(r.get("when") or "")))
    except Exception:  # noqa: BLE001
        pass
    return out


@dataclass
class Provider:
    name: str        # for logs and limits
    engine: str
    title: str
    ready: object    # (env) -> bool
    run: object      # (memory, words, area, env, fetch) -> list[Result]
    fallback_only: bool = False
    cap: str = ""    # DAILY key


PROVIDERS = [
    Provider("brave", "web", "Brave Search", lambda env: gateway.configured("brave", env),
             lambda m, w, a, env, fetch: _brave(m, w, "web", env, fetch), cap="brave"),
    Provider("tavily", "web", "Tavily", lambda env: gateway.configured("tavily", env),
             lambda m, w, a, env, fetch: _tavily(m, w, "web", env, fetch), cap="tavily"),
    Provider("serper", "web", "Google (Serper)", lambda env: gateway.configured("serper", env),
             lambda m, w, a, env, fetch: _serper_web(m, w, "web", env, fetch), fallback_only=True, cap="serper"),
    Provider("google_places", "local", "Google Places", lambda env: gateway.configured("google_places", env) or
             gateway.configured("serper", env), lambda m, w, a, env, fetch: _places(m, w, a, env, fetch),
             cap="google_places"),
    Provider("osm", "local", "OpenStreetMap", lambda env: True, lambda m, w, a, env, fetch: _osm(m, w, a, env, fetch)),
    Provider("shopping", "products", "Stores", lambda env: True, lambda m, w, a, env, fetch: _shopping(m, w, env, fetch)),
    Provider("pricebook", "products", "Your price book", lambda env: True, lambda m, w, a, env, fetch: _pricebook(m, w)),
    Provider("brave_products", "products", "Brave Search", lambda env: gateway.configured("brave", env),
             lambda m, w, a, env, fetch: [r.__class__(**{**r.__dict__, "engine": "products"}) for r in
                                          _brave(m, f"{w} price", "web", env, fetch)], cap="brave"),
    Provider("brave_news", "news", "Brave News", lambda env: gateway.configured("brave", env),
             lambda m, w, a, env, fetch: _brave(m, w, "news", env, fetch), cap="brave"),
    Provider("news", "news", "News services", lambda env: True, lambda m, w, a, env, fetch: _news(m, w, env, fetch)),
    Provider("tavily_research", "research", "Tavily", lambda env: gateway.configured("tavily", env),
             lambda m, w, a, env, fetch: _tavily(m, w, "research", env, fetch), cap="tavily"),
    Provider("papers", "research", "Scholarly sources", lambda env: True,
             lambda m, w, a, env, fetch: _papers(m, w, env, fetch)),
    Provider("internal", "internal", "Your own things", lambda env: True, lambda m, w, a, env, fetch: _internal(m, w)),
]


# --- limits, cache, what's kept -------------------------------------------------------------------------------
def _ready(memory: Memory) -> None:
    memory.db.executescript(SCHEMA)


def _cap(name: str, env=None) -> int:
    import os  # noqa: PLC0415
    raw = (env if env is not None else os.environ).get(f"BUDDY_SEARCH_CAP_{name.upper()}")
    try:
        return int(raw) if raw not in (None, "") else DAILY.get(name, 10 ** 6)
    except ValueError:
        return DAILY.get(name, 10 ** 6)


def _used(memory: Memory, name: str) -> int:
    return int(memory.get(f"search:used:{name}:{local_now().date().isoformat()}") or 0)


def _count(memory: Memory, name: str) -> None:
    key = f"search:used:{name}:{local_now().date().isoformat()}"
    memory.set(key, str(int(memory.get(key) or 0) + 1))


def _cache_get(memory: Memory, key: str):
    _ready(memory)
    row = memory.db.execute("SELECT payload, expires FROM search_cache WHERE key = ?",
                            (hashlib.sha256(key.encode()).hexdigest(),)).fetchone()
    if row and row["expires"] > time.time():
        return json.loads(row["payload"])
    return None


def _cache_put(memory: Memory, key: str, provider: str, payload) -> None:
    ttl = POLICY.get(provider, {}).get("cache_s", 0)
    if not ttl:
        return
    _ready(memory)
    memory.db.execute("INSERT OR REPLACE INTO search_cache (key, provider, created, expires, payload) VALUES "
                      "(?, ?, ?, ?, ?)", (hashlib.sha256(key.encode()).hexdigest(), provider, time.time(),
                                          time.time() + ttl, json.dumps(payload)))
    memory.db.execute("DELETE FROM search_cache WHERE expires < ?", (time.time(),))
    memory.db.commit()


def policy(provider: str, env=None) -> dict:
    import os  # noqa: PLC0415
    p = dict(POLICY.get(provider) or POLICY["serper"])
    if provider == "brave" and str((env if env is not None else os.environ).get("BUDDY_BRAVE_STORAGE_RIGHTS")) == "1":
        p |= {"keep": "full", "why": "your Brave plan includes storage rights"}
    return p


def _keep(memory: Memory, results: list[Result], env=None) -> None:
    """What may be kept of one provider's own results, by its terms (before merging, so one provider's fields are
    never kept under another's terms; never the owner's own things)."""
    _ready(memory)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    for r in results:
        p = policy(r.provider, env)
        if p["keep"] == "none":
            continue
        title, url, detail = r.title, r.url, {}
        if p["keep"] == "id":
            if not r.source_id:
                continue
            row_id, title, url = f"{r.provider}:{r.source_id}", "", ""  # Google Places: the place ID only
        else:
            row_id = f"{r.provider}:{r.source_id or canonical(r.url) or _norm_name(r.title)[:200]}"
        if p["keep"] == "full":
            detail = {"snippet": r.snippet, **{k: v for k, v in r.extra.items() if v not in (None, "")}}
        memory.db.execute(
            "INSERT INTO search_kept (id, provider, engine, title, url, source_id, detail, licence, first_seen, "
            "last_seen) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET last_seen = excluded.last_seen",
            (row_id[:500], r.provider, r.engine, title[:300], url[:500], r.source_id[:200],
             json.dumps(detail)[:4000], p["licence"], now, now))
    memory.db.commit()


# --- the search -----------------------------------------------------------------------------------------------
def _plan(engines: list[str], env=None) -> list[Provider]:
    out = []
    for engine in engines:
        mine = [p for p in PROVIDERS if p.engine == engine and p.ready(env)]
        first = [p for p in mine if not p.fallback_only]
        out += first or mine
    return out


async def _run_one(memory, p: Provider, words: str, area: str, env, fetch, timeout: float) -> dict:
    started = time.monotonic()
    row = {"provider": p.name, "title": p.title, "engine": p.engine, "ok": False, "n": 0, "ms": 0, "error": "",
           "cached": False}
    cache_key = f"{p.name}|{words.lower()}|{area.lower()}"
    if p.name not in ("internal", "pricebook") and (hit := _cache_get(memory, cache_key)) is not None:
        return row | {"ok": True, "n": len(hit), "cached": True, "results": [Result(**h) for h in hit]}
    if p.cap and _used(memory, p.cap) >= _cap(p.cap, env):
        return row | {"error": "today's limit reached", "results": []}
    try:
        got = await asyncio.wait_for(asyncio.to_thread(p.run, memory, words, area, env, fetch), timeout)
    except asyncio.TimeoutError:
        return row | {"error": "took too long", "ms": int((time.monotonic() - started) * 1000), "results": []}
    except Exception as e:  # noqa: BLE001  one provider failing is a reason to use the others
        return row | {"error": " ".join(str(e).split())[:160] or type(e).__name__,
                      "ms": int((time.monotonic() - started) * 1000), "results": []}
    if p.cap:
        _count(memory, p.cap)
    got = [r for r in got if r.title]
    policy_name = got[0].provider if got else p.name
    if p.name not in ("internal", "pricebook"):
        _cache_put(memory, cache_key, policy_name if policy_name in POLICY else "serper", [r.__dict__ for r in got])
    return row | {"ok": True, "n": len(got), "ms": int((time.monotonic() - started) * 1000), "results": got}


def rank(per_provider: list[list[Result]], engine: str) -> list[dict]:
    """Merge the providers' lists: one entry per thing, scored by reciprocal rank fusion (k=60), a bonus for each
    other source that found it, official sources, and freshness for news."""
    merged: dict[str, dict] = {}
    for results in per_provider:
        for i, r in enumerate(results):
            key = identity(r)
            score = 1 / (60 + i + 1)
            if key not in merged:
                merged[key] = {"id": key, "title": r.title, "url": r.url, "snippet": r.snippet, "engine": engine,
                               "kind": r.kind, "provider": r.provider, "source": r.source, "site": r.site(),
                               "published": r.published, "source_id": r.source_id, "extra": dict(r.extra),
                               "found_by": [r.source], "score": score}
                continue
            m = merged[key]
            m["score"] += score
            if r.source not in m["found_by"]:
                m["found_by"].append(r.source)
            for k, v in r.extra.items():  # fill what the first source lacked (OSM's hours, Google's rating)
                if m["extra"].get(k) in (None, "") and v not in (None, ""):
                    m["extra"][k] = v
            if len(r.snippet) > len(m["snippet"]):
                m["snippet"] = r.snippet
            if not m["url"] and r.url:
                m["url"] = r.url
    out = list(merged.values())
    for m in out:
        m["score"] *= 1 + 0.25 * (len(m["found_by"]) - 1)
        if OFFICIAL.search(m["site"] or ""):
            m["score"] *= 1.15
            m["official"] = True
        if engine == "news" and _days_old(m["published"]) is not None and _days_old(m["published"]) <= 2:
            m["score"] *= 1.2
        m["corroborated"] = len(m["found_by"]) > 1
        if engine == "local":
            _by_reviews(m)
        m["score"] = round(m["score"], 5)
    if engine == "products":  # offers with a price first, cheapest first; then the pages around them
        def priced(m):
            price = m["extra"].get("price")
            return isinstance(price, (int, float)) and m["kind"] == "product"
        return sorted([m for m in out if priced(m)], key=lambda m: m["extra"]["price"]) + \
            sorted([m for m in out if not priced(m)], key=lambda m: -m["score"])
    return sorted(out, key=lambda m: -m["score"])


def _by_reviews(m: dict) -> None:
    """Places: raised or lowered by the review-count-aware rating (a 4.6 from 5,000 beats a 5.0 from 4); a place
    without a rating counts as ordinary. A very high rating from few reviews is marked."""
    from .reviews import FEW, review_score  # noqa: PLC0415
    rated = review_score(m["extra"].get("rating"), m["extra"].get("reviews"))
    if rated is None:
        return
    m["review_score"] = rated
    m["score"] *= (rated / 4.2) ** 4
    try:
        if float(m["extra"]["rating"]) >= FEW[0] and 0 < int(m["extra"].get("reviews") or 0) < FEW[1]:
            m["caution"] = "few reviews"
    except (TypeError, ValueError):
        pass


def _days_old(text: str) -> float | None:
    try:
        d = datetime.fromisoformat(str(text)[:19].replace("Z", ""))
    except ValueError:
        return None
    return (datetime.now() - d).total_seconds() / 86400


async def search(memory: Memory, query: str, engines: list[str] | None = None, area: str = "", env=None,
                 fetch=None, timeout: float = 15.0, per_engine: int = 10) -> dict:
    """Search everything that fits the question, at once. Never raises for a provider failing."""
    started = time.monotonic()
    asked = understand(query, memory)
    if not asked["query"]:
        raise ToolError("What should I search for?")
    if engines:
        bad = [e for e in engines if e not in ENGINES]
        if bad:
            raise ToolError("Engines are: " + ", ".join(ENGINES))
        asked["engines"] = list(dict.fromkeys(engines))
    if area:
        asked["area"] = " ".join(str(area).split())[:80]
    plan = _plan(asked["engines"], env)
    runs = await asyncio.gather(*(_run_one(memory, p, asked["words"] if p.engine == "local" else asked["query"],
                                           asked["area"], env, fetch, timeout) for p in plan))
    # a primary that failed or is over its limit: its engine's fallback steps in
    for engine in asked["engines"]:
        if not any(r["ok"] and r["n"] for r in runs if r["engine"] == engine):
            for p in [p for p in PROVIDERS if p.engine == engine and p.fallback_only and p.ready(env)
                      and p not in plan]:
                runs.append(await _run_one(memory, p, asked["query"], asked["area"], env, fetch, timeout))
    out_engines = {}
    for engine in asked["engines"]:
        lists = [r["results"] for r in runs if r["engine"] == engine and r["results"]]
        out_engines[engine] = rank(lists, engine)[:per_engine]
    flat = [r for rs in out_engines.values() for r in rs]
    for run in runs:
        if run["results"] and not run.get("cached"):
            _keep(memory, run["results"], env)
    took = int((time.monotonic() - started) * 1000)
    providers = [{k: v for k, v in r.items() if k != "results"} for r in runs]
    _ready(memory)
    memory.db.execute("INSERT INTO search_history (at, query, engines, providers, results, ms) VALUES (?, ?, ?, ?, ?, ?)",
                      (local_now().isoformat(timespec="seconds"), asked["query"], json.dumps(asked["engines"]),
                       json.dumps(providers), len(flat), took))
    memory.db.commit()
    attributions = sorted({policy(r["provider"], env)["licence"] for r in flat if r["provider"] == "osm"})
    return {"query": asked["query"], "engines": asked["engines"], "area": asked["area"], "results": out_engines,
            "providers": providers, "took_ms": took, "attribution": attributions,
            "note": "Each result says where it came from. Only your search words (and for places an area name) "
                    "went to the providers; a search of your own things stayed on your server."}


async def summarise(memory: Memory, found: dict, env=None, fetch=None) -> dict | None:
    """A short answer with numbered sources, from the top results. Your own things: Claude only."""
    from . import maestro  # noqa: PLC0415
    top = [r for rs in found["results"].values() for r in rs[:4]][:10]
    if not top:
        return None
    lines = "\n".join(f"[{i}] {r['title']} ({r['source']}{', ' + r['site'] if r['site'] else ''}): {r['snippet'][:400]}"
                      for i, r in enumerate(top, 1))
    prompt = (f"Question: {found['query']}\nSources:\n{lines}\n\nAnswer the question in 2 to 4 sentences from these "
              'sources only, citing them like [1]. Reply with one line of JSON: {"answer": "...", "sources": [1, 2]}.')
    personal = "internal" in found["engines"]
    got = await maestro.ask(memory, "summarise_search", prompt, personal=personal, fetch=fetch, env=env)
    if not got or not isinstance(got.get("answer"), str):
        return None
    cited = [top[i - 1] for i in got.get("sources") or [] if isinstance(i, int) and 0 < i <= len(top)]
    return {"answer": got["answer"][:1200], "sources": [{"n": top.index(c) + 1, "title": c["title"], "url": c["url"],
                                                         "source": c["source"]} for c in cited]}


# --- the owner's view: providers, use, history ----------------------------------------------------------------
def status(memory: Memory, env=None) -> dict:
    _ready(memory)
    since = (local_now() - timedelta(days=7)).isoformat(timespec="seconds")
    stats: dict[str, dict] = {}
    for row in memory.db.execute("SELECT providers FROM search_history WHERE at >= ?", (since,)):
        for p in json.loads(row["providers"]):
            s = stats.setdefault(p["provider"], {"calls": 0, "failed": 0, "ms": [], "cached": 0})
            s["calls"] += 1
            s["failed"] += 0 if p["ok"] else 1
            s["cached"] += 1 if p.get("cached") else 0
            if p["ms"]:
                s["ms"].append(p["ms"])
    out = []
    for p in PROVIDERS:
        s = stats.get(p.name, {"calls": 0, "failed": 0, "ms": [], "cached": 0})
        pol = policy({"brave_products": "brave", "brave_news": "brave", "tavily_research": "tavily"}.get(p.name, p.name),
                     env)
        out.append({"provider": p.name, "title": p.title, "engine": ENGINES[p.engine], "ready": bool(p.ready(env)),
                    "fallback_only": p.fallback_only, "today": _used(memory, p.cap) if p.cap else None,
                    "daily_limit": _cap(p.cap, env) if p.cap else None, "week_calls": s["calls"],
                    "week_failed": s["failed"], "week_cached": s["cached"],
                    "avg_ms": round(sum(s["ms"]) / len(s["ms"])) if s["ms"] else None,
                    "keeps": pol["keep"], "keeps_why": pol["why"], "licence": pol["licence"]})
    history = [dict(r) | {"engines": json.loads(r["engines"])} for r in memory.db.execute(
        "SELECT id, at, query, engines, results, ms FROM search_history ORDER BY id DESC LIMIT 20")]
    kept = memory.db.execute("SELECT COUNT(*) FROM search_kept").fetchone()[0]
    return {"providers": out, "history": history, "kept": kept,
            "keys": {"brave": "BUDDY_BRAVE_KEY", "tavily": "BUDDY_TAVILY_KEY",
                     "google_places": "BUDDY_GOOGLE_PLACES_KEY", "serper": "BUDDY_SERPER_KEY"}}


def forget(memory: Memory, history_id: int | None = None) -> int:
    """One search from the history, or (no id) the history, the cache and what was kept."""
    _ready(memory)
    if history_id:
        n = memory.db.execute("DELETE FROM search_history WHERE id = ?", (int(history_id),)).rowcount
    else:
        n = memory.db.execute("DELETE FROM search_history").rowcount
        memory.db.execute("DELETE FROM search_cache")
        memory.db.execute("DELETE FROM search_kept")
    memory.db.commit()
    return n


TOOL = {
    "name": "search_everywhere",
    "description": (
        "Buddy Search: one search over many providers, chosen by what is asked: web (Brave, Tavily; Google via "
        "Serper as fallback), places (Google Places and OpenStreetMap, in the area named or the owner's "
        "neighbourhood), products and prices (Google Shopping, Best Buy, eBay, the owner's price book), news "
        "(Brave News and the news services), research (Tavily, Europe PMC, OpenAlex, arXiv, the paper library) and "
        "the owner's own things (documents, what Buddy remembers, email/messages/calendar, saved papers; these "
        "never leave the server). Results are merged, de-duplicated and ranked, each with its source; 'found_by' "
        "lists every source that had it. Use it for discovery across sources; web_fetch to read a page. engines "
        "is optional (Buddy works it out); summary=true adds a short cited answer."),
    "input_schema": {"type": "object", "properties": {
        "query": {"type": "string"},
        "engines": {"type": "array", "items": {"type": "string", "enum": list(ENGINES)}},
        "area": {"type": "string", "description": "a place name for places searches (never coordinates)"},
        "summary": {"type": "boolean"}}, "required": ["query"]},
}


async def run_tool(memory: Memory, args: dict) -> str:
    found = await search(memory, str(args.get("query") or ""), args.get("engines") or None, str(args.get("area") or ""))
    compact = {e: [{k: r[k] for k in ("title", "url", "snippet", "source", "found_by", "published")} |
                   {k: v for k, v in r["extra"].items() if k in ("address", "rating", "price", "store", "category",
                                                                  "open_now", "venue") and v not in (None, "")}
                   for r in rs[:8]] for e, rs in found["results"].items()}
    out = {"query": found["query"], "engines": found["engines"], "area": found["area"], "results": compact,
           "providers": [{k: p[k] for k in ("title", "ok", "n", "error")} for p in found["providers"]]}
    if args.get("summary"):
        out["summary"] = await summarise(memory, found)
    return json.dumps(out)
