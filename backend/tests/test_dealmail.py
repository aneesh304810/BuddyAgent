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
        assert readonly  # nothing in the inbox is ever changed
        return "OK", [b"2"]

    def uid(self, cmd, *args):
        if cmd == "search":
            return "OK", [b" ".join(str(i + 1).encode() for i in range(len(self.messages)))]
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
    assert dealmail.run(memory, env={}, connect=lambda env: FakeIMAP([])).startswith("read 0 emails")
    page = pricebook.deals_page(memory, days=7)
    assert page["counts"] == {"product": 2, "flight": 1, "hotel": 0}
    sony = next(d for d in page["deals"] if d["title"].startswith("Sony"))
    assert sony["is_lowest"] and sony["off"] == 0.33 and sony["url"].startswith("https://")
    fare = next(d for d in page["deals"] if d["domain"] == "flight")
    assert pricebook.chart(memory, fare["item"], "flight")["points"][0]["price"] == 398
    assert [d["title"][:4] for d in pricebook.deals_page(memory, query="oled")["deals"]] == ["LG 6"]


def test_not_set_up_says_how():
    assert "BUDDY_DEALS_EMAIL_ADDRESS" in dealmail.run(Memory(":memory:"), env={})
