"""A scan into the day's meals by time and place, and whether the day is on track on fats and unhealthy food.
Code only: the place is compared on the server, and no AI model is given any of it."""

import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from buddy import checkin, coach, foodverdict, scanmeal
from buddy.maps import Maps
from buddy.memory import Memory
from buddy.tools import ToolError
from tests.test_dietcheck import _me
from tests.test_foodverdict import BREAD, CHIPS, OATS

HOME = (12.9716, 77.5946)
STORE = (12.9800, 77.6000)
CAFE = (12.9900, 77.6100)
LUNCH = datetime(2026, 11, 26, 12, 30)
EVENING = datetime(2026, 11, 26, 21, 30)


def _at(m, pos, minutes_ago=1):
    when = datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
    m.set("location:current", json.dumps({"lat": pos[0], "lng": pos[1], "area": "", "city": "", "at": when.isoformat()}))


def _owner(tmp_path):
    m = Memory(tmp_path / "b.db")
    _me(m, plan_diet={"calories": 2000})
    Maps(m).name_place("home", *HOME)
    return m


def test_where_by_place_and_freshness(tmp_path):
    m = _owner(tmp_path)
    got = scanmeal.where(m, LUNCH)
    assert got["do"] == "ask" and "Share my location" in got["why"] and got["slot"] == "lunch"
    _at(m, HOME)
    assert scanmeal.where(m, LUNCH) | {"why": ""} == {"do": "log", "place": "home", "slot": "lunch",
                                                     "can_remember": False, "why": ""}
    _at(m, CAFE)
    got = scanmeal.where(m, LUNCH)
    assert got["do"] == "ask" and got["can_remember"]
    _at(m, HOME, minutes_ago=45)  # too old to say where the owner is now
    got = scanmeal.where(m, LUNCH)
    assert got["do"] == "ask" and "45 minutes ago" in got["why"] and not got["can_remember"]


def test_marked_store_is_shopping_and_marked_cafe_is_eating(tmp_path):
    m = _owner(tmp_path)
    with pytest.raises(ToolError):  # no position: nothing to mark
        scanmeal.remember(m, "store", "Costco")
    _at(m, STORE)
    assert [p["name"] for p in scanmeal.remember(m, "store", " Costco ")] == ["Costco"]
    got = scanmeal.where(m, LUNCH)
    assert got["do"] == "verdict" and got["place"] == "Costco" and "isn't added" in got["why"]
    _at(m, (STORE[0] + 0.0005, STORE[1]))  # about 55 m away: the same store
    assert scanmeal.where(m, LUNCH)["do"] == "verdict"
    _at(m, CAFE)
    scanmeal.remember(m, "eat", "")
    assert scanmeal.where(m, LUNCH)["do"] == "log"
    assert {p["name"] for p in scanmeal.places(m)} == {"Costco", "A place I eat"}
    with pytest.raises(ToolError):
        scanmeal.remember(m, "pub")
    assert scanmeal.forget_place(m) == 2 and scanmeal.places(m) == []


def test_eat_undo_and_recount(tmp_path):
    m = _owner(tmp_path)
    scan = foodverdict.keep(m, CHIPS, "sometimes")
    got = scanmeal.eat(m, scan, 2, now=LUNCH)
    assert got["slot"] == "lunch" and got["what"] == "Sea salt kettle chips (2 × 1 oz (28 g), 56 g)"
    meals = coach.eaten(m, LUNCH.date())
    assert meals["sat_fat_g"] == pytest.approx(4.5, abs=0.05) and meals["calories"] == 300
    back = scanmeal.recount(m, got["meal_id"], 1)
    assert back["slot"] == "lunch" and back["servings"] == 1
    assert coach.eaten(m, LUNCH.date())["calories"] == 150
    scanmeal.uneat(m, back["meal_id"])
    assert coach.eaten(m, LUNCH.date())["meals"] == []
    coach.add_meal(m, "Rice and dal", 500, 15, now=LUNCH)  # may reuse the id: it still didn't come from a scan
    meal_id = coach.eaten(m, LUNCH.date())["meals"][0]["id"]
    with pytest.raises(ToolError):
        scanmeal.uneat(m, meal_id)
    with pytest.raises(ToolError):
        scanmeal.eat(m, scan, 0)
    with pytest.raises(ToolError):
        scanmeal.eat(m, 999)


def test_on_track_pace_over_and_a_lighter_dinner(tmp_path):
    m = _owner(tmp_path)
    assert scanmeal.ontrack(m, LUNCH)["meals"] == 0
    oats = foodverdict.keep(m, OATS, "good")
    scanmeal.eat(m, oats, 1, now=LUNCH.replace(hour=8))
    early = scanmeal.ontrack(m, LUNCH.replace(hour=8, minute=30))
    assert early["on_track"] and early["lines"] == []
    chips = foodverdict.keep(m, CHIPS, "sometimes")
    got = scanmeal.eat(m, chips, 4, now=LUNCH)["ontrack"]  # 9 g saturated fat, 3.6 g sodium... by lunch
    sat = next(r for r in got["rows"] if r["key"] == "sat_fat_g")
    assert sat["limit"] == 22 and sat["state"] == "ok"  # 9.5 of 22 g by lunch is within pace
    scanmeal.eat(m, chips, 4, now=LUNCH)
    got = scanmeal.ontrack(m, LUNCH)
    sat = next(r for r in got["rows"] if r["key"] == "sat_fat_g")
    assert sat["state"] == "ahead" and not got["on_track"]
    assert "Saturated fat today 18.5 of your 22 g: ahead of pace for this time of day." in got["lines"]
    assert got["advice"][0].startswith("For dinner keep saturated fat under 3.5 g, fat under 3.9 g, sodium under 284 mg")
    nxt = got["next_meal"]
    assert nxt["slot"] == "dinner" and nxt["items"] and nxt["totals"]["sat_fat_g"] <= 3.5 and not nxt["fits"]
    assert "No AI model" in got["note"]
    scanmeal.eat(m, chips, 4, now=EVENING)
    late = scanmeal.ontrack(m, EVENING)
    assert next(r for r in late["rows"] if r["key"] == "sat_fat_g")["state"] == "over"
    assert any(line.endswith("close to your limit.") or "over your" in line for line in late["lines"])
    assert late["advice"] == ["Tomorrow's menu (Food → Menu) keeps the day within your numbers."]


def test_ultra_processed_sugar_and_the_evening_line(tmp_path):
    m = _owner(tmp_path)
    assert scanmeal.evening_line(m, EVENING) == ""
    bread = foodverdict.keep(m, BREAD, "sometimes")
    scanmeal.eat(m, bread, 1, now=LUNCH)
    scanmeal.eat(m, bread, 1, now=EVENING)
    coach.add_meal(m, "Gulab jamun", 300, 3, now=EVENING, extra={"added_sugar_g": 25})
    got = scanmeal.ontrack(m, EVENING)
    assert got["ultra_processed"] == 2 and got["sugary_meals"] == 1
    assert any("ultra-processed foods today" in line for line in got["lines"])
    line = scanmeal.evening_line(m, EVENING)
    assert line.startswith("Food today went over on added sugar in 1 meal, 2 ultra-processed foods")


def test_check_in_carries_the_line(tmp_path, monkeypatch):
    m = _owner(tmp_path)
    monkeypatch.setattr(scanmeal, "evening_line", lambda memory, now=None: "Food today went over on sodium.")
    assert "Food today went over on sodium." in checkin.readback(m, {})


def test_api_where_eat_and_ontrack(tmp_path, monkeypatch):
    from buddy import server
    m = _owner(tmp_path)
    monkeypatch.setattr(server, "memory", m)
    monkeypatch.setattr(server, "authorized", lambda t: t == "s3cret")
    client, auth = TestClient(server.app), {"Authorization": "Bearer s3cret"}
    _at(m, STORE)
    r = client.post("/api/health/where", json={"action": "remember", "kind": "store", "name": "Costco"}, headers=auth)
    assert r.status_code == 200 and r.json()["places"][0]["name"] == "Costco"
    got = client.get("/api/health/where", headers=auth).json()
    assert got["do"] == "verdict" and got["places"][0]["kind"] == "store"
    scan = client.post("/api/health/verdict", json={"food": OATS, "keep": True}, headers=auth).json()["id"]
    ate = client.post("/api/health/scans", json={"action": "eat", "id": scan, "servings": 1}, headers=auth).json()
    assert ate["meal_id"] and "ontrack" in ate
    assert client.get("/api/health/ontrack", headers=auth).json()["meals"] == 1
    assert client.post("/api/health/scans", json={"action": "uneat", "meal": ate["meal_id"]}, headers=auth).status_code == 200
    assert client.post("/api/health/scans", json={"action": "uneat", "meal": ate["meal_id"]}, headers=auth).status_code == 400
    assert client.post("/api/health/where", json={"action": "forget"}, headers=auth).json()["places"] == []
    assert client.get("/api/health/ontrack").status_code in (401, 403)
