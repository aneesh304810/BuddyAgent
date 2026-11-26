"""The freshness watch: newer guidelines and data than Buddy's rules are flagged (never applied), screenings they
touch show as under review, the task force's own words are kept verbatim, and a newer Buddy is noticed read-only."""

import io
import json
import urllib.error
import urllib.parse

import pytest
from fastapi.testclient import TestClient

from buddy import freshness, prevention
from buddy.memory import Memory
from buddy.tools import ToolError, local_now


def pubmed(titles: dict[str, list[str]]):
    """A fake PubMed: the titles each query (picked by a word in it) returns."""
    def fetch(url, data=None, headers=None, method=None):
        q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        if "esearch" in url:
            found = next((t for word, t in titles.items() if word in q["term"][0]), [])
            return json.dumps({"esearchresult": {"idlist": [str(100 + i) for i in range(len(found))]}})
        if "esummary" in url:
            found = next((t for t in titles.values() if t), [])
            ids = q["id"][0].split(",")
            every = [t for ts in titles.values() for t in ts]
            return json.dumps({"result": {i: {"title": every[int(i) - 100] if int(i) - 100 < len(every) else found[0],
                                              "pubdate": "2027 Jan 5"} for i in ids}})
        if "/fdc/" in url:
            return json.dumps({"foods": [{"publishedDate": "2024-10-31"}]})
        raise urllib.error.HTTPError(url, 404, "Not Found", {}, io.BytesIO(b""))
    return fetch


def test_a_new_task_force_statement_puts_its_screening_under_review(tmp_path):
    m = Memory(tmp_path / "b.db")
    fetch = pubmed({"Task Force": ["Screening for Breast Cancer: US Preventive Services Task Force Recommendation "
                                   "Statement.", "Primary Care Interventions to Support Breastfeeding: US Preventive "
                                   "Services Task Force Recommendation Statement."]})
    got = {w["key"]: w for w in freshness.run(m, env={}, fetch=fetch)["watches"]}
    assert got["uspstf"]["status"] == "changed" and got["uspstf"]["via"] == "PubMed"
    assert [p["items"] for p in got["uspstf"]["papers"]] == [["breast"], []]  # breastfeeding isn't breast cancer
    assert got["nhanes"]["status"] == "current" and got["fndds"]["status"] == "not_set_up"
    assert got["dga"]["detail"] == "The next edition is due in 2030."
    assert "breast" in freshness.reviews(m) and "after Buddy's rules were checked" in freshness.reviews(m)["breast"][0]
    prevention.save_profile(m, {"birth_year": local_now().year - 45, "sex": "female", "ancestry": ["indian_kerala"]})
    items = prevention.view(m)["items"]
    assert items[0]["key"] == "breast" and items[0]["status"] == "review" and items[0]["review"]


@pytest.mark.parametrize("title,changed", [
    ("9. Pharmacologic Approaches to Glycemic Treatment: Standards of Care in Diabetes-2027.", True),
    ("9. Pharmacologic Approaches to Glycemic Treatment: Standards of Care in Diabetes-2026.", False)])
def test_a_newer_ada_edition(tmp_path, title, changed):
    got = freshness.watch_edition(Memory(tmp_path / "b.db"), freshness.ADA_Q, "2026-06-01", freshness._ada_newer,
                                  "the 2026 Standards", fetch=pubmed({"Diabetes": [title]}))
    assert (got["status"] == "changed") is changed


def test_a_newer_cholesterol_guideline(tmp_path):
    title = "2028 ACC/AHA/NLA Guideline on the Management of Dyslipidemia: A Report of the ACC/AHA Joint Committee."
    got = freshness.watch_edition(Memory(tmp_path / "b.db"), freshness.LIPIDS_Q, "2026-06-01",
                                  freshness._lipids_newer, "the 2026 guideline", fetch=pubmed({"dyslipidemia": [title]}))
    assert got["status"] == "changed" and "2028" in got["detail"]
    assert not freshness._lipids_newer("2026 Dyslipidemia Guideline-at-a-Glance.")


def api_data(grade="B", year="2019"):
    return {"specificRecommendations": [
        {"id": 1, "title": "<b>Breast Cancer: Screening</b>", "grade": grade, "gradeVer": 1, "sex": "female",
         "ageRange": [40, 74], "text": "<p>The USPSTF recommends biennial screening mammography.</p>", "general": "1"},
        {"id": 2, "title": "Hypertension in Adults: Screening", "grade": "A", "gradeVer": 1, "sex": "",
         "ageRange": [18, 120], "text": "Screen for hypertension.", "general": "2"}],
        "generalRecommendations": {
            "1": {"uspstfAlias": "breast-cancer-screening", "topicYear": year, "title": "Breast Cancer: Screening"},
            "2": {"uspstfAlias": "hypertension-in-adults-screening", "topicYear": "2021",
                  "title": "Hypertension in Adults: Screening"}}}


def test_the_task_force_api_compares_its_data_and_keeps_its_words(tmp_path):
    m = Memory(tmp_path / "b.db")
    env, seen = {"BUDDY_USPSTF_KEY": "k-9"}, []

    def fetch_for(data):
        def fetch(url, data_=None, headers=None, method=None):
            seen.append(url)
            return json.dumps(data)
        return fetch
    first = freshness.watch_uspstf(m, env=env, fetch=fetch_for(api_data()))
    query = urllib.parse.parse_qs(urllib.parse.urlparse(seen[0]).query)
    assert query == {"key": ["k-9"]}  # the whole data set: no age, sex or anything else about the owner
    assert first["status"] == "current" and "2 topics" in first["detail"]
    assert freshness.watch_uspstf(m, env=env, fetch=fetch_for(api_data()))["status"] == "current"
    changed = freshness.watch_uspstf(m, env=env, fetch=fetch_for(api_data(grade="A")))
    assert changed["status"] == "changed" and changed["papers"][0]["items"] == ["breast"]
    newer = freshness.watch_uspstf(Memory(tmp_path / "c.db"), env=env, fetch=fetch_for(api_data(year="2027")))
    assert newer["status"] == "changed"  # newer than the date Buddy's rules were checked
    words = freshness.words(m)["breast"][0]
    assert words == {"title": "Breast Cancer: Screening", "grade": "A",
                     "text": "The USPSTF recommends biennial screening mammography."}


def test_a_newer_usda_release_and_the_next_nhanes_cycle(tmp_path):
    m = Memory(tmp_path / "b.db")
    newer = freshness.watch_fndds(m, env={"BUDDY_USDA_KEY": "k"},
                                  fetch=lambda url, *a: json.dumps({"foods": [{"publishedDate": "2026-10-31"}]}))
    assert newer["status"] == "changed" and "2026-10-31" in newer["detail"]
    out = freshness.watch_nhanes(m, fetch=lambda url, *a: b"")
    assert out["status"] == "changed"


def test_a_newer_buddy_is_noticed_without_changing_anything(tmp_path):
    m = Memory(tmp_path / "b.db")
    answers = {("rev-parse", "HEAD"): "a" * 40, ("rev-parse", "--abbrev-ref", "HEAD"): "main",
               ("log", "-1", "--format=%cs"): "2026-11-20", ("ls-remote", "--heads", "origin", "main"):
               "b" * 40 + "\trefs/heads/main"}

    def git(*args):
        if args[0] == "merge-base":
            raise ToolError("not an ancestor")
        return answers[args]
    got = freshness.update(m, git=git)
    assert got["status"] == "changed" and got["command"].endswith("sudo bash deploy/update.sh")
    answers[("ls-remote", "--heads", "origin", "main")] = "a" * 40 + "\trefs/heads/main"
    assert freshness.update(m, git=git)["status"] == "current"

    def broken(*args):
        raise ToolError("could not read from remote repository")
    assert freshness.update(m, git=broken)["status"] == "unknown"
    assert not freshness.update_due(m)


def test_check_now_at_most_hourly_and_the_endpoints(tmp_path, monkeypatch):
    from buddy import server
    m = Memory(tmp_path / "b.db")
    monkeypatch.setattr(freshness, "run", lambda memory, env=None, fetch=None: {"watches": []})
    monkeypatch.setattr(freshness, "update", lambda memory, git=None: {"status": "current"})
    monkeypatch.setattr(server, "memory", m)
    monkeypatch.setattr(server, "authorized", lambda t: t == "s3cret")
    client, auth = TestClient(server.app), {"Authorization": "Bearer s3cret"}
    assert client.post("/api/freshness", json={}, headers=auth).status_code == 200
    again = client.post("/api/freshness", json={}, headers=auth)
    assert again.status_code == 400 and "within the hour" in again.json()["detail"]
    assert "no AI model" in client.get("/api/freshness", headers=auth).json()["note"]
