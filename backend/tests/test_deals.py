"""Deal hunts and price watches."""

import asyncio
from types import SimpleNamespace

import pytest

from buddy import deals, research
from buddy.jobs import JobRunner, run_job_tool
from buddy.memory import Memory
from buddy.thrift import Thrift
from buddy.tools import ToolError


def answer(text):
    usage = SimpleNamespace(input_tokens=2000, output_tokens=200, cache_read_input_tokens=0,
                            cache_creation_input_tokens=0, server_tool_use=SimpleNamespace(web_search_requests=2))
    return SimpleNamespace(model="claude-sonnet-5-5", usage=usage, stop_reason="end_turn",
                           content=[SimpleNamespace(type="text", text=text)])


class FakeSearch:
    def __init__(self, prices):
        self.prices = list(prices)
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self.create))

    async def create(self, **kw):
        assert kw["tools"][0]["type"].startswith("web_search")
        price = self.prices.pop(0)
        return answer(f'Checked three sites.\n{{"found": true, "price": {price}, "where": "LATAM", '
                      f'"url": "https://latam.com", "note": "no checked bag"}}')


def test_watch_tells_on_target_and_big_drops_once(monkeypatch):
    told = []

    async def notify(kind, text, **_):
        told.append(text)
        return True

    monkeypatch.setattr(deals, "client", FakeSearch([520, 500, 430, 430, 380]))
    w = deals.PriceWatcher(Memory(":memory:"), notify)
    watch = w.add("MIA to LIM, 10-17 Dec, economy", target=450)
    for _ in range(5):
        asyncio.run(w.check(w.get(watch["id"])))
    assert len(told) == 2
    assert "430.00 USD at LATAM, at or under your target of 450" in told[0] and "no checked bag" in told[0]
    assert "380.00" in told[1] and "under your target" in told[1]
    assert w.get(watch["id"])["best_price"] == 380


def test_watch_limits_and_approval():
    memory = Memory(":memory:")
    w = deals.PriceWatcher(memory)
    out = asyncio.run(deals.run_watch_tool(w, "watch_price", {"what": "Sony XM6", "target": 300}, Thrift(memory)))
    assert out.startswith("APPROVAL NEEDED") and "1.80 dollars a month" in out and w.active() == []
    for i in range(deals.MAX_WATCHES):
        w.add(f"thing {i}", None)
    with pytest.raises(ToolError, match="already have"):
        w.add("one more", 10)
    assert "Stopped" in asyncio.run(deals.run_watch_tool(w, "price_watches", {"stop_id": w.active()[0]["id"]}))


def test_find_deals_is_a_research_job_in_deals_mode():
    memory = Memory(":memory:")

    async def finished(job):
        return True

    runner = JobRunner(memory, finished)
    runner.register("research", research.run_research)
    out = run_job_tool(runner, "find_deals", {"request": "Best price on a Kindle Paperwhite in Lima",
                                              "category": "shopping", "depth": "quick"})
    assert "started" in out
    job = runner.list()[0]
    params = runner.get(job["id"])["params"]
    assert params["mode"] == "deals" and params["category"] == "shopping"
    assert "deal hunt (shopping)" in research._system("deals", "shopping")
    runner.cancel(job["id"])
