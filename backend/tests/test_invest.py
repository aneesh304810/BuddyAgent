"""The Investments tab: strategy, market trends, Plaid investment accounts and the watchlist."""

from datetime import date

from buddy import invest
from buddy.markets import Markets
from buddy.memory import Memory
from buddy.plaid import Plaid


class NoPrices:
    def latest(self, market, symbols):
        return {s: 123.45 for s in symbols if market == "us"}


def test_dashboard_has_every_section_and_counts_money_moved_into_investments():
    memory = Memory(":memory:")
    Plaid(memory, env={})  # creates the Plaid tables
    year = date.today().isoformat()
    memory.db.executemany(
        "INSERT INTO fin_transfers (source, day, amount, currency, name, kind, recipient, account) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [("a", year, 500.0, "USD", "Transfer to Fidelity brokerage", "own", "Savings and investments", "1234"),
         ("b", year, 200.0, "USD", "Online transfer to savings", "own", "Savings and investments", "1234"),
         ("c", year, 80.0, "USD", "Zelle to Neha", "person", "Neha", "1234")])
    mk = Markets(memory, env={})
    mk.watch("NVDA")
    mk.data = NoPrices()
    out = invest.dashboard(memory, mk)
    assert set(out) == {"strategy", "settings", "watchlist", "trends", "accounts"}
    assert out["watchlist"] == [{"symbol": "NVDA", "market": "us", "note": "", "price": 123.45}]
    assert [m["name"] for m in out["accounts"]["moved_in"]] == ["Transfer to Fidelity brokerage"]  # savings isn't investing
    assert out["accounts"]["moved_in_year"] == 500.0
    assert "not financial advice" in out["strategy"]["note"]
    assert {t["kind"] for t in out["trends"]["topics"]} <= {"markets", "crypto", "india"}  # no AI topics here
    assert out["trends"]["topics"]  # the default market topics


def test_money_dashboard_no_longer_carries_trade_ideas():
    from buddy import review
    import inspect
    assert "markets" not in inspect.getsource(review.dashboard)
