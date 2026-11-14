"""Book now or wait, for a flight or a hotel stay at today's price: a call with its reasons, written down and
judged later against the prices Buddy then sees, so the reasons can be re-weighed like the shopping ones.

  Reasons (each a push towards waiting or towards booking now):
    window         how far ahead the trip is. A rule of thumb until the route's own pattern is learned: fares
                   usually climb in the last three weeks, and dates four months or more away often come down
    route_pattern  what the pattern learner found for this route (the usual price by days before the trip): is a
                   cheaper stretch still ahead, or is this already the cheap stretch
    vs_usual       today's price against the route's (or the place's) usual price in the price book
    near_lowest    at or near the lowest Buddy has seen for it
  The call is "wait" when the chance of a price 8% or more lower, before two weeks ahead of the trip, is a half
  or more. It is saved with its reasons; the route is then followed (free, through Travelpayouts) so the outcome
  is known (a hotel's rate is looked up again every three days through Google Hotels), and whentobuy.learn("flight" / "hotel") re-weighs each reason once it has 15 judged calls.

For hotels the better advice is often neither: book a rate with free cancellation now and let Buddy re-check,
and the call says so. Buddy never books.
"""

import json
import re
from datetime import date, timedelta

from . import whentobuy
from .memory import Memory
from .tools import ToolError, local_now

BUCKETS = ((0, "0-7"), (8, "8-21"), (22, "22-60"), (61, "61-120"), (121, "121+"))


def _bucket(days: int) -> str:
    return [name for start, name in BUCKETS if days >= start][-1]


def advise(memory: Memory, domain: str, item: str, price: float, travel_date: str, today: date | None = None,
           save: bool = True, place: str = "", nights: int = 1) -> dict:
    """domain: flight (item: a route like EWR-LIM) or hotel (item: the hotel, or the place; price: per night;
    place: where to search for it again; nights: the stay). travel_date: YYYY-MM-DD."""
    from . import patterns, pricebook  # noqa: PLC0415
    if domain not in ("flight", "hotel"):
        raise ToolError("Flight or hotel?")
    item = " ".join(str(item or "").split())[:120]
    try:
        trip = date.fromisoformat(str(travel_date)[:10])
    except ValueError:
        raise ToolError("Which date is the trip (YYYY-MM-DD)?") from None
    if not item or not price or price <= 0:
        raise ToolError("Give the route or place and today's price.")
    today = today or local_now().date()
    ahead = (trip - today).days
    if ahead < 0:
        raise ToolError("That date has passed.")
    learned_w = whentobuy.weights(memory, domain)
    parts, reasons, chance = {}, [], 0.2

    def bump(name: str, delta: float) -> float:
        parts[name] = round(parts.get(name, 0) + delta, 3)
        return delta * float((learned_w.get(name) or {}).get("weight", 1.0))

    if domain == "flight":
        if ahead <= 21:
            chance += bump("window", -0.3)
            reasons.append(f"the trip is {ahead} days away: fares usually climb in the last three weeks")
        elif ahead >= 121:
            chance += bump("window", 0.2)
            reasons.append(f"the trip is {ahead} days away: fares this far out often come down (a rule of thumb "
                           "until Buddy has learned this route)")
    elif ahead <= 3:
        chance += bump("window", -0.1)
        reasons.append("the stay is days away: little time for a better rate")
    known = (patterns.get(memory).get("flights" if domain == "flight" else "hotels") or {}).get(pricebook.key(item))
    usual = (known or {}).get("median")
    if domain == "flight" and known and known.get("by_days_before"):
        by, now = known["by_days_before"], _bucket(ahead)
        later = {name: by[name] for start, name in BUCKETS if name in by and start < [s for s, n in BUCKETS if n == now][0]
                 and start >= 8}  # stretches still ahead, not counting the last week
        if now in by and later:
            best = min(later, key=later.get)
            if later[best] <= by[now] * 0.95:
                chance += bump("route_pattern", 0.3)
                reasons.append(f"on this route, fares {best} days before the trip have run "
                               f"{1 - later[best] / by[now]:.0%} lower than at this point ({known['prices']} prices seen)")
            elif min(later.values()) >= by[now] * 1.03:
                chance += bump("route_pattern", -0.25)
                reasons.append(f"on this route this is usually the cheap stretch: fares closer to the trip have "
                               f"run higher ({known['prices']} prices seen)")
    hist = [r["price"] for r in pricebook.history(memory, item, domain, 365)]
    usual = usual or (sorted(hist)[len(hist) // 2] if len(hist) >= 5 else None)
    low = min(hist) if hist else None
    if usual:
        if price <= usual * 0.9:
            chance += bump("vs_usual", -0.2)
            reasons.append(f"${price:,.0f} is well under the usual ${usual:,.0f} for it")
        elif price >= usual * 1.1:
            chance += bump("vs_usual", 0.15)
            reasons.append(f"${price:,.0f} is above the usual ${usual:,.0f} for it")
    if low and price <= low * 1.03:
        chance += bump("near_lowest", -0.2)
        reasons.append(f"it's at or near the lowest Buddy has seen (${low:,.0f})")
    if not hist and not known:
        reasons.append("Buddy has no price history for it yet: this call rests on rules of thumb")
    chance = round(max(0.05, min(chance, 0.85)), 2)
    by_date = min(today + timedelta(days=21), trip - timedelta(days=14))
    decision = "wait" if chance >= 0.5 and by_date > today else "buy_now"
    out = {"what": item, "kind": domain, "trip_date": trip.isoformat(), "days_ahead": ahead, "price_now": price,
           "decision": "wait" if decision == "wait" else "book now", "chance_of_a_lower_price": chance,
           "price_worth_waiting_for": round(price * 0.9, 2), "recheck_by": max(by_date, today).isoformat(),
           "reasons": reasons,
           "note": ("Rough odds, not a promise. " + ("For a hotel, a rate with free cancellation booked now and "
                    "re-checked later usually beats waiting. " if domain == "hotel" else "")
                    + "Buddy follows the price to see how this call turns out. Buddy never books.")}
    if save and by_date > today:
        whentobuy._ready(memory)
        out["prediction_id"] = pricebook.run(
            memory, "INSERT INTO predictions (domain, item, kind, made_on, price, decision, chance, target, event, "
                    "by_date, parts, travel_date, nights) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) RETURNING id",
            (domain, item, domain, today.isoformat(), price, decision, chance, round(price * 0.9, 2),
             " ".join(str(place or "").split())[:120] if domain == "hotel" else "",
             by_date.isoformat(), json.dumps(parts), trip.isoformat(), max(1, int(nights or 1))))[0]["id"]
    return out


TOOL = {
    "name": "travel_timing",
    "description": (
        "Book now or wait, for a flight or a hotel stay at today's price. kind: flight (what: the route as two "
        "airport codes, like EWR-LIM) or hotel (what: the hotel's name, or the place when it's about the "
        "area's rates; place: the city or area, so Buddy can look the rate up again; nights: the length of the "
        "stay). price: today's price in dollars (per night for a hotel). date: the departure or check-in date "
        "(YYYY-MM-DD). Gives the call, the chance of a lower price, the date "
        "to re-check by, and the reasons (how far ahead the trip is, what Buddy has learned for the route, today's "
        "price against the usual and the lowest seen). The call is saved and judged later, so Buddy learns which "
        "reasons hold. Use it for 'should I book this flight now' or 'will this hotel get cheaper'. Say it is "
        "rough odds, and for hotels suggest a free-cancellation rate. Buddy never books."),
    "input_schema": {"type": "object", "properties": {
        "kind": {"type": "string", "enum": ["flight", "hotel"]}, "what": {"type": "string"},
        "price": {"type": "number"}, "date": {"type": "string"}, "place": {"type": "string"},
        "nights": {"type": "integer"}},
        "required": ["kind", "what", "price", "date"], "additionalProperties": False},
}


def run_tool(memory: Memory, args: dict) -> str:
    what = str(args.get("what", ""))
    if args.get("kind") == "flight":
        m = re.search(r"\b([A-Za-z]{3})\s*(?:-|–|to|→)\s*([A-Za-z]{3})\b", what)
        what = f"{m.group(1)}-{m.group(2)}".upper() if m else what
    return json.dumps(advise(memory, args.get("kind", ""), what, args.get("price") or 0, args.get("date", ""),
                             place=args.get("place") or "", nights=args.get("nights") or 1))
