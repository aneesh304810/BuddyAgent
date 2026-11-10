"""Amazon prices through Buddy Connect's Shopping group: Keepa, then Google Shopping, then the web search."""

import asyncio

from buddy import amazon, deals, gateway
from buddy.memory import Memory

LINK = "https://www.amazon.com/Sony-WH-1000XM5-Wireless-Headphones/dp/B09XS7JWHH/ref=sr_1_1"


def test_reads_links_asins_and_names():
    assert amazon.is_amazon(LINK) and amazon.asin_of(LINK) == "B09XS7JWHH"
    assert amazon.name_of(LINK) == "Sony WH 1000XM5 Wireless Headphones"
    assert amazon.is_amazon("AirPods Pro on Amazon") and amazon.name_of("AirPods Pro on Amazon") == "AirPods Pro"
    assert not amazon.is_amazon("flight MIA to LIM in March")


def test_google_shopping_picks_the_cheapest_amazon_offer(tmp_path):
    m = Memory(tmp_path / "b.db")

    def fetch(url, data=None, headers=None):
        assert url == "https://google.serper.dev/shopping" and headers["X-API-KEY"] == "k"
        return (b'{"shopping": [{"title": "Sony XM5", "source": "Best Buy", "price": "$299.99"},'
                b'{"title": "Sony XM5", "source": "Amazon.com", "price": "$278.00", "link": "https://amzn.to/x"},'
                b'{"title": "Sony XM5 renewed", "source": "Amazon.com - Seller", "price": "$1,249.00"}]}')
    got = amazon.lookup(m, LINK, env={"BUDDY_SERPER_KEY": "k"}, fetch=fetch)
    assert got["price"] == 278.0 and got["where"] == "Amazon (Google Shopping)"
    st = {s["name"]: s for s in gateway.status(m, env={"BUDDY_SERPER_KEY": "k"})}
    assert st["serper_shopping"]["group"] == "Shopping" and st["serper_shopping"]["calls_this_month"] == 1


def test_keepa_first_when_there_is_a_key_and_its_key_is_never_logged(tmp_path):
    m = Memory(tmp_path / "b.db")

    def fetch(url, data=None, headers=None):
        assert url.startswith("https://api.keepa.com/product?") and "asin=B09XS7JWHH" in url
        return b'{"products": [{"title": "Sony WH-1000XM5", "stats": {"current": [27800, 26999], "avg90": [31000]}}]}'
    got = amazon.lookup(m, LINK, env={"BUDDY_KEEPA_KEY": "secret"}, fetch=fetch)
    assert got["price"] == 278.0 and "90-day average $310.00" in got["note"]
    assert "secret" not in str([dict(r) for r in m.db.execute("SELECT * FROM api_calls")])


def test_nothing_set_up_returns_none_for_the_web_search_fallback(tmp_path):
    assert amazon.lookup(Memory(tmp_path / "b.db"), LINK, env={}) is None


def test_amazon_watches_use_the_cheap_lookup_and_cost_far_less(tmp_path, monkeypatch):
    assert deals.monthly_cost(24, LINK) < 0.1 < deals.monthly_cost(24, "MIA to LIM flights")
    m = Memory(tmp_path / "b.db")
    w = deals.PriceWatcher(m)
    watch = w.add(LINK, target=280.0)
    monkeypatch.setattr(amazon, "lookup", lambda memory, what, env=None, fetch=None: {
        "found": True, "price": 278.0, "where": "Amazon (Google Shopping)", "url": "u", "note": ""})
    told = asyncio.run(w.check(watch))
    assert told and "278.00" in told and "target" in told
