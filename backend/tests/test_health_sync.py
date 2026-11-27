"""'Sync my Oura' in the conversation: the device sends its new days, and the reply says how many (or how to connect
it). Never a value, so the model may see it; the numbers stay on the Health page."""

import pytest

from buddy import connecthub, oura, privacygate, whoop
from buddy.connectors.hub import ConnectorHub
from buddy.memory import Memory
from buddy.tools import ToolError


@pytest.fixture
def memory(tmp_path):
    m = Memory(tmp_path / "buddy.db")
    ConnectorHub(m)
    return m


def test_sync_my_oura(memory, monkeypatch):
    monkeypatch.setattr(oura, "linked", lambda m, env=None: True)
    monkeypatch.setattr(oura, "sync", lambda m, n=None, env=None, fetch=None, kind="": 3)
    got = connecthub.run_sync_tool(memory, {"source": "oura"})
    assert got == "Synced 3 days from the Oura Ring. The numbers are on the Health page."
    assert "health_sync" not in privacygate.HEALTH_TOOLS  # counts only: the model may see this reply


def test_a_device_that_isnt_connected_says_how_to_connect_it(memory, monkeypatch):
    monkeypatch.setattr(oura, "linked", lambda m, env=None: False)
    monkeypatch.setattr(whoop, "linked", lambda m, env=None: False)
    assert "isn't connected to Buddy yet: Profile > Connections > Health > Oura Ring > Connect" in \
        connecthub.run_sync_tool(memory, {"source": "oura"})
    assert connecthub.run_sync_tool(memory, {"source": "all"}).startswith("No health device is connected")
    with pytest.raises(ToolError):
        connecthub.run_sync_tool(memory, {"source": "mychart"})  # lab results aren't synced from the conversation


def test_all_syncs_each_connected_device_and_a_failure_says_where_to_look(memory, monkeypatch):
    monkeypatch.setattr(oura, "linked", lambda m, env=None: True)
    monkeypatch.setattr(whoop, "linked", lambda m, env=None: True)
    monkeypatch.setattr(oura, "sync", lambda m, n=None, env=None, fetch=None, kind="": 2)

    def refused(*a, **k):
        raise RuntimeError("WHOOP refused the sign-in: resting heart rate 52")  # never repeated to the model
    monkeypatch.setattr(whoop, "sync", refused)
    got = connecthub.run_sync_tool(memory, {"source": "all"})
    assert "Synced 2 days from the Oura Ring." in got and "WHOOP couldn't be read just now (RuntimeError)" in got
    assert "52" not in got
