"""Money agent: spending from emails and by voice, bills and reminders, limits."""

import asyncio
import json
from datetime import timedelta

from buddy import mailbrain
from buddy.money import Money, bill_reminder_text, run_money_tool
from buddy.memory import Memory
from buddy.tools import local_now

from tests.test_mailbrain import FakeClaude, add_email, setup  # noqa: F401  (fixture)


def money_of(kind, amount=0.0, merchant="", category="other", currency="USD", due=""):
    return {"kind": kind, "amount": amount, "currency": currency, "merchant": merchant,
            "category": category, "card": "1111", "due_date": due}


def test_spending_by_voice_and_limits():
    m = Money(Memory(":memory:"))
    run_money_tool(m, "spending_limit", {"category": "food", "amount": 100})
    assert run_money_tool(m, "add_expense", {"amount": 50, "what": "Tacos", "category": "food"}) == "Recorded."
    out = run_money_tool(m, "add_expense", {"amount": 35, "what": "Sushi", "category": "food"})
    assert "food is at 85 of your 100 USD limit" in out
    out = run_money_tool(m, "add_expense", {"amount": 20, "what": "Pizza", "category": "food"})
    assert "over" in out
    assert "Heads up" not in run_money_tool(m, "add_expense", {"amount": 5, "what": "Tea", "category": "food"})
    run_money_tool(m, "add_expense", {"amount": 40, "what": "Taxi", "category": "transport", "currency": "pen"})
    summary = json.loads(run_money_tool(m, "spending", {"period": "this_month"}))
    assert summary["by_currency"]["USD"]["total"] == 110 and summary["by_currency"]["PEN"]["total"] == 40
    assert summary["top_merchants"][0]["merchant"] == "Tacos"
    limits = json.loads(run_money_tool(m, "spending_limit", {}))
    assert limits[0]["left"] == -10


def test_bills_reminders_and_payment():
    m = Money(Memory(":memory:"))
    due = (local_now().date() + timedelta(days=2)).isoformat()
    later = (local_now().date() + timedelta(days=20)).isoformat()
    m.add_bill("Chase Sapphire", 412.5, "USD", due)
    m.add_bill("Rent", 1500, "USD", later)
    assert [b["biller"] for b in m.bills_to_remind()] == ["Chase Sapphire"]
    assert "Chase Sapphire bill of 412.50 USD is due on" in bill_reminder_text(m.bills_to_remind()[0])
    m.reminded(m.bills_to_remind()[0]["id"])
    assert m.bills_to_remind() == []  # once a day
    m.from_email(9, money_of("payment", 412.5, "Chase"), None)
    assert [b["biller"] for b in m.open_bills(30)] == ["Rent"]


def test_email_manager_reads_money(setup, monkeypatch):
    memory, manager, told, _ = setup
    manager.money.set_limit("shopping", 100)
    due = (local_now().date() + timedelta(days=10)).isoformat()
    add_email(memory, 1, "Chase", "You made a purchase")
    add_email(memory, 2, "Amex", "Your statement is ready")
    add_email(memory, 3, "Shop", "Sale: shoes from $49")
    ids = [r["id"] for r in memory.db.execute("SELECT id FROM records ORDER BY id")]
    fake = FakeClaude(lambda kw: [
        {"id": ids[0], "priority": "normal", "reason": "Amazon charge.", "rules": [],
         "money": money_of("charge", 120, "Amazon", "shopping")},
        {"id": ids[1], "priority": "normal", "reason": "Amex statement.", "rules": [],
         "money": money_of("bill", 980, "Amex", due=due)},
        {"id": ids[2], "priority": "low", "reason": "Promo.", "rules": [], "money": money_of("none")},
    ])
    monkeypatch.setattr(mailbrain, "client", fake)
    asyncio.run(manager.sort_new())
    assert "Also fill \"money\"" in fake.calls[0]["system"][0]["text"]
    assert manager.money.recent()[0]["merchant"] == "Amazon"
    assert manager.money.open_bills(30)[0]["biller"] == "Amex"
    assert any("shopping is at 120 of your 100 USD limit" in t for t in told)
