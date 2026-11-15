"""The shared signals library, the theme model's rows, and the history back-fill."""

import json
from datetime import date, timedelta

import pytest

from buddy import bip, factors, modeltrain, pricebook, themes
from buddy.connectors.hub import ConnectorHub
from buddy.memory import Memory

from tests.test_themes import Prices, add, d, filings

TODAY = date(2026, 11, 16)


@pytest.fixture
def memory(tmp_path):
    from buddy import gateway
    gateway._recent.clear()
    m = Memory(tmp_path / "buddy.db")
    ConnectorHub(m)
    yield m
    pricebook.close_all()


def deal(memory, title, days_ago, source="feed", domain="product", n=[0]):
    n[0] += 1
    from buddy import dealmail  # noqa: F401  (its columns)
    pricebook.run(memory, "ALTER TABLE deals ADD COLUMN IF NOT EXISTS domain VARCHAR DEFAULT 'product'")
    pricebook.run(memory, "INSERT INTO deals (seen_on, source, title, url, price, domain) VALUES (?, ?, ?, ?, 10, ?)",
                  (d(days_ago), source, title, f"https://x.example/{n[0]}", domain))


def test_investing_factors_come_from_the_evidence_for_the_companys_technologies(memory):
    filings(memory)  # five SMR filings naming OKLO and SMR
    add(memory, "news", 2)
    add(memory, "grant", 20)
    themes._ready(memory)
    key = bip._run(memory, "SELECT key FROM bip_signals WHERE kind = 'news'")[0]["key"]
    bip._run(memory, "INSERT INTO theme_tone VALUES (?, 'good', ?)", (key, d(0)))
    bip._run(memory, "INSERT INTO theme_news VALUES ('co:OKLO:9', 'OKLO', 'Oklo Inc.', 'Oklo loses a licence', "
                     "'https://n.example/9', ?, 'n.example', ?)", (d(3), d(0)))
    bip._run(memory, "INSERT INTO theme_tone VALUES ('co:OKLO:9', 'bad', ?)", (d(0),))
    got = factors.investing(memory, "oklo", TODAY)
    assert got["f_evidence_90"] == 7 and got["f_evidence_kinds"] == 3 and got["f_themes"] == 1
    assert got["f_good_news_14"] == 1 and got["f_bad_news_14"] == 1 and got["f_patterns_30"] == 0
    assert got["f_stage"] == bip.STAGES.index(bip.maturity({"filing": 5, "news": 1, "grant": 1}, 1)["stage"])
    themes.detect(memory, TODAY, Prices({"OKLO": 50.0, "SMR": 20.0, "SPY": 600.0}))
    assert factors.investing(memory, "OKLO", TODAY)["f_patterns_30"] == 1
    assert factors.investing(memory, "AAPL", TODAY) == {}  # no technology's filings name it


def test_shopping_and_travel_factors_count_deals_from_the_inbox_reddit_and_feeds(memory):
    pricebook.run(memory, "SELECT 1")
    deal(memory, "Sony WH-1000XM5 headphones $279", 5, "email: Best Buy")
    deal(memory, "Sony WH-1000XM5 headphones open box", 40, "reddit")
    deal(memory, "Sony WH-1000XM5 lowest ever", 200, "feed")  # too old
    deal(memory, "LG OLED TV", 3, "feed")
    got = factors.shopping(memory, "Sony WH-1000XM5", "electronics", TODAY)
    assert got == {"f_deals_named_90": 2.0, "f_inbox_deals_90": 1.0, "f_reddit_deals_90": 1.0}
    for i in range(4):
        deal(memory, f"EWR to LIM fare sale {i}", 3 + i, "email: airline", "flight")
    deal(memory, "JFK to LHR fare sale", 45, "feed", "flight")
    memory.set(factors.OIL_KEY, json.dumps({"day": TODAY.isoformat(), "change": 0.12}))
    trip = factors.travel(memory, "flight", "EWR-LIM", TODAY + timedelta(days=60), TODAY)
    assert trip["f_deals_30"] == 4 and trip["f_deals_named_30"] == 4 and trip["f_deals_growth"] == 3.0
    assert trip["f_days_out"] == 60 and trip["f_oil_30"] == 0.12
    assert "f_oil_30" not in factors.travel(memory, "hotel", "Lima", TODAY + timedelta(days=60), TODAY)


def test_oil_is_looked_up_once_a_day_and_missing_without_its_key(memory):
    calls = []

    def fetch(url, data=None, headers=None):
        calls.append(url)
        rows = [{"period": (TODAY - timedelta(days=i)).isoformat(), "value": 80.0 if i < 5 else 64.0} for i in range(40)]
        return json.dumps({"response": {"data": rows}})
    assert factors.oil(memory, TODAY, {}, fetch) is None and calls == []  # no key: no call
    env = {"BUDDY_EIA_KEY": "k" * 20}
    assert factors.oil(memory, TODAY + timedelta(days=1), env, fetch) == 0.25
    assert factors.oil(memory, TODAY + timedelta(days=1), env, fetch) == 0.25 and len(calls) == 1


def test_factors_are_saved_with_a_shopping_call_and_reach_the_trainer(memory):
    from buddy import whentobuy
    deal(memory, "Dyson V15 vacuum $499", 4, "email: Dyson")
    got = whentobuy.advise(memory, "Dyson V15 vacuum", 649.0, "home", today=TODAY)
    saved = pricebook.run(memory, "SELECT factors FROM predictions WHERE id = ?", (got["prediction_id"],))[0]
    assert json.loads(saved["factors"])["f_inbox_deals_90"] == 1.0
    pricebook.run(memory, "UPDATE predictions SET outcome = 'right', best_seen = 500 WHERE id = ?", (got["prediction_id"],))
    row = modeltrain.rows(memory, "product")[0]
    assert row["x"]["f_inbox_deals_90"] == 1.0 and row["y"] == 1 and any(k.startswith("reason_") for k in row["x"])
    assert whentobuy.features_of({"sale": 0.3}, "home") == {"reason_sale": 0.3, "kind_electronics": 0, "kind_home": 1,
                                                            "kind_clothing": 0}


def test_an_event_keeps_a_snapshot_and_finished_events_are_the_theme_models_rows(memory):
    filings(memory)
    prices = Prices({"OKLO": {"2026-06-01": 50.0, d(-90): 60.0}, "SMR": 20.0, "SPY": 600.0})
    themes.detect(memory, TODAY, prices)
    event = themes._events(memory)[0]
    assert event["features"]["f_filings_90"] == 5 and event["features"]["f_companies"] == 2
    assert modeltrain.rows(memory, "theme") == []  # nothing finished yet
    themes.measure(memory, TODAY + timedelta(days=91), prices.on(TODAY + timedelta(days=91)))
    row = modeltrain.rows(memory, "theme")[0]
    assert row["y"] == 1 and row["x"]["pattern_filings"] == 1 and row["x"]["pattern_news"] == 0
    assert row["x"]["f_evidence_90"] == 5 and row["when"] == TODAY.isoformat()
    assert "theme" in modeltrain.DOMAINS and modeltrain.readiness(memory, "theme")["rows"] == 1


def test_history_is_replayed_with_only_what_was_known_then(memory):
    for i in range(4):  # four filings about ten months ago, one a month before those
        add(memory, "filing", 300 + i, "Oklo Inc.  (OKLO)")
    add(memory, "filing", 340, "Oklo Inc.  (OKLO)")
    for i in range(4):  # and a later jump, naming a second company that did not file before
        add(memory, "filing", 120 + i, "NuScale Power Corp  (SMR)")
    early, later = TODAY - timedelta(days=300), TODAY - timedelta(days=120)
    prices = Prices({"OKLO": {"2025-01-01": 10.0, (early + timedelta(days=30)).isoformat(): 12.0,
                              (early + timedelta(days=90)).isoformat(): 15.0},
                     "SMR": {"2025-01-01": 20.0}, "SPY": {"2025-01-01": 500.0, (early + timedelta(days=90)).isoformat(): 525.0}},
                    start=date(2025, 1, 1))
    written = themes.replay(memory, 12, prices, TODAY)
    events = [e for e in themes._events(memory) if e["pattern"] == "filings"]
    assert written == len(themes._events(memory)) and len(events) == 2 and all(e["backfilled"] for e in events)
    first, second = events[-1], events[0]
    assert first["day"] == early.isoformat() and first["symbols"] == ["OKLO"]  # NuScale had not filed yet
    assert (first["r30"], first["r90"], first["s90"]) == (0.2, 0.5, 0.05)
    assert second["day"] == later.isoformat() and set(second["symbols"]) == {"OKLO", "SMR"}
    assert themes.replay(memory, 12, prices, TODAY) == 0  # running it again writes nothing twice
    record = next(r for r in themes.record(themes._events(memory)) if r["pattern"] == "filings")
    assert record["from_history"] == 2 and record["days_90"]["finished"] == 2
    assert len(modeltrain.rows(memory, "theme")) == 2


def test_the_past_is_asked_only_of_sources_that_can_answer_for_a_date(memory):
    asked = []

    def fetch(url, data=None, headers=None):
        asked.append(url)
        if "efts.sec.gov" in url and "forms=" not in url:
            return json.dumps({"hits": {"hits": [{"_id": f"0001-25-{len(asked)}:d.htm", "_source": {
                "display_names": ["Oklo Inc.  (OKLO)  (CIK 0000012345)"], "form": "8-K",
                "file_date": "2026-03-01", "ciks": ["0000012345"]}}]}})
        return json.dumps({})
    memory.set("BUDDY_SEC_CONTACT", "x")
    import os
    os.environ.setdefault("BUDDY_SEC_CONTACT", "owner@example.com")
    bip.switch(memory, "ai", False), bip.switch(memory, "cybersecurity", False), bip.switch(memory, "infrastructure", False)
    got = themes.backfill_evidence(memory, months=6, fetch=fetch, today=TODAY, step=90)
    hosts = {u.split("/")[2] for u in asked}
    assert hosts <= {"efts.sec.gov", "api.usaspending.gov", "api.openalex.org", "api.nsf.gov", "www.research.gov"}
    assert not any(h in hosts for h in ("api.gdeltproject.org", "export.arxiv.org", "api.github.com", "hn.algolia.com"))
    assert got["asked"] > 0 and themes.backfill_status(memory)["state"] == "collecting"
    kept = bip._run(memory, "SELECT day FROM bip_signals")
    assert all(str(r["day"]) == "2026-03-01" for r in kept)  # only records dated on or before the day asked about


def test_the_overview_lists_three_engines_and_the_endpoints_need_the_owner(memory, monkeypatch):
    out = factors.overview(memory)
    assert [e["id"] for e in out["engines"]] == ["investing", "travel", "shopping"]
    assert [m["domain"] for m in out["engines"][0]["models"]] == ["investing", "theme"]
    assert out["engines"][1]["models"][0]["needed"] == modeltrain.MIN_ROWS and out["history"] == {"state": "not run"}
    assert all(e["learns_from"] for e in out["engines"])
    from fastapi.testclient import TestClient

    from buddy import server
    monkeypatch.setattr(server, "memory", memory)
    monkeypatch.setattr(server, "authorized", lambda t: t == "owner-token")
    client = TestClient(server.app)
    assert client.get("/api/intel/predict").status_code == 401
    assert client.post("/api/intel/predict", json={"history": True}).status_code == 401
    ok = {"Authorization": "Bearer owner-token"}
    assert len(client.get("/api/intel/predict", headers=ok).json()["engines"]) == 3
    assert client.post("/api/intel/predict", json={}, headers=ok).status_code == 400
