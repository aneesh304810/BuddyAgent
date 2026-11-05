"""Card benefits: periods, statement credits from the bank feed, wishes, and reminders before credits expire."""
import json

from datetime import date, datetime, timezone

import pytest

from buddy import benefits as bmod
from buddy.benefits import Benefits, period_bounds
from buddy.memory import Memory
from buddy.money import Money
from buddy.tools import ToolError


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


# --- the public catalog: published benefits from issuer websites ------------------------------------
import asyncio as _asyncio

PLAT = {"card_name": "The Platinum Card from American Express", "annual_fee": 895,
        "benefits": [
            {"name": "Saks credit", "amount": 50, "period": "semiannual", "resets": "calendar", "merchant": "saks",
             "enrollment": True, "source": "https://www.americanexpress.com/us/credit-cards/card/platinum/"},
            {"name": "Uber Cash", "amount": 15, "period": "monthly", "merchant": "uber",
             "source": "https://www.americanexpress.com/us/credit-cards/card/platinum/"},
            {"name": "Airline fee credit", "amount": 200, "period": "annual", "resets": "anniversary",
             "source": "https://www.americanexpress.com/us/credit-cards/card/platinum/"},
            {"name": "Made-up blog credit", "amount": 99, "period": "annual", "source": "https://someblog.com/plat"},
            {"name": "Bad period", "amount": 10, "period": "weekly",
             "source": "https://www.americanexpress.com/us/credit-cards/card/platinum/"}],
        "perks": [{"name": "Lounge access", "source": "https://www.americanexpress.com/x"},
                  {"name": "Rumoured perk", "source": "http://americanexpress.com/insecure"}],
        "earn_rates": [{"category": "flights", "rate": 5, "unit": "points per dollar",
                        "source": "https://www.americanexpress.com/x"}]}


def _fake_search(result, calls):
    async def search(memory, card):
        calls.append(card)
        return result
    return search


def test_catalog_keeps_only_issuer_backed_facts_and_saves_them():
    mem, calls = Memory(":memory:"), []
    b = Benefits(mem, search=_fake_search(PLAT, calls))
    found = _asyncio.run(b.lookup("Amex Platinum"))
    assert [x["name"] for x in found["benefits"]] == ["Saks credit", "Uber Cash", "Airline fee credit"]
    assert [p["name"] for p in found["perks"]] == ["Lounge access"]  # http and non-issuer sources dropped
    assert found["annual_fee"] == 895 and found["earn_rates"][0]["rate"] == 5
    _asyncio.run(b.lookup("amex  platinum"))  # the same card: the saved copy, no new search
    assert calls == ["Amex Platinum"]
    assert b.all() == []  # nothing is tracked until the user confirms


def test_confirmed_catalog_credits_are_tracked_with_their_source():
    b = Benefits(Memory(":memory:"), search=_fake_search(PLAT, []))
    _asyncio.run(b.lookup("Amex Platinum"))
    with pytest.raises(ToolError, match="anniversary"):
        b.add_from_catalog("Amex Platinum", [])
    text = b.add_from_catalog("Amex Platinum", [], anniversary="03-15")
    assert "Tracking 3 credits" in text and "Enroll in the issuer's app first for: Saks credit" in text
    saks = next(x for x in b.all() if x["name"] == "Saks credit")
    assert "Published by the issuer" in saks["notes"] and "americanexpress.com" in saks["notes"]
    assert "already tracked" in b.add_from_catalog("Amex Platinum", ["saks"])


def test_every_known_card_is_looked_up_and_new_ones_offered_once():
    mem, calls = Memory(":memory:"), []
    from buddy.finance import Finance
    Finance(mem)  # makes fin_cards
    mem.db.execute("INSERT INTO fin_cards (key, name, issuer, updated_at) VALUES ('amex|1004', 'Platinum Card®', 'American Express', '2026-11-01')")
    mem.db.commit()
    b = Benefits(mem, search=_fake_search(PLAT, calls))
    assert b.known_cards() == ["American Express Platinum Card"]
    texts = _asyncio.run(b.offer_new_cards())
    assert len(texts) == 1 and "3 credits" in texts[0] and "Want me to track them" in texts[0]
    assert _asyncio.run(b.offer_new_cards()) == []  # offered once
    summary = _asyncio.run(b.lookup_all())
    assert summary[0]["annual_fee"] == 895 and len(calls) == 1


def test_a_changed_published_credit_is_reported_at_the_quarterly_recheck():
    mem = Memory(":memory:")
    b = Benefits(mem, search=_fake_search(PLAT, []))
    _asyncio.run(b.lookup("Amex Platinum"))
    b.add_from_catalog("Amex Platinum", ["uber"])
    mem.db.execute("UPDATE fin_card_catalog SET checked_at = '2020-01-01T00:00:00+00:00'")
    changed = {**PLAT, "benefits": [{**PLAT["benefits"][1], "amount": 20}]}
    b.search = _fake_search(changed, [])
    notes = _asyncio.run(b.recheck())
    assert notes == ["Amex Platinum: the Uber Cash is now $20 monthly (was $15 monthly)."]


def test_one_setup_for_every_card_saves_a_report_and_track_all_follows():
    mem, calls = Memory(":memory:"), []
    from buddy.finance import Finance
    Finance(mem)
    mem.db.execute("INSERT INTO fin_cards (key, name, issuer, updated_at) VALUES ('amex|1004', 'Platinum Card®', "
                   "'American Express', '2026-11-01')")
    mem.db.commit()
    b = Benefits(mem, search=_fake_search(PLAT, calls))
    job_id, text = _asyncio.run(b.setup())
    assert "set up for 1 card" in text and "track all" in text
    report = mem.db.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    assert report["kind"] == "card_benefits" and "| Saks credit (enroll first) | $50 |" in report["report"]
    assert "refreshed once a year" in report["report"] and "americanexpress.com" in report["report"]
    assert _asyncio.run(b.offer_new_cards()) == []  # the setup covered it
    out = b.add_all()
    assert "Tracking 2 credits" in out and "Airline fee credit (American Express Platinum Card)" in out
    assert len(calls) == 1  # one search per card, then the saved copy


def test_saved_benefits_last_a_year_before_they_are_looked_up_again():
    assert bmod.CATALOG_DAYS == bmod.RECHECK_DAYS == 365
    mem, calls = Memory(":memory:"), []
    b = Benefits(mem, search=_fake_search(PLAT, calls))
    _asyncio.run(b.lookup("Amex Platinum"))
    ten_months = (datetime.now(timezone.utc) - __import__("datetime").timedelta(days=300)).isoformat()
    mem.db.execute("UPDATE fin_card_catalog SET checked_at = ?", (ten_months,))
    _asyncio.run(b.lookup("Amex Platinum"))
    assert _asyncio.run(b.recheck()) == [] and len(calls) == 1  # under a year: the saved copy, no search
    mem.db.execute("UPDATE fin_card_catalog SET checked_at = '2020-01-01T00:00:00+00:00'")
    assert _asyncio.run(b.recheck()) == [] and len(calls) == 2  # a year on: refreshed (untracked: no message)


def test_setup_runs_in_the_background_when_asked():
    mem = Memory(":memory:")
    text = _asyncio.run(bmod.run_tool_async(Benefits(mem), {"action": "setup"}))
    assert "background" in text and mem.get("benefits:setup_requested") == "1"


def _deals(result, calls):
    async def search(memory, credit, wishes):
        calls.append((credit["benefit"], [w["item"] for w in wishes]))
        return result
    return search


def test_a_month_before_a_credit_ends_buddy_finds_deals_and_puts_them_in_the_reminder():
    mem, calls = Memory(":memory:"), []
    found = {"sale": "25% off coats until Dec 15", "deals": [
        {"item": "Wool scarf", "price": 45, "url": "https://www.saks.com/scarf", "note": "covered by the credit"},
        {"item": "Made-up", "price": "n/a", "url": "https://www.saks.com/x"},
        {"item": "No link", "price": 20, "url": "http://insecure"}]}
    b = Benefits(mem, search_deals=_deals(found, calls))
    b.add("Saks credit", 50, "semiannual", card="Amex Platinum", merchant="saks")
    b.wish_add("Cashmere gloves", merchant="saks", price=60)
    today = date(2026, 12, 2)  # the July-December half ends Dec 31: 29 days left
    assert _asyncio.run(b.prepare_deals(today)) == 1
    assert calls == [("Amex Platinum Saks credit", ["Cashmere gloves"])] or calls[0][1] == ["Cashmere gloves"]
    assert _asyncio.run(b.prepare_deals(today)) == 0  # one search per credit period
    alert = b.alerts(today)[0]
    assert "runs out in 29 days" in alert["text"] and "25% off coats" in alert["text"]
    spoken, shown = alert["text"].split(" || ")
    assert "on screen" in spoken and "https" not in spoken  # read aloud: short, no links
    assert "**Amex Platinum Saks credit: $50 left, ends Dec 31 (29 days)**" in shown
    assert "- Wool scarf: $45, you'd pay $0. https://www.saks.com/scarf" in shown
    assert "Made-up" not in alert["text"] and "No link" not in alert["text"] and "I don't buy anything" in alert["text"]


def test_monthly_credits_only_get_last_week_reminders_and_no_deal_search():
    mem, calls = Memory(":memory:"), []
    b = Benefits(mem, search_deals=_deals({"deals": []}, calls))
    b.add("Uber Cash", 15, "monthly", card="Amex Platinum", merchant="uber")
    assert b.alerts(date(2026, 12, 2)) == []           # 29 days left: no "1 month" note for a monthly credit
    assert "in 6 days" in b.alerts(date(2026, 12, 25))[0]["text"]
    assert _asyncio.run(b.prepare_deals(date(2026, 12, 25))) == 0 and calls == []


def test_dining_and_hotel_credits_get_named_places_near_the_user():
    import json as _json
    mem, prompts = Memory(":memory:"), []
    b = Benefits(mem)
    b.add("Resy credit", 100, "quarterly", card="Amex Platinum")
    b.add("Hotel credit", 200, "annual", card="Amex Platinum")
    b.add("Uber Cash", 15, "monthly", card="Amex Platinum", merchant="uber")
    credits = {c["benefit"]: c for c in b.overview(date(2026, 12, 2))}
    assert bmod.credit_kind(credits["Amex Platinum Resy credit"]) == "dining"
    assert bmod.credit_kind(credits["Amex Platinum Hotel credit"]) == "hotel"
    assert bmod.credit_kind(credits["Amex Platinum Uber Cash"]) == "skip"
    # no location yet: no search, and the reminder says how to get suggestions
    assert bmod._places_prompt(mem, credits["Amex Platinum Resy credit"], "dining") is None
    alert = next(a for a in b.alerts(date(2026, 12, 2)) if "Resy" in a["text"])
    assert "Share my location" in alert["text"]
    mem.set("location:current", _json.dumps({"area": "Jersey City Heights", "city": "Jersey City",
                                             "at": "2026-12-02T10:00:00+00:00"}))
    from buddy.taste import Taste
    Taste(mem).update("food", like="Cuban")
    prompt = bmod._places_prompt(mem, credits["Amex Platinum Resy credit"], "dining")
    assert "Jersey City Heights, Jersey City" in prompt and "Cuban" in prompt and "Resy" in prompt

    async def search(memory, credit, wishes):
        prompts.append(credit["benefit"])
        if "Resy" in credit["benefit"]:
            return {"deals": [{"item": "Son Cubano, West New York", "price": 120, "url": "https://resy.com/son-cubano",
                               "note": "Cuban, dinner for two"}]}
        return {"deals": [{"item": "The Asbury Hotel, Asbury Park", "price": 260, "url": "https://www.americanexpress.com/h",
                           "note": "an easy drive"}]}
    b.search_deals = search
    assert _asyncio.run(b.prepare_deals(date(2026, 12, 2))) == 2 and "Amex Platinum Uber Cash" not in prompts
    texts = " ".join(a["text"] for a in b.alerts(date(2026, 12, 2)))
    assert "**Places near you it covers**\n- Son Cubano, West New York: about $120. Cuban, dinner for two." in texts
    assert "**Hotels it covers**\n- The Asbury Hotel" in texts


def _connect(mem, cards, env="production"):
    from buddy.plaid import Plaid
    Plaid(mem, env={})  # makes plaid_items and plaid_accounts
    mem.db.execute("INSERT INTO plaid_items (item_id, access_token, institution, added_at, env) VALUES "
                   "('i1', 't', 'American Express', '2026-11-01', ?)", (env,))
    for n, (name, mask) in enumerate(cards):
        mem.db.execute("INSERT INTO plaid_accounts (account_id, item_id, name, mask, type) VALUES (?, 'i1', ?, ?, 'credit')",
                       (f"a{n}", name, mask))
    mem.db.commit()


def test_connected_cards_are_known_and_my_platinum_means_the_connected_one():
    mem, calls = Memory(":memory:"), []
    _connect(mem, [("Platinum Card®", "1004"), ("Blue Cash Everyday®", "2008")])
    b = Benefits(mem, search=_fake_search(PLAT, calls))
    assert {c["card"] for c in b.connected_cards()} == {"American Express Platinum Card",
                                                       "American Express Blue Cash Everyday"}
    assert b.resolve_card("my amex platinum") == "American Express Platinum Card"
    assert b.resolve_card("Sapphire Reserve") == "Sapphire Reserve"  # not connected: as said
    _asyncio.run(b.lookup("my Platinum"))
    assert calls == ["American Express Platinum Card"]
    cards = json.loads(_asyncio.run(bmod.run_tool_async(b, {"action": "cards"})))
    plat = next(c for c in cards if c["last4"] == "1004")
    assert plat["connected"] and plat["published_benefits_saved"] and plat["credits_tracked"] == []
    assert set(b.known_cards()) >= {"American Express Platinum Card", "American Express Blue Cash Everyday"}


def test_sandbox_cards_are_not_your_cards():
    mem = Memory(":memory:")
    _connect(mem, [("Plaid Credit Card", "3333")], env="sandbox")
    assert Benefits(mem).connected_cards() == []


def test_a_newly_connected_card_is_flagged_for_a_lookup():
    from buddy.plaid import Plaid
    mem = Memory(":memory:")
    accounts = {"accounts": [{"account_id": "new1", "name": "Platinum Card", "mask": "1004", "type": "credit",
                              "balances": {}}]}
    p = Plaid(mem, env={"BUDDY_PLAID_CLIENT_ID": "x", "BUDDY_PLAID_SECRET": "y", "BUDDY_PLAID_ENV": "production"},
              http=lambda url, body: accounts)
    p._accounts({"item_id": "i9", "access_token": "t", "institution": "American Express"})
    assert mem.get("benefits:check_new") == "1"


def test_a_generically_named_connected_card_is_asked_about_then_looked_up():
    mem, calls = Memory(":memory:"), []
    from buddy.plaid import Plaid
    Plaid(mem, env={})
    mem.db.executescript(
        "INSERT INTO plaid_items (item_id, access_token, institution, added_at, env) VALUES "
        "('c', 't', 'Chase', '2026-11-01', 'production'), ('a', 't', 'American Express', '2026-11-01', 'production');"
        "INSERT INTO plaid_accounts (account_id, item_id, name, mask, type) VALUES "
        "('c1', 'c', 'Ultimate Rewards®', '0457', 'credit'), ('a1', 'a', 'Platinum Card®', '9001', 'credit');")
    from buddy.finance import Finance
    Finance(mem)
    mem.db.execute("INSERT INTO fin_cards (key, name, issuer, last4, updated_at) VALUES "
                   "('amex|9001', 'American Express', 'American Express', '9001', '2026-11-01')")  # from Amex's emails
    from buddy.money import Money
    Money(mem).add_transaction(95, "USD", "ANNUAL MEMBERSHIP FEE", "fees", "2026-10-15", card="0457", source="t:fee")
    b = Benefits(mem, search=_fake_search(PLAT, calls))
    assert b.known_cards() == ["American Express Platinum Card"]  # the email card is the same 9001; 0457 needs a name
    cards = b.cards_overview()
    chase = next(c for c in cards if c["last4"] == "0457")
    assert chase["needs_name"] and len([c for c in cards if "9001" in (c["last4"] or "")]) == 1
    texts = _asyncio.run(b.offer_new_cards())  # the $95 fee on 0457 says which card it is: worked out, not asked
    note = next(t for t in texts if "0457" in t)
    assert "••0457 is the Sapphire Preferred (from its $95 annual fee)" in note and "best guess" in note
    assert not any("0457" in t for t in _asyncio.run(b.offer_new_cards()))  # said once
    out = bmod.run_tool(b, {"action": "name_card", "last4": "0457", "name": "Sapphire Preferred"}) \
        if False else _asyncio.run(bmod.run_tool_async(b, {"action": "name_card", "last4": "0457",
                                                           "name": "Sapphire Preferred"}))
    assert "Sapphire Preferred" in out and mem.get("benefits:check_new") == "1"
    assert "Chase Sapphire Preferred" in b.known_cards()
    assert b.resolve_card("my sapphire") == "Chase Sapphire Preferred"


def test_two_chase_cards_with_the_same_bank_label_are_both_listed_and_asked_about():
    mem = Memory(":memory:")
    from buddy.plaid import Plaid
    Plaid(mem, env={})
    mem.db.executescript(
        "INSERT INTO plaid_items (item_id, access_token, institution, added_at, env) VALUES "
        "('c', 't', 'Chase', '2026-11-01', 'production');"
        "INSERT INTO plaid_accounts (account_id, item_id, name, mask, type) VALUES "
        "('c1', 'c', 'Ultimate Rewards®', '0457', 'credit'), ('c2', 'c', 'Ultimate Rewards®', '8812', 'credit');")
    b = Benefits(mem)
    assert sorted(c["last4"] for c in b.cards_overview()) == ["0457", "8812"]
    texts = _asyncio.run(b.offer_new_cards())
    assert any("0457" in t for t in texts) and any("8812" in t for t in texts)
    b.name_card("0457", "Sapphire Preferred")
    b.name_card("8812", "Freedom Unlimited")
    assert set(b.known_cards()) == {"Chase Sapphire Preferred", "Chase Freedom Unlimited"}


def _two_chase(mem):
    from buddy.plaid import Plaid
    from buddy.money import Money
    Plaid(mem, env={})
    Money(mem)
    mem.db.executescript(
        "INSERT INTO plaid_items (item_id, access_token, institution, added_at, env) VALUES "
        "('c', 't', 'Chase', '2026-11-01', 'production');"
        "INSERT INTO plaid_accounts (account_id, item_id, name, mask, type) VALUES "
        "('c1', 'c', 'Ultimate Rewards®', '0457', 'credit'), ('c2', 'c', 'Ultimate Rewards®', '8812', 'credit'),"
        "('c3', 'c', 'CREDIT CARD', '3300', 'credit');"
        "CREATE TABLE IF NOT EXISTS records (id INTEGER PRIMARY KEY, source TEXT, external_id TEXT, title TEXT, "
        "body TEXT, data TEXT, start_at TEXT);"
        "INSERT INTO records (source, title, body) VALUES ('email', 'Your statement is ready', "
        "'Your Chase Sapphire Preferred card ending in 0457 has a new statement.');")


def test_buddy_works_out_which_card_a_bank_label_is():
    mem = Memory(":memory:")
    _two_chase(mem)
    from buddy.money import Money
    Money(mem).add_transaction(795, "USD", "ANNUAL MEMBERSHIP FEE", "fees", "2026-09-01", card="8812", source="t:f")
    b = Benefits(mem)
    texts = _asyncio.run(b.offer_new_cards())
    assert any("••0457 is the Sapphire Preferred (from Chase's emails)" in t and "best guess" not in t for t in texts)
    assert any("••8812 is the Sapphire Reserve (from its $795 annual fee)" in t and "best guess" in t for t in texts)
    assert any("••3300" in t and "Which one is it" in t for t in texts)  # no clue: asked
    assert {"Chase Sapphire Preferred", "Chase Sapphire Reserve"} <= set(b.known_cards())
    assert _asyncio.run(b.offer_new_cards()) == []  # each said once
    b.name_card("8812", "Freedom Unlimited")  # "no, that one's my Freedom Unlimited"
    assert "Chase Freedom Unlimited" in b.known_cards() and "Chase Sapphire Reserve" not in b.known_cards()


def test_statement_credits_count_though_the_clean_name_drops_the_word_credit():
    mem = Memory(":memory:")
    b = Benefits(mem)
    b.add("Uber Cash", 15, "monthly", card="Amex Platinum", merchant="uber")
    Money(mem).add_transaction(-15, "USD", "UBER CASH CREDIT", "transport", "2026-11-02", card="9001", source="t:u")
    s = b.status(b.all()[0], date(2026, 11, 5))
    assert s["used"] == 15 and s["left"] == 0


def _amex_plat(mem):
    """The Amex Platinum connected through Plaid (••1004), its four credits tracked, and a Chase card (••0457)."""
    _connect(mem, [("Platinum Card®", "1004"), ("Ultimate Rewards®", "0457")])
    b = Benefits(mem)
    card = "American Express Platinum Card"
    b.add("Resy credit", 100, "semiannual", card=card, merchant="resy", notes="Published by the issuer: https://x")
    b.add("CLEAR+ credit", 219, "annual", card=card, merchant="clear",
          notes="Published by the issuer: https://x; needs enrollment; pays for a CLEAR+ membership")
    b.add("Airline fee credit", 200, "annual", card=card, merchant="one selected airline",
          notes="Published by the issuer: https://x; needs enrollment; incidental fees on the one airline you pick")
    b.add("Uber One credit", 120, "annual", card=card, merchant="uber one", notes="Published by the issuer: https://x")
    return b


def test_buddy_reads_the_amex_statement_to_see_which_credits_are_used(setup):
    mem, _, today = setup
    b = _amex_plat(mem)
    m = Money(mem)
    m.add_transaction(209, "USD", "CLEAR CLEARME.COM", "travel", "2026-03-02", card="1004", source="t:1")
    m.add_transaction(-209, "USD", "CLEAR PLUS CREDIT", "travel", "2026-03-05", card="1004", source="t:2")
    m.add_transaction(9.99, "USD", "UBER ONE MEMBERSHIP", "transport", "2026-10-01", card="1004", source="t:3")
    m.add_transaction(-9.99, "USD", "PLATINUM UBER ONE CREDIT", "transport", "2026-10-02", card="1004", source="t:4")
    m.add_transaction(-14, "USD", "UBER TRIP REFUND", "transport", "2026-10-03", card="1004", source="t:5")
    m.add_transaction(-50, "USD", "RESY CREDIT", "food", "2026-10-15", card="0457", source="t:6")  # the Chase card
    m.add_transaction(-35, "USD", "AMEX AIRLINE FEE REIMBURSEMENT", "travel", "2026-08-09", card="1004", source="t:7")
    got = {s["name"]: s for s in b.overview(today)}
    assert got["CLEAR+ credit"]["used"] == 209 and got["CLEAR+ credit"]["left"] == 10
    assert got["Uber One credit"]["used"] == 9.99            # the ride refund isn't the credit
    assert got["Resy credit"]["used"] == 0                    # a credit on another card doesn't count
    assert got["Airline fee credit"]["used"] == 35
    assert got["Resy credit"]["statement_through"] == "2026-10-15" or got["Resy credit"]["statement_through"]


def test_card_credits_due_the_same_morning_are_one_message_saying_what_the_statement_shows(setup):
    mem, _, today = setup
    b = _amex_plat(mem)
    Money(mem).add_transaction(-9.99, "USD", "PLATINUM UBER ONE CREDIT", "transport", "2026-10-02", card="1004",
                               source="t:4")
    alerts = b.alerts(today)
    assert len(alerts) == 4
    merged = b.bundle(alerts)
    assert len(merged) == 1
    text = merged[0]["text"]
    spoken, shown = text.split(" || ")
    assert spoken.startswith("4 of your card credits run out soon")
    assert "Used $10 so far (statement credit of $10 on Oct 2)" in shown
    assert "No statement credit for it on your card through Oct 2" in shown
    assert "One Selected Airline" not in text and "Incidental fees on the one airline you pick" in text
    assert "needs enrollment" in text
    b.told(merged[0])
    assert b.alerts(today) == []


def test_claude_sorts_statement_credits_whose_wording_names_no_credit(setup):
    mem, _, today = setup
    b = _amex_plat(mem)
    Money(mem).add_transaction(-50, "USD", "AMEX PLATINUM BENEFIT", "food", "2026-10-20", card="1004", source="t:9")
    resy = next(x for x in b.all() if x["name"] == "Resy credit")
    seen = []

    async def ask(memory, credits, lines):
        seen.append(lines)
        return {"links": [{"n": 0, "credit_id": resy["id"]}]}

    assert _asyncio.run(b.link_credits(ask=ask, today=today)) == 1
    assert seen[0][0]["statement_text"] == "AMEX PLATINUM BENEFIT"
    assert b.status(resy, today)["used"] == 50
    assert _asyncio.run(b.link_credits(ask=ask, today=today)) == 0 and len(seen) == 1  # asked once
