"""Lab results from patient portals over SMART on FHIR: the owner signs in at their health system (a one-time state
and PKCE), Buddy reads lab Observations page by page, keeps each once, renews with a refresh token or says "sign in
again" without one; nothing reaches an AI model."""

import base64
import hashlib
import json
import urllib.parse

import pytest
from fastapi.testclient import TestClient

from buddy import connecthub, fhirlabs, gateway, healthrecords, labs
from buddy.memory import Memory

ENV = {"BUDDY_EPIC_CLIENT_ID": "epic-client"}
BASE = "https://fhir.hospital.example/api/FHIR/R4"
AUTH, TOKEN = "https://fhir.hospital.example/oauth2/authorize", "https://fhir.hospital.example/oauth2/token"


@pytest.fixture(autouse=True)
def _fresh_limits():
    gateway._recent.clear()


def obs(oid, name, value, unit, low=None, high=None, flag="", day="2026-11-02", loinc=""):
    o = {"resourceType": "Observation", "id": oid, "status": "final",
         "category": [{"coding": [{"code": "laboratory"}]}],
         "code": {"text": name, "coding": [{"system": "http://loinc.org", "code": loinc, "display": name}] if loinc else []},
         "valueQuantity": {"value": value, "unit": unit, "system": "http://unitsofmeasure.org", "code": unit},
         "effectiveDateTime": f"{day}T08:15:00-05:00"}
    rng = {}
    if low is not None:
        rng["low"] = {"value": low, "unit": unit}
    if high is not None:
        rng["high"] = {"value": high, "unit": unit}
    if rng:
        o["referenceRange"] = [rng]
    if flag:
        o["interpretation"] = [{"coding": [{"code": flag}]}]
    return o


class Hospital:
    """A fake Epic site: SMART configuration, a token endpoint that checks PKCE, and paged lab Observations."""

    def __init__(self, refresh=True, pages=None):
        self.refresh, self.calls, self.forms, self.verifier_ok = refresh, [], [], False
        self.challenge = ""
        self.pages = pages or [
            [obs("o1", "LDL Cholesterol", 132, "mg/dL", high=99, flag="H", loinc="13457-7"),
             obs("o2", "Hemoglobin A1c", 5.4, "%", high=5.6, loinc="4548-4"),
             {"resourceType": "Observation", "id": "o3", "status": "final", "code": {"text": "Urine culture"},
              "valueString": "No growth", "effectiveDateTime": "2026-11-02"}],
            [obs("o4", "Lipase", 31, "U/L", low=11, high=82, loinc="3040-3"),
             obs("o5", "Glucose", 88, "mg/dL", low=70, high=99, loinc="2345-7")]]

    def __call__(self, url, data=None, headers=None):
        self.calls.append(url)
        if url == BASE + "/.well-known/smart-configuration":
            return {"authorization_endpoint": AUTH, "token_endpoint": TOKEN, "capabilities": ["launch-standalone"]}
        if url == TOKEN:
            form = dict(urllib.parse.parse_qsl(data.decode()))
            self.forms.append(form)
            assert form["client_id"] == "epic-client"
            if form["grant_type"] == "authorization_code":
                digest = base64.urlsafe_b64encode(hashlib.sha256(form["code_verifier"].encode()).digest()).decode()
                assert digest.rstrip("=") == self.challenge and form["code"] == "c0de"
            got = {"access_token": f"tok{len(self.forms)}", "expires_in": 3600, "patient": "eP1", "scope": "x"}
            if self.refresh:
                got["refresh_token"] = f"ref{len(self.forms)}"
            return got
        assert headers["Authorization"].startswith("Bearer tok") and headers["Accept"] == "application/fhir+json"
        q = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(url).query))
        assert q["patient"] == "eP1" and q["category"] == "laboratory"
        page = int(q.get("page", "0"))
        bundle = {"resourceType": "Bundle", "entry": [{"resource": r} for r in self.pages[page]], "link": []}
        if page + 1 < len(self.pages):
            bundle["link"].append({"relation": "next", "url": f"{BASE}/Observation?{urllib.parse.urlencode({**q, 'page': page + 1})}"})
        return bundle


def _connect(m, net):
    m.set("server:base_url", "https://buddy.tail1.ts.net")
    link = fhirlabs.start_link(m, ENV, BASE, "Example Health", fetch=net)
    q = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(link["url"]).query))
    assert link["url"].startswith(AUTH) and q["aud"] == BASE and q["code_challenge_method"] == "S256"
    assert q["redirect_uri"] == "https://buddy.tail1.ts.net/connect/mychart/linked" and "offline_access" in q["scope"]
    assert "patient/Observation.read" in q["scope"] and q["client_id"] == "epic-client"
    net.challenge = q["code_challenge"]
    return fhirlabs.finish_link(m, "c0de", q["state"], env=ENV, fetch=net)


def test_sign_in_uses_pkce_and_a_state_that_works_once(tmp_path):
    m = Memory(tmp_path / "b.db")
    net = Hospital()
    assert _connect(m, net) == {"linked": True, "name": "Example Health"} and fhirlabs.linked(m, ENV)
    with pytest.raises(gateway.ConnectError, match="expired or was already used"):
        fhirlabs.finish_link(m, "c0de", "again", env=ENV, fetch=net)
    portal = fhirlabs.portals(m)[0]
    assert portal["connected"] and portal["lasting"] and "tok" not in json.dumps(fhirlabs.portals(m))


def test_lab_results_are_read_page_by_page_and_kept_once(tmp_path):
    m = Memory(tmp_path / "b.db")
    net = Hospital()
    _connect(m, net)
    assert fhirlabs.sync(m, env=ENV, fetch=net) == {"Example Health": 4}  # the urine culture has no number
    by = {t["test"]: t for t in labs.results(m)}
    ldl = by["ldl"]["latest"]
    assert (ldl["value"], ldl["ref_high"], ldl["flag"], ldl["band"]) == (132, 99, "H", "above the lab's range")
    assert ldl["loinc"] == "13457-7" and ldl["lab"] == "Example Health" and ldl["collected"] == "2026-11-02"
    assert by["loinc:3040-3"]["other"] and by["loinc:3040-3"]["label"] == "Lipase"  # a test Buddy has no name for
    assert "Lipase" not in labs.brief(m)  # never in the lines a model may get
    rec = m.db.execute("SELECT * FROM health_records WHERE connector = 'mychart' AND measure = 'lab:glucose'").fetchone()
    assert rec["loinc"] == "2345-7" and rec["quality"] == "lab" and rec["source_ref"] == "mychart:o5"
    # The next read asks from a week before the last one, and keeps nothing twice
    assert fhirlabs.sync(m, env=ENV, fetch=net) == {"Example Health": 0}
    assert "date=ge" in [u for u in net.calls if "/Observation?" in u][-2]
    assert connecthub.history(m, "mychart")[0]["kind"] == "incremental"


def test_a_corrected_result_replaces_the_one_held(tmp_path):
    m = Memory(tmp_path / "b.db")
    net = Hospital(pages=[[obs("o1", "LDL Cholesterol", 132, "mg/dL", high=99, loinc="13457-7")]])
    _connect(m, net)
    fhirlabs.sync(m, env=ENV, fetch=net)
    net.pages = [[obs("o1", "LDL Cholesterol", 128, "mg/dL", high=99, loinc="13457-7")]]
    assert fhirlabs.sync(m, env=ENV, fetch=net) == {"Example Health": 1}
    assert [t["latest"]["value"] for t in labs.results(m)] == [128]


def test_without_a_refresh_token_it_says_sign_in_again_after_the_hour(tmp_path):
    m = Memory(tmp_path / "b.db")
    net = Hospital(refresh=False)
    _connect(m, net)
    fhirlabs.sync(m, env=ENV, fetch=net)
    orgs = json.loads(m.get(fhirlabs.ORGS_KEY))
    orgs[BASE]["until"] = 0  # the hour is up
    m.set(fhirlabs.ORGS_KEY, json.dumps(orgs))
    with pytest.raises(gateway.ConnectError, match="Sign in to Example Health again"):
        fhirlabs.sync(m, env=ENV, fetch=net)
    st = {r["name"]: r for r in gateway.status(m, ENV)}["mychart"]
    assert st["state"] == "expired" and "sign in again" in st["expired"]
    assert connecthub.history(m, "mychart")[0]["ok"] == 0


def test_with_a_refresh_token_it_renews_by_itself(tmp_path):
    m = Memory(tmp_path / "b.db")
    net = Hospital()
    _connect(m, net)
    orgs = json.loads(m.get(fhirlabs.ORGS_KEY))
    orgs[BASE]["until"] = 0
    m.set(fhirlabs.ORGS_KEY, json.dumps(orgs))
    fhirlabs.sync(m, env=ENV, fetch=net)
    assert net.forms[-1]["grant_type"] == "refresh_token" and net.forms[-1]["refresh_token"] == "ref1"


def test_not_set_up_and_bad_addresses_are_said_plainly(tmp_path):
    m = Memory(tmp_path / "b.db")
    with pytest.raises(gateway.ConnectError, match="BUDDY_EPIC_CLIENT_ID"):
        fhirlabs.start_link(m, {}, BASE)
    with pytest.raises(gateway.ConnectError, match="Choose your health system first"):
        fhirlabs.start_link(m, ENV)
    with pytest.raises(gateway.ConnectError, match="https://"):
        fhirlabs.start_link(m, ENV, "http://plain.example/fhir")
    entry = gateway.CATALOG["mychart"]
    assert entry["privacy"] == "personal" and entry["link"] == "fhirlabs" and "AI model" in entry["used_for"]


def test_the_directory_finds_a_health_system_by_its_words(tmp_path):
    m = Memory(tmp_path / "b.db")
    bundle = {"resourceType": "Bundle", "entry": [
        {"resource": {"resourceType": "Endpoint", "name": "Example Health System", "address": BASE + "/"}},
        {"resource": {"resourceType": "Endpoint", "name": "Other Clinic", "address": "https://other.example/fhir"}}]}
    calls = []

    def fetch(url, data=None, headers=None):
        calls.append(url)
        return bundle
    assert fhirlabs.directory(m, "example health", fetch=fetch) == [{"name": "Example Health System", "base": BASE}]
    assert fhirlabs.directory(m, "clinic", fetch=fetch)[0]["name"] == "Other Clinic" and len(calls) == 1  # cached


def test_disconnect_and_delete_one_source(tmp_path):
    m = Memory(tmp_path / "b.db")
    net = Hospital()
    _connect(m, net)
    fhirlabs.sync(m, env=ENV, fetch=net)
    said = connecthub.disconnect(m, "mychart")
    assert said.startswith("Disconnected from Example Health.") and not fhirlabs.linked(m, ENV)
    assert labs.results(m) == [] and healthrecords.forget(m, "mychart") == 0


def test_the_labs_tab_lists_portals_without_tokens(tmp_path, monkeypatch):
    from buddy import server
    m = Memory(tmp_path / "b.db")
    monkeypatch.setattr(server, "memory", m)
    monkeypatch.setattr(server, "authorized", lambda t: t == "s3cret")
    for k, v in ENV.items():
        monkeypatch.setenv(k, v)
    net = Hospital()
    _connect(m, net)
    client, auth = TestClient(server.app), {"Authorization": "Bearer s3cret"}
    got = client.post("/api/health/mychart", json={"action": "list"}, headers=auth).json()
    assert got["set_up"] and got["portals"][0]["name"] == "Example Health" and "tok" not in json.dumps(got)
    assert client.post("/api/health/mychart", json={"action": "disconnect"}, headers=auth).status_code == 400
    assert client.post("/api/health/mychart", json={"action": "connect", "base": "nope"}, headers=auth).status_code == 400
