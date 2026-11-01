"""The Reports tab: each report's type, and archived once older than a week; Insights per model."""

from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from buddy import usage


def test_reports_have_a_type_and_archive_after_a_week():
    from buddy import server
    from buddy.jobs import SCHEMA

    db = server.memory.db
    db.executescript(SCHEMA)
    old = (datetime.now(timezone.utc) - timedelta(days=9)).isoformat()
    new = datetime.now(timezone.utc).isoformat()
    for kind, title, when in (("money_weekly", "Money this week, old", old), ("research", "Phone plans", new),
                              ("tutor", "English review", old)):
        db.execute("INSERT INTO jobs (kind, title, params, status, created_at, finished_at, summary) VALUES "
                   "(?, ?, '{}', 'done', ?, ?, 's')", (kind, title, when, when))
    db.commit()
    with TestClient(server.app) as c:
        jobs = {j["title"]: j for j in c.get("/api/jobs").json()}
    assert jobs["Money this week, old"]["type"] == "Money" and jobs["Money this week, old"]["archived"]
    assert jobs["Phone plans"]["type"] == "Research" and not jobs["Phone plans"]["archived"]
    assert jobs["English review"]["type"] == "English"


def test_insights_shows_what_each_model_was_used_for():
    from buddy import insights, server

    class U:
        def __init__(self, i, o):
            self.input_tokens, self.output_tokens = i, o
            self.cache_read_input_tokens = self.cache_creation_input_tokens = 0
    usage.record(server.memory, "claude-haiku-5-5", U(1200, 80), purpose="chat")
    usage.record(server.memory, "claude-haiku-5-5", U(3000, 400), purpose="brain:night")
    usage.record(server.memory, "claude-haiku-5-5", U(800, 60), purpose="chat")
    report = insights.report(server.memory, days=1)
    haiku = next(m for m in report["by_model"] if m["model"] == "claude-haiku-5-5")
    kinds = {k["name"]: k for k in haiku["purposes"]}
    assert kinds["chat"]["requests"] >= 2 and kinds["chat"]["input_tokens"] >= 2000
    assert kinds["brain:night"]["output_tokens"] >= 400
    assert "cache_write_tokens" in haiku
