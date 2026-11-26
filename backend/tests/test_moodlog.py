"""The mood and stress journal: the owner's own entries, a day as the average of its entries, a factor in the personal
model, kept on the server and never given to an AI model."""

import pytest
from fastapi.testclient import TestClient

from buddy import healthmodel, moodlog, privacygate
from buddy.memory import Memory
from buddy.tools import ToolError, local_now


def test_entries_are_checked_and_a_day_is_their_average(tmp_path):
    m = Memory(tmp_path / "b.db")
    moodlog.add(m, mood=4, stress=2, tags=["work", "made up"], note="  long   day  ")
    moodlog.add(m, mood=2, stress=4, energy=3)
    day = moodlog.daily(m)[local_now().date().isoformat()]
    assert (day["mood"], day["stress"], day["energy"], day["entries"]) == (3.0, 3.0, 3.0, 2)
    first = moodlog.entries(m)[-1]
    assert first["tags"] == ["work"] and first["note"] == "long day"  # unknown tags dropped, spaces tidied
    with pytest.raises(ToolError, match="at least one"):
        moodlog.add(m, mood=9)
    with pytest.raises(ToolError, match="hasn't come"):
        moodlog.add(m, mood=3, day="2999-01-01")
    view = moodlog.view(m)
    assert view["week"]["mood"] == 3.0 and view["tags"] == [("work", 1)] and "988" in view["note"]


def test_the_journal_is_a_factor_in_the_personal_model(tmp_path):
    from datetime import timedelta
    from buddy import oura
    m = Memory(tmp_path / "b.db")
    oura._ready(m)
    today = local_now().date()
    for i in range(80, 0, -1):
        day, nxt = (today - timedelta(days=i)).isoformat(), (today - timedelta(days=i - 1)).isoformat()
        stressed = i % 2 == 0
        moodlog.add(m, stress=5 if stressed else 1, day=day)
        m.db.execute("INSERT OR REPLACE INTO oura_daily (day, readiness, workouts, updated_at) VALUES (?, ?, '[]', 'x')",
                     (nxt, 60 if stressed else 80))
    m.db.commit()
    got = healthmodel.learn(m)
    found = [p for p in got["patterns"] if p["factor"] == "stress" and p["outcome"] == "readiness"]
    assert found and not found[0]["better"] and "stress (your journal)" in found[0]["text"]


def test_a_journal_note_is_health_information_to_the_gateway():
    assert privacygate.health("I feel anxious about work again")


def test_the_page_adds_deletes_and_forgets(tmp_path, monkeypatch):
    from buddy import server
    monkeypatch.setattr(server, "memory", Memory(tmp_path / "b.db"))
    monkeypatch.setattr(server, "authorized", lambda t: t == "s3cret")
    client, auth = TestClient(server.app), {"Authorization": "Bearer s3cret"}
    out = client.post("/api/health/mood", json={"mood": 4, "stress": 2, "tags": ["exercise"]}, headers=auth).json()
    assert out["text"] == "Noted." and out["entries"][0]["mood"] == 4
    assert client.post("/api/health/mood", json={"mood": 0}, headers=auth).status_code == 400
    gone = client.post("/api/health/mood", json={"action": "delete", "id": out["entries"][0]["id"]}, headers=auth).json()
    assert gone["text"] == "Deleted." and gone["entries"] == []
    assert client.post("/api/health/mood", json={"action": "forget"}, headers=auth).status_code == 400
    assert client.post("/api/health/mood", json={"action": "forget", "confirm": True}, headers=auth).json()["text"].startswith("Deleted all")
