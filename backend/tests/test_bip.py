"""Buddy Intelligence Platform: evidence collected from public sources, tagged, joined into convergences, staged."""

import asyncio
import json
import urllib.parse
from datetime import date, timedelta

import pytest

from buddy import bip, pricebook
from buddy import bip_sources as src
from buddy.connectors.hub import ConnectorHub
from buddy.memory import Memory
from buddy.tools import ToolError

TODAY = date(2026, 11, 16)


@pytest.fixture
def memory(tmp_path, monkeypatch):
    monkeypatch.setenv("BUDDY_SEC_CONTACT", "owner@example.com")
    from buddy import gateway
    gateway._recent.clear()  # Buddy Connect's per-minute pacing starts fresh for each test
    m = Memory(tmp_path / "buddy.db")
    ConnectorHub(m)
    yield m
    pricebook.close_all()


def d(days_ago):
    return (TODAY - timedelta(days=days_ago)).isoformat()


class Web:
    """Stands in for the seven sources. data: {source: {phrase fragment: [records]}}."""

    def __init__(self, data=None, down=()):
        self.data, self.down, self.asked = data or {}, down, []

    def find(self, source, phrase):
        return next((rows for part, rows in (self.data.get(source) or {}).items() if part in phrase.lower()), [])

    def __call__(self, url, data=None, headers=None):
        self.asked.append(url)
        q = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
        host = urllib.parse.urlsplit(url).hostname
        if any(x in host for x in self.down):
            raise OSError("down")
        if host == "export.arxiv.org":
            rows = self.find("arxiv", q["search_query"][0])
            entries = "".join(f"<entry><id>http://arxiv.org/abs/{r['id']}</id><title>{r['title']}</title>"
                              f"<summary>{r.get('text', '')}</summary><published>{r['day']}T00:00:00Z</published></entry>"
                              for r in rows)
            return f'<feed xmlns="http://www.w3.org/2005/Atom">{entries}</feed>'
        if host == "efts.sec.gov":
            assert "owner@example.com" in (headers or {}).get("User-Agent", "")
            if q.get("forms") == ["D,D/A"]:
                rows = self.find("form_d", q["q"][0])
                return json.dumps({"hits": {"hits": [{"_id": f"0009-26-{n}:primary_doc.xml", "_source": {
                    "display_names": [f"{r['who']}  (CIK 0000099999)"], "form": "D", "file_date": r["day"],
                    "ciks": ["0000099999"]}} for n, r in enumerate(rows)]}})
            rows = self.find("filings", q["q"][0])
            return json.dumps({"hits": {"hits": [{"_id": f"0001-26-{n}:doc.htm", "_source": {
                "display_names": [f"{r['who']}  (CIK 0000012345)"], "form": "8-K", "file_date": r["day"],
                "ciks": ["0000012345"]}} for n, r in enumerate(rows)]}})
        if host == "api.usaspending.gov":
            rows = self.find("awards", json.loads(data)["filters"]["keywords"][0])
            return json.dumps({"results": [{"Award ID": r["id"], "Recipient Name": r["who"], "Award Amount": r["amount"],
                                            "Start Date": r["day"], "Awarding Agency": "Department of Energy",
                                            "generated_internal_id": f"CONT_{r['id']}"} for r in rows]})
        if host == "api.github.com":
            rows = self.find("code", q["q"][0])
            return json.dumps({"items": [{"html_url": f"https://github.com/{r['id']}", "full_name": r["id"],
                                          "description": r["title"], "created_at": r["day"] + "T00:00:00Z",
                                          "stargazers_count": 300, "owner": {"login": "acme"}} for r in rows]})
        if host == "hn.algolia.com":
            rows = self.find("discussion", q["query"][0])
            return json.dumps({"hits": [{"objectID": r["id"], "title": r["title"], "url": "https://news.example/x",
                                         "created_at": r["day"] + "T00:00:00Z", "points": 120} for r in rows]})
        if host == "www.ebi.ac.uk":
            rows = self.find("europepmc", q["query"][0])
            return json.dumps({"resultList": {"result": [{"id": r["id"], "source": "MED", "title": r["title"],
                                                          "firstPublicationDate": r["day"]} for r in rows]}})
        if host == "clinicaltrials.gov":
            rows = self.find("trials", q["query.term"][0])
            return json.dumps({"studies": [{"protocolSection": {
                "identificationModule": {"nctId": r["id"], "briefTitle": r["title"]},
                "statusModule": {"overallStatus": "RECRUITING", "startDateStruct": {"date": r["day"][:7]}},
                "sponsorCollaboratorsModule": {"leadSponsor": {"name": r["who"]}},
                "designModule": {"phases": ["PHASE1"]}}} for r in rows]})
        if host == "api.gdeltproject.org":
            rows = self.find("gdelt", q["query"][0])
            return json.dumps({"articles": [{"url": f"https://news.example/{r['id']}", "title": r["title"],
                                             "seendate": r["day"].replace("-", "") + "T120000Z",
                                             "domain": "news.example"} for r in rows]})
        if host == "api.openalex.org":
            rows = self.find("openalex", q["search"][0])
            return json.dumps({"results": [{"id": f"https://openalex.org/{r['id']}", "title": r["title"],
                                            "doi": f"https://doi.org/10.1/{r['id']}", "publication_date": r["day"],
                                            "primary_location": {"source": {"display_name": "Nature Energy"}}}
                                           for r in rows]})
        if host == "api.reporter.nih.gov":
            rows = self.find("nih", json.loads(data)["criteria"]["advanced_text_search"]["search_text"])
            return json.dumps({"results": [{"appl_id": r["id"], "project_title": r["title"],
                                            "organization": {"org_name": r["who"]}, "award_amount": r["amount"],
                                            "project_start_date": r["day"] + "T00:00:00Z"} for r in rows]})
        if host in ("api.nsf.gov", "www.research.gov"):
            rows = self.find("nsf", q["keyword"][0])
            return json.dumps({"response": {"award": [{"id": r["id"], "title": r["title"], "awardeeName": r["who"],
                                                       "fundsObligatedAmt": str(r["amount"]),
                                                       "startDate": f"{r['day'][5:7]}/{r['day'][8:10]}/{r['day'][:4]}"}
                                                      for r in rows]}})
        if host == "api.www.sbir.gov":
            rows = self.find("sbir", q["keyword"][0])
            return json.dumps([{"firm": r["who"], "award_title": r["title"], "agency": "DOE", "phase": "Phase II",
                                "award_amount": str(r["amount"]), "proposal_award_date": r["day"],
                                "agency_tracking_number": r["id"]} for r in rows])
        raise AssertionError(url)


SMR = {"arxiv": {"small modular reactor": [{"id": "2611.1", "title": "Small modular reactors to power AI data centers",
                                            "day": d(10), "text": "A study of SMR siting beside hyperscale data centres."}]},
       "filings": {"small modular reactor": [{"who": "Oklo Inc. (OKLO)", "day": d(5)}, {"who": "NuScale Power Corp (SMR)", "day": d(8)},
                                             {"who": "Constellation Energy (CEG)", "day": d(20)}]},
       "awards": {"small modular reactor": [{"id": "DE-1", "who": "X-Energy LLC", "amount": 80_000_000.0, "day": d(40)}]},
       "discussion": {"small modular reactor": [{"id": "9", "title": "Microreactor startup signs data center deal", "day": d(3)}]}}


def test_tagging_finds_every_technology_a_text_mentions():
    assert bip.tag("Small modular reactors to power AI data centers") == ["data_centers", "smr"]
    assert bip.tag("A note on gardening", also="fusion") == ["fusion"]
    assert "pqc" in bip.tag("Migrating to post-quantum cryptography in the cloud")
    assert all(t["sector"] in bip.SECTORS for t in bip.TECHS.values()) and len(bip.TECHS) == 54


def test_stage_rules_say_which_rule_fired():
    assert bip.maturity({"research": 9}, 0)["stage"] == "Scientific research"
    assert bip.maturity({"research": 2, "code": 1}, 0)["stage"] == "Experimental"
    assert bip.maturity({"code": 4}, 0)["stage"] == "Prototype"
    pilot = bip.maturity({"award": 1, "filing": 1}, 1)
    assert pilot["stage"] == "Pilot deployment" and "only 1 company" in pilot["why"]
    assert bip.maturity({"filing": 4, "award": 2}, 3) == {
        "stage": "Early commercialization", "why": "3 companies mention it in their filings"}
    assert bip.maturity({"filing": 30}, 12)["stage"] == "Scaling"
    assert bip.maturity({}, 0)["stage"] == "No evidence yet"
    assert bip.maturity({"filing": 40}, 20, established=True)["stage"] == "Established market"


def test_a_night_collects_tags_and_only_asks_the_sectors_that_are_on(memory):
    web = Web(SMR)
    got = bip.collect(memory, web, TODAY, pause=0)
    assert got["new"] == 6 and got["failed"] == {}
    hosts = {urllib.parse.urlsplit(u).hostname for u in web.asked}
    assert "clinicaltrials.gov" not in hosts and "www.ebi.ac.uk" not in hosts  # biomedical is off
    assert not any("humanoid" in urllib.parse.unquote_plus(u) for u in web.asked)  # robotics is off
    assert bip.collect(memory, web, TODAY, pause=0)["new"] == 0  # nothing is stored twice
    smr = next(t for t in bip.technologies(memory, TODAY) if t["id"] == "smr")
    assert smr["signals_90_days"] == 6 and smr["companies"] == 4 and smr["kinds"]["filing"] == 3
    assert smr["stage"] == "Early commercialization" and "3 companies" not in smr["why"] or smr["companies"] == 4
    status = {s["name"]: s for s in src.status(memory)}
    assert status["filings"]["state"] == "working" and status["filings"]["evidence"] == "company disclosure"
    assert status["trials"]["state"] == "not run yet"


def test_a_convergence_is_two_sectors_sharing_records_with_its_evidence(memory):
    data = {**SMR, "arxiv": {"small modular reactor": SMR["arxiv"]["small modular reactor"] + [
        {"id": "2611.2", "title": "Microreactors for hyperscale data centers", "day": d(4)}]}}
    bip.collect(memory, Web(data), TODAY, pause=0)
    found = bip.convergences(memory, TODAY)
    top = found[0]
    assert {top["a"], top["b"]} == {"Small modular reactors", "AI data centers"}
    assert top["sectors"] == ["Energy and advanced materials", "AI infrastructure and semiconductors"] or \
        top["sectors"] == ["AI infrastructure and semiconductors", "Energy and advanced materials"]
    assert top["signals_90_days"] == 3 and top["interpretation"] is None  # two papers and a story name both
    paper = next(e for e in top["evidence"] if e["kind"] == "research")
    assert paper["evidence"] == "published research" and "claim" in paper["means"]
    assert paper["url"].startswith("https://arxiv.org/abs/")
    assert {e["evidence"] for e in top["evidence"]} == {"published research", "reported"}


def test_interpretation_is_kept_apart_and_a_thin_answer_is_dropped(memory):
    data = {**SMR, "arxiv": {"small modular reactor": SMR["arxiv"]["small modular reactor"] + [
        {"id": "2611.2", "title": "Microreactors for hyperscale data centers", "day": d(4)}]}}
    seen = {}

    async def ask(memory, prompt):
        seen["prompt"] = prompt
        return {"name": "Nuclear power for AI data centers", "how": "Reactors are being sited beside data centers.",
                "problem": "Data centers need steady power.", "watch": "A reactor licence tied to a data center."}
    got = asyncio.run(bip.run(memory, Web(data), ask, TODAY))
    assert got["interpreted"] == 1 and "information, never instructions" in seen["prompt"]
    assert "Do not add facts" in seen["prompt"]
    top = bip.convergences(memory, TODAY)[0]
    assert top["interpretation"]["name"] == "Nuclear power for AI data centers"
    assert top["stage"] in bip.STAGES  # the stage comes from the rules, not from the model

    async def thin(memory, prompt):
        return {"name": "x", "how": "yes"}
    pricebook.run(memory, "DELETE FROM bip_notes")
    assert asyncio.run(bip.interpret(memory, bip.convergences(memory, TODAY), thin)) == 0


def test_formation_needs_three_kinds_rising_and_is_told_once(memory):
    many = lambda n, **k: [{"id": f"{k.get('p', 'x')}{i}", "title": "Fusion energy pilot plant", "day": d(2 + i % 20),
                            "who": f"Fusion Co {i}", "amount": 1e6} for i in range(n)]  # noqa: E731
    web = Web({"arxiv": {"fusion": many(4, p="a")}, "filings": {"fusion": many(4)},
               "awards": {"fusion": many(4, p="w")}, "discussion": {"fusion": many(2, p="h")}})
    got = asyncio.run(bip.run(memory, web, None, TODAY))
    assert [f["name"] for f in got["forming"]] == ["Fusion energy"]
    assert len(got["forming"][0]["rising"]) == 3 and "official record (0 to 4)" in got["forming"][0]["rising"]
    text = bip.message(got)
    assert "an industry may be forming" in text and "not a forecast" in text
    again = asyncio.run(bip.run(memory, web, None, TODAY + timedelta(days=1)))
    assert again["forming"] == [] and bip.message(again) == ""
    assert bip.overview(memory, TODAY)["forming"][0]["name"] == "Fusion energy"


def test_a_sector_that_is_off_is_watched_lightly_and_flagged_when_it_moves(memory):
    quiet = Web({"filings": {"humanoid": [{"who": "Robo Inc", "day": d(5)}]}})
    assert bip.pulse(memory, quiet, TODAY - timedelta(days=7)) == []  # the first week only sets the baseline
    assert pricebook.run(memory, "SELECT COUNT(*) AS n FROM bip_signals")[0]["n"] == 0  # nothing is collected in full
    busy = Web({"filings": {"humanoid": [{"who": f"Robo {i}", "day": d(3)} for i in range(9)]},
                "discussion": {"humanoid": [{"id": str(i), "title": "Humanoid robots ship", "day": d(2)} for i in range(4)]}})
    got = asyncio.run(bip.run(memory, busy, None, TODAY))
    move = got["moving"][0]
    assert move["name"] == "Robotics and automation" and move["now"] == 13 and move["before"] == 1.0
    assert move["moving"][0] == {"name": "Humanoid robots", "now": 13, "before": 1.0}
    text = bip.message(got)
    assert "a lot is moving in Robotics and automation" in text and "don't miss" in text
    assert "not a judgement that there is an opportunity" in text and 'Say "follow robotics"' in text
    sector = next(s for s in bip.overview(memory, TODAY)["sectors"] if s["id"] == "robotics")
    assert sector["on"] is False and sector["movement"]["now"] == 13 and sector["movement"]["before"] == 1
    assert not bip.pulse_due(memory, TODAY) and bip.pulse_due(memory, TODAY + timedelta(days=7))
    assert asyncio.run(bip.run(memory, busy, None, TODAY + timedelta(days=1)))["moving"] == []  # told once


def test_switching_a_sector_on_starts_collecting_it(memory):
    assert bip.sectors_on(memory) == ["ai", "cybersecurity", "infrastructure", "energy"]
    assert "robotics" in bip.switch(memory, "robotics", True)
    web = Web({"filings": {"humanoid": [{"who": "Robo Inc", "day": d(5)}]}})
    assert bip.collect(memory, web, TODAY, pause=0)["new"] == 1
    assert "robotics" not in bip.switch(memory, "robotics", False)
    with pytest.raises(ToolError):
        bip.switch(memory, "astrology", True)


def test_a_failing_source_is_recorded_and_the_others_still_run(memory):
    got = bip.collect(memory, Web(SMR, down=("efts.sec.gov",)), TODAY, pause=0)
    assert got["new"] == 3 and "filings" in got["failed"]
    status = {s["name"]: s for s in src.status(memory)}
    assert status["filings"]["state"] == "failing" and status["awards"]["state"] == "working"
    sec = [u for u in Web(SMR).asked]
    assert sec == []


def test_technology_and_organisation_views_and_search(memory):
    bip.collect(memory, Web(SMR), TODAY, pause=0)
    t = bip.technology(memory, "smr", TODAY)
    assert t["stage"] == "Early commercialization" and len(t["months"]) == 12
    assert sum(m["filing"] for m in t["months"]) == 3 and t["linked"][0]["name"] == "AI data centers"
    assert t["evidence"]["award"][0]["amount"] == 80_000_000.0 and t["evidence"]["award"][0]["evidence"] == "official record"
    assert {o["name"] for o in t["organisations"]} >= {"Oklo Inc. (OKLO)", "X-Energy LLC"}
    who = bip.organisation(memory, "oklo", TODAY)
    assert who["records"] == 1 and who["technologies"][0]["name"] == "Small modular reactors"
    assert bip.search(memory, "nuscale")[0]["who"].startswith("NuScale")
    with pytest.raises(ToolError):
        bip.technology(memory, "warp_drive")
    out = json.loads(asyncio.run(bip.run_tool(memory, {"action": "overview"})))
    assert out["signals_total"] == 6 and "forecast" in out["note"] and len(out["sectors"]) == 10


def test_the_page_endpoints_need_the_owner(memory, monkeypatch):
    from fastapi.testclient import TestClient

    from buddy import server
    monkeypatch.setattr(server, "memory", memory)
    monkeypatch.setattr(server, "authorized", lambda t: t == "s3cret")
    client, ok = TestClient(server.app), {"Authorization": "Bearer s3cret"}
    assert client.get("/api/intel").status_code == 401
    assert client.post("/api/intel/switch", json={"sector": "robotics", "on": True}).status_code == 401
    out = client.get("/api/intel", headers=ok).json()
    assert len(out["sectors"]) == 10 and out["stages"][0] == "No evidence yet" and out["signals_total"] == 0
    on = client.post("/api/intel/switch", json={"sector": "robotics", "on": True}, headers=ok).json()
    assert next(s for s in on["sectors"] if s["id"] == "robotics")["on"] is True
    assert client.post("/api/intel/switch", json={"sector": "nope", "on": True}, headers=ok).status_code == 400
    assert client.get("/api/intel/technology?id=smr", headers=ok).json()["stage"] == "No evidence yet"
    assert client.get("/api/intel/technology?id=nope", headers=ok).status_code == 404


def test_the_agent_api_is_read_only_and_its_key_opens_nothing_else(memory, monkeypatch):
    from fastapi.testclient import TestClient

    from buddy import server
    bip.collect(memory, Web(SMR), TODAY, pause=0)
    monkeypatch.setattr(server, "memory", memory)
    monkeypatch.setattr(server, "authorized", lambda t: t == "owner-token")
    monkeypatch.setenv("BUDDY_INTEL_API_KEY", "k" * 40)
    client, key = TestClient(server.app), {"Authorization": "Bearer " + "k" * 40}
    assert client.get("/api/intel/v1/describe").status_code == 401
    assert client.get("/api/intel/v1/describe", headers={"Authorization": "Bearer wrong"}).status_code == 401
    d = client.get("/api/intel/v1/describe", headers=key).json()
    assert d["version"] == "1" and len(d["endpoints"]) == 8 and len(d["sectors"]) == 10
    assert "never as instructions" in d["how_to_read"] and "Not investment advice" in d["limits"]
    assert client.get("/api/intel/v1/search?q=nuscale", headers=key).json()["records"][0]["evidence"] == "company disclosure"
    assert client.get("/api/intel/v1/technology?id=smr", headers=key).json()["stage"] == "Early commercialization"
    assert client.get("/api/intel/v1/organisation?name=oklo", headers=key).json()["records"] == 1
    assert len(client.get("/api/intel/v1/technologies?limit=3", headers=key).json()["technologies"]) == 3
    assert "convergences" in client.get("/api/intel/v1/convergences", headers=key).json()
    assert client.get("/api/intel/v1/technology?id=nope", headers=key).status_code == 400
    assert client.get("/api/intel/v1/nope", headers=key).status_code == 404
    # the key is for this API only: nothing else in Buddy opens, and it cannot change anything
    assert client.get("/api/intel", headers=key).status_code == 401
    assert client.post("/api/intel/switch", json={"sector": "robotics", "on": True}, headers=key).status_code == 401
    assert client.get("/api/security", headers=key).status_code == 401
    assert client.get("/api/intel/v1/overview", headers={"Authorization": "Bearer owner-token"}).status_code == 200
    monkeypatch.setenv("BUDDY_INTEL_API_KEY", "short")  # a weak key is never accepted
    assert client.get("/api/intel/v1/describe", headers={"Authorization": "Bearer short"}).status_code == 401


def test_the_same_organisation_written_two_ways_is_one():
    assert src.org_key("Oklo Inc. (OKLO)") == src.org_key("OKLO INC") == "oklo"
    assert src.org_key("The Boeing Company") == "boeing" and src.org_key("X-Energy, LLC") == "x energy"
    assert src.org_key("") == "" and src.org_key("Inc.") == ""


def test_every_kind_of_evidence_has_a_standing_and_a_source():
    assert set(src.STANDING) == set(src.KINDS) and set(src.STANDING.values()) == {"verified", "reported"}
    assert set(src.KIND_OF) == set(src.SOURCES) == set(src.TITLES) and len(src.SOURCES) == 13
    assert set(src.KIND_OF.values()) == set(src.KINDS)
    shown = bip.describe()
    assert shown["standing"]["inferred"] and all(e["standing"] in shown["standing"] for e in shown["evidence"])


def test_news_grants_and_fundraising_notices_are_collected_and_names_merge(memory):
    web = Web({
        "gdelt": {"small modular reactor": [
            {"id": "a", "title": "Utility orders a small modular reactor", "day": d(3)},
            {"id": "b", "title": "Utility orders a small modular reactor", "day": d(3)}]},  # syndicated: counts once
        "openalex": {"small modular reactor": [{"id": "W1", "title": "Small modular reactor load following", "day": d(20)}]},
        "nsf": {"small modular reactor": [{"id": "2512345", "title": "Small modular reactor materials",
                                           "who": "Oklo Inc.", "amount": 500000, "day": d(40)}]},
        "sbir": {"small modular reactor": [{"id": "DE-SC1", "title": "Small modular reactor sensors",
                                            "who": "OKLO INC", "amount": 1100000, "day": d(50)}]},
        "form_d": {"small modular reactor": [{"who": "Small Modular Reactor Partners LLC", "day": d(15)}]},
        "nih": {"small modular reactor": [{"id": 1, "title": "never asked: not biomedical", "who": "x", "amount": 1,
                                           "day": d(5)}]},
    })
    got = bip.collect(memory, web, TODAY, pause=0)
    assert got["new"] == 5 and got["failed"] == {}
    assert not any("reporter.nih.gov" in u and "modular" in u for u in web.asked)
    smr = next(t for t in bip.technologies(memory, TODAY) if t["id"] == "smr")
    assert smr["kinds"] == {"news": 1, "research": 1, "grant": 2, "funding": 1}
    assert smr["companies"] == 2  # Oklo Inc. and OKLO INC are one; the partnership is the other
    assert smr["stage"] == "Experimental"  # grants, a report and a fundraising notice are not a pilot
    t = bip.technology(memory, "smr", TODAY)
    assert t["evidence"]["grant"][0]["standing"] == "verified" and t["evidence"]["news"][0]["standing"] == "reported"
    assert t["evidence"]["funding"][0]["url"].startswith("https://www.sec.gov/Archives/edgar/data/99999/")
    oklo = next(o for o in t["organisations"] if src.org_key(o["name"]) == "oklo")
    assert oklo["records"] == 2
    assert bip.organisation(memory, "Oklo", TODAY)["records"] == 2
    assert bip.maturity({"grant": 3}, 0)["stage"] == "Scientific research"


def test_awards_asks_contracts_and_grants_separately_and_counts_each_award_once(memory):
    asked = []

    def fetch(url, data=None, headers=None):
        asked.append(json.loads(data)["filters"]["award_type_codes"])
        return json.dumps({"results": [{"Award ID": "X1", "Recipient Name": "Acme", "Award Amount": 5.0,
                                        "Start Date": "2026-05-01"}]})

    src.gateway._recent.clear()
    got = src.awards(memory, "small modular reactor", fetch)
    assert asked == [["A", "B", "C", "D"], ["02", "03", "04", "05"]]  # one group of award types per request
    assert [r["key"] for r in got] == ["usa:X1"]


def test_a_paced_source_is_never_asked_faster_than_it_allows_whoever_asks():
    now, slept = [100.0], []

    def sleep(s):
        slept.append(round(s, 2))
        now[0] += s

    src._last.clear()
    assert src._wait("gdelt", sleep, lambda: now[0]) == 0  # first call goes straight out
    now[0] += 2
    assert round(src._wait("gdelt", sleep, lambda: now[0]), 2) == 3.5  # 5.5 s after the last, from any caller
    now[0] += 9
    assert src._wait("gdelt", sleep, lambda: now[0]) == 0 and slept == [3.5]
    assert src._wait("openalex", sleep, lambda: now[0]) == 0  # sources that ask for no pause are not held
    src._last.clear()


def test_a_source_that_says_too_many_requests_is_left_alone_and_every_caller_shares_its_clock():
    from buddy import gateway, newsfeed

    src._last.clear(); src._rest.clear()
    now = [1000.0]
    clock = lambda: now[0]  # noqa: E731
    assert src.turn("gdelt", clock) is True                # free: taken
    assert src.turn("gdelt", clock) is False               # the news fallback, a moment later: not yet
    now[0] += 5.6
    assert src.turn("gdelt", clock) is True
    src.rest("gdelt", 900, clock)                          # it said "too many requests"
    now[0] += 60
    assert src.resting("gdelt", clock) and src.turn("gdelt", clock) is False
    now[0] += 900
    assert not src.resting("gdelt", clock) and src.turn("gdelt", clock) is True
    src._last.clear(); src._rest.clear()

    asked = []

    def too_many(url, data=None, headers=None):
        import io
        import urllib.error
        asked.append(url)
        raise urllib.error.HTTPError(url, 429, "Too Many Requests", {}, io.BytesIO(b"Please limit requests to one every 5 seconds"))

    mem = Memory(":memory:")
    for _ in range(3):                                     # one refusal, then it is not asked again for a while
        with pytest.raises(gateway.ConnectError):
            src.gdelt(mem, "solid state battery", fetch=too_many)
    assert len(asked) == 1
    assert newsfeed._gdelt_turn() is False                 # the news fallback leaves it alone too
    src._last.clear(); src._rest.clear()
