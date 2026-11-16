"""Loading the trading engine's data: every step runs, one failing does not stop the rest, and the panel shows
what is actually held."""

import pytest

from buddy import labsetup, pricebook, stratlab
from buddy.connectors.hub import ConnectorHub
from buddy.memory import Memory


@pytest.fixture
def memory(tmp_path):
    m = Memory(tmp_path / "buddy.db")
    ConnectorHub(m)
    yield m
    pricebook.close_all()


def test_every_step_runs_and_a_failure_is_kept_in_its_own_words(memory):
    ran = []

    def good(m):
        ran.append("good")
        return "3 added"

    def bad(m):
        ran.append("bad")
        raise RuntimeError("HTTP 403: Forbidden")

    def skip(m):
        ran.append("skip")
        return "skipped: no key is set"

    state = labsetup.load_all(memory, [("picks", "Recommended watchlist", good), ("factors", "Factor returns", bad),
                                       ("sentiment", "News sentiment", skip), ("lab", "Today's lab trades", good)])
    assert ran == ["good", "bad", "skip", "good"] and state["running"] == "" and state["finished"]
    assert state["factors"]["ok"] is False and state["factors"]["said"] == "RuntimeError: HTTP 403: Forbidden"
    assert state["sentiment"]["skipped"] and not state["sentiment"]["ok"] and state["picks"]["ok"]
    rows = {r["key"]: r for r in labsetup.status(memory)["rows"]}
    assert rows["factors"]["state"] == "failed" and rows["sentiment"]["state"] == "skipped"
    assert rows["picks"]["state"] == "empty"   # the step said it worked, but nothing is held: shown as empty
    assert len(rows) == 8 and rows["replay"]["state"] == "empty" and rows["replay"]["last"] == ""


def test_the_panel_reads_what_is_held_not_what_a_step_claimed(memory):
    stratlab._ready(memory)
    pricebook.run(memory, "INSERT INTO lab_trades (strategy, symbol, opened, entry, stop, target, shares, hold_days, why, "
                          "features, spy_entry, closed, exit, ret, spy_ret, why_closed, status, backfilled) VALUES "
                          "('trend', 'AAPL', '2024-01-02', 100, 95, 110, 10, 28, 'x', '{}', 400, '2024-01-20', 110, 0.1, 0.02, "
                          "'reached the target', 'closed', TRUE)")
    got = labsetup.status(memory)
    replay = next(r for r in got["rows"] if r["key"] == "replay")
    assert replay["loaded"] and replay["state"] == "loaded" and "1 past trades, 2024-01-02 to 2024-01-20" in replay["holds"]
    assert got["sandbox"] is False and "sandbox.sh" in got["sandbox_note"]
    assert "judged trades" in next(r for r in got["rows"] if r["key"] == "model")["holds"]


def test_a_load_cut_off_by_a_restart_does_not_leave_the_button_stuck(memory):
    import json
    from datetime import timedelta
    from buddy.tools import local_now
    memory.set(labsetup.STATE_KEY, json.dumps({"running": "Replay over past prices",
                                               "progress_at": (local_now() - timedelta(minutes=3)).isoformat(timespec="seconds")}))
    assert labsetup.status(memory)["running"] == "Replay over past prices"
    assert labsetup.load_in_background(memory) == "It is already loading."
    memory.set(labsetup.STATE_KEY, json.dumps({"running": "Replay over past prices",
                                               "progress_at": (local_now() - timedelta(minutes=40)).isoformat(timespec="seconds")}))
    got = labsetup.status(memory)
    assert got["running"] == "" and got["cut_off"] is True   # too long without progress: it was cut off
    memory.set(labsetup.STATE_KEY, json.dumps({"running": "Replay over past prices"}))  # an older record with no time
    assert labsetup.status(memory)["running"] == ""


def test_loading_also_queues_the_open_source_starting_points(memory, monkeypatch):
    from buddy import researchdesk
    asked = []
    monkeypatch.setattr(researchdesk, "arxiv_new", lambda m: 2)

    def add(m, url):
        asked.append(url)
        if "06_strategy" in url:
            raise RuntimeError("HTTP 403: rate limit")
        return {"added": 3, "licence": "MIT", "looked_at": 5}

    monkeypatch.setattr(researchdesk, "github_add", add)
    said = labsetup._papers(memory)
    assert len(asked) == 4 and said.startswith("2 new arXiv papers; 9 notebooks and files from 3 open-source folders")
    assert "1 folders failed (RuntimeError: HTTP 403: rate limit)" in said
