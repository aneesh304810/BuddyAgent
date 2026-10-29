"""Bank feed (SimpleFIN): balances, transactions, categories, no double counting."""

import asyncio
import json
from types import SimpleNamespace

from buddy import bankfeed
from buddy.bankfeed import BankFeed, _get_json, run_balances_tool
from buddy.memory import Memory

DATA = {"errors": [], "accounts": [
    {"id": "card1", "name": "Sapphire ...1111", "currency": "USD", "balance": "-512.40", "available-balance": "4487.60",
     "balance-date": 1793232000, "org": {"name": "Chase", "domain": "chase.com"}, "transactions": [
         {"id": "t1", "posted": 1793145600, "amount": "-42.10", "description": "WHOLEFDS MKT #10234 MIAMI"},
         {"id": "t2", "posted": 1793145600, "amount": "-18.75", "description": "UBER *TRIP HELP.UBER.COM"},
         {"id": "t3", "posted": 1793232000, "amount": "600.00", "description": "AUTOMATIC PAYMENT - THANK YOU"},
         {"id": "t4", "posted": 1793232000, "amount": "12.00", "description": "AMAZON MKTPL REFUND"},
     ]},
    {"id": "chk", "name": "Checking", "currency": "USD", "balance": "3200.00", "balance-date": 1793232000,
     "org": {"name": "Chase"}, "transactions": [
         {"id": "t5", "posted": 1793232000, "amount": "-600.00", "description": "CHASE CREDIT CRD AUTOPAY"}]},
]}
CATS = {"WHOLEFDS MKT #10234 MIAMI": ("Whole Foods", "groceries"), "UBER *TRIP HELP.UBER.COM": ("Uber", "transport"),
        "AUTOMATIC PAYMENT - THANK YOU": ("Card payment", "transfer"), "AMAZON MKTPL REFUND": ("Amazon", "shopping"),
        "CHASE CREDIT CRD AUTOPAY": ("Card payment", "transfer")}


class FakeClaude:
    def __init__(self):
        self.calls = 0
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self.create))

    async def create(self, **kw):
        self.calls += 1
        asked = json.loads(kw["messages"][0]["content"].split(":\n", 1)[1])
        rows = [{"description": d, "merchant": CATS[d][0], "category": CATS[d][1]} for d in asked]
        usage = SimpleNamespace(input_tokens=500, output_tokens=200, cache_read_input_tokens=0,
                                cache_creation_input_tokens=0, server_tool_use=None)
        return SimpleNamespace(model="claude-haiku-5-5", usage=usage, stop_reason="end_turn",
                               content=[SimpleNamespace(type="text", text=json.dumps({"merchants": rows}))])


def test_sync_balances_and_spending(monkeypatch):
    fake = FakeClaude()
    monkeypatch.setattr(bankfeed, "client", fake)
    urls = []
    memory = Memory(":memory:")
    feed = BankFeed(memory, env={"BUDDY_SIMPLEFIN_URL": "https://u:p@bridge.example/simplefin"},
                    fetch=lambda url: urls.append(url) or DATA)
    result = asyncio.run(feed.sync())
    assert result["new_transactions"] == 3  # the two card-payment legs are skipped
    assert urls[0].startswith("https://u:p@bridge.example/simplefin/accounts?start-date=")
    spent = {t["merchant"]: t["amount"] for t in feed.money.recent()}
    assert spent == {"Whole Foods": 42.1, "Uber": 18.75, "Amazon": -12.0}
    balances = {b["name"]: b["balance"] for b in feed.balances()}
    assert balances == {"Sapphire ...1111": -512.4, "Checking": 3200.0}
    assert asyncio.run(feed.sync())["new_transactions"] == 0 and fake.calls == 1  # nothing twice; merchants remembered
    assert memory.get("money:bank_feed") == "1"
    # emails no longer add charges once the feed works (bills still come from emails)
    assert feed.money.from_email(5, {"kind": "charge", "amount": 42.1, "currency": "USD", "merchant": "Whole Foods",
                                     "category": "groceries", "card": "", "due_date": ""}, None) == []
    assert len(feed.money.recent()) == 3


def test_not_connected_explains():
    feed = BankFeed(Memory(":memory:"), env={})
    assert "SimpleFIN" in asyncio.run(run_balances_tool(feed, {}))


def test_basic_auth_from_access_url(monkeypatch):
    seen = {}

    class Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b'{"accounts": []}'

    def fake_open(request, timeout):
        seen["url"], seen["auth"] = request.full_url, request.get_header("Authorization")
        return Resp()

    monkeypatch.setattr(bankfeed.urllib.request, "urlopen", fake_open)
    assert _get_json("https://user:p%40ss@bridge.example/simplefin/accounts") == {"accounts": []}
    assert seen["url"] == "https://bridge.example/simplefin/accounts"  # no credentials in the URL itself
    assert seen["auth"] == "Basic dXNlcjpwQHNz"  # user:p@ss
