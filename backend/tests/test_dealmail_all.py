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
