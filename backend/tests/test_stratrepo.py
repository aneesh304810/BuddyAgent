"""Strategy repository: the catalogue of collected strategies by family, the scorecard worked out from Buddy's own
trades, the pass/fail gate, and discovery from a public directory of strategy papers."""

from datetime import date, timedelta

import pytest

from buddy import pricebook, researchdesk, stratlab, stratrepo
from buddy.connectors.hub import ConnectorHub
from buddy.memory import Memory


@pytest.fixture
def memory(tmp_path):
    m = Memory(tmp_path / "buddy.db")
    ConnectorHub(m)
    yield m
    pricebook.close_all()


def trades(memory, strategy, rets, market=0.0, backfilled=True, start=date(2022, 1, 3), every=14, regime="up"):
    stratlab._ready(memory)
    for i, ret in enumerate(rets):
        opened = start + timedelta(days=i * every)
        pricebook.run(memory, "INSERT INTO lab_trades (strategy, symbol, opened, entry, stop, target, shares, hold_days, why, "
                              "features, spy_entry, status, closed, exit, ret, spy_ret, backfilled, regime) VALUES "
                              "(?, 'AAA', ?, 100, 93, 114, 10, 28, 'x', '{}', 400, 'closed', ?, 100, ?, ?, ?, ?)",
                      (strategy, opened.isoformat(), (opened + timedelta(days=10)).isoformat(), ret, market, backfilled, regime))


def test_the_scorecard_is_worked_out_from_the_trades_after_costs():
    assert stratrepo.scorecard([], 1000, 0.002) == {}
    rows = [{"opened": "2024-01-02", "closed": "2024-01-12", "ret": 0.102, "spy_ret": 0.01},
            {"opened": "2024-01-05", "closed": "2024-01-20", "ret": -0.048, "spy_ret": 0.0},   # two held at once
            {"opened": "2024-06-03", "closed": "2024-06-13", "ret": 0.052, "spy_ret": 0.02},
            {"opened": "2024-12-20", "closed": "2025-01-01", "ret": 0.002, "spy_ret": 0.0}]
    got = stratrepo.scorecard(rows, 1000, 0.002)
    assert got["trades"] == 4 and got["won"] == 2 and got["book"] == 2000      # the stake times the most held at once
    assert got["cost_paid"] == 8.0 and got["from"] == "2024-01-02" and got["to"] == "2025-01-01"
    assert got["years"] == 1.0 and got["yearly_return"] == pytest.approx(0.05, abs=0.001)   # +100 -50 +50 +0 on 2,000
    assert got["worst_fall"] == pytest.approx(50 / 2100, abs=0.0001)           # from 2,100 down to 2,050
    assert got["average_vs_market"] == pytest.approx((0.09 - 0.05 + 0.03 + 0.0) / 4, abs=0.0001)
    assert got["trades_a_year"] == 4.0 and got["days_held"] == pytest.approx(11.8, abs=0.1) and got["sharpe"] > 0


def test_a_rule_passes_only_when_it_beats_the_market_keeps_doing_so_and_does_not_fall_too_far(memory):
    assert stratrepo.gate({}, {})[0] == "not enough history"
    trades(memory, "trend", [0.04, 0.03, -0.01, 0.05] * 10, market=0.005)                  # steady, ahead of the market
    trades(memory, "dip", [0.03] * 28 + [-0.05] * 12, market=0.0)                          # good early, bad lately
    trades(memory, "momentum", [0.02] * 12)                                                 # too few to say
    trades(memory, "trend", [0.01] * 3, backfilled=False, start=date(2026, 10, 1), every=3)  # a few live trades
    got = stratrepo.view(memory)
    by = {s["key"]: s for s in got["strategies"]}
    assert by["trend"]["verdict"] == "passed" and by["trend"]["stage"] == "Passed on past prices; paper trading"
    assert by["trend"]["history"]["trades"] == 40 and by["trend"]["live"]["trades"] == 3 and by["trend"]["latest"] > 0
    assert by["dip"]["verdict"] == "failed" and "did not beat it in the latest stretch" in by["dip"]["reasons"]
    assert by["momentum"]["verdict"] == "not enough history" and "12 of 30" in by["momentum"]["reasons"][0]
    assert got["strategies"][0]["key"] == "trend"                                           # what passed comes first
    counts = got["counts"]
    assert counts["passed"] == 1 and counts["failed"] == 1 and counts["proven"] == 0
    assert counts["collected"] == len(got["strategies"]) and counts["trading"] == 6 and counts["waiting"] >= 14


def test_every_rule_is_filed_under_a_family_and_keeps_its_source(memory):
    got = stratrepo.view(memory)
    by = {s["key"]: s for s in got["strategies"]}
    assert by["u_dual_momentum"]["family"] == "dual_momentum" and by["u_earnings_drift"]["family"] == "pead"
    assert by["u_low_beta_low_lottery"]["family"] == "bab" and by["sectors"]["family"] == "sectors"
    assert by["u_faber_10_month_average"]["stage"] == "Collected: waiting for your approval"
    assert "Faber" in by["u_faber_10_month_average"]["source"]
    assert "no history" in by["u_earnings_drift"]["reasons"][0]            # company figures cannot be replayed
    families = {f["key"]: f for f in got["families"]}
    wanted = {"dual_momentum", "high_52", "sma_10", "quality", "value", "low_vol", "bab", "qmj", "sectors", "pead"}
    assert wanted <= set(families) and all(families[k]["rules"] >= 1 for k in wanted)
    assert all(s["family"] != "other" for s in got["strategies"])
    assert stratrepo.family_of("mine", {"buy": [{"field": "pb", "op": "<", "value": 1}], "rank": None}) == "value"
    assert stratrepo.family_of("mine", {"buy": [{"field": "atr_pct", "op": "<", "value": 1}], "rank": None}) == "other"
    assert any("never run" in p for p in got["policy"]) and any("vectorbt" == r["title"] for r in got["references"])


LIST = """# Strategy research papers: how to recreate

Returns are as reported by the authors.

## Momentum

**[Momentum Without Crashes](https://arxiv.org/abs/2501.01234)** (2025)
🔧 Requires daily prices; rank on 12-month return skipping the last month.
📈 Reported 14% a year.
⚠️ Title/abstract only; not independently verified in this pass.

**[A Journal Paper On Momentum](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=1)** (2024)
🔧 Requires monthly returns.

**[Residual Momentum Revisited](https://arxiv.org/pdf/2402.05555v2)** (2024)
🔧 Requires daily prices and factor returns.

## Intraday & overnight

**[Overnight Drift](https://arxiv.org/abs/2403.00001)** (2024)
🔧 Requires minute bars.

## Value & quality

**[Momentum Without Crashes](https://arxiv.org/abs/2501.01234)** (2025)
🔧 Listed twice.
"""


def test_discovery_queues_only_arxiv_papers_that_daily_data_can_test(memory):
    found = stratrepo.parse_papers(LIST)
    assert [p["url"] for p in found] == ["https://arxiv.org/abs/2501.01234", "https://arxiv.org/abs/2402.05555",
                                         "https://arxiv.org/abs/2501.01234"]
    assert "rank on 12-month return" in found[0]["summary"] and "not independently verified" in found[0]["summary"]
    assert "14%" not in found[0]["summary"]                       # a published return is never kept
    asked = []

    def fetch(url, data=None, headers=None):
        asked.append(url)
        return LIST.encode()

    got = stratrepo.discover(memory, fetch)
    assert got == {"found": 3, "added": 2}                        # the paper listed twice is queued once
    assert len(asked) == 1 and "WallStreetPete/awesome-open-source-trading/contents/docs/papers.md" in asked[0]
    queue = researchdesk.queue(memory)
    assert {q["url"] for q in queue} == {"https://arxiv.org/abs/2501.01234", "https://arxiv.org/abs/2402.05555"}
    assert all(q["source"] == "arXiv" and "CC0" in q["licence"] for q in queue)
    assert stratrepo.discover(memory, fetch)["added"] == 0        # nothing is queued twice
    view = stratrepo.view(memory)
    assert view["counts"]["queued"] == 2 and "3 papers in the list, 0 new" in view["last_discovery"]
