"""Phone calls for the owner (Buddy Connect, Calls group).

Plivo places the call and streams its audio to this server. Pipecat runs the conversation here: Silero (on this
server) hears when someone is talking, Deepgram turns their words into text, Claude's quick model decides what to
say, and ElevenLabs says it in a natural female voice. Cost: audio goes to Deepgram only while someone is talking,
so a long hold costs little more than the phone line; Claude is asked only when a person (or a phone menu) has
finished speaking, and a recording heard before ("your call is important to us") isn't sent to Claude again.

Kinds of call:
  appointment   book (or move) an appointment, e.g. with a doctor, within the times the owner allowed, using
                their calendar to avoid clashes; Buddy may give name, callback number, date of birth and insurance.
  question      ask something simple (hours, whether they take an insurance) and report back.
  hold_connect  get through the phone menu, wait on hold, and when a real person answers, ring the owner and join
                them in (banks and card companies: only the account holder can be verified, never Buddy).

Rules, in code and in the call's instructions: every call is approved by the owner first (thrift.ask_action);
Buddy introduces itself truthfully as the owner's assistant, never claims to be the owner, and never denies being
an AI when asked; it shares only the allowed facts (never SSN, card numbers, passwords); it books only inside the
allowed times and never pays, cancels or agrees to fees; calls aren't recorded (only a written summary of the
outcome is kept, 180 days). At most CALLS_PER_DAY calls a day; 15 minutes each (90 for hold_connect).

Plivo reaches only /calls/* on the public address (Tailscale Funnel on port 8443, set up by deploy/calls.sh);
every request there carries the call's random token in its path, and Plivo's own requests its signature too.
"""

import asyncio
import base64
import contextlib
import hashlib
import hmac
import json
import logging
import re
import secrets
import urllib.parse
from datetime import datetime, timedelta, timezone
from xml.sax.saxutils import escape, quoteattr

from . import gateway
from .config import settings
from .memory import Memory
from .tools import ToolError, local_now

log = logging.getLogger("buddy.calls")
CALLS_PER_DAY = 5
LIMIT_S = {"appointment": 15 * 60, "question": 15 * 60, "hold_connect": 90 * 60}
KEEP_DAYS = 180
DEFAULT_VOICE = "21m00Tcm4TlvDq8ikWAM"  # ElevenLabs' "Rachel" (female); BUDDY_CALL_VOICE changes it
TTS_MODEL = "eleven_flash_v2_5"  # ElevenLabs' fast, cheap voice model; BUDDY_CALL_VOICE_MODEL picks another (e.g. a newer Turbo)
STT_MODEL = "nova-3-general"
KINDS = ("appointment", "question", "hold_connect")
QUIET_START_S = 4.0  # nobody speaks this long after the pick-up: Buddy speaks first
SETUP = "sudo bash /opt/buddy/deploy/calls.sh"

SCHEMA = """
CREATE TABLE IF NOT EXISTS phone_calls (
    id INTEGER PRIMARY KEY,
    created_at TEXT NOT NULL,
    who TEXT NOT NULL, number TEXT NOT NULL, kind TEXT NOT NULL, goal TEXT NOT NULL,
    windows TEXT NOT NULL DEFAULT '', token TEXT NOT NULL, call_sid TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'planned',      -- planned | calling | answered | connected | done | failed
    outcome TEXT NOT NULL DEFAULT '',            -- the written summary the owner gets; no recording, no transcript
    minutes REAL NOT NULL DEFAULT 0
);
"""


def _ready(memory: Memory) -> None:
    memory.db.executescript(SCHEMA)
    cutoff = (datetime.now(timezone.utc) - timedelta(days=KEEP_DAYS)).isoformat()
    memory.db.execute("DELETE FROM phone_calls WHERE created_at < ?", (cutoff,))


def _env(name: str, env=None) -> str:
    return gateway._secret(env, name)


def owner(env=None) -> dict:
    """What a call may say about the owner (the server's settings, saved by deploy/calls.sh)."""
    return {"name": _env("BUDDY_OWNER_FULL_NAME", env) or settings.user_name,
            "phone": _env("BUDDY_OWNER_PHONE", env), "dob": _env("BUDDY_OWNER_DOB", env),
            "insurance": _env("BUDDY_OWNER_INSURANCE", env)}


def normalise(number: str) -> str:
    digits = re.sub(r"\D", "", number or "")
    if len(digits) == 10:
        digits = "1" + digits
    if not 11 <= len(digits) <= 15:
        raise ToolError(f"{number!r} doesn't look like a phone number.")
    return "+" + digits


def get(memory: Memory, call_id: int) -> dict:
    _ready(memory)
    row = memory.db.execute("SELECT * FROM phone_calls WHERE id = ?", (call_id,)).fetchone()
    if not row:
        raise ToolError(f"No call {call_id}.")
    return dict(row)


def update(memory: Memory, call_id: int, **fields) -> None:
    sets = ", ".join(f"{k} = ?" for k in fields)
    memory.db.execute(f"UPDATE phone_calls SET {sets} WHERE id = ?", (*fields.values(), call_id))
    memory.db.commit()


def plan(memory: Memory, who: str, number: str, kind: str, goal: str, windows: str = "") -> dict:
    _ready(memory)
    if kind not in KINDS:
        raise ToolError("The kind of call is appointment, question or hold_connect.")
    if not (goal or "").strip():
        raise ToolError("What is the call for?")
    today = local_now().date().isoformat()
    n = memory.db.execute("SELECT COUNT(*) FROM phone_calls WHERE created_at >= ? AND status != 'planned'",
                          (today,)).fetchone()[0]
    if n >= CALLS_PER_DAY:
        raise ToolError(f"I've made {CALLS_PER_DAY} calls today already; let's continue tomorrow.")
    cur = memory.db.execute(
        "INSERT INTO phone_calls (created_at, who, number, kind, goal, windows, token) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (datetime.now(timezone.utc).isoformat(), who.strip()[:80], normalise(number), kind, goal.strip()[:500],
         (windows or "").strip()[:200], secrets.token_urlsafe(24)))
    memory.db.commit()
    return get(memory, cur.lastrowid)


def ready(env=None) -> str:
    """'' when calls can be made, else what's missing and how to fix it."""
    missing = [n for n in ("plivo", "deepgram", "elevenlabs") if not gateway.configured(n, env)]
    if missing or not _env("BUDDY_CALLS_PUBLIC_URL", env).startswith("https://"):
        return f"Calls aren't set up yet ({', '.join(missing) or 'public address'}): {SETUP}"
    return ""


def _public(env=None) -> str:
    base = _env("BUDDY_CALLS_PUBLIC_URL", env).rstrip("/")
    if not base.startswith("https://"):
        raise ToolError(f"Calls aren't set up yet: {SETUP}")
    return base


def url_for(call: dict, what: str, env=None) -> str:
    """The public address Plivo uses for one step of one call (the token is in the path, not a query)."""
    return f"{_public(env)}/calls/{what}/{call['id']}/{call['token']}"


# --- Plivo -----------------------------------------------------------------------------------------------------
def _plivo(memory, path: str, form: dict | None = None, *, method: str = "", env=None, fetch=None):
    auth_id = _env("BUDDY_PLIVO_AUTH_ID", env)
    return gateway.call(memory, "plivo", f"/v1/Account/{auth_id}{path}", json_body=form, method=method, env=env,
                        fetch=fetch, purpose="phone call", personal=True)


def plivo_health(memory=None, env=None, fetch=None) -> None:
    """Buddy Connect's Test: read the Plivo account (no call is made)."""
    _plivo(memory, "/", env=env, fetch=fetch)


def caller_id(env=None) -> str:
    """The number the other side sees: the owner's own mobile when they chose it (BUDDY_CALL_SHOW_MY_NUMBER=1, after
    verifying it as a caller ID in Plivo's console), else Buddy's Plivo number."""
    if _env("BUDDY_CALL_SHOW_MY_NUMBER", env) == "1" and owner(env)["phone"]:
        return normalise(owner(env)["phone"])
    return _env("BUDDY_PLIVO_NUMBER", env)


def dial(memory: Memory, call: dict, env=None, fetch=None) -> str:
    """Ask Plivo to place the call. Plivo then asks /calls/answer for what to do when they pick up."""
    got = _plivo(memory, "/Call/", {
        "from": caller_id(env).lstrip("+"), "to": call["number"].lstrip("+"),
        "answer_url": url_for(call, "answer", env), "answer_method": "POST",
        "hangup_url": url_for(call, "hangup", env), "hangup_method": "POST",
        "time_limit": LIMIT_S[call["kind"]], "ring_timeout": 45}, env=env, fetch=fetch)
    request = str((got or {}).get("request_uuid", ""))
    update(memory, call["id"], call_sid=request, status="calling")
    return request


def answer_xml(memory: Memory, call: dict, form: dict, env=None) -> str:
    """They picked up: stream the call's audio both ways to /calls/stream."""
    update(memory, call["id"], call_sid=str(form.get("CallUUID") or call["call_sid"]), status="answered")
    stream = "wss://" + url_for(call, "stream", env).split("://", 1)[1]
    return ('<?xml version="1.0" encoding="UTF-8"?><Response><Stream bidirectional="true" keepCallAlive="true" '
            f'contentType="audio/x-mulaw;rate=8000" streamTimeout="{LIMIT_S[call["kind"]]}">'
            + escape(stream) + "</Stream></Response>")


def connect_xml(call: dict, env=None) -> str:
    """hold_connect: a person answered; ring the owner and join them in."""
    phone = owner(env)["phone"]
    if call["kind"] != "hold_connect" or not phone:
        return '<?xml version="1.0" encoding="UTF-8"?><Response><Hangup/></Response>'
    return ('<?xml version="1.0" encoding="UTF-8"?><Response><Dial callerId='
            + quoteattr(_env("BUDDY_PLIVO_NUMBER", env)) + ' timeout="40"><Number>' + escape(normalise(phone))
            + "</Number></Dial></Response>")


def press(memory: Memory, call_uuid: str, digits: str, env=None, fetch=None) -> None:
    _plivo(memory, f"/Call/{call_uuid}/DTMF/", {"digits": digits, "leg": "aleg"}, env=env, fetch=fetch)


def transfer(memory: Memory, call: dict, env=None, fetch=None) -> None:
    _plivo(memory, f"/Call/{call['call_sid']}/", {"legs": "aleg", "aleg_url": url_for(call, "connect", env),
                                                   "aleg_method": "POST"}, env=env, fetch=fetch)


def hangup(memory: Memory, call_uuid: str, env=None, fetch=None) -> None:
    try:
        _plivo(memory, f"/Call/{call_uuid}/", method="DELETE", env=env, fetch=fetch)
    except gateway.ConnectError as e:
        if e.status != 404:  # 404: the call already ended
            raise


def signature_ok(url: str, params: dict, nonce: str, signature: str, env=None) -> bool:
    """Plivo's X-Plivo-Signature-V3 for a POST: HMAC-SHA256 (auth token) of the URL, '?', the fields sorted by
    name as name+value, then '.' and the nonce. The header may hold several signatures, comma-separated."""
    token = _env("BUDDY_PLIVO_AUTH_TOKEN", env)
    if not (token and nonce and signature):
        return False
    parts = urllib.parse.urlparse(url)
    base = urllib.parse.urlunparse((parts.scheme, parts.netloc, parts.path, "", "", ""))
    query = urllib.parse.parse_qs(parts.query, keep_blank_values=True)
    if query:
        base += "?" + "&".join(f"{k}={v}" for k in sorted(query) for v in sorted(query[k])) + ("." if params else "")
    elif params:
        base += "?"
    base += "".join(f"{k}{params[k]}" for k in sorted(params))
    want = base64.b64encode(hmac.new(token.encode(), f"{base}.{nonce}".encode(), hashlib.sha256).digest()).decode()
    return any(hmac.compare_digest(want, s.strip()) for s in signature.split(","))


def token_ok(memory: Memory, call_id: int, token: str, *, ended_ok: bool = False) -> dict | None:
    """The call this path's token belongs to, while it's live (the hang-up report may also come after the end)."""
    try:
        call = get(memory, call_id)
    except ToolError:
        return None
    closed = ("planned",) if ended_ok else ("planned", "done", "failed")
    if call["status"] in closed or not hmac.compare_digest(call["token"], token or ""):
        return None
    return call


def hung_up(memory: Memory, call: dict, form: dict) -> str:
    """Plivo's hang-up report. Returns a message for the owner when the call didn't happen."""
    status = str(form.get("CallStatus") or "").lower()
    cause = str(form.get("HangupCause") or "")
    minutes = round(int(form.get("BillDuration") or form.get("Duration") or 0) / 60, 1)
    done = call["status"] in ("done", "connected")
    answered = call["status"] == "answered"
    update(memory, call["id"], status="done" if done or answered else "failed", minutes=minutes)
    if done:
        return ""
    if answered:
        if not call["outcome"]:
            update(memory, call["id"], outcome="The call ended before anything was settled.")
        return f"The call to {call['who']} ended before anything was settled. Want me to try again?"
    why = {"busy": "busy", "no-answer": "no answer", "timeout": "no answer", "cancel": "cancelled"}.get(
        status, cause.replace("_", " ").lower() or status or "it didn't connect")
    return f"The call to {call['who']} didn't go through ({why}). Want me to try again later?"


# --- the conversation ------------------------------------------------------------------------------------------
def _busy(memory: Memory) -> str:
    try:
        from .connectors.hub import agenda  # noqa: PLC0415
        return "; ".join(f"{e['when']} {e['title']}" for e in agenda(memory, 21)) or "nothing on the calendar"
    except Exception:
        return "calendar unknown: offer times and say you'll confirm"


def system_prompt(memory: Memory, call: dict, env=None) -> str:
    o = owner(env)
    first = o["name"].split()[0] if o["name"] else "them"
    may = [f"full name: {o['name']}"]
    if o["phone"]:
        may.append(f"callback number: {o['phone']}")
    if call["kind"] == "appointment":
        if o["dob"]:
            may.append(f"date of birth: {o['dob']}")
        if o["insurance"]:
            may.append(f"insurance: {o['insurance']}")
    today = local_now().strftime("%A %d %B %Y, %I:%M %p")
    kind = {
        "appointment": "Book the appointment. Agree a time only inside the allowed times and when the calendar is "
                       "free; repeat it back to confirm (day, date, time, with whom, anything to bring). If you reach "
                       f"voicemail, leave a short message asking them to call {first} back on the callback number, "
                       "then end.",
        "question": "Ask the question, note the answer exactly, thank them and end. If you reach voicemail, end "
                    "without a message.",
        "hold_connect": "Get through the phone menu to a real person for this goal (press keys, or say the option), "
                        "wait patiently on hold, and the moment a real person answers say: 'Hi, thank you. One "
                        f"moment please, I'm connecting {first} now.' and connect. Don't discuss the account yourself; "
                        "if the menu asks for an account or card number, press 0 or say 'representative'.",
    }[call["kind"]]
    return f"""You are on a live phone call as {o['name']}'s assistant, calling {call['who']}. \
Today is {today}. Goal: {call['goal']}. {('Allowed times: ' + call['windows'] + '.') if call['windows'] else ''}
Calendar (busy): {_busy(memory)}.
{kind}

What you hear is speech-to-text of the other side, so it may have small errors. "[No one has spoken yet]" means \
they picked up and are silent: greet them.
How you sound: a warm, natural, efficient human assistant on the phone. Short sentences, one thing at a time, \
plain words, small acknowledgements ("Sure", "Got it", "Perfect"). Spell names and read numbers digit by digit \
when asked. If they say "please hold", say "Of course" and wait. On hold music or a recording, stay silent.
Introduce yourself as: "Hi, this is {o['name']}'s assistant, calling on {first}'s behalf." Never say you are \
{first}. If anyone asks whether you're a real person, a robot or an AI, answer honestly: "I'm an AI assistant \
calling for {first}." Never deny it.
You may share only: {'; '.join(may)}. Never give a Social Security number, card or account numbers, passwords \
or anything not listed. If they need something else, say you'll check with {first} and call back. Never pay, \
cancel, or agree to fees or anything outside the goal. The call isn't recorded.

Reply ONLY with one line of JSON: {{"say": "what to say now ('' to stay silent)", "digits": "keys to press, e.g. \
'1', or ''", "action": "continue" | "connect" | "end", "outcome": "when ending or connecting: one or two \
sentences for {first} (what was agreed, date and time, what to bring, reference numbers)"}}. \
Use "connect" only when a real person has answered on a hold_connect call."""


def parse(text: str) -> dict:
    m = None
    for m in re.finditer(r"\{.*\}", text or "", re.S):
        pass
    try:
        got = json.loads(m.group(0)) if m else {}
    except json.JSONDecodeError:
        got = {}
    if not isinstance(got, dict) or not got:
        got = {"say": (text or "").strip()[:400], "action": "continue"}
    got["say"] = str(got.get("say") or "").strip()[:600]
    got["digits"] = re.sub(r"[^0-9*#]", "", str(got.get("digits") or ""))[:20]
    got["action"] = got.get("action") if got.get("action") in ("continue", "connect", "end") else "continue"
    got["outcome"] = str(got.get("outcome") or "").strip()[:600]
    return got


async def think(memory: Memory, call: dict, history: list[dict], env=None) -> dict:
    """Claude's next move on the call (the quick model: answers must come fast)."""
    from . import usage  # noqa: PLC0415
    from .config import claude_client  # noqa: PLC0415
    response = await claude_client().messages.create(model=settings.fast_model, max_tokens=300,
                                                     system=system_prompt(memory, call, env), messages=history)
    if hasattr(response, "usage"):
        usage.record(memory, response.model, response.usage, purpose="phone_call", response=response)
    return parse(" ".join(b.text for b in response.content if getattr(b, "type", "") == "text"))


class Mind:
    """The call's conversation, apart from the audio: what they said in, Buddy's moves out (say, press keys,
    connect the owner, end). The words live only in this object while the call lasts."""

    def __init__(self, memory: Memory, call: dict, *, say, press_keys, finish, brain=None, env=None):
        self.memory, self.call, self.env = memory, call, env
        self.say, self.press_keys, self.finish = say, press_keys, finish
        self.brain = brain or think
        self.history: list[dict] = []
        self.recordings: set[str] = set()  # things heard that needed no answer (hold messages): not re-sent
        self.ended = False
        self.heard_any = self.spoke_any = False

    @staticmethod
    def _key(text: str) -> str:
        return " ".join(re.sub(r"[^a-z0-9 ]", "", text.lower()).split())

    async def heard(self, said: str) -> None:
        said = (said or "").strip()
        if not said or self.ended:
            return
        if said != "[No one has spoken yet]":
            self.heard_any = True
        key = self._key(said)
        if len(key) > 25 and key in self.recordings:
            return  # the same hold message again: nothing to decide
        self.history.append({"role": "user", "content": f"[They said] {said}"})
        move = await self.brain(self.memory, self.call, self._history(), self.env)
        if self.ended:
            return
        if move["action"] == "connect" and self.call["kind"] != "hold_connect":
            move["action"] = "continue"  # only hold_connect calls may ring the owner in
        self.history.append({"role": "assistant", "content": json.dumps(move)})
        if not move["say"] and not move["digits"] and move["action"] == "continue":
            self.recordings.add(key)
        if move["digits"]:
            await self.press_keys(move["digits"])
        if move["say"]:
            self.spoke_any = True
            await self.say(move["say"])
        if move["action"] in ("connect", "end"):
            self.ended = True
            await self.finish(move)

    def _history(self) -> list[dict]:
        h = self.history[-40:]
        while h and h[0]["role"] != "user":
            h = h[1:]
        merged: list[dict] = []  # two heard lines in a row (they spoke twice before Buddy answered): one turn
        for m in h:
            if merged and merged[-1]["role"] == m["role"] == "user":
                merged[-1] = {"role": "user", "content": merged[-1]["content"] + "\n" + m["content"]}
            else:
                merged.append(m)
        return merged


def outcome_message(call: dict, move: dict, env=None) -> str:
    if move["action"] == "connect":
        return (f"{call['who']}: a person is on the line. I'm ringing you now from Buddy's Plivo number; pick up "
                "to take the call.")
    return f"Call to {call['who']} done. {move.get('outcome') or ''}".strip()


def gate_class():
    """Pipecat's pieces are imported only when a call starts (they're installed by deploy/calls.sh)."""
    from pipecat.frames.frames import (  # noqa: PLC0415
        InputAudioRawFrame, VADUserStartedSpeakingFrame, VADUserStoppedSpeakingFrame)
    from pipecat.processors.frame_processor import FrameDirection, FrameProcessor  # noqa: PLC0415

    class Gate(FrameProcessor):
        """Sends audio on to Deepgram only while someone is talking (plus the half second before Silero noticed),
        so hold music and silence cost nothing."""

        PRE_ROLL_S = 0.6

        def __init__(self):
            super().__init__()
            self.open, self.held, self.held_s = False, [], 0.0

        async def process_frame(self, frame, direction):
            await super().process_frame(frame, direction)
            if isinstance(frame, InputAudioRawFrame) and direction == FrameDirection.DOWNSTREAM:
                if self.open:
                    await self.push_frame(frame, direction)
                    return
                self.held.append(frame)
                self.held_s += len(frame.audio) / (2 * frame.sample_rate * max(frame.num_channels, 1))
                while self.held and self.held_s > self.PRE_ROLL_S + 1e-6:
                    old = self.held.pop(0)
                    self.held_s -= len(old.audio) / (2 * old.sample_rate * max(old.num_channels, 1))
                return
            if isinstance(frame, VADUserStartedSpeakingFrame) and direction == FrameDirection.DOWNSTREAM:
                self.open = True
                for old in self.held:
                    await self.push_frame(old, direction)
                self.held, self.held_s = [], 0.0
            await self.push_frame(frame, direction)
            if isinstance(frame, VADUserStoppedSpeakingFrame) and direction == FrameDirection.DOWNSTREAM:
                self.open = False
    return Gate


async def run_stream(memory: Memory, websocket, call: dict, deliver=None, env=None) -> None:
    """One call's audio, both ways, until it ends: Plivo's stream -> Silero (who is speaking) -> the cost gate ->
    Deepgram (words) -> Mind (Claude) -> ElevenLabs (voice) -> Plivo."""
    from pipecat.audio.vad.silero import SileroVADAnalyzer  # noqa: PLC0415
    from pipecat.audio.vad.vad_analyzer import VADParams  # noqa: PLC0415
    from pipecat.frames.frames import (  # noqa: PLC0415
        BotStartedSpeakingFrame, BotStoppedSpeakingFrame, EndFrame, InterimTranscriptionFrame,
        TranscriptionFrame, TTSSpeakFrame, VADUserStartedSpeakingFrame,
        VADUserStoppedSpeakingFrame)
    from pipecat.pipeline.pipeline import Pipeline  # noqa: PLC0415
    from pipecat.pipeline.worker import PipelineParams, PipelineWorker  # noqa: PLC0415
    from pipecat.processors.audio.vad_processor import VADProcessor  # noqa: PLC0415
    from pipecat.processors.frame_processor import FrameDirection, FrameProcessor  # noqa: PLC0415
    from pipecat.runner.utils import parse_telephony_websocket  # noqa: PLC0415
    from pipecat.serializers.plivo import PlivoFrameSerializer  # noqa: PLC0415
    from pipecat.services.deepgram.stt import DeepgramSTTService  # noqa: PLC0415
    from pipecat.services.elevenlabs.tts import ElevenLabsTTSService  # noqa: PLC0415
    from pipecat.transcriptions.language import Language  # noqa: PLC0415
    from pipecat.transports.websocket.fastapi import FastAPIWebsocketParams, FastAPIWebsocketTransport  # noqa: PLC0415
    from pipecat.workers.runner import WorkerRunner  # noqa: PLC0415

    Gate = gate_class()

    class Talk(FrameProcessor):
        """Between Deepgram and ElevenLabs: gathers what they said, asks Mind when they've finished, speaks,
        lets them interrupt, and finishes the call once Buddy's last words have been said."""

        def __init__(self):
            super().__init__()
            self.words: list[str] = []
            self.user_talking = self.bot_talking = False
            self.reply: asyncio.Task | None = None
            self.quiet_done = asyncio.Event()
            self.started = asyncio.get_running_loop().time()

        def _later(self, delay: float) -> None:
            if self.reply and not self.reply.done():
                self.reply.cancel()
            self.reply = asyncio.ensure_future(self._answer(delay))

        async def _answer(self, delay: float) -> None:
            await asyncio.sleep(delay)
            if self.user_talking or not self.words:
                return
            said, self.words = " ".join(self.words), []
            try:
                await mind.heard(said)
            except Exception as e:  # Claude unreachable mid-call: don't leave them hanging
                log.warning("call %s: no reply (%s)", call["id"], type(e).__name__)
                await speak("Sorry, I'm having trouble on my end. I'll call back shortly. Thank you!")
                mind.ended = True
                await finish({"action": "end", "say": "", "digits": "",
                              "outcome": "The call broke off on Buddy's side; nothing was settled."})

        async def process_frame(self, frame, direction):
            await super().process_frame(frame, direction)
            if isinstance(frame, VADUserStartedSpeakingFrame):
                if direction == FrameDirection.DOWNSTREAM:
                    self.user_talking = True
                    if self.reply and not self.reply.done():
                        self.reply.cancel()
                    if self.bot_talking and not mind.ended:  # they talk over Buddy: Buddy stops
                        await self.broadcast_interruption()
                await self.push_frame(frame, direction)
                return
            if isinstance(frame, VADUserStoppedSpeakingFrame):
                if direction == FrameDirection.DOWNSTREAM:
                    self.user_talking = False
                    self._later(0.7)  # Deepgram's last words arrive just after
                await self.push_frame(frame, direction)
                return
            if isinstance(frame, TranscriptionFrame):
                if frame.text.strip():
                    self.words.append(frame.text.strip())
                    if not self.user_talking:
                        self._later(0.3)
                return  # their words never go to the voice
            if isinstance(frame, InterimTranscriptionFrame):
                return
            if isinstance(frame, BotStartedSpeakingFrame):
                self.bot_talking = True
            elif isinstance(frame, BotStoppedSpeakingFrame):
                self.bot_talking = False
                done_speaking.set()
            await self.push_frame(frame, direction)

    env_get = (lambda n: _env(n, env))
    o = owner(env)
    _kind, data = await parse_telephony_websocket(websocket)
    serializer = PlivoFrameSerializer(stream_id=data.get("stream_id") or "", call_id=data.get("call_id"),
                                      params=PlivoFrameSerializer.InputParams(auto_hang_up=False))
    transport = FastAPIWebsocketTransport(websocket=websocket, params=FastAPIWebsocketParams(
        audio_in_enabled=True, audio_out_enabled=True, add_wav_header=False, serializer=serializer))
    stt = DeepgramSTTService(
        api_key=env_get("BUDDY_DEEPGRAM_KEY"), mip_opt_out=True,
        settings=DeepgramSTTService.Settings(model=STT_MODEL, language=Language.EN_US, smart_format=True,
                                             numerals=True, punctuate=True,
                                             keyterm=[w for w in o["name"].split() if len(w) > 2] or None))
    tts = ElevenLabsTTSService(api_key=env_get("BUDDY_ELEVENLABS_KEY"), settings=ElevenLabsTTSService.Settings(
        voice=env_get("BUDDY_CALL_VOICE") or DEFAULT_VOICE, model=env_get("BUDDY_CALL_VOICE_MODEL") or TTS_MODEL))
    talk = Talk()
    done_speaking = asyncio.Event()
    call_uuid = data.get("call_id") or call["call_sid"]

    async def speak(text: str) -> None:
        done_speaking.clear()
        await talk.push_frame(TTSSpeakFrame(text), FrameDirection.DOWNSTREAM)

    async def press_keys(digits: str) -> None:
        await asyncio.to_thread(press, memory, call_uuid, digits, env)

    async def finish(move: dict) -> None:
        if move.get("say"):  # let Buddy's last words be heard first
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(done_speaking.wait(), timeout=4 + 0.08 * len(move["say"]))
        connect = move["action"] == "connect" and call["kind"] == "hold_connect" and bool(o["phone"])
        update(memory, call["id"], status="connected" if connect else "done",
               outcome=move.get("outcome") or ("A person answered; you were rung in." if connect else ""))
        if deliver:
            try:
                await deliver("call", outcome_message(call, {**move, "action": "connect" if connect else "end"}))
            except Exception:
                log.warning("call %s: couldn't tell the owner", call["id"])
        try:
            if connect:
                await asyncio.to_thread(transfer, memory, {**call, "call_sid": call_uuid}, env)
            else:
                await asyncio.to_thread(hangup, memory, call_uuid, env)
        finally:
            await worker.queue_frame(EndFrame())

    mind = Mind(memory, call, say=speak, press_keys=press_keys, finish=finish, env=env)
    pipeline = Pipeline([transport.input(),
                         VADProcessor(vad_analyzer=SileroVADAnalyzer(params=VADParams(stop_secs=0.6))),
                         Gate(), stt, talk, tts, transport.output()])
    worker = PipelineWorker(pipeline, params=PipelineParams(audio_in_sample_rate=8000, audio_out_sample_rate=8000),
                            idle_timeout_secs=None)

    @transport.event_handler("on_client_disconnected")
    async def _gone(_transport, _client):
        await worker.cancel()

    async def nobody_speaks():
        await asyncio.sleep(QUIET_START_S)
        if not mind.heard_any and not talk.words and not talk.user_talking:
            await mind.heard("[No one has spoken yet]")

    starter = asyncio.ensure_future(nobody_speaks())
    try:
        await WorkerRunner(handle_sigint=False).run(worker)
    finally:
        starter.cancel()
        if talk.reply and not talk.reply.done():
            talk.reply.cancel()
        if mind.heard_any:
            gateway.report(memory, "deepgram", True)
        if mind.spoke_any:
            gateway.report(memory, "elevenlabs", True)


# --- the tool --------------------------------------------------------------------------------------------------
TOOL = {
    "name": "phone_call",
    "description": "Call a business for the user, as their assistant (Buddy Connect: "
    "Plivo, with a natural voice). kind: appointment (book or move one, e.g. with their doctor, inside `windows`, "
    "checking their calendar; may give their name, callback number, date of birth and insurance), question (ask "
    "and report back), hold_connect (banks, card companies: get through the menu, wait on hold, and ring the user "
    "when a person answers; only the user verifies). Find the number first (memory, library, contacts, places "
    "search) and confirm it. The user approves every call before it's made. status: the latest calls and their "
    "outcomes.",
    "input_schema": {"type": "object", "properties": {
        "action": {"type": "string", "enum": ["call", "status"]},
        "who": {"type": "string", "description": "who is being called, e.g. Dr. Mehta's office"},
        "number": {"type": "string"},
        "kind": {"type": "string", "enum": list(KINDS)},
        "goal": {"type": "string", "description": "what to get done, with the facts they'll need"},
        "windows": {"type": "string", "description": "appointment: allowed days and times, e.g. 'mornings next week'"}},
        "required": ["action"], "additionalProperties": False},
}


def status_text(memory: Memory) -> str:
    _ready(memory)
    rows = [dict(r) for r in memory.db.execute(
        "SELECT id, created_at, who, kind, status, outcome, minutes FROM phone_calls WHERE status != 'planned' "
        "ORDER BY id DESC LIMIT 5")]
    return json.dumps(rows) if rows else "No calls yet."


async def run_tool(memory: Memory, thrift, args: dict) -> str:
    if args.get("action") == "status":
        return status_text(memory)
    missing = ready()
    if missing:
        return missing
    if thrift is None:
        return "I need your yes before calling, and I can't ask right now."
    call = plan(memory, args.get("who") or "the business", args.get("number") or "", args.get("kind") or "question",
                args.get("goal") or "", args.get("windows") or "")
    first = (owner()["name"].split() or ["your"])[0]
    shares = ["your name", "callback number"] + (["date of birth", "insurance"] if call["kind"] == "appointment"
                                                 else [])
    what = {"appointment": f"book: {call['goal']}" + (f" ({call['windows']})" if call["windows"] else ""),
            "question": f"ask: {call['goal']}",
            "hold_connect": f"get through the menu and hold for {call['goal']}, then ring you when a person answers"}

    async def go() -> str:
        await asyncio.to_thread(dial, memory, call)
        return (f"Calling {call['who']} now. I'll tell you how it goes"
                + (", and ring you when a person answers." if call["kind"] == "hold_connect" else "."))
    mine = caller_id() != _env("BUDDY_PLIVO_NUMBER")
    return thrift.ask_action(
        f"Call {call['who']} at {call['number']}{' from your number' if mine else ''} as {first}'s assistant to "
        f"{what[call['kind']]}? "
        f"I may share {', '.join(shares)}; nothing else, no payments, and the call isn't recorded.", go)
