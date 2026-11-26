"""The owner's WHOOP band: linked like the Oura Ring (a one-time state), read page by page from WHOOP's v2 API,
one row a day (recovery and sleep on the day you woke, strain on the day it began), kept with their source."""

import json
import urllib.parse

import pytest

from buddy import coach, connecthub, gateway, healthrecords, whoop
from buddy.memory import Memory
from buddy.tools import local_now

ENV = {"BUDDY_WHOOP_CLIENT_ID": "wid", "BUDDY_WHOOP_CLIENT_SECRET": "wsec"}
TODAY = local_now().date().isoformat()


@pytest.fixture(autouse=True)
def _fresh_limits():
    gateway._recent.clear()


DATA = {
    "/v2/activity/sleep": [
        {"id": "s1", "start": f"{TODAY}T04:10:00.000Z", "end": f"{TODAY}T11:40:00.000Z", "timezone_offset": "-05:00",
         "nap": False, "score_state": "SCORED", "score": {
             "stage_summary": {"total_light_sleep_time_milli": 12_000_000, "total_slow_wave_sleep_time_milli": 5_400_000,
                               "total_rem_sleep_time_milli": 6_000_000},
             "respiratory_rate": 14.6, "sleep_performance_percentage": 91, "sleep_efficiency_percentage": 93.5}},
        {"id": "nap", "start": f"{TODAY}T19:00:00.000Z", "end": f"{TODAY}T19:30:00.000Z", "nap": True,
         "score_state": "SCORED", "score": {}}],
    "/v2/recovery": [{"cycle_id": 1, "sleep_id": "s1", "created_at": f"{TODAY}T11:45:00.000Z", "score_state": "SCORED",
                      "score": {"recovery_score": 68, "resting_heart_rate": 52, "hrv_rmssd_milli": 61.3,
                                "spo2_percentage": 96.5, "skin_temp_celsius": 33.4}}],
    "/v2/cycle": [{"id": 1, "start": f"{TODAY}T11:40:00.000Z", "timezone_offset": "-05:00", "score_state": "SCORED",
                   "score": {"strain": 11.2, "kilojoule": 9205, "average_heart_rate": 66, "max_heart_rate": 151}}],
    "/v2/activity/workout": [{"id": "w1", "start": f"{TODAY}T12:00:00.000Z", "end": f"{TODAY}T12:45:00.000Z",
                              "timezone_offset": "-05:00", "sport_name": "running", "score": {"strain": 9.8}}],
}


class Whoop:
    def __init__(self, data=None):
        self.data, self.calls, self.forms = data or DATA, [], []

    def __call__(self, url, data=None, headers=None):
        self.calls.append(url)
        if url == whoop.TOKEN_URL:
            form = dict(urllib.parse.parse_qsl(data.decode()))
            self.forms.append(form)
            assert form["client_id"] == "wid" and form["client_secret"] == "wsec"
            return {"access_token": f"a{len(self.forms)}", "refresh_token": f"r{len(self.forms)}", "expires_in": 3600}
        path = urllib.parse.urlparse(url).path.removeprefix("/developer")
        q = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(url).query))
        assert headers["Authorization"].startswith("Bearer a") and q.get("limit", "25") == "25" or path.endswith("basic")
        records = self.data.get(path, [])
        if q.get("nextToken"):
            return {"records": records[1:], "next_token": None}
        return {"records": records[:1], "next_token": "p2" if len(records) > 1 else None}


def _connect(m, net):
    m.set("server:base_url", "https://buddy.tail1.ts.net")
    url = whoop.start_link(m, env=ENV)["url"]
    q = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(url).query))
    assert url.startswith(whoop.AUTHORIZE_URL) and "offline" in q["scope"] and "read:recovery" in q["scope"]
    assert q["redirect_uri"] == "https://buddy.tail1.ts.net/connect/whoop/linked"
    return whoop.finish_link(m, "c0de", q["state"], env=ENV, fetch=net)


def test_connect_and_one_row_a_day_from_the_four_kinds(tmp_path):
    m = Memory(tmp_path / "b.db")
    net = Whoop()
    assert _connect(m, net) == {"linked": True} and whoop.linked(m, ENV)
    assert whoop.sync(m, env=ENV, fetch=net) == 1
    d = [r for r in whoop.days(m, 2) if r["day"] == TODAY][0]  # TODAY was fixed at import: a run past midnight
    assert (d["recovery"], d["resting_hr"], d["hrv"], d["skin_temp"]) == (68, 52, 61.3, 33.4)
    assert d["sleep_minutes"] == 390 and d["deep_minutes"] == 90 and d["rem_minutes"] == 100  # the nap left out
    assert d["strain"] == 11.2 and d["calories"] == 2200 and d["max_hr"] == 151 and d["respiratory_rate"] == 14.6
    assert d["workouts"] == [{"type": "running", "minutes": 45, "strain": 9.8}]
    rec = m.db.execute("SELECT * FROM health_records WHERE connector = 'whoop' AND measure = 'recovery'").fetchone()
    assert rec["quality"] == "estimated" and rec["source_ref"] == f"whoop_daily:{TODAY}"
    assert connecthub.history(m, "whoop")[0]["kind"] == "initial"
    assert any("nextToken=p2" in u for u in net.calls)  # the second page was read


def test_the_health_plan_reads_whoop_when_there_is_no_ring(tmp_path):
    m = Memory(tmp_path / "b.db")
    net = Whoop()
    _connect(m, net)
    whoop.sync(m, env=ENV, fetch=net)
    day = coach._data(m, 2)[TODAY]
    assert day["recovery"] == 68 and day["hrv"] == 61.3 and day["sleep_minutes"] == 390


def test_a_refused_refresh_shows_sign_in_again(tmp_path):
    m = Memory(tmp_path / "b.db")
    net = Whoop()
    _connect(m, net)

    def refuse(url, data=None, headers=None):
        raise gateway.ConnectError("whoop", "WHOOP said HTTP 400", 400, "invalid_grant")
    with pytest.raises(gateway.ConnectError):
        whoop.access_token(m, env=ENV, fetch=refuse, fresh=True)
    assert {r["name"]: r for r in gateway.status(m, ENV)}["whoop"]["state"] == "expired"


def test_disconnect_and_delete(tmp_path):
    m = Memory(tmp_path / "b.db")
    net = Whoop()
    _connect(m, net)
    whoop.sync(m, env=ENV, fetch=net)
    assert connecthub.disconnect(m, "whoop").startswith("WHOOP is disconnected from Buddy.")
    assert not whoop.linked(m, ENV) and whoop.days(m, 1) == [] and healthrecords.forget(m, "whoop") == 0


def test_whoop_is_a_personal_health_service():
    e = gateway.CATALOG["whoop"]
    assert e["privacy"] == "personal" and e["link"] == "whoop" and e["setup_command"].endswith("whoop.sh")
    assert gateway.configured("whoop", ENV) and not gateway.configured("whoop", {})
