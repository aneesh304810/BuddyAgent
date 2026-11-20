"""The glasses door: where a pair of glasses joins Buddy's brain (the Devices and Link modules).

A pair of glasses does not reach this server by itself. The glasses runtime does, from the phone: it holds the
connection to the glasses, fits what Buddy sends to the lens or the ear, applies the privacy rule, and reports what
the wearer actually got (xr/src/link.js). This side keeps the session and gives the brain four tools:

  glasses_describe   what the connected glasses can do
  glasses_show       a title, some lines, and optionally an alert
  glasses_ask        a yes-or-no question; the result is the wearer's answer
  glasses_choose     a short list; the result is the one the wearer picked

The messages are the contract in docs/BWCP.md, version 0.2, over the WebSocket /ws/glasses (the same access check
as the phone's connection):

  from the runtime   hello (the device and its profile), result, state, said, problem, bye
  to the runtime     welcome | refused, call (one of the tools), cancel

The privacy rule is applied twice. The runtime applies all of it. This side applies the part that must hold even
if the runtime were wrong: content that may reach neither the lens nor the ear on the route the glasses declared
is never sent at all (health anywhere but a direct route; personal through another company's cloud). A device
that declares no route, or one this side does not know, is treated as the strictest.

Nothing about a session is kept after it ends, except one meter line a day for the device (modules.py).
NOT YET RUN WITH REAL GLASSES: tested against stand-ins for the runtime, and with the runtime in a browser.
"""

import asyncio
import json
import logging
import os
import secrets
import time

from . import modules

log = logging.getLogger("buddy.glasslink")

PROTOCOL, VERSION = "bwcp", "0.2"
LEVELS = ("public", "personal", "health")
ROUTES = ("direct", "maker-app", "cloud")
INPUTS = ("tap", "double", "long", "swipe")
SHOW_SECONDS = 15      # how long a screen may take to be acknowledged
WAIT_SECONDS = 60      # how long a question waits for the wearer (BUDDY_GLASSES_WAIT)
PAGE = "/glasses.html"  # the page of this server that runs the glasses runtime in the phone's browser


class Refused(Exception):
    """This hello is not accepted; the message is what the runtime is told."""


def _text(value, limit: int) -> str:
    return " ".join(str(value if value is not None else "").split())[:limit]


def _whole(value, low: int, high: int, default: int) -> int:
    try:
        return max(low, min(high, int(value)))
    except (TypeError, ValueError):
        return default


def profile_of(raw) -> dict:
    """What the runtime said the glasses can do, checked. Anything missing or strange becomes the careful answer:
    no capability it did not clearly state, and the strictest route."""
    raw = raw if isinstance(raw, dict) else {}
    display = raw.get("display") if isinstance(raw.get("display"), dict) else None
    return {
        "name": _text(raw.get("name"), 80) or "Glasses",
        "display": None if display is None else {
            "kind": "color" if display.get("kind") == "color" else "text",
            "lines": _whole(display.get("lines"), 1, 40, 5), "cols": _whole(display.get("cols"), 4, 120, 30),
            "images": display.get("images") is True},
        "speaker": raw.get("speaker") is True, "mic": raw.get("mic") is True, "camera": raw.get("camera") is True,
        "input": [i for i in INPUTS if isinstance(raw.get("input"), list) and i in raw["input"]],
        "route": raw.get("route") if raw.get("route") in ROUTES else "cloud",
    }


def allowed(level: str, channel: str, profile: dict) -> bool:
    """May content of this privacy level reach the wearer this way (channel: "display" or "speech")? The same rule
    as the runtime's (xr/src/privacy.js), without the two choices only the wearer or the deployer can make there
    (reading personal details aloud, accepting a cloud route): here both count as not chosen."""
    if level == "public":
        return True
    if profile["route"] == "cloud":
        return False
    if channel == "display":
        return level == "personal" or profile["route"] == "direct"
    return False  # speech: personal only if the wearer chose that, which the runtime knows and this side does not


class Session:
    """One pair of glasses, connected."""

    def __init__(self, device: dict, profile: dict, send):
        self.id = secrets.token_hex(4)
        self.device, self.profile, self.send = device, profile, send
        self.state: dict = {}
        self.since = time.time()
        self.waiting: dict[int, asyncio.Future] = {}
        self._next = 0

    async def call(self, tool: str, args: dict, timeout: float) -> dict:
        """Ask the runtime to run one tool, and wait for what it reports."""
        self._next += 1
        call_id = self._next
        answer = asyncio.get_running_loop().create_future()
        self.waiting[call_id] = answer
        try:
            await self.send({"op": "call", "id": call_id, "tool": tool, "args": args})
            return await asyncio.wait_for(answer, timeout)
        except asyncio.TimeoutError:
            try:  # take the question off the lens: nobody is waiting for the answer any more
                await self.send({"op": "cancel", "id": call_id})
            except Exception:  # the connection went at the same moment  # nosec B110
                pass
            raise
        finally:
            self.waiting.pop(call_id, None)

    def settle(self, message: dict) -> None:
        answer = self.waiting.get(message.get("id"))
        if answer is None or answer.done():
            return
        if message.get("ok") is True and isinstance(message.get("result"), dict):
            answer.set_result(message["result"])
        else:
            answer.set_exception(RuntimeError(_text(message.get("error"), 200) or "the glasses could not do that"))

    def end(self) -> None:
        for answer in self.waiting.values():
            if not answer.done():
                answer.set_exception(ConnectionError("the glasses disconnected"))
        self.waiting.clear()

    def about(self) -> dict:
        p = self.profile
        return {"glasses": " ".join(x for x in (self.device["maker"], self.device["model"]) if x) or p["name"],
                "display": None if p["display"] is None else {"lines": p["display"]["lines"],
                                                              "characters": p["display"]["cols"],
                                                              "pictures": p["display"]["images"]},
                "speaker": p["speaker"], "microphone": p["mic"], "camera": p["camera"], "input": p["input"],
                "route": p["route"], "state": self.state,
                "connected_minutes": round((time.time() - self.since) / 60, 1)}


class GlassLink:
    """The glasses connected to this server. Usually one pair; the newest is the one the brain talks to."""

    def __init__(self):
        self.sessions: dict[str, Session] = {}

    def attach(self, memory, hello, send, env=None) -> Session:
        if not isinstance(hello, dict) or hello.get("op") != "hello" or hello.get("protocol") != PROTOCOL:
            raise Refused("This door speaks the Buddy wearable contract: the first message must be its hello.")
        if not str(hello.get("version") or "").startswith("0."):
            raise Refused(f"This server speaks version {VERSION} of the contract.")
        for module in ("devices", "link"):
            if not modules.is_on(module, env):
                raise Refused(f"The {modules.BY_ID[module].title} module is switched off on this server.")
        raw = hello.get("device") if isinstance(hello.get("device"), dict) else {}
        device = {"maker": _text(raw.get("maker"), 40), "model": _text(raw.get("model"), 60), "id": _text(raw.get("id"), 80)}
        session = Session(device, profile_of(hello.get("profile")), send)
        self.sessions[session.id] = session
        who = " ".join(x for x in (device["maker"], device["model"], device["id"]) if x) or "unnamed glasses"
        try:  # the meter: this device was active today
            modules.mark(memory, "devices", "device_day", who, env=env)
        except Exception:  # a wrong meter never refuses a pair of glasses  # nosec B110
            pass
        log.info("glasses connected: %s (%s route, session %s)", who, session.profile["route"], session.id)
        return session

    def heard(self, session: Session, message) -> bool:
        """One message from the runtime. False when it said goodbye."""
        if not isinstance(message, dict):
            return True
        op = message.get("op")
        if op == "result":
            session.settle(message)
        elif op == "state":
            for key in ("battery", "charging", "link", "worn"):
                if key in message and isinstance(message[key], (int, float, bool, str)):
                    session.state[key] = message[key] if not isinstance(message[key], str) else _text(message[key], 20)
        elif op == "problem":
            log.warning("glasses %s reported a problem: %s (%s)", session.id, _text(message.get("code"), 40),
                        _text(message.get("about"), 200))
        elif op == "bye":
            log.info("glasses %s said goodbye (%s)", session.id, _text(message.get("why"), 40))
            return False
        # "said" (the wearer's words) and anything this side does not know: not handled yet, ignored
        return True

    def detach(self, session: Session) -> None:
        session.end()
        self.sessions.pop(session.id, None)

    def current(self) -> Session | None:
        return next(reversed(self.sessions.values()), None)

    def status(self) -> list[dict]:
        return [{"session": s.id, **s.device, **s.about()} for s in self.sessions.values()]


link = GlassLink()  # the one door of this server


# --- the brain's tools ----------------------------------------------------------------------------------------
_PRIVACY = {"type": "string", "enum": list(LEVELS), "description": "How private this is. personal (a name with "
            "details, an address, a code, a number, money): shown in the lens, not read aloud. health: shown only on "
            "glasses reached directly from the phone, never spoken. Default public."}
TOOLS = [
    {"name": "glasses_describe",
     "description": "The smart glasses connected to Buddy right now, if any: display size or none, speaker, "
     "microphone, camera, how the wearer gives input, battery. Call it before using the other glasses tools.",
     "input_schema": {"type": "object", "properties": {}, "additionalProperties": False}},
    {"name": "glasses_show",
     "description": "Put something in front of the wearer of the connected glasses: a title and a few short lines. "
     "On glasses with no display it is read to them. Keep it short: a lens may show five lines of thirty "
     "characters. The result says what actually reached them (shown, spoken, withheld): if something was held "
     "back, tell them it is on their phone.",
     "input_schema": {"type": "object", "required": ["title"], "additionalProperties": False, "properties": {
         "title": {"type": "string", "maxLength": 60, "description": "A few words naming what this is."},
         "lines": {"type": "array", "maxItems": 12, "items": {"type": "string", "maxLength": 200}},
         "alert": {"type": "string", "maxLength": 200,
                   "description": "Something the wearer must not miss. Always spoken where there is a speaker."},
         "privacy": _PRIVACY}}},
    {"name": "glasses_ask",
     "description": "Ask the wearer of the connected glasses a yes-or-no question and wait for the answer (a tap "
     "or the word). The result has answer yes or no, or cancelled, or says that nobody answered.",
     "input_schema": {"type": "object", "required": ["question"], "additionalProperties": False, "properties": {
         "question": {"type": "string", "maxLength": 200}, "title": {"type": "string", "maxLength": 60},
         "privacy": _PRIVACY}}},
    {"name": "glasses_choose",
     "description": "Offer the wearer of the connected glasses a short list (2 to 8 options) and wait for their "
     "choice. The result has the choice, or cancelled, or says that nobody answered.",
     "input_schema": {"type": "object", "required": ["title", "options"], "additionalProperties": False, "properties": {
         "title": {"type": "string", "maxLength": 60},
         "options": {"type": "array", "minItems": 2, "maxItems": 8, "items": {"type": "string", "maxLength": 80}},
         "privacy": _PRIVACY}}},
]
TOOL_NAMES = {t["name"] for t in TOOLS}
NOT_CONNECTED = ("No glasses are connected to Buddy right now. To connect a pair, open this server's Glasses page "
                 f"({PAGE}) in Chrome on the phone and follow it.")


def _wait(env=None) -> float:
    try:
        return max(5.0, min(600.0, float((env if env is not None else os.environ).get("BUDDY_GLASSES_WAIT") or WAIT_SECONDS)))
    except ValueError:
        return float(WAIT_SECONDS)


async def run_tool(name: str, args: dict, door: GlassLink | None = None, env=None) -> str:
    """Run one of the glasses tools for the brain. Always answers in words or JSON; never raises to the caller."""
    session = (door or link).current()
    if session is None:
        return NOT_CONNECTED
    if name == "glasses_describe":
        return json.dumps(session.about())
    level = args.get("privacy") or "public"
    if level not in LEVELS:
        return f"privacy must be one of: {', '.join(LEVELS)}."
    profile = session.profile
    can_show = profile["display"] is not None and allowed(level, "display", profile)
    can_say = profile["speaker"] and allowed(level, "speech", profile)
    speaks_personal = level == "personal" and profile["speaker"] and profile["route"] != "cloud"  # the wearer's choice
    if level != "public" and not (can_show or can_say or speaks_personal):
        return json.dumps({"shown": False, "spoken": False, "withheld": 1, "note": (
            f"Not sent: {level} details may not reach these glasses ({profile['route']} route"
            f"{'' if profile['display'] else ', no display'}). Tell them it is on their phone.")})
    asks = name in ("glasses_ask", "glasses_choose")
    try:
        result = await session.call(name, args, _wait(env) if asks else SHOW_SECONDS)
    except asyncio.TimeoutError:
        return (f"Nobody answered on the glasses within {round(_wait(env))} seconds; the question was taken off the "
                "lens." if asks else "The glasses did not confirm it in time; it may not have been shown.")
    except ConnectionError:
        return "The glasses disconnected before this reached the wearer."
    except RuntimeError as e:
        return f"The glasses could not do that: {e}"
    return json.dumps(result)
