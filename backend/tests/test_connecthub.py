"""Buddy Connect's connector hub: sync history (counts, never values), the expired and needs-approval states, the
terms a source's data comes under, and syncing, deleting or disconnecting one source on its own."""

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from buddy import connecthub, gateway, health_data, healthrecords, oura
from buddy.memory import Memory
from buddy.tools import local_now

from .test_oura import DATA, ENV, Oura, _connect


@pytest.fixture(autouse=True)
def _fresh_limits():
    gateway._recent.clear()


def _day(n):
    return (local_now().date() - timedelta(days=n)).isoformat()


def test_a_sync_is_recorded_with_counts_and_a_failed_one_with_its_error(tmp_path):
    m = Memory(tmp_path / "b.db")
    with connecthub.syncing(m, "oura", "initial") as got:
        got.update(records=40, new=40, reached="2026-11-25")
    with pytest.raises(OSError), connecthub.syncing(m, "oura", "incremental") as got:
        got["records"] = 3
        raise OSError("down")
    rows = connecthub.history(m, "oura")
    assert [(r["kind"], r["ok"], r["records"]) for r in rows] == [("incremental", 0, 3), ("initial", 1, 40)]
    assert rows[0]["error"] == "OSError: down" and rows[1]["reached"] == "2026-11-25"
    last = connecthub.last(m, "oura")
    assert last["last"]["ok"] == 0 and last["last_ok"]["records"] == 40
    assert set(rows[0]) == {"id", "kind", "started_at", "finished_at", "ok", "records", "new_records", "reached", "error"}


def test_history_keeps_the_last_fifty_per_source(tmp_path):
    m = Memory(tmp_path / "b.db")
    for _ in range(connecthub.KEEP_ROWS + 5):
        with connecthub.syncing(m, "oura", "incremental"):
            pass
    with connecthub.syncing(m, "health_connect", "phone"):
        pass
    count = lambda c: m.db.execute("SELECT COUNT(*) FROM connect_syncs WHERE connector = ?", (c,)).fetchone()[0]  # noqa: E731
    assert count("oura") == connecthub.KEEP_ROWS and count("health_connect") == 1


def test_states_expired_and_needs_approval():
    assert gateway.state_of({"set_up": True, "expired": "Oura no longer accepts Buddy's link", "last_ok": True}) == "expired"
    assert gateway.state_of({"set_up": False, "expired": "x"}) == "not_set_up"  # not connected at all
    assert gateway.state_of({"set_up": False, "approval": "Garmin approves business developers only"}) == "needs_approval"
    assert gateway.state_of({"set_up": True, "approval": "x", "last_ok": True}) == "working"
    rows = [{"name": "a", "title": "A", "group": "Health", "set_up": True, "expired": "x"},
            {"name": "b", "title": "B", "group": "Health", "set_up": False, "approval": "y"}]
    sections = gateway.arrange(rows)
    assert sections[0] == {"title": "Needs attention", "kind": "attention", "names": ["a"]}
    assert sections[-1] == {"title": "Not set up", "kind": "not_set_up", "names": ["b"]}


def test_oura_refusing_the_sign_in_shows_expired_until_connected_again(tmp_path):
    m = Memory(tmp_path / "b.db")
    net = Oura(DATA)
    _connect(m, net)
    assert {r["name"]: r for r in gateway.status(m, ENV)}["oura"]["state"] != "expired"
    net.valid = set()  # Oura no longer accepts Buddy's refresh token
    with pytest.raises(gateway.ConnectError, match="Connect again"):
        oura.access_token(m, env=ENV, fetch=net, fresh=True)
    st = {r["name"]: r for r in gateway.status(m, ENV)}["oura"]
    assert st["state"] == "expired" and "no longer accepts" in st["expired"]
    _connect(m, Oura(DATA))  # connected again: cleared
    assert {r["name"]: r for r in gateway.status(m, ENV)}["oura"]["state"] != "expired"


def test_terms_are_shown_where_known():
    for name in ("cftc", "finra", "openfda", "openfoodfacts", "oura", "health_connect"):
        assert gateway.CATALOG[name]["terms"], name
    assert "non-commercial" in gateway.CATALOG["finra"]["terms"] and "public domain" in gateway.CATALOG["cftc"]["terms"]


def test_delete_one_source_leaves_the_other(tmp_path):
    m = Memory(tmp_path / "b.db")
    net = Oura(DATA)
    _connect(m, net)
    oura.sync(m, env=ENV, fetch=net)
    health_data.ingest(m, {"days": [{"day": _day(0), "steps": 8000, "resting_hr": 58}]})
    said = connecthub.forget(m, "oura")
    assert said.startswith("Deleted what Oura Ring gave Buddy (")
    assert m.db.execute("SELECT COUNT(*) FROM oura_daily").fetchone()[0] == 0
    assert connecthub.history(m, "oura") == []
    left = {r["connector"] for r in m.db.execute("SELECT connector FROM health_records")}
    assert left == {"health_connect"} and health_data.days(m, 1)[0]["steps"] == 8000


def test_disconnect_forgets_the_sign_in_and_the_data(tmp_path):
    m = Memory(tmp_path / "b.db")
    net = Oura(DATA)
    _connect(m, net)
    oura.sync(m, env=ENV, fetch=net)
    said = connecthub.disconnect(m, "oura")
    assert said.startswith("The Oura Ring is disconnected from Buddy.") and not oura.linked(m, ENV)
    assert connecthub.expired(m, "oura") == "" and healthrecords.forget(m, "oura") == 0
    assert "Buddy app" in connecthub.disconnect(m, "health_connect")  # the phone sends it: turned off there
    assert "open the Buddy app" in connecthub.sync_now(m, "health_connect")
    with pytest.raises(KeyError):
        connecthub.forget(m, "serper")


def test_the_page_endpoints_need_a_confirm_to_delete(tmp_path, monkeypatch):
    from buddy import server
    monkeypatch.setattr(server, "memory", Memory(tmp_path / "b.db"))
    monkeypatch.setattr(server, "authorized", lambda t: t == "s3cret")
    client, auth = TestClient(server.app), {"Authorization": "Bearer s3cret"}
    health_data.ingest(server.memory, {"days": [{"day": _day(0), "steps": 8000}]})
    got = client.get("/api/health/sources", headers=auth).json()["sources"]
    phone = {s["connector"]: s for s in got}["health_connect"]
    assert phone["records"] == 1 and phone["last"]["kind"] == "phone" and phone["terms"]
    assert client.post("/api/health/sources", json={"action": "forget", "connector": "health_connect"},
                       headers=auth).status_code == 400  # no confirm
    assert client.post("/api/health/sources", json={"action": "drop", "connector": "health_connect"},
                       headers=auth).status_code == 400
    out = client.post("/api/health/sources", json={"action": "forget", "connector": "health_connect", "confirm": True},
                      headers=auth).json()
    assert "Deleted" in out["text"] and {s["connector"]: s for s in out["sources"]}["health_connect"]["records"] == 0
