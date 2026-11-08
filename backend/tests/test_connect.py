"""The brain connects: plans from emails and uploads, recall across sources, lessons from corrections."""

import asyncio
import json
from datetime import date, datetime, timedelta, timezone

from buddy import connect
from buddy.brain import Brain
from buddy.connect import Connect, learn_from_correction
from buddy.connectors.hub import ConnectorHub
from buddy.memory import Memory

DAY = (date.today() + timedelta(days=3)).isoformat()


def add_email(memory, title, body, sender="American Airlines <no-reply@aa.com>"):
    now = datetime.now(timezone.utc).isoformat()
    memory.db.execute("INSERT INTO records (source, external_id, kind, title, body, start_at, data, synced_at) "
                      "VALUES ('email', ?, 'email', ?, ?, ?, ?, ?)",
                      (title + body, title, body, now, json.dumps({"from": sender, "account": "me@gmail.com"}), now))


def setup():
    memory = Memory(":memory:")
    ConnectorHub(memory)
    return memory


def test_flight_in_an_email_becomes_a_plan_and_is_tracked(monkeypatch):
    memory = setup()
    add_email(memory, "Your trip confirmation: Miami to Lima", f"Flight AA 1234 departs MIA {DAY} 3:40 PM, arrives LIM. "
              "Record locator XYZ123.")
    add_email(memory, "Weekly deals", "Sale on shoes")  # not travel: never read
    seen = []

    async def extract(mem, content):
        seen.append(content)
        return {"plans": [{"kind": "flight", "date": DAY, "time": "15:40", "title": "AA1234 MIA -> LIM",
                           "flight": "AA 1234", "from": "MIA", "to": "LIM", "confirmation": "XYZ123"}]}
    told = asyncio.run(Connect(memory).scan(extract=extract))
    assert len(seen) == 1 and "Weekly deals" not in seen[0]
    assert told and "AA1234" in told[0] and "an email from American Airlines" in told[0]
    plans = Connect(memory).upcoming()
    assert plans[0]["title"] == "AA1234 MIA -> LIM" and plans[0]["starts"] == f"{DAY} 15:40"
    from buddy.flights import Flights
    assert [(f["flight"], f["day"]) for f in Flights(memory, env={}).tracked()] == [("AA1234", DAY)]
    assert asyncio.run(Connect(memory).scan(extract=extract)) == []  # read once


def test_pack_brings_plans_and_matching_emails():
    memory = setup()
    Connect(memory).save_plan({"kind": "flight", "date": DAY, "title": "AA1234 MIA -> LIM", "flight": "AA1234"},
                              "an email from American Airlines")
    add_email(memory, "Hilton Miraflores reservation", "Your stay at Hilton Miraflores, check-in Nov 10")
    lines = Connect(memory).pack_lines("what's my flight status")
    assert any("upcoming flight (from an email from American Airlines): AA1234 MIA -> LIM" in x for x in lines)
    hotel = Connect(memory).pack_lines("where am I staying in miraflores")
    assert any("Hilton Miraflores" in x for x in hotel)


def test_a_correction_becomes_a_lesson():
    memory = setup()
    brain = Brain(memory, embed=lambda texts: None)
    lesson = learn_from_correction(brain, "what is my flight status", "I don't know which flight you mean.",
                                   "it's in my email, I also uploaded it")
    assert "look in email" in lesson
    assert any(p["topic"] == "where to look" for p in brain.preferences())
    assert learn_from_correction(brain, "hi", "Hello!", "it's in my email") == ""  # no 'don't know': nothing learned


def test_flight_status_without_a_number_uses_known_flights(monkeypatch):
    memory = setup()
    Connect(memory).save_plan({"kind": "flight", "date": DAY, "title": "AA1234", "flight": "AA1234"}, "an email")
    from buddy import flights
    monkeypatch.setattr(flights.Flights, "lookup", lambda self, f, d="", since="": {"flight": f, "dep_iata": "MIA",
                                                                                     "arr_iata": "LIM"})
    out = json.loads(flights.run_tool(memory, {"action": "status"}))
    assert out[0]["flight"] == "AA1234"


from buddy.connect import Moments  # noqa: E402

MIA, LIM, HOME = (25.795, -80.287), (-12.022, -77.114), (25.79, -80.13)  # home: Miami Beach, ~16 km from MIA


def trip(memory, day):
    c = Connect(memory)
    c.save_plan({"kind": "flight", "date": day, "time": "15:40", "title": "AA1234 MIA -> LIM", "flight": "AA1234",
                 "from": "MIA", "to": "LIM"}, "an email from American Airlines")
    c.save_plan({"kind": "hotel", "date": (date.fromisoformat(day) + timedelta(days=0)).isoformat(),
                 "end_date": (date.fromisoformat(day) + timedelta(days=4)).isoformat(), "title": "Hilton Miraflores",
                 "confirmation": "H778899"}, "an email from Hilton")


def moments(memory):
    return Moments(memory, geocode=lambda q: {"MIA airport": MIA, "LIM airport": LIM}.get(q))


def say_all(m, now, here):
    out = m.now(now=now, here=here)
    for x in out:
        m.told(x)
    return [x["key"].split(":")[0] for x in out], out


def test_a_flight_day_without_being_asked():
    memory = setup()
    day = date.today() + timedelta(days=1)
    trip(memory, day.isoformat())
    m = moments(memory)
    eve = datetime.combine(day - timedelta(days=1), datetime.min.time()).replace(hour=19)
    keys, out = say_all(m, eve, {"lat": HOME[0], "lng": HOME[1], "city": "Miami Beach"})
    assert keys == ["eve"] and "Tomorrow: AA1234 MIA -> LIM at 3:40 PM" in out[0]["text"]
    assert say_all(m, eve, None)[0] == []  # said once
    morning = datetime.combine(day, datetime.min.time()).replace(hour=8)
    keys, out = say_all(m, morning, {"lat": HOME[0], "lng": HOME[1]})
    assert "day" in keys and "hotel" not in keys  # hotel check-in is announced from 9
    assert any("Aim to be at MIA by 1:40 PM" in x["text"] for x in out)
    leave = datetime.combine(day, datetime.min.time()).replace(hour=13, minute=15)  # 3:40 - 2h - ~42 min
    keys, out = say_all(m, leave, {"lat": HOME[0], "lng": HOME[1]})
    assert "leave" in keys and next(x for x in out if x["key"].startswith("leave"))["urgent"]
    at_airport = datetime.combine(day, datetime.min.time()).replace(hour=14)
    keys, out = say_all(m, at_airport, {"lat": MIA[0], "lng": MIA[1]})
    assert keys == ["at"] and "You're at MIA" in out[0]["text"] and "boarding about 3:00 PM" in out[0]["text"]
    landed = datetime.combine(day, datetime.min.time()).replace(hour=22)
    keys, out = say_all(m, landed, {"lat": LIM[0], "lng": LIM[1], "city": "Lima"})
    assert keys == ["landed"] and "Welcome to Lima. Your stay: Hilton Miraflores" in out[0]["text"]


def test_nothing_without_plans_or_far_from_the_day():
    memory = setup()
    trip(memory, (date.today() + timedelta(days=10)).isoformat())
    assert moments(memory).now(now=datetime.now().replace(hour=19), here=None) == []


def add_event(memory, title, start_local, end_local, where=""):
    from buddy.tools import local_now
    tz = local_now().tzinfo
    s = start_local.replace(tzinfo=tz).astimezone(timezone.utc).isoformat()
    e = end_local.replace(tzinfo=tz).astimezone(timezone.utc).isoformat()
    memory.db.execute("INSERT INTO records (source, external_id, kind, title, body, start_at, end_at, data, synced_at) "
                      "VALUES ('calendar', ?, 'event', ?, ?, ?, ?, ?, ?)",
                      (title + s, title, f"Where: {where}" if where else "", s, e, json.dumps({"location": where}), s))


def test_a_bill_in_a_bank_notification_is_kept_and_recalled():
    memory = setup()
    now = datetime.now(timezone.utc).isoformat()
    due = (date.today() + timedelta(days=4)).isoformat()
    memory.db.execute("INSERT INTO records (source, external_id, kind, title, body, start_at, data, synced_at) VALUES "
                      "('notifications', 'n1', 'notification', 'Chase: Payment due', ?, ?, ?, ?)",
                      (f"Your payment of $412.00 is due on {due}.", now, json.dumps({"app_name": "Chase"}), now))

    async def extract(mem, content):
        return {"plans": [{"kind": "bill", "date": due, "title": "Chase", "biller": "Chase Sapphire", "amount": 412.0,
                           "currency": "USD"}]}
    told = asyncio.run(Connect(memory).scan(extract=extract))
    assert told and "Chase Sapphire, 412.00 USD due" in told[0]
    assert any("open bill: Chase Sapphire 412.00 USD" in x for x in Connect(memory).pack_lines("when is my bill due"))
    assert asyncio.run(Connect(memory).scan(extract=extract)) == []


def test_bills_more_than_the_cash(monkeypatch):
    memory = setup()
    from buddy.money import Money
    from buddy import plaid
    Money(memory).add_bill("Rent", 2400, "USD", (date.today() + timedelta(days=2)).isoformat())
    monkeypatch.setattr(plaid.Plaid, "accounts_overview", lambda self: {
        "groups": {"cash": [{"balance": 1800}]}, "totals": {"cash": 1800.0}})
    out = moments(memory).now(now=datetime.now().replace(hour=10), here=None)
    assert any("$2,400 in bills is due" in m["text"] and "have $1,800" in m["text"] for m in out)


def test_time_to_leave_for_an_appointment_and_tomorrows_overlap():
    memory = setup()
    today = date.today()
    start = datetime.combine(today, datetime.min.time()).replace(hour=15)
    add_event(memory, "Dentist", start, start + timedelta(hours=1), "123 Collins Ave, Miami Beach")
    tomorrow = datetime.combine(today + timedelta(days=1), datetime.min.time())
    add_event(memory, "Team sync", tomorrow.replace(hour=10), tomorrow.replace(hour=11), "Zoom")
    add_event(memory, "Doctor", tomorrow.replace(hour=10, minute=30), tomorrow.replace(hour=11, minute=30))
    m = Moments(memory, geocode=lambda q: (25.86, -80.12) if "Collins" in q else None)  # ~8 km from HOME
    here = {"lat": HOME[0], "lng": HOME[1]}
    assert m.now(now=start - timedelta(hours=2), here=here) == []  # too early to leave
    out = m.now(now=start - timedelta(minutes=35), here=here)
    assert out[0]["text"].startswith("Time to leave for Dentist at 3:00 PM") and out[0]["urgent"]
    evening = datetime.combine(today, datetime.min.time()).replace(hour=19)
    out = m.now(now=evening, here=None)
    assert any("Team sync (10:00–11:00 AM) overlaps Doctor (10:30 AM)" in x["text"] for x in out)


def test_calendar_questions_get_the_agenda():
    memory = setup()
    soon = datetime.now() + timedelta(hours=3)
    add_event(memory, "Lunch with Neha", soon, soon + timedelta(hours=1))
    assert any("Lunch with Neha" in x for x in Connect(memory).pack_lines("what's on my calendar today"))
