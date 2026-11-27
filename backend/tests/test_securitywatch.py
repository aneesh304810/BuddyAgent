"""The threat watch: published vulnerabilities matched to what the server runs; open, fixed, why and how."""

import asyncio
import json
import urllib.error
from datetime import date, timedelta

import pytest

from buddy import securitywatch as sw
from buddy.connectors.hub import ConnectorHub
from buddy.memory import Memory
from buddy.tools import ToolError

TODAY = date(2026, 11, 16)
VULN = {"id": "GHSA-aaaa-bbbb-cccc", "aliases": ["CVE-2026-1111"], "summary": "Request smuggling in the HTTP parser",
        "severity": [{"type": "CVSS_V3", "score": "CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H"}],
        "affected": [{"package": {"name": "h11", "ecosystem": "PyPI"},
                      "ranges": [{"type": "ECOSYSTEM", "events": [{"introduced": "0"}, {"fixed": "0.16.0"}]}]}]}
PDF = {"id": "PYSEC-2026-7", "aliases": ["CVE-2026-2222"], "summary": "A crafted PDF makes the reader loop forever",
       "severity": [{"type": "CVSS_V3", "score": "CVSS:3.1/AV:N/AC:L/PR:N/UI:R/S:U/C:N/I:N/A:H"}],
       "affected": [{"package": {"name": "pypdf"}, "ranges": [{"events": [{"introduced": "0"}, {"fixed": "5.1.0"}]}]}]}
KEV = [{"cveID": "CVE-2026-1111", "vendorProject": "python-hyper", "product": "h11", "dateAdded": "2026-11-10",
        "vulnerabilityName": "h11 request smuggling", "shortDescription": "x", "requiredAction": "Update.",
        "knownRansomwareCampaignUse": "Known"},
       {"cveID": "CVE-2026-9999", "vendorProject": "OpenBSD", "product": "OpenSSH", "dateAdded": "2026-11-12",
        "vulnerabilityName": "OpenSSH pre-auth remote code execution", "shortDescription": "Remote attackers run code.",
        "requiredAction": "Apply updates per vendor instructions.", "knownRansomwareCampaignUse": "Unknown"},
       {"cveID": "CVE-2020-0001", "vendorProject": "Linux", "product": "Kernel", "dateAdded": "2021-01-01",
        "vulnerabilityName": "old", "shortDescription": "old"},
       {"cveID": "CVE-2026-5555", "vendorProject": "Acme", "product": "Router", "dateAdded": "2026-11-12",
        "vulnerabilityName": "not ours", "shortDescription": "x"}]
GOOD_ENV = {"BUDDY_DB_KEY": "k" * 40}


@pytest.fixture
def memory(tmp_path, monkeypatch):
    monkeypatch.setattr(sw, "_config", lambda: ("t" * 43, False))
    m = Memory(tmp_path / "buddy.db")
    ConnectorHub(m)
    return m


def net(vulns=None, kev=KEV, epss=None, down=(), breaches=()):
    """A stand-in for the three public databases. vulns: {package: [detail, ...]}."""
    vulns = vulns or {}
    seen = []

    def fetch(url, data=None, headers=None):
        seen.append(url)
        if any(d in url for d in down):
            raise urllib.error.URLError("down")
        if "querybatch" in url:
            asked = json.loads(data)["queries"]
            return json.dumps({"results": [{"vulns": [{"id": v["id"]} for v in vulns.get(q["package"]["name"], [])]}
                                           for q in asked]})
        if "/v1/vulns/" in url:
            wanted = url.rsplit("/", 1)[-1]
            return json.dumps(next(v for vs in vulns.values() for v in vs if v["id"] == wanted))
        if "known_exploited" in url:
            return json.dumps({"vulnerabilities": kev})
        if "haveibeenpwned" in url:
            assert "buddy" in (headers or {}).get("User-Agent", "")
            return json.dumps(list(breaches))
        if "epss" in url:
            return json.dumps({"data": [{"cve": c, "epss": str(p)} for c, p in (epss or {}).items()]})
        raise AssertionError(url)
    fetch.seen = seen
    return fetch


def run(memory, fetch, pkgs=None, report=None, waiting=None, env=GOOD_ENV, today=TODAY):
    return sw.scan(memory, fetch, pkgs if pkgs is not None else {"h11": "0.14.0", "pypdf": "4.0.0", "rich": "13.0"},
                   report if report is not None else {"at": today.isoformat(), "findings": [], "facts": {}},
                   waiting, env, today)


def test_the_published_score_and_its_plain_words():
    worst = sw.cvss("CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H")
    assert worst["score"] == 9.8 and sw.level(worst["score"]) == "critical"
    assert sw.cvss("CVSS:3.1/AV:N/AC:L/PR:N/UI:R/S:C/C:L/I:L/A:N")["score"] == 6.1
    assert sw.cvss("CVSS:3.1/AV:L/AC:H/PR:L/UI:N/S:U/C:H/I:N/A:N")["score"] == 4.7
    assert sw.cvss("nonsense") == {} and sw.level(None, "MODERATE") == "medium"
    words = sw.how(worst["parts"])
    assert "over the network" in words and "without signing in" in words and "no action from you" in words
    assert "take over" in sw.gains(worst["parts"])
    assert "phone-call path" in sw.reach("h11") and "crafted file" in sw.reach("pypdf")
    assert "private network with the access token" in sw.reach("rich")


def test_a_vulnerable_package_is_found_with_why_how_and_the_fix(memory):
    got = run(memory, net({"h11": [VULN]}, kev=[], epss={"CVE-2026-1111": 0.42}))
    assert got["sources"]["Buddy's packages (OSV)"] == "ok" and len(got["new"]) == 1
    r = sw.report(memory, GOOD_ENV)
    f = r["open"][0]
    assert f["severity"] == "critical" and f["score"] == 9.8 and f["component"] == "h11" and f["fixed_in"] == "0.16.0"
    assert f["layer"] == "app" and "CVE-2026-1111" in f["ids"] and f["chance_of_exploit_30_days"] == "42.0%"
    assert "take over" in f["why"] and "over the network" in f["exploit"] and "phone-call path" in f["exploit"]
    assert "Upgrade h11 to 0.16.0" in f["fix"] and f["link"].endswith("GHSA-aaaa-bbbb-cccc")
    assert r["status"] == "Act now" and r["counts"]["critical"] == 1 and r["packages_checked"] == 3
    app = next(layer for layer in r["layers"] if layer["key"] == "app")
    assert app["level"] == "critical" and app["open"] == 1
    assert next(layer for layer in r["layers"] if layer["key"] == "sandbox")["level"] == "good"


def test_only_names_and_versions_are_sent_out(memory):
    fetch = net()
    run(memory, fetch, pkgs={"h11": "0.14.0"})
    assert all(u.startswith(("https://api.osv.dev/", "https://www.cisa.gov/", "https://api.first.org/",
                              "https://haveibeenpwned.com/"))
               for u in fetch.seen)


def test_an_exploited_flaw_is_raised_and_platform_news_is_shown(memory):
    run(memory, net({"h11": [{**VULN, "severity": []}]}))
    r = sw.report(memory, GOOD_ENV)
    mine = next(f for f in r["open"] if f["component"] == "h11")
    assert mine["exploited"] and mine["severity"] == "critical" and "real attacks right now" in mine["why"]
    assert "ransomware" in mine["why"]
    ssh = next(f for f in r["open"] if "OpenSSH" in f["title"])
    assert ssh["severity"] == "high" and ssh["layer"] == "server" and "remote sign-in" in ssh["why"]
    assert "can't tell from here whether the exact version" in ssh["exploit"]
    titles = " ".join(f["title"] for f in r["open"])
    assert "old" not in titles and "not ours" not in titles  # too old, or not something this server runs
    assert r["exploited_open"] == 2
    told = sw.alerts(memory)
    assert len(told) == 1 and "being exploited now" in told[0] and "Why it matters" in told[0]
    assert sw.alerts(memory) == []  # said once


def test_fixed_is_marked_when_a_later_scan_no_longer_finds_it(memory):
    run(memory, net({"h11": [VULN], "pypdf": [PDF]}, kev=[]))
    later = run(memory, net({"pypdf": [PDF]}, kev=[]), pkgs={"h11": "0.16.0", "pypdf": "4.0.0"},
                today=TODAY + timedelta(days=7))
    assert len(later["fixed"]) == 1 and "h11" in later["fixed"][0]
    r = sw.report(memory, GOOD_ENV)
    assert [f["component"] for f in r["open"]] == ["pypdf"] and r["open"][0]["layer"] == "content"
    assert r["fixed"][0]["component"] == "h11" and r["fixed"][0]["closed_on"] == "2026-11-23"
    assert "package was upgraded" in r["fixed"][0]["note"]
    assert next(layer for layer in r["layers"] if layer["key"] == "app")["fixed"] == 1
    back = run(memory, net({"h11": [VULN], "pypdf": [PDF]}, kev=[]), today=TODAY + timedelta(days=9))
    assert any("h11" in f["title"] for f in back["new"])  # found again: reopened and announced again


def test_an_unreachable_database_never_marks_anything_fixed(memory):
    run(memory, net({"h11": [VULN]}, kev=[]))
    got = run(memory, net(down=("osv.dev",), kev=[]), today=TODAY + timedelta(days=1))
    assert got["fixed"] == [] and "couldn't be reached" in got["sources"]["Buddy's packages (OSV)"]
    r = sw.report(memory, GOOD_ENV)
    assert len(r["open"]) == 1 and "earlier findings kept" in r["sources"]["Buddy's packages (OSV)"]


def test_server_and_settings_findings_carry_their_explanations(memory, monkeypatch):
    monkeypatch.setattr(sw, "_config", lambda: ("short", False))
    report = {"at": (TODAY - timedelta(days=20)).isoformat(), "facts": {"firewall": False},
              "findings": [{"severity": "act", "title": "The firewall isn't blocking incoming connections",
                            "detail": "Status: inactive", "fix": "sudo ufw enable"},
                           {"severity": "watch", "title": "SSH accepts passwords", "detail": "", "fix": "disable it"}]}
    monkeypatch.setattr(sw, "server_check", lambda: report)
    run(memory, net(kev=[]), pkgs={}, report=report, waiting=["openssl", "libc6"], env={})
    by = {f["title"]: f for f in sw.report(memory, {})["open"]}
    wall = by["The firewall isn't blocking incoming connections"]
    assert wall["severity"] == "high" and wall["layer"] == "network" and "scanning the internet" in wall["exploit"]
    assert by["SSH accepts passwords"]["layer"] == "server" and "guessed" in by["SSH accepts passwords"]["why"]
    assert by["2 Ubuntu security updates are waiting"]["component"] == "libc6, openssl" or \
        by["2 Ubuntu security updates are waiting"]["component"] == "openssl, libc6"
    assert by["The weekly server check last ran 20 days ago"]["severity"] == "medium"
    assert by["The access token is short (5 characters)"]["severity"] == "high"
    assert by["The database is not encrypted"]["layer"] == "database"
    guards = {g["name"]: g for g in sw.report(memory, {})["defences"]}
    assert guards["Firewall blocking incoming connections"]["ok"] is False
    assert guards["Database encrypted"]["ok"] is False and guards["Access token on every request"]["ok"] is False


def test_a_clean_scan_says_good_without_claiming_safety(memory):
    assert sw.report(memory, GOOD_ENV)["status"] == "Not scanned yet"
    run(memory, net(kev=[]), waiting=[])
    r = sw.report(memory, GOOD_ENV)
    assert r["status"] == "Good: nothing open" and r["open"] == [] and sw.alerts(memory) == []
    assert "can't prove the server is safe" in r["limits"]
    assert len(r["trend"]) == 8 and [layer["key"] for layer in r["layers"]] == [k for k, _, _ in sw.LAYERS]


def test_accepting_a_risk_needs_a_reason_and_keeps_it_listed(memory):
    run(memory, net({"pypdf": [PDF]}, kev=[]))
    fid = sw.report(memory, GOOD_ENV)["open"][0]["id"]
    with pytest.raises(ToolError):
        sw.accept(memory, fid, "ok")
    assert "Marked as accepted" in sw.accept(memory, fid, "Buddy only opens PDFs from my own email")
    r = sw.report(memory, GOOD_ENV)
    assert r["open"] == [] and r["accepted"][0]["note"].startswith("accepted by the owner")


def test_patching_asks_first_and_only_leaves_a_request(memory, tmp_path, monkeypatch):
    monkeypatch.setattr(sw, "PATCH_REQUEST", tmp_path / "state" / "patch-request")
    asked = []

    class Thrift:
        def ask_action(self, question, run, **kw):
            asked.append((question, run))
            return "APPROVAL NEEDED"
    assert "need your yes" in asyncio.run(sw.run_tool(memory, {"action": "patch_now"}))
    assert asyncio.run(sw.run_tool(memory, {"action": "patch_now"}, Thrift())) == "APPROVAL NEEDED"
    assert not sw.PATCH_REQUEST.exists() and "Buddy may restart" in asked[0][0]
    assert "Patching has started" in asyncio.run(asked[0][1]()) and sw.PATCH_REQUEST.exists()
    assert sw.report(memory, GOOD_ENV)["patch_requested"] is True


def test_pending_updates_reads_only_security_lines():
    class Out:
        returncode = 0
        stdout = ("Listing...\nopenssl/noble-security 3.0.13 amd64 [upgradable from: 3.0.2]\n"
                  "vim/noble-updates 9.1 amd64 [upgradable from: 9.0]\n")
    assert sw.pending_updates(lambda *a, **k: Out()) == ["openssl"]

    def missing(*a, **k):
        raise OSError("no apt")
    assert sw.pending_updates(missing) is None


def test_the_page_endpoints_need_the_owner(memory, monkeypatch):
    from fastapi.testclient import TestClient

    from buddy import server
    monkeypatch.setattr(server, "memory", memory)
    monkeypatch.setattr(server, "authorized", lambda t: t == "s3cret")
    client = TestClient(server.app)
    assert client.get("/api/security").status_code == 401
    assert client.post("/api/security/scan").status_code == 401
    out = client.get("/api/security", headers={"Authorization": "Bearer s3cret"}).json()
    assert out["status"] == "Not scanned yet" and len(out["layers"]) == 9


# --- outside services: what Buddy is connected to, breaches at them, and the disconnect switch ----------------
def used(memory, provider, days_ago=1, ok=1, error=""):
    from buddy import gateway
    gateway._table(memory)
    at = (sw.datetime.now(sw.timezone.utc) - timedelta(days=days_ago)).isoformat()
    memory.db.execute("INSERT INTO api_calls (at, provider, endpoint, ok, seconds, error) VALUES (?, ?, '/x', ?, 0.1, ?)",
                      (at, provider, ok, error))
    memory.db.commit()


SERVICE_ENV = {**GOOD_ENV, "BUDDY_FINNHUB_KEY": "k", "BUDDY_PLIVO_AUTH_ID": "k", "BUDDY_PLIVO_AUTH_TOKEN": "k", "BUDDY_PLIVO_NUMBER": "+12015550100", "BUDDY_SERPER_KEY": "k"}


def test_services_lists_what_buddy_is_connected_to_with_its_activity(memory):
    used(memory, "finnhub"), used(memory, "finnhub", ok=0, error="timeout")
    rows = {s["name"]: s for s in sw.services(memory, SERVICE_ENV)}
    fin = rows["finnhub"]
    assert fin["calls_7_days"] == 2 and fin["failed_7_days"] == 1 and fin["sees"] == "public data only"
    assert fin["reaches"] == ["finnhub.io"] and fin["can_disconnect"] and not fin["off"]
    assert rows["plivo"]["sees"] == "your personal data"
    assert "bestbuy" not in rows  # no key set: not connected, so not listed
    assert sw._domain("https://api.example.co.uk/v1") == "example.co.uk"


def test_a_breach_at_a_connected_service_is_found_and_one_elsewhere_is_not(memory):
    used(memory, "finnhub")
    real = sw.local_now().date()
    recent = (real - timedelta(days=20)).isoformat()
    hits = [{"Name": "Plivo", "Domain": "plivo.com", "BreachDate": recent, "AddedDate": recent + "T00:00:00Z",
             "DataClasses": ["Email addresses", "Phone numbers"]},
            {"Name": "Finnhub", "Domain": "finnhub.io", "BreachDate": "2020-01-01", "AddedDate": "2020-02-01T00:00:00Z"},
            {"Name": "Other", "Domain": "nothing-we-use.example", "BreachDate": recent, "AddedDate": recent}]
    got = run(memory, net(kev=[], breaches=hits), pkgs={}, env=SERVICE_ENV, today=real)
    assert got["sources"]["Breaches at connected services (Have I Been Pwned)"] == "ok"
    r = sw.report(memory, SERVICE_ENV)
    found = [f for f in r["open"] if f["source"] == "Outside services"]
    assert len(found) == 1 and found[0]["severity"] == "high" and found[0]["layer"] == "outside"
    assert "Plivo" in found[0]["title"] and "Phone numbers" in found[0]["why"] and "your personal data" in found[0]["why"]
    assert "doesn't by itself mean your key" in found[0]["why"] and "disconnect" in found[0]["fix"]
    plivo = next(s for s in r["services"] if s["name"] == "plivo")
    assert plivo["incidents"] and r["services_summary"]["with_incidents"] == 1
    assert next(layer for layer in r["layers"] if layer["key"] == "outside")["level"] == "high"
    later = run(memory, net(kev=[], breaches=[]), pkgs={}, env=SERVICE_ENV, today=real)
    assert any("Plivo" in t for t in later["fixed"])  # off the list: closed


def test_a_refused_key_and_a_forgotten_key_are_flagged(memory):
    used(memory, "finnhub", ok=0, error="HTTP 401 Unauthorized")
    used(memory, "serper", days_ago=90)
    run(memory, net(kev=[]), pkgs={}, env=SERVICE_ENV, today=sw.local_now().date())
    titles = {f["title"]: f for f in sw.report(memory, SERVICE_ENV)["open"]}
    assert any("refused Buddy's key" in t and "Finnhub" in t for t in titles)
    idle = next(f for t, f in titles.items() if "hasn't been used for" in t)
    assert idle["severity"] == "low" and "Serper" in idle["title"]


def test_disconnecting_stops_calls_at_once_and_can_be_undone(memory):
    from buddy import gateway
    calls = []

    def fetch(url, data=None, headers=None):
        calls.append(url)
        return "{}"
    env = {"BUDDY_FINNHUB_KEY": "k"}
    gateway.call(memory, "finnhub", "/quote?symbol=X", fetch=fetch, env=env)
    out = asyncio.run(sw.run_tool(memory, {"action": "disconnect", "service": "finnhub", "reason": "breach"}))
    assert "is disconnected" in out and "key is still stored" in out
    with pytest.raises(gateway.ConnectError, match="disconnected"):
        gateway.call(memory, "finnhub", "/quote?symbol=X", fetch=fetch, env=env)
    assert len(calls) == 1
    row = next(s for s in sw.services(memory, env) if s["name"] == "finnhub")
    assert row["off"] is True  # still listed, shown as disconnected
    assert "connected again" in asyncio.run(sw.run_tool(memory, {"action": "reconnect", "service": "Finnhub"}))
    gateway.call(memory, "finnhub", "/quote?symbol=X", fetch=fetch, env=env)
    assert len(calls) == 2
    with pytest.raises(ToolError, match="own thinking"):
        gateway.set_off(memory, "claude", True)


def test_a_disconnected_phone_service_blocks_calls_and_the_page_can_switch(memory, monkeypatch):
    from fastapi.testclient import TestClient

    from buddy import calls, gateway, server
    gateway.set_off(memory, "plivo", True)
    with pytest.raises(ToolError, match="Calls are off"):
        calls.plan(memory, "Dr", "2015550123", "question", "hours")
    monkeypatch.setattr(server, "memory", memory)
    monkeypatch.setattr(server, "authorized", lambda t: t == "s3cret")
    client = TestClient(server.app)
    assert client.post("/api/security/service", json={"name": "plivo", "off": False}).status_code == 401
    out = client.post("/api/security/service", json={"name": "plivo", "off": False},
                      headers={"Authorization": "Bearer s3cret"}).json()
    assert "connected again" in out["text"] and not gateway.is_off(memory, "plivo")
    assert client.post("/api/security/service", json={"name": "claude", "off": True},
                       headers={"Authorization": "Bearer s3cret"}).status_code == 400


PHONE_KEV = [{"cveID": "CVE-2026-125350", "vendorProject": "Google", "product": "Chromium V8", "dateAdded": "2026-11-12",
              "vulnerabilityName": "Google Chromium V8 use-after-free", "shortDescription": "Code runs from a web page.",
              "requiredAction": "Apply updates per vendor instructions."},
             {"cveID": "CVE-2026-7777", "vendorProject": "Android", "product": "Framework", "dateAdded": "2026-11-03",
              "vulnerabilityName": "Android Framework privilege escalation", "shortDescription": "An app gains rights."},
             {"cveID": "CVE-2026-8888", "vendorProject": "Samsung", "product": "Mobile Devices", "dateAdded": "2026-11-10",
              "vulnerabilityName": "Samsung image library out-of-bounds write", "shortDescription": "A picture runs code."}]


def test_a_phone_flaw_closes_when_the_phone_reports_an_update_after_it_was_listed(memory):
    from datetime import datetime, timezone
    run(memory, net({}, kev=PHONE_KEV))
    phone = [f for f in sw.report(memory, GOOD_ENV)["open"] if f["layer"] == "device"]
    assert len(phone) == 3 and all("open the Buddy app" in f["fix"] for f in phone)  # no report yet: open
    when = datetime(2026, 11, 15, tzinfo=timezone.utc)
    got = sw.set_device(memory, {"security_patch": "2026-11-01", "maker": "samsung", "android": "16",
                                 "webview": "142.0.7444.90", "webview_updated": "2026-11-14", "email": "x@y.z"}, when)
    assert "email" not in got and got["security_patch"] == "2026-11-01"  # only the update levels are kept
    later = run(memory, net({}, kev=PHONE_KEV))
    still = [f for f in sw.report(memory, GOOD_ENV)["open"] if f["layer"] == "device"]
    # Chromium: the web view updated Nov 14, after the Nov 12 listing. Android: the Nov 1 patch is before the Nov 3
    # listing (that month's later patch may hold it), so it stays open with the patch date in the fix; Samsung the same.
    assert sorted(f["component"] for f in still) == ["Android Framework", "Samsung Mobile Devices"]
    assert any("2026-11-01" in f["fix"] and "Software update" in f["fix"] for f in still)
    assert len(later["fixed"]) == 1
    sw.set_device(memory, {"security_patch": "2026-12-01", "maker": "samsung", "webview_updated": "2026-11-14"}, when)
    run(memory, net({}, kev=PHONE_KEV))
    assert [f for f in sw.report(memory, GOOD_ENV)["open"] if f["layer"] == "device"] == []
    sw.set_device(memory, {"security_patch": "2026-10-01", "maker": "google", "webview_updated": "2026-11-14"}, when)
    run(memory, net({}, kev=PHONE_KEV))
    assert [f["component"] for f in sw.report(memory, GOOD_ENV)["open"] if f["layer"] == "device"] == \
        ["Android Framework"]  # a Pixel: the Samsung flaw isn't its; the Android one is open again
    assert sw.device(memory, date(2027, 1, 30)) == {}  # an old report isn't trusted


def test_the_phone_report_api(memory, monkeypatch):
    from fastapi.testclient import TestClient

    from buddy import server
    monkeypatch.setattr(server, "memory", memory)
    monkeypatch.setattr(server, "authorized", lambda t: t == "s3cret")
    client = TestClient(server.app)
    got = client.post("/api/security/device", json={"security_patch": "2026-11-05", "webview": "142.0"},
                      headers={"Authorization": "Bearer s3cret"}).json()
    assert got["device"]["security_patch"] == "2026-11-05"
    assert client.post("/api/security/device", json={}).status_code == 401
