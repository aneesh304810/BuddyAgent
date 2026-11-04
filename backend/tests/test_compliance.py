"""Security controls (access log, lockout, headers) and the SOC 2 readiness report."""

import json
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from buddy import compliance, guard, health, makers
from buddy.memory import Memory

GOOD = {"Authorization": "Bearer s3cret"}


@pytest.fixture
def srv(monkeypatch, tmp_path):
    from buddy import server

    mem = Memory(":memory:")
    monkeypatch.setattr(server, "authorized", lambda t: t == "s3cret")
    monkeypatch.setattr(server, "memory", mem)
    monkeypatch.setattr(guard, "guard", guard.Guard(":memory:"))
    monkeypatch.setattr(makers, "FILES_DIR", tmp_path / "files")
    return server, mem


def test_every_page_gets_security_headers_but_the_bank_page_keeps_its_own(srv):
    server, _ = srv
    with TestClient(server.app) as c:
        page = c.get("/", headers={"host": "buddy.example.ts.net"})
        csp = page.headers["content-security-policy"]
        assert "script-src 'self'" in csp and "frame-ancestors 'none'" in csp and "wss://buddy.example.ts.net" in csp
        assert page.headers["x-frame-options"] == "DENY" and page.headers["x-content-type-options"] == "nosniff"
        assert "max-age" in page.headers["strict-transport-security"]
        api = c.get("/api/jobs", headers=GOOD)
        assert api.headers["cache-control"] == "no-store"
        bank = c.get("/plaid/link?state=nope")
        assert "content-security-policy" not in bank.headers and bank.headers["x-frame-options"] == "DENY"


def test_failed_sign_ins_lock_the_address_out_and_are_logged(srv):
    server, _ = srv
    with TestClient(server.app) as c:
        for _ in range(guard.LOCKOUT_FAILURES):
            assert c.get("/api/jobs?token=wrong", headers={"Authorization": "Bearer wrong"}).status_code == 401
        locked = c.get("/api/jobs", headers=GOOD)
        assert locked.status_code == 429 and int(locked.headers["retry-after"]) > 800
        assert c.get("/health").status_code == 200  # monitoring still works
        with pytest.raises(WebSocketDisconnect) as e, c.websocket_connect("/ws?token=s3cret") as ws:
            ws.receive_json()
        assert e.value.code == 4429
    g = guard.guard
    stats = g.stats()
    assert stats["failed_sign_ins"] == guard.LOCKOUT_FAILURES and stats["lockouts"] == 1
    rows = g.recent(50)
    assert all("?" not in r["path"] and "wrong" not in json.dumps(r) for r in rows)  # no query strings, no tokens
    g.reset()
    assert g.locked("testclient") == 0


def test_the_access_log_records_calls_and_app_connections_from_the_tailnet_address(srv):
    server, _ = srv
    with TestClient(server.app) as c:
        c.get("/api/jobs", headers={**GOOD, "x-forwarded-for": "6.6.6.6, 100.101.102.103",
                                    "tailscale-user-login": "owner@example.com"})
        with pytest.raises(WebSocketDisconnect), c.websocket_connect("/ws?token=bad") as ws:
            ws.receive_json()
        c.get("/style.css")  # static files aren't logged
    rows = guard.guard.recent()
    assert [(r["kind"], r["status"]) for r in rows] == [("ws", 401), ("http", 200)]
    # TestClient's peer isn't 127.0.0.1, so the forwarded address isn't trusted here
    assert rows[1]["ip"] == "testclient" and rows[1]["user"] == "owner@example.com"
    assert guard.client("127.0.0.1", {"x-forwarded-for": "6.6.6.6, 100.101.102.103"}) == ("100.101.102.103", "")
    assert guard.client("100.1.1.1", {"x-forwarded-for": "6.6.6.6"})[0] == "100.1.1.1"


def test_the_access_log_drops_entries_past_retention():
    g = guard.Guard(":memory:")
    g.record("http", "1.1.1.1", "", "GET", "/api/x", 200)
    g.db.execute("UPDATE access_log SET at = '2020-01-01T00:00:00+00:00'")
    g._purged = 0
    g.record("http", "1.1.1.1", "", "GET", "/api/y", 200)
    assert [r["path"] for r in g.recent()] == ["/api/y"]


def _security_json(tmp_path, monkeypatch, **facts):
    base = {"keys_sealed": True, "token_length": 43, "db_encrypted": True, "keys": 12, "keys_older_than_rotation": 0,
            "keys_without_date": 0, "rotate_days": 90, "log_lines_with_keys_7d": 0, "exposed_listeners": 0,
            "firewall_deny_incoming": True, "ssh_password_login": False, "tailscale_funnel_on": False,
            "access_review_last": "2026-10-01", "access_review_due": False, "access_differences": 0,
            "auto_security_updates": True, "security_updates_waiting": 0, "backups": 9, "backup_age_hours": 5.0,
            "backups_encrypted": True, "disk_used_pct": 40, "service_exposure": 3.1, "vulnerabilities": 0,
            "timers": {"buddy-maintain.timer": "active", "buddy-backup.timer": "active",
                       "buddy-access.timer": "active"}}
    path = tmp_path / "security.json"
    path.write_text(json.dumps({"at": datetime.now(timezone.utc).isoformat(), "status": "good", "findings": [],
                                "facts": {**base, **facts}}))
    monkeypatch.setattr(health, "SECURITY_REPORT", str(path))


def test_a_well_run_server_scores_high_and_a_weak_one_shows_its_gaps(srv, tmp_path, monkeypatch):
    _, mem = srv
    from dataclasses import replace
    monkeypatch.setattr(compliance, "settings", replace(compliance.settings, access_token="x" * 43, dev_mode=False))
    monkeypatch.setenv("BUDDY_DB_KEY", "a" * 64)
    _security_json(tmp_path, monkeypatch)
    items = compliance.controls(compliance.evidence(mem))
    by = {c["id"]: c for c in items}
    assert len(items) >= 30 and not [c for c in items if c["status"] == compliance.GAP]
    assert compliance.score(items) >= 80
    assert by["CC6.4"]["status"] == compliance.CONFIRM  # physical security is DigitalOcean's: keep their report

    _security_json(tmp_path, monkeypatch, firewall_deny_incoming=False, ssh_password_login=True,
                   access_review_last="", timers={})
    monkeypatch.setattr(compliance, "settings", replace(compliance.settings, access_token="short"))
    weak = {c["id"]: c for c in compliance.controls(compliance.evidence(mem))}
    for cid in ("CC6.6a", "CC6.6c", "CC6.2", "CC6.3", "CC6.1a"):
        assert weak[cid]["status"] == compliance.GAP and weak[cid]["fix"], cid
    assert "access.sh review" in weak["CC6.2"]["fix"]


def test_without_a_security_check_the_report_says_how_to_run_it(srv, tmp_path, monkeypatch):
    _, mem = srv
    monkeypatch.setattr(health, "SECURITY_REPORT", str(tmp_path / "missing.json"))
    by = {c["id"]: c for c in compliance.controls(compliance.evidence(mem))}
    assert by["CC4.1"]["status"] == compliance.GAP and "security.sh install" in by["CC4.1"]["fix"]
    assert "security.sh check" in by["CC6.1d"]["evidence"]


def test_the_report_is_a_word_document_in_reports_with_a_download_link(srv, tmp_path, monkeypatch):
    pytest.importorskip("docx")
    server, mem = srv
    _security_json(tmp_path, monkeypatch)
    with TestClient(server.app) as c:
        assert c.post("/api/compliance/report").status_code == 401
        out = c.post("/api/compliance/report", headers={**GOOD, "host": "buddy.example.ts.net"}).json()
        assert out["download"].startswith("https://buddy.example.ts.net/api/files/") and "sig=" in out["download"]
        doc = c.get(out["url"])
        assert doc.status_code == 200 and doc.content[:2] == b"PK"  # a .docx is a zip
        assert server.REPORT_TYPES["soc_report"] == "Security"
        full = dict(mem.db.execute("SELECT * FROM jobs WHERE id = ?", (out["report_id"],)).fetchone())
        assert full["kind"] == "soc_report"
        first = full["report"].splitlines()[0]
        assert first.startswith("[Download the full report (Word)](https://buddy.example.ts.net/api/files/")
        assert "not a SOC 2 report" in full["report"]
        live = c.get("/api/compliance", headers=GOOD).json()
        assert live["controls"] and "CPA" in live["notice"]
    from docx import Document
    import io
    text = "\n".join(p.text for p in Document(io.BytesIO(doc.content)).paragraphs)
    assert "Important notice" in text and "not a SOC 2 report" in text and "Gaps and remediation plan" in text


def test_the_chat_tool_shows_the_document_and_says_it_is_not_an_audit(srv, tmp_path, monkeypatch):
    pytest.importorskip("docx")
    _, mem = srv
    _security_json(tmp_path, monkeypatch)
    out = json.loads(compliance.run_tool(mem, {}))
    assert out["report_id"] and 0 <= out["score"] <= 100 and "not a SOC 2 report" in out["note"]
    assert compliance.latest_age_days(mem) == 0


def test_the_weekly_check_writes_facts_for_the_report(tmp_path, monkeypatch):
    import importlib.util
    from pathlib import Path
    spec = importlib.util.spec_from_file_location("maintain_facts", Path(__file__).resolve().parents[2]
                                                  / "deploy" / "maintain.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, "PLAIN_ENV", tmp_path / "buddy.env")
    monkeypatch.setattr(mod, "SEALED_ENV", tmp_path / "buddy.env.cred")
    monkeypatch.setattr(mod, "KEY_DATES", tmp_path / "key-dates")
    mod.check_secrets({"BUDDY_TOKEN": "t" * 43, "BUDDY_DB_KEY": "a" * 64, "ANTHROPIC_API_KEY": "sk-secret-value"})
    assert mod.facts["token_length"] == 43 and mod.facts["db_encrypted"] and mod.facts["keys_without_date"] == 3
    assert "sk-secret-value" not in json.dumps(mod.facts)


def test_company_documents_are_searchable_and_follow_the_files(tmp_path, monkeypatch):
    from buddy import companydocs, library
    monkeypatch.setattr(library, "LIB_DIR", tmp_path / "library")
    docs = tmp_path / "docs"
    (docs / "company").mkdir(parents=True)
    (docs / "company" / "privacy-policy.md").write_text(
        "# Buddy Privacy Policy\n\nBank transactions are kept for 24 months, then deleted automatically.\n")
    (docs / "SECURITY.md").write_text("# Security\n\nThe weekly security check scans logs for leaked keys and "
                                      "checks the firewall.\n")
    monkeypatch.setattr(companydocs, "DOCS", docs)
    mem = Memory(":memory:")
    assert companydocs.sync(mem) == {"documents": 2, "updated": 2, "removed": 0}
    assert companydocs.sync(mem)["updated"] == 0  # nothing changed: nothing redone
    lib = library.Library(mem, understand=lambda *a: {})
    hit = lib.search_words("transactions kept months")
    assert hit and hit[0]["title"] == "Buddy Privacy Policy"
    assert {d["title"] for d in companydocs.listing(mem)} == {"Buddy Privacy Policy", "Security"}

    (docs / "SECURITY.md").write_text("# Security\n\nNow with a brute-force lockout after ten failed sign-ins.\n")
    (docs / "company" / "privacy-policy.md").unlink()
    assert companydocs.sync(mem) == {"documents": 1, "updated": 1, "removed": 1}
    assert lib.search_words("lockout failed") and not lib.search_words("transactions kept months")
    assert len(companydocs.listing(mem)) == 1


def test_the_repo_docs_include_the_security_implementation_guide():
    from buddy import companydocs
    keys = companydocs.files()
    assert "company/security-implementation.md" in keys and "SECURITY.md" in keys
    text = keys["company/security-implementation.md"].read_text()
    assert "Catalogue of checks" in text and "not a SOC 2 report" in text


def test_the_latest_soc_report_is_in_the_library_and_status_is_live(srv, tmp_path, monkeypatch):
    pytest.importorskip("docx")
    from buddy import companydocs, library
    monkeypatch.setattr(library, "LIB_DIR", tmp_path / "library")
    _, mem = srv
    _security_json(tmp_path, monkeypatch)
    compliance.generate(mem)
    compliance.generate(mem)  # a newer one replaces the older copy
    soc = [d for d in companydocs.listing(mem) if d["title"].startswith("SOC 2 readiness")]
    assert len(soc) == 1
    lib = library.Library(mem, understand=lambda *a: {})
    assert lib.search_words("readiness firewall")
    live = json.loads(compliance.run_tool(mem, {"action": "status"}))
    assert live["facts"]["firewall_deny_incoming"] is True and live["controls"]
    assert compliance.latest_age_days(mem) == 0
