"""Card optimizer: benefits by category, coming up, best card per spending, missed rewards, statement evidence."""

import asyncio
from datetime import date, datetime, timezone

import pytest

from buddy import benefits as bmod
from buddy.benefits import Benefits
from buddy.card_optimizer import Optimizer, categorize, transaction_kind
from buddy.memory import Memory
from buddy.money import Money
from buddy.plaid import Plaid

SRC = "https://www.americanexpress.com/plat"
CH = "https://creditcards.chase.com/x"
PLAT = {"card_name": "Platinum", "annual_fee": 895, "benefits": [
    {"name": "Uber Cash", "amount": 15, "period": "monthly", "merchant": "uber", "kind": "rides",
     "statement_words": ["uber cash"], "source": SRC},
    {"name": "Digital entertainment credit", "amount": 25, "period": "monthly", "merchant": "disney",
     "kind": "streaming", "statement_words": ["digital entertainment"], "source": SRC},
    {"name": "Hotel credit", "amount": 300, "period": "semiannual", "merchant": "amex travel", "kind": "hotel", "source": SRC}],
    "perks": [{"name": "Centurion lounge access", "source": SRC}, {"name": "Purchase protection", "source": SRC}],
    "earn_rates": [{"category": "flights booked directly", "rate": 5, "unit": "points", "reward": "MR points", "source": SRC},
                   {"category": "all other purchases", "rate": 1, "unit": "points", "reward": "MR points", "source": SRC}]}
FU = {"card_name": "Freedom Unlimited", "annual_fee": 0, "benefits": [], "perks": [{"name": "Purchase protection", "source": CH}],
      "earn_rates": [{"category": "dining", "rate": 3, "unit": "percent", "reward": "cash back", "source": CH},
                     {"category": "everything else", "rate": 1.5, "unit": "percent", "reward": "cash back", "source": CH}]}


@pytest.fixture
def setup(monkeypatch):
    monkeypatch.setattr(bmod, "local_now", lambda: datetime(2026, 11, 20, 10, 0, tzinfo=timezone.utc))
    import buddy.card_optimizer as co
    monkeypatch.setattr(co, "local_now", lambda: datetime(2026, 11, 20, 10, 0, tzinfo=timezone.utc))
    mem = Memory(":memory:")
    Plaid(mem, env={})
    mem.db.execute("INSERT INTO plaid_items (item_id, access_token, institution, added_at, env) VALUES "
                   "('a','t','American Express','2026-01-01','production'),('c','t','Chase','2026-01-01','production')")
    mem.db.execute("INSERT INTO plaid_accounts (account_id,item_id,name,mask,type) VALUES ('a1','a','Platinum Card','9001','credit'),"
                   "('c1','c','Freedom Unlimited','0457','credit')")

    async def search(memory, card):
        return FU if "freedom" in card.lower() else PLAT
    b = Benefits(mem, search=search)
    for card in [c["card"] for c in b.connected_cards()]:
        asyncio.run(b.lookup(card))
    plat = next(c["card"] for c in b.connected_cards() if "Platinum" in c["card"])
    b.add_from_catalog(plat, ["Uber Cash", "Digital entertainment credit"])
    m = Money(mem)
    m.add_transaction(-15, "USD", "AMEX UBER CASH CREDIT", "transport", "2026-11-03", card="9001", source="t1")
    m.add_transaction(60, "USD", "SON CUBANO", "food", "2026-11-10", card="9001", source="t2")       # dining on the Plat
    m.add_transaction(-300, "USD", "AMEX HOTEL CREDIT", "travel", "2026-08-02", card="9001", source="t3")
    return mem, b


def test_benefits_by_category_with_expiring_badges(setup):
    mem, b = setup
    view = Optimizer(b).view(date(2026, 11, 20))
    cats = {g["category"]: g for g in view["browse"]}
    assert {"Travel", "Entertainment", "Protection & assistance"} <= set(cats)
    subs = {s["name"]: s for s in cats["Entertainment"]["subs"]}
    assert subs["Streaming services"]["year_value"] == 300 and subs["Streaming services"]["expiring"] == 1
    prot = {s["name"]: s for s in cats["Protection & assistance"]["subs"]}
    assert prot["Purchase protection"]["count"] == 2 and prot["Purchase protection"]["cards"] == 2
    assert categorize("Centurion lounge access") == ("Travel", "Airport lounge")


def test_coming_up_and_plaid_evidence_for_tracked_and_untracked_credits(setup):
    mem, b = setup
    view = Optimizer(b).view(date(2026, 11, 20))
    names = [x["name"] for x in view["coming_up"]]
    assert names == ["Digital entertainment credit"]            # Uber Cash used this month: nothing left
    plat = next(c for c in view["cards"] if "Platinum" in c["card"])
    uber = next(x for x in plat["credits"] if x["name"] == "Uber Cash")
    assert uber["credits_seen"] == [{"day": "2026-11-03", "amount": 15.0}]
    hotel = next(x for x in plat["credits"] if x["name"] == "Hotel credit")   # not tracked: checked anyway
    assert hotel.get("seen") == [] and hotel["statement_through"] == "2026-11-10"


def test_best_card_per_spending_and_missed_rewards(setup):
    mem, b = setup
    view = Optimizer(b).view(date(2026, 11, 20))
    guide = {g["kind"]: g for g in view["earn_guide"]}
    assert guide["dining"]["best"]["card"].endswith("Freedom Unlimited") and guide["dining"]["best"]["value"] == 3
    assert guide["other"]["best"]["value"] == 1.5
    assert any("directly" in l["category"] for l in guide["travel"]["limited"])   # 5x flights booked directly: "up to"
    missed = view["missed"]
    assert missed["items"][0]["merchant"] == "Son Cubano" and missed["items"][0]["missed"] == 1.2   # 60 x (3% - 1%)
    assert view["summary"]["missed_30d"] == 1.2
    assert transaction_kind({"category": "transport", "merchant": "Shell Oil"}) == "gas"
