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
    assert "No status, gate, delay, updated times yet" in f.track("AA1234", DAY)


def test_needs_a_key():
    f = Flights(Memory(":memory:"), env={}, fetch=AirLabs())
    assert "BUDDY_AIRLABS_KEY" in f.track("AA1234", DAY)
    with pytest.raises(ToolError):
        f.lookup("AA1234")


def test_old_flights_are_deleted_after_30_days(fl):
    fl.track("AA1234", DAY)
    fl.memory.db.execute("UPDATE flight_watch SET day = '2020-01-01'")
    fl.check()
    assert fl.tracked(include_done=True) == []



def add_message(memory, kind, title, body, source):
    from buddy.connectors.hub import ConnectorHub
    ConnectorHub(memory)
    now = datetime.now(timezone.utc).isoformat()
    data = {"app_name": source} if kind == "notification" else {"from": source}
    memory.db.execute("INSERT INTO records (source, external_id, kind, title, body, start_at, data, synced_at) "
                      "VALUES (?, ?, ?, ?, ?, ?, ?, ?)", ("notifications" if kind == "notification" else "email",
                                                          f"{title}{body}", kind, title, body, now, json.dumps(data), now))


def test_airline_app_gives_the_gate_and_delay_on_a_free_plan():
    fake = AirLabs()
    fake.row = {k: v for k, v in fake.row.items() if k in ("flight_iata", "dep_iata", "arr_iata", "dep_time",
                                                             "arr_time", "dep_time_utc", "arr_time_utc")}
    f = Flights(Memory(":memory:"), env={"BUDDY_AIRLABS_KEY": "k"}, fetch=fake)
    f.track("AA1234", DAY)
    new_time = (DEP + timedelta(minutes=50)).strftime("%-I:%M %p")
    add_message(f.memory, "notification", "Flight 1234 update",
                f"Your flight 1234 to Lima now departs at {new_time} from gate D20.", "American")
    news = [a["text"] for a in f.check()]
    assert any("delayed about 50 minutes" in t and "gate D20" in t for t in news), news
    assert f.tracked()[0]["last"]["airline_said"] == ["American"]


def test_airline_email_cancellation_and_boarding():
    f = Flights(Memory(":memory:"), env={}, fetch=AirLabs())  # no keys at all: messages only
    f.track("UA 88", DAY)
    add_message(f.memory, "email", "UA88 is now boarding", "Flight UA 88 is now boarding at gate C3.", "United <n@united.com>")
    assert any("boarding at gate C3" in a["text"] for a in f.check())
    add_message(f.memory, "email", "Your flight UA88 has been cancelled", "We're sorry, flight UA88 was cancelled.",
                "United <n@united.com>")
    assert any("CANCELLED" in a["text"] for a in f.check())


def test_someone_elses_message_is_ignored():
    f = Flights(Memory(":memory:"), env={}, fetch=AirLabs())
    f.track("AA1234", DAY)
    add_message(f.memory, "notification", "Mom", "my flight 1234 gate is B5", "WhatsApp")
    assert f.from_messages("AA1234", DAY) == {}


def aero_reply():
    out = (DEP + timedelta(minutes=25)).strftime("%Y-%m-%dT%H:%M:00Z")
    return json.dumps({"flights": [{"ident_iata": "AA1234", "origin": {"code_iata": "MIA", "timezone": "UTC"},
                                     "destination": {"code_iata": "LIM", "timezone": "UTC"},
                                     "scheduled_out": DEP.strftime("%Y-%m-%dT%H:%M:00Z"), "estimated_out": out,
                                     "scheduled_in": ARR.strftime("%Y-%m-%dT%H:%M:00Z"), "gate_origin": "D7",
                                     "terminal_origin": "N", "departure_delay": 1500, "status": "Scheduled",
                                     "cancelled": False}]}).encode()


def test_flightaware_fills_what_airlabs_lacks_and_is_rationed():
    fake = AirLabs()
    fake.row = {k: v for k, v in fake.row.items() if k in ("flight_iata", "dep_iata", "arr_iata", "dep_time",
                                                             "arr_time", "dep_time_utc", "arr_time_utc")}
    calls = []

    def aero(url, headers):
        calls.append(url)
        assert headers["x-apikey"] == "fa"
        return aero_reply()
    f = Flights(Memory(":memory:"), env={"BUDDY_AIRLABS_KEY": "k", "BUDDY_AEROAPI_KEY": "fa"}, fetch=fake,
                aero_fetch=aero)
    s = f.lookup("AA1234", DAY)
    assert s["dep_gate"] == "D7" and s["dep_delayed"] == 25 and s["from_flightaware"]
    f.lookup("AA1234", DAY)
    assert len(calls) == 1  # at most every 30 minutes per flight
