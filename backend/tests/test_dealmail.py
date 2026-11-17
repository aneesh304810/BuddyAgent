"""The deals inbox: deals pulled out of newsletters (products, flights, hotels), kept in the price book, and the
Deals page's data. No network: the inbox is faked."""

from email.message import EmailMessage

import pytest

from buddy import dealmail, pricebook
from buddy.connectors.hub import ConnectorHub
from buddy.memory import Memory

STORE_HTML = """<html><head><style>.x{color:red}</style></head><body><table>
<tr><td><a href="https://track.bestbuy.example/c/1?u=sony">Sony WH-1000XM6 Wireless Noise Cancelling Headphones</a>
 <br>Now $299.99 Reg. $449.99</td></tr>
<tr><td><a href="https://track.bestbuy.example/c/2">LG 65" C5 OLED TV</a> $1,499.99 was $2,199.99 — ends Nov 30</td></tr>
<tr><td><a href="https://www.bestbuy.example/unsubscribe">Unsubscribe</a> from $0 emails</td></tr>
</table></body></html>"""


def message(sender, subject, html=None, text=None):
    m = EmailMessage()
    m["From"], m["Subject"] = sender, subject
    if text:
        m.set_content(text)
    if html:
        m.add_alternative(html, subtype="html")
    return m


@pytest.fixture
def memory(tmp_path):
    m = Memory(tmp_path / "buddy.db")
    ConnectorHub(m)
    yield m
    pricebook.close_all()


def test_store_newsletter_products_with_regular_price_and_links():
    deals = dealmail.deals_in(message("Best Buy <BestBuyInfo@emailinfo.bestbuy.com>", "Early Black Friday deals",
                                      html=STORE_HTML))
    assert len(deals) == 2  # the unsubscribe line isn't a deal
    sony = deals[0]
    assert sony["title"].startswith("Sony WH-1000XM6") and sony["price"] == 299.99 and sony["regular"] == 449.99
    assert sony["domain"] == "product" and sony["url"] == "https://track.bestbuy.example/c/1?u=sony"
    assert sony["store"] == "Best Buy"
    tv = deals[1]
    assert tv["price"] == 1499.99 and tv["regular"] == 2199.99 and tv["ends"].lower().startswith("nov 30")


def test_fare_and_hotel_sale_emails():
    fares = dealmail.deals_in(message("Delta <deals@t.delta.com>", "Fare sale: book by Nov 20",
                                      text="Round-trip fares on sale\nJFK to LIM from $398\nhttps://delta.example/lim\n"
                                           "New York to Bogota from $289 round trip\nhttps://delta.example/bog"))
    assert [d["domain"] for d in fares] == ["flight", "flight"]
    assert fares[0]["item"] == "JFK-LIM" and fares[0]["price"] == 398 and fares[0]["url"] == "https://delta.example/lim"
    assert fares[1]["item"] == "new york-bogota"
    hotels = dealmail.deals_in(message("Marriott Bonvoy <news@email.marriott.com>", "Weekend stays",
                                       text="Stay in Lima from $119 per night\nhttps://marriott.example/lima"))
    assert hotels[0]["domain"] == "hotel" and hotels[0]["item"] == "hotels:lima" and hotels[0]["price"] == 119


class FakeIMAP:
    def __init__(self, messages):
        self.messages = messages
        self.peeked = []

    def select(self, box, readonly=False):
        self.readonly = readonly  # reading is read-only; only the tidy-up afterwards may change the inbox
        return "OK", [b"2"]

    def uid(self, cmd, *args):
        if cmd == "search":
            return "OK", [b" ".join(str(i + 1).encode() for i in range(len(self.messages)))]
        assert cmd == "fetch"  # these emails have no List-Unsubscribe header: the tidy-up never removes them
        uid = int(args[0])
        self.peeked.append(args[1])
        return "OK", [(b"x", self.messages[uid - 1].as_bytes())]

    def logout(self):
        pass


def test_nightly_run_keeps_deals_once_and_feeds_the_page(memory):
    msgs = [message("Best Buy <b@bestbuy.example>", "Deals", html=STORE_HTML),
            message("Delta <deals@delta.example>", "Fares", text="JFK to LIM from $398\nhttps://delta.example/lim")]
    imap = FakeIMAP(msgs)
    status = dealmail.run(memory, env={}, connect=lambda env: imap)
    assert status.startswith("read 2 emails: 3 deals, 3 new") and all("PEEK" in p for p in imap.peeked)
    assert imap.readonly is False  # the tidy-up ran after the reading, and found nothing to remove
    assert dealmail.run(memory, env={}, connect=lambda env: FakeIMAP([])).startswith("read 0 emails")
    page = pricebook.deals_page(memory, days=7)
    assert page["counts"] == {"product": 2, "flight": 1, "hotel": 0}
    sony = next(d for d in page["deals"] if d["title"].startswith("Sony"))
    assert not sony["is_lowest"] and sony["seen_days"] == 1 and sony["off"] == 0.33  # one sighting is not "the lowest"
    assert sony["off"] == 0.33 and sony["url"].startswith("https://")
    fare = next(d for d in page["deals"] if d["domain"] == "flight")
    assert pricebook.chart(memory, fare["item"], "flight")["points"][0]["price"] == 398
    assert [d["title"][:4] for d in pricebook.deals_page(memory, query="oled")["deals"]] == ["LG 6"]


def test_not_set_up_says_how():
    assert "BUDDY_DEALS_EMAIL_ADDRESS" in dealmail.run(Memory(":memory:"), env={})


class CleanIMAP(FakeIMAP):
    """Three old emails Buddy has read: two newsletters and one personal email."""
    host = "imap.gmail.com"

    def __init__(self):
        super().__init__([])
        self.stored, self.selected_readonly = [], None

    def select(self, box, readonly=False):
        self.selected_readonly = readonly
        return "OK", [b"3"]

    def uid(self, cmd, *args):
        if cmd == "search":
            assert "BEFORE" in args[1] and "UID 1:3" in args[1]
            return "OK", [b"1 2 3"]
        if cmd == "fetch":
            header = b"" if args[0] == "2" else b"List-Unsubscribe: <https://x/unsub>\r\n"
            return "OK", [(b"x", header)]
        self.stored.append(args)
        return "OK", [b""]


def test_read_newsletters_go_to_trash_after_two_days_personal_mail_stays(memory):
    memory.set(dealmail.LAST_KEY, "3")
    imap = CleanIMAP()
    assert dealmail.clean(memory, env={}, connect=lambda env: imap) == 2
    assert [s[0] for s in imap.stored] == ["1", "3"] and all(s[1] == "+X-GM-LABELS" for s in imap.stored)
    assert dealmail.clean(memory, env={"BUDDY_DEALS_EMAIL_CLEAN_DAYS": "0"}, connect=lambda env: CleanIMAP()) == 0
    memory.set(dealmail.LAST_KEY, "")
    assert dealmail.clean(memory, env={}, connect=lambda env: CleanIMAP()) == 0  # nothing read yet: nothing removed


def test_a_flood_of_product_deals_does_not_push_flights_and_hotels_off_the_page(memory):
    """Seen live: after a night with thousands of product deals the Flights and Hotels tabs looked empty."""
    msgs = [message("Delta <deals@delta.example>", "Fares", text="JFK to LIM from $398\nhttps://delta.example/lim")]
    dealmail.run(memory, env={}, connect=lambda env: FakeIMAP(msgs))
    pricebook.run(memory, "INSERT INTO deals (seen_on, source, title, url, price, store) SELECT current_date, 'feed', "
                          "'Gadget ' || i, 'https://shop.example/' || i, 10 + i, 'Shop' FROM range(40) t(i)")
    page = pricebook.deals_page(memory, days=7, limit=10)
    assert page["counts"] == {"product": 40, "flight": 1, "hotel": 0}  # the true totals, not just what is shown
    assert page["shown"] == {"product": 10, "flight": 1, "hotel": 0}
    assert any(d["domain"] == "flight" and "LIM" in d["title"] for d in page["deals"])
    only = pricebook.deals_page(memory, days=7, domain="flight", limit=10)
    assert [d["domain"] for d in only["deals"]] == ["flight"]


DIGEST = """<html><body><table>
<tr><td><a href="https://slickdeals.example/f/1">3-Pk 10.5-Oz Lavazza Super Crema Whole Bean Coffee</a></td></tr>
<tr><td>$14.50</td></tr>
<tr><td>$5.52</td></tr><tr><td>$28</td></tr><tr><td>$1,599.08</td></tr><tr><td>$25.99</td></tr>
</table></body></html>"""


def test_an_alert_email_is_not_listed_again_and_again_under_its_subject_at_other_prices():
    deals = dealmail.deals_in(message("Slickdeals Alerts <alerts@slickdeals.example>",
                                      "Buddy: 3-Pk 10.5-Oz Lavazza Super Crema Whole Bean Coffee for $14.50 at Amazon",
                                      html=DIGEST))
    titles = [d["title"] for d in deals]
    assert titles[0] == "3-Pk 10.5-Oz Lavazza Super Crema Whole Bean Coffee" and deals[0]["price"] == 14.50
    assert deals[0]["url"] == "https://slickdeals.example/f/1"  # the link on the line above the price
    assert len(deals) == 2 and deals[1]["price"] == 14.50  # the subject names one more deal at most, at its own price
    assert not deals[1]["title"].startswith("Buddy:")


def test_a_news_story_with_a_sum_of_money_is_not_a_flight_deal():
    html = """<html><body><p><a href="https://news.example/debt">U.S. debt now equals the whole economy — and tax cuts
    since 2001 added over $10 trillion</a></p></body></html>"""
    assert dealmail.deals_in(message("Blogtrottr <busybee@blogtrottr.example>", "U.S. debt now equals the whole economy",
                                     html=html)) == []
    assert dealmail._prices("added over $10 trillion, $3.5 billion, $2M and a $19.99 book") == [19.99]
    assert not dealmail.FLIGHT.search("the whole economy") and dealmail.FLIGHT.search("Basic Economy fare to Lima")


def test_a_deal_without_its_own_link_takes_the_one_on_the_next_line():
    html = """<html><body><p>Round Trip Phoenix to St Maarten $333 Airfares on Delta</p>
    <p><a href="https://slickdeals.example/f/9">See the deal</a></p></body></html>"""
    deal = dealmail.deals_in(message("Slickdeals Alerts <a@slickdeals.example>", "Travel deals", html=html))[0]
    assert deal["domain"] == "flight" and deal["price"] == 333 and deal["url"] == "https://slickdeals.example/f/9"


def test_tidy_removes_only_the_misread_rows(memory):
    def deal(title, price):
        return {"title": title, "domain": "product", "item": title, "price": price, "regular": None,
                "store": "Slickdeals Alerts", "url": "", "ends": "", "subject": title}
    dealmail.keep(memory, [deal("Lavazza coffee for $14.50", p) for p in (5.52, 28.0, 1599.08)] + [deal("Good kettle", 30.0)])
    dealmail.keep(memory, [deal("Debt added over $10 trillion", 10.0)])
    assert dealmail.tidy(memory) == {"repeated": 3, "sums": 1, "prices": 3}
    left = pricebook.run(memory, "SELECT title FROM deals")
    assert [r["title"] for r in left] == ["Good kettle"]
    assert dealmail.tidy(memory) == {"repeated": 0, "sums": 0, "prices": 0}  # safe to run again
