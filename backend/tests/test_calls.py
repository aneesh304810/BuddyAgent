"""Phone calls (Buddy Connect, Calls group): Plivo places them, Pipecat runs the audio here, Claude decides; every
call needs the owner's yes, shares only allowed facts, and Plivo's requests must carry the call's token and
signature."""

import asyncio
import base64
import hashlib
import hmac
import json

import pytest

from buddy import calls, gateway
from buddy.memory import Memory
from buddy.tools import ToolError

ENV = {"BUDDY_PLIVO_AUTH_ID": "MAID", "BUDDY_PLIVO_AUTH_TOKEN": "ptok", "BUDDY_PLIVO_NUMBER": "+12015550100",
       "BUDDY_DEEPGRAM_KEY": "dg", "BUDDY_ELEVENLABS_KEY": "el", "BUDDY_CALLS_PUBLIC_URL": "https://box.ts.net:8443",
       "BUDDY_OWNER_FULL_NAME": "Sam Rivera", "BUDDY_OWNER_PHONE": "201-555-0199", "BUDDY_OWNER_DOB": "1990-01-02",
       "BUDDY_OWNER_INSURANCE": "Aetna PPO W123"}


@pytest.fixture
def env(monkeypatch):
    for k, v in ENV.items():
        monkeypatch.setenv(k, v)
    return ENV


class Plivo:
    def __init__(self, reply=None):
        self.calls = []
        self.reply = reply if reply is not None else {"request_uuid": "req-1"}

    def __call__(self, url, data=None, headers=None, method=None):
        self.calls.append((url, json.loads(data) if data else None, headers or {}, method))
        return b"" if method == "DELETE" else json.dumps(self.reply).encode()


def _signed(url, params, token="ptok", nonce="12345"):
    base = url + ("?" + "".join(f"{k}{params[k]}" for k in sorted(params)) if params else "")
    sig = base64.b64encode(hmac.new(token.encode(), f"{base}.{nonce}".encode(), hashlib.sha256).digest()).decode()
    return nonce, sig


# --- planning and Plivo ----------------------------------------------------------------------------------------
def test_plan_checks_number_kind_goal_and_the_daily_limit(tmp_path):
    m = Memory(tmp_path / "b.db")
    with pytest.raises(ToolError):
        calls.plan(m, "Dr", "12", "appointment", "checkup")
    with pytest.raises(ToolError):
        calls.plan(m, "Dr", "2015550123", "pay_bill", "x")
    c = calls.plan(m, "Dr. Mehta", "(201) 555-0123", "appointment", "annual checkup", "mornings next week")
    assert c["number"] == "+12015550123" and c["status"] == "planned" and len(c["token"]) > 20
    for _ in range(calls.CALLS_PER_DAY):
        calls.update(m, calls.plan(m, "X", "2015550123", "question", "hours?")["id"], status="done")
    with pytest.raises(ToolError, match="calls today"):
        calls.plan(m, "X", "2015550123", "question", "hours?")


def test_not_ready_until_every_piece_is_set_up(monkeypatch, env):
    assert calls.ready() == ""
    monkeypatch.delenv("BUDDY_DEEPGRAM_KEY")
    assert "deepgram" in calls.ready() and "calls.sh" in calls.ready()


def test_dial_goes_through_buddy_connect_with_basic_auth(tmp_path, env):
    m, p = Memory(tmp_path / "b.db"), Plivo()
    c = calls.plan(m, "Dr. Mehta", "2015550123", "appointment", "checkup")
    assert calls.dial(m, c, fetch=p) == "req-1"
    url, body, headers, _ = p.calls[0]
    assert url == "https://api.plivo.com/v1/Account/MAID/Call/"
    assert headers["Authorization"] == "Basic " + base64.b64encode(b"MAID:ptok").decode()
    assert body["to"] == "12015550123" and body["from"] == "12015550100" and body["time_limit"] == 900
    assert body["answer_url"] == f"https://box.ts.net:8443/calls/answer/{c['id']}/{c['token']}"
    assert calls.get(m, c["id"])["status"] == "calling"
    logged = m.db.execute("SELECT endpoint FROM api_calls WHERE provider = 'plivo'").fetchone()[0]
    assert "MAID" not in logged and c["token"] not in logged


def test_can_show_the_owners_own_number(tmp_path, env, monkeypatch):
    m, p = Memory(tmp_path / "b.db"), Plivo()
    monkeypatch.setenv("BUDDY_CALL_SHOW_MY_NUMBER", "1")
    hold = calls.plan(m, "Chase", "8005550100", "hold_connect", "dispute")
    calls.dial(m, hold, fetch=p)
    assert p.calls[0][1]["from"] == "12015550199"
    assert 'callerId="+12015550100"' in calls.connect_xml(hold)  # ringing the owner in: from Buddy's number


def test_hold_connect_calls_may_last_90_minutes(tmp_path, env):
    m, p = Memory(tmp_path / "b.db"), Plivo()
    calls.dial(m, calls.plan(m, "Chase", "8005550100", "hold_connect", "dispute a charge"), fetch=p)
    assert p.calls[0][1]["time_limit"] == 5400


def test_answer_streams_audio_to_this_call_only(tmp_path, env):
    m = Memory(tmp_path / "b.db")
    c = calls.plan(m, "Dr", "2015550123", "appointment", "checkup")
    xml = calls.answer_xml(m, c, {"CallUUID": "cu-9"})
    assert f"wss://box.ts.net:8443/calls/stream/{c['id']}/{c['token']}</Stream>" in xml
    assert 'bidirectional="true"' in xml and "audio/x-mulaw;rate=8000" in xml
    got = calls.get(m, c["id"])
    assert got["call_sid"] == "cu-9" and got["status"] == "answered"


def test_connect_rings_only_the_owner_and_only_on_hold_connect(tmp_path, env):
    m = Memory(tmp_path / "b.db")
    hold = calls.plan(m, "Chase", "8005550100", "hold_connect", "dispute")
    xml = calls.connect_xml(hold)
    assert "<Number>+12015550199</Number>" in xml and 'callerId="+12015550100"' in xml
    assert "<Hangup/>" in calls.connect_xml(calls.plan(m, "Dr", "2015550123", "appointment", "checkup"))


def test_keys_transfer_and_hangup_use_plivos_call_api(tmp_path, env):
    m, p = Memory(tmp_path / "b.db"), Plivo()
    c = {**calls.plan(m, "Chase", "8005550100", "hold_connect", "dispute"), "call_sid": "cu-1"}
    calls.press(m, "cu-1", "1#", fetch=p)
    calls.transfer(m, c, fetch=p)
    calls.hangup(m, "cu-1", fetch=p)
    (u1, b1, _, _), (u2, b2, _, _), (u3, _, _, meth) = p.calls
    assert u1.endswith("/Call/cu-1/DTMF/") and b1 == {"digits": "1#", "leg": "aleg"}
    assert u2.endswith("/Call/cu-1/") and b2["aleg_url"].endswith(f"/calls/connect/{c['id']}/{c['token']}")
    assert u3.endswith("/Call/cu-1/") and meth == "DELETE"


def test_plivo_signature(env):
    url = "https://box.ts.net:8443/calls/answer/1/tok"
    params = {"CallUUID": "u", "From": "+1", "To": "+2"}
    nonce, sig = _signed(url, params)
    assert calls.signature_ok(url, params, nonce, sig)
    assert calls.signature_ok(url, params, nonce, "junk, " + sig)
    assert not calls.signature_ok(url, {**params, "To": "+3"}, nonce, sig)
    assert not calls.signature_ok(url, params, "999", sig)
    assert not calls.signature_ok(url, params, nonce, "")


def test_token_must_match_a_live_call(tmp_path):
    m = Memory(tmp_path / "b.db")
    c = calls.plan(m, "Dr", "2015550123", "question", "hours?")
    assert calls.token_ok(m, c["id"], c["token"]) is None  # not dialled yet
    calls.update(m, c["id"], status="calling")
    assert calls.token_ok(m, c["id"], c["token"])["id"] == c["id"]
    assert calls.token_ok(m, c["id"], "wrong") is None and calls.token_ok(m, 999, c["token"]) is None
    calls.update(m, c["id"], status="done")
    assert calls.token_ok(m, c["id"], c["token"]) is None
    assert calls.token_ok(m, c["id"], c["token"], ended_ok=True)


def test_hang_up_report(tmp_path):
    m = Memory(tmp_path / "b.db")
    c = calls.plan(m, "Dr. Mehta", "2015550123", "question", "hours?")
    calls.update(m, c["id"], status="calling")
    msg = calls.hung_up(m, calls.get(m, c["id"]), {"CallStatus": "busy", "Duration": "0"})
    assert "didn't go through (busy)" in msg and calls.get(m, c["id"])["status"] == "failed"
    c2 = calls.plan(m, "Dr. Mehta", "2015550123", "question", "hours?")
    calls.update(m, c2["id"], status="done", outcome="Open 9 to 5.")
    assert calls.hung_up(m, calls.get(m, c2["id"]), {"CallStatus": "completed", "BillDuration": "150"}) == ""
    assert calls.get(m, c2["id"])["minutes"] == 2.5
    c3 = calls.plan(m, "Dr. Mehta", "2015550123", "question", "hours?")
    calls.update(m, c3["id"], status="answered")
    assert "ended before" in calls.hung_up(m, calls.get(m, c3["id"]), {"CallStatus": "completed"})


# --- the conversation ------------------------------------------------------------------------------------------
def test_prompt_shares_dob_and_insurance_only_for_appointments(tmp_path, env):
    m = Memory(tmp_path / "b.db")
    appt = calls.system_prompt(m, calls.plan(m, "Dr", "2015550123", "appointment", "checkup"))
    assert "1990-01-02" in appt and "Aetna PPO W123" in appt and "Sam Rivera's assistant" in appt
    assert "Never deny it" in appt and "Social Security" in appt
    hold = calls.system_prompt(m, calls.plan(m, "Chase", "8005550100", "hold_connect", "dispute"))
    assert "1990-01-02" not in hold and "Aetna" not in hold and "201-555-0199" in hold


def test_parse_keeps_only_safe_moves():
    got = calls.parse('ok {"say": "Hi", "digits": "1a2#", "action": "transfer_money"}')
    assert got == {"say": "Hi", "digits": "12#", "action": "continue", "outcome": ""}
    assert calls.parse("Hello there")["say"] == "Hello there"


class Moves:
    def __init__(self, *moves):
        self.moves, self.seen = list(moves), []

    async def __call__(self, memory, call, history, env=None):
        self.seen.append(history)
        return calls.parse(json.dumps(self.moves.pop(0)))


def _mind(m, call, brain):
    out = {"said": [], "keys": [], "finished": []}

    async def say(t):
        out["said"].append(t)

    async def keys(d):
        out["keys"].append(d)

    async def finish(move):
        out["finished"].append(move)
    return calls.Mind(m, call, say=say, press_keys=keys, finish=finish, brain=brain), out


def test_mind_presses_keys_skips_repeated_hold_messages_and_connects(tmp_path):
    m = Memory(tmp_path / "b.db")
    call = calls.plan(m, "Chase", "8005550100", "hold_connect", "dispute")
    hold = "Your call is important to us, please stay on the line"
    brain = Moves({"digits": "2"}, {"say": ""}, {"say": "Hi, thank you. One moment please.", "action": "connect",
                                                  "outcome": "Agent Dana answered."})
    mind, out = _mind(m, call, brain)

    async def go():
        await mind.heard("For disputes press 2")
        await mind.heard(hold)
        await mind.heard(hold)  # the same recording: Claude isn't asked again
        await mind.heard("Hi, this is Dana, how can I help?")
        await mind.heard("Hello?")  # after the end: ignored
    asyncio.run(go())
    assert out["keys"] == ["2"] and len(brain.seen) == 3
    assert out["finished"][0]["action"] == "connect" and mind.ended


def test_mind_never_connects_on_other_calls(tmp_path):
    m = Memory(tmp_path / "b.db")
    call = calls.plan(m, "Dr", "2015550123", "appointment", "checkup")
    mind, out = _mind(m, call, Moves({"say": "Sure", "action": "connect"}))
    asyncio.run(mind.heard("Hello, Dr. Mehta's office"))
    assert out["said"] == ["Sure"] and not out["finished"] and not mind.ended


def test_history_merges_back_to_back_lines():
    mind = calls.Mind(None, {"kind": "question"}, say=None, press_keys=None, finish=None)
    mind.history = [{"role": "assistant", "content": "x"}, {"role": "user", "content": "a"},
                    {"role": "user", "content": "b"}]
    assert mind._history() == [{"role": "user", "content": "a\nb"}]


# --- the tool --------------------------------------------------------------------------------------------------
class Thrift:
    def __init__(self):
        self.asked = []

    def ask_action(self, question, run):
        self.asked.append((question, run))
        return "APPROVAL NEEDED"


def test_tool_asks_first_and_dials_only_after_yes(tmp_path, env, monkeypatch):
    m, t, p = Memory(tmp_path / "b.db"), Thrift(), Plivo()
    monkeypatch.setattr(calls, "dial", lambda mem, c, env=None, fetch=None: p.calls.append(c) or "req")
    got = asyncio.run(calls.run_tool(m, t, {"action": "call", "who": "Dr. Mehta", "number": "2015550123",
                                            "kind": "appointment", "goal": "checkup"}))
    assert got == "APPROVAL NEEDED" and not p.calls
    question, run = t.asked[0]
    assert "Sam's assistant" in question and "date of birth" in question and "isn't recorded" in question
    assert "Calling Dr. Mehta" in asyncio.run(run()) and len(p.calls) == 1


def test_tool_says_how_to_set_up_and_shows_status(tmp_path):
    m = Memory(tmp_path / "b.db")
    assert "calls.sh" in asyncio.run(calls.run_tool(m, Thrift(), {"action": "call"}))
    assert asyncio.run(calls.run_tool(m, None, {"action": "status"})) == "No calls yet."


def test_buddy_connect_lists_the_calls_group(env):
    assert "Calls" in gateway.GROUPS
    for name in ("plivo", "deepgram", "elevenlabs"):
        assert gateway.group_of(name) == "Calls" and gateway.configured(name)
        assert gateway.CATALOG[name]["privacy"] == "personal"
    assert "calls.sh" in gateway.setup_hint("plivo")
    seen = []
    gateway.health("deepgram", fetch=lambda u, d, h: seen.append(h) or b"{}")
    assert seen[0]["Authorization"] == "Token dg"


# --- the live audio (Pipecat), with the outside services stood in for ----------------------------------------
def test_gate_sends_audio_to_deepgram_only_while_someone_talks():
    pytest.importorskip("pipecat")
    from pipecat.frames.frames import InputAudioRawFrame, VADUserStartedSpeakingFrame, VADUserStoppedSpeakingFrame
    from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

    gate, got = calls.gate_class()(), []

    async def push(frame, direction=FrameDirection.DOWNSTREAM):
        got.append(frame)

    async def nothing(self, frame, direction):
        pass

    async def go():
        down = FrameDirection.DOWNSTREAM
        chunk = lambda: InputAudioRawFrame(audio=b"\0\0" * 1600, sample_rate=8000, num_channels=1)  # 0.2 s
        for _ in range(10):  # 2 s of hold music: nothing goes on, only the last 0.6 s is kept
            await gate.process_frame(chunk(), down)
        assert got == []
        await gate.process_frame(VADUserStartedSpeakingFrame(), down)
        assert len(got) == 4 and isinstance(got[-1], VADUserStartedSpeakingFrame)  # 3 held chunks, then the start
        await gate.process_frame(chunk(), down)
        await gate.process_frame(VADUserStoppedSpeakingFrame(), down)
        await gate.process_frame(chunk(), down)
        assert len(got) == 6  # the speech and the stop; audio after the stop is held back again
    gate.push_frame = push
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(FrameProcessor, "process_frame", nothing)
        asyncio.run(go())


def test_a_whole_call_over_plivos_stream(monkeypatch, env):
    """Plivo connects, nobody speaks, Buddy greets (Claude stood in for), its words are played to the call, and
    once they've been heard the call is hung up and the owner told."""
    pytest.importorskip("pipecat")
    from fastapi.testclient import TestClient
    from pipecat.frames.frames import TTSAudioRawFrame, TTSSpeakFrame
    from pipecat.processors.frame_processor import FrameProcessor
    from pipecat.services.deepgram import stt as dg
    from pipecat.services.elevenlabs import tts as el

    from buddy import server

    class StandInSettings:
        def __init__(self, **kw):
            self.kw = kw

    class FakeSTT(FrameProcessor):
        Settings = StandInSettings

        def __init__(self, **kw):
            super().__init__()

        async def process_frame(self, frame, direction):
            await super().process_frame(frame, direction)
            await self.push_frame(frame, direction)

    class FakeTTS(FakeSTT):
        async def process_frame(self, frame, direction):
            await FrameProcessor.process_frame(self, frame, direction)
            if isinstance(frame, TTSSpeakFrame):
                spoken.append(frame.text)
                await self.push_frame(TTSAudioRawFrame(audio=b"\0\0" * 2400, sample_rate=8000, num_channels=1))
                return
            await self.push_frame(frame, direction)

    spoken, hung, told = [], [], []
    monkeypatch.setattr(dg, "DeepgramSTTService", FakeSTT)
    monkeypatch.setattr(el, "ElevenLabsTTSService", FakeTTS)
    monkeypatch.setattr(calls, "QUIET_START_S", 0.2)
    monkeypatch.setattr(calls, "think", Moves({"say": "Hi, this is Sam Rivera's assistant.", "action": "end",
                                               "outcome": "They're open 9 to 5."}))
    monkeypatch.setattr(calls, "hangup", lambda mem, uuid, env=None, fetch=None: hung.append(uuid))

    async def deliver(kind, text, **extra):
        told.append(text)
    monkeypatch.setattr(server, "deliver", deliver)

    # set up before the server starts: its background jobs share the database connection
    c = calls.plan(server.memory, "Dr. Mehta", "2015550123", "question", "opening hours")
    calls.update(server.memory, c["id"], status="answered", call_sid="cu-7")
    with TestClient(server.app) as client:
        with pytest.raises(Exception):  # a wrong token is refused
            with client.websocket_connect(f"/calls/stream/{c['id']}/nope") as ws:
                ws.receive_text()
        played = []
        with client.websocket_connect(f"/calls/stream/{c['id']}/{c['token']}") as ws:
            ws.send_text(json.dumps({"event": "start", "start": {"streamId": "st-1", "callId": "cu-7"}}))
            silence = base64.b64encode(b"\xff" * 160).decode()
            for _ in range(5):
                ws.send_text(json.dumps({"event": "media", "media": {"payload": silence}}))
            try:
                while True:
                    msg = json.loads(ws.receive_text())
                    if msg.get("event") == "playAudio":
                        played.append(msg)
                        ws.send_text(json.dumps({"event": "media", "media": {"payload": silence}}))
            except Exception:
                pass  # Buddy ended the call: the stream closes
    assert spoken == ["Hi, this is Sam Rivera's assistant."] and played and played[0]["streamId"] == "st-1"
    assert hung == ["cu-7"]
    got = calls.get(server.memory, c["id"])
    assert got["status"] == "done" and got["outcome"] == "They're open 9 to 5."
    assert any(t == "Call to Dr. Mehta done. They're open 9 to 5." for t in told)


def test_plivo_requests_need_token_and_signature(monkeypatch, env):
    from fastapi.testclient import TestClient

    from buddy import server
    c = calls.plan(server.memory, "Dr", "2015550123", "question", "hours?")
    calls.update(server.memory, c["id"], status="calling")
    with TestClient(server.app) as client:
        url = f"https://box.ts.net:8443/calls/answer/{c['id']}/{c['token']}"
        form = {"CallUUID": "cu-1", "To": "12015550123"}
        nonce, sig = _signed(url, form)
        headers = {"X-Plivo-Signature-V3-Nonce": nonce, "X-Plivo-Signature-V3": sig}
        ok = client.post(f"/calls/answer/{c['id']}/{c['token']}", data=form, headers=headers)
        assert ok.status_code == 200 and "<Stream" in ok.text and ok.headers["content-type"].startswith("application/xml")
        bad = client.post(f"/calls/answer/{c['id']}/{c['token']}", data={**form, "To": "x"}, headers=headers)
        assert bad.status_code == 403
        assert client.post(f"/calls/answer/{c['id']}/wrong", data=form, headers=headers).status_code == 403
