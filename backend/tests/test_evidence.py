"""The research digest: the owner's topics as plain words, PubMed's papers kept and ranked by evidence, abstracts
parsed safely, once a week and at most hourly on request."""

import json
import urllib.parse
from datetime import date

import pytest
from fastapi.testclient import TestClient

from buddy import evidence
from buddy.memory import Memory
from buddy.tools import ToolError

SUMMARY = {"result": {"uids": ["111", "222", "333"],
                      "111": {"title": "A <i>trial</i> of walking", "fulljournalname": "GeroScience", "pubdate": "2026 Oct 3",
                              "pubtype": ["Journal Article", "Randomized Controlled Trial"],
                              "authors": [{"name": "Rao K"}], "articleids": [{"idtype": "doi", "value": "10.1/x"}]},
                      "222": {"title": "Walking and ageing: a meta-analysis", "fulljournalname": "Nature aging",
                              "pubdate": "2026 Sep", "pubtype": ["Journal Article", "Meta-Analysis"], "authors": []},
                      "333": {"title": "", "pubtype": []}}}
ABSTRACT = ('<?xml version="1.0" ?>\n<!DOCTYPE PubmedArticleSet PUBLIC "-//NLM//DTD PubMedArticle, 1st January 2025//EN" '
            '"https://dtd.nlm.nih.gov/ncbi/pubmed/out/pubmed_250101.dtd">\n<PubmedArticleSet><PubmedArticle><MedlineCitation>'
            '<Article><Abstract><AbstractText Label="BACKGROUND">Why it <i>matters</i>.</AbstractText>'
            '<AbstractText Label="RESULTS">It   helped.</AbstractText></Abstract></Article></MedlineCitation>'
            '</PubmedArticle></PubmedArticleSet>')


class Fake:
    def __init__(self):
        self.urls = []

    def __call__(self, url, data=None, headers=None):
        self.urls.append(url)
        if "esearch" in url:
            return json.dumps({"esearchresult": {"idlist": ["111", "222", "333", "bad"]}})
        if "esummary" in url:
            return json.dumps(SUMMARY)
        return ABSTRACT


def test_topics_are_plain_words_and_filters_are_added(tmp_path):
    m = Memory(tmp_path / "b.db")
    s = evidence.choose(m, ["longevity", "made-up"], ['creatine OR "x"[Journal]', "ab", "creatine or x journal"])
    assert s["presets"] == ["longevity"] and s["custom"] == ["creatine OR x Journal"]  # brackets and quotes gone
    q = evidence.query("(creatine)", evidence.settings(m))
    assert evidence.STRONG in q and q.endswith(evidence.PEOPLE)
    loose = evidence.choose(m, [], ["sleep"], strong=False, people=False)
    assert evidence.query("(sleep)", loose) == "(sleep)"
    with pytest.raises(ToolError, match="five"):
        evidence.choose(m, [], ["one", "two", "three", "four", "five", "six"])


def test_papers_are_kept_and_ranked_by_evidence(tmp_path):
    m = Memory(tmp_path / "b.db")
    evidence.choose(m, ["longevity"], ["walking"])
    fake = Fake()
    got = evidence.refresh(m, fetch=fake)
    assert got == {"found": 4, "new": 4, "days": 90}  # two topics, two good papers each; 90 days the first time
    search = urllib.parse.parse_qs(urllib.parse.urlparse(fake.urls[2]).query)
    assert search["term"][0].startswith("(walking) AND") and search["tool"] == ["buddy"] and "email" not in search
    assert evidence.refresh(m, fetch=Fake())["days"] == 30 and evidence.refresh(m, fetch=Fake())["new"] == 0
    topics = {t["key"]: t for t in evidence.view(m)["topics"]}
    papers = topics["own:walking"]["papers"]
    assert [p["pmid"] for p in papers] == ["222", "111"]  # the meta-analysis before the trial
    assert papers[0]["evidence"] == "Meta-analysis or systematic review" and papers[1]["title"] == "A trial of walking"
    assert papers[1]["author"] == "Rao K" and papers[1]["doi"] == "10.1/x" and papers[0]["new"]
    assert papers[0]["url"] == "https://pubmed.ncbi.nlm.nih.gov/222/"
    evidence.choose(m, ["longevity"], [])  # a topic dropped: its papers go
    assert m.db.execute("SELECT COUNT(*) FROM evidence_papers WHERE topic = 'own:walking'").fetchone()[0] == 0


def test_an_abstract_is_parsed_and_entities_refused(tmp_path):
    m = Memory(tmp_path / "b.db")
    got = evidence.abstract(m, "111", fetch=Fake())
    assert got["sections"] == [{"label": "Background", "text": "Why it matters."}, {"label": "Results", "text": "It helped."}]
    with pytest.raises(ToolError, match="unexpected"):
        evidence.abstract(m, "111", fetch=lambda *a, **k: '<!DOCTYPE x [<!ENTITY a "b">]><x>&a;</x>')
    with pytest.raises(ToolError, match="PubMed number"):
        evidence.abstract(m, "1 OR 2", fetch=Fake())


def test_weekly_on_sunday_and_at_most_hourly_on_request(tmp_path, monkeypatch):
    m = Memory(tmp_path / "b.db")
    monkeypatch.setattr(evidence, "local_now", lambda: __import__("datetime").datetime(2026, 11, 29, 3, 5))  # a Sunday
    assert not evidence.due(m)  # no topics yet
    evidence.choose(m, ["sleep"], [])
    assert evidence.due(m)
    evidence.refresh(m, fetch=Fake())
    assert not evidence.due(m)  # once that Sunday
    evidence.refresh_now(m, fetch=Fake())
    with pytest.raises(ToolError, match="within the hour"):
        evidence.refresh_now(m, fetch=Fake())
    assert evidence.rank(["Practice Guideline"])[0] == 0 and evidence.rank(["Letter"]) == (5, "Other")
    assert evidence._sort_date("2026 Oct 3", "x") == "2026-10-03" and evidence._sort_date("", "2026-01-01") == "2026-01-01"
    assert date.fromisoformat(evidence._sort_date("2026 Sep", "x"))


def test_the_page_endpoints(tmp_path, monkeypatch):
    from buddy import server
    monkeypatch.setattr(server, "memory", Memory(tmp_path / "b.db"))
    monkeypatch.setattr(server, "authorized", lambda t: t == "s3cret")
    monkeypatch.setattr(evidence, "refresh", lambda memory, **k: {"found": 0, "new": 0, "days": 90})
    client, auth = TestClient(server.app), {"Authorization": "Bearer s3cret"}
    out = client.post("/api/health/research", json={"action": "topics", "presets": ["sleep"], "custom": ["creatine"]},
                      headers=auth).json()
    assert out["text"] == "Nothing new in the last 90 days." and [t["key"] for t in out["topics"]] == ["sleep", "own:creatine"]
    assert client.get("/api/health/research?pmid=abc", headers=auth).status_code == 400
    assert client.post("/api/health/research", json={"action": "nope"}, headers=auth).status_code == 400
