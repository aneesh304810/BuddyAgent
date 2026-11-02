"""Diagnostics: phone and server events -> experience health, setup test checks, export. No audio, no words."""

import json
import time
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from buddy import diagnostics, insights
from buddy.memory import Memory


def since(days=1):
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


def ms_ago(seconds=0):
    return (time.time() - seconds) * 1000


def test_phone_batch_is_cleaned_and_summarised():
    mem = Memory(":memory:")
    events = []
    for i in range(10):  # the earbud mic is noisy, the phone mic is fine
        events.append({"at": ms_ago(100 + i), "category": "clip", "event": "sent",
                       "data": {"mic": "headset", "snr_db": 6.0 + i % 3, "noise_db": 52, "speech_db": 58,
                                "clipped": False, "text": "secret words never kept"}})
        events.append({"at": ms_ago(200 + i), "category": "clip", "event": "sent",
                       "data": {"mic": "phone", "snr_db": 22.0, "noise_db": 38, "speech_db": 60, "clipped": i < 3}})
    events += [{"at": ms_ago(50), "category": "clip", "event": "dropped", "data": {"reason": "too_short"}},
               {"at": ms_ago(40), "category": "network", "event": "ping", "data": {"rtt_ms": 320, "net": "wifi"}},
               {"at": ms_ago(30), "category": "network", "event": "disconnected", "data": {}},
               {"at": ms_ago(20), "category": "network", "event": "reconnected", "data": {"offline_ms": 4200}},
               {"at": ms_ago(10), "category": "background", "event": "service_restarted", "data": {}},
               {"at": ms_ago(10), "category": "background", "event": "service_restarted", "data": {}},
               {"at": ms_ago(5), "category": "speech", "event": "recognizer_error", "data": {"error": "busy"}},
               {"at": "garbage", "category": "nope", "event": "x"}]
    assert diagnostics.record_batch(mem, events) == len(events)
    stored = [json.loads(r["data"]) for r in mem.db.execute("SELECT data FROM diag_events")]
    assert not any("text" in d for d in stored)  # words never stored
    h = diagnostics.health(mem, since())
    assert h["microphone"]["by_mic"]["headset"]["low_snr_pct"] == 100
    assert h["microphone"]["by_mic"]["phone"]["clipped_pct"] == 30
    assert h["network"]["ping_ms"] == 320 and h["network"]["disconnects"] == 1 and h["network"]["reconnect_ms"] == 4200
    assert h["background"]["events"]["service_restarted"] == 2
    assert h["speech"]["errors"]["recognizer_error: busy"] == 1
    tips = " ".join(h["tips"])
    assert "headset mic is noisy" in tips and "tailscale ping" in tips and "Unrestricted" in tips


def test_server_events_and_slowest_moments():
    mem = Memory(":memory:")
    for ms in (2600, 2900, 2400):
        diagnostics.server_event(mem, "speech", "stt", engine="local", ms=ms, confidence=0.8)
    diagnostics.server_event(mem, "server", "slow_reply", with_load=True, first_word_ms=6200, total_ms=9000)
    insights.record_voice_timing(mem, {"engine": "local", "wait_ms": 1000, "network_ms": 300, "stt_ms": 2900,
                                       "to_thinking_ms": 200, "first_word_ms": 6200, "total_ms": 10600})
    h = diagnostics.health(mem, since())
    assert h["speech"]["engines"]["local"]["ms"] == 2600
    assert any("Groq" in t for t in h["tips"])
    slow = h["slowest"][0]
    assert slow["total_ms"] == 10600 and slow["steps"]["speech_to_text"] == 2900 and slow["confidence"] == 0.8
    assert h["server"]["slow_replies"] == 1
    assert "load" in diagnostics.server_load() or "mem_free_mb" in diagnostics.server_load()


def test_setup_test_rates_and_explains():
    mem = Memory(":memory:")
    good = diagnostics.rate_test(mem, {"noise_db": 35, "speech_db": 62, "snr_db": 27, "clipped": False, "rtt_ms": 60,
                                       "stt_ms": 480, "engine": "groq", "heard_ok": True, "tts_ms": 400,
                                       "battery_ok": True})
    assert good["passed"] == good["total"] and good["summary"] == "All good."
    bad = diagnostics.rate_test(mem, {"noise_db": 61, "speech_db": 40, "snr_db": 4, "mic": "headset",
                                      "rtt_ms": 410, "stt_ms": 2700, "engine": "local", "heard_ok": False})
    fixes = " ".join(c["fix"] for c in bad["checks"] if not c["ok"])
    assert bad["passed"] == 0 and "headset mic" in fixes and "BUDDY_GROQ_KEY" in fixes and "tailscale" in fixes
    assert mem.db.execute("SELECT COUNT(*) FROM diag_events WHERE category = 'test'").fetchone()[0] == 2


def test_export_and_switching_off(monkeypatch):
    mem = Memory(":memory:")
    diagnostics.record_batch(mem, [{"at": ms_ago(5), "category": "network", "event": "ping", "data": {"rtt_ms": 80}}])
    text, kind = diagnostics.export(mem, since())
    assert kind == "text/csv" and text.splitlines()[0].startswith("at,source,category,event") and "ping" in text
    body, kind = diagnostics.export(mem, since(), "json")
    assert json.loads(body)["events"][0]["event"] == "ping"
    monkeypatch.setenv("BUDDY_DIAGNOSTICS", "0")
    assert diagnostics.record_batch(mem, [{"at": ms_ago(1), "category": "mic", "event": "x"}]) == 0
    assert insights.report(mem, days=1)["experience"] == {"on": False}


def test_endpoints_and_websocket():
    from buddy import server
    with TestClient(server.app) as c:
        with c.websocket_connect("/ws") as ws:
            ws.send_json({"type": "diag_ping", "t": 123})
            assert ws.receive_json() == {"type": "diag_pong", "t": 123}
            ws.send_json({"type": "diag", "events": [{"at": ms_ago(1), "category": "mic", "event": "bt_disconnected"}]})
            ws.send_json({"type": "diag_ping", "t": 124})
            ws.receive_json()
        h = c.get("/api/diagnostics").json()
        assert h["microphone"]["events"].get("bt_disconnected") == 1
        assert c.post("/api/diagnostics/test", json={"rtt_ms": 50}).json()["checks"][0]["ok"]
        r = c.get("/api/diagnostics/export", params={"format": "csv"})
        assert r.headers["content-type"].startswith("text/csv") and "bt_disconnected" in r.text
