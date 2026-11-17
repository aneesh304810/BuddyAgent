"""Strategy repository (part of Buddy Intelligence): every investment strategy Buddy has collected, in one
catalogue, with where it came from, what it needs, how it tested, and whether it passed.

  Discover   arXiv's quantitative-finance papers (researchdesk.py), public GitHub files with a stated licence,
             documents the owner uploads, and a public directory of strategy papers (CC0) read here.
  Collect    only where a licence allows it; the source and its link stay with the rule. Published returns are
             never copied: a strategy is worth what Buddy's own test of it says.
  Extract    each strategy becomes a rule in the fixed form (stratrules.py): universe, entry, exit, holding
             period, needed data. No downloaded code is ever run.
  Validate   the rule is replayed over past daily prices by Buddy's own replay (stratlab.replay).
  Evaluate   the scorecard below: yearly return, Sharpe ratio, worst fall, trades a year, costs, and the result
             against the market, in rising and falling markets and in the latest stretch.
             A rule also has to clear the luck test: the more rules are tried, the better the best of them
             looks by chance alone, so its Sharpe ratio is judged against what the luckiest of that many rules
             would show (the deflated Sharpe ratio of Bailey and Lopez de Prado).
  Learn      rules that the owner approves paper-trade every weekday; a rule is "proven live" only on its own
             live trades.

What the scorecard is, and is not. Every trade is a fixed pretend stake, so the "book" a rule needs is its
stake times the most trades it ever held at once. Yearly return, Sharpe and worst fall are worked out on that
book from the trades' closing dates. That is a fair way to compare rules with each other; it is not what a real
account would have earned. The replay uses today's list of companies, so companies that failed are missing and
the past looks better than it was. Sizing is one fixed stake per trade; no rule sizes positions differently.
"""

import logging
import math
import re
from datetime import date
from statistics import NormalDist

from .memory import Memory
from .tools import local_now

log = logging.getLogger("buddy.stratrepo")

FAMILIES = [  # key, name, what Buddy learns from it, the research it goes back to, the rules that belong to it
    ("dual_momentum", "Dual momentum", "Relative and absolute momentum", "Antonacci", ("u_dual_momentum",)),
    ("high_52", "52-week high", "Price strength and trend continuation", "George and Hwang",
     ("u_52_week_high", "u_volume_momentum", "breakout")),
    ("sma_10", "10-month average and trend", "Tactical market timing", "Faber",
     ("u_faber_10_month_average", "u_golden_cross", "u_macd_trend", "trend")),
    ("momentum", "Momentum", "Winners keep winning for some months", "Jegadeesh and Titman",
     ("momentum", "u_market_state_momentum")),
    ("quality", "Quality", "Profitability, stability and growth", "Hou, Mo, Xue and Zhang; Novy-Marx",
     ("u_profitable_and_growing",)),
    ("value", "Value", "Cheap stocks against their fundamentals", "Fama and French", ("u_lowest_pe",)),
    ("low_vol", "Low volatility", "Lower-risk stock selection", "Blitz and van Vliet", ("u_low_volatility",)),
    ("bab", "Betting against beta", "Risk-adjusted stock selection", "Frazzini and Pedersen",
     ("u_low_beta_low_lottery",)),
    ("qmj", "Quality minus junk", "High-quality against low-quality companies (the buying side only)",
     "Asness, Frazzini and Pedersen", ("u_quality_value",)),
    ("sectors", "Sector rotation", "Rotate between strong sectors", "Moskowitz and Grinblatt", ("sectors",)),
    ("pead", "Post-earnings drift", "Momentum after an earnings surprise", "Bernard and Thomas", ("u_earnings_drift",)),
    ("reversal", "Mean reversion", "A hard short-term fall inside a longer rise tends to recover", "Jegadeesh; Lehmann",
     ("dip", "u_one_spread_below_average")),
    ("insiders", "Insider buying", "Company insiders buying their own shares", "Lakonishok and Lee", ("insiders",)),
]
BY_FIELD = (  # for a rule nobody has filed: the first field it reads that names a family
    ("earnings_surprise", "pead"), ("pe", "value"), ("pb", "value"), ("roe", "quality"), ("gross_margin", "quality"),
    ("eps_growth", "quality"), ("beta", "bab"), ("vol_63", "low_vol"), ("lottery", "bab"), ("rsi", "reversal"),
    ("zscore_30", "reversal"), ("below_high", "high_52"), ("high_252", "high_52"), ("ret_126", "momentum"),
    ("ret_252", "momentum"), ("ret_63", "momentum"), ("above_200", "sma_10"), ("avg50_above_200", "sma_10"),
    ("macd_hist", "sma_10"))
LIVE_MIN = 10   # live closed trades before a rule's live record is compared with its test
MIN_TRADES, WORST_FALL = 30, 0.35  # a rule passes only with this many past trades and no deeper fall than this
NOT_LUCK = 0.95  # how sure the luck test must be that a rule's Sharpe ratio is real, given how many were tried
PAPER_LIST = "https://github.com/WallStreetPete/awesome-open-source-trading/blob/main/docs/papers.md"
PAPER_SECTIONS = ("earnings events", "analyst revisions", "momentum", "value & quality", "fundamental composites",
                  "factor timing")  # daily data is enough; intraday, options, crypto and AI-agent sections are left out
REFERENCES = [  # looked at for method only; nothing from them is run
    {"title": "Machine Learning for Trading (Stefan Jansen)", "licence": "MIT for the code; its bundled market data has its own terms and is not used",
     "use": "Strategy notebooks are read into rules from the research queue", "url": "https://github.com/stefan-jansen/machine-learning-for-trading"},
    {"title": "Awesome Open-Source Trading", "licence": "CC0 (public domain)",
     "use": "Its list of strategy papers is read to find papers worth testing", "url": "https://github.com/WallStreetPete/awesome-open-source-trading"},
    {"title": "ML4T Skills", "licence": "Apache-2.0", "use": "Method reference for honest testing (no look-ahead, walk-forward). Not run",
     "url": "https://github.com/ml4t/skills"},
    {"title": "QuantConnect LEAN", "licence": "Apache-2.0", "use": "Not run: Buddy replays rules with its own code, so no outside engine is needed",
     "url": "https://github.com/QuantConnect/Lean"},
    {"title": "vectorbt", "licence": "Apache-2.0 with Commons Clause (limits selling services built on it)",
     "use": "Not used, because of that licence limit", "url": "https://github.com/polakowo/vectorbt"},
]
POLICY = [
    "Daily prices only: a strategy that needs intraday or options data is not collected.",
    "Nothing is copied from a source without a stated licence, and the source's link stays with the rule.",
    "Downloaded code is never run. A strategy is rewritten as a rule in Buddy's fixed form and replayed by Buddy's own code.",
    "A published return is never trusted or shown: only Buddy's own replay and live paper trades count.",
    "Every result has the cost of buying and selling taken off.",
    "The more rules are tried, the luckier the best one looks. A rule passes only if its result is unlikely to be "
    "luck after allowing for how many rules were tested.",
    "Known gap: the replay uses today's list of companies, not the list as it was each year, so the past looks "
    "better than it was. Past S&P 500 membership is not available from a free, licensed source yet.",
]


def _run(memory: Memory, sql: str, params: tuple = ()) -> list[dict]:
    from . import pricebook  # noqa: PLC0415
    return pricebook.run(memory, sql, params)


def family_of(key: str, rule: dict | None = None) -> str:
    for fam, _, _, _, keys in FAMILIES:
        if key in keys:
            return fam
    if rule:
        fields = [c["field"] for c in (rule.get("buy") or [])] + ([rule["rank"]["field"]] if rule.get("rank") else [])
        for field, fam in BY_FIELD:
            if field in fields:
                return fam
    return "other"


def scorecard(rows: list[dict], stake: float, cost: float) -> dict:
    """The figures for one rule from its closed trades ({"opened", "closed", "ret", "spy_ret"}). See the module
    note for what the book is. Returns {} when there is nothing to score."""
    rows = sorted((r for r in rows if r.get("closed") and r.get("opened") and r.get("ret") is not None),
                  key=lambda r: str(r["closed"]))
    if not rows:
        return {}
    day = lambda v: date.fromisoformat(str(v)[:10])  # noqa: E731
    events = sorted([(day(r["opened"]), 1) for r in rows] + [(day(r["closed"]), -1) for r in rows], key=lambda e: (e[0], e[1]))
    held = most = 0
    for _, step in events:
        held += step
        most = max(most, held)
    book = stake * max(1, most)
    first, last = min(day(r["opened"]) for r in rows), day(rows[-1]["closed"])
    years = max((last - first).days / 365.25, 1 / 12)
    profit = sum(stake * (r["ret"] - cost) for r in rows)
    value, peak, worst = book, book, 0.0
    months: dict[str, float] = {}
    for r in rows:
        gain = stake * (r["ret"] - cost)
        value += gain
        peak = max(peak, value)
        worst = max(worst, (peak - value) / peak if peak > 0 else 0.0)
        months[str(r["closed"])[:7]] = months.get(str(r["closed"])[:7], 0.0) + gain / book
    span = (last.year - first.year) * 12 + last.month - first.month + 1
    monthly = list(months.values()) + [0.0] * max(0, span - len(months))  # a month with no closed trade earned nothing
    mean = sum(monthly) / len(monthly)
    spread = math.sqrt(sum((m - mean) ** 2 for m in monthly) / (len(monthly) - 1)) if len(monthly) > 1 else 0.0
    end = max(book + profit, 0.0)
    shape = {}
    if spread > 0 and len(monthly) >= 12:  # what the luck test needs: the monthly ratio and the shape of the months
        n = len(monthly)
        shape = {"monthly_sharpe": mean / spread, "months": n,
                 "skew": sum((m - mean) ** 3 for m in monthly) / n / spread ** 3 * ((n - 1) / n) ** 1.5,
                 "kurtosis": sum((m - mean) ** 4 for m in monthly) / n / spread ** 4 * ((n - 1) / n) ** 2}
    against = [r["ret"] - cost - r["spy_ret"] for r in rows if r.get("spy_ret") is not None]
    return {"trades": len(rows), "won": sum(1 for r in rows if r["ret"] - cost > 0), "years": round(years, 1),
            "book": round(book), "yearly_return": round((end / book) ** (1 / years) - 1, 4) if end > 0 else -1.0,
            "sharpe": round(mean / spread * math.sqrt(12), 2) if spread > 0 else None,
            "worst_fall": round(worst, 4), "trades_a_year": round(len(rows) / years, 1),
            "days_held": round(sum((day(r["closed"]) - day(r["opened"])).days for r in rows) / len(rows), 1),
            "cost_paid": round(stake * cost * len(rows), 2),
            "average_vs_market": round(sum(against) / len(against), 4) if against else None,
            "from": first.isoformat(), "to": last.isoformat(), **shape}


def not_luck(history: dict, tried: list[float]) -> float | None:
    """How likely it is that a rule's Sharpe ratio is real and not the best of many tries (0 to 1). tried: the
    monthly Sharpe ratio of every rule that was tested. None when there is too little to say."""
    if not history.get("months") or len(tried) < 2:
        return None
    mean = sum(tried) / len(tried)
    var = sum((t - mean) ** 2 for t in tried) / (len(tried) - 1)
    norm, n, euler = NormalDist(), len(tried), 0.5772156649
    by_luck = math.sqrt(var) * ((1 - euler) * norm.inv_cdf(1 - 1 / n) + euler * norm.inv_cdf(1 - 1 / (n * math.e)))
    sr = history["monthly_sharpe"]
    wobble = 1 - history["skew"] * sr + (history["kurtosis"] - 1) / 4 * sr * sr
    if wobble <= 0:
        return None
    return round(norm.cdf((sr - by_luck) * math.sqrt(history["months"] - 1) / math.sqrt(wobble)), 3)


def gate(history: dict, splits: dict, luck: float | None = None, tried: int = 0) -> tuple[str, list[str]]:
    """Did the rule pass on past prices? ("passed" | "failed" | "not enough history", the reasons)."""
    if not history or history.get("trades", 0) < MIN_TRADES:
        return "not enough history", [f"{(history or {}).get('trades', 0)} of {MIN_TRADES} past trades; run the replay"]
    checks = [
        ((history.get("average_vs_market") or 0) > 0, "beat the market after costs", "did not beat the market after costs"),
        ((splits.get("latest") or {}).get("average_vs_market") is not None and splits["latest"]["average_vs_market"] > 0,
         "also beat it in the latest stretch, which it was not chosen on", "did not beat it in the latest stretch"),
        (history.get("worst_fall", 1) <= WORST_FALL, f"worst fall {history.get('worst_fall', 0):.0%}",
         f"worst fall {history.get('worst_fall', 0):.0%}, deeper than {WORST_FALL:.0%}"),
        ((history.get("sharpe") or 0) > 0, f"Sharpe ratio {history.get('sharpe')}", "its return did not pay for its swings"),
    ]
    if luck is not None:
        checks.append((luck >= NOT_LUCK, f"{luck:.0%} sure it is not luck, allowing for the {tried} rules tried",
                       f"only {luck:.0%} sure it is not luck, allowing for the {tried} rules tried ({NOT_LUCK:.0%} needed)"))
    return ("passed" if all(ok for ok, _, _ in checks) else "failed"), [good if ok else bad for ok, good, bad in checks]


def watch(history: dict, live: dict, status: str) -> dict:
    """Is a rule doing live what it did in its test? {"state", "says"}. A rule is only judged once it has LIVE_MIN
    live closed trades; before that the answer is "too early", never a guess."""
    n = int(live.get("trades") or 0)
    if status != "active":
        return {"state": "not trading", "says": "Not paper trading: " + ("waiting for your approval" if status == "proposed" else status)}
    if n < LIVE_MIN:
        return {"state": "too early", "says": f"{n} of {LIVE_MIN} live closed trades: too early to compare with its test"}
    now, then = live.get("average_vs_market"), history.get("average_vs_market")
    if now is None:
        return {"state": "too early", "says": "Its live trades have no market comparison yet"}
    if now <= 0:
        return {"state": "failing", "says": f"Losing to the market live ({now:+.1%} a trade over {n} trades)"
                + (f"; it beat it by {then:+.1%} on past prices" if then is not None else "")}
    if then is not None and then > 0 and now < then * 0.5:
        return {"state": "weaker", "says": f"Ahead of the market live ({now:+.1%} a trade) but under half its test ({then:+.1%})"}
    return {"state": "holding", "says": f"Holding up live: {now:+.1%} a trade against the market over {n} trades"
            + (f" (its test: {then:+.1%})" if then is not None else "")}


def view(memory: Memory) -> dict:
    """The catalogue for the Intelligence page: every rule by family with its source, scorecards and stage."""
    from . import stratlab, stratrules  # noqa: PLC0415
    stratlab._ready(memory)
    closed = _run(memory, "SELECT strategy, backfilled, ret, spy_ret, CAST(opened AS VARCHAR) AS opened, "
                          "CAST(closed AS VARCHAR) AS closed, regime FROM lab_trades WHERE status = 'closed'")
    try:
        added = {r["key"]: r for r in stratrules.rules(memory)}
    except Exception:
        log.info("stratrepo: added rules unavailable", exc_info=True)
        added = {}
    entries = [{"key": k, "name": n, "rule": words, "needs": reads, "hold_days": hold, "status": "active", "custom": None,
                "source": "Built into Buddy (a widely used rule)", "source_url": "", "licence": "common knowledge", "live_only": []}
               for k, (n, words, reads, hold) in stratlab.STRATEGIES.items()]
    entries += [{"key": k, "name": r["name"], "rule": r["in_words"], "needs": ", ".join(r["needs"]) or "Daily prices",
                 "hold_days": r["rule"]["hold_days"], "status": r["status"], "custom": r["rule"], "source": r["source"] or "Your document",
                 "source_url": r["source_url"], "licence": r["licence"], "live_only": r["live_only"]} for k, r in added.items()]
    out = []
    cards = {e["key"]: (scorecard([r for r in closed if r["strategy"] == e["key"] and r["backfilled"]], stratlab.STAKE, stratlab.COST),
                        scorecard([r for r in closed if r["strategy"] == e["key"] and not r["backfilled"]], stratlab.STAKE, stratlab.COST))
             for e in entries}
    tried = [h["monthly_sharpe"] for h, _ in cards.values() if h.get("months")]
    for e in entries:
        history, now = cards[e["key"]]
        history, now = dict(history), dict(now)
        splits = stratlab._splits([r for r in closed if r["strategy"] == e["key"] and r["backfilled"]])
        luck = not_luck(history, tried)
        verdict, reasons = gate(history, splits, luck, len(tried))
        for card in (history, now):
            for k in ("monthly_sharpe", "months", "skew", "kurtosis"):
                card.pop(k, None)
        history["not_luck"] = luck
        if e["live_only"] and verdict == "not enough history":
            reasons = ["it reads company figures that have no history, so it can only be judged on live trades"]
        proven = now.get("trades", 0) >= MIN_TRADES and (now.get("average_vs_market") or 0) > 0
        stage = ("Proven live" if proven else "Collected: waiting for your approval" if e["status"] == "proposed"
                 else "Paused" if e["status"] == "paused" else "Failed on past prices" if verdict == "failed"
                 else "Passed on past prices; paper trading" if verdict == "passed" else "Paper trading; not yet tested on past prices")
        out.append({**{k: v for k, v in e.items() if k != "custom"}, "family": family_of(e["key"], e["custom"]),
                    "stage": stage, "verdict": verdict, "reasons": reasons, "history": history, "live": now,
                    "watch": watch(history, now, e["status"]),
                    "rising_market": (splits.get("rising_market") or {}).get("average_vs_market"),
                    "falling_market": (splits.get("falling_market") or {}).get("average_vs_market"),
                    "latest": (splits.get("latest") or {}).get("average_vs_market")})
    order = {"Proven live": 0, "Passed on past prices; paper trading": 1}
    out.sort(key=lambda s: (order.get(s["stage"], 2), -(s["history"].get("average_vs_market") or -9)))
    for place, s in enumerate(out, 1):   # the ranking: proven first, then passed, then by the test against the market
        s["rank"] = place
    families = [{"key": fam, "name": name, "learns": learns, "research": research,
                 "rules": sum(1 for s in out if s["family"] == fam),
                 "passed": sum(1 for s in out if s["family"] == fam and s["verdict"] == "passed")}
                for fam, name, learns, research, _ in FAMILIES]
    other = sum(1 for s in out if s["family"] == "other")
    if other:
        families.append({"key": "other", "name": "Other", "learns": "Rules that fit none of the families above",
                         "research": "", "rules": other, "passed": sum(1 for s in out if s["family"] == "other" and s["verdict"] == "passed")})
    try:
        queued = _run(memory, "SELECT COUNT(*) AS n FROM research_queue WHERE status = 'new'")[0]["n"]
    except Exception:
        queued = 0
    return {"strategies": out, "families": families, "references": REFERENCES, "policy": POLICY,
            "counts": {"collected": len(out), "waiting": sum(1 for s in out if s["status"] == "proposed"),
                       "trading": sum(1 for s in out if s["status"] == "active"),
                       "passed": sum(1 for s in out if s["verdict"] == "passed"),
                       "failed": sum(1 for s in out if s["verdict"] == "failed"),
                       "proven": sum(1 for s in out if s["stage"] == "Proven live"), "queued": queued,
                       "failing_live": sum(1 for s in out if s["watch"]["state"] == "failing"),
                       "weaker_live": sum(1 for s in out if s["watch"]["state"] == "weaker")},
            "last_discovery": memory.get(DISCOVER_KEY) or "",
            "note": "Yearly return, Sharpe ratio and worst fall are worked out on the pretend money each rule needs "
                    "(its fixed stake times the most trades it held at once), after costs. They compare rules with "
                    "each other; they are not what a real account would have earned. Nothing real is bought."}


# --- discover: a public directory of strategy papers ---------------------------------------------------------
DISCOVER_KEY = "stratrepo:discovered"
ENTRY = re.compile(r"^\*\*\[(?P<title>[^\]]{6,300})\]\((?P<url>https://arxiv\.org/(?:abs|pdf|html)/(?P<id>\d{4}\.\d{4,5})(?:v\d+)?)\)\*\*"
                   r"\s*(?:\((?P<year>\d{4})\))?")


def parse_papers(text: str) -> list[dict]:
    """The arXiv papers in the directory's sections that daily data can test: [{"title", "url", "day", "summary"}].
    The directory's own notes are kept as the summary; its reported returns are not."""
    out, section, last = [], "", None
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("## "):
            section, last = line[3:].lower(), None
            continue
        m = ENTRY.match(line)
        if m:
            last = None
            if any(section.startswith(s) for s in PAPER_SECTIONS):
                last = {"title": " ".join(m["title"].split()), "url": f"https://arxiv.org/abs/{m['id']}",
                        "day": f"{m['year']}-01-01" if m["year"] else "", "summary": f"Listed under: {section}."}
                out.append(last)
        elif last is not None and line.startswith(("🔧", "⚠")):
            last["summary"] = (last["summary"] + " " + re.sub(r"^\W+", "", line))[:1400]
    return out


def discover(memory: Memory, fetch=None) -> dict:
    """Read the public paper directory (CC0) and queue its testable arXiv papers. Nothing is read into a rule or
    traded until the owner picks a paper from the research queue. Returns {"found", "added"}."""
    from . import researchdesk  # noqa: PLC0415
    researchdesk._ready(memory)
    _, data = researchdesk._github_file(memory, PAPER_LIST, fetch)
    found = parse_papers(data.decode("utf-8", "replace"))
    added = sum(1 for p in found if researchdesk._keep(memory, "arXiv", p["title"], p["url"], p["day"], p["summary"],
                                                      "arXiv; listed in a CC0 directory"))
    memory.set(DISCOVER_KEY, f"{local_now().date().isoformat()}: {len(found)} papers in the list, {added} new")
    return {"found": len(found), "added": added}
