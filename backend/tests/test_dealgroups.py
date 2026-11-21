"""The Deals page's groups (category, brand, product; route, airline; place, chain), its sorting and its "crazy
deals"; travel feeds filed as flights and hotels; a feed read once before it is added; and the Predicted list
worked out from the price book itself, with what it rests on."""

import io
import json
import urllib.error
from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from buddy import dealcollector as dc
from buddy import dealforecast as df
from buddy import dealmail, pricebook, server
from buddy.connectors.hub import ConnectorHub
from buddy.memory import Memory
from buddy.tools import ToolError


@pytest.fixture
def memory(tmp_path):
    m = Memory(tmp_path / "d.db")
    ConnectorHub(m)
    return m


def deal(title, price, regular=None, domain="product", item="", store="Shop", url=""):
    return {"title": title, "domain": domain, "item": item or title, "price": price, "regular": regular, "store": store,
            "url": url, "ends": "", "subject": title}


def stock(memory):
    """A small price book: five products, three fares on two routes, two hotel rates."""
    dealmail.keep(memory, [
        deal("Sony WH-1000XM5 Wireless Headphones $248", 248.0, 399.0),
        deal("Sony WF-1000XM5 Earbuds $199", 199.0),
        deal("LG C4 65-inch OLED TV $1,196", 1196.0, 2499.0),          # 52% off: a crazy deal
        deal("Samsung 55-inch QLED TV $497", 497.0, 600.0),
        deal("cotton socks 4-pack $12", 12.0),
        deal("JFK to LIM from $398 on LATAM", 398.0, domain="flight", item="JFK-LIM", store="LATAM"),
        deal("JFK to LIM nonstop $455 on Delta", 455.0, domain="flight", item="JFK-LIM", store="Delta"),
        deal("ERROR FARE: MIA to SJU $49 roundtrip", 49.0, domain="flight", item="MIA-SJU", store="Deal site"),
        deal("Hilton hotel in Lima from $89 per night", 89.0, domain="hotel", item="hotels:lima", store="Hilton"),
        deal("Boutique hotel in Cusco from $59 per night", 59.0, domain="hotel", item="hotels:cusco", store="Travel site"),
    ])


def test_products_group_by_category_brand_and_product(memory):
    stock(memory)
    cats = {g["name"]: g for g in pricebook.deal_groups(memory, 7, "product", "category")["groups"]}
    assert cats["tv"]["deals"] == 2 and cats["tv"]["lowest"] == 497.0 and cats["tv"]["best_off"] == 0.52
    assert cats["tv"]["label"] == "TV" and cats["earbuds and headphones"]["deals"] == 2
    brands = pricebook.deal_groups(memory, 7, "product", "brand")
    by = {g["name"]: g for g in brands["groups"]}
    assert by["sony"]["deals"] == 2 and by["sony"]["label"] == "Sony" and by["lg"]["label"] == "LG"
    assert by[""]["label"] == "Not filed" and by[""]["deals"] == 1                 # no brand Buddy knows in its headline
    assert brands["groups"][0]["name"] == "sony" and brands["deals"] == 5          # the biggest group first
    assert len(pricebook.deal_groups(memory, 7, "product", "product")["groups"]) == 5
    assert [g["name"] for g in pricebook.deal_groups(memory, 7, "product", "brand", query="oled")["groups"]] == ["lg"]


def test_flights_group_by_route_and_airline_hotels_by_place_and_chain(memory):
    stock(memory)
    routes = {g["name"]: g for g in pricebook.deal_groups(memory, 7, "flight", "route")["groups"]}
    assert routes["jfk-lim"]["deals"] == 2 and routes["jfk-lim"]["label"] == "JFK to LIM" and routes["jfk-lim"]["lowest"] == 398.0
    airlines = {g["name"]: g["label"] for g in pricebook.deal_groups(memory, 7, "flight", "airline")["groups"]}
    assert airlines["latam"] == "Latam" and "delta" in airlines
    assert {g["name"] for g in pricebook.deal_groups(memory, 7, "hotel", "place")["groups"]} == {"lima", "cusco"}
    chains = {g["name"]: g["deals"] for g in pricebook.deal_groups(memory, 7, "hotel", "chain")["groups"]}
    assert chains == {"hilton": 1, "": 1}
    assert pricebook.group_label("flight", "route", "new york-paris") == "New York to Paris"
    with pytest.raises(ValueError, match="route, airline"):
        pricebook.deal_groups(memory, 7, "flight", "category")                     # a flight has no category to group by


def test_one_groups_deals_and_the_sorting(memory):
    stock(memory)
    tvs = pricebook.deals_page(memory, 7, "product", by="category", group="tv")
    assert {d["brand"] for d in tvs["deals"]} == {"lg", "samsung"} and tvs["group_label"] == "TV" and tvs["by"] == "category"
    unfiled = pricebook.deals_page(memory, 7, "product", by="brand", group="")
    assert [d["title"][:6] for d in unfiled["deals"]] == ["cotton"]                # the deals Buddy could not file
    cheapest = pricebook.deals_page(memory, 7, "product", sort="price")["deals"]
    assert [d["price"] for d in cheapest] == sorted(d["price"] for d in cheapest)
    saving = pricebook.deals_page(memory, 7, "product", sort="saving")["deals"]
    assert [d["off"] for d in saving[:3]] == [0.52, 0.38, 0.17]
    assert pricebook.deals_page(memory, 7, "flight", by="route", group="jfk-lim", sort="price")["deals"][0]["price"] == 398.0
    assert pricebook.deals_page(memory, 7)["sort"] == "new" and pricebook.deals_page(memory, 7)["group"] is None


def test_a_crazy_deal_is_half_price_far_under_its_usual_or_called_a_mistake(memory):
    stock(memory)
    crazy = pricebook.deals_page(memory, 7, "product", crazy=True)
    assert [(d["brand"], d["crazy_why"]) for d in crazy["deals"]] == [("lg", ["52% off the regular price"])]
    assert crazy["found"]["product"] == 1 and crazy["crazy"] is True
    fares = pricebook.deals_page(memory, 7, "flight", crazy=True)["deals"]
    assert [d["crazy_why"] for d in fares] == [["called a price mistake by the source"]]
    # Far under its own usual price: the route has been seen at about 500 on five days, and now at 300.
    for back in range(2, 7):
        pricebook.observe(memory, "flight", "BOS-DUB", 500.0, seller="Aer Lingus", source="price watch",
                          seen_at=datetime.now(timezone.utc) - timedelta(days=back))
    dealmail.keep(memory, [deal("BOS to DUB fares from $300 on Aer Lingus", 300.0, domain="flight", item="BOS-DUB", store="Aer Lingus"),
                           deal("BOS to DUB fares from $60 on Aer Lingus", 60.0, domain="flight", item="BOS-DUB", store="Aer Lingus")])
    fares = {d["price"]: d for d in pricebook.deals_page(memory, 7, "flight")["deals"] if d["item"] == "bos-dub"}
    assert fares[300.0]["under"] == 0.4 and fares[300.0]["usual_price"] == 500.0
    assert fares[300.0]["crazy_why"] == ["40% under the usual price Buddy has seen"]
    assert fares[60.0]["under"] is None and not fares[60.0]["crazy"]              # 88% under: more likely another thing
    first = pricebook.deals_page(memory, 7, "flight", crazy=True)["deals"][0]
    assert first["price"] == 300.0                                               # the biggest saving comes first


TRAVEL = b"""<?xml version="1.0"?><rss version="2.0"><channel>
<item><title>Non-stop from New York to Paris, France for only $298 roundtrip</title><link>https://fares.example/1</link></item>
<item><title>Delta: New York \xe2\x80\x93 Barcelona, Spain. $386 (Basic Economy). Roundtrip, including all Taxes</title><link>https://fares.example/2</link></item>
<item><title>Business Class from Stockholm to Asia starting at \xe2\x82\xac1,343</title><link>https://fares.example/3</link></item>
<item><title>10 tips to find cheap flights this winter</title><link>https://fares.example/4</link></item>
<item><title>4* hotel in Lisbon, Portugal from $59 per night</title><link>https://fares.example/5</link></item>
<item><title>The best travel credit cards for $95 a year</title><link>https://fares.example/6</link></item>
</channel></rss>"""


def fetch(url, data=None, headers=None, *rest):
    if "refused.example" in url:
        raise urllib.error.HTTPError(url, 403, "Forbidden", {}, io.BytesIO(b""))
    if url.endswith("/robots.txt"):
        return b"User-agent: *\nDisallow: /" if "blocked.example" in url else b"User-agent: *\nDisallow: /private\n"
    if "fares.example" in url:
        return TRAVEL
    if "notafeed.example" in url:
        return b"<html><body>Hello</body></html>"
    if "empty.example" in url:
        return b'<?xml version="1.0"?><rss version="2.0"><channel></channel></rss>'
    raise AssertionError(url)


def test_a_travel_feed_is_filed_as_flights_and_hotels_and_its_articles_are_left_out(memory):
    memory.set(dc.FEEDS_KEY, json.dumps([{"name": "Fare site", "service": "deal_feed", "url": "https://fares.example/feed/",
                                          "kind": "travel"}]))
    new, status = dc.read_feeds(memory, fetch)
    assert status == ["Fare site: 3 new"]                                         # two fares and a hotel rate
    page = pricebook.deals_page(memory, 7)
    assert page["counts"] == {"product": 0, "flight": 2, "hotel": 1}
    fares = {d["product"]: d for d in page["deals"] if d["domain"] == "flight"}
    assert set(fares) == {"new york-paris", "new york-barcelona"}
    assert fares["new york-barcelona"]["brand"] == "delta" and fares["new york-paris"]["brand"] == ""   # the feed is no airline
    assert [d["place"] for d in page["deals"] if d["domain"] == "hotel"] == ["lisbon"]
    routes = pricebook.deal_groups(memory, 7, "flight", "route")["groups"]
    assert {g["label"] for g in routes} == {"New York to Paris", "New York to Barcelona"}
    kept = pricebook.run(memory, "SELECT domain, brand FROM observations ORDER BY domain, brand")
    assert [(r["domain"], r["brand"]) for r in kept] == [("flight", ""), ("flight", "delta"), ("hotel", "")]


def test_a_sender_that_is_not_an_airline_is_not_filed_as_one(memory):
    from buddy import productid
    assert [productid.is_airline(n) for n in ("Delta", "Azul Airlines", "Korean Air", "JetSMART")] == [True] * 4
    assert [productid.is_airline(n) for n in ("Going", "Deal site", "Google Flights", "Scott's Cheap Flights", "")] == [False] * 5
    assert productid.travel("flight", "jfk-lim", "Going", "JFK to LIM from $398")["brand"] == ""
    assert productid.travel("flight", "jfk-lim", "Going", "JFK to LIM from $398 on LATAM")["brand"] == "latam"
    # What was filed under a sender before this rule is put right by the nightly tidy-up.
    dealmail.keep(memory, [deal("JFK to LIM from $398", 398.0, domain="flight", item="JFK-LIM", store="Delta"),
                           deal("MIA to SJU from $129", 129.0, domain="flight", item="MIA-SJU", store="Fare club")])
    pricebook.run(memory, "UPDATE deals SET brand = 'going' WHERE item = 'mia-sju'")
    pricebook.run(memory, "UPDATE observations SET brand = 'going' WHERE item = 'mia-sju'")
    assert dealmail.tidy(memory)["not_airlines"] == 2 and dealmail.tidy(memory)["not_airlines"] == 0
    assert {g["name"] for g in pricebook.deal_groups(memory, 7, "flight", "airline")["groups"]} == {"delta", ""}


def test_a_product_feed_keeps_the_regular_price_it_states():
    d = dc.deal_from({"title": "DEWALT 20V MAX Drill Kit $99 (Reg. $179) at Home Depot", "url": "https://x.example/1"}, "feed")
    assert (d["domain"], d["price"], d["regular"], d["store"]) == ("product", 99.0, 179.0, "Home Depot")
    plain = dc.deal_from({"title": "PAC-MAN Gaming Light $80", "url": "https://x.example/2"}, "feed")
    assert plain["domain"] == "product" and plain["regular"] is None             # a product feed never makes a flight


def test_a_feed_is_read_once_before_it_is_added(memory):
    tried = dc.try_feed(memory, "https://fares.example/feed/", "travel", fetch)
    assert tried["items"] == 6 and tried["deals"] == 3 and tried["priced"] == 3
    assert tried["examples"][0].startswith("Non-stop from New York to Paris")
    for url, said in (("https://blocked.example/feed", "robots rules don't allow it"),
                      ("https://refused.example/feed", "would not show its robots rules"),   # unread is not "forbidden"
                      ("https://notafeed.example/feed", "not with a feed"),
                      ("https://empty.example/feed", "not with a feed"), ("ftp://x", "full https address")):
        with pytest.raises(ToolError, match=said):
            dc.try_feed(memory, url, "product", fetch)
    assert "Added" in dc.add_feed(memory, "https://fares.example/feed/", "Fare site", "travel")
    assert dc.feeds(memory)[-1] == {"name": "Fare site", "service": "deal_feed", "url": "https://fares.example/feed/",
                                    "kind": "travel"}
    with pytest.raises(ToolError, match="which feed"):
        dc.remove_feed(memory, "")                                                # never "remove them all"
    assert len(dc.feeds(memory)) == len(dc.DEFAULT_FEEDS) + 1


def test_sources_lists_feeds_what_they_said_and_what_can_be_added(memory):
    memory.set(dc.STATUS_KEY, json.dumps({"day": "2026-11-20", "new_deals": 34, "feeds": [
        "DealNews: 34 new", "r/deals: couldn't read it (HTTP 403)"]}))
    s = dc.sources(memory, env={})
    said = {f["name"]: f["said"] for f in s["feeds"]}
    assert said["DealNews"] == "34 new" and said["r/deals"].startswith("couldn't read it") and said["Woot"] == ""
    assert s["last_run"] == "2026-11-20" and len(s["suggested"]) == len(dc.SUGGESTED_FEEDS)
    assert {o["name"]: o["on"] for o in s["other"]}["Deals inbox"] is False
    dc.add_feed(memory, dc.SUGGESTED_FEEDS[0]["url"], dc.SUGGESTED_FEEDS[0]["name"], "travel")
    after = dc.sources(memory, env={"BUDDY_BESTBUY_KEY": "k"})
    assert dc.SUGGESTED_FEEDS[0]["name"] not in [f["name"] for f in after["suggested"]]       # added: no longer on offer
    assert {o["name"]: o["on"] for o in after["other"]}["Best Buy"] is True
    assert all(f["url"].startswith("https://") and f["kind"] in dc.KINDS for f in dc.SUGGESTED_FEEDS)


def put(memory, day, title, price, n):
    pricebook.run(memory, "INSERT INTO deals (seen_on, source, title, url, price, store) VALUES (?, 'feed', ?, ?, ?, 'Shop')",
                  (day, title, f"https://d.example/{n}", price))


def test_predictions_come_from_the_price_book_without_the_nightly_learning(memory):
    today = date(2026, 11, 20)
    pricebook.deals_page(memory)                                                  # the tables exist
    n = 0
    for day, price in (("2026-10-30", 278), ("2026-11-06", 268), ("2026-11-13", 248)):          # every 7 days
        n += 1
        put(memory, day, f"Sony WH-1000XM5 Wireless Headphones ${price}", price, n)
    for day, model in (("2026-10-28", "DCD771"), ("2026-11-03", "DCF887"), ("2026-11-09", "DCS570"), ("2026-11-15", "DCD800")):
        n += 1
        put(memory, day, f"DEWALT {model} 20V MAX cordless drill kit $99", 99, n)                # a brand, every 6 days
    for back in range(12):                                                                       # Anker: deals every day
        n += 1
        put(memory, (today - timedelta(days=back)).isoformat(), f"Anker {700 + back} Nano USB-C charger ${20 + back}", 20 + back, n)
    put(memory, "2026-11-19", "Ninja Air Fryer 4qt $59", 59, 999)                                # seen once: no rhythm
    book = df.from_book(memory, today, 45)
    sony = next(p for p in book["products"] if p["what"] == "Sony WH-1000XM5")
    assert (sony["when"], sony["expect_price"], sony["confidence"]) == ("2026-11-20", 248.0, "medium")
    assert sony["why"] == "on deal on 3 days, about every 7 days" and sony["category"] == "earbuds and headphones"
    assert not any("Ninja" in p["what"] for p in book["products"])
    dewalt = next(p for p in book["brands"] if p["brand"] == "dewalt")
    assert dewalt["when"] == "2026-11-21" and dewalt["type"] == "brand" and "4 days across 4 products" in dewalt["why"]
    assert any(name.startswith("Anker") for name in book["steady"]) and not any(p["brand"] == "anker" for p in book["brands"])
    page = df.predicted_page(memory, 45, today)
    kinds = {p["type"] for p in page["predictions"]}
    assert {"product", "brand", "sale"} <= kinds
    assert [p["when"] for p in page["predictions"]] == sorted(p["when"] for p in page["predictions"])
    rests = page["basis"]
    assert rests["kinds"]["product"]["deals"] == 20 and rests["kinds"]["product"]["first"] == "2026-10-28"
    assert rests["repeat"]["product"] == 1 and rests["learned_on"] == "" and rests["error"] == ""
    assert any(name.startswith("Anker") for name in rests["steady"])


def test_the_season_dates_are_shown_and_decide_nothing():
    days = {e["name"]: e["day"] for e in df.season(2026)}
    assert days == {"Travel Tuesday": date(2026, 12, 1), "Green Monday": date(2026, 12, 14), "Super Saturday": date(2026, 12, 19)}
    assert df.season(2027)[2]["day"] == date(2027, 12, 18)
    from buddy import whentobuy
    assert not any("Tuesday" in e["name"] for e in whentobuy.calendar(2026))    # the buy-or-wait calendar is unchanged


def test_predicted_says_so_when_the_price_book_cannot_be_read(memory, monkeypatch):
    def broken(*a, **k):
        raise RuntimeError("INTERNAL Error")
    monkeypatch.setattr(pricebook, "run", broken)
    page = df.predicted_page(memory, 45, date(2026, 11, 20))
    assert "could not be read" in page["basis"]["error"]
    assert {p["type"] for p in page["predictions"]} == {"sale"}                  # the calendar still shows


def test_the_deals_endpoints(monkeypatch):
    monkeypatch.setattr(server, "authorized", lambda t: t == "s3cret")
    headers = {"Authorization": "Bearer s3cret"}
    stock(server.memory)
    with TestClient(server.app) as client:
        assert client.get("/api/deals/groups").status_code == 401
        got = client.get("/api/deals/groups?domain=product&by=brand", headers=headers).json()
        assert {"sony", "lg"} <= {g["name"] for g in got["groups"]}
        assert client.get("/api/deals/groups?domain=hotel&by=route", headers=headers).status_code == 400
        tvs = client.get("/api/deals?domain=product&by=category&group=tv&sort=saving", headers=headers).json()
        assert [d["brand"] for d in tvs["deals"]] == ["lg", "samsung"] and tvs["group_label"] == "TV"
        assert [d["brand"] for d in client.get("/api/deals?domain=product&crazy=1", headers=headers).json()["deals"]] == ["lg"]
        page = client.get("/api/deals/predicted", headers=headers).json()
        assert set(page) >= {"predictions", "basis"} and set(page["basis"]) >= {"kinds", "repeat", "learning", "error"}
        assert len(client.get("/api/deals/sources", headers=headers).json()["suggested"]) == len(dc.SUGGESTED_FEEDS)
        monkeypatch.setattr(dc, "try_feed", lambda memory, url, kind="product", fetch=None: {"items": 6, "deals": 3, "priced": 3,
                                                                                         "examples": ["A fare"]})
        added = client.post("/api/deals/sources/add", headers=headers,
                            json={"url": "https://fares.example/feed/", "name": "Fare site", "kind": "travel"}).json()
        assert added["tried"]["deals"] == 3 and added["feeds"][-1]["kind"] == "travel"

        def refused(memory, url, kind="product", fetch=None):
            raise ToolError("I could not read that feed (HTTP 403), so I did not add it.")
        monkeypatch.setattr(dc, "try_feed", refused)
        no = client.post("/api/deals/sources/add", headers=headers, json={"url": "https://other.example/feed"})
        assert no.status_code == 400 and "did not add it" in no.json()["detail"]
        gone = client.post("/api/deals/sources/remove", headers=headers, json={"url": "https://fares.example/feed/"}).json()
        assert gone["removed"] == "Removed 1 feed." and all(f["name"] != "Fare site" for f in gone["feeds"])
        assert client.post("/api/deals/sources/remove", headers=headers, json={"url": ""}).status_code == 400


def counting(monkeypatch):
    """Every statement the price book is asked to run, in order."""
    asked, real = [], pricebook.run

    def run(memory, sql, params=(), heal=True):
        asked.append(" ".join(sql.split()))
        return real(memory, sql, params, heal)
    monkeypatch.setattr(pricebook, "run", run)
    return asked


def test_a_batch_of_new_deals_is_filed_by_one_statement_not_one_each(memory, monkeypatch):
    pricebook.deals_page(memory)                                                  # the tables exist
    for n in range(60):
        put(memory, "2026-11-20", f"Sony WH-1000XM{n} Wireless Headphones ${100 + n}", 100 + n, n)
    pricebook.run(memory, "INSERT INTO deals (seen_on, source, title, url, price, store, domain, item) VALUES "
                          "('2026-11-20', 'feed', 'JFK to LIM from $398 on LATAM', 'https://d.example/f', 398, NULL, 'flight', 'jfk-lim')")
    pricebook.run(memory, "INSERT INTO observations (seen_at, domain, item, price, seller, source) VALUES "
                          "(TIMESTAMP '2026-11-20 10:00:00', 'flight', 'jfk-lim', 398, NULL, NULL)")   # no seller, no source
    asked = counting(monkeypatch)
    assert pricebook.identify(memory) == 62                                       # 61 deals and 1 price
    assert sum(1 for s in asked if s.startswith("UPDATE deals")) == 1
    assert sum(1 for s in asked if s.startswith("UPDATE observations")) == 1
    filed = pricebook.run(memory, "SELECT COUNT(*) AS n, COUNT(product) AS filed, COUNT(DISTINCT product) AS products FROM deals")[0]
    assert (filed["n"], filed["filed"], filed["products"]) == (61, 61, 61)
    fare = pricebook.run(memory, "SELECT product, brand, place FROM deals WHERE domain = 'flight'")[0]
    assert fare == {"product": "jfk-lim", "brand": "latam", "place": "lim"}
    assert pricebook.run(memory, "SELECT product, place FROM observations")[0] == {"product": "jfk-lim", "place": "lim"}
    del asked[:]
    assert pricebook.identify(memory) == 0 and not any(s.startswith("UPDATE") for s in asked)   # nothing left to file


def test_what_adds_deals_files_them_so_a_page_does_not_have_to(memory, monkeypatch):
    memory.set(dc.FEEDS_KEY, json.dumps([{"name": "Fare site", "service": "deal_feed", "url": "https://fares.example/feed/",
                                          "kind": "travel"}]))
    dc.read_feeds(memory, fetch)
    dealmail.keep(memory, [deal("Sony WH-1000XM5 Wireless Headphones $248", 248.0, 399.0)])
    unfiled = pricebook.run(memory, "SELECT COUNT(*) AS n FROM deals WHERE product IS NULL")[0]["n"]
    assert unfiled == 0                                                           # before any page was opened
    asked = counting(monkeypatch)
    page = pricebook.deals_page(memory, 7)
    assert page["counts"] == {"product": 1, "flight": 2, "hotel": 1}
    assert not any(s.startswith("UPDATE") for s in asked)                         # the page only reads
    del asked[:]
    again, status = dc.read_feeds(memory, fetch)                                  # the same feed, nothing new in it
    assert again == [] and status == ["Fare site: 0 new"]
    assert not any(s.startswith("INSERT") for s in asked)                         # asked once which items it has
    assert sum(1 for s in asked if s.startswith("SELECT url FROM deals")) == 1
