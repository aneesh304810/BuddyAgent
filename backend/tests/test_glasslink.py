"""The glasses door: hello, the brain's four tools, the privacy rule on this side, and the meter."""

import asyncio
import json
import threading
from pathlib import Path

import pytest

from buddy import agent, glasslink, modules
from buddy.memory import Memory

TEXT = {"name": "Text glasses", "display": {"kind": "text", "lines": 5, "cols": 30, "images": False},
        "speaker": False, "mic": True, "camera": False, "input": ["tap", "double", "long"], "route": "direct"}
EAR = {"name": "Audio glasses", "display": None, "speaker": True, "mic": True, "camera": True, "input": ["tap"],
       "route": "maker-app"}


def hello(profile=None, **more):
    return {"op": "hello", "protocol": "bwcp", "version": "0.2",
            "device": {"maker": "brilliant", "model": "halo", "id": "Halo 4F2A"}, "profile": profile or TEXT, **more}


class Runtime:
    """Stands in for the glasses runtime on the phone: keeps what the server sent, and answers the way it is told."""

    def __init__(self, door, answer=None):
        self.door, self.answer, self.sent, self.session = door, answer, [], None

    async def send(self, message):
        self.sent.append(message)
        if message["op"] == "call" and self.answer is not None:
            reply = self.answer(message)
            if reply is not None:
                asyncio.get_running_loop().call_soon(self.door.heard, self.session, {"op": "result", "id": message["id"], **reply})

    def join(self, memory, profile=None, env=None):
        self.session = self.door.attach(memory, hello(profile), self.send, env=env)
        return self.session


LANDED = {"ok": True, "result": {"shown": True, "spoken": False, "withheld": 0}}


def test_a_profile_is_taken_carefully_and_an_unknown_route_is_the_strictest():
    p = glasslink.profile_of({"display": {"kind": "weird", "lines": "9999", "cols": None}, "speaker": "yes", "mic": True,
                              "input": ["tap", "teleport"], "route": "satellite"})
    assert p["display"] == {"kind": "text", "lines": 40, "cols": 30, "images": False}
    assert (p["speaker"], p["mic"], p["camera"], p["input"], p["route"]) == (False, True, False, ["tap"], "cloud")
    assert glasslink.profile_of(None) == {"name": "Glasses", "display": None, "speaker": False, "mic": False,
                                          "camera": False, "input": [], "route": "cloud"}


def test_the_privacy_rule_on_this_side_matches_the_runtimes_strictest_reading():
    direct, maker, cloud = ({**TEXT, "route": r} for r in ("direct", "maker-app", "cloud"))
    for profile in (direct, maker, cloud):
        assert glasslink.allowed("public", "display", profile) and glasslink.allowed("public", "speech", profile)
    assert glasslink.allowed("personal", "display", direct) and glasslink.allowed("personal", "display", maker)
    assert not glasslink.allowed("personal", "display", cloud)        # not through another company's servers
    assert glasslink.allowed("health", "display", direct)
    assert not glasslink.allowed("health", "display", maker) and not glasslink.allowed("health", "display", cloud)
    for level in ("personal", "health"):
        assert not any(glasslink.allowed(level, "speech", p) for p in (direct, maker, cloud))  # never read out here


def test_hello_is_checked_and_a_switched_off_module_refuses():
    door, mem = glasslink.GlassLink(), Memory(":memory:")
    for bad, why in (({"op": "hi"}, "first message"), ("text", "first message"),
                     (hello(version="1.0"), "speaks version 0.2"), (hello(protocol="other"), "first message")):
        with pytest.raises(glasslink.Refused, match=why):
            door.attach(mem, bad, None)
    with pytest.raises(glasslink.Refused, match="Devices module is switched off"):
        door.attach(mem, hello(), None, env={"BUDDY_MODULES": "core"})
    assert door.current() is None
    session = door.attach(mem, hello(), None)
    assert door.current() is session and session.device == {"maker": "brilliant", "model": "halo", "id": "Halo 4F2A"}
    door.detach(session)
    assert door.current() is None and door.status() == []


def test_with_no_glasses_the_brain_is_told_so_and_where_to_connect():
    said = asyncio.run(glasslink.run_tool("glasses_show", {"title": "Hi"}, glasslink.GlassLink()))
    assert "No glasses are connected" in said and "/glasses.html" in said


def test_the_brain_shows_asks_and_chooses_and_is_told_what_landed():
    door, mem = glasslink.GlassLink(), Memory(":memory:")
    answers = {"glasses_show": LANDED,
               "glasses_ask": {"ok": True, "result": {"answer": "yes", "shown": True, "spoken": False, "withheld": 0}},
               "glasses_choose": {"ok": True, "result": {"choice": "B14", "index": 1, "shown": True, "spoken": False, "withheld": 0}}}
    runtime = Runtime(door, lambda m: answers[m["tool"]])

    async def body():
        runtime.join(mem)
        door.heard(runtime.session, {"op": "state", "battery": 0.62, "link": "good", "secret": "x"})
        about = json.loads(await glasslink.run_tool("glasses_describe", {}, door))
        shown = json.loads(await glasslink.run_tool("glasses_show", {"title": "Gate", "lines": ["B14"]}, door))
        asked = json.loads(await glasslink.run_tool("glasses_ask", {"question": "Navigate there?"}, door))
        chosen = json.loads(await glasslink.run_tool("glasses_choose", {"title": "Gate", "options": ["B12", "B14"]}, door))
        return about, shown, asked, chosen

    about, shown, asked, chosen = asyncio.run(body())
    assert about["glasses"] == "brilliant halo" and about["display"] == {"lines": 5, "characters": 30, "pictures": False}
    assert about["state"] == {"battery": 0.62, "link": "good"} and about["route"] == "direct"
    assert shown == {"shown": True, "spoken": False, "withheld": 0}
    assert asked["answer"] == "yes" and chosen["choice"] == "B14"
    assert [m["tool"] for m in runtime.sent] == ["glasses_show", "glasses_ask", "glasses_choose"]  # describe: no trip
    assert runtime.sent[0] == {"op": "call", "id": 1, "tool": "glasses_show", "args": {"title": "Gate", "lines": ["B14"]}}


def test_private_content_that_may_not_reach_the_glasses_is_never_sent():
    door, mem = glasslink.GlassLink(), Memory(":memory:")
    runtime = Runtime(door, lambda m: LANDED)

    async def tried(profile, level):
        runtime.sent.clear()
        if runtime.session:
            door.detach(runtime.session)
        runtime.join(mem, profile)
        said = await glasslink.run_tool("glasses_show", {"title": "x", "lines": ["y"], "privacy": level}, door)
        return bool(runtime.sent), json.loads(said)

    async def body():
        return {
            "health over a maker's app": await tried({**TEXT, "route": "maker-app"}, "health"),
            "health, direct, with a display": await tried(TEXT, "health"),
            "health, direct, sound only": await tried({**EAR, "route": "direct"}, "health"),
            "personal through a cloud": await tried({**TEXT, "route": "cloud"}, "personal"),
            "personal, sound only": await tried(EAR, "personal"),   # the wearer may have chosen to hear it: theirs to decide
            "public through a cloud": await tried({**TEXT, "route": "cloud"}, "public"),
            "no route given": await tried({k: v for k, v in TEXT.items() if k != "route"}, "personal"),
        }

    got = asyncio.run(body())
    sent = {case: was for case, (was, _) in got.items()}
    assert sent == {"health over a maker's app": False, "health, direct, with a display": True,
                    "health, direct, sound only": False, "personal through a cloud": False,
                    "personal, sound only": True, "public through a cloud": True, "no route given": False}
    held = got["health over a maker's app"][1]
    assert held["shown"] is False and held["withheld"] == 1 and "on their phone" in held["note"]
    assert "privacy must be one of" in asyncio.run(glasslink.run_tool("glasses_show", {"title": "x", "privacy": "secret"}, door))


def test_no_answer_a_lost_connection_and_a_refusal_come_back_as_words():
    door, mem = glasslink.GlassLink(), Memory(":memory:")

    async def quick():
        silent = Runtime(door)
        session = silent.join(mem)
        task = asyncio.ensure_future(glasslink.run_tool("glasses_ask", {"question": "Send it?"}, door))
        await asyncio.sleep(0.01)
        door.detach(session)                                     # the glasses went away while Buddy waited
        gone = await task
        broken = Runtime(door, lambda m: {"ok": False, "error": "glasses_ask needs a question."})
        broken.join(mem)
        refused = await glasslink.run_tool("glasses_ask", {"question": ""}, door)
        return gone, refused

    gone, refused = asyncio.run(quick())
    assert gone == "The glasses disconnected before this reached the wearer."
    assert refused == "The glasses could not do that: glasses_ask needs a question."

    async def timed_out(monkey_wait):
        runtime = Runtime(door)
        session = runtime.join(mem)
        with pytest.raises(asyncio.TimeoutError):
            await session.call("glasses_ask", {"question": "Send it?"}, monkey_wait)
        return runtime.sent

    sent = asyncio.run(timed_out(0.02))
    assert [m["op"] for m in sent] == ["call", "cancel"] and sent[1]["id"] == sent[0]["id"]  # taken off the lens


def test_a_device_is_counted_once_a_day_and_a_statement_shows_the_active_devices():
    door, mem = glasslink.GlassLink(), Memory(":memory:")
    for _ in range(3):                                           # reconnecting is still one device today
        door.detach(door.attach(mem, hello(), None))
    other = hello()
    other["device"] = {"maker": "even", "model": "g2", "id": "G2-77"}
    door.attach(mem, other, None)
    lines = {line["unit"]: line["amount"] for m in modules.statement(mem)["modules"] if m["module"] == "devices"
             for line in m["lines"]}
    assert lines["device_day"] == 2 and lines["active_device"] == 2


def test_the_tools_are_the_glasses_frameworks_four_and_belong_to_the_link_module(monkeypatch):
    names = [t["name"] for t in glasslink.TOOLS]
    assert names == ["glasses_describe", "glasses_show", "glasses_ask", "glasses_choose"]
    framework = (Path(__file__).resolve().parents[2] / "xr" / "src" / "agent.js").read_text()
    assert all(f'name: "{name}"' in framework for name in names)  # the runtime knows every tool the brain can call
    assert all(modules.module_of_tool(name) == "link" for name in names)

    convo = agent.Conversation(Memory(":memory:"))
    assert set(names) <= {t["name"] for t in convo._all_tools()}
    assert "No glasses are connected" in asyncio.run(convo._dispatch("glasses_describe", {}))
    monkeypatch.setenv("BUDDY_MODULES", "all,-link")
    assert not set(names) & {t["name"] for t in convo._all_tools()}
    assert "Link module is switched off" in asyncio.run(convo._dispatch("glasses_show", {"title": "x"}))


def test_the_door_on_the_server_welcomes_a_runtime_and_the_test_line_reaches_it(monkeypatch):
    from fastapi.testclient import TestClient

    from buddy import server

    monkeypatch.setattr(server, "authorized", lambda t: t == "s3cret")
    headers = {"Authorization": "Bearer s3cret"}
    with TestClient(server.app) as c:
        with pytest.raises(Exception):                            # no token: the door stays shut
            with c.websocket_connect("/ws/glasses") as ws:
                ws.receive_json()
        with c.websocket_connect("/ws/glasses?token=s3cret") as ws:
            ws.send_json({"op": "nonsense"})
            assert ws.receive_json()["op"] == "refused"
        assert c.post("/admin/glasses/test", headers=headers).json()["ok"] is False  # nothing connected
        with c.websocket_connect("/ws/glasses?token=s3cret") as ws:
            ws.send_json(hello())
            welcome = ws.receive_json()
            assert welcome["op"] == "welcome" and welcome["version"] == "0.2"
            assert c.get("/admin/glasses", headers=headers).json()["connected"][0]["model"] == "halo"
            got = {}
            asker = threading.Thread(target=lambda: got.update(c.post("/admin/glasses/test", headers=headers).json()))
            asker.start()
            call = ws.receive_json()
            assert call["op"] == "call" and call["tool"] == "glasses_show" and "connected to Buddy" in call["args"]["lines"][0]
            ws.send_json({"op": "result", "id": call["id"], **LANDED})
            asker.join(timeout=10)
            assert got == {"ok": True, "shown": True, "spoken": False, "withheld": 0}
            ws.send_json({"op": "bye", "why": "off"})
        assert c.get("/admin/glasses", headers=headers).json()["connected"] == []
        assert c.get("/xr/src/link.js").status_code == 200       # the page can load the glasses framework
