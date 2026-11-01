"""The brain: meaning + keyword search over documents and facts, and the people and things in them."""

import asyncio
import re

import numpy as np
import pytest
from fastapi.testclient import TestClient

from buddy import brain as brain_mod
from buddy import library
from buddy.memory import Memory
from tests.test_library import LEASE, fake_understand

# A stand-in for the embedding model: words map to concepts, so "car insurance" lands near "Geico auto policy".
CONCEPTS = {"car": 0, "auto": 0, "vehicle": 0, "insurance": 1, "policy": 1, "geico": 1, "insurer": 1,
            "cat": 2, "pets": 2, "pet": 2, "rent": 3, "lease": 3, "landlord": 3, "passport": 4, "travel": 4}


def fake_embed(texts):
    out = np.zeros((len(texts), 8), dtype=np.float32)
    for i, t in enumerate(texts):
        for w in re.findall(r"[a-z]+", t.lower()):
            if w in CONCEPTS:
                out[i, CONCEPTS[w]] += 1
        out[i, 7] += 0.01
    return out / np.linalg.norm(out, axis=1, keepdims=True)


@pytest.fixture
def mem(tmp_path, monkeypatch):
    monkeypatch.setattr(library, "LIB_DIR", tmp_path / "library")
    return Memory(":memory:")


def test_meaning_search_finds_what_keywords_miss(mem):
    lib = library.Library(mem, understand=fake_understand(facts=[]))
    doc = b"GEICO. Your auto policy GA-4471 covers collision up to $50,000. Renews 2026-12-01."
    item = lib.add("geico.txt", doc)
    asyncio.run(lib.ingest(item["id"], doc))
    mem.remember("Amma's cat is called Minnu")
    brain = brain_mod.Brain(mem, embed=fake_embed)
    assert brain.index_pending() >= 2
    assert brain._by_words("car insurance", {"chunk"}, 5) == []  # no shared words...
    found = brain.recall("car insurance")
    assert found and found[0]["source"] == "document" and found[0]["file"] == "geico.txt"  # ...found by meaning
    assert brain.recall("GA-4471")[0]["file"] == "geico.txt"  # and exact numbers by words
    assert any(f["source"] == "fact" and "Minnu" in f["text"] for f in brain.recall("pets"))
    mem.forget("Minnu")
    brain.index_pending()  # vectors of deleted facts are dropped
    assert not any("Minnu" in f.get("text", "") for f in brain.recall("pets"))


def test_people_and_things_come_from_documents_and_conversation(mem):
    lib = library.Library(mem, understand=fake_understand())
    item = lib.add("lease.txt", LEASE)
    asyncio.run(lib.ingest(item["id"], LEASE))
    brain = brain_mod.Brain(mem, embed=fake_embed)
    landlord = brain.person("landlord")
    assert landlord["name"] == "Oak Street Homes" and landlord["relation"] == "landlord"
    assert landlord["documents"][0]["file"] == "lease.txt"
    brain.note("Amma", "person", "mother", "lives in Kochi")
    brain.note("amma", "person", "", "birthday 14 March")  # same person: details added, nothing blanked
    amma = brain.person("Amma")
    assert amma["relation"] == "mother" and "Kochi" in amma["notes"] and "14 March" in amma["notes"]
    assert {p["name"] for p in brain.people()} == {"Oak Street Homes", "Amma"}
    lib.forget(item["id"])
    assert brain.person("landlord")["documents"] == []


def test_without_the_model_search_still_works_on_words(mem, monkeypatch):
    monkeypatch.setattr(brain_mod, "ENABLED", False)
    lib = library.Library(mem, understand=fake_understand())
    item = lib.add("lease.txt", LEASE)
    asyncio.run(lib.ingest(item["id"], LEASE))
    brain = brain_mod.Brain(mem)
    assert brain.index_pending() == 0 and brain.status()["meaning_search"] is False
    assert brain.recall("pets cat")[0]["file"] == "lease.txt"


def test_brain_tool_and_library_screen_endpoints(tmp_path, monkeypatch):
    from buddy import server
    monkeypatch.setattr(library, "LIB_DIR", tmp_path / "library")
    lib = library.Library(server.memory, understand=fake_understand())
    item = lib.add("lease2.txt", LEASE + b" Parking space 14.")
    asyncio.run(lib.ingest(item["id"], LEASE + b" Parking space 14."))
    out = asyncio.run(brain_mod.run_tool(brain_mod.Brain(server.memory), {"action": "recall", "query": "parking"}))
    assert out.startswith(brain_mod.UNTRUSTED) and "lease2.txt" in out
    with TestClient(server.app) as c:
        listing = c.get("/api/library").json()
        assert any(i["id"] == item["id"] for i in listing["items"])
        assert any(p["name"] == "Oak Street Homes" for p in listing["people"])
        assert c.get("/api/library", params={"q": "parking"}).json()["items"][0]["id"] == item["id"]
        assert "Parking space 14" in c.get(f"/api/library/{item['id']}").json()["text"]
        assert c.get(f"/api/library/{item['id']}/file").content.startswith(b"RESIDENTIAL LEASE")
        assert c.get("/api/people", params={"name": "landlord"}).json()["name"] == "Oak Street Homes"
        assert c.delete(f"/api/library/{item['id']}").json()["ok"]
        assert c.get(f"/api/library/{item['id']}").status_code == 404
