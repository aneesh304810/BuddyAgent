"""The price store: daily prices are kept, only what is missing is downloaded, and a re-stated history is replaced
and written down."""

from datetime import date, timedelta

import pytest

from buddy import pricebook, pricestore
from buddy.connectors.hub import ConnectorHub
from buddy.memory import Memory

TODAY = date(2026, 11, 17)


@pytest.fixture
def memory(tmp_path):
    m = Memory(tmp_path / "buddy.db")
    ConnectorHub(m)
    yield m
    pricebook.close_all()


def series(n: int, start: float = 100.0, end: date = TODAY, scale: float = 1.0) -> list[dict]:
    """n consecutive days ending on `end`, the close rising by 1 a day."""
    out = []
    for i in range(n):
        c = (start + i) * scale
        out.append({"d": (end - timedelta(days=n - 1 - i)).isoformat(), "o": c - 1, "h": c + 1, "l": c - 2, "c": c, "v": 1000 + i})
    return out


class Provider:
    """Stands in for the price provider: remembers what it was asked for and answers with the last `days` of each."""
    source_name = "test"

    def __init__(self, history: dict[str, list[dict]]):
        self.history, self.asked = history, []

    def bars(self, market, symbols, days=420):
        self.asked.append((tuple(symbols), days))
        return {s: self.history[s][-days:] for s in symbols if s in self.history}


def test_prices_are_kept_and_the_next_time_only_the_recent_days_are_asked_for(memory):
    provider = Provider({"AAA": series(300), "SPY": series(300, 400.0)})
    first = pricestore.fetch(memory, provider, ["SPY", "AAA"], days=250, today=TODAY)
    assert provider.asked == [(("AAA", "SPY"), 250)]                              # nothing held: the whole window
    assert len(first["AAA"]) == 250 and first["AAA"][-1] == {"d": TODAY.isoformat(), "o": 398.0, "h": 400.0, "l": 397.0, "c": 399.0, "v": 1299.0}
    assert pricestore.held(memory, ["AAA"], "test")["AAA"] == {"first": first["AAA"][0]["d"], "last": TODAY.isoformat(), "days": 250}
    later = TODAY + timedelta(days=3)
    provider.history = {"AAA": series(303, end=later), "SPY": series(303, 400.0, end=later)}
    again = pricestore.fetch(memory, provider, ["AAA", "SPY"], days=250, today=later)
    assert provider.asked[-1] == (("AAA", "SPY"), 13)                             # three days on: 3 plus the overlap
    assert again["AAA"][-1]["d"] == later.isoformat() and again["AAA"][-1]["c"] == 402.0
    assert len(again["AAA"]) == 251 and again["AAA"][-4]["c"] == 399.0            # the kept days are still there
    got = pricestore.status(memory)
    assert got["symbols"] == 2 and got["prices"] == 506 and got["last"] == later.isoformat() and got["restated"] == 0
    assert got["sources"][0]["source"] == "test"


def test_a_new_symbol_gets_its_whole_window_and_the_others_only_their_recent_days(memory):
    provider = Provider({"AAA": series(300)})
    pricestore.fetch(memory, provider, ["AAA"], days=250, today=TODAY)
    provider.history["BBB"] = series(300, 50.0)
    got = pricestore.fetch(memory, provider, ["AAA", "BBB"], days=250, today=TODAY)
    assert provider.asked[1:] == [(("AAA",), 10), (("BBB",), 250)]
    assert len(got["BBB"]) == 250 and len(got["AAA"]) == 250
    assert "ZZZ" not in pricestore.fetch(memory, provider, ["AAA", "ZZZ"], days=250, today=TODAY)   # the provider has none


def test_a_restated_history_is_fetched_again_replaced_and_written_down(memory):
    provider = Provider({"AAA": series(300), "SPY": series(300, 400.0)})
    pricestore.fetch(memory, provider, ["AAA", "SPY"], days=250, today=TODAY)
    later = TODAY + timedelta(days=2)
    provider.history = {"AAA": series(302, end=later, scale=0.5), "SPY": series(302, 400.0, end=later)}   # a 2-for-1 split
    got = pricestore.fetch(memory, provider, ["AAA", "SPY"], days=250, today=later)
    assert provider.asked[-2:] == [(("AAA", "SPY"), 12), (("AAA",), 250)]         # noticed in the overlap, then all of AAA
    assert got["AAA"][0]["c"] < 150 and got["AAA"][-1]["c"] == 200.5 and len(got["AAA"]) == 250   # every kept day is the new series
    assert len(got["SPY"]) == 251 and got["SPY"][0]["c"] > 400                    # SPY was not touched
    changes = pricebook.run(memory, "SELECT symbol, source, was, now FROM price_revisions")
    assert len(changes) == 1 and changes[0]["symbol"] == "AAA" and changes[0]["now"] == changes[0]["was"] * 0.5
    status = pricestore.status(memory)
    assert status["restated"] == 1 and status["restated_symbols"] == 1


def test_the_newest_day_held_may_change_without_counting_as_a_restatement(memory):
    provider = Provider({"AAA": series(300)})
    pricestore.fetch(memory, provider, ["AAA"], days=250, today=TODAY)
    changed = series(300)
    changed[-1] = {**changed[-1], "c": 410.0}                                     # fetched before the close; now the final price
    provider.history = {"AAA": changed}
    got = pricestore.fetch(memory, provider, ["AAA"], days=250, today=TODAY)
    assert got["AAA"][-1]["c"] == 410.0 and pricestore.status(memory)["restated"] == 0
    assert provider.asked[-1] == (("AAA",), 10)


def test_prices_from_two_sources_are_kept_apart_and_a_broken_store_falls_back_to_the_provider(memory, monkeypatch):
    one, two = Provider({"AAA": series(300)}), Provider({"AAA": series(300, 500.0)})
    two.source_name = "other"
    pricestore.fetch(memory, one, ["AAA"], days=250, today=TODAY)
    got = pricestore.fetch(memory, two, ["AAA"], days=250, today=TODAY)
    assert two.asked == [(("AAA",), 250)] and got["AAA"][-1]["c"] == 799.0        # nothing of the first source was used
    assert pricestore.load(memory, ["AAA"], "test", 250)["AAA"][-1]["c"] == 399.0
    assert {s["source"] for s in pricestore.status(memory)["sources"]} == {"test", "other"}
    monkeypatch.setattr(pricestore, "held", lambda *a: (_ for _ in ()).throw(RuntimeError("store down")))
    direct = pricestore.fetch(memory, one, ["AAA"], days=250, today=TODAY)
    assert direct["AAA"][-1]["c"] == 399.0 and one.asked[-1] == (("AAA",), 250)


class NoName:
    alpaca = ("id", "secret")


def test_the_source_is_named_after_where_the_prices_come_from():
    assert pricestore.source_of(NoName()) == "alpaca" and pricestore.source_of(object()) == "other"
    assert pricestore.source_of(Provider({})) == "test"
