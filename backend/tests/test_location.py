"""The location tool resolves the phone's GPS fix to a street address, nearby places and the shop it's in, and
still gives neighbourhood and city when no place matches. The routine names exact places for the owner only."""

import json
from datetime import timedelta

from buddy import location as loc_mod
from buddy import routine
from buddy.location import Location, around, metres, parse_reverse, shop_at
from buddy.maps import Maps
from buddy.memory import Memory
from buddy.tools import local_now

HERE = (40.74200, -74.00480)
OSM_IN_CAFE = {"category": "amenity", "type": "cafe", "name": "Blue Bottle Coffee",
               "address": {"house_number": "450", "road": "West 15th Street", "neighbourhood": "Chelsea",
                           "city": "New York", "state": "New York", "country_code": "us"}}
OSM_ON_STREET = {"category": "highway", "type": "residential", "name": "West 15th Street",
                 "address": {"house_number": "452", "road": "West 15th Street", "neighbourhood": "Chelsea",
                             "city": "New York", "state": "New York", "country_code": "us"}}


def _offset(m_north: float, m_east: float = 0.0) -> tuple[float, float]:
    return HERE[0] + m_north / 111_320, HERE[1] + m_east / 84_300


def _places():
    cafe, deli, bar = _offset(8), _offset(-30, 10), _offset(400)
    return [{"name": "Blue Bottle Coffee", "category": "Coffee shop", "address": "450 W 15th St",
             "lat": cafe[0], "lng": cafe[1]},
            {"name": "Chelsea Deli", "category": "Deli", "address": "440 W 15th St", "lat": deli[0], "lng": deli[1]},
            {"name": "Far Bar", "category": "Bar", "address": "9th Ave", "lat": bar[0], "lng": bar[1]}]


def test_parse_reverse_street_and_inside():
    p = parse_reverse(OSM_IN_CAFE)
    assert p["street"] == "450 West 15th Street" and p["area"] == "Chelsea" and p["city"] == "New York"
    assert p["inside"] == {"name": "Blue Bottle Coffee", "kind": "cafe"}
    assert "inside" not in parse_reverse(OSM_ON_STREET)


def test_sample_coordinate_gives_street_nearby_and_current_shop():
    memory = Memory(":memory:")
    here = Location(memory, reverse=lambda la, ln: parse_reverse(OSM_IN_CAFE), nearby=lambda la, ln: _places())
    now = here.update(*HERE, 10)
    assert now["street"] == "450 West 15th Street"
    assert [p["name"] for p in now["nearby"]] == ["Blue Bottle Coffee", "Chelsea Deli"]  # Far Bar is 400 m off
    assert now["nearby"][0]["distance_m"] <= 10 and now["nearby"][1]["distance_m"] <= 50
    assert now["current_shop"] == "Blue Bottle Coffee" and now["current_shop_category"] == "Coffee shop"
    assert here.label() == "Chelsea, New York" and "inside" not in now
    assert json.loads(memory.get("location:current"))["current_shop"] == "Blue Bottle Coffee"


def test_current_shop_from_nearest_place_when_osm_says_street():
    near, shop = around(*HERE, parse_reverse(OSM_ON_STREET), _places())
    assert shop["name"] == "Blue Bottle Coffee"  # 8 m away: inside it
    far = [p for p in _places() if p["name"] != "Blue Bottle Coffee"]
    near, shop = around(*HERE, parse_reverse(OSM_ON_STREET), far)
    assert shop is None and [p["name"] for p in near] == ["Chelsea Deli"]  # 30 m: nearby, not inside


def test_no_place_matches_still_neighbourhood_and_city():
    memory = Memory(":memory:")
    here = Location(memory, reverse=lambda la, ln: {"area": "Miraflores", "city": "Lima", "country": "PE"},
                    nearby=lambda la, ln: [])
    now = here.update(-12.1211, -77.0297, 30)
    assert now["area"] == "Miraflores" and now["city"] == "Lima"
    assert now["current_shop"] == "" and now["nearby"] == [] and now["street"] == ""
    assert here.label() == "Miraflores, Lima"


def test_lookup_failures_dont_break_location():
    def boom(lat, lng):
        raise OSError("down")
    memory = Memory(":memory:")
    now = Location(memory, reverse=lambda la, ln: parse_reverse(OSM_ON_STREET), nearby=boom).update(*HERE)
    assert now["street"] == "452 West 15th Street" and now["current_shop"] == "" and now["city"] == "New York"
    now = Location(memory, reverse=boom, nearby=boom).update(*HERE)
    assert now["lat"] == round(HERE[0], 5) and now["current_shop"] == ""


def test_nearby_uses_serper_through_gateway_and_skips_without_key(monkeypatch):
    from buddy import gateway
    monkeypatch.delenv("BUDDY_SERPER_KEY", raising=False)
    loc_mod._near.clear()
    assert loc_mod._nearby(*HERE) == []
    calls = []

    def fake_call(memory, name, path, **kw):
        calls.append((name, path, kw["json_body"]))
        return {"places": [{"title": "Blue Bottle Coffee", "category": "Coffee shop", "latitude": HERE[0],
                            "longitude": HERE[1], "address": "450 W 15th St"}, {"title": "No coords"}]}
    monkeypatch.setattr(gateway, "call", fake_call)
    monkeypatch.setenv("BUDDY_SERPER_KEY", "test-key")
    got = loc_mod._nearby(*HERE)
    assert [p["name"] for p in got] == ["Blue Bottle Coffee"]
    assert calls[0][0] == "serper" and calls[0][1] == "/places" and calls[0][2]["ll"].startswith("@40.742")
    assert "test-key" not in json.dumps(calls)  # the key is added by the gateway, server-side
    loc_mod._near.clear()


def test_metres():
    assert abs(metres(HERE, _offset(100)) - 100) < 1


def test_routine_names_shops_for_owner_only():
    memory = Memory(":memory:")
    maps = Maps(memory)
    home = (40.70, -74.10)
    maps.name_place("home", *home)
    cafe = _offset(5)
    memory.set(loc_mod.SHOPS_KEY, json.dumps([{"name": "Blue Bottle Coffee", "lat": cafe[0], "lng": cafe[1]}]))
    assert shop_at(memory, *HERE) == "Blue Bottle Coffee" and shop_at(memory, *home) == ""
    tz = local_now().tzinfo
    start = (local_now() - timedelta(days=28)).replace(hour=0, minute=0, second=0, microsecond=0)
    for d in range(28):
        day = start + timedelta(days=d)
        for minute in range(0, 24 * 60, 15):
            t = (day + timedelta(minutes=minute)).astimezone(tz)
            spot = HERE if day.weekday() == 5 and 600 <= minute < 660 else home
            maps.log_location(*spot, at=t)
    routine.save(memory, routine.learn(memory))
    r = routine.week(memory)
    assert "at Blue Bottle Coffee 10 am to 11 am" in routine.describe(r, exact=True)
    assert "Blue Bottle" not in routine.describe(r) and "out (errands) 10 am to 11 am" in routine.describe(r)
    assert "Blue Bottle Coffee" in routine.run_tool(memory, {"action": "show"})


def test_activity_now_and_here_line_keep_exact_place_for_owner():
    memory = Memory(":memory:")
    Location(memory, reverse=lambda la, ln: parse_reverse(OSM_IN_CAFE), nearby=lambda la, ln: _places()).update(*HERE)
    a = routine.activity_now(memory)
    assert a["what"] == "out (errands)" and "Blue Bottle Coffee" in a["place"]
    assert "Blue Bottle" not in routine.now_line(memory)  # what callers can hear
    line = routine.here_line(memory)
    assert line.startswith("at Blue Bottle Coffee (Coffee shop), 450 West 15th Street") and "Chelsea Deli" in line
    assert "Exact place now: at Blue Bottle Coffee" in routine.run_tool(memory, {"action": "show"})
    assert maps_forget_clears_shops(memory)


def maps_forget_clears_shops(memory) -> bool:
    assert loc_mod.shops_seen(memory)
    Maps(memory).forget_location()
    return loc_mod.shops_seen(memory) == []
