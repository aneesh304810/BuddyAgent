"""Flight status (AirLabs): looked up, tracked from 4 hours before, each change said once."""

import json
from datetime import datetime, timedelta, timezone

import pytest

from buddy.flights import Flights, normal_flight
from buddy.memory import Memory
from buddy.tools import ToolError

DEP = datetime.now(timezone.utc) + timedelta(hours=2)
ARR = DEP + timedelta(hours=5)
DAY = DEP.strftime("%Y-%m-%d")
f16 = lambda d: d.strftime("%Y-%m-%d %H:%M")  # noqa: E731


class AirLabs:
    def __init__(self):
        self.row = {"flight_iata": "AA1234", "dep_iata": "MIA", "arr_iata": "LIM", "dep_time": f16(DEP),
                    "dep_time_utc": f16(DEP), "arr_time": f16(ARR), "arr_time_utc": f16(ARR), "status": "scheduled",
                    "dep_terminal": "N", "dep_gate": "D12", "dep_delayed": None}
        self.calls = []

    def __call__(self, url):
        self.calls.append(url)
        assert "api_key=k" in url
        if "/schedules" in url:
            return json.dumps({"response": [self.row]}).encode()
        return json.dumps({"response": {}}).encode()


@pytest.fixture
def fl():
    return Flights(Memory(":memory:"), env={"BUDDY_AIRLABS_KEY": "k"}, fetch=AirLabs())


def test_flight_numbers():
    assert normal_flight("aa 1234") == "AA1234" and normal_flight("B6-023") == "B623"
    with pytest.raises(ToolError):
        normal_flight("to Lima")


def test_status_in_words(fl):
    s = fl.lookup("AA 1234", DAY)
    text = fl.describe(s)
    assert text.startswith("AA1234 MIA → LIM, departs") and "terminal N gate D12" in text


def test_track_then_each_change_once(fl):
    out = fl.track("AA1234", DAY, "to Lima")
    assert "Tracking AA1234" in out and "gate D12" in out
    assert fl.check() == []  # nothing new since tracking
    fl.fetch.row.update(dep_delayed=35, dep_estimated=f16(DEP + timedelta(minutes=35)), dep_gate="D20")
    news = [a["text"] for a in fl.check()]
    assert any("delayed about 35 minutes" in t for t in news) and any("gate change: now gate D20 (was D12)" in t
                                                                      for t in news)
    assert all(t.endswith("(to Lima)") for t in news)
    assert fl.check() == []  # said already
    fl.fetch.row.update(dep_delayed=40)
    assert fl.check() == []  # 40 isn't 15 more than 30
    fl.fetch.row.update(dep_delayed=None, dep_actual=f16(DEP + timedelta(minutes=40)), status="active")
    assert [a["text"] for a in fl.check()][0].startswith("AA1234 has taken off")
    fl.fetch.row.update(status="landed", arr_actual=f16(ARR), arr_gate="12", arr_baggage="4")
    landed = [a["text"] for a in fl.check()]
    assert landed and "landed in LIM" in landed[0] and "carousel 4" in landed[0]
    assert fl.tracked() == []  # done after landing


def test_cancellation(fl):
    fl.track("AA1234", DAY)
    fl.fetch.row.update(status="cancelled")
    assert "CANCELLED" in fl.check()[0]["text"]


def test_not_checked_outside_the_window(fl):
    fl.track("AA1234", DAY)
    calls = len(fl.fetch.calls)
    assert fl.check(now=DEP - timedelta(hours=6)) == [] and len(fl.fetch.calls) == calls


def test_says_what_a_free_plan_lacks():
    fake = AirLabs()
    fake.row = {k: v for k, v in fake.row.items() if k in ("flight_iata", "dep_iata", "arr_iata", "dep_time",
                                                             "arr_time", "dep_time_utc", "arr_time_utc")}
    f = Flights(Memory(":memory:"), env={"BUDDY_AIRLABS_KEY": "k"}, fetch=fake)
    assert "doesn't give status, gate, delay, updated times" in f.track("AA1234", DAY)


def test_needs_a_key():
    f = Flights(Memory(":memory:"), env={}, fetch=AirLabs())
    assert "airlabs.co" in f.track("AA1234", DAY)
    with pytest.raises(ToolError):
        f.lookup("AA1234")


def test_old_flights_are_deleted_after_30_days(fl):
    fl.track("AA1234", DAY)
    fl.memory.db.execute("UPDATE flight_watch SET day = '2020-01-01'")
    fl.check()
    assert fl.tracked(include_done=True) == []
