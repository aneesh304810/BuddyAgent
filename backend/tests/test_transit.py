"""Transit alerts: MTA, NJ Transit and PATH, for the lines the user rides, said once when they start and clear."""

import json
import time
from datetime import datetime

import pytest

from buddy import transit
from buddy.memory import Memory
from buddy.tools import ToolError
from buddy.transit import Transit, mta_route, provider_of

NOW = time.time()


def mta_feed(*alerts):
    return {"entity": [{"id": f"lmm:alert:{i}", "alert": {
        "active_period": [{"start": NOW - 60}], "informed_entity": [{"agency_id": agency, "route_id": route}],
        "header_text": {"translation": [{"text": text, "language": "en"}]},
        "transit_realtime.mercury_alert": {"alert_type": kind}}} for i, (agency, route, kind, text) in enumerate(alerts)]}


class Feeds:
    def __init__(self):
        self.mta = mta_feed(("MTASBWY", "Q", "Delays", "Uptown [Q] trains are running with delays."),
                            ("MTASBWY", "1", "Reduced Service", "[1] runs every 8 minutes"),
                            ("LI", "1", "Delays", "Port Washington Branch trains are delayed 10 minutes."))
        self.njt = [{"MSG_ID": "77", "MSG_TYPE": "Delay", "MSG_TEXT": "Hudson-Bergen Light Rail: service suspended "
                     "between Hoboken and 9th St due to a signal problem.", "MSG_LINE_SCOPE": "Hudson-Bergen Light Rail"},
                    {"MSG_ID": "78", "MSG_TEXT": "Northeast Corridor trains 15 minutes late.", "MSG_LINE_SCOPE": "NEC"}]
        self.calls = []

    def __call__(self, url, data=None, headers=None):
        self.calls.append(url)
        if "mta.info" in url:
            return json.dumps(self.mta).encode()
        if url.endswith("getToken"):
            assert b'name="username"' in data and "multipart/form-data" in headers["Content-Type"]
            return b'{"Authenticated": "True", "UserToken": "tok"}'
        if url.endswith("getStationMSG"):
            assert b"tok" in data
            return json.dumps(self.njt).encode()
        if "ridepath" in url:
            return json.dumps({"results": [{"consideredStation": "JSQ", "destinations": [{"label": "ToNY", "messages": [
                {"headSign": "33rd Street", "arrivalTimeMessage": "4 min"}]}]}]}).encode()
        return b'{"status": "Success", "data": []}'


@pytest.fixture
def t():
    return Transit(Memory(":memory:"), env={"BUDDY_NJT_USERNAME": "me", "BUDDY_NJT_PASSWORD": "pw"}, fetch=Feeds())


def test_lines_are_understood():
    assert provider_of("Hudson-Bergen Light Rail") == "njt" and provider_of("NJ Transit Northeast Corridor") == "njt"
    assert provider_of("PATH Newark-WTC") == "path" and provider_of("Q train") == "mta"
    assert (mta_route("the Q train"), mta_route("7"), mta_route("M15 bus"), mta_route("LIRR Port Washington")) == \
        ("Q", "7", "M15", "")


def test_status_from_each_feed(t):
    for line, station in (("Q train", ""), ("LIRR Port Washington", ""), ("Hudson-Bergen Light Rail", ""),
                          ("PATH Journal Square-33rd St", "Journal Square")):
        t.watch(line, station=station, days="daily")
    lines = {x["line"]: x for x in t.status()["lines"]}
    assert [a["text"] for a in lines["Q train"]["alerts"]] == ["Uptown [Q] trains are running with delays."]
    assert lines["Q train"]["ok"] is False  # the [1] train's alert isn't the Q's
    assert "Port Washington" in lines["LIRR Port Washington"]["alerts"][0]["text"]
    assert [a["key"] for a in lines["Hudson-Bergen Light Rail"]["alerts"]] == ["njt:77"]  # not the NEC message
    assert lines["PATH Journal Square-33rd St"]["next_trains"] == [{"to": "33rd Street", "in": "4 min", "direction": "ToNY"}]
    assert lines["PATH Journal Square-33rd St"]["ok"] is True


def test_alerts_said_once_and_again_when_cleared(t):
    t.watch("Q", days="daily")
    first = t.check()
    assert [a["kind"] for a in first] == ["start"] and first[0]["text"].startswith("Q: Uptown [Q]")
    t.told(first[0])
    assert t.check() == []  # still the same alert: nothing new
    t.fetch.mta = mta_feed()  # the delay is over
    t._cache.clear()
    cleared = t.check()
    assert [a["kind"] for a in cleared] == ["clear"] and "cleared" in cleared[0]["text"]
    t.told(cleared[0])
    assert t.check() == []


def test_only_checked_in_the_commute_window(t):
    t.watch("Q", days="weekdays", times=["8-9:30"])
    monday_8 = datetime(2026, 11, 9, 8, 15)
    assert t.active(t.watches()[0], monday_8)
    assert not t.active(t.watches()[0], datetime(2026, 11, 9, 10, 0))
    assert not t.active(t.watches()[0], datetime(2026, 11, 8, 8, 15))  # Sunday
    assert t.check(datetime(2026, 11, 9, 12, 0)) == []
    assert t.due(monday_8) and not t.due(monday_8)  # once per five minutes


def test_njt_needs_its_developer_account_and_reuses_the_token():
    no_keys = Transit(Memory(":memory:"), env={}, fetch=Feeds())
    assert "developer.njtransit.com" in no_keys.watch("River LINE")
    with pytest.raises(ToolError):
        no_keys.njt_token()
    t = Transit(Memory(":memory:"), env={"BUDDY_NJT_USERNAME": "me", "BUDDY_NJT_PASSWORD": "pw"}, fetch=Feeds())
    t.njt_messages()
    t._cache.clear()
    t.njt_messages()
    assert sum(u.endswith("getToken") for u in t.fetch.calls) == 1


def test_bad_times_are_refused(t):
    with pytest.raises(ToolError):
        t.watch("Q", times=["morning"])


def test_status_for_an_unwatched_line(t):
    out = t.status("Q")
    assert out["lines"][0]["line"] == "Q" and out["lines"][0]["alerts"]
