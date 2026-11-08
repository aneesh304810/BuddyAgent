"""Weather (Open-Meteo) and packages (shipping emails and notifications, 17TRACK)."""

import json
from datetime import date, datetime, timezone

from buddy.connectors.hub import ConnectorHub
from buddy.memory import Memory
from buddy.packages import Packages, find_numbers, status_in
from buddy.weather import Weather

TODAY = date.today().isoformat()


def meteo(url):
    if "geocoding" in url:
        return json.dumps({"results": [{"name": "Lima", "admin1": "Lima Province", "country": "Peru",
                                        "latitude": -12.04, "longitude": -77.03}]}).encode()
    return json.dumps({"timezone": "America/New_York",
                       "current": {"temperature_2m": 51.4, "apparent_temperature": 48.0, "weather_code": 3},
                       "daily": {"time": [TODAY], "weather_code": [63], "temperature_2m_max": [55.2],
                                 "temperature_2m_min": [44.8], "precipitation_probability_max": [80]},
                       "hourly": {"time": [f"{TODAY}T09:00", f"{TODAY}T15:00"],
                                  "precipitation_probability": [20, 75]}}).encode()


def test_weather_forecast_and_words():
    w = Weather(Memory(":memory:"), fetch=meteo)
    p = w.place("Lima, Peru")
    assert p["name"] == "Lima, Lima Province, Peru"
    fc = w.forecast(p["lat"], p["lng"], 1, p["name"])
    assert fc["days"][0] == {"day": TODAY, "conditions": "rain", "wet": True, "high": 55, "low": 45, "rain_chance": 80}
    assert Weather.wet_from(fc, TODAY) == "from 3 PM"
    text = Weather.describe(fc, 1)
    assert text.startswith("Lima, Lima Province, Peru: Now 51°F, cloudy") and "80% chance of rain" in text


def add(memory, kind, title, body, who):
    now = datetime.now(timezone.utc).isoformat()
    data = {"app_name": who} if kind == "notification" else {"from": f"{who} <ship@x.com>"}
    memory.db.execute("INSERT INTO records (source, external_id, kind, title, body, start_at, data, synced_at) VALUES "
                      "(?, ?, ?, ?, ?, ?, ?, ?)", ("email" if kind == "email" else "notifications", title + body, kind,
                                                   title, body, now, json.dumps(data), now))


def test_tracking_numbers_and_status_from_text():
    assert find_numbers("Your UPS package 1Z999AA10123456784 has shipped") == [("UPS", "1Z999AA10123456784")]
    assert find_numbers("Order 123456789012 confirmed") == []  # 12 digits without 'FedEx': not a FedEx number
    assert find_numbers("FedEx tracking 123456789012") == [("FedEx", "123456789012")]
    assert status_in("Your package is out for delivery") == "out_for_delivery"
    assert status_in("Delivered: left at front door") == "delivered"


def test_packages_from_emails_then_said_once():
    m = Memory(":memory:")
    ConnectorHub(m)
    add(m, "email", "Your order has shipped", "AirPods Pro. UPS tracking number 1Z999AA10123456784.", "Amazon")
    add(m, "email", "Weekly deals", "50% off", "Shop")
    pk = Packages(m, env={})
    assert pk.scan() == 1
    p = pk.active()[0]
    assert p["carrier"] == "UPS" and p["status"] == "shipped" and "ups.com/track?tracknum=1Z999" in p["link"]
    add(m, "notification", "UPS: Out for delivery", "1Z999AA10123456784 is out for delivery today", "UPS")
    pk.scan()
    news = pk.news()
    assert len(news) == 1 and "is out for delivery today" in news[0]["text"]
    pk.told(news[0])
    assert pk.news() == []
    add(m, "notification", "UPS: Delivered", "1Z999AA10123456784 was delivered, left at front door", "UPS")
    pk.scan()
    news = pk.news(away_from_home=True)
    assert "was delivered; it's waiting at home" in news[0]["text"]
    assert any("1Z999AA10123456784" in x for x in []) or pk.lines() == []  # delivered: not 'on the way' any more


def test_17track_live_status():
    m = Memory(":memory:")
    calls = []

    def track(url, data, headers):
        calls.append(url.rsplit("/", 1)[1])
        assert headers["17token"] == "k"
        if url.endswith("register"):
            return b'{"code": 0, "data": {"accepted": [{"number": "1Z999AA10123456784"}]}}'
        return json.dumps({"code": 0, "data": {"accepted": [{"number": "1Z999AA10123456784", "track_info": {
            "latest_status": {"status": "InTransit"}, "latest_event": {"description": "Departed facility",
                                                                       "location": "Newark, NJ"},
            "time_metrics": {"estimated_delivery_date": {"from": f"{TODAY}T00:00:00"}}}}]}}).encode()
    pk = Packages(m, env={"BUDDY_17TRACK_KEY": "k"}, fetch=track)
    pk.add("1Z999AA10123456784", "UPS", "AirPods")
    assert pk.refresh() == 1 and calls == ["register", "gettrackinfo"]
    p = pk.active()[0]
    assert p["status"] == "in_transit" and p["last_event"] == "Departed facility Newark, NJ" and p["eta"] == TODAY
    assert pk.refresh() == 0  # checked: not again for two hours
