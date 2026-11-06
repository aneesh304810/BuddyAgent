"""Card optimizer: the user's card benefits organized like a benefits tracker, and which card to use for what.

  browse      every published credit and perk across the cards, by category (Travel, Food & dining, Retail &
              shopping, Protection & assistance, Entertainment, Other) and subcategory (Hotel perks, Airline perks,
              Restaurant...), each with how many benefits from how many cards, the value a year and how many are
              running out soon (tracked credits with money left that end within a month)
  coming_up   tracked credits with money left, by the date they reset or expire
  earn_guide  the best card for each kind of spending (groceries, gas, dining, travel, transit, streaming,
              drugstores, everything else) from each card's published earn rates; points and miles valued at
              BUDDY_POINT_CENTS (1 cent by default); offers limited to a portal or one merchant shown as "up to"
  missed      purchases in the last 30 days where another of the user's cards would have earned more, and what
              that adds up to a year
All numbers by code from the issuer lookups and the bank feed; nothing is invented.
"""

import os
import re
from datetime import timedelta

from .tools import local_now

CATEGORIES = [  # (category, subcategory, words)
    ("Travel", "Hotel perks", r"hotel|fine hotels|fhr|hotel collection|resort|marriott|hilton|hyatt|ihg|the edit"),
    ("Travel", "Airline perks", r"airline|flight|delta|united|southwest|companion|checked bag|seat"),
    ("Travel", "Airport lounge", r"lounge|centurion|priority pass|sky club"),
    ("Travel", "Airport security (TSA)", r"clear|global entry|tsa|precheck|nexus"),
    ("Travel", "Rideshare", r"uber|lyft|rideshare|ride"),
    ("Travel", "Car rental", r"car rental|rental car|hertz|avis|national|auto rental"),
    ("Travel", "Trip insurance & protection", r"trip|travel insurance|baggage|delay|cancellation|interruption|travel accident"),
    ("Travel", "Travel credit", r"travel credit|travel"),
    ("Food & dining", "Food delivery", r"doordash|dashpass|grubhub|instacart|delivery|uber eats|caviar"),
    ("Food & dining", "Restaurant", r"resy|restaurant|dining|opentable|tock|dunkin|shake shack|cheesecake"),
    ("Food & dining", "Grocery", r"grocery|supermarket|walmart\+|whole foods|kroger"),
    ("Entertainment", "Streaming services", r"digital entertainment|streaming|disney|hulu|netflix|peacock|espn|"
                                             r"youtube|spotify|nytimes|new york times|audible|siriusxm|paramount"),
    ("Entertainment", "Event perks", r"event|ticket|presale|concert|experiences|sports"),
    ("Retail & shopping", "Retailer perks", r"saks|lululemon|amazon|walmart|equinox|soulcycle|oura|best buy|retail|"
                                            r"store|shop|apple"),
    ("Retail & shopping", "Promotional financing", r"0%|intro apr|pay over time|financing|plan it|my chase plan"),
    ("Protection & assistance", "Purchase protection", r"purchase protection|return protection"),
    ("Protection & assistance", "Extended warranty", r"warranty"),
    ("Protection & assistance", "Cell phone protection", r"cell phone|phone protection"),
    ("Protection & assistance", "Fraud & identity", r"fraud|identity|zero liability|credit monitoring"),
    ("Protection & assistance", "Roadside assistance", r"roadside|towing"),
    ("Protection & assistance", "Travel & emergency assistance", r"assistance|emergency|concierge"),
    ("Other", "Anniversary & bonus", r"anniversary|bonus|points|reward|cardmember"),
]
CATEGORY_ORDER = ["Travel", "Food & dining", "Retail & shopping", "Entertainment", "Protection & assistance", "Other"]
ICONS = {"Travel": "✈", "Food & dining": "🍴", "Retail & shopping": "🛍", "Entertainment": "🎟",
         "Protection & assistance": "🛡", "Other": "🎖"}

# Kinds of spending, the words an issuer uses for each, and how bank transactions map onto them.
SPEND = [("groceries", "Groceries", r"grocer|supermarket"),
         ("gas", "Gas", r"\bgas\b|fuel|ev charging|gas stations"),
         ("dining", "Dining", r"dining|restaurant|takeout|food delivery"),
         ("travel", "Travel", r"travel|flight|airfare|hotel|airline|rental car|cruise|vacation"),
         ("transit", "Transit & rideshare", r"transit|rideshare|uber|lyft|parking|toll|commut|train|taxi"),
         ("streaming", "Streaming", r"streaming"),
         ("drugstores", "Drugstores", r"drugstore|pharmac"),
         ("other", "Everything else", r"all other|everything else|other purchases|all purchases|every purchase|"
                                      r"other eligible|base")]
RESTRICTED = re.compile(r"(?i)\bvia\b|through|booked|portal|chase travel|amex travel|capital one travel|directly|"
                        r"prepaid|amazon|whole foods|selected|rotating|quarterly|up to|first \$|caps?\b|limit")
STREAMING_MERCHANTS = re.compile(r"(?i)netflix|spotify|hulu|disney|youtube|max\b|hbo|peacock|paramount|apple tv|audible")


def point_cents() -> float:
    try:
        return float(os.getenv("BUDDY_POINT_CENTS") or 1.0)
    except ValueError:
        return 1.0


def categorize(name: str, kind: str = "") -> tuple[str, str]:
    text = f"{name} {kind}".lower()
    if kind == "dining":
        text += " restaurant"
    for cat, sub, rx in CATEGORIES:
        if re.search(rx, text):
            return cat, sub
    return "Other", "Other perks"


def rate_value(r: dict) -> float:
    """Cash-back-equivalent percent for one earn rate: 3% cash back -> 3; 5x points -> 5 x point value."""
    unit = str(r.get("unit") or "").lower()
    rate = float(r.get("rate") or 0)
    if "percent" in unit or unit.startswith("%") or "cash" in str(r.get("reward") or "").lower():
        return rate
    return rate * point_cents()


def spend_kind(category: str) -> str | None:
    low = category.lower()
    for key, _label, rx in SPEND:
        if re.search(rx, low):
            return key
    return None


def card_rates(saved: dict) -> dict:
    """{spend kind: (best plain value, label, [limited offers])} for one card's published earn rates."""
    out: dict[str, dict] = {}
    for r in saved.get("earn_rates", []):
        kind = spend_kind(str(r.get("category") or ""))
        if not kind:
            continue
        value = rate_value(r)
        label = _rate_label(r)
        slot = out.setdefault(kind, {"value": 0.0, "label": "", "limited": []})
        if RESTRICTED.search(str(r.get("category") or "") + " " + str(r.get("note") or "")):
            slot["limited"].append({"value": value, "label": label, "category": str(r.get("category"))[:60]})
        elif value > slot["value"]:
            slot["value"], slot["label"] = value, label
    return out


def _rate_label(r: dict) -> str:
    from .benefits import _rate_text  # noqa: PLC0415
    text = _rate_text(r)
    return text.split(" on ")[0] if " on " in text else text


def transaction_kind(t: dict) -> str:
    cat, text = t.get("category") or "", f"{t.get('merchant', '')} {t.get('detail', '')}".lower()
    if cat in ("food",):
        return "dining"
    if cat == "groceries":
        return "groceries"
    if cat == "travel":
        return "travel"
    if cat == "transport":
        return "gas" if re.search(r"gas|fuel|shell|exxon|chevron|bp\b|mobil|sunoco|wawa", text) else "transit"
    if cat in ("subscriptions", "entertainment") and STREAMING_MERCHANTS.search(text):
        return "streaming"
    if cat == "health" and re.search(r"pharmac|cvs|walgreens|rite aid|drug", text):
        return "drugstores"
    return "other"


class Optimizer:
    def __init__(self, benefits):
        self.b = benefits
        self.memory = benefits.memory

    def _cards(self) -> list[dict]:
        out = []
        for c in self.b.cards_overview():
            saved = self.b.cached(c["card"], days=10 ** 5, any_version=True) or {}
            out.append({**c, "saved": saved, "rates": card_rates(saved)})
        return out

    # --- by category ------------------------------------------------------------------------------------
    def browse(self, dash: dict) -> list[dict]:
        cats: dict[str, dict] = {}
        for c in dash["cards"]:
            saved = self.b.cached(c["card"], days=10 ** 5, any_version=True) or {}
            kinds = {b["name"].lower(): b.get("kind", "") for b in saved.get("benefits", [])}
            items = [("credit", x["name"], x, kinds.get(x["name"].lower(), "")) for x in c["credits"]] + \
                [("perk", p, None, "") for p in c["perks"]]
            for typ, name, x, kind in items:
                cat, sub = categorize(name, kind)
                group = cats.setdefault(cat, {"category": cat, "icon": ICONS.get(cat, "•"), "subs": {}})
                s = group["subs"].setdefault(sub, {"name": sub, "items": [], "cards": set(), "year_value": 0.0,
                                                   "expiring": 0})
                s["cards"].add(c["card"])
                entry = {"type": typ, "name": name, "card": c["card"], "last4": c.get("last4", "")}
                if x:
                    entry.update({k: x.get(k) for k in ("amount", "period", "tracked", "id", "left", "ends", "days_left",
                                                         "year_value", "enrollment", "used_this_year")})
                    s["year_value"] += x.get("year_value") or 0
                    if x.get("tracked") and (x.get("left") or 0) > 0 and (x.get("days_left") or 999) <= 31:
                        s["expiring"] += 1
                s["items"].append(entry)
        out = []
        for cat in CATEGORY_ORDER + sorted(set(cats) - set(CATEGORY_ORDER)):
            if cat not in cats:
                continue
            subs = sorted(cats[cat]["subs"].values(), key=lambda s: (-s["year_value"], -len(s["items"])))
            for s in subs:
                s["cards"] = len(s["cards"])
                s["count"] = len(s["items"])
                s["year_value"] = round(s["year_value"], 2)
            out.append({"category": cat, "icon": cats[cat]["icon"], "count": sum(s["count"] for s in subs),
                        "year_value": round(sum(s["year_value"] for s in subs), 2),
                        "expiring": sum(s["expiring"] for s in subs),
                        "preview": ", ".join(s["name"] for s in subs[:2]) + (f" and {len(subs) - 2} more" if len(subs) > 2 else ""),
                        "subs": subs})
        return out

    # --- which card for what -----------------------------------------------------------------------------
    def earn_guide(self, cards: list[dict] | None = None) -> list[dict]:
        cards = cards if cards is not None else self._cards()
        out = []
        for key, label, _rx in SPEND:
            options = []
            for c in cards:
                slot = c["rates"].get(key) or {}
                base = slot.get("value") or (c["rates"].get("other") or {}).get("value") or 0
                if base:
                    options.append({"card": c["card"], "last4": c.get("last4", ""), "value": round(base, 2),
                                    "label": slot.get("label") or (c["rates"].get("other") or {}).get("label", "")})
            if not options:
                continue
            options.sort(key=lambda o: -o["value"])
            best = options[0]
            ties = [o["card"] for o in options[1:] if abs(o["value"] - best["value"]) < 0.01]
            limited = [{"card": c["card"], **lim} for c in cards for lim in (c["rates"].get(key) or {}).get("limited", [])
                       if lim["value"] > best["value"]]
            out.append({"kind": key, "label": label, "best": best, "ties": ties,
                        "limited": sorted(limited, key=lambda x: -x["value"])[:3]})
        return out

    def missed(self, days: int = 30, cards: list[dict] | None = None) -> dict:
        cards = cards if cards is not None else self._cards()
        by_last4 = {c["last4"]: c for c in cards if c.get("last4")}
        if not by_last4:
            return {"items": [], "total": 0.0, "yearly": 0.0, "days": days}
        since = (local_now().date() - timedelta(days=days)).isoformat()
        try:
            rows = [dict(r) for r in self.memory.db.execute(
                "SELECT day, amount, merchant, category, card, detail FROM transactions WHERE day >= ? AND amount > 0 "
                "AND currency = 'USD' ORDER BY day DESC", (since,))]
        except Exception:
            rows = []

        def value(card: dict, kind: str) -> float:
            return (card["rates"].get(kind) or {}).get("value") or (card["rates"].get("other") or {}).get("value") or 0.0
        items, total = [], 0.0
        for t in rows:
            used = by_last4.get(str(t.get("card") or "")[-4:])
            if not used or not used["rates"]:
                continue
            kind = transaction_kind(t)
            have = value(used, kind)
            best = max(cards, key=lambda c: value(c, kind))
            gap = value(best, kind) - have
            if gap <= 0 or best["card"] == used["card"]:
                continue
            lost = round(t["amount"] * gap / 100, 2)
            if lost < 0.01:
                continue
            total += lost
            items.append({"day": t["day"], "merchant": t["merchant"], "amount": t["amount"], "kind": kind,
                          "used": {"card": used["card"], "value": round(have, 2)},
                          "better": {"card": best["card"], "value": round(value(best, kind), 2)}, "missed": lost})
        return {"items": items[:20], "total": round(total, 2), "yearly": round(total * 365 / days, 2), "days": days,
                "point_cents": point_cents()}

    def check_untracked(self, dash: dict, today=None) -> None:
        """Credits not tracked yet, checked against the card's own statement (Plaid): a statement credit this period
        that names the credit means it was used. Adds 'seen' to each untracked credit."""
        from .benefits import CREDIT_TEXT, _words, period_bounds  # noqa: PLC0415
        today = today or local_now().date()
        for c in dash["cards"]:
            last4 = c.get("last4") or ""
            if not last4:
                continue
            saved = self.b.cached(c["card"], days=10 ** 5, any_version=True) or {}
            pubs = {b["name"].lower(): b for b in saved.get("benefits", [])}
            for x in c["credits"]:
                if x.get("tracked"):
                    continue
                pub = pubs.get(x["name"].lower()) or {}
                words = list(pub.get("statement_words") or []) or _words(pub.get("merchant") or "")
                if not words:
                    continue
                start, end = period_bounds({"period": x["period"], "resets": x.get("resets", "calendar"),
                                            "created_at": f"{today.isoformat()}T00:00:00+00:00"}, today)
                got = [t for t in self.b._transactions(start, min(end, today))
                       if str(t.get("card") or "")[-4:] == last4 and t["amount"] < 0 and CREDIT_TEXT.search(t["text"])
                       and any(re.search(rf"(?<![a-z0-9]){re.escape(w.strip())}", t["text"]) for w in words)]
                x["seen"] = [{"day": t["day"], "amount": round(-t["amount"], 2)} for t in got][-3:]
                x["seen_used"] = round(min(x["amount"], sum(-t["amount"] for t in got)), 2)
                x["statement_through"] = self.b.statement_through(c["card"])

    def view(self, today=None) -> dict:
        dash = self.b.dashboard(today)
        try:
            self.check_untracked(dash, today)
        except Exception:
            pass
        cards = self._cards()
        coming = sorted((dict(x, card=c["card"]) for c in dash["cards"] for x in c["credits"]
                         if x.get("tracked") and (x.get("left") or 0) > 0), key=lambda x: x.get("ends") or "9999")
        browse = self.browse(dash)
        missed = self.missed(cards=cards)
        return {**dash, "browse": browse, "coming_up": coming[:12],
                "earn_guide": self.earn_guide(cards), "missed": missed,
                "summary": {"benefits": sum(g["count"] for g in browse), "cards": len(dash["cards"]),
                            "year_value": dash["totals"]["year_value"], "used_this_year": dash["totals"]["used_this_year"],
                            "missed_30d": missed["total"]}}
