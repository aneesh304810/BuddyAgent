"""Recommended watchlist: picked from the Intelligence evidence, archived when quiet, grouped by sector."""

import json
from datetime import date, timedelta

import pytest

from buddy import bip, pricebook, signals, watchpicks
from buddy.connectors.hub import ConnectorHub
from buddy.memory import Memory
from buddy.tools import ToolError

TODAY = date(2026, 11, 16)


@pytest.fixture
def memory(tmp_path):
    m = Memory(tmp_path / "buddy.db")
    ConnectorHub(m)
    yield m
    pricebook.close_all()


def filing(memory, who, days_ago, tech="smr", n=[0]):
    n[0] += 1
    bip._ready(memory)
    day = (TODAY - timedelta(days=days_ago)).isoformat()
    bip._run(memory, "INSERT INTO bip_signals (key, kind, source, title, url, day, who, amount, techs, sectors, seen) "
                     "VALUES (?, 'filing', 'test', ?, 'https://example.org/x', ?, ?, NULL, ?, ?, ?)",
             (f"w{n[0]}", f"filing {n[0]}", day, who, json.dumps([tech]), json.dumps([bip.TECHS[tech]["sector"]]), day))


def picks(view, key="recommended"):
    return [p["symbol"] for s in view[key] for i in s["industries"] for p in i["picks"]]


def test_a_company_named_in_a_recent_filing_is_recommended_with_its_sector_and_reason(memory):
    filing(memory, "Oklo Inc.  (OKLO)", 3)
    filing(memory, "Oklo Inc.  (OKLO)", 9)
    filing(memory, "NuScale Power Corp  (SMR)", 200)  # named long ago, nothing new: not recommended
    assert watchpicks.refresh(memory, TODAY) == {"added": ["OKLO"], "returned": [], "archived": []}
    view = watchpicks.view(memory, TODAY)
    assert picks(view) == ["OKLO"] and view["recommended_count"] == 1
    sector = view["recommended"][0]
    assert sector["sector"] == bip.SECTORS[bip.TECHS["smr"]["sector"]][0] and sector["industries"][0]["industry"] == bip.TECHS["smr"]["name"]
    pick = sector["industries"][0]["picks"][0]
    assert pick["why"].startswith("named in 2 filings on") and pick["first_seen"] == TODAY.isoformat()
    assert watchpicks.symbols(memory) == {"OKLO"} and "OKLO" in signals._universe(memory)  # joins the daily scan


def test_a_quiet_recommendation_is_archived_then_comes_back_when_the_evidence_does(memory):
    filing(memory, "Oklo Inc.  (OKLO)", 3)
    watchpicks.refresh(memory, TODAY)
    later = TODAY + timedelta(days=45)  # the filing is now 48 days old and nothing new came
    assert watchpicks.refresh(memory, later)["archived"] == ["OKLO"]
    view = watchpicks.view(memory, later)
    assert picks(view) == [] and picks(view, "archive") == ["OKLO"] and view["archive_count"] == 1
    assert view["archive"][0]["industries"][0]["picks"][0]["archived_on"] == later.isoformat()
    assert watchpicks.symbols(memory) == set()  # an archived one leaves the daily scan
    bip._run(memory, "INSERT INTO bip_signals (key, kind, source, title, url, day, who, amount, techs, sectors, seen) VALUES "
                     "('again', 'filing', 'test', 'new filing', 'https://example.org/x', ?, 'Oklo Inc.  (OKLO)', NULL, ?, ?, ?)",
             (later.isoformat(), json.dumps(["smr"]), json.dumps([bip.TECHS["smr"]["sector"]]), later.isoformat()))
    assert watchpicks.refresh(memory, later + timedelta(days=1))["returned"] == ["OKLO"]
    assert picks(watchpicks.view(memory, later + timedelta(days=1))) == ["OKLO"]


def test_a_dismissed_recommendation_stays_away_until_restored(memory):
    filing(memory, "Oklo Inc.  (OKLO)", 3)
    watchpicks.refresh(memory, TODAY)
    view = watchpicks.decide(memory, "oklo", "dismiss")
    assert picks(view) == [] and view["dismissed"] == ["OKLO"]
    watchpicks.refresh(memory, TODAY)  # still active in the evidence, still dismissed
    assert picks(watchpicks.view(memory, TODAY)) == []
    assert picks(watchpicks.decide(memory, "OKLO", "restore")) == ["OKLO"]
    with pytest.raises(ToolError):
        watchpicks.decide(memory, "NOPE", "dismiss")
