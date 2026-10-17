"""Background jobs: queue, run, fail, cancel, survive restarts, deliver later."""

import asyncio

import pytest

from buddy import jobs as jobs_mod
from buddy.jobs import JobFailed, JobResult, JobRunner, run_job_tool
from buddy.memory import Memory
from buddy.tools import ToolError


def make_runner(notify_ok=True):
    mem = Memory(":memory:")
    told = []

    async def notify(job):
        if notify_ok is True or (callable(notify_ok) and notify_ok()):
            told.append(job)
            return True
        return False

    return JobRunner(mem, notify, max_parallel=2), told


async def drive(runner, until, timeout=3.0):
    loop = asyncio.create_task(runner.run())
    try:
        async with asyncio.timeout(timeout):
            while not until():
                await asyncio.sleep(0.01)
    finally:
        await runner.close()
        loop.cancel()


def test_job_runs_and_user_is_told(monkeypatch):
    monkeypatch.setattr(jobs_mod, "TICK_SECONDS", 0.01)
    runner, told = make_runner()

    async def handler(job, ctx):
        return JobResult(summary=f"answer to {job['params']['q']}", report="# Report", sources=[{"title": "a", "url": "https://a"}])

    runner.register("research", handler)
    job_id = runner.submit("research", "rent in Lima", {"q": "rent"})
    asyncio.run(drive(runner, lambda: told))
    job = runner.get(job_id)
    assert job["status"] == "done"
    assert job["summary"] == "answer to rent"
    assert job["sources"] == [{"title": "a", "url": "https://a"}]
    assert job["delivered"] == 1
    assert told[0]["id"] == job_id


def test_failure_is_reported_with_reason(monkeypatch):
    monkeypatch.setattr(jobs_mod, "TICK_SECONDS", 0.01)
    runner, told = make_runner()

    async def handler(job, ctx):
        raise JobFailed("no sources found")

    runner.register("research", handler)
    job_id = runner.submit("research", "x", {})
    asyncio.run(drive(runner, lambda: told))
    assert runner.get(job_id)["status"] == "failed"
    assert runner.get(job_id)["error"] == "no sources found"


def test_results_wait_for_the_phone(monkeypatch):
    monkeypatch.setattr(jobs_mod, "TICK_SECONDS", 0.01)
    online = {"now": False}
    runner, told = make_runner(notify_ok=lambda: online["now"])

    async def handler(job, ctx):
        return JobResult(summary="done")

    runner.register("research", handler)
    job_id = runner.submit("research", "x", {})

    async def scenario():
        loop = asyncio.create_task(runner.run())
        async with asyncio.timeout(3):
            while runner.get(job_id)["status"] != "done":
                await asyncio.sleep(0.01)
            await asyncio.sleep(0.05)
            assert told == [] and runner.get(job_id)["delivered"] == 0
            online["now"] = True  # the phone reconnects
            while not told:
                await asyncio.sleep(0.01)
        await runner.close()
        loop.cancel()

    asyncio.run(scenario())
    assert runner.get(job_id)["delivered"] == 1


def test_cancel_running_job(monkeypatch):
    monkeypatch.setattr(jobs_mod, "TICK_SECONDS", 0.01)
    runner, told = make_runner()

    async def handler(job, ctx):
        await asyncio.sleep(60)

    runner.register("research", handler)
    job_id = runner.submit("research", "x", {})

    async def scenario():
        loop = asyncio.create_task(runner.run())
        async with asyncio.timeout(3):
            while job_id not in runner.tasks:
                await asyncio.sleep(0.01)
            assert runner.cancel(job_id)
            while runner.get(job_id)["status"] != "cancelled":
                await asyncio.sleep(0.01)
        await runner.close()
        loop.cancel()

    asyncio.run(scenario())
    assert told == []  # you cancelled it; no announcement


def test_restart_resumes_running_job_once():
    runner, _ = make_runner()
    runner.register("research", lambda job, ctx: None)
    job_id = runner.submit("research", "x", {})
    runner.memory.db.execute("UPDATE jobs SET status = 'running', attempts = 1 WHERE id = ?", (job_id,))
    runner._recover()
    assert runner.get(job_id)["status"] == "queued"
    runner.memory.db.execute("UPDATE jobs SET status = 'running', attempts = 2 WHERE id = ?", (job_id,))
    runner._recover()
    assert runner.get(job_id)["status"] == "failed"


def test_shutdown_leaves_running_job_for_next_start(monkeypatch):
    monkeypatch.setattr(jobs_mod, "TICK_SECONDS", 0.01)
    runner, _ = make_runner()

    async def handler(job, ctx):
        await asyncio.sleep(60)

    runner.register("research", handler)
    job_id = runner.submit("research", "x", {})
    asyncio.run(drive(runner, lambda: job_id in runner.tasks))
    assert runner.get(job_id)["status"] == "running"


def test_start_research_tool_masks_the_question():
    runner, _ = make_runner()
    runner.register("research", lambda job, ctx: None)
    out = run_job_tool(runner, "start_research", {
        "question": "Which card is best for my 4111 1111 1111 1111 spending?",
        "depth": "quick", "mode": "web",
    })
    assert "started" in out
    job = runner.get(1)
    assert "4111 1111" not in job["params"]["question"] and "****1111" in job["params"]["question"]


def test_daily_limit():
    runner, _ = make_runner()
    runner.register("research", lambda job, ctx: None)
    original = jobs_mod.settings
    jobs_mod.settings = type("S", (), {"research_daily_limit": 1, "monthly_budget_usd": 40})()
    try:
        run_job_tool(runner, "start_research", {"question": "a", "depth": "quick", "mode": "web"})
        with pytest.raises(ToolError, match="limit"):
            run_job_tool(runner, "start_research", {"question": "b", "depth": "quick", "mode": "web"})
    finally:
        jobs_mod.settings = original


def test_job_report_and_list():
    runner, _ = make_runner()
    runner.register("research", lambda job, ctx: None)
    job_id = runner.submit("research", "laptops under 1500", {})
    runner._finish(job_id, "done", summary="The X1 wins.", report="# R\n" + "x" * 20000, findings='["a"]', sources="[]")
    assert "laptops" in run_job_tool(runner, "list_jobs", {"query": "laptops"})
    report = run_job_tool(runner, "job_report", {"id": job_id})
    assert "The X1 wins." in report and "[report shortened]" in report
