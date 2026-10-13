from fastapi.testclient import TestClient

from buddy import server, tools


def test_phone_timezone_is_adopted_and_remembered():
    with TestClient(server.app) as c, c.websocket_connect("/ws") as ws:
        ws.send_json({"type": "hello", "timezone": "America/Sao_Paulo"})
        ws.send_json({"type": "hello", "timezone": "Not/AZone"})  # ignored
        ws.send_json({"type": "interrupt"})  # round-trip so the hellos are processed
    assert str(tools.user_tz()) == "America/Sao_Paulo"
    assert server.memory.get("timezone") == "America/Sao_Paulo"
