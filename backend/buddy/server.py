"""HTTP + WebSocket server. The phone connects to /ws and streams text both ways.

Protocol (JSON messages):
  phone -> server  {"type": "user", "text": "..."}   something the user said
                   {"type": "interrupt"}              user started talking over Buddy
                   {"type": "ambient", "text": "...", "lang": "hi-IN"}
                                                      listen-in mode: speech around the user
                   {"type": "ambient_stop"}           listen-in mode switched off
  server -> phone  {"type": "say", "text": "..."}    one sentence to speak
                   {"type": "done"}                   reply finished
                   {"type": "nudge", "kind": "...", "text": "..."}  Buddy speaking up
                   {"type": "whisper", "kind": "...", "text": "..."}
                                                      listen-in: translation / about_you / correction
                   {"type": "error", "text": "..."}
"""

import asyncio
import contextlib
import logging
import secrets

import anthropic
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles

from . import proactive
from .agent import Conversation
from .ambient import AmbientListener
from .config import settings
from .memory import Memory

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
log = logging.getLogger("buddy")

memory = Memory(settings.db_path)
# Live connections, each with its own conversation.
connections: dict[WebSocket, Conversation] = {}


async def deliver(kind: str, text: str) -> bool:
    if not connections:
        return False
    for ws, convo in list(connections.items()):
        with contextlib.suppress(Exception):
            await ws.send_json({"type": "nudge", "kind": kind, "text": text})
            convo.note_proactive(text)
    return True


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(proactive.run(memory, deliver))
    yield
    task.cancel()


app = FastAPI(title="Buddy", lifespan=lifespan)


@app.get("/health")
def health() -> dict:
    return {"ok": True}


def addressed_to_buddy(text: str) -> str | None:
    """If speech starts with the assistant's name, return the rest as a command."""
    name = settings.assistant_name.lower()
    words = text.split(maxsplit=1)
    if words and words[0].lower().strip(",.!?:") in (name, f"hey{name}") and len(words) > 1:
        return words[1].lstrip(",.!?: ")
    lowered = text.lower()
    if lowered.startswith(f"hey {name}"):
        rest = text[len(f"hey {name}"):].lstrip(",.!?: ")
        return rest or None
    return None


def authorized(token: str | None) -> bool:
    if not settings.access_token:
        return True  # local development only; set BUDDY_TOKEN before exposing
    return token is not None and secrets.compare_digest(token, settings.access_token)


@app.websocket("/ws")
async def talk(ws: WebSocket) -> None:
    if not authorized(ws.query_params.get("token")):
        await ws.close(code=4401)
        return
    await ws.accept()
    convo = Conversation(memory)
    connections[ws] = convo
    current: asyncio.Task | None = None

    async def say(text: str) -> None:
        await ws.send_json({"type": "say", "text": text})

    async def whisper(kind: str, text: str) -> None:
        await ws.send_json({"type": "whisper", "kind": kind, "text": text})
        convo.note_proactive(text)

    listener = AmbientListener(memory, whisper)

    async def answer(text: str) -> None:
        try:
            await convo.reply(text, say)
            await ws.send_json({"type": "done"})
        except asyncio.CancelledError:
            raise
        except anthropic.APIError as e:
            log.warning("Claude API error: %s", e)
            await ws.send_json({"type": "error", "text": "I can't reach my brain right now."})

    async def stop_current() -> None:
        if current and not current.done():
            current.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await current

    try:
        while True:
            msg = await ws.receive_json()
            if msg.get("type") == "interrupt":
                await stop_current()
            elif msg.get("type") == "user" and msg.get("text", "").strip():
                await stop_current()  # new speech always wins over an old reply
                current = asyncio.create_task(answer(msg["text"].strip()))
            elif msg.get("type") == "ambient" and msg.get("text", "").strip():
                text = msg["text"].strip()
                command = addressed_to_buddy(text)
                if command:  # "Buddy, what did he just say?" is a command, not ambient talk
                    await stop_current()
                    current = asyncio.create_task(answer(command))
                else:
                    listener.hear(text, str(msg.get("lang", "")))
            elif msg.get("type") == "ambient_stop":
                listener.stop()
    except WebSocketDisconnect:
        pass
    finally:
        listener.stop()
        connections.pop(ws, None)
        await stop_current()


# The voice client (works in phone browsers today; native apps come later).
app.mount("/", StaticFiles(directory=settings.web_dir, html=True), name="web")
