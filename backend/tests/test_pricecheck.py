"""The price cross-check: a second source checks a few kept prices a day and says where the two disagree."""

import json
import urllib.error
from datetime import date, datetime, timedelta

import pytest

from buddy import pricebook, pricecheck, pricestore, stratrepo
from buddy.connectors.hub import ConnectorHub
from buddy.memory import Memory

TODAY = date(2026, 11, 17)
ENV = {"BUDDY_TIINGO_KEY": "tk"}


@pytest.fixture
def memory(tmp_path):
    m = Memory(tmp_path / "buddy.db")
    ConnectorHub(m)
    yield m
    pricebook.close_all()


def bars(n: int = 40, start: float = 100.0) -> list[dict]:
    return [{"d": (TODAY - timedelta(days=n - 1 - i)).isoformat(), "o": start + i, "h": start + i, "l": start + i,
             "c": start + i, "v": 1000} for i in range(n)]


def second(ours: list[dict], change: dict | None = None, split: str = "") -> list[dict]:
    """The second source's rows for the same days, optionally with one day's close changed or a split marked."""
    return [{"date": b["d"] + "T00:00:00.000Z", "close": (change or {}).get(b["d"], b["c"]),
             "splitFactor": 2.0 if b["d"] == split else 1.0} for b in ours]


def test_a_symbol_agrees_differs_or_is_not_compared():
    ours = bars()
    assert pricecheck.compare(ours, second(ours)) == {"state": "agrees", "days": 39, "worst": 0.0, "day": ours[0]["d"],
                                                      "ours": 100.0, "theirs": 100.0, "note": ""}
    off = pricecheck.compare(ours, second(ours, {ours[10]["d"]: 121.0}))
    assert off["state"] == "differs" and off["day"] == ours[10]["d"] and (off["ours"], off["theirs"]) == (110.0, 121.0)
    assert pricecheck.compare(ours, second(ours, {ours[-1]["d"]: 999.0}))["state"] == "agrees"   # the newest day is left out
    assert pricecheck.compare(ours, second(ours, {ours[5]["d"]: 105.5}))["state"] == "agrees"    # within 1%
    assert pricecheck.compare(ours, second(ours, split=ours[20]["d"])) == {
        "state": "not compared", "days": 0, "note": "its shares split inside the window"}
    assert pricecheck.compare(ours, second(ours)[:3])["note"] == "fewer than five days in common"


def test_nothing_is_sent_until_the_key_is_set(memory):
    pricestore.keep(memory, {"AAA": bars()}, "test")
    asked = []
    got = pricecheck.daily(memory, env={}, fetch=lambda *a: asked.append(a), today=TODAY)
    assert got["checked"] == 0 and asked == []
    assert pricecheck.status(memory, env={})["set_up"] is False and "BUDDY_TIINGO_KEY" in pricecheck.status(memory, env={})["note"]
    assert not pricecheck.due(memory, datetime(2026, 11, 17, 11, 0), env={})
    assert pricecheck.due(memory, datetime(2026, 11, 17, 11, 0), env=ENV) and not pricecheck.due(memory, datetime(2026, 11, 17, 9, 0), env=ENV)


def test_the_daily_check_goes_round_the_list_and_records_what_it_finds(memory, monkeypatch):
    monkeypatch.setattr(pricecheck, "CHECK_A_DAY", 3)
    kept = {s: bars(start=100.0 + 50 * i) for i, s in enumerate(["AAA", "BBB", "BRK.B", "DDD", "EEE"])}
    pricestore.keep(memory, kept, "test")
    asked = []

    def fetch(url, data=None, headers=None):
        asked.append((url, (headers or {}).get("Authorization")))
        symbol = url.split("/tiingo/daily/")[1].split("/")[0]
        if symbol == "BBB":
            return json.dumps(second(kept["BBB"], {kept["BBB"][25]["d"]: 200.0})).encode()
        if symbol == "DDD":
            raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)
        if symbol == "EEE":
            raise urllib.error.HTTPError(url, 429, "Too Many Requests", {}, None)
        return json.dumps(second(kept["BRK.B" if symbol == "BRK-B" else symbol])).encode()

    first = pricecheck.daily(memory, env=ENV, fetch=fetch, today=TODAY)
    assert first == {"checked": 3, "agree": 2, "differ": 1, "skipped": 0,
                     "differing": [{"symbol": "BBB", "day": kept["BBB"][25]["d"], "ours": 175.0, "theirs": 200.0, "by": 0.125}]}
    assert [a[0].split("/tiingo/daily/")[1].split("/")[0] for a in asked] == ["AAA", "BBB", "BRK-B"]   # the share class, their way
    assert all(a[1] == "Token tk" for a in asked) and "startDate=" in asked[0][0] and "tk" not in asked[0][0]
    said = pricecheck.message(first)
    assert "BBB on " in said and "175.00 against 200.00" in said and "Nothing was changed" in said
    next_day = pricecheck.daily(memory, env=ENV, fetch=fetch, today=TODAY + timedelta(days=1))
    assert next_day["checked"] == 1 and next_day["skipped"] == 1          # DDD: not there; then EEE: limit reached, stop
    status = pricecheck.status(memory, env=ENV)
    assert (status["agree"], status["differ"], status["not_compared"]) == (2, 1, 1) and status["differing"][0]["symbol"] == "BBB"
    assert status["last"] == (TODAY + timedelta(days=1)).isoformat()
    row = pricebook.run(memory, "SELECT state, note FROM price_checks WHERE symbol = 'DDD'")[0]
    assert row == {"state": "not compared", "note": "the second source has no prices for it"}
    assert pricebook.run(memory, "SELECT 1 FROM price_checks WHERE symbol = 'EEE'") == []            # tried again tomorrow
    assert pricecheck.message({"differing": []}) == ""
    assert pricestore.load(memory, ["BBB"], "test", 60)["BBB"][25]["c"] == 175.0                      # the kept price is untouched
    assert stratrepo.view(memory)["prices"]["check"]["a_day"] == 3
