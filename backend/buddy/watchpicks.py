"""Recommended watchlist: public companies the Intelligence evidence points at, refreshed each night.

Your own watchlist stays yours. This is a second list that Buddy keeps from its market intelligence:

  recommended  a company a technology's filings name, where the company was named in a filing in the last
               30 days or one of the technology's evidence patterns fired in the last 30 days. Each one says
               why, with the sector and the technology it belongs to. The strongest 30 are listed and are
               added to the daily scan next to your own symbols.
  archived     a recommended company with nothing new for 30 days. It is moved out of the list, not deleted,
               and kept grouped by sector and technology with the dates it was recommended. If its evidence
               picks up again it comes back by itself.
  dismissed    one you removed. Buddy does not recommend it again unless you restore it.

It reads only the public evidence (filings, patterns) in the price book. No AI model is used and no outside
call is made here. A recommendation is a reason to look, not advice to buy; nothing is ever bought.
"""

import logging
from datetime import date, timedelta

from . import bip
from .memory import Memory
from .tools import ToolError, local_now

log = logging.getLogger("buddy.watchpicks")

ACTIVE_DAYS = 30   # named in a filing, or a pattern fired, within this many days
ARCHIVE_AFTER = 30  # days with nothing new before a recommendation is archived
SHOWN = 30          # the strongest recommendations listed, and added to the daily scan
SCHEMA = ["""CREATE TABLE IF NOT EXISTS watch_picks (
    symbol VARCHAR PRIMARY KEY, name VARCHAR, sector VARCHAR, industry VARCHAR, why VARCHAR, score DOUBLE,
    first_seen DATE, last_active DATE, status VARCHAR DEFAULT 'active', archived_on DATE)"""]


def _ready(memory: Memory) -> None:
    bip._ready(memory)
    for statement in SCHEMA:
        bip._run(memory, statement)


def candidates(memory: Memory, today: date) -> list[dict]:
    """Companies with something new in the last ACTIVE_DAYS, each with its sector, technology, reason and score."""
    from . import themes  # noqa: PLC0415
    rows = themes._rows(memory, today)
    since = (today - timedelta(days=ACTIVE_DAYS)).isoformat()
    fired: dict[str, list[str]] = {}
    for e in themes._events(memory):
        if e["day"] >= since and e["pattern"] in themes.PATTERNS and themes.PATTERNS[e["pattern"]][0] not in fired.get(e["tech"], []):
            fired.setdefault(e["tech"], []).append(themes.PATTERNS[e["pattern"]][0])
    best: dict[str, dict] = {}
    for t in bip.technologies(memory, today):
        mine = [r for r in rows if t["id"] in r["techs"]]
        recent = {c["symbol"]: c["records"] for c in themes.tickers([r for r in mine if r["day"] >= since], t["id"])}
        patterns = fired.get(t["id"], [])
        for c in themes.tickers(mine, t["id"]):
            n = recent.get(c["symbol"], 0)
            if not n and not patterns:
                continue
            why = []
            if n:
                why.append(f"named in {n} filing{'s' if n != 1 else ''} on {t['name']} in the last {ACTIVE_DAYS} days")
            if patterns:
                why.append(f"{patterns[0].lower()} for {t['name']}" + (f" (and {len(patterns) - 1} more)" if len(patterns) > 1 else ""))
            pick = {"symbol": c["symbol"], "name": c["name"], "sector": t["sector_name"], "industry": t["name"],
                    "why": "; ".join(why)[:300], "score": float(n + 3 * len(patterns) + min(c["records"], 10) / 10)}
            if pick["score"] > best.get(c["symbol"], {"score": -1})["score"]:
                best[c["symbol"]] = pick  # a company in two technologies is listed under its strongest
    return sorted(best.values(), key=lambda p: (-p["score"], p["symbol"]))


def refresh(memory: Memory, today: date | None = None) -> dict:
    """The nightly pass: add or renew what is active, bring back archived ones that are active again, and
    archive what has had nothing new for ARCHIVE_AFTER days. Returns {"added", "returned", "archived"}."""
    today = today or local_now().date()
    _ready(memory)
    have = {r["symbol"]: r for r in bip._run(memory, "SELECT symbol, status FROM watch_picks")}
    out: dict[str, list[str]] = {"added": [], "returned": [], "archived": []}
    for p in candidates(memory, today):
        old = have.get(p["symbol"])
        if old is None:
            bip._run(memory, "INSERT INTO watch_picks (symbol, name, sector, industry, why, score, first_seen, last_active, "
                             "status) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'active')",
                     (p["symbol"], p["name"], p["sector"], p["industry"], p["why"], p["score"], today.isoformat(),
                      today.isoformat()))
            out["added"].append(p["symbol"])
            continue
        status = old["status"] if old["status"] == "dismissed" else "active"
        if old["status"] == "archived":
            out["returned"].append(p["symbol"])
        bip._run(memory, "UPDATE watch_picks SET name = ?, sector = ?, industry = ?, why = ?, score = ?, last_active = ?, "
                         "status = ?, archived_on = NULL WHERE symbol = ?",
                 (p["name"], p["sector"], p["industry"], p["why"], p["score"], today.isoformat(), status, p["symbol"]))
    cutoff = (today - timedelta(days=ARCHIVE_AFTER)).isoformat()
    for r in bip._run(memory, "SELECT symbol FROM watch_picks WHERE status = 'active' AND last_active < ?", (cutoff,)):
        bip._run(memory, "UPDATE watch_picks SET status = 'archived', archived_on = ? WHERE symbol = ?",
                 (today.isoformat(), r["symbol"]))
        out["archived"].append(r["symbol"])
    return out


def _grouped(rows: list[dict]) -> list[dict]:
    """Sector, then technology, then the companies: [{"sector", "count", "industries": [{"industry", "picks"}]}]."""
    tree: dict[str, dict[str, list]] = {}
    for r in rows:
        tree.setdefault(r["sector"] or "Other", {}).setdefault(r["industry"] or "Other", []).append(r)
    return [{"sector": s, "count": sum(len(v) for v in inds.values()),
             "industries": [{"industry": i, "picks": picks} for i, picks in sorted(inds.items())]}
            for s, inds in sorted(tree.items())]


def view(memory: Memory, today: date | None = None) -> dict:
    """What the Watchlist tab shows under your own symbols: recommended, archived and dismissed."""
    today = today or local_now().date()
    _ready(memory)
    rows = bip._run(memory, "SELECT * FROM watch_picks ORDER BY score DESC, symbol")
    try:
        from .markets import Markets  # noqa: PLC0415
        own = {w["symbol"].upper() for w in Markets(memory).watchlist()}
    except Exception:
        own = set()
    for r in rows:
        for k in ("first_seen", "last_active", "archived_on"):
            r[k] = str(r[k]) if r.get(k) else ""
        r["yours"] = r["symbol"] in own
        r["quiet_days"] = (today - date.fromisoformat(r["last_active"])).days if r["last_active"] else None
    active = [r for r in rows if r["status"] == "active"][:SHOWN]
    archived = sorted((r for r in rows if r["status"] == "archived"), key=lambda r: r["archived_on"], reverse=True)
    return {"recommended": _grouped(active), "recommended_count": len(active),
            "archive": _grouped(archived), "archive_count": len(archived),
            "dismissed": [r["symbol"] for r in rows if r["status"] == "dismissed"],
            "rules": f"Recommended: a company a technology's filings name, with a filing or an evidence pattern in the "
                     f"last {ACTIVE_DAYS} days. Archived after {ARCHIVE_AFTER} days with nothing new, and brought back if "
                     "the evidence picks up. A reason to look, not advice to buy."}


def symbols(memory: Memory) -> set[str]:
    """The recommended symbols the daily scan should also read (the strongest SHOWN)."""
    try:
        _ready(memory)
        return {r["symbol"] for r in bip._run(
            memory, "SELECT symbol FROM watch_picks WHERE status = 'active' ORDER BY score DESC, symbol LIMIT ?", (SHOWN,))}
    except Exception:
        log.info("watchpicks: no recommendations to scan", exc_info=True)
        return set()


def decide(memory: Memory, symbol: str, action: str) -> dict:
    """dismiss: stop recommending it. restore: recommend it again (from the archive or after a dismissal)."""
    _ready(memory)
    sym = str(symbol or "").strip().upper()
    if not bip._run(memory, "SELECT symbol FROM watch_picks WHERE symbol = ?", (sym,)):
        raise ToolError(f"{sym or 'That symbol'} is not in Buddy's recommendations.")
    if action == "dismiss":
        bip._run(memory, "UPDATE watch_picks SET status = 'dismissed', archived_on = NULL WHERE symbol = ?", (sym,))
    elif action == "restore":
        bip._run(memory, "UPDATE watch_picks SET status = 'active', archived_on = NULL, last_active = ? WHERE symbol = ?",
                 (local_now().date().isoformat(), sym))
    else:
        raise ToolError("Unknown action.")
    return view(memory)
