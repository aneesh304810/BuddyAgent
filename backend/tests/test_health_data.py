"""Buddy Connect's Health group: the phone's own data, and public drug, topic and food information."""

import json
from datetime import timedelta

import pytest

from buddy import gateway, health_data
from buddy.memory import Memory
from buddy.tools import local_now


def _day(n):
    return (local_now().date() - timedelta(days=n)).isoformat()


def test_phone_days_are_kept_summed_and_old_ones_dropped(tmp_path):
    m = Memory(tmp_path / "b.db")
    n = health_data.ingest(m, {"days": [
        {"day": _day(1), "steps": 8000, "sleep_minutes": 420, "resting_hr": 58,
         "workouts": [{"type": "running", "minutes": 30}]},
        {"day": _day(0), "steps": "10000.4", "sleep_minutes": 390, "resting_hr": 60},
        {"day": _day(120), "steps": 1},          # older than 90 days: dropped
        {"day": "not a date", "steps": 5}]})
    assert n == 3
    s = health_data.summary(m, 7)
    assert s["days"] == 2 and s["avg_steps"] == 9000 and s["avg_sleep_minutes"] == 405
    assert s["workouts"] == [{"day": _day(1), "type": "running", "minutes": 30}]
    st = {x["name"]: x for x in gateway.status(m, env={})}
    assert st["health_connect"]["group"] == "Health" and st["health_connect"]["last_ok"] is True
    assert st["health_connect"]["privacy"] == "personal"


def test_no_phone_data_says_how_to_connect(tmp_path):
    assert "Connections > Health" in health_data.run_my_health(Memory(tmp_path / "b.db"), {})


def test_drug_label_from_openfda(tmp_path):
    def fetch(url, data=None, headers=None):
        assert url.startswith("https://api.fda.gov/drug/label.json?search=") and "ibuprofen" in url
        return json.dumps({"results": [{"openfda": {"brand_name": ["Advil"], "generic_name": ["IBUPROFEN"]},
                                        "indications_and_usage": ["temporarily relieves minor aches"],
                                        "warnings": ["Stomach bleeding warning"]}]}).encode()
    got = health_data.drug(Memory(tmp_path / "b.db"), "ibuprofen", fetch=fetch)
    assert got["name"] == "Advil" and "aches" in got["used_for"] and "bleeding" in got["warnings"]


def test_nothing_found_on_openfda_is_said_plainly(tmp_path, monkeypatch):
    def nothing(*a, **k):
        raise gateway.ConnectError("openfda", "openFDA said HTTP 404", 404)
    monkeypatch.setattr(health_data, "drug", nothing)
    said = health_data.run_health_info(Memory(tmp_path / "b.db"), {"action": "drug", "query": "zzz"})
    assert "Nothing found" in said


def test_medlineplus_topic_and_no_entities(tmp_path):
    xml = (b'<?xml version="1.0"?><nlmSearchResult><list><document url="https://medlineplus.gov/sleepdisorders.html">'
           b'<content name="title">Sleep &lt;span&gt;Disorders&lt;/span&gt;</content>'
           b'<content name="FullSummary">&lt;p&gt;Sleep disorders are conditions...&lt;/p&gt;</content>'
           b'</document></list></nlmSearchResult>')
    got = health_data.topic(Memory(tmp_path / "b.db"), "sleep", fetch=lambda u, d=None, h=None: xml)
    assert got["title"] == "Sleep Disorders" and got["summary"].startswith("Sleep disorders") and "medlineplus" in got["url"]
    evil = b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "b">]><x/>'
    with pytest.raises(Exception):
        health_data.topic(Memory(tmp_path / "c.db"), "sleep", fetch=lambda u, d=None, h=None: evil)


def test_food_by_barcode(tmp_path):
    def fetch(url, data=None, headers=None):
        assert url == "https://world.openfoodfacts.org/api/v2/product/3017624010701.json"
        return json.dumps({"product": {"product_name": "Nutella", "nutriments": {"sugars_100g": 56.3},
                                       "nutriscore_grade": "e", "allergens_tags": ["en:milk"]}}).encode()
    got = health_data.food(Memory(tmp_path / "b.db"), "3017624010701", fetch=fetch)
    assert got["name"] == "Nutella" and got["per_100g"]["sugars"] == 56.3 and got["nutriscore"] == "e"


def test_health_tools_never_count_as_general():
    from buddy import agent
    assert not {"my_health", "health_info"} & agent.GENERAL_TOOLS
