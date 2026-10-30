"""Background jobs: work Buddy does while you get on with your day.

Jobs live in the encrypted database, so they survive restarts: a job that was
running when the server stopped starts again (once). When a job finishes,
Buddy tells you; if your phone is offline, it tells you when you reconnect.
"""

import asyncio
import json
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone

from . import usage
from .config import settings
from .memory import Memory
from .privacy import mask
from .thrift import Thrift
from .tools import ToolError

log = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY,
    kind TEXT NOT NULL,
    title TEXT NOT NULL,
    params TEXT NOT NULL,           -- JSON, masked
    status TEXT NOT NULL,           -- queued | running | done | failed | cancelled
    created_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    summary TEXT,                   -- short, spoken
    report TEXT,                    -- Markdown
    findings TEXT,                  -- JSON list
    sources TEXT,                   -- JSON list of {title, url}
    error TEXT,
    cost_usd REAL NOT NULL DEFAULT 0,
    attempts INTEGER NOT NULL DEFAULT 0,
    delivered INTEGER NOT NULL DEFAULT 0
);
"""

MAX_ATTEMPTS = 2
TICK_SECONDS = 2.0
FINISHED = ("done", "failed", "cancelled")


class JobFailed(Exception):
    """A job could not finish; the message is told to the user."""


@dataclass
class JobResult:
    summary: str
    report: str = ""
    findings: list[str] = field(default_factory=list)
    sources: list[dict] = field(default_factory=list)
    confidence: str = ""  # high | medium | low, when the job can judge it


class JobContext:
    """What a running job can use: memory, and cost tracking against its cap."""

    def __init__(self, memory: Memory, job: dict):
        self.memory = memory
        self.job = job
        self.cost = 0.0
        self.step_started = time.monotonic()

    def record(self, response) -> float:
        # Time since the last step: the model's answer time (plus any tool work in between).
        cost = usage.record(self.memory, response.model, response.usage, response=response,
                            started=self.step_started, label=self.job.get("kind") or "job")
        self.step_started = time.monotonic()
        self.cost += cost
        self.memory.db.execute("UPDATE jobs SET cost_usd = ? WHERE id = ?", (self.cost, self.job["id"]))
        self.memory.db.commit()
        return cost


Handler = Callable[[dict, JobContext], Awaitable[JobResult]]
# Tells the user a job finished; returns False when no phone is listening.
Notify = Callable[[dict], Awaitable[bool]]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class JobRunner:
    def __init__(self, memory: Memory, notify: Notify, max_parallel: int | None = None):
        self.memory = memory
        self.notify = notify
        self.max_parallel = max_parallel or settings.max_parallel_jobs
        self.handlers: dict[str, Handler] = {}
        self.tasks: dict[int, asyncio.Task] = {}
        self.wake = asyncio.Event()
        self.closing = False
        memory.db.executescript(SCHEMA)
        try:  # added after the first release
            memory.db.execute("ALTER TABLE jobs ADD COLUMN confidence TEXT")
        except Exception:
            pass

    def register(self, kind: str, handler: Handler) -> None:
        self.handlers[kind] = handler

    # --- queue -----------------------------------------------------------
    def submit(self, kind: str, title: str, params: dict) -> int:
        if kind not in self.handlers:
            raise ToolError(f"No job type {kind!r}.")
        cur = self.memory.db.execute(
            "INSERT INTO jobs (kind, title, params, status, created_at) VALUES (?, ?, ?, 'queued', ?)",
            (kind, mask(title)[:120], json.dumps(params), _now()),
        )
        self.memory.db.commit()
        self.wake.set()
        return cur.lastrowid

    def started_today(self, kind: str) -> int:
        today = datetime.now(timezone.utc).date().isoformat()
        row = self.memory.db.execute(
            "SELECT COUNT(*) AS n FROM jobs WHERE kind = ? AND created_at LIKE ?", (kind, f"{today}%")
        ).fetchone()
        return int(row["n"])

    def get(self, job_id: int) -> dict | None:
        row = self.memory.db.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return _decode(row) if row else None

    def list(self, query: str = "", limit: int = 20) -> list[dict]:
        sql = "SELECT id, kind, title, status, created_at, finished_at, summary, cost_usd FROM jobs"
        args: list = []
        words = [w for w in query.lower().split() if len(w) > 2]
        if words:
            sql += " WHERE " + " OR ".join("lower(title || ' ' || COALESCE(summary, '')) LIKE ?" for _ in words)
            args = [f"%{w}%" for w in words]
        rows = self.memory.db.execute(sql + " ORDER BY id DESC LIMIT ?", args + [limit])
        return [dict(r) for r in rows]

    def cancel(self, job_id: int) -> bool:
        job = self.get(job_id)
        if not job or job["status"] in FINISHED:
            return False
        if job_id in self.tasks:
            self.tasks[job_id].cancel()  # the task marks itself cancelled
        else:
            self._finish(job_id, "cancelled", error="Cancelled before it started.", delivered=1)
        return True

    # --- running ---------------------------------------------------------
    async def run(self) -> None:
        """Run queued jobs forever and deliver finished ones."""
        self._recover()
        while not self.closing:
            try:
                self._start_queued()
                await self.deliver_pending()
            except Exception:
                log.exception("job loop error")
            self.wake.clear()
            try:
                await asyncio.wait_for(self.wake.wait(), TICK_SECONDS)
            except asyncio.TimeoutError:
                pass

    async def close(self) -> None:
        """Stop for a restart: running jobs stay 'running' and resume next start."""
        self.closing = True
        self.wake.set()
        for task in list(self.tasks.values()):
            task.cancel()
        await asyncio.gather(*self.tasks.values(), return_exceptions=True)

    async def deliver_pending(self) -> None:
        rows = self.memory.db.execute(
            "SELECT * FROM jobs WHERE delivered = 0 AND status IN ('done', 'failed') ORDER BY id"
        ).fetchall()
        for row in rows:
            if not await self.notify(_decode(row)):
                return  # nobody listening; try again later
            self.memory.db.execute("UPDATE jobs SET delivered = 1 WHERE id = ?", (row["id"],))
            self.memory.db.commit()

    def _recover(self) -> None:
        for row in self.memory.db.execute("SELECT id, attempts FROM jobs WHERE status = 'running'").fetchall():
            if row["attempts"] < MAX_ATTEMPTS:
                self.memory.db.execute("UPDATE jobs SET status = 'queued' WHERE id = ?", (row["id"],))
            else:
                self._finish(row["id"], "failed", error="Stopped twice by server restarts.")
        self.memory.db.commit()

    def _start_queued(self) -> None:
        free = self.max_parallel - len(self.tasks)
        if free <= 0:
            return
        rows = self.memory.db.execute(
            "SELECT * FROM jobs WHERE status = 'queued' ORDER BY id LIMIT ?", (free,)
        ).fetchall()
        for row in rows:
            job = _decode(row)
            self.memory.db.execute(
                "UPDATE jobs SET status = 'running', started_at = ?, attempts = attempts + 1 WHERE id = ?",
                (_now(), job["id"]),
            )
            self.memory.db.commit()
            self.tasks[job["id"]] = asyncio.create_task(self._execute(job))

    async def _execute(self, job: dict) -> None:
        job_id = job["id"]
        try:
            handler = self.handlers.get(job["kind"])
            if handler is None:
                raise JobFailed(f"No handler for {job['kind']} jobs.")
            if usage.month_to_date(self.memory) >= Thrift(self.memory).month_budget():
                raise JobFailed("This month's AI budget is used up.")
            result = await handler(job, JobContext(self.memory, job))
            self._finish(
                job_id, "done",
                summary=result.summary, report=result.report,
                findings=json.dumps(result.findings), sources=json.dumps(result.sources),
                confidence=result.confidence or None,
            )
        except asyncio.CancelledError:
            if not self.closing:
                self._finish(job_id, "cancelled", error="Cancelled.", delivered=1)
            raise
        except JobFailed as e:
            self._finish(job_id, "failed", error=str(e))
        except Exception as e:
            log.exception("job %s failed", job_id)
            self._finish(job_id, "failed", error=f"Something went wrong ({type(e).__name__}).")
        finally:
            self.tasks.pop(job_id, None)
            self.wake.set()

    def _finish(self, job_id: int, status: str, delivered: int = 0, **fields) -> None:
        fields = {"status": status, "finished_at": _now(), "delivered": delivered, **fields}
        assignments = ", ".join(f"{k} = ?" for k in fields)
        self.memory.db.execute(f"UPDATE jobs SET {assignments} WHERE id = ?", [*fields.values(), job_id])
        self.memory.db.commit()


def _decode(row) -> dict:
    job = dict(row)
    for key in ("params", "findings", "sources"):
        if job.get(key):
            job[key] = json.loads(job[key])
    return job


# --- tools for the conversation ---------------------------------------------

JOB_TOOLS = [
    {
        "name": "start_research",
        "description": (
            "Hand a question to the research and analytics agent. It works in the background "
            "and you tell the user when it's done; meanwhile keep talking. Use it for anything "
            "needing several sources, careful comparison or calculations, not for quick facts "
            "(use web_search for those). depth 'quick' (roughly 20-50 cents) for a focused "
            "question; 'deep' (roughly 1-3 dollars) for a full report. Before a deep job, tell "
            "the user the plan and cost in one sentence and start it only after they agree. "
            "mode 'web' researches online; mode 'data' analyzes figures you pass in `data` "
            "(for example from another agent) with code."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "question": {"type": "string", "description": "The full question, with context"},
                "depth": {"type": "string", "enum": ["quick", "deep"]},
                "mode": {"type": "string", "enum": ["web", "data"]},
                "data": {"type": "string", "description": "Only for mode 'data': the figures to analyze"},
            },
            "required": ["question", "depth", "mode"],
            "additionalProperties": False,
        },
    },
    {
        "name": "find_deals",
        "description": (
            "Hand a deal hunt to the research agent: the best flights, hotels, car hire, or the best "
            "price on something to buy. It compares many sellers on the TRUE total (hidden fees: baggage, "
            "seats, resort and booking fees, taxes at checkout, shipping), checks cancellation and refund "
            "rules, seller complaints, coupons and reviews, and says whether to buy now or wait. "
            "Runs in the background like start_research (same costs). Put every detail in the request: "
            "dates, flexibility, cities, budget, model, size. Afterwards you can offer watch_price."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "request": {"type": "string", "description": "What to find, with all the details"},
                "category": {"type": "string", "enum": ["travel", "shopping", "other"]},
                "depth": {"type": "string", "enum": ["quick", "deep"]},
            },
            "required": ["request", "category", "depth"],
            "additionalProperties": False,
        },
    },
    {
        "name": "list_jobs",
        "description": "List recent background jobs (research and others) with status and short "
        "summary. Optional keywords filter by topic.",
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "additionalProperties": False,
        },
    },
    {
        "name": "job_report",
        "description": "Read a finished job's report, key findings and sources, to answer the "
        "user's follow-up questions about it.",
        "input_schema": {
            "type": "object",
            "properties": {"id": {"type": "integer"}},
            "required": ["id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "cancel_job",
        "description": "Cancel a queued or running background job.",
        "input_schema": {
            "type": "object",
            "properties": {"id": {"type": "integer"}},
            "required": ["id"],
            "additionalProperties": False,
        },
    },
]
JOB_TOOL_NAMES = {t["name"] for t in JOB_TOOLS}
# Rough cost of one research job, for the approval question.
RESEARCH_COST = {"quick": (0.35, "about 20 to 50 cents"), "deep": (2.00, "about 1 to 3 dollars")}
REPORT_CHARS_FOR_CLAUDE = 12_000


def run_job_tool(runner: JobRunner, name: str, args: dict, thrift: Thrift | None = None) -> str:
    if name == "find_deals":  # a research job in deals mode
        category = args.get("category") if args.get("category") in ("travel", "shopping") else "other"
        args = {"question": args.get("request", ""), "depth": args.get("depth", "quick"),
                "mode": "deals", "category": category}
        name = "start_research"
    if name == "start_research":
        if runner.started_today("research") >= settings.research_daily_limit:
            raise ToolError(
                f"Today's limit of {settings.research_daily_limit} research jobs is reached "
                "(BUDDY_RESEARCH_DAILY_LIMIT)."
            )
        budget = thrift.month_budget() if thrift else settings.monthly_budget_usd
        if usage.month_to_date(runner.memory) >= budget:
            raise ToolError("This month's AI budget is used up.")
        question = mask(args["question"].strip())
        if not question:
            raise ToolError("The question is empty.")
        params = {
            "question": question,
            "depth": "deep" if args.get("depth") == "deep" else "quick",
            "mode": args.get("mode") if args.get("mode") in ("data", "deals") else "web",
        }
        if params["mode"] == "deals":
            params["category"] = args.get("category", "other")
        if params["mode"] == "data":
            params["data"] = mask(args.get("data", ""))[:100_000]
        estimate, in_words = RESEARCH_COST[params["depth"]]
        if thrift and estimate > settings.approve_over_usd:
            async def start() -> str:
                runner.submit("research", question, params)
                return "Great, it's started. I'll tell you when it's done."

            topic = question if len(question) <= 80 else question[:77] + "…"
            what = "deal hunt" if params["mode"] == "deals" else "research job"
            return thrift.ask_action(
                f"A {params['depth']} {what} on \"{topic}\" costs {in_words}. Shall I start it?", start
            )
        job_id = runner.submit("research", question, params)
        return f"Research job {job_id} started ({params['depth']}). The result will arrive by itself."
    if name == "list_jobs":
        jobs = runner.list(args.get("query", ""))
        return json.dumps(jobs) if jobs else "No jobs yet."
    if name == "job_report":
        job = runner.get(int(args["id"]))
        if not job:
            raise ToolError("No such job.")
        report = job.get("report") or ""
        if len(report) > REPORT_CHARS_FOR_CLAUDE:
            report = report[:REPORT_CHARS_FOR_CLAUDE] + "\n[report shortened]"
        return json.dumps({
            "id": job["id"], "title": job["title"], "status": job["status"],
            "summary": job.get("summary"), "findings": job.get("findings"),
            "sources": job.get("sources"), "error": job.get("error"), "report": report,
        })
    if name == "cancel_job":
        return "Cancelled." if runner.cancel(int(args["id"])) else "That job isn't running or queued."
    raise ToolError(f"Unknown tool {name}.")
