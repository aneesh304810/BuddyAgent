"""The money review: categories against the usual, fees, subscriptions, cards, ways to save and a budget."""

import json
from datetime import timedelta

import pytest

from buddy import advisor, review
from buddy.benefits import Benefits
from buddy.finance import Finance
from buddy.memory import Memory
from buddy.money import Money
from buddy.plaid import Plaid
from buddy.tools import local_now


def day_in(month_offset: int, day: int) -> str:
    first = local_now().date().replace(day=1)
    return review._months_back(first, month_offset).replace(day=day).isoformat()


@pytest.fixture
def mem():
    m = Memory(":memory:")
    Money(m)
    Plaid(m, env={})  # creates fin_income
    n = 0

    def tx(day, amount, merchant, category, card="1004", detail=""):
        nonlocal n
        n += 1
        m.db.execute("INSERT INTO transactions (day, amount, currency, merchant, category, card, source, created_at, "
                     "detail) VALUES (?, ?, 'USD', ?, ?, ?, ?, ?, ?)",
                     (day, amount, merchant, category, card, f"plaid:i:{n}", day, detail))
    # three usual months before last month, then last month (offset 1) with more eating out and a fee
    for off in (4, 3, 2, 1):
        tx(day_in(off, 3), 1800, "Landlord LLC", "bills", detail="Rent")
        tx(day_in(off, 5), 420, "Whole Foods", "groceries", detail="Groceries")
        tx(day_in(off, 9), 15.49, "Netflix", "subscriptions", detail="Tv and movies")
        tx(day_in(off, 12), 60, "Shell", "transport", detail="Gas")
        tx(day_in(off, 15), 180 if off > 1 else 520, "Cafe Roma", "food", detail="Restaurant")
        tx(day_in(off, 20), 40, "Uber", "transport", detail="Taxis and ride shares")
        m.db.execute("INSERT INTO fin_income (source, day, amount, currency, name, kind) VALUES (?, ?, 4200, 'USD', "
                     "'ACME PAYROLL', 'Wages')", (f"plaid:i:pay{off}", day_in(off, 1)))
    tx(day_in(1, 22), 39, "LATE FEE", "fees")
    tx(day_in(1, 25), 890, "Best Buy", "shopping", detail="Electronics")
    m.db.commit()
    Finance(m).find_subscriptions()
    return m


def test_review_numbers(mem):
    r = review.Review(mem).compute("last_month")
    assert r["spent"] == pytest.approx(1800 + 420 + 15.49 + 60 + 520 + 40 + 39 + 890)
    assert r["income"] == 4200 and r["left_over"] == pytest.approx(4200 - r["spent"])
    cats = {c["category"]: c for c in r["categories"]}
    assert cats["food"]["usual_per_month"] == pytest.approx(180, rel=0.15) and cats["food"]["change"] > 100
    assert cats["food"]["top_details"][0][0] == "Restaurant" and cats["transport"]["count"] == 2
    assert cats["fees"]["group"] == "fees" and r["fees"]["total"] == 39
    assert r["split"]["needs"] == pytest.approx(1800 + 420 + 60 + 40)
    assert r["biggest"][0]["merchant"] == "Landlord LLC"
    assert r["unusual"] == []  # Best Buy is big, but with no shopping history there's no "usual" to compare
    assert len(r["trend"]) == 6 and r["trend"][-2]["income"] == 4200


def test_ways_to_save_are_quantified_and_sorted(mem):
    adv = advisor.Advisor(mem)
    Finance(mem).card(name="Gold Card", issuer="Amex", last4="1004", apr=27.0, statement_balance=3000)
    Benefits(mem).add("Dining credit", 10, "monthly", card="Amex Gold", merchant="grubhub")
    r = review.Review(mem).compute("last_month")
    ops = {o["what"]: o for o in r["opportunities"]}
    assert ops["Eating out above your usual"]["yearly"] == pytest.approx((520 - 180) * 12, rel=0.15)
    assert ops["Fees and interest"]["yearly"] == pytest.approx(39 * 12, rel=0.1)
    assert "Amex Gold Dining credit running out" in ops
    yearly = [o["yearly"] for o in r["opportunities"]]
    assert yearly == sorted(yearly, reverse=True) and r["possible_yearly_savings"] == pytest.approx(sum(yearly))
    assert r["todo"] and all("$" in t for t in r["todo"])
    budget = {b["category"]: b for b in r["budget"]}
    assert budget["fees"]["budget"] == 0 and budget["food"]["budget"] <= 0.9 * 200
    assert adv.income() == {}  # income came from the bank, nothing had to be told


def test_report_is_saved_and_spoken(mem):
    job_id, summary, r = review.Review(mem).save("last_month")
    row = mem.db.execute("SELECT kind, title, report, summary FROM jobs WHERE id = ?", (job_id,)).fetchone()
    assert row["kind"] == "money_review" and row["title"].startswith("Money review")
    for heading in ("## Where the money went", "## Six-month trend", "## Subscriptions", "## Ways to save",
                    "## Suggested monthly budget", "## Fees and interest"):
        assert heading in row["report"]
    assert "a year you could save" in summary and "Reports" in summary
    out = json.loads(advisor.run_tool(advisor.Advisor(mem), {"action": "review", "period": "last_3_months"}))
    assert out["saved_report"] and out["possible_yearly_savings"] >= 0


def test_empty_period_says_how(mem):
    empty = Memory(":memory:")
    Money(empty)
    with pytest.raises(Exception) as e:
        review.Review(empty).save("this_month")
    assert "connect my bank" in str(e.value)
    with pytest.raises(Exception):
        review.period_bounds("fortnight", local_now().date())


def test_plaid_keeps_detail_labels_and_income(mem):
    from tests.test_plaid import FakePlaid, ENV  # noqa: PLC0415
    fake = FakePlaid()
    p = Plaid(mem, env=ENV, http=fake)
    p._transaction("item-x", {"transaction_id": "pay1", "account_id": "a", "amount": -2500, "date": day_in(0, 1),
                              "name": "ACME PAYROLL", "personal_finance_category": {"primary": "INCOME",
                                                                                    "detailed": "INCOME_WAGES"}}, {})
    p._transaction("item-x", {"transaction_id": "c1", "account_id": "a", "amount": 4.5, "date": day_in(0, 1),
                              "merchant_name": "Blue Bottle", "personal_finance_category": {
                                  "primary": "FOOD_AND_DRINK", "detailed": "FOOD_AND_DRINK_COFFEE"}}, {})
    p._transaction("item-x", {"transaction_id": "f1", "account_id": "a", "amount": 35, "date": day_in(0, 1),
                              "name": "OVERDRAFT FEE", "personal_finance_category": {
                                  "primary": "BANK_FEES", "detailed": "BANK_FEES_OVERDRAFT_FEES"}}, {})
    inc = mem.db.execute("SELECT amount, kind FROM fin_income WHERE source = 'plaid:item-x:pay1'").fetchone()
    assert inc["amount"] == 2500 and inc["kind"] == "Wages"
    coffee = mem.db.execute("SELECT category, detail FROM transactions WHERE merchant = 'Blue Bottle'").fetchone()
    assert coffee["category"] == "food" and coffee["detail"] == "Coffee"
    assert mem.db.execute("SELECT category FROM transactions WHERE merchant = 'OVERDRAFT FEE'").fetchone()[0] == "fees"
