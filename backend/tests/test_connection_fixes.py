"""Connections that failed for Buddy's own reasons (seen on the owner's Connections page, 25 November 2026)."""

import json

import pytest

from buddy import bip_sources, free_models, gateway, modelscout, openrouter_catalog
from buddy.memory import Memory


def test_a_source_that_keeps_refusing_is_left_alone_longer_each_time():
    """GDELT refused 271 of 342 requests in a month although Buddy waits 5.5 s between them."""
    now = [1000.0]
    clock = lambda: now[0]  # noqa: E731
    bip_sources._rest.clear()
    bip_sources._strikes.clear()
    rests = []
    for _ in range(5):
        bip_sources.rest("gdelt", clock=clock)
        rests.append(bip_sources._rest["gdelt"] - now[0])
    assert rests == [900, 3600, 14400, 57600, 86400]           # 15 min, 1 h, 4 h, 16 h, then a day at most
    bip_sources.recovered("gdelt")
    bip_sources.rest("gdelt", clock=clock)
    assert bip_sources._rest["gdelt"] - now[0] == 900          # it answered in between: short again
    bip_sources._rest.clear()
    bip_sources._strikes.clear()


def test_a_refused_request_rests_the_source(monkeypatch):
    bip_sources._rest.clear()
    bip_sources._strikes.clear()

    def refuse(*a, **k):
        raise gateway.ConnectError("sbir", "SBIR.gov said HTTP 403: Forbidden", 403)
    monkeypatch.setattr(gateway, "call_url", refuse)
    with pytest.raises(gateway.ConnectError):
        bip_sources._get(None, "sbir", "https://api.www.sbir.gov/x", fetch=lambda *a, **k: None)
    assert bip_sources.resting("sbir")
    with pytest.raises(gateway.ConnectError, match="leaving it alone"):  # not asked again while it rests
        bip_sources._get(None, "sbir", "https://api.www.sbir.gov/x", fetch=lambda *a, **k: None)
    bip_sources._rest.clear()
    bip_sources._strikes.clear()


def test_an_error_page_is_said_in_words():
    """Reddit answered a server with a web page; the Connections page showed its code."""
    said = gateway.reason_of(b"<body class=theme-beta><div><style>.theme-light,:root{--x:1}</style></div></body>")
    assert said == "a web page instead of data (the service may be blocking requests from servers)"
    assert gateway.reason_of(b'{"message": "Not enough credits"}') == "Not enough credits"


def test_a_service_buddy_no_longer_uses_is_not_shown_as_failing():
    m = Memory(":memory:")
    m.set("connect:last:github_models", json.dumps({"ok": False, "error": "error: ValueError", "at": "2026-11-25T10:00"}))
    row = next(r for r in gateway.status(m, env={"BUDDY_GITHUB_MODELS_KEY": "k"}) if r["name"] == "github_models")
    assert row["state"] == "unused" and "Not used any more" in row["used_for"]


def test_the_daily_model_check_skips_providers_not_used_for_text(monkeypatch):
    m = Memory(":memory:")
    free = free_models.FreeModels(m, env={"BUDDY_GITHUB_MODELS_KEY": "k", "BUDDY_GROQ_KEY": "g", "BUDDY_MISTRAL_KEY": "x"})
    looked = []
    monkeypatch.setattr(modelscout, "scout", lambda memory, f, provider, fetch=None: looked.append(provider) or
                        {"provider": provider})
    modelscout.run(m, free)
    assert looked == ["mistral"]


def test_a_free_model_that_answers_with_an_error_says_so_and_the_next_one_is_tried(monkeypatch):
    m = Memory(":memory:")
    asked = []

    def http(url, body, headers, timeout):
        asked.append(body["model"])
        if body["model"] == "first/model:free":
            return {"error": {"message": "Provider returned error", "code": 502}}
        return {"choices": [{"message": {"content": "ready"}}], "usage": {}}
    free = free_models.FreeModels(m, env={"BUDDY_OPENROUTER_KEY": "k"}, http=http)
    monkeypatch.setattr(openrouter_catalog, "models_for", lambda memory, kind: ["first/model:free", "second/model:free"])
    bad = []
    monkeypatch.setattr(openrouter_catalog, "mark_bad", lambda memory, model: bad.append(model))
    assert free._call("openrouter", "s", "p")[0] == "ready"
    assert asked == ["first/model:free", "second/model:free"] and bad == ["first/model:free"]
    with pytest.raises(ValueError, match="answered with an error: Provider returned error"):
        free._call_model("openrouter", "first/model:free", "s", "p", 10, 10)
    reason = free._broken("mistral", ValueError("empty answer"))
    assert reason.startswith("ValueError: empty answer")       # not just "ValueError"
