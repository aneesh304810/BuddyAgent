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


def _mail(frm, subject, html):
    from email.message import EmailMessage
    m = EmailMessage()
    m["From"], m["Subject"], m["List-Unsubscribe"] = frm, subject, "<mailto:x@example.com>"
    m.set_content("x")
    m.add_alternative(html, subtype="html")
    return m


ACME_MAIL = ("<table><tr><td><a href='https://www.acmemarkets.com/foru/coupons-deals.html'>Save $1.00 on Fage Total "
             "Greek Yogurt 32 oz</a> Clip in app. Expires Dec 3</td></tr>"
             "<tr><td><a href='https://www.acmemarkets.com/foru/coupons-deals.html'>Buy 2 get 1 free Chobani</a></td></tr>"
             "<tr><td>Cheerios cereal</td></tr><tr><td>$0.75 off</td></tr>"
             "<tr><td>Save 10 cents per gallon with fuel rewards</td></tr>"
             "<tr><td>Free delivery on orders over $35. Save $10 on your first delivery order</td></tr>"
             "<tr><td><a href='https://www.acmemarkets.com/unsubscribe'>Unsubscribe</a> · Privacy · © 2026</td></tr></table>")


def test_coupons_without_a_price_are_read_from_store_emails():
    from buddy import dealmail
    m = _mail("ACME Markets <acme@email.acmemarkets.com>", "Your for U deals are here", ACME_MAIL)
    got = [(c["title"], c["ends"]) for c in dealmail.coupons_in(m)]
    assert got == [("Save $1.00 on Fage Total Greek Yogurt 32 oz", "Dec 3"), ("Buy 2 get 1 free Chobani", ""),
                   ("Cheerios cereal: $0.75 off", "")]  # not fuel, delivery or the footer
    assert dealmail.deals_in(m) == []  # a saving is never a price
    wfm = _mail("Whole Foods Market <wfm@example.com>", "Prime member deals",
                "<table><tr><td><a href='https://www.wholefoodsmarket.com/x'>35% off Siggi's yogurt</a></td></tr>"
                "<tr><td>Free delivery on orders over $35 for Prime members</td></tr></table>")
    assert dealmail.deals_in(wfm) == []  # "orders over $35" is the least to spend, not the yogurt's price
    assert [c["title"] for c in dealmail.coupons_in(wfm)] == ["35% off Siggi's yogurt"]


def test_coupons_from_emails_reach_the_list_and_the_advice(memory):
    from buddy import dealmail
    m = _mail("ACME Markets <acme@email.acmemarkets.com>", "Your for U deals are here", ACME_MAIL)
    today = date(2026, 11, 27)
    assert grocerysavings.keep_coupons(memory, dealmail.coupons_in(m), today) == 3
    assert grocerysavings.keep_coupons(memory, dealmail.coupons_in(m), today) == 0  # the same email again: nothing new
    assert grocerysavings.keep_coupons(memory, [{"title": "Save $50 on Sony headphones", "store": "Best Buy"}], today) == 0
    found = grocerysavings.coupons(memory, found=[], today=today)
    fage = next(c for c in found if "Fage" in c["title"])
    assert fage["ends"] == "2026-12-03" and fage["value"] == 1.0 and fage["price"] is None and fage["state"] == "found"
    assert not pricebook.history(memory, "fage total greek yogurt", "product", 90)  # no price from a coupon
    groceries.add_buy(memory, "greek yogurt")
    assert grocerysavings.advice(memory, "greek yogurt", found=[], coupon_list=found)["do"] == "clip"
    grocerysavings.mark_coupon(memory, fage["key"], "clipped", fage["title"], fage["store"])
    assert grocerysavings.savings(memory, today)["this_month"]["estimated"] == 1.0
    later = grocerysavings.coupons(memory, found=[], today=date(2026, 12, 4))  # Fage ended on Dec 3
    assert all("Fage" not in c["title"] for c in later) and any("Chobani" in c["title"] for c in later)
    assert grocerysavings.coupons(memory, found=[], today=date(2027, 1, 5)) == []  # no end date: listed 30 days
