"""Card benefits: credits and perks that quietly expire, tracked so none is wasted. Advice only.

  "My Platinum has a $50 Saks credit every six months"   -> add (card, name, amount, period, merchant)
  "how much is left on my card credits?"                  -> list: this period's used, left and days left
  "I want the Lululemon Define jacket but it's pricey"    -> wish_add (item, merchant, price)
  "what should I use my credits on?"                      -> plan: credits running out, matched to wishes

How "used" is known: statement credits in the bank feed (Plaid or email), i.e. refunds whose description
names the benefit's merchant and says "credit" (or the words the user gave in match), within the current
period. Purchases at that merchant this period are shown too, since a credit can take weeks to post.
The user can also say "I used my Saks credit" (used).

Reminders (once a day, from the proactive loop): when a credit still has money left, at 60, 30, 14, 7
and 2 days before it resets or expires, each said once. From 60 days out, wishes at that merchant are
matched ("with the credit you'd pay $23") and Buddy offers a price watch, which asks before it costs
anything. Buddy never buys anything; issuers change benefits, so the user's card terms win.

The public catalog ("look up my Platinum's benefits"): Claude searches only the issuers' own websites
(ISSUER_DOMAINS) and returns the card's published credits, annual fee and earn rates as facts in Buddy's
own words, each with its source page; anything not from an issuer page is dropped. Set up once
for every card in the background (setup: a Card benefits report in Reports and one message), kept in
fin_card_catalog and refreshed every REFRESH_DAYS (a year); a new card is looked up when it first appears.
Nothing is tracked until the user confirms (add_from_catalog, or card "all"). At the yearly refresh,
changes to tracked credits are reported.
Personal offers (Amex Offers, Chase Offers) and points balances aren't public: email or screenshots.
"""

import json
import logging
import re
import time
import urllib.parse
from datetime import date, datetime, timedelta, timezone

from .memory import Memory
from .tools import ToolError, local_now

log = logging.getLogger(__name__)

# Only the card issuers' own websites count as a source for published benefits.
ISSUER_DOMAINS = ["americanexpress.com", "chase.com", "capitalone.com", "citi.com", "bankofamerica.com",
                  "wellsfargo.com", "discover.com", "usbank.com", "barclaycardus.com", "barclays.us",
                  "synchrony.com", "navyfederal.org", "penfed.org", "apple.com", "goldman.com", "bilt.com",
                  "robinhood.com", "sofi.com"]
# Issuers usually change published benefits about once a year: looked up once (the setup), kept, refreshed yearly.
REFRESH_DAYS = int(__import__("os").getenv("BUDDY_CARD_REFRESH_DAYS", "365") or 365)
CATALOG_DAYS = REFRESH_DAYS  # a looked-up card is reused this long
RECHECK_DAYS = REFRESH_DAYS  # and then looked up again; changes to tracked credits are reported
CATALOG_SQL = """
CREATE TABLE IF NOT EXISTS fin_card_catalog (
    card_key TEXT PRIMARY KEY,         -- "amex platinum"
    card_name TEXT NOT NULL,           -- the issuer's name for it
    data TEXT NOT NULL,                -- {"annual_fee", "benefits": [...], "earn_rates": [...], "notes"}
    sources TEXT NOT NULL DEFAULT '[]',
    checked_at TEXT NOT NULL
);
"""
LOOKUP_PROMPT = """Today is {today}. Find the currently published benefits of this credit card: "{card}".
Use only the card issuer's own website (search it, and open the card's benefits or rates-and-fees page).
Report only facts stated there, in your own short words (no copied paragraphs). Leave out anything you can't
find on the issuer's site. Statement credits that reset (monthly, quarterly, every six months, yearly) or
one-time credits go in "benefits"; perks with no dollar amount (lounge access, status, insurance) go in "perks".
End your answer with one line of JSON, exactly this shape:
{{"card_name": "...", "annual_fee": 0, "benefits": [{{"name": "Saks credit", "amount": 50, "period":
"monthly|quarterly|semiannual|annual|once", "resets": "calendar|anniversary", "merchant": "saks", "enrollment":
true, "note": "short condition", "source": "https://..."}}], "perks": [{{"name": "...", "source": "https://..."}}],
"earn_rates": [{{"category": "flights", "rate": 5, "unit": "points per dollar", "note": "booked directly",
"source": "https://..."}}], "as_of_note": "what the page says about dates or changes"}}"""

PERIODS = ("monthly", "quarterly", "semiannual", "annual", "once")
REMIND_DAYS = (60, 30, 14, 7, 2)
# A monthly credit is always within 30 days of its end: it gets the last-week reminders only.
REMIND_BY_PERIOD = {"monthly": (7, 2)}
DEALS_FROM_DAYS = 31         # the background deal search runs this close to a credit's end (the 1-month notice)
DEALS_PER_DAY = 3
DEALS_SQL = """
CREATE TABLE IF NOT EXISTS fin_credit_deals (
    benefit_id INTEGER NOT NULL,
    period_end TEXT NOT NULL,          -- one search per credit period
    found_at TEXT NOT NULL,
    data TEXT NOT NULL,                -- {"sale": "...", "deals": [{"item", "price", "url", "note"}]}
    PRIMARY KEY (benefit_id, period_end)
);
"""
DEALS_PROMPT = """Today is {today}. {user} has a ${left:.0f} {credit} that must be used at {merchant} by {ends}.
Find the best current ways to use it: sales or promotions running now at {merchant}, and {count} specific items
in stock that cost about ${left:.0f} or a little more (so the credit covers most or all of it).{wishes}
Prefer the store's own website. Never invent a price or a sale; skip anything you can't confirm on a page.
End with one line of JSON, exactly this shape:
{{"sale": "a sale running now, with its end date, or empty", "deals": [{{"item": "...", "price": 0, "url":
"https://...", "note": "why it's a good use of the credit"}}]}}"""
WATCH_FROM_DAYS = 60

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS fin_benefits (
    id INTEGER PRIMARY KEY,
    card TEXT NOT NULL DEFAULT '',      -- the card's name as the user says it ("Amex Platinum")
    name TEXT NOT NULL,                 -- "Lululemon credit"
    amount REAL NOT NULL,               -- per period
    currency TEXT NOT NULL DEFAULT 'USD',
    period TEXT NOT NULL,               -- monthly | quarterly | semiannual | annual | once
    resets TEXT NOT NULL DEFAULT 'calendar',  -- calendar | anniversary (annual only)
    anniversary TEXT NOT NULL DEFAULT '',     -- MM-DD, for anniversary resets
    expires TEXT NOT NULL DEFAULT '',         -- YYYY-MM-DD, for one-time credits
    merchant TEXT NOT NULL DEFAULT '',        -- where it can be used ("lululemon")
    match TEXT NOT NULL DEFAULT '',           -- words in the statement credit's description, if not the merchant
    notes TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS fin_benefit_uses (
    id INTEGER PRIMARY KEY,
    benefit_id INTEGER NOT NULL,
    day TEXT NOT NULL,
    amount REAL NOT NULL,
    note TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS fin_wishes (
    id INTEGER PRIMARY KEY,
    item TEXT NOT NULL,                 -- "Define jacket"
    merchant TEXT NOT NULL DEFAULT '',  -- "lululemon"
    price REAL,                         -- what it costs, as the user said
    currency TEXT NOT NULL DEFAULT 'USD',
    notes TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
"""


def _quarter_start(d: date) -> date:
    return date(d.year, 3 * ((d.month - 1) // 3) + 1, 1)


def _add_months(d: date, months: int) -> date:
    m = d.month - 1 + months
    return date(d.year + m // 12, m % 12 + 1, 1)


def _anniversary(year: int, mmdd: str) -> date:
    month, day = (int(x) for x in mmdd.split("-"))
    try:
        return date(year, month, day)
    except ValueError:  # 02-29 in a common year
        return date(year, month, 28)


def period_bounds(b: dict, today: date) -> tuple[date, date]:
    """First and last day of the benefit's current period (the last day is when it's lost)."""
    p = b["period"]
    if p == "monthly":
        start = today.replace(day=1)
        return start, _add_months(start, 1) - timedelta(days=1)
    if p == "quarterly":
        start = _quarter_start(today)
        return start, _add_months(start, 3) - timedelta(days=1)
    if p == "semiannual":
        start = date(today.year, 1 if today.month <= 6 else 7, 1)
        return start, _add_months(start, 6) - timedelta(days=1)
    if p == "annual":
        if b.get("resets") == "anniversary" and b.get("anniversary"):
            start = _anniversary(today.year, b["anniversary"])
            if start > today:
                start = _anniversary(today.year - 1, b["anniversary"])
            return start, _anniversary(start.year + 1, b["anniversary"]) - timedelta(days=1)
        return date(today.year, 1, 1), date(today.year, 12, 31)
    created = datetime.fromisoformat(b["created_at"]).date()
    end = date.fromisoformat(b["expires"]) if b.get("expires") else created + timedelta(days=365)
    return created, end


def _words(text: str) -> list[str]:
    return [w for w in re.split(r"[,;/]+|\s{2,}", (text or "").lower()) if w.strip()]


def _key(card: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", " ", card.lower().replace("american express", "amex"))).strip()


def _official(url: str) -> bool:
    host = (urllib.parse.urlparse(str(url or "")).hostname or "").lower()
    return url.startswith("https://") and any(host == d or host.endswith("." + d) for d in ISSUER_DOMAINS)


def _last_json(text: str) -> dict | None:
    decoder, found = json.JSONDecoder(), None
    for i, ch in enumerate(text):
        if ch == "{":
            try:
                obj, _ = decoder.raw_decode(text[i:])
            except ValueError:
                continue
            if isinstance(obj, dict) and ("benefits" in obj or "card_name" in obj):
                found = obj
    return found


def clean_catalog(raw: dict) -> dict:
    """Keep only what an issuer page backs, in the shapes Buddy tracks."""
    def num(v):
        try:
            return round(float(v), 2)
        except (TypeError, ValueError):
            return None
    benefits = []
    for b in raw.get("benefits") or []:
        amount, period = num(b.get("amount")), str(b.get("period") or "").lower()
        if not (_official(b.get("source")) and amount and amount > 0 and period in PERIODS and b.get("name")):
            continue
        benefits.append({"name": str(b["name"])[:80], "amount": amount, "period": period,
                         "resets": "anniversary" if b.get("resets") == "anniversary" else "calendar",
                         "merchant": str(b.get("merchant") or "")[:60].lower(),
                         "enrollment": bool(b.get("enrollment")), "note": str(b.get("note") or "")[:160],
                         "source": b["source"]})
    perks = [{"name": str(p.get("name"))[:100], "source": p["source"]} for p in raw.get("perks") or []
             if p.get("name") and _official(p.get("source"))][:15]
    rates = [{"category": str(r.get("category"))[:40], "rate": num(r.get("rate")), "unit": str(r.get("unit") or "")[:30],
              "note": str(r.get("note") or "")[:120], "source": r["source"]} for r in raw.get("earn_rates") or []
             if r.get("category") and num(r.get("rate")) and _official(r.get("source"))][:15]
    fee = num(raw.get("annual_fee"))
    return {"card_name": str(raw.get("card_name") or "")[:80], "annual_fee": fee, "benefits": benefits,
            "perks": perks, "earn_rates": rates, "as_of_note": str(raw.get("as_of_note") or "")[:200]}


async def _search_issuer(memory: Memory, card: str) -> dict | None:
    """Claude with web search and fetch limited to the issuers' sites; the answer's last JSON line."""
    from . import usage
    from .agent import fallback_kwargs
    from .config import claude_client, settings
    from .tools import web_fetch

    client = claude_client()
    model = settings.chat_model
    fetch = {**web_fetch(4), "allowed_domains": ISSUER_DOMAINS}
    messages = [{"role": "user", "content": LOOKUP_PROMPT.format(today=local_now().strftime("%d %B %Y"), card=card)}]
    response = None
    for _ in range(4):  # the server-side search loop may pause and resume
        started = time.monotonic()
        response = await client.beta.messages.create(
            model=model, max_tokens=4_000, messages=messages,
            tools=[{"type": "web_search_20260209", "name": "web_search", "max_uses": 4,
                    "allowed_domains": ISSUER_DOMAINS}, fetch],
            output_config={"effort": "low"}, cache_control={"type": "ephemeral"}, **fallback_kwargs(model))
        if hasattr(response, "usage"):
            usage.record(memory, response.model, response.usage, purpose="card_lookup", response=response,
                         started=started)
        if response.stop_reason != "pause_turn":
            break
        messages.append({"role": "assistant", "content": response.content})
    text = "\n".join(b.text for b in response.content if getattr(b, "type", "") == "text")
    return _last_json(text)


DINING_WORDS = ("resy", "dining", "restaurant", "opentable", "tock", "dinner", "grubhub", "dine")
HOTEL_WORDS = ("hotel", "fine hotels", "fhr", "hotel collection", "the edit", "resort", "stay", "lodging")
SKIP_WORDS = ("uber", "lyft", "airline", "streaming", "entertainment", "clear", "global entry", "tsa", "walmart+",
              "equinox", "cell phone", "wireless", "doordash", "instacart", "dashpass")
PLACES_PROMPT = """Today is {today}. {user} has a ${left:.0f} {credit} to use by {ends}.
{what} Near: {where}.{taste}
Suggest {count} specific, named {kind} where this credit works (check on the program's or the place's own site
that it is eligible: {program}), currently open, well reviewed, and that fit {user}'s tastes. For each: the name,
the neighbourhood or city, why it's a good pick, a typical price, and a booking link (https). Never invent a place
or a price; skip anything you can't confirm on a page.
End with one line of JSON, exactly this shape:
{{"sale": "", "deals": [{{"item": "name, neighbourhood", "price": 0, "url": "https://...", "note": "why, and what to
book (e.g. dinner for two)"}}]}}"""


def credit_kind(credit: dict) -> str:
    """store (a shop: sales and items), dining (restaurants nearby), hotel (hotels), or skip (no deal to find)."""
    text = f"{credit.get('benefit', '')} {credit.get('merchant', '')}".lower()
    if any(w in text for w in SKIP_WORDS):
        return "skip"
    if any(w in text for w in DINING_WORDS):
        return "dining"
    if any(w in text for w in HOTEL_WORDS):
        return "hotel"
    return "store" if credit.get("merchant") else "skip"


def _where(memory: Memory) -> str:
    """The latest place the phone shared (area and city), or the city most of the user's spending is in."""
    try:
        here = json.loads(memory.get("location:current") or "null") or {}
    except ValueError:
        here = {}
    label = ", ".join(p for p in (here.get("area"), here.get("city")) if p)
    return label or (memory.get("user:city") or "")


def _tastes(memory: Memory, category: str) -> str:
    try:
        from .taste import Taste

        t = Taste(memory).get(category)
    except Exception:
        return ""
    parts = []
    if t["likes"]:
        parts.append("likes " + ", ".join(t["likes"][:6]))
    if t["dislikes"]:
        parts.append("avoids " + ", ".join(t["dislikes"][:4]))
    if t["budget"]:
        parts.append(f"budget {t['budget']}")
    return (" Their taste: " + "; ".join(parts) + ".") if parts else ""


def _trips(memory: Memory) -> str:
    try:
        rows = memory.db.execute("SELECT destination, month FROM travel_watches ORDER BY month LIMIT 3").fetchall()
    except Exception:
        return ""
    return "; ".join(f"{r['destination']} in {r['month']}" for r in rows)


def _places_prompt(memory: Memory, credit: dict, kind: str) -> str | None:
    from .config import settings

    where = _where(memory)
    if kind == "dining":
        if not where:
            return None
        what = "It's a dining credit, used at restaurants in its program."
        program = "Resy" if "resy" in credit["benefit"].lower() else "the card's dining program"
        taste, label, count = _tastes(memory, "food"), "restaurants", 4
    else:
        trips = _trips(memory)
        what = ("It's a hotel credit, used at hotels in the card's program (for example Fine Hotels + Resorts or The "
                "Hotel Collection), usually for a stay booked through the card's travel site."
                + (f" Upcoming trips: {trips}; prefer hotels there." if trips else ""))
        if not (where or trips):
            return None
        where = where or trips
        program = "the card issuer's hotel program"
        taste, label, count = _tastes(memory, "hotels"), "hotels (in trip destinations first, else an easy drive away)", 3
    return PLACES_PROMPT.format(today=local_now().strftime("%d %B %Y"), user=settings.user_name, left=credit["left"],
                                credit=credit["benefit"], ends=_nice(credit["ends"]), what=what, where=where or "unknown",
                                taste=taste, count=count, kind=label, program=program)


async def _search_deals(memory: Memory, credit: dict, wishes: list[dict]) -> dict | None:
    """Claude with web search: current sales and items that fit the credit at its merchant; for dining and
    hotel credits, named restaurants near the user or hotels at their trips, matched to their taste."""
    from . import usage
    from .agent import fallback_kwargs
    from .config import claude_client, settings
    from .tools import web_fetch

    client = claude_client()
    model = settings.chat_model
    wish_text = ("" if not wishes else " They want: " + "; ".join(
        f"{w['item']}" + (f" (about ${w['price']:.0f})" if w.get("price") else "") for w in wishes[:3]) +
        ". Check these first.")
    kind = credit_kind(credit)
    if kind in ("dining", "hotel"):
        prompt = _places_prompt(memory, credit, kind)
        if prompt is None:
            return {"sale": "", "deals": [], "needs": "location"}
    else:
        prompt = DEALS_PROMPT.format(today=local_now().strftime("%d %B %Y"), user=settings.user_name, left=credit["left"],
                                     credit=credit["benefit"], merchant=credit["merchant"].title(),
                                     ends=_nice(credit["ends"]), count=3, wishes=wish_text)
    messages = [{"role": "user", "content": prompt}]
    response = None
    for _ in range(4):
        started = time.monotonic()
        response = await client.beta.messages.create(
            model=model, max_tokens=2_500, messages=messages,
            tools=[{"type": "web_search_20260209", "name": "web_search", "max_uses": 3}, web_fetch(3)],
            output_config={"effort": "low"}, cache_control={"type": "ephemeral"}, **fallback_kwargs(model))
        if hasattr(response, "usage"):
            usage.record(memory, response.model, response.usage, purpose="credit_deals", response=response,
                         started=started)
        if response.stop_reason != "pause_turn":
            break
        messages.append({"role": "assistant", "content": response.content})
    text = "\n".join(b.text for b in response.content if getattr(b, "type", "") == "text")
    decoder, found = json.JSONDecoder(), None
    for i, ch in enumerate(text):
        if ch == "{":
            try:
                obj, _ = decoder.raw_decode(text[i:])
            except ValueError:
                continue
            if isinstance(obj, dict) and "deals" in obj:
                found = obj
    return found


class Benefits:
    def __init__(self, memory: Memory, search=None, search_deals=None):
        self.memory = memory
        memory.db.executescript(SCHEMA_SQL + CATALOG_SQL + DEALS_SQL)
        self.search = search or _search_issuer
        self.search_deals = search_deals or _search_deals

    # --- deals that make the most of a credit before it runs out ---------------------------
    def deals_for(self, benefit_id: int, period_end: str) -> dict | None:
        row = self.memory.db.execute("SELECT data FROM fin_credit_deals WHERE benefit_id = ? AND period_end = ?",
                                     (benefit_id, period_end)).fetchone()
        return json.loads(row["data"]) if row else None

    async def find_deals(self, credit: dict) -> dict:
        wishes = [w for w in self.wishes() if credit["merchant"] and w["merchant"]
                  and _same_shop(credit["merchant"], w["merchant"])]
        raw = await self.search_deals(self.memory, credit, wishes) or {}
        deals = []
        for d in raw.get("deals") or []:
            url = str(d.get("url") or "")
            try:
                price = round(float(d.get("price")), 2)
            except (TypeError, ValueError):
                continue
            if url.startswith("https://") and d.get("item") and price > 0:
                deals.append({"item": str(d["item"])[:90], "price": price, "url": url,
                              "you_pay": round(max(0.0, price - credit["left"]), 2), "note": str(d.get("note") or "")[:140]})
        data = {"sale": str(raw.get("sale") or "")[:200], "deals": deals[:4], "kind": credit_kind(credit)}
        if raw.get("needs"):  # no location yet: not saved, so it's tried again once the phone shares one
            return {**data, "needs": raw["needs"]}
        self.memory.db.execute("INSERT OR REPLACE INTO fin_credit_deals (benefit_id, period_end, found_at, data) "
                               "VALUES (?, ?, ?, ?)", (credit["id"], credit["ends"],
                                                       datetime.now(timezone.utc).isoformat(), json.dumps(data)))
        self.memory.db.commit()
        return data

    async def prepare_deals(self, today: date | None = None, per_day: int = DEALS_PER_DAY) -> int:
        """Background, daily: credits with money left that end within a month get one deal search per period,
        before their 1-month reminder. Monthly credits and ones with no store are skipped."""
        done = 0
        for p in self.plan(today, within=DEALS_FROM_DAYS):
            if done >= per_day:
                break
            kind = credit_kind(p)
            if p["maybe_pending"] or kind == "skip" or (p["period"] == "monthly" and kind == "store"):
                continue
            if self.deals_for(p["id"], p["ends"]) is not None:
                continue
            try:
                await self.find_deals(p)
            except Exception as e:
                log.info("credit deal search failed: %s", type(e).__name__)
                self.memory.db.execute("INSERT OR REPLACE INTO fin_credit_deals VALUES (?, ?, ?, ?)",
                                       (p["id"], p["ends"], datetime.now(timezone.utc).isoformat(),
                                        json.dumps({"sale": "", "deals": [], "failed": True})))
                self.memory.db.commit()
            done += 1
        return done

    # --- the public catalog: an issuer's published benefits ------------------------------
    def cached(self, card: str, days: int = CATALOG_DAYS) -> dict | None:
        row = self.memory.db.execute("SELECT * FROM fin_card_catalog WHERE card_key = ?", (_key(card),)).fetchone()
        if not row:
            return None
        if datetime.now(timezone.utc) - datetime.fromisoformat(row["checked_at"]) > timedelta(days=days):
            return None
        return {**json.loads(row["data"]), "checked": row["checked_at"][:10]}

    async def lookup(self, card: str, fresh: bool = False) -> dict:
        card = (card or "").strip()
        if not card:
            raise ToolError("Which card? For example: Amex Platinum, Chase Sapphire Reserve.")
        if not fresh and (hit := self.cached(card)):
            return {**hit, "from": "saved lookup"}
        raw = await self.search(self.memory, card)
        if not raw:
            raise ToolError(f"I couldn't find {card}'s benefits on the issuer's website. Check the card's name, or "
                            "share a screenshot of its benefits page.")
        data = clean_catalog(raw)
        if not (data["benefits"] or data["perks"] or data["earn_rates"]):
            raise ToolError(f"The issuer's pages I found for {card} didn't list benefits I could confirm.")
        now = datetime.now(timezone.utc).isoformat()
        sources = sorted({x["source"] for k in ("benefits", "perks", "earn_rates") for x in data[k]})
        self.memory.db.execute("INSERT OR REPLACE INTO fin_card_catalog (card_key, card_name, data, sources, "
                               "checked_at) VALUES (?, ?, ?, ?, ?)",
                               (_key(card), data["card_name"] or card, json.dumps(data), json.dumps(sources), now))
        self.memory.db.commit()
        return {**data, "checked": now[:10], "from": "issuer website"}

    def add_from_catalog(self, card: str, names: list[str] | None = None, anniversary: str = "") -> str:
        """Track the published credits the user confirmed (all of them when names is empty)."""
        row = self.memory.db.execute("SELECT * FROM fin_card_catalog WHERE card_key = ?", (_key(card),)).fetchone()
        if not row:
            raise ToolError("Look the card up first (lookup), then tell me which credits to track.")
        data, checked = json.loads(row["data"]), row["checked_at"][:10]
        wanted = [n.lower() for n in names or [] if n]
        picked = [b for b in data["benefits"] if not wanted or any(w in b["name"].lower() for w in wanted)]
        if not picked:
            raise ToolError("None of those match the card's published credits.")
        if any(b["resets"] == "anniversary" for b in picked) and not re.fullmatch(r"\d{2}-\d{2}", anniversary or ""):
            raise ToolError("Some of these reset on the card's anniversary: what month and day did you open the card "
                            "(MM-DD)?")
        existing = {(r["card"].lower(), r["name"].lower()) for r in self.all()}
        done = []
        for b in picked:
            if (card.lower(), b["name"].lower()) in existing:
                continue
            self.add(b["name"], b["amount"], b["period"], card, b["merchant"], "", b["resets"],
                     anniversary if b["resets"] == "anniversary" else "",
                     notes=(f"Published by the issuer, checked {checked}: {b['source']}"
                            + ("; needs enrollment" if b["enrollment"] else "") + (f"; {b['note']}" if b["note"] else "")))
            done.append(f"{b['name']} (${b['amount']:.0f} {b['period']})")
        need = [b["name"] for b in picked if b["enrollment"]]
        return ((f"Tracking {len(done)} credit{'s' if len(done) != 1 else ''} on {card}: " + "; ".join(done) + "."
                 if done else "Those credits are already tracked.")
                + (f" Enroll in the issuer's app first for: {', '.join(need)}." if need else "")
                + " Issuers change benefits, so your card's terms win.")

    # --- every card the user has -----------------------------------------------------------
    def known_cards(self) -> list[str]:
        """Every card Buddy knows: from the bank connections and statements (fin_cards), and named in benefits."""
        names: dict[str, str] = {}
        try:
            for r in self.memory.db.execute("SELECT name, issuer FROM fin_cards"):
                name = re.sub(r"[®™©]", "", f"{r['issuer'] or ''} {r['name'] or ''}").strip()
                if r["issuer"] and r["issuer"].lower() in (r["name"] or "").lower():
                    name = re.sub(r"[®™©]", "", r["name"]).strip()
                if len(_key(name)) >= 4:
                    names.setdefault(_key(name), name)
        except Exception:
            pass
        for b in self.all():
            if b["card"]:
                names.setdefault(_key(b["card"]), b["card"])
        return list(names.values())

    async def lookup_all(self, fresh: bool = False) -> list[dict]:
        """Each known card's published benefits (saved lookups reused for 30 days)."""
        out = []
        for card in self.known_cards():
            try:
                found = await self.lookup(card, fresh)
                out.append({"card": card, "card_name": found["card_name"], "annual_fee": found["annual_fee"],
                            "credits": [f"{b['name']}: ${b['amount']:.0f} {b['period']}" +
                                        (" (enroll first)" if b["enrollment"] else "") for b in found["benefits"]],
                            "perks": [p["name"] for p in found["perks"]],
                            "earn_rates": [f"{r['rate']:g} {r['unit']} on {r['category']}" for r in found["earn_rates"]],
                            "checked": found["checked"], "tracked": self._tracked_names(card)})
            except ToolError as e:
                out.append({"card": card, "not_found": str(e)})
        return out

    def _tracked_names(self, card: str) -> list[str]:
        return [b["name"] for b in self.all() if _key(b["card"]) == _key(card)]

    async def offer_new_cards(self, per_day: int = 2) -> list[str]:
        """Daily: a card Buddy hasn't looked up yet is looked up once, and its published credits offered."""
        texts = []
        for card in self.known_cards():
            mark = f"benefits:offered:{_key(card)}"
            if self.memory.get(mark) or self.cached(card, days=10 ** 5) or len(texts) >= per_day:
                continue
            self.memory.set(mark, local_now().date().isoformat())
            try:
                found = await self.lookup(card)
            except Exception as e:  # not an issuer card we can find, or no connection: offered once only
                log.info("benefits lookup for a new card failed: %s", type(e).__name__)
                continue
            credits = [b for b in found["benefits"] if b["name"] not in self._tracked_names(card)]
            if not credits:
                continue
            total = sum(b["amount"] * {"monthly": 12, "quarterly": 4, "semiannual": 2}.get(b["period"], 1) for b in credits)
            listing = "; ".join(f"{b['name']} ${b['amount']:.0f} {b['period']}" for b in credits[:8])
            texts.append(f"I looked up your {found['card_name'] or card} on the issuer's website: {len(credits)} "
                         f"credits, up to about ${total:,.0f} a year ({listing}). Want me to track them and remind you "
                         "before each one runs out? Issuers change these, so your card's terms win.")
        return texts

    async def setup(self, fresh: bool = False) -> tuple[int | None, str]:
        """The one-time setup (and the on-demand refresh): every known card looked up, a Card benefits report
        saved in Reports, and one message that asks which credits to track."""
        from .finance import purge_reports
        from .jobs import SCHEMA as JOBS_SCHEMA

        found = await self.lookup_all(fresh)
        for card in self.known_cards():
            self.memory.set(f"benefits:offered:{_key(card)}", local_now().date().isoformat())
        self.memory.set("benefits:setup_done", local_now().date().isoformat())
        good = [f for f in found if not f.get("not_found")]
        if not good:
            return None, ("I couldn't find published benefits for your cards on the issuers' websites yet. Tell me the "
                          "card names (\"Amex Platinum\"), or share a screenshot of a card's benefits page.")
        lines = [f"Published benefits of your cards, from each issuer's website. Saved and refreshed once a year "
                 f"(next: {(local_now().date() + timedelta(days=REFRESH_DAYS)):%B %Y}). Issuers change these, so your "
                 "card's terms win.", ""]
        total_year = 0.0
        for f in good:
            data = self.cached(f["card"]) or {}
            lines += [f"## {f['card_name'] or f['card']}", ""]
            if f.get("annual_fee") is not None:
                lines.append(f"Annual fee: ${f['annual_fee']:,.0f}")
            lines += ["", "| Credit | Amount | Resets | Tracked |", "|---|---|---|---|"]
            for b in data.get("benefits", []):
                total_year += b["amount"] * {"monthly": 12, "quarterly": 4, "semiannual": 2}.get(b["period"], 1)
                lines.append(f"| {b['name']}{' (enroll first)' if b['enrollment'] else ''} | ${b['amount']:,.0f} | "
                             f"{b['period']}{', card anniversary' if b['resets'] == 'anniversary' else ''} | "
                             f"{'yes' if b['name'] in f['tracked'] else 'no'} |")
            if f["perks"]:
                lines += ["", "Perks: " + "; ".join(f["perks"])]
            if f["earn_rates"]:
                lines += ["", "Earns: " + "; ".join(f["earn_rates"])]
            sources = sorted({x["source"] for k in ("benefits", "perks", "earn_rates") for x in data.get(k, [])})
            lines += ["", f"Checked {f['checked']}. Sources:"] + [f"- [{urllib.parse.urlparse(u).netloc}]({u})"
                                                               for u in sources[:4]] + [""]
        missing = [f["card"] for f in found if f.get("not_found")]
        if missing:
            lines.append("Not found on an issuer's website: " + ", ".join(missing) + ".")
        summary = (f"Card benefits are set up for {len(good)} card{'s' if len(good) != 1 else ''}: up to about "
                   f"${total_year:,.0f} a year in credits. The full list is in Reports. Which should I track and remind "
                   "you about? Say \"track all\", or name them.")
        now = datetime.now(timezone.utc).isoformat()
        self.memory.db.executescript(JOBS_SCHEMA)
        purge_reports(self.memory)
        job_id = self.memory.db.execute(
            "INSERT INTO jobs (kind, title, params, status, created_at, started_at, finished_at, summary, report, findings, "
            "sources, delivered) VALUES ('card_benefits', ?, '{}', 'done', ?, ?, ?, ?, ?, '[]', '[]', 1)",
            (f"Card benefits, {local_now():%B %Y}", now, now, now, summary, "\n".join(lines))).lastrowid
        self.memory.db.commit()
        return job_id, summary

    def add_all(self, anniversaries: dict[str, str] | None = None) -> str:
        """"Track all": every saved card's published credits; anniversary credits wait for the card's date."""
        done, waiting = [], []
        for row in self.memory.db.execute("SELECT * FROM fin_card_catalog ORDER BY card_name").fetchall():
            data = json.loads(row["data"])
            card = next((c for c in self.known_cards() if _key(c) == row["card_key"]), row["card_name"])
            day = (anniversaries or {}).get(row["card_key"], "")
            plain = [b["name"] for b in data["benefits"] if b["resets"] != "anniversary" or day]
            if plain:
                done.append(self.add_from_catalog(card, plain, day))
            waiting += [f"{b['name']} ({card})" for b in data["benefits"] if b["resets"] == "anniversary" and not day]
        if not done and not waiting:
            raise ToolError("No cards are set up yet: say \"set up my card benefits\".")
        return " ".join(done) + (f" These reset on the card's anniversary, so tell me each card's opening month and day "
                                 f"to track them: {', '.join(waiting)}." if waiting else "")

    async def recheck(self, today: date | None = None) -> list[str]:
        """Quarterly: cards whose credits came from the catalog are looked up again; what changed is reported."""
        notes = []
        for row in self.memory.db.execute("SELECT * FROM fin_card_catalog").fetchall():
            if datetime.now(timezone.utc) - datetime.fromisoformat(row["checked_at"]) < timedelta(days=RECHECK_DAYS):
                continue
            tracked = [b for b in self.all() if _key(b["card"]) == row["card_key"] and "Published by" in b["notes"]]
            try:  # every saved card is refreshed; only tracked credits get a message
                new = await self.lookup(row["card_name"], fresh=True)
            except ToolError:
                continue
            if not tracked:
                continue
            now = {b["name"].lower(): b for b in new["benefits"]}
            for b in tracked:
                n = now.get(b["name"].lower())
                if n is None:
                    notes.append(f"{b['card']}: the {b['name']} is no longer on the issuer's site.")
                elif abs(n["amount"] - b["amount"]) >= 0.01 or n["period"] != b["period"]:
                    notes.append(f"{b['card']}: the {b['name']} is now ${n['amount']:.0f} {n['period']} "
                                 f"(was ${b['amount']:.0f} {b['period']}).")
            known = {b["name"].lower() for b in tracked}
            for n in new["benefits"]:
                if n["name"].lower() not in known:
                    notes.append(f"{row['card_name']}: a new published credit, {n['name']} (${n['amount']:.0f} "
                                 f"{n['period']}). Say if you want it tracked.")
        return notes

    # --- benefits ------------------------------------------------------------------------
    def add(self, name: str, amount: float, period: str, card: str = "", merchant: str = "", match: str = "",
            resets: str = "calendar", anniversary: str = "", expires: str = "", currency: str = "USD",
            notes: str = "") -> str:
        name, period = (name or "").strip(), (period or "").strip().lower()
        if not name or not amount or float(amount) <= 0:
            raise ToolError("A benefit needs a name and an amount, e.g. a $50 Saks credit.")
        if period not in PERIODS:
            raise ToolError(f"How often does it reset? One of: {', '.join(PERIODS)}.")
        if resets == "anniversary" and not re.fullmatch(r"\d{2}-\d{2}", anniversary or ""):
            raise ToolError("For a credit that resets on the card anniversary, give the date as MM-DD.")
        if period == "once" and expires:
            try:
                date.fromisoformat(expires)
            except ValueError:
                raise ToolError("Give the expiry date as YYYY-MM-DD.") from None
        cur = self.memory.db.execute(
            "INSERT INTO fin_benefits (card, name, amount, currency, period, resets, anniversary, expires, merchant, "
            "match, notes, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (card.strip(), name, round(float(amount), 2), (currency or "USD").upper()[:3], period,
             resets if resets in ("calendar", "anniversary") else "calendar", anniversary or "", expires or "",
             (merchant or "").strip().lower(), (match or "").strip().lower(), notes.strip(),
             datetime.now(timezone.utc).isoformat()))
        self.memory.db.commit()
        b = self._get(cur.lastrowid)
        s = self.status(b)
        return (f"Tracking {self._label(b)}: ${b['amount']:.0f} {period}. This period ends {s['ends']}"
                f" ({s['days_left']} days), ${s['left']:.2f} left. Card benefits change, so check your card's terms.")

    def _get(self, benefit_id: int) -> dict:
        row = self.memory.db.execute("SELECT * FROM fin_benefits WHERE id = ?", (benefit_id,)).fetchone()
        if not row:
            raise ToolError(f"No benefit {benefit_id}.")
        return dict(row)

    def all(self) -> list[dict]:
        return [dict(r) for r in self.memory.db.execute("SELECT * FROM fin_benefits ORDER BY id")]

    def find(self, what) -> dict:
        what = str(what or "").strip().lower()
        items = self.all()
        hit = next((b for b in items if str(b["id"]) == what), None) or next(
            (b for b in items if what and (what in b["name"].lower() or what in b["merchant"]
                                           or what in f"{b['card']} {b['name']}".lower())), None)
        if not hit:
            raise ToolError(f"No card benefit matching '{what}'.")
        return hit

    def remove(self, what) -> str:
        b = self.find(what)
        self.memory.db.execute("DELETE FROM fin_benefit_uses WHERE benefit_id = ?", (b["id"],))
        self.memory.db.execute("DELETE FROM fin_benefits WHERE id = ?", (b["id"],))
        self.memory.db.commit()
        return f"Stopped tracking {self._label(b)}."

    def mark_used(self, what, amount: float | None = None, note: str = "") -> str:
        b = self.find(what)
        s = self.status(b)
        amount = s["left"] if amount is None else round(float(amount), 2)
        if amount <= 0:
            return f"Nothing left to use on {self._label(b)} this period."
        self.memory.db.execute("INSERT INTO fin_benefit_uses (benefit_id, day, amount, note) VALUES (?, ?, ?, ?)",
                               (b["id"], local_now().date().isoformat(), amount, note))
        self.memory.db.commit()
        s = self.status(b)
        return f"Noted ${amount:.2f} used on {self._label(b)}; ${s['left']:.2f} left until {s['ends']}."

    @staticmethod
    def _label(b: dict) -> str:
        return f"{b['card']} {b['name']}".strip() if b["card"] and b["card"].lower() not in b["name"].lower() else b["name"]

    # --- how much is used ----------------------------------------------------------------
    def _transactions(self, start: date, end: date) -> list[dict]:
        try:
            return [dict(r) for r in self.memory.db.execute(
                "SELECT day, amount, merchant, card FROM transactions WHERE day >= ? AND day <= ?",
                (start.isoformat(), end.isoformat()))]
        except Exception:  # no money data yet
            return []

    def status(self, b: dict, today: date | None = None) -> dict:
        today = today or local_now().date()
        start, end = period_bounds(b, today)
        words = _words(b["match"]) or _words(b["merchant"])
        credited = spent = 0.0
        for t in self._transactions(start, min(end, today)):
            desc = t["merchant"].lower()
            if not words or not any(w in desc for w in words):
                continue
            if t["amount"] < 0 and (b["match"] or "credit" in desc):
                credited += -t["amount"]         # the statement credit posted
            elif t["amount"] > 0:
                spent += t["amount"]             # bought there; the credit may still be on its way
        told = sum(r[0] for r in self.memory.db.execute(
            "SELECT amount FROM fin_benefit_uses WHERE benefit_id = ? AND day >= ? AND day <= ?",
            (b["id"], start.isoformat(), end.isoformat())))
        used = min(b["amount"], round(credited + told, 2))
        pending = round(max(0.0, min(spent, b["amount"]) - used), 2) if spent else 0.0
        return {"id": b["id"], "benefit": self._label(b), "amount": b["amount"], "currency": b["currency"],
                "period": b["period"], "starts": start.isoformat(), "ends": end.isoformat(),
                "days_left": (end - today).days, "used": used, "left": round(b["amount"] - used, 2),
                "spent_there": round(spent, 2), "maybe_pending": pending, "merchant": b["merchant"],
                "expired": end < today}

    def overview(self, today: date | None = None) -> list[dict]:
        out = [self.status(b, today) for b in self.all()]
        return sorted((s for s in out if not s["expired"]), key=lambda s: s["days_left"])

    # --- wishes --------------------------------------------------------------------------
    def wish_add(self, item: str, merchant: str = "", price: float | None = None, currency: str = "USD",
                 notes: str = "") -> str:
        item = (item or "").strip()
        if not item:
            raise ToolError("What do they want?")
        self.memory.db.execute(
            "INSERT INTO fin_wishes (item, merchant, price, currency, notes, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (item, (merchant or "").strip().lower(), round(float(price), 2) if price else None,
             (currency or "USD").upper()[:3], notes.strip(), datetime.now(timezone.utc).isoformat()))
        self.memory.db.commit()
        match = [s for s in self.overview() if s["left"] > 0 and merchant and _same_shop(s["merchant"], merchant)]
        extra = f" Your {match[0]['benefit']} has ${match[0]['left']:.0f} left until {match[0]['ends']}." if match else ""
        return f"Added to the wish list: {item}{f' ({merchant})' if merchant else ''}.{extra}"

    def wishes(self) -> list[dict]:
        return [dict(r) for r in self.memory.db.execute("SELECT * FROM fin_wishes ORDER BY id")]

    def wish_remove(self, what) -> str:
        what = str(what or "").strip().lower()
        hit = next((w for w in self.wishes() if str(w["id"]) == what or (what and what in w["item"].lower())), None)
        if not hit:
            raise ToolError(f"Nothing on the wish list matching '{what}'.")
        self.memory.db.execute("DELETE FROM fin_wishes WHERE id = ?", (hit["id"],))
        self.memory.db.commit()
        return f"Removed {hit['item']} from the wish list."

    # --- plan and reminders --------------------------------------------------------------
    def plan(self, today: date | None = None, within: int = WATCH_FROM_DAYS) -> list[dict]:
        """Credits with money left that run out within `within` days, each with the wishes it could pay for."""
        wishes = self.wishes()
        out = []
        for s in self.overview(today):
            if s["left"] <= 0 or s["days_left"] > within:
                continue
            ideas = []
            for w in wishes:
                if s["merchant"] and w["merchant"] and _same_shop(s["merchant"], w["merchant"]):
                    idea = {"item": w["item"], "price": w["price"]}
                    if w["price"]:
                        idea["you_pay"] = round(max(0.0, w["price"] - s["left"]), 2)
                    ideas.append(idea)
            out.append({**s, "ideas": ideas})
        return out

    def alerts(self, today: date | None = None) -> list[dict]:
        """Reminders due today, each {text, mark}; call told(alert) once it reached the user."""
        today = today or local_now().date()
        out = []
        for p in self.plan(today, within=max(REMIND_DAYS)):
            steps = REMIND_BY_PERIOD.get(p["period"], REMIND_DAYS)
            step = next((d for d in sorted(steps) if p["days_left"] <= d), None)
            if step is None:
                continue
            mark = f"benefit:told:{p['id']}:{p['ends']}:{step}"
            if self.memory.get(mark):
                continue
            when = "today" if p["days_left"] == 0 else f"in {p['days_left']} days ({_nice(p['ends'])})"
            text = f"Your {p['benefit']} has ${p['left']:.0f} left and runs out {when}."
            found = self.deals_for(p["id"], p["ends"]) or {}
            if p["maybe_pending"]:
                text += f" You spent ${p['spent_there']:.0f} there this period; the credit may not have posted yet."
            elif credit_kind(p) == "dining" and not _where(self.memory):
                text += (" Turn on Share my location in Settings and I'll suggest restaurants near you that take it.")
            elif found.get("deals") or found.get("sale"):
                if found.get("sale"):
                    text += f" {p['merchant'].title()} has {found['sale']}."
                picks = found.get("deals") or []
                if picks and found.get("kind") in ("dining", "hotel"):
                    text += (" Places near you it covers: " if found["kind"] == "dining" else " Hotels it covers: ") + \
                        "; ".join(f"{d['item']}, about ${d['price']:.0f}" + (f" ({d['note']})" if d["note"] else "")
                                  + f" {d['url']}" for d in picks[:3]) + "."
                elif picks:
                    text += " Good ways to use it: " + "; ".join(
                        f"{d['item']} ${d['price']:.0f} (you'd pay ${d['you_pay']:.0f}) {d['url']}" for d in picks[:3]) + "."
                text += " You decide; I don't buy anything."
            elif p["ideas"]:
                i = p["ideas"][0]
                if i.get("price"):
                    text += f" You wanted the {i['item']} (${i['price']:.0f}): with the credit you'd pay ${i['you_pay']:.0f}."
                else:
                    text += f" You wanted the {i['item']}."
                text += " Want me to watch for a deal on it?"
            elif p["merchant"]:
                text += f" Anything you've wanted from {p['merchant'].title()}? I can look for deals."
            out.append({"text": text, "mark": mark, "benefit": p["id"]})
        return out

    def told(self, alert: dict) -> None:
        self.memory.set(alert["mark"], local_now().date().isoformat())


def _same_shop(a: str, b: str) -> bool:
    a, b = a.lower().strip(), b.lower().strip()
    return bool(a and b and (a in b or b in a))


def _nice(day: str) -> str:
    d = date.fromisoformat(day)
    return f"{d:%b} {d.day}"


BENEFITS_TOOL = {
    "name": "card_benefits",
    "description": "The user's credit card credits and perks that expire (store, travel, dining, streaming credits...) "
    "and their wish list. add: when they tell you about a benefit (card, name, amount per period, period, merchant "
    "where it's used; resets anniversary + anniversary MM-DD for card-year credits; expires YYYY-MM-DD for one-time "
    "credits; match = words in the statement credit if not the merchant). Never invent a card's benefits: add only "
    "what the user tells you or confirms, and say issuers can change them. list: used, left and days left this "
    "period (statement credits are read from the bank feed). used (what, amount; all that's left if no amount). "
    "remove (what). wish_add (item, merchant, price) when they say they want something but it's expensive; wishes; "
    "wish_remove (what). plan: credits running out within 60 days with wishes they could pay for. To watch a deal, "
    "use price_watch (it asks first). You suggest; the user buys. "
    "lookup (card; fresh to skip the saved copy): the card's published credits, perks, annual fee and earn rates "
    "from the issuer's own website, with source links and the date checked (a few cents; saved 30 days). Show "
    "them and ask which to track. lookup_all: the same for every card Buddy knows (bank connections, statements, "
    "benefits), with what's already tracked; use it for 'what benefits do all my cards have?'. setup (fresh to "
    "re-check now): looks up every card in the background, once; saved and refreshed yearly, a report in Reports. "
    "deals (what): search now for current sales and items that fit a credit's amount at its store (a few cents; "
    "Buddy also does this by itself about a month before a credit runs out, and puts the deals in that reminder). "
    "Then add_from_catalog (card \"all\" for every saved card, when they say \"track all\") (card, names; empty = all; anniversary MM-DD if any reset on "
    "the card anniversary). Say which need enrollment. Personal offers (Amex Offers, Chase Offers) and points "
    "balances aren't public: they come from the issuer's emails or a screenshot the user shares.",
    "input_schema": {"type": "object", "properties": {
        "action": {"type": "string", "enum": ["add", "list", "used", "remove", "wish_add", "wishes", "wish_remove",
                                              "plan", "lookup", "lookup_all", "setup", "add_from_catalog", "deals"]},
        "names": {"type": "array", "items": {"type": "string"}}, "fresh": {"type": "boolean"},
        "what": {"type": "string"}, "card": {"type": "string"}, "name": {"type": "string"},
        "amount": {"type": "number"}, "period": {"type": "string", "enum": list(PERIODS)},
        "resets": {"type": "string", "enum": ["calendar", "anniversary"]},
        "anniversary": {"type": "string", "description": "MM-DD"},
        "expires": {"type": "string", "description": "YYYY-MM-DD"},
        "merchant": {"type": "string"}, "match": {"type": "string"},
        "item": {"type": "string"}, "price": {"type": "number"}, "currency": {"type": "string"},
        "notes": {"type": "string"}},
        "required": ["action"], "additionalProperties": False},
}


async def run_tool_async(b: Benefits, args: dict) -> str:
    a = args.get("action")
    if a == "lookup":
        return json.dumps(await b.lookup(str(args.get("card") or ""), bool(args.get("fresh"))), ensure_ascii=False)
    if a == "lookup_all":
        found = await b.lookup_all(bool(args.get("fresh")))
        return json.dumps(found, ensure_ascii=False) if found else ("No cards yet: connect a bank, share a statement, or "
                                                                    "tell me which cards you have.")
    if a == "deals":
        credit = next((s for s in b.overview() if s["id"] == b.find(args.get("what") or "")["id"]), None)
        if not credit or credit["left"] <= 0:
            return "That credit has nothing left this period."
        found = await b.find_deals(credit)
        return json.dumps({"credit": credit["benefit"], "left": credit["left"], "ends": credit["ends"], **found},
                          ensure_ascii=False)
    if a == "setup":
        b.memory.set("benefits:setup_requested", "fresh" if args.get("fresh") else "1")
        return ("Started in the background: every card's published benefits from the issuers' websites. It takes a "
                "few minutes; the full list goes in Reports and I'll ask which credits to track.")
    if a == "add_from_catalog" and str(args.get("card") or "").strip().lower() in ("all", "all cards", "every card"):
        return b.add_all()
    if a == "add_from_catalog":
        return b.add_from_catalog(str(args.get("card") or ""), list(args.get("names") or []),
                                  str(args.get("anniversary") or ""))
    return run_tool(b, args)


def run_tool(b: Benefits, args: dict) -> str:
    a = args.get("action")
    if a == "add":
        return b.add(str(args.get("name") or ""), float(args.get("amount") or 0), str(args.get("period") or ""),
                     str(args.get("card") or ""), str(args.get("merchant") or ""), str(args.get("match") or ""),
                     str(args.get("resets") or "calendar"), str(args.get("anniversary") or ""),
                     str(args.get("expires") or ""), str(args.get("currency") or "USD"), str(args.get("notes") or ""))
    if a == "list":
        found = b.overview()
        return json.dumps(found) if found else "No card benefits tracked yet. Ask which credits their cards have."
    if a == "used":
        return b.mark_used(args.get("what") or "", args.get("amount"), str(args.get("notes") or ""))
    if a == "remove":
        return b.remove(args.get("what") or "")
    if a == "wish_add":
        return b.wish_add(str(args.get("item") or ""), str(args.get("merchant") or ""), args.get("price"),
                          str(args.get("currency") or "USD"), str(args.get("notes") or ""))
    if a == "wishes":
        found = b.wishes()
        return json.dumps(found) if found else "The wish list is empty."
    if a == "wish_remove":
        return b.wish_remove(args.get("what") or "")
    if a == "plan":
        found = b.plan()
        return json.dumps(found) if found else "No credits with money left run out in the next 60 days."
    raise ToolError("Unknown card_benefits action.")
