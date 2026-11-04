"""Finance phase D: banks and cards through Plaid (a fake Plaid answers here)."""

import asyncio
import json
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from buddy import finance, library, plaid
from buddy.bankfeed import BankFeed
from buddy.memory import Memory
from buddy.money import Money
from buddy.tools import local_now

ENV = {"BUDDY_PLAID_CLIENT_ID": "cid", "BUDDY_PLAID_SECRET": "sec", "BUDDY_PLAID_ENV": "sandbox"}


def ago(days: int) -> str:
    return (local_now().date() - timedelta(days=days)).isoformat()


class FakePlaid:
    """Answers the Plaid endpoints Buddy uses, and records what it was asked."""

    def __init__(self):
        self.calls = []
        self.removed = False
        self.due = (local_now().date() + timedelta(days=12)).isoformat()

    def __call__(self, url, payload):
        path = url.split(".plaid.com")[1]
        assert url.startswith("https://sandbox.plaid.com") and payload["client_id"] == "cid"
        self.calls.append((path, payload))
        if path == "/link/token/create":
            return {"link_token": "link-sandbox-123"}
        if path == "/item/public_token/exchange":
            return {"access_token": "access-sandbox-xyz", "item_id": "item-1"}
        accounts = [
            {"account_id": "acc-card", "name": "Gold Card", "official_name": "Amex Gold Card", "mask": "1004",
             "type": "credit", "subtype": "credit card",
             "balances": {"current": 2450.5, "limit": 10000, "iso_currency_code": "USD"}},
            {"account_id": "acc-save", "name": "Savings", "official_name": "High Yield Savings", "mask": "7781",
             "type": "depository", "subtype": "savings",
             "balances": {"current": 8000, "available": 8000, "iso_currency_code": "USD"}}]
        if path == "/accounts/get":
            return {"accounts": accounts}
        if path == "/transactions/sync":
            if payload["cursor"] == "":
                return {"added": [
                    {"transaction_id": "t1", "account_id": "acc-card", "amount": 15.49, "date": ago(40),
                     "merchant_name": "Netflix", "iso_currency_code": "USD",
                     "personal_finance_category": {"primary": "ENTERTAINMENT", "detailed": "ENTERTAINMENT_TV_AND_MOVIES"}},
                    {"transaction_id": "t2", "account_id": "acc-card", "amount": 15.49, "date": ago(10),
                     "merchant_name": "Netflix", "iso_currency_code": "USD",
                     "personal_finance_category": {"primary": "ENTERTAINMENT", "detailed": "ENTERTAINMENT_TV_AND_MOVIES"}},
                    {"transaction_id": "t3", "account_id": "acc-card", "amount": 92.3, "date": ago(3),
                     "merchant_name": "Whole Foods", "iso_currency_code": "USD",
                     "personal_finance_category": {"primary": "FOOD_AND_DRINK", "detailed": "FOOD_AND_DRINK_GROCERIES"}},
                    {"transaction_id": "t4", "account_id": "acc-card", "amount": -1200, "date": ago(5),
                     "name": "PAYMENT THANK YOU", "iso_currency_code": "USD",
                     "personal_finance_category": {"primary": "LOAN_PAYMENTS", "detailed": "LOAN_PAYMENTS_CREDIT_CARD_PAYMENT"}},
                    {"transaction_id": "t5", "account_id": "acc-card", "amount": 30, "date": ago(1), "pending": True,
                     "merchant_name": "Cafe", "personal_finance_category": {"primary": "FOOD_AND_DRINK"}}],
                    "modified": [], "removed": [], "next_cursor": "c1", "has_more": True}
            return {"added": [], "modified": [], "removed": [{"transaction_id": "t3"}], "next_cursor": "c2",
                    "has_more": False}
        if path == "/liabilities/get":
            return {"accounts": accounts, "liabilities": {"credit": [{
                "account_id": "acc-card", "aprs": [{"apr_type": "purchase_apr", "apr_percentage": 27.49}],
                "last_statement_balance": 1980.25, "last_statement_issue_date": ago(13), "minimum_payment_amount": 40,
                "next_payment_due_date": self.due, "last_payment_amount": 500, "last_payment_date": ago(20)}]}}
        if path == "/item/remove":
            self.removed = True
            return {"request_id": "r"}
        return {"error_code": "UNKNOWN", "error_message": path}


@pytest.fixture
def mem(tmp_path, monkeypatch):
    monkeypatch.setattr(library, "LIB_DIR", tmp_path / "library")
    return Memory(":memory:")


def linked(mem):
    fake = FakePlaid()
    p = plaid.Plaid(mem, env=ENV, http=fake)
    link = p.start_link("https://buddy.example.ts.net")
    state = link["url"].split("state=")[1]
    p.finish_link(state, "public-sandbox-1", "American Express")
    return p, fake, state


def test_linking_needs_the_one_time_state(mem):
    p, fake, state = linked(mem)
    assert p.items()[0]["access_token"] == "access-sandbox-xyz"
    create = dict(fake.calls)["/link/token/create"]
    assert create["products"] == ["transactions"] and create["required_if_supported_products"] == ["liabilities"]
    with pytest.raises(Exception):  # used once: the same state can't link again
        p.finish_link(state, "public-sandbox-2", "Evil")
    p2 = plaid.Plaid(mem, env=ENV, http=FakePlaid())
    p2.start_link("https://x")
    with pytest.raises(Exception):
        p2.finish_link("wrong-state", "public", "")


def test_sync_brings_transactions_balances_and_the_card_calendar(mem):
    p, fake, _ = linked(mem)
    got = p.sync(force=True)
    assert got["cards"] == 1 and not got["problems"]
    rows = {r["merchant"]: r for r in mem.db.execute("SELECT * FROM transactions")}
    assert set(rows) == {"Netflix"}  # groceries removed by the second page; payment and pending skipped
    assert rows["Netflix"]["card"] == "1004" and rows["Netflix"]["category"] == "subscriptions"
    assert not mem.get("money:bank_feed")  # sandbox test data never replaces the real charges from emails
    card = finance.Finance(mem).cards()[0]
    assert card["name"] == "Amex Gold Card" and card["last4"] == "1004" and card["apr"] == 27.49
    assert card["minimum_due"] == 40 and card["credit_limit"] == 10000 and card["next_due"] == fake.due
    assert finance.Finance(mem).credit_use()[0]["balance_now"] == 2450.5  # from the bank balance
    assert Money(mem).open_bills(30)[0]["amount"] == 1980.25
    assert any(s["merchant"] == "Netflix" for s in finance.Finance(mem).subscriptions()["subscriptions"])
    from buddy.advisor import Advisor
    assert Advisor(mem).savings() == 8000
    p.sync()  # not due again for 6 hours: no calls
    assert [c for c, _ in fake.calls].count("/transactions/sync") == 2


def test_login_required_is_reported_not_crashed(mem):
    p, fake, _ = linked(mem)

    def broken(url, payload):
        return {"error_code": "ITEM_LOGIN_REQUIRED", "error_message": "login"}
    p._http = broken
    got = p.sync(force=True)
    assert "sign in again" in got["problems"][0] and "sign in again" in p.status()[0]["problem"]


def test_unlink_revokes_and_deletes(mem):
    from buddy.advisor import Advisor
    p, fake, _ = linked(mem)
    p.sync(force=True)
    Advisor(mem).set_reward("1004", "dining", 4)
    assert "Unlinked American Express" in p.unlink("american express")
    assert fake.removed and p.items() == []
    assert mem.db.execute("SELECT COUNT(*) FROM transactions WHERE source LIKE 'plaid:%'").fetchone()[0] == 0
    assert mem.db.execute("SELECT COUNT(*) FROM bank_accounts").fetchone()[0] == 0
    assert finance.Finance(mem).cards() == [] and mem.get("money:bank_feed") == ""
    # nothing found in the deleted data lingers: its subscription and the card's reward rates are gone
    assert finance.Finance(mem).subscriptions()["subscriptions"] == []
    assert mem.db.execute("SELECT COUNT(*) FROM fin_rewards").fetchone()[0] == 0


def test_simplefin_stands_down_only_for_a_real_plaid_bank(mem):
    feed = BankFeed(mem, env={"BUDDY_SIMPLEFIN_URL": "https://user:pw@bridge.simplefin.org/simplefin"})
    assert feed.configured()
    p, _, _ = linked(mem)  # sandbox: SimpleFIN keeps running, emails keep counting
    assert feed.configured() and p.status()[0]["test_data"]
    mem.db.execute("UPDATE plaid_items SET env = 'production'")
    assert not feed.configured()
    p.sync(force=True)
    assert mem.get("money:bank_feed") == "1"  # a real bank replaces charges from emails


def test_unlink_keeps_real_subscriptions(mem):
    p, fake, _ = linked(mem)
    p.sync(force=True)
    for n, day in enumerate((ago(70), ago(40), ago(10))):  # a real subscription seen in emails
        mem.db.execute("INSERT INTO transactions (day, amount, currency, merchant, category, source, created_at) "
                       "VALUES (?, 11.99, 'USD', 'Spotify', 'subscriptions', ?, ?)", (day, f"email:{n}", day))
    finance.Finance(mem).find_subscriptions()
    p.unlink("american express")
    names = {s["merchant"] for s in finance.Finance(mem).subscriptions()["subscriptions"]}
    assert "Spotify" in names and "Netflix" not in names


def test_old_databases_mark_existing_banks_as_real(tmp_path):
    db_mem = Memory(":memory:")
    db_mem.db.execute("CREATE TABLE plaid_items (item_id TEXT PRIMARY KEY, access_token TEXT NOT NULL, "
                      "institution TEXT NOT NULL DEFAULT '', cursor TEXT NOT NULL DEFAULT '', last_sync TEXT, "
                      "last_liabilities TEXT, error TEXT NOT NULL DEFAULT '', added_at TEXT NOT NULL)")
    db_mem.db.execute("INSERT INTO plaid_items (item_id, access_token, added_at) VALUES ('i', 't', '2026-01-01')")
    p = plaid.Plaid(db_mem, env=ENV, http=FakePlaid())
    assert p.items()[0]["env"] == "production"


def test_not_configured_says_how(mem):
    p = plaid.Plaid(mem, env={})
    out = asyncio.run(plaid.run_tool(p, {"action": "status"}))
    assert "set.sh BUDDY_PLAID_CLIENT_ID" in out
    with pytest.raises(Exception):
        p.start_link("https://x")


def test_link_page_and_exchange_endpoints(monkeypatch):
    from buddy import server
    fake = FakePlaid()
    real = plaid.Plaid.__init__

    def init(self, memory, env=None, http=None):
        real(self, memory, env=ENV, http=fake)
    monkeypatch.setattr(plaid.Plaid, "__init__", init)
    with TestClient(server.app) as c:
        url = c.post("/api/plaid/link").json()["url"]
        assert url.startswith("http://testserver/plaid/link?state=")
        page = c.get(url.replace("http://testserver", ""))
        assert page.status_code == 200 and "link-sandbox-123" in page.text and "cdn.plaid.com" in page.text
        assert c.get("/plaid/link", params={"state": "nope"}).status_code == 403
        state = url.split("state=")[1]
        assert c.post("/plaid/exchange", json={"state": state, "public_token": "pt", "institution": "Chase"}).json()["ok"]
        assert c.post("/plaid/exchange", json={"state": state, "public_token": "pt"}).status_code == 400
        banks = c.get("/api/plaid").json()
        assert banks["configured"] and banks["banks"][0]["institution"] == "Chase"
        assert "Unlinked" in c.delete("/api/plaid/item-1").json()["result"]


def test_real_bank_replaces_earlier_copies_once(mem):
    p, fake, _ = linked(mem)
    mem.db.execute("UPDATE plaid_items SET env = 'production'")
    rows = [(ago(41), 15.49, "NETFLIX.COM", "email:1"), (ago(9), 15.49, "Netflix", "simplefin:9"),
            (ago(10), 15.49, "Netflix", "email:2"), (ago(25), 15.49, "Netflix", "email:3"),
            (ago(10), 6.00, "Street tacos", "manual:1:0")]
    for day, amount, merchant, source in rows:
        mem.db.execute("INSERT INTO transactions (day, amount, currency, merchant, category, source, created_at) "
                       "VALUES (?, ?, 'USD', ?, 'other', ?, ?)", (day, amount, merchant, source, day))
    p.sync(force=True)
    left = {r["source"] for r in mem.db.execute("SELECT source FROM transactions WHERE source NOT LIKE 'plaid:%'")}
    # Plaid's two Netflix charges (40 and 10 days ago) each replace one copy within 3 days, the closest one;
    # a charge 25 days ago has no Plaid match and stays; cash is never touched
    assert left == {"simplefin:9", "email:3", "manual:1:0"}


def test_sandbox_never_replaces_real_rows(mem):
    p, fake, _ = linked(mem)
    mem.db.execute("INSERT INTO transactions (day, amount, currency, merchant, category, source, created_at) "
                   "VALUES (?, 15.49, 'USD', 'Netflix', 'other', 'email:1', ?)", (ago(10), ago(10)))
    p.sync(force=True)
    assert mem.db.execute("SELECT COUNT(*) FROM transactions WHERE source = 'email:1'").fetchone()[0] == 1


def test_plaid_takes_over_each_linked_card(mem):
    p, fake, _ = linked(mem)
    mem.db.execute("UPDATE plaid_items SET env = 'production'")
    rows = [(ago(9), 58.00, "Bistro (with tip)", "1004", "email:1"),      # differs from any Plaid amount
            (ago(80), 20.00, "Before Plaid's history", "1004", "email:2"),  # older than Plaid's first charge
            (ago(9), 33.00, "Other card", "9911", "email:3"),                # a card Plaid doesn't cover
            (ago(9), 7.00, "Cash lunch", "1004", "manual:7:0")]              # cash is never touched
    for day, amount, merchant, card, source in rows:
        mem.db.execute("INSERT INTO transactions (day, amount, currency, merchant, category, card, source, created_at) "
                       "VALUES (?, ?, 'USD', ?, 'food', ?, ?, ?)", (day, amount, merchant, card, source, day))
    p.sync(force=True)
    left = {r["source"] for r in mem.db.execute("SELECT source FROM transactions WHERE source NOT LIKE 'plaid:%'")}
    assert left == {"email:2", "email:3", "manual:7:0"}


def test_card_emails_are_early_signals_once_plaid_is_the_record(mem, monkeypatch):
    from datetime import datetime, timezone
    p, fake, _ = linked(mem)
    mem.db.execute("UPDATE plaid_items SET env = 'production'")
    p.sync(force=True)
    money = Money(mem)
    before = mem.db.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
    big = money.from_email(501, {"kind": "charge", "amount": 412.5, "currency": "USD", "merchant": "Best Buy",
                                 "card": "1004", "category": "shopping"}, ago(0))
    assert big == ["Card alert: $412.50 at Best Buy on card 1004. It shows in Money once your bank posts it."]
    assert money.from_email(502, {"kind": "charge", "amount": 12, "currency": "USD", "merchant": "Cafe"}, ago(0)) == []
    assert money.from_email(503, {"kind": "refund", "amount": 45, "currency": "USD", "merchant": "Amazon"},
                            ago(0)) == ["Refund on the way: $45.00 from Amazon."]
    assert mem.db.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == before  # nothing stored twice
    assert mem.get("plaid:sync_soon")
    calls = [c for c, _ in fake.calls].count("/transactions/sync")
    p.sync()  # synced just now: too soon, Plaid needs a little time
    assert [c for c, _ in fake.calls].count("/transactions/sync") == calls
    old = (datetime.now(timezone.utc) - timedelta(minutes=45)).isoformat()
    mem.db.execute("UPDATE plaid_items SET last_sync = ?", (old,))
    mem.set("plaid:sync_soon", datetime.now(timezone.utc).isoformat())
    p.sync()  # a card email after a 45-minute-old sync: sync now, not in 6 hours
    assert [c for c, _ in fake.calls].count("/transactions/sync") > calls
    monkeypatch.setenv("BUDDY_CARD_ALERT_AMOUNT", "0")  # alerts off
    assert money.from_email(504, {"kind": "charge", "amount": 900, "currency": "USD", "merchant": "X"}, ago(0)) == []


def test_a_chase_mortgage_from_liabilities_is_kept_with_its_next_payment_reminder():
    from buddy.memory import Memory
    from buddy.money import Money
    from buddy.plaid import Plaid
    mem = Memory(":memory:")
    response = {"accounts": [{"account_id": "mtg1", "mask": "4321", "type": "loan", "subtype": "mortgage",
                              "balances": {"current": 412345.67, "iso_currency_code": "USD"}}],
                "liabilities": {"credit": [], "mortgage": [{
                    "account_id": "mtg1", "interest_rate": {"percentage": 6.125, "type": "fixed"},
                    "next_monthly_payment": 2950.12, "next_payment_due_date": "2099-12-01",
                    "last_payment_amount": 2950.12, "last_payment_date": "2099-10-30", "escrow_balance": 3100.5,
                    "ytd_interest_paid": 21000.0, "ytd_principal_paid": 6200.0, "origination_principal_amount": 450000,
                    "maturity_date": "2053-06-01", "loan_term": "30 year", "has_pmi": False,
                    "property_address": {"street": "1 Private Lane", "city": "Hoboken"}}]}}
    p = Plaid(mem, env={"BUDDY_PLAID_CLIENT_ID": "x", "BUDDY_PLAID_SECRET": "y", "BUDDY_PLAID_ENV": "production"},
              http=lambda url, body: response)
    n = p.liabilities({"item_id": "i1", "access_token": "t", "institution": "Chase"}, {})
    assert n == 1
    loan = p.loans()[0]
    assert loan["name"] == "Chase mortgage ••4321" and loan["rate"] == 6.125 and loan["rate_type"] == "fixed"
    assert loan["next_payment"] == 2950.12 and loan["next_due"] == "2099-12-01" and loan["escrow"] == 3100.5
    assert "Private Lane" not in json.dumps(loan)  # the property address isn't kept
    bills = Money(mem).open_bills(days=40000)
    assert any(b["biller"] == "Chase mortgage ••4321" and b["amount"] == 2950.12 for b in bills)
