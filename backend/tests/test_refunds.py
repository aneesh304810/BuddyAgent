"""Money back on the cards: refunds and statement credits listed with where they came from, which cards Buddy can't
see refunds for, refunds the owner is owed (matched when they post, a reminder when late), and refund emails kept
for watching when a bank feed is on."""

from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from buddy import refunds
from buddy.memory import Memory
from buddy.money import Money
from buddy.plaid import SCHEMA_SQL as PLAID
from buddy.tools import ToolError, local_now


@pytest.fixture
def m(tmp_path):
    memory = Memory(tmp_path / "b.db")
    Money(memory)
    return memory


def _tx(m, amount, merchant, card="1001", day=None, source=None, detail="Refund"):
    day = day or local_now().date().isoformat()
    n = m.db.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
    m.db.execute("INSERT INTO transactions (day, amount, currency, merchant, category, card, source, created_at, detail) "
                 "VALUES (?, ?, 'USD', ?, 'travel', ?, ?, ?, ?)",
                 (day, amount, merchant, card, source or f"plaid:item:{n}", day, detail))
    m.db.commit()


def test_refunds_and_credits_are_listed_with_their_source(m):
    _tx(m, 250.0, "Delta")                       # a charge: not money back
    _tx(m, -100.0, "Marriott Vacation Club")     # a refund from the bank feed
    _tx(m, -20.0, "Platinum Resy Credit", detail="Statement credit")
    _tx(m, -15.0, "Target", card="", source="email:9")
    got = refunds.credits(m)
    assert [(c["merchant"], c["amount"], c["kind"], c["from"]) for c in got][:3] == [
        ("Target", 15.0, "Refund", "an email"), ("Platinum Resy Credit", 20.0, "Statement credit", "your bank feed"),
        ("Marriott Vacation Club", 100.0, "Refund", "your bank feed")]
    assert [c["merchant"] for c in refunds.credits(m, card="1001")] == ["Platinum Resy Credit", "Marriott Vacation Club"]


def test_the_tool_says_which_cards_it_cant_see(m):
    m.db.executescript(PLAID)
    m.db.execute("INSERT INTO plaid_accounts (account_id, item_id, name, mask, type) VALUES "
                 "('a', 'i', 'Chase Sapphire', '4242', 'credit')")
    from buddy.finance import Finance
    Finance(m)
    m.db.execute("INSERT INTO fin_cards (key, name, issuer, last4, updated_at) VALUES "
                 "('amex|1001', 'Amex Platinum', 'American Express', '1001', 'x'), "
                 "('chase|4242', 'Chase Sapphire', 'Chase', '4242', 'x')")
    m.db.commit()
    out = refunds.run_tool(m, {"action": "list"})
    assert out.startswith("No refunds or statement credits in the last 90 days")  # never a made-up one
    assert "Cards Buddy sees every refund on (linked): Chase Sapphire ••4242" in out
    assert "can't see refunds on unless an email or a statement shows them: Amex Platinum ••1001" in out


def test_a_refund_owed_is_matched_when_it_posts(m):
    r = refunds.expect(m, "Marriott", 100, card="Amex 1001", note="cancelled the Encore package")
    assert r["expected_by"] == (local_now().date() + timedelta(days=14)).isoformat()
    assert refunds.expect(m, "Marriott Vacation Club", 100)["already"]  # the same refund said twice
    assert refunds.match(m) == []
    _tx(m, -100.0, "MARRIOTT VACATION CLUB", card="1001")
    said = refunds.match(m)
    assert said and "Your refund from Marriott posted: $100.00 on card 1001" in said[0]
    assert refunds.owed(m) == [] and refunds.match(m) == []


def test_a_different_amount_or_card_isnt_the_refund(m):
    refunds.expect(m, "Marriott", 100, card="1001")
    _tx(m, -40.0, "Marriott", card="1001")      # another refund from them
    _tx(m, -100.0, "Marriott", card="9999")     # on another card
    assert refunds.match(m) == [] and len(refunds.owed(m)) == 1


def test_a_late_refund_is_said_once(m):
    refunds.expect(m, "Marriott", 100, by=local_now().date().isoformat())
    later = local_now().date() + timedelta(days=1)
    said = refunds.reminders(m, later)
    assert len(said) == 1 and "hasn't shown on a card Buddy can see" in said[0]
    assert refunds.reminders(m, later) == []


def test_received_and_cancel_by_hand(m):
    a = refunds.expect(m, "Marriott", 100)
    b = refunds.expect(m, "Delta", 50)
    refunds.received(m, a["id"])
    refunds.cancel(m, b["id"])
    assert refunds.owed(m) == []
    with pytest.raises(ToolError):
        refunds.cancel(m, 999)
    with pytest.raises(ToolError):
        refunds.expect(m, "", 5)


def test_a_refund_email_is_watched_for_when_a_bank_feed_is_on(m):
    m.set("money:bank_feed", "1")
    notes = Money(m).from_email(42, {"kind": "refund", "amount": 100, "merchant": "Marriott", "currency": "USD",
                                     "card": "1001"}, "2026-11-20T10:00:00")
    assert notes and "Refund on the way" in notes[0]
    assert [(r["merchant"], r["amount"], r["source"]) for r in refunds.owed(m)] == [("Marriott", 100.0, "email:42")]


def test_the_api(m, monkeypatch):
    from buddy import server
    monkeypatch.setattr(server, "memory", m)
    monkeypatch.setattr(server, "authorized", lambda t: t == "s3cret")
    client, auth = TestClient(server.app), {"Authorization": "Bearer s3cret"}
    got = client.post("/api/refunds", json={"action": "expect", "merchant": "Marriott", "amount": 100}, headers=auth).json()
    assert got["owed"][0]["merchant"] == "Marriott" and got["credits"] == []
    assert client.get("/api/refunds").status_code == 401


def test_the_page_says_when_a_refund_it_was_watching_for_posted(m, monkeypatch):
    from buddy import server
    monkeypatch.setattr(server, "memory", m)
    monkeypatch.setattr(server, "authorized", lambda t: t == "s3cret")
    _tx(m, -100.0, "Marriott Vacation Club")
    got = TestClient(server.app).post("/api/refunds", json={"action": "expect", "merchant": "Marriott", "amount": 100},
                                      headers={"Authorization": "Bearer s3cret"}).json()
    assert got["posted"] and "Your refund from Marriott posted" in got["posted"][0] and got["owed"] == []
