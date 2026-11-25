"""Short selling in the watchlist's US stocks, from FINRA: the daily file (only the date is sent), short interest
(the watchlist's ticker symbols), worked out against each stock's own history; shown by code, given to no AI."""

import json
from datetime import date, datetime, timedelta

import pytest

from buddy import agent, finra, gateway, pricebook
from buddy.connectors.hub import ConnectorHub
from buddy.markets import SCHEMA_SQL
from buddy.memory import Memory


@pytest.fixture
def memory(tmp_path):
    m = Memory(tmp_path / "buddy.db")
    ConnectorHub(m)
    m.db.executescript(SCHEMA_SQL)
    yield m
    pricebook.close_all()


def watch(memory, *symbols, market="us"):
    for s in symbols:
        memory.db.execute("INSERT OR REPLACE INTO mkt_watch (symbol, market, note, added_at) VALUES (?, ?, '', "
                          "'2026-11-01')", (s, market))
    memory.db.commit()


def day_file(day: date, shares: dict) -> bytes:
    """FINRA's file for one day: shares = {symbol: (short, total)}; other stocks fill it out."""
    stamp = day.strftime("%Y%m%d")
    lines = ["Date|Symbol|ShortVolume|ShortExemptVolume|TotalVolume|Market"]
    lines += [f"{stamp}|{s}|{short:.6f}|12|{total:.6f}|B,Q,N" for s, (short, total) in shares.items()]
    lines += [f"{stamp}|ZZZ{i}|10|0|20|Q" for i in range(3)]
    lines.append(str(len(lines) - 1))
    return "\n".join(lines).encode()


class Files:
    """cdn.finra.org: a file each weekday but the holidays, in which OKLO's short share is 40%, except the latest."""

    def __init__(self, holidays=(), latest=None, spike=0.40):
        self.holidays, self.latest, self.spike, self.urls, self.posts = set(holidays), latest, spike, [], []

    def __call__(self, url, data=None, headers=None):
        if data is not None:  # the short interest API
            self.posts.append((url, json.loads(data), headers))
            return json.dumps(SHORT_INTEREST)
        self.urls.append(url)
        day = datetime.strptime(url[-12:-4], "%Y%m%d").date()
        if day in self.holidays:
            raise gateway.ConnectError("finra", "FINRA said HTTP 403", 403)
        share = self.spike if day == self.latest else 0.40
        return day_file(day, {"OKLO": (share * 1000, 1000.0), "BRK/B": (480.0, 1000.0), "AAPL": (510.5, 1000.0),
                              "NOTMINE": (900.0, 1000.0)})


SHORT_INTEREST = [
    {"symbolCode": "OKLO", "settlementDate": "2026-10-30", "issueName": "Oklo Inc.", "currentShortPositionQuantity": 37873964,
     "previousShortPositionQuantity": 35934803, "averageDailyVolumeQuantity": 7753770, "daysToCoverQuantity": 4.88,
     "changePercent": 5.4},
    {"symbolCode": "OKLO", "settlementDate": "2026-11-13", "issueName": "Oklo Inc.", "currentShortPositionQuantity": 48467978,
     "previousShortPositionQuantity": 37873964, "averageDailyVolumeQuantity": 9288050, "daysToCoverQuantity": 5.22,
     "changePercent": 27.97},
    {"symbolCode": "AAPL", "settlementDate": "2026-11-13", "issueName": "Apple Inc.", "currentShortPositionQuantity": 143500000,
     "previousShortPositionQuantity": 144800000, "averageDailyVolumeQuantity": 51000000, "daysToCoverQuantity": 2.8,
     "changePercent": -0.9},
]


def test_the_daily_file_is_read_in_code_and_only_the_watchlist_is_kept():
    got = finra.parse_file(day_file(date(2026, 11, 24), {"OKLO": (1426208.969633, 3712434.76475), "BRK/B": (5, 10),
                                                         "AAPL": (1, 2)}), {"OKLO", "BRK.B"})
    assert got == [("OKLO", date(2026, 11, 24), 1426208.969633, 12.0, 3712434.76475),
                   ("BRK.B", date(2026, 11, 24), 5.0, 12.0, 10.0)]  # FINRA's BRK/B is the watchlist's BRK.B
    assert finra.parse_file(b"", {"OKLO"}) == [] and finra.norm("brk-b") == "BRK.B"


def test_first_read_fills_a_month_skips_holidays_and_sends_nothing_but_the_date(memory):
    watch(memory, "OKLO", "BRK.B", "AAPL")
    watch(memory, "BTC", market="crypto")  # not a US stock: not looked for
    files = Files(holidays={date(2026, 11, 11)})
    got = finra.pull_volume(memory, fetch=files, now=datetime(2026, 11, 25, 20, 0))
    assert got["symbols"] == 3 and got["missing"] == 1 and got["days"] == finra.BACKFILL_DAYS - 1
    assert all(u.startswith("https://cdn.finra.org/equity/regsho/daily/CNMSshvol2026") and "OKLO" not in u
               for u in files.urls)
    assert files.urls[-1].endswith("CNMSshvol20261125.txt")  # after 7 pm, today's file counts
    held = {r["symbol"] for r in pricebook.run(memory, "SELECT DISTINCT symbol FROM finra_short_volume")}
    assert held == {"OKLO", "BRK.B", "AAPL"}  # the rest of the market isn't kept
    again = Files()
    assert finra.pull_volume(memory, fetch=again, now=datetime(2026, 11, 26, 9, 0))["days"] == 0 and again.urls == []
    finra.pull_volume(memory, fetch=again, now=datetime(2026, 11, 27, 9, 0))  # before 7 pm: up to yesterday
    assert [u[-12:-4] for u in again.urls] == ["20261126"]


def test_the_days_short_share_against_the_stocks_own_average(memory):
    watch(memory, "OKLO")
    finra.pull_volume(memory, fetch=Files(latest=date(2026, 11, 25), spike=0.62), now=datetime(2026, 11, 25, 20, 0))
    oklo = finra.page(memory)["stocks"][0]
    assert oklo["short_share"] == 0.62 and oklo["usual_share"] == 0.40 and oklo["days"] == finra.AVERAGE_DAYS
    assert oklo["flags"] == ["more short selling than usual"]
    assert "62% of its off-exchange volume was short sales, against 40% on an average day over the 20 trading days " \
           "before: more short selling than usual." in oklo["reading"]


def test_short_interest_asks_once_for_the_watchlist_and_flags_what_stands_out(memory):
    watch(memory, "OKLO", "AAPL")
    files = Files()
    got = finra.pull_interest(memory, fetch=files, today=date(2026, 11, 25))
    assert got == {"rows": 3, "symbols": 2}
    url, body, headers = files.posts[0]
    assert url == finra.SI_URL and headers["Accept"] == "application/json"
    assert body["domainFilters"] == [{"fieldName": "symbolCode", "values": ["AAPL", "OKLO"]}]
    assert body["dateRangeFilters"][0]["startDate"] == (date(2026, 11, 25) - timedelta(days=120)).isoformat()
    stocks = finra.page(memory)["stocks"]
    assert [s["symbol"] for s in stocks] == ["OKLO", "AAPL"]  # the one with something to say first
    assert stocks[0]["flags"] == ["5.2 days to cover", "short interest up 28%"]
    assert stocks[0]["reading"] == ("48.5 million shares were held short on 2026-11-13 (up 28.0% from the time "
                                    "before), 5.2 days of average volume to buy back.")
    assert stocks[1]["flags"] == [] and "down 0.9%" in stocks[1]["reading"]
    # Next time from the latest settlement date; a stock FINRA has nothing for isn't asked about every day.
    watch(memory, "BRK.B")
    assert finra.interest_due(memory, datetime(2026, 11, 26, 1))  # a new stock on the watchlist
    finra.pull_interest(memory, fetch=files, today=date(2026, 11, 26))
    assert finra.pull_interest(memory, fetch=files, today=date(2026, 11, 27))["rows"] == 3
    assert files.posts[-1][1]["dateRangeFilters"][0]["startDate"] == "2026-11-12"
    assert not finra.interest_due(memory, datetime(2026, 11, 28, 1))  # BRK.B was asked; no report due yet
    assert not finra.interest_due(memory, datetime(2026, 12, 5, 1))
    assert finra.interest_due(memory, datetime(2026, 12, 6, 1))  # the 28 November one is about out


def test_nothing_without_a_watchlist_or_once_switched_off(memory):
    files = Files()
    assert "watchlist" in finra.page(memory)["empty"] and finra.nightly(memory, fetch=files) == ""
    watch(memory, "OKLO")
    gateway.set_off(memory, "finra", True)
    assert finra.pull_volume(memory, fetch=files)["note"] == "FINRA is switched off."
    assert finra.pull_interest(memory, fetch=files)["note"] == "FINRA is switched off."
    assert files.urls == [] and files.posts == [] and "switched off" in finra.page(memory)["empty"]


def test_due_once_a_day_in_the_evening_or_the_next_morning(memory):
    watch(memory, "OKLO")
    assert not finra.volume_due(memory, datetime(2026, 11, 25, 15))
    assert finra.volume_due(memory, datetime(2026, 11, 25, 19)) and finra.volume_due(memory, datetime(2026, 11, 26, 2))
    memory.set(finra.DAY_KEY, "2026-11-26")
    assert not finra.volume_due(memory, datetime(2026, 11, 26, 21))
    said = finra.nightly(memory, fetch=Files(), now=datetime(2026, 11, 27, 2))
    assert said.startswith("FINRA short sale volume:") and "short interest: 3 rows" in said


def test_finras_data_is_personal_use_shown_by_code_and_given_to_no_ai_model(memory):
    entry = gateway.CATALOG["finra"]
    assert entry["hosts"] == ["https://api.finra.org", "https://cdn.finra.org"] and entry["no_backup"]
    assert "non-commercial" in entry["used_for"] and "not given to an AI model" in entry["used_for"]
    assert "non-commercial" in finra.page(memory)["terms"]
    tools = [t for t in vars(agent).values() if isinstance(t, dict) and "input_schema" in t]
    assert tools and not any("finra" in json.dumps(t).lower() for t in tools)
    assert not any("finra" in name for names in __import__("buddy.modules", fromlist=["TOOLS"]).TOOLS.values()
                   for name in names)
