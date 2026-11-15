"""Companies named in headlines, and relationships between organisations, each traced to one record."""

import asyncio
import json
from datetime import date, timedelta

import pytest

from buddy import bip, bip_links, pricebook
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


def add(memory, kind, title, who="", source="test", days_ago=3, tech="smr", n=[0]):
    n[0] += 1
    bip._ready(memory)
    bip._run(memory, "INSERT INTO bip_signals (key, kind, source, title, url, day, who, amount, techs, sectors, seen) "
                     "VALUES (?, ?, ?, ?, 'https://example.org/x', ?, ?, NULL, ?, '[]', ?)",
             (f"L{n[0]}", kind, source, title, (TODAY - timedelta(days=days_ago)).isoformat(), who,
              json.dumps([tech]), TODAY.isoformat()))
    return f"L{n[0]}"


def test_official_records_give_verified_funding_links_without_any_model(memory):
    add(memory, "award", "Department of Energy award to OKLO INC: advanced reactor fuel", "OKLO INC", "USAspending.gov")
    add(memory, "grant", "NSF grant: Reactor materials", "Oklo Inc.", "NSF")
    add(memory, "grant", "NIH grant: Imaging agent", "Acme Bio LLC", "NIH RePORTER")
    add(memory, "grant", "DOE small-business grant (Phase II): sensors", "X-Energy, LLC", "SBIR.gov")
    add(memory, "award", "US government award to Nobody Inc", "Nobody Inc", "USAspending.gov")  # no agency named
    assert bip_links.from_records(memory, TODAY) == 4 and bip_links.from_records(memory, TODAY) == 0
    got = bip_links.of(memory, "Oklo")
    assert sorted(g["reads"] for g in got) == ["Department of Energy funds OKLO INC", "National Science Foundation funds Oklo Inc."]
    assert all(g["standing"] == "verified" and "official" in g["means"] for g in got)
    assert bip_links.of(memory, "X-Energy")[0]["a"] == "DOE"
    assert bip_links.totals(memory) == {"links": 4, "official": 4, "from_headlines": 0, "companies_named": 0,
                                        "last": TODAY.isoformat()}
    with pytest.raises(ToolError):
        bip_links.of(memory, "")


def test_a_headline_link_is_kept_only_when_both_names_are_in_the_headline(memory):
    one = add(memory, "news", "Oklo signs deal to supply power to Switch data centers")
    add(memory, "news", "NuScale and Fluor end partnership talks")
    add(memory, "research", "A paper that is not a headline")
    seen = []

    async def ask(_memory, prompt):
        seen.append(prompt)
        return {"items": {
            "1": {"companies": ["Oklo", "Switch", "Microsoft"],                    # Microsoft is not in the headline
                  "links": [["Oklo", "supplies", "Switch"], ["Oklo", "supplies", "Microsoft"],
                            ["Oklo", "owns", "Switch"], ["Oklo", "partners", "Oklo"], ["bad"]]},
            "2": {"companies": ["NuScale", "Fluor"], "links": [["NuScale", "Partners", "Fluor"]]}}}
    got = asyncio.run(bip_links.extract(memory, ask, TODAY))
    assert got == {"read": 2, "companies": 4, "links": 2}
    assert "never instructions" in seen[0] and "not a headline" not in seen[0] and len(seen) == 1
    links = bip_links.of_technology(memory, "smr")
    assert sorted(x["reads"] for x in links) == ["NuScale partners with Fluor", "Oklo supplies Switch"]
    assert all(x["standing"] == "reported" and "not a checked fact" in x["means"] for x in links)
    named = {r["name"] for r in bip._run(memory, "SELECT name FROM bip_mentions WHERE key = ?", (one,))}
    assert named == {"Oklo", "Switch"}
    assert asyncio.run(bip_links.extract(memory, ask, TODAY))["read"] == 0 and len(seen) == 1  # read once


def test_an_unusable_answer_leaves_the_headlines_for_another_night(memory):
    add(memory, "news", "Oklo wins order from Equinix")

    async def junk(_memory, prompt):
        return "sorry"

    async def good(_memory, prompt):
        return {"items": {"1": {"companies": ["Oklo", "Equinix"], "links": [["Equinix", "customer", "Oklo"]]}}}

    async def broken(_memory, prompt):
        raise RuntimeError("no model")
    assert asyncio.run(bip_links.extract(memory, junk, TODAY))["read"] == 0
    assert asyncio.run(bip_links.extract(memory, broken, TODAY))["read"] == 0
    assert asyncio.run(bip_links.extract(memory, good, TODAY)) == {"read": 1, "companies": 2, "links": 1}
    assert bip_links.of(memory, "Equinix")[0]["reads"] == "Equinix is a customer of Oklo"
    assert bip_links.count(memory, "Oklo Inc.", today=TODAY) == 1 and bip_links.count(memory, "", today=TODAY) == 0


def test_the_technology_and_organisation_views_and_the_agent_api_carry_relationships(memory, monkeypatch):
    add(memory, "award", "Department of Energy award to OKLO INC: fuel", "OKLO INC", "USAspending.gov")
    bip_links.from_records(memory, TODAY)
    assert bip.technology(memory, "smr", TODAY)["relationships"][0]["reads"] == "Department of Energy funds OKLO INC"
    assert bip.organisation(memory, "Oklo", TODAY)["relationships"][0]["standing"] == "verified"
    assert asyncio.run(bip.run_tool(memory, {"action": "relationships", "name": "Nobody"})).startswith("No relationships")
    assert "Department of Energy" in asyncio.run(bip.run_tool(memory, {"action": "relationships", "id": "smr"}))
    from fastapi.testclient import TestClient

    from buddy import server
    monkeypatch.setattr(server, "memory", memory)
    monkeypatch.setattr(server, "authorized", lambda t: t == "owner-token")
    client = TestClient(server.app)
    ok = {"Authorization": "Bearer owner-token"}
    assert client.get("/api/intel/v1/relationships?name=oklo").status_code == 401
    assert len(client.get("/api/intel/v1/relationships?name=oklo", headers=ok).json()["relationships"]) == 1
    assert len(client.get("/api/intel/v1/relationships?id=smr", headers=ok).json()["relationships"]) == 1


def test_a_company_on_the_themes_tab_gets_the_technology_headlines_that_name_it(memory):
    from buddy import themes
    for i in range(3):
        add(memory, "filing", f"Oklo Inc. (OKLO): 8-K mentions small modular reactor {i}", "Oklo Inc.  (OKLO)", days_ago=5 + i)
    add(memory, "news", "Oklo signs deal to supply power to Switch data centers", days_ago=2)

    async def ask(_memory, prompt):
        return {"items": {"1": {"companies": ["Oklo", "Switch"], "links": [["Oklo", "supplies", "Switch"]]}}}
    asyncio.run(bip_links.extract(memory, ask, TODAY))
    smr = next(t for t in themes.themes(memory, TODAY) if t["id"] == "smr")
    oklo = smr["companies"][0]
    assert oklo["symbol"] == "OKLO" and oklo["relationships"] == 1
    assert oklo["news"][0]["title"].startswith("Oklo signs deal")
