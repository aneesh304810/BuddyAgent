"""Finance phase C: the money advisor (best card, rewards missed, paying off, paydays, goals, monthly check-in)."""

import json
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from buddy import advisor, finance, library
from buddy.memory import Memory
from buddy.money import Money
from buddy.tools import local_now


@pytest.fixture
def mem(tmp_path, monkeypatch):
    monkeypatch.setattr(library, "LIB_DIR", tmp_path / "library")
    return Memory(":memory:")


def ago(days: int) -> str:
    return (local_now().date() - timedelta(days=days)).isoformat()


def spend(mem, merchant, amount, days, card, category):
    Money(mem).add_transaction(amount, "USD", merchant, category, ago(days), card, f"t:{merchant}:{days}:{card}:{amount}")


def two_cards(mem):
    fin = finance.Finance(mem)
    fin.card(name="Amex Gold", issuer="American Express", last4="1004", credit_limit=10000, apr=27.0, source="told")
    fin.card(name="Freedom Unlimited", issuer="Chase", last4="4242", credit_limit=8000, apr=22.0, source="told")
    return advisor.Advisor(mem)


def test_presets_best_card_and_rewards_missed(mem):
    adv = two_cards(mem)
    assert set(adv.apply_presets()) == {"Amex Gold", "Freedom Unlimited"}
    rewards = {r["card"]: r for r in adv.rewards()}
    assert rewards["Amex Gold"]["rates"]["groceries"] == 4 and rewards["Amex Gold"]["preset_to_confirm"]
    best = adv.best_cards()
    assert best["groceries"]["card"] == "Amex Gold" and best["drugstores"]["card"] == "Freedom Unlimited"
    assert best["other"] == {"card": "Freedom Unlimited", "rate": 1.5}
    assert adv.best_for("Whole Foods")["card"] == "Amex Gold"
    assert adv.best_for("CVS pharmacy")["card"] == "Freedom Unlimited"
    spend(mem, "Whole Foods", 800, 20, "4242", "groceries")  # 1.5x where 4x was available
    missed = adv.missed_rewards()
    assert missed[0]["best_card"] == "Amex Gold" and missed[0]["missed_about"] == 20.0
    assert adv.confirm_rewards("amex") == "Confirmed Amex Gold's reward rates."
    adv.set_reward("1004", "flights", 5)
    assert {r["card"]: r for r in adv.rewards()}["Amex Gold"]["rates"]["flights"] == 5


def test_payoff_plan_highest_apr_first_saves_interest(mem):
    adv = two_cards(mem)
    debts = [{"card": "Amex Gold", "balance": 3000, "apr": 27.0, "minimum": 90, "carrying_balance": True,
              "currency": "USD"},
             {"card": "Freedom Unlimited", "balance": 1200, "apr": 22.0, "minimum": 40, "carrying_balance": True,
              "currency": "USD"}]
    plan = adv.payoff_plan(500, debts)
    assert plan["highest_apr_first"]["order"] == ["Amex Gold", "Freedom Unlimited"]
    assert plan["smallest_balance_first"]["order"] == ["Freedom Unlimited", "Amex Gold"]
    assert 8 <= plan["highest_apr_first"]["months"] <= 10
    assert plan["saves_with_highest_apr_first"] > 0
    assert "less than the minimums" in adv.payoff_plan(100, debts)["note"]
    assert adv.payoff_plan(500, [])["note"].startswith("No card is carrying a balance")


def test_interest_shows_a_card_is_carrying_a_balance(mem):
    adv = two_cards(mem)
    fin = finance.Finance(mem)
    fin.card(name="Amex Gold", issuer="American Express", last4="1004", statement_date=ago(10),
             statement_balance=2500, source="statement")
    spend(mem, "INTEREST CHARGE ON PURCHASES", 48.2, 9, "1004", "other")
    debts = {d["card"]: d for d in adv.debts()}
    assert debts["Amex Gold"]["carrying_balance"] and debts["Amex Gold"]["interest_last_month"] == 48.2


def test_due_dates_against_paydays_and_autopay(mem):
    adv = two_cards(mem)
    fin = finance.Finance(mem)
    today = local_now().date()
    due = today + timedelta(days=10)
    fin.card(name="Amex Gold", issuer="American Express", last4="1004", due_date=due.isoformat(), due_day=due.day,
             source="statement")
    adv.set_payday("biweekly", (due - timedelta(days=1)).isoformat())  # paid the day before it's due
    setup = {p["card"]: p for p in adv.payment_setup()}
    assert setup["Amex Gold"]["days_after_payday"] == 1 and "move the due date" in setup["Amex Gold"]["suggest"]
    assert "full statement balance" in setup["Amex Gold"]["autopay_tip"]
    adv.set_autopay("amex", "full")
    assert "autopay_tip" not in {p["card"]: p for p in adv.payment_setup()}["Amex Gold"]
    adv.set_payday("monthly", days=[1, 15])
    assert all(d.day in (1, 15) for d in adv.paydays(today, today + timedelta(days=40)))
    with pytest.raises(Exception):
        adv.set_payday("daily")


def test_goals(mem):
    adv = two_cards(mem)
    first = local_now().date().replace(day=1)
    for months_back in (1, 2, 3):
        day = (first - timedelta(days=30 * months_back - 10))
        Money(mem).add_transaction(3000, "USD", "Rent", "bills", day.isoformat(), "", f"rent:{months_back}")
    adv.add_goal("emergency_fund", target=3, current=4500)
    adv.add_goal("pay_off", card="amex")
    adv.add_goal("save", name="Lima trip", target=2000, current=500,
                 due=(local_now().date() + timedelta(days=150)).isoformat())
    goals = {g["kind"]: g for g in adv.goals()}
    assert goals["emergency_fund"]["target"] == pytest.approx(9000, rel=0.35)
    assert goals["emergency_fund"]["months_covered"] == pytest.approx(1.5, rel=0.35)
    assert goals["save"]["percent"] == 25 and goals["save"]["save_a_month"] == 300.0
    assert adv.update_goal("lima", 800).startswith("Updated") and adv.remove_goal("lima").startswith("Removed")


def test_monthly_check_in_and_timing(mem, monkeypatch):
    adv = two_cards(mem)
    adv.apply_presets()
    adv.set_income(6000)
    last_month = local_now().date().replace(day=1) - timedelta(days=5)
    Money(mem).add_transaction(900, "USD", "Whole Foods", "groceries", last_month.isoformat(), "4242", "m1")
    Money(mem).add_transaction(25, "USD", "LATE FEE", "other", last_month.isoformat(), "1004", "m2")
    m = adv.month()
    assert m["spent"] == 925 and m["left_over"] == 5075 and m["interest_and_fees"] == 25
    assert any("Use Amex Gold for groceries" in t for t in m["todo"])
    text = adv.month_markdown(m)
    assert "# Money check-in" in text and "## Rewards missed" in text and advisor.DISCLAIMER in text
    job_id, summary = adv.save_month()
    assert "925 dollars" in summary and "5,075 left over" in summary
    assert mem.db.execute("SELECT kind FROM jobs WHERE id = ?", (job_id,)).fetchone()["kind"] == "money_monthly"
    monkeypatch.setattr(advisor, "MONTHLY_AT", f"{local_now().day} 00:00")
    assert advisor.monthly_due(mem)
    mem.set("finance:monthly", local_now().strftime("%Y-%m"))
    assert not advisor.monthly_due(mem)


def test_tool_and_api(mem):
    adv = two_cards(mem)
    out = json.loads(advisor.run_tool(adv, {"action": "advice"}))
    assert out["best_card_for"]["groceries"]["card"] == "Amex Gold" and out["rule"] == advisor.DISCLAIMER
    assert "Amex Gold" in advisor.run_tool(adv, {"action": "best_card", "what": "dinner at a restaurant"})
    from buddy import server
    with TestClient(server.app) as c:
        data = c.get("/api/finance").json()
        assert {"best_card_for", "rewards", "goals"} <= set(data)
