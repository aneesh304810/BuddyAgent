"""The Practice tab of the Investments page: Buddy's paper trades (what was bought and sold, and why) beside the
day's activity across markets, and how crypto and the other markets have been moving together.

  the practice book   from simbook.py: the book's value, open positions marked to the latest close, closed trades
                      with the reasons each was opened on and why it was closed, skipped signals, a day-by-day
                      log, and what the learning has made of them (the reason weights, the trained model's state)
  across markets      the day, week and month moves of stock indexes, sectors, commodity funds, bond funds and
                      the main cryptocurrencies, from daily prices
  moving together     for bitcoin and ether against stocks, tech stocks, gold, oil and long bonds: how closely
                      their daily moves have matched over about 30 and 90 trading days (correlation, -1 to 1),
                      and whether one side's move tended to show up in the other the next day

Correlation says two things moved together, not that one caused the other; the reading says so. Paper trades are
US stocks and funds only (the signals come from tickers people discuss): crypto appears in the market activity
and the links, not in the trades. Market prices are public data; nothing here is personal. Cached for 30 minutes.
"""

import json
import logging
import math
import time
from datetime import date

from .memory import Memory

log = logging.getLogger(__name__)

CACHE_KEY, CACHE_SECONDS = "crossmarket:cache", 1800
COINS = {"BTC/USD": "bitcoin", "ETH/USD": "ether", "SOL/USD": "solana"}
AGAINST = {"SPY": "US stocks (S&P 500)", "QQQ": "tech stocks (Nasdaq 100)", "GLD": "gold", "USO": "oil",
           "TLT": "long-term Treasury bonds"}
WINDOWS = (30, 90)


def _change(bars: list[dict], n: int) -> float | None:
    return round(bars[-1]["c"] / bars[-1 - n]["c"] - 1, 4) if len(bars) > n and bars[-1 - n]["c"] else None


def _moves(bars: list[dict]) -> dict | None:
    if len(bars) < 2:
        return None
    return {"price": bars[-1]["c"], "day": _change(bars, 1), "week": _change(bars, 5), "month": _change(bars, 21),
            "as_of": str(bars[-1]["d"])}


def correlation(a: list[float], b: list[float]) -> float | None:
    """Pearson correlation of two equal-length lists; None with fewer than 15 pairs or no movement."""
    n = min(len(a), len(b))
    if n < 15:
        return None
    a, b = a[-n:], b[-n:]
    ma, mb = sum(a) / n, sum(b) / n
    va, vb = sum((x - ma) ** 2 for x in a), sum((y - mb) ** 2 for y in b)
    if va <= 0 or vb <= 0:
        return None
    return round(sum((x - ma) * (y - mb) for x, y in zip(a, b)) / math.sqrt(va * vb), 2)


def _returns(bars: list[dict], days: list[str]) -> list[float]:
    """Close-to-close moves over the given days (days both markets traded), so the pairs line up."""
    close = {str(b["d"]): b["c"] for b in bars}
    return [close[d] / close[p] - 1 for p, d in zip(days, days[1:]) if close.get(p)]


def strength(r: float | None) -> str:
    if r is None:
        return "not enough days to say"
    size = abs(r)
    word = "barely" if size < 0.2 else "loosely" if size < 0.4 else "fairly closely" if size < 0.7 else "closely"
    if size < 0.2:
        return "barely linked"
    return f"{word} together" if r > 0 else f"{word} in opposite directions"


def link(coin: list[dict], other: list[dict]) -> dict:
    """How one coin and one market have moved together: same-day, and each side against the other's next day."""
    days = sorted({str(b["d"]) for b in coin} & {str(b["d"]) for b in other})
    out = {}
    for w in WINDOWS:
        recent = days[-(w + 1):]
        a, b = _returns(coin, recent), _returns(other, recent)
        out[f"same_day_{w}"] = correlation(a, b)
    recent = days[-(WINDOWS[-1] + 1):]
    a, b = _returns(coin, recent), _returns(other, recent)
    out["coin_then_market_next_day"] = correlation(a[:-1], b[1:])
    out["market_then_coin_next_day"] = correlation(b[:-1], a[1:])
    out["days"] = len(a)
    return out


def reading(coin: str, other: str, k: dict) -> str:
    now, longer = k.get(f"same_day_{WINDOWS[0]}"), k.get(f"same_day_{WINDOWS[-1]}")
    if now is None:
        return f"Not enough shared trading days yet to compare {coin} with {other}."
    text = f"Over the last {WINDOWS[0]} trading days {coin} and {other} moved {strength(now)} ({now:+.2f})"
    if longer is not None and abs(now - longer) >= 0.25:
        text += f", {'more' if abs(now) > abs(longer) else 'less'} than over {WINDOWS[-1]} days ({longer:+.2f})"
    lead, follow = k.get("coin_then_market_next_day"), k.get("market_then_coin_next_day")
    best = max(((abs(lead or 0), f"{coin}'s move tended to show up in {other} the next day ({lead:+.2f})" if lead else ""),
                (abs(follow or 0), f"{other}'s move tended to show up in {coin} the next day ({follow:+.2f})" if follow else "")))
    text += f". {best[1][0].upper() + best[1][1:]}, a pattern in past days, not a rule." if best[0] >= 0.25 else \
        ". Neither side's move told much about the other's next day."
    return text


def today_line(coins: dict, others: dict) -> str:
    """One plain sentence on how crypto and stocks moved on the latest day."""
    btc, spy = (coins.get("bitcoin") or {}).get("day"), (others.get("S&P 500") or {}).get("day")
    if btc is None or spy is None:
        return ""
    same = (btc > 0) == (spy > 0)
    return (f"On the latest day bitcoin moved {btc:+.1%} and US stocks {spy:+.1%}: "
            + ("the same direction" + (", with crypto moving much more" if abs(btc) > 3 * abs(spy) and abs(btc) > 0.01
                                       else "") if same else "opposite directions") + ".")


def markets(memory: Memory, data=None, fresh: bool = False) -> dict:
    """Today's activity by kind of market, and how crypto has moved with the others. Cached 30 minutes."""
    saved = json.loads(memory.get(CACHE_KEY) or "{}")
    if not fresh and data is None and saved and time.time() - saved.get("at", 0) < CACHE_SECONDS:
        return saved["value"]
    from . import market_school as ms  # noqa: PLC0415
    from .markets import Data  # noqa: PLC0415
    data = data or Data(memory)
    groups = {"Stock indexes": ms.INDEXES, "Sectors": ms.SECTORS, "Commodities (funds)": ms.COMMODITIES,
              "Bonds (funds)": ms.BONDS}
    symbols = list(dict.fromkeys(s for g in groups.values() for s in g))
    us, crypto, problems = {}, {}, []
    try:
        us = data.bars("us", symbols, days=200)
    except Exception as e:
        log.info("crossmarket us: %s", type(e).__name__)
        problems.append("stock and fund prices couldn't be read")
    try:
        crypto = data.bars("crypto", list(COINS), days=200)
    except Exception as e:
        log.info("crossmarket crypto: %s", type(e).__name__)
        problems.append("crypto prices couldn't be read")
    out = {"groups": [], "links": [], "problems": problems}
    flat = {}
    for title, names in groups.items():
        rows = [{"symbol": s, "name": n, **m} for s, n in names.items() if (m := _moves(us.get(s) or []))]
        rows.sort(key=lambda r: -(r["day"] or 0))
        flat.update({r["name"]: r for r in rows})
        if rows:
            out["groups"].append({"title": title, "rows": rows})
    coins = [{"symbol": s.split("/")[0], "name": n, **m} for s, n in COINS.items() if (m := _moves(crypto.get(s) or []))]
    if coins:
        out["groups"].append({"title": "Crypto", "rows": coins})
    for sym, coin in list(COINS.items())[:2]:
        for other, name in AGAINST.items():
            if crypto.get(sym) and us.get(other):
                k = link(crypto[sym], us[other])
                out["links"].append({"coin": coin, "with": name, **k, "reading": reading(coin, name, k)})
    out["today"] = today_line({c["name"]: c for c in coins}, flat)
    out["note"] = ("Correlation runs from -1 (opposite) to 1 (together); near 0 means no link. It shows what moved "
                   "together, not what caused what, and it changes over time.")
    out["as_of"] = max([r["as_of"] for g in out["groups"] for r in g["rows"]], default="")
    if out["groups"]:
        memory.set(CACHE_KEY, json.dumps({"at": time.time(), "value": out}))
    return out


def _reasons(memory: Memory, trades: list[dict]) -> dict:
    """{(symbol, day): [reasons]} from the signals the trades were opened on."""
    from . import pricebook  # noqa: PLC0415
    keys = sorted({t["symbol"] for t in trades})
    if not keys:
        return {}
    try:
        rows = pricebook.run(memory, "SELECT key, day, action, reasons FROM recs WHERE key IN ("
                                     + ", ".join("?" * len(keys)) + ")", tuple(keys))  # nosec B608: placeholders only
    except Exception:
        return {}
    return {(r["key"], str(r["day"])): json.loads(r["reasons"] or "[]") for r in rows}


PART_WORDS = {"tone": "what people were saying", "trend": "the price trend", "insiders": "insider buying or selling",
              "analysts": "analysts' ratings"}


def _because(t: dict, reasons: dict) -> list[str]:
    got = reasons.get((t["symbol"], str(t["opened"])))
    if got:
        return got
    parts = json.loads(t.get("parts") or "{}")
    way = [f"{PART_WORDS.get(k, k)} pointed {'up' if v > 0 else 'down'}" for k, v in parts.items() if v]
    return way or ["the signal's reasons weren't kept for this one"]


def _way(v) -> str:
    return "up" if v > 0 else "down" if v < 0 else "none"


def used(t: dict) -> list[dict]:
    """What a paper trade was decided on, grouped by where it came from: Reddit, the price, company facts, and
    the Intelligence evidence (news read as good or bad, filings, awards, relationships). Each entry: source,
    what it said, and which way it pointed (up, down or none). Only what was kept with the trade is shown."""
    parts = json.loads(t.get("parts") or "{}")
    f = json.loads(t.get("features") or "{}")
    out = []
    if f.get("mentions") is not None or parts.get("tone"):
        says = []
        if f.get("mentions") is not None:
            times = f.get("times_usual")
            says.append(f"{f['mentions']:g} posts the night before" + (f", {times:g} times its usual" if times else ""))
        if f.get("tone") is not None:
            says.append(f"tone {f['tone']:+.2f} on a scale from -1 to +1")
        out.append({"source": "Reddit", "says": "; ".join(says) or "the tone of the posts",
                    "way": _way(parts.get("tone") or 0)})
    if f.get("rsi") is not None or parts.get("trend"):
        says = ([f"strength {f['rsi']:g} of 100"] if f.get("rsi") is not None else []) + (
            [f"usual daily swing {f['atr_pct']:.1%}"] if f.get("atr_pct") else [])
        out.append({"source": "Price", "says": "; ".join(says) or "the price trend",
                    "way": _way(parts.get("trend") or 0)})
    facts = [(name, parts.get(k) or 0) for k, name in (("insiders", "insider buying or selling"),
                                                      ("analysts", "analysts' ratings")) if parts.get(k)]
    if f.get("business"):
        facts.append(("the business checklist read it " + ("strong" if f["business"] > 0 else "weak"), f["business"]))
    if t.get("read"):
        facts.append((f"the price checklist read it {t['read']}", 0))
    if facts:
        ways = {_way(v) for _, v in facts if v}
        out.append({"source": "Company facts", "says": "; ".join(n for n, _ in facts),
                    "way": ways.pop() if len(ways) == 1 else "none"})
    if f.get("f_good_news_14") or f.get("f_bad_news_14"):
        good, bad = int(f.get("f_good_news_14") or 0), int(f.get("f_bad_news_14") or 0)
        out.append({"source": "News", "says": f"{good} good and {bad} bad headlines in 14 days, read by an AI",
                    "way": _way(good - bad)})
    if f.get("f_evidence_90"):
        says = [f"{int(f['f_evidence_90'])} records in 90 days across {int(f.get('f_evidence_kinds') or 0)} kinds "
                "(filings, awards, research...)"]
        if f.get("f_evidence_growth"):
            says.append(f"{f['f_evidence_growth']:+.0%} against the 90 days before")
        if f.get("f_patterns_30"):
            says.append(f"{int(f['f_patterns_30'])} evidence patterns fired in 30 days")
        if f.get("f_links_90"):
            says.append(f"named in {int(f['f_links_90'])} relationships (funding, supply, partnership)")
        out.append({"source": "Intelligence", "says": "; ".join(says), "way": _way(f.get("f_evidence_growth") or 0)})
    return out


def strategies(memory: Memory, stats: dict, model: dict | None) -> dict:
    """The strategies the practice books follow, how each has done, and what is being learned or tested."""
    w = stats.get("weights") or {}
    names = {"tone": "What people say on Reddit", "trend": "The price trend", "insiders": "Insider buying or selling",
             "analysts": "Analysts' ratings"}
    inputs = [{"name": names[k], "weight": (w.get(k) or {}).get("weight", 1.0), "trades": (w.get(k) or {}).get("trades", 0),
               "won": (w.get(k) or {}).get("won", 0)} for k in names]
    o = stats.get("overall")
    out = [{"key": "signals", "name": "Signals",
            "rule": "Each weekday Buddy scores stocks and funds on four reasons. A strong score opens a paper buy, a "
                    "weak one marks it to avoid. A trade closes at its stop, its target, an opposite signal or after "
                    "28 days.",
            "uses": ["Reddit", "Price", "Company facts", "News", "Intelligence"],
            "record": f"{o['won']} of {o['trades']} closed calls were right" if o else "No closed trades yet",
            "trades": o["trades"] if o else 0, "inputs": inputs}]
    learning = []
    moved = [i for i in inputs if i["weight"] != 1.0]
    for i in moved:
        learning.append(f"{i['name']} now counts {i['weight']:.2f} times (it was right in {i['won']} of {i['trades']} "
                        "trades it pointed the way of).")
    if not moved:
        learning.append("The four reasons still count equally: a reason is re-weighed once 10 closed trades have "
                        "followed it.")
    if model:
        if model.get("model_in_use"):
            top = ", ".join(str(n).replace("f_", "").replace("reason_", "").replace("_", " ")
                            for n, _ in ((model.get("last_run") or {}).get("strongest") or [])[:3])
            learning.append("A trained model now sets the odds on each signal" + (f"; it leans most on {top}." if top
                                                                                    else "."))
        else:
            last = model.get("last_run")
            learning.append(f"A trained model is not in use yet: {model.get('rows', 0)} of {model.get('needed', 0)} "
                            "judged signals collected" + (f"; the last attempt ({last['on']}) was {last['status']}."
                                                          if last else "."))
    testing = []
    try:
        from . import themes  # noqa: PLC0415
        events = themes._events(memory)
        card = themes.scorecard(events)
        out.append({"key": "themes", "name": "Themes", "rule": card["rule"],
                    "uses": ["Intelligence", "News"],
                    "record": card.get("says") or "", "trades": card["baskets"], "open": card["open"],
                    "finished": card["finished"], "profit": card["profit"], "market_profit": card["market_profit"],
                    "from_history": card["from_history"]})
        for r in themes.record(events):
            if r["pattern"] != "forming":
                testing.append({"name": r["title"], "rule": r["rule"], "seen": r["events"],
                                "lean": r["days_90"]["lean"], "says": r["days_90"]["says"]})
    except Exception as e:
        log.info("practice strategies: themes unavailable (%s)", type(e).__name__)
    return {"following": out, "learning": learning, "testing": testing,
            "note": "A pattern under test is only measured against the market. It starts no trades until it has 30 "
                    "finished baskets, and nothing here ever uses real money."}


def practice(memory: Memory, data=None, today: date | None = None) -> dict:
    """The practice book for the page: summary, open positions at the latest price, closed trades, skipped
    signals, a day-by-day log and what has been learned."""
    from . import pricebook, simbook  # noqa: PLC0415
    from .tools import local_now  # noqa: PLC0415
    today = today or local_now().date()
    stats = simbook.stats(memory)
    rows = pricebook.run(memory, "SELECT * FROM sim_trades ORDER BY opened DESC, id DESC LIMIT 400")
    reasons = _reasons(memory, rows)
    held = [t for t in rows if t["status"] == "open"]
    last = {}
    if held:
        try:
            from .markets import Data  # noqa: PLC0415
            bars = (data or Data(memory)).bars("us", sorted({t["symbol"] for t in held}), days=10)
            last = {s: b[-1]["c"] for s, b in bars.items() if b}
        except Exception as e:
            log.info("practice prices: %s", type(e).__name__)

    def row(t: dict) -> dict:
        out = {"symbol": t["symbol"], "category": t["category"], "kind": "buy" if t["side"] == "long" else "avoid",
               "opened": str(t["opened"]), "entry": t["entry"], "stop": t["stop"], "target": t["target"],
               "shares": t["shares"], "cost": round(t["shares"] * t["entry"], 2), "risk": t.get("risk"),
               "checklists": t.get("read") or "", "because": _because(t, reasons), "strategy": "Signals",
               "used": used(t)}
        if t["status"] == "open":
            now = last.get(t["symbol"])
            out.update({"price": now, "days_held": (today - date.fromisoformat(str(t["opened"]))).days,
                        "change": round(now / t["entry"] - 1, 4) if now else None,
                        "profit": round(t["shares"] * (now - t["entry"]), 2) if now and t["side"] == "long" else None})
        elif t["status"] == "closed":
            profit = t["shares"] * (t["exit"] - t["entry"]) if t["side"] == "long" else None
            out.update({"closed": str(t["closed"]), "exit": t["exit"], "why_closed": t["why"], "change": t["ret"],
                        "spy_change": t["spy_ret"], "won": simbook._won(t),
                        "profit": round(profit, 2) if profit is not None else None,
                        "r": round(profit / t["risk"], 2) if profit is not None and t.get("risk") else None,
                        "days_held": (date.fromisoformat(str(t["closed"])) - date.fromisoformat(str(t["opened"]))).days})
        else:
            out["why_skipped"] = t["why"]
        return out

    open_ = [row(t) for t in held]
    closed = sorted((row(t) for t in rows if t["status"] == "closed"), key=lambda r: r["closed"], reverse=True)
    skipped = [row(t) for t in rows if t["status"] == "skipped"]
    log_ = {}
    for r in open_ + closed + skipped:
        verb = "Skipped" if "why_skipped" in r else ("Bought" if r["kind"] == "buy" else "Marked to avoid")
        log_.setdefault(r["opened"], []).append(
            {"symbol": r["symbol"], "what": verb, "price": r["entry"],
             "why": r.get("why_skipped") or "; ".join(r["because"][:2])})
        if r.get("closed"):
            log_.setdefault(r["closed"], []).append(
                {"symbol": r["symbol"], "what": "Sold" if r["kind"] == "buy" else "Judged", "price": r["exit"],
                 "why": f"{r['why_closed']} ({r['change']:+.1%})"})
    money = dict(stats["money"])
    money["unrealised"] = round(sum(r["profit"] or 0 for r in open_), 2)
    money["value_now"] = round(money["equity"] + money["unrealised"], 2)
    try:
        from . import modeltrain  # noqa: PLC0415
        model = (modeltrain.status(memory).get("domains") or {}).get(modeltrain.NAMES["investing"])
    except Exception:
        model = None
    return {"money": money, "overall": stats["overall"], "buys": stats["buys"], "avoids": stats["avoids"],
            "by_category": stats["by_category"], "open": open_, "closed": closed[:60], "skipped": skipped[:20],
            "log": [{"day": d, "events": e} for d, e in sorted(log_.items(), reverse=True)][:30],
            "weights": stats["weights"], "model": model, "note": stats["note"],
            "strategies": strategies(memory, stats, model),
            "limits": "Paper trades are US stocks and funds only; crypto shows in the market activity below, not in "
                      "the trades. Prices are daily closes, so a real fill could differ. No real money moves."}


def dashboard(memory: Memory, data=None, fresh: bool = False) -> dict:
    out = {}
    for name, fn in (("practice", lambda: practice(memory, data)), ("markets", lambda: markets(memory, data, fresh))):
        try:
            out[name] = fn()
        except Exception as e:
            log.warning("practice dashboard %s failed: %s", name, type(e).__name__, exc_info=True)
            out[name] = {"error": "This part couldn't be read just now."}
    return json.loads(json.dumps(out, default=str))
