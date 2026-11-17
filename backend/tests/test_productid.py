"""Product identity: a deal headline is filed under its product, brand and category, so prices become history."""

from datetime import datetime, timedelta, timezone

import pytest

from buddy import pricebook, productid
from buddy.connectors.hub import ConnectorHub
from buddy.memory import Memory


@pytest.fixture
def memory(tmp_path):
    m = Memory(tmp_path / "buddy.db")
    ConnectorHub(m)
    yield m
    pricebook.close_all()


def test_the_same_product_is_read_the_same_from_different_headlines():
    a = productid.identity("Amazon - Baseus S2 4K Solar Wireless Outdoor Security Camera $79.99")
    b = productid.identity("Baseus S2 4K Solar Wireless Outdoor Security Camera for $74.99 at Amazon")
    assert a == b == {"product": "baseus s2", "brand": "baseus", "category": "security camera"}
    assert productid.product("Amazon - Baseus X1 Pro Dual-Tracking Solar Security Camera Outdoor Wireless $109.99") == "baseus x1 pro"
    assert productid.identity("Eufy Security eufyCam C37 Wireless 2K Solar Security Camera $59.99")["product"] == "eufy c37"
    assert productid.product("Sony WH-1000XM6 Wireless Noise Canceling Headphones $398 + Free Shipping") == "sony wh-1000xm6"
    assert productid.product("sony wh-1000xm6") == "sony wh-1000xm6"          # a watch phrase files with the deals
    assert productid.identity('65" LG C4 OLED 4K TV $1,296.99') == {"product": "lg c4", "brand": "lg", "category": "tv"}


def test_a_multi_pack_is_its_own_product_and_specs_are_not_model_names():
    assert productid.product("2-Pk Wyze Solar Cam Pan 2K HD Solar Smart Security Camera") == "wyze solar cam pan security x2"
    assert productid.product("Wyze Solar Cam Pan 2K HD Solar Smart Security Camera") == "wyze solar cam pan security"
    assert productid.product("Soliom SH801-4 Wireless Outdoor Solar 4K Security Camera (4-Pack) with 3W Solar Panel $257.40") == "soliom sh801-4 x4"
    assert productid.pack("3-Pk 10.5-Oz Lavazza Super Crema Whole Bean Coffee") == 3
    assert productid.identity("3-Pk 10.5-Oz Lavazza Super Crema Whole Bean Coffee") == {
        "product": "lavazza super crema whole bean x3", "brand": "lavazza", "category": "coffee"}
    assert productid.product("DEWALT 20V MAX Cordless Drill Combo Kit (2-Tool) DCK240C2 $139") == "dewalt dck240c2"
    one = productid.product("Security Cameras: Blink Outdoor 4 Camera + Battery Doorbell 2K+ & Sync Module Core for $28 at Amazon")
    two = productid.product("Blink Mini Pan-Tilt 2K+ Rotating Security Camera: Outdoor $30, Indoor")
    assert one != two and one.startswith("blink outdoor 4") and two.startswith("blink mini")
    assert productid.category("Nike Men's Revolution 7 Running Shoes $39.97") == "shoes"
    assert productid.identity("") == {"product": "", "brand": "", "category": "other"}


def test_prices_build_a_history_per_product_and_only_then_is_a_price_called_the_lowest(memory):
    now = datetime.now(timezone.utc)

    def deal(title, price, days_ago, url):
        day = (now - timedelta(days=days_ago)).date().isoformat()
        pricebook.run(memory, "INSERT INTO deals (seen_on, source, title, url, price, store) VALUES (?, 'feed', ?, ?, ?, 'Amazon')",
                      (day, title, url, price))
        pricebook.observe(memory, "product", title, price, seller="Amazon", seen_at=now - timedelta(days=days_ago))

    deal("Amazon - Baseus S2 4K Solar Wireless Outdoor Security Camera $99.99", 99.99, 5, "https://x.example/1")
    deal("Baseus S2 4K Solar Wireless Outdoor Security Camera for $89.99 at Amazon", 89.99, 3, "https://x.example/2")
    deal("Amazon - Baseus S2 4K Solar Wireless Outdoor Security Camera $79.99", 79.99, 0, "https://x.example/3")
    deal("Eufy Security eufyCam C37 Wireless 2K Solar Security Camera $59.99", 59.99, 0, "https://x.example/4")
    got = {d["url"]: d for d in pricebook.deals_page(memory)["deals"]}
    s2 = got["https://x.example/3"]
    assert (s2["product"], s2["brand"], s2["category"]) == ("baseus s2", "baseus", "security camera")
    assert s2["seen_days"] == 3 and s2["is_lowest"] is True and s2["lowest_seen"] == 79.99
    assert got["https://x.example/2"]["is_lowest"] is False and got["https://x.example/2"]["lowest_seen"] == 79.99
    first = got["https://x.example/4"]
    assert first["seen_days"] == 1 and first["is_lowest"] is False and first["lowest_seen"] is None   # seen once: not "lowest"
    assert pricebook.lowest(memory, "baseus s2") == 79.99
    assert pricebook.lowest(memory, "Amazon - Baseus S2 4K Solar Wireless Outdoor Security Camera $85") == 79.99
    assert [p["price"] for p in pricebook.chart(memory, "baseus s2")["points"]] == [99.99, 89.99, 79.99]
    assert len(pricebook.history(memory, "Baseus S2")) == 3
    assert s2["usual"] is None                                   # three Baseus camera deals: too few for a "usual"
    deal("Baseus N1 2K Indoor Security Camera $29.99", 29.99, 1, "https://x.example/5")
    deal("Baseus X1 Pro Dual-Tracking Solar Security Camera $109.99", 109.99, 1, "https://x.example/6")
    usual = {d["url"]: d for d in pricebook.deals_page(memory)["deals"]}["https://x.example/3"]["usual"]
    assert usual == {"price": 89.99, "deals": 5, "products": 3}


def test_prices_recorded_before_products_existed_are_filed_the_first_time(memory):
    pricebook.run(memory, "INSERT INTO observations (seen_at, domain, item, price) VALUES (?, 'product', ?, 50)",
                  (pricebook.utc(datetime.now(timezone.utc)), "amazon - anker 737 power bank $50"))
    pricebook.run(memory, "INSERT INTO observations (seen_at, domain, item, price) VALUES (?, 'flight', 'mia-lim', 300)",
                  (pricebook.utc(datetime.now(timezone.utc)),))
    assert pricebook.identify(memory) == 2 and pricebook.identify(memory) == 0
    rows = {r["item"]: r for r in pricebook.run(memory, "SELECT item, product, brand, category FROM observations")}
    assert rows["amazon - anker 737 power bank $50"]["brand"] == "anker"
    assert rows["amazon - anker 737 power bank $50"]["category"] == "charger and cable"
    assert rows["mia-lim"]["product"] == "mia-lim" and rows["mia-lim"]["category"] == "not stated"


def test_flights_file_by_route_airline_and_cabin_and_hotels_by_place_and_chain(memory):
    assert productid.travel("flight", "MIA-LIM", "serpapi:LATAM Airlines", cabin_class="business") == {
        "product": "mia-lim", "place": "lim", "brand": "latam", "category": "business"}
    assert productid.travel("flight", "jfk-lhr", "Delta deals", "Business class to London from $2,100 round trip")["category"] == "business"
    assert productid.travel("flight", "jfk-lhr", "best")["brand"] == ""            # a search provider is not an airline
    assert productid.cabin("Basic economy fares from $99") == "economy" and productid.cabin("Fares from $99") == "not stated"
    assert productid.travel("hotel", "hotels:lisbon", "google:Lisbon Marriott Hotel") == {
        "product": "hotels:lisbon", "place": "lisbon", "brand": "marriott", "category": "hotel"}
    now = datetime.now(timezone.utc)
    for days_ago, price, cabin in ((6, 900, "economy"), (4, 850, "economy"), (2, 800, "economy"), (5, 3200, "business"),
                                   (3, 3000, "business"), (1, 2900, "business")):
        pricebook.observe(memory, "flight", "MIA-LIM", price, source="serpapi:LATAM Airlines", cabin=cabin,
                          seen_at=now - timedelta(days=days_ago))
    rows = pricebook.run(memory, "SELECT DISTINCT product, brand, category, place FROM observations ORDER BY category")
    assert rows == [{"product": "mia-lim", "brand": "latam", "category": "business", "place": "lim"},
                    {"product": "mia-lim", "brand": "latam", "category": "economy", "place": "lim"}]
    day = now.date().isoformat()
    pricebook.run(memory, "INSERT INTO deals (seen_on, source, title, url, price, store, domain, item) VALUES (?, 'email', "
                          "'Business class Miami to Lima from $2,900 on LATAM', 'https://x.example/b', 2900, 'LATAM', 'flight', 'mia-lim')", (day,))
    deal = pricebook.deals_page(memory)["deals"][0]
    assert (deal["product"], deal["brand"], deal["category"], deal["place"]) == ("mia-lim", "latam", "business", "lim")
    assert deal["is_lowest"] is True and deal["lowest_seen"] == 2900     # the lowest business fare, not judged against economy


def test_the_engines_read_a_products_own_record_its_brand_and_its_category(memory):
    from buddy import factors, whentobuy
    now = datetime.now(timezone.utc)

    def seen(title, prices, **more):
        for i, price in enumerate(prices):
            pricebook.observe(memory, more.pop("domain", "product") if i == 0 and "domain" in more else more.get("d", "product"),
                              title, price, seen_at=now - timedelta(days=30 - i * 3), **{k: v for k, v in more.items() if k != "d"})

    seen("Baseus S2 4K Solar Security Camera", [100, 100, 100, 70])           # dips 30% below its usual
    seen("Baseus N1 2K Indoor Security Camera", [50, 50, 40])                 # dips 20%
    seen("Baseus X1 Pro Solar Security Camera", [120, 120, 108])              # dips 10%
    seen("Eufy C37 Solar Security Camera", [60, 60, 60])                      # never dips
    got = pricebook.levels(memory, "product", "Amazon - Baseus S2 4K Solar Security Camera $95")
    assert got["identity"]["product"] == "baseus s2" and got["own"] == {"prices": 4, "days": 4, "low": 70.0, "usual": 100.0}
    assert got["peers"] == {"dip": 0.2, "products": 3} and got["group"] == {"dip": 0.15, "products": 4}
    figures = factors.shopping(memory, "Baseus S2 4K Solar Security Camera", "electronics", price=95)
    assert figures["f_vs_own_low"] == round(95 / 70 - 1, 4) and figures["f_vs_own_usual"] == -0.05
    assert figures["f_peers_dip"] == 0.2 and figures["f_group_products"] == 4.0 and figures["f_own_days"] == 4.0
    # a camera Buddy has never seen is judged by what its brand's cameras have done
    new = whentobuy.advise(memory, "Baseus Z9 Dual Lens Security Camera", 150, "electronics", save=False)
    assert any("has not seen this one before" in r and "20%" in r and "3 products" in r for r in new["reasons"])
    assert not any("no price history" in r for r in new["reasons"])
    unknown = whentobuy.advise(memory, "Acme Garden Gnome", 20, "home", save=False)
    assert any("no price history" in r for r in unknown["reasons"])
    # flights: the route in its cabin, then the airline in that cabin, then the destination
    for i, price in enumerate([900, 880, 860, 700]):
        pricebook.observe(memory, "flight", "MIA-LIM", price, source="serpapi:LATAM Airlines", cabin="economy",
                          seen_at=now - timedelta(days=20 - i))
    for i, price in enumerate([3000, 3000, 2700]):
        pricebook.observe(memory, "flight", "MIA-LIM", price, source="serpapi:LATAM Airlines", cabin="business",
                          seen_at=now - timedelta(days=20 - i))
    for i, price in enumerate([500, 500, 450]):
        pricebook.observe(memory, "flight", "JFK-LIM", price, source="serpapi:Delta", cabin="economy",
                          seen_at=now - timedelta(days=20 - i))
    economy = pricebook.levels(memory, "flight", "MIA-LIM", seller="LATAM", cabin="economy")
    assert economy["own"]["low"] == 700 and economy["own"]["prices"] == 4          # the business fares are not in it
    assert economy["peers"]["products"] == 1 and economy["group"]["products"] == 2  # LATAM economy; economy to Lima
    business = pricebook.levels(memory, "flight", "MIA-LIM", seller="LATAM", cabin="business")
    assert business["own"]["low"] == 2700 and business["peers"]["dip"] == 0.1
    trip = factors.travel(memory, "flight", "MIA-LIM", price=770, airline="LATAM", cabin="economy")
    assert trip["f_vs_own_low"] == 0.1 and trip["f_group_products"] == 2.0
