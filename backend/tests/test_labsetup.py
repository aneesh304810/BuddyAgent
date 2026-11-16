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
