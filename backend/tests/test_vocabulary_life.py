"""The speech vocabulary grows from Buddy's whole brain: people, places, plans, chat contacts, transit lines."""

import json
from datetime import datetime, timezone

from buddy import vocabulary
from buddy.memory import Memory


def test_names_from_everything_buddy_knows():
    m = Memory(":memory:")
    from buddy.brain import Brain
    from buddy.connect import Connect
    from buddy.connectors.hub import ConnectorHub
    from buddy.maps import Maps
    from buddy.transit import Transit
    Brain(m, embed=lambda t: None).note("Priyanka", "person", "colleague")
    Maps(m).name_place("Barranco studio", -12.14, -77.02)
    Connect(m).save_plan({"kind": "hotel", "date": "2026-12-10", "title": "Hilton Miraflores"}, "an email")
    ConnectorHub(m)
    now = datetime.now(timezone.utc).isoformat()
    m.db.execute("INSERT INTO records (source, external_id, kind, title, body, start_at, data, synced_at) VALUES "
                 "('notifications', 'x', 'notification', 'WhatsApp: Anjali Menon', 'hi', ?, ?, ?)",
                 (now, json.dumps({"app": "com.whatsapp", "can_reply": True}), now))
    Transit(m, env={}).watch("Hudson-Bergen Light Rail", days="daily")
    found = vocabulary.words(m)
    for w in ("Priyanka", "Barranco Studio", "Hilton", "Miraflores", "Anjali Menon", "Hudson-Bergen Light Rail"):
        assert w in found, w
    assert "Names:" in vocabulary.whisper_prompt(m)
