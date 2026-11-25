"""Who holds the futures: the CFTC's weekly Commitments of Traders report, for 17 markets.

Every Friday the US Commodity Futures Trading Commission publishes, for each futures market, how many contracts
three kinds of trader held on the Tuesday before:

  large speculators   funds and other big traders who trade for profit ("non-commercial" in the report)
  hedgers             producers, users and dealers protecting a business ("commercial")
  small traders       everyone too small to report ("non-reportable")

Buddy reads the futures-only report for the markets below (metals, energy, stock indexes, Treasuries, currencies,
bitcoin, volatility and crops), keeps five years of it in the price book, and works out for each market: the
large speculators' net position (long minus short), its share of all open contracts, the change from the week
before, and how that position ranks against the last three years of weeks. A position higher than in 90% of
those weeks is "near a 3-year high" (as long as speculators get: crowded); lower than in 90%, "near a 3-year low".
Crowded positions have sometimes come before turns in price, but not reliably: this is background for learning,
never a call to buy or sell.

For stock index, Treasury and currency futures the "commercial" group includes banks and asset managers, so the
speculator/hedger split says less there than it does for commodities; the page says so.

Only the market codes and a date go to the CFTC (publicreporting.cftc.gov, no key). The report is a US government
work, in the public domain; the CFTC asks to be credited, and the page does. Market data only, nothing personal.
"""

import json
import logging
import urllib.parse
from datetime import date, datetime, timedelta

from . import gateway
from .memory import Memory
from .tools import local_now

log = logging.getLogger(__name__)

DATASET = "/resource/6dca-aqww.json"  # Legacy report, futures only
MARKETS = {  # CFTC contract market code -> (our name, group). Names in the report change over time; codes don't.
    "088691": ("Gold", "Metals"), "084691": ("Silver", "Metals"), "085692": ("Copper", "Metals"),
    "067651": ("Crude oil (WTI)", "Energy"), "023651": ("Natural gas", "Energy"),
    "13874A": ("S&P 500 (E-mini)", "Stock indexes"), "209742": ("Nasdaq 100 (E-mini)", "Stock indexes"),
    "1170E1": ("VIX (volatility)", "Stock indexes"),
    "042601": ("2-year Treasury note", "Treasuries"), "043602": ("10-year Treasury note", "Treasuries"),
    "020601": ("Treasury bond", "Treasuries"),
    "099741": ("Euro", "Currencies"), "097741": ("Japanese yen", "Currencies"),
    "133741": ("Bitcoin (CME)", "Crypto"),
    "002602": ("Corn", "Crops"), "005602": ("Soybeans", "Crops"), "001602": ("Wheat", "Crops"),
}
SAID = {"088691": "gold", "084691": "silver", "085692": "copper", "067651": "crude oil", "023651": "natural gas",
        "13874A": "S&P 500 futures", "209742": "Nasdaq 100 futures", "1170E1": "VIX futures",
        "042601": "2-year Treasury notes", "043602": "10-year Treasury notes", "020601": "Treasury bonds",
        "099741": "the euro", "097741": "the yen", "133741": "bitcoin futures", "002602": "corn",
        "005602": "soybeans", "001602": "wheat"}  # each market's name inside a sentence
HIGH, LOW = "near a 3-year high", "near a 3-year low"
GROUPS = ("Stock indexes", "Treasuries", "Currencies", "Metals", "Energy", "Crypto", "Crops")
FINANCIAL = {"Stock indexes", "Treasuries", "Currencies"}
FIELDS = {  # the report's column -> ours
    "open_interest_all": "open_interest",
    "noncomm_positions_long_all": "spec_long", "noncomm_positions_short_all": "spec_short",
    "comm_positions_long_all": "hedger_long", "comm_positions_short_all": "hedger_short",
    "nonrept_positions_long_all": "small_long", "nonrept_positions_short_all": "small_short",
}
RANK_WEEKS, MIN_WEEKS, KEEP_DAYS = 156, 52, 5 * 366
CROWDED = 90  # a rank at or above this is near a 3-year high; at or below 100 - this, near a 3-year low
PULLED_KEY = "cftc:pulled_on"
SOURCE = ("Commitments of Traders (futures only), U.S. Commodity Futures Trading Commission. Public data; "
          "Buddy's working-out, not the CFTC's.")
NOTE = ("Large speculators are funds and other big traders; hedgers are producers, users and dealers protecting a "
        "business. The rank says how the speculators' net position compares with the last three years of weeks; "
        "near a 3-year high or low means crowded, which has sometimes come before a turn in price, but not "
        "reliably. Positions are from the Tuesday before the Friday they're published. For learning, not advice.")
FINANCIAL_NOTE = ("For stock index, Treasury and currency futures, the hedgers include banks and asset managers, "
                  "so the split says less here than for commodities.")

SCHEMA = ("CREATE TABLE IF NOT EXISTS cot_weekly (code VARCHAR, day DATE, report_name VARCHAR, open_interest DOUBLE, "
          "spec_long DOUBLE, spec_short DOUBLE, hedger_long DOUBLE, hedger_short DOUBLE, small_long DOUBLE, "
          "small_short DOUBLE, PRIMARY KEY (code, day))")


def _run(memory: Memory, sql: str, params: tuple = ()) -> list[dict]:
    from . import pricebook  # noqa: PLC0415
    return pricebook.run(memory, sql, params)


def _ready(memory: Memory) -> None:
    _run(memory, SCHEMA)


def _day(value) -> date | None:
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _number(value) -> float | None:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if f == f else None


def query_path(codes: list[str], since: date, limit: int = 5000) -> str:
    """The Socrata query for these markets' weekly rows since a date, oldest first."""
    listed = ", ".join("'" + c + "'" for c in codes if c in MARKETS)  # our own codes only: nothing else goes in
    return DATASET + "?" + urllib.parse.urlencode({
        "$select": ", ".join(["report_date_as_yyyy_mm_dd", "cftc_contract_market_code", "market_and_exchange_names",
                              *FIELDS]),
        "$where": f"cftc_contract_market_code in({listed}) AND report_date_as_yyyy_mm_dd >= "
                  f"'{since.isoformat()}T00:00:00'",
        "$order": "report_date_as_yyyy_mm_dd ASC", "$limit": str(limit)})


def latest_day(memory: Memory) -> date | None:
    try:
        _ready(memory)
        got = _run(memory, "SELECT MAX(day) AS day FROM cot_weekly")
    except Exception:
        log.info("cftc: none held", exc_info=True)
        return None
    return _day(got[0]["day"]) if got and got[0]["day"] else None


def pull(memory: Memory, env=None, fetch=None, today: date | None = None) -> dict:
    """Read the weekly rows Buddy doesn't have yet (three years the first time; the last three weeks again, in
    case the CFTC corrected them) and keep them."""
    today = today or local_now().date()
    _ready(memory)
    latest = latest_day(memory)
    since = latest - timedelta(days=21) if latest else today - timedelta(days=RANK_WEEKS * 7 + 14)
    got = gateway.call(memory, "cftc", query_path(list(MARKETS), since), env=env, fetch=fetch,
                       purpose="futures positioning") or []
    kept = 0
    for row in got if isinstance(got, list) else []:
        code, day = str(row.get("cftc_contract_market_code") or "").strip(), _day(row.get("report_date_as_yyyy_mm_dd"))
        values = {ours: _number(row.get(theirs)) for theirs, ours in FIELDS.items()}
        if code not in MARKETS or day is None or values["open_interest"] is None:
            continue
        _run(memory, "INSERT OR REPLACE INTO cot_weekly (code, day, report_name, open_interest, spec_long, spec_short, "
                     "hedger_long, hedger_short, small_long, small_short) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
             (code, day.isoformat(), str(row.get("market_and_exchange_names") or "")[:120],
              *(values[k] for k in FIELDS.values())))
        kept += 1
    _run(memory, "DELETE FROM cot_weekly WHERE day < ?", ((today - timedelta(days=KEEP_DAYS)).isoformat(),))
    memory.set(PULLED_KEY, today.isoformat())
    newest = latest_day(memory)
    return {"rows": kept, "latest": newest.isoformat() if newest else "", "was": latest.isoformat() if latest else ""}


def due(memory: Memory, now: datetime | None = None) -> bool:
    """The report comes out on Friday afternoon (US Eastern) for the Tuesday before, so a new one is out ten
    days after the latest one held. Asked at most once a day: the first time at once, then from Friday 5 pm on
    (and each day after, if a holiday pushed it to Monday)."""
    now = now or local_now()
    if memory.get(PULLED_KEY) == now.date().isoformat():
        return False
    latest = latest_day(memory)
    if latest is None:
        return True
    wait = (now.date() - latest).days
    return wait > 10 or (wait == 10 and now.hour >= 17)


def _rank(value: float, history: list[float]) -> int | None:
    """Where a value stands among past weeks, 0 to 100: the share of them it's higher than (a week it equals
    counts half, so a position that keeps coming back isn't called a 3-year low)."""
    if len(history) < MIN_WEEKS:
        return None
    return round(100 * (sum(1 for h in history if h < value) + sum(0.5 for h in history if h == value)) / len(history))


def _contracts(n: float) -> str:
    return f"{abs(round(n)):,} contract{'s' if abs(round(n)) != 1 else ''}"


def standing(rank: int | None) -> str:
    if rank is None:
        return ""
    if rank >= CROWDED:
        return HIGH
    if rank <= 100 - CROWDED:
        return LOW
    return "higher than usual" if rank >= 70 else "lower than usual" if rank <= 30 else "middle of its range"


def reading(name: str, m: dict) -> str:
    """One plain paragraph on where the large speculators stand in a market."""
    side = "long" if m["net"] > 0 else "short" if m["net"] < 0 else "flat"
    said = SAID.get(m.get("code", ""), name)
    text = (f"In {said}, large speculators are net {side}" + (f" {_contracts(m['net'])}" if side != "flat" else "")
            + (f" ({m['net_pct']}% of all open contracts)" if m.get("net_pct") is not None else ""))
    if m.get("week_change") is not None and m["week_change"]:
        more = (m["week_change"] > 0) == (m["net"] >= 0)
        text += f", {'more' if more else 'less'} {side if side != 'flat' else 'long'} than a week before by " \
                f"{_contracts(m['week_change'])}"
    text += "."
    if m.get("rank") is not None and m["standing"] == LOW:
        how = "least long" if m["net"] > 0 else "most short"
        text += (f" That's lower than in {100 - m['rank']}% of the last {m['weeks']} weeks: near the {how} "
                 f"(bearish) they have been in three years, a crowded position.")
    elif m.get("rank") is not None:
        how = "least short" if m["net"] < 0 else "most long"
        text += f" That's higher than in {m['rank']}% of the last {m['weeks']} weeks"
        text += (f": near the {how} (bullish) they have been in three years, a crowded position."
                 if m["standing"] == HIGH else ".")
    else:
        text += f" There are only {m['weeks']} weeks to compare it with so far."
    if m.get("hedger_net") is not None and m["net"] and (m["hedger_net"] > 0) != (m["net"] > 0):
        text += f" Hedgers are on the other side, net {'long' if m['hedger_net'] > 0 else 'short'}."
    return text


def markets(memory: Memory) -> list[dict]:
    """Each market's latest week, worked out against its own history."""
    try:
        _ready(memory)
        rows = _run(memory, "SELECT * FROM cot_weekly ORDER BY code, day")
    except Exception:
        log.info("cftc: none held", exc_info=True)
        return []
    by: dict[str, list[dict]] = {}
    for r in rows:
        by.setdefault(r["code"], []).append(r)
    out = []
    for code, (name, group) in MARKETS.items():
        weeks = by.get(code) or []
        if not weeks:
            continue
        now = weeks[-1]
        net = (now["spec_long"] or 0) - (now["spec_short"] or 0)
        history = [(w["spec_long"] or 0) - (w["spec_short"] or 0) for w in weeks[-RANK_WEEKS - 1:-1]]
        prev = weeks[-2] if len(weeks) > 1 and (_day(now["day"]) - _day(weeks[-2]["day"])).days <= 14 else None
        m = {"code": code, "name": name, "group": group, "day": str(_day(now["day"])), "net": net,
             "net_share": round(net / now["open_interest"], 3) if now["open_interest"] else None,
             "net_pct": round(abs(net) * 100 / now["open_interest"]) if now["open_interest"] else None,
             "open_interest": now["open_interest"],
             "week_change": net - ((prev["spec_long"] or 0) - (prev["spec_short"] or 0)) if prev else None,
             "hedger_net": (now["hedger_long"] or 0) - (now["hedger_short"] or 0),
             "small_net": (now["small_long"] or 0) - (now["small_short"] or 0),
             "rank": _rank(net, history), "weeks": len(history),
             "trend": [round((w["spec_long"] or 0) - (w["spec_short"] or 0)) for w in weeks[-26:]]}
        m["standing"] = standing(m["rank"])
        m["reading"] = reading(name, m)
        out.append(m)
    return out


def page(memory: Memory) -> dict:
    """The positioning card: markets by group, the crowded ones first in a short list, and where it comes from."""
    found = markets(memory)
    if not found:
        return {"groups": [], "crowded": [], "as_of": "", "source": SOURCE, "note": NOTE,
                "empty": "Nothing read yet: Buddy reads the report the first time the server runs its nightly jobs, "
                         "or press Read now."}
    groups = [{"title": g, "rows": [m for m in found if m["group"] == g], "note": ""} for g in GROUPS]
    groups = [g for g in groups if g["rows"]]
    financial = [g for g in groups if g["title"] in FINANCIAL]
    if financial:  # said once, under the last of the three
        financial[-1]["note"] = FINANCIAL_NOTE
    as_of = max(m["day"] for m in found)
    return {"groups": groups, "as_of": as_of,
            "crowded": [{"name": m["name"], "standing": m["standing"]} for m in found if m["standing"] in (HIGH, LOW)],
            "source": SOURCE, "note": NOTE, "pulled_on": memory.get(PULLED_KEY) or ""}


def check(memory, env=None, fetch=None) -> None:
    """Buddy Connect's test: one row for gold."""
    gateway.call(memory, "cftc", query_path(["088691"], local_now().date() - timedelta(days=30), limit=1), env=env,
                 fetch=fetch, purpose="health")


def _find(name: str) -> list[str]:
    words = name.lower().split()
    aliases = {"oil": "067651", "crude": "067651", "gas": "023651", "s&p": "13874A", "sp500": "13874A",
               "nasdaq": "209742", "vix": "1170E1", "bitcoin": "133741", "btc": "133741", "yen": "097741",
               "euro": "099741", "10-year": "043602", "2-year": "042601", "bond": "020601", "bonds": "020601"}
    hits = [aliases[w] for w in words if w in aliases]
    hits += [c for c, (n, g) in MARKETS.items() if any(w in n.lower() or w == g.lower() for w in words)]
    return list(dict.fromkeys(hits))


TOOL = {
    "name": "futures_positioning",
    "description": (
        "Who holds the futures, from the CFTC's weekly Commitments of Traders report, for gold, silver, copper, "
        "crude oil, natural gas, the S&P 500 and Nasdaq 100 futures, VIX, 2-year and 10-year Treasury notes, "
        "Treasury bonds, the euro, the yen, bitcoin (CME), corn, soybeans and wheat. show (market, optional: a "
        "name like gold or oil; empty for all): large speculators' net position, its share of open contracts, the "
        "week's change, and its rank against the last three years (0 to 100: near a 3-year high at 90 or more, "
        "near a 3-year low at 10 or less, both crowded), with hedgers' side. refresh: read the latest report now (done weekly anyway). Always give "
        "the report date, say positions are from the Tuesday before and that crowded positions have sometimes, "
        "not reliably, come before turns; never advice to buy or sell. Credit the CFTC as the source."),
    "input_schema": {"type": "object", "properties": {
        "action": {"type": "string", "enum": ["show", "refresh"]}, "market": {"type": "string"}},
        "required": ["action"], "additionalProperties": False},
}


def run_tool(memory: Memory, args: dict) -> str:
    if args.get("action") == "refresh":
        try:
            got = pull(memory)
        except gateway.ConnectError as e:
            return json.dumps({"error": str(e)})
        return json.dumps({"read": got, **page(memory)})
    found = markets(memory)
    wanted = _find(str(args.get("market") or ""))
    if args.get("market") and not wanted:
        return json.dumps({"error": f"Not one of the markets Buddy follows: {', '.join(n for n, _ in MARKETS.values())}."})
    rows = [m for m in found if not wanted or m["code"] in wanted]
    if not rows:
        return json.dumps({"error": "No report read yet. Say 'refresh' to read it now."})
    return json.dumps({"markets": [{k: v for k, v in m.items() if k != "trend"} for m in rows], "source": SOURCE,
                       "note": NOTE})
