"""Market school: a short lesson every weekday morning, so the owner learns how markets work from what is
actually happening, plus the free official data that explains it.

  The lesson (weekdays from 8 AM New York time, before the US open; or when asked):
    what moved  the big indexes, the sectors, commodities (gold, silver, oil, gas, copper, farm goods) and bonds,
                yesterday and over the week, from the daily prices Buddy already reads (markets.Data)
    why         rates and inflation (FRED), oil and gas (EIA)
    one idea    a concept explained simply, picked for today (oil moved a lot: how oil funds work; the yield
                curve flipped: what that means) or the next one not yet taught
    a theme     how the themes worth learning about did this week and month (AI chips, power and the grid,
                copper, defence, reshoring, gold, energy, cash-like Treasuries...)
  -> spoken as a short message, kept in Reports. A free model writes it when one is set up (public numbers only),
     else Claude's cheap model; without either, plain words from the numbers.

  Data (only series names and ticker symbols go out, never anything personal):
    FRED  (St. Louis Fed; free key, BUDDY_FRED_KEY)   Fed funds rate, 2- and 10-year Treasury yields, CPI, jobs
    EIA   (US Energy Information Administration; free key, BUDDY_EIA_KEY)   WTI oil and Henry Hub natural gas
    SEC   (EDGAR; free, no key)   a company's yearly revenue, profit, EPS, cash and debt from its filings. SEC asks
          every app to name a contact in its requests: BUDDY_SEC_CONTACT (an email the owner picks for this).

Education, not advice: Buddy explains and shows numbers; it never trades and never tells the owner what to buy.
"""

import json
import logging
import time
import urllib.parse
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from . import gateway
from .memory import Memory
from .tools import ToolError

log = logging.getLogger(__name__)

DONE_KEY, OFF_KEY, TAUGHT_KEY = "school:done", "school:off", "school:taught"
MACRO_KEY, TICKERS_KEY = "school:macro", "school:sec_tickers"
START = (8, 0)  # New York time, weekdays

INDEXES = {"SPY": "S&P 500", "QQQ": "Nasdaq 100", "IWM": "small companies (Russell 2000)", "DIA": "Dow"}
SECTORS = {"XLK": "technology", "XLF": "financials", "XLE": "energy", "XLV": "health care", "XLI": "industrials",
           "XLY": "consumer spending", "XLP": "consumer staples", "XLU": "utilities", "XLB": "materials",
           "XLC": "communication services", "XLRE": "real estate"}
COMMODITIES = {"GLD": "gold", "SLV": "silver", "USO": "oil", "UNG": "natural gas", "CPER": "copper",
               "DBA": "farm goods"}
BONDS = {"TLT": "long-term Treasuries", "SGOV": "short-term Treasuries (cash-like)"}
THEMES = {
    "AI chips": (["SMH"], "The chipmakers behind AI. Big swings: they lead when AI excitement is high."),
    "AI power and the grid": (["XLU", "GRID"], "AI data centres need huge amounts of electricity: utilities and "
                              "grid equipment."),
    "Copper and electrification": (["CPER", "COPX"], "Copper wires the grid, EVs and data centres; supply is tight."),
    "Defence": (["ITA"], "Defence contractors: government budgets and world conflicts drive them."),
    "Reshoring and industrials": (["XLI", "PAVE"], "Factories, construction and infrastructure moving back home."),
    "Communication services": (["XLC"], "Google, Meta, Netflix and telecoms; some analysts call it cheap now."),
    "Gold as a hedge": (["GLD"], "Gold often holds up when inflation runs hot or markets get scared."),
    "Oil and energy": (["XLE", "USO"], "Oil companies and oil itself: wars, supply and demand swing them."),
    "Cash-like Treasuries": (["SGOV"], "Very short government bonds: they pay more when the Fed raises rates."),
    "Long bonds": (["TLT"], "Long government bonds: they fall when rates rise and rise when rates fall."),
}
FRED_SERIES = {"fed_funds": "DFF", "two_year": "DGS2", "ten_year": "DGS10", "cpi": "CPIAUCSL",
               "unemployment": "UNRATE"}
EIA_SERIES = {"oil": "PET.RWTC.D", "gas": "NG.RNGWHHD.D"}

# Concepts, in the order they're taught when nothing in today's numbers calls for a particular one.
CONCEPTS = [
    ("etf", "What an ETF is", "An ETF is a basket of many investments you buy as one ticker. VOO or SPY holds the 500 "
     "biggest US companies, so one purchase spreads your money across all of them. Cheap, simple, and the core of "
     "most long-term portfolios."),
    ("expense_ratio", "Expense ratios", "Every fund charges a yearly fee, the expense ratio. 0.03% costs $3 a year on "
     "$10,000; 1% costs $100, every year, compounding. Over decades the cheap fund usually wins."),
    ("diversify", "Diversification", "Spreading money across many companies, sectors and kinds of assets so one bad "
     "bet can't sink you. An S&P 500 fund is diversified across companies but still all US stocks."),
    ("rates", "Why interest rates move markets", "When the Fed raises rates, borrowing gets dearer, companies earn "
     "less and safe bonds pay more, so stocks, especially fast-growing ones, usually come under pressure. Cuts do "
     "the opposite."),
    ("inflation", "Inflation and your money", "Inflation is prices rising. At 4% a year, cash loses about 4% of its "
     "buying power each year. Stocks, gold and some commodities have historically kept up better over long periods."),
    ("yield_curve", "The yield curve", "Normally a 10-year Treasury pays more than a 2-year. When the 2-year pays "
     "more (an 'inverted' curve), markets expect trouble ahead; it has come before most US recessions, though "
     "with long and uneven delays."),
    ("rotation", "Sector rotation", "Money moves between sectors as the story changes: into tech when growth is "
     "exciting, into utilities and staples when people get nervous, into energy when oil rises."),
    ("commodity_funds", "How oil and gas funds really work", "Funds like USO and UNG hold futures contracts, not "
     "barrels. Rolling them each month can cost money ('contango'), so over months they can lag the oil price "
     "you see in the news. Good for short views, risky to hold for years."),
    ("gold", "Why people own gold", "Gold pays no interest or dividends. People hold it as insurance against "
     "inflation, a weak dollar or a crisis. GLD tracks the gold price; miners (GDX) swing much more."),
    ("pe", "The P/E ratio", "Price divided by a year's earnings per share. A P/E of 20 means you pay $20 for each $1 "
     "of yearly profit. High P/E means investors expect growth; it doesn't by itself mean expensive or cheap."),
    ("earnings", "Earnings season", "Four times a year companies report profits. Prices react to the result "
     "against expectations, not the result itself: great numbers can still fall if people hoped for more."),
    ("dca", "Dollar-cost averaging", "Investing a fixed amount on a schedule (say every month) instead of all at "
     "once. You buy more shares when prices are low and fewer when high, and you avoid trying to time the market."),
    ("rebalance", "Rebalancing", "Pick a mix (say 80% stocks, 20% bonds). As markets move the mix drifts; once or "
     "twice a year you sell a bit of what grew and buy what lagged to get back. It quietly sells high and buys low."),
    ("bonds", "Bonds and duration", "A bond is a loan that pays interest. When rates rise, existing bonds lose value; "
     "the longer the bond (its 'duration'), the bigger the drop. Short Treasuries barely move."),
    ("weighting", "Market-cap vs equal weight", "The S&P 500 gives the biggest companies the most weight, so a handful "
     "of tech giants drive it. An equal-weight version (RSP) gives each company the same slice."),
    ("volatility", "Volatility and the VIX", "Volatility is how much prices swing. The VIX measures what traders "
     "expect for the next month: around 12-15 is calm, above 30 is fear."),
    ("dividends", "Dividends", "Some companies pay part of their profit to shareholders in cash, usually quarterly. "
     "Dividend yield is the yearly payout divided by the price."),
    ("tax_accounts", "Account types", "Gains in a 401(k) or IRA grow untaxed until you withdraw (Roth: never taxed if "
     "you follow the rules). In a regular brokerage account you pay tax on dividends and on gains when you sell."),
    ("tax_loss", "Tax-loss harvesting", "In a taxable account, selling an investment at a loss can lower your tax "
     "bill; you can buy a similar (not identical) fund to stay invested. Watch the 30-day 'wash sale' rule."),
    ("stop_loss", "Stops and position size", "Traders decide before buying where they'd admit they're wrong (the "
     "stop) and size the position so hitting it costs a small, fixed share of the account, often 1%."),
    ("timeline", "Your timeline", "Money you need within about three years usually belongs in cash or short bonds, "
     "not stocks: a 30% drop right before you need it is the real risk."),
]
CONCEPT_BY_KEY = {c[0]: c for c in CONCEPTS}


# --- data -----------------------------------------------------------------------------------------------------
def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def fred(memory: Memory, series: str, limit: int = 400, env=None, fetch=None) -> list[tuple[str, float]]:
    """A FRED series, newest first: [(date, value)]. Missing days ('.') are skipped."""
    key = gateway._secret(env, "BUDDY_FRED_KEY")
    if not key:
        raise ToolError("FRED isn't set up: get a free key at fred.stlouisfed.org, then "
                        "sudo bash /opt/buddy/deploy/set.sh BUDDY_FRED_KEY")
    q = urllib.parse.urlencode({"series_id": series, "api_key": key, "file_type": "json", "sort_order": "desc",
                                "limit": limit})
    got = gateway.call(memory, "fred", "/fred/series/observations?" + q, env=env, fetch=fetch,
                       purpose="market_school") or {}
    return [(o["date"], v) for o in got.get("observations") or [] if (v := _num(o.get("value"))) is not None]


def eia(memory: Memory, series: str, days: int = 120, env=None, fetch=None) -> list[tuple[str, float]]:
    """An EIA series (the v1-style id, through API v2's seriesid route), newest first."""
    key = gateway._secret(env, "BUDDY_EIA_KEY")
    if not key:
        raise ToolError("EIA isn't set up: get a free key at eia.gov/opendata, then "
                        "sudo bash /opt/buddy/deploy/set.sh BUDDY_EIA_KEY")
    q = urllib.parse.urlencode({"api_key": key, "start": (date.today() - timedelta(days=days)).isoformat()})
    got = gateway.call(memory, "eia", f"/v2/seriesid/{series}?" + q, env=env, fetch=fetch,
                       purpose="market_school") or {}
    rows = ((got.get("response") or {}).get("data")) or []
    out = [(str(r.get("period")), v) for r in rows if (v := _num(r.get("value"))) is not None]
    return sorted(out, reverse=True)


def _ago(rows: list[tuple[str, float]], days: int) -> float | None:
    """The value about `days` before the newest one."""
    if not rows:
        return None
    cut = (date.fromisoformat(rows[0][0][:10]) - timedelta(days=days)).isoformat()
    return next((v for d, v in rows if d[:10] <= cut), None)


def macro(memory: Memory, env=None, fetch=None, fresh: bool = False) -> dict:
    """Rates, inflation, jobs, oil and gas, with their recent change; kept 6 hours. Missing keys leave gaps."""
    if not fresh:
        try:
            saved = json.loads(memory.get(MACRO_KEY) or "{}")
            if saved and time.time() - saved.get("at", 0) < 6 * 3600:
                return saved["data"]
        except (ValueError, KeyError):
            pass
    out: dict = {"missing": []}
    if gateway._secret(env, "BUDDY_FRED_KEY"):
        try:
            for name in ("fed_funds", "two_year", "ten_year"):
                rows = fred(memory, FRED_SERIES[name], 60, env, fetch)
                if rows:
                    month = _ago(rows, 30)
                    out[name] = {"value": rows[0][1], "date": rows[0][0],
                                 "month_change": round(rows[0][1] - month, 2) if month is not None else None}
            cpi = fred(memory, FRED_SERIES["cpi"], 14, env, fetch)
            if len(cpi) >= 13:
                out["inflation"] = {"value": round((cpi[0][1] / cpi[12][1] - 1) * 100, 1), "date": cpi[0][0]}
            jobs = fred(memory, FRED_SERIES["unemployment"], 2, env, fetch)
            if jobs:
                out["unemployment"] = {"value": jobs[0][1], "date": jobs[0][0]}
            if "two_year" in out and "ten_year" in out:
                out["curve"] = round(out["ten_year"]["value"] - out["two_year"]["value"], 2)
        except Exception as e:
            log.info("FRED: %s", e)
            out["missing"].append("FRED (rates and inflation) didn't answer")
    else:
        out["missing"].append("FRED (rates and inflation): sudo bash /opt/buddy/deploy/set.sh BUDDY_FRED_KEY")
    if gateway._secret(env, "BUDDY_EIA_KEY"):
        for name in ("oil", "gas"):
            try:
                rows = eia(memory, EIA_SERIES[name], 60, env, fetch)
                if rows:
                    week = _ago(rows, 7)
                    out[name] = {"value": rows[0][1], "date": rows[0][0],
                                 "week_pct": round((rows[0][1] / week - 1) * 100, 1) if week else None}
            except Exception as e:
                log.info("EIA %s: %s", name, e)
                out["missing"].append(f"EIA ({name}) didn't answer")
    else:
        out["missing"].append("EIA (oil and gas): sudo bash /opt/buddy/deploy/set.sh BUDDY_EIA_KEY")
    memory.set(MACRO_KEY, json.dumps({"at": time.time(), "data": out}))
    return out


def _sec_headers(env=None) -> dict:
    contact = gateway._secret(env, "BUDDY_SEC_CONTACT")
    if not contact:
        raise ToolError("SEC asks every app to name a contact email in its requests. Pick one for this (it goes only "
                        "to the SEC): sudo bash /opt/buddy/deploy/set.sh BUDDY_SEC_CONTACT")
    return {"User-Agent": f"Buddy personal assistant {contact}", "Accept": "application/json"}


def _cik(memory: Memory, ticker: str, env=None, fetch=None) -> tuple[str, str]:
    """(10-digit CIK, company name) for a ticker; SEC's ticker list is kept for 30 days."""
    try:
        saved = json.loads(memory.get(TICKERS_KEY) or "{}")
    except ValueError:
        saved = {}
    if not saved or time.time() - saved.get("at", 0) > 30 * 86400:
        got = gateway.call_url(memory, "sec", ["https://www.sec.gov/files/company_tickers.json"],
                               headers=_sec_headers(env), fetch=fetch, purpose="market_school") or {}
        rows = got.values() if isinstance(got, dict) else got
        saved = {"at": time.time(), "map": {str(r["ticker"]).upper(): [str(r["cik_str"]).zfill(10), r.get("title", "")]
                                            for r in rows if r.get("ticker")}}
        memory.set(TICKERS_KEY, json.dumps(saved))
    hit = saved.get("map", {}).get(ticker.upper().replace(".", "-"))
    if not hit:
        raise ToolError(f"SEC has no company filings for {ticker.upper()}. ETFs and funds don't file company "
                        "financials; ask about what the fund holds or its fees instead.")
    return hit[0], hit[1]


REVENUE = ("Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax", "SalesRevenueNet")
FACTS = {"net_income": ("NetIncomeLoss",), "eps": ("EarningsPerShareDiluted",),
         "cash": ("CashAndCashEquivalentsAtCarryingValue",), "debt": ("LongTermDebtNoncurrent", "LongTermDebt")}


def _yearly(gaap: dict, names: tuple, unit: str = "USD") -> dict[int, float]:
    """Fiscal year -> value from 10-K filings (the latest filing wins for each year)."""
    best: dict[int, tuple[str, float]] = {}
    for name in names:
        for row in ((gaap.get(name) or {}).get("units") or {}).get(unit, []):
            if row.get("form") != "10-K" or row.get("fp") != "FY" or not row.get("fy"):
                continue
            if row.get("start") and row.get("end"):  # a whole year, not a quarter repeated in the 10-K
                span = (date.fromisoformat(row["end"]) - date.fromisoformat(row["start"])).days
                if span < 300:
                    continue
            year = int(str(row.get("end", ""))[:4] or row["fy"])
            if year not in best or row.get("filed", "") > best[year][0]:
                best[year] = (row.get("filed", ""), float(row["val"]))
        if best:
            break
    return {y: v for y, (_, v) in sorted(best.items())}


def company(memory: Memory, ticker: str, env=None, fetch=None) -> dict:
    """A company's last few years from its SEC filings: revenue, profit, margin, EPS, cash and debt."""
    ticker = (ticker or "").strip().upper()
    if not ticker:
        raise ToolError("Which company? Give its ticker, like AAPL.")
    cik, name = _cik(memory, ticker, env, fetch)
    facts = gateway.call(memory, "sec", f"/api/xbrl/companyfacts/CIK{cik}.json", headers=_sec_headers(env),
                         env=env, fetch=fetch, purpose="market_school") or {}
    gaap = (facts.get("facts") or {}).get("us-gaap") or {}
    revenue = _yearly(gaap, REVENUE)
    years = sorted(revenue)[-4:]
    data = {k: _yearly(gaap, names, "USD/shares" if k == "eps" else "USD") for k, names in FACTS.items()}
    rows = []
    for y in years:
        ni = data["net_income"].get(y)
        rows.append({"year": y, "revenue": revenue[y], "net_income": ni,
                     "margin_pct": round(ni / revenue[y] * 100, 1) if ni is not None and revenue[y] else None,
                     "eps": data["eps"].get(y), "cash": data["cash"].get(y), "debt": data["debt"].get(y)})
    growth = (round((revenue[years[-1]] / revenue[years[-2]] - 1) * 100, 1)
              if len(years) >= 2 and revenue[years[-2]] else None)
    return {"ticker": ticker, "company": facts.get("entityName") or name, "years": rows,
            "revenue_growth_pct": growth, "source": "SEC EDGAR (the company's yearly 10-K filings)",
            "note": "Numbers as filed; explain what they mean in plain words. Education, not a buy or sell call."}


# --- prices ---------------------------------------------------------------------------------------------------
def _change(bars: list[dict], n: int) -> float | None:
    if len(bars) <= n or not bars[-1 - n]["c"]:
        return None
    return round((bars[-1]["c"] / bars[-1 - n]["c"] - 1) * 100, 1)


def _ytd(bars: list[dict]) -> float | None:
    year = str(date.today().year)
    first = next((b for b in bars if str(b["d"]).startswith(year)), None)
    return round((bars[-1]["c"] / first["c"] - 1) * 100, 1) if first and first["c"] else None


def moves(memory: Memory, env=None, fetch=None, data=None) -> dict:
    """Day, week, month, 3-month and year-to-date moves for the indexes, sectors, commodities, bonds and themes."""
    from .markets import Data  # noqa: PLC0415
    data = data or Data(memory, env=env, fetch=fetch)
    theme_symbols = [s for syms, _ in THEMES.values() for s in syms]
    symbols = list(dict.fromkeys([*INDEXES, *SECTORS, *COMMODITIES, *BONDS, *theme_symbols]))
    bars = data.bars("us", symbols, days=400)
    out = {}
    for s in symbols:
        b = bars.get(s) or []
        if len(b) >= 2:
            out[s] = {"price": b[-1]["c"], "day": _change(b, 1), "week": _change(b, 5), "month": _change(b, 21),
                      "three_months": _change(b, 63), "ytd": _ytd(b), "as_of": str(b[-1]["d"])}
    return out


def themes(prices: dict) -> list[dict]:
    rows = []
    for name, (syms, why) in THEMES.items():
        got = [prices[s] for s in syms if s in prices]
        if not got:
            continue
        avg = lambda k: (round(sum(g[k] for g in got if g[k] is not None) / n, 1)  # noqa: E731
                         if (n := sum(1 for g in got if g[k] is not None)) else None)
        rows.append({"theme": name, "funds": syms, "why": why, "week": avg("week"), "month": avg("month"),
                     "three_months": avg("three_months"), "ytd": avg("ytd")})
    return sorted(rows, key=lambda r: -(r["month"] if r["month"] is not None else -999))


# --- the lesson -----------------------------------------------------------------------------------------------
def _taught(memory: Memory) -> list[str]:
    try:
        return json.loads(memory.get(TAUGHT_KEY) or "[]")
    except ValueError:
        return []


def pick_concept(memory: Memory, prices: dict, mac: dict) -> tuple:
    """Today's idea: one today's numbers call for (not taught in the last 2 weeks), else the next one not taught."""
    taught = _taught(memory)
    recent = set(taught[-10:])
    wanted = []
    oil = (prices.get("USO") or {}).get("day")
    if oil is not None and abs(oil) >= 3:
        wanted.append("commodity_funds")
    if mac.get("curve") is not None and mac["curve"] < 0:
        wanted.append("yield_curve")
    rate_move = (mac.get("ten_year") or {}).get("month_change")
    if rate_move is not None and abs(rate_move) >= 0.25:
        wanted += ["rates", "bonds"]
    if (mac.get("inflation") or {}).get("value", 0) >= 3.5:
        wanted.append("inflation")
    gold = (prices.get("GLD") or {}).get("week")
    if gold is not None and abs(gold) >= 3:
        wanted.append("gold")
    sector_days = [abs((prices.get(s) or {}).get("day") or 0) for s in SECTORS]
    if sector_days and max(sector_days) >= 2:
        wanted.append("rotation")
    for key in wanted:
        if key not in recent:
            return CONCEPT_BY_KEY[key]
    return next((c for c in CONCEPTS if c[0] not in taught), CONCEPTS[len(taught) % len(CONCEPTS)])


def lesson_data(memory: Memory, env=None, fetch=None, data=None) -> dict:
    try:
        prices = moves(memory, env, fetch, data)
    except Exception as e:
        log.info("market school prices: %s", e)
        prices = {}
    mac = macro(memory, env, fetch)
    concept = pick_concept(memory, prices, mac)
    sector_rows = sorted(((SECTORS[s], prices[s]["day"]) for s in SECTORS if s in prices and
                          prices[s]["day"] is not None), key=lambda r: r[1])
    return {"day": date.today().isoformat(), "indexes": {INDEXES[s]: prices[s] for s in INDEXES if s in prices},
            "sectors_best": sector_rows[-2:][::-1], "sectors_worst": sector_rows[:2],
            "commodities": {COMMODITIES[s]: prices[s] for s in COMMODITIES if s in prices},
            "bonds": {BONDS[s]: prices[s] for s in BONDS if s in prices},
            "macro": mac, "themes": themes(prices),
            "concept": {"key": concept[0], "title": concept[1], "text": concept[2]}}


def _pct(v) -> str:
    return "flat" if v is None or v == 0 else f"{'up' if v > 0 else 'down'} {abs(v):g}%"


def plain(d: dict) -> str:
    """The lesson from the numbers alone (no model)."""
    parts = []
    sp = d["indexes"].get("S&P 500")
    if sp:
        parts.append(f"The S&P 500 was {_pct(sp['day'])} on the day and {_pct(sp['week'])} on the week.")
    if d["sectors_best"]:
        parts.append(f"Best sector: {d['sectors_best'][0][0]} ({_pct(d['sectors_best'][0][1])}); weakest: "
                     f"{d['sectors_worst'][0][0]} ({_pct(d['sectors_worst'][0][1])}).")
    com = [f"{k} {_pct(v['day'])}" for k, v in d["commodities"].items() if k in ("gold", "oil", "copper")]
    if com:
        parts.append("Commodities: " + ", ".join(com) + ".")
    m = d["macro"]
    rates = []
    if "fed_funds" in m:
        rates.append(f"the Fed's rate is {m['fed_funds']['value']:g}%")
    if "ten_year" in m:
        rates.append(f"the 10-year Treasury pays {m['ten_year']['value']:g}%")
    if "inflation" in m:
        rates.append(f"inflation is {m['inflation']['value']:g}% a year")
    if rates:
        parts.append("Background: " + ", ".join(rates) + ".")
    parts.append(f"Today's idea, {d['concept']['title'].lower()}: {d['concept']['text']}")
    if d["themes"]:
        t = d["themes"][0]
        parts.append(f"Theme to watch: {t['theme']}, {_pct(t['month'])} this month. {t['why']}")
    return " ".join(parts)


PROMPT = """You are a patient teacher helping someone new to investing learn from today's market. Write today's
Market school message from these numbers only (no other facts, no predictions, no buy or sell advice).

{data}

Answer with one line of JSON: {{"spoken": "...", "takeaway": "..."}}
- spoken: about 6 short spoken sentences: what moved and the likely reason in plain words (rates, inflation, oil,
  a sector rotation), then today's idea ("{title}") explained simply and tied to today's numbers, then the theme
  to watch. No jargon without a one-line explanation. No lists or symbols.
- takeaway: one sentence the learner should remember."""


async def write(memory: Memory, d: dict) -> tuple[str, str]:
    """(spoken lesson, takeaway): a free model first (public numbers only), then Claude's cheap model, then plain."""
    from .markets import _ask_claude  # noqa: PLC0415
    prompt = PROMPT.format(data=json.dumps({k: v for k, v in d.items() if k != "themes"} |
                                           {"themes": d["themes"][:3]}, ensure_ascii=False),
                           title=d["concept"]["title"])
    got = None
    try:
        from .benefits import last_json  # noqa: PLC0415
        from .free_models import FreeModels, NeedsClaude  # noqa: PLC0415
        free = FreeModels(memory)
        if free.enabled():
            try:
                ans = await free.run("summarize", prompt, system="Answer with the one line of JSON asked for.",
                                     purpose="market_school", claude_fallback=False, check_text=False)
                got = last_json(ans["answer"])
            except NeedsClaude:
                got = None
        if not (got and got.get("spoken")):
            got = await _ask_claude(memory, prompt)
    except Exception as e:  # the budget hold, no model, a bad answer: the numbers still teach
        log.info("market school writing: %s", e)
        got = None
    if got and str(got.get("spoken", "")).strip():
        return str(got["spoken"]).strip(), str(got.get("takeaway", "")).strip()
    return plain(d), d["concept"]["text"].split(". ")[0] + "."


def report(d: dict, spoken: str, takeaway: str) -> str:
    lines = [f"# Market school, {date.fromisoformat(d['day']):%A %B %d, %Y}", "", spoken, ""]
    if takeaway:
        lines += [f"**Remember:** {takeaway}", ""]
    lines += [f"## Today's idea: {d['concept']['title']}", "", d["concept"]["text"], ""]
    if d["indexes"]:
        lines += ["## What moved", "", "| | Day | Week | Month | Year to date |", "|---|---|---|---|---|"]
        for group in ("indexes", "commodities", "bonds"):
            for name, p in d[group].items():
                lines.append(f"| {name} | {_cell(p['day'])} | {_cell(p['week'])} | {_cell(p['month'])} | "
                             f"{_cell(p['ytd'])} |")
        lines.append("")
    m = d["macro"]
    facts = [(label, m[k]) for k, label in (("fed_funds", "Fed funds rate"), ("two_year", "2-year Treasury"),
                                             ("ten_year", "10-year Treasury"), ("inflation", "Inflation (CPI, a year)"),
                                             ("unemployment", "Unemployment")) if k in m]
    if facts:
        lines += ["## Rates and inflation (FRED)", ""]
        lines += [f"- {label}: {v['value']:g}% (as of {v['date']})" for label, v in facts]
        if m.get("curve") is not None:
            lines.append(f"- 10-year minus 2-year: {m['curve']:+g} points "
                         + ("(inverted: see 'the yield curve')" if m["curve"] < 0 else "(normal)"))
        lines.append("")
    energy = [(label, m[k]) for k, label in (("oil", "WTI oil, $ a barrel"), ("gas", "Natural gas, $ per MMBtu"))
              if k in m]
    if energy:
        lines += ["## Energy (EIA)", ""] + [f"- {label}: {v['value']:g} ({_pct(v['week_pct'])} on the week)"
                                             for label, v in energy] + [""]
    if d["themes"]:
        lines += ["## Themes to learn about", "", "| Theme | Funds | Week | Month | 3 months | Why it matters |",
                  "|---|---|---|---|---|---|"]
        lines += [f"| {t['theme']} | {', '.join(t['funds'])} | {_cell(t['week'])} | {_cell(t['month'])} | "
                  f"{_cell(t['three_months'])} | {t['why']} |" for t in d["themes"]]
        lines.append("")
    if m.get("missing"):
        lines += ["## Not connected yet", ""] + [f"- {x}" for x in m["missing"]] + [""]
    lines.append("_Education, not advice: Buddy never trades or tells you what to buy._")
    return "\n".join(lines)


def _cell(v) -> str:
    return "–" if v is None else f"{v:+g}%"


async def lesson(memory: Memory, env=None, fetch=None, data=None) -> tuple[int, str]:
    """Build today's lesson, keep it in Reports, remember the idea taught. Returns (report id, spoken text)."""
    import asyncio  # noqa: PLC0415
    d = await asyncio.to_thread(lesson_data, memory, env, fetch, data)
    spoken, takeaway = await write(memory, d)
    taught = [k for k in _taught(memory) if k != d["concept"]["key"]] + [d["concept"]["key"]]
    memory.set(TAUGHT_KEY, json.dumps(taught[-60:]))
    from .finance import purge_reports  # noqa: PLC0415
    from .jobs import SCHEMA as JOBS_SCHEMA  # noqa: PLC0415
    now = datetime.now(timezone.utc).isoformat()
    memory.db.executescript(JOBS_SCHEMA)
    purge_reports(memory)
    title = f"Market school, {date.fromisoformat(d['day']):%b %d, %Y}"
    memory.db.execute("DELETE FROM jobs WHERE kind = 'market_school' AND title = ?", (title,))
    job_id = memory.db.execute(
        "INSERT INTO jobs (kind, title, params, status, created_at, started_at, finished_at, summary, report, findings, "
        "sources, delivered) VALUES ('market_school', ?, '{}', 'done', ?, ?, ?, ?, ?, '[]', '[]', 1)",
        (title, now, now, now, spoken, report(d, spoken, takeaway))).lastrowid
    memory.db.commit()
    return job_id, "📚 Market school: " + spoken + " The full lesson is in Reports."


def school_day(now: datetime | None = None) -> str:
    return (now or datetime.now(timezone.utc)).astimezone(ZoneInfo("America/New_York")).date().isoformat()


def due(memory: Memory, now: datetime | None = None) -> bool:
    """Weekdays, once, from 8 AM New York time, unless turned off."""
    if memory.get(OFF_KEY) == "1":
        return False
    t = (now or datetime.now(timezone.utc)).astimezone(ZoneInfo("America/New_York"))
    if t.weekday() >= 5 or (t.hour, t.minute) < START:
        return False
    return memory.get(DONE_KEY) != school_day(now)


TOOL = {
    "name": "market_school",
    "description": (
        "Teach the owner investing from real data (education, never buy or sell advice; Buddy never trades). "
        "today: today's lesson now (what moved and why, one concept explained, a theme to watch; kept in Reports). "
        "themes: how the themes worth learning about did (AI chips, power and the grid, copper, defence, reshoring, "
        "communication services, gold, oil, short and long Treasuries), by week, month, 3 months and year. "
        "macro: Fed rate, 2- and 10-year yields, the yield curve, inflation, unemployment (FRED), oil and gas (EIA). "
        "company: a company's yearly revenue, profit, margin, EPS, cash and debt from its SEC filings (ticker). "
        "concept: Buddy's simple explanation of a concept (etf, expense_ratio, diversify, rates, inflation, "
        "yield_curve, rotation, commodity_funds, gold, pe, earnings, dca, rebalance, bonds, weighting, volatility, "
        "dividends, tax_accounts, tax_loss, stop_loss, timeline); for anything else, explain it yourself simply. "
        "on/off: the weekday morning lesson. Explain numbers in plain words and say what they mean for a learner."),
    "input_schema": {"type": "object", "properties": {
        "action": {"type": "string", "enum": ["today", "themes", "macro", "company", "concept", "on", "off"]},
        "ticker": {"type": "string"}, "concept": {"type": "string"}},
        "required": ["action"], "additionalProperties": False},
}


async def run_tool(memory: Memory, args: dict) -> str:
    import asyncio  # noqa: PLC0415
    action = args.get("action")
    if action == "today":
        _, text = await lesson(memory)
        memory.set(DONE_KEY, school_day())
        return text
    if action == "themes":
        prices = await asyncio.to_thread(moves, memory)
        return json.dumps({"themes": themes(prices), "note": "Themes to learn about, not picks."}, ensure_ascii=False)
    if action == "macro":
        return json.dumps(await asyncio.to_thread(macro, memory, None, None, True), ensure_ascii=False)
    if action == "company":
        return json.dumps(await asyncio.to_thread(company, memory, args.get("ticker", "")), ensure_ascii=False)
    if action == "concept":
        key = str(args.get("concept") or "").strip().lower().replace(" ", "_").replace("-", "_")
        c = CONCEPT_BY_KEY.get(key) or next((c for c in CONCEPTS if key and key in c[1].lower().replace(" ", "_")),
                                            None)
        if not c:
            return json.dumps({"known": [k for k, *_ in CONCEPTS],
                               "note": "Not in Buddy's list: explain it yourself, simply, with an everyday example."})
        return json.dumps({"title": c[1], "text": c[2]})
    if action == "off":
        memory.set(OFF_KEY, "1")
        return "Market school is off. Say 'turn on market school' to bring the morning lesson back."
    if action == "on":
        memory.set(OFF_KEY, "")
        return "Market school is on: a short lesson every weekday morning from 8 AM New York time."
    raise ToolError("Use today, themes, macro, company, concept, on or off.")


def check_fred(memory=None, env=None, fetch=None) -> None:
    fred(memory, "DFF", 1, env, fetch)


def check_eia(memory=None, env=None, fetch=None) -> None:
    eia(memory, "PET.RWTC.D", 14, env, fetch)


def check_sec(memory=None, env=None, fetch=None) -> None:
    gateway.call(memory, "sec", "/api/xbrl/companyfacts/CIK0000320193.json", headers=_sec_headers(env), env=env,
                 fetch=fetch, purpose="health")
