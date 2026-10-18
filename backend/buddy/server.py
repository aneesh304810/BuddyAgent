"""HTTP + WebSocket server. The phone connects to /ws and streams text both ways.

Protocol (JSON messages):
  phone -> server  {"type": "hello", "timezone": "America/Sao_Paulo"}  on connect
                   {"type": "user", "text": "..."}   something the user said
                   {"type": "interrupt"}              user started talking over Buddy
                   {"type": "ambient", "text": "...", "lang": "hi-IN"}
                                                      listen-in mode: speech around the user
                   {"type": "ambient_stop"}           listen-in mode switched off
  server -> phone  {"type": "say", "text": "..."}    one sentence to speak
                   {"type": "done"}                   reply finished
                   {"type": "nudge", "kind": "...", "text": "...", "job_id": 3}
                                                      Buddy speaking up (job_id: a finished
                                                      background job; see GET /api/jobs/{id})
                   {"type": "whisper", "kind": "...", "text": "..."}
                                                      listen-in: translation / about_you / correction
                   {"type": "error", "text": "..."}
"""

import asyncio
import contextlib
import logging
import secrets

import anthropic
from fastapi import Depends, FastAPI, Header, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles

from . import proactive, research, usage
from .agent import Conversation
from .agents import AgentRegistry, load_specs
from .ambient import AmbientListener
from .config import settings
from .jobs import JobRunner
from .memory import Memory
from .thrift import Thrift
from .tools import set_user_tz

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
log = logging.getLogger("buddy")

memory = Memory(settings.db_path)
registry = AgentRegistry(memory, load_specs(settings.agents_file))
if memory.get("timezone"):
    set_user_tz(memory.get("timezone"))  # last zone the phone reported
# Live connections, each with its own conversation.
connections: dict[WebSocket, Conversation] = {}


async def deliver(kind: str, text: str, **extra) -> bool:
    if not connections:
        return False
    for ws, convo in list(connections.items()):
        with contextlib.suppress(Exception):
            await ws.send_json({"type": "nudge", "kind": kind, "text": text, **extra})
            convo.note_proactive(text)
    return True


async def job_finished(job: dict) -> bool:
    """Tell the user a background job is done (or why it failed)."""
    if job["status"] == "done":
        text = f"Your research on {job['title']} is ready. {job.get('summary') or ''}".strip()
    else:
        text = f"The research on {job['title']} didn't finish: {job.get('error') or 'unknown error'}"
    return await deliver("job_done", text, job_id=job["id"])


thrift = Thrift(memory)
jobs = JobRunner(memory, job_finished)
jobs.register("research", research.run_research)


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    await registry.start()
    task = asyncio.create_task(proactive.run(memory, deliver))
    job_loop = asyncio.create_task(jobs.run())
    try:
        yield
    finally:
        task.cancel()
        await jobs.close()
        job_loop.cancel()
        await registry.close()


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


def require_admin(authorization: str | None = Header(default=None)) -> None:
    token = (authorization or "").removeprefix("Bearer ").strip()
    if not authorized(token or None):
        raise HTTPException(status_code=401, detail="bad token")


# --- admin: only you, with the token, over your private network ------------
@app.get("/admin/agents", dependencies=[Depends(require_admin)])
def admin_agents() -> list[dict]:
    return registry.status()


@app.post("/admin/agents/{name}/{action}", dependencies=[Depends(require_admin)])
def admin_agent_switch(name: str, action: str) -> dict:
    if action not in ("enable", "disable"):
        raise HTTPException(status_code=404)
    if name not in registry.specs:
        raise HTTPException(status_code=404, detail="no such agent")
    registry.set_enabled(name, action == "enable")
    note = " Restart Buddy to connect it." if action == "enable" and name not in registry.clients else ""
    return {"name": name, "enabled": action == "enable", "note": note.strip()}


@app.get("/admin/audit", dependencies=[Depends(require_admin)])
def admin_audit(limit: int = 100) -> list[dict]:
    return registry.audit_log(min(limit, 1000))


@app.get("/admin/usage", dependencies=[Depends(require_admin)])
def admin_usage() -> dict:
    return {
        "month_to_date_usd": round(usage.month_to_date(memory), 4),
        "monthly_budget_usd": settings.monthly_budget_usd,
        "allowances": thrift.status(),
        "by_month_and_model": usage.summary(memory),
    }


# --- background job reports: the phone apps show them -----------------------
@app.get("/api/jobs", dependencies=[Depends(require_admin)])
def api_jobs(limit: int = 50) -> list[dict]:
    return jobs.list(limit=min(limit, 200))


@app.get("/api/jobs/{job_id}", dependencies=[Depends(require_admin)])
def api_job(job_id: int) -> dict:
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="no such job")
    return job


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
    convo = Conversation(memory, registry, jobs, thrift)
    connections[ws] = convo
    jobs.wake.set()  # results that finished while the phone was away go out now
    current: asyncio.Task | None = None

    async def say(text: str) -> None:
        await ws.send_json({"type": "say", "text": text})

    async def whisper(kind: str, text: str) -> None:
        await ws.send_json({"type": "whisper", "kind": kind, "text": text})
        convo.note_proactive(text)

    listener = AmbientListener(memory, whisper, thrift)

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
            if msg.get("type") == "hello":
                tz = str(msg.get("timezone", ""))
                if tz and set_user_tz(tz):
                    memory.set("timezone", tz)
            elif msg.get("type") == "interrupt":
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
