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
    for heading in ("## Where the money went", "## Six-month trend", "## Subscription tracker", "## Ways to save",
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


def test_transfers_housing_and_cards(mem):
    from types import SimpleNamespace  # noqa: F401
    p = Plaid(mem, env={})
    mem.db.execute("INSERT INTO plaid_accounts (account_id, item_id, name, mask, type) VALUES "
                   "('chk', 'i', 'Everyday Checking', '5678', 'depository'), ('amx', 'i', 'Amex Gold Card', '1004', 'credit')")
    accounts = {"chk": {"mask": "5678", "name": "Everyday Checking"}, "amx": {"mask": "1004"}}
    def t(tid, amount, name, primary, detailed, acct="chk", merchant=None):
        p._transaction("i", {"transaction_id": tid, "account_id": acct, "amount": amount, "date": day_in(1, 10),
                             "name": name, "merchant_name": merchant,
                             "personal_finance_category": {"primary": primary, "detailed": detailed}}, accounts)
    t("z1", 300, "Zelle payment to NEHA SHARMA Conf# 123", "TRANSFER_OUT", "TRANSFER_OUT_ACCOUNT_TRANSFER")
    t("z2", 200, "Zelle payment to NEHA SHARMA Conf# 456", "TRANSFER_OUT", "TRANSFER_OUT_ACCOUNT_TRANSFER")
    t("r1", 1000, "REMITLY* SEND", "TRANSFER_OUT", "TRANSFER_OUT_OTHER_TRANSFER_OUT", merchant="Remitly")
    t("s1", 500, "Online Transfer to SAV ...9911", "TRANSFER_OUT", "TRANSFER_OUT_SAVINGS")
    t("m1", 2100, "LOAN SERVICING MORTGAGE", "LOAN_PAYMENTS", "LOAN_PAYMENTS_MORTGAGE_PAYMENT")
    t("cc", 900, "AMEX EPAYMENT", "LOAN_PAYMENTS", "LOAN_PAYMENTS_CREDIT_CARD_PAYMENT")
    mem.db.commit()
    kinds = {r["recipient"]: r["kind"] for r in mem.db.execute("SELECT recipient, kind FROM fin_transfers")}
    assert kinds == {"Neha Sharma": "person", "Remitly": "abroad", "Savings and investments": "own"}
    assert mem.db.execute("SELECT category FROM transactions WHERE merchant LIKE '%MORTGAGE%'").fetchone()[0] == "housing"
    assert not mem.db.execute("SELECT 1 FROM transactions WHERE merchant = 'AMEX EPAYMENT'").fetchone()  # not spending
    r = review.Review(mem).compute("last_month")
    tr = r["transfers"]
    assert tr["to_people"] == 500 and tr["abroad"] == 1000 and tr["to_savings"] == 500
    neha = next(x for x in tr["recipients"] if x["to"] == "Neha Sharma")
    assert neha["total"] == 500 and neha["count"] == 2 and neha["from"] == ["Everyday Checking ••5678"]
    assert r["left_over"] == pytest.approx(4200 - r["spent"] - 1500)  # sent away is gone; savings still yours
    house = dict(r["house_by_card"])
    assert house["Everyday Checking ••5678"] == 2100 and house["Amex Gold Card ••1004"] == 1800
    assert r["cards_used"][0]["card"].startswith("Amex Gold Card")
    md = review.Review(mem).markdown(r)
    for part in ("## Money in and out", "Sent to people", "Sent abroad", "## By card and account",
                 "House expenses", "**Neha Sharma**", "abroad via **Remitly**", "## Waste and leaks",
                 "## Subscription tracker", "Fee: "):
        assert part in md, part


def test_advisor_notes_use_the_numbers_and_never_block_the_report(mem, monkeypatch):
    import asyncio
    from types import SimpleNamespace
    seen = {}

    async def create(**kw):
        seen.update(kw)
        return SimpleNamespace(model="claude-sonnet-5-5", stop_reason="end_turn",
                               usage=SimpleNamespace(input_tokens=900, output_tokens=300, cache_read_input_tokens=0,
                                                     cache_creation_input_tokens=0, server_tool_use=None),
                               content=[SimpleNamespace(type="text", text="**Overall** You kept 10%.")])
    monkeypatch.setattr(review, "client", SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(create=create))))
    job_id, summary, r = asyncio.run(review.Review(mem).save_full("last_month"))
    report = mem.db.execute("SELECT report FROM jobs WHERE id = ?", (job_id,)).fetchone()[0]
    assert "## Advisor's review" in report and "You kept 10%" in report
    assert "ONLY the numbers" in seen["system"] and '"possible_yearly_savings"' in seen["messages"][0]["content"]

    async def broken(**kw):
        raise RuntimeError("down")
    monkeypatch.setattr(review, "client", SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(create=broken))))
    job_id, _, _ = asyncio.run(review.Review(mem).save_full("last_month"))
    report = mem.db.execute("SELECT report FROM jobs WHERE id = ?", (job_id,)).fetchone()[0]
    assert "## Where the money went" in report and "## Advisor's review" not in report


def test_rent_by_zelle_and_family_support(mem):
    p = Plaid(mem, env={})
    mem.db.execute("INSERT INTO plaid_accounts (account_id, item_id, name, mask, type) VALUES "
                   "('chk', 'i', 'Everyday Checking', '5678', 'depository')")
    accounts = {"chk": {"mask": "5678", "name": "Everyday Checking"}}
    for off in (4, 3, 2, 1):  # the same Zelle to the landlord and Remitly home every month
        for tid, amount, name, merchant in ((f"z{off}", 1950, "Zelle payment to RAVI KUMAR Conf# 9", None),
                                             (f"r{off}", 800, "REMITLY* SEND", "Remitly")):
            p._transaction("i", {"transaction_id": tid, "account_id": "chk", "amount": amount, "date": day_in(off, 2),
                                 "name": name, "merchant_name": merchant, "personal_finance_category": {
                                     "primary": "TRANSFER_OUT", "detailed": "TRANSFER_OUT_ACCOUNT_TRANSFER"}}, accounts)
    mem.db.commit()
    rv = review.Review(mem)
    before = rv.compute("last_month")
    qs = " ".join(before["transfers"]["questions"])
    assert "Ravi Kumar every month your rent?" in qs and "Remitly every month your parents'" in qs
    assert before["transfers"]["to_people"] == 1950  # not yet known: shown as a transfer to a person

    assert "counted as housing" in advisor.run_tool(advisor.Advisor(mem), {"action": "label_transfer",
                                                                          "what": "ravi kumar", "label": "rent"})
    out = advisor.run_tool(advisor.Advisor(mem), {"action": "label_transfer", "what": "remitly",
                                                  "label": "family support", "name": "parents' maintenance"})
    assert "never as waste" in out and "parents' maintenance" in out
    r = review.Review(mem).compute("last_month")
    housing = next(c for c in r["categories"] if c["category"] == "housing")
    assert housing["total"] == 1950 and housing["usual_per_month"] == pytest.approx(1950)
    assert dict(r["house_by_card"])["Everyday Checking ••5678"] == 1950
    assert r["spent"] == pytest.approx(before["spent"] + 1950)
    t = r["transfers"]
    assert t["to_people"] == 0 and t["family"] == 800 and t["questions"] == []
    assert r["left_over"] == pytest.approx(before["left_over"])  # same money out, now counted the right way
    md = review.Review(mem).markdown(r)
    assert "Family support" in md and "family support via **Remitly** (parents' maintenance)" in md
    assert not any("Remitly" in w["what"] for w in r["waste"])
    assert not any("Remitly" in o["what"] or "Family" in o["what"] for o in r["opportunities"])


def test_one_label_for_every_remittance_service(mem):
    p = Plaid(mem, env={})
    for tid, name, merchant in (("a", "REMITLY* SEND", "Remitly"), ("b", "WISE US INC", "Wise"),
                                ("c", "WESTERN UNION 123", "Western Union")):
        p._transaction("i", {"transaction_id": tid, "account_id": "x", "amount": 300, "date": day_in(1, 5), "name": name,
                             "merchant_name": merchant, "personal_finance_category": {
                                 "primary": "TRANSFER_OUT", "detailed": "TRANSFER_OUT_OTHER_TRANSFER_OUT"}}, {})
    out = review.Review(mem).label_transfer("remit", "family_support", "parents' maintenance")
    assert "all remittances" in out
    t = review.Review(mem).compute("last_month")["transfers"]
    assert t["family"] == 900 and t["abroad"] == 0
