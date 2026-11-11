"""The Oura Ring through Buddy Connect: each user connects their own account (a sign-in link with a one-time state,
back to this server's own page); Oura's one-use refresh tokens are renewed and the newest kept; each day's scores,
sleep, HRV, temperature and workouts become one row; a kind the link may not read is skipped."""

import io
import json
import urllib.error
import urllib.parse

import pytest

from buddy import gateway, oura
from buddy.memory import Memory
from buddy.tools import local_now

ENV = {"BUDDY_OURA_CLIENT_ID": "cid", "BUDDY_OURA_CLIENT_SECRET": "sec"}
BACK = "https://buddy.tail1.ts.net/connect/oura/linked"
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
            if form["grant_type"] == "authorization_code":
                assert form["code"] == "c0de" and form["redirect_uri"] == BACK
                form["refresh_token"] = "r0"
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


def _connect(m, net):
    m.set("server:base_url", "https://buddy.tail1.ts.net")
    url = oura.start_link(m, env=ENV)["url"]
    q = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(url).query))
    assert url.startswith(oura.AUTHORIZE_URL) and q["redirect_uri"] == BACK and q["client_id"] == "cid"
    return oura.finish_link(m, "c0de", q["state"], env=ENV, fetch=net)


def test_connect_signs_in_once_with_the_state_and_keeps_the_tokens(tmp_path):
    m = Memory(tmp_path / "b.db")
    net = Oura()
    assert not oura.linked(m, ENV)
    with pytest.raises(gateway.ConnectError, match="Connect"):
        oura.access_token(m, env=ENV, fetch=net)
    assert _connect(m, net) == {"linked": True} and oura.linked(m, ENV)
    with pytest.raises(gateway.ConnectError, match="expired or was already used"):  # the state works once
        oura.finish_link(m, "c0de", "anything", env=ENV, fetch=net)
    st = {r["name"]: r for r in gateway.status(m, ENV)}["oura"]
    assert st["can_link"] and st["linked"] and st["set_up"]


def test_a_wrong_state_is_refused(tmp_path):
    m = Memory(tmp_path / "b.db")
    m.set("server:base_url", "https://buddy.tail1.ts.net")
    oura.start_link(m, env=ENV)
    with pytest.raises(gateway.ConnectError, match="expired"):
        oura.finish_link(m, "c0de", "forged", env=ENV, fetch=Oura())
    assert not oura.linked(m, ENV)


def test_refresh_tokens_rotate_and_the_newest_is_kept(tmp_path):
    m = Memory(tmp_path / "b.db")
    net = Oura()
    _connect(m, net)  # the code gives r0 and a1... renewals rotate from there
    assert json.loads(m.get(oura.TOKENS_KEY))["refresh"] == "r1"
    assert oura.access_token(m, env=ENV, fetch=net) == "a1"  # still valid: no renewal
    assert oura.access_token(m, env=ENV, fetch=net, fresh=True) == "a2"  # renewed with r1, not the used r0
    assert json.loads(m.get(oura.TOKENS_KEY))["refresh"] == "r2"


def test_a_refused_link_says_to_connect_again(tmp_path):
    m = Memory(tmp_path / "b.db")
    net = Oura()
    _connect(m, net)
    net.valid = set()
    with pytest.raises(gateway.ConnectError, match="Connect again"):
        oura.access_token(m, env=ENV, fetch=net, fresh=True)


def test_not_connected_shows_as_connect_in_the_app(tmp_path):
    m = Memory(tmp_path / "b.db")
    st = {r["name"]: r for r in gateway.status(m, ENV)}["oura"]
    assert st["can_link"] and not st["linked"] and not st["set_up"] and st["setup"] == ""
    st = {r["name"]: r for r in gateway.status(m, {})}["oura"]  # not offered on this server yet
    assert not st.get("can_link") and st["setup"] == "sudo bash /opt/buddy/deploy/oura.sh"


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
    _connect(m, net)
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
    net = Oura()
    _connect(m, net)
    assert oura.check(m, env=ENV, fetch=net)["weight"] == 78.0
    assert oura.profile(m) == {"age": 41, "weight": 78.0, "height": 1.78, "biological_sex": "male"}  # no email


def test_oura_is_a_personal_health_service_in_buddy_connect():
    e = gateway.CATALOG["oura"]
    assert e["group"] == "Health" and e["privacy"] == "personal"
    assert gateway.configured("oura", ENV) and not gateway.configured("oura", {})
    assert gateway.setup_hint("oura") == "sudo bash /opt/buddy/deploy/oura.sh"


def test_sync_if_due_waits_and_never_raises(tmp_path):
    m = Memory(tmp_path / "b.db")
    net = Oura(DATA)
    assert not oura.sync_if_due(m, env=ENV, fetch=net)  # not connected yet
    _connect(m, net)
    assert oura.sync_if_due(m, env=ENV, fetch=net)
    assert not oura.sync_if_due(m, env=ENV, fetch=net)  # synced a moment ago

    def down(*a, **k):
        raise OSError("down")
    m.set(oura.SYNC_KEY, "")
    assert not oura.sync_if_due(m, env=ENV, fetch=down)


def test_the_return_page_connects_and_hides_the_code_in_logs(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from buddy import server
    m = Memory(tmp_path / "b.db")
    m.set("server:base_url", "https://buddy.tail1.ts.net")
    for k, v in ENV.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr(server, "memory", m)
    net = Oura()
    monkeypatch.setattr(gateway, "_transport", net)
    state = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(oura.start_link(m)["url"]).query))["state"]
    monkeypatch.setattr(oura, "sync_if_due", lambda *a, **k: False)
    client = TestClient(server.app)
    bad = client.get("/connect/oura/linked", params={"code": "c0de", "state": "forged"})
    assert bad.status_code == 400 and "expired" in bad.text
    state = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(oura.start_link(m)["url"]).query))["state"]
    ok = client.get("/connect/oura/linked", params={"code": "c0de", "state": state})
    assert ok.status_code == 200 and "Oura Ring is connected" in ok.text and oura.linked(m)
    assert client.get("/connect/oura/linked", params={"error": "access_denied"}).status_code == 400
    assert server._HideTokens.TOKEN.sub(r"\1***", "/connect/oura/linked?code=abc&state=xyz") == \
        "/connect/oura/linked?code=***&state=***"
