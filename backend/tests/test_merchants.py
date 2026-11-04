"""Merchant names: one place, one name, whatever the bank calls it."""

import json

from buddy import merchants as mm
from buddy.memory import Memory
from buddy.merchants import Merchants
from buddy.money import Money


def names(mem, *raws, **kw):
    m = Merchants(mem)
    return [m.resolve(r, **kw) for r in raws]


def test_one_restaurant_many_bank_spellings():
    mem = Memory(":memory:")
    got = names(mem, "SON CUBANO RESTAURANT ON THE HUDSON", "SQ *SON CUBANO", "TST* SON CUBANO 0042 WEST NEW YORK NJ",
                "Son Cubano")
    again = names(mem, "SON CUBANO RESTAURANT ON THE HUDSON", "SQ *SON CUBANO")
    assert set(got[1:]) == set(again) == {"Son Cubano"}, (got, again)  # the group keeps the shortest name


def test_brands_and_processors_are_cleaned_but_distinct_services_stay_apart():
    mem = Memory(":memory:")
    assert set(names(mem, "AMZN Mktp US*2K4AB1", "Amazon.com*RT5XY", "AMAZON MKTPL*ZZ")) == {"Amazon"}
    assert names(mem, "UBER *EATS PENDING", "UBER *TRIP HELP.UBER.COM") == ["Uber Eats", "Uber"]
    assert names(mem, "NETFLIX.COM 866-579-7172 CA") == ["Netflix"]
    assert names(mem, "PAYPAL *SPOTIFY P1A2B3") == ["Spotify"]
    assert names(mem, "LA MAR CEBICHERÍA") == ["La Mar Cebichería"]  # letters in any language are kept


def test_plaids_merchant_id_wins_over_spelling():
    mem = Memory(":memory:")
    a = Merchants(mem).resolve("BLUE BTL COFFEE 1234 NEW YORK NY", entity_id="ent-42", plaid_name="Blue Bottle Coffee")
    b = Merchants(mem).resolve("BBC MOBILE ORDER", entity_id="ent-42")
    assert a == b == "Blue Bottle Coffee"


def test_new_transactions_keep_the_bank_text_and_get_the_clean_name():
    mem = Memory(":memory:")
    money = Money(mem)
    money.add_transaction(118, "USD", "SON CUBANO RESTAURANT ON THE HUDSON", "food", "2026-10-27", source="t:1")
    money.add_transaction(38, "USD", "SQ *SON CUBANO", "food", "2026-10-24", source="t:2")
    rows = mem.db.execute("SELECT merchant, merchant_raw FROM transactions ORDER BY id").fetchall()
    assert rows[0]["merchant"] == rows[1]["merchant"]
    assert [r["merchant_raw"] for r in rows] == ["SON CUBANO RESTAURANT ON THE HUDSON", "SQ *SON CUBANO"]


def test_the_users_corrections_win_and_are_applied_to_past_and_future_charges():
    mem = Memory(":memory:")
    money = Money(mem)
    money.add_transaction(20, "USD", "JOES PIZZA 22", "food", "2026-10-01", source="t:1")
    money.add_transaction(25, "USD", "JOE'S FAMOUS PIZZA", "food", "2026-10-02", source="t:2")
    out = mm.run_tool(mem, {"action": "merge", "names": ["Joes Pizza", "Joe's Famous Pizza"], "into": "Joe's Pizza"})
    assert "Joe's Pizza" in out
    assert {r[0] for r in mem.db.execute("SELECT merchant FROM transactions")} == {"Joe's Pizza"}
    money.add_transaction(22, "USD", "JOE'S FAMOUS PIZZA", "food", "2026-10-09", source="t:3")
    assert mem.db.execute("SELECT merchant FROM transactions WHERE source = 't:3'").fetchone()[0] == "Joe's Pizza"
    top = json.loads(mm.run_tool(mem, {"action": "list"}))
    assert top[0]["merchant"] == "Joe's Pizza" and top[0]["transactions"] == 3 and len(top[0]["bank_spellings"]) == 2


def test_stored_transactions_are_cleaned_once():
    mem = Memory(":memory:")
    Money(mem)
    for i, raw in enumerate(("AMZN Mktp US*1", "Amazon.com*2", "SQ *SON CUBANO", "SON CUBANO RESTAURANT ON THE HUDSON")):
        mem.db.execute("INSERT INTO transactions (day, amount, currency, merchant, category, source, created_at) "
                       "VALUES ('2026-10-01', 10, 'USD', ?, 'food', ?, '2026-10-01')", (raw, f"old:{i}"))
    changed = Merchants(mem).reclean()
    assert changed >= 3
    assert len({r[0] for r in mem.db.execute("SELECT merchant FROM transactions")}) == 2
    assert mem.db.execute("SELECT merchant_raw FROM transactions WHERE source = 'old:0'").fetchone()[0] == "AMZN Mktp US*1"
