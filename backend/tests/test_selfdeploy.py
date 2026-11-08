"""Self-deploy, Buddy's side: a fix is reviewed first, and only the owner's ask plus a yes push and deploy it."""

import asyncio
import json

from buddy import selfdeploy
from buddy.memory import Memory


class Jobs:
    def __init__(self, jobs):
        self.jobs = {j["id"]: j for j in jobs}

    def get(self, job_id):
        return self.jobs.get(job_id)


class Thrift:
    def __init__(self):
        self.asked = []

    def ask_action(self, question, run):
        self.asked.append((question, run))
        return question


def test_a_fix_waits_for_review_then_one_yes_pushes_and_requests_the_deploy(tmp_path, monkeypatch):
    monkeypatch.setattr(selfdeploy, "DIR", tmp_path)
    (tmp_path / "flag").write_text("REPO_URL=x\nBASE=main\n")
    monkeypatch.setattr(selfdeploy, "FLAG", tmp_path / "flag")
    m = Memory(":memory:")
    job = {"id": 7, "title": "Stop the briefing retry loop", "report":
           "# x\n\n## What I did\nWhat's new: briefings are written at most 3 times a day.\n"
           "How to test it yourself: 1. Say 'brief me' ...\n\n## Changes\n```diff\n...```"}
    selfdeploy.mark_ready(m, job)
    review = selfdeploy.review_text(job)
    assert "What's new" in review and "How to test it yourself" in review and "diff" not in review

    pushed = []

    async def push(j):
        pushed.append(j["id"])
        return "Pushed to the buddy/self-7 branch. You can open a pull request from GitHub."
    t = Thrift()
    q = selfdeploy.ship(m, Jobs([job]), t, push)
    assert "Push, merge and deploy" in q and pushed == []  # asked, nothing done yet
    said = asyncio.run(t.asked[0][1]())  # the owner's yes
    assert pushed == [7] and "Asked the server to deploy buddy/self-7" in said
    assert json.loads((tmp_path / "request.json").read_text())["branch"] == "buddy/self-7"
    assert selfdeploy.ready(m) == []

    (tmp_path / "result.json").write_text(json.dumps({"status": "rolled_back", "message": "Buddy wasn't healthy."}))
    assert selfdeploy.take_result().startswith("My fix is rolled back") and selfdeploy.take_result() == ""


def test_only_buddy_branches_and_nothing_without_a_waiting_fix(tmp_path, monkeypatch):
    monkeypatch.setattr(selfdeploy, "DIR", tmp_path)
    monkeypatch.setattr(selfdeploy, "FLAG", tmp_path / "flag")
    (tmp_path / "flag").write_text("x")
    assert "isn't a buddy" in selfdeploy.request("main")
    assert "no finished fix" in selfdeploy.ship(Memory(":memory:"), Jobs([]), Thrift(), None)
