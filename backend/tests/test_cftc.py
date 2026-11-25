"""Who holds the futures: the CFTC's weekly Commitments of Traders report, read by market code and worked out."""

import json
import urllib.parse
from datetime import date, datetime, timedelta

import pytest

from buddy import cftc, gateway, modules, pricebook
from buddy.connectors.hub import ConnectorHub
from buddy.memory import Memory

TODAY = date(2026, 11, 25)
LATEST = date(2026, 11, 17)  # a Tuesday


@pytest.fixture
def memory(tmp_path):
    m = Memory(tmp_path / "buddy.db")
    ConnectorHub(m)
    yield m
    pricebook.close_all()


def week_rows(code, weeks, net_of, oi=400_000.0):
    """The report's rows (strings, as Socrata sends them) for one market, oldest first."""
    rows = []
    for i in range(weeks):
        day = LATEST - timedelta(weeks=weeks - 1 - i)
        net = net_of(i)
        rows.append({"report_date_as_yyyy_mm_dd": f"{day.isoformat()}T00:00:00.000", "cftc_contract_market_code": code,
                     "market_and_exchange_names": "SOME NAME - EXCHANGE", "open_interest_all": str(oi),
                     "noncomm_positions_long_all": str(100_000 + max(net, 0)),
                     "noncomm_positions_short_all": str(100_000 + max(-net, 0)),
                     "comm_positions_long_all": "50000", "comm_positions_short_all": str(50_000 + net),
                     "nonrept_positions_long_all": "1000", "nonrept_positions_short_all": "2000"})
    return rows


class Fake:
    def __init__(self, rows):
        self.rows, self.urls = rows, []

    def __call__(self, url, data=None, headers=None):
        self.urls.append(url)
        where = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)["$where"][0]
        since = where.split(">= '")[1][:10]
        return json.dumps([r for r in self.rows if r["report_date_as_yyyy_mm_dd"][:10] >= since])


def rows_for_three_markets():
    return (week_rows("088691", 160, lambda i: 1000 * i)  # gold: rising every week, so the latest is the highest
            + week_rows("084691", 160, lambda i: 40_000 - 200 * i)  # silver: still net long, but the least in years
            + week_rows("13874A", 160, lambda i: (-1) ** i * 5000))  # S&P 500: back and forth


def test_the_query_asks_only_for_the_markets_buddy_follows_and_a_date():
    path = cftc.query_path(["088691", "'; DROP", "084691"], date(2023, 11, 1))
    where = urllib.parse.parse_qs(urllib.parse.urlparse(path).query)["$where"][0]
    assert path.startswith("/resource/6dca-aqww.json?")
    assert where == ("cftc_contract_market_code in('088691', '084691') AND report_date_as_yyyy_mm_dd >= "
                     "'2023-11-01T00:00:00'")
    assert len(cftc.MARKETS) == 17 and set(m[1] for m in cftc.MARKETS.values()) == set(cftc.GROUPS)


def test_first_read_takes_three_years_then_only_the_last_weeks_again(memory):
    fake = Fake(rows_for_three_markets())
    got = cftc.pull(memory, fetch=fake, today=TODAY)
    assert got["latest"] == LATEST.isoformat() and got["was"] == ""
    first = urllib.parse.parse_qs(urllib.parse.urlparse(fake.urls[0]).query)["$where"][0]
    assert f"'{(TODAY - timedelta(days=156 * 7 + 14)).isoformat()}T00:00:00'" in first
    assert fake.urls[0].startswith("https://publicreporting.cftc.gov/resource/")
    held = pricebook.run(memory, "SELECT COUNT(*) AS n FROM cot_weekly")[0]["n"]
    again = cftc.pull(memory, fetch=fake, today=TODAY)
    second = urllib.parse.parse_qs(urllib.parse.urlparse(fake.urls[1]).query)["$where"][0]
    assert f"'{(LATEST - timedelta(days=21)).isoformat()}T00:00:00'" in second  # corrections are read again
    assert again["rows"] == 12 and pricebook.run(memory, "SELECT COUNT(*) AS n FROM cot_weekly")[0]["n"] == held


def test_each_market_is_worked_out_against_its_own_three_years(memory):
    cftc.pull(memory, fetch=Fake(rows_for_three_markets()), today=TODAY)
    by = {m["name"]: m for m in cftc.markets(memory)}
    gold = by["Gold"]
    assert gold["net"] == 159_000 and gold["week_change"] == 1000 and gold["net_share"] == round(159_000 / 400_000, 3)
    assert gold["rank"] == 100 and gold["weeks"] == 156 and gold["standing"] == cftc.HIGH
    assert gold["hedger_net"] == -159_000 and gold["small_net"] == -1000 and len(gold["trend"]) == 26
    assert "In gold, large speculators are net long 159,000 contracts (40% of all open contracts), more long than a " \
           "week before by 1,000 contracts." in gold["reading"]
    assert "near the most long (bullish) they have been in three years" in gold["reading"]
    assert "Hedgers are on the other side, net short." in gold["reading"]
    silver = by["Silver"]
    assert silver["net"] > 0 and silver["rank"] == 0 and silver["standing"] == cftc.LOW
    assert "near the least long (bearish)" in silver["reading"] and "less long than a week before" in silver["reading"]
    sp = by["S&P 500 (E-mini)"]  # back and forth: half the weeks equal today's, counted half each
    assert sp["net"] == -5000 and sp["rank"] == 25 and sp["standing"] == "lower than usual"


def test_page_groups_markets_lists_the_crowded_ones_and_credits_the_cftc(memory):
    empty = cftc.page(memory)
    assert empty["groups"] == [] and "Read now" in empty["empty"]
    cftc.pull(memory, fetch=Fake(rows_for_three_markets()), today=TODAY)
    page = cftc.page(memory)
    assert [g["title"] for g in page["groups"]] == ["Stock indexes", "Metals"]
    assert page["groups"][0]["note"] == cftc.FINANCIAL_NOTE and page["groups"][1]["note"] == ""
    assert page["crowded"] == [{"name": "Gold", "standing": cftc.HIGH}, {"name": "Silver", "standing": cftc.LOW}]
    assert page["as_of"] == LATEST.isoformat()
    assert "Commodity Futures Trading Commission" in page["source"] and "not advice" in page["note"]


def test_too_few_weeks_says_so_instead_of_a_rank(memory):
    cftc.pull(memory, fetch=Fake(week_rows("088691", 20, lambda i: 100 * i)), today=TODAY)
    gold = cftc.markets(memory)[0]
    assert gold["rank"] is None and gold["standing"] == "" and "only 19 weeks to compare it with" in gold["reading"]


def test_due_once_the_next_friday_report_is_out(memory):
    assert cftc.due(memory, datetime(2026, 11, 25, 9))  # nothing held: at once
    cftc.pull(memory, fetch=Fake(week_rows("088691", 3, lambda i: i)), today=TODAY)
    assert not cftc.due(memory, datetime(2026, 11, 25, 22))  # read today already
    assert not cftc.due(memory, datetime(2026, 11, 26, 22))  # 9 days after the Tuesday held
    assert not cftc.due(memory, datetime(2026, 11, 27, 16))  # Friday before the report
    assert cftc.due(memory, datetime(2026, 11, 27, 17))  # Friday afternoon: out
    assert cftc.due(memory, datetime(2026, 11, 30, 8))  # a holiday pushed it to Monday: asked again


def test_the_chat_tool_shows_a_market_by_its_everyday_name(memory):
    cftc.pull(memory, fetch=Fake(rows_for_three_markets()), today=TODAY)
    gold = json.loads(cftc.run_tool(memory, {"action": "show", "market": "gold"}))
    assert [m["name"] for m in gold["markets"]] == ["Gold"] and "trend" not in gold["markets"][0]
    sp = json.loads(cftc.run_tool(memory, {"action": "show", "market": "the S&P"}))
    assert [m["name"] for m in sp["markets"]] == ["S&P 500 (E-mini)"]
    assert len(json.loads(cftc.run_tool(memory, {"action": "show"}))["markets"]) == 3
    assert "Not one of the markets" in json.loads(cftc.run_tool(memory, {"action": "show", "market": "llamas"}))["error"]
    assert "futures_positioning" in modules.TOOLS["investing"] and cftc.TOOL["name"] == "futures_positioning"
    assert "advice" in cftc.TOOL["description"] and "finra" not in json.dumps(cftc.TOOL).lower()


def test_catalog_entry_and_switching_it_off(memory):
    entry = gateway.CATALOG["cftc"]
    assert entry["hosts"] == ["https://publicreporting.cftc.gov"] and entry["auth"] == {"type": "none"}
    assert entry["privacy"] == "public" and "public domain" in entry["used_for"]
    gateway.set_off(memory, "cftc", True)
    with pytest.raises(gateway.ConnectError):
        cftc.pull(memory, fetch=Fake([]), today=TODAY)
