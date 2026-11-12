"""Phone calls (Buddy Connect, Calls group): Plivo places them, Pipecat runs the audio here, Claude decides; every
call needs the owner's yes, shares only allowed facts, and Plivo's requests must carry the call's token and
signature."""

import re
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
    assert got == {"say": "Hi", "digits": "12#", "action": "continue", "outcome": "", "priority": "normal",
                   "caller": "", "callback": "", "meeting": None}
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


def test_with_flux_the_gate_keeps_listening_briefly_after_they_stop():
    pytest.importorskip("pipecat")
    from pipecat.frames.frames import InputAudioRawFrame, VADUserStartedSpeakingFrame, VADUserStoppedSpeakingFrame
    from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

    gate, got = calls.gate_class()(tail_s=0.4), []

    async def push(frame, direction=FrameDirection.DOWNSTREAM):
        got.append(frame)

    async def nothing(self, frame, direction):
        pass

    async def go():
        down = FrameDirection.DOWNSTREAM
        chunk = lambda: InputAudioRawFrame(audio=b"\0\0" * 1600, sample_rate=8000, num_channels=1)  # 0.2 s
        await gate.process_frame(VADUserStartedSpeakingFrame(), down)
        await gate.process_frame(VADUserStoppedSpeakingFrame(), down)
        for _ in range(4):  # 0.4 s of the pause goes on to Flux, then the gate closes
            await gate.process_frame(chunk(), down)
        assert sum(isinstance(f, InputAudioRawFrame) for f in got) == 2 and abs(gate.sent_s - 0.4) < 1e-6
    gate.push_frame = push
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(FrameProcessor, "process_frame", nothing)
        asyncio.run(go())


@pytest.mark.parametrize("refuse_first", [False, True])
def test_a_whole_call_over_plivos_stream(monkeypatch, env, refuse_first):
    """Plivo connects, nobody speaks, Buddy greets (Claude stood in for), its words are played to the call, and
    once they've been heard the call is hung up and the owner told."""
    pytest.importorskip("pipecat")
    from fastapi.testclient import TestClient
    from pipecat.frames.frames import TTSAudioRawFrame, TTSSpeakFrame
    from pipecat.processors.frame_processor import FrameProcessor
    from pipecat.services.deepgram import stt as dg
    from pipecat.services.deepgram.flux import stt as dgf
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

    made = []

    class FakeTTS(FakeSTT):
        def __init__(self, **kw):
            super().__init__(**kw)
            made.append(kw["settings"].kw)
            self.refuse = refuse_first and len(made) == 1  # ElevenLabs says HTTP 400 to the first voice

        async def process_frame(self, frame, direction):
            from pipecat.frames.frames import StartFrame
            if self.refuse and isinstance(frame, StartFrame):
                await FrameProcessor.process_frame(self, frame, direction)
                await self.push_frame(frame, direction)
                await self.push_error(error_msg="server rejected WebSocket connection: HTTP 400",
                                      force_treat_as_permanent=True)
                return
            await FrameProcessor.process_frame(self, frame, direction)
            if isinstance(frame, TTSSpeakFrame):
                spoken.append(frame.text)
                await self.push_frame(TTSAudioRawFrame(audio=b"\0\0" * 2400, sample_rate=8000, num_channels=1))
                return
            await self.push_frame(frame, direction)

    spoken, hung, told = [], [], []
    monkeypatch.setattr(dg, "DeepgramSTTService", FakeSTT)
    monkeypatch.setattr(dgf, "DeepgramFluxSTTService", FakeSTT)
    monkeypatch.setattr(el, "ElevenLabsTTSService", FakeTTS)
    monkeypatch.setattr(calls, "QUIET_START_S", 0.2)
    monkeypatch.setattr(calls, "voice_model", lambda memory, env=None, fetch=None: "eleven_test_turbo")
    moves = Moves({"say": "Hi, this is Sam Rivera's assistant.", "action": "end", "outcome": "They're open 9 to 5."})
    turns = []

    async def think(memory, call, history, env=None):
        from buddy import usage
        turns.append(usage.current_turn.get())  # an approved call is the owner's own request, not background work
        return await moves(memory, call, history, env)
    monkeypatch.setattr(calls, "think", think)
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
    assert made[0]["model"] == "eleven_test_turbo" and made[1]["model"] == calls.TTS_MODEL  # the backup voice
    if refuse_first:
        assert "eleven_test_turbo" in calls._bad_models(server.memory)
    assert turns and turns[0].startswith("phone_call:")
    got = calls.get(server.memory, c["id"])
    assert got["status"] == "done" and got["outcome"] == "They're open 9 to 5."
    greeting = "Hi, this is Sam Rivera's assistant."
    assert got["voice_chars"] == len(greeting) and got["listen_seconds"] == 0  # silence never reached Deepgram
    assert got["cost_usd"] == round(len(greeting) / 1000 * 0.04, 5)
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


def test_picks_the_cheapest_real_time_voice_model(tmp_path, env, monkeypatch):
    m = Memory(tmp_path / "b.db")
    models = [{"model_id": "eleven_v4", "name": "Eleven v4", "can_do_text_to_speech": True},
              {"model_id": "eleven_flash_v2_5", "name": "Eleven Flash v2.5", "can_do_text_to_speech": True},
              {"model_id": "eleven_turbo_v2_5", "name": "Eleven Turbo v2.5", "can_do_text_to_speech": True},
              {"model_id": "eleven_v4_turbo", "name": "Eleven v4 Turbo", "can_do_text_to_speech": True},
              {"model_id": "eleven_v5_turbo_sts", "name": "Eleven v5 Turbo", "can_do_text_to_speech": False}]
    asked = []

    def fetch(url, data=None, headers=None):
        asked.append(url)
        assert headers["xi-api-key"] == "el"
        return json.dumps(models).encode()
    assert calls.voice_model(m, fetch=fetch) == "eleven_flash_v2_5"  # Flash: the quickest to start speaking
    assert calls.voice_model(m, fetch=fetch) == "eleven_flash_v2_5" and len(asked) == 1  # asked once a day
    monkeypatch.setenv("BUDDY_CALL_VOICE_MODEL", "eleven_flash_v2_5")
    assert calls.voice_model(m, fetch=fetch) == "eleven_flash_v2_5"  # the owner's choice wins


def test_voice_model_falls_back_when_elevenlabs_wont_say(tmp_path, env):
    import urllib.error
    m = Memory(tmp_path / "b.db")

    def refuse(url, data=None, headers=None):
        raise urllib.error.HTTPError(url, 401, "no", {}, None)
    assert calls.voice_model(m, fetch=refuse) == calls.TTS_MODEL


def test_insights_show_what_calls_cost(tmp_path, env):
    m = Memory(tmp_path / "b.db")
    c = calls.plan(m, "Dr. Mehta", "2015550123", "appointment", "checkup")
    calls.update(m, c["id"], status="done", outcome="Booked.")
    calls.add_cost(m, c["id"], "deepgram", "listening", 0.01, 78)
    calls.add_cost(m, c["id"], "elevenlabs", "voice", 0.08)
    m.db.execute("UPDATE phone_calls SET cost_usd = cost_usd + 0.02 WHERE id = ?", (c["id"],))  # Claude
    calls.hung_up(m, calls.get(m, c["id"]), {"CallStatus": "completed", "BillDuration": "300"})
    failed = calls.plan(m, "Shop", "2015550124", "question", "hours?")
    calls.update(m, failed["id"], status="calling")
    calls.hung_up(m, calls.get(m, failed["id"]), {"CallStatus": "busy", "Duration": "0"})
    out = calls.overview(m, "2000-01-01")
    assert out["calls"] == 2 and out["done"] == 1 and out["failed"] == 1 and out["minutes"] == 5.0
    assert out["parts"] == {"plivo": 0.065, "deepgram": 0.01, "elevenlabs": 0.08, "claude": 0.02}
    assert out["cost_usd"] == 0.175 and out["per_call_usd"] == 0.0875
    assert "outcome" not in out["recent"][0]  # what was said stays out of Insights
    from buddy import insights
    assert insights._phone_calls(m, "2000-01-01")["calls"] == 2
    assert insights._phone_calls(Memory(tmp_path / "empty.db"), "2000-01-01") is None
    assert insights.purpose_name("phone_call") == "Phone calls"


def test_prices_can_be_changed(monkeypatch):
    assert calls.price("BUDDY_CALL_PRICE_LINE_MIN") == 0.013
    monkeypatch.setenv("BUDDY_CALL_PRICE_LINE_MIN", "0.02")
    assert calls.price("BUDDY_CALL_PRICE_LINE_MIN") == 0.02
    monkeypatch.setenv("BUDDY_CALL_PRICE_LINE_MIN", "lots")
    assert calls.price("BUDDY_CALL_PRICE_LINE_MIN") == 0.013


def test_phone_numbers_reach_the_call_tool_but_accounts_stay_masked():
    from buddy.privacy import mask
    assert mask("I did my phone number is 9254874248") == "I did my phone number is 9254874248"
    assert mask("call 19254874248 and ask their hours") == "call 19254874248 and ask their hours"
    assert mask("my account number is 9254874248") == "my account number is account ****4248"
    assert mask("routing 021000021 for the call") == "routing account ****0021 for the call"
    assert mask("9254874248") == "account ****4248"  # no word about phoning: still hidden


def test_me_calls_the_owners_saved_mobile(tmp_path, env):
    m, t = Memory(tmp_path / "b.db"), Thrift()
    asyncio.run(calls.run_tool(m, t, {"action": "call", "who": "my mobile", "number": "me", "kind": "question",
                                      "goal": "a test call"}))
    assert "+12015550199" in t.asked[0][0]


def test_a_refused_voice_model_isnt_picked_again(tmp_path, env):
    m = Memory(tmp_path / "b.db")
    models = [{"model_id": "eleven_v4_turbo", "name": "Eleven v4 Turbo"},
              {"model_id": "eleven_flash_v3", "name": "Eleven Flash v3"}]
    fetch = lambda url, data=None, headers=None: json.dumps(models).encode()
    assert calls.voice_model(m, fetch=fetch) == "eleven_flash_v3"
    calls.model_refused(m, "eleven_flash_v3")
    assert calls.voice_model(m, fetch=fetch) == "eleven_v4_turbo"
    calls.model_refused(m, calls.TTS_MODEL)  # the fallback itself is never written off
    assert calls.TTS_MODEL not in calls._bad_models(m)


def test_elevenlabs_test_speaks_with_the_call_voice_and_shows_why_it_fails(tmp_path, env):
    import urllib.error
    m, seen = Memory(tmp_path / "b.db"), []
    m.set("calls:voice_model", json.dumps({"model": "eleven_v4_turbo", "day": calls.local_now().date().isoformat()}))

    def ok(url, data=None, headers=None):
        seen.append((url, json.loads(data)))
        return b"\0\0"
    assert gateway.health("elevenlabs", m, fetch=ok) == (True, "ok")
    assert "/v1/text-to-speech/21m00Tcm4TlvDq8ikWAM?model_id=eleven_v4_turbo" in seen[0][0]
    assert seen[0][1] == {"text": "Hi", "model_id": "eleven_v4_turbo"}

    def refuse(url, data=None, headers=None):
        raise urllib.error.HTTPError(url, 400, "bad", {}, __import__("io").BytesIO(
            b'{"detail": {"message": "model eleven_v4_turbo is not available on your plan"}}'))
    ok_, said = gateway.health("elevenlabs", m, fetch=refuse)
    assert not ok_ and "not available on your plan" in said and "model eleven_v4_turbo" in said


def test_call_audio_libraries_keep_no_transcript_in_the_logs(monkeypatch):
    pytest.importorskip("pipecat")
    from loguru import logger
    monkeypatch.setattr(calls, "_QUIET", {"done": False})
    seen = []
    calls._quiet_pipecat()
    sink = logger.add(lambda m: seen.append(m), level="WARNING")
    logger.debug("Generating TTS [date of birth 1990-01-02]")
    logger.warning("websocket closed")
    logger.remove(sink)
    assert len(seen) == 1 and "websocket closed" in seen[0]


def test_debug_switch_logs_what_was_heard_only_when_on(tmp_path, monkeypatch, caplog):
    m = Memory(tmp_path / "b.db")
    call = calls.plan(m, "Dr", "2015550123", "question", "hours?")
    mind, _out = _mind(m, call, Moves({"say": "Hi"}, {"say": "Thanks"}))
    with caplog.at_level("WARNING", logger="buddy.calls"):
        asyncio.run(mind.heard("Hello, Dr. Mehta's office"))
        assert "heard" not in caplog.text
        monkeypatch.setenv("BUDDY_CALL_DEBUG", "1")
        asyncio.run(mind.heard("We open at nine"))
    assert "heard: We open at nine" in caplog.text and "decided:" in caplog.text


def test_daily_limit_skips_test_calls_and_calls_that_never_connected(tmp_path, env, monkeypatch):
    m = Memory(tmp_path / "b.db")
    monkeypatch.setenv("BUDDY_CALLS_PER_DAY", "2")
    for _ in range(4):  # test calls to the owner's own mobile
        calls.update(m, calls.plan(m, "me", "201-555-0199", "question", "test")["id"], status="done")
    for _ in range(3):  # busy, no answer
        calls.update(m, calls.plan(m, "Shop", "2015550124", "question", "hours?")["id"], status="failed")
    for _ in range(2):
        calls.update(m, calls.plan(m, "Shop", "2015550124", "question", "hours?")["id"], status="done")
    with pytest.raises(ToolError, match="2 calls today"):
        calls.plan(m, "Shop", "2015550124", "question", "hours?")
    assert calls.plan(m, "me", "2015550199", "question", "one more test")  # still allowed


def test_replies_never_read_code_aloud():
    nested = '{"say": "{\\"say\\": \\"Sorry, I can\'t share a password. I\'ll check with Aneesh.\\"}", "action": "continue"}'
    assert calls.parse(nested)["say"] == "Sorry, I can't share a password. I'll check with Aneesh."
    cut = '{"say": "Sorry, I can\'t share a password. Thank you.", "digits": "", "action": "end", "outcome": "No pass'
    got = calls.parse(cut)
    assert got["say"] == "Sorry, I can't share a password. Thank you." and got["action"] == "end"
    assert calls.parse('{"say": "{\\"oops\\": 1}"}')["say"] == ""
    assert calls.parse("Hello there")["say"] == "Hello there"


def test_calling_the_owner_never_shows_their_own_number(tmp_path, env, monkeypatch):
    m, p = Memory(tmp_path / "b.db"), Plivo()
    monkeypatch.setenv("BUDDY_CALL_SHOW_MY_NUMBER", "1")
    calls.dial(m, calls.plan(m, "me", "201-555-0199", "question", "test"), fetch=p)
    assert p.calls[0][1]["from"] == "12015550100"  # Buddy's Plivo number: the carrier would ask for a voicemail PIN
    calls.dial(m, calls.plan(m, "Dr", "2015550123", "question", "hours?"), fetch=p)
    assert p.calls[1][1]["from"] == "12015550199"  # others still see the owner's number, as chosen


# --- calls Buddy answers for the owner (forwarded when they don't pick up) ------------------------------------------
def test_buddys_number_is_linked_to_answer_forwarded_calls(tmp_path, env):
    m, p = Memory(tmp_path / "b.db"), Plivo({"app_id": "A1"})
    assert calls.link_inbound(m, fetch=p) == "A1"
    (u1, b1, _, _), (u2, b2, _, _) = p.calls
    key = calls.inbound_key()
    assert u1.endswith("/Account/MAID/Application/")
    assert b1["answer_url"] == f"https://box.ts.net:8443/calls/inbound/0/{key}" and b1["hangup_url"].endswith(
        f"/calls/missed/0/{key}")
    assert u2.endswith("/Number/12015550100/") and b2 == {"app_id": "A1"}
    assert calls.link_inbound(m, fetch=p) == "A1" and len(p.calls) == 2  # nothing changed: nothing sent


def test_forwarded_call_is_answered_with_what_buddy_knows(tmp_path, env):
    m = Memory(tmp_path / "b.db")
    m.remember("Dr. Mehta's office phone is (201) 555-0123")
    call = calls.screen_call(m, {"From": "12015550123", "CallUUID": "in-1", "STIRVerification": "Verified"})
    assert call["kind"] == "screen" and call["number"] == "+12015550123" and "Dr. Mehta" in call["who"]
    prompt = calls.system_prompt(m, call)
    assert "couldn't pick up" in prompt and "Dr. Mehta" in prompt and "Verified" in prompt
    assert "Never say where Sam is" in prompt and '"scam"' in prompt and "never like a bot" in prompt
    assert "Never deny it" in prompt and "This caller is someone Buddy knows, so you may say what Sam is doing" in prompt
    assert "AI" not in calls.screen_greeting() and "Sam's phone" in calls.screen_greeting()
    unknown = calls.screen_call(m, {"From": "anonymous", "CallUUID": "in-2"})
    assert unknown["number"] == "unknown" and unknown["goal"] == ""


def test_screened_calls_have_a_daily_limit(tmp_path, env, monkeypatch):
    m = Memory(tmp_path / "b.db")
    monkeypatch.setattr(calls, "SCREENS_PER_DAY", 2)
    assert calls.screen_call(m, {"From": "12015550001"}) and calls.screen_call(m, {"From": "12015550002"})
    assert calls.screen_call(m, {"From": "12015550003"}) is None
    calls.plan(m, "Dr", "2015550123", "question", "hours?")  # outgoing calls aren't held back by screened ones


def test_what_the_owner_is_told_depends_on_the_caller(tmp_path, env):
    m = Memory(tmp_path / "b.db")
    call = calls.screen_call(m, {"From": "12015550123", "CallUUID": "in-1"})
    urgent = calls.parse('{"say": "Thanks", "action": "end", "priority": "urgent", "caller": "Mom", '
                         '"callback": "201-555-0123", "outcome": "Call back today about dinner."}')
    assert calls.outcome_message(call, urgent) == ("📞 Urgent: missed call from Mom (201-555-0123). Call back "
                                                    "today about dinner.")
    scam = calls.parse('{"say": "Goodbye", "action": "end", "priority": "scam", "outcome": "IRS threat"}')
    assert calls.outcome_message(call, scam) == ""
    calls.screened(m, call, scam)
    calls.screened(m, call, scam)
    assert calls.scams_today(m) == 2
    assert calls.parse('{"say": "x", "priority": "whatever"}')["priority"] == "normal"


def test_caller_hung_up_early(tmp_path, env):
    m = Memory(tmp_path / "b.db")
    m.remember("Mom's mobile is 201-555-0777")
    stranger = calls.screen_call(m, {"From": "12015550999", "CallUUID": "s-1"})
    mom = calls.screen_call(m, {"From": "12015550777", "CallUUID": "s-2"})
    calls.update(m, stranger["id"], status="answered")
    calls.update(m, mom["id"], status="answered")
    assert calls.screen_hung_up(m, {"CallUUID": "s-1", "BillDuration": "12"}) == ""  # unknown and silent: nothing
    told = calls.screen_hung_up(m, {"CallUUID": "s-2", "BillDuration": "8"})
    assert "Missed call from Mom" in told and "without leaving a message" in told
    assert calls.get(m, stranger["id"])["status"] == "done"
    assert calls.screen_hung_up(m, {"CallUUID": "nope"}) == ""


def test_forwarded_calls_need_the_key_and_plivos_signature(monkeypatch, env):
    from fastapi.testclient import TestClient

    from buddy import server
    key = calls.inbound_key()
    url = f"https://box.ts.net:8443/calls/inbound/0/{key}"
    form = {"From": "12015550123", "To": "12015550100", "CallUUID": "in-9"}
    nonce, sig = _signed(url, form)
    headers = {"X-Plivo-Signature-V3-Nonce": nonce, "X-Plivo-Signature-V3": sig}
    monkeypatch.setattr(calls, "link_inbound", lambda memory, env=None, fetch=None: "")
    with TestClient(server.app) as client:
        ok = client.post(f"/calls/inbound/0/{key}", data=form, headers=headers)
        assert ok.status_code == 200 and "<Stream" in ok.text and "/calls/stream/" in ok.text
        assert client.post("/calls/inbound/0/wrong", data=form, headers=headers).status_code == 403
        assert client.post(f"/calls/inbound/0/{key}", data={**form, "From": "1"}, headers=headers).status_code == 403


def test_buddys_own_echo_isnt_taken_for_the_caller():
    said = "Hi, this is Aneesh Nair's assistant, calling on Aneesh's behalf. Could you tell me what time it is?"
    assert calls.looks_like_echo("calling on Aneesh's behalf could you tell me", said)
    assert calls.looks_like_echo("uh", said)  # no real words
    assert not calls.looks_like_echo("It's ten past seven", said)
    assert not calls.looks_like_echo("Sorry, who is this?", said)
    asked = "Is the 3 pm appointment on Tuesday still available?"
    assert not calls.looks_like_echo("No", asked) and not calls.looks_like_echo("OK", asked)  # short answers count
    assert not calls.looks_like_echo("Yes, the 3 pm appointment is available", asked)  # repeats it, but answers
    assert not calls.looks_like_echo("Tuesday", asked)
    assert calls.looks_like_echo("um, hmm", asked)
    assert calls.looks_like_echo("appointment on Tuesday", asked)  # a stretch of Buddy's own words


def test_voice_is_sent_a_little_ahead_to_absorb_network_jitter():
    import time

    class Output:
        _send_interval = 0.04

        async def _write_audio_sleep(self):
            raise AssertionError("replaced")
    out = Output()
    calls._send_ahead(out)

    async def go():
        start = time.monotonic()
        for _ in range(10):  # 0.4 s of audio: all of it goes at once, as the reserve
            await out._write_audio_sleep()
        fast = time.monotonic() - start
        for _ in range(5):  # then it's sent in real time, keeping the reserve
            await out._write_audio_sleep()
        return fast, time.monotonic() - start
    fast, total = asyncio.run(go())
    assert fast < 0.05 and 0.15 < total < 0.35


class _FakeStream:
    def __init__(self, pieces):
        self.pieces = pieces

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    @property
    async def text_stream(self):
        for p in self.pieces:
            yield p

    async def get_final_message(self):
        return object()  # no usage: nothing to record


def test_buddy_speaks_each_sentence_as_claude_writes_it(tmp_path, env, monkeypatch):
    m = Memory(tmp_path / "b.db")
    call = {**calls.plan(m, "Dr", "2015550123", "question", "hours?"), "_system": "x"}
    pieces = ["Sure. Thursday ", "at two works", " for us.\n## ", '{"action": "end", "outcome": "Thursday 2pm"}']

    class Client:
        class messages:
            @staticmethod
            def stream(**kw):
                return _FakeStream(pieces)
    monkeypatch.setattr(calls, "_CLIENT", Client)
    spoken = []

    async def say_now(chunk):
        spoken.append(chunk)
    move = asyncio.run(calls.think(m, call, [{"role": "user", "content": "hi"}], say_now=say_now))
    assert spoken == ["Sure. Thursday at two works for us."]  # "Sure." alone would be rushed
    assert move["say"] == "Sure. Thursday at two works for us." and move["action"] == "end"
    assert move["spoken"] == "Sure. Thursday at two works for us." and move["outcome"] == "Thursday 2pm"

    out = {"said": [], "finished": []}

    async def say(t):
        out["said"].append(t)

    async def finish(mv):
        out["finished"].append(mv)
    mind = calls.Mind(m, call, say=say, press_keys=None, finish=finish)
    asyncio.run(mind.heard("What time works?"))
    assert out["said"] == ["Sure. Thursday at two works for us."]  # once, never twice
    assert "## " in mind.history[-1]["content"]


def test_voice_gets_the_opening_sentence_then_the_rest_in_one_piece(tmp_path, env, monkeypatch):
    m = Memory(tmp_path / "b.db")
    call = {**calls.plan(m, "Dr", "2015550123", "question", "hours?"), "_system": "x"}
    pieces = ["Yes, we're open on Monday. ", "We close at six. ", "Anything else? ", "\n## ", '{"action": "continue"}']

    class Client:
        class messages:
            @staticmethod
            def stream(**kw):
                return _FakeStream(pieces)
    monkeypatch.setattr(calls, "_CLIENT", Client)
    spoken = []

    async def say_now(chunk):
        spoken.append(chunk)
    move = asyncio.run(calls.think(m, call, [{"role": "user", "content": "hi"}], say_now=say_now))
    assert spoken == ["Yes, we're open on Monday.", "We close at six. Anything else?"]
    assert move["spoken"] == move["say"]


def test_send_ahead_starts_fresh_after_an_interruption():
    import time

    class Output:
        _send_interval = 0.04

        async def _write_audio_sleep(self):
            pass
    out = Output()
    calls._send_ahead(out)

    async def go():
        for _ in range(30):  # 1.2 s of audio queued ahead
            await out._write_audio_sleep()
        out._next_send_time = 0  # Pipecat: interrupted, Plivo's queue cleared
        start = time.monotonic()
        for _ in range(10):  # the reserve is rebuilt at once, not after the old one "plays out"
            await out._write_audio_sleep()
        return time.monotonic() - start
    assert asyncio.run(go()) < 0.05


def test_old_and_new_reply_forms_are_both_read():
    new = calls.parse('Of course.\n## {"digits": "2", "action": "continue"}')
    assert new["say"] == "Of course." and new["digits"] == "2"
    old = calls.parse('{"say": "Hi", "action": "end"}')
    assert old["say"] == "Hi" and old["action"] == "end"
    silent = calls.parse('\n## {"action": "continue"}')
    assert silent["say"] == ""


def test_goodbye_words_tell_a_farewell_from_more_to_say():
    assert calls.is_goodbye("OK, thanks, you too, bye!") and calls.is_goodbye("Have a good one.")
    assert calls.is_goodbye("Thank you so much, take care")
    assert not calls.is_goodbye("Oh wait, one more thing, can she bring her ID?")
    assert not calls.is_goodbye("No, actually Thursday is better") and not calls.is_goodbye("")


def _closing_mind(tmp_path, replies, db="b.db"):
    m = Memory(tmp_path / db)
    call = {**calls.plan(m, "Dr", "2015550123", "question", "hours?"), "_system": "x"}
    out = {"said": [], "finished": [], "closing": []}

    async def brain(memory, call, history, env=None):
        return dict(replies.pop(0))

    async def say(t):
        out["said"].append(t)

    async def finish(mv):
        out["finished"].append(mv)

    async def closing(mv):
        out["closing"].append(mv)
    mind = calls.Mind(m, call, say=say, press_keys=None, finish=finish, brain=brain, closing=closing)
    return mind, out


def _move(say, action="continue", **kw):
    return {"say": say, "digits": "", "action": action, "outcome": kw.pop("outcome", ""), **kw}


def test_buddy_waits_for_their_goodbye_before_hanging_up(tmp_path):
    mind, out = _closing_mind(tmp_path, [_move("Great, thanks so much, have a lovely day!", "end", outcome="Open 9-5")])
    asyncio.run(mind.heard("We're open nine to five."))
    assert out["closing"] and not out["finished"] and not mind.ended  # goodbye said, line still open
    asyncio.run(mind.heard("You too, bye!"))
    assert out["said"][-1] == "Bye now." and out["finished"][0]["outcome"] == "Open 9-5" and mind.ended


def test_more_to_say_after_the_goodbye_carries_on(tmp_path):
    mind, out = _closing_mind(tmp_path, [_move("Thanks, bye now!", "end"), _move("Oh sure, what is it?")])
    asyncio.run(mind.heard("That's all set."))
    asyncio.run(mind.heard("Oh wait, one more thing about parking"))
    assert not out["finished"] and not mind.ended and out["said"][-1] == "Oh sure, what is it?"


def test_scams_and_unanswered_calls_still_end_at_once(tmp_path):
    mind, out = _closing_mind(tmp_path, [_move("Not interested, thanks. Bye now.", "end", priority="scam")])
    asyncio.run(mind.heard("Your car's extended warranty is about to expire"))
    assert out["finished"] and not out["closing"] and mind.ended
    mind, out = _closing_mind(tmp_path, [_move("", "end")], db="c.db")
    asyncio.run(mind.heard("[No one has spoken yet]"))
    assert out["finished"] and not out["closing"]


def test_call_me_rings_only_the_owner_and_tells_them_after_yes(tmp_path, env, monkeypatch):
    m, t, p = Memory(tmp_path / "b.db"), Thrift(), Plivo()
    monkeypatch.setattr(calls, "dial", lambda mem, c, env=None, fetch=None: p.calls.append(c) or "req")
    briefing = "Rain this afternoon in Union City; Amex is due tomorrow; Neha called twice."
    got = asyncio.run(calls.run_tool(m, t, {"action": "call", "kind": "tell_me", "number": "2015550123",
                                            "goal": briefing}))
    assert got == "APPROVAL NEEDED" and not p.calls
    question, run = t.asked[0]
    assert "+12015550199" in question and "Amex is due tomorrow" in question  # the owner's own mobile, never another
    assert asyncio.run(run()) == "Calling you now." and p.calls[0]["number"] == "+12015550199"
    prompt = calls.system_prompt(m, p.calls[0])
    assert briefing in prompt and "it's Buddy" in prompt and "share nothing at all" in prompt  # checks who answered
    assert calls.outcome_message(p.calls[0], {"action": "end", "outcome": ""}) == "Our call's done."


def test_persona_knows_it_can_phone():
    from buddy.persona import PERSONA
    assert "phone_call" in PERSONA and "Never say you can't call" in PERSONA.replace("\\\n", "")


def test_an_empty_reply_is_asked_again_never_left_as_silence(tmp_path):
    mind, out = _closing_mind(tmp_path, [_move(""), _move("Hi! Sorry about that, I'm here.")])
    asyncio.run(mind.heard("Hello?"))
    assert out["said"] == ["Hi! Sorry about that, I'm here."]
    mind, out = _closing_mind(tmp_path, [_move(""), _move("")], db="c.db")
    asyncio.run(mind.heard("Can you repeat the time?"))
    assert out["said"] == ["Sorry, could you say that again?"]


def test_hold_lines_may_stay_silent(tmp_path):
    mind, out = _closing_mind(tmp_path, [_move("")])
    mind.call["kind"] = "hold_connect"
    asyncio.run(mind.heard("Please hold"))
    assert out["said"] == []


def test_inbound_application_name_is_one_plivo_accepts():
    import inspect
    src = inspect.getsource(calls.link_inbound)
    name = re.search(r'"app_name": "([^"]+)"', src).group(1)
    assert re.fullmatch(r"[A-Za-z0-9_-]+", name)


def test_call_replies_dont_think_silently_and_fall_back_if_a_model_insists(tmp_path, monkeypatch):
    m = Memory(tmp_path / "b.db")
    call = {**calls.plan(m, "Dr", "2015550123", "question", "hours?"), "_system": "x"}
    seen = []

    class Client:
        class messages:
            @staticmethod
            def stream(**kw):
                seen.append(kw)
                if "thinking" in kw and len(seen) == 1 and refuse:
                    raise RuntimeError("thinking.type: disabled is not supported on this model")
                return _FakeStream(["Sure, we're open nine to five.\n## ", '{"action": "continue"}'])
    for refuse in (False, True):
        seen.clear()
        calls._THINK_OFF_REFUSED.clear()
        monkeypatch.setattr(calls, "_CLIENT", Client)
        move = asyncio.run(calls.think(m, call, [{"role": "user", "content": "hi"}]))
        assert move["say"] == "Sure, we're open nine to five."
        assert seen[0]["thinking"] == {"type": "disabled"} and seen[0]["output_config"] == {"effort": "low"}
        if refuse:  # that model gets low effort and room to think instead, and isn't asked again
            assert "thinking" not in seen[1] and seen[1]["max_tokens"] == 2048
            assert calls._THINK_OFF_REFUSED
