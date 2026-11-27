"""Several taught foods on one plate: the first time each is added, what the owner picked is kept as its usual, so
the next plate goes in with one tap; a food without a usual comes back for the owner to pick once."""

from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from buddy import coach, healthpage, platefoods
from buddy.connectors.hub import ConnectorHub
from buddy.memory import Memory
from buddy.tools import ToolError

DINNER = datetime(2026, 11, 26, 22, 27)


@pytest.fixture
def memory(tmp_path):
    m = Memory(tmp_path / "buddy.db")
    ConnectorHub(m)
    return m


PITA = {"action": "meal", "what": "Pita bread, white (1 × 1 pita, 60 g)", "calories": 165, "protein_g": 5,
        "carbs_g": 33.4, "fat_g": 0.7, "sodium_mg": 322, "slot": "dinner", "dish": "Pita"}


def test_the_first_add_is_remembered_and_the_next_plate_goes_in_at_once(memory):
    healthpage.act(memory, PITA)  # picked once, from the plate's walk-through
    assert platefoods.usual(memory, "pita")["what"].startswith("Pita bread")  # any case
    got = platefoods.add_plate(memory, ["Pita", "Grilled chicken", "pita", "Blueberry salad"], now=DINNER)
    assert got["added"] == ["Pita"] and got["unknown"] == ["Grilled chicken", "Blueberry salad"]
    assert got["slot"] == "dinner"  # by the time, 10:27 PM
    meals = coach.eaten(memory, DINNER.date())["meals"]
    assert [m["what"] for m in meals] == ["Pita bread, white (1 × 1 pita, 60 g)"] and meals[0]["sodium_mg"] == 322
    healthpage.act(memory, {**PITA, "what": "Pita bread, whole wheat (1 × 1 pita, 64 g)", "calories": 170})
    assert platefoods.usual(memory, "Pita")["calories"] == 170  # the latest pick wins
    platefoods.forget(memory, "Pita")
    assert platefoods.foods(memory) == []
    with pytest.raises(ToolError):
        platefoods.add_plate(memory, [])


def test_a_meal_without_a_dish_is_not_remembered(memory):
    healthpage.act(memory, {k: v for k, v in PITA.items() if k != "dish"})
    assert platefoods.foods(memory) == []


def test_the_plate_api(memory, monkeypatch):
    from buddy import server
    monkeypatch.setattr(server, "memory", memory)
    monkeypatch.setattr(server, "authorized", lambda t: t == "s3cret")
    client, auth = TestClient(server.app), {"Authorization": "Bearer s3cret"}
    assert client.post("/api/health/page", json=PITA, headers=auth).status_code == 200
    got = client.post("/api/health/page", json={"action": "plate", "dishes": ["Pita", "Grilled chicken"],
                                                "slot": "dinner"}, headers=auth).json()
    assert got["added"] == ["Pita"] and got["unknown"] == ["Grilled chicken"]
    assert client.post("/api/health/page", json={"action": "plate", "dishes": ["Pita"]}).status_code == 401
