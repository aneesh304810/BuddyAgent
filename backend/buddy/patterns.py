"""Nightly pattern learning in the sandbox: Buddy hands the price book to the sandbox, which learns price patterns
(sandbox_jobs/price_patterns.py, offline, in a throwaway container) and Buddy keeps what it learned in the price book.

  export   the price book's prices, products' kinds, launches, predictions and the sale windows, as gzipped CSV
           (market data only: products, routes, hotels, prices, dates; never anything personal)
  learn    in the sandbox workspace "price-patterns", with no network: sale discounts per kind, the odds of a 10%
           drop, price against age since launch, flight fares against days before departure, hotel weekday and
           month patterns, and how Buddy's own buy-or-wait calls turned out
  keep     output/patterns.json goes into the price book's patterns table; when_to_buy reads it and prefers what
           was learned (with how many items stand behind it) over its rules of thumb

Raw prices are kept 90 days; the patterns are kept for good. Each month's learning is saved (the latest run of
the month wins) and merge() combines the months, weighting each by how many items or prices stood behind it; a
sale's discounts are kept per sale and year, so Black Friday 2026 is still known long after its raw prices are
gone, and Black Friday 2027 adds to it. Prices older than 90 days are deleted only once a successful run has
learned from them, so nothing is thrown away unlearned (without the sandbox, raw prices simply wait).

Runs once a night (02:30, before the backup) when the sandbox is set up (deploy/sandbox.sh); without it, the
patterns simply wait, and buy-or-wait keeps using its rules of thumb.
"""

import base64
import json
import logging
import tempfile
import time
from datetime import date, datetime, timezone
from pathlib import Path

from .memory import Memory
from .tools import ToolError, local_now

log = logging.getLogger(__name__)

WORKSPACE = "price-patterns"
SCRIPT = Path(__file__).parent / "sandbox_jobs" / "price_patterns.py"
MAX_UPLOAD = 14_000_000
DAY_KEY, STATUS_KEY, LEARNED_KEY = "patterns:day", "patterns:status", "patterns:learned_on"
KEEP_RAW_DAYS = 90
MONTHS = 36  # how far back learned months still count
HOUR = 2
_cache: dict = {}


def _status(memory: Memory, text: str) -> str:
    memory.set(STATUS_KEY, json.dumps({"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "status": text}))
    return text


def export(memory: Memory, folder: Path, days: int = 730) -> dict[str, Path]:
    """The inputs for the learner, as files in `folder` (the last `days` days of prices)."""
    from . import pricebook, shopper, whentobuy  # noqa: PLC0415
    folder.mkdir(parents=True, exist_ok=True)
    files = {}
    since = (date.today().toordinal() - days)
    cut = date.fromordinal(since).isoformat()
    queries = {
        "observations": f"SELECT seen_at, domain, item, price, seller, travel_date FROM observations "
                        f"WHERE seen_at >= TIMESTAMP '{cut}'",
        "launches": "SELECT item, kind, launch_date, launch_price, successor_date FROM launches",
        "predictions": "SELECT item, kind, decision, chance, outcome FROM predictions",
        "deals": f"SELECT seen_on, store, COALESCE(NULLIF(kind, ''), 'other') AS kind, COALESCE(domain, 'product') "
                 f"AS domain, COALESCE(NULLIF(model, ''), lower(title)) AS model, price, regular_price, "
                 f"COALESCE(NULLIF(reason, ''), 'price drop') AS reason FROM deals WHERE seen_on >= DATE '{cut}'",
    }
    for name, sql in queries.items():
        target = folder / f"{name}.csv.gz"
        pricebook.run(memory, f"COPY ({sql}) TO '{target}' (HEADER, DELIMITER ',', COMPRESSION GZIP)")
        files[name] = target
    import csv  # noqa: PLC0415
    import gzip  # noqa: PLC0415
    items = pricebook.run(memory, "SELECT DISTINCT o.item, l.kind FROM observations o LEFT JOIN launches l "
                                  "ON l.item = o.item WHERE o.domain = 'product'")
    with gzip.open(folder / "items.csv.gz", "wt", newline="") as f:
        w = csv.writer(f)
        w.writerow(["item", "kind"])
        for r in items:
            w.writerow([r["item"], r["kind"] or shopper.kind_of(r["item"])])
    files["items"] = folder / "items.csv.gz"
    years = range(date.fromordinal(since).year, date.today().year + 1)
    events = [{"name": e["name"], "start": e["start"].isoformat(), "end": e["end"].isoformat(),
               "for": sorted(e["for"] & {"electronics", "home", "clothing", "other"})}
              for y in years for e in whentobuy.calendar(y)]
    (folder / "events.json").write_text(json.dumps([e for e in events if e["for"]]))
    files["events"] = folder / "events.json"
    return files


async def learn(memory: Memory, call=None) -> str:
    """Export, learn in the sandbox (offline), keep the patterns. Returns a status line."""
    from . import pricebook, sandbox  # noqa: PLC0415
    call = call or sandbox.call
    if call is sandbox.call and not sandbox.available():
        return _status(memory, "waiting: the sandbox isn't set up (deploy/sandbox.sh), so no patterns were learned")
    started = time.monotonic()
    with tempfile.TemporaryDirectory() as tmp:
        files = export(memory, Path(tmp))
        if sum(p.stat().st_size for p in files.values()) > MAX_UPLOAD:
            files = export(memory, Path(tmp), days=365)
        await call("create", workspace=WORKSPACE)
        await call("run", workspace=WORKSPACE, command="rm -rf input output && mkdir -p input output", network=False,
                   timeout=60)
        for name, path in files.items():
            data = path.read_bytes()
            if len(data) > MAX_UPLOAD:
                return _status(memory, f"skipped: the {name} file is over 14 MB even for one year")
            await call("write_b64", workspace=WORKSPACE, path=f"input/{path.name}",
                       content_b64=base64.b64encode(data).decode())
    await call("write", workspace=WORKSPACE, path="price_patterns.py", content=SCRIPT.read_text())
    ran = await call("run", workspace=WORKSPACE, command="python3 price_patterns.py .", network=False, timeout=900)
    try:
        got = await call("fetch", workspace=WORKSPACE, path="patterns.json")
        learned = json.loads(base64.b64decode(got["base64"]))
    except (sandbox.SandboxError, KeyError, ValueError) as e:
        out = str((ran or {}).get("output", ""))[-400:]
        return _status(memory, f"failed: the learner didn't produce patterns ({e}). {out}")
    keep(memory, learned)
    memory.set(LEARNED_KEY, local_now().date().isoformat())
    found = {k: len(v) for k, v in learned.items() if isinstance(v, dict)}
    return _status(memory, f"learned from {learned.get('prices', 0)} prices in {time.monotonic() - started:.0f}s: "
                           + ", ".join(f"{k} {n}" for k, n in found.items()))


def keep(memory: Memory, learned: dict, month: str = "") -> None:
    """Save a run's patterns as this month's (the latest run of the month replaces earlier ones)."""
    from . import pricebook  # noqa: PLC0415
    month = month or local_now().strftime("%Y-%m")
    pricebook.run(memory, "INSERT OR REPLACE INTO pattern_months VALUES (?, 'price', ?, ?)",
                  (month, json.dumps(learned), learned.get("fitted_at", "")))
    _cache.clear()


def _wavg(rows: list[tuple[float, int]]) -> float:
    total = sum(n for _, n in rows)
    return round(sum(v * n for v, n in rows) / total, 4) if total else 0.0


def merge(months: list[dict]) -> dict:
    """Combine the learned months into one picture, each result weighted by the items or prices behind it."""
    if not months:
        return {}
    newest = months[-1]
    out = {"fitted_at": newest.get("fitted_at"), "months": len(months),
           "prices": sum(m.get("prices", 0) for m in months), "products": max(m.get("products", 0) for m in months)}
    # sales: one result per sale and year (the run that saw the most items), then combined across years
    per_year: dict = {}
    for m in months:
        for kind, evs in (m.get("event_discounts") or {}).items():
            for key, v in evs.items():
                have = per_year.setdefault(kind, {}).get(key)
                if not have or v["items"] > have["items"]:
                    per_year[kind][key] = v
    out["event_discounts"] = {}
    for kind, evs in per_year.items():
        by_event: dict = {}
        for v in evs.values():
            by_event.setdefault(v.get("event", ""), []).append(v)
        out["event_discounts"][kind] = {
            name: {"median": _wavg([(v["median"], v["items"]) for v in vs]),
                   "p25": _wavg([(v["p25"], v["items"]) for v in vs]), "p75": _wavg([(v["p75"], v["items"]) for v in vs]),
                   "items": sum(v["items"] for v in vs), "years": sorted(v.get("year") for v in vs)}
            for name, vs in by_event.items()}

    def combine(field: str, value: str, weight: str) -> dict:
        acc: dict = {}
        for m in months:
            for a, inner in (m.get(field) or {}).items():
                for b, v in inner.items():
                    acc.setdefault(a, {}).setdefault(b, []).append((v[value], v[weight]))
        return {a: {b: {value: _wavg(vs), weight: sum(n for _, n in vs)} for b, vs in inner.items()}
                for a, inner in acc.items()}
    out["drop_odds"] = combine("drop_odds", "chance", "items")
    out["lifecycle"] = combine("lifecycle", "share_of_launch_price", "products")
    for field in ("flights", "hotels"):  # routes and places: the month with the most prices behind it
        best: dict = {}
        for m in months:
            for k, v in (m.get(field) or {}).items():
                if k not in best or v.get("prices", 0) >= best[k].get("prices", 0):
                    best[k] = v
        out[field] = best
    out["calibration"] = newest.get("calibration") or {}  # predictions are kept whole: the newest run sees them all
    # deal rhythms: an item's deal dates and prices are unioned across months, so its rhythm outlives the raw data
    items: dict = {}
    for m in months:
        for k, v in (m.get("item_deals") or {}).items():
            have = items.setdefault(k, {"dates": [], "prices": [], "depths": [], "stores": []})
            for d, p, dep in zip(v.get("dates", []), v.get("prices", []), v.get("depths", [None] * 999)):
                if d not in have["dates"]:
                    have["dates"].append(d)
                    have["prices"].append(p)
                    have["depths"].append(dep or 0)
            have["stores"] = sorted(set(have["stores"]) | set(v.get("stores", [])))
    out["item_deals"] = items
    reasons: dict = {}
    for m in months:
        for kind, shares in (m.get("deal_reasons") or {}).items():
            for r, v in shares.items():
                reasons.setdefault(kind, {}).setdefault(r, 0)
                reasons[kind][r] += v.get("deals", 0)
    out["deal_reasons"] = {k: {r: {"deals": n, "share": round(n / sum(rs.values()), 2)} for r, n in rs.items()}
                           for k, rs in reasons.items()}
    best: dict = {}
    for m in months:  # a store's rhythm: the month that saw the most of its deals
        for k, v in (m.get("store_rhythm") or {}).items():
            if k not in best or v.get("deals", 0) >= best[k].get("deals", 0):
                best[k] = v
    out["store_rhythm"] = best
    return out


def get(memory: Memory) -> dict:
    """Everything learned so far, merged across months ({} until the first run)."""
    from . import pricebook  # noqa: PLC0415
    k = pricebook.path_for(memory)
    if k in _cache and time.monotonic() - _cache[k][0] < 600:
        return _cache[k][1]
    try:
        rows = pricebook.run(memory, "SELECT data FROM pattern_months WHERE name = 'price' ORDER BY month DESC "
                                     f"LIMIT {MONTHS}")
        data = merge([json.loads(r["data"]) for r in reversed(rows)])
    except Exception:
        log.exception("patterns: reading them failed")
        data = {}
    _cache[k] = (time.monotonic(), data)
    return data


def prune_raw(memory: Memory) -> int:
    """Delete raw prices and deals older than 90 days, but only what a successful run has already learned from."""
    from datetime import timedelta  # noqa: PLC0415
    from . import pricebook  # noqa: PLC0415
    learned_on = memory.get(LEARNED_KEY)
    if not learned_on:
        return 0
    cut = min(date.fromisoformat(learned_on), date.today()) - timedelta(days=KEEP_RAW_DAYS)
    n = pricebook.run(memory, "SELECT COUNT(*) AS n FROM observations WHERE seen_at < ?", (cut.isoformat(),))[0]["n"]
    pricebook.run(memory, "DELETE FROM observations WHERE seen_at < ?", (cut.isoformat(),))
    pricebook.run(memory, "DELETE FROM deals WHERE seen_on < ?", (cut.isoformat(),))
    return int(n)


def due(memory: Memory, now: datetime | None = None) -> bool:
    now = now or local_now()
    return now.hour >= HOUR and now.hour < HOUR + 4 and memory.get(DAY_KEY) != now.date().isoformat()


def summary(memory: Memory) -> dict:
    p = get(memory)
    try:
        status = json.loads(memory.get(STATUS_KEY) or "{}")
    except ValueError:
        status = {}
    if not p:
        return {"status": status or "not learned yet", "note": "Learned every night in the sandbox once it's set up."}
    return {"status": status, "fitted_at": p.get("fitted_at"), "prices": p.get("prices"), "products": p.get("products"),
            "sale_discounts": p.get("event_discounts"), "drop_odds": p.get("drop_odds"), "lifecycle": p.get("lifecycle"),
            "flights": {r: {k: v[k] for k in ("cheapest_window", "by_days_before", "prices")}
                        for r, v in list((p.get("flights") or {}).items())[:10]},
            "hotels": dict(list((p.get("hotels") or {}).items())[:10]), "calibration": p.get("calibration")}


def raise_if_missing(memory: Memory) -> None:
    if not get(memory):
        raise ToolError("No patterns yet: they're learned in the sandbox each night once it's set up.")
