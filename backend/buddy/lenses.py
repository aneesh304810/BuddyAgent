"""Investor checklists: what each school of investing would check about a company, computed in code from its own
SEC filings (10-K figures) and today's price. The numbers are the company's; the checklists are in the spirit of
each investor's published method, not a claim about what that person would say today.

  Graham      the defensive investor's tests from The Intelligent Investor (ch. 14): price no more than 15 times
              three-year average earnings, price-to-book no more than 1.5 (or the two multiplied under 22.5),
              current assets twice current liabilities, long-term debt under working capital, profits every year,
              a dividend, and earnings growth
  Quality     the tests commonly used in the Buffett and Munger tradition (their letters stress them; this is not a
              formula they published): return on equity over 15% year after year, steady or rising margins, free
              cash flow every year, debt that a few years of profit could repay
  Lynch       growth at a reasonable price, from One Up on Wall Street: the P/E against the growth rate (PEG; about
              1 is fair, 2 is dear), with dividends counted, and modest debt
  Greenblatt  the two numbers of The Little Book That Beats the Market: earnings yield (operating profit over
              enterprise value) and return on capital. He ranks every stock against the others; one company can't
              be ranked, so fixed marks stand in (8% and 25%)
  Marks       the second-level question from The Most Important Thing: how much optimism is already in the price
              (a high multiple, a price far above its 200-day average, a crowd)

Banks and insurers report differently (no current assets or operating profit), so those tests show "not reported"
rather than a pass or fail. Filings are yearly and up to 15 months old: a checklist is a starting point for
reading the filing, never a verdict. Only the ticker goes to the SEC.
"""

import json
import logging
from datetime import date

from .memory import Memory
from .tools import ToolError, local_now

log = logging.getLogger(__name__)

KEEP_DAYS = 7
NAMES = {
    "revenue": ("Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax", "SalesRevenueNet"),
    "net_income": ("NetIncomeLoss",), "eps": ("EarningsPerShareDiluted",),
    "ebit": ("OperatingIncomeLoss",), "equity": ("StockholdersEquity",), "assets": ("Assets",),
    "current_assets": ("AssetsCurrent",), "current_liabilities": ("LiabilitiesCurrent",),
    "debt": ("LongTermDebtNoncurrent", "LongTermDebt"), "cash": ("CashAndCashEquivalentsAtCarryingValue",),
    "ocf": ("NetCashProvidedByUsedInOperatingActivities",),
    "capex": ("PaymentsToAcquirePropertyPlantAndEquipment",),
    "dividend": ("CommonStockDividendsPerShareDeclared", "CommonStockDividendsPerShareCashPaid"),
    "shares": ("WeightedAverageNumberOfDilutedSharesOutstanding",),
    "ppe": ("PropertyPlantAndEquipmentNet",),
}
UNITS = {"eps": "USD/shares", "dividend": "USD/shares", "shares": "shares"}


def _ready(memory: Memory) -> None:
    from . import pricebook  # noqa: PLC0415  public company figures live with the other market data
    pricebook.run(memory, "CREATE TABLE IF NOT EXISTS fundamentals (ticker VARCHAR PRIMARY KEY, fetched DATE, "
                          "company VARCHAR, data VARCHAR)")


def financials(memory: Memory, ticker: str, env=None, fetch=None, fresh: bool = False) -> dict:
    """{"company", "years": {year: {revenue, net_income, eps, ...}}} from the company's 10-K filings, kept a week."""
    from . import gateway, market_school, pricebook  # noqa: PLC0415
    ticker = (ticker or "").strip().upper().lstrip("$")
    if not ticker:
        raise ToolError("Which company? Give its ticker, like AAPL.")
    _ready(memory)
    today = local_now().date()
    row = pricebook.run(memory, "SELECT fetched, company, data FROM fundamentals WHERE ticker = ?", (ticker,))
    if row and not fresh and (today - date.fromisoformat(str(row[0]["fetched"]))).days < KEEP_DAYS:
        return {"ticker": ticker, "company": row[0]["company"],
                "years": {int(y): v for y, v in json.loads(row[0]["data"]).items()}}
    cik, name = market_school._cik(memory, ticker, env, fetch)
    facts = gateway.call(memory, "sec", f"/api/xbrl/companyfacts/CIK{cik}.json",
                         headers=market_school._sec_headers(env), env=env, fetch=fetch, purpose="lenses") or {}
    gaap = (facts.get("facts") or {}).get("us-gaap") or {}
    series = {k: market_school._yearly(gaap, names, UNITS.get(k, "USD")) for k, names in NAMES.items()}
    years = sorted(set(series["net_income"]) | set(series["revenue"]))[-10:]
    data = {y: {k: series[k].get(y) for k in NAMES} for y in years}
    company = facts.get("entityName") or name
    pricebook.run(memory, "INSERT OR REPLACE INTO fundamentals VALUES (?, ?, ?, ?)",
                  (ticker, today.isoformat(), company, json.dumps(data)))
    return {"ticker": ticker, "company": company, "years": data}


def _last(years: dict, key: str):
    for y in sorted(years, reverse=True):
        if years[y].get(key) is not None:
            return years[y][key]
    return None


def _growth(years: dict, key: str, span: int = 5) -> float | None:
    """Yearly growth rate over up to `span` years (compound), when both ends are positive."""
    have = [(y, years[y][key]) for y in sorted(years) if years[y].get(key) is not None]
    if len(have) < 3:
        return None
    (y0, a), (y1, b) = have[-min(span + 1, len(have))], have[-1]
    if a <= 0 or b <= 0 or y1 == y0:
        return None
    return (b / a) ** (1 / (y1 - y0)) - 1


def measures(fin: dict, price: float | None) -> dict:
    """The figures every checklist uses. None where the filings don't give one."""
    ys = fin["years"]
    if not ys:
        raise ToolError(f"No yearly figures in {fin['ticker']}'s SEC filings (a fund, or a foreign company).")
    latest = max(ys)
    g = lambda k: _last(ys, k)  # noqa: E731
    eps_hist = [ys[y]["eps"] for y in sorted(ys) if ys[y].get("eps") is not None]
    ni_hist = [ys[y]["net_income"] for y in sorted(ys) if ys[y].get("net_income") is not None]
    roe = [ys[y]["net_income"] / ys[y]["equity"] for y in sorted(ys)
           if ys[y].get("net_income") is not None and (ys[y].get("equity") or 0) > 0]
    margins = [ys[y]["ebit"] / ys[y]["revenue"] for y in sorted(ys) if ys[y].get("ebit") is not None
               and ys[y].get("revenue")]
    fcf = [ys[y]["ocf"] - (ys[y].get("capex") or 0) for y in sorted(ys) if ys[y].get("ocf") is not None]
    eps, shares, equity, debt, cash = g("eps"), g("shares"), g("equity"), g("debt") or 0.0, g("cash") or 0.0
    eps3 = sum(eps_hist[-3:]) / len(eps_hist[-3:]) if eps_hist else None
    book = equity / shares if equity and shares else None
    cap = price * shares if price and shares else None
    ev = cap + debt - cash if cap else None
    ebit, ca, cl, ppe = g("ebit"), g("current_assets"), g("current_liabilities"), g("ppe")
    capital = (ca - cl) + ppe if None not in (ca, cl, ppe) and (ca - cl) + ppe > 0 else None
    growth = _growth(ys, "eps")
    pe = price / eps if price and eps and eps > 0 else None
    div = g("dividend") if ys[latest].get("dividend") is not None or g("dividend") else None
    return {
        "as_of_year": latest, "years_of_data": len(ys), "price": price,
        "pe": pe, "pe_3yr": price / eps3 if price and eps3 and eps3 > 0 else None,
        "pb": price / book if price and book and book > 0 else None,
        "current_ratio": ca / cl if ca and cl else None,
        "working_capital": ca - cl if None not in (ca, cl) else None, "debt": debt,
        "profit_years": sum(1 for v in ni_hist if v > 0), "years_checked": len(ni_hist),
        "dividend": div, "dividend_yield": div / price if div and price else None,
        "eps_growth": growth, "revenue_growth": _growth(ys, "revenue"),
        "roe_avg": sum(roe) / len(roe) if roe else None, "roe_min": min(roe) if roe else None,
        "margin_now": margins[-1] if margins else None, "margin_then": margins[0] if len(margins) >= 3 else None,
        "fcf_years": sum(1 for v in fcf if v > 0), "fcf_checked": len(fcf),
        "fcf_yield": fcf[-1] / cap if fcf and cap else None,
        "debt_to_income": debt / ni_hist[-1] if ni_hist and ni_hist[-1] > 0 else None,
        "debt_to_equity": debt / equity if equity and equity > 0 else None,
        "earnings_yield": ebit / ev if ebit is not None and ev and ev > 0 else None,
        "return_on_capital": ebit / capital if ebit is not None and capital else None,
        "peg": pe / (growth * 100) if pe and growth and growth > 0 else None,
    }


def _check(name: str, value, ok, shown: str) -> dict:
    """One test: passed True / False, or None when the filings don't report what it needs."""
    return {"test": name, "passed": None if value is None else bool(ok(value)),
            "value": "not reported" if value is None else shown}


def _verdict(checks: list[dict]) -> str:
    done = [c for c in checks if c["passed"] is not None]
    if len(done) < 2:
        return "not enough data"
    share = sum(c["passed"] for c in done) / len(done)
    return "passes" if share >= 0.75 else "fails" if share <= 0.34 else "mixed"


def graham(m: dict) -> dict:
    pe_pb = m["pe_3yr"] * m["pb"] if m["pe_3yr"] and m["pb"] else None
    checks = [
        _check("price at most 15 times 3-year average earnings", m["pe_3yr"], lambda v: v <= 15,
               f"{m['pe_3yr']:.1f} times" if m["pe_3yr"] else ""),
        _check("price-to-book at most 1.5, or P/E times P/B under 22.5", pe_pb,
               lambda v: m["pb"] <= 1.5 or v <= 22.5, f"P/B {m['pb']:.1f}, product {pe_pb:.0f}" if pe_pb else ""),
        _check("current assets at least twice current liabilities", m["current_ratio"], lambda v: v >= 2,
               f"{m['current_ratio']:.1f} times" if m["current_ratio"] else ""),
        _check("long-term debt no more than working capital", m["working_capital"],
               lambda v: m["debt"] <= v, f"debt ${m['debt'] / 1e9:.1f}B vs working capital "
                                         f"${(m['working_capital'] or 0) / 1e9:.1f}B"),
        _check("a profit in every year on file", m["years_checked"] or None,
               lambda v: m["profit_years"] == v, f"{m['profit_years']} of {m['years_checked']} years"),
        _check("pays a dividend", m["dividend"] if m["dividend"] is not None else (0 if m["years_of_data"] else None),
               lambda v: v > 0, f"${m['dividend']:.2f} a share" if m["dividend"] else "none reported"),
        _check("earnings per share growing", m["eps_growth"], lambda v: v >= 0.03,
               f"{m['eps_growth']:+.0%} a year" if m["eps_growth"] is not None else ""),
    ]
    return {"lens": "Graham (defensive investor)", "verdict": _verdict(checks), "checks": checks}


def quality(m: dict) -> dict:
    checks = [
        _check("return on equity averages 15% or more", m["roe_avg"], lambda v: v >= 0.15,
               f"{m['roe_avg']:.0%} average, {m['roe_min']:.0%} worst year" if m["roe_avg"] is not None else ""),
        _check("operating margin held or rose", m["margin_then"], lambda v: m["margin_now"] >= v - 0.02,
               f"{m['margin_then']:.0%} then, {m['margin_now']:.0%} now" if m["margin_then"] is not None else ""),
        _check("free cash flow every year", m["fcf_checked"] or None, lambda v: m["fcf_years"] == v,
               f"{m['fcf_years']} of {m['fcf_checked']} years"),
        _check("debt repayable from four years of profit", m["debt_to_income"], lambda v: v <= 4,
               f"{m['debt_to_income']:.1f} years of profit" if m["debt_to_income"] is not None else ""),
    ]
    return {"lens": "Quality (Buffett and Munger tradition)", "verdict": _verdict(checks), "checks": checks}


def lynch(m: dict) -> dict:
    total = ((m["eps_growth"] or 0) + (m["dividend_yield"] or 0)) * 100 / m["pe"] if m["pe"] and m["eps_growth"] else None
    checks = [
        _check("P/E no more than about 1.5 times the growth rate (PEG)", m["peg"], lambda v: v <= 1.5,
               f"PEG {m['peg']:.1f} (P/E {m['pe']:.0f}, growth {m['eps_growth']:.0%})" if m["peg"] else ""),
        _check("growth plus dividend yield at least the P/E", total, lambda v: v >= 1,
               f"{total:.1f} (2 or more is what Lynch looked for)" if total is not None else ""),
        _check("debt under 80% of equity", m["debt_to_equity"], lambda v: v <= 0.8,
               f"{m['debt_to_equity']:.0%}" if m["debt_to_equity"] is not None else ""),
    ]
    return {"lens": "Lynch (growth at a reasonable price)", "verdict": _verdict(checks), "checks": checks}


def greenblatt(m: dict) -> dict:
    checks = [
        _check("earnings yield of 8% or more", m["earnings_yield"], lambda v: v >= 0.08,
               f"{m['earnings_yield']:.1%}" if m["earnings_yield"] is not None else ""),
        _check("return on capital of 25% or more", m["return_on_capital"], lambda v: v >= 0.25,
               f"{m['return_on_capital']:.0%}" if m["return_on_capital"] is not None else ""),
    ]
    return {"lens": "Greenblatt (earnings yield and return on capital)", "verdict": _verdict(checks),
            "checks": checks, "note": "He ranks every stock against the others; these fixed marks stand in."}


def marks(m: dict, above_200day: float | None = None, crowd: float | None = None) -> dict:
    checks = [
        _check("the multiple leaves room for disappointment (P/E under 35)", m["pe"], lambda v: v < 35,
               f"P/E {m['pe']:.0f}" if m["pe"] else ""),
        _check("the price isn't far above its 200-day average", above_200day, lambda v: v < 0.25,
               f"{above_200day:+.0%}" if above_200day is not None else ""),
        _check("no crowd piling in", crowd, lambda v: v < 3, f"{crowd:g} times its usual attention" if crowd else ""),
    ]
    return {"lens": "Marks (how much optimism is in the price)", "verdict": _verdict(checks), "checks": checks}


def read(m: dict, lenses: list[dict]) -> dict:
    """Two plain words for the signals: is the price rich, and is the business strong."""
    by = {x["lens"].split(" ")[0]: x["verdict"] for x in lenses}
    if m["pe"] is None and (m["profit_years"] < m["years_checked"] or not m["years_checked"]):
        price = "no profits to value"
    elif (m["peg"] or 0) >= 2 and (m["earnings_yield"] is None or m["earnings_yield"] < 0.03):
        price = "expensive"
    elif by.get("Graham") == "passes" or (m["earnings_yield"] or 0) >= 0.08 or (m["peg"] or 9) <= 1:
        price = "cheap"
    else:
        price = "fair"
    return {"price": price, "business": {"passes": "strong", "fails": "weak"}.get(by.get("Quality"), "mixed")}


def checklists(memory: Memory, ticker: str, price: float | None = None, above_200day: float | None = None,
               crowd: float | None = None, env=None, fetch=None) -> dict:
    """Every checklist for one company. `price`: today's; without it only the business tests can be run."""
    fin = financials(memory, ticker, env, fetch)
    m = measures(fin, price)
    lenses = [graham(m), quality(m), lynch(m), greenblatt(m), marks(m, above_200day, crowd)]
    return {"ticker": fin["ticker"], "company": fin["company"], "figures_from": f"10-K filings to {m['as_of_year']}",
            "price_used": price, "read": read(m, lenses), "lenses": lenses,
            "note": "Checklists in the spirit of each investor's published method, computed from SEC filings; not "
                    "what that person would say today, and not advice. 'Not reported' means the filings don't give "
                    "that figure (common for banks)."}


def brief(memory: Memory, ticker: str, price: float, above_200day: float | None = None, crowd: float | None = None,
          env=None, fetch=None) -> dict | None:
    """For a signal: {"price": cheap | fair | expensive | no profits to value, "business": ..., "line": one
    sentence}. None when the SEC has no filings for it (a fund) or isn't set up."""
    try:
        got = checklists(memory, ticker, price, above_200day, crowd, env, fetch)
    except Exception as e:
        log.info("lenses %s: %s", ticker, e)
        return None
    by = ", ".join(f"{x['lens'].split(' ')[0]} {x['verdict']}" for x in got["lenses"] if x["verdict"] != "not enough data")
    return {**got["read"], "line": f"investor checklists ({got['figures_from']}): the price looks "
                                   f"{got['read']['price']}, the business {got['read']['business']} ({by})"}


TOOL = {
    "name": "investor_checks",
    "description": (
        "Run the classic investors' checklists on a US company from its own SEC filings and today's price: Graham "
        "(defensive investor's tests), Quality (the Buffett and Munger tradition: return on equity, margins, free "
        "cash flow, debt), Lynch (P/E against growth), Greenblatt (earnings yield and return on capital) and Marks "
        "(how much optimism is in the price). Give the ticker. Each test shows pass, fail or 'not reported' with "
        "the number. Use it for 'is X a good company', 'is X expensive', 'what would Graham check'. Say these are "
        "checklists in the spirit of each method from yearly filings, not what the person would say today, and "
        "not advice. Funds (ETFs) have no filings to check."),
    "input_schema": {"type": "object", "properties": {"ticker": {"type": "string"}},
                     "required": ["ticker"], "additionalProperties": False},
}


def run_tool(memory: Memory, args: dict) -> str:
    from .markets import Data, sma  # noqa: PLC0415
    ticker = (args.get("ticker") or "").strip().upper().lstrip("$")
    price = above = None
    try:
        closes = [b["c"] for b in Data(memory).bars("us", [ticker], days=320).get(ticker) or []]
        if closes:
            price = closes[-1]
            avg = sma(closes, 200)
            above = price / avg - 1 if avg else None
    except Exception:
        log.info("lenses: no price for %s", ticker, exc_info=True)
    return json.dumps(checklists(memory, ticker, price, above), default=str)
