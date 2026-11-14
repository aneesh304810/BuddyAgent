"""Shopping profile: the interview, purchase history from order emails, and what Buddy learns (no network)."""

import json
from datetime import timedelta

import pytest

from buddy import shopper
from buddy.connectors.hub import ConnectorHub
from buddy.memory import Memory
from buddy.tools import ToolError, local_now


@pytest.fixture
def memory(tmp_path):
    m = Memory(tmp_path / "s.db")
    ConnectorHub(m)  # the records table
    return m


def email(m, subject, sender, body="", days_ago=0):
    day = (local_now() - timedelta(days=days_ago)).isoformat()
    m.db.execute("INSERT INTO records (source, external_id, kind, title, body, start_at, data, synced_at) "
                 "VALUES ('email', ?, 'email', ?, ?, ?, ?, ?)",
                 (f"<{subject}{days_ago}@x>", subject, body, day, json.dumps({"from": sender}), day))
    m.db.commit()


def test_interview_goes_general_first_and_saves_answers(memory):
    q = shopper.next_questions(memory)
    assert q["kind"] == "general" and len(q["questions"]) == 2 and "one at a time" in q["how"]
    shopper.save(memory, "general", {"research_style": ["Reddit", "YouTube"], "patience": "weeks for a good deal"},
                 ["first_step", "buy_now"])
    shopper.save(memory, "general", {"research_style": ["reddit", "RTINGS"]}, ["regret", "refurb"])
    prof, done = shopper.profile(memory, "general")
    assert prof["research_style"] == ["Reddit", "YouTube", "RTINGS"]  # merged, no repeats
    assert set(done) == {"first_step", "buy_now", "regret", "refurb"}
    assert shopper.next_questions(memory)["kind"] == "electronics"  # general finished: the next kind
    with pytest.raises(ToolError):
        shopper.save(memory, "electronics", {"favourite_colour": "blue"})
    with pytest.raises(ToolError):
        shopper.save(memory, "cars", {"notes": "x"})


def test_order_and_return_emails_become_lasting_history(memory):
    email(memory, 'Your Amazon.com order of "Sony WH-1000XM6 Wireless Headphones" has shipped',
          "Amazon.com <shipment-tracking@amazon.com>", "Order total: $329.99\nYou saved $70.00", days_ago=10)
    email(memory, "Order confirmation #1234", "Best Buy <BestBuyInfo@emailinfo.bestbuy.com>",
          "Grand Total: $1,199.00 for LG OLED TV", days_ago=40)
    email(memory, "Your return received: Dyson V15 vacuum", "Target <orders@target.com>", "", days_ago=5)
    email(memory, "Price drop on items in your cart", "Amazon.com <store-news@amazon.com>", "")
    email(memory, "Your Delta eTicket Receipt", "Delta Air Lines <DeltaAirLines@t.delta.com>", "Total: $412.30",
          days_ago=20)
    email(memory, "Lunch on Friday?", "Sam <sam@x.com>", "")
    assert shopper.scan_orders(memory) == 4
    assert shopper.scan_orders(memory) == 0  # each email read once
    rows = {r["item"] or r["merchant"]: dict(r) for r in memory.db.execute("SELECT * FROM shop_purchases")}
    sony = rows["Sony WH-1000XM6 Wireless Headphones"]
    assert sony["kind"] == "electronics" and sony["amount"] == 329.99 and sony["on_sale"] == 1
    assert rows["Best Buy"]["kind"] == "electronics" and rows["Best Buy"]["amount"] == 1199.0
    assert any(r["event"] == "return" for r in rows.values())
    assert rows["Delta Air Lines"]["kind"] == "travel"


def test_learned_summary_and_time_to_decide(memory):
    shopper.considering(memory, "Sony WH-1000XM6 headphones", "electronics")
    memory.db.execute("UPDATE shop_considering SET since = ?", ((local_now() - timedelta(days=21)).date().isoformat(),))
    memory.db.commit()
    email(memory, 'Ordered: "Sony WH-1000XM6 Wireless Headphones"', "Amazon.com <auto-confirm@amazon.com>",
          "Order Total: $299.00 Lightning Deal", days_ago=0)
    shopper.scan_orders(memory)
    got = shopper.learned(memory, "electronics")
    assert got["days_to_decide_median"] == 21 and got["bought_on_sale_pct"] == 100
    assert got["brands_bought"] == ["Sony"] and got["typical_spend"] == 299.0 and "Amazon.com" in got["top_shops"]
    out = json.loads(shopper.run_tool(memory, {"action": "show", "kind": "electronics"}))
    assert "learned_from_purchases" in out["electronics"] and "Never buy" in out["how_to_use"]


def test_kind_of_from_item_then_store():
    assert shopper.kind_of("Ninja air fryer") == "home"
    assert shopper.kind_of("Levi's 501 jeans") == "clothing"
    assert shopper.kind_of("", "Home Depot") == "home"
    assert shopper.kind_of("gift card", "Amazon") == "other"


def test_forget_keeps_history(memory):
    shopper.save(memory, "travel", {"loyalty": ["Delta SkyMiles"]}, ["loyalty"])
    assert "Forgot your travel" in shopper.run_tool(memory, {"action": "forget", "kind": "travel"})
    assert shopper.profile(memory, "travel") == ({}, [])


def test_shop_for_researches_the_owners_way(memory):
    from buddy import research
    from buddy.jobs import JobRunner, run_job_tool

    shopper.save(memory, "general", {"research_style": ["Reddit"], "patience": "a few weeks"}, ["first_step"])
    shopper.save(memory, "electronics", {"trusted_sources": ["RTINGS"], "avoid_brands": ["Beats"],
                                         "must_haves": ["noise cancelling"], "target_discount_pct": 20}, ["sources"])
    email(memory, 'Ordered: "Sony WF-1000XM5 earbuds"', "Amazon.com <auto-confirm@amazon.com>",
          "Order Total: $228.00 You saved $72", days_ago=60)

    async def finished(job):
        return True
    runner = JobRunner(memory, finished)
    runner.register("research", research.run_research)
    out = run_job_tool(runner, "shop_for", {"what": "over-ear headphones for flights", "kind": "electronics",
                                            "depth": "quick"})
    assert "started" in out
    params = runner.get(runner.list()[0]["id"])["params"]
    assert params["mode"] == "buy" and params["category"] == "electronics"
    assert "Avoids brands (electronics): Beats" in params["profile"] and "Researches by: Reddit" in params["profile"]
    assert "Waits for a discount of (%) (electronics): 20" in params["profile"]
    assert "usually buys at Amazon.com" in params["profile"] and "100% of orders on sale" in params["profile"]
    first = research._first_message(params)
    assert first.startswith("Help me decide what to buy: over-ear headphones") and "follow it" in first
    system = research._system("buy", "electronics")
    assert "research THEIR way" in system and "never put it in searches" in system
    considered = memory.db.execute("SELECT item, kind FROM shop_considering").fetchone()
    assert considered["item"] == "over-ear headphones for flights" and considered["kind"] == "electronics"
    runner.cancel(runner.list()[0]["id"])


def test_brief_without_a_profile_suggests_the_interview(memory):
    assert "shopping interview" in shopper.brief(memory, "home")


def test_checkin_two_weeks_after_once_a_day_and_review(memory):
    from datetime import datetime as dt
    email(memory, 'Ordered: "Dyson V15 Detect vacuum"', "Amazon.com <auto-confirm@amazon.com>",
          "Order Total: $649.00", days_ago=16)
    email(memory, 'Ordered: "USB-C cable 2-pack"', "Amazon.com <auto-confirm@amazon.com>", "Order Total: $12.99",
          days_ago=16)
    email(memory, 'Ordered: "Levi\'s 501 jeans"', "Levi's <orders@levi.com>", "Order Total: $79.50", days_ago=3)
    shopper.scan_orders(memory)
    noon = dt.combine(local_now().date(), dt.min.time()).replace(hour=12)
    p = shopper.checkin_due(memory, noon)
    assert p["item"] == "Dyson V15 Detect vacuum"  # cheap things and too-recent ones wait
    assert shopper.checkin_due(memory, noon.replace(hour=22)) is None  # daytime only
    text = shopper.checkin_text(p, noon)
    assert "how's the Dyson V15 Detect vacuum" in text and "return window" in text
    shopper.asked(memory, p["id"], noon)
    assert shopper.checkin_due(memory, noon) is None  # one a day
    out = shopper.run_tool(memory, {"action": "review", "item": "", "verdict": "regret",
                                    "note": "loses suction on carpet"})
    assert "regret" in out and "regrets" in out
    prof, _ = shopper.profile(memory, "home")
    assert prof["regrets"] == ["Dyson V15 Detect vacuum: loses suction on carpet"]
    sat = shopper.learned(memory, "home")["after_buying"]
    assert sat["regret"] == 1 and "Dyson" in sat["regretted"][0]
    with pytest.raises(ToolError):
        shopper.review(memory, "Dyson", "meh")


def test_returned_items_are_not_checked_in(memory):
    from datetime import datetime as dt
    email(memory, 'Ordered: "Bose QuietComfort Ultra headphones"', "Amazon.com <auto-confirm@amazon.com>",
          "Order Total: $429.00", days_ago=20)
    email(memory, "Your return received: Bose QuietComfort Ultra headphones", "Amazon.com <return@amazon.com>",
          "", days_ago=2)
    shopper.scan_orders(memory)
    noon = dt.combine(local_now().date(), dt.min.time()).replace(hour=12)
    assert shopper.checkin_due(memory, noon) is None
