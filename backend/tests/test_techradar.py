"""Technology that matters for Buddy, and the AI security section: gathered, sorted, verified in code, remembered."""

import asyncio
import json
from datetime import date, timedelta

import pytest

from buddy import techradar as tr
from buddy.connectors.hub import ConnectorHub
from buddy.memory import Memory
from buddy.tools import ToolError

TODAY = date(2026, 11, 16)
BRAIN, FREE = tr.features()[0][0], tr.features()[1][0]


@pytest.fixture
def memory(tmp_path):
    m = Memory(tmp_path / "buddy.db")
    ConnectorHub(m)
    return m


class News:
    """Stands in for trends.py: one saved AI topic, an extra search per section, and GitHub's repo facts."""

    def __init__(self, memory, extra=None, security=None, repos=None):
        self.memory, self.extra, self.security, self.repos, self.asked = memory, extra or [], security or [], repos or {}, []

    def topics(self):
        return [{"name": "AI", "kind": "ai", "query": "x"}, {"name": "US markets", "kind": "markets", "query": "y"}]

    def collect(self, topic, days=1):
        return {"stories": self.security if "Security" in topic["name"] else self.extra}

    def _json(self, provider, url, headers=None):
        self.asked.append(url)
        return self.repos[url.rsplit("/", 1)[-1]]


def story(title, url="", sources=("Hacker News",), take=""):
    return {"title": title, "url": url, "sources": list(sources), "take": take}


def answer(*picked):
    """A model that says each picked story (matched by a word in its title) matters; the rest don't."""
    async def ask(memory, prompt):
        listing = json.loads(prompt.split("Stories:\n")[1].split("\nAnswer with")[0])
        out = []
        for row in listing:
            hit = next((p for p in picked if p["match"] in row["title"]), None)
            out.append({"n": row["n"], "relevance": 0} if not hit else
                       {"n": row["n"], **{k: v for k, v in hit.items() if k != "match"}})
        return {"items": out}
    return ask


VOICE = {"match": "fastwhisper", "relevance": 3, "area": "voice and speech", "feature": BRAIN, "kind": "library",
         "what": "An open speech recogniser that runs on a small server.",
         "idea": "Buddy could transcribe Talk mode on its own server instead of a paid service.", "effort": "medium",
         "caution": ""}
REPO = {"full_name": "acme/fastwhisper", "license": {"spdx_id": "MIT"}, "archived": False,
        "pushed_at": "2026-11-10T00:00:00Z", "stargazers_count": 4200}


def test_verification_is_decided_in_code():
    code = {"url": "https://github.com/acme/fastwhisper", "sources": ["GitHub"]}
    live = {"licence": "mit", "archived": False, "pushed": "2026-11-10", "stars": 4200}
    assert tr.verify(code, live, TODAY)[0] == "verified" and "4,200 stars" in tr.verify(code, live, TODAY)[1]
    assert tr.verify(code, {**live, "licence": ""}, TODAY) == (
        "partly", "code on GitHub with 4,200 stars, last changed 2026-11-10, licence none stated: no open licence, so "
                  "it can't simply be used; seen in GitHub only")
    assert tr.verify(code, {**live, "archived": True}, TODAY)[0] == "unverified"
    assert tr.verify(code, {**live, "pushed": "2024-01-01"}, TODAY)[0] == "partly"
    assert tr.verify({"url": "https://arxiv.org/abs/1", "sources": ["arXiv"]}, None, TODAY)[0] == "partly"
    assert tr.verify({"url": "https://www.anthropic.com/news/x", "sources": ["Hacker News", "Reddit"]}, None, TODAY)[0] == "verified"
    assert tr.verify({"url": "https://blog.example/x", "sources": ["Reddit"]}, None, TODAY) == (
        "unverified", "seen in Reddit only; no first-hand source")
    assert tr.verify({"url": "https://blog.example/x", "sources": ["a", "b", "c"]}, None, TODAY)[0] == "verified"


def test_a_night_keeps_what_matters_for_buddy_with_what_it_could_do(memory):
    memory.set("trends:brief:AI", json.dumps({"stories": [story("OpenAI raises another round", "https://news.example/1")]}))
    memory.set("trends:brief:US markets", json.dumps({"stories": [story("Fed holds rates")]}))
    news = News(memory, extra=[story("acme/fastwhisper: speech recognition on a small server",
                                     "https://github.com/acme/fastwhisper", ("GitHub", "Hacker News"))],
                repos={"fastwhisper": REPO})
    got = asyncio.run(tr.run(memory, news, answer(VOICE), TODAY))
    assert got["read"] == 2 and got["kept"] == 1 and got["new"] == ["acme/fastwhisper: speech recognition on a small server"]
    page = tr.page(memory)
    item = page["items"][0]
    assert item["verified"] == "verified" and "licence mit" in item["evidence"] and item["feature"] == BRAIN
    assert item["idea"].startswith("Buddy could transcribe") and item["effort"] == "medium" and item["nights"] == 1
    assert page["by_area"] == {"voice and speech": 1} and "nothing is installed" in page["note"]
    assert news.asked == ["https://api.github.com/repos/acme/fastwhisper"]
    assert "new technology idea for Buddy" in tr.message(got)
    again = asyncio.run(tr.run(memory, news, answer(VOICE), TODAY + timedelta(days=1)))
    assert again["new"] == [] and tr.page(memory)["items"][0]["nights"] == 2 and tr.message(again) == ""


def test_what_the_model_may_not_say_is_dropped(memory):
    stories = [story("a"), story("b"), story("c"), story("d")]
    kept = tr.clean([{"n": 0, "relevance": 3, "feature": "A feature Buddy doesn't have", "idea": "do something clever"},
                     {"n": 1, "relevance": 1, "feature": BRAIN, "idea": "loosely related only"},
                     {"n": 9, "relevance": 3, "feature": BRAIN, "idea": "a story that isn't there"},
                     {"n": 2, "relevance": 3, "feature": BRAIN.upper(), "idea": "use it for faster answers", "area": "x",
                      "kind": "magic", "effort": "huge"}, "junk"], stories)
    assert len(kept) == 1 and kept[0]["title"] == "c" and kept[0]["feature"] == BRAIN
    assert kept[0]["area"] == "other" and kept[0]["kind"] == "" and kept[0]["effort"] == ""


def test_a_single_second_hand_story_waits_until_it_comes_up_again(memory):
    news = News(memory, extra=[story("fastwhisper beats everything, says a blog", "https://blog.example/x", ("Reddit",))])
    got = asyncio.run(tr.run(memory, news, answer(VOICE), TODAY))
    assert got["kept"] == 1 and got["new"] == [] and tr.page(memory)["items"] == []  # unverified: not shown
    asyncio.run(tr.run(memory, news, answer(VOICE), TODAY + timedelta(days=1)))
    shown = tr.page(memory)["items"]
    assert len(shown) == 1 and shown[0]["verified"] == "unverified" and shown[0]["nights"] == 2


def test_rating_keeps_interested_first_and_dismissed_teaches_the_next_night(memory):
    news = News(memory, extra=[story("acme/fastwhisper: speech", "https://github.com/acme/fastwhisper", ("GitHub",)),
                               story("Anthropic ships a tool", "https://www.anthropic.com/news/t", ("Hacker News", "Reddit"))],
                repos={"fastwhisper": REPO})
    tool = {**VOICE, "match": "Anthropic", "area": "agents and tools", "idea": "Buddy could use the new tool for tasks."}
    asyncio.run(tr.run(memory, news, answer(VOICE, tool), TODAY))
    ids = {i["title"][:9]: i["id"] for i in tr.page(memory)["items"]}
    assert "plan it" in tr.rate(memory, ids["Anthropic"], "interested")
    assert tr.page(memory)["items"][0]["title"].startswith("Anthropic")
    assert "leave similar ones out" in tr.rate(memory, ids["acme/fast"], "dismissed")
    assert [i["title"][:9] for i in tr.page(memory)["items"]] == ["Anthropic"]
    seen = {}

    async def ask(memory, prompt):
        seen["prompt"] = prompt
        return {"items": []}
    asyncio.run(tr.run(memory, news, ask, TODAY + timedelta(days=1)))
    assert "acme/fastwhisper: speech" in seen["prompt"].split("not for Buddy")[1].split("Stories:")[0]
    with pytest.raises(ToolError):
        tr.rate(memory, 999, "interested")
    with pytest.raises(ToolError):
        tr.rate(memory, ids["Anthropic"], "installed")


INJECT = {"match": "calendar invite", "relevance": 3, "area": "security and privacy", "feature": BRAIN,
          "threat": "An attacker hides instructions in a calendar invite so the assistant acts on them.",
          "defence": 1, "what": "Researchers showed hidden instructions in invites steering an AI assistant.",
          "idea": "Confirm calendar text is marked as untrusted like email is.", "effort": "small", "caution": ""}
VOICECLONE = {"match": "voice clone", "relevance": 3, "area": "voice and speech", "feature": BRAIN,
              "threat": "An attacker clones the owner's voice to approve actions by speech.", "defence": 0,
              "what": "Cheap voice cloning now passes speaker checks.",
              "idea": "Require a tap, not speech, for high-risk approvals.", "effort": "medium", "caution": ""}


def test_the_security_section_matches_threats_to_defences_and_puts_gaps_first(memory):
    news = News(memory, security=[
        story("Hidden text in a calendar invite hijacks AI assistants", "https://sec.example/1", ("Hacker News", "Reddit", "Google News")),
        story("A voice clone fooled a bank's speaker check", "https://sec.example/2", ("Hacker News", "Google News")),
        story("Security vendor raises funding", "https://sec.example/3", ("Google News",))])
    got = asyncio.run(tr.run(memory, news, answer(INJECT, VOICECLONE), TODAY))
    assert got["new_gaps"] == ["A voice clone fooled a bank's speaker check"] and len(got["new"]) == 2
    sec = tr.page(memory)["security"]
    assert [i["gap"] for i in sec["items"]] == [True, False] and sec["gaps"] == 1 and sec["covered"] == 1
    gap, covered = sec["items"]
    assert gap["threat"].startswith("An attacker clones") and gap["defence"] == "" and "tap, not speech" in gap["idea"]
    assert covered["defence"] == tr.DEFENCES[0] and covered["verified"] == "verified"
    assert tr.page(memory)["items"] == []  # security stories never leak into the technology list
    text = tr.message(got)
    assert "no defence listed" in text and "A voice clone fooled" in text
    assert "not proof Buddy is exposed" in sec["note"] and len(sec["defences"]) == len(tr.DEFENCES)


def test_a_security_item_needs_a_threat_and_a_real_defence_number():
    stories = [story("x"), story("y")]
    kept = tr.clean([{"n": 0, "relevance": 3, "feature": BRAIN, "idea": "check the thing holds", "threat": "",
                      "defence": 1},
                     {"n": 1, "relevance": 2, "feature": BRAIN, "idea": "check the thing holds",
                      "threat": "An attacker reads the data in transit.", "defence": 99}], stories, "security")
    assert len(kept) == 1 and kept[0]["defence"] == "" and kept[0]["threat"].startswith("An attacker")


def test_one_section_failing_does_not_lose_the_other_and_the_page_endpoint_works(memory, monkeypatch):
    news = News(memory, extra=[story("Anthropic ships a tool", "https://www.anthropic.com/news/t", ("Hacker News", "Reddit"))])

    async def ask(memory, prompt):
        if "defences Buddy already has" in prompt:
            raise RuntimeError("model down")
        return await answer({**VOICE, "match": "Anthropic"})(memory, prompt)
    news.security = [story("An attack", "https://sec.example/1", ("a", "b"))]
    got = asyncio.run(tr.run(memory, news, ask, TODAY))
    assert got["kept"] == 1 and len(tr.page(memory)["items"]) == 1 and tr.page(memory)["security"]["items"] == []
    assert tr.due(memory) in (True, False)
    from fastapi.testclient import TestClient

    from buddy import server
    monkeypatch.setattr(server, "memory", memory)
    monkeypatch.setattr(server, "authorized", lambda t: t == "s3cret")
    client = TestClient(server.app)
    assert client.post("/api/news/tech", json={"id": 1, "status": "interested"}).status_code == 401
    item = tr.page(memory)["items"][0]
    out = client.post("/api/news/tech", json={"id": item["id"], "status": "interested"},
                      headers={"Authorization": "Bearer s3cret"}).json()
    assert out["tech"]["items"][0]["status"] == "interested" and "plan it" in out["text"]
    assert "tech" in client.get("/api/news", headers={"Authorization": "Bearer s3cret"}).json()
