"""Google Nest through Buddy Connect: read the thermostat, change it only after a yes, renew Google's sign-in."""

import asyncio
import json
from datetime import datetime

import pytest

from buddy import gateway, nest
from buddy.memory import Memory

ENV = {"BUDDY_NEST_CLIENT_ID": "cid", "BUDDY_NEST_CLIENT_SECRET": "sec", "BUDDY_NEST_REFRESH_TOKEN": "ref",
       "BUDDY_NEST_PROJECT_ID": "proj"}
DEVICE = {"name": "enterprises/proj/devices/dev1", "type": "sdm.devices.types.THERMOSTAT",
          "traits": {"sdm.devices.traits.Info": {"customName": "Hallway"},
                     "sdm.devices.traits.Temperature": {"ambientTemperatureCelsius": 19.0},
                     "sdm.devices.traits.Humidity": {"ambientHumidityPercent": 41},
                     "sdm.devices.traits.ThermostatMode": {"mode": "HEAT"},
                     "sdm.devices.traits.ThermostatEco": {"mode": "OFF"},
                     "sdm.devices.traits.ThermostatHvac": {"status": "HEATING"},
                     "sdm.devices.traits.ThermostatTemperatureSetpoint": {"heatCelsius": 21.0}}}


class Google:
    def __init__(self):
        self.calls, self.tokens = [], 0

    def __call__(self, url, data=None, headers=None):
        self.calls.append((url, json.loads(data) if data and url.endswith("Command") else data, headers or {}))
        if url == "https://oauth2.googleapis.com/token":
            self.tokens += 1
            assert b"refresh_token=ref" in data and b"grant_type=refresh_token" in data
            return json.dumps({"access_token": f"at{self.tokens}", "expires_in": 3599}).encode()
        assert headers["Authorization"].startswith("Bearer at")
        if url.endswith("/devices"):
            return json.dumps({"devices": [DEVICE, {"type": "sdm.devices.types.DOORBELL"}]}).encode()
        return b"{}"


def test_reads_the_thermostat_in_fahrenheit_and_reuses_the_token(tmp_path):
    m, g = Memory(tmp_path / "b.db"), Google()
    found = nest.devices(m, env=ENV, fetch=g)
    assert len(found) == 1 and found[0]["now_f"] == 66 and found[0]["heat_to_f"] == 70
    assert nest.describe(found[0]) == "Hallway: 66°F, 41% humidity; heating to 70°F, heating now"
    nest.devices(m, env=ENV, fetch=g)
    assert g.tokens == 1
    assert g.calls[1][0] == "https://smartdevicemanagement.googleapis.com/v1/enterprises/proj/devices"


def test_setting_the_temperature_sends_celsius_to_the_right_command(tmp_path):
    m, g = Memory(tmp_path / "b.db"), Google()
    assert nest.set_temperature(m, 72, env=ENV, fetch=g) == "Set Hallway to 72°F."
    url, body, _ = g.calls[-1]
    assert url.endswith("/devices/dev1:executeCommand")
    assert body == {"command": "sdm.devices.commands.ThermostatTemperatureSetpoint.SetHeat",
                    "params": {"heatCelsius": 22.2}}
    with pytest.raises(Exception):
        nest.set_temperature(m, 120, env=ENV, fetch=g)


def test_changes_need_a_yes(tmp_path):
    asked = []

    class Thrift:
        def ask_action(self, question, run, **kw):
            asked.append(question)
            return "APPROVAL NEEDED"
    said = asyncio.run(nest.run_tool(Memory(tmp_path / "b.db"), Thrift(), {"action": "set_temperature",
                                                                             "fahrenheit": 70}))
    assert said == "APPROVAL NEEDED" and asked == ["Set the thermostat to 70°F?"]


def test_a_revoked_link_says_how_to_relink(tmp_path):
    import io
    import urllib.error

    def fetch(url, data=None, headers=None):
        raise urllib.error.HTTPError(url, 400, "x", {}, io.BytesIO(b'{"error": "invalid_grant"}'))
    with pytest.raises(gateway.ConnectError, match="deploy/nest.sh"):
        nest.devices(Memory(tmp_path / "b.db"), env=ENV, fetch=fetch)


def test_not_linked_is_listed_with_its_setup_command(tmp_path):
    st = {s["name"]: s for s in gateway.status(Memory(tmp_path / "b.db"), env={})}
    assert st["nest"]["group"] == "Home" and not st["nest"]["set_up"]
    assert st["nest"]["setup"] == "sudo bash /opt/buddy/deploy/nest.sh" and st["nest"]["can_test"]


def test_eco_offer_after_leaving_once_a_day(tmp_path, monkeypatch):
    m = Memory(tmp_path / "b.db")
    m.set("commute:routine", '{"morning": {"leave": "08:10", "arrive": "08:50"}, "lines": []}')
    monkeypatch.setattr(gateway, "configured", lambda name, env=None: True)
    monkeypatch.setattr(nest, "devices", lambda memory, env=None, fetch=None: [
        {"name": "Hallway", "now_f": 70, "humidity": None, "mode": "HEAT", "eco": False, "running": "",
         "heat_to_f": 72, "cool_to_f": None, "online": True}])
    monday = datetime(2026, 11, 9, 8, 35)
    got = nest.nudges(m, monday)
    assert len(got) == 1 and "eco" in got[0]["text"] and "thermostat to eco" in got[0]["text"]
    assert nest.nudges(m, monday) == []                              # once a day
    assert nest.nudges(m, datetime(2026, 11, 10, 8, 0)) == []        # before leaving
