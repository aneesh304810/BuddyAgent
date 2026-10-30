"""Rich cards in the chat: places, stays, flights, news, money, with photos, maps and links.

Tools that find things (Google Maps places, Google and Reddit results, news, price watches,
money) offer each result here and get back a short id ("p1", "n2"...). Claude sees only the
id, never the photo or map data, so this adds almost no tokens. To show results, Claude calls
show_cards with the ids it picks and a short note on why. Cards are built here from what the
tools really returned, so a link or photo can't be made up. Pages Claude's own web search
found (Airbnb listings, flights, articles) can be shown by their URL, checked against the
search results of the same turn.
"""

from contextvars import ContextVar
from urllib.parse import quote_plus, urlparse

MAX_SHOWN = 8

TRAVEL = ("airbnb.", "booking.com", "vrbo.", "expedia.", "hotels.com", "agoda.", "kayak.", "skyscanner.",
          "google.com/travel", "momondo.", "trip.com", "latam.com", "united.com", "aa.com", "delta.com")
STAYS = ("airbnb.", "booking.com", "vrbo.", "hotels.com", "agoda.")
FINANCE = ("finance.yahoo.", "bloomberg.", "marketwatch.", "cnbc.", "wsj.", "ft.com", "reuters.com/markets",
           "investing.com", "morningstar.")


def _domain(url: str) -> str:
    return (urlparse(url).netloc or "").removeprefix("www.")


def _link_kind(url: str) -> str:
    u = url.lower()
    if any(s in u for s in STAYS):
        return "stay"
    if any(s in u for s in TRAVEL):
        return "flight" if any(s in u for s in ("flight", "kayak.", "skyscanner.", "momondo.", "google.com/travel")) \
            else "travel"
    if any(s in u for s in FINANCE):
        return "finance"
    return "link"


def _https(url) -> str:
    url = str(url or "")
    return url if url.startswith("https://") else ""


def maps_link(title: str, address: str = "", cid: str = "", lat=None, lng=None) -> str:
    if cid:
        return f"https://www.google.com/maps?cid={quote_plus(str(cid))}"
    if title:
        return "https://www.google.com/maps/search/?api=1&query=" + quote_plus(f"{title} {address}".strip())
    if lat is not None and lng is not None:
        return f"https://www.google.com/maps/search/?api=1&query={lat},{lng}"
    return ""


def place_card(p: dict) -> dict:
    other = p.get("other_reviews") or {}
    reviews = [{"from": f"r/{r.get('subreddit')}" if r.get("subreddit") else "Reddit", "says": r.get("says", ""),
                "url": _https(r.get("link"))} for r in other.get("reddit", [])]
    reviews += [{"from": r.get("site", ""), "says": r.get("says", ""), "url": _https(r.get("link"))}
                for r in other.get("review_sites", [])]
    catches = [{"from": c.get("from", ""), "says": c.get("says", ""), "url": _https(c.get("link"))}
               for c in other.get("complaints_and_catches", [])]
    lat, lng = p.get("latitude"), p.get("longitude")
    return {
        "kind": "place",
        "title": p.get("title", ""),
        "subtitle": " · ".join(x for x in (p.get("category"), p.get("priceLevel")) if x),
        "rating": p.get("rating"),
        "reviews_count": p.get("ratingCount"),
        "address": p.get("address", ""),
        "hours": p.get("openingHours") if isinstance(p.get("openingHours"), dict) else None,
        "phone": p.get("phoneNumber", ""),
        "image": _https(p.get("thumbnailUrl")),
        "lat": lat, "lng": lng,
        "maps_url": maps_link(p.get("title", ""), p.get("address", ""), p.get("cid", ""), lat, lng),
        "url": _https(p.get("website")),
        "reviews": reviews[:6],
        "catches": catches[:4],
        "caution": p.get("caution", "") or other.get("caution", ""),
    }


def news_card(n: dict) -> dict:
    url = _https(n.get("link"))
    return {"kind": _link_kind(url) if _link_kind(url) == "finance" else "news", "title": n.get("title", ""),
            "subtitle": " · ".join(x for x in (n.get("source") or _domain(url), n.get("date")) if x),
            "text": n.get("snippet", ""), "image": _https(n.get("imageUrl")), "url": url}


def link_card(r: dict) -> dict:
    url = _https(r.get("link") or r.get("url"))
    return {"kind": _link_kind(url), "title": r.get("title", "") or _domain(url),
            "subtitle": " · ".join(x for x in (_domain(url), r.get("date") or r.get("page_age")) if x),
            "text": (r.get("snippet") or r.get("text") or "")[:300], "url": url}


def reddit_card(p: dict) -> dict:
    return {"kind": "reddit", "title": p.get("title", ""),
            "subtitle": " · ".join(x for x in (f"r/{p['subreddit']}" if p.get("subreddit") else "",
                                               f"{p['score']} points" if p.get("score") is not None else "") if x),
            "text": (p.get("text") or "")[:300], "url": _https(p.get("link"))}


def watch_card(w: dict) -> dict:
    price = w.get("last_price") or w.get("best_price")
    return {"kind": "deal", "title": w.get("what", ""),
            "subtitle": " · ".join(x for x in (w.get("last_where"), f"best {w.get('currency', '')} {w['best_price']}"
                                               if w.get("best_price") else "") if x),
            "price": f"{w.get('currency', '')} {price}".strip() if price else "",
            "text": f"Target {w.get('currency', '')} {w['target']}" if w.get("target") else "",
            "url": _https(w.get("last_url"))}


def money_card(title: str, rows: list[tuple[str, str]], text: str = "") -> dict:
    return {"kind": "money", "title": title, "rows": [{"label": a, "value": b} for a, b in rows][:12], "text": text}


class CardBox:
    """The cards one reply can show: offered by tools, picked by Claude."""

    PREFIX = {"place": "p", "news": "n", "finance": "n", "link": "w", "stay": "w", "flight": "w", "travel": "w",
              "reddit": "r", "deal": "d", "money": "m", "image": "i", "file": "f"}

    def __init__(self):
        self.cards: dict[str, dict] = {}
        self.by_url: dict[str, dict] = {}
        self.picked: list[dict] = []

    def add(self, card: dict) -> str | None:
        if not card.get("title"):
            return None
        prefix = self.PREFIX.get(card["kind"], "c")
        card_id = f"{prefix}{sum(1 for k in self.cards if k.startswith(prefix)) + 1}"
        self.cards[card_id] = {**card, "id": card_id}
        if card.get("url"):
            self.by_url.setdefault(card["url"], self.cards[card_id])
        return card_id

    def seen_web_results(self, content) -> None:
        """Pages Claude's own web search returned this turn: showable by URL."""
        for block in content or []:
            if getattr(block, "type", "") != "web_search_tool_result":
                continue
            for item in getattr(block, "content", None) or []:
                url = _https(getattr(item, "url", ""))
                if url and url not in self.by_url:
                    self.add(link_card({"url": url, "title": getattr(item, "title", ""),
                                        "page_age": getattr(item, "page_age", "")}))

    def pick(self, choices: list[dict]) -> str:
        shown, unknown = [], []
        for choice in choices[:MAX_SHOWN]:
            key = str(choice.get("id", "")).strip()
            card = self.cards.get(key) or self.by_url.get(key)
            if card is None:
                unknown.append(key)
                continue
            if any(c["id"] == card["id"] for c in self.picked):
                continue
            self.picked.append({**card, "note": str(choice.get("note", ""))[:200]})
            shown.append(card["id"])
        if unknown:
            return (f"Showing {len(shown)}. Not shown (not from this turn's results): {', '.join(unknown)}. "
                    "Use the card ids or URLs the tools gave you.")
        return f"Showing {len(shown)} cards."


current: ContextVar[CardBox | None] = ContextVar("current_cards", default=None)


def offer(card: dict) -> str | None:
    """Called by tools: register a result as a card for this reply. None outside a conversation."""
    box = current.get()
    return box.add(card) if box is not None else None


def tag(items: list[dict], build, strip: tuple[str, ...] = ()) -> list[dict]:
    """Offer each item as a card; return copies for Claude with the card id and without `strip` keys."""
    out = []
    for item in items:
        card_id = offer(build(item))
        slim = {k: v for k, v in item.items() if k not in strip}
        if card_id:
            slim["card"] = card_id
        out.append(slim)
    return out


SHOW_TOOL = {
    "name": "show_cards",
    "description": "Show results as rich cards in the user's chat (photo, rating, map, price, reviews, links they "
    "can tap): places, stays, flights, deals, news, money. Use the 'card' ids tools gave you, or the URL of a page "
    "your web search found this turn (an Airbnb listing, a flight, an article). Pick the best few, best first, "
    "with a short note on why each. Call it in the same response as your spoken answer; don't read links aloud.",
    "input_schema": {
        "type": "object",
        "properties": {
            "cards": {
                "type": "array",
                "maxItems": MAX_SHOWN,
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string", "description": "A card id like p1, or a page URL"},
                        "note": {"type": "string", "description": "Why this one, in a few words"},
                    },
                    "required": ["id"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["cards"],
        "additionalProperties": False,
    },
}
