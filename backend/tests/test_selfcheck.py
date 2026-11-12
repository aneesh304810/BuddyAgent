"""Buddy's self-check: its own log grouped by cause (call words never read, numbers masked), the server's state, a
resize suggested (never done), and a diagnosis only for new problems."""

import json
from types import SimpleNamespace

from buddy import selfcheck
from buddy.memory import Memory

LOG = [
    "2026-11-12 16:01:14,614 buddy.calls call 37: audio loop held up 0.20 s (the voice stutters when this happens)",
    "2026-11-12 16:01:45,100 buddy.calls call 37: audio loop held up 0.21 s (the voice stutters when this happens)",
    "2026-11-12 16:01:08,939 buddy.calls call 37 heard: my card is 4111 1111 1111 1111, call Priya at 201-555-0123",
    "2026-11-12 15:59:47,000 buddy.mailbrain email manager: sorting failed; trying again later",
    "Traceback (most recent call last):",
    '  File "/opt/buddy/backend/buddy/mailbrain.py", line 270, in sort_new',
    "    verdicts = await self._classify(emails)",
    "buddy.budgets.BudgetStop: Background AI work is paused until you approve this month's pace.",
    "buddy.service: A process of this unit has been killed by the OOM killer.",
    "buddy.service: Failed with result 'oom-kill'.",
    "Traceback (most recent call last):",
    '  File "/opt/buddy/backend/buddy/dining.py", line 40, in search',
    "KeyError: 'places' for aneesh@example.com",
    "INFO:     100.71.142.72:0 - \"GET /health HTTP/1.1\" 200 OK",
]


def _run(lines):
    def run(cmd, **kw):
        assert cmd[:3] == ["journalctl", "-u", "buddy"]
        out = "\n".join(json.dumps({"MESSAGE": m, "__REALTIME_TIMESTAMP": str(1762963200000000 + i)})
                        for i, m in enumerate(lines))
        return SimpleNamespace(stdout=out)
    return run


def test_problems_are_grouped_by_cause_and_call_words_never_read():
    found = selfcheck.problems(selfcheck.read_log(run=_run(LOG)))
    kinds = [p["kind"] for p in found]
    assert kinds[:2] == ["out of memory", "out of memory"]  # the kill and the service result
    stutter = next(p for p in found if p["kind"] == "call audio stutter")
    assert stutter["count"] == 2  # "0.20 s" and "0.21 s": one cause
    budget = next(p for p in found if p["kind"] == "budget pause")
    assert budget["where"] == "buddy/mailbrain.py:sort_new"
    err = next(p for p in found if p["where"] == "buddy/dining.py:search")
    assert "<email>" in err["sample"] and "aneesh" not in err["sample"]
    text = json.dumps(found)
    assert "4111" not in text and "Priya" not in text and "555-0123" not in text  # the debug call line is skipped
    assert "GET /health" not in text


def test_check_diagnoses_only_new_problems_and_suggests_a_resize(tmp_path, monkeypatch):
    m = Memory(tmp_path / "b.db")
    monkeypatch.setattr(selfcheck, "server_now", lambda: {"memory_gb": 1.9, "mem_used_pct": 96, "swap_gb": 0,
                                                          "swap_used_pct": None, "load_per_cpu": 0.4, "cpus": 1,
                                                          "disk_used_pct": 40, "disk_free_gb": 30})
    asked = []

    def create(**kw):
        asked.append(json.loads(kw["messages"][0]["content"]))
        assert kw["thinking"] == {"type": "disabled"}
        sig = asked[-1]["problems"][0]["signature"]
        return SimpleNamespace(content=[SimpleNamespace(text=json.dumps({"problems": [
            {"signature": sig, "meaning": "Buddy ran out of memory and was restarted.", "fix": "Resize to 4 GB.",
             "code_fix": False, "brief": ""}]}))])
    drop = lambda url: {"droplets": [{"name": "other", "size_slug": "s-1vcpu-2gb", "size": {"memory": 2048,
                                       "vcpus": 1, "price_monthly": 12}, "region": {"slug": "nyc3"}}]}
    report = selfcheck.check(m, run=_run(LOG), create=create, env={"BUDDY_DO_TOKEN": "t"}, fetch=drop)
    assert report["droplet"]["memory_mb"] == 2048 and report["problems"][0]["fix"] == "Resize to 4 GB."
    assert any("Resize the droplet" in a and "$12/month" in a for a in report["advice"])
    assert any("no swap" in a for a in report["advice"])
    text, urgent = selfcheck.message(report)
    assert urgent and "ran out of memory" in text
    for p in report["problems"]:  # told: the next check doesn't ask Claude again
        pass
    m.set(selfcheck.TOLD_KEY, json.dumps({p["signature"]: report["at"] for p in report["problems"]}))
    again = selfcheck.check(m, run=_run(LOG), create=create, env={})
    assert len(asked) == 1 and all(p.get("seen_before") for p in again["problems"])


def test_without_claude_the_grouped_problems_still_go_out(tmp_path):
    def paused(**kw):
        raise RuntimeError("BudgetStop")
    report = selfcheck.check(Memory(tmp_path / "b.db"), run=_run(LOG), create=paused, env={})
    assert report["problems"] and report["problems"][0]["meaning"] == ""
    assert selfcheck.message(report)[0].startswith("🔧")


def test_an_unreadable_log_is_not_an_error(tmp_path):
    def broken(cmd, **kw):
        raise OSError("no journalctl")
    assert selfcheck.read_log(run=broken) == []
    assert selfcheck.server_now()["disk_used_pct"] >= 0
