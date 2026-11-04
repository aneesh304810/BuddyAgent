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


def test_transactions_older_than_24_months_are_deleted():
    from datetime import timedelta

    from buddy import money as money_mod
    from buddy.memory import Memory
    from buddy.tools import local_now

    mem = Memory(":memory:")
    m = money_mod.Money(mem)
    today = local_now().date()
    m.add_transaction(12.0, "USD", "Old cafe", "food", day=(today - timedelta(days=800)).isoformat())
    m.add_transaction(9.0, "USD", "New cafe", "food", day=(today - timedelta(days=30)).isoformat())
    money_mod._purged_on.clear()
    money_mod.Money(mem)
    assert [r["merchant"] for r in mem.db.execute("SELECT merchant FROM transactions")] == ["New cafe"]


def test_the_same_charge_in_two_emails_is_announced_once():
    from buddy.memory import Memory
    from buddy.money import Money
    mem = Memory(":memory:")
    mem.set("money:bank_feed", "1")
    m = Money(mem)
    charge = {"kind": "charge", "amount": 60585, "currency": "INR", "merchant": "Etihad Airways", "card": "9001"}
    first = m.from_email(1, charge, "2026-11-04T23:40:00")
    assert len(first) == 1 and "60,585.00 INR at Etihad Airways on card 9001" in first[0]
    assert m.from_email(2, dict(charge), "2026-11-04T23:41:00") == []


def test_new_columns_are_added_with_the_encrypted_databases_own_error_class():
    """sqlcipher3 raises sqlcipher3.dbapi2.OperationalError, not sqlite3's: the check must not depend on the class."""
    from buddy.memory import Memory
    from buddy.money import _add_column

    class OperationalError(Exception):  # stands in for sqlcipher3.dbapi2.OperationalError
        pass

    class Wrapped:
        def __init__(self, db):
            self.db = db

        def execute(self, sql, *a):
            try:
                return self.db.execute(sql, *a)
            except Exception as e:
                raise OperationalError(str(e)) from None

        def commit(self):
            self.db.commit()

    mem = Memory(":memory:")
    mem.db.execute("CREATE TABLE old_tx (id INTEGER PRIMARY KEY, merchant TEXT)")
    real, mem.db = mem.db, Wrapped(mem.db)
    try:
        _add_column(mem, "old_tx", "merchant_raw", "TEXT NOT NULL DEFAULT ''")
        _add_column(mem, "old_tx", "merchant_raw", "TEXT NOT NULL DEFAULT ''")  # already there: nothing to do
    finally:
        mem.db = real
    assert mem.db.execute("SELECT merchant_raw FROM old_tx").fetchall() == []


def test_an_older_encrypted_database_starts_and_is_upgraded(tmp_path, monkeypatch):
    """The real thing: SQLCipher, an old transactions table without the newer columns."""
    import secrets
    pytest_mod = __import__("pytest")
    pytest_mod.importorskip("sqlcipher3")
    from buddy import db
    from buddy.memory import Memory
    from buddy.merchants import Merchants
    from buddy.money import Money
    monkeypatch.setenv("BUDDY_DB_KEY", secrets.token_hex(32))
    path = tmp_path / "old.db"
    conn = db.connect(path)
    conn.execute("CREATE TABLE transactions (id INTEGER PRIMARY KEY, day TEXT NOT NULL, amount REAL NOT NULL, "
                 "currency TEXT NOT NULL, merchant TEXT NOT NULL, category TEXT NOT NULL, card TEXT NOT NULL DEFAULT '', "
                 "source TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL)")
    conn.execute("INSERT INTO transactions (day, amount, currency, merchant, category, source, created_at) VALUES "
                 "('2026-10-01', 38, 'USD', 'SQ *SON CUBANO', 'food', 'old:1', '2026-10-01')")
    conn.commit()
    conn.close()
    mem = Memory(str(path))
    Money(mem)  # adds detail and merchant_raw
    Merchants(mem).reclean()
    row = mem.db.execute("SELECT merchant, merchant_raw, detail FROM transactions").fetchone()
    assert tuple(row) == ("Son Cubano", "SQ *SON CUBANO", "")
