"""Life log: imports (Timeline, Takeout, calendar, anything else) and search."""

import asyncio
import io
import json
import zipfile
from types import SimpleNamespace

from fastapi.testclient import TestClient

from buddy import lifelog, server
from buddy.lifelog import LifeLog, run_lifelog_tool
from buddy.memory import Memory

TAKEOUT = {"timelineObjects": [
    {"placeVisit": {"location": {"name": "Machu Picchu", "address": "Aguas Calientes, Peru",
                                 "latitudeE7": -131631000, "longitudeE7": -725450000, "placeId": "p1"},
                    "duration": {"startTimestamp": "2024-03-12T13:00:00Z", "endTimestamp": "2024-03-12T17:00:00Z"}}},
    {"activitySegment": {"activityType": "FLYING", "distance": 1_100_000,
                         "duration": {"startTimestamp": "2024-03-10T08:00:00Z", "endTimestamp": "2024-03-10T11:00:00Z"}}},
    {"activitySegment": {"activityType": "WALKING", "distance": 900,
                         "duration": {"startTimestamp": "2024-03-12T12:00:00Z", "endTimestamp": "2024-03-12T12:20:00Z"}}},
]}
TIMELINE = {"semanticSegments": [
    {"startTime": "2025-06-01T09:00:00-04:00", "endTime": "2025-06-01T18:00:00-04:00",
     "visit": {"topCandidate": {"placeId": "w", "semanticType": "WORK", "placeLocation": {"latLng": "25.76°, -80.19°"}}}},
    {"startTime": "2025-06-01T18:00:00-04:00", "endTime": "2025-06-01T18:30:00-04:00", "activity": {}},
]}
ICS = b"BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nUID:x1\r\nSUMMARY:Priya's wedding\r\nLOCATION:Kochi\r\n" \
      b"DTSTART;VALUE=DATE:20230115\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n"


def test_known_formats_in_a_zip_and_search():
    log = LifeLog(Memory(":memory:"))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("Takeout/Location History/Semantic Location History/2024/2024_MARCH.json", json.dumps(TAKEOUT))
        z.writestr("Timeline.json", json.dumps(TIMELINE))
        z.writestr("calendar.ics", ICS)
        z.writestr("photo.jpg", b"\xff\xd8")  # ignored
    result = asyncio.run(log.import_file("takeout.zip", buf.getvalue()))
    assert result["by_kind"] == {"place": 2, "trip": 1, "event": 1} and result["files"] == 3
    assert log.search("machu")[0]["place"] == "Aguas Calientes, Peru"
    assert [e["title"] for e in log.search(start="2024-03-01", end="2024-03-31")] == [
        "Machu Picchu", "Journey (flying, 1100 km)"]
    assert log.search(kind="event")[0]["title"] == "Priya's wedding"
    again = asyncio.run(log.import_file("takeout.zip", buf.getvalue()))
    assert again["added"] == 0  # importing twice doesn't duplicate


def test_other_exports_are_read_by_the_model(monkeypatch):
    calls = []

    async def create(**kw):
        calls.append(kw)
        found = {"events": [{"start": "2023-07-02", "end": "2023-07-06", "kind": "stay", "title": "Airbnb in Cusco",
                             "place": "Cusco, Peru", "details": "Hosted by Rosa"}]}
        usage = SimpleNamespace(input_tokens=5000, output_tokens=100, cache_read_input_tokens=0,
                                cache_creation_input_tokens=0, server_tool_use=None)
        return SimpleNamespace(model="claude-haiku-5-5", usage=usage, stop_reason="end_turn",
                               content=[SimpleNamespace(type="text", text=json.dumps(found))])

    monkeypatch.setattr(lifelog, "client", SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(create=create))))
    log = LifeLog(Memory(":memory:"))
    html = b"<html><body><h1>Your trips</h1><p>Cusco, Jul 2 - 6, 2023, card 4111 1111 1111 1111</p></body></html>"
    result = asyncio.run(log.import_file("airbnb/reservations.html", html))
    assert result["by_kind"] == {"stay": 1}
    assert "4111 1111" not in calls[0]["messages"][0]["content"]  # masked before the model sees it
    assert "Cusco" in run_lifelog_tool(log, {"query": "cusco"})


def test_daily_diary_and_overview():
    log = LifeLog(Memory(":memory:"))
    assert "empty" in run_lifelog_tool(log, {})
    log.log_day("Done today: Gym. Calendar: dentist.")
    log.log_day("Done today: Gym, groceries.")  # the evening entry is replaced, not doubled
    assert json.loads(run_lifelog_tool(log, {}))["by_kind"] == {"day": 1}
    assert "groceries" in run_lifelog_tool(log, {"kind": "day"})


def test_upload_endpoint_needs_the_token(monkeypatch):
    monkeypatch.setattr(server, "authorized", lambda t: t == "s3cret")
    with TestClient(server.app) as c:
        assert c.post("/api/import?name=Timeline.json", content=json.dumps(TIMELINE)).status_code == 401
        r = c.post("/api/import?name=Timeline.json", content=json.dumps(TIMELINE),
                   headers={"Authorization": "Bearer s3cret"})
    assert r.status_code == 200 and r.json()["files"] == 1
