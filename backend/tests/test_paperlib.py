"""The paper library: every paper from every source kept once, with what each source knew, until the owner deletes
it; the digest's abstracts kept too."""

import json
import urllib.parse

from fastapi.testclient import TestClient

from buddy import evidence, freshness, paperlib
from buddy.memory import Memory


def test_a_paper_is_known_by_its_number_whatever_the_link():
    assert paperlib.ids("https://pubmed.ncbi.nlm.nih.gov/37947085/")["pmid"] == "37947085"
    assert paperlib.ids("https://europepmc.org/article/MED/37947085")["pmid"] == "37947085"
    assert paperlib.ids("http://arxiv.org/abs/2401.01234v3")["arxiv"] == "2401.01234"
    assert paperlib.ids("https://doi.org/10.1161/CIRCULATIONAHA.123.067626")["doi"] == "10.1161/circulationaha.123.067626"
    assert paperlib.ids("https://openalex.org/W4389")["openalex"] == "W4389"
    assert paperlib.key_for(paperlib.ids("https://pubmed.ncbi.nlm.nih.gov/1/", doi="10.1/x")) == "pmid:1"
    assert paperlib.key_for(paperlib.ids("ftp://nope")) == ""


def test_found_twice_kept_once_and_filled_in_never_emptied(tmp_path):
    m = Memory(tmp_path / "b.db")
    k = paperlib.keep(m, "PREVENT <i>equations</i>", "PubMed", "Health research digest: heart", pmid="37947085")
    assert k == "pmid:37947085"
    paperlib.keep(m, "PREVENT equations", "Europe PMC", "Buddy Intelligence: prevent",
                  url="https://europepmc.org/article/MED/37947085", venue="Circulation", abstract="Background: ...")
    paperlib.keep(m, "PREVENT equations", "PubMed", "Health research digest: heart", pmid="37947085", venue="")
    got = paperlib.search(m)
    assert got["all"] == 1
    p = got["papers"][0]
    assert p["title"] == "PREVENT equations" and p["venue"] == "Circulation" and p["abstract"] == "Background: ..."
    assert p["url"] == "https://pubmed.ncbi.nlm.nih.gov/37947085/"
    assert [f["source"] for f in p["found_by"]] == ["PubMed", "Europe PMC"]
    assert got["counts"]["PubMed"] == 1 and got["counts"]["Europe PMC"] == 1 and got["counts"]["arXiv"] == 0


def test_search_by_words_and_source_and_delete(tmp_path):
    m = Memory(tmp_path / "b.db")
    paperlib.keep_items(m, [{"title": "Momentum in crypto", "url": "https://arxiv.org/abs/2401.00001", "summary": "We test"},
                            {"title": "Odd", "url": "javascript:alert(1)"}, "not a dict"], "arXiv", "Research desk")
    paperlib.keep(m, "Statins and Lp(a)", "PubMed", "digest", pmid="5", abstract="lipoprotein a in South Asians")
    assert paperlib.search(m)["all"] == 2  # the bad link and the non-paper were skipped, nothing failed
    assert [p["key"] for p in paperlib.search(m, q="south asians")["papers"]] == ["pmid:5"]
    assert [p["key"] for p in paperlib.search(m, source="arXiv")["papers"]] == ["arxiv:2401.00001"]
    assert paperlib.forget(m, "pmid:5") == 1 and paperlib.search(m)["all"] == 1
    assert paperlib.forget(m) == 1 and paperlib.search(m)["all"] == 0


def pubmed_fake(ids, xml_abstracts=True):
    def fetch(url, data=None, headers=None, method=None):
        q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        if "esearch" in url:
            return json.dumps({"esearchresult": {"idlist": ids}})
        if "esummary" in url:
            return json.dumps({"result": {i: {"title": f"Trial {i}", "pubdate": "2026 Nov", "fulljournalname": "JAMA",
                                              "pubtype": ["Randomized Controlled Trial"],
                                              "authors": [{"name": "Rao A"}]} for i in ids}})
        if "efetch" in url:
            arts = "".join(f"<PubmedArticle><MedlineCitation><PMID>{i}</PMID><Article><Abstract>"
                           f"<AbstractText Label='RESULTS'>Lower risk {i}.</AbstractText></Abstract></Article>"
                           f"</MedlineCitation></PubmedArticle>" for i in q["id"][0].split(","))
            return f"<?xml version='1.0'?><PubmedArticleSet>{arts}</PubmedArticleSet>".encode()
        raise AssertionError(url)
    return fetch


def test_the_digest_keeps_every_paper_with_its_abstract(tmp_path):
    m = Memory(tmp_path / "b.db")
    evidence.choose(m, ["prediabetes"], [], strong=True, people=True)
    evidence.refresh(m, env={}, fetch=pubmed_fake(["101", "102"]))
    got = paperlib.search(m)
    assert got["all"] == 2 and {p["abstract"] for p in got["papers"]} == {"Results: Lower risk 101.",
                                                                        "Results: Lower risk 102."}
    p = next(p for p in got["papers"] if p["key"] == "pmid:101")
    assert p["venue"] == "JAMA" and p["kinds"] == ["Randomized Controlled Trial"] and p["authors"] == "Rao A"
    assert p["found_by"][0]["why"].startswith("Health research digest: ")
    evidence.choose(m, [], ["sleep"], strong=True, people=True)  # the topic is dropped: the library still has them
    assert paperlib.search(m)["all"] == 2


def test_the_freshness_watch_papers_are_kept(tmp_path):
    m = Memory(tmp_path / "b.db")
    papers = [{"pmid": "7", "title": "Screening for X: USPSTF Recommendation Statement", "date": "2027 Jan",
               "url": "https://pubmed.ncbi.nlm.nih.gov/7/"}]
    freshness.WATCHES, old = [("t", "Test", "x", lambda memory, env=None, fetch=None: {"status": "changed",
                                                                                    "papers": papers})], freshness.WATCHES
    try:
        freshness.run(m, env={})
    finally:
        freshness.WATCHES = old
    assert paperlib.search(m)["papers"][0]["found_by"][0]["why"] == "Freshness watch: Test"


def test_the_endpoints(tmp_path, monkeypatch):
    from buddy import server
    m = Memory(tmp_path / "b.db")
    paperlib.keep(m, "A paper", "arXiv", "Trends: ai", url="https://arxiv.org/abs/2401.00002")
    monkeypatch.setattr(server, "memory", m)
    monkeypatch.setattr(server, "authorized", lambda t: t == "s3cret")
    client, auth = TestClient(server.app), {"Authorization": "Bearer s3cret"}
    assert client.get("/api/papers?q=paper", headers=auth).json()["total"] == 1
    assert client.get("/api/papers?source=Nope", headers=auth).status_code == 400
    assert client.post("/api/papers", json={"action": "forget", "key": "arxiv:2401.00002"}, headers=auth).json() == {
        "deleted": 1}
    assert client.post("/api/papers", json={"action": "nope"}, headers=auth).status_code == 400
