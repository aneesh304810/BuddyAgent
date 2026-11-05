"""Card benefits: credits and perks that quietly expire, tracked so none is wasted. Advice only.

  "My Platinum has a $50 Saks credit every six months"   -> add (card, name, amount, period, merchant)
  "how much is left on my card credits?"                  -> list: this period's used, left and days left
  "I want the Lululemon Define jacket but it's pricey"    -> wish_add (item, merchant, price)
  "what should I use my credits on?"                      -> plan: credits running out, matched to wishes

How "used" is known: the card's own statement. Money back on the card the credit belongs to (its connected
last 4) that says credit or reimbursement goes to the tracked credit on that card it names best ("PLATINUM UBER
ONE CREDIT" -> the Uber One credit, not Uber Cash); lines whose wording names none ("AMEX PLATINUM BENEFIT") are
sorted by Claude once a day (link_credits). Plaid often labels these credits income or a transfer in: plaid.py
keeps them with the card's charges. Purchases at that merchant this period are shown too, since a credit can
take weeks to post. Reminders say what the statement shows ("no statement credit through Oct 2"), and several
due the same morning are one message.
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
# Everything Buddy needs to handle a card comes back in this one shape, for any card from any issuer, so a new
# card needs no new code: how each credit shows on the statement (statement_words), what kind of credit it is
# (which decides deals, places or a how-to note), the perks of this exact card, and earn rates in fixed units.
CATALOG_VERSION = 2
CREDIT_KINDS = ("store", "dining", "hotel", "travel", "rides", "streaming", "membership", "other")
RATE_UNITS = ("percent", "points", "miles")
LOOKUP_PROMPT = """Today is {today}. Find the currently published benefits of this credit card: "{card}".
Use only the card issuer's own website (search it, and open the card's benefits or rates-and-fees page).
Report only facts stated there, in your own short words (no copied paragraphs). Leave out anything you can't
find on the issuer's site. Statement credits that reset (monthly, quarterly, every six months, yearly) or
one-time credits go in "benefits"; perks with no dollar amount (lounge access, status, insurance) go in "perks".
Only this exact card: leave out perks or credits the page says are for a different card (e.g. "Flex only").
For each credit: "kind" is one of store (a shop), dining (restaurants), hotel, travel (airline fees, CLEAR,
Global Entry), rides (Uber, Lyft), streaming (digital entertainment), membership (a subscription such as Walmart+
or Uber One), other; "statement_words" are 1-3 short lowercase words that would appear on the card statement
line when this credit posts (usually the merchant or program name, e.g. ["resy"], ["clear"], ["airline"]),
never generic words like credit, card, platinum or the issuer's name.
Earn rates: "unit" is percent (cash back), points or miles; "reward" names it ("cash back", "Ultimate Rewards
points", "Membership Rewards points"); "category" is short (under 60 characters).
End your answer with one line of JSON, exactly this shape:
{{"card_name": "...", "annual_fee": 0, "benefits": [{{"name": "Saks credit", "amount": 50, "period":
"monthly|quarterly|semiannual|annual|once", "resets": "calendar|anniversary", "merchant": "saks", "kind": "store",
"statement_words": ["saks"], "enrollment": true, "note": "short condition", "source": "https://..."}}],
"perks": [{{"name": "...", "source": "https://..."}}], "earn_rates": [{{"category": "flights", "rate": 5,
"unit": "points", "reward": "Membership Rewards points", "note": "booked directly", "source": "https://..."}}],
"as_of_note": "what the page says about dates or changes"}}"""

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


# Words in a credit's name that don't say which credit it is.
NAME_NOISE = {"credit", "credits", "card", "statement", "the", "and", "for", "fee", "fees", "annual", "monthly",
              "quarterly", "semiannual", "yearly", "platinum", "gold", "green", "amex", "american", "express", "chase",
              "sapphire", "reserve", "preferred", "plus", "membership", "one", "selected", "your", "with", "per", "year",
              "month", "benefit", "cash", "back", "up", "to", "of", "on", "in", "at"}
# Money back on a card that is a statement credit, not a refund of a purchase.
CREDIT_TEXT = re.compile(r"(?i)\b(credit|reimburs\w*|cr)\b")


def _name_words(text: str) -> list[str]:
    return sorted({w for w in re.findall(r"[a-z0-9]+", (text or "").lower()) if len(w) >= 3 and w not in NAME_NOISE})


LINK_PROMPT = """These are statement credits (money back) on {user}'s credit cards, and the card credits {user} tracks.
For each statement line, say which tracked credit it is, using the card (last 4) and the wording. If none fits (a
refund, a welcome bonus, points redeemed), say null. Don't guess between two credits: null.
Tracked credits: {credits}
Statement lines: {lines}
Answer with one line of JSON only: {{"links": [{{"n": 0, "credit_id": 12}}]}}"""


async def _ask_links(memory: Memory, credits: list[dict], lines: list[dict]) -> dict | None:
    from . import usage
    from .agent import fallback_kwargs
    from .config import claude_client, settings

    model = settings.fast_model
    started = time.monotonic()
    prompt = LINK_PROMPT.format(user=settings.user_name or "the user",
                                credits=json.dumps(credits, ensure_ascii=False), lines=json.dumps(lines, ensure_ascii=False))
    response = await claude_client().beta.messages.create(model=model, max_tokens=1_500,
                                                          messages=[{"role": "user", "content": prompt}],
                                                          **fallback_kwargs(model))
    if hasattr(response, "usage"):
        usage.record(memory, response.model, response.usage, purpose="card_credit_links", response=response,
                     started=started)
    return _last_json("\n".join(b.text for b in response.content if getattr(b, "type", "") == "text"))


def _words(text: str) -> list[str]:
    return [w for w in re.split(r"[,;/]+|\s{2,}", (text or "").lower()) if w.strip()]


GENERIC_CARD = re.compile(r"(?i)^(?:ultimate rewards|credit card|rewards card|rewards|card|visa|mastercard|"
                          r"visa signature|visa infinite|world (?:elite )?mastercard|signature|cardmember|account|"
                          r"american express|amex|chase|citi|capital one|bank of america|wells fargo|discover)?$")
CARD_CHOICES = {"chase": "Sapphire Preferred, Sapphire Reserve, Freedom Unlimited, Freedom Flex, Ink",
                "american": "Platinum, Gold, Green, Blue Cash Everyday, Blue Cash Preferred",
                "citi": "Strata Premier, Double Cash, Custom Cash", "capital": "Venture X, Venture, Savor, Quicksilver"}


# The issuer's cards, most specific first, for spotting a card's name in emails and statements.
CARD_NAMES = {
    "chase": ["Sapphire Reserve", "Sapphire Preferred", "Freedom Unlimited", "Freedom Flex", "Freedom Rise", "Freedom",
              "Ink Business Preferred", "Ink Business Unlimited", "Ink Business Cash", "Ink Preferred", "Ink Unlimited",
              "Ink Cash", "Amazon Prime Visa", "United Explorer", "United Quest", "United Club", "Southwest Rapid Rewards",
              "Marriott Bonvoy Boundless", "IHG One Rewards", "World of Hyatt", "Aeroplan"],
    "american": ["Platinum", "Gold", "Green", "Blue Cash Everyday", "Blue Cash Preferred", "Delta SkyMiles Reserve",
                 "Delta SkyMiles Platinum", "Delta SkyMiles Gold", "Hilton Honors Aspire", "Hilton Honors Surpass",
                 "Marriott Bonvoy Brilliant", "Business Platinum", "Business Gold", "Blue Business Plus"],
    "citi": ["Strata Elite", "Strata Premier", "Double Cash", "Custom Cash", "Costco Anywhere"],
    "capital": ["Venture X", "Venture", "VentureOne", "Savor", "SavorOne", "Quicksilver"],
}
# Annual fees that point to one card (US, as published; issuers change them, so this is a clue, not proof).
FEE_CLUES = {"chase": {95: "Sapphire Preferred", 550: "Sapphire Reserve", 795: "Sapphire Reserve"},
             "american": {695: "Platinum", 895: "Platinum", 250: "Gold", 325: "Gold", 150: "Green"},
             "capital": {395: "Venture X"}}
# Statement credits only one card gives.
CREDIT_CLUES = {"chase": [(r"travel credit", "Sapphire Reserve"), (r"hotel credit", "Sapphire Preferred")],
                "american": [(r"airline fee|saks|uber cash|digital entertainment|platinum", "Platinum"),
                             (r"dining credit|dunkin", "Gold")]}


def is_generic(name: str, issuer: str = "") -> bool:
    """'Ultimate Rewards', 'CREDIT CARD', 'American Express': the bank's label, not which card it is."""
    rest = re.sub(r"[®™©]", "", name or "").strip()
    for word in (issuer or "").split():
        rest = re.sub(rf"(?i)\b{re.escape(word)}\b", "", rest)
    return bool(GENERIC_CARD.match(re.sub(r"\s+", " ", rest).strip()))


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
                         "kind": b.get("kind") if b.get("kind") in CREDIT_KINDS else "",
                         "statement_words": _statement_words(b.get("statement_words")),
                         "source": b["source"]})
    card_name = str(raw.get("card_name") or "")[:80]
    perks = [{"name": str(p.get("name"))[:100], "source": p["source"]} for p in raw.get("perks") or []
             if p.get("name") and _official(p.get("source")) and _perk_applies(str(p["name"]), card_name)][:15]
    rates = [{"category": str(r.get("category"))[:80], "rate": num(r.get("rate")), "unit": str(r.get("unit") or "")[:30],
              "reward": str(r.get("reward") or "")[:40], "note": str(r.get("note") or "")[:120], "source": r["source"]}
             for r in raw.get("earn_rates") or []
             if r.get("category") and num(r.get("rate")) and _official(r.get("source"))][:15]
    fee = num(raw.get("annual_fee"))
    return {"version": CATALOG_VERSION, "card_name": card_name, "annual_fee": fee, "benefits": benefits,
            "perks": perks, "earn_rates": rates, "as_of_note": str(raw.get("as_of_note") or "")[:200]}


def _statement_words(v) -> list[str]:
    words = v if isinstance(v, list) else str(v or "").split(",")
    out = []
    for w in words:
        w = " ".join(str(w).lower().split())[:30]
        if w and w not in NAME_NOISE and w not in out:
            out.append(w)
    return out[:3]


def _perk_applies(perk: str, card: str) -> bool:
    """'Cell Phone Protection (Freedom Flex only)' on a Chase Freedom page: not this card's perk."""
    only = re.search(r"(?i)\b([a-z][\w+ ]{2,40}?)\s+only\b", perk or "")
    if not only:
        return True
    words = [w for w in _key(only.group(1)).split() if w not in ("the", "card", "cardmembers", "for")]
    return not words or all(w in _key(card).split() for w in words)


def _rate_text(r: dict) -> str:
    """'3 percent cash back on dining' -> '3% cash back on dining'; '5 points per dollar' -> '5x points'."""
    category = str(r.get("category") or "").strip()
    if category.count("(") > category.count(")"):  # cut short when it was saved: drop the unfinished aside
        category = category[:category.rindex("(")].strip()
    unit = str(r.get("unit") or "").strip()
    rate = r.get("rate") or 0
    low = unit.lower()
    if low in RATE_UNITS:  # the current shape: unit + reward
        reward = str(r.get("reward") or ("cash back" if low == "percent" else low)).strip()
        head = f"{rate:g}% {reward}" if low == "percent" else f"{rate:g}x {reward}"
        return f"{head} on {category}" if category else head
    if "percent" in low or low.startswith("%"):
        head = f"{rate:g}% " + re.sub(r"(?i)^(percent|%)\s*", "", unit).strip()
    elif re.search(r"(?i)(points?|miles?) per dollar", low):
        head = f"{rate:g}x " + re.sub(r"(?i)\s*per dollar.*", "", unit).strip()
    else:
        head = f"{rate:g} {unit}".strip()
    return f"{head.strip()} on {category}" if category else head.strip()


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
    kind = credit.get("kind")
    if kind in CREDIT_KINDS:
        return kind if kind in ("store", "dining", "hotel") else "skip"
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
    def cached(self, card: str, days: int = CATALOG_DAYS, any_version: bool = False) -> dict | None:
        row = self.memory.db.execute("SELECT * FROM fin_card_catalog WHERE card_key = ?", (_key(card),)).fetchone()
        if not row:
            return None
        if datetime.now(timezone.utc) - datetime.fromisoformat(row["checked_at"]) > timedelta(days=days):
            return None
        data = json.loads(row["data"])
        if not any_version and data.get("version", 1) < CATALOG_VERSION:
            return None  # saved in an older shape: looked up again (recheck), shown as it is meanwhile
        return {**data, "checked": row["checked_at"][:10]}

    def _published(self, b: dict) -> dict:
        """The issuer lookup's entry for a tracked credit (kind, statement words, note), if it came from one."""
        saved = self.cached(b["card"], days=10 ** 5, any_version=True) or {}
        return next((x for x in saved.get("benefits", []) if x["name"].lower() == b["name"].lower()), {})

    async def lookup(self, card: str, fresh: bool = False) -> dict:
        card = self.resolve_card((card or "").strip())
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
        for pub in data["benefits"]:  # tracked credits learn how they show on the statement
            if pub["statement_words"]:
                self.memory.db.execute("UPDATE fin_benefits SET match = ? WHERE lower(name) = ? AND match = '' AND "
                                       "notes LIKE 'Published by%'", (", ".join(pub["statement_words"]),
                                                                      pub["name"].lower()))
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
            self.add(b["name"], b["amount"], b["period"], card, b["merchant"], ", ".join(b.get("statement_words") or []),
                     b["resets"],
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
    def connected_cards(self) -> list[dict]:
        """Credit cards from the banks connected through Plaid (real banks, not sandbox), with issuer and last 4."""
        try:
            rows = self.memory.db.execute(
                "SELECT a.name, a.mask, i.institution FROM plaid_accounts a JOIN plaid_items i ON i.item_id = a.item_id "
                "WHERE a.type = 'credit' AND COALESCE(i.env, 'production') != 'sandbox' ORDER BY i.institution, a.name"
            ).fetchall()
        except Exception:
            return []
        out = []
        for r in rows:
            name = re.sub(r"[®™©]", "", r["name"] or "").strip()
            issuer = (r["institution"] or "").strip()
            mask = r["mask"] or ""
            told = self.memory.get(f"card:name:{mask}") if mask else None  # "my Chase 0457 is a Sapphire Preferred"
            if told:
                name = told
            full = name if issuer and issuer.lower().split()[0] in name.lower() else f"{issuer} {name}".strip()
            out.append({"card": full, "issuer": issuer, "last4": mask, "account_name": name,
                        "generic": not told and is_generic(name, issuer)})
        return out

    def name_card(self, last4: str, name: str) -> str:
        """The user says which card a generically named connected account is."""
        last4, name = re.sub(r"\D", "", str(last4 or ""))[-4:], re.sub(r"[®™©]", "", str(name or "")).strip()
        match = [c for c in self.connected_cards() if c["last4"] == last4]
        if not match or not name:
            raise ToolError("Which card (its last 4 digits) and what is it called, e.g. 0457 Sapphire Preferred?")
        self.memory.set(f"card:name:{last4}", name[:60])
        self.memory.set(f"benefits:offered:{_key(match[0]['card'])}", "")
        self.memory.set("benefits:check_new", "1")  # look the named card up now
        return f"Got it: ••{last4} is your {match[0]['issuer']} {name}. I'm looking up its published benefits now."

    def annual_fee_hint(self, last4: str) -> str:
        """The annual fee charged on this card, from its own transactions: a hint for which card it is."""
        try:
            row = self.memory.db.execute(
                "SELECT amount, day FROM transactions WHERE card = ? AND amount > 0 AND (lower(merchant) LIKE "
                "'%annual%fee%' OR lower(merchant) LIKE '%membership fee%' OR lower(merchant_raw) LIKE '%annual%fee%') "
                "ORDER BY day DESC LIMIT 1", (last4,)).fetchone()
        except Exception:
            return ""
        return f"a ${row['amount']:,.0f} annual fee was charged on {row['day']}" if row else ""

    def identify(self, card: dict) -> tuple[str, str, str] | None:
        """(name, how sure: sure|likely, the clue) for a connected card whose bank label doesn't say which card it is,
        from Buddy's own data: a card email or statement naming it with these last 4, its annual fee, its credits."""
        issuer = (card["issuer"] or "").lower().split()[0] if card["issuer"] else ""
        names, last4 = CARD_NAMES.get(issuer, []), card["last4"]
        if not last4:
            return None
        db = self.memory.db
        try:  # a statement or card email Buddy read for these last 4 (finance.card saved its name)
            for r in db.execute("SELECT name FROM fin_cards WHERE last4 = ?", (last4,)):
                hit = next((n for n in names if n.lower() in (r["name"] or "").lower()), None)
                if hit:
                    return hit, "sure", "your card statements"
        except Exception:
            pass
        try:  # emails that name the card and its last 4 ("Sapphire Preferred ending in 0457")
            for r in db.execute("SELECT title, body FROM records WHERE source = 'email' AND (title LIKE ? OR body LIKE ?) "
                                "ORDER BY id DESC LIMIT 50", (f"%{last4}%", f"%{last4}%")):
                text = f"{r['title']} {r['body']}"
                for n in names:
                    if re.search(rf"(?i){re.escape(n)}[^.\n]{{0,60}}{last4}|{last4}[^.\n]{{0,60}}{re.escape(n)}", text):
                        return n, "sure", f"{card['issuer']}'s emails"
        except Exception:
            pass
        try:
            fee = db.execute("SELECT amount FROM transactions WHERE card = ? AND amount > 0 AND (lower(merchant) LIKE "
                             "'%annual%fee%' OR lower(merchant) LIKE '%membership fee%' OR lower(merchant_raw) LIKE "
                             "'%annual%fee%') ORDER BY day DESC LIMIT 1", (last4,)).fetchone()
        except Exception:
            fee = None
        if fee and FEE_CLUES.get(issuer, {}).get(int(round(fee["amount"]))):
            return FEE_CLUES[issuer][int(round(fee["amount"]))], "likely", f"its ${fee['amount']:,.0f} annual fee"
        try:
            credits = [r["merchant"] + " " + (r["merchant_raw"] or "") for r in db.execute(
                "SELECT merchant, merchant_raw FROM transactions WHERE card = ? AND amount < 0", (last4,))]
        except Exception:
            credits = []
        for pattern, n in CREDIT_CLUES.get(issuer, []):
            if any(re.search(pattern, c, re.I) for c in credits):
                return n, "likely", "a statement credit only that card gives"
        return None

    def identify_all(self) -> list[str]:
        """Names the generically labelled connected cards Buddy can work out, and says so once each."""
        notes = []
        for c in self.connected_cards():
            if not c["generic"]:
                continue
            found = self.identify(c)
            if not found:
                continue
            name, sure, clue = found
            self.memory.set(f"card:name:{c['last4']}", name)
            self.memory.set(f"card:name_how:{c['last4']}", f"{sure}: {clue}")
            self.memory.set("benefits:check_new", "1")
            notes.append(f"I worked out that your {c['issuer']} card ••{c['last4']} is the {name} (from {clue}). "
                         + ("Say if it's a different card." if sure == "sure"
                            else "That's my best guess, so say if it's a different card."))
        return notes

    def unnamed_cards(self) -> list[str]:
        """Questions for connected cards whose bank name doesn't say which card they are."""
        out = []
        for c in self.connected_cards():
            if c["generic"]:
                hint = self.annual_fee_hint(c["last4"])
                choices = CARD_CHOICES.get(c["issuer"].lower().split()[0] if c["issuer"] else "", "")
                out.append(f"Your {c['issuer']} card ••{c['last4']} shows up as \"{c['account_name']}\", which doesn't say "
                           f"which card it is{' (' + hint + ')' if hint else ''}. Which one is it" +
                           (f": {choices}?" if choices else "?") + " Then I'll look up its benefits.")
        return out

    def resolve_card(self, phrase: str) -> str:
        """'my Platinum' / 'amex plat' -> the connected card's full name, when one matches."""
        generic = {"my", "the", "card", "credit", "amex", "american", "express", "chase", "citi", "capital", "one",
                   "bank", "of", "america"}
        want = [w for w in _key(phrase).split() if w not in generic]
        want = [w[:4] for w in want]
        if not want:
            return phrase
        hits = [c["card"] for c in self.connected_cards()
                if all(any(k.startswith(w) for k in _key(c["card"]).split()) for w in want)]
        return hits[0] if len(hits) == 1 else phrase

    def cards_overview(self) -> list[dict]:
        """Each card Buddy knows: where it came from, last 4, published benefits saved, credits tracked."""
        out, seen = [], set()
        connected = self.connected_cards()
        for c in connected + [{"card": n, "issuer": "", "last4": ""} for n in self.known_cards()]:
            k = _key(c["card"])
            # two cards can share a bank label (both Chase cards say "Ultimate Rewards"): the last 4 tells them apart
            if (k, c.get("last4", "")) in seen or (not c.get("last4") and any(x[0] == k for x in seen)):
                continue
            seen.add((k, c.get("last4", "")))
            saved = self.cached(c["card"], days=10 ** 5, any_version=True)
            out.append({"card": c["card"], "last4": c.get("last4", ""), "needs_name": bool(c.get("generic")),
                        "connected": any(_key(x["card"]) == k for x in connected),
                        "published_benefits_saved": bool(saved), "checked": (saved or {}).get("checked", ""),
                        "credits_tracked": self._tracked_names(c["card"])})
        return out

    def known_cards(self) -> list[str]:
        """Every card Buddy knows: connected through Plaid, from statements (fin_cards), and named in benefits."""
        names: dict[str, str] = {}
        connected = self.connected_cards()
        masks = {c["last4"] for c in connected if c["last4"]}
        for c in connected:
            if not c["generic"]:  # "Chase Ultimate Rewards" isn't a card: asked about instead (unnamed_cards)
                names.setdefault(_key(c["card"]), c["card"])
        try:
            for r in self.memory.db.execute("SELECT name, issuer, last4 FROM fin_cards"):
                if r["last4"] and r["last4"] in masks:
                    continue  # the same card as a connected one (emails, statements)
                name = re.sub(r"[®™©]", "", f"{r['issuer'] or ''} {r['name'] or ''}").strip()
                if r["issuer"] and r["issuer"].lower() in (r["name"] or "").lower():
                    name = re.sub(r"[®™©]", "", r["name"]).strip()
                if len(_key(name)) >= 4 and not is_generic(name, r["issuer"] or ""):
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
        """Daily: a card Buddy hasn't looked up yet is looked up once, and its published credits offered. A connected
        card whose bank name doesn't say which card it is gets one question instead."""
        texts = self.identify_all()  # cards Buddy can work out itself: named, and said once
        for c, q in zip([c for c in self.connected_cards() if c["generic"]], self.unnamed_cards()):
            mark = f"benefits:asked:{c['last4']}"
            if not self.memory.get(mark):
                self.memory.set(mark, local_now().date().isoformat())
                texts.append(q)
        for card in self.known_cards():
            mark = f"benefits:offered:{_key(card)}"
            if self.memory.get(mark) or self.cached(card, days=10 ** 5, any_version=True) or len(texts) >= per_day:
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

        worked_out = self.identify_all()
        found = await self.lookup_all(fresh)
        questions = worked_out + self.unnamed_cards()
        for card in self.known_cards():
            self.memory.set(f"benefits:offered:{_key(card)}", local_now().date().isoformat())
        self.memory.set("benefits:setup_done", local_now().date().isoformat())
        good = [f for f in found if not f.get("not_found")]
        if not good and questions:
            return None, " ".join(questions)
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
                   "you about? Say \"track all\", or name them." + ("" if not questions else " " + " ".join(questions)))
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
            due = datetime.now(timezone.utc) - datetime.fromisoformat(row["checked_at"]) >= timedelta(days=RECHECK_DAYS)
            old_shape = json.loads(row["data"]).get("version", 1) < CATALOG_VERSION
            if not (due or old_shape):
                continue
            tracked = [b for b in self.all() if _key(b["card"]) == row["card_key"] and "Published by" in b["notes"]]
            try:  # every saved card is refreshed; only tracked credits get a message
                new = await self.lookup(row["card_name"], fresh=True)
            except ToolError:
                continue
            if not tracked or not due:
                continue  # an older saved shape brought up to date: nothing to report
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
    # Read from the card's own statement: money back on that card ("Platinum Resy Credit", "AMEX CLEAR PLUS CREDIT",
    # "AIRLINE FEE REIMBURSEMENT") is a statement credit; each goes to the one tracked credit on that card it names
    # best, and the ones no name explains are sorted by Claude once (link_credits). Purchases there are shown too,
    # since a credit can take weeks to post.
    def _transactions(self, start: date, end: date) -> list[dict]:
        try:
            rows = [dict(r) for r in self.memory.db.execute(
                "SELECT day, amount, merchant, card, merchant_raw, source FROM transactions WHERE day >= ? AND day <= ?",
                (start.isoformat(), end.isoformat()))]
        except Exception:  # an older database without merchant_raw
            try:
                rows = [dict(r) for r in self.memory.db.execute(
                    "SELECT day, amount, merchant, card, source FROM transactions WHERE day >= ? AND day <= ?",
                    (start.isoformat(), end.isoformat()))]
            except Exception:
                return []
        # The clean name ("Uber") drops what says it's a statement credit ("UBER CASH CREDIT"): match on both.
        for r in rows:
            r["text"] = f"{r['merchant']} {r.get('merchant_raw') or ''}".lower()
        return rows

    def _card_last4(self, card: str) -> str:
        """The connected card a tracked credit belongs to (its last 4), so only that card's statement counts."""
        if not hasattr(self, "_last4_cache"):
            self._last4_cache = {}
        k = _key(card or "")
        if k not in self._last4_cache:
            hits = [c["last4"] for c in self.connected_cards() if c["last4"] and _key(c["card"]) == k]
            self._last4_cache[k] = hits[0] if len(hits) == 1 else ""
        return self._last4_cache[k]

    def _on_card(self, b: dict, t: dict) -> bool:
        last4 = self._card_last4(b["card"])
        return not last4 or not t.get("card") or str(t["card"])[-4:] == last4

    def _score(self, b: dict, text: str) -> int:
        """How well a statement line names this credit: the user's match words or the merchant (2 each), and the
        telling words of the credit's name ('resy', 'clear', 'airline', 'saks')."""
        score = 0
        for phrase in _words(b["match"]) or _words(b["merchant"]):
            if re.search(rf"(?<![a-z0-9]){re.escape(phrase.strip())}(?![a-z0-9])", text):
                score += 2
        own = set(_key(b["card"] or "").split())  # "platinum" in "Platinum Resy Credit" says nothing about which credit
        for w in [w for w in _name_words(f"{b['name']} {b['merchant']}") if w not in own]:
            if re.search(rf"(?<![a-z0-9]){re.escape(w)}", text):
                score += 1
        return score

    def _owner(self, t: dict, tracked: list[dict]) -> int | None:
        """Which tracked credit a statement credit on a card belongs to (its id), or None."""
        link = self.memory.get(f"benefit:link:{t.get('source') or ''}") if t.get("source") else None
        if link:
            return int(link) if link.isdigit() else None
        says_credit = bool(CREDIT_TEXT.search(t["text"]))
        best, top = None, 0
        for b in tracked:
            if not self._on_card(b, t):
                continue
            told = any(re.search(rf"(?<![a-z0-9]){re.escape(w.strip())}(?![a-z0-9])", t["text"]) for w in _words(b["match"]))
            if not (says_credit or told):
                continue  # a refund of a purchase, not a statement credit
            score = self._score(b, t["text"])
            if score > top:
                best, top = b["id"], score
        return best

    def _matches(self, b: dict, start: date, end: date) -> tuple[list[dict], list[dict]]:
        """(statement credits that counted, purchases there) for one benefit between two dates."""
        tracked = [x for x in self.all() if _key(x["card"]) == _key(b["card"])] or [b]
        phrases = _words(b["merchant"])
        credits, buys = [], []
        for t in self._transactions(start, end):
            if not self._on_card(b, t):
                continue
            if t["amount"] < 0:
                if self._owner(t, tracked) == b["id"]:
                    credits.append({**t, "amount": -t["amount"]})
            elif t["amount"] > 0 and phrases and any(
                    re.search(rf"(?<![a-z0-9]){re.escape(p.strip())}(?![a-z0-9])", t["text"]) for p in phrases):
                buys.append(t)
        return credits, buys

    def status(self, b: dict, today: date | None = None) -> dict:
        today = today or local_now().date()
        start, end = period_bounds(b, today)
        credits, buys = self._matches(b, start, min(end, today))
        credited, spent = sum(c["amount"] for c in credits), sum(t["amount"] for t in buys)
        told = sum(r[0] for r in self.memory.db.execute(
            "SELECT amount FROM fin_benefit_uses WHERE benefit_id = ? AND day >= ? AND day <= ?",
            (b["id"], start.isoformat(), end.isoformat())))
        used = min(b["amount"], round(credited + told, 2))
        pending = round(max(0.0, min(spent, b["amount"]) - used), 2) if spent else 0.0
        return {"id": b["id"], "benefit": self._label(b), "amount": b["amount"], "currency": b["currency"],
                "period": b["period"], "starts": start.isoformat(), "ends": end.isoformat(),
                "days_left": (end - today).days, "used": used, "left": round(b["amount"] - used, 2),
                "spent_there": round(spent, 2), "maybe_pending": pending, "merchant": b["merchant"],
                "expired": end < today, "card": b["card"], "name": b["name"], "kind": self._published(b).get("kind", ""),
                "credits_seen": [{"day": c["day"], "amount": round(c["amount"], 2)} for c in credits][-3:],
                "statement_through": self.statement_through(b["card"])}

    def statement_through(self, card: str) -> str:
        """The latest day Buddy has this card's transactions for (what 'used' was read from)."""
        last4 = self._card_last4(card)
        if not last4:
            return ""
        try:
            row = self.memory.db.execute("SELECT MAX(day) FROM transactions WHERE card = ?", (last4,)).fetchone()
        except Exception:
            return ""
        return (row[0] or "") if row else ""

    async def link_credits(self, ask=None, today: date | None = None, limit: int = 40) -> int:
        """Statement credits on the cards whose wording names no tracked credit ("AMEX PLATINUM BENEFIT"): Claude
        says which credit each is, once (kept in kv benefit:link:<source>). Returns how many were linked."""
        today = today or local_now().date()
        tracked = self.all()
        if not tracked:
            return 0
        todo = []
        for t in self._transactions(today - timedelta(days=400), today):
            if t["amount"] >= 0 or not t.get("source") or self.memory.get(f"benefit:link:{t['source']}"):
                continue
            if self._owner(t, tracked) is not None:
                continue  # its wording already says which credit it is
            if not any(self._on_card(b, t) and self._card_last4(b["card"]) for b in tracked):
                continue  # not on a card with tracked credits
            todo.append(t)
        todo = todo[:limit]
        if not todo:
            return 0
        credits = [{"id": b["id"], "card": b["card"], "card_last4": self._card_last4(b["card"]), "credit": b["name"],
                    "amount": b["amount"], "period": b["period"], "where": b["merchant"]} for b in tracked]
        lines = [{"n": i, "day": t["day"], "amount": -t["amount"], "card_last4": t.get("card") or "",
                  "statement_text": (t.get("merchant_raw") or t["merchant"])[:80]} for i, t in enumerate(todo)]
        try:
            got = await (ask or _ask_links)(self.memory, credits, lines)
        except Exception:
            log.exception("statement credit linking failed")
            return 0
        ids = {b["id"] for b in tracked}
        linked = 0
        for item in (got or {}).get("links", []):
            try:
                t = todo[int(item.get("n"))]
            except (TypeError, ValueError, IndexError):
                continue
            bid = item.get("credit_id")
            ok = isinstance(bid, int) and bid in ids
            self.memory.set(f"benefit:link:{t['source']}", str(bid) if ok else "none")
            linked += ok
        return linked

    def _used_this_year(self, b: dict, today: date) -> float:
        start = date(today.year, 1, 1)
        credits, _ = self._matches(b, start, today)
        told = sum(r[0] for r in self.memory.db.execute(
            "SELECT amount FROM fin_benefit_uses WHERE benefit_id = ? AND day >= ?", (b["id"], start.isoformat())))
        periods = {"monthly": today.month, "quarterly": (today.month - 1) // 3 + 1,
                   "semiannual": 1 if today.month <= 6 else 2}.get(b["period"], 1)
        return round(min(sum(c["amount"] for c in credits) + told, b["amount"] * periods), 2)

    def dashboard(self, today: date | None = None) -> dict:
        """Every card: fee, published credits (tracked ones with used / left / days left), perks, earn rates,
        and totals: credit value a year against the fees, used this year, what ends soon."""
        today = today or local_now().date()
        per_year = {"monthly": 12, "quarterly": 4, "semiannual": 2}
        tracked = self.all()
        cards, seen_tracked = [], set()
        for c in self.cards_overview():
            saved = self.cached(c["card"], days=10 ** 5, any_version=True) or {}
            mine = [b for b in tracked if _key(b["card"]) == _key(c["card"])]
            by_name = {b["name"].lower(): b for b in mine}
            credits = []
            for pub in saved.get("benefits", []):
                b = by_name.pop(pub["name"].lower(), None)
                item = {"name": pub["name"], "amount": pub["amount"], "period": pub["period"],
                        "enrollment": pub.get("enrollment", False), "note": pub.get("note", ""),
                        "source": pub.get("source", ""), "resets": pub.get("resets", "calendar"),
                        "year_value": pub["amount"] * per_year.get(pub["period"], 1), "tracked": bool(b)}
                if b:
                    st = self.status(b, today)
                    item.update({k: st[k] for k in ("id", "used", "left", "ends", "days_left", "maybe_pending",
                                                    "starts")})
                    item["used_this_year"] = self._used_this_year(b, today)
                    seen_tracked.add(b["id"])
                credits.append(item)
            for b in by_name.values():  # tracked credits the user told Buddy about (not on the issuer's list)
                st = self.status(b, today)
                credits.append({"name": b["name"], "amount": b["amount"], "period": b["period"], "tracked": True,
                                "year_value": b["amount"] * per_year.get(b["period"], 1), "source": "",
                                "used_this_year": self._used_this_year(b, today),
                                **{k: st[k] for k in ("id", "used", "left", "ends", "days_left", "maybe_pending",
                                                      "starts")}})
                seen_tracked.add(b["id"])
            fee = saved.get("annual_fee")
            used_year = round(sum(x.get("used_this_year", 0) for x in credits), 2)
            cards.append({"card": c["card"], "last4": c["last4"], "connected": c["connected"],
                          "needs_name": c["needs_name"], "annual_fee": fee, "checked": saved.get("checked", ""),
                          "credits": sorted(credits, key=lambda x: (not x["tracked"], x.get("days_left", 999),
                                                                    -x["year_value"])),
                          "perks": [p["name"] for p in saved.get("perks", []) if _perk_applies(p["name"], c["card"])],
                          "earn_rates": [_rate_text(r) for r in saved.get("earn_rates", [])],
                          "year_value": round(sum(x["year_value"] for x in credits), 2), "used_this_year": used_year,
                          "sources": sorted({x["source"] for x in credits if x.get("source")})[:3]})
        soon = sorted((dict(x, card=c["card"]) for c in cards for x in c["credits"]
                       if x["tracked"] and x.get("left", 0) > 0 and x.get("days_left", 999) <= 31),
                      key=lambda x: x["days_left"])
        return {"today": today.isoformat(), "cards": cards,
                "totals": {"cards": len(cards), "fees": round(sum(c["annual_fee"] or 0 for c in cards), 2),
                           "year_value": round(sum(c["year_value"] for c in cards), 2),
                           "used_this_year": round(sum(c["used_this_year"] for c in cards), 2),
                           "left_this_period": round(sum(x.get("left", 0) for c in cards for x in c["credits"]
                                                         if x["tracked"]), 2)},
                "ending_soon": soon[:8], "questions": self.unnamed_cards()}

    def credit_detail(self, benefit_id: int, today: date | None = None) -> dict:
        """One tracked credit: this period, what counted, purchases waiting for a credit, past periods, deals."""
        today = today or local_now().date()
        b = self._get(int(benefit_id))
        st = self.status(b, today)
        start, end = date.fromisoformat(st["starts"]), date.fromisoformat(st["ends"])
        credits, buys = self._matches(b, start, min(end, today))
        told = [dict(r) for r in self.memory.db.execute(
            "SELECT day, amount, note FROM fin_benefit_uses WHERE benefit_id = ? AND day >= ? AND day <= ?",
            (b["id"], start.isoformat(), end.isoformat()))]
        history, d = [], start - timedelta(days=1)
        for _ in range(4):  # the periods before this one
            ps, pe = period_bounds(b, d)
            got, _ = self._matches(b, ps, pe)
            told_then = sum(r[0] for r in self.memory.db.execute(
                "SELECT amount FROM fin_benefit_uses WHERE benefit_id = ? AND day >= ? AND day <= ?",
                (b["id"], ps.isoformat(), pe.isoformat())))
            history.append({"from": ps.isoformat(), "to": pe.isoformat(),
                            "used": round(min(b["amount"], sum(c["amount"] for c in got) + told_then), 2)})
            if b["period"] == "once":
                break
            d = ps - timedelta(days=1)
        steps = REMIND_BY_PERIOD.get(b["period"], REMIND_DAYS)
        reminders = [(end - timedelta(days=n)).isoformat() for n in steps if end - timedelta(days=n) >= today]
        notes = b["notes"] or ""
        source = re.search(r"https://\S+", notes)
        return {**st, "card": b["card"], "name": b["name"], "notes": notes, "source": source.group(0).rstrip(";") if source
                else "", "enrollment": "needs enrollment" in notes, "statement_credits": credits, "purchases": buys,
                "marked_used": told, "history": history, "reminders": reminders,
                "deals": self.deals_for(b["id"], st["ends"]) or {}, "kind": credit_kind({**st, "benefit": st["benefit"]})}

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
            seen = self._seen(p)
            if seen:
                text += f" {seen}"
            found = self.deals_for(p["id"], p["ends"]) or {}
            kind = credit_kind(p)
            line = (f"- **{p['name']}** ({p['card'] or 'card'}): ${p['left']:.0f} of ${p['amount']:.0f} left, ends "
                    f"{_nice(p['ends'])} ({'today' if p['days_left'] == 0 else str(p['days_left']) + ' days'}). "
                    + (seen or ""))
            hint = self._hint(p)
            if p["maybe_pending"]:
                text += f" You spent ${p['spent_there']:.0f} there this period; the credit may not have posted yet."
            elif credit_kind(p) == "dining" and not _where(self.memory):
                text += (" Turn on Share my location in Settings and I'll suggest restaurants near you that take it.")
            elif found.get("deals") or found.get("sale"):
                # Spoken short; on screen a headline, the sale, then one line per suggestion (|| splits them).
                picks, kind = found.get("deals") or [], found.get("kind")
                heading = {"dining": "Places near you it covers", "hotel": "Hotels it covers"}.get(kind, "Good ways to use it")
                where = {"dining": "places near you that take it", "hotel": "hotels it covers"}.get(kind, "ways to use it")
                spoken = text + (f" I found {len(picks[:3])} {where}; they're on screen." if picks else "")
                shown = [f"**{p['benefit']}: ${p['left']:.0f} left, ends {_nice(p['ends'])} "
                         f"({'today' if p['days_left'] == 0 else str(p['days_left']) + ' days'})**"]
                if found.get("sale"):
                    shown.append(f"{(p['merchant'] or 'The store').title()} has {found['sale']}.")
                if picks:
                    shown.append(f"**{heading}**")
                    for d in picks[:3]:
                        if kind in ("dining", "hotel"):
                            shown.append(f"- {d['item']}: about ${d['price']:.0f}." + (f" {d['note']}." if d["note"] else "")
                                         + f" {d['url']}")
                        else:
                            shown.append(f"- {d['item']}: ${d['price']:.0f}, you'd pay ${d['you_pay']:.0f}. {d['url']}")
                shown.append("You decide; I don't buy anything.")
                text = spoken + " || " + "\n".join(shown)
            elif p["ideas"]:
                i = p["ideas"][0]
                if i.get("price"):
                    text += f" You wanted the {i['item']} (${i['price']:.0f}): with the credit you'd pay ${i['you_pay']:.0f}."
                else:
                    text += f" You wanted the {i['item']}."
                text += " Want me to watch for a deal on it?"
            elif hint:
                text += f" {hint}"
            elif kind == "store" and p["merchant"]:
                text += f" Anything you've wanted from {p['merchant'].title()}? I can look for deals."
            out.append({"text": text, "mark": mark, "benefit": p["id"], "left": p["left"],
                        "line": (line + (f" {hint}" if hint and not p["maybe_pending"] else "")).strip(),
                        "rich": " || " in text or bool(p["ideas"]) or bool(p["maybe_pending"])})
        return out

    def _seen(self, p: dict) -> str:
        """What the card's statement shows for this credit, in a few words."""
        got = p.get("credits_seen") or []
        if p["used"] > 0 and got:
            last = got[-1]
            return f"Used ${p['used']:.0f} so far (statement credit of ${last['amount']:.0f} on {_nice(last['day'])})."
        if p["used"] > 0:
            return f"You told me ${p['used']:.0f} is used."
        if p.get("statement_through"):
            return f"No statement credit for it on your card through {_nice(p['statement_through'])}."
        return ""

    def _hint(self, p: dict) -> str:
        """How the credit can be used, from the issuer's own note saved with it, and enrollment if it needs it."""
        try:
            notes = self._get(p["id"])["notes"] or ""
        except ToolError:
            return ""
        parts = [x.strip() for x in notes.split(";")]
        note = next((x for x in parts[1:] if x and x != "needs enrollment"), "")
        out = f"{note[0].upper()}{note[1:].rstrip('.')}." if note and credit_kind(p) == "skip" else ""
        if "needs enrollment" in parts and p["used"] <= 0 and not self._ever_used(p["id"]):
            out += " It needs enrollment in the card's app first, if you haven't."
        return out.strip()

    def _ever_used(self, benefit_id: int) -> bool:
        b = self._get(benefit_id)
        today = local_now().date()
        credits, _ = self._matches(b, today - timedelta(days=730), today)
        return bool(credits) or bool(self.memory.db.execute(
            "SELECT 1 FROM fin_benefit_uses WHERE benefit_id = ? LIMIT 1", (benefit_id,)).fetchone())

    @staticmethod
    def bundle(alerts: list[dict]) -> list[dict]:
        """Several plain reminders on one morning become one message (a line each); ones with deals or places stay
        their own message."""
        plain = [a for a in alerts if not a.get("rich") and a.get("line")]
        if len(plain) < 2:
            return alerts
        rest = [a for a in alerts if a not in plain]
        total = sum(a["left"] for a in plain)
        spoken = f"{len(plain)} of your card credits run out soon, ${total:,.0f} left in all. They're on screen."
        shown = [f"**{len(plain)} card credits running out (${total:,.0f} left)**"] + [a["line"] for a in plain] + [
            "If I missed one you've used, say \"I used my … credit\". Details are in Card benefits."]
        merged = {"text": spoken + " || " + "\n".join(shown), "marks": [a["mark"] for a in plain],
                  "benefit": None, "rich": True}
        return [merged] + rest

    def told(self, alert: dict) -> None:
        for mark in alert.get("marks") or [alert["mark"]]:
            self.memory.set(mark, local_now().date().isoformat())


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
    "what the user tells you or confirms, and say issuers can change them. When they ask about a card's benefits "
    "(what's left, what to use before year end) and that card has nothing tracked, call lookup straight away "
    "without asking first (the issuer's published list is not a guess), answer from it, then offer to track. list: used, left and days left this "
    "period (statement credits are read from the bank feed). used (what, amount; all that's left if no amount). "
    "remove (what). wish_add (item, merchant, price) when they say they want something but it's expensive; wishes; "
    "wish_remove (what). plan: credits running out within 60 days with wishes they could pay for. To watch a deal, "
    "use price_watch (it asks first). You suggest; the user buys. "
    "cards: the user's cards (connected banks first, with last 4), whether their published benefits are saved and "
    "which credits are tracked; use for 'what cards do I have?' and to know which card 'my Platinum' is. A card "
    "marked needs_name (the bank only says 'Ultimate Rewards' or 'Credit card'): ask which card it is, then "
    "name_card (last4, name). "
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
                                              "plan", "lookup", "lookup_all", "setup", "add_from_catalog", "deals",
                                              "cards", "name_card"]},
        "last4": {"type": "string"},
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
    if a == "name_card":
        return b.name_card(str(args.get("last4") or ""), str(args.get("name") or ""))
    if a == "cards":
        found = b.cards_overview()
        return json.dumps(found, ensure_ascii=False) if found else ("No cards yet: connect a bank (\"connect my bank\"), "
                                                                    "share a statement, or tell me your cards.")
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
