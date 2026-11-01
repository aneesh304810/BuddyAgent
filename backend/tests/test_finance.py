"""Finance phase A: subscriptions found in the transactions, the keep/cancel list, and the card calendar."""

import asyncio
import json
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from buddy import finance, library
from buddy.memory import Memory
from buddy.money import Money
from buddy.tools import local_now


@pytest.fixture
def mem(tmp_path, monkeypatch):
    monkeypatch.setattr(library, "LIB_DIR", tmp_path / "library")
    return Memory(":memory:")


def ago(days: int) -> str:
    return (local_now().date() - timedelta(days=days)).isoformat()


def charges(mem, merchant, amounts_days, card="1004", currency="USD", category="subscriptions"):
    money = Money(mem)
    for amount, days in amounts_days:
        money.add_transaction(amount, currency, merchant, category, ago(days), card, f"t:{merchant}:{days}:{card}")


def test_merchant_names_are_normalised():
    assert finance.merchant_key("SQ *NETFLIX.COM 866-579-7172") == "netflix"
    assert finance.merchant_key("Netflix.com") == "netflix"
    assert finance.merchant_key("SPOTIFY USA 8777781161") == "spotify"
    assert finance.merchant_key("TST* Blue Bottle Coffee #12") == "blue bottle coffee"


def test_subscriptions_are_found_with_totals_and_flags(mem):
    charges(mem, "NETFLIX.COM", [(15.49, 95), (15.49, 65), (15.49, 35), (17.99, 5)])
    charges(mem, "Amazon Prime*AB12", [(139.0, 375), (139.0, 10)], category="shopping")
    charges(mem, "Spotify USA", [(11.99, 62), (11.99, 32)], card="1004")
    charges(mem, "Spotify USA", [(11.99, 2)], card="7788")
    charges(mem, "Whole Foods", [(82.1, 28), (12.5, 21), (140.0, 14), (33.0, 7)], category="groceries")
    charges(mem, "Hulu", [(9.99, 6)])
    fin = finance.Finance(mem)
    fin.find_subscriptions()
    subs = {s["merchant"]: s for s in fin.subscriptions()["subscriptions"]}
    assert set(subs) == {"NETFLIX.COM", "Amazon Prime*AB12", "Spotify USA", "Hulu"}  # not the grocery store
    assert subs["NETFLIX.COM"]["cadence"] == "monthly" and "price up from 15.49" in subs["NETFLIX.COM"]["flags"]
    assert subs["Amazon Prime*AB12"]["cadence"] == "yearly" and subs["Amazon Prime*AB12"]["yearly_cost"] == 139.0
    assert "charged on more than one card" in subs["Spotify USA"]["flags"]
    assert "probably a subscription (seen once)" in subs["Hulu"]["flags"]
    total = fin.subscriptions()["totals"]["USD"]
    assert total["count"] == 4 and total["yearly"] == round(17.99 * 12 + 139 + 11.99 * 12 + 9.99 * 12, 2)


def test_keep_cancel_and_watching_after_a_cancel(mem):
    charges(mem, "Disney Plus", [(13.99, 70), (13.99, 40), (13.99, 10)])
    fin = finance.Finance(mem)
    fin.find_subscriptions()
    assert fin.decide("disney", "cancel")["status"] == "cancel"
    assert fin.cancel_page("disney")["url"] == "https://www.disneyplus.com/account/subscription"
    # Charged again after the decision: Buddy says the cancel may not have gone through.
    charges(mem, "Disney Plus", [(13.99, -1)])  # tomorrow
    mem.db.execute("UPDATE fin_subscriptions SET decided_at = ?", (ago(5),))
    fin.find_subscriptions()
    texts = [a["text"] for a in fin.alerts()]
    assert any("after you decided to cancel" in t for t in texts)
    with pytest.raises(Exception):
        fin.decide("disney", "delete")


def test_cancelled_is_confirmed_when_no_charge_comes(mem):
    charges(mem, "Peacock", [(7.99, 100), (7.99, 70), (7.99, 40)])
    fin = finance.Finance(mem)
    fin.find_subscriptions()
    fin.decide("peacock", "cancel")
    mem.db.execute("UPDATE fin_subscriptions SET decided_at = ?", (ago(39),))
    fin.find_subscriptions()
    assert mem.db.execute("SELECT status FROM fin_subscriptions").fetchone()["status"] == "cancelled"


def test_alerts_are_said_once(mem):
    charges(mem, "Adobe", [(54.99, 40), (59.99, 10)])
    charges(mem, "Audible Annual", [(149.5, 355)])
    charges(mem, "Audible Annual", [(149.5, 720)])
    fin = finance.Finance(mem)
    fin.find_subscriptions()
    alerts = fin.alerts()
    texts = [a["text"] for a in alerts]
    assert any("Adobe went up from 54.99 to 59.99" in t for t in texts)
    assert any("Audible Annual renews" in t for t in texts)
    assert any(t.startswith("New subscription: Adobe") for t in texts)
    for a in alerts:
        fin.told(a)
    assert fin.alerts() == []


def test_card_calendar_from_statement_emails_and_what_the_user_says(mem):
    money = Money(mem)
    due = (local_now().date() + timedelta(days=5)).isoformat()
    money.add_bill("American Express", 1240.55, "USD", due, "1004", "email:1")
    money.add_bill("Electric company", 80, "USD", due, "", "email:2")
    fin = finance.Finance(mem)
    assert fin.cards_from_bills() == 1
    fin.card(name="Amex Gold", issuer="American Express", last4="371449631004", credit_limit=5000, apr=27.49,
             source="told")
    fin.cards_from_bills()  # the next statement email doesn't undo the name the user gave
    cards = fin.cards()
    assert len(cards) == 1 and cards[0]["last4"] == "1004"  # only ever the last four digits
    assert cards[0]["next_due"] == due and cards[0]["used_percent"] == 25
    texts = [a["text"] for a in fin.alerts()]
    assert any("due in 5 days" in t and "1240.55" in t for t in texts)
    assert fin.forget("1004") == "Removed the card Amex Gold." and fin.cards() == []


def test_a_statement_upload_fills_the_calendar_and_finds_charges(mem):
    lib = library.Library(mem, understand=lambda *a: None)
    item = lib.add("chase-oct.pdf", b"%PDF statement")
    mem.db.execute("INSERT INTO library_chunks (item_id, n, text) VALUES (?, 0, ?)",
                   (item["id"], "CHASE SAPPHIRE statement, card ending 4242 ..."))
    Money(mem).add_transaction(15.49, "USD", "Netflix.com", "subscriptions", ago(40), "4242", "email:9")
    due = (local_now().date() + timedelta(days=20)).isoformat()
    seen = {}

    async def fake_extract(memory, text):
        seen["text"] = text
        return {"is_card_statement": True, "issuer": "Chase", "card_name": "Sapphire Preferred", "last4": "4242",
                "currency": "USD", "statement_date": ago(5), "due_date": due, "statement_balance": 812.4,
                "minimum_due": 40, "credit_limit": 10000, "apr": 24.99,
                "transactions": [{"date": ago(40), "merchant": "NETFLIX.COM", "amount": 15.49, "category": "subscriptions"},
                                 {"date": ago(10), "merchant": "NETFLIX.COM", "amount": 15.49, "category": "subscriptions"},
                                 {"date": ago(12), "merchant": "Trader Joe's", "amount": 54.2, "category": "groceries"}]}
    fin = finance.Finance(mem)
    got = asyncio.run(fin.import_statement(item["id"], extract=fake_extract))
    assert got["imported"] and got["transactions"] == 2  # the Netflix charge from the email isn't counted twice
    card = fin.cards()[0]
    assert card["name"] == "Sapphire Preferred" and card["next_due"] == due and card["apr"] == 24.99
    assert Money(mem).open_bills(30)[0]["biller"] == "Sapphire Preferred"  # the usual bill reminders take it
    assert any(s["merchant"] == "NETFLIX.COM" for s in fin.subscriptions()["subscriptions"])
    assert "statement balance 812.40" in finance.statement_line(got)
    assert finance.looks_like_statement({"doc_type": "credit card statement", "title": "", "summary": ""})


def test_tool_and_money_screen_endpoints(mem):
    from buddy import server

    charges(server.memory, "Dropbox", [(11.99, 61), (11.99, 31), (11.99, 1)])
    fin = finance.Finance(server.memory)
    fin.find_subscriptions()
    opened = []

    async def open_links(links, compare=""):
        opened.extend(links)
    out = asyncio.run(finance.run_tool(fin, {"action": "cancel_help", "what": "dropbox"}, open_links))
    assert "Opened Dropbox" in out and opened[0]["url"] == "https://www.dropbox.com/account/plan"
    assert json.loads(asyncio.run(finance.run_tool(fin, {"action": "report"})))["totals"]["USD"]["count"] >= 1
    with TestClient(server.app) as c:
        data = c.get("/api/finance").json()
        sub = next(s for s in data["subscriptions"] if s["merchant"] == "Dropbox")
        assert c.post(f"/api/finance/subscriptions/{sub['id']}", params={"status": "keep"}).json()["status"] == "keep"
        assert c.post(f"/api/finance/subscriptions/{sub['id']}", params={"status": "nope"}).status_code == 400
        assert c.get(f"/api/finance/subscriptions/{sub['id']}/cancel").json()["url"].startswith("https://www.dropbox")
