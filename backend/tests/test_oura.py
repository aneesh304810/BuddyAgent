"""The Oura Ring through Buddy Connect: Oura's one-use refresh tokens are renewed and the newest kept; each day's
scores, sleep, HRV, temperature and workouts become one row; a kind the link may not read is skipped."""

import io
import json
import urllib.error
import urllib.parse

import pytest

from buddy import gateway, oura
from buddy.memory import Memory
from buddy.tools import local_now

ENV = {"BUDDY_OURA_CLIENT_ID": "cid", "BUDDY_OURA_CLIENT_SECRET": "sec", "BUDDY_OURA_REFRESH_TOKEN": "r0"}
TODAY = local_now().date().isoformat()


@pytest.fixture(autouse=True)
def _fresh_limits():
    gateway._recent.clear()


class Oura:
    """A fake Oura: rotates refresh tokens (each works once) and serves collections."""

    def __init__(self, data=None, refuse=()):
        self.data, self.refuse = data or {}, set(refuse)
        self.valid, self.n, self.calls = {"r0"}, 0, []

    def __call__(self, url, data=None, headers=None):
        self.calls.append(url)
        if url == oura.TOKEN_URL:
            form = dict(urllib.parse.parse_qsl(data.decode()))
            assert form["client_id"] == "cid" and form["client_secret"] == "sec"
            if form["refresh_token"] not in self.valid:
                raise urllib.error.HTTPError(url, 400, "x", {}, io.BytesIO(b'{"error": "invalid_grant"}'))
            self.valid.discard(form["refresh_token"])
            self.n += 1
            self.valid.add(f"r{self.n}")
            return {"access_token": f"a{self.n}", "refresh_token": f"r{self.n}", "expires_in": 86400}
        kind = urllib.parse.urlparse(url).path.rsplit("/", 1)[-1]
        assert headers["Authorization"].startswith("Bearer a")
        if kind in self.refuse:
            raise urllib.error.HTTPError(url, 401, "x", {}, io.BytesIO(b'{"detail": "scope"}'))
        if kind == "personal_info":
            return {"age": 41, "weight": 78.0, "height": 1.78, "biological_sex": "male", "email": "x"}
        return {"data": self.data.get(kind, []), "next_token": None}


def test_refresh_tokens_rotate_and_the_newest_is_kept(tmp_path):
    m = Memory(tmp_path / "b.db")
    net = Oura()
    assert oura.access_token(m, env=ENV, fetch=net) == "a1"
    assert json.loads(m.get(oura.TOKENS_KEY))["refresh"] == "r1"
    assert oura.access_token(m, env=ENV, fetch=net) == "a1"  # still valid: no renewal
    assert oura.access_token(m, env=ENV, fetch=net, fresh=True) == "a2"  # renewed with r1, not the used r0
    # oura.sh run again: a new link replaces the saved tokens
    net.valid.add("new")
    assert oura.access_token(m, env={**ENV, "BUDDY_OURA_REFRESH_TOKEN": "new"}, fetch=net) == "a3"


def test_a_refused_link_says_to_link_again(tmp_path):
    m = Memory(tmp_path / "b.db")
    net = Oura()
    net.valid = set()
    with pytest.raises(gateway.ConnectError, match="oura.sh"):
        oura.access_token(m, env=ENV, fetch=net)
    with pytest.raises(gateway.ConnectError, match="isn't linked"):
        oura.access_token(m, env={}, fetch=net)


DATA = {
    "daily_readiness": [{"day": TODAY, "score": 64, "temperature_deviation": 0.6}],
    "daily_sleep": [{"day": TODAY, "score": 71}],
    "daily_activity": [{"day": TODAY, "score": 80, "steps": 6400, "active_calories": 350, "total_calories": 2300}],
    "sleep": [{"day": TODAY, "type": "long_sleep", "total_sleep_duration": 6.5 * 3600, "deep_sleep_duration": 3600,
               "rem_sleep_duration": 5400, "efficiency": 88, "average_hrv": 38, "lowest_heart_rate": 52,
               "average_heart_rate": 58.4, "bedtime_start": f"{TODAY}T23:41:00-05:00",
               "bedtime_end": f"{TODAY}T06:50:00-05:00"},
              {"day": TODAY, "type": "late_nap", "total_sleep_duration": 1200}],
    "daily_stress": [{"day": TODAY, "stress_high": 3600, "recovery_high": 1800}],
    "workout": [{"day": TODAY, "activity": "running", "start_datetime": f"{TODAY}T07:00:00-05:00",
                 "end_datetime": f"{TODAY}T07:35:00-05:00", "intensity": "moderate", "calories": 320.4}],
}


def test_sync_makes_one_row_a_day_and_skips_what_it_may_not_read(tmp_path):
    m = Memory(tmp_path / "b.db")
    net = Oura(DATA, refuse={"daily_resilience", "daily_spo2"})
    assert oura.sync(m, env=ENV, fetch=net) == 1
    d = oura.days(m, 1)[0]
    assert d["readiness"] == 64 and d["sleep_score"] == 71 and d["steps"] == 6400
    assert d["sleep_minutes"] == 390 and d["deep_minutes"] == 60 and d["hrv"] == 38 and d["lowest_hr"] == 52
    assert d["bedtime"] == "23:41" and d["wake"] == "06:50" and d["temp_deviation"] == 0.6
    assert d["stress_minutes"] == 60 and d["resilience"] is None
    assert d["workouts"] == [{"type": "running", "minutes": 35, "intensity": "moderate", "calories": 320}]
    s = oura.summary(m, 7)
    assert s["today"]["readiness"] == 64 and s["avg_steps"] == 6400


def test_test_button_keeps_the_profile_for_targets(tmp_path):
    m = Memory(tmp_path / "b.db")
    assert oura.check(m, env=ENV, fetch=Oura())["weight"] == 78.0
    assert oura.profile(m) == {"age": 41, "weight": 78.0, "height": 1.78, "biological_sex": "male"}  # no email


def test_oura_is_a_personal_health_service_in_buddy_connect():
    e = gateway.CATALOG["oura"]
    assert e["group"] == "Health" and e["privacy"] == "personal"
    assert gateway.configured("oura", ENV) and not gateway.configured("oura", {})
    assert gateway.setup_hint("oura") == "sudo bash /opt/buddy/deploy/oura.sh"


def test_sync_if_due_waits_and_never_raises(tmp_path):
    m = Memory(tmp_path / "b.db")
    assert not oura.sync_if_due(m, env={})
    net = Oura(DATA)
    assert oura.sync_if_due(m, env=ENV, fetch=net)
    assert not oura.sync_if_due(m, env=ENV, fetch=net)  # synced a moment ago

    def down(*a, **k):
        raise OSError("down")
    m.set(oura.SYNC_KEY, "")
    assert not oura.sync_if_due(m, env=ENV, fetch=down)
