"""Themes: the Intelligence Platform's evidence joined to the market, and tested with practice money.

  Themes     each technology the platform follows, with the public companies its filings name, how its evidence
             is moving, and which of those companies are on the owner's watchlist or in their trade ideas and
             practice portfolio. Matching is plain code: no model sees the owner's lists.
  Patterns   fixed rules that notice a change in the evidence (PATTERNS below): several kinds rising at once,
             a jump in company filings, a jump in news, a jump in research, a large award or grant, a run of
             private fundraising notices. Each time a rule fires for a technology that has public companies, an
             event is written with that day's closing prices.
  Practice   every event is a practice basket: pretend money split equally across up to five of the companies,
             measured after 30 and after 90 days against the S&P 500 fund (SPY) over the same days. Nothing is
             bought. No costs or taxes are taken off, so real results would be a little worse.
  Record     per pattern: how many baskets have finished, how many beat the market, the middle result. Until a
             pattern has MIN_EVENTS finished baskets Buddy says only that there is not enough history. After
             that it says which way the pattern has leaned so far. It is a record of the past, never a promise,
             and events close together in time are not independent tests.

Stored in the public-data store beside the evidence: these are public prices and Buddy's own practice results.
"""

import json
import logging
import re
import statistics
from datetime import date, timedelta

from . import bip
from . import bip_sources as src
from .memory import Memory
from .tools import local_now

log = logging.getLogger(__name__)

SCHEMA = (
    "CREATE SEQUENCE IF NOT EXISTS theme_id",
    "CREATE TABLE IF NOT EXISTS theme_events (id BIGINT PRIMARY KEY DEFAULT nextval('theme_id'), day DATE, "
    "tech VARCHAR, pattern VARCHAR, why VARCHAR, symbols VARCHAR, entry VARCHAR, spy DOUBLE, r30 DOUBLE, "
    "s30 DOUBLE, r90 DOUBLE, s90 DOUBLE, evidence VARCHAR)",
)
PATTERNS = {  # name -> (title, the rule in words)
    "forming": ("Several kinds of evidence rising together",
                "three or more kinds of evidence each at least 3 records and 1.5 times the 30 days before"),
    "filings": ("A jump in company filings", "3 or more filings in 30 days and at least double the 30 days before"),
    "news": ("A jump in news and discussion", "5 or more reports in 14 days and at least double the 14 days before"),
    "research": ("A jump in research", "5 or more papers in 30 days and at least double the 30 days before"),
    "award": ("A large award or grant", "a US federal award or research grant of $10 million or more in the last 30 days"),
    "funding": ("A run of private fundraising", "3 or more private fundraising notices in 30 days"),
}
HORIZONS = (30, 90)
MIN_EVENTS = 30  # finished baskets a pattern needs before Buddy says which way it has leaned
BASKET, STAKE, BIG = 5, 10_000.0, 10_000_000.0
TICKER = re.compile(r"\(([A-Z]{1,5}(?:[.\-][A-Z])?)(?:,\s*[A-Z.\-]+)*\)")
NOTE = ("A record of what happened after past evidence changes, tested with practice money. It is not advice and "
        "not a forecast: more evidence about a technology does not mean its shares rise, and the price often moves "
        "first. Buddy never trades.")


def _ready(memory: Memory) -> None:
    bip._ready(memory)
    for statement in SCHEMA:
        bip._run(memory, statement)


def ticker(who: str) -> str:
    found = TICKER.search(str(who or ""))
    return found.group(1) if found else ""


def tickers(rows: list[dict], tech: str) -> list[dict]:
    """The public companies a technology's filings name, most records first."""
    seen: dict[str, dict] = {}
    for r in rows:
        symbol = ticker(r["who"]) if r["kind"] == "filing" and tech in r["techs"] else ""
        if symbol:
            entry = seen.setdefault(symbol, {"symbol": symbol, "name": TICKER.sub("", r["who"]).strip(" ,"), "records": 0})
            entry["records"] += 1
    return sorted(seen.values(), key=lambda t: (-t["records"], t["symbol"]))


def yours(memory: Memory) -> dict[str, str]:
    """Symbols on the owner's watchlist, in their trade ideas or in the practice portfolio -> where. Code only."""
    out: dict[str, str] = {}
    try:
        from . import paper  # noqa: PLC0415
        out.update({str(symbol).upper(): "practice portfolio" for symbol in paper.state(memory)["held"]})
    except Exception:
        log.debug("themes: no practice portfolio", exc_info=True)
    try:
        from .markets import Markets  # noqa: PLC0415
        mk = Markets(memory)
        out.update({str(p["symbol"]).upper(): "open trade idea" for p in mk.positions()})
        out.update({str(w["symbol"]).upper(): "watchlist" for w in mk.watchlist() if w.get("market") == "us"})
    except Exception:
        log.debug("themes: no watchlist", exc_info=True)
    return out


# --- the rules ----------------------------------------------------------------------------------------------
def _count(rows: list[dict], kinds: tuple, start: str, end: str) -> int:
    return sum(1 for r in rows if r["kind"] in kinds and start <= r["day"] < end)


def fired(rows: list[dict], today: date, forming: dict | None = None) -> list[dict]:
    """The patterns one technology's records show today. rows: that technology's records, last 60 days or more."""
    day = lambda n: (today - timedelta(days=n)).isoformat()  # noqa: E731
    end, out = (today + timedelta(days=1)).isoformat(), []

    def jump(name: str, kinds: tuple, span: int, least: int) -> None:
        now, before = _count(rows, kinds, day(span), end), _count(rows, kinds, day(2 * span), day(span))
        if now >= least and now >= 2 * before:
            out.append({"pattern": name, "why": f"{now} in the last {span} days against {before} in the {span} before",
                        "kinds": kinds, "since": day(span)})
    if forming:
        out.append({"pattern": "forming", "why": "; ".join(forming["rising"]), "kinds": src.KINDS, "since": day(30)})
    jump("filings", ("filing",), 30, 3)
    jump("news", ("news", "discussion"), 14, 5)
    jump("research", ("research",), 30, 5)
    big = [r for r in rows if r["kind"] in ("award", "grant") and (r["amount"] or 0) >= BIG and day(30) <= r["day"] < end]
    if big:
        top = max(big, key=lambda r: r["amount"])
        out.append({"pattern": "award", "why": f"${top['amount'] / 1e6:,.0f} million to {top['who'] or 'a recipient'}",
                    "kinds": ("award", "grant"), "since": day(30)})
    notices = _count(rows, ("funding",), day(30), end)
    if notices >= 3:
        out.append({"pattern": "funding", "why": f"{notices} private fundraising notices in 30 days",
                    "kinds": ("funding",), "since": day(30)})
    return out


def _data(memory: Memory, data):
    if data is not None:
        return data
    from .markets import Data  # noqa: PLC0415
    return Data(memory)


def _at(bars: list[dict], day: str) -> float | None:
    """The close on that day, or the last one before it."""
    price = None
    for b in bars or []:
        if str(b["d"])[:10] > day:
            break
        price = b["c"]
    return price


def detect(memory: Memory, today: date | None = None, data=None) -> list[dict]:
    """Write an event, with today's closes, for each pattern that fires on a technology with public companies.
    A pattern is written once per technology in any 30 days."""
    today = today or local_now().date()
    _ready(memory)
    rows = bip._signals(memory, today - timedelta(days=365))
    forming = {f["id"]: f for f in bip.formations(memory, today)}
    recent = {(r["tech"], r["pattern"]) for r in bip._run(
        memory, "SELECT tech, pattern FROM theme_events WHERE day > ?", ((today - timedelta(days=30)).isoformat(),))}
    wanted = []
    for tid, t in bip.TECHS.items():
        if t["sector"] not in bip.sectors_on(memory):
            continue
        mine = [r for r in rows if tid in r["techs"]]
        names = tickers(mine, tid)[:BASKET]
        if not names:
            continue
        for f in fired(mine, today, forming.get(tid)):
            if (tid, f["pattern"]) not in recent:
                wanted.append((tid, f, names, mine))
    if not wanted:
        return []
    symbols = sorted({c["symbol"] for item in wanted for c in item[2]} | {"SPY"})
    try:
        bars = _data(memory, data).bars("us", symbols, days=10)
    except Exception as e:
        log.info("themes: no prices tonight (%s)", type(e).__name__)
        return []
    last = {s: b[-1]["c"] for s, b in bars.items() if b and b[-1].get("c")}
    out = []
    for tid, f, names, mine in wanted:
        entry = {n["symbol"]: last[n["symbol"]] for n in names if n["symbol"] in last}
        if not entry or "SPY" not in last:
            continue  # no price for any of its companies: nothing to measure
        proof = [{"title": r["title"], "url": r["url"], "day": r["day"], "kind": r["kind"]}
                 for r in mine if r["kind"] in f["kinds"] and r["day"] >= f["since"]][:6]
        bip._run(memory, "INSERT INTO theme_events (day, tech, pattern, why, symbols, entry, spy, evidence) "
                         "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                 (today.isoformat(), tid, f["pattern"], f["why"], json.dumps(sorted(entry)), json.dumps(entry),
                  last["SPY"], json.dumps(proof)))
        out.append({"tech": tid, "name": bip.TECHS[tid]["name"], "pattern": f["pattern"],
                    "title": PATTERNS[f["pattern"]][0], "why": f["why"], "symbols": sorted(entry)})
    return out


def measure(memory: Memory, today: date | None = None, data=None) -> int:
    """Fill in each event's 30- and 90-day result once those days have passed. Returns how many were filled."""
    today = today or local_now().date()
    _ready(memory)
    due = [e for e in _events(memory) for n in HORIZONS
           if e[f"r{n}"] is None and (today - date.fromisoformat(e["day"])).days >= n]
    if not due:
        return 0
    oldest = min(date.fromisoformat(e["day"]) for e in due)
    symbols = sorted({s for e in due for s in e["entry"]} | {"SPY"})
    try:
        bars = _data(memory, data).bars("us", symbols, days=(today - oldest).days + 10)
    except Exception as e:
        log.info("themes: no prices to measure with (%s)", type(e).__name__)
        return 0
    filled = 0
    for e in {e["id"]: e for e in due}.values():
        for n in HORIZONS:
            target = date.fromisoformat(e["day"]) + timedelta(days=n)
            if e[f"r{n}"] is not None or target > today:
                continue
            moves = [(_at(bars.get(s), target.isoformat()) or 0) / p - 1 for s, p in e["entry"].items()
                     if p and _at(bars.get(s), target.isoformat())]
            spy = _at(bars.get("SPY"), target.isoformat())
            if not moves or not spy or not e["spy"]:
                continue
            bip._run(memory, f"UPDATE theme_events SET r{n} = ?, s{n} = ? WHERE id = ?",  # n is 30 or 90, from HORIZONS
                     (round(sum(moves) / len(moves), 4), round(spy / e["spy"] - 1, 4), e["id"]))
            filled += 1
    return filled


def _events(memory: Memory) -> list[dict]:
    _ready(memory)
    rows = bip._run(memory, "SELECT * FROM theme_events ORDER BY day DESC, id DESC")
    for r in rows:
        r["day"] = str(r["day"])
        r["symbols"], r["entry"], r["evidence"] = (json.loads(r[k] or d) for k, d in
                                                   (("symbols", "[]"), ("entry", "{}"), ("evidence", "[]")))
    return rows


# --- the record ---------------------------------------------------------------------------------------------
def lean(done: list[tuple[float, float]]) -> dict:
    """What a pattern's finished baskets show. done: (basket return, market return) pairs."""
    n = len(done)
    if not n:
        return {"finished": 0, "lean": "none", "says": f"No finished baskets yet; {MIN_EVENTS} are needed."}
    excess = [r - s for r, s in done]
    beat, middle = sum(1 for x in excess if x > 0), statistics.median(excess)
    out = {"finished": n, "beat_market": beat, "share": round(beat / n, 2), "middle_against_market": round(middle, 4),
           "average_return": round(sum(r for r, _ in done) / n, 4)}
    if n < MIN_EVENTS:
        return {**out, "lean": "none", "says": f"Not enough history yet: {n} of {MIN_EVENTS} finished baskets."}
    if beat / n >= 0.6 and middle > 0:
        return {**out, "lean": "up", "says": f"So far these have beaten the market {beat} times in {n}."}
    if beat / n <= 0.4 and middle < 0:
        return {**out, "lean": "down", "says": f"So far these have trailed the market {n - beat} times in {n}."}
    return {**out, "lean": "flat", "says": f"No clear edge either way in {n} finished baskets."}


def record(events: list[dict]) -> list[dict]:
    out = []
    for name, (title, rule) in PATTERNS.items():
        mine = [e for e in events if e["pattern"] == name]
        row = {"pattern": name, "title": title, "rule": rule, "events": len(mine)}
        for n in HORIZONS:
            row[f"days_{n}"] = lean([(e[f"r{n}"], e[f"s{n}"]) for e in mine if e[f"r{n}"] is not None])
        out.append(row)
    return out


def scorecard(events: list[dict]) -> dict:
    """The practice strategy: $10,000 of pretend money in each 'forming' basket, held 90 days."""
    mine = [e for e in events if e["pattern"] == "forming"]
    done = [e for e in mine if e["r90"] is not None]
    return {"stake": STAKE, "baskets": len(mine), "open": len(mine) - len(done), "finished": len(done),
            "profit": round(sum(STAKE * e["r90"] for e in done), 2),
            "market_profit": round(sum(STAKE * e["s90"] for e in done), 2),
            **{k: v for k, v in lean([(e["r90"], e["s90"]) for e in done]).items() if k != "finished"},
            "rule": "When several kinds of evidence rise together for a technology, put $10,000 of pretend money "
                    "equally into up to five public companies its filings name, and close after 90 days."}


def themes(memory: Memory, today: date | None = None, events: list[dict] | None = None) -> list[dict]:
    today = today or local_now().date()
    rows = bip._signals(memory, today - timedelta(days=365))
    owned, events = yours(memory), _events(memory) if events is None else events
    live = {}
    for e in events:
        if (today - date.fromisoformat(e["day"])).days <= 30:
            live.setdefault(e["tech"], []).append(e["pattern"])
    stats = {r["pattern"]: r for r in record(events)}
    out = []
    for t in bip.technologies(memory, today):
        names = tickers([r for r in rows if t["id"] in r["techs"]], t["id"])
        if not t["signals_90_days"] and not names:
            continue
        out.append({**{k: t[k] for k in ("id", "name", "sector_name", "stage", "growth", "signals_90_days", "kinds")},
                    "companies": names[:8],
                    "yours": [{"symbol": n["symbol"], "where": owned[n["symbol"]]} for n in names if n["symbol"] in owned],
                    "active": [{"pattern": p, "title": PATTERNS[p][0], "record": stats[p]["days_90"]["says"],
                                "lean": stats[p]["days_90"]["lean"]} for p in dict.fromkeys(live.get(t["id"], []))]})
    return sorted(out, key=lambda t: (not t["yours"], not t["active"], -t["signals_90_days"]))


def page(memory: Memory, data=None, today: date | None = None) -> dict:
    """Everything the Themes tab shows."""
    today = today or local_now().date()
    events = _events(memory)
    open_ = [e for e in events if e["r90"] is None]
    last = {}
    if open_:
        try:
            bars = _data(memory, data).bars("us", sorted({s for e in open_ for s in e["entry"]} | {"SPY"}), days=10)
            last = {s: b[-1]["c"] for s, b in bars.items() if b}
        except Exception as e:
            log.info("themes prices: %s", type(e).__name__)

    def view(e: dict) -> dict:
        moves = [last[s] / p - 1 for s, p in e["entry"].items() if p and s in last]
        return {"day": e["day"], "tech": e["tech"], "name": bip.TECHS.get(e["tech"], {}).get("name", e["tech"]),
                "pattern": e["pattern"], "title": PATTERNS.get(e["pattern"], (e["pattern"],))[0], "why": e["why"],
                "symbols": e["symbols"], "evidence": e["evidence"],
                "days": (today - date.fromisoformat(e["day"])).days,
                "after_30": e["r30"], "market_30": e["s30"], "after_90": e["r90"], "market_90": e["s90"],
                "so_far": round(sum(moves) / len(moves), 4) if moves and e["r90"] is None else None,
                "market_so_far": round(last["SPY"] / e["spy"] - 1, 4) if moves and e["r90"] is None and "SPY" in last
                and e["spy"] else None}
    return {"themes": themes(memory, today, events), "events": [view(e) for e in events[:60]],
            "patterns": record(events), "practice": scorecard(events), "needed": MIN_EVENTS, "note": NOTE,
            "yours": len(yours(memory))}


def news(memory: Memory, today: date | None = None, days: int = 3) -> list[dict]:
    """Recent evidence changes, for Buddy News."""
    today = today or local_now().date()
    owned, stats = yours(memory), None
    out = []
    for e in _events(memory):
        if (today - date.fromisoformat(e["day"])).days > days:
            break
        stats = stats or {r["pattern"]: r for r in record(_events(memory))}
        out.append({"day": e["day"], "tech": e["tech"], "name": bip.TECHS.get(e["tech"], {}).get("name", e["tech"]),
                    "title": PATTERNS[e["pattern"]][0], "why": e["why"], "symbols": e["symbols"],
                    "yours": [s for s in e["symbols"] if s in owned], "evidence": e["evidence"][:3],
                    "record": stats[e["pattern"]]["days_90"]["says"]})
    return out


def run(memory: Memory, today: date | None = None, data=None) -> dict:
    """After the nightly evidence run: notice the patterns, and fill in results that have come due."""
    today = today or local_now().date()
    filled = measure(memory, today, data)
    return {"new": detect(memory, today, data), "measured": filled}


def message(result: dict, memory: Memory) -> str:
    """Worth saying when it touches something the owner follows, or when several kinds rose together."""
    owned = yours(memory)
    new = [e for e in result.get("new") or [] if e["pattern"] == "forming" or any(s in owned for s in e["symbols"])]
    if not new:
        return ""
    stats = {r["pattern"]: r for r in record(_events(memory))}
    lines = []
    for e in new[:5]:
        mine = [s for s in e["symbols"] if s in owned]
        lines.append(f"- **{e['name']}**: {e['title'].lower()} ({e['why']}). Companies in its filings: "
                     f"{', '.join(e['symbols'])}" + (f". On your lists: {', '.join(mine)}" if mine else "")
                     + f". {stats[e['pattern']]['days_90']['says']}")
    return (f"Themes: the evidence changed for {new[0]['name']}"
            + (f" and {len(new) - 1} more" if len(new) > 1 else "") + ". Details are on screen. || "
            "**📈 Themes: the evidence changed**\n\n" + "\n".join(lines)
            + "\n\nBuddy opened a practice basket for each to see what follows. Not advice; nothing was bought.")
