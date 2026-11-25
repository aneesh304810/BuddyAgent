"""Deals and card offers from the owner's regular email: bulk mail only, read-only, personal mail never opened."""

import json
from datetime import date
from email.message import EmailMessage

import pytest

from buddy import dealcollector, dealforecast, dealmail, pricebook
from buddy.connectors.hub import ConnectorHub
from buddy.memory import Memory

ENV = {"BUDDY_EMAIL_ADDRESS": "me@gmail.com", "BUDDY_EMAIL_APP_PASSWORD": "x",
       "BUDDY_EMAIL_2_ADDRESS": "work@yahoo.com", "BUDDY_EMAIL_2_APP_PASSWORD": "y",
       "BUDDY_DEALS_EMAIL_ADDRESS": "deals@gmail.com", "BUDDY_DEALS_EMAIL_APP_PASSWORD": "z"}
TODAY = date(2026, 11, 15)


@pytest.fixture
def memory(tmp_path):
    m = Memory(tmp_path / "buddy.db")
    ConnectorHub(m)
    yield m
    pricebook.close_all()


def mail(sender, subject, text, bulk=True):
    m = EmailMessage()
    m["From"], m["Subject"] = sender, subject
    if bulk:
        m["List-Unsubscribe"] = "<https://example.com/unsub>"
    m.set_content(text)
    return m


class Box:
    """One account's inbox: records which messages had their body fetched, and that it was opened read-only."""

    def __init__(self, messages):
        self.messages, self.bodies, self.readonly, self.changed = messages, [], None, []

    def select(self, box, readonly=False):
        self.readonly = readonly
        return "OK", [b"1"]

    def uid(self, cmd, *args):
        if cmd == "search":
            return "OK", [b" ".join(str(i + 1).encode() for i in range(len(self.messages)))]
        if cmd != "fetch":
            self.changed.append(cmd)
            return "OK", [b""]
        uid, spec = int(args[0]), args[1]
        if "HEADER.FIELDS" not in spec:
            self.bodies.append(uid)
        return "OK", [(b"x", self.messages[uid - 1].as_bytes())]

    def logout(self):
        pass


AMEX = ("Amex Offers for you\n"
        "Spend $250 or more, get $50 back at Best Buy. Expires 12/31\n"
        "Earn 5,000 Membership Rewards points after spending $500 at Delta Air Lines by 1/15\n"
        "Get 10% back at Home Depot until Dec 20\n"
        "Spend $100 or more, get $20 back at your favorite places\n")


def test_only_bulk_mail_is_opened_and_nothing_is_changed(memory):
    inbox = Box([mail("Mom <mom@example.com>", "Dinner Sunday?", "Come at 6. Bring the $20 you owe me!", bulk=False),
                 mail("Best Buy <deals@bestbuy.example>", "Deals", "Sony WH-1000XM6 headphones $299\nhttps://bestbuy.example/x"),
                 mail("American Express <offers@americanexpress.example>", "New Amex Offers", AMEX)])
    work = Box([])
    boxes = {"me@gmail.com": inbox, "work@yahoo.com": work}
    status = dealmail.run_all(memory, ENV, lambda account: boxes[account.address])
    assert inbox.bodies == [2, 3]                                   # the personal email's body was never fetched
    assert inbox.readonly is True and work.readonly is True and inbox.changed == []
    assert "me@gmail.com: 2 marketing emails of 3 new, 1 deals (1 new), 3 new card offers" in status
    assert "work@yahoo.com: 0 marketing emails of 0 new" in status and "deals@gmail.com" not in status
    assert pricebook.run(memory, "SELECT COUNT(*) AS n FROM deals WHERE title LIKE '%$20 you owe%'")[0]["n"] == 0
    assert pricebook.run(memory, "SELECT source, price FROM deals")[0] == {"source": "your email", "price": 299.0}
    assert dealmail.run_all(memory, ENV, lambda account: Box([])).count("0 marketing emails") == 2


def test_card_offers_are_read_from_issuers_only_and_kept_with_their_dates(memory):
    got = dealmail.offers_in(mail("American Express <offers@americanexpress.example>", "Offers", AMEX))
    by = {o["merchant"]: o for o in got}
    assert by["Best Buy"] == {"issuer": "American Express", "merchant": "Best Buy", "spend": 250.0, "reward": 50.0,
                              "reward_kind": "cash", "ends": "12/31", "subject": "Offers"}
    assert by["Delta Air Lines"]["reward_kind"] == "points" and by["Delta Air Lines"]["reward"] == 5000.0
    assert by["Home Depot"]["reward_kind"] == "percent" and by["Home Depot"]["spend"] is None
    assert len(got) == 3                                             # "your favorite places" names no merchant
    assert dealmail.offers_in(mail("Best Buy <d@bestbuy.example>", "x", AMEX)) == []      # not a card issuer
    assert dealmail.keep_offers(memory, got, TODAY) == 3 and dealmail.keep_offers(memory, got, TODAY) == 0
    running = dealmail.offers(memory, today=TODAY)
    assert [o["ends"] for o in running] == ["2026-12-31", "2027-01-15", "2026-12-20"]
    assert dealmail.offers(memory, "bestbuy.com Best Buy", TODAY)[0]["offer"] == \
        "American Express: $50 back at Best Buy when you spend $250 or more, until 2026-12-31"
    assert [o["merchant"] for o in dealmail.offers(memory, today=date(2027, 1, 5))] == ["Delta Air Lines"]  # the rest ended
    assert dealmail.offers(memory, today=date(2027, 6, 1)) == []


def test_a_watched_deal_mentions_the_card_offer_at_that_store_and_the_tool_lists_them(memory):
    dealmail.keep_offers(memory, dealmail.offers_in(
        mail("American Express <o@americanexpress.example>", "Offers", AMEX)))
    memory.set(dealforecast.PENDING_KEY, json.dumps([{
        "title": "Sony WH-1000XM6", "price": 299.0, "lowest": None, "store": "Best Buy", "reason": "sale",
        "url": "https://example.com/d", "source": "feed"}]))
    told = dealforecast.take_pending(memory)[0][0]
    assert "You also have a card offer there: American Express: $50 back at Best Buy when you spend $250 or more" in told
    listed = json.loads(dealcollector.run_tool(memory, {"action": "card_offers", "query": "home depot"}))
    assert listed["offers"] == [dealmail.offers(memory, "home depot")[0]["offer"]] and "issuer's app" in listed["note"]
    assert "None running at Tesla" in json.loads(dealcollector.run_tool(memory, {"action": "card_offers",
                                                                               "query": "Tesla"}))["offers"]


def test_it_can_be_turned_off_and_the_nightly_run_reports_both(memory):
    assert [a.address for a in dealmail.accounts(ENV)] == ["me@gmail.com", "work@yahoo.com"]
    assert dealmail.accounts({**ENV, "BUDDY_DEALS_FROM_ALL_EMAIL": "0"}) == []
    promo = Box([mail("REI <r@rei.example>", "Sale", "Tent $199\nhttps://rei.example/t")])
    status = dealmail.run(memory, ENV, connect=lambda env: Box([]), connect_account=lambda a: promo)
    assert status.startswith("read 0 emails") and "Your email accounts (marketing mail only, nothing changed there)" in status
    assert not dealmail.is_bulk(mail("a@b.example", "hi", "x", bulk=False))


# --- an Amex Offers email that was not picked up (stores shown only as logos) ------------------------------------------
AMEX_HTML = """<html><body><table>
<tr><td><img src="https://x/amex.png" alt="American Express"></td><td>Account ending: 12345</td></tr>
<tr><td>Total earnings from Amex Offers: $200.00 in statement credits</td></tr>
<tr><td><img src="https://x/dell.png" alt="Dell Technologies logo"></td></tr>
<tr><td>Spend $500 or more, earn $100 back, up to 2 times</td></tr>
<tr><td>Expires 12/31/2026</td></tr>
<tr><td><img src="https://x/hilton.png" alt="Hilton"></td></tr>
<tr><td>Earn +3 Membership Rewards&reg; points per eligible dollar spent, up to 3,000 points</td></tr>
<tr><td><img src="https://x/w.png" alt="Walmart+"> Earn a $10 statement credit on a purchase of $50 or more</td></tr>
<tr><td><img src="https://x/spacer.gif" alt=""><a href="https://x/add"><img alt="Add to Card button"></a></td></tr>
</table></body></html>"""


def html_mail(sender, subject, html):
    m = EmailMessage()
    m["From"], m["Subject"], m["List-Unsubscribe"] = sender, subject, "<https://example.com/unsub>"
    m.set_content("Your offers are inside.")
    m.add_alternative(html, subtype="html")
    return m


def test_stores_shown_only_as_logos_and_other_wordings_are_read():
    got = dealmail.offers_in(html_mail("Amex Offers from American Express <AmericanExpress@welcome.aexp.example>",
                                       "Aneesh, your new Amex Offers are inside!", AMEX_HTML))
    by = {o["merchant"]: o for o in got}
    assert set(by) == {"Dell Technologies", "Hilton", "Walmart+"}         # the logo's label, without "logo"
    assert (by["Dell Technologies"]["spend"], by["Dell Technologies"]["reward"]) == (500, 100)
    assert by["Dell Technologies"]["ends"] == "12/31/2026"                  # the date in the cell under the offer
    assert (by["Hilton"]["reward"], by["Hilton"]["reward_kind"]) == (3, "points_per_dollar")
    assert (by["Walmart+"]["spend"], by["Walmart+"]["reward"]) == (50, 10)  # the reward first, the spend after
    assert "$200" not in str(got)                                          # earnings so far are not an offer
    assert dealmail.picture_words("Dell logo") == "Dell" and dealmail.picture_words("spacer image") == ""
    assert dealmail.offer_short({"reward_kind": "cash", "reward": 100, "spend": 500}) == "$100 back on $500+"
    assert dealmail.offer_short({"reward_kind": "points_per_dollar", "reward": 3, "spend": 0}) == "3 extra points per $"


def test_issuers_emails_are_read_once_more_and_personal_mail_is_never_opened(memory):
    amex = html_mail("American Express <offers@americanexpress.example>", "Your offers", AMEX_HTML)
    friend = mail("Chase Miller <chase@example.com>", "Lunch?", "Spend $20 or more at Lokal, get $5 back, ha", bulk=False)
    news = mail("Chase <news@chase.example>", "Chase news", "Nothing to see")
    inbox, work = Box([amex, friend, news]), Box([])
    boxes = {"me@gmail.com": inbox, "work@yahoo.com": work}
    status = dealmail.reread_offers(memory, ENV, lambda account: boxes[account.address])
    assert inbox.bodies == [1, 3]                         # the friend's email: headers only, never its body
    assert inbox.readonly is True and inbox.changed == []
    assert "3 card offers found that were missed" in status
    assert {o["merchant"] for o in dealmail.offers(memory)} >= {"Dell Technologies", "Hilton", "Walmart+"}
    again = Box([amex])
    assert dealmail.reread_offers(memory, ENV, lambda account: again) == "" and again.bodies == []  # only once


def test_a_failed_account_means_it_is_tried_again_next_night(memory):
    def down(account):
        raise OSError("no connection")
    assert dealmail.reread_offers(memory, ENV, down) == "" and not memory.get(dealmail.REREAD_KEY)


def test_the_money_page_lists_the_offers_soonest_ending_first(monkeypatch):
    from fastapi.testclient import TestClient

    from buddy import server
    monkeypatch.setattr(server, "authorized", lambda t: t == "s3cret")
    server.memory.db.execute("DROP TABLE IF EXISTS card_offers")
    dealmail.keep_offers(server.memory, [
        {"issuer": "American Express", "merchant": "Dell Technologies", "spend": 500, "reward": 100,
         "reward_kind": "cash", "ends": "12/31/2099", "subject": "x"},
        {"issuer": "Chase", "merchant": "Hilton", "spend": None, "reward": 3, "reward_kind": "points_per_dollar",
         "ends": "", "subject": "x"},
        {"issuer": "Citi", "merchant": "Lokal", "spend": 20, "reward": 5, "reward_kind": "cash", "ends": "1/15/2099",
         "subject": "x"}])
    with TestClient(server.app) as client:
        assert client.get("/api/money/offers").status_code == 401
        got = client.get("/api/money/offers", headers={"Authorization": "Bearer s3cret"}).json()["offers"]
    assert [o["merchant"] for o in got] == ["Lokal", "Dell Technologies", "Hilton"]  # undated last
    assert got[1]["reward"] == "$100 back on $500+" and got[1]["ends"] == "2099-12-31"
