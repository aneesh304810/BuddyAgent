"""Factor yardsticks, dated stock lists, and news sentiment."""

import asyncio
import json
from datetime import date

import pytest

from buddy import benchmarks, factors, newssent, pricebook, stratlab
from buddy.connectors.hub import ConnectorHub
from buddy.memory import Memory
from buddy.tools import ToolError

FRENCH = """This file was created using CRSP data.

,Mkt-RF,SMB,HML,RF
192607,    2.96,   -2.56,   -2.43,    0.22
192608,    2.64,   -1.17,    3.82,    0.25
192609,  -99.99,    0.10,    0.20,    0.23

 Annual Factors: January-December
,Mkt-RF,SMB,HML,RF
  1927,   29.47,   -2.04,   -4.54,    3.12
"""


@pytest.fixture
def memory(tmp_path):
    m = Memory(tmp_path / "buddy.db")
    ConnectorHub(m)
    yield m
    pricebook.close_all()


def test_factor_files_keep_the_monthly_rows_only(memory):
    rows = benchmarks.parse_factors(FRENCH, ("market", "size", "value", "cash"))
    assert ("1926-07", "market", 0.0296) in rows and ("1926-08", "value", 0.0382) in rows
    assert not any(m.startswith("1927") for m, _, _ in rows)            # the yearly rows are left out
    assert ("1926-09", "market", -0.9999) not in rows and ("1926-09", "size", 0.001) in rows  # -99.99 is "missing"
    asked = []

    def fetch(url, data=None, headers=None):
        asked.append(url)
        return b"zip"

    async def reader(name, data):
        return {"text": FRENCH if len(asked) == 1 else "192607, 0.36\n192608, 1.10\n", "where": "sandbox", "error": ""}

    assert asyncio.run(benchmarks.refresh_factors(memory, fetch, reader)) == 13
    assert all(u.startswith("https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/ftp/") for u in asked)
    got = {r["name"]: r for r in benchmarks.factors(memory)["rows"]}
    assert got["momentum"]["months"] == 2 and round(got["momentum"]["last_12_months"], 4) == round(1.0036 * 1.011 - 1, 4)
    assert "cash" not in got and benchmarks.factors(memory)["from"] == "1926-07"


def test_a_holdings_file_becomes_a_dated_list_the_lab_can_use(memory):
    assert benchmarks.symbols_in("Name\tTicker\tWeight\nApple Inc\tAAPL\t7.1\nBerkshire\tBRK.B\t1.7\nCash\tUSD\t0.1\n\t-\t\n") == ["AAPL", "BRK-B"]
    with pytest.raises(ToolError):
        benchmarks.symbols_in("Name,Weight\nApple,7")

    async def reader(name, data):
        return {"text": "Ticker,Name\n" + "\n".join(f"{s},x" for s in ("AAPL", "MSFT", "ZZZT", "QQQA", "QQQB", "QQQC")),
                "where": "sandbox", "error": ""}

    got = asyncio.run(benchmarks.upload_list(memory, "My List", "holdings.csv", b"...", reader))
    assert got["name"] == "my_list" and got["symbols"] == 6
    benchmarks.keep_list(memory, "my_list", "My List", ["AAPL", "MSFT", "NEWW", "QQQA", "QQQB"], "uploaded", date(2026, 12, 1))
    one = benchmarks.lists(memory)[0]
    assert one["versions"] == 2 and one["symbols"] == 5 and not one["in_use"]  # the older version is kept, dated
    assert "ZZZT" not in stratlab.universe(memory)
    assert "now trades the my_list list" in benchmarks.use_in_lab(memory, "my_list")
    assert "NEWW" in stratlab.universe(memory) and "AAPL" in stratlab.universe(memory) and benchmarks.lists(memory)[0]["in_use"]
    benchmarks.use_in_lab(memory, "")
    assert "NEWW" not in stratlab.universe(memory)
    with pytest.raises(ToolError):
        benchmarks.use_in_lab(memory, "nope")


def test_news_sentiment_is_kept_per_company_and_reaches_the_trade_figures(memory):
    env = {"BUDDY_ALPHAVANTAGE_KEY": "k1", "BUDDY_MARKETAUX_KEY": "k2"}
    asked = []

    def fetch(url, data=None, headers=None):
        asked.append(url)
        if "alphavantage" in url:
            return json.dumps({"feed": [{"ticker_sentiment": [{"ticker": "OKLO", "relevance_score": "0.8", "ticker_sentiment_score": "0.4"},
                                                              {"ticker": "OKLO", "relevance_score": "0.1", "ticker_sentiment_score": "-0.9"},
                                                              {"ticker": "SMR", "relevance_score": "0.9", "ticker_sentiment_score": "-0.5"}]}]})
        return json.dumps({"data": [{"entities": [{"symbol": "OKLO", "sentiment_score": 0.2}]},
                                    {"entities": [{"symbol": "OKLO", "sentiment_score": 0.0}]}]})

    assert newssent.configured({}) == [] and newssent.configured(env) == ["alphavantage", "marketaux"]
    got = newssent.refresh(memory, env, fetch, date(2026, 11, 16), ["OKLO"])
    assert got == {"asked": 2, "kept": 2, "failed": {}}
    assert "tickers=OKLO" in asked[0] and "symbols=OKLO" in asked[1]  # only the ticker goes out
    assert newssent.recent(memory, "oklo", 7, date(2026, 11, 17)) == {"score": round((0.4 + 0.1 * 2) / 3, 4), "articles": 3}
    assert newssent.recent(memory, "OKLO", 7, date(2026, 12, 20)) is None
    assert factors.investing(memory, "ZZZZ") == {}  # a company outside the themes: no figures claimed
