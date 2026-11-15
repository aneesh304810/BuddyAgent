"""The Practice tab: paper trades with their reasons, the day across markets, and how crypto moves with them."""

import json
from datetime import date, timedelta

import pytest

from buddy import crossmarket, pricebook, recommend, simbook
from buddy.connectors.hub import ConnectorHub
from buddy.memory import Memory

DAY = date(2026, 11, 2)


@pytest.fixture
def memory(tmp_path):
    m = Memory(tmp_path / "buddy.db")
    ConnectorHub(m)
    yield m
    pricebook.close_all()


def series(closes, start=date(2026, 6, 1)):
    return [{"d": (start + timedelta(days=i)).isoformat(), "o": c, "h": c * 1.01, "l": c * 0.99, "c": c, "v": 1}
            for i, c in enumerate(closes)]


def wave(n=120, step=0.01, flip=1, lag=0):
    """A price that alternates up and down; flip -1 mirrors it, lag shifts it a day."""
    out, p = [], 100.0
    for i in range(n):
        p *= 1 + flip * step * (1 if ((i + lag) // 1) % 3 else -2)
        out.append(round(p, 4))
    return out


class Fake:
    def __init__(self, us=None, crypto=None, fail=()):
        self.us, self.crypto, self.fail = us or {}, crypto or {}, fail

    def bars(self, market, symbols, days=60):
        if market in self.fail:
            raise OSError("down")
        src = self.crypto if market == "crypto" else self.us
        return {s: src[s] for s in symbols if s in src}


def test_correlation_and_its_words():
    a = [0.01, -0.02, 0.03, -0.01, 0.02] * 4
    assert crossmarket.correlation(a, a) == 1.0
    assert crossmarket.correlation(a, [-x for x in a]) == -1.0
    assert crossmarket.correlation(a[:5], a[:5]) is None  # too few days
    assert crossmarket.correlation(a, [0.0] * 20) is None  # nothing moved
    assert crossmarket.strength(0.8) == "closely together"
    assert crossmarket.strength(-0.5) == "fairly closely in opposite directions"
    assert crossmarket.strength(0.05) == "barely linked"
    assert "not enough" in crossmarket.strength(None)


def test_markets_groups_the_day_and_reads_the_crypto_links(memory):
    data = Fake(us={"SPY": series(wave()), "QQQ": series(wave(step=0.012)), "GLD": series(wave(flip=-1)),
                    "XLK": series(wave())},
                crypto={"BTC/USD": series(wave(step=0.03)), "ETH/USD": series(wave(step=0.04))})
    out = crossmarket.markets(memory, data)
    titles = [g["title"] for g in out["groups"]]
    assert titles == ["Stock indexes", "Sectors", "Commodities (funds)", "Crypto"]
    btc = next(r for g in out["groups"] if g["title"] == "Crypto" for r in g["rows"] if r["symbol"] == "BTC")
    assert btc["day"] is not None and btc["week"] is not None and btc["month"] is not None
    spy = next(k for k in out["links"] if k["coin"] == "bitcoin" and k["with"].startswith("US stocks"))
    gold = next(k for k in out["links"] if k["coin"] == "bitcoin" and k["with"] == "gold")
    assert spy["same_day_30"] > 0.9 and "together" in spy["reading"]
    assert gold["same_day_30"] < -0.9 and "opposite" in gold["reading"]
    assert "bitcoin moved" in out["today"] and "caused" in out["note"]
    assert not any(k["with"] == "oil" for k in out["links"])  # no oil prices were read: no made-up link


def test_markets_is_cached_and_says_what_could_not_be_read(memory):
    out = crossmarket.markets(memory, Fake(us={"SPY": series(wave())}, fail=("crypto",)))
    assert out["problems"] == ["crypto prices couldn't be read"] and out["links"] == []
    assert json.loads(memory.get(crossmarket.CACHE_KEY))["value"]["groups"]
    assert crossmarket.markets(memory)["groups"] == out["groups"]  # served from the cache, no network
    nothing = crossmarket.markets(memory, Fake(fail=("us", "crypto")))
    assert nothing["groups"] == [] and len(nothing["problems"]) == 2


def sig(key, action="buy", price=100.0, reasons=None):
    return {"key": key, "category": "stocks", "action": action, "price": price,
            "reasons": reasons or [f"{key} is trending up"],
            "facts": {"parts": {"tone": 1, "trend": 1}, "lenses": {"price": "fair"}, "atr_pct": 0.02}}


def test_practice_shows_what_was_bought_and_sold_and_why(memory):
    recommend._ready(memory)
    for j in (sig("WIN", reasons=["people are talking about it 3 times more than usual", "price above its average"]),
              sig("HOLD"), sig("BAD", "sell", reasons=["insiders are selling"])):
        pricebook.run(memory, "INSERT INTO recs (day, category, key, action, reasons, facts, price_then) VALUES "
                              "(?, ?, ?, ?, ?, ?, ?)", (DAY.isoformat(), j["category"], j["key"], j["action"],
                                                        json.dumps(j["reasons"]), "{}", j["price"]))
        simbook.open_from(memory, [j], DAY, 500.0)
    start = DAY
    us = {"WIN": [{"d": (start + timedelta(days=1)).isoformat(), "o": 100, "h": 120, "l": 99, "c": 118, "v": 1}],
          "HOLD": [{"d": (start + timedelta(days=1)).isoformat(), "o": 100, "h": 103, "l": 99, "c": 102, "v": 1}],
          "BAD": [{"d": (start + timedelta(days=1)).isoformat(), "o": 100, "h": 101, "l": 99, "c": 100, "v": 1}],
          "SPY": [{"d": (start + timedelta(days=1)).isoformat(), "o": 500, "h": 506, "l": 499, "c": 505, "v": 1}]}
    data = Fake(us=us)
    assert simbook.update(memory, start + timedelta(days=2), data) == 1
    out = crossmarket.practice(memory, data, today=start + timedelta(days=2))
    won = out["closed"][0]
    assert won["symbol"] == "WIN" and won["won"] and won["why_closed"] == "reached the target"
    assert won["because"][0].startswith("people are talking") and won["r"] == 2.0 and won["spy_change"] == 0.01
    held = next(t for t in out["open"] if t["symbol"] == "HOLD")
    assert held["price"] == 102 and held["change"] == 0.02 and held["profit"] > 0 and held["days_held"] == 2
    avoid = next(t for t in out["open"] if t["symbol"] == "BAD")
    assert avoid["kind"] == "avoid" and avoid["because"] == ["insiders are selling"] and avoid["profit"] is None
    assert out["money"]["value_now"] == round(out["money"]["equity"] + held["profit"], 2)
    days = {d["day"]: d["events"] for d in out["log"]}
    assert {e["what"] for e in days[DAY.isoformat()]} == {"Bought", "Marked to avoid"}
    sold = days[(start + timedelta(days=1)).isoformat()][0]
    assert sold["what"] == "Sold" and "reached the target" in sold["why"]
    assert "crypto" in out["limits"] and set(out["weights"]) >= {"tone", "trend"}


def test_a_trade_shows_what_it_used_by_source():
    t = {"parts": json.dumps({"tone": 1, "trend": 1, "insiders": -1}), "read": "cheap",
         "features": json.dumps({"mentions": 40, "times_usual": 3, "tone": 0.4, "rsi": 62, "atr_pct": 0.02,
                                 "business": 1, "f_good_news_14": 3, "f_bad_news_14": 1, "f_evidence_90": 12,
                                 "f_evidence_kinds": 4, "f_evidence_growth": 0.5, "f_patterns_30": 2, "f_links_90": 1})}
    got = {u["source"]: u for u in crossmarket.used(t)}
    assert list(got) == ["Reddit", "Price", "Company facts", "News", "Intelligence"]
    assert "40 posts" in got["Reddit"]["says"] and "3 times its usual" in got["Reddit"]["says"]
    assert got["Reddit"]["way"] == "up" and got["Price"]["way"] == "up"
    assert got["Company facts"]["way"] == "none"  # insiders pointed down, the business read pointed up
    assert got["News"]["says"].startswith("3 good and 1 bad") and got["News"]["way"] == "up"
    assert "12 records" in got["Intelligence"]["says"] and "relationships" in got["Intelligence"]["says"]
    assert crossmarket.used({"parts": "{}", "features": "{}"}) == []  # nothing kept, nothing claimed


def test_practice_names_its_strategies_and_what_it_is_learning(memory):
    simbook.open_from(memory, [sig("NEW")], DAY, 500.0)
    out = crossmarket.practice(memory, Fake(), today=DAY)
    assert out["open"][0]["strategy"] == "Signals"
    assert [u["source"] for u in out["open"][0]["used"]][:2] == ["Reddit", "Price"]
    s = out["strategies"]
    assert [x["key"] for x in s["following"]] == ["signals", "themes"]
    assert s["following"][0]["record"] == "No closed trades yet" and len(s["following"][0]["inputs"]) == 4
    assert "count equally" in s["learning"][0]
    assert {x["lean"] for x in s["testing"]} == {"none"} and len(s["testing"]) == 7
    memory.set(simbook.WEIGHTS_KEY, json.dumps({"on": "2026-01-01", "parts": {"tone": {"weight": 1.3, "trades": 12, "won": 9}}}))
    learned = crossmarket.practice(memory, Fake(), today=DAY)["strategies"]["learning"]
    assert learned[0].startswith("What people say on Reddit now counts 1.30 times")


def test_a_trade_without_a_kept_signal_still_says_why(memory):
    simbook.open_from(memory, [sig("OLD")], DAY, 500.0)
    out = crossmarket.practice(memory, Fake(), today=DAY)
    assert out["open"][0]["because"] == ["what people were saying pointed up", "the price trend pointed up"]
    assert out["open"][0]["price"] is None  # no price could be read: shown as unknown, not as zero


def test_dashboard_survives_a_broken_part(memory, monkeypatch):
    monkeypatch.setattr(crossmarket, "markets", lambda *a, **k: 1 / 0)
    out = crossmarket.dashboard(memory, Fake())
    assert "error" in out["markets"] and out["practice"]["open"] == []


def test_the_page_endpoint_needs_the_owner_and_answers(memory, monkeypatch):
    from fastapi.testclient import TestClient

    from buddy import server
    monkeypatch.setattr(server, "memory", memory)
    monkeypatch.setattr(crossmarket, "markets", lambda *a, **k: {"groups": [], "links": []})
    monkeypatch.setattr(server, "authorized", lambda t: t == "s3cret")
    client = TestClient(server.app)
    assert client.get("/api/invest/practice").status_code == 401
    out = client.get("/api/invest/practice", headers={"Authorization": "Bearer s3cret"}).json()
    assert out["practice"]["open"] == [] and out["markets"]["groups"] == []
