"""The journal (append only), the committee, the paper portfolio and the investor library."""

import json
from datetime import datetime, timezone

import pytest

from buddy import investors as library
from buddy import journal, paper, pricebook, recommend
from buddy.connectors.hub import ConnectorHub
from buddy.memory import Memory

T0 = datetime(2026, 9, 1, tzinfo=timezone.utc)


@pytest.fixture
def memory(tmp_path, monkeypatch):
    monkeypatch.delenv("BUDDY_FINNHUB_KEY", raising=False)
    m = Memory(tmp_path / "buddy.db")
    ConnectorHub(m)
    yield m
    pricebook.close_all()


class FakeData:
    def bars(self, market, symbols, days=400):
        return {s: [{"d": f"d{i:03d}", "o": 100 + i * 0.2, "h": 101 + i * 0.2, "l": 99 + i * 0.2,
                     "c": 100 + i * 0.2 - (3 if i % 4 == 0 else 0), "v": 1} for i in range(260)] for s in symbols}

    def latest(self, market, symbols):
        return {}


# --- the journal ----------------------------------------------------------------------------------------------
def test_a_thesis_needs_what_would_prove_it_wrong_and_is_never_rewritten(memory):
    with pytest.raises(Exception, match="prove it wrong"):
        journal.thesis(memory, "MSFT", "Cloud keeps growing", "Azure +25% a year", "")
    journal.thesis(memory, "msft", "Cloud keeps growing", "Azure +25% a year", "Azure growth under 15% for two "
                   "quarters", 400.0, {"price": "fair", "business": "strong"}, T0)
    journal.note(memory, "MSFT", "Q1: Azure +27%", 420.0)
    got = journal.review(memory, "MSFT", 440.0, {"price": "expensive", "business": "strong"},
                         "Expectation met; price ran ahead of it")
    t = got["theses"][0]
    assert t["price_then"] == 400.0 and t["change"] == 0.1 and t["checklists_changed"]
    assert t["would_prove_it_wrong"].startswith("Azure growth under 15%")
    kinds = [e["kind"] for e in journal.entries(memory, "MSFT")]
    assert kinds == ["thesis", "note", "review"]
    assert journal.entries(memory, "MSFT")[0]["text"] == "Cloud keeps growing"     # as written
    assert not [n for n in dir(journal) if n.startswith(("update", "delete", "edit"))]  # no way to rewrite
    with pytest.raises(Exception, match="no thesis"):
        journal.review(memory, "NVDA", 100.0)


def test_the_committee_gathers_what_there_is_and_says_what_is_missing(memory, monkeypatch):
    from buddy import lenses
    monkeypatch.setattr(lenses, "checklists", lambda memory, s, price, above, crowd, env=None, fetch=None: {
        "company": "Microsoft Corp", "read": {"price": "expensive", "business": "strong"}, "lenses": []})
    journal.thesis(memory, "MSFT", "Cloud keeps growing", "x", "growth stalls", 400.0, None, T0)
    got = journal.committee(memory, "MSFT", FakeData())
    assert got["price"]["price"] > 100 and got["checklists"]["read"]["price"] == "expensive"
    assert got["journal"][0]["would_prove_it_wrong"] == "growth stalls"
    assert got["funds"]["held_by"] == [] and any(m.startswith("finnhub: no key") for m in got["missing"])
    who = {x["investor"] for x in got["lessons_to_apply"]}
    assert who & {"Benjamin Graham", "Peter Lynch", "Howard Marks", "Seth Klarman"}  # the 'expensive' lessons
    assert "strongest case AGAINST" in got["how_to_answer"] and "not quotations" in got["how_to_answer"]


# --- the library ----------------------------------------------------------------------------------------------
def test_the_library_matches_the_situation_and_is_honest_about_itself():
    assert len(library.PRINCIPLES) >= 20 and not any(p["checked"] for p in library.PRINCIPLES)
    assert all(p["principle"] and p["question"].endswith("?") and '"' not in p["principle"]
               for p in library.PRINCIPLES)                                  # ideas, never quotations
    tags = library.situation({"price": "expensive", "business": "strong"}, {"times_usual": 6, "rsi": 80})
    assert tags == {"expensive", "strong", "crowd", "stretched"}
    got = library.lessons(tags, 3)
    assert got[0]["investor"] == "Howard Marks"                              # three tags matched: most specific
    assert len({p["investor"] for p in got}) == 3                            # three points of view
    assert library.lessons(library.situation({"price": "cheap", "business": "strong"}), 5)[0]["key"] in (
        "good_and_cheap", "cyclical_trap", "scuttlebutt")
    assert library.lessons(set()) == [] and library.lessons(set(), skip_always=False)
    assert library.lessons({"unproven"})[0]["investor"] == "Nassim Nicholas Taleb"
    assert [p["investor"] for p in library.search("lynch")] == ["Peter Lynch"] * 3
    assert library.search("margin of safety")[0]["investor"] in ("Benjamin Graham", "Seth Klarman")
    assert "not yet checked" in library.NOTE


def test_a_signal_carries_the_lesson_for_its_situation(memory):
    bars = FakeData().bars("us", ["NVDA"])["NVDA"]
    hot = {"key": "NVDA", "category": "stocks", "mentions": 8, "times_usual": 8.0, "tone": 0.6}
    rich = {"price": "expensive", "business": "strong", "line": "the price looks expensive"}
    j = recommend.judge(hot, bars, {"insiders": 40.0, "lenses": rich})
    assert j["action"] == "caution"
    assert j["reasons"][-1].startswith("a question from Howard Marks (The Most Important Thing, 2011): ")


# --- the paper portfolio --------------------------------------------------------------------------------------
def test_paper_trades_positions_and_the_index_comparison(memory):
    prices = {"MSFT": 400.0, "SPY": 500.0, "KO": 60.0}
    quote = lambda s: prices[s]  # noqa: E731
    assert paper.show(memory, quote)["cash"] == 100_000
    got = paper.trade(memory, "buy", "msft", 50, quote=quote)
    assert got["value"] == 20_000 and got["cash_left"] == 80_000 and "no real order" in got["note"]
    paper.trade(memory, "buy", "KO", amount=6_000, quote=quote)
    with pytest.raises(Exception, match="in cash"):
        paper.trade(memory, "buy", "MSFT", 1000, quote=quote)
    with pytest.raises(Exception, match="holds 50 shares"):
        paper.trade(memory, "sell", "MSFT", 60, quote=quote)
    with pytest.raises(Exception, match="doesn't hold"):
        paper.trade(memory, "sell", "NVDA", 1, price=10.0)
    prices.update({"MSFT": 440.0, "SPY": 525.0})
    paper.trade(memory, "sell", "MSFT", 20, quote=quote)                    # realised: 20 x $40
    s = paper.show(memory, quote)
    msft = next(p for p in s["positions"] if p["symbol"] == "MSFT")
    assert msft["shares"] == 30 and msft["average_cost"] == 400.0 and msft["gain_pct"] == 0.1
    assert s["realised_gain"] == 800.0 and s["total"] == 102_000.0 and s["return_pct"] == 0.02
    assert s["same_money_in_spy_pct"] == 0.05                               # the index did better: shown plainly
    paper.trade(memory, "sell", "KO", quote=quote)                          # no size: the whole position
    assert [p["symbol"] for p in paper.show(memory, quote)["positions"]] == ["MSFT"]
    with pytest.raises(Exception, match="confirm"):
        paper.reset(memory)
    paper.reset(memory, True)
    assert paper.show(memory, quote)["cash"] == 100_000 and json.loads(paper.run_tool(memory, {"action": "trades"})) == []
