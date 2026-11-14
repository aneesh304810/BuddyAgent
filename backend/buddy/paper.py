"""A paper portfolio: pretend money, real prices, so the owner can test ideas before risking anything.

Starts with $100,000 of pretend cash. 'Buy' and 'sell' record a pretend trade at the latest price Buddy can get
(Finnhub's quote, else Alpaca's latest, else the last daily close). The portfolio shows each position's gain, the
cash, the total return, and the same money put into the S&P 500 fund (SPY) on the day of the first trade, which
is the honest comparison. No costs, taxes or slippage are taken off, so real results would be a little worse.
Nothing here is a real order: Buddy never trades. It is the owner's own record and stays on their server.
"""

import json
import logging
from datetime import datetime, timezone

from .memory import Memory
from .tools import ToolError

log = logging.getLogger(__name__)

START = 100_000.0
SPY_KEY = "paper:spy_start"
SCHEMA = """
CREATE TABLE IF NOT EXISTS paper_trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, symbol TEXT NOT NULL, side TEXT NOT NULL,
    shares REAL NOT NULL, price REAL NOT NULL, note TEXT DEFAULT ''
);
"""


def _ready(memory: Memory) -> None:
    memory.db.executescript(SCHEMA)


def price_of(memory: Memory, symbol: str) -> float:
    """The latest price Buddy can get for a US symbol."""
    from . import finnhub  # noqa: PLC0415
    from .markets import Data  # noqa: PLC0415
    try:
        if finnhub.configured():
            q = finnhub.quote(memory, symbol)
            if q:
                return q["price"]
    except Exception:
        log.info("paper: finnhub quote failed", exc_info=True)
    data = Data(memory)
    got = data.latest("us", [symbol]).get(symbol)
    if got:
        return got
    bars = data.bars("us", [symbol], days=10).get(symbol) or []
    if not bars:
        raise ToolError(f"I can't get a price for {symbol}.")
    return bars[-1]["c"]


def trades(memory: Memory) -> list[dict]:
    _ready(memory)
    return [dict(r) for r in memory.db.execute("SELECT * FROM paper_trades ORDER BY id")]


def state(memory: Memory) -> dict:
    """Cash, positions (shares and average cost) and realised gain, from the trades."""
    cash, held, realised = START, {}, 0.0
    for t in trades(memory):
        p = held.setdefault(t["symbol"], {"shares": 0.0, "cost": 0.0})
        if t["side"] == "buy":
            cash -= t["shares"] * t["price"]
            p["shares"] += t["shares"]
            p["cost"] += t["shares"] * t["price"]
        else:
            avg = p["cost"] / p["shares"] if p["shares"] else 0.0
            cash += t["shares"] * t["price"]
            realised += t["shares"] * (t["price"] - avg)
            p["shares"] -= t["shares"]
            p["cost"] -= t["shares"] * avg
    return {"cash": cash, "realised": realised,
            "held": {s: p for s, p in held.items() if p["shares"] > 1e-9}}


def trade(memory: Memory, side: str, symbol: str, shares: float | None = None, amount: float | None = None,
          note: str = "", price: float | None = None, quote=None, now: datetime | None = None) -> dict:
    symbol = (symbol or "").strip().upper().lstrip("$")
    if not symbol:
        raise ToolError("Which symbol?")
    quote = quote or (lambda s: price_of(memory, s))
    price = price or quote(symbol)
    st = state(memory)
    have = st["held"].get(symbol, {}).get("shares", 0.0)
    if side == "sell" and not shares and not amount:
        shares = have  # "sell NVDA": all of it
    shares = shares if shares else (amount / price if amount else None)
    if not shares or shares <= 0:
        raise ToolError("How many shares, or how many dollars' worth?")
    if side == "buy" and shares * price > st["cash"] + 0.005:
        raise ToolError(f"That costs ${shares * price:,.2f} and the paper portfolio has ${st['cash']:,.2f} in cash.")
    if side == "sell" and shares > have + 1e-9:
        raise ToolError(f"The paper portfolio holds {have:g} shares of {symbol}." if have
                        else f"The paper portfolio doesn't hold {symbol}.")
    _ready(memory)
    if not trades(memory) and not memory.get(SPY_KEY):
        try:
            memory.set(SPY_KEY, str(quote("SPY")))
        except Exception:
            log.info("paper: no SPY price for the benchmark", exc_info=True)
    memory.db.execute("INSERT INTO paper_trades (at, symbol, side, shares, price, note) VALUES (?, ?, ?, ?, ?, ?)",
                      ((now or datetime.now(timezone.utc)).isoformat(timespec="seconds"), symbol, side,
                       round(shares, 6), price, " ".join((note or "").split())[:300]))
    memory.db.commit()
    return {"paper_trade": f"{side} {shares:g} {symbol} at ${price:,.2f}", "value": round(shares * price, 2),
            "cash_left": round(state(memory)["cash"], 2), "note": "Pretend money: no real order was placed."}


def show(memory: Memory, quote=None) -> dict:
    quote = quote or (lambda s: price_of(memory, s))
    st, all_ = state(memory), trades(memory)
    if not all_:
        return {"cash": START, "note": "The paper portfolio is empty: $100,000 of pretend cash. Say 'paper buy 10 "
                                       "MSFT' to start."}
    positions, value, missing = [], st["cash"], []
    for s, p in sorted(st["held"].items()):
        try:
            now = quote(s)
        except Exception:
            missing.append(s)
            now = p["cost"] / p["shares"]
        worth = p["shares"] * now
        value += worth
        positions.append({"symbol": s, "shares": round(p["shares"], 4), "average_cost": round(p["cost"] / p["shares"], 2),
                          "price_now": round(now, 2), "value": round(worth, 2),
                          "gain": round(worth - p["cost"], 2), "gain_pct": round(worth / p["cost"] - 1, 4)})
    out = {"since": all_[0]["at"][:10], "positions": positions, "cash": round(st["cash"], 2),
           "total": round(value, 2), "return_pct": round(value / START - 1, 4),
           "realised_gain": round(st["realised"], 2), "trades": len(all_),
           "note": "Pretend money at real prices; no costs or taxes taken off. Not a real account."}
    try:
        start = float(memory.get(SPY_KEY) or 0)
        if start:
            out["same_money_in_spy_pct"] = round(quote("SPY") / start - 1, 4)
    except Exception:
        missing.append("SPY")
    if missing:
        out["no_price_for"] = missing
    return out


def reset(memory: Memory, sure: bool = False) -> str:
    if not sure:
        raise ToolError("Resetting deletes every paper trade. Ask the owner to confirm first.")
    _ready(memory)
    memory.db.execute("DELETE FROM paper_trades")
    memory.db.commit()
    memory.set(SPY_KEY, "")
    return "The paper portfolio is back to $100,000 of pretend cash."


TOOL = {
    "name": "paper_portfolio",
    "description": (
        "A pretend portfolio for testing ideas with no real money: $100,000 of paper cash, real prices. buy / "
        "sell (symbol, shares or amount in dollars, note?): record a paper trade at the latest price; 'sell' "
        "with no size sells the whole position. show: positions with gains, cash, total return, and the same "
        "money in the S&P 500 fund since the first trade. trades: the list. reset (sure: true, only after the "
        "owner confirms): start over. Use it when the owner says 'paper buy', 'pretend', 'simulate', or wants to "
        "test a signal or thesis. Always say it is pretend money and that no real order was placed."),
    "input_schema": {"type": "object", "properties": {
        "action": {"type": "string", "enum": ["buy", "sell", "show", "trades", "reset"]},
        "symbol": {"type": "string"}, "shares": {"type": "number"}, "amount": {"type": "number"},
        "note": {"type": "string"}, "sure": {"type": "boolean"}},
        "required": ["action"], "additionalProperties": False},
}


def run_tool(memory: Memory, args: dict) -> str:
    a = args.get("action")
    if a in ("buy", "sell"):
        return json.dumps(trade(memory, a, args.get("symbol", ""), args.get("shares"), args.get("amount"),
                                args.get("note", "")))
    if a == "trades":
        return json.dumps(trades(memory))
    if a == "reset":
        return reset(memory, bool(args.get("sure")))
    return json.dumps(show(memory))
