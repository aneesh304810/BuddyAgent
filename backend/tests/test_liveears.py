"""Live hearing for Talk mode: the phone streams a turn, Flux says the words and when it ended, the same checks
apply (voice match, then the usual road to Claude), and a turn Flux can't do is heard the old way from the same audio."""

import asyncio
import base64
from types import SimpleNamespace

import pytest

from buddy import agent, calls, liveears, server
from buddy.memory import Memory

KEY = {"BUDDY_DEEPGRAM_KEY": "dg"}
PCM = b"\x01\x00" * 1600  # a tenth of a second


def chunk(pcm=PCM, **extra):
    return {"type": "live", "op": "audio", "pcm": base64.b64encode(pcm).decode(), **extra}


class Ears:
    """Flux stood in for: keeps what it is fed; the test plays its part."""

    made = []

    def __init__(self, on_partial, on_final, env=None, languages=("en",), terms=(), on_state=None):
        self.on_partial, self.on_final, self.languages, self.terms = on_partial, on_final, list(languages), list(terms)
        self.on_state = on_state
        self.fed, self.alive, self.down, self.failed, self.closed = b"", True, False, "", False
        Ears.made.append(self)

    @property
    def usable(self):
        return self.alive and not self.down

    async def start(self):
        pass

    async def feed(self, pcm):
        self.fed += pcm

    async def close(self):
        self.closed = True


def talk(tmp_path, voice="you", env=KEY):
    out = {"sent": [], "clips": []}

    async def send(message):
        out["sent"].append(message)

    async def hear_clip(message):
        out["clips"].append(message)

    async def voice_ok(pcm):
        out["voice_pcm"] = pcm
        return voice

    Ears.made.clear()
    live = liveears.LiveTalk(Memory(tmp_path / "b.db"), send, hear_clip, voice_ok, env=env, make_ears=Ears,
                             terms=["Kochi", "Priya"])
    return live, out


def run(coro):
    return asyncio.run(coro)


def test_where_live_hearing_can_be_used(monkeypatch):
    assert liveears.available(KEY) and not liveears.available({})  # needs the Deepgram key
    assert not liveears.available({**KEY, "BUDDY_TALK_LIVE": "off"})
    # the phone's Talk language: every language named must be one Flux hears
    assert liveears.hears("en-US") and liveears.hears("auto:hi-IN,en-IN") and liveears.hears("es-ES")
    assert not liveears.hears("auto:ml-IN,en-IN") and not liveears.hears("ml-IN")  # Malayalam stays with Whisper
    monkeypatch.setattr("buddy.whisper.known_languages", lambda: ["en", "ml"])
    assert not liveears.hears("auto")  # "any language": the owner's own, which include one Flux doesn't have
    monkeypatch.setattr("buddy.whisper.known_languages", lambda: ["en", "es"])
    assert liveears.hears("auto")


def test_a_turn_is_heard_as_it_is_spoken(tmp_path):
    async def go():
        live, out = talk(tmp_path)
        await live.handle({"type": "live", "op": "hello", "lang": "en-US"})
        assert out["sent"] == [{"type": "live_state", "on": True, "end_ms": 3200}]
        ears = Ears.made[0]
        assert ears.terms == ["Kochi", "Priya"] and ears.languages == ["en"]
        await live.handle({"type": "live", "op": "start", "id": "t1", "pcm": base64.b64encode(PCM).decode(), "lang": "en-US"})
        await live.handle(chunk())
        await ears.on_partial("what's the wea")
        await live.handle(chunk(sp=False))  # the pause after the sentence travels too: Flux judges the end from it
        assert ears.fed == PCM * 3
        await ears.on_final("What's the weather in Kochi", "en")
        await asyncio.sleep(0.05)
        heard = out["sent"][-1]
        assert out["sent"][1] == {"type": "live_partial", "text": "what's the wea", "id": "t1"}
        assert heard["type"] == "heard" and heard["text"] == "What's the weather in Kochi" and heard["engine"] == "live"
        assert heard["id"] == "t1" and heard["lang"] == "en" and heard["wait_ms"] >= 0
        assert out["voice_pcm"] == PCM * 3 and not out["clips"]  # voice match heard the turn; no clip was needed
        assert live.just_heard("What's the weather in Kochi") and not live.just_heard("something typed")
        await ears.on_final("a late echo of the same turn", "en")  # nothing is open: ignored
        await live.handle(chunk())                                  # audio with no turn open: ignored
        await asyncio.sleep(0.02)
        assert out["sent"][-1] is heard and ears.fed == PCM * 3
        rows = live.memory.db.execute("SELECT provider, purpose FROM model_calls").fetchall()
        assert [tuple(r) for r in rows] == [("deepgram", "speech")]  # what it cost is in Insights
        await live.handle({"type": "live", "op": "bye"})
        assert ears.closed and out["sent"][-1]["on"] is False
    run(go())


def test_someone_elses_voice_is_not_answered(tmp_path):
    async def go():
        live, out = talk(tmp_path, voice="not_you")
        await live.handle({"type": "live", "op": "hello", "lang": "en-US"})
        await live.handle({"type": "live", "op": "start", "id": "t2", "pcm": base64.b64encode(PCM).decode()})
        await Ears.made[0].on_final("Play some music", "en")
        await asyncio.sleep(0.05)
        assert out["sent"][-1]["text"] == "" and out["sent"][-1]["missed"] == "not_you" and out["sent"][-1]["id"] == "t2"
        assert not live.just_heard("Play some music")
    run(go())


def test_a_turn_flux_does_not_end_is_heard_the_old_way_from_the_same_audio(tmp_path):
    async def go():
        live, out = talk(tmp_path)
        await live.handle({"type": "live", "op": "hello", "lang": "en-US"})
        await live.handle({"type": "live", "op": "start", "id": "t3", "pcm": base64.b64encode(PCM).decode(),
                           "lang": "en-US", "mic": "phone"})
        await live.handle(chunk())
        await live.handle({"type": "live", "op": "end"})  # the phone's own silence limit passed first
        clip = out["clips"][0]
        assert base64.b64decode(clip["pcm"]) == PCM * 2 and clip["mode"] == "talk" and clip["id"] == "t3"
        assert clip["lang"] == "en-US" and clip["mic"] == "phone"
        await Ears.made[0].on_final("too late", "en")  # Flux answers after all: the turn is already being heard
        await asyncio.sleep(0.02)
        assert len(out["clips"]) == 1 and not [m for m in out["sent"] if m["type"] == "heard"]
    run(go())


def test_deepgram_dropping_out_mid_turn_loses_nothing(tmp_path):
    async def go():
        live, out = talk(tmp_path)
        await live.handle({"type": "live", "op": "hello", "lang": "en-US"})
        await live.handle({"type": "live", "op": "start", "id": "t4", "pcm": base64.b64encode(PCM).decode()})
        ears = Ears.made[0]
        ears.down = True  # Deepgram's connection dropped (the engine reconnects by itself)
        await live.handle(chunk())
        assert out["sent"][-1] == {"type": "live_state", "on": False, "end_ms": 3200}  # the phone goes back to clips
        await live.handle({"type": "live", "op": "end"})
        assert base64.b64decode(out["clips"][0]["pcm"]) == PCM * 2  # every bit of the turn, heard as a clip
        # still down when the next turn starts: it is kept and heard as a clip, and nothing is fed to a dead line
        fed = ears.fed
        await live.handle({"type": "live", "op": "start", "id": "t5", "pcm": base64.b64encode(PCM).decode()})
        await live.handle({"type": "live", "op": "end"})
        assert ears.fed == fed and base64.b64decode(out["clips"][1]["pcm"]) == PCM
        # it comes back: the phone is told, and turns are heard live again
        ears.down = False
        await ears.on_state(True)
        assert out["sent"][-1] == {"type": "live_state", "on": True, "end_ms": 3200}
        await live.handle({"type": "live", "op": "start", "id": "t6", "pcm": base64.b64encode(PCM).decode()})
        assert ears.fed == fed + PCM
    run(go())


def test_live_hearing_stays_off_where_it_cannot_help(tmp_path):
    async def go():
        live, out = talk(tmp_path)
        await live.handle({"type": "live", "op": "hello", "lang": "auto:ml-IN,en-IN"})  # Malayalam: Whisper's job
        assert out["sent"] == [{"type": "live_state", "on": False, "end_ms": 3200}] and not Ears.made
        live, out = talk(tmp_path, env={})  # no Deepgram key
        await live.handle({"type": "live", "op": "hello", "lang": "en-US"})
        assert out["sent"][0]["on"] is False and not Ears.made

        class Broken(Ears):
            async def start(self):
                raise RuntimeError("the call engine isn't installed")
        live, out = talk(tmp_path)
        live.make_ears = Broken
        await live.handle({"type": "live", "op": "hello", "lang": "en-US"})
        assert out["sent"][0]["on"] is False  # couldn't start: Talk mode carries on in clips
    run(go())


def test_the_real_pipeline_carries_audio_to_flux_and_its_words_back(monkeypatch):
    """Pipecat's own pipeline with Flux stood in for: the phone's audio reaches the hearing service, opted out of
    Deepgram's model improvement programme, and its partial and final words come back with the turn's language."""
    pytest.importorskip("pipecat")
    from pipecat.frames.frames import InputAudioRawFrame, InterimTranscriptionFrame, TranscriptionFrame
    from pipecat.processors.frame_processor import FrameProcessor
    from pipecat.services.deepgram.flux import stt as dgf
    from pipecat.transcriptions.language import Language

    made = []

    class FakeFlux(FrameProcessor):
        class Settings:
            def __init__(self, **kw):
                self.kw = kw

        def __init__(self, **kw):
            super().__init__()
            self.kw, self.got = kw, 0
            made.append(self)

        def event_handler(self, name):
            return lambda f: f

        async def process_frame(self, frame, direction):
            await super().process_frame(frame, direction)
            if isinstance(frame, InputAudioRawFrame):
                self.got += len(frame.audio)
                if self.got == 3200:
                    await self.push_frame(InterimTranscriptionFrame("hola buenos", "u", "", Language.ES))
                if self.got == 6400:
                    await self.push_frame(TranscriptionFrame("Hola, buenos días.", "u", "", Language.ES))
            await self.push_frame(frame, direction)

    monkeypatch.setattr(dgf, "DeepgramFluxSTTService", FakeFlux)
    got = []

    async def on_partial(text):
        got.append(("partial", text))

    async def on_final(text, lang):
        got.append(("final", text, lang))

    async def go():
        ears = liveears.LiveEars(on_partial, on_final, env=KEY, languages=["en", "es"], terms=["Kochi"])
        await ears.start()
        for _ in range(2):
            await ears.feed(PCM)
        for _ in range(40):
            await asyncio.sleep(0.05)
            if len(got) == 2:
                break
        alive = ears.alive
        await ears.close()
        return alive

    assert run(go())
    assert got == [("partial", "hola buenos"), ("final", "Hola, buenos días.", "es")]
    flux = made[0]
    assert flux.kw["mip_opt_out"] is True and flux.kw["api_key"] == "dg"  # never kept by Deepgram to train on
    settings = flux.kw["settings"].kw
    assert settings["model"] == calls.FLUX_MODEL and settings["keyterm"] == ["Kochi"]
    assert settings["language_hints"] == [Language.EN, Language.ES] and settings["eot_timeout_ms"] == 2500
    english = liveears.LiveEars(on_partial, on_final, env=KEY, languages=["en"])

    async def only_english():
        await english.start()
        await english.close()
    run(only_english())
    assert made[1].kw["settings"].kw["model"] == calls.FLUX_MODEL_EN and "language_hints" not in made[1].kw["settings"].kw


class FakeStream:
    def __init__(self, text):
        self.text = text

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def __aiter__(self):
        async def events():
            yield SimpleNamespace(type="text", text=self.text)
        return events()

    async def get_final_message(self):
        return SimpleNamespace(stop_reason="end_turn", model="claude-haiku-5-5",
                               usage=SimpleNamespace(input_tokens=10, output_tokens=5),
                               content=[SimpleNamespace(type="text", text=self.text)])


def test_over_the_phones_connection_a_live_turn_is_answered_without_the_unfinished_wait(monkeypatch):
    """The app's side of it: hello, a turn streamed, Flux ends it, the text comes back as "heard", and the app's
    "user" message with those words is answered at once, though they end without a full stop (which a clip's text
    would be held three seconds for)."""
    from fastapi.testclient import TestClient

    Ears.made.clear()
    monkeypatch.setattr(liveears, "LiveEars", Ears)
    monkeypatch.setattr(liveears, "available", lambda env=None: True)
    monkeypatch.setattr(server.voiceprint, "on", lambda memory: False)
    monkeypatch.setattr(agent.client.beta.messages, "stream", lambda **kw: FakeStream("Sunny, about thirty degrees."))
    with TestClient(server.app) as c:
        assert c.get("/health").json()["live"] is True
        with c.websocket_connect("/ws") as ws:
            ws.send_json({"type": "live", "op": "hello", "lang": "en-US"})
            assert ws.receive_json() == {"type": "live_state", "on": True, "end_ms": 3200}
            ws.send_json({"type": "live", "op": "start", "id": "t9", "pcm": base64.b64encode(PCM).decode(), "lang": "en-US"})
            ws.send_json(chunk())
            ws.send_json({"type": "diag_ping", "t": 1})  # the server has read the audio once this comes back
            assert ws.receive_json()["type"] == "diag_pong"
            ears = Ears.made[0]
            assert ears.fed == PCM * 2
            c.portal.call(ears.on_final, "what's the weather in Kochi and", "en")
            heard = ws.receive_json()
            assert heard["type"] == "heard" and heard["engine"] == "live" and heard["text"].endswith("Kochi and")
            ws.send_json({"type": "user", "text": heard["text"]})
            kinds = []
            while not kinds or kinds[-1] != "done":
                kinds.append(ws.receive_json()["type"])
            assert kinds == ["thinking", "say", "done"]  # no "waiting": Flux had already judged the turn finished


# --- noise removal (denoise.py): a switch, off unless asked for ------------------------------------------------
class Engine:
    """The model stood in for: halves every sample, a frame behind, so what went through it can be told apart."""

    def __init__(self):
        self.held, self.resets = [], 0

    def run(self, samples, rate):
        assert rate == 16_000
        ready, self.held = self.held, list(samples)
        return SimpleNamespace(samples=[s / 2 for s in ready], sample_rate=rate)

    def flush(self):
        ready, self.held = self.held, []
        return SimpleNamespace(samples=[s / 2 for s in ready], sample_rate=16_000)

    def reset(self):
        self.resets += 1
        self.held = []


def test_noise_removal_is_off_unless_switched_on_and_installed(tmp_path, monkeypatch):
    import sys

    from buddy import denoise
    assert denoise.setting({}) == "off" and denoise.setting({"BUDDY_NOISE_CANCEL": "talk"}) == "talk"
    assert denoise.setting({"BUDDY_NOISE_CANCEL": "on"}) == "talk" and denoise.setting({"BUDDY_NOISE_CANCEL": "nonsense"}) == "off"
    # the library is stood in for: it is installed on the server (deploy/noise.sh), not where the tests run
    monkeypatch.setitem(sys.modules, "sherpa_onnx", SimpleNamespace(OnlineSpeechDenoiser=object))
    monkeypatch.setenv("BUDDY_NOISE_MODEL", str(tmp_path / "missing.onnx"))
    assert not denoise.available() and not denoise.wanted({"BUDDY_NOISE_CANCEL": "talk"})  # switched on, no model
    assert not denoise.wanted({})
    (tmp_path / "model.onnx").write_bytes(b"x")
    monkeypatch.setenv("BUDDY_NOISE_MODEL", str(tmp_path / "model.onnx"))
    assert denoise.wanted({"BUDDY_NOISE_CANCEL": "talk"}) and not denoise.wanted({"BUDDY_NOISE_CANCEL": "off"})
    monkeypatch.setitem(sys.modules, "sherpa_onnx", SimpleNamespace())  # an older library, without the denoiser
    assert not denoise.available()
    monkeypatch.setitem(sys.modules, "sherpa_onnx", None)               # the library isn't installed at all
    assert not denoise.available() and not denoise.wanted({"BUDDY_NOISE_CANCEL": "talk"})


def test_the_cleaner_turns_the_phones_audio_into_the_models_and_back():
    from buddy import denoise
    cleaner = denoise.Cleaner(engine=Engine())
    loud = (1000).to_bytes(2, "little", signed=True) * 4 + (-2000).to_bytes(2, "little", signed=True) * 4
    assert cleaner.clean(loud) == b""        # the model works a frame behind
    assert cleaner.clean(loud) == (500).to_bytes(2, "little", signed=True) * 4 + (-1000).to_bytes(2, "little", signed=True) * 4
    assert len(cleaner.rest()) == len(loud) and cleaner.clean(b"") == b""
    cleaner.reset()
    assert cleaner.engine.resets == 1 and cleaner.rest() == b""


def test_when_switched_on_flux_hears_cleaned_audio_and_voice_match_the_microphones(tmp_path, monkeypatch):
    from buddy import denoise
    monkeypatch.setattr(denoise, "available", lambda: True)
    marked = b"\x10\x00" * 1600

    class Marker:
        resets = 0

        def clean(self, pcm):
            return marked if pcm else b""

        def reset(self):
            Marker.resets += 1

    async def go():
        live, out = talk(tmp_path, env={**KEY, "BUDDY_NOISE_CANCEL": "talk"})
        live.make_cleaner = Marker
        await live.handle({"type": "live", "op": "hello", "lang": "en-US"})
        await live.handle({"type": "live", "op": "start", "id": "n1", "pcm": base64.b64encode(PCM).decode()})
        await live.handle(chunk())
        ears = Ears.made[0]
        assert ears.fed == marked * 2 and Marker.resets == 1      # Flux hears the cleaned audio; each turn starts fresh
        await ears.on_final("Book a table for two", "en")
        await asyncio.sleep(0.05)
        assert out["voice_pcm"] == PCM * 2                        # voice match hears what the microphone heard
        # a turn that has to be heard the old way is heard unaltered too
        await live.handle({"type": "live", "op": "start", "id": "n2", "pcm": base64.b64encode(PCM).decode()})
        await live.handle({"type": "live", "op": "end"})
        assert base64.b64decode(out["clips"][0]["pcm"]) == PCM

        class Breaks(Marker):
            def clean(self, pcm):
                raise RuntimeError("the model stopped")
        live.cleaner = Breaks()
        before = len(ears.fed)
        await live.handle({"type": "live", "op": "start", "id": "n3", "pcm": base64.b64encode(PCM).decode()})
        await live.handle(chunk())
        assert ears.fed[before:] == PCM * 2 and live.cleaner is None  # it failed: speech goes on, unaltered

        off, out_off = talk(tmp_path)  # not switched on: nothing is made, nothing is changed
        off.make_cleaner = Marker
        await off.handle({"type": "live", "op": "hello", "lang": "en-US"})
        await off.handle({"type": "live", "op": "start", "id": "n4", "pcm": base64.b64encode(PCM).decode()})
        assert off.cleaner is None and Ears.made[-1].fed == PCM
    run(go())


def test_the_real_noise_model_takes_out_the_background(monkeypatch):
    """Only where the model file is present (it is not downloaded for tests): steady noise comes out much quieter,
    a voiced sound comes through, and it keeps up with speech many times over."""
    import math
    import os
    import random
    import time

    from buddy import denoise
    if not os.getenv("BUDDY_NOISE_MODEL") or not denoise.available():
        pytest.skip("the noise removal model isn't on this machine")
    rng = random.Random(1)
    seconds, rate = 4, 16_000
    samples = []
    for i in range(seconds * rate):
        t = i / rate
        voiced = sum(math.sin(2 * math.pi * 140 * k * t) / k for k in range(1, 12)) * 0.08 if math.sin(2 * math.pi * 3 * t) > 0 else 0.0
        samples.append(max(-1.0, min(1.0, voiced + rng.gauss(0, 0.05))))
    pcm = denoise.Cleaner._pcm(samples)
    cleaner = denoise.Cleaner()
    started, out = time.perf_counter(), b""
    for i in range(0, len(pcm), 3072):  # as the phone sends it: about a tenth of a second at a time
        out += cleaner.clean(pcm[i:i + 3072])
    out += cleaner.rest()
    took = time.perf_counter() - started
    assert len(out) == len(pcm) and took < seconds / 2

    def level(data, keep):
        values = [v for i, v in enumerate(denoise.Cleaner._floats(data)) if keep(i / rate)]
        return math.sqrt(sum(v * v for v in values) / len(values))
    quiet, sounding = (lambda t: math.sin(2 * math.pi * 3 * t) <= 0), (lambda t: math.sin(2 * math.pi * 3 * t) > 0)
    assert level(out, quiet) < level(pcm, quiet) / 4          # the noise between the sounds: far quieter
    assert level(out, sounding) > level(pcm, sounding) / 3    # the voiced sound: still there
