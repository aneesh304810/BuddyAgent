"""Deal notifications: price drops and deals that shopping and travel apps send to the owner's phone, read into
the price book like the deals inbox's emails.

The Buddy app already passes the phone's notifications to the server (only with Notification access on). Every
hour this reads the new ones from shopping and travel apps only (Amazon, Best Buy, Target, Walmart, Costco, eBay,
Google Shopping price tracking, Slickdeals, Hopper, Google Flights, Kayak, Skyscanner, airlines, Booking, Expedia,
Hotels.com, Marriott, Hilton, Hyatt...), by text rules (no AI). A notification counts as a deal when it has a
price and isn't about an order, delivery, payment or account. Each deal gets its reason, goes into the price book
and is checked against the "watch for it" alerts.

Notifications from every other app (chats, banks, mail...) are never looked at here, and nothing personal goes
into the price book: only the item, price, app and date. The phone only receives what the apps send it: nothing
is browsed or scraped.
"""

import json
import re
from datetime import datetime

from .memory import Memory

LAST_KEY, STATUS_KEY = "dealnotif:last_record", "dealnotif:status"
APPS = re.compile(
    r"amazon|best ?buy|target|walmart|costco|ebay|newegg|b&h|home depot|lowe'?s|wayfair|ikea|macy|nordstrom|nike|"
    r"menards|ace hardware|harbor freight|tractor supply|northern tool|acme tools|grainger|floor ?& ?decor|"
    r"build\.com|ferguson|sherwin|msc\b|honey|pcpartpicker|shopsavvy|micro center|adorama|samsung|zappos|"
    r"sam'?s club|bj'?s|momondo|autoslash|travelzoo|emirates|qatar|turkish|"
    r"adidas|uniqlo|slickdeals|dealnews|woot|honey|rakuten|shopping|shop\b|keepa|camel|"
    r"hopper|flights|kayak|skyscanner|going|expedia|booking|hotels\.com|priceline|trivago|airbnb|vrbo|"
    r"delta|united|american airlines|jetblue|southwest|alaska|latam|avianca|copa|air india|"
    r"marriott|hilton|hyatt|ihg", re.I)
NOT_DEAL = re.compile(
    r"\b(your order|order #|ordered|shipped|out for delivery|delivered|arriving|package|refund|return|payment|"
    r"charged|statement|balance|password|sign[- ]?in|verification|code is|otp|boarding|check[- ]?in|gate|"
    r"your trip|your booking|your reservation|confirmation|receipt|review your|rate your)\b", re.I)
DEAL = re.compile(r"\b(price drop\w*|dropped|now \$|deal\w*|sale|% off|save \$?\d|lowest|discount|clearance|"
                  r"fares? from|from \$|per night|/night|limited[- ]time|lightning|coupon|back in stock at)\b", re.I)
QUOTED = re.compile(r"[\"“']([^\"”']{6,120})[\"”']")
LEAD = re.compile(r"^(?:price drop(?:ped)?(?: alert)?|deal alert|deal of the day|lightning deal|sale|new deal|"
                  r"limited[- ]time deal|price alert|good news)\s*[:!\-–]\s*", re.I)
TAIL = re.compile(r"\s*(?:is now|now|dropped to|just dropped to|for|from|at|–|-|:)?\s*(?:only\s*)?\$\s?\d.*$", re.I)


def deal_in(title: str, text: str, app: str) -> dict | None:
    """One notification as a deal (the same shape as the deals inbox's), or None."""
    from .dealmail import CITIES, FLIGHT, HOTEL, PRICE, REGULAR, ROUTE, SAVINGS, _num  # noqa: PLC0415
    both = f"{title} {text}".strip()
    if not APPS.search(app) or NOT_DEAL.search(both) or not DEAL.search(both):
        return None
    saved = SAVINGS.search(both)  # "Save $60" is the saving, not the price
    prices = [_num(p) for p in PRICE.findall(SAVINGS.sub(" ", both)) if 0 < _num(p) < 100000]
    if not prices:
        return None
    quoted = QUOTED.search(both)
    name = quoted.group(1) if quoted else TAIL.sub("", LEAD.sub("", title if PRICE.search(title) or len(title) > 12
                                                                else text)).strip(" .!-–:")
    if len(name) < 5:
        name = TAIL.sub("", LEAD.sub("", text)).strip(" .!-–:")
    if len(name) < 5:
        return None
    reg = REGULAR.search(both)
    price = min(prices)
    regular = _num(reg.group(1)) if reg else (max(prices) if len(prices) > 1 else None)
    if regular is None and saved:
        regular = price + _num(saved.group(1) or saved.group(2))
    domain = "flight" if FLIGHT.search(both) or ROUTE.search(both) else "hotel" if HOTEL.search(both) else "product"
    item = name
    if domain == "flight":
        r = ROUTE.search(both)
        c = None if r else CITIES.search(both)
        item = f"{r.group(1)}-{r.group(2)}" if r else f"{c.group(1).strip()}-{c.group(2).strip()}".lower() if c else name
    elif domain == "hotel":
        places = [re.sub(r"\s*\b(Hotels?|Resorts?|Stays?|Deals?)\b.*$", "", m).strip()  # "in Lima Hotels..." -> Lima
                  for m in re.findall(r"\bin\s+((?:[A-Z][a-zA-Z.]+\s?){1,3})", both)]
        places = sorted((p for p in places if len(p) >= 3), key=len)
        item = f"hotels:{places[0].lower()}" if places else f"hotels:{name.lower()}"
    return {"title": name[:160], "domain": domain, "item": item, "price": price,
            "regular": regular if regular and regular > price else None, "store": app[:60], "url": "", "ends": "",
            "subject": both[:160]}


def scan(memory: Memory) -> int:
    """New notifications from shopping and travel apps, as deals into the price book. Returns how many were kept."""
    from . import dealforecast, dealmail  # noqa: PLC0415
    last = int(memory.get(LAST_KEY) or 0)
    try:
        rows = memory.db.execute("SELECT id, title, body, data, start_at FROM records WHERE kind = 'notification' "
                                 "AND id > ? ORDER BY id LIMIT 1000", (last,)).fetchall()
    except Exception:  # no notifications have ever arrived
        return 0
    kept = seen = 0
    for r in rows:
        last = max(last, r["id"])
        meta = json.loads(r["data"] or "{}")
        app = str(meta.get("app_name") or "")
        if not APPS.search(app):
            continue  # chats, banks, mail and the rest are never read here
        title = (r["title"] or "").split(": ", 1)[-1]
        text = re.sub(r"^\[[^\]]*\]\s*", "", r["body"] or "")
        d = deal_in(title, text, app)
        if not d:
            continue
        seen += 1
        try:
            when = datetime.fromisoformat(str(r["start_at"]))
        except ValueError:
            when = None
        kept += dealmail.keep(memory, [d], when, source=f"app: {app}")
        dealforecast.check(memory, [{**d, "source": f"app: {app}"}])
    memory.set(LAST_KEY, str(last))
    if rows:
        memory.set(STATUS_KEY, json.dumps({"at": datetime.now().isoformat(timespec="seconds"),
                                           "status": f"{seen} deal notifications, {kept} new kept"}))
    return kept
