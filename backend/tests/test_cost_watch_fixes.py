"""Cost watch's wasted calls (issue #39): services that fail again and again rest instead of being asked on every
pass (Reddit, Serper, GDELT); background card lookups never fall to paid Claude; one OpenRouter free model failing
moves on to the next free one instead of handing the work to paid Claude."""

import asyncio
import io
import json
import time
import urllib.error

import pytest

from buddy import bip_sources, gateway, signals
from buddy import benefits as bmod
from buddy import free_models as fm
from buddy.memory import Memory


class Net:
    def __init__(self, answer):
        self.answer, self.calls = answer, []

    def __call__(self, url, data=None, headers=None):
        self.calls.append(url)
        got = self.answer(url) if callable(self.answer) else self.answer
        if isinstance(got, BaseException):
            raise got
        return got


def _http(url, code):
    return urllib.error.HTTPError(url, code, "x", {}, io.BytesIO(b'{"message": "no"}'))


# --- (2)(3)(4) a service that keeps failing rests ------------------------------------------------------------

def _api_calls(m, provider):
    return m.db.execute("SELECT COUNT(*) AS n FROM api_calls WHERE provider = ?", (provider,)).fetchone()["n"]


def test_serper_failing_every_call_stops_being_called(tmp_path):
    m = Memory(tmp_path / "b.db")
    url = "https://google.serper.dev/search"
    net = Net(lambda u: _http(u, 403))
    for _ in range(gateway.REST_AFTER):
        with pytest.raises(gateway.ConnectError):
            gateway.call(m, "serper", "/search", json_body={"q": "x"}, fetch=net, env={"BUDDY_SERPER_KEY": "k"})
    assert len(net.calls) == gateway.REST_AFTER
    for _ in range(20):  # the next passes: refused here, nothing sent, nothing logged as a failed call
        with pytest.raises(gateway.Resting, match="resting"):
            gateway.call(m, "serper", "/search", json_body={"q": "x"}, fetch=net, env={"BUDDY_SERPER_KEY": "k"})
    assert len(net.calls) == gateway.REST_AFTER and net.calls[0] == url
    assert _api_calls(m, "serper") == gateway.REST_AFTER
    assert 0 < gateway.resting_for(m, "serper") <= gateway.REST_FIRST
    assert gateway.resting_for(Memory(tmp_path / "b.db"), "serper") > 0  # remembered across restarts


def test_reddit_failing_every_call_stops_being_called(tmp_path):
    m = Memory(tmp_path / "b.db")
    net = Net(urllib.error.URLError("blocked"))
    for _ in range(30):
        with pytest.raises(Exception):
            signals.read(m, "stocks", fetch=net, token="t")
    assert len(net.calls) == gateway.REST_AFTER  # not 30


def test_gdelt_failing_stops_then_tries_again_after_its_rest(tmp_path, monkeypatch):
    m = Memory(tmp_path / "b.db")
    state = {"down": True}
    net = Net(lambda u: _http(u, 429) if state["down"] else b'{"articles": []}')
    for _ in range(12):
        with pytest.raises(gateway.ConnectError):
            bip_sources.gdelt(m, "solid state battery", fetch=net)
    assert len(net.calls) == gateway.REST_AFTER
    state["down"] = False
    later = time.time() + gateway.REST_FIRST + 1
    monkeypatch.setattr(gateway.time, "time", lambda: later)
    assert bip_sources.gdelt(m, "solid state battery", fetch=net) == []  # rested: asked again, and it works
    assert gateway.resting_for(m, "gdelt") == 0 and gateway._rest_state(m, "gdelt") == {}


def test_rest_grows_each_time_it_trips_and_one_success_clears_it(tmp_path, monkeypatch):
    m = Memory(tmp_path / "b.db")
    now = [1_000_000.0]
    monkeypatch.setattr(gateway.time, "time", lambda: now[0])
    for trip in (1, 2, 3):
        for _ in range(gateway.REST_AFTER):
            gateway._failed(m, "gdelt")
        assert gateway.resting_for(m, "gdelt") == min(gateway.REST_FIRST * 2 ** (trip - 1), gateway.REST_MAX)
        now[0] += gateway.REST_MAX + 1
    gateway._worked(m, "gdelt")
    for _ in range(gateway.REST_AFTER):
        gateway._failed(m, "gdelt")
    assert gateway.resting_for(m, "gdelt") == gateway.REST_FIRST  # back to the shortest rest


def test_a_few_failures_and_not_found_do_not_rest_a_service(tmp_path):
    m = Memory(tmp_path / "b.db")
    for _ in range(gateway.REST_AFTER - 1):
        gateway._failed(m, "reddit")
    gateway._worked(m, "reddit")  # a success in between: the count starts over
    for _ in range(gateway.REST_AFTER - 1):
        gateway._failed(m, "reddit")
    for _ in range(10):
        gateway._failed(m, "reddit", 404)  # one missing page says nothing about the service
    assert gateway.resting_for(m, "reddit") == 0


def test_health_check_is_let_through_while_resting_and_clears_it(tmp_path):
    m = Memory(tmp_path / "b.db")
    for _ in range(gateway.REST_AFTER):
        gateway._failed(m, "serper")
    assert gateway.resting_for(m, "serper") > 0
    net = Net(b'{"organic": []}')
    assert gateway.health("serper", m, env={"BUDDY_SERPER_KEY": "k"}, fetch=net) == (True, "ok")
    assert gateway.resting_for(m, "serper") == 0 and len(net.calls) == 1


def test_calls_without_memory_rest_too():
    net = Net(lambda u: _http(u, 500))
    for _ in range(15):
        with pytest.raises(gateway.ConnectError):
            gateway.call(None, "serper", "/search", data=b"{}", fetch=net, env={"BUDDY_SERPER_KEY": "k"})
    assert len(net.calls) == gateway.REST_AFTER  # a POST is never re-sent, so one request per failed call


# --- (1) background card lookups never use paid Claude --------------------------------------------------------

def _no_free(monkeypatch):
    async def free_fails(memory, card):
        return None
    monkeypatch.setattr(bmod, "_search_issuer_free", free_fails)
    paid = []

    async def claude(memory, card):
        paid.append(card)
        return {"card_name": card}
    monkeypatch.setattr(bmod, "_search_issuer", claude)
    return paid


def test_background_card_lookup_skips_instead_of_paying_claude(monkeypatch):
    paid = _no_free(monkeypatch)
    with bmod.free_only():
        assert asyncio.run(bmod._search_free_first(Memory(":memory:"), "Chase Sapphire Reserve")) is None
    assert paid == []
    # asked by the user: Claude's own search is still the last resort
    assert asyncio.run(bmod._search_free_first(Memory(":memory:"), "Chase Sapphire Reserve")) == {
        "card_name": "Chase Sapphire Reserve"}
    assert paid == ["Chase Sapphire Reserve"]


def test_recheck_and_new_card_offers_never_reach_claude(monkeypatch):
    paid = _no_free(monkeypatch)
    m = Memory(":memory:")
    b = bmod.Benefits(m)
    old = "2020-01-01T00:00:00+00:00"
    m.db.execute("INSERT INTO fin_card_catalog (card_key, card_name, data, sources, checked_at) VALUES (?, ?, ?, '[]', ?)",
                 (bmod._key("Northwind Voyager Card"), "Northwind Voyager Card",
                  json.dumps({"benefits": [], "perks": [], "earn_rates": [], "version": 1}), old))
    assert asyncio.run(b.recheck()) == []
    monkeypatch.setattr(bmod.Benefits, "known_cards", lambda self: ["Contoso Gold Card"])
    monkeypatch.setattr(bmod.Benefits, "identify_all", lambda self: [])
    monkeypatch.setattr(bmod.Benefits, "connected_cards", lambda self: [])
    assert asyncio.run(b.offer_new_cards()) == []
    assert paid == []


# --- (5) OpenRouter: one free model failing moves on to the next free one ------------------------------------

def _openrouter(monkeypatch, behaviour, env=None):
    from buddy import openrouter_catalog
    monkeypatch.setattr(openrouter_catalog, "models_for", lambda memory, kind: ["a:free", "b:free", "c:free", "d:free"])
    marked = []
    monkeypatch.setattr(openrouter_catalog, "mark_bad", lambda memory, model: marked.append(model))
    asked = []

    def http(url, body, headers, timeout):
        asked.append(body["model"])
        got = behaviour(body["model"], url)
        if isinstance(got, BaseException):
            raise got
        return {"choices": [{"message": {"content": f"answer from {body['model']}"}}]}
    f = fm.FreeModels(Memory(":memory:"), env=env or {"BUDDY_FREE_MODELS": "1", "BUDDY_OPENROUTER_KEY": "o"},
                      http=http)
    paid = []

    async def claude(system, prompt, kind=""):
        paid.append(prompt)
        return "answer from claude"
    f._claude = claude
    return f, asked, marked, paid


def test_a_busy_or_broken_openrouter_model_moves_on_to_the_next_free_one(monkeypatch):
    def behaviour(model, url):
        return {"a:free": fm.RateLimited(20), "b:free": _http(url, 502)}.get(model)
    f, asked, marked, paid = _openrouter(monkeypatch, behaviour)
    got = asyncio.run(f.run("summarize", "a public article about tides"))
    assert got["free"] and got["answer"] == "answer from c:free"
    assert asked == ["a:free", "b:free", "c:free"] and paid == [] and marked == []


def test_gone_openrouter_models_are_set_aside_and_the_next_answers(monkeypatch):
    f, asked, marked, paid = _openrouter(monkeypatch, lambda m, u: _http(u, 404) if m == "a:free" else None)
    assert asyncio.run(f.run("explain", "how do tides work"))["answer"] == "answer from b:free"
    assert marked == ["a:free"] and paid == []


def test_openrouter_tries_a_few_models_not_all_and_then_reports_the_failure(monkeypatch):
    f, asked, marked, paid = _openrouter(monkeypatch, lambda m, u: _http(u, 500))
    with pytest.raises(fm.NeedsClaude):
        asyncio.run(f.run("explain", "how do tides work", claude_fallback=False))
    assert asked == ["a:free", "b:free", "c:free"]  # OPENROUTER_TRIES, not every model


def test_free_models_at_their_limit_dont_use_up_the_tries(monkeypatch):
    """Two free models answer 'at my limit' at once: the others still get their go before paid Claude."""
    env = {"BUDDY_FREE_MODELS": "1", "BUDDY_GEMINI_KEY": "g", "BUDDY_MISTRAL_KEY": "m", "BUDDY_CEREBRAS_KEY": "c",
           "BUDDY_NVIDIA_KEY": "n", "BUDDY_OPENROUTER_KEY": "o"}
    calls = []

    def http(url, body, headers, timeout):
        name = next(n for n, p in fm.PROVIDERS.items() if url.startswith(p["base"]) and n != "deepseek")
        calls.append(name)
        if name in ("gemini", "mistral"):
            raise fm.RateLimited(30)
        if name in ("cerebras", "nvidia"):
            raise OSError("down")
        return {"choices": [{"message": {"content": "answer from openrouter"}}]}
    from buddy import openrouter_catalog
    monkeypatch.setattr(openrouter_catalog, "models_for", lambda memory, kind: ["a:free"])
    f = fm.FreeModels(Memory(":memory:"), env=env, http=http)

    async def claude(system, prompt, kind=""):
        raise AssertionError("paid Claude was used")
    f._claude = claude
    got = asyncio.run(f.run("summarize", "a public article about tides"))
    assert got["free"] and calls == ["gemini", "mistral", "cerebras", "nvidia", "openrouter"]


def test_a_refused_openrouter_key_is_not_tried_on_every_model(monkeypatch):
    f, asked, marked, paid = _openrouter(monkeypatch, lambda m, u: _http(u, 401))
    with pytest.raises(fm.NeedsClaude):
        asyncio.run(f.run("explain", "how do tides work", claude_fallback=False))
    assert asked == ["a:free"] and f.resting("openrouter")  # rests a day, as before
