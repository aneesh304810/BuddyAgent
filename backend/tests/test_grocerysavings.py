"""Groceries savings: grocery deals kept apart from the Deals page, coupons Buddy lists but never clips, advice per
shopping-list item that weighs the price and the owner's own health verdict, the week's plan, savings and the
wishlist. Code only: no AI model decides any of it."""

from datetime import date, datetime, timezone

import pytest
from fastapi.testclient import TestClient

from buddy import dealmail, foodverdict, groceries, grocerysavings, pricebook
from buddy.connectors.hub import ConnectorHub
from buddy.memory import Memory
from tests.test_dietcheck import _me
from tests.test_foodverdict import CHIPS


@pytest.fixture
def memory(tmp_path):
    m = Memory(tmp_path / "buddy.db")
    ConnectorHub(m)
    _me(m, plan_diet={"calories": 2000})
    yield m
    pricebook.close_all()


def deal(title, price, store, url="", regular=None, ends=""):
    return {"title": title, "domain": "product", "item": title, "price": price, "regular": regular, "store": store,
            "url": url, "ends": ends, "subject": title}


def test_which_deals_are_groceries():
    assert grocerysavings.is_grocery("Chobani yogurt 2/$5", "ShopRite")
    assert grocerysavings.is_grocery("Paper towels 12 rolls", "Target", "household")
    assert grocerysavings.is_grocery("Large eggs, 18 ct", "Walmart")
    assert not grocerysavings.is_grocery("Sony WH-1000XM5 headphones", "Walmart")
    assert not grocerysavings.is_grocery("Dyson V15 vacuum", "Target")
    assert grocerysavings.is_grocery("Anything this week", "ACME Markets")


def test_grocery_deals_have_their_own_page_and_leave_the_deals_page(memory):
    dealmail.keep(memory, [deal("ShopRite: Chobani Greek yogurt $0.99", 0.99, "ShopRite"),
                           deal("Large eggs 18 ct $2.99", 2.99, "Walmart"),
                           deal("Sony WH-1000XM5 headphones $248", 248.0, "Walmart")])
    on_deals = [d["title"] for d in pricebook.deals_page(memory, 7, "product", groceries="exclude")["deals"]]
    on_groceries = [d["title"] for d in grocerysavings.deals(memory)]
    assert on_deals == ["Sony WH-1000XM5 headphones $248"]
    assert sorted(on_groceries) == ["Large eggs 18 ct $2.99", "ShopRite: Chobani Greek yogurt $0.99"]
    assert len(pricebook.deals_page(memory, 7, "product")["deals"]) == 3  # one price book: nothing stored twice


def test_coupons_are_listed_with_links_and_marked_by_the_owner(memory):
    dealmail.keep(memory, [deal("Digital coupon: $1.00 off Fage yogurt", 4.99, "ShopRite",
                                url="https://www.shoprite.com/offer/1", ends="2099-12-31"),
                           deal("Save $2 on Tide with ACME for U", 9.99, "ACME", ends="2001-01-01"),  # ended
                           deal("Bananas 49c/lb", 0.49, "ShopRite")])
    found = grocerysavings.coupons(memory)
    assert [c["title"] for c in found] == ["Digital coupon: $1.00 off Fage yogurt"]
    c = found[0]
    assert c["state"] == "found" and c["value"] == 1.0 and c["link"] == "https://www.shoprite.com/offer/1"
    assert grocerysavings.savings(memory)["this_month"]["estimated"] == 0.0  # found isn't saved
    grocerysavings.mark_coupon(memory, c["key"], "clipped", c["title"], c["store"])
    assert grocerysavings.coupons(memory)[0]["state"] == "clipped"
    assert grocerysavings.savings(memory)["this_month"]["estimated"] == 1.0  # clipped: an estimate
    grocerysavings.mark_coupon(memory, c["key"], "dismissed", c["title"], c["store"])
    assert grocerysavings.coupons(memory) == []
    with pytest.raises(Exception):
        grocerysavings.mark_coupon(memory, c["key"], "redeemed-by-ai")


def test_advice_weighs_the_owners_health_before_the_price(memory):
    groceries.add_buy(memory, "kettle chips")
    groceries.add_buy(memory, "greek yogurt")
    groceries.add_buy(memory, "spinach")
    foodverdict.keep(memory, CHIPS, "sometimes")
    dealmail.keep(memory, [deal("Kettle chips sea salt $1.99", 1.99, "ShopRite"),
                           deal("Digital coupon: $1.00 off Fage Greek yogurt", 4.99, "ShopRite", ends="2099-12-31")])
    plan = grocerysavings.plan(memory)
    by = {a["item"]: a for a in plan["advice"]}
    chips = grocerysavings.advice(memory, "kettle chips")
    if foodverdict.judge(memory, CHIPS)["verdict"] == "skip":  # Better to skip for this owner: even on sale
        assert chips["do"] == "skip"
    assert by["greek yogurt"]["do"] == "clip" and "clip it in the store's app" in by["greek yogurt"]["why"]
    assert by["spinach"]["do"] == "as_needed" and "no prices seen" in by["spinach"]["why"]
    assert "spinach" in plan["unpriced"] and plan["clip"][0]["title"].startswith("Digital coupon")


def test_buy_now_at_the_lowest_price_seen(memory):
    groceries.add_buy(memory, "eggs")
    for p in (3.99, 3.79, 4.29):
        pricebook.observe(memory, "product", "large eggs 18 ct", p, seller="ShopRite", source="deal email")
    pricebook.observe(memory, "product", "large eggs 18 ct", 2.99, seller="ACME", source="deal email")
    got = grocerysavings.advice(memory, "eggs")
    assert got["do"] == "buy_now" and got["store"] == "ACME" and got["price"] == 2.99


def test_a_second_store_only_when_it_beats_the_trip(memory):
    groceries.add_buy(memory, "eggs")
    groceries.add_buy(memory, "milk")
    pricebook.observe(memory, "product", "eggs 12 ct", 3.00, seller="ShopRite", source="deal email")
    pricebook.observe(memory, "product", "milk gallon", 4.00, seller="ShopRite", source="deal email")
    pricebook.observe(memory, "product", "eggs 12 ct", 2.50, seller="ACME", source="deal email")
    got = grocerysavings.plan(memory)
    assert got["single"] == {"store": "ShopRite", "items": 2, "total": 7.0}
    assert got["pair"]["saving"] == 0.5 and not got["pair"]["worth_it"]  # $0.50 isn't worth a $5 trip
    grocerysavings.set_trip_cost(memory, 0.25)
    assert grocerysavings.plan(memory)["pair"]["worth_it"]
    with pytest.raises(Exception):
        grocerysavings.set_trip_cost(memory, 500)


def test_the_wishlist(memory):
    grocerysavings.wish_add(memory, "Dyson V15 vacuum", 500)
    w = grocerysavings.wishlist(memory)[0]
    assert w["do"] in ("watching", "wait") and w["latest"] is None  # no price yet: the next sale, or watching
    pricebook.observe(memory, "product", "dyson v15 vacuum", 479.0, seller="Target", source="deal feed")
    w = grocerysavings.wishlist(memory)[0]
    assert w["do"] == "buy_now" and "at or under your price" in w["why"]
    assert grocerysavings.wish_remove(memory, w["id"]) == []


def test_a_sale_on_now_is_not_waited_for(memory, monkeypatch):
    from buddy import dealforecast
    monkeypatch.setattr(dealforecast, "forecast", lambda m, what, kind="", price=None, today=None: {"next_deals": [
        {"when": "2026-11-27", "window": ["2026-11-27", "2026-12-01"], "why": "Black Friday to Cyber Monday"}]})
    grocerysavings.wish_add(memory, "Dyson V15 vacuum", 500)
    on = grocerysavings.wishlist(memory, date(2026, 11, 28))[0]
    assert on["do"] == "watching" and "on now, until 2026-12-01" in on["why"] and "$500.00" in on["why"]
    before = grocerysavings.wishlist(memory, date(2026, 11, 10))[0]
    assert before["do"] == "wait" and before["why"].startswith("around 2026-11-27")


def test_coupon_marks_go_after_a_year(memory):
    grocerysavings.mark_coupon(memory, "https://www.shoprite.com/offer/9", "clipped", "$2 off coffee", "ShopRite")
    memory.db.execute("UPDATE grocery_coupons SET updated_at = '2024-01-05T10:00:00+00:00'")
    memory.db.commit()
    assert grocerysavings.savings(memory, date(2026, 11, 27))["months"] == []
    assert memory.db.execute("SELECT COUNT(*) FROM grocery_coupons").fetchone()[0] == 0


def test_the_api(memory, monkeypatch):
    from buddy import server
    monkeypatch.setattr(server, "memory", memory)
    monkeypatch.setattr(server, "authorized", lambda t: t == "s3cret")
    client, auth = TestClient(server.app), {"Authorization": "Bearer s3cret"}
    dealmail.keep(memory, [deal("Digital coupon: $1.00 off Fage yogurt", 4.99, "ShopRite", ends="2099-12-31"),
                           deal("Sony WH-1000XM5 headphones $248", 248.0, "Walmart")])
    page = client.get("/api/groceries/savings", headers=auth).json()
    assert [d["title"] for d in page["deals"]] == ["Digital coupon: $1.00 off Fage yogurt"] and page["coupons"]
    key = page["coupons"][0]["key"]
    after = client.post("/api/groceries/savings", json={"action": "coupon", "key": key, "state": "clipped",
                                                        "title": page["coupons"][0]["title"]}, headers=auth).json()
    assert after["coupons"][0]["state"] == "clipped"
    assert client.post("/api/groceries/savings", json={"action": "trip_cost", "dollars": 3}, headers=auth).json()["trip_cost"] == 3
    assert [d["title"] for d in client.get("/api/deals?days=7", headers=auth).json()["deals"]] == \
        ["Sony WH-1000XM5 headphones $248"]  # the Deals page leaves groceries out
    w = client.post("/api/wishlist", json={"action": "add", "what": "Kindle", "target": 90}, headers=auth).json()
    assert w["wishlist"][0]["what"] == "Kindle"
    assert client.get("/api/groceries/savings").status_code in (401, 403)
