"""Cancelling subscriptions: guided, by email, with proof, disputes and free-trial reminders."""

import asyncio
import json
from datetime import date

from buddy import cancelling, finance
from buddy.cancelling import Cancelling
from buddy.memory import Memory
from buddy.money import Money


def _sub(mem, merchant="Hulu", amount=17.99, days=((5, 1), (6, 1), (7, 1), (8, 1)), card="1004"):
    money = Money(mem)
    for i, (m, d) in enumerate(days):
        money.add_transaction(amount, "USD", merchant, "subscriptions", f"2026-{m:02d}-{d:02d}", card=card,
                              source=f"t:{merchant}:{i}")
    finance.Finance(mem).find_subscriptions()


def test_guided_cancel_opens_the_page_with_a_discount_script_and_the_traps():
    mem = Memory(":memory:")
    _sub(mem)
    opened = []

    async def open_links(links):
        opened.extend(links)
    out = json.loads(asyncio.run(finance.run_tool(finance.Finance(mem), {"action": "cancel_help", "what": "hulu"},
                                                  open_links)))
    assert opened[0]["url"].startswith("https://secure.hulu.com") and out["opened"]
    assert "discount" in out["retention_script"] and "Hulu" in out["retention_script"]
    assert any("Finish cancellation" in t for t in out["traps"]) and "Never press the final" in out["next"]
    assert finance.Finance(mem)._find("hulu")["status"] == "cancel"  # watched: a charge after this is flagged


def test_cancel_by_email_is_written_for_a_draft_or_a_send_after_yes():
    mem = Memory(":memory:")
    _sub(mem, merchant="Gold's Gym", amount=40)
    out = json.loads(cancelling.run_tool(mem, {"action": "cancel_email", "what": "gold", "to": "billing@gym.example",
                                               "account_email": "me@example.com"}))
    assert out["to"] == "billing@gym.example" and "Cancel my" in out["subject"]
    assert "effective immediately" in out["body"] and "me@example.com" in out["body"] and "create_draft" in out["next"]


def test_a_cancellation_confirmation_email_is_kept_as_proof():
    mem = Memory(":memory:")
    _sub(mem)
    notes = Cancelling(mem).scan_email({"id": 7, "title": "Your Hulu subscription has been cancelled",
                                        "body": "We're sorry to see you go.", "start_at": "2026-09-10T08:00:00"},
                                       "Hulu <account@hulumail.com>")
    assert notes and "Saved Hulu's cancellation confirmation (Sep 10) as proof" in notes[0]
    assert Cancelling(mem).scan_email({"id": 7, "title": "Your Hulu subscription has been cancelled", "body": "",
                                       "start_at": "2026-09-10"}, "Hulu") == []  # once
    assert Cancelling(mem).proofs()[0]["day"] == "2026-09-10"


def test_charges_after_cancelling_get_a_dispute_letter_with_the_proof():
    mem = Memory(":memory:")
    _sub(mem)
    Cancelling(mem).scan_email({"id": 7, "title": "Your Hulu subscription has been cancelled", "body": "",
                                "start_at": "2026-08-10T08:00:00"}, "Hulu <account@hulumail.com>")
    Money(mem).add_transaction(17.99, "USD", "Hulu", "subscriptions", "2026-09-01", card="1004", source="t:after")
    from buddy.plaid import Plaid
    Plaid(mem, env={})
    mem.db.executescript("INSERT INTO plaid_items (item_id, access_token, institution, added_at) VALUES "
                         "('i', 't', 'American Express', '2026-01-01');"
                         "INSERT INTO plaid_accounts (account_id, item_id, name, mask, type) VALUES "
                         "('a', 'i', 'Platinum Card', '1004', 'credit');")
    out = json.loads(cancelling.run_tool(mem, {"action": "dispute_help", "what": "hulu"}))
    assert [c["day"] for c in out["charges_after_cancel"]] == ["2026-09-01"]
    assert out["cancelled_on"] == "2026-08-10" and "Amex app" in out["where_to_file"]
    assert "I cancelled this subscription on 2026-08-10" in out["letter"] and "has been cancelled" in out["letter"]


def test_a_free_trial_signup_gets_a_reminder_two_days_before_it_charges():
    mem = Memory(":memory:")
    c = Cancelling(mem)
    notes = c.scan_email({"id": 9, "title": "Welcome! Your free trial has started",
                          "body": "Your 7-day free trial ends on November 20, 2026. You'll be charged $13.99/month after.",
                          "start_at": "2026-11-13T09:00:00"}, "Peacock <hello@peacocktv.com>")
    assert notes and "starts charging Nov 20 (then $13.99)" in notes[0]
    assert c.trial_alerts(date(2026, 11, 16)) == []
    alert = c.trial_alerts(date(2026, 11, 18))[0]
    assert "in 2 days (Nov 20), $13.99" in alert["text"] and "cancel page" in alert["text"]
    c.told_trial(alert)
    assert c.trial_alerts(date(2026, 11, 18)) == []
    assert "no more reminders" in c.keep_trial("peacock")
    assert c.trials() == []


def test_trial_dates_in_the_usual_formats():
    got = date(2026, 12, 20)
    assert cancelling._parse_date("Jan 3", got) == date(2027, 1, 3)      # said in December: next year
    assert cancelling._parse_date("12/28/2026", got) == date(2026, 12, 28)
    assert cancelling._parse_date("2026-12-31", got) == date(2026, 12, 31)
    assert cancelling._parse_date("5 January 2027", got) == date(2027, 1, 5)
