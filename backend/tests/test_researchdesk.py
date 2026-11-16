"""Research desk: what is queued from arXiv and GitHub, the licence rule, and reading a pick into a proposed rule."""

import asyncio
import json
from datetime import date

import pytest

from buddy import pricebook, stratrules
from buddy import researchdesk as research
from buddy.connectors.hub import ConnectorHub
from buddy.memory import Memory
from buddy.tools import ToolError

TODAY = date(2026, 11, 15)  # a Sunday
FEED = """<feed xmlns="http://www.w3.org/2005/Atom">
<entry><id>http://arxiv.org/abs/2611.01234v1</id><published>2026-11-12T10:00:00Z</published>
 <title>Momentum at the 52-week high: an out-of-sample test</title><summary>We test a trading strategy that buys
 stocks near their 52-week high and holds them for a month.</summary></entry>
<entry><id>http://arxiv.org/abs/2611.05555v1</id><published>2026-11-11T10:00:00Z</published>
 <title>A note on stochastic volatility surfaces</title><summary>We derive a closed form.</summary></entry>
<entry><id>http://arxiv.org/abs/2609.00001v1</id><published>2026-09-01T10:00:00Z</published>
 <title>An old momentum trading strategy</title><summary>Backtest of momentum.</summary></entry>
</feed>"""


@pytest.fixture
def memory(tmp_path):
    m = Memory(tmp_path / "buddy.db")
    ConnectorHub(m)
    yield m
    pricebook.close_all()


def test_arxiv_queues_only_new_papers_that_look_like_a_testable_strategy(memory):
    asked = []

    def fetch(url, data=None, headers=None):
        asked.append(url)
        return FEED

    assert research.arxiv_new(memory, fetch, today=TODAY) == 1
    assert "export.arxiv.org/api/query" in asked[0] and "q-fin.PM" in asked[0]
    got = research.queue(memory)
    assert [r["title"] for r in got] == ["Momentum at the 52-week high: an out-of-sample test"]
    assert got[0]["url"] == "https://arxiv.org/abs/2611.01234v1" and got[0]["source"] == "arXiv"
    assert research.arxiv_new(memory, fetch, today=TODAY) == 0  # the same paper is queued once
    assert research.due(memory, __import__("datetime").datetime(2026, 11, 15, 10)) is True
    assert "1 new research paper" not in research.weekly(memory, fetch, __import__("datetime").datetime(2026, 11, 15, 10))
    assert research.due(memory, __import__("datetime").datetime(2026, 11, 15, 11)) is False  # once a week


def test_github_needs_a_stated_licence_and_queues_only_readable_files(memory):
    def fetch(url, data=None, headers=None):
        if url.endswith("/license"):
            return json.dumps({"license": {"spdx_id": "Apache-2.0" if "/open/" in url else "NOASSERTION"}})
        return json.dumps([{"name": "momentum.py", "path": "lib/momentum.py", "type": "file", "size": 900,
                            "html_url": "https://github.com/acme/open/blob/main/lib/momentum.py"},
                           {"name": "logo.png", "path": "lib/logo.png", "type": "file", "size": 900, "html_url": "x"},
                           {"name": "big.py", "path": "lib/big.py", "type": "file", "size": 9_000_000, "html_url": "y"},
                           {"name": "sub", "path": "lib/sub", "type": "dir", "size": 0, "html_url": "z"}])

    got = research.github_add(memory, "https://github.com/acme/open/tree/main/lib", fetch)
    assert got == {"added": 1, "licence": "Apache-2.0", "looked_at": 4}
    assert research.queue(memory)[0]["licence"] == "Apache-2.0"
    with pytest.raises(ToolError, match="states no licence"):
        research.github_add(memory, "https://github.com/acme/closed", fetch)
    with pytest.raises(ToolError):
        research.github_add(memory, "https://example.com/acme/open", fetch)


def test_reading_a_pick_makes_a_proposed_rule_that_carries_its_source(memory):
    research._ready(memory)
    research._keep(memory, "arXiv", "Momentum at the 52-week high", "https://arxiv.org/abs/2611.01234v1", "2026-11-12",
                   "We test a strategy that buys stocks near their 52-week high.", "arXiv")
    item = research.queue(memory)[0]
    fetched, prompts = [], []

    def fetch(url, data=None, headers=None):
        fetched.append(url)
        return b"%PDF"

    async def reader(name, data):
        return {"text": "Buy stocks at a new 52-week high and hold for a month. " * 20, "where": "sandbox", "error": ""}

    async def ask(m, prompt):
        prompts.append(prompt)
        return {"name": "Year high", "summary": "Buys at a one-year high.", "buy": [{"field": "high_252", "op": ">=", "value": 1}]}

    rule = asyncio.run(research.read(memory, item["id"], fetch, ask, reader))
    assert fetched == ["https://export.arxiv.org/pdf/2611.01234v1"] and "hold for a month" in prompts[0]
    assert rule["status"] == "proposed" and rule["source_url"] == item["url"] and rule["licence"] == "arXiv"
    assert research.queue(memory) == [] and research.queue(memory, "read")[0]["rule_key"] == rule["key"]
    assert stratrules.one(memory, rule["key"])["source_url"] == item["url"]
    assert research.view(memory)["counts"] == {"read": 1}
