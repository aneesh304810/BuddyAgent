"""The same background message is never sent twice in 12 hours (a safety net for any repeating check)."""

import asyncio

from buddy import server


class WS:
    def __init__(self):
        self.sent = []
        self.headers = {}

    async def send_json(self, m):
        self.sent.append(m)


class Convo:
    def note_proactive(self, text, kind):
        pass


def test_same_message_once(monkeypatch):
    ws = WS()
    monkeypatch.setattr(server, "connections", {ws: Convo()})
    monkeypatch.setattr(server, "_recent_nudges", {})
    for _ in range(3):
        assert asyncio.run(server.deliver("money", "Market scan: 8 ideas worth watching"))
    asyncio.run(server.deliver("money", "Market scan: 5 ideas worth watching"))
    asyncio.run(server.deliver("ride", "Your driver is arriving"))
    asyncio.run(server.deliver("ride", "Your driver is arriving"))
    assert [m["text"] for m in ws.sent] == ["Market scan: 8 ideas worth watching", "Market scan: 5 ideas worth watching",
                                           "Your driver is arriving", "Your driver is arriving"]
