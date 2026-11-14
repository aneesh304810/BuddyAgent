"""Check direct: for every hotel in a search, the hotel's own price against the booking sites', and what booking
direct adds; and a record of who really was cheaper, per chain.

Where the direct price comes from (no scraping of hotel sites):
  Google Hotels   each hotel's result lists its sellers' nightly prices, often including the hotel's own site
                  ("Marriott.com", "Hilton", "Official site"); Buddy picks that one out
  the owner       "Marriott direct was $189 a night" (after checking as a member): note()
Every comparison is kept (hotel, chain, the direct and the best booking-site price, when). stats() then says, per
chain, how often direct was cheaper or the same and by how much, so the advice is from this owner's own searches.

The rule (the owner can change it): prefer booking direct when it's within 5% of the cheapest booking site, because
direct bookings earn the chain's points and nights, usually have better change and cancellation terms, and carry
the chain's best-rate guarantee. Buddy never books: it shows the link to the hotel's own site.
"""

import json
import re
import urllib.parse
from datetime import datetime, timezone
from statistics import median

from .memory import Memory
from .tools import ToolError, local_now

RULE_KEY = "hoteldirect:prefer_within_pct"
DEFAULT_WITHIN = 5.0

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS hotel_direct (
    id INTEGER PRIMARY KEY,
    day TEXT NOT NULL,
    hotel TEXT NOT NULL,
    chain TEXT NOT NULL DEFAULT '',
    place TEXT NOT NULL DEFAULT '',
    direct REAL NOT NULL,            -- a night, on the hotel's own site
    ota REAL,                        -- a night, the cheapest booking site
    ota_source TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL             -- google | owner
);
"""

# chain: (loyalty program, its site, brand names that belong to it)
CHAINS = {
    "Marriott": ("Marriott Bonvoy", "marriott.com", r"marriott|courtyard|sheraton|westin|w hotel|ritz[- ]carlton|"
                 r"residence inn|fairfield|springhill|aloft|moxy|le m[eé]ridien|st\.? regis|renaissance|ac hotel|"
                 r"four points|element by|towneplace|autograph collection|delta hotels|tribute portfolio|edition"),
    "Hilton": ("Hilton Honors", "hilton.com", r"hilton|doubletree|hampton|embassy suites|homewood suites|home2|"
               r"conrad|waldorf|curio|canopy by|tru by|tapestry|motto by|spark by"),
    "Hyatt": ("World of Hyatt", "hyatt.com", r"hyatt|andaz|thompson|alila|miraval|caption by"),
    "IHG": ("IHG One Rewards", "ihg.com", r"intercontinental|holiday inn|crowne plaza|kimpton|staybridge|candlewood|"
            r"hotel indigo|avid hotel|voco|even hotel|regent"),
    "Wyndham": ("Wyndham Rewards", "wyndhamhotels.com", r"wyndham|ramada|days inn|super 8|la quinta|howard johnson|"
                r"travelodge|microtel|baymont|wingate|hawthorn"),
    "Choice": ("Choice Privileges", "choicehotels.com", r"comfort inn|comfort suites|quality inn|sleep inn|clarion|"
               r"econo lodge|cambria|rodeway|mainstay|ascend"),
    "Best Western": ("Best Western Rewards", "bestwestern.com", r"best western|surestay|aiden by"),
    "Accor": ("ALL - Accor Live Limitless", "all.accor.com", r"sofitel|novotel|\bibis\b|fairmont|pullman|mercure|"
              r"swiss[oô]tel|raffles|mgallery"),
    "Radisson": ("Radisson Rewards", "radissonhotels.com", r"radisson|park plaza|country inn"),
}
OTAS = re.compile(r"booking\.com|expedia|hotels\.com|priceline|agoda|trip\.com|kayak|trivago|orbitz|travelocity|"
                  r"hotwire|tripadvisor|vio\.com|super\.com|snaptravel|ebookers|zenhotels|hopper|algotels|"
                  r"prestigia|getaroom|stayforlong|hurb|despegar|booking", re.I)


def _ready(memory: Memory) -> None:
    memory.db.executescript(SCHEMA_SQL)


def chain_of(name: str) -> str:
    for chain, (_, _, rx) in CHAINS.items():
        if re.search(rx, name or "", re.I):
            return chain
    return ""


def within_pct(memory: Memory) -> float:
    try:
        return float(memory.get(RULE_KEY) or DEFAULT_WITHIN)
    except ValueError:
        return DEFAULT_WITHIN


def _direct_seller(name: str, chain: str, sellers: list[dict]) -> dict | None:
    """The seller that is the hotel itself: the chain's own site, "official site", or the hotel's own name."""
    words = {w for w in re.findall(r"[a-z]{4,}", (name or "").lower())} - {"hotel", "hotels", "resort", "suites", "inn"}
    for s in sellers:
        src = str(s.get("source") or "")
        low = src.lower()
        if OTAS.search(low):
            continue
        if "official" in low:
            return s
        if chain and (chain.lower() in low or CHAINS[chain][1].split(".")[0] in low
                      or re.search(CHAINS[chain][2], low, re.I)):
            return s
        if words and len(words & set(re.findall(r"[a-z]{4,}", low))) >= max(1, min(2, len(words))):
            return s
    return None


def direct_link(name: str, chain: str, place: str = "") -> str:
    """Where to check the member price: a search that lands on the hotel's own page (the chain's site when known)."""
    site = CHAINS[chain][1] if chain else ""
    q = f"{name} {place}".strip() + (f" site:{site}" if site else " official site")
    return "https://www.google.com/search?" + urllib.parse.urlencode({"q": q})


def compare(memory: Memory, offer: dict, place: str = "") -> dict:
    """One hotel offer (travel.py's shape, with sellers): direct against the cheapest booking site, and the call."""
    name, nights, cur = offer.get("name", ""), offer.get("nights") or 1, offer.get("currency", "USD")
    chain = chain_of(name)
    sellers = [s for s in offer.get("sellers") or [] if s.get("per_night")]
    direct = _direct_seller(name, chain, sellers)
    others = [s for s in sellers if s is not direct]
    ota = min(others, key=lambda s: s["per_night"]) if others else None
    out = {"hotel": name, "chain": chain, "program": CHAINS[chain][0] if chain else "",
           "direct_per_night": direct["per_night"] if direct else None,
           "direct_seller": direct["source"] if direct else "",
           "best_site_per_night": ota["per_night"] if ota else None, "best_site": ota["source"] if ota else "",
           "check_direct_link": direct_link(name, chain, place), "verdict": "", "why": ""}
    perks = (f"it earns {out['program']} points and nights, usually has better change and cancellation terms, and "
             f"{chain}'s best-rate guarantee applies" if chain else
             "the hotel usually gives better change and cancellation terms and treats direct guests better")
    if direct and ota:
        diff = direct["per_night"] - ota["per_night"]
        pct = diff / ota["per_night"] * 100
        stay = abs(diff) * nights
        if diff <= 0:
            out["verdict"] = "book direct"
            out["why"] = (f"direct is {'the same price' if diff == 0 else f'{cur} {stay:,.0f} cheaper for the stay'} "
                          f"than {ota['source']}, and {perks}")
        elif pct <= within_pct(memory):
            out["verdict"] = "book direct"
            out["why"] = (f"direct is only {cur} {stay:,.0f} more for the stay ({pct:.0f}%) than {ota['source']}, "
                          f"and {perks}; signed-in members often see a lower rate still")
        else:
            out["verdict"] = "booking site, unless the member rate closes the gap"
            out["why"] = (f"{ota['source']} is {cur} {stay:,.0f} cheaper for the stay ({pct:.0f}%); check the member "
                          f"rate on the hotel's site first, and "
                          + (f"{chain}'s best-rate guarantee may match it" if chain else "ask the hotel to match it"))
    elif direct:
        out["verdict"], out["why"] = "book direct", f"only the hotel's own price is shown, and {perks}"
    else:
        out["verdict"] = "check direct"
        out["why"] = ("the hotel's own price isn't in the results: check it"
                      + (f" as a {out['program']} member (free to join)" if chain else " on the hotel's site")
                      + f" before booking with {ota['source']}" if ota else "the hotel's own price isn't shown")
    past = stats(memory, chain).get(chain) if chain else None
    if past and past["checks"] >= 3:
        out["your_record"] = (f"in your {past['checks']} {chain} checks, direct was cheaper or the same "
                              f"{past['direct_wins_pct']}% of the time (typically {past['typical_diff_pct']:+.0f}%)")
    return out


def record(memory: Memory, c: dict, place: str = "", source: str = "google") -> None:
    if not c.get("direct_per_night"):
        return
    _ready(memory)
    day = local_now().date().isoformat()
    if memory.db.execute("SELECT 1 FROM hotel_direct WHERE day = ? AND hotel = ? AND source = ?",
                         (day, c["hotel"], source)).fetchone():
        return
    memory.db.execute("INSERT INTO hotel_direct (day, hotel, chain, place, direct, ota, ota_source, source) "
                      "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                      (day, c["hotel"][:120], c.get("chain", ""), place[:80], c["direct_per_night"],
                       c.get("best_site_per_night"), c.get("best_site", ""), source))
    memory.db.commit()


def note(memory: Memory, hotel: str, direct: float, site_price: float | None = None, site: str = "",
         place: str = "") -> str:
    """The owner checked the hotel's own site: keep the price (and the booking site's, if they said it)."""
    hotel = " ".join(str(hotel or "").split())
    if not hotel or not direct or float(direct) <= 0:
        raise ToolError("Which hotel, and what was the price a night on its own site?")
    _ready(memory)
    if site_price is None:  # the cheapest booking site from the last search that had this hotel
        row = memory.db.execute("SELECT ota, ota_source FROM hotel_direct WHERE hotel LIKE ? AND ota IS NOT NULL "
                                "ORDER BY id DESC LIMIT 1", (f"%{hotel[:40]}%",)).fetchone()
        if row:
            site_price, site = row["ota"], row["ota_source"]
    chain = chain_of(hotel)
    memory.db.execute("INSERT INTO hotel_direct (day, hotel, chain, place, direct, ota, ota_source, source) "
                      "VALUES (?, ?, ?, ?, ?, ?, ?, 'owner')",
                      (local_now().date().isoformat(), hotel[:120], chain, place[:80], float(direct),
                       float(site_price) if site_price else None, site[:60]))
    memory.db.commit()
    if site_price:
        diff = float(direct) - float(site_price)
        return (f"Noted: {hotel} direct {direct:,.0f} a night, "
                + ("the same as" if diff == 0 else f"{abs(diff):,.0f} {'less' if diff < 0 else 'more'} than")
                + f" {site or 'the booking site'}. I'll use it for {chain or 'this hotel'} next time.")
    return f"Noted: {hotel} direct {direct:,.0f} a night."


def stats(memory: Memory, chain: str = "") -> dict:
    """Per chain ('' = every chain, and 'independent'): how often direct was cheaper or the same, and by how much."""
    _ready(memory)
    rows = memory.db.execute("SELECT chain, direct, ota FROM hotel_direct WHERE ota IS NOT NULL AND ota > 0"
                             + (" AND chain = ?" if chain else ""), (chain,) if chain else ()).fetchall()
    by: dict[str, list[float]] = {}
    for r in rows:
        by.setdefault(r["chain"] or "independent", []).append((r["direct"] - r["ota"]) / r["ota"] * 100)
    return {c: {"checks": len(v), "direct_wins_pct": round(100 * sum(d <= 0 for d in v) / len(v)),
                "typical_diff_pct": round(median(v), 1)} for c, v in by.items()}


def advice(comparisons: list[dict], currency: str) -> list[str]:
    """Lines for the search's advice: the top hotels' direct-or-site call."""
    out = []
    for c in comparisons[:3]:
        if c["verdict"]:
            line = f"{c['hotel']}: {c['verdict']} — {c['why']}."
            if c.get("your_record"):
                line += f" ({c['your_record']}.)"
            out.append(line)
    return out


TOOL = {
    "name": "hotel_direct",
    "description": (
        "Booking a hotel direct against booking sites. Every hotels search already carries a 'direct' entry per "
        "hotel (the hotel's own price when Google shows it, the cheapest booking site, the call and why, and a link "
        "to check the member price on the hotel's own site): say it for the hotel you recommend. note: hotel, "
        "direct (a night, what the owner saw on the hotel's own site as a member), optionally site_price and site: "
        "kept so Buddy learns which chains are really cheaper direct. stats: per chain, how often direct was "
        "cheaper or the same and by how much, from the owner's own checks. rule: within_pct (prefer direct when "
        "it's within this % of the cheapest site; 5 by default). Buddy never books."),
    "input_schema": {"type": "object", "properties": {
        "action": {"type": "string", "enum": ["note", "stats", "rule"]},
        "hotel": {"type": "string"}, "direct": {"type": "number"}, "site_price": {"type": "number"},
        "site": {"type": "string"}, "place": {"type": "string"}, "within_pct": {"type": "number"}},
        "required": ["action"], "additionalProperties": False},
}


def run_tool(memory: Memory, args: dict) -> str:
    action = args.get("action")
    if action == "note":
        return note(memory, args.get("hotel", ""), args.get("direct") or 0, args.get("site_price"),
                    args.get("site", ""), args.get("place", ""))
    if action == "stats":
        got = stats(memory)
        return json.dumps(got or {"note": "No comparisons yet: they build up from hotel searches and from prices "
                                          "you tell me you saw on the hotel's own site."})
    if action == "rule":
        if args.get("within_pct") is not None:
            memory.set(RULE_KEY, str(max(0.0, min(float(args["within_pct"]), 50.0))))
        return f"I prefer booking direct when it's within {within_pct(memory):g}% of the cheapest booking site."
    raise ToolError("Use note, stats or rule.")
