"""The owner's Nest thermostat, through Buddy Connect (Home group) and Google's Smart Device Management API.

Read: room temperature and humidity, mode (heat, cool, both, off), eco, the target, and whether it's heating or
cooling now. Change (set the temperature, the mode, eco): only after the owner's yes (thrift.ask_action), never on
Buddy's own. Linked once with deploy/nest.sh (Google sign-in; the tokens stay in the server's settings).
Temperatures are shown in Fahrenheit; Google's API speaks Celsius.
"""

from datetime import datetime, timedelta

from . import gateway
from .tools import ToolError, local_now

API = "/v1/enterprises/{project}"
T = "sdm.devices.traits."
MODES = {"heat": "HEAT", "cool": "COOL", "heatcool": "HEATCOOL", "auto": "HEATCOOL", "both": "HEATCOOL", "off": "OFF"}


def f_of(c):
    return None if c is None else round(c * 9 / 5 + 32)


def c_of(f):
    return round((float(f) - 32) * 5 / 9, 1)


def _call(memory, path: str, body=None, env=None, fetch=None):
    project = gateway._secret(env, "BUDDY_NEST_PROJECT_ID")
    for attempt in (0, 1):
        token = gateway.oauth_token(memory, "nest", env=env, fetch=fetch, fresh=attempt == 1)
        try:
            return gateway.call(memory, "nest", API.format(project=project) + path,
                                json_body=body, headers={"Authorization": f"Bearer {token}"}, env=env, fetch=fetch,
                                purpose="home", personal=True)
        except gateway.ConnectError as e:
            if e.status == 401 and attempt == 0:  # the access token ran out early: once more with a fresh one
                continue
            raise


def devices(memory, env=None, fetch=None) -> list[dict]:
    """Every thermostat the owner shared with Buddy, read now."""
    got = _call(memory, "/devices", env=env, fetch=fetch) or {}
    out = []
    for d in got.get("devices") or []:
        if not str(d.get("type", "")).endswith("THERMOSTAT"):
            continue
        tr = d.get("traits") or {}
        sp = tr.get(T + "ThermostatTemperatureSetpoint") or {}
        name = (tr.get(T + "Info") or {}).get("customName") or \
            next((r.get("displayName") for r in d.get("parentRelations") or [] if r.get("displayName")), "Thermostat")
        out.append({
            "id": d.get("name", ""), "name": name,
            "now_f": f_of((tr.get(T + "Temperature") or {}).get("ambientTemperatureCelsius")),
            "humidity": (tr.get(T + "Humidity") or {}).get("ambientHumidityPercent"),
            "mode": (tr.get(T + "ThermostatMode") or {}).get("mode", ""),
            "eco": (tr.get(T + "ThermostatEco") or {}).get("mode", "OFF") != "OFF",
            "running": (tr.get(T + "ThermostatHvac") or {}).get("status", ""),
            "heat_to_f": f_of(sp.get("heatCelsius")), "cool_to_f": f_of(sp.get("coolCelsius")),
            "online": (tr.get(T + "Connectivity") or {}).get("status", "ONLINE") == "ONLINE",
        })
    return out


def describe(d: dict) -> str:
    target = (f"heating to {d['heat_to_f']}°F" if d["mode"] == "HEAT" else
              f"cooling to {d['cool_to_f']}°F" if d["mode"] == "COOL" else
              f"keeping {d['heat_to_f']}-{d['cool_to_f']}°F" if d["mode"] == "HEATCOOL" else "off")
    running = {"HEATING": ", heating now", "COOLING": ", cooling now"}.get(d["running"], "")
    return (f"{d['name']}: {d['now_f']}°F" + (f", {d['humidity']}% humidity" if d["humidity"] is not None else "")
            + f"; {'eco' if d['eco'] else target}{running}" + ("" if d["online"] else " (offline)"))


def _pick(memory, which: str = "", env=None, fetch=None) -> dict:
    found = devices(memory, env, fetch)
    if not found:
        raise ToolError("I can't see a Nest thermostat: link it with sudo bash /opt/buddy/deploy/nest.sh.")
    if which:
        match = [d for d in found if which.lower() in d["name"].lower()]
        if match:
            return match[0]
    return found[0]


def _command(memory, device_id: str, command: str, params: dict, env=None, fetch=None):
    path = "/devices/" + device_id.rsplit("/", 1)[-1] + ":executeCommand"
    return _call(memory, path, {"command": "sdm.devices.commands." + command, "params": params}, env, fetch)


def set_temperature(memory, f: float, which: str = "", env=None, fetch=None) -> str:
    if not 45 <= float(f) <= 90:
        raise ToolError("Nest takes temperatures from 45 to 90°F.")
    d = _pick(memory, which, env, fetch)
    if d["eco"]:
        _command(memory, d["id"], "ThermostatEco.SetMode", {"mode": "OFF"}, env, fetch)
    if d["mode"] == "COOL":
        _command(memory, d["id"], "ThermostatTemperatureSetpoint.SetCool", {"coolCelsius": c_of(f)}, env, fetch)
    elif d["mode"] == "HEAT":
        _command(memory, d["id"], "ThermostatTemperatureSetpoint.SetHeat", {"heatCelsius": c_of(f)}, env, fetch)
    else:
        raise ToolError(f"{d['name']} is {d['mode'].lower() or 'off'}: set it to heat or cool first.")
    return f"Set {d['name']} to {round(float(f))}°F."


def set_mode(memory, mode: str, which: str = "", env=None, fetch=None) -> str:
    want = MODES.get((mode or "").lower().replace(" ", "").replace("-", ""))
    d = _pick(memory, which, env, fetch)
    if (mode or "").lower() == "eco":
        _command(memory, d["id"], "ThermostatEco.SetMode", {"mode": "MANUAL_ECO"}, env, fetch)
        return f"{d['name']} is on eco."
    if not want:
        raise ToolError("Modes are heat, cool, heat-cool, off or eco.")
    _command(memory, d["id"], "ThermostatMode.SetMode", {"mode": want}, env, fetch)
    return f"{d['name']} is set to {mode.lower()}."


TOOL = {
    "name": "thermostat",
    "description": "The user's Nest thermostat (Buddy Connect, Home group). status: room temperature, humidity, "
    "mode, eco, target, whether it's heating or cooling now. set_temperature (fahrenheit), set_mode (heat, cool, "
    "heat-cool, off, eco): these change their home, so they ask the user first and happen only after a yes.",
    "input_schema": {"type": "object", "properties": {
        "action": {"type": "string", "enum": ["status", "set_temperature", "set_mode"]},
        "fahrenheit": {"type": "number"}, "mode": {"type": "string"},
        "which": {"type": "string", "description": "the thermostat's name, if there's more than one"}},
        "required": ["action"], "additionalProperties": False},
}


async def run_tool(memory, thrift, args: dict) -> str:
    import asyncio  # noqa: PLC0415
    action, which = args.get("action"), args.get("which", "")
    if action == "status":
        found = await asyncio.to_thread(devices, memory)
        return "\n".join(describe(d) for d in found) or "No Nest thermostat shared with Buddy."
    if thrift is None:
        return "I need your yes to change the thermostat, and I can't ask right now."
    if action == "set_temperature":
        f = args.get("fahrenheit")
        if f is None:
            return "To what temperature?"

        async def go() -> str:
            return await asyncio.to_thread(set_temperature, memory, f, which)
        return thrift.ask_action(f"Set the thermostat to {round(float(f))}°F?", go)
    if action == "set_mode":
        mode = args.get("mode", "")

        async def go_mode() -> str:
            return await asyncio.to_thread(set_mode, memory, mode, which)
        return thrift.ask_action(f"Switch the thermostat to {mode}?", go_mode)
    raise ToolError("Use status, set_temperature or set_mode.")


def nudges(memory, now: datetime | None = None) -> list[dict]:
    """Weekdays, 15-45 minutes after the usual time the owner leaves home: if the house is still heating or cooling
    to a comfortable setting and not on eco, offer eco (they say yes in chat; Buddy never changes it by itself)."""
    from . import commute  # noqa: PLC0415
    now = now or local_now().replace(tzinfo=None)
    r = commute.routine(memory)
    if now.weekday() >= 5 or "morning" not in r or not gateway.configured("nest"):
        return []
    h, m = map(int, r["morning"]["leave"].split(":"))
    left = now.replace(hour=h, minute=m, second=0, microsecond=0)
    if not left + timedelta(minutes=15) <= now <= left + timedelta(minutes=45):
        return []
    key = f"nest:away:{now.date()}"
    if memory.get(key):
        return []
    memory.set(key, "1")  # checked once a day, whatever it finds
    try:
        found = devices(memory)
    except Exception:
        return []
    busy = [d for d in found if not d["eco"] and d["mode"] in ("HEAT", "COOL", "HEATCOOL")]
    if not busy:
        return []
    d = busy[0]
    return [{"key": key, "urgent": False,
             "text": f"You've usually left home by now, and {describe(d)}. Want me to switch it to eco until you're "
                     "back? Say \"thermostat to eco\" and I'll do it."}]


def status_line(memory) -> str:
    """One line for the morning briefing ('' when there's no Nest)."""
    if not gateway.configured("nest"):
        return ""
    try:
        found = devices(memory)
    except Exception:
        return ""
    return "; ".join(describe(d) for d in found)

