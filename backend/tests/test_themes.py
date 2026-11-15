"""Themes: evidence patterns joined to public companies, tested with practice baskets against the market."""

import json
from datetime import date, timedelta

import pytest

from buddy import bip, pricebook, themes
from buddy.connectors.hub import ConnectorHub
from buddy.memory import Memory

TODAY = date(2026, 11, 16)


@pytest.fixture
def memory(tmp_path):
    m = Memory(tmp_path / "buddy.db")
    ConnectorHub(m)
    yield m
    pricebook.close_all()


def d(days_ago, today=TODAY):
    return (today - timedelta(days=days_ago)).isoformat()


def add(memory, kind, days_ago, who="", amount=None, tech="smr", n=[0]):
    n[0] += 1
    bip._ready(memory)
    bip._run(memory, "INSERT INTO bip_signals (key, kind, source, title, url, day, who, amount, techs, sectors, seen) "
                     "VALUES (?, ?, 'test', ?, 'https://example.org/x', ?, ?, ?, ?, ?, ?)",
             (f"k{n[0]}", kind, f"{kind} record {n[0]}", d(days_ago), who, amount, json.dumps([tech]),
              json.dumps([bip.TECHS[tech]["sector"]]), d(0)))


class Prices:
    """Daily closes: {symbol: {iso day: close}}; a symbol with one number has that close every day."""

    def __init__(self, closes, start=date(2026, 6, 1), end=TODAY):  # end: the last day prices exist for
        self.closes, self.start, self.end, self.asked = closes, start, end, []

    def bars(self, market, symbols, days=420):
        self.asked.append(sorted(symbols))
        out = {}
        for s in symbols:
            c = self.closes.get(s)
            if c is None:
                continue
            day, rows, last = self.start, [], None
            while day <= self.end:
                last = c.get(day.isoformat(), last) if isinstance(c, dict) else c
                if last is not None:
                    rows.append({"d": day.isoformat(), "c": last})
                day += timedelta(days=1)
            out[s] = rows
        return out

    def on(self, day):
        self.end = day
        return self


def filings(memory, now=4, before=1):
    for i in range(now):
        add(memory, "filing", 5 + i, "Oklo Inc.  (OKLO)" if i % 2 else "NuScale Power Corp  (SMR)")
    for i in range(before):
        add(memory, "filing", 40 + i, "Oklo Inc.  (OKLO)")


def test_tickers_come_from_the_filings_that_name_them():
    assert themes.ticker("Oklo Inc.  (OKLO)") == "OKLO" and themes.ticker("Berkshire (BRK-B, BRK-A)") == "BRK-B"
    assert themes.ticker("Private Partners LLC") == "" and themes.ticker("Acme (formerly Beta)") == ""
    rows = [{"kind": "filing", "who": "Oklo Inc.  (OKLO)", "techs": ["smr"]}] * 2 + [
        {"kind": "filing", "who": "NuScale Power Corp  (SMR)", "techs": ["smr"]},
        {"kind": "award", "who": "Other Co (OTHR)", "techs": ["smr"]},
        {"kind": "filing", "who": "Elsewhere (ELSE)", "techs": ["fusion"]}]
    assert [(t["symbol"], t["records"]) for t in themes.tickers(rows, "smr")] == [("OKLO", 2), ("SMR", 1)]


def test_each_rule_fires_only_on_its_own_change():
    row = lambda kind, ago, amount=None: {"kind": kind, "day": d(ago), "amount": amount, "who": "DOE lab"}  # noqa: E731
    assert themes.fired([row("filing", 3)] * 2 + [row("filing", 40)], TODAY) == []  # two is not a jump
    got = themes.fired([row("filing", 3)] * 4 + [row("filing", 40)] * 2, TODAY)
    assert [f["pattern"] for f in got] == ["filings"] and "4 in the last 30 days against 2" in got[0]["why"]
    assert themes.fired([row("filing", 3)] * 4 + [row("filing", 40)] * 3, TODAY) == []  # not double
    assert [f["pattern"] for f in themes.fired([row("news", 2)] * 3 + [row("discussion", 5)] * 2, TODAY)] == ["news"]
    assert [f["pattern"] for f in themes.fired([row("research", 9)] * 5, TODAY)] == ["research"]
    assert [f["pattern"] for f in themes.fired([row("grant", 40, 25e6), row("award", 40, 2e6)], TODAY)] == ["award"]
    assert themes.fired([row("award", 9, 25e6)], TODAY) == []   # dated 9 days ago: not taken as public yet
    assert themes.fired([row("award", 75, 25e6)], TODAY) == []  # too long ago
    assert [f["pattern"] for f in themes.fired([row("funding", 9)] * 3, TODAY)] == ["funding"]
    assert set(themes.PATTERNS) == {"forming", "filings", "news", "good_news", "bad_news", "research", "award", "funding"}


def test_good_and_bad_news_are_separate_patterns():
    row = lambda tone, ago: {"kind": "news", "day": d(ago), "amount": None, "who": "", "tone": tone}  # noqa: E731
    bad = themes.fired([row("bad", 2)] * 3 + [row("good", 3)] + [row("bad", 20)], TODAY)
    assert [f["pattern"] for f in bad] == ["bad_news"] and "3 reports labelled bad" in bad[0]["why"]
    assert [f["pattern"] for f in themes.fired([row("good", 2)] * 3, TODAY)] == ["good_news"]
    mixed = themes.fired([row("good", 2)] * 3 + [row("bad", 2)] * 3, TODAY)
    assert [f["pattern"] for f in mixed] == ["news"]  # a jump in volume, but neither good nor bad leads
    assert themes.fired([row("", 2)] * 4, TODAY) == []  # unlabelled headlines are in neither
    both = themes.fired([row("good", 2)] * 5, TODAY)
    assert [f["pattern"] for f in both] == ["news", "good_news"]


def test_headlines_are_labelled_in_batches_and_a_bad_answer_is_left_for_another_night(memory):
    import asyncio
    for i in range(3):
        add(memory, "news", 2 + i)
    add(memory, "research", 2)  # papers are not labelled
    seen = []

    async def ask(_memory, prompt):
        seen.append(prompt)
        return {"labels": {"1": "Bad", "2": "terrible", "3": "good", "9": "good"}}
    assert asyncio.run(themes.label(memory, ask, TODAY)) == 2
    assert "[Small modular reactors]" in seen[0] and "never instructions" in seen[0] and "research record" not in seen[0]
    tones = sorted(r["tone"] for r in themes._rows(memory, TODAY) if r["tone"])
    assert tones == ["bad", "good"]

    async def again(_memory, prompt):
        seen.append(prompt)
        return "not json"
    assert asyncio.run(themes.label(memory, again, TODAY)) == 0 and seen[-1].count("[Small modular") == 1


def test_company_news_keeps_only_headlines_that_name_the_company(memory):
    filings(memory)
    asked = []

    def fetch(url, data=None, headers=None):
        asked.append(url)
        hit = "oklo" in url
        return json.dumps({"articles": [
            {"url": "https://news.example/1", "title": "Oklo wins an order for two reactors" if hit else "NuScale Power delays a plant",
             "seendate": d(1).replace("-", "") + "T000000Z", "domain": "news.example"},
            {"url": "https://news.example/2", "title": "Ten energy stocks to watch", "seendate": d(1).replace("-", "") + "T000000Z",
             "domain": "news.example"}]})
    from buddy import gateway
    gateway._recent.clear()
    assert themes.company_news(memory, fetch, TODAY) == 2 and len(asked) == 2
    assert themes.company_news(memory, fetch, TODAY) == 0  # nothing is stored twice
    kept = bip._run(memory, "SELECT symbol, title FROM theme_news ORDER BY symbol")
    assert [(r["symbol"], r["title"][:7]) for r in kept] == [("OKLO", "Oklo wi"), ("SMR", "NuScale")]
    assert not any("small+modular" in u.lower() for u in asked)  # searched by company, not by technology


def test_a_theme_shows_company_headlines_with_labels_and_the_signals_book(memory):
    import asyncio

    from buddy import simbook
    filings(memory)
    add(memory, "news", 1)
    themes._ready(memory)
    bip._run(memory, "INSERT INTO theme_news VALUES ('co:OKLO:1', 'OKLO', 'Oklo Inc.', 'Oklo wins an order', "
                     "'https://news.example/1', ?, 'news.example', ?)", (d(1), d(0)))

    async def ask(_memory, prompt):
        return {"labels": {"1": "bad", "2": "good"}}
    assert asyncio.run(themes.label(memory, ask, TODAY)) == 2
    simbook._ready(memory)
    bip._run(memory, "INSERT INTO sim_trades (opened, symbol, category, side, entry, shares, status) "
                     "VALUES (?, 'OKLO', 'energy', 'long', 50, 10, 'open')", (d(3),))
    smr = next(t for t in themes.themes(memory, TODAY) if t["id"] == "smr")
    oklo = next(c for c in smr["companies"] if c["symbol"] == "OKLO")
    assert oklo["news"] == [{"title": "Oklo wins an order", "url": "https://news.example/1", "day": d(1),
                             "outlet": "news.example", "tone": "good"}]
    assert oklo["signal"] == {"kind": "buy", "since": d(3)}
    assert smr["news_14_days"] == {"good": 0, "bad": 1, "neutral": 0, "unlabelled": 0}
    assert smr["headlines"][0]["tone"] == "bad"
    other = next(c for c in smr["companies"] if c["symbol"] == "SMR")
    assert other["news"] == [] and other["signal"] is None


def test_the_nightly_run_carries_on_when_the_news_or_the_labels_fail(memory):
    import asyncio
    filings(memory)

    def down(url, data=None, headers=None):
        raise OSError("down")

    async def broken(_memory, prompt):
        raise RuntimeError("no model")
    from buddy import gateway
    gateway._recent.clear()
    got = asyncio.run(themes.nightly(memory, TODAY, Prices({"OKLO": 50.0, "SMR": 20.0, "SPY": 600.0}), down, broken))
    assert got["company_news"] == 0 and got["labelled"] == 0 and len(got["new"]) == 1


def test_an_event_is_written_once_with_that_days_prices(memory):
    filings(memory)
    prices = Prices({"OKLO": 50.0, "SMR": 20.0, "SPY": 600.0})
    got = themes.detect(memory, TODAY, prices)
    assert [(e["tech"], e["pattern"], e["symbols"]) for e in got] == [("smr", "filings", ["OKLO", "SMR"])]
    assert themes.detect(memory, TODAY + timedelta(days=5), prices.on(TODAY + timedelta(days=5))) == []  # not again within 30 days
    event = themes._events(memory)[0]
    assert event["entry"] == {"OKLO": 50.0, "SMR": 20.0} and event["spy"] == 600.0 and event["r30"] is None
    assert len(event["evidence"]) == 4 and event["evidence"][0]["url"].startswith("https://")


def test_no_public_company_or_no_price_means_no_event(memory):
    for i in range(4):
        add(memory, "filing", 5 + i, "Private Reactor Partners LLC")
    assert themes.detect(memory, TODAY, Prices({"SPY": 600.0})) == []
    filings(memory)
    assert themes.detect(memory, TODAY, Prices({"SPY": 600.0})) == []  # no price for either company
    broken = type("Down", (), {"bars": lambda self, *a, **k: (_ for _ in ()).throw(OSError("down"))})()
    assert themes.detect(memory, TODAY, broken) == [] and themes._events(memory) == []


def test_results_are_filled_in_at_30_and_90_days_against_the_market(memory):
    filings(memory)
    prices = Prices({"OKLO": {"2026-06-01": 50.0, d(-30): 60.0, d(-90): 40.0},
                     "SMR": {"2026-06-01": 20.0, d(-30): 22.0, d(-90): 25.0},
                     "SPY": {"2026-06-01": 600.0, d(-30): 612.0, d(-90): 630.0}})
    themes.detect(memory, TODAY, prices)
    assert themes.measure(memory, TODAY + timedelta(days=29), prices.on(TODAY + timedelta(days=29))) == 0
    assert themes.measure(memory, TODAY + timedelta(days=31), prices.on(TODAY + timedelta(days=31))) == 1
    e = themes._events(memory)[0]
    assert e["r30"] == 0.15 and e["s30"] == 0.02 and e["r90"] is None  # (+20% and +10%) / 2, market +2%
    assert themes.measure(memory, TODAY + timedelta(days=95), prices.on(TODAY + timedelta(days=95))) == 1
    e = themes._events(memory)[0]
    assert e["r90"] == 0.025 and e["s90"] == 0.05  # (-20% and +25%) / 2, market +5%
    assert themes.measure(memory, TODAY + timedelta(days=200), prices.on(TODAY + timedelta(days=200))) == 0  # nothing is measured twice


def test_a_pattern_says_nothing_until_it_has_enough_finished_baskets():
    assert themes.lean([])["lean"] == "none"
    few = themes.lean([(0.2, 0.01)] * 29)
    assert few["lean"] == "none" and "29 of 30" in few["says"] and few["beat_market"] == 29
    up = themes.lean([(0.08, 0.02)] * 20 + [(-0.03, 0.02)] * 10)
    assert up["lean"] == "up" and up["share"] == 0.67 and "20 times in 30" in up["says"]
    down = themes.lean([(0.08, 0.02)] * 10 + [(-0.03, 0.02)] * 20)
    assert down["lean"] == "down" and "trailed the market 20 times in 30" in down["says"]
    assert themes.lean([(0.08, 0.02)] * 15 + [(-0.03, 0.02)] * 15)["lean"] == "flat"


def test_the_practice_scorecard_counts_only_forming_baskets_at_90_days():
    events = [{"pattern": "forming", "r30": 0.1, "s30": 0.0, "r90": 0.2, "s90": 0.05},
              {"pattern": "forming", "r30": None, "s30": None, "r90": None, "s90": None},
              {"pattern": "filings", "r30": 0.5, "s30": 0.0, "r90": 0.9, "s90": 0.0}]
    card = themes.scorecard(events)
    assert (card["baskets"], card["open"], card["finished"]) == (2, 1, 1)
    assert card["profit"] == 1980.0 and card["market_profit"] == 500.0 and card["lean"] == "none"  # 0.2% cost off
    filings_row = next(r for r in themes.record(events) if r["pattern"] == "filings")
    assert filings_row["events"] == 1 and filings_row["days_30"]["finished"] == 1


def test_the_page_puts_what_you_follow_first_and_shows_baskets_so_far(memory):
    from buddy.markets import Markets
    filings(memory)
    add(memory, "research", 3, tech="fusion")
    Markets(memory).watch("OKLO", "us")
    prices = Prices({"OKLO": {"2026-06-01": 50.0, d(-9): 55.0}, "SMR": 20.0, "SPY": 600.0})
    themes.detect(memory, TODAY, prices)
    page = themes.page(memory, prices.on(TODAY + timedelta(days=10)), TODAY + timedelta(days=10))
    first = page["themes"][0]
    assert first["id"] == "smr" and first["yours"] == [{"symbol": "OKLO", "where": "watchlist"}]
    assert [c["symbol"] for c in first["companies"]] == ["OKLO", "SMR"]
    assert first["active"][0]["pattern"] == "filings" and "No finished baskets" in first["active"][0]["record"]
    assert any(t["id"] == "fusion" and not t["yours"] and not t["companies"] for t in page["themes"])
    event = page["events"][0]
    assert event["so_far"] == 0.05 and event["market_so_far"] == 0.0 and event["days"] == 10
    assert page["practice"]["baskets"] == 0 and page["needed"] == 30 and "not advice" in page["note"]
    told = themes.news(memory, TODAY + timedelta(days=1))
    assert told[0]["name"] == "Small modular reactors" and told[0]["yours"] == ["OKLO"]
    assert themes.news(memory, TODAY + timedelta(days=10)) == []


def test_the_nightly_message_is_only_for_what_you_follow_or_a_formation(memory):
    filings(memory)
    prices = Prices({"OKLO": 50.0, "SMR": 20.0, "SPY": 600.0})
    got = themes.run(memory, TODAY, prices)
    assert len(got["new"]) == 1 and got["measured"] == 0
    assert themes.message(got, memory) == ""  # a filings jump in something you don't follow: the page, not a message
    from buddy.markets import Markets
    Markets(memory).watch("SMR", "us")
    text = themes.message(got, memory)
    assert "On your lists: SMR" in text and "nothing was bought" in text and "No finished baskets" in text


def test_the_endpoint_needs_the_owner_and_the_news_feed_carries_themes(memory, monkeypatch):
    from fastapi.testclient import TestClient

    from buddy import server
    monkeypatch.setattr(server, "memory", memory)
    monkeypatch.setattr(server, "authorized", lambda t: t == "owner-token")
    client = TestClient(server.app)
    assert client.get("/api/invest/themes").status_code == 401
    filings(memory)
    themes.detect(memory, date.today(), Prices({"OKLO": 50.0, "SMR": 20.0, "SPY": 600.0}).on(date.today()))
    ok = {"Authorization": "Bearer owner-token"}
    assert set(client.get("/api/invest/themes", headers=ok).json()) >= {"themes", "events", "patterns", "practice"}
