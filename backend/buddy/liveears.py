"""Live hearing for Talk mode, on the same engine the phone calls use.

Talk mode used to work in clips: the phone waited for a silence, sent the whole clip, the server checked the voice,
turned it into text, sent the text back, and held any sentence that looked unfinished for three seconds or more
before answering. Here the phone sends your speech while you are still saying it. Deepgram's Flux (through Pipecat,
as on calls) writes the words as they come and decides that you have finished from what you said, not from a timer:
a complete sentence is answered at once, an unfinished one is given time.

  phone  -> {"type": "live", "op": "hello", "lang": "auto"}          Talk mode is on (or its language changed)
  server -> {"type": "live_state", "on": true, "end_ms": 3200}       live hearing can be used (end_ms: how long the
                                                                      phone waits in silence before ending a turn
                                                                      itself)
  phone  -> {"type": "live", "op": "start", "id": "a1", "pcm": ...}  you started speaking (with the half second before)
  phone  -> {"type": "live", "op": "audio", "pcm": ..., "sp": true}  more of it, a tenth of a second at a time
  server -> {"type": "live_partial", "text": "what's the wea"}       the words so far
  server -> {"type": "heard", "text": ..., "engine": "live", ...}    you finished: the same message a clip gets
  phone  -> {"type": "live", "op": "end"}                            the phone's own silence limit passed first
  phone  -> {"type": "live", "op": "bye"}                            Talk mode is off

What goes where: your Talk-mode speech goes to Deepgram, opted out of its model improvement programme (as on calls),
instead of Groq. Listen-in never comes here: other people's speech stays on this server. The words then take the
same road as before: voice match, the Privacy Gateway, Claude. Buddy still answers with the phone's own voice.

If live hearing can't be used (no Deepgram key, the call engine not installed, a language Flux doesn't have,
Deepgram unreachable or silent), the turn is heard the old way from the same audio, so nothing said is lost.
BUDDY_TALK_LIVE=off switches it off.

Noise removal (denoise.py) is a switch, off unless BUDDY_NOISE_CANCEL=talk: Deepgram advises sending speech
unaltered. When on, the audio Flux hears is cleaned on this server first; voice match and the clip fallback still
get what the microphone heard.
"""

import asyncio
import base64
import contextlib
import importlib.util
import logging
import os
import time

from . import calls, denoise, gateway

log = logging.getLogger("buddy.liveears")

RATE = 16_000                    # the phone records 16 kHz, 16-bit, mono
EOT = 0.7                        # how sure Flux must be that you've finished
EOT_TIMEOUT_MS = 2500            # an unfinished sentence ("so what I was thinking is...") waits this long for the rest
PHONE_END_MS = EOT_TIMEOUT_MS + 700  # the phone ends the turn itself only after Flux has had its say
MAX_TURN_SECONDS = 60            # a turn longer than this is ended here (a stuck microphone, a radio)
PARTIAL_EVERY_S = 0.15           # the words so far are sent to the screen at most this often
VOICE_SECONDS = 6.0              # voice match looks at this much of the turn (its start)


def _env(name: str, env=None) -> str:
    return gateway._secret(env, name)


def _number(name: str, default: float, env=None) -> float:
    try:
        return float(_env(name, env) or default)
    except ValueError:
        return default


def available(env=None) -> bool:
    """Live hearing can run here: not switched off, a Deepgram key, and the call engine installed."""
    if (_env("BUDDY_TALK_LIVE", env) or "on").lower() in ("off", "0", "no"):
        return False
    return bool(_env("BUDDY_DEEPGRAM_KEY", env)) and importlib.util.find_spec("pipecat") is not None


def spoken_languages(setting: str) -> list[str]:
    """The languages the phone's Talk setting names ("en-US", "auto:ml-IN,en-IN"); for plain "auto" or nothing, the
    owner's own languages."""
    setting = (setting or "").strip().lower()
    if setting.startswith("auto:"):
        return [t.split("-")[0] for t in setting[5:].split(",") if t.strip()]
    if setting in ("", "auto"):
        from . import whisper  # noqa: PLC0415
        return whisper.known_languages()
    return [setting.split("-")[0]]


def hears(setting: str) -> bool:
    """Flux has every language this person speaks to Buddy. Malayalam, Tamil and the rest it doesn't have stay on the
    clip path, where Whisper does them well."""
    langs = spoken_languages(setting)
    return bool(langs) and all(lang in calls.CALL_LANGUAGES for lang in langs)


def _load_engine() -> None:
    """Import the call engine's pieces (once per server start; later calls return at once)."""
    if importlib.util.find_spec("pipecat") is None:
        return  # not installed: starting the ears says so
    import pipecat.pipeline.pipeline  # noqa: F401, PLC0415
    import pipecat.pipeline.worker  # noqa: F401, PLC0415
    import pipecat.services.deepgram.flux.stt  # noqa: F401, PLC0415
    import pipecat.workers.runner  # noqa: F401, PLC0415


class LiveEars:
    """Flux for one phone connection: audio in, the words so far and each finished turn out."""

    def __init__(self, on_partial, on_final, env=None, languages=("en",), terms=(), on_state=None):
        self.on_partial, self.on_final, self.on_state, self.env = on_partial, on_final, on_state, env
        self.languages, self.terms = list(languages), list(terms)
        self.worker = self.runner = None
        self.down = False  # Deepgram's connection is down right now (the engine reconnects by itself)
        self.failed = ""

    @property
    def alive(self) -> bool:
        """The engine is running (its connection may be down for a moment)."""
        return self.runner is not None and not self.runner.done() and not self.failed

    @property
    def usable(self) -> bool:
        """A turn started now would be heard live."""
        return self.alive and not self.down

    async def start(self) -> None:
        from pipecat.frames.frames import InterimTranscriptionFrame, TranscriptionFrame  # noqa: PLC0415
        from pipecat.pipeline.pipeline import Pipeline  # noqa: PLC0415
        from pipecat.pipeline.worker import PipelineParams, PipelineWorker  # noqa: PLC0415
        from pipecat.processors.frame_processor import FrameProcessor  # noqa: PLC0415
        from pipecat.workers.runner import WorkerRunner  # noqa: PLC0415

        calls._quiet_pipecat()
        ears = self

        class Words(FrameProcessor):
            async def process_frame(self, frame, direction):
                await super().process_frame(frame, direction)
                if isinstance(frame, TranscriptionFrame):
                    lang = str(getattr(frame.language, "value", frame.language) or "")
                    await ears.on_final(frame.text or "", lang)
                elif isinstance(frame, InterimTranscriptionFrame):
                    await ears.on_partial(frame.text or "")
                await self.push_frame(frame, direction)

        langs = [c for c in self.languages if c in calls.CALL_LANGUAGES] or ["en"]
        # Leans towards the languages this person speaks (English first), so an accent isn't taken for another one.
        many = ["en"] + [c for c in langs if c != "en"] if len(langs) > 1 or langs != ["en"] else ["en"]
        stt = calls.flux_hearing(self.env, languages=list(calls.CALL_LANGUAGES) if len(many) > 1 else ["en"],
                                 hints=many, terms=self.terms, eot=_number("BUDDY_TALK_EOT", EOT, self.env),
                                 eot_timeout_ms=int(_number("BUDDY_TALK_EOT_TIMEOUT_MS", EOT_TIMEOUT_MS, self.env)))

        def fail(why) -> None:  # the engine itself stopped: over for this connection
            self.failed = str(why)[:200] or "stopped"

        async def state(down: bool) -> None:
            changed, self.down = down != self.down, down
            if changed and self.on_state is not None:
                with contextlib.suppress(Exception):
                    await self.on_state(not down)

        with contextlib.suppress(Exception):  # the events are a courtesy: without them, a silent Flux is still caught
            @stt.event_handler("on_connected")
            async def _connected(_service):
                await state(False)

            @stt.event_handler("on_disconnected")
            async def _disconnected(_service):
                await state(True)

            @stt.event_handler("on_connection_error")
            async def _refused(_service, error):  # the engine tries again by itself; until then, turns go as clips
                log.warning("live hearing: Deepgram refused or dropped the connection (%s)", str(error)[:200])
                await state(True)

        self.worker = PipelineWorker(Pipeline([stt, Words()]), params=PipelineParams(
            audio_in_sample_rate=RATE, audio_out_sample_rate=RATE), idle_timeout_secs=None)
        self.runner = asyncio.ensure_future(WorkerRunner(handle_sigint=False).run(self.worker))
        self.runner.add_done_callback(lambda task: fail(task.exception() if not task.cancelled() and task.exception()
                                                        else "stopped"))

    async def feed(self, pcm: bytes) -> None:
        from pipecat.frames.frames import InputAudioRawFrame  # noqa: PLC0415
        if pcm:
            await self.worker.queue_frame(InputAudioRawFrame(audio=pcm, sample_rate=RATE, num_channels=1))

    async def close(self) -> None:
        if self.worker is not None:
            with contextlib.suppress(Exception):
                await self.worker.cancel()
        if self.runner is not None and not self.runner.done():
            self.runner.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self.runner


class LiveTalk:
    """One phone connection's live Talk hearing: its turns in, heard text out.

    send(dict) goes to the phone. hear_clip(msg) hears a turn the old way (the same message a clip sends).
    voice_ok(pcm) says whether the voice is the owner's ("you", "not_you" or "off")."""

    def __init__(self, memory, send, hear_clip, voice_ok, env=None, make_ears=None, terms=(), make_cleaner=None):
        self.memory, self.send, self.hear_clip, self.voice_ok, self.env = memory, send, hear_clip, voice_ok, env
        self.make_ears = make_ears or (lambda **kw: LiveEars(**kw))
        self.make_cleaner = make_cleaner or denoise.Cleaner
        self.terms = list(terms)
        self.ears = None
        self.cleaner = None  # noise removal, when switched on (denoise.py)
        self.on = False
        self.setting = ""
        self.turn: dict | None = None
        self.last = {"text": "", "at": -1e9}  # the last text heard live, and when: it needs no "is it finished?" wait
        self._partial_at = 0.0
        self._busy = asyncio.Lock()

    # --- from the phone ----------------------------------------------------------------------------------
    async def handle(self, msg: dict) -> None:
        op = msg.get("op")
        if op == "hello":
            await self._hello(str(msg.get("lang") or ""))
        elif op == "start":
            await self._start(msg)
        elif op == "audio":
            await self._audio(msg)
        elif op == "end":
            await self._end()
        elif op == "cancel":
            self.turn = None  # Buddy began to speak on the loudspeaker, or the mode changed: nothing to answer
        elif op == "bye":
            await self.close()
            await self._say_state(False)

    async def _hello(self, setting: str) -> None:
        self.setting = setting
        want = available(self.env) and hears(setting)
        if want and (self.ears is None or not self.ears.alive):
            await self._open()
        elif not want:
            await self.close()
        await self._say_state(want and self.ears is not None and self.ears.usable)

    async def _ears_state(self, up: bool) -> None:
        """Deepgram's connection dropped, or came back: the phone goes to clips, or back to live turns."""
        if self.ears is not None:
            await self._say_state(up and self.ears.alive)

    async def _open(self) -> None:
        await self.close()
        try:
            await asyncio.to_thread(_load_engine)  # its first import takes a moment: not on the server's main loop
            self.ears = self.make_ears(on_partial=self._partial, on_final=self._final, env=self.env,
                                       languages=spoken_languages(self.setting), terms=self.terms,
                                       on_state=self._ears_state)
            await self.ears.start()
        except Exception as e:  # not installed, a bad key: Talk mode carries on in clips
            log.warning("live hearing couldn't start (%s: %s): Talk mode uses clips", type(e).__name__, str(e)[:200])
            self.ears = None
            return
        self.cleaner = None
        if denoise.wanted(self.env):
            try:
                self.cleaner = await asyncio.to_thread(self.make_cleaner)
            except Exception as e:  # the hearing works without it
                log.warning("noise removal couldn't start (%s: %s): speech is heard as it is", type(e).__name__,
                            str(e)[:200])

    async def _say_state(self, on: bool) -> None:
        self.on = on
        await self.send({"type": "live_state", "on": on,
                         "end_ms": int(_number("BUDDY_TALK_EOT_TIMEOUT_MS", EOT_TIMEOUT_MS, self.env)) + 700})

    async def _start(self, msg: dict) -> None:
        now = time.monotonic()
        self.turn = {"id": msg.get("id"), "pcm": bytearray(), "lang": str(msg.get("lang") or self.setting),
                     "ns": bool(msg.get("ns")), "mic": msg.get("mic"), "started": now, "last_speech": now,
                     "partial": "", "done": False, "live": bool(self.ears is not None and self.ears.usable)}
        if not self.turn["live"] and self.on:  # the phone thought live hearing was on: it isn't, for now
            await self._say_state(False)
        if self.cleaner is not None:
            with contextlib.suppress(Exception):
                self.cleaner.reset()
        await self._audio(msg)

    async def _audio(self, msg: dict) -> None:
        turn = self.turn
        if turn is None or turn["done"] or not msg.get("pcm"):
            return
        try:
            pcm = base64.b64decode(msg["pcm"])
        except (ValueError, TypeError):
            return
        turn["pcm"] += pcm
        if msg.get("sp", True):
            turn["last_speech"] = time.monotonic()
        if turn["live"]:
            try:
                if not self.ears.usable:
                    raise RuntimeError(self.ears.failed or "the connection dropped")
                await self.ears.feed(await self._cleaned(pcm))
            except Exception as e:  # Deepgram dropped out mid-turn: the turn is still being kept, and heard at its end
                log.warning("live hearing stopped mid-turn (%s): this turn is heard as a clip", str(e)[:120])
                turn["live"] = False
                await self._say_state(False)
        if len(turn["pcm"]) > MAX_TURN_SECONDS * RATE * 2:
            await self._end()

    async def _cleaned(self, pcm: bytes) -> bytes:
        """What Flux is given: the audio as it is, or with the background taken out when that is switched on. If the
        cleaning fails it is dropped for this connection, and the speech goes on unaltered."""
        if self.cleaner is None:
            return pcm
        try:
            return await asyncio.to_thread(self.cleaner.clean, pcm)
        except Exception as e:
            log.warning("noise removal failed (%s: %s): speech is heard as it is", type(e).__name__, str(e)[:120])
            self.cleaner = None
            return pcm

    async def _end(self) -> None:
        """The phone's own silence limit passed (or the turn got too long) before Flux called it: hear the turn the
        old way, from the audio kept here."""
        turn = self.turn
        if turn is None or turn["done"]:
            return
        turn["done"], self.turn = True, None
        if turn["live"]:
            log.info("live hearing gave no end for a turn of %.1f s: heard as a clip", len(turn["pcm"]) / (RATE * 2))
        await self.hear_clip({"type": "audio", "pcm": base64.b64encode(bytes(turn["pcm"])).decode(), "mode": "talk",
                              "lang": turn["lang"], "id": turn["id"], "ns": turn["ns"], "mic": turn["mic"],
                              "_got": time.monotonic()})

    # --- from Flux ---------------------------------------------------------------------------------------
    async def _partial(self, text: str) -> None:
        turn, now = self.turn, time.monotonic()
        if turn is None or turn["done"] or not text.strip():
            return
        turn["partial"] = text
        if now - self._partial_at >= PARTIAL_EVERY_S:
            self._partial_at = now
            await self.send({"type": "live_partial", "text": text.strip(), "id": turn["id"]})

    async def _final(self, text: str, lang: str = "") -> None:
        turn, text = self.turn, " ".join((text or "").split())
        if turn is None or turn["done"] or not turn["live"] or not text:
            return  # nothing open (the phone ended it first), or an empty turn: the phone's own limit ends that one
        turn["done"], self.turn = True, None
        asyncio.ensure_future(self._heard(turn, text, lang))  # voice match takes a moment: never hold Flux's loop

    async def _heard(self, turn: dict, text: str, lang: str) -> None:
        from . import diagnostics, usage  # noqa: PLC0415
        async with self._busy:
            now = time.monotonic()
            pcm = bytes(turn["pcm"])
            seconds = len(pcm) / (RATE * 2)
            wait_ms = round((now - turn["last_speech"]) * 1000)
            with contextlib.suppress(Exception):
                usage.count(self.memory, "speech:live")
                usage.log_call(self.memory, provider="deepgram", model=calls.FLUX_MODEL, purpose="speech", seconds=seconds,
                               cost=round(seconds / 60 * calls.price("BUDDY_CALL_PRICE_LISTEN_MIN", self.env), 6))
            who = "off"
            try:
                who = await self.voice_ok(pcm[:int(VOICE_SECONDS * RATE * 2)])
            except Exception:
                log.warning("live hearing: voice match failed; the turn is taken as yours", exc_info=True)
            base = {"id": turn["id"], "engine": "live", "wait_ms": wait_ms,
                    "stt_ms": round((time.monotonic() - now) * 1000)}
            heard = who != "not_you"
            with contextlib.suppress(Exception):
                diagnostics.server_event(self.memory, "speech", "stt", engine="live", ms=wait_ms, lang=lang or "",
                                         mode="talk", verdict="keep" if heard else "not_you", heard=heard)
            if not heard:  # Talk mode answers only the owner's voice
                await self.send({"type": "heard", "text": "", "missed": "not_you", **base})
                return
            self.last = {"text": text, "at": time.monotonic()}
            await self.send({"type": "heard", "text": text, "lang": calls.heard_language(text, lang) or lang.split("-")[0],
                             **base})

    def just_heard(self, text: str, within: float = 5.0) -> bool:
        """These words came from live hearing a moment ago: Flux already judged the turn finished."""
        return bool(text) and text.strip() == self.last["text"] and time.monotonic() - self.last["at"] < within

    async def close(self) -> None:
        ears, self.ears, self.turn, self.cleaner = self.ears, None, None, None
        if ears is not None:
            with contextlib.suppress(Exception):
                await ears.close()
