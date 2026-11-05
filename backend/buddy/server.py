"""HTTP + WebSocket server. The phone connects to /ws and streams text both ways.

Protocol (JSON messages):
  phone -> server  {"type": "hello", "timezone": "America/Sao_Paulo"}  on connect
                   {"type": "user", "text": "..."}   something the user said
                   {"type": "interrupt"}              user started talking over Buddy
                   {"type": "ambient", "text": "...", "lang": "hi-IN"}
  phone -> server  {"type": "audio", "pcm": "<base64 16 kHz mono 16-bit>", "lang": "auto", "mode": "talk"}
  server -> phone  {"type": "heard", "text": "...", "lang": "es"}  (Whisper on the server)
  phone -> server  {"type": "rate", "turn": "a1b2c3", "up": true}  thumbs on a reply (null clears)
  phone -> server  {"type": "location", "lat": -12.12, "lng": -77.03, "accuracy": 50}  (when shared)
  server -> phone  {"type": "vocabulary", "words": [...]}  names and words to recognise (after hello)
  server -> phone  {"type": "thinking"}  the question arrived and Buddy is working on it
  server -> phone  {"type": "approval", "question": "..."}  Buddy is waiting for a yes:
                   show Approve / Decline; they send a user message "Yes, go ahead." / "No."
                                                      listen-in mode: speech around the user
                   {"type": "ambient_stop"}           listen-in mode switched off
                   {"type": "notification", "device", "app", "app_name", "title", "text",
                    "posted_at", "key"}               a notification on that phone
                   {"type": "phone_calendar", "device", "events": [{id, title, start,
                    end, calendar, location, all_day, busy_only}]}  the phone's next week
  server -> phone  {"type": "say", "text": "...", "filler": true?, "display": "..."?}  one sentence to speak
                                                      (display: what to show instead, e.g. English)
                                                      (filler: "hang on, checking" while tools run)
                   {"type": "cards", "turn": "a1b2c3", "cards": [...]}  results to show under the reply
                   {"type": "done", "turn": "a1b2c3"} reply finished (turn: for a thumbs rating)
                   {"type": "nudge", "kind": "...", "text": "...", "job_id": 3}
                                                      Buddy speaking up (job_id: a finished
                                                      background job; see GET /api/jobs/{id})
                   {"type": "whisper", "kind": "...", "text": "..."}
                                                      listen-in: translation / about_you / correction
                   {"type": "open", "links": [{"app": "uber://...", "url": "https://...", "label": "..."}],
                    "compare": "id"}                  open Uber / Lyft with a trip filled in (the
                                                      first now, the next when Buddy is back in front);
                                                      with compare, the ride reader reads both prices
                   {"type": "form", "action": "read|scroll|fill", "id": "...", "fills": [...]}
                                                      the form helper: read / fill the browser form; the
                                                      phone answers {"type": "form_screen" | "form_result"}
                   {"type": "error", "text": "..."}
"""

import asyncio
import base64
import contextlib
import json
import logging
import os
import re
import secrets
import time
from datetime import datetime, timedelta, timezone

import anthropic
from fastapi import Depends, FastAPI, Header, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles

from . import health as healthcheck
from . import guard
from . import advisor, codejob, diagnostics, finance, plaid, travel, insights, thinking, makers, proactive, research, tutor, usage, vocabulary, voiceprint
from .agent import FILLERS, Conversation
from .agents import AgentRegistry, load_specs
from .ambient import AmbientListener, is_think_cue
from .config import settings
from .jobs import JobRunner
from .memory import Memory
from .connectors import ConnectorHub
from .connectors.phone import NotificationsConnector, PhoneCalendarConnector, should_announce
from .money import Money
from .rides import RideFollower, take_screen
from .privacy import SecretFilter, mask
from .bankfeed import BankFeed
from .builder import AgentBuilder
from .deals import PriceWatcher
from .finops import FinOps
from . import lifelog
from .lifelog import LifeLog
from .library import Library, report as library_report
from .brain import Brain
from .location import Location
from . import addressee
from . import whisper as speech_to_text  # not `whisper`: that's Listen-in's whisper below
from .mailbrain import MailManager
from .thrift import Thrift
from .tools import ToolError, set_user_tz

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
for _handler in logging.getLogger().handlers:  # keys never reach the journal
    _handler.addFilter(SecretFilter())
# HTTP libraries log every request's full address at INFO, and some addresses carry secrets
# (an MCP link, a bank-feed link): only their warnings and errors are worth keeping.
for _name in ("httpx", "httpcore", "urllib3", "mcp.client.streamable_http", "mcp.client.sse"):
    logging.getLogger(_name).setLevel(logging.WARNING)

# Live trace of every message between the phone and Buddy, for troubleshooting
# (deploy/trace.sh). Off by default: conversations don't go to the system log.
trace_log = logging.getLogger("buddy.trace")
tracing = {"on": os.getenv("BUDDY_TRACE", "") == "1"}


def trace(direction: str, msg: dict) -> None:
    if not tracing["on"]:
        return
    kind = msg.get("type", "?")
    extra = msg.get("via") or msg.get("kind") or msg.get("lang") or ""
    text = msg.get("text") or msg.get("question") or ""
    if kind == "audio":
        text = f"{len(msg.get('pcm', '')) * 3 // 4 // 32000:.0f} s of audio"
    elif kind == "location":
        text = "location update"
    trace_log.info("%s %-12s %-8s %s", direction, kind, extra, mask(str(text))[:160])
log = logging.getLogger("buddy")
# "Talk mode" / "Listen-in mode" / "go to sleep" / "wake up": switched at once, no trip to Claude.
MODE_WORDS = re.compile(
    r"^(?:(?:hey|hi|ok|okay)[\s,]+)?(?:(?:buddy|bro|aliya|machaa)[\s,]+)?(?:please[\s,]+)?"
    r"(?:(?:switch|go|change|move|back)\s+(?:back\s+)?(?:to|into)\s+)?(?:the\s+)?"
    r"(?P<mode>talk|talking|listen[\s-]?in|listening|room|sleep)(?:\s+mode)?"
    r"(?:[\s,]+(?:please|buddy|bro))?[.!]*$", re.I)
WAKE_UP = re.compile(r"^(?:(?:hey|ok|okay)[\s,]+)?(?:buddy|bro|aliya|machaa)?[\s,]*(?:wake up|let'?s talk)"
                     r"(?:[\s,]+(?:buddy|bro))?[.!]*$", re.I)
GO_TO_SLEEP = re.compile(r"^(?:(?:hey|ok|okay)[\s,]+)?(?:buddy|bro|aliya|machaa)?[\s,]*(?:go to sleep|you can sleep"
                         r"(?: now)?|sleep now)\b.*$", re.I)


def speech_quality(memory, ns: str, confidence: float | None, kept: bool, unsure: bool) -> None:
    """Per clip, for Insights' noise-suppressor comparison: confidence, and whether it was used."""
    key = f"speechq:{ns}"
    usage.count(memory, f"{key}:clips")
    if confidence is not None:
        usage.count(memory, f"{key}:scored")
        usage.count(memory, f"{key}:conf_milli", int(round(confidence * 1000)))
    if not kept:
        usage.count(memory, f"{key}:dropped")
    elif unsure:
        usage.count(memory, f"{key}:unsure")


def unfinished(text: str) -> bool:
    """Does Whisper's text look cut off mid-sentence? '...', no closing punctuation, a trailing comma,
    or a last word that can't end a sentence. (Only for Whisper's text: the phone's has no punctuation.)"""
    t = text.strip()
    return bool(UNFINISHED.search(t) or DANGLING.search(t) or not FINISHED.search(t) or t.endswith(","))


def mode_command(text: str) -> str | None:
    """'talk' or 'listen' for a spoken mode switch, else None."""
    t = text.strip()
    if WAKE_UP.match(t):
        return "talk"
    if GO_TO_SLEEP.match(t):
        return "listen"
    m = MODE_WORDS.match(t)
    if not m:
        return None
    return "talk" if m.group("mode").lower().startswith("talk") else "listen"


# Whisper ends a sentence cut off by a pause with "..."; wait this long for the rest before answering.
UNFINISHED = re.compile(r"(\.\.\.|…)\s*$")
# ...or a sentence ending on a word that can't end one ("how Buddy is doing its", "the last resort, but.")
DANGLING = re.compile(r"\b(?:its|the|a|an|to|and|but|or|of|with|my|your|our|their|if|because|than|from)[\s,.]*$",
                      re.I)
FRAGMENT_WAIT_SECONDS = float(os.getenv("BUDDY_FRAGMENT_WAIT", "3"))
FINISHED = re.compile(r"[.?!।؟。]['\")\]]*\s*$")  # Whisper punctuates a complete sentence

memory = Memory(settings.db_path)
from .persona import use_memory as _use_memory  # noqa: E402
_use_memory(memory)  # briefings and nudges follow the instructions the user gave too
registry = AgentRegistry(memory, load_specs(settings.agents_file))
if memory.get("timezone"):
    set_user_tz(memory.get("timezone"))  # last zone the phone reported
# Live connections, each with its own conversation.
connections: dict[WebSocket, Conversation] = {}


async def deliver(kind: str, text: str, **extra) -> bool:
    if not connections:
        return False
    spoken, sep, shown = text.partition("||")  # "spoken || shown": short when read aloud, structured on screen
    display = shown.strip() if sep and spoken.strip() and shown.strip() else ""
    text = spoken.strip() if display else text.replace("||", " ").strip()
    for ws, convo in list(connections.items()):
        with contextlib.suppress(Exception):
            trace("→", {"type": "nudge", "kind": kind, "text": text})
            message = {"type": "nudge", "kind": kind, "text": text, **extra}
            if display:
                message["display"] = display
            if extra.get("cards"):
                message["cards"] = _absolute(extra["cards"], ws)  # full links for this phone
            await ws.send_json(message)
            convo.note_proactive(text)
    return True


def _ride_fare(app: str, fare: float, currency: str) -> None:
    """A finished ride's fare goes into spending, unless the email receipt already put it there."""
    money = Money(memory)
    if hub.is_enabled("email"):
        return  # the receipt email is read for money anyway: don't count the ride twice
    money.add_transaction(fare, currency, app, "transport", source=f"ride:{app}:{int(time.time() // 600)}")


async def job_finished(job: dict) -> bool:
    """Tell the user a background job is done (or why it failed)."""
    if job["kind"] == "build_agent" and job["status"] == "done":
        name = (job.get("params") or {}).get("name", "")

        async def switch_on() -> str:
            return await builder.activate(name)

        question = "Shall I switch it on?"
        thrift.ask_action(question, switch_on)
        return await deliver("job_done", f"{job.get('summary') or ''} {question}".strip(), job_id=job["id"])
    if job["kind"] == "tutor":
        if job["status"] != "done":
            return await deliver("job_done", job.get("error") or "The English review didn't finish.", job_id=job["id"])
        return await deliver("job_done", f"Your English review is ready. {job.get('summary') or ''}".strip(),
                             job_id=job["id"])
    if job["kind"] == "code":
        if job["status"] != "done":
            return await deliver("job_done", f"The task didn't finish: {job.get('error') or 'unknown error'}",
                                 job_id=job["id"])
        text = f"Done: {job['title']}. {job.get('summary') or ''}".strip()
        found = [str(f).split(":", 1)[1] for f in (job.get("findings") or []) if str(f).startswith("output:")]
        results = [c for c in (makers.card_for(memory, fid) for fid in found) if c]
        if (job.get("params") or {}).get("repo") and "changed:yes" in str(job.get("findings")):
            async def push_it() -> str:
                return await codejob.push(job)
            question = "Shall I push the changes to a new buddy branch on GitHub?"
            thrift.ask_action(question, push_it)
            text = f"{text} {question}"
        return await deliver("job_done", text, job_id=job["id"], cards=results)
    if job["status"] == "done":
        text = f"Your research on {job['title']} is ready. {job.get('summary') or ''}".strip()
        params = job.get("params") or {}
        if job.get("confidence") == "low" and params.get("depth") == "quick":
            # Not good enough: offer the next step up, and wait for a yes.
            async def go_deeper() -> str:
                jobs.submit("research", job["title"], {**params, "depth": "deep"})
                return "Okay, I've started the deep report. I'll tell you when it's done."

            question = ("I'm not fully confident in it, though. Want me to dig deeper with a "
                        "deep report, for about 1 to 3 dollars?")
            thrift.ask_action(question, go_deeper)
            text = f"{text} {question}"
    else:
        text = f"The research on {job['title']} didn't finish: {job.get('error') or 'unknown error'}"
    return await deliver("job_done", text, job_id=job["id"])


thrift = Thrift(memory)
hub = ConnectorHub(memory)
finops = FinOps(memory)
mail = MailManager(memory, deliver)


async def _offer_label_rules(items: list[dict]) -> bool:
    """The weekly label suggestions, as one batch question (one yes creates them all)."""
    if not connections:
        return False  # nobody to ask now: try again later
    for item in items:
        async def add(item=item) -> str:
            mail.add_rule(item["rule"], "label", item["label"])
            return f"Label rule set: {item['rule']} → {item['label']}."
        thrift.ask_action(f"Shall I label emails from {item['sender']} as {item['label']}?", add,
                          group="weekly_labels", item=item["say"], plural="email label rules")
    question = thrift.question()
    if not await deliver("mail", f"Weekly inbox tidy-up: {question}"):
        return False
    for ws in list(connections):  # show Approve / Decline on the phone
        with contextlib.suppress(Exception):
            await ws.send_json({"type": "approval", "question": question})
    return True


mail.suggest = _offer_label_rules
watcher = PriceWatcher(memory, deliver)
bank = BankFeed(memory, deliver)
jobs = JobRunner(memory, job_finished)
jobs.register("research", research.run_research)
builder = AgentBuilder(memory, registry)
jobs.register("build_agent", builder.build)
jobs.register("code", codejob.run_code_task)
jobs.register("tutor", tutor.run_review)
for _spec in builder.active_specs():  # agents built or connected earlier
    registry.specs.setdefault(_spec.name, _spec)


async def _index_brain() -> None:
    """Keep the meaning-search vectors up to date (new facts and passages every few minutes), and
    run the brain's overnight pass once a night."""
    await asyncio.sleep(30)  # let Buddy start answering first
    while True:
        try:
            added = await asyncio.to_thread(Brain(memory).index_pending)
            if added:
                log.info("brain: indexed %s passages and facts", added)
            if thinking.night_due(memory):
                await thinking.nightly(memory)
        except Exception:
            log.exception("brain indexing failed; trying again later")
        await asyncio.sleep(180)


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    from . import social as social_module

    social_module.METER = lambda provider, endpoint: travel.log_call(memory, provider, endpoint, True, 0.0, "search")
    await registry.start()
    task = asyncio.create_task(proactive.run(memory, deliver))
    job_loop = asyncio.create_task(jobs.run())
    sync_loop = asyncio.create_task(hub.run())
    mail_loop = asyncio.create_task(mail.run())
    watch_loop = asyncio.create_task(watcher.run())
    bank_loop = asyncio.create_task(bank.run())
    plaid_loop = asyncio.create_task(plaid.Plaid(memory).run(deliver))
    health_loop = asyncio.create_task(healthcheck.run(memory, finops, deliver))
    tutor_loop = asyncio.create_task(tutor.run(memory, jobs))
    brain_loop = asyncio.create_task(_index_brain())
    if memory.get("merchants:cleaned") != "1":  # once: every stored transaction gets a clean merchant name
        from .merchants import Merchants

        async def _clean_merchants() -> None:
            try:
                Money(memory)  # adds the merchant_raw column on older databases
                changed = await asyncio.to_thread(Merchants(memory).reclean)
                memory.set("merchants:cleaned", "1")
                log.info("merchant names cleaned: %d transactions regrouped", changed)
            except Exception:
                log.exception("merchant cleaning failed")
        asyncio.create_task(_clean_merchants())
    if os.getenv("BUDDY_COMPANY_DOCS", "1") not in ("", "0"):
        from . import companydocs

        async def _company_docs() -> None:  # Buddy Labs' documents into the library, for questions
            try:
                await asyncio.to_thread(companydocs.sync, memory)
            except Exception:
                log.exception("company documents sync failed")
        asyncio.create_task(_company_docs())
    try:
        yield
    finally:
        task.cancel()
        await jobs.close()
        job_loop.cancel()
        sync_loop.cancel()
        mail_loop.cancel()
        watch_loop.cancel()
        bank_loop.cancel()
        plaid_loop.cancel()
        health_loop.cancel()
        tutor_loop.cancel()
        brain_loop.cancel()
        await registry.close()


# No public API docs: the only callers are Buddy's own apps.
app = FastAPI(title="Buddy", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)


@app.middleware("http")
async def protect(request: Request, call_next):
    """Lockout, access log and security headers for every page and API call (guard.py)."""
    from fastapi.responses import JSONResponse

    path = request.url.path
    ip, user = guard.client(request.client.host if request.client else None, request.headers)
    started = time.monotonic()
    wait = guard.guard.locked(ip) if path != "/health" else 0
    if wait:
        response = JSONResponse({"detail": "Too many failed sign-ins from this address. Try again later."},
                                status_code=429, headers={"Retry-After": str(wait)})
    else:
        response = await call_next(request)
    if response.status_code == 401 and not wait:
        guard.guard.failed(ip, user, path)
    if guard.wants_log(path, response.status_code):
        guard.guard.record("http", ip, user, request.method, path, response.status_code,
                           int((time.monotonic() - started) * 1000))
    existing = {k.lower() for k in response.headers.keys()}
    for name, value in guard.security_headers(path, request.headers.get("host", ""), existing).items():
        response.headers[name] = value
    return response


class _HideTokens(logging.Filter):
    """The phone connects with ?token=...: keep it out of the server's logs."""

    TOKEN = re.compile(r"(token=)[^&\s\"']+")

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple):
            record.args = tuple(self.TOKEN.sub(r"\1***", a) if isinstance(a, str) else a for a in record.args)
        if isinstance(record.msg, str):
            record.msg = self.TOKEN.sub(r"\1***", record.msg)
        return True


for _name in ("uvicorn.access", "uvicorn.error", "uvicorn"):
    logging.getLogger(_name).addFilter(_HideTokens())
    logging.getLogger(_name).addFilter(SecretFilter())  # uvicorn's own handlers skip the root filter


def _base_url(headers) -> str:
    host = headers.get("x-forwarded-host") or headers.get("host") or ""
    scheme = "http" if host.startswith(("127.", "localhost", "testserver")) else "https"
    return f"{scheme}://{host}" if host else ""


def _absolute(items: list[dict], ws: WebSocket) -> list[dict]:
    """Links to Buddy's own files (/api/files/...) as full addresses, so the phone can load them."""
    host = ws.headers.get("x-forwarded-host") or ws.headers.get("host") or ""
    scheme = "http" if host.startswith(("127.", "localhost")) else "https"
    out = []
    for card in items:
        card = dict(card)
        for key in ("url", "image"):
            if str(card.get(key, "")).startswith("/api/files/") and host:
                card[key] = f"{scheme}://{host}{card[key]}"
        out.append(card)
    return out


@app.get("/api/files/{file_id}")
def api_file(file_id: str, exp: str = "", sig: str = "", authorization: str | None = Header(default=None)):
    """A diagram, chart, image or document Buddy made. The chat's links carry a signature that
    expires in 30 days; the access token works too."""
    from fastapi.responses import FileResponse

    token = (authorization or "").removeprefix("Bearer ").strip()
    if not (makers.link_ok(file_id, exp, sig) or authorized(token or None)):
        raise HTTPException(status_code=401, detail="This link has expired or isn't valid.")
    found = makers.find(memory, file_id)
    if not found:
        raise HTTPException(status_code=404, detail="That file is gone.")
    path, row = found
    return FileResponse(path, media_type=makers.MIME[row["ext"]], filename=f"{row['name']}.{row['ext']}",
                        content_disposition_type="inline" if row["ext"] in ("png", "jpg") else "attachment",
                        headers={"Cache-Control": "private, max-age=86400", "X-Content-Type-Options": "nosniff"})


@app.get("/health")
def health() -> dict:
    return {"ok": True, "whisper": speech_to_text.available()}


# How speech recognition mishears "Buddy" ("Budo, Buddy, why…"); only these count on their own.
NAME_SOUNDS = {"buddy", "buddie", "budo", "buddo", "buddi", "budy", "baddy", "baddie", "bodi", "bodhi", "buddha"}
GREETINGS = {"hey", "hi", "hello", "ok", "okay", "yo"}


def addressed_to_buddy(text: str) -> str | None:
    """If speech starts with the assistant's name (or how it's misheard, or "hey" and the name),
    return the rest as a command."""
    names = NAME_SOUNDS | {settings.assistant_name.lower(), f"hey{settings.assistant_name.lower()}"}
    words = text.split()
    clean = [w.lower().strip(",.!?:;") for w in words]
    skip = 0
    if clean and clean[0] in GREETINGS and len(clean) > 1 and (clean[1] in names or clean[1] in ("body", "birdie")):
        skip = 2
    elif clean and clean[0] in names:
        skip = 1
    if not skip:
        return None
    while skip < len(clean) and clean[skip] in names:  # "Budo, Buddy, why…"
        skip += 1
    rest = " ".join(words[skip:]).lstrip(",.!?: ")
    return rest or None


def brain_error(e: anthropic.APIError) -> str:
    """Say why Claude can't be reached, in words the user can act on."""
    text = str(e).lower()
    if isinstance(e, anthropic.AuthenticationError):
        return "I can't reach my brain: the Claude API key is missing or wrong. Check ANTHROPIC_API_KEY in the server settings file."
    if "credit balance" in text or "billing" in text:
        return "I can't reach my brain: the Claude account is out of credit. Add some in the Claude Console under Billing."
    if isinstance(e, anthropic.PermissionDeniedError):
        return "I can't reach my brain: this Claude API key isn't allowed to do that. Check the key's workspace in the Claude Console."
    if isinstance(e, anthropic.NotFoundError):
        return "I can't reach my brain: the model I'm set to use isn't available on this account."
    if isinstance(e, anthropic.RateLimitError):
        return "I'm being rate limited by Claude. Give me a minute."
    if isinstance(e, anthropic.APIConnectionError):
        return "I can't reach my brain: the server can't connect to Claude right now."
    if isinstance(e, anthropic.InternalServerError) or getattr(e, "status_code", 0) in (500, 529):
        return "Claude is overloaded or having trouble right now. Try again in a minute."
    code = getattr(e, "status_code", None)
    return f"I can't reach my brain right now (error {code}). The server log has the details." if code \
        else "I can't reach my brain right now."


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


@app.get("/admin/connectors", dependencies=[Depends(require_admin)])
def admin_connectors() -> list[dict]:
    return hub.status()


@app.post("/admin/connectors/{name}/{action}", dependencies=[Depends(require_admin)])
async def admin_connector_action(name: str, action: str) -> dict:
    if name not in hub.connectors or action not in ("enable", "disable", "sync"):
        raise HTTPException(status_code=404)
    if action == "sync":
        try:
            return {"name": name, "result": await hub.sync(name)}
        except Exception as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
    hub.set_enabled(name, action == "enable")
    return {"name": name, "enabled": action == "enable"}


@app.get("/admin/audit", dependencies=[Depends(require_admin)])
def admin_audit(limit: int = 100) -> list[dict]:
    return registry.audit_log(min(limit, 1000))


@app.get("/admin/access-log", dependencies=[Depends(require_admin)])
def admin_access_log(limit: int = 200, failures: bool = False) -> dict:
    """Who called Buddy, from where, and the result (guard.py); failures=true: failed sign-ins and lockouts."""
    return {"stats": guard.guard.stats(30), "entries": guard.guard.recent(limit, only_failures=failures)}


# --- SOC 2 readiness: the assessment and its Word document (compliance.py) --------------------
@app.get("/api/compliance", dependencies=[Depends(require_admin)])
def api_compliance() -> dict:
    """The controls and their status right now, without saving a report."""
    from . import compliance

    ev = compliance.evidence(memory)
    items = compliance.controls(ev)
    return {"score": compliance.score(items), "summary": compliance.summary(items), "controls": items,
            "notice": compliance.NOTICE, "checked_at": ev["check_at"]}


@app.post("/api/compliance/report", dependencies=[Depends(require_admin)])
def api_compliance_report(request: Request) -> dict:
    """Make the report: saved in Reports, with a link to download the Word document."""
    from . import compliance

    base = _base_url(request.headers)
    if base.startswith("https://"):
        with contextlib.suppress(Exception):
            memory.set("server:base_url", base)
    try:
        out = compliance.generate(memory)
    except ToolError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    return {**out, "download": base + out["url"] if base else out["url"]}


@app.get("/admin/usage", dependencies=[Depends(require_admin)])
def admin_usage() -> dict:
    return {
        "month_to_date_usd": round(usage.month_to_date(memory), 4),
        "monthly_budget_usd": settings.monthly_budget_usd,
        "allowances": thrift.status(),
        "by_month_and_model": usage.summary(memory),
    }


@app.post("/admin/trace", dependencies=[Depends(require_admin)])
def admin_trace(on: bool) -> dict:
    """Switch the live message trace on or off (deploy/trace.sh), without a restart."""
    tracing["on"] = on
    return {"tracing": on}


@app.get("/admin/costs", dependencies=[Depends(require_admin)])
async def admin_costs(fresh: bool = False) -> dict:
    """Everything running Buddy costs this month: Claude, DigitalOcean, Zapier, fixed costs."""
    return await finops.report(fresh)


@app.get("/admin/insights", dependencies=[Depends(require_admin)])
def admin_insights(days: int = 7, recent: int = 60, model: str = "", purpose: str = "") -> dict:
    """Every model request: what it was for, which model, tokens, cost, answer time and a short preview."""
    return insights.report(memory, days=days, recent=recent, model=model, purpose=purpose)


@app.get("/admin/health", dependencies=[Depends(require_admin)])
def admin_health(history: int = 30) -> dict:
    """The latest daily health check (what got worse, what to fix) and the trend over past checks."""
    return {"latest": healthcheck.latest(memory), "history": healthcheck.history(memory, min(history, 365))}


@app.post("/admin/health/run", dependencies=[Depends(require_admin)])
async def admin_health_run() -> dict:
    """Run the health check now."""
    try:
        costs = await finops.report()
    except Exception:
        costs = None
    report = healthcheck.check(memory, finops_report=costs)
    report["id"] = healthcheck.save(memory, report)
    return report


@app.get("/admin/insights/export", dependencies=[Depends(require_admin)])
def admin_insights_export(table: str = "calls", days: int = 30):
    """CSV of the request log (calls) or reply timings (turns), to study or share for fixes.
    Previews are already short and masked."""
    import csv
    import io

    from fastapi.responses import Response

    name = {"calls": "model_calls", "turns": "turns"}.get(table)
    if not name:
        raise HTTPException(status_code=400, detail="table is calls or turns")
    since = (datetime.now(timezone.utc) - timedelta(days=max(1, min(days, 365)))).isoformat()
    rows = memory.db.execute(f"SELECT * FROM {name} WHERE at >= ? ORDER BY at", (since,)).fetchall()  # nosec B608
    out = io.StringIO()
    writer = csv.writer(out)
    if rows:
        writer.writerow(rows[0].keys())
        writer.writerows([tuple(r) for r in rows])
    return Response(out.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="buddy-{table}.csv"'})


# --- life log: files uploaded from the phone (Timeline, Takeout, Airbnb...) ----
@app.post("/api/import", dependencies=[Depends(require_admin)])
async def api_import(request: Request, name: str = "upload.json") -> dict:
    """The raw file is the body (no form encoding); ?name= keeps its file name."""
    limit = lifelog.MAX_UPLOAD_BYTES
    if int(request.headers.get("content-length") or 0) > limit:
        raise HTTPException(status_code=413, detail="That file is too big (150 MB at most).")
    chunks, size = [], 0
    async for chunk in request.stream():  # stop reading as soon as it's too big
        size += len(chunk)
        if size > limit:
            raise HTTPException(status_code=413, detail="That file is too big (150 MB at most).")
        chunks.append(chunk)
    data = b"".join(chunks)
    if not data:
        raise HTTPException(status_code=400, detail="empty file")
    try:
        return await LifeLog(memory).import_file(name, data)
    except ToolError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


# --- the library: documents, photos, PDFs, spreadsheets, CSV, JSON from the phone ----
@app.post("/api/library", dependencies=[Depends(require_admin)])
async def api_library(request: Request, name: str = "upload", note: str = "", vault: bool = False) -> dict:
    """The raw file is the body; ?name= its file name, ?note= what it is, ?vault=true to keep it locked.
    Answers at once; Buddy reads it in the background and says what it found in the chat."""
    from . import library as lib_module

    if int(request.headers.get("content-length") or 0) > lib_module.MAX_UPLOAD:
        raise HTTPException(status_code=413, detail="That file is over 25 MB.")
    chunks, size = [], 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > lib_module.MAX_UPLOAD:
            raise HTTPException(status_code=413, detail="That file is over 25 MB.")
        chunks.append(chunk)
    data = b"".join(chunks)
    lib = Library(memory)
    try:
        item = lib.add(name, data, note=note, vault=vault)
    except ToolError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    if item.get("duplicate"):
        await deliver("library", library_report(item))
        return {"id": item["id"], "status": "duplicate"}

    async def read_it() -> None:
        try:
            done = await lib.ingest(item["id"], data)
        except Exception:
            log.exception("library: reading %s failed", item["name"])
            await deliver("library", f"Saved {item['name']}, but I couldn't read it just now.")
            return
        text = library_report(done)
        with contextlib.suppress(Exception):  # searchable by meaning straight away (else the loop does it)
            await asyncio.to_thread(Brain(memory).index_pending)
        if finance.looks_like_statement(done):
            try:  # a card statement: the card's dates and amounts, its charges, a bill to remind
                got = await finance.Finance(memory).import_statement(done["id"], first_filing=True)
                if got.get("imported"):
                    text += finance.statement_line(got)
            except Exception:
                log.exception("finance: reading the statement failed")
        if done["important"]:
            facts = done["important"]

            async def keep() -> str:
                return lib.save_facts(done["id"], facts)
            thrift.ask_action("Shall I remember these?", keep)
            text += " I found: " + "; ".join(facts) + ". Shall I remember these?"
        await deliver("library", text)
    asyncio.create_task(read_it())
    return {"id": item["id"], "status": "reading", "name": item["name"]}


@app.get("/api/library", dependencies=[Depends(require_admin)])
def api_library_list(q: str = "", kind: str = "") -> dict:
    """The Library screen: recent items, or the items that match a search (by meaning and words)."""
    lib = Library(memory)
    if q.strip():
        seen, items = set(), []
        for hit in lib.search(q, limit=20):
            if hit["id"] not in seen:
                seen.add(hit["id"])
                items.append({**[i for i in lib.recent(limit=500) if i["id"] == hit["id"]][0], "match": hit["text"][:300]})
        return {"items": items, "people": []}
    return {"items": lib.recent(kind, limit=200), "people": Brain(memory).people()[:30]}


@app.get("/api/library/{item_id}", dependencies=[Depends(require_admin)])
def api_library_item(item_id: str) -> dict:
    try:
        return Library(memory).open(item_id)
    except ToolError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@app.post("/api/library/{item_id}/unlock", dependencies=[Depends(require_admin)])
def api_library_unlock(item_id: str) -> dict:
    """The owner tapping Unlock in their own app is the yes a vault item needs."""
    try:
        return {"ok": True, "text": Library(memory).unlock(item_id)}
    except ToolError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@app.delete("/api/library/{item_id}", dependencies=[Depends(require_admin)])
def api_library_forget(item_id: str) -> dict:
    try:
        return {"ok": True, "text": Library(memory).forget(item_id)}
    except ToolError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@app.get("/api/library/{item_id}/file", dependencies=[Depends(require_admin)])
def api_library_file(item_id: str):
    from fastapi.responses import Response

    from . import library as lib_module

    lib = Library(memory)
    try:
        data, row = lib_module.original(memory, item_id)
    except ToolError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    if row["vault"] and not lib.unlocked(item_id):
        raise HTTPException(status_code=403, detail="Vault item: unlock it first.")
    kind = lib_module.IMAGES.get(row["ext"]) or {"pdf": "application/pdf", "csv": "text/csv", "json": "application/json",
                                                 "txt": "text/plain"}.get(row["ext"], "application/octet-stream")
    return Response(data, media_type=kind, headers={"Content-Disposition": f'inline; filename="{row["name"]}"'})


@app.get("/api/people", dependencies=[Depends(require_admin)])
def api_people(name: str = "") -> dict:
    brain_ = Brain(memory)
    if name:
        found = brain_.person(name)
        if not found:
            raise HTTPException(status_code=404, detail="No one by that name.")
        return found
    return {"people": brain_.people(), "status": brain_.status()}


@app.get("/api/finance", dependencies=[Depends(require_admin)])
def api_finance(stopped: bool = False) -> dict:
    """The Money screen: subscriptions with totals, and the card calendar."""
    fin = finance.Finance(memory)
    use = {c["id"]: c for c in fin.credit_use()}
    cards = [{**c, **({"balance_now": use[c["id"]]["balance_now"], "used_now": use[c["id"]]["used_now"],
                       "pay_to_get_under_30": use[c["id"]]["pay_to_get_under_30"]} if c["id"] in use else {})}
             for c in fin.cards()]
    adv = advisor.Advisor(memory)
    adv.apply_presets()
    return {**fin.subscriptions(include_stopped=stopped), "cards": cards, "best_card_for": adv.best_cards(),
            "rewards": adv.rewards(), "goals": adv.goals()}


@app.post("/api/finance/subscriptions/{sub_id}", dependencies=[Depends(require_admin)])
def api_finance_decide(sub_id: int, status: str) -> dict:
    try:
        sub = finance.Finance(memory).decide(str(sub_id), status)
    except ToolError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"ok": True, "status": sub["status"]}


@app.get("/api/finance/subscriptions/{sub_id}/cancel", dependencies=[Depends(require_admin)])
def api_finance_cancel_page(sub_id: int) -> dict:
    try:
        return finance.Finance(memory).cancel_page(str(sub_id))
    except ToolError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


# --- Travel: the app's Travel screen
@app.get("/api/travel", dependencies=[Depends(require_admin)])
def api_travel() -> dict:
    t = travel.Travel(memory)
    return {"sources": t.sources(), "profile": t.profile(), "learned": t.learned(), "searches": t.searches(),
            "watches": t.watches()}


@app.post("/api/travel/search", dependencies=[Depends(require_admin)])
async def api_travel_search(body: dict) -> dict:
    t = travel.Travel(memory)
    try:
        if body.get("kind") == "hotels":
            result = await t.hotels(str(body.get("place", "")), str(body.get("check_in", "")),
                                    str(body.get("check_out", "")), int(body.get("adults") or 2))
        else:
            result = await t.flights(str(body.get("origin", "")), str(body.get("destination", "")),
                                     str(body.get("depart", "")), str(body.get("return", "") or ""),
                                     int(body.get("adults") or 1))
    except ToolError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return t.search(result["search_id"])


@app.get("/api/travel/searches/{search_id}", dependencies=[Depends(require_admin)])
def api_travel_get(search_id: str) -> dict:
    try:
        return travel.Travel(memory).search(search_id)
    except ToolError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@app.post("/api/travel/searches/{search_id}/offers/{offer_id}/refresh", dependencies=[Depends(require_admin)])
async def api_travel_refresh(search_id: str, offer_id: str) -> dict:
    try:
        return await travel.Travel(memory).refresh(search_id, offer_id)
    except (ToolError, travel.SourceError) as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@app.post("/api/travel/searches/{search_id}/offers/{offer_id}/chose", dependencies=[Depends(require_admin)])
def api_travel_chose(search_id: str, offer_id: str) -> dict:
    """The app's 'Open to book' tap: Buddy learns from the choice."""
    try:
        return travel.Travel(memory).chose(search_id, offer_id)
    except ToolError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@app.get("/api/travel/watches", dependencies=[Depends(require_admin)])
def api_travel_watches() -> list[dict]:
    return travel.Travel(memory).watches()


@app.post("/api/travel/watches", dependencies=[Depends(require_admin)])
def api_travel_watch(body: dict) -> dict:
    try:
        return travel.Travel(memory).watch(str(body.get("origin", "")), str(body.get("destination", "")),
                                           str(body.get("month", "")), body.get("target") or None,
                                           str(body.get("return_month") or ""))
    except ToolError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@app.delete("/api/travel/watches/{watch_id}", dependencies=[Depends(require_admin)])
def api_travel_unwatch(watch_id: int) -> dict:
    try:
        return {"result": travel.Travel(memory).unwatch(str(watch_id))}
    except ToolError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@app.post("/api/travel/profile", dependencies=[Depends(require_admin)])
def api_travel_profile(body: dict) -> dict:
    return travel.Travel(memory).set_profile(**{k: body.get(k) for k in travel.DEFAULT_PROFILE})


# --- card benefits dashboard (web/cards.html): every card, its credits, and each credit's details
@app.get("/api/cards/dashboard", dependencies=[Depends(require_admin)])
async def api_cards_dashboard() -> dict:
    from . import benefits
    return json.loads(json.dumps(await asyncio.to_thread(benefits.Benefits(memory).dashboard), default=str))


@app.get("/api/cards/credit/{benefit_id}", dependencies=[Depends(require_admin)])
async def api_cards_credit(benefit_id: int) -> dict:
    from . import benefits
    try:
        return json.loads(json.dumps(await asyncio.to_thread(benefits.Benefits(memory).credit_detail, benefit_id),
                                     default=str))
    except (KeyError, TypeError, ToolError) as e:
        raise HTTPException(status_code=404, detail="No such credit.") from e


@app.post("/api/cards/action", dependencies=[Depends(require_admin)])
async def api_cards_action(body: dict) -> dict:
    """track (card, name, anniversary?) · untrack (id) · used (id, amount?): from the dashboard's buttons."""
    from . import benefits
    b = benefits.Benefits(memory)
    action = str(body.get("action") or "")
    try:
        if action == "track":
            text = b.add_from_catalog(str(body.get("card") or ""), [str(body.get("name") or "")],
                                      str(body.get("anniversary") or ""))
        elif action == "untrack":
            text = b.remove(str(body.get("id") or ""))
        elif action == "used":
            amount = body.get("amount")
            text = b.mark_used(str(body.get("id") or ""), float(amount) if amount not in (None, "") else None,
                               "marked on the dashboard")
        else:
            raise ToolError("Unknown action.")
    except ToolError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"ok": True, "text": text}


# --- money dashboard (web/money.html): numbers per period, and the transactions behind any slice
@app.get("/api/money/dashboard", dependencies=[Depends(require_admin)])
async def api_money_dashboard(period: str = "last_month") -> dict:
    from . import review
    try:
        return await asyncio.to_thread(review.dashboard, memory, period)
    except ToolError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@app.get("/api/money/transactions", dependencies=[Depends(require_admin)])
async def api_money_transactions(start: str, end: str, category: str = "", detail: str = "", merchant: str = "",
                                 card: str = "", transfer: str = "", recipient: str = "") -> dict:
    from . import review
    try:
        return await asyncio.to_thread(review.drill, memory, start, end, category, detail, merchant, card, transfer,
                                       recipient)
    except ToolError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


# --- Plaid: linking a bank (the page is opened in the phone's browser; its one-time state authenticates it)
@app.get("/api/plaid", dependencies=[Depends(require_admin)])
def api_plaid() -> dict:
    p = plaid.Plaid(memory)
    return {"configured": p.configured(), "banks": p.status()}


@app.post("/api/plaid/link", dependencies=[Depends(require_admin)])
async def api_plaid_link(request: Request) -> dict:
    try:
        return await asyncio.to_thread(plaid.Plaid(memory).start_link, _base_url(request.headers))
    except (ToolError, plaid.PlaidError) as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@app.delete("/api/plaid/{item_id}", dependencies=[Depends(require_admin)])
async def api_plaid_unlink(item_id: str) -> dict:
    try:
        return {"result": await asyncio.to_thread(plaid.Plaid(memory).unlink, item_id)}
    except (ToolError, plaid.PlaidError) as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@app.get("/plaid/link")
def plaid_link_page(state: str = ""):
    from fastapi.responses import HTMLResponse

    link = plaid.Plaid(memory).pending(state)
    if not link:
        return HTMLResponse(plaid.error_page("This link expired or was already used. Ask Buddy to connect the bank "
                                             "again."), status_code=403)
    return HTMLResponse(plaid.link_page(link["token"], link["state"]), headers={"Cache-Control": "no-store",
                                                                                "Referrer-Policy": "no-referrer"})


@app.get("/plaid/oauth")
def plaid_oauth_page(oauth_state_id: str = ""):
    """Banks that sign in on their own site come back here; Link picks up where it left off."""
    from fastapi.responses import HTMLResponse

    link = plaid.Plaid(memory).pending()
    if not link or not oauth_state_id:
        return HTMLResponse(plaid.error_page("This link expired. Ask Buddy to connect the bank again."), status_code=403)
    return HTMLResponse(plaid.link_page(link["token"], link["state"], oauth=True),
                        headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})


@app.post("/plaid/exchange")
async def plaid_exchange(body: dict) -> dict:
    p = plaid.Plaid(memory)
    try:
        got = await asyncio.to_thread(p.finish_link, str(body.get("state", "")), str(body.get("public_token", "")),
                                      str(body.get("institution", "")))
    except (ToolError, plaid.PlaidError) as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    async def first_sync() -> None:
        try:
            done = await asyncio.to_thread(p.sync, True)
            await deliver("money", f"Connected {got['institution'] or 'your bank'}: {done['added']} transactions"
                          + (f" and {done['cards']} card{'s' if done['cards'] != 1 else ''} with due dates, minimums "
                             "and APRs" if done["cards"] else "") + ". Your subscriptions and card calendar are updated.")
        except Exception:
            log.exception("plaid: first sync failed")
            await deliver("money", f"Connected {got['institution'] or 'your bank'}, but the first sync failed; "
                          "I'll try again shortly.")
    asyncio.create_task(first_sync())
    return {"ok": True}


# --- Diagnostics: experience health, setup test, export (diagnostics.py; BUDDY_DIAGNOSTICS=0 turns it off)
@app.get("/api/diagnostics", dependencies=[Depends(require_admin)])
def api_diagnostics(days: int = 7) -> dict:
    since = (datetime.now(timezone.utc) - timedelta(days=max(1, min(days, 30)))).isoformat()
    return diagnostics.health(memory, since)


@app.get("/api/diagnostics/export", dependencies=[Depends(require_admin)])
def api_diagnostics_export(days: int = 30, format: str = "csv"):
    from fastapi.responses import Response

    since = (datetime.now(timezone.utc) - timedelta(days=max(1, min(days, 30)))).isoformat()
    body, kind = diagnostics.export(memory, since, "json" if format == "json" else "csv")
    name = f"buddy-diagnostics-{datetime.now(timezone.utc):%Y%m%d}.{'json' if format == 'json' else 'csv'}"
    return Response(body, media_type=kind, headers={"Content-Disposition": f'attachment; filename="{name}"'})


@app.post("/api/diagnostics/stt", dependencies=[Depends(require_admin)])
async def api_diagnostics_stt(body: dict) -> dict:
    """'Test my setup': turn the test sentence into text and say how long it took (not kept)."""
    started = time.monotonic()
    meta: dict = {}
    try:
        text, lang = await speech_to_text.transcribe(base64.b64decode(str(body.get("pcm", ""))),
                                                     str(body.get("lang", "")), "", mode="talk", meta=meta)
    except speech_to_text.WhisperUnavailable as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    return {"text": text, "lang": lang, "ms": round((time.monotonic() - started) * 1000),
            "engine": meta.get("engine", "local"), "confidence": meta.get("confidence")}


@app.post("/api/diagnostics/test", dependencies=[Depends(require_admin)])
def api_diagnostics_test(body: dict) -> dict:
    return diagnostics.rate_test(memory, body)


@app.get("/api/brain", dependencies=[Depends(require_admin)])
def api_brain() -> dict:
    """What Buddy has learned about the user: preferences, dates it watches, questions waiting."""
    brain_ = Brain(memory)
    return {"status": brain_.status(), "preferences": brain_.preferences(), "dates": brain_.dates(),
            "questions": brain_.conflicts()}


@app.delete("/api/brain/preferences/{pref_id}", dependencies=[Depends(require_admin)])
def api_forget_preference(pref_id: int) -> dict:
    if not Brain(memory).forget_preference(str(pref_id)):
        raise HTTPException(status_code=404, detail="No such preference.")
    return {"ok": True}


@app.post("/api/brain/questions/{conflict_id}", dependencies=[Depends(require_admin)])
def api_resolve(conflict_id: int, keep: str) -> dict:
    return {"result": Brain(memory).resolve(conflict_id, keep)}


# --- background job reports: the phone apps show them -----------------------
REPORT_TYPES = {"research": "Research", "money_weekly": "Money", "money_monthly": "Money", "money_review": "Money",
                "tutor": "English", "soc_report": "Security", "card_benefits": "Money",
                "code": "Code", "build_agent": "Agents"}
ARCHIVE_AFTER_DAYS = 7


@app.get("/api/jobs", dependencies=[Depends(require_admin)])
def api_jobs(limit: int = 300) -> list[dict]:
    """Reports newest first, each with its type and whether it's archived (older than a week)."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=ARCHIVE_AFTER_DAYS)).isoformat()
    out = []
    for j in jobs.list(limit=min(limit, 500)):
        when = j.get("finished_at") or j.get("created_at") or ""
        out.append({**j, "type": REPORT_TYPES.get(j.get("kind", ""), "Other"),
                    "archived": j.get("status") in ("done", "failed", "cancelled") and when < cutoff})
    return out


@app.get("/api/jobs/{job_id}", dependencies=[Depends(require_admin)])
def api_job(job_id: int) -> dict:
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="no such job")
    return job


def authorized(token: str | None) -> bool:
    if not settings.access_token:
        return settings.dev_mode  # no token set: closed, except on a developer's machine
    return token is not None and secrets.compare_digest(token, settings.access_token)


@app.websocket("/ws")
async def talk(ws: WebSocket) -> None:
    ip, user = guard.client(ws.client.host if ws.client else None, ws.headers)
    if guard.guard.locked(ip):
        guard.guard.record("ws", ip, user, "WS", "/ws", 429)
        await ws.close(code=4429)
        return
    if not authorized(ws.query_params.get("token")):
        guard.guard.record("ws", ip, user, "WS", "/ws", 401)
        guard.guard.failed(ip, user, "/ws")
        await ws.close(code=4401)
        return
    guard.guard.record("ws", ip, user, "WS", "/ws", 101)
    await ws.accept()
    convo = Conversation(memory, registry, jobs, thrift, hub, finops, mail, watcher, builder)
    connections[ws] = convo
    session_started = time.monotonic()
    diagnostics.server_event(memory, "network", "phone_connected")
    with contextlib.suppress(Exception):  # the address the phone reaches Buddy at, for links like the Plaid page
        memory.set("server:base_url", _base_url(ws.headers))
    jobs.wake.set()  # results that finished while the phone was away go out now
    current: asyncio.Task | None = None
    last_exchange = {"at": -1e9, "said": ""}  # when Buddy last answered, and what it said
    trace("·", {"type": "connected"})

    async def send(message: dict) -> None:
        trace("→", message)
        await ws.send_json(message)

    async def say(text: str) -> None:
        # "Hang on, checking" while a tool works: spoken, but shown as a status line, not an answer.
        if text in FILLERS:
            return await send({"type": "say", "text": text, "filler": True})
        # Two-language replies: "spoken || shown" (e.g. Malayalam spoken, English on screen).
        spoken, sep, shown = text.partition("||")
        if sep and spoken.strip() and shown.strip():
            return await send({"type": "say", "text": spoken.strip(), "display": shown.strip()})
        await send({"type": "say", "text": text.replace("||", " ").strip()})

    async def whisper(kind: str, text: str) -> None:
        await send({"type": "whisper", "kind": kind, "text": text})
        convo.note_proactive(text)

    listener = AmbientListener(memory, whisper, thrift)
    convo.room = listener.recent  # "what are they talking about?" in Listen-in

    async def switch_mode(mode: str) -> None:  # "sleep until I say Hey Buddy" -> Listen-in
        await send({"type": "mode", "mode": mode})

    convo.switch_mode = switch_mode

    async def open_links(links: list[dict], compare: str = "") -> None:  # Uber / Lyft with the trip filled in
        await send({"type": "open", "links": links, **({"compare": compare} if compare else {})})

    convo.open_links = open_links

    replies: dict[str, asyncio.Future] = {}  # requests to the phone waiting for its answer (the form helper)

    async def phone_request(message: dict, timeout: float = 25) -> dict:
        request_id = secrets.token_hex(4)
        replies[request_id] = asyncio.get_running_loop().create_future()
        try:
            await send({**message, "id": request_id})
            return await asyncio.wait_for(replies[request_id], timeout)
        except asyncio.TimeoutError:
            return {"error": "no answer from the phone (is the Buddy app open and up to date?)"}
        finally:
            replies.pop(request_id, None)

    convo.phone_request = phone_request

    pending: dict = {"text": "", "task": None}  # a sentence cut off by a pause, waiting for its end
    last_heard = [-1e9]  # when Whisper last sent text back
    last_user: list = ["", -1e9]  # the last thing said to Buddy, and when
    unsure_heard: dict[str, float] = {}  # room lines Whisper wasn't sure of (text -> when)

    async def start_answer(text: str, via: str) -> None:
        nonlocal current
        await stop_current()  # new speech always wins over an old reply
        await send({"type": "thinking"})  # "got it": the phone plays a soft tone
        current = asyncio.create_task(answer(text, via))

    async def answer_unfinished(via: str) -> None:
        """No rest of the sentence came: answer what there is."""
        await asyncio.sleep(FRAGMENT_WAIT_SECONDS)
        text = pending["text"]
        pending.update(text="", task=None)
        if text:
            await start_answer(text, via)

    enroll_clips: list[bytes] = []

    async def send_voice_status() -> None:
        print_ = voiceprint.stored(memory)
        await send({"type": "voice", "installed": voiceprint.available(), "enrolled": bool(print_),
                    "on": voiceprint.on(memory), "sentences": voiceprint.ENROLL_SENTENCES,
                    "consent_version": voiceprint.POLICY_VERSION, "policy_url": voiceprint.POLICY_URL})

    async def switch_now(mode: str) -> None:
        await stop_current()
        await send({"type": "say", "text": "Listen-in mode." if mode == "listen" else "Talk mode."})
        await send({"type": "mode", "mode": mode})
        await send({"type": "done", "turn": ""})

    audio_tasks: set[asyncio.Task] = set()
    room_slot: dict = {"waiting": None, "worker": None}

    async def hear_room() -> None:
        while room_slot["waiting"] is not None:
            msg, room_slot["waiting"] = room_slot["waiting"], None
            await hear_audio(msg)

    async def hear_audio(msg: dict) -> None:
        """Speech recognition on this server (Whisper): send back the text; the phone routes it
        like its own recognizer's (talk, Listen-in, "Hey Buddy")."""
        try:
            started = time.monotonic()
            pcm = base64.b64decode(msg["pcm"])
            if msg.get("mode") == "talk" and voiceprint.on(memory):
                # Talk mode answers only the user's own voice; when unsure, stay quiet.
                try:
                    who, score = await asyncio.to_thread(voiceprint.check, memory, pcm,
                                                         time.monotonic() - last_exchange["at"])
                except voiceprint.VoiceMatchUnavailable:
                    who, score = "you", None
                if score is not None:  # the scores, to see where the user's voice sits and tune the bar
                    usage.count(memory, "voice:you" if who == "you" else "voice:not_you")
                if who == "you" and score is not None:
                    trace("·", {"type": "voice_ok", "text": f"voice {score:.2f}"})
                if who != "you":
                    trace("·", {"type": "not_you", "text": f"voice {'too short' if score is None else f'{score:.2f}'}"})
                    usage.count(memory, "voice:ignored")
                    # The phone is waiting to know: say it was heard but not taken (it tells the user).
                    await send({"type": "heard", "text": "", "missed": "not_you", "id": msg.get("id"),
                                "stt_ms": round((time.monotonic() - started) * 1000), "engine": "voice match"})
                    return
            stats: dict | None = {} if msg.get("mode") == "talk" and tutor.ON else None
            meta: dict = {}
            heard, lang = await speech_to_text.transcribe(
                base64.b64decode(msg["pcm"]), str(msg.get("lang", "")), vocabulary.whisper_prompt(memory),
                **({"stats": stats} if stats is not None else {}), mode=str(msg.get("mode") or ""), meta=meta)
            if stats:
                tutor.log_speech(memory, stats, lang)
            log.info("whisper (%s) took %.1fs", meta.get("engine", "local"), time.monotonic() - started)
            usage.count(memory, f"speech:{meta.get('engine', 'local')}")
            if meta.get("groq_skipped"):
                usage.count(memory, "speech:groq_skipped")
                if wait := speech_to_text.groq_limit_seconds():  # the phone uses its own recognizer meanwhile
                    await send({"type": "speech_limit", "seconds": wait})
            if meta.get("engine") == "groq":  # in Insights with Buddy's other model calls (no text kept)
                usage.log_call(memory, provider="groq", model=speech_to_text.groq_model(), purpose="speech",
                               seconds=meta["seconds"], cost=speech_to_text.groq_cost(meta["audio_seconds"]))
            mode = "talk" if msg.get("mode") == "talk" else "room"
            verdict = speech_to_text.judge(mode, meta.get("confidence"), len(base64.b64decode(msg["pcm"])) / 32000)
            for group in ["on" if msg.get("ns") else "off"] + (
                    [f"mic-{msg.get('mic')}"] if mode == "room" and msg.get("mic") in ("phone", "headset") else []):
                speech_quality(memory, group, meta.get("confidence"),
                               kept=bool(heard) and verdict != "drop", unsure=verdict == "unsure")
            if heard and verdict == "drop":  # Talk: TV or other people, not you; not answered
                log.info("whisper: dropped an unsure %s clip (%.2f)", mode, meta.get("confidence") or 0)
                heard = ""
            stt_ms = round((time.monotonic() - started) * 1000)
            if msg.get("_got") and (started - msg["_got"]) > 0.3:  # diagnostics: waited behind other clips
                diagnostics.server_event(memory, "server", "clip_queued", wait_ms=round((started - msg["_got"]) * 1000),
                                         mode=mode)
            diagnostics.server_event(memory, "speech", "stt", with_load=stt_ms > 1500, engine=meta.get("engine", "local"),
                                     ms=stt_ms, confidence=meta.get("confidence"), lang=lang or "", mode=mode,
                                     verdict=verdict or "", heard=bool(heard))
            if meta.get("groq_skipped"):
                diagnostics.server_event(memory, "speech", "groq_fallback", error=str(meta.get("groq_skipped"))[:40])
            if heard:
                last_heard[0] = time.monotonic()
                if verdict == "unsure":
                    unsure_heard[heard] = time.monotonic()  # Listen-in: kept, but marked as unclear
                await send({"type": "heard", "text": heard, "lang": lang, "id": msg.get("id"), "stt_ms": stt_ms,
                            "engine": meta.get("engine", "local")})
            elif mode == "talk":  # nothing usable: tell the phone, so it can say so instead of waiting
                await send({"type": "heard", "text": "", "missed": "unclear" if verdict == "drop" else "nothing",
                            "id": msg.get("id"), "stt_ms": stt_ms, "engine": meta.get("engine", "local")})
        except speech_to_text.WhisperUnavailable as e:
            await send({"type": "error", "text": str(e)})
        except Exception:
            log.exception("whisper failed")

    async def answer(text: str, via: str = "user") -> None:
        try:
            agent_asks = set(registry.pending)
            thrift_asks = thrift.pending.id if thrift.pending else None
            spoken: list[str] = []

            async def say_and_keep(sentence: str) -> None:
                spoken.append(sentence)
                await say(sentence)

            started = time.monotonic()
            await convo.reply(text, say_and_keep, via)
            log.info("reply took %.1fs (%d sentences)", time.monotonic() - started, len(spoken))
            last_exchange.update(at=time.monotonic(), said=" ".join(spoken))
            # Buddy just asked for a yes (to send, book, spend...): the phone shows
            # Approve and Decline buttons, which answer "yes" or "no" for the user.
            new_thrift = thrift.pending is not None and thrift.pending.id != thrift_asks
            if new_thrift or set(registry.pending) - agent_asks:
                question = thrift.question() if new_thrift else " ".join(spoken)[-400:]
                await send({"type": "approval", "question": question})
            if convo.cards.picked:
                await send({"type": "cards", "turn": convo.turn_key,
                            "cards": _absolute(convo.cards.picked, ws)})
            await send({"type": "done", "turn": convo.turn_key})
        except asyncio.CancelledError:
            raise
        except anthropic.APIError as e:
            log.warning("Claude API error: %s", e)
            await send({"type": "error", "text": brain_error(e)})

    async def room_or_buddy(text: str, lang: str) -> None:
        """No "Hey Buddy": decide from context whether it's said to Buddy or to the room."""
        nonlocal current
        since = time.monotonic() - last_exchange["at"]
        room = [t for _, _, t in list(listener.segments)]
        if addressee.worth_checking(text, since) and await addressee.is_for_buddy(
                memory, text, last_exchange["said"], since, room):
            trace("·", {"type": "for_buddy", "text": text})
            await stop_current()
            await send({"type": "thinking"})
            current = asyncio.create_task(answer(text, "room"))
        else:
            listener.hear(text, lang)

    async def stop_current() -> None:
        if current and not current.done():
            current.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await current

    try:
        while True:
            msg = await ws.receive_json()
            trace("←", msg)
            if msg.get("type") == "hello":
                tz = str(msg.get("timezone", ""))
                if tz and set_user_tz(tz):
                    memory.set("timezone", tz)
                # Names and words to recognise: the phone's recogniser biases towards them.
                await send({"type": "vocabulary", "words": vocabulary.words(memory)})
                await send_voice_status()
            elif msg.get("type") == "voice_enroll" and msg.get("pcm"):
                # Settings -> Teach Buddy my voice: one sentence at a time, the voiceprint after the last.
                enroll_clips.append(base64.b64decode(msg["pcm"]))
                if int(msg.get("index", 0)) + 1 >= int(msg.get("total", len(voiceprint.ENROLL_SENTENCES))):
                    clips, enroll_clips[:] = list(enroll_clips), []
                    try:
                        result = await asyncio.to_thread(voiceprint.enroll, memory, clips)
                    except voiceprint.VoiceMatchUnavailable as e:
                        result = {"ok": False, "why": str(e)}
                    await send({"type": "voice_enrolled", **result})
                    await send_voice_status()
            elif msg.get("type") == "voice_enroll_start":
                enroll_clips.clear()
                if msg.get("consent"):  # the consent screen's "I agree", for this policy version
                    voiceprint.record_consent(memory, str(msg["consent"]))
            elif msg.get("type") == "voice_set":  # Settings switch / forget button
                voiceprint.run_tool(memory, {"action": "forget" if msg.get("forget") else
                                             ("turn_on" if msg.get("on") else "turn_off")})
                await send_voice_status()
            elif msg.get("type") == "interrupt":
                await stop_current()
            elif msg.get("type") == "user" and msg.get("text", "").strip():
                via = "room" if msg.get("via") == "room" else "user"  # "Hey Buddy" heard in Listen-in
                text = msg["text"].strip()
                if text == last_user[0] and time.monotonic() - last_user[1] < 3:
                    continue  # the same words twice in a row within seconds: a duplicate, not a new question
                last_user[:] = [text, time.monotonic()]
                if pending["task"]:  # the rest of a sentence cut off by a pause: put it back together
                    pending["task"].cancel()
                    text = f"{pending['text']} {text}"
                    pending.update(text="", task=None)
                if switch := mode_command(text):  # "Listen-in mode", "go to sleep", "wake up"
                    await switch_now(switch)
                    continue
                spoken = time.monotonic() - last_heard[0] < 3  # just heard by Whisper, not typed
                if via == "user" and spoken and unfinished(text) and len(text) < 400:
                    # "Why can't we use..." then a pause to think: wait a moment for the rest.
                    pending["text"] = UNFINISHED.sub("", text).strip().rstrip(".")
                    pending["task"] = asyncio.create_task(answer_unfinished(via))
                    await send({"type": "waiting"})  # the app shows "Go on…"
                    continue
                await start_answer(text, via)
            elif msg.get("type") == "ambient" and msg.get("text", "").strip():
                text = msg["text"].strip()
                for old_line in [k for k, t in unsure_heard.items() if time.monotonic() - t > 120]:
                    unsure_heard.pop(old_line, None)
                unclear = unsure_heard.pop(text, None) is not None
                command = addressed_to_buddy(text)
                if command and (switch := mode_command(command)):
                    await switch_now(switch)
                elif command:  # "Buddy, what did he just say?" is a command, not ambient talk
                    await stop_current()
                    await send({"type": "thinking"})
                    current = asyncio.create_task(answer(command, "room"))
                elif is_think_cue(text):  # "let me think": whisper them an answer now
                    listener.hear(text, str(msg.get("lang", "")))
                    listener.think()
                elif unclear:  # Whisper wasn't sure: room talk, marked, never taken as said to Buddy
                    listener.hear(text, str(msg.get("lang", "")), unclear=True)
                else:
                    asyncio.create_task(room_or_buddy(text, str(msg.get("lang", ""))))
            elif msg.get("type") == "notification":
                added = hub.ingest("notifications", [NotificationsConnector.to_record(msg)])
                if RideFollower.is_ride(msg):  # Uber / Lyft: follow the trip, say what matters
                    # (not `say`: that's this connection's speaking function)
                    ride_news, card = RideFollower(memory, record_fare=_ride_fare).update(msg)
                    if ride_news:  # the moments that matter; the rest is there for "where's my Uber?"
                        await deliver("ride", ride_news, cards=[card] if card else [])
                elif added and should_announce(msg):
                    who = str(msg.get("title", "")).strip()
                    said = mask(str(msg.get("text", "")).strip())[:200]
                    app_name = str(msg.get("app_name") or "a notification")
                    await deliver("notification", f"{app_name}{', ' + who if who else ''}: {said}")
            elif msg.get("type") in ("form_screen", "form_result"):  # the form helper answering phone_request
                waiting = replies.get(str(msg.get("id", "")))
                if waiting is not None and not waiting.done():
                    waiting.set_result(msg)
            elif msg.get("type") == "ride_screen":  # the ride reader: Uber's or Lyft's prices on screen
                news, ride_cards = take_screen(memory, msg)
                if news:
                    await deliver("ride", news, cards=ride_cards)
            elif msg.get("type") == "phone_calendar":
                device = str(msg.get("device", "phone"))
                records = PhoneCalendarConnector.to_records(msg)
                # Each phone's push replaces only that phone's events.
                hub.memory.db.execute(
                    "DELETE FROM records WHERE source = 'phone_calendar' AND body LIKE ?", (f"[{device}]%",)
                )
                hub.ingest("phone_calendar", records)
            elif msg.get("type") == "diag":  # the phone's diagnostics batch (no audio, no words)
                if diagnostics.enabled():
                    diagnostics.record_batch(memory, msg.get("events") or [])
                else:
                    await send({"type": "diag_off"})
            elif msg.get("type") == "diag_ping":  # the phone times the round trip
                await send({"type": "diag_pong", "t": msg.get("t")})
            elif msg.get("type") == "audio" and msg.get("pcm"):
                msg["_got"] = time.monotonic()
                # Speech recognition on this server (Whisper) runs beside the conversation, so the
                # phone's other messages aren't held up. Room talk that's piling up is skipped
                # (it's stale by then); what you say in Talk mode never is.
                if msg.get("mode") == "talk":
                    task = asyncio.create_task(hear_audio(msg))
                    audio_tasks.add(task)
                    task.add_done_callback(audio_tasks.discard)
                    continue
                # Room audio: one clip transcribing, one waiting, and the waiting one is always the
                # newest (often you saying "Hey Buddy"); an older one still waiting is dropped.
                if room_slot["waiting"] is not None:
                    log.info("whisper busy: dropped an older room clip for a newer one")
                room_slot["waiting"] = msg
                if room_slot["worker"] is None or room_slot["worker"].done():
                    room_slot["worker"] = asyncio.create_task(hear_room())
            elif msg.get("type") == "location":
                # The user shares where they are (Settings -> Share my location); only the latest is kept.
                with contextlib.suppress(TypeError, ValueError):
                    await asyncio.to_thread(Location(memory).update, float(msg["lat"]), float(msg["lng"]),
                                            msg.get("accuracy"))
            elif msg.get("type") == "voice_timing":  # how long each step of one utterance took, for Insights
                with contextlib.suppress(Exception):
                    insights.record_voice_timing(memory, msg)
            elif msg.get("type") == "rate" and msg.get("turn"):
                up = msg.get("up")
                usage.rate_turn(memory, str(msg["turn"])[:32], None if up is None else bool(up))
            elif msg.get("type") == "location_off":
                Location(memory).forget()
            elif msg.get("type") == "ambient_stop":
                listener.stop()
    except WebSocketDisconnect as e:
        trace("·", {"type": "disconnected", "text": f"code {e.code}"})
    finally:
        diagnostics.server_event(memory, "network", "phone_disconnected",
                                 session_s=round(time.monotonic() - session_started))
        listener.stop()
        connections.pop(ws, None)
        await stop_current()


# The voice client (works in phone browsers today; native apps come later).
app.mount("/", StaticFiles(directory=settings.web_dir, html=True), name="web")
