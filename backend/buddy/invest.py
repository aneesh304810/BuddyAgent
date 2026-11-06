"""The Investments tab (web/invest.html): trade strategy, market trends, the Plaid investment accounts and the watchlist.

Everything here is read-only. The strategy is markets.py's ideas being watched and the trades the user told Buddy
about; trends are the News topics about markets, crypto and Indian stocks (trends.py); accounts are the brokerage
and retirement balances Plaid reports, plus the money moved into them. Buddy never trades: ideas, not advice.
"""

import json
import logging
import re
from datetime import date, timedelta

from .memory import Memory

log = logging.getLogger(__name__)

MARKET_KINDS = ("markets", "crypto", "india")
INVEST_WORDS = re.compile(r"invest|brokerage|retire|401k|\bira\b|roth|fidelity|vanguard|schwab|robinhood|etrade|"
                          r"e\*trade|merrill|wealthfront|betterment|acorns|webull|interactive brokers|coinbase|"
                          r"\bstash\b|public\.com|m1 finance|sofi invest", re.I)


def accounts(memory: Memory) -> dict:
    """The investment accounts Plaid knows (balances as last reported) and the money moved into investments."""
    out = {"accounts": [], "total": 0.0, "moved_in": [], "moved_in_year": 0.0, "connected": False,
           "note": "Balances only: the holdings inside each account need Plaid's Investments product, which isn't on."}
    try:
        from .plaid import Plaid
        overview = Plaid(memory, env={}).accounts_overview()
        out["connected"] = overview.get("accounts", 0) > 0
        out["accounts"] = overview.get("groups", {}).get("investments", [])
        out["total"] = overview.get("totals", {}).get("investments", 0.0)
    except Exception:
        log.info("investments: no Plaid accounts", exc_info=True)
    try:
        since = (date.today() - timedelta(days=365)).isoformat()
        rows = memory.db.execute("SELECT day, amount, currency, name, recipient, account FROM fin_transfers "
                                 "WHERE kind = 'own' AND day >= ? ORDER BY day DESC", (since,)).fetchall()
        generic = "Savings and investments"  # Plaid's label for any own transfer: not proof it's investing
        moved = [dict(r) for r in rows
                 if INVEST_WORDS.search(f"{r['name']} {'' if r['recipient'] == generic else r['recipient']}")]
        out["moved_in"] = moved[:15]
        year = str(date.today().year)
        out["moved_in_year"] = round(sum(m["amount"] for m in moved if m["day"].startswith(year)
                                         and m["currency"] == "USD"), 2)
    except Exception:  # no Plaid tables yet
        pass
    return out


def trends(memory: Memory) -> dict:
    """The News topics about markets, crypto and Indian stocks, with their latest stories."""
    try:
        from .trends import Trends
        feed = Trends(memory).feed()
    except Exception:
        log.info("investments: trends unavailable", exc_info=True)
        return {"feed": [], "topics": []}
    topics = [t for t in feed["topics"] if t["kind"] in MARKET_KINDS]
    names = {t["name"] for t in topics}
    return {"topics": topics, "feed": [f for f in feed["feed"] if f.get("topic") in names]}


def watchlist(memory: Memory, markets=None, prices: bool = True) -> list[dict]:
    """The watchlist, each with its latest price where a source has one."""
    from .markets import Markets
    mk = markets or Markets(memory)
    items = mk.watchlist()
    if prices and items:
        for m in {w["market"] for w in items}:
            try:
                got = mk.data.latest(m, [w["symbol"] for w in items if w["market"] == m])
            except Exception:
                got = {}
            for w in items:
                if w["market"] == m and w["symbol"] in got:
                    w["price"] = got[w["symbol"]]
    return items


def dashboard(memory: Memory, markets=None, prices: bool = True) -> dict:
    from .markets import Markets
    mk = markets or Markets(memory)
    strategy = mk.dashboard()
    try:
        settings = mk.settings()
    except Exception:
        settings = {}
    return json.loads(json.dumps({
        "strategy": {k: strategy[k] for k in ("setups", "positions", "last_scan", "note")},
        "settings": {"markets": settings.get("markets", []), "risk_pct": settings.get("risk_pct")},
        "watchlist": watchlist(memory, mk, prices),
        "trends": trends(memory),
        "accounts": accounts(memory),
    }, default=str))
