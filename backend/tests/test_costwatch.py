"""Cost watch: the daily analysis finds the patterns that waste money, with the code they live in."""

from datetime import datetime, timedelta, timezone

from buddy import costwatch
from buddy.memory import Memory


def add(m, at, purpose, model="claude-sonnet-5-5", cost=0.01, inp=2000, out=150, request="write the nudge", ok=1,
        stop="end_turn"):
    m.db.execute("INSERT INTO model_calls (at, provider, model, purpose, input_tokens, output_tokens, cost_usd, ok, "
                 "stop, request) VALUES (?, 'claude', ?, ?, ?, ?, ?, ?, ?, ?)",
                 (at.isoformat(), model, purpose, inp, out, cost, ok, stop, request))


def test_the_briefing_loop_is_found_with_its_code_and_a_fix():
    m = Memory(":memory:")
    today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    for i in range(7):  # a usual week: one briefing a day
        add(m, today - timedelta(days=2 + i, hours=-8), "briefing", cost=0.01, request=f"brief {i}")
    for i in range(800):  # yesterday: every 15 seconds while the phone was away
        add(m, today - timedelta(days=1) + timedelta(seconds=15 * i), "briefing", cost=0.006)
    r = costwatch.analyse(m)
    kinds = {f["kind"] for f in r["findings"]}
    assert {"loop", "spike", "big model"} <= kinds
    loop = [f for f in r["findings"] if f["kind"] == "loop"][0]
    assert "800 calls" in loop["what"] and loop["saving_month_usd"] > 100
    assert any(c.startswith("backend/buddy/proactive.py:") for c in loop["code"])
    assert "repeat" not in kinds  # the loop already explains the repeats
    md = costwatch.markdown(r, [])
    assert "fix the cost bugs" in md and "[loop]" in costwatch.fix_brief(r)
    assert "Fixing them saves about" in costwatch.message(r)


def test_a_quiet_day_has_nothing_to_fix():
    m = Memory(":memory:")
    today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    add(m, today - timedelta(hours=5), "chat", cost=0.02)
    r = costwatch.analyse(m)
    assert r["findings"] == [] and "Nothing wasteful" in costwatch.message(r)


def test_the_report_is_saved_once_a_day():
    m = Memory(":memory:")
    r = costwatch.analyse(m)
    costwatch.save(m, r, [])
    costwatch.save(m, r, [])
    assert m.db.execute("SELECT COUNT(*) FROM jobs WHERE kind = 'cost_watch'").fetchone()[0] == 1


def test_a_reset_archives_the_old_costs_and_since_reset_shows_only_whats_new():
    from buddy import budgets, insights
    m = Memory(":memory:")
    now = datetime.now(timezone.utc)
    for i in range(300):  # before the fix: the briefing loop
        add(m, now - timedelta(hours=3) + timedelta(seconds=15 * i), "briefing", cost=0.006)
    add(m, now - timedelta(hours=2), "chat", cost=0.5, ok=0, stop="overloaded")
    said = budgets.start_fresh(m)
    assert "archived in Reports" in said
    arch = m.db.execute("SELECT title, report FROM jobs WHERE kind = 'cost_archive'").fetchone()
    assert arch["title"].startswith("Cost archive,") and "| briefing |" in arch["report"]
    add(m, datetime.now(timezone.utc) + timedelta(seconds=1), "chat", cost=0.02)
    add(m, datetime.now(timezone.utc) + timedelta(seconds=2), "free:make", cost=0.0, ok=0, stop="429 rate limited")
    r = costwatch.since_reset(m)
    assert r["total_usd"] == 0.02 and " – " in r["day"]
    ins = insights.report(m, since_reset=True)
    assert ins["since_reset"] and ins["totals"]["requests"] == 2
    assert ins["failing"][0]["purpose"] == "free:make" and "429" in ins["failing"][0]["reason"]
