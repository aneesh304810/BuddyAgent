"""Card benefits: periods, statement credits from the bank feed, wishes, and reminders before credits expire."""

from datetime import date, datetime, timezone

import pytest

from buddy import benefits as bmod
from buddy.benefits import Benefits, period_bounds
from buddy.memory import Memory
from buddy.money import Money


@pytest.fixture
def setup(monkeypatch):
    memory = Memory(":memory:")
    Money(memory)  # creates the transactions table
    today = date(2026, 11, 4)
    monkeypatch.setattr(bmod, "local_now", lambda: datetime(2026, 11, 4, 10, 0, tzinfo=timezone.utc))
    return memory, Benefits(memory), today


def tx(memory, n, day, amount, merchant, card="Amex Platinum"):
    memory.db.execute(
        "INSERT INTO transactions (day, amount, currency, merchant, category, card, source, created_at) "
        "VALUES (?, ?, 'USD', ?, 'shopping', ?, ?, ?)", (day, amount, merchant, card, f"t:{n}", day))


def test_period_bounds():
    d = date(2026, 11, 4)
    assert period_bounds({"period": "monthly"}, d) == (date(2026, 11, 1), date(2026, 11, 30))
    assert period_bounds({"period": "quarterly"}, d) == (date(2026, 10, 1), date(2026, 12, 31))
    assert period_bounds({"period": "semiannual"}, d) == (date(2026, 7, 1), date(2026, 12, 31))
    assert period_bounds({"period": "annual"}, d) == (date(2026, 1, 1), date(2026, 12, 31))
    assert period_bounds({"period": "annual", "resets": "anniversary", "anniversary": "03-15"}, d) == \
        (date(2026, 3, 15), date(2027, 3, 14))
    assert period_bounds({"period": "annual", "resets": "anniversary", "anniversary": "12-01"}, d) == \
        (date(2025, 12, 1), date(2026, 11, 30))
    once = {"period": "once", "expires": "2027-01-31", "created_at": "2026-10-01T00:00:00+00:00"}
    assert period_bounds(once, d) == (date(2026, 10, 1), date(2027, 1, 31))


def test_statement_credits_and_purchases_are_read_from_the_feed(setup):
    memory, b, today = setup
    msg = b.add("Lululemon credit", 75, "quarterly", card="Amex Platinum", merchant="lululemon")
    assert "$75 quarterly" in msg and "2026-12-31" in msg and "check your card's terms" in msg
    tx(memory, 1, "2026-10-12", 40, "LULULEMON ATHLETICA #12")            # bought there
    tx(memory, 2, "2026-10-20", -40, "PLATINUM LULULEMON CREDIT")         # the statement credit
    tx(memory, 3, "2026-10-22", -25, "LULULEMON REFUND")                  # a plain return: not the credit
    tx(memory, 4, "2026-09-10", -75, "PLATINUM LULULEMON CREDIT")         # last quarter
    s = b.overview()[0]
    assert s["used"] == 40 and s["left"] == 35 and s["days_left"] == 57 and s["maybe_pending"] == 0
    tx(memory, 5, "2026-11-02", 30, "lululemon.com")                      # credit not posted yet
    s = b.overview()[0]
    assert s["spent_there"] == 70 and s["maybe_pending"] == 30
    assert "Noted $35.00 used" in b.mark_used("lululemon") and b.overview()[0]["left"] == 0


def test_plan_matches_wishes_and_reminders_are_said_once(setup):
    memory, b, today = setup
    b.add("Saks credit", 50, "semiannual", card="Amex Platinum", merchant="saks")
    b.add("Lululemon credit", 75, "quarterly", card="Amex Platinum", merchant="lululemon")
    b.add("Uber Cash", 15, "monthly", card="Amex Platinum", merchant="uber")
    b.add("Annual travel credit", 300, "annual", resets="anniversary", anniversary="03-15", card="Sapphire")
    assert "Lululemon credit has $75 left" in b.wish_add("Define jacket", "Lululemon", 98)
    plan = {p["benefit"]: p for p in b.plan()}
    assert set(plan) == {"Amex Platinum Saks credit", "Amex Platinum Lululemon credit", "Amex Platinum Uber Cash"}
    assert plan["Amex Platinum Lululemon credit"]["ideas"] == [{"item": "Define jacket", "price": 98.0, "you_pay": 23.0}]
    alerts = b.alerts()
    lulu = next(a for a in alerts if "Lululemon" in a["text"])
    assert "$75 left and runs out in 57 days (Dec 31)" in lulu["text"]
    assert "you'd pay $23" in lulu["text"] and "watch for a deal" in lulu["text"]
    for a in alerts:
        b.told(a)
    assert b.alerts() == []                                   # each step once
    later = date(2026, 12, 3)                                 # 28 days left: the 30-day step
    again = b.alerts(later)
    assert any("Lululemon" in a["text"] and "28 days" in a["text"] for a in again)


def test_nothing_is_invented_and_bad_input_is_explained(setup):
    memory, b, today = setup
    assert b.overview() == [] and "No card benefits tracked yet" in bmod.run_tool(b, {"action": "list"})
    with pytest.raises(bmod.ToolError):
        b.add("Mystery credit", 0, "monthly")
    with pytest.raises(bmod.ToolError):
        b.add("Hotel credit", 100, "yearly")
    with pytest.raises(bmod.ToolError):
        b.add("Card-year credit", 100, "annual", resets="anniversary")
    assert "issuers can change" in bmod.BENEFITS_TOOL["description"]
    assert "Removed" in (b.wish_add("Boots", "Saks", 400) and b.wish_remove("boots"))


def test_tool_is_offered_and_advice_includes_credits(setup):
    memory, b, today = setup
    from buddy import advisor, agent
    source = open(agent.__file__).read()
    assert "benefits.BENEFITS_TOOL" in source and 'name == "card_benefits"' in source
    b.add("Saks credit", 50, "semiannual", merchant="saks")
    adv = advisor.Advisor(memory)
    assert adv.advice()["card_credits_running_out"][0]["benefit"] == "Saks credit"
