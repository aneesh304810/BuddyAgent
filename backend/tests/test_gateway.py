"""Buddy Connect: one catalog, one way to call every outside service."""

import io
import urllib.error

import pytest

from buddy import gateway
from buddy.memory import Memory


@pytest.fixture(autouse=True)
def _fresh_limits():
    gateway._recent.clear()


class Net:
    """A fake network: answers by url; an Exception (or callable) answer is raised / called."""

    def __init__(self, answers):
        self.answers, self.calls = answers, []

    def __call__(self, url, data=None, headers=None):
        self.calls.append((url, data, dict(headers or {})))
        got = self.answers[url]
        if isinstance(got, list):
            got = got.pop(0)
        if isinstance(got, BaseException):
            raise got
        return got


def _http_error(url, code, body=b""):
    return urllib.error.HTTPError(url, code, "x", {}, io.BytesIO(body))


def test_key_goes_in_the_header_and_calls_are_logged(tmp_path):
    m = Memory(tmp_path / "b.db")
    net = Net({"https://google.serper.dev/search": b'{"organic": []}'})
    got = gateway.call(m, "serper", "/search", json_body={"q": "x"}, fetch=net, env={"BUDDY_SERPER_KEY": "k"})
    assert got == {"organic": []} and net.calls[0][2]["X-API-KEY"] == "k"
    s = next(x for x in gateway.status(m, env={"BUDDY_SERPER_KEY": "k"}) if x["name"] == "serper")
    assert s["set_up"] and s["last_ok"] and s["calls_this_month"] == 1 and s["cost_this_month"] > 0


def test_missing_key_says_how_to_set_it():
    with pytest.raises(gateway.ConnectError, match="set.sh BUDDY_SERPER_KEY"):
        gateway.call(None, "serper", "/search", json_body={}, fetch=Net({}), env={})


def test_the_service_own_reason_comes_back(tmp_path):
    url = "https://google.serper.dev/search"
    net = Net({url: _http_error(url, 400, b'{"message":"Not enough credits","statusCode":400}')})
    with pytest.raises(gateway.ConnectError) as e:
        gateway.call(Memory(tmp_path / "b.db"), "serper", "/search", json_body={}, fetch=net, env={"BUDDY_SERPER_KEY": "k"})
    assert e.value.status == 400 and "Not enough credits" in str(e.value)


def test_backup_host_only_when_the_first_cant_be_reached():
    a, b = (h + "/getStationMSG" for h in gateway.CATALOG["njt_rail"]["hosts"])
    net = Net({a: urllib.error.URLError("no such host"), b: b"[]"})
    assert gateway.call(None, "njt_rail", "/getStationMSG", data=b"x", fetch=net) == []
    assert [c[0] for c in net.calls] == [a, b]


def test_a_server_hiccup_is_retried_once():
    url = "https://www.panynj.gov/bin/portauthority/ridepath.json"
    net = Net({url: [_http_error(url, 503), b'{"results": []}']})
    assert gateway.call(None, "path", "/bin/portauthority/ridepath.json", fetch=net) == {"results": []}
    assert len(net.calls) == 2


def test_personal_data_only_to_services_that_may_see_it():
    with pytest.raises(gateway.ConnectError, match="may not see personal data"):
        gateway.call(None, "serper", "/search", json_body={}, fetch=Net({}), env={"BUDDY_SERPER_KEY": "k"},
                     personal=True)


def test_rate_limit(monkeypatch):
    monkeypatch.setitem(gateway.CATALOG["mta"], "per_minute", 2)
    url = "https://api-endpoint.mta.info/x"
    net = Net({url: b"{}"})
    gateway.call(None, "mta", "/x", fetch=net)
    gateway.call(None, "mta", "/x", fetch=net)
    with pytest.raises(gateway.ConnectError, match="too many calls"):
        gateway.call(None, "mta", "/x", fetch=net)


def test_token_is_minted_once_and_reused(tmp_path):
    m = Memory(tmp_path / "b.db")
    login = gateway.CATALOG["njt_bus"]["hosts"][0] + "/authenticateUser"
    net = Net({login: b'{"Authenticated": "True", "UserToken": "t1"}'})
    env = {"BUDDY_NJT_USERNAME": "me@x.com", "BUDDY_NJT_PASSWORD": "pw"}
    assert gateway.token(m, "njt_bus", env=env, fetch=net) == "t1"
    assert gateway.token(m, "njt_bus", env=env, fetch=net) == "t1" and len(net.calls) == 1


def test_a_refused_login_says_why_and_is_not_retried_within_the_hour(tmp_path):
    m = Memory(tmp_path / "b.db")
    login = gateway.CATALOG["njt_rail"]["hosts"][0] + "/getToken"
    net = Net({login: _http_error(login, 500, b'{"errorMessage": "Missing user account."}')})
    env = {"BUDDY_NJT_USERNAME": "me", "BUDDY_NJT_PASSWORD": "pw"}
    with pytest.raises(gateway.ConnectError, match="Missing user account"):
        gateway.token(m, "njt_rail", env=env, fetch=net)
    with pytest.raises(gateway.ConnectError, match="this hour"):
        gateway.token(m, "njt_rail", env=env, fetch=net)
    assert len(net.calls) == 1


def test_every_service_is_listed_and_the_tool_reads_them(tmp_path):
    m = Memory(tmp_path / "b.db")
    names = {s["name"] for s in gateway.status(m, env={})}
    assert {"serper", "njt_rail", "njt_bus", "mta", "path", "claude", "plaid", "groq"} <= names
    said = gateway.run_tool(m, {"action": "list"})
    assert "NJ Transit trains" in said and "not set up" in said
    assert gateway.name_for("https://raildata.njtransit.com/api/TrainData/getToken") == "njt_rail"
