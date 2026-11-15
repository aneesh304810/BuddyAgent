"""The subscription cancel service: review, one yes per cancellation, email or phone or guided, then checking."""

import asyncio
import json
from datetime import date, datetime

import pytest

from buddy import calls, finance, subcancel
from buddy.cancelling import Cancelling
from buddy.memory import Memory
from buddy.money import Money
from buddy.tools import ToolError

ENV = {"BUDDY_PLIVO_AUTH_ID": "MAID", "BUDDY_PLIVO_AUTH_TOKEN": "ptok", "BUDDY_PLIVO_NUMBER": "+12015550100",
       "BUDDY_DEEPGRAM_KEY": "dg", "BUDDY_ELEVENLABS_KEY": "el", "BUDDY_CALLS_PUBLIC_URL": "https://box.ts.net:8443",
       "BUDDY_OWNER_FULL_NAME": "Sam Rivera", "BUDDY_OWNER_PHONE": "201-555-0199",
       "BUDDY_EMAIL_ADDRESS": "sam@example.com", "BUDDY_EMAIL_APP_PASSWORD": "app pass word"}


@pytest.fixture
def env(monkeypatch):
    for k, v in ENV.items():
        monkeypatch.setenv(k, v)
    return ENV


def _sub(mem, merchant="Hulu", amount=17.99, days=((7, 1), (8, 1), (9, 1), (10, 1)), card="1004"):
    money = Money(mem)
    for i, (m, d) in enumerate(days):
        money.add_transaction(amount, "USD", merchant, "subscriptions", f"2026-{m:02d}-{d:02d}", card=card,
                              source=f"t:{merchant}:{i}:{amount}")
    finance.Finance(mem).find_subscriptions(date(2026, 10, 20))


class Thrift:
    """Keeps the question and the action, so a test can say yes."""

    def __init__(self):
        self.asked = []

    def ask_action(self, question, run, **kw):
        self.asked.append((question, run, kw))
        return "APPROVAL NEEDED"

    def yes(self):
        return asyncio.run(self.asked[-1][1]())


class Smtp:
    sent = []

    def __init__(self, host, port):
        self.host, self.port = host, port

    def login(self, user, password):
        self.user = user

    def send_message(self, message):
        Smtp.sent.append((self.host, self.user, message))

    def quit(self):
        pass


def test_review_lists_costs_and_what_changed():
    mem = Memory(":memory:")
    _sub(mem)
    money = Money(mem)
    for i, (m, amount) in enumerate(((7, 10.0), (8, 10.0), (9, 10.0), (10, 14.0))):
        money.add_transaction(amount, "USD", "Netflix", "subscriptions", f"2026-{m:02d}-03", card="1004",
                              source=f"n:{i}")
    finance.Finance(mem).find_subscriptions(date(2026, 10, 20))
    got = subcancel.review(mem)
    first = got["subscriptions"][0]
    assert first["merchant"] == "Netflix" and any("price up" in f for f in first["look_at"])  # worth a look: first
    assert first["monthly_cost"] == 14.0 and got["totals"]["USD"]["count"] == 2
    assert "can't see whether you use" in got["note"]
    text = subcancel.review_message(mem)
    spoken, shown = text.split(" || ")
    assert "2 subscriptions" in spoken and "1 worth a look" in spoken
    assert "Netflix: $14.00 monthly (price up from 10.00)" in shown and "Hulu: $17.99 monthly, $215.88 a year" in shown
    assert subcancel.review_message(Memory(":memory:")) == ""


def test_the_monthly_review_is_due_once_a_month_after_nine():
    mem = Memory(":memory:")
    assert not subcancel.review_due(mem, datetime(2026, 11, 2, 8, 0))
    assert subcancel.review_due(mem, datetime(2026, 11, 2, 9, 5))
    subcancel.mark_reviewed(mem, datetime(2026, 11, 2, 9, 5))
    assert not subcancel.review_due(mem, datetime(2026, 11, 20, 10, 0))
    assert subcancel.review_due(mem, datetime(2026, 12, 1, 9, 0))


def test_email_route_asks_first_and_sends_only_after_the_yes(env):
    mem, thrift = Memory(":memory:"), Thrift()
    _sub(mem, merchant="Gold's Gym", amount=40)
    Smtp.sent.clear()
    out = asyncio.run(subcancel.start(mem, thrift, {"what": "gold", "to": "billing@gym.example"}, smtp=Smtp))
    question = thrift.asked[0][0]
    assert out == "APPROVAL NEEDED" and Smtp.sent == [] and subcancel.status(mem)["cancellations"] == []
    assert "$40.00 monthly" in question and "billing@gym.example" in question and "from sam@example.com" in question
    assert "Please cancel my Gold's Gym subscription" in question  # the exact words are shown before the yes
    assert thrift.asked[0][2] == {}  # asked on its own, never bundled with other cancellations
    said = thrift.yes()
    host, user, message = Smtp.sent[0]
    assert host == "smtp.gmail.com" and user == "sam@example.com" and message["To"] == "billing@gym.example"
    assert "sam@example.com" in message.get_content() and "Sent the cancellation email" in said
    job = subcancel.status(mem)["cancellations"][0]
    assert job["route"] == "email" and job["status"] == "waiting" and job["target"] == "billing@gym.example"
    assert finance.Finance(mem)._find("gold")["status"] == "cancel"
    again = asyncio.run(subcancel.start(mem, thrift, {"what": "gold", "to": "billing@gym.example"}, smtp=Smtp))
    assert "already asked" in again and len(Smtp.sent) == 1


def test_email_route_refuses_a_made_up_address_and_reports_a_failed_send(env):
    mem, thrift = Memory(":memory:"), Thrift()
    _sub(mem)
    with pytest.raises(ToolError, match="official help page"):
        asyncio.run(subcancel.start(mem, thrift, {"what": "hulu", "route": "email", "to": "their support"}))

    def broken(host, port):
        raise OSError("no route")
    asyncio.run(subcancel.start(mem, thrift, {"what": "hulu", "to": "help@hulu.example"}, smtp=broken))
    said = thrift.yes()
    assert "couldn't be sent" in said and "Nothing was cancelled" in said
    assert subcancel.status(mem)["cancellations"] == [] and finance.Finance(mem)._find("hulu")["status"] != "cancel"


def test_without_a_way_to_ask_nothing_is_sent(env):
    mem = Memory(":memory:")
    _sub(mem)
    out = asyncio.run(subcancel.start(mem, None, {"what": "hulu", "to": "help@hulu.example"}, smtp=Smtp))
    assert "need your yes" in out and subcancel.status(mem)["cancellations"] == []


def test_phone_route_plans_a_cancellation_call_and_dials_after_the_yes(env, monkeypatch):
    mem, thrift = Memory(":memory:"), Thrift()
    _sub(mem)
    dialled = []
    monkeypatch.setattr(calls, "dial", lambda memory, call, **k: dialled.append(call["id"]) or "uuid")
    with pytest.raises(ToolError, match="phone number"):
        asyncio.run(subcancel.start(mem, thrift, {"what": "hulu", "route": "phone"}))
    out = asyncio.run(subcancel.start(mem, thrift, {"what": "hulu", "number": "888-555-0142",
                                                    "account_email": "sam@example.com"}))
    question = thrift.asked[0][0]
    assert out == "APPROVAL NEEDED" and dialled == []
    assert "+18885550142" in question and "turn down offers" in question and "no card numbers" in question
    assert "sam@example.com" in question and "isn't recorded" in question
    assert "Calling Hulu now" in thrift.yes() and len(dialled) == 1
    job = subcancel.status(mem)["cancellations"][0]
    assert job["route"] == "phone" and job["status"] == "waiting"
    call = calls.get(mem, dialled[0])
    assert call["kind"] == "cancellation" and "Turn down every offer" in call["goal"]
    prompt = calls.system_prompt(mem, call)
    assert "confirmation or reference number" in prompt and "the account's email address" in prompt
    assert "Never pay, agree to a fee" in prompt and "Never pay, cancel" not in prompt
    assert "Social Security" in prompt and "Never deny it" in prompt and "Never say it was cancelled unless" in prompt


def test_an_offer_is_accepted_only_when_the_owner_set_one(env):
    mem = Memory(":memory:")
    _sub(mem)
    sub = finance.Finance(mem)._find("hulu")
    assert "only if: half price for six months" in subcancel.call_goal(sub, accept="half price for six months")
    assert "Turn down every offer" in subcancel.call_goal(sub)


def test_other_calls_still_never_cancel_and_the_phone_tool_points_here(env):
    mem = Memory(":memory:")
    prompt = calls.system_prompt(mem, calls.plan(mem, "Dr", "2015550123", "question", "hours"))
    assert "Never pay, cancel, or agree to fees" in prompt
    out = asyncio.run(calls.run_tool(mem, Thrift(), {"kind": "cancellation", "number": "8885550142", "goal": "x"}))
    assert "cancel_subscription" in out


def test_guided_route_opens_the_page_and_is_watched():
    mem = Memory(":memory:")
    _sub(mem)
    opened = []

    async def open_links(links):
        opened.extend(links)
    out = json.loads(asyncio.run(subcancel.run_tool(mem, {"action": "start", "what": "hulu"}, None, open_links)))
    assert opened[0]["url"].startswith("https://secure.hulu.com") and "Never press the final" in out["next"]
    assert subcancel.status(mem)["cancellations"][0]["route"] == "guided"


def _job(mem, started="2026-10-20", route="email", call_id=None):
    sub = finance.Finance(mem)._find("hulu")
    return subcancel._job(mem, sub, route, "help@hulu.example", "sent", call_id, date.fromisoformat(started))


def test_check_reports_a_charge_after_cancelling_once():
    mem = Memory(":memory:")
    _sub(mem)
    _job(mem)
    assert subcancel.check(mem, date(2026, 10, 25)) == []
    Money(mem).add_transaction(17.99, "USD", "Hulu", "subscriptions", "2026-11-01", card="1004", source="late")
    told = subcancel.check(mem, date(2026, 11, 2))
    assert len(told) == 1 and "charged you $17.99 on 2026-11-01" in told[0] and "dispute letter" in told[0]
    assert subcancel.status(mem)["cancellations"][0]["status"] == "still_charging"
    assert subcancel.check(mem, date(2026, 11, 3)) == []


def test_check_confirms_from_the_proof_then_reports_it_stopped():
    mem = Memory(":memory:")
    _sub(mem)
    _job(mem)
    Cancelling(mem).scan_email({"id": 7, "title": "Your Hulu subscription has been cancelled", "body": "",
                                "start_at": "2026-10-21T10:00:00"}, "Hulu <no-reply@hulu.com>")
    assert subcancel.check(mem, date(2026, 10, 22)) == []  # the proof was announced when its email arrived
    assert subcancel.status(mem)["cancellations"][0]["status"] == "confirmed"
    told = subcancel.check(mem, date(2026, 11, 8))
    assert told == ["Hulu is cancelled for good: its next charge was due 2026-10-31 and didn't come."]
    assert subcancel.status(mem)["cancellations"][0]["status"] == "stopped"


def test_check_nudges_once_when_nothing_is_heard():
    mem = Memory(":memory:")
    _sub(mem)
    _job(mem, started="2026-10-02")
    mem.db.execute("UPDATE fin_subscriptions SET next_charge = '2026-12-01'")
    told = subcancel.check(mem, date(2026, 10, 10))
    assert len(told) == 1 and "No confirmation from Hulu yet, 8 days" in told[0] and "a call" in told[0]
    assert subcancel.check(mem, date(2026, 10, 11)) == []


def test_check_keeps_the_calls_outcome_and_reports_a_call_that_failed(env):
    mem = Memory(":memory:")
    _sub(mem)
    call = calls.plan(mem, "Hulu", "8885550142", "cancellation", "Cancel it")
    _job(mem, route="phone", call_id=call["id"])
    calls.update(mem, call["id"], status="done", outcome="Cancelled. Confirmation HX-4412, ends Nov 1.")
    assert subcancel.check(mem, date(2026, 10, 21)) == []
    assert "HX-4412" in subcancel.status(mem)["cancellations"][0]["note"]
    mem2 = Memory(":memory:")
    _sub(mem2)
    call = calls.plan(mem2, "Hulu", "8885550142", "cancellation", "Cancel it")
    _job(mem2, route="phone", call_id=call["id"])
    calls.update(mem2, call["id"], status="failed")
    assert "didn't get through" in subcancel.check(mem2, date(2026, 10, 21))[0]
    assert subcancel.status(mem2)["cancellations"][0]["status"] == "failed"


def test_the_tool_reviews_and_says_when_there_is_nothing():
    mem = Memory(":memory:")
    assert "No subscriptions found" in asyncio.run(subcancel.run_tool(mem, {"action": "review"}))
    _sub(mem)
    assert json.loads(asyncio.run(subcancel.run_tool(mem, {"action": "status"})))["cancellations"] == []
