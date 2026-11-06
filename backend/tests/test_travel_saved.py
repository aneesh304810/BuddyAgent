"""Saved searches: kept, re-run every morning, a deal said once."""

import asyncio
import json
from datetime import date, datetime, timedelta, timezone

import pytest

from buddy.memory import Memory
from buddy.tools import ToolError
from buddy.travel_saved import SavedSearches

DAY = (date.today() + timedelta(days=40)).isoformat()


class FakeTravel:
    """Stands in for Travel: stored searches, and live re-runs at prices the test sets."""

    def __init__(self, memory):
        self.memory = memory
        self.prices = []
        self.store = {"s1": {"id": "s1", "kind": "flights", "title": f"MIA → LIM, {DAY}",
                             "query": {"origin": "MIA", "destination": "LIM", "depart": DAY, "return": "", "adults": 1,
                                       "currency": "USD"},
                             "results": {"offers": [{"true_total": 450.0, "source": "google", "airline": "LATAM",
                                                     "summary": "nonstop"},
                                                    {"true_total": 120.0, "source": "duffel", "test": True},
                                                    {"true_total": 99.0, "source": "travelpayouts"}]}}}

    def search(self, sid):
        if sid not in self.store:
            raise ToolError("No such search.")
        return self.store[sid]

    async def flights(self, *a):
        price = self.prices.pop(0)
        return {"search_id": f"new{price}", "offers": [{"true_total": price, "source": "google", "airline": "LATAM",
                                                        "summary": "nonstop"}]}


@pytest.fixture
def saved():
    memory = Memory(":memory:")
    return SavedSearches(memory, FakeTravel(memory))


def run(saved, price):
    saved.travel.prices.append(price)
    out = asyncio.run(saved.check(force=True))
    for a in out:
        saved.told(a)
    return out


def test_save_uses_the_real_cheapest_price(saved):
    item = saved.save("s1", target=380)
    assert item["baseline"] == 450.0  # not the Duffel test fare or a cached hint
    assert "$450 now" in item["text"] and "$380 or less" in item["text"]
    assert saved.save("s1", target=360)["id"] == item["id"]  # saving again updates, not duplicates


def test_alerts_on_a_drop_once_then_again_only_lower(saved):
    saved.save("s1")
    assert run(saved, 445.0) == []  # under 5%: nothing
    first = run(saved, 410.0)
    assert len(first) == 1 and "down $40 (9%)" in first[0]["text"] and first[0]["search_id"] == "new410.0"
    assert run(saved, 405.0) == []  # same deal, said already
    again = run(saved, 380.0)
    assert len(again) == 1 and "lowest since you saved it (was $405)" in again[0]["text"]
    s = saved.saved()[0]
    assert s["best"] == 380.0 and s["last"] == 380.0 and s["change_pct"] == -15.6
    assert s["search_id"] == "new380.0"


def test_target_price(saved):
    saved.save("s1", target=440)
    out = run(saved, 438.0)
    assert len(out) == 1 and "at or under your $440 target" in out[0]["text"]


def test_checked_once_a_day_and_ended_after_the_trip(saved):
    saved.save("s1")
    assert asyncio.run(saved.check()) == []  # just saved: not due yet
    assert saved.travel.prices == []
    saved.memory.db.execute("UPDATE travel_saved SET query = ?", (json.dumps(
        {**saved.travel.store["s1"]["query"], "depart": (date.today() - timedelta(days=1)).isoformat()}),))
    asyncio.run(saved.check(force=True))
    assert saved.saved() == []


def test_limits(saved):
    with pytest.raises(ToolError):
        saved.save("nope")
    saved.travel.store["s1"]["results"]["offers"] = []
    with pytest.raises(ToolError):
        saved.save("s1")
