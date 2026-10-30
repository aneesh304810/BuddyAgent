"""The daily health check: what got worse lately, and what to fix."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from fastapi.testclient import TestClient

from buddy import free_models as fm
from buddy import health, insights, server, usage
from buddy.memory import Memory

NOW = datetime.now(timezone.utc)


def turn(mem, i, days_ago, first=1.0, total=2.0, outcome="ok", first_model="h", final_model="h", tools=""):
    mem.db.execute(
        "INSERT INTO turns (turn, at, via, first_model, final_model, first_word_seconds, total_seconds, rounds, "
        "tools, sentences, filler, outcome) VALUES (?, ?, 'user', ?, ?, ?, ?, 1, ?, 1, 0, ?)",
        (f"t{days_ago}-{i}", (NOW - timedelta(days=days_ago, minutes=i)).isoformat(), first_model, final_model,
         first, total, tools, outcome))


def call(mem, days_ago, provider="claude", purpose="chat", ok=1, verdict="", stop="", rating=None, turn_id=""):
    mem.db.execute(
        "INSERT INTO model_calls (at, provider, model, purpose, ok, verdict, stop, rating, turn, cost_usd, seconds) "
        "VALUES (?, ?, 'm', ?, ?, ?, ?, ?, ?, 0.01, 1)",
        ((NOW - timedelta(days=days_ago)).isoformat(), provider, purpose, ok, verdict, stop, rating, turn_id))


def test_quiet_week_is_good():
    mem = Memory(":memory:")
    for i in range(10):
        turn(mem, i, 1)
        turn(mem, i, 9)
    report = health.check(mem, NOW)
    assert report["status"] == "good" and report["findings"] == []


def test_slower_failing_and_inaccurate_week_is_flagged_with_fixes():
    mem = Memory(":memory:")
    for i in range(10):
        turn(mem, i, 9, first=1.0)                                     # the week before: quick
        turn(mem, i, 2, first=6.0, total=30, tools="web_search")        # this week: slow
    for i in range(3):
        turn(mem, 100 + i, 2, outcome="error: Timeout")
    for i in range(10):
        call(mem, 2, provider="deepseek", purpose="general:code", verdict="redone" if i < 6 else "used")
        call(mem, 2, provider="groq", purpose="general:translate", ok=0, stop="error: OSError")
    report = health.check(mem, NOW)
    titles = " | ".join(f["title"] for f in report["findings"])
    assert report["status"] == "act"
    assert "First words take 6.0s" in titles
    assert "replies failed" in titles
    assert "groq fails" in titles
    assert "deepseek gets general task: code wrong" in titles
    assert all(f["fix"] for f in report["findings"])
    assert report["findings"][0]["severity"] == "act"  # most urgent first


def test_reconciliation_catches_unlogged_spend():
    mem = Memory(":memory:")
    call(mem, 5)  # the log started 5 days ago
    usage.record(mem, "claude-sonnet-5-5", SimpleNamespace(input_tokens=1000, output_tokens=10,
                                                           cache_read_input_tokens=0, cache_creation_input_tokens=0))
    assert not any(f["area"] == "reconciliation" for f in health.check(mem, NOW)["findings"])
    two_days_ago = (NOW - timedelta(days=2)).date().isoformat()
    mem.db.execute("INSERT INTO usage (day, model, cost_usd) VALUES (?, 'claude-x', 5.0)", (two_days_ago,))
    report = health.check(mem, NOW)
    assert any(f["area"] == "reconciliation" for f in report["findings"])
    assert report["metrics"]["reconciliation"]["tally_usd"] == 5.0
    # Spend from before the log existed doesn't count.
    old = (NOW - timedelta(days=6)).date().isoformat()
    mem.db.execute("DELETE FROM usage WHERE day = ?", (two_days_ago,))
    mem.db.execute("INSERT INTO usage (day, model, cost_usd) VALUES (?, 'claude-x', 5.0)", (old,))
    assert not any(f["area"] == "reconciliation" for f in health.check(mem, NOW)["findings"])


def test_reports_are_kept_and_trend_over_time():
    mem = Memory(":memory:")
    health.save(mem, health.check(mem, NOW))
    health.save(mem, health.check(mem, NOW))
    assert health.latest(mem)["status"] == "good"
    assert len(health.history(mem)) == 2
    assert health.morning_line(mem) == ""


def test_free_models_learn_from_claudes_reviews():
    mem = Memory(":memory:")
    env = {"BUDDY_FREE_MODELS": "1", "BUDDY_OPENROUTER_KEY": "o", "BUDDY_GITHUB_MODELS_KEY": "h"}
    f = fm.FreeModels(mem, env=env)
    assert f.order("code")[0] == "deepseek"
    for i in range(6):
        call(mem, 1, provider="deepseek", purpose="general:code", verdict="redone")
    f._accuracy = (0.0, {})  # refresh now instead of in 10 minutes
    assert f.order("code")[0] == "github"


def test_speed_in_insights():
    mem = Memory(":memory:")
    for i in range(4):
        turn(mem, i, 0, first=1.0 + i, total=3.0, tools="find_places")
    turn(mem, 9, 0, first_model="haiku", final_model="sonnet")
    s = insights.report(mem)["speed"]
    assert s["replies"] == 5 and s["first_word_median"] == 2.0
    assert s["handed_over_rate"] == 0.2 and s["slowest_tools"][0]["tool"] == "find_places"
    assert any("→" in r["route"] for r in s["by_route"])


def test_health_endpoints_and_export(monkeypatch):
    monkeypatch.setattr(server, "authorized", lambda t: t == "s3cret")
    headers = {"Authorization": "Bearer s3cret"}
    with TestClient(server.app) as client:
        assert client.get("/admin/health").status_code == 401
        ran = client.post("/admin/health/run", headers=headers).json()
        assert ran["status"] in ("good", "watch", "act") and ran["id"]
        assert client.get("/admin/health", headers=headers).json()["latest"]["id"] == ran["id"]
        csv = client.get("/admin/insights/export?table=turns", headers=headers)
        assert csv.status_code == 200 and "text/csv" in csv.headers["content-type"]
        assert client.get("/admin/insights/export?table=facts", headers=headers).status_code == 400


def test_daily_digest_goes_to_the_chat_once(monkeypatch):
    import asyncio

    from buddy import agent

    mem = Memory(":memory:")
    for i in range(10):
        turn(mem, i, 1)
    report = health.check(mem, NOW)
    report["id"] = health.save(mem, report)
    mem.set("health:digest_pending", str(report["id"]))
    asked = []

    async def fake_compose(system, instruction, memory=None, label=""):
        asked.append((instruction, label))
        return "You were quick this week. Nothing needed. Tap for the full picture."
    monkeypatch.setattr(agent, "compose", fake_compose)
    sent = []

    async def deliver(kind, text, **extra):
        sent.append((kind, text, extra))
        return True

    assert asyncio.run(health.send_digest(mem, deliver))
    assert sent == [("health", "You were quick this week. Nothing needed. Tap for the full picture.",
                     {"status": "good"})]
    assert asked[0][1] == "health_digest" and '"replies": 10' in asked[0][0]
    assert mem.get("health:digest_pending") is None and not health.digest_due(mem)


def test_digest_waits_for_a_phone_and_has_a_plain_fallback(monkeypatch):
    import asyncio

    from buddy import agent

    mem = Memory(":memory:")
    for i in range(3):
        turn(mem, 100 + i, 1, outcome="error: Timeout")
    for i in range(10):
        turn(mem, i, 1)
    report = health.check(mem, NOW)
    health.save(mem, report)
    mem.set("health:digest_pending", "1")

    async def broken(*a, **k):
        raise RuntimeError("over budget")
    monkeypatch.setattr(agent, "compose", broken)

    async def nobody(kind, text, **extra):
        return False
    assert not asyncio.run(health.send_digest(mem, nobody))
    assert mem.get("health:digest_pending") == "1"  # still waiting
    assert health.plain_digest(report).startswith("Daily check: ") and "fix:" in health.plain_digest(report)
