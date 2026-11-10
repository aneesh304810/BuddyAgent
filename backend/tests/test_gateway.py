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


def test_flights_go_through_connect_and_the_key_is_never_logged(tmp_path):
    from buddy.flights import Flights
    m = Memory(tmp_path / "b.db")

    def fetch(url):
        return b'{"error": {"code": "wrong_api_key"}}'
    f = Flights(m, env={"BUDDY_AIRLABS_KEY": "secret-key"}, fetch=fetch)
    with pytest.raises(Exception):
        f._get("schedules", flight_iata="AA1")
    rows = [dict(r) for r in m.db.execute("SELECT provider, endpoint FROM api_calls")]
    assert rows and rows[0]["provider"] == "airlabs" and "secret-key" not in str(rows)
    s = next(x for x in gateway.status(m, env={"BUDDY_AIRLABS_KEY": "k"}) if x["name"] == "airlabs")
    assert s["last_ok"] is False and "wrong_api_key" in s["last_error"]


def test_weather_goes_through_connect(tmp_path):
    from buddy import weather
    m = Memory(tmp_path / "b.db")
    weather._cache.clear()
    w = weather.Weather(m, fetch=lambda url: b'{"current": {"temperature_2m": 12}}')
    assert w._get(weather.FORECAST + "latitude=1&longitude=2")["current"]["temperature_2m"] == 12
    assert m.db.execute("SELECT provider FROM api_calls").fetchone()["provider"] == "openmeteo"


def test_markets_reddit_and_alpaca_are_logged_under_their_own_names(tmp_path):
    from buddy.markets import Data
    m = Memory(tmp_path / "b.db")
    d = Data(m, env={}, fetch=lambda url, headers=None, data=None: b'{"ok": 1}')
    assert d._json("alpaca", "https://data.alpaca.markets/v2/stocks/snapshots?symbols=SPY") == {"ok": 1}
    assert d._json("reddit", "https://www.reddit.com/r/stocks/search.json?q=x") == {"ok": 1}
    names = [r["provider"] for r in m.db.execute("SELECT provider FROM api_calls ORDER BY id")]
    assert names == ["alpaca", "reddit"]


def test_free_models_report_into_the_status(tmp_path):
    from buddy import free_models
    m = Memory(tmp_path / "b.db")
    free_models._report(m, "github", False, "HTTP 200: OK")
    free_models._report(m, "groq", True)
    st = {s["name"]: s for s in gateway.status(m, env={})}
    assert st["github_models"]["last_ok"] is False and "HTTP 200" in st["github_models"]["last_error"]
    assert st["groq"]["last_ok"] is True and st["groq"]["in_connect"]


def test_status_says_how_to_set_up_and_what_can_be_tested(tmp_path):
    st = {s["name"]: s for s in gateway.status(Memory(tmp_path / "b.db"), env={})}
    assert st["njt_rail"]["setup"] == ("sudo bash /opt/buddy/deploy/set.sh BUDDY_NJT_USERNAME; "
                                       "sudo bash /opt/buddy/deploy/set.sh BUDDY_NJT_PASSWORD")
    assert st["mta"]["setup"] == "" and st["mta"]["can_test"] and not st["reddit"]["can_test"]


def test_status_uses_the_real_last_call_from_the_logs(tmp_path):
    from buddy import usage
    from buddy.travel import log_call
    m = Memory(tmp_path / "b.db")
    gateway._table(m)
    log_call(m, "github", "/search/repositories", True, 0.2, "trends", cost=0.0)  # before Buddy Connect noted any
    usage.log_call(m, provider="claude", model="claude-sonnet-5-5", purpose="chat", seconds=1.0)
    usage.log_call(m, provider="github", model="openai/gpt-4.1-mini", purpose="general", seconds=1.0, ok=False,
                   stop="error: HTTP 200: OK")
    st = {s["name"]: s for s in gateway.status(m, env={})}
    assert st["github"]["last_ok"] is True and st["github"]["calls_this_month"] == 1
    assert st["claude"]["last_ok"] is True and st["claude"]["calls_this_month"] == 1
    assert st["github_models"]["last_ok"] is False and "HTTP 200" in st["github_models"]["last_error"]


def test_find_a_service_for_a_job_and_how_to_attach_it(tmp_path):
    m = Memory(tmp_path / "b.db")
    names = [r["name"] for r in gateway.find("NJ Transit train messages", m, env={})]
    assert names[0] == "njt_rail"
    said = gateway.run_tool(m, {"action": "find", "query": "flight status"})
    assert "AirLabs" in said and "not attached" in said and "set.sh BUDDY_AIRLABS_KEY" in said
    assert "No service" in gateway.run_tool(m, {"action": "find", "query": "zzzz qqqq"})
