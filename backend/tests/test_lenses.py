"""Investor checklists from SEC filings: the figures, each lens, the read, caching and banks."""

import json

import pytest

from buddy import lenses, pricebook
from buddy.connectors.hub import ConnectorHub
from buddy.memory import Memory

ENV = {"BUDDY_SEC_CONTACT": "me@example.com"}


@pytest.fixture
def memory(tmp_path):
    m = Memory(tmp_path / "buddy.db")
    ConnectorHub(m)
    yield m
    pricebook.close_all()


def fact(values, unit="USD", instant=False):
    rows = []
    for year, v in values.items():
        row = {"form": "10-K", "fp": "FY", "fy": year, "end": f"{year}-12-31", "filed": f"{year + 1}-02-01", "val": v}
        if not instant:
            row["start"] = f"{year}-01-01"
        rows.append(row)
    return {"units": {unit: rows}}


def company(eps, revenue=1000e6, ebit_share=0.3, equity=2000e6, shares=100e6, debt=300e6, ca=900e6, cl=300e6,
            dividend=1.0, bank=False):
    years = list(range(2026 - len(eps), 2026))
    gaap = {
        "Revenues": fact({y: revenue * (1.08 ** i) for i, y in enumerate(years)}),
        "NetIncomeLoss": fact({y: e * shares for y, e in zip(years, eps)}),
        "EarningsPerShareDiluted": fact(dict(zip(years, eps)), "USD/shares"),
        "StockholdersEquity": fact({y: equity for y in years}, instant=True),
        "Assets": fact({y: equity * 2 for y in years}, instant=True),
        "LongTermDebtNoncurrent": fact({y: debt for y in years}, instant=True),
        "CashAndCashEquivalentsAtCarryingValue": fact({y: 200e6 for y in years}, instant=True),
        "NetCashProvidedByUsedInOperatingActivities": fact({y: e * shares * 1.2 for y, e in zip(years, eps)}),
        "PaymentsToAcquirePropertyPlantAndEquipment": fact({y: 50e6 for y in years}),
        "WeightedAverageNumberOfDilutedSharesOutstanding": fact({y: shares for y in years}, "shares"),
    }
    if dividend:
        gaap["CommonStockDividendsPerShareDeclared"] = fact({y: dividend for y in years}, "USD/shares")
    if not bank:
        gaap.update({"OperatingIncomeLoss": fact({y: revenue * (1.08 ** i) * ebit_share for i, y in enumerate(years)}),
                     "AssetsCurrent": fact({y: ca for y in years}, instant=True),
                     "LiabilitiesCurrent": fact({y: cl for y in years}, instant=True),
                     "PropertyPlantAndEquipmentNet": fact({y: 400e6 for y in years}, instant=True)})
    return {"entityName": "Test Co", "facts": {"us-gaap": gaap}}


def fetcher(facts, seen=None):
    def fetch(url, data=None, headers=None):
        if seen is not None:
            seen.append(url)
        if "company_tickers" in url:
            return json.dumps({"0": {"ticker": "TEST", "cik_str": 1234, "title": "Test Co"}})
        return json.dumps(facts)
    return fetch


STEADY = [3.0, 3.2, 3.5, 3.8, 4.0, 4.3, 4.6, 5.0]


def test_a_cheap_steady_company_passes_the_value_tests(memory):
    got = lenses.checklists(memory, "test", 60.0, 0.05, None, ENV, fetcher(company(STEADY)))
    by = {x["lens"].split(" ")[0]: x for x in got["lenses"]}
    assert got["company"] == "Test Co" and got["figures_from"] == "10-K filings to 2025"
    assert by["Graham"]["verdict"] == "passes"
    pe = next(c for c in by["Graham"]["checks"] if "15 times" in c["test"])
    assert pe["passed"] and pe["value"] == "12.9 times"          # 60 / average of 4.3, 4.6, 5.0
    assert by["Quality"]["verdict"] == "passes" and by["Greenblatt"]["verdict"] == "passes"
    assert got["read"] == {"price": "cheap", "business": "strong"}
    assert "not what that person would say today" in got["note"]


def test_the_same_company_at_a_high_price_is_expensive_and_marks_worries(memory):
    got = lenses.checklists(memory, "TEST", 500.0, 0.4, 6.0, ENV, fetcher(company(STEADY)))
    by = {x["lens"].split(" ")[0]: x["verdict"] for x in got["lenses"]}
    assert by["Graham"] != "passes" and by["Lynch"] == "fails" and by["Marks"] == "fails"
    assert by["Quality"] == "passes"                              # the business didn't change, only the price
    assert got["read"] == {"price": "expensive", "business": "strong"}


def test_losses_and_a_bank_are_reported_honestly(memory):
    loser = lenses.checklists(memory, "TEST", 20.0, None, None, ENV,
                              fetcher(company([-1.0, -0.5, -2.0, -1.5], dividend=0)))
    assert loser["read"]["price"] == "no profits to value" and loser["read"]["business"] == "weak"


def test_what_a_bank_does_not_report_is_not_a_fail(memory):
    got = lenses.checklists(memory, "TEST", 60.0, None, None, ENV, fetcher(company(STEADY, bank=True)))
    graham = next(x for x in got["lenses"] if x["lens"].startswith("Graham"))
    ratio = next(c for c in graham["checks"] if "twice current liabilities" in c["test"])
    assert ratio["passed"] is None and ratio["value"] == "not reported"
    green = next(x for x in got["lenses"] if x["lens"].startswith("Greenblatt"))
    assert green["verdict"] == "not enough data"


def test_filings_are_fetched_once_a_week_and_only_the_ticker_goes_out(memory):
    seen = []
    fetch = fetcher(company(STEADY), seen)
    lenses.checklists(memory, "TEST", 60.0, None, None, ENV, fetch)
    lenses.checklists(memory, "TEST", 80.0, None, None, ENV, fetch)
    assert len([u for u in seen if "companyfacts" in u]) == 1
    assert all("60" not in u.split("CIK")[-1].replace("0000001234", "") for u in seen if "companyfacts" in u)


def test_the_brief_for_a_signal(memory):
    b = lenses.brief(memory, "TEST", 500.0, 0.4, 6.0, ENV, fetcher(company(STEADY)))
    assert b["price"] == "expensive" and "the price looks expensive, the business strong" in b["line"]
    assert lenses.brief(memory, "NOPE", 10.0, None, None, ENV, fetcher(company(STEADY))) is None  # not at the SEC
