"""Your way of buying: how you research and decide, per kind of purchase, so Buddy shops like you would.

Buddy learns it two ways:
  The interview   a short spoken conversation (a few questions at a time, any time: "let's do my shopping
                  interview"): what you check first, who you trust, the brands you like and avoid, how long you'll
                  wait for a deal and what makes you buy now, your must-haves and budgets, what you regretted.
                  Asked per kind of purchase (electronics, home, travel, clothing), since most people decide each
                  differently.
  Your purchases  every order and return email Buddy reads (shop, item, price, whether it was on sale) is kept in
                  your purchase history (email itself is only kept 30 days), plus the bank feed's shopping and
                  travel charges: which shops you use, what you usually spend, how often you buy on sale, returns.
                  And when you tell Buddy you're thinking of buying something, it notes the day, so when the order
                  arrives it learns how long you take to decide.

It's read code-only (no AI): order emails are recognised by their subject words. All of it stays on your server and
is used only by Claude when it advises you on a purchase. Buddy still never buys or pays.
"""

import json
import re
from datetime import date, datetime, timedelta, timezone
from email.utils import parseaddr
from statistics import median

from .memory import Memory
from .tools import ToolError, local_now

KINDS = ("general", "electronics", "home", "travel", "clothing")
SCANNED_KEY = "shop:orders_scanned"   # the last email record id read for orders

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS shop_profile (
    kind TEXT PRIMARY KEY,           -- general | electronics | home | travel | clothing
    profile TEXT NOT NULL,           -- JSON: the owner's answers, merged
    answered TEXT NOT NULL DEFAULT '[]',  -- interview question ids already covered
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS shop_purchases (
    id INTEGER PRIMARY KEY,
    record_id INTEGER UNIQUE,        -- the email it came from
    day TEXT NOT NULL,
    merchant TEXT NOT NULL,
    item TEXT NOT NULL DEFAULT '',
    amount REAL,
    kind TEXT NOT NULL,              -- electronics | home | travel | clothing | other
    on_sale INTEGER NOT NULL DEFAULT 0,
    event TEXT NOT NULL DEFAULT 'order'   -- order | return
);
CREATE TABLE IF NOT EXISTS shop_reviews (
    purchase_id INTEGER PRIMARY KEY, -- shop_purchases.id
    asked_on TEXT NOT NULL,
    verdict TEXT NOT NULL DEFAULT '', -- happy | okay | regret ('' = not answered yet)
    note TEXT NOT NULL DEFAULT '',
    answered_on TEXT
);
CREATE TABLE IF NOT EXISTS shop_considering (
    id INTEGER PRIMARY KEY,
    item TEXT NOT NULL,
    kind TEXT NOT NULL,
    since TEXT NOT NULL,             -- the day the owner first mentioned it
    bought_on TEXT,                  -- filled when a matching order arrives
    days_to_buy INTEGER
);
"""

# What each answer fills: lists merge, the rest overwrite.
FIELDS = {
    "research_style": "list", "trusted_sources": "list", "trusted_brands": "list", "avoid_brands": "list",
    "must_haves": "list", "stores": "list", "regrets": "list", "loyalty": "list",
    "patience": "text", "buy_trigger": "text", "target_discount_pct": "number", "budget": "text",
    "refurbished": "text", "notes": "list",
}

QUESTIONS = {
    "general": [
        ("first_step", "When you're about to buy something that matters, what's the first thing you do?",
         ["research_style"]),
        ("buy_now", "What makes you buy now instead of waiting for a better price?", ["buy_trigger", "patience"]),
        ("regret", "Is there a purchase you regret? What went wrong?", ["regrets"]),
        ("refurb", "How do you feel about refurbished or open-box, if it saves real money?", ["refurbished"]),
    ],
    "electronics": [
        ("check_first", "When you bought your last gadget, like headphones, a laptop or a phone, what did you check "
         "first?", ["research_style", "must_haves"]),
        ("brands", "Which brands do you trust for electronics, and any you avoid?", ["trusted_brands", "avoid_brands"]),
        ("sources", "Whose reviews do you believe: a website, a YouTuber, a subreddit?", ["trusted_sources"]),
        ("wait", "How long would you wait for a sale on a gadget, and what discount makes you buy?",
         ["patience", "target_discount_pct"]),
        ("budget", "Roughly what do you like to spend on things like headphones, a laptop or a TV?", ["budget"]),
        ("where", "Where do you usually buy electronics?", ["stores"]),
    ],
    "home": [
        ("looks_specs", "For home things, furniture, appliances or a vacuum, do you care more about how it looks, "
         "the specs, or what other people say about it?", ["research_style", "must_haves"]),
        ("brands", "Any brands you trust or avoid for home and appliances?", ["trusted_brands", "avoid_brands"]),
        ("wait", "Would you wait for a holiday sale on a big appliance, and what discount is worth waiting for?",
         ["patience", "target_discount_pct"]),
        ("where", "Where do you buy bigger home items?", ["stores"]),
    ],
    "travel": [
        ("when_book", "Do you book flights and hotels early, or wait for a deal closer to the date?",
         ["patience", "buy_trigger"]),
        ("loyalty", "Any airlines, hotel chains or points programs you stick with?", ["loyalty", "trusted_brands"]),
        ("matters", "What matters most to you: price, nonstop, the times, comfort, or the location?",
         ["must_haves"]),
        ("flexible", "How flexible are your dates usually, a day or two either side?", ["notes"]),
        ("where", "Where do you usually book: the airline's site, Google Flights, Expedia, somewhere else?",
         ["stores", "trusted_sources"]),
    ],
    "clothing": [
        ("brands", "Which clothing and shoe brands do you know fit you well?", ["trusted_brands", "notes"]),
        ("sales", "Do you wait for end-of-season sales, or buy when you need something?", ["patience", "buy_trigger"]),
        ("where", "Online or in the store, and which shops?", ["stores"]),
    ],
}
QUESTION_BY_ID = {(k, q[0]): q for k, qs in QUESTIONS.items() for q in qs}

# Recognising orders, returns and what kind of purchase they are (plain words, no AI).
ORDER = re.compile(r"\b(your order|order confirm\w*|order #|order no\.?|order number|ordered:|has shipped|shipped:|"
                   r"out for delivery|delivered:|thank(s| you) for (your )?(order|purchase|shopping)|"
                   r"purchase confirm\w*|your receipt|booking confirm\w*|reservation confirm\w*|e-?ticket|"
                   r"itinerary)(?!\w)", re.I)  # no closing \b: "ordered:" and "order #" end in punctuation
RETURN = re.compile(r"\b(return (received|started|request\w*|label|approved|confirm\w*)|your return|refund "
                    r"(issued|processed|on the way))\b", re.I)
NOT_ORDER = re.compile(r"\b(cart|wishlist|price drop|sale ends|deal of the day|recommended|you might like|"
                       r"review your|rate your|survey)\b", re.I)
SALE = re.compile(r"\b(you saved|savings|\d+% off|discount|promo(tion)? code|coupon|deal price|sale price|"
                  r"lightning deal|limited[- ]time deal)\b", re.I)
TOTAL = re.compile(r"(?:order total|grand total|total charged|total paid|amount paid|total)\s*[:\-]?\s*(?:USD\s*)?"
                   r"\$\s?([\d,]+\.\d{2})", re.I)
QUOTED = re.compile(r"[\"“']([^\"”']{4,120})[\"”']")
KIND_WORDS = {
    "electronics": r"headphone|earbud|airpods|laptop|macbook|iphone|ipad|galaxy|pixel|phone|tablet|monitor|\btv\b|"
                   r"television|camera|speaker|soundbar|charger|cable|usb|ssd|hard drive|router|keyboard|mouse|"
                   r"smartwatch|watch|kindle|console|playstation|xbox|nintendo|gpu|graphics card|printer|drone|"
                   r"echo|\bhub\b|smart plug|battery pack|power bank",
    "home": r"vacuum|roomba|sofa|couch|chair|desk|table|lamp|mattress|pillow|sheets|bedding|towel|blender|"
            r"mixer|coffee maker|espresso|air fryer|instant pot|cookware|pan|knife|dishwasher|washer|dryer|"
            r"refrigerator|fridge|microwave|oven|air purifier|humidifier|fan|heater|rug|curtain|shelf|storage|"
            r"tool|drill|garden|grill|thermostat|"
            # building and home improvement
            r"saw|sander|grinder|impact driver|wrench|hammer|ladder|compressor|nailer|generator|mower|trimmer|"
            r"blower|pressure washer|lumber|plywood|drywall|flooring|tile|paint|primer|faucet|toilet|vanity|"
            r"water heater|insulation|shingle|concrete|deck|fence|cabinet|countertop|dewalt|milwaukee|makita|"
            r"ryobi|ridgid|bosch|kobalt|craftsman|husky|shop-?vac",
    "clothing": r"shirt|t-shirt|tee|jeans|pants|trousers|shorts|dress|skirt|jacket|coat|hoodie|sweater|"
                r"sneaker|shoe|boot|sandal|sock|underwear|belt|hat|cap|scarf|glove|suit|blazer",
    "travel": r"flight|airline|boarding|itinerary|e-?ticket|hotel|booking|reservation|check-in|rental car|"
              r"airbnb|vrbo|marriott|hilton|hyatt|delta|united|american airlines|jetblue|southwest|expedia",
}
KIND_RE = {k: re.compile(rf"\b({v})", re.I) for k, v in KIND_WORDS.items()}
STORE_KIND = {
    "electronics": r"best ?buy|apple|b&h|newegg|samsung|micro center|dell|lenovo|bose|sony|adorama|pcpartpicker|"
                   r"\bhp\b|asus",
    "home": r"home depot|lowe'?s|ikea|wayfair|bed bath|crate|pottery barn|west elm|williams.sonoma|overstock|"
            r"container store|ashley|menards|ace hardware|harbor freight|tractor supply|northern tool|acme tools|"
            r"grainger|floor ?& ?decor|build\.com|ferguson|sherwin|true value|msc industrial",
    "clothing": r"nike|adidas|uniqlo|h&m|zara|gap|old navy|banana republic|j\.?crew|macy'?s|nordstrom|"
                r"lululemon|levi|under armour|new balance|asos|kohl'?s",
    "travel": r"airline|airways|delta|united|american air|jetblue|southwest|alaska air|spirit|frontier|expedia|"
              r"booking\.com|hotels\.com|kayak|priceline|marriott|hilton|hyatt|ihg|airbnb|vrbo|hertz|avis|enterprise",
}
STORE_RE = {k: re.compile(rf"({v})", re.I) for k, v in STORE_KIND.items()}


def _ready(memory: Memory) -> None:
    memory.db.executescript(SCHEMA_SQL)


def kind_of(item: str, merchant: str = "") -> str:
    """electronics | home | travel | clothing | other, from the item's words, else the shop."""
    for k in ("travel", "electronics", "clothing", "home"):
        if item and KIND_RE[k].search(item):
            return k
    for k in ("travel", "electronics", "clothing", "home"):
        if merchant and STORE_RE[k].search(merchant):
            return k
    return "other"


def _norm_kind(kind: str) -> str:
    kind = (kind or "").strip().lower()
    if kind not in KINDS:
        raise ToolError(f"Kinds of purchase: {', '.join(KINDS)}.")
    return kind


# --- the profile ----------------------------------------------------------------------------------------------
def profile(memory: Memory, kind: str) -> tuple[dict, list[str]]:
    _ready(memory)
    row = memory.db.execute("SELECT profile, answered FROM shop_profile WHERE kind = ?", (kind,)).fetchone()
    if not row:
        return {}, []
    return json.loads(row["profile"] or "{}"), json.loads(row["answered"] or "[]")


def save(memory: Memory, kind: str, fields: dict, answered: list[str] | None = None) -> dict:
    """Merge the owner's answers into one kind's profile: lists add (no repeats), the rest replace."""
    kind = _norm_kind(kind)
    prof, done = profile(memory, kind)
    unknown = [k for k in fields if k not in FIELDS]
    if unknown:
        raise ToolError(f"Unknown fields {unknown}; use {', '.join(FIELDS)}.")
    for key, value in fields.items():
        if value in (None, "", []):
            continue
        if FIELDS[key] == "list":
            items = value if isinstance(value, list) else [value]
            have = prof.get(key, [])
            seen = {str(x).strip().lower() for x in have}
            prof[key] = have + [str(x).strip() for x in items if str(x).strip() and str(x).strip().lower() not in seen]
        elif FIELDS[key] == "number":
            try:
                prof[key] = float(value)
            except (TypeError, ValueError) as e:
                raise ToolError(f"{key} is a number, like 15.") from e
        else:
            prof[key] = str(value).strip()
    done = list(dict.fromkeys(done + [a for a in (answered or []) if (kind, a) in QUESTION_BY_ID]))
    memory.db.execute(
        "INSERT INTO shop_profile (kind, profile, answered, updated_at) VALUES (?, ?, ?, ?) ON CONFLICT(kind) DO "
        "UPDATE SET profile = excluded.profile, answered = excluded.answered, updated_at = excluded.updated_at",
        (kind, json.dumps(prof), json.dumps(done), datetime.now(timezone.utc).isoformat()))
    memory.db.commit()
    return prof


def next_questions(memory: Memory, kind: str = "", n: int = 2) -> dict:
    """The next interview questions: for `kind`, or the first kind with questions left (general first)."""
    kinds = [_norm_kind(kind)] if kind else list(KINDS)
    for k in kinds:
        _, done = profile(memory, k)
        left = [q for q in QUESTIONS[k] if q[0] not in done]
        if left:
            total = len(QUESTIONS[k])
            return {"kind": k, "progress": f"{total - len(left)} of {total} done for {k}",
                    "questions": [{"id": q[0], "ask": q[1], "fills": q[2]} for q in left[:n]],
                    "how": "Ask these in your own words, one at a time, by voice: short and friendly, like a "
                           "friend who's curious. Listen, ask one follow-up if the answer is vague, then call "
                           "shopping_profile save with this kind, the fields their answer fills (their words, "
                           "short) and answered = the question ids covered. Then ask if they want to keep going. "
                           "They can stop any time."}
    return {"done": True, "note": "Every interview question is answered. They can add or change anything by just "
                                  "saying it (save), or redo a kind (forget, then interview)."}


def forget(memory: Memory, kind: str = "") -> str:
    _ready(memory)
    if kind:
        memory.db.execute("DELETE FROM shop_profile WHERE kind = ?", (_norm_kind(kind),))
    else:
        memory.db.execute("DELETE FROM shop_profile")
    memory.db.commit()
    return f"Forgot your {kind or 'whole'} shopping profile. Your purchase history is kept (say so to clear it too)."


# --- purchases from email -------------------------------------------------------------------------------------
def _merchant(sender: str) -> str:
    name, address = parseaddr(sender or "")
    name = re.sub(r"\b(no-?reply|orders?|shipping|ship-confirm|auto-confirm|notifications?|customer service)\b",
                  "", name, flags=re.I).strip(" -|:,")
    if name:
        return name[:60]
    domain = address.split("@")[-1].split(".")
    return (domain[-2] if len(domain) >= 2 else address).title()[:60]


def _item(subject: str, body: str) -> str:
    got = QUOTED.search(subject or "")
    if got:
        return got.group(1).strip().rstrip(".")[:120]
    m = re.search(r"(?:ordered|shipped|delivered|your order of)\s*[:\-]?\s*(.{4,100}?)(?:\s+and \d+ more|\.|$)",
                  subject or "", re.I)
    return m.group(1).strip(" \"'“”")[:120] if m else ""


def read_email(row: dict) -> dict | None:
    """One email record as a purchase or a return, or None."""
    subject, body = row.get("title") or "", row.get("body") or ""
    data = json.loads(row.get("data") or "{}") if isinstance(row.get("data"), str) else (row.get("data") or {})
    if NOT_ORDER.search(subject):
        return None
    event = "return" if RETURN.search(subject) else "order" if ORDER.search(subject) else ""
    if not event:
        return None
    merchant = _merchant(data.get("from", ""))
    item = _item(subject, body)
    total = TOTAL.search(body)
    amount = float(total.group(1).replace(",", "")) if total else None
    return {"record_id": row["id"], "day": str(row.get("start_at") or "")[:10] or local_now().date().isoformat(),
            "merchant": merchant, "item": item, "amount": amount, "kind": kind_of(f"{item} {subject}", merchant),
            "on_sale": int(bool(SALE.search(body))), "event": event}


def scan_orders(memory: Memory, limit: int = 500) -> int:
    """Read new email records for orders and returns into the lasting purchase history. Code only."""
    _ready(memory)
    last = int(memory.get(SCANNED_KEY) or 0)
    try:
        rows = memory.db.execute("SELECT id, title, body, data, start_at FROM records WHERE source = 'email' AND id > ? "
                                 "ORDER BY id LIMIT ?", (last, limit)).fetchall()
    except Exception:  # no records table yet: the email connector hasn't run
        return 0
    added = 0
    for r in rows:
        got = read_email(dict(r))
        if got:
            cur = memory.db.execute(
                "INSERT OR IGNORE INTO shop_purchases (record_id, day, merchant, item, amount, kind, on_sale, event) "
                "VALUES (:record_id, :day, :merchant, :item, :amount, :kind, :on_sale, :event)", got)
            added += cur.rowcount
            if got["event"] == "order" and got["item"]:
                _bought(memory, got)
    if rows:
        memory.set(SCANNED_KEY, str(rows[-1]["id"]))
    memory.db.commit()
    return added


# --- thinking about buying ------------------------------------------------------------------------------------
def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]{3,}", (text or "").lower())} - {"the", "and", "for", "with", "new"}


def considering(memory: Memory, item: str, kind: str = "") -> str:
    """Note that the owner is thinking of buying something: when it's ordered, Buddy learns how long they took."""
    item = " ".join(str(item or "").split())[:120]
    if not item:
        raise ToolError("What are you thinking of buying?")
    _ready(memory)
    kind = kind if kind in KINDS[1:] else kind_of(item)
    if not memory.db.execute("SELECT 1 FROM shop_considering WHERE lower(item) = lower(?) AND bought_on IS NULL",
                             (item,)).fetchone():
        memory.db.execute("INSERT INTO shop_considering (item, kind, since) VALUES (?, ?, ?)",
                          (item, kind, local_now().date().isoformat()))
        memory.db.commit()
    return f"Noted: you're thinking about {item}."


def _bought(memory: Memory, order: dict) -> None:
    words = _words(order["item"])
    for c in memory.db.execute("SELECT id, item, since FROM shop_considering WHERE bought_on IS NULL").fetchall():
        want = _words(c["item"])
        if want and len(want & words) >= max(1, min(2, len(want))):
            days = (date.fromisoformat(order["day"]) - date.fromisoformat(c["since"])).days
            memory.db.execute("UPDATE shop_considering SET bought_on = ?, days_to_buy = ? WHERE id = ?",
                              (order["day"], max(days, 0), c["id"]))


# --- what Buddy learned ---------------------------------------------------------------------------------------
def learned(memory: Memory, kind: str, days: int = 365) -> dict:
    """From the purchase history and the bank feed: shops, spend, sale share, returns, brands, time to decide."""
    _ready(memory)
    since = (local_now().date() - timedelta(days=days)).isoformat()
    rows = [dict(r) for r in memory.db.execute(
        "SELECT day, merchant, item, amount, on_sale, event FROM shop_purchases WHERE kind = ? AND day >= ? "
        "ORDER BY day DESC", (kind, since))]
    orders = [r for r in rows if r["event"] == "order"]
    shops: dict[str, int] = {}
    for r in orders:
        shops[r["merchant"]] = shops.get(r["merchant"], 0) + 1
    try:  # bank-feed charges at shops that clearly belong to this kind (Amazon and the like stay out: mixed)
        money_kind = "travel" if kind == "travel" else "shopping"
        bank = [dict(r) for r in memory.db.execute(
            "SELECT merchant, amount FROM transactions WHERE category = ? AND day >= ? AND amount > 0",
            (money_kind, since))]
    except Exception:
        bank = []
    bank = [b for b in bank if kind_of("", b["merchant"]) == kind]
    for b in bank:
        shops[b["merchant"]] = shops.get(b["merchant"], 0) + 1
    amounts = [r["amount"] for r in orders if r["amount"]] + [b["amount"] for b in bank]
    brands: dict[str, int] = {}
    for r in orders:
        first = (r["item"].split() or [""])[0].strip(",:-")
        if first[:1].isupper() and len(first) > 1 and not first.isdigit():
            brands[first] = brands.get(first, 0) + 1
    decided = [r["days_to_buy"] for r in memory.db.execute(
        "SELECT days_to_buy FROM shop_considering WHERE kind = ? AND days_to_buy IS NOT NULL", (kind,))]
    waiting = [dict(r) for r in memory.db.execute(
        "SELECT item, since FROM shop_considering WHERE kind = ? AND bought_on IS NULL ORDER BY since", (kind,))]
    return {
        "orders_from_email": len(orders), "bank_charges": len(bank),
        "top_shops": sorted(shops, key=lambda s: -shops[s])[:5],
        "typical_spend": round(median(amounts), 2) if amounts else None,
        "biggest_spend": round(max(amounts), 2) if amounts else None,
        "bought_on_sale_pct": round(100 * sum(r["on_sale"] for r in orders) / len(orders)) if orders else None,
        "returns": sum(1 for r in rows if r["event"] == "return"),
        "brands_bought": sorted(brands, key=lambda b: -brands[b])[:6],
        "days_to_decide_median": median(decided) if decided else None,
        "thinking_about": waiting[:5],
        "after_buying": satisfaction(memory, kind),
        "recent": [f"{r['day']}: {r['item'] or 'an order'} from {r['merchant']}"
                   + (f" (${r['amount']:,.2f})" if r["amount"] else "") + (" on sale" if r["on_sale"] else "")
                   + (" RETURNED" if r["event"] == "return" else "") for r in rows[:6]],
    }


def show(memory: Memory, kind: str = "") -> dict:
    kinds = [_norm_kind(kind)] if kind else list(KINDS)
    out = {}
    for k in kinds:
        prof, done = profile(memory, k)
        entry = {"told_me": prof, "interview": f"{len(done)} of {len(QUESTIONS[k])} questions"}
        if k != "general":
            entry["learned_from_purchases"] = learned(memory, k)
        out[k] = entry
    if not kind:
        out["general"]["applies_to"] = "every kind of purchase"
    out["how_to_use"] = ("Shop like they would: their trusted sources and brands first, their must-haves as filters, "
                         "their patience and target discount for buy-now-or-wait, and their stores. Mention when "
                         "what they told you and what they actually do differ (e.g. they say they wait for sales "
                         "but mostly buy at full price). Never buy for them.")
    return out


LABELS = {"research_style": "Researches by", "trusted_sources": "Trusts", "trusted_brands": "Likes brands",
          "avoid_brands": "Avoids brands", "must_haves": "Must-haves", "budget": "Budget", "stores": "Buys at",
          "patience": "Patience", "buy_trigger": "Buys now when", "target_discount_pct": "Waits for a discount of (%)",
          "refurbished": "Refurbished/open-box", "regrets": "Regretted", "loyalty": "Loyal to", "notes": "Notes"}


def brief(memory: Memory, kind: str) -> str:
    """The profile in a few plain lines, for the research agent: what they told Buddy (general, then this kind)
    and what their purchases show. No item list, no amounts beyond typical spend."""
    lines = []
    for k in ("general", kind) if kind in KINDS[1:] else ("general",):
        prof, _ = profile(memory, k)
        for key, label in LABELS.items():
            v = prof.get(key)
            if v not in (None, "", []):
                lines.append(f"- {label}{'' if k == 'general' else f' ({k})'}: "
                             + (", ".join(map(str, v)) if isinstance(v, list) else f"{v:g}" if isinstance(v, float)
                                else str(v)))
    if kind in KINDS[1:]:
        scan_orders(memory)
        got = learned(memory, kind)
        seen = []
        if got["top_shops"]:
            seen.append(f"usually buys at {', '.join(got['top_shops'][:3])}")
        if got["typical_spend"]:
            seen.append(f"typical spend ${got['typical_spend']:,.0f}")
        if got["bought_on_sale_pct"] is not None:
            seen.append(f"{got['bought_on_sale_pct']}% of orders on sale")
        if got["returns"]:
            seen.append(f"{got['returns']} return{'s' if got['returns'] != 1 else ''} in the last year")
        if got["brands_bought"]:
            seen.append(f"bought brands {', '.join(got['brands_bought'][:4])}")
        if got["days_to_decide_median"] is not None:
            seen.append(f"takes about {got['days_to_decide_median']:g} days to decide")
        if seen:
            lines.append("- What their purchases show: " + "; ".join(seen))
    return "\n".join(lines) or "- No shopping profile yet: research the usual careful way, and suggest the " \
                                "shopping interview at the end."


# --- after the purchase: "how's the new X?" ---------------------------------------------------------------------
CHECKIN_AFTER = {"clothing": (7, 30), "default": (14, 45)}  # days after the order: ask in this window
CHECKIN_HOURS = (10, 20)
CHECKIN_DAY_KEY = "shop:checkin_day"


def _returned(memory: Memory, p: dict) -> bool:
    want = _words(p["item"])
    for r in memory.db.execute("SELECT item, merchant FROM shop_purchases WHERE event = 'return' AND day >= ?",
                               (p["day"],)):
        if (want and len(want & _words(r["item"])) >= max(1, min(2, len(want)))) or \
                (not r["item"] and r["merchant"] == p["merchant"]):
            return True
    return False


def checkin_due(memory: Memory, now: datetime | None = None) -> dict | None:
    """One purchase worth asking about today (at most one a day, daytime only), or None."""
    _ready(memory)
    now = now or local_now()
    if memory.get(CHECKIN_DAY_KEY) == now.date().isoformat() or not CHECKIN_HOURS[0] <= now.hour < CHECKIN_HOURS[1]:
        return None
    rows = memory.db.execute(
        "SELECT p.* FROM shop_purchases p LEFT JOIN shop_reviews r ON r.purchase_id = p.id WHERE p.event = 'order' "
        "AND r.purchase_id IS NULL AND p.item != '' AND p.kind != 'travel' AND (p.amount IS NULL OR p.amount >= 40) "
        "ORDER BY p.amount DESC").fetchall()
    for row in rows:
        p = dict(row)
        lo, hi = CHECKIN_AFTER.get(p["kind"], CHECKIN_AFTER["default"])
        age = (now.date() - date.fromisoformat(p["day"])).days
        if lo <= age <= hi and not _returned(memory, p):
            return p
    return None


def checkin_text(p: dict, now: datetime | None = None) -> str:
    age = ((now or local_now()).date() - date.fromisoformat(p["day"])).days
    name = p["item"] if len(p["item"]) <= 60 else p["item"][:57].rsplit(" ", 1)[0] + "…"
    text = (f"🛍️ Quick one: how's the {name} from {p['merchant']} working out? Happy with it, just okay, or do you "
            "regret it? Your answer helps me pick better next time.")
    if age <= 25:
        text += " If it's not right, the return window is probably still open (often 30 days; check the store's policy)."
    return text


def asked(memory: Memory, purchase_id: int, now: datetime | None = None) -> None:
    now = now or local_now()
    memory.db.execute("INSERT OR IGNORE INTO shop_reviews (purchase_id, asked_on) VALUES (?, ?)",
                      (purchase_id, now.date().isoformat()))
    memory.set(CHECKIN_DAY_KEY, now.date().isoformat())
    memory.db.commit()


def review(memory: Memory, item: str, verdict: str, note: str = "") -> str:
    """The owner's verdict on something they bought: kept, and a regret also goes into their profile."""
    verdict = (verdict or "").strip().lower()
    if verdict not in ("happy", "okay", "regret"):
        raise ToolError("Verdict: happy, okay or regret.")
    _ready(memory)
    want = _words(item)
    cands = [dict(r) for r in memory.db.execute(
        "SELECT p.*, r.verdict AS done FROM shop_purchases p LEFT JOIN shop_reviews r ON r.purchase_id = p.id "
        "WHERE p.event = 'order' AND p.item != '' ORDER BY (r.asked_on IS NOT NULL AND r.verdict = '') DESC, "
        "p.day DESC LIMIT 200")]
    hit = next((c for c in cands if want and len(want & _words(c["item"])) >= max(1, min(2, len(want)))), None)
    if not hit and not want:
        hit = next((c for c in cands if c["done"] == ""), None)  # the one Buddy just asked about
    if not hit:
        raise ToolError("I can't find that purchase in your order history; say the item as the order email had it.")
    memory.db.execute("INSERT INTO shop_reviews (purchase_id, asked_on, verdict, note, answered_on) VALUES "
                      "(?, ?, ?, ?, ?) ON CONFLICT(purchase_id) DO UPDATE SET verdict = excluded.verdict, "
                      "note = excluded.note, answered_on = excluded.answered_on",
                      (hit["id"], local_now().date().isoformat(), verdict, note.strip()[:300],
                       local_now().date().isoformat()))
    memory.db.commit()
    kind = hit["kind"] if hit["kind"] in KINDS else "general"
    if verdict == "regret":
        save(memory, kind if kind != "other" else "general",
             {"regrets": f"{hit['item'][:80]}" + (f": {note.strip()[:120]}" if note.strip() else "")})
    return (f"Noted: {verdict} with the {hit['item'][:60]}."
            + (" I added it to your regrets so I'll watch for the same problem next time." if verdict == "regret"
               else ""))


def satisfaction(memory: Memory, kind: str) -> dict:
    _ready(memory)
    rows = [dict(r) for r in memory.db.execute(
        "SELECT p.item, p.merchant, r.verdict, r.note FROM shop_reviews r JOIN shop_purchases p ON p.id = r.purchase_id "
        "WHERE p.kind = ? AND r.verdict != ''", (kind,))]
    return {"happy": sum(r["verdict"] == "happy" for r in rows), "okay": sum(r["verdict"] == "okay" for r in rows),
            "regret": sum(r["verdict"] == "regret" for r in rows),
            "regretted": [f"{r['item'][:60]} ({r['merchant']})" + (f": {r['note']}" if r["note"] else "")
                          for r in rows if r["verdict"] == "regret"][:5]}


TOOL = {
    "name": "shopping_profile",
    "description": (
        "The owner's way of buying, per kind (general, electronics, home, travel, clothing): how they research, "
        "trusted sources and brands, brands to avoid, patience and the discount that makes them buy, must-haves, "
        "budgets, stores, regrets, loyalty programs; plus what Buddy learned from their purchase history (shops, "
        "typical spend, how often on sale, returns, brands, how long they take to decide). "
        "BEFORE advising on any purchase, call show for that kind and shop their way. "
        "interview: the next questions to ask (start of the shopping interview, or 'ask me more'); then save their "
        "answers. save: kind, fields (research_style, trusted_sources, trusted_brands, avoid_brands, must_haves, "
        "stores, regrets, loyalty, notes are lists; patience, buy_trigger, budget, refurbished are text; "
        "target_discount_pct a number) and answered (question ids); also whenever they mention a preference in "
        "passing. considering: item (and kind) when they say they're thinking of buying something. "
        "learn: re-read recent order and return emails now. forget: a kind, or everything. "
        "review: their verdict on something they bought (item, verdict happy|okay|regret, note: why, in their "
        "words), when they answer Buddy's 'how's the new X?' or mention it themselves; a regret also goes into "
        "their profile. If they regret it and it's recent, remind them to check the return window."),
    "input_schema": {"type": "object", "properties": {
        "action": {"type": "string", "enum": ["show", "interview", "save", "considering", "learn", "forget",
                                              "review"]},
        "verdict": {"type": "string", "enum": ["", "happy", "okay", "regret"]}, "note": {"type": "string"},
        "kind": {"type": "string", "enum": ["", *KINDS]},
        "fields": {"type": "object"},
        "answered": {"type": "array", "items": {"type": "string"}},
        "item": {"type": "string"}},
        "required": ["action"], "additionalProperties": False},
}


def run_tool(memory: Memory, args: dict) -> str:
    action, kind = args.get("action"), args.get("kind") or ""
    if action == "show":
        scan_orders(memory)
        return json.dumps(show(memory, kind), ensure_ascii=False)
    if action == "interview":
        return json.dumps(next_questions(memory, kind), ensure_ascii=False)
    if action == "save":
        if not kind:
            raise ToolError("Which kind: general, electronics, home, travel or clothing?")
        prof = save(memory, kind, args.get("fields") or {}, args.get("answered") or [])
        return json.dumps({"saved": prof, "next": next_questions(memory, kind)}, ensure_ascii=False)
    if action == "considering":
        return considering(memory, args.get("item", ""), kind)
    if action == "learn":
        n = scan_orders(memory)
        return f"Read your recent order emails: {n} new purchase{'s' if n != 1 else ''} or return{'s' if n != 1 else ''}."
    if action == "forget":
        return forget(memory, kind)
    if action == "review":
        return review(memory, args.get("item", ""), args.get("verdict", ""), args.get("note", ""))
    raise ToolError("Use show, interview, save, considering, learn, forget or review.")
