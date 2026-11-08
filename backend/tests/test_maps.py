"""Buddy's eight maps: places and routines, situation, source trust, goals, commitments, people, money flow,
skills and costs."""

import json
from datetime import date, datetime, timedelta, timezone

from buddy.connectors.hub import ConnectorHub
from buddy.maps import Maps
from buddy.memory import Memory
from buddy.tools import local_now

HOME, WORK = (40.7768, -74.0117), (40.7128, -74.0060)  # Port Imperial, Lower Manhattan


def setup():
    m = Memory(":memory:")
    ConnectorHub(m)
    return m, Maps(m)


def at_local(d: date, h: int, mi: int = 0) -> datetime:
    return datetime(d.year, d.month, d.day, h, mi, tzinfo=local_now().tzinfo).astimezone(timezone.utc)


def test_places_and_the_usual_leave_time():
    m, maps = setup()
    today = date.today()
    for back in range(1, 15):
        d = today - timedelta(days=back)
        for h in (1, 3, 23):
            maps.log_location(*HOME, at=at_local(d, h))
        if d.weekday() < 5:
            maps.log_location(*HOME, at=at_local(d, 7, 50))
            maps.log_location(40.770, -74.02, at=at_local(d, 8, 10))  # left home
            for h in (11, 13, 15):
                maps.log_location(*WORK, at=at_local(d, h))
    found = maps.learn_places()
    assert set(found) == {"home", "work"}
    assert maps.place_at(*WORK) == "work"
    assert maps.usual_leave() == "08:10"
    maps.name_place("gym", 40.75, -74.03)
    assert maps.place_at(40.75, -74.03) == "gym"


def test_situation_driving_and_hold():
    m, maps = setup()
    now = datetime.now(timezone.utc)
    maps.log_location(40.70, -74.00, at=now - timedelta(minutes=10))
    maps.log_location(40.76, -74.00, at=now)  # ~6.7 km in 10 minutes
    s = maps.situation()
    assert s["driving"] and s["moving_kmh"] >= 25
    assert maps.hold_non_urgent()


def test_source_trust():
    m, maps = setup()
    for _ in range(8):
        maps.trust("flight_status", True)
    for _ in range(2):
        maps.trust("flight_status", False)
    maps.trust("rare", True)
    assert maps.trust_scores() == {"flight_status": 75}  # (8+1)/(10+2); 'rare' seen once: not yet


def test_goals_and_priorities():
    m, maps = setup()
    now = datetime.now(timezone.utc).isoformat()
    m.db.execute("INSERT INTO goals (text, created_at) VALUES ('Run a half marathon', ?)", (now,))
    from buddy.advisor import Advisor
    Advisor(m)
    m.db.execute("INSERT INTO fin_goals (kind, name, target, due, created_at, updated_at) VALUES "
                 "('save', 'Lima trip fund', 3000, '2027-06-01', ?, ?)", (now, now))
    from buddy.brain import Brain
    Brain(m, embed=lambda t: None).prefer("priorities", "family first")
    goals = maps.goals()
    assert "Run a half marathon" in goals and any("Lima trip fund (save, 3,000 USD, by 2027-06-01)" in g for g in goals)
    assert "priority: family first" in goals


def test_commitments_from_what_you_say():
    m, maps = setup()
    monday = date(2026, 11, 9)
    p = maps.promise_in("ok, I'll send Priya the deck by Friday", today=monday)
    assert p == {"what": "send Priya the deck", "due": "2026-11-13"}
    assert maps.promise_in("I'll call Amma tomorrow", today=monday)["due"] == "2026-11-10"
    assert maps.promise_in("I like coffee", today=monday) is None
    due = (date.today() + timedelta(days=1)).isoformat()
    assert maps.add_commitment("send Priya the deck", due, "Priya")
    assert not maps.add_commitment("send Priya the deck", due)  # once
    nudge = [n for n in maps.nudges(datetime.now().replace(hour=11)) if n["key"].startswith("promise")]
    assert nudge and "You said you'd send Priya the deck (for Priya), due tomorrow" in nudge[0]["text"]
    assert maps.done_commitment("deck") == "Marked done." and maps.commitments() == []


def test_people_last_heard_and_quiet_family():
    m, maps = setup()
    from buddy.brain import Brain
    b = Brain(m, embed=lambda t: None)
    b.note("Amma", "person", "mother")
    old = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    m.db.execute("INSERT INTO records (source, external_id, kind, title, body, start_at, data, synced_at) VALUES "
                 "('notifications', 'w1', 'notification', 'WhatsApp: Amma', 'call me', ?, ?, ?)",
                 (old, json.dumps({"app_name": "WhatsApp"}), old))
    assert maps.last_heard("Amma")[:10] == old[:10]
    assert [p["name"] for p in maps.quiet_family()] == ["Amma"]
    assert "last heard from" in maps.people_lines("how is amma doing?")[0]


def test_money_flow_shortfall(monkeypatch):
    m, maps = setup()
    from buddy import plaid
    from buddy.money import Money
    money = Money(m)
    money.add_bill("Avalon Rent", 2400, "USD", (date.today() + timedelta(days=3)).isoformat())
    m.db.execute("INSERT INTO transactions (day, amount, currency, merchant, category, card, source, created_at) "
                 "VALUES ('2026-10-01', 2400, 'USD', 'AVALON RENT ONLINE', 'housing', '1234', 'p1', '2026-10-01')")
    monkeypatch.setattr(plaid.Plaid, "accounts_overview", lambda self: {"groups": {"cash": [
        {"name": "Chase Checking", "last4": "1234", "balance": 1800}, {"name": "Savings", "last4": "9999",
                                                                       "balance": 9000}]}, "totals": {"cash": 10800}})
    assert maps.paying_account("Avalon Rent") == "1234"
    short = maps.shortfalls()
    assert short and short[0]["account"] == "Chase Checking ••1234" and short[0]["need"] == 2400
    text = [n for n in maps.nudges(datetime.now().replace(hour=10)) if n["key"].startswith("flow")][0]["text"]
    assert "holds $1,800" in text


def test_skills_and_costs():
    m, maps = setup()
    from buddy.travel import log_call
    from buddy.markets import API_CALLS_SQL
    m.db.executescript(API_CALLS_SQL)
    for ok in (True, True, True, False):
        log_call(m, "airlabs", "schedules", ok, 0.2, "flight status", cost=0.0)
    log_call(m, "flightaware", "flights", True, 0.3, "flight status", cost=0.005)
    sk = {x["service"]: x for x in maps.skills()}
    assert sk["airlabs"]["works"] == 75 and sk["flightaware"]["cost_usd"] == 0.01
    lines = maps.context_lines("which service is most reliable for flights?")
    assert any("airlabs 4 calls, 75% worked" in x for x in lines)
