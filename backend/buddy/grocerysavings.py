"""Groceries savings: the grocery side of Buddy's deals, kept apart from the Deals page (electronics, home, travel),
with advice for each thing on the shopping list that weighs the price and the owner's own health numbers. Code only:
no AI model decides or sees any of it.

Where the prices come from (all within the stores' terms; nothing is scraped and no store login is used):
  the deals inbox     the weekly ads and coupon emails the owner gets from ACME, ShopRite, Target, Costco, CVS,
                      Walgreens and others (dealmail.py), kept in the price book with every other price
  the deal feeds      grocery deals among the feeds Buddy reads (dealcollector.py)
  the owner           prices said or scanned ("eggs were 3.49 at ShopRite"), and receipts (a later step)

  is_grocery(...)     which deals are groceries: a grocery store's, filed as grocery or household, or food words at
                      a general store (Walmart, Target, Costco, CVS...). The Deals page leaves these out.
  deals / coupons     grocery deals of the last two weeks, and the coupons among them (found -> clipped -> used, or
                      dismissed). Buddy can't clip a coupon: it lists the ones that fit, with the store's own link,
                      and the owner taps Clip in the store's app, then "Clipped" here. A coupon found is never
                      counted as money saved; a clipped one is an estimate; only a receipt proves it (later step).
  advice(item)        for a shopping-list item: Skip (Better to skip for the owner, even on sale), Clip in the app,
                      Buy now (at or near the lowest price seen), Wait (a deal is likely soon, from its own rhythm),
                      or Buy as needed, each with the reason.
  plan()              this week: the store with the lowest known total for the list, a second store only when it
                      saves more than a trip costs (the owner's figure, $5 unless they change it), coupons to clip,
                      what to wait for. Prices not seen are not guessed.
  savings()           estimated (clipped coupons) and proven (receipts) savings, by month.
  wishlist            things the owner wants that aren't groceries: the lowest price seen, the latest, and the next
                      likely deal, from the same price book (free: no searches are paid for).
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone

from .memory import Memory
from .tools import ToolError, local_now

SCHEMA = """
CREATE TABLE IF NOT EXISTS grocery_coupons (
    key TEXT PRIMARY KEY,                  -- the deal's link, or its price-book id
    state TEXT NOT NULL,                   -- clipped | used | dismissed (found = no row)
    title TEXT NOT NULL DEFAULT '',
    store TEXT NOT NULL DEFAULT '',
    value REAL,                            -- dollars off, when the coupon says so
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS grocery_coupon_offers (  -- coupons with no price of their own, from store emails
    key TEXT PRIMARY KEY, title TEXT NOT NULL, store TEXT NOT NULL DEFAULT '', url TEXT NOT NULL DEFAULT '',
    ends TEXT NOT NULL DEFAULT '', seen_on TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS wishlist (
    id INTEGER PRIMARY KEY, what TEXT NOT NULL, target REAL, added_at TEXT NOT NULL
);
"""
TRIP_KEY, TRIP_DEFAULT = "groceries:trip_cost", 5.0
KEEP_DAYS = 400  # coupon marks: a year of savings by month, then they go
OFFER_DAYS = 30  # a coupon from an email without an end date is listed this long
GROCERS = (r"shop ?rite|acme|stop (?:&|and) shop|whole foods|trader joe|aldi|wegmans|kroger|patel brothers|h ?mart|"
           r"lidl|sprouts|safeway|foodtown|key food|giant|food lion|publix|weis|price ?rite|instacart|fresh ?direct")
GENERAL = r"walmart|target|costco|cvs|walgreens|amazon|bj'?s|sam'?s club|dollar general|rite aid"
FOOD = (r"milk|eggs?|chicken|beef|pork|turkey|salmon|shrimp|fish|yogurt|cheese|butter|bread|bagels?|rice|pasta|"
        r"flour|sugar|cereal|oats|oatmeal|granola|fruit|apples?|bananas?|berries|strawberr|blueberr|grapes|oranges?|"
        r"avocados?|tomato|potato|onions?|lettuce|spinach|salad|broccoli|carrots?|vegetables?|veggies|produce|juice|"
        r"coffee beans|ground coffee|k-?cups?|\btea\b|snacks?|chips|crackers|cookies|nuts|almonds|peanut butter|soup|"
        r"beans|lentils|frozen|ice cream|pizza|sauce|oil|spices|grocer|food|paper towels?|toilet paper|detergent|"
        r"dish soap|trash bags|diapers|wipes")
COUPON = re.compile(r"coupon|digital (?:deal|offer)|\bclip|for ?u\b|price plus|\$\s?\d+(?:\.\d\d)? off|save \$\s?\d|"
                    r"buy \d+,? get|\bbogo\b|\d+% off", re.I)
VALUE = re.compile(r"(?:save\s+)?\$\s?(\d+(?:\.\d\d)?)\s*off|save\s+\$\s?(\d+(?:\.\d\d)?)", re.I)
_GROCER, _GENERAL, _FOOD = (re.compile(rf"\b(?:{x})", re.I) for x in (GROCERS, GENERAL, FOOD))
GROCERY_CATEGORIES = ("grocery", "household")
ROUNDUP = re.compile(r"\bup to\s+\d{1,2}\s?%", re.I)  # "Snacks & Candy Deals: Up to 78% off": a sale, not a coupon

# The store, by its usual name: the sender's, else the one a deal site's headline names ("... @ Amazon").
STORE_NAMES = (("ShopRite", r"shop ?rite"), ("ACME", r"\bacme\b"), ("Whole Foods", r"whole ?foods"),
               ("Trader Joe's", r"trader joe"), ("Stop & Shop", r"stop (?:&|and) shop"), ("Wegmans", r"wegmans"),
               ("Aldi", r"\baldi\b"), ("Lidl", r"\blidl\b"), ("Kroger", r"kroger"), ("H Mart", r"\bh ?mart\b"),
               ("Patel Brothers", r"patel brothers"), ("Sprouts", r"sprouts"), ("Safeway", r"safeway"),
               ("Giant", r"\bgiant\b"), ("Food Lion", r"food lion"), ("Publix", r"publix"), ("Weis", r"\bweis\b"),
               ("PriceRite", r"price ?rite"), ("Foodtown", r"foodtown"), ("Key Food", r"key food"),
               ("FreshDirect", r"fresh ?direct"), ("Instacart", r"instacart"), ("Walmart", r"walmart"),
               ("Target", r"\btarget\b"), ("Costco", r"costco"), ("Amazon", r"amazon|\bamz\b"), ("CVS", r"\bcvs\b"),
               ("Walgreens", r"walgreens"), ("BJ's", r"\bbj'?s\b"), ("Sam's Club", r"sam'?s club"),
               ("Dollar General", r"dollar general"), ("Rite Aid", r"rite aid"))
_STORE_RES = [(n, re.compile(x, re.I)) for n, x in STORE_NAMES]
# What it is, for grouping by product when it isn't on the shopping list. Order matters: hair oil is personal care.
PRODUCT_GROUPS = (
    ("Personal care", r"\bhair\b|shampoo|conditioner|\bsoap\b|body wash|toothpaste|deodorant|skin ?care|lotion|razor|"
                      r"sunscreen|cosmetic|makeup"),
    ("Household", r"paper towels?|toilet paper|tissues|detergent|trash bags|dish ?soap|dishwasher|laundry|batteries|"
                  r"\bwipes\b|diapers|cleaner|foil|plastic wrap|sponges?"),
    ("Yogurt", r"yogh?urt|\bskyr\b|chobani|\bfage\b|siggi|oikos|dannon|yoplait|stonyfield|noosa"), ("Eggs", r"\beggs?\b"), ("Milk & cream", r"\bmilk\b|\bcream\b|creamer"),
    ("Cheese & butter", r"cheese|butter\b"),
    ("Meat & fish", r"chicken|beef|pork|turkey|salmon|shrimp|\bfish\b|bacon|sausage|\bham\b|steak|tuna"),
    ("Fruit & vegetables", r"fruit|apples?\b|bananas?|berr(?:y|ies)|grapes|oranges?|avocados?|tomato|potato|onions?|"
                           r"lettuce|spinach|salad|broccoli|carrots?|vegetables?|veggies|produce|lemons?|limes?"),
    ("Bread & bakery", r"bread|bagels?|muffins?|tortillas?|\bbuns?\b|croissant"),
    ("Cereal & breakfast", r"cereal|cheerios|kellogg|frosted flakes|special k|\boats\b|oatmeal|granola|pancake|"
                           r"waffle"),
    ("Pasta, rice & sauces", r"pasta|spaghetti|noodles|\brice\b|sauce|salsa|ketchup|mayo|dressing"),
    ("Snacks & candy", r"snacks?|chips|crackers|cookies|candy|chocolate|pub mix|pretzels?|popcorn|\bnuts\b|almonds|"
                       r"\bbars?\b|jerky|doritos|\blay'?s\b|pringles|oreos?|\butz\b|cheez-?it|goldfish"),
    ("Drinks", r"juice|soda|sparkling|\bwater\b|coffee|\btea\b|k-?cups?|drinks?|kombucha|seltzer|coca-?cola|"
               r"pepsi|gatorade|la ?croix"),
    ("Frozen", r"frozen|ice cream|pizza"),
    ("Pantry", r"flour|sugar|\boil\b|spices?|beans|lentils|soup|peanut butter|honey|jam|canned|broth"),
)
_PRODUCT_RES = [(n, re.compile(x, re.I)) for n, x in PRODUCT_GROUPS]
DEAL_SITE = re.compile(r"slickdeals|dealnews|brad'?s deals|9to5|deals? alert|kinja|woot|hip2save|krazy coupon|"
                       r"the krazy|living rich|frugal|coupons?\.com|ibotta|fetch", re.I)


def store_name(store: str, title: str = "") -> str:
    """The store, by its usual name: the sender (ACME Markets is ACME), else the store a deal site's headline names
    ("~$2.68 After Coupon & S&S @ Amazon"), else the sender itself, else "Deal sites"."""
    store = (store or "").strip()
    for name, rx in _STORE_RES:
        if rx.search(store):
            return name
    at = re.search(r"(?:@|\bat|\bfrom|\bvia)\s+([A-Za-z'&. ]{3,30}?)(?:\s*[-–—:|(]|$|\s+(?:for|with|on|w/))", title or "")
    for name, rx in _STORE_RES:
        if (at and rx.search(at.group(1))) or re.search(rf"^\s*{rx.pattern}\b", title or "", re.I):
            return name
    for name, rx in _STORE_RES:  # a store named anywhere in a deal site's headline
        if DEAL_SITE.search(store) or not store:
            if rx.search(title or ""):
                return name
    return "Deal sites" if not store or DEAL_SITE.search(store) else store[:40]


def product_group(title: str, items: list[str] | None = None) -> tuple[str, bool]:
    """What a coupon or deal is for: the shopping-list item it matches (and True), else a kind of product."""
    for t in items or []:
        if _match(t, title):
            return t[:1].upper() + t[1:], True
    for name, rx in _PRODUCT_RES:
        if rx.search(title or ""):
            return name, False
    return "Other", False


def _grouped(rows: list[dict], items: list[str]) -> list[dict]:
    out = []
    for r in rows:
        product, on_list = product_group(r.get("title") or "", items)
        out.append(r | {"where": store_name(r.get("store") or "", r.get("title") or ""), "product": product,
                        "on_list": on_list})
    return out


def _ready(memory: Memory) -> None:
    memory.db.executescript(SCHEMA)


def is_grocery(title: str, store: str = "", category: str = "") -> bool:
    """A grocery deal: a grocery store's, filed as grocery or household, or food words at a general store."""
    if (category or "").lower() in GROCERY_CATEGORIES or _GROCER.search(store or ""):
        return True
    return bool(_GENERAL.search(store or "") and _FOOD.search(title or ""))


def sql_filter(only: bool) -> tuple[str, tuple]:
    """The same test as is_grocery, for the price book's deals (DuckDB): grocery deals only, or none of them."""
    cond = ("(COALESCE(d.category, '') IN ('grocery', 'household') OR regexp_matches(lower(COALESCE(d.store, '')), ?) "
            "OR (regexp_matches(lower(COALESCE(d.store, '')), ?) AND regexp_matches(lower(d.title), ?)))")
    params = (rf"\b({GROCERS.lower()})", rf"\b({GENERAL})", rf"\b({FOOD})")
    return (cond if only else f"NOT {cond}"), params


# --- grocery deals and coupons ------------------------------------------------------------------------------------
def deals(memory: Memory, days: int = 14, query: str = "") -> list[dict]:
    """Grocery deals seen in the last `days` (the deals inbox and the feeds), newest first."""
    from . import pricebook  # noqa: PLC0415
    try:
        return pricebook.deals_page(memory, days, "product", query, 300, groceries="only")["deals"]
    except Exception:  # noqa: BLE001 - no price book yet
        return []


def _key(d: dict) -> str:
    url = str(d.get("url") or "")
    return url if url.startswith("https://") else f"deal:{d.get('id')}"


def coupon_value(title: str) -> float | None:
    m = VALUE.search(title or "")
    return float(m.group(1) or m.group(2)) if m else None


def _offer_key(store: str, title: str) -> str:
    import hashlib  # noqa: PLC0415
    return "coupon:" + hashlib.sha1(f"{store.lower()}|{title.lower()}".encode(), usedforsecurity=False).hexdigest()[:20]


def _drop_old_offers(memory: Memory, today: date) -> None:
    memory.db.execute("DELETE FROM grocery_coupon_offers WHERE (ends != '' AND ends < ?) OR (ends = '' AND seen_on < ?)",
                      ((today - timedelta(days=7)).isoformat(), (today - timedelta(days=OFFER_DAYS)).isoformat()))


def keep_coupons(memory: Memory, found: list[dict], today: date | None = None) -> int:
    """Coupons with no price of their own, from store emails (dealmail.coupons_in): the grocery ones are kept for the
    Groceries page, never as prices. Returns how many were new."""
    from .dealmail import _ends_iso  # noqa: PLC0415
    _ready(memory)
    today = today or local_now().date()
    new = 0
    for c in found:
        title, store = str(c.get("title") or "")[:160], str(c.get("store") or "")[:60]
        if not title or not is_grocery(title, store):
            continue
        url = str(c.get("url") or "")
        key = _offer_key(store, title)
        ends = _ends_iso(str(c.get("ends") or ""), today)
        had = memory.db.execute("SELECT 1 FROM grocery_coupon_offers WHERE key = ?", (key,)).fetchone()
        memory.db.execute("INSERT INTO grocery_coupon_offers (key, title, store, url, ends, seen_on) VALUES (?, ?, ?, ?, ?, ?) "
                          "ON CONFLICT(key) DO UPDATE SET url = excluded.url, ends = excluded.ends, seen_on = excluded.seen_on",
                          (key, title, store, url if url.startswith("https://") else "", ends, today.isoformat()))
        new += 0 if had else 1
    _drop_old_offers(memory, today)
    memory.db.commit()
    return new


def coupons(memory: Memory, found: list[dict] | None = None, today: date | None = None) -> list[dict]:
    """The coupons that haven't ended, with the owner's mark on each (found when unmarked): those among the grocery
    deals (with a price), and those from store emails with no price of their own."""
    _ready(memory)
    today = today or local_now().date()
    marks = {r["key"]: dict(r) for r in memory.db.execute("SELECT * FROM grocery_coupons")}
    out = []
    seen: set[tuple[str, str]] = set()
    for d in found if found is not None else deals(memory, 30):
        if not COUPON.search(str(d.get("title") or "")) or ROUNDUP.search(str(d.get("title") or "")):
            continue
        same = (store_name(d.get("store") or "", d["title"]), str(d["title"]).lower())
        if same in seen:
            continue  # the same coupon in two emails
        seen.add(same)
        if d.get("ends") and str(d["ends"])[:10] < today.isoformat():
            continue
        key = _key(d)
        state = (marks.get(key) or {}).get("state", "found")
        if state == "dismissed":
            continue
        out.append({"key": key, "title": d["title"], "store": d.get("store") or "", "ends": str(d.get("ends") or "")[:10],
                    "price": d.get("price"), "value": coupon_value(d["title"]), "state": state,
                    "link": d.get("url") if str(d.get("url") or "").startswith("https://") else "",
                    "seen": str(d.get("seen_on") or "")[:10]})
    listed = {(c["store"].lower(), c["title"].lower()) for c in out}
    for r in memory.db.execute("SELECT * FROM grocery_coupon_offers WHERE (ends = '' AND seen_on >= ?) OR ends >= ? "
                               "ORDER BY seen_on DESC, title", ((today - timedelta(days=OFFER_DAYS)).isoformat(),
                                                                today.isoformat())):
        state = (marks.get(r["key"]) or {}).get("state", "found")
        if state == "dismissed" or (r["store"].lower(), r["title"].lower()) in listed:
            continue
        out.append({"key": r["key"], "title": r["title"], "store": r["store"], "ends": r["ends"], "price": None,
                    "value": coupon_value(r["title"]), "state": state, "link": r["url"], "seen": r["seen_on"]})
    return out


def mark_coupon(memory: Memory, key: str, state: str, title: str = "", store: str = "") -> dict:
    """The owner: clipped it in the store's app, used it, not interested, or back to found."""
    if state not in ("clipped", "used", "dismissed", "found"):
        raise ToolError("Clipped, used, dismissed or found.")
    _ready(memory)
    key = str(key or "")[:500]
    if not key:
        raise ToolError("Which coupon?")
    if state == "found":
        memory.db.execute("DELETE FROM grocery_coupons WHERE key = ?", (key,))
    else:
        memory.db.execute("INSERT INTO grocery_coupons (key, state, title, store, value, updated_at) VALUES "
                          "(?, ?, ?, ?, ?, ?) ON CONFLICT(key) DO UPDATE SET state = excluded.state, "
                          "updated_at = excluded.updated_at",
                          (key, state, str(title)[:200], str(store)[:60], coupon_value(title),
                           datetime.now(timezone.utc).isoformat(timespec="seconds")))
    memory.db.commit()
    return {"key": key, "state": state}


# --- advice for each thing on the list ----------------------------------------------------------------------------
def _match(text: str, title: str) -> bool:
    from .groceries import _fits, _words  # noqa: PLC0415
    want = _words(text)
    words = [w[:-1] if len(w) > 3 and w.endswith("s") else w for w in re.findall(r"[a-z0-9]+", text.lower())]
    last = next((w for w in reversed(words) if w in want), "")
    return _fits(want, last, _words(title))


def _health(memory: Memory, text: str) -> dict | None:
    """The owner's verdict on a product like this one, from what they scanned (their own numbers: code only)."""
    from . import foodverdict  # noqa: PLC0415
    try:
        scans = foodverdict.scans(memory, 200)
    except Exception:  # noqa: BLE001
        return None
    same = [s for s in scans if _match(text, f"{s.get('name', '')} {s.get('brand', '')}")]
    return same[0] if same else None


def advice(memory: Memory, text: str, found: list[dict] | None = None, coupon_list: list[dict] | None = None,
           today: date | None = None) -> dict:
    """What to do about one thing on the shopping list, and why: skip | clip | buy_now | wait | as_needed."""
    from . import dealforecast, groceries, pricebook  # noqa: PLC0415
    today = today or local_now().date()
    out = {"item": text, "do": "as_needed", "why": "", "price": None, "store": "", "link": "", "when": ""}
    health = _health(memory, text)
    if health and health.get("verdict") == "skip":
        better = None
        try:
            better = groceries.better_than(memory, health["id"])
        except Exception:  # noqa: BLE001
            better = None
        out |= {"do": "skip", "why": f"{health['name']} is Better to skip for you"
                + (f": {health['headline'].split(':', 1)[1].strip()}" if ":" in health.get("headline", "") else "")
                + (f". Better for you: {better['name']}." if better else ".")}
        return out
    clip = next((c for c in (coupon_list if coupon_list is not None else coupons(memory))
                 if _match(text, c["title"]) and c["state"] in ("found", "clipped")), None)
    best = groceries.deals_for(memory, text, seen=found if found is not None else None, limit=3)
    try:
        hist = [r["price"] for r in pricebook.history(memory, text, "product", 90)]
    except Exception:  # noqa: BLE001
        hist = []
    low = min(hist) if len(hist) >= 3 else None
    if clip:
        out |= {"do": "clip", "store": clip["store"], "link": clip["link"], "when": clip["ends"],
                "why": f"{clip['title']} at {clip['store']}" + (f", until {clip['ends']}" if clip["ends"] else "")
                + (": clipped, use it at checkout" if clip["state"] == "clipped" else ": clip it in the store's app")}
        same = [b for b in best if (b.get("store") or "").lower() == clip["store"].lower()]
        if same:
            out["price"] = same[0]["price"]
        return out
    if best:
        top = best[0]
        out |= {"price": top["price"], "store": top["store"], "link": top["link"]}
        if low is not None and top["price"] <= low * 1.02:
            out |= {"do": "buy_now", "why": f"${top['price']:.2f} at {top['store']} is the lowest Buddy has seen in "
                                             f"90 days ({len(hist)} prices)"}
            return out
    try:
        nxt = next((f for f in dealforecast.forecast(memory, text, kind="other").get("next_deals", [])
                    if f.get("source") == "its own deal history"
                    and date.fromisoformat(f["when"]) <= today + timedelta(days=21)), None)
    except Exception:  # noqa: BLE001
        nxt = None
    if nxt:
        out |= {"do": "wait", "when": nxt["when"],
                "why": f"a deal is likely around {date.fromisoformat(nxt['when']):%a %b %d}: {nxt['why']}"}
        return out
    if best:
        out["why"] = (f"${best[0]['price']:.2f} at {best[0]['store']}"
                      + (f"; the lowest seen in 90 days is ${low:.2f}" if low is not None else "")
                      + ": no better price known to wait for")
    else:
        out["why"] = ("no prices seen for it yet: forward store weekly ads to your deals inbox, or tell Buddy what "
                      "you paid")
    return out


# --- this week ----------------------------------------------------------------------------------------------------
def trip_cost(memory: Memory) -> float:
    try:
        return float(memory.get(TRIP_KEY) or TRIP_DEFAULT)
    except ValueError:
        return TRIP_DEFAULT


def set_trip_cost(memory: Memory, dollars) -> float:
    try:
        value = round(float(dollars), 2)
    except (TypeError, ValueError):
        raise ToolError("How much is a trip to a store worth to you, in dollars?") from None
    if not 0 <= value <= 100:
        raise ToolError("Between $0 and $100.")
    memory.set(TRIP_KEY, str(value))
    return value


def plan(memory: Memory, today: date | None = None) -> dict:
    """This week's shopping: advice per item, the cheapest store for what has prices, a second store only when the
    saving beats a trip, the coupons to clip and what to wait for."""
    from . import groceries  # noqa: PLC0415
    today = today or local_now().date()
    items = groceries.to_buy(memory)
    found = groceries._seen(memory)
    coupon_list = coupons(memory, today=today)
    advise = [advice(memory, t["text"], found, coupon_list, today) | {"id": t["id"], "who": t.get("who", "")}
              for t in items]
    prices: dict[str, dict[str, float]] = {}  # store -> item -> lowest price seen in two weeks
    for t in items:
        for d in groceries.deals_for(memory, t["text"], seen=found, limit=10):
            prices.setdefault(d["store"], {})[t["text"]] = d["price"]
    stores = sorted(prices, key=lambda s: (-len(prices[s]), sum(prices[s].values())))
    single = None
    if stores:
        s = stores[0]
        single = {"store": s, "items": len(prices[s]), "total": round(sum(prices[s].values()), 2)}
    pair, cost = None, trip_cost(memory)
    if len(stores) >= 2:
        best = None
        for i, a in enumerate(stores):
            for b in stores[i + 1:]:
                names = set(prices[a]) | set(prices[b])
                total = sum(min(prices[a].get(n, 1e9), prices[b].get(n, 1e9)) for n in names)
                if best is None or (len(names), -total) > (best[2], -best[3]):
                    best = (a, b, len(names), total)
        if best:
            a, b, n, total = best
            base = single["total"] if single and single["items"] == n else None
            saving = round(base - total, 2) if base is not None else None
            pair = {"stores": [a, b], "items": n, "total": round(total, 2), "saving": saving,
                    "worth_it": bool(saving is not None and saving > cost)}
    return {"advice": advise, "single": single, "pair": pair, "trip_cost": cost,
            "clip": [c for c in coupon_list if c["state"] == "found" and any(_match(t["text"], c["title"]) for t in items)],
            "wait": [a for a in advise if a["do"] == "wait"],
            "unpriced": [a["item"] for a in advise if a["price"] is None and a["do"] != "skip"],
            "note": "From the prices Buddy has seen in the last two weeks (store emails, deal feeds, what you told it): "
                    "items without a price aren't guessed. A second store is suggested only when it saves more than "
                    f"a trip (${cost:.2f}). Worked out by Buddy's code; no AI model sees it."}


# --- savings ------------------------------------------------------------------------------------------------------
def savings(memory: Memory, today: date | None = None) -> dict:
    """Estimated savings (coupons the owner clipped or used) by month; proven savings come from receipts."""
    _ready(memory)
    today = today or local_now().date()
    memory.db.execute("DELETE FROM grocery_coupons WHERE updated_at < ?",
                      ((today - timedelta(days=KEEP_DAYS)).isoformat(),))
    memory.db.commit()
    months: dict[str, dict] = {}
    for r in memory.db.execute("SELECT state, value, store, updated_at FROM grocery_coupons WHERE state IN "
                               "('clipped', 'used') AND value IS NOT NULL"):
        m = str(r["updated_at"])[:7]
        row = months.setdefault(m, {"month": m, "estimated": 0.0, "proven": 0.0, "coupons": 0})
        row["estimated"] = round(row["estimated"] + float(r["value"]), 2)
        row["coupons"] += 1
    rows = sorted(months.values(), key=lambda r: r["month"], reverse=True)
    this = next((r for r in rows if r["month"] == today.isoformat()[:7]), {"estimated": 0.0, "proven": 0.0})
    return {"months": rows, "this_month": this,
            "note": "Estimated: the dollars off on the coupons you clipped or used. Proven savings will come from "
                    "your receipts (reading a receipt photo on the phone is the next step); a coupon found is never "
                    "counted."}


# --- the wishlist (not groceries) ---------------------------------------------------------------------------------
def wish_add(memory: Memory, what: str, target=None) -> list[dict]:
    _ready(memory)
    what = " ".join(str(what or "").split())[:120]
    if len(what) < 2:
        raise ToolError("What do you want?")
    try:
        target = round(float(target), 2) if target not in (None, "") else None
    except (TypeError, ValueError):
        target = None
    memory.db.execute("INSERT INTO wishlist (what, target, added_at) VALUES (?, ?, ?)",
                      (what, target, datetime.now(timezone.utc).isoformat(timespec="seconds")))
    memory.db.commit()
    return wishlist(memory)


def wish_remove(memory: Memory, wish_id: int) -> list[dict]:
    _ready(memory)
    memory.db.execute("DELETE FROM wishlist WHERE id = ?", (int(wish_id),))
    memory.db.commit()
    return wishlist(memory)


def wishlist(memory: Memory, today: date | None = None) -> list[dict]:
    """Each wish with the latest and lowest prices seen (90 days), the next likely deal, and buy now or wait."""
    from . import dealforecast, pricebook  # noqa: PLC0415
    _ready(memory)
    today = today or local_now().date()
    out = []
    for r in memory.db.execute("SELECT * FROM wishlist ORDER BY id"):
        w = dict(r)
        try:
            hist = pricebook.history(memory, w["what"], "product", 90)
        except Exception:  # noqa: BLE001
            hist = []
        latest = hist[-1] if hist else None
        low = min((h["price"] for h in hist), default=None)
        try:
            nxt = next(iter(dealforecast.forecast(memory, w["what"], today=today).get("next_deals", [])), None)
        except Exception:  # noqa: BLE001
            nxt = None
        if latest and ((w["target"] and latest["price"] <= w["target"]) or (low and latest["price"] <= low * 1.02
                                                                              and len(hist) >= 3)):
            do, why = "buy_now", (f"${latest['price']:,.2f} at {latest.get('seller') or 'a store'}: "
                                  + ("at or under your price" if w["target"] and latest["price"] <= w["target"]
                                     else f"the lowest in 90 days ({len(hist)} prices)"))
        elif nxt and (nxt.get("window") or [""])[0] <= today.isoformat() <= (nxt.get("window") or ["", ""])[1]:
            do, why = "watching", (f"a sale is on now, until {nxt['window'][1]}: {nxt['why']}. Buddy is looking for a "
                                   + (f"price at or under ${w['target']:,.2f}" if w["target"] else "price for it"))
        elif nxt:
            do, why = "wait", f"around {nxt['when']}: {nxt['why']}"
        else:
            do, why = "watching", "no price seen yet: Buddy keeps an eye on the deal feeds and your store emails"
        out.append(w | {"latest": latest["price"] if latest else None, "seller": (latest or {}).get("seller", ""),
                        "lowest": low, "prices": len(hist), "do": do, "why": why})
    return out


def page(memory: Memory) -> dict:
    """Everything the Groceries page's savings parts show; coupons and deals carry their store (where) and product
    (the shopping-list item they match, or a kind of product), for grouping by store or by product."""
    from . import groceries  # noqa: PLC0415
    items = [t["text"] for t in groceries.to_buy(memory)]
    found_deals, found_coupons = deals(memory), coupons(memory)
    shown = {c["key"] for c in found_coupons}  # a coupon that is also a deal is listed once, as the coupon
    once, seen = [], set()
    for d in found_deals:
        same = (store_name(d.get("store") or "", d["title"]), str(d["title"]).lower(), d.get("price"))
        if _key(d) in shown or same in seen:
            continue  # the same deal from two emails is listed once
        seen.add(same)
        once.append(d)
    return {"plan": plan(memory), "deals": _grouped(once[:120], items), "coupons": _grouped(found_coupons, items),
            "savings": savings(memory), "trip_cost": trip_cost(memory)}
