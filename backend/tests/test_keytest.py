

def test_the_inventory_says_what_is_set_and_what_is_missing_without_showing_a_key():
    from buddy import gateway, keytest
    env = {"ANTHROPIC_API_KEY": "sk-secret-value", "BUDDY_PLIVO_AUTH_ID": "id-only"}
    text = "\n".join(keytest.inventory(env))
    assert "sk-secret-value" not in text and "id-only" not in text  # values are never printed
    assert "set            Claude (Anthropic)" in text
    assert "MISSING BUDDY_PLIVO_AUTH_TOKEN" in text  # a service with several keys needs all of them
    assert "MISSING BUDDY_TOKEN  (required)" in text and "no key needed  Open-Meteo (weather)" in text
    assert "BUDDY_SEC_CONTACT is not a key" in text
    for entry in gateway.CATALOG.values():  # every catalogue service is listed
        assert entry["title"] in text
    assert text.count("MISSING BUDDY_TIINGO_KEY") == 1  # seen live: Tiingo listed twice, and counted twice as missing
    assert "Your mailbox" in text and "Duffel" in text   # keys outside the catalogue are still listed


# --- every key gets a test, and the count at the end -----------------------------------------------------------------
import gzip  # noqa: E402
import io  # noqa: E402
import json  # noqa: E402
import urllib.error  # noqa: E402

import pytest  # noqa: E402


def answers(table):
    """A fake outside world: the first address fragment that matches decides the answer (a dict, or a status)."""
    seen = []

    def fetch(url, data=None, headers=None, *rest):
        seen.append((url, headers or {}))
        for part, said in table.items():
            if part in url:
                if isinstance(said, int):
                    raise urllib.error.HTTPError(url, said, "no", {}, io.BytesIO(b'{"message": "Invalid API key"}'))
                return json.dumps(said).encode()
        raise urllib.error.HTTPError(url, 404, "no", {}, io.BytesIO(b""))
    fetch.seen = seen
    return fetch


def test_a_service_that_takes_its_key_in_the_address_is_tested_and_the_key_is_not_kept():
    from buddy import gateway
    env = {"BUDDY_ALPHAVANTAGE_KEY": "AV-SECRET-12345", "BUDDY_AIRLABS_KEY": "AIR-SECRET-12345",
           "BUDDY_KEEPA_KEY": "KEEPA-SECRET-12345", "BUDDY_MARKETAUX_KEY": "MX-SECRET-12345"}
    fetch = answers({"alphavantage": {"Global Quote": {"01. symbol": "IBM"}}, "airlabs": {"response": {"pong": True}},
                     "keepa": {"tokensLeft": 20}, "marketaux": {"data": []}})
    for name in ("alphavantage", "airlabs", "keepa", "marketaux"):
        assert gateway.health(name, None, env, fetch) == (True, "ok")
    assert any("apikey=AV-SECRET-12345" in u for u, _ in fetch.seen)              # the key went where the service reads it
    assert "SECRET" not in gateway._redact("/v1/news/all?limit=1&api_token=MX-SECRET-12345")   # and is not written down
    # A wrong key that is answered with "200 OK": the refusal is in the answer, and is read there.
    limited = answers({"alphavantage": {"Information": "25 requests per day"}, "airlabs": {"error": {"message": "Unknown api_key"}}})
    ok, said = gateway.health("alphavantage", None, env, limited)
    assert not ok and "not with what a working key gets" in said and "25 requests per day" in said
    ok, said = gateway.health("airlabs", None, env, limited)
    assert not ok and "refused the key" in said and "Unknown api_key" in said
    assert all("SECRET" not in gateway.health(n, None, env, limited)[1] for n in ("alphavantage", "airlabs"))


def test_every_service_with_a_key_is_tested_or_says_why_not():
    from buddy import gateway, keytest
    env = {"BUDDY_NEWSCATCHER_KEY": "NC-SECRET-12345", "BUDDY_TIINGO_KEY": "TI-SECRET-12345",
           "BUDDY_PLAID_CLIENT_ID": "plaid-id-12345"}
    fetch = answers({"newscatcher": 401, "tiingo": {"message": "You successfully sent a request"}})
    rows = keytest.services(env, None, fetch)
    text = "\n".join(rows)
    assert "  OK    Tiingo (price cross-check)" in text
    fail = next(r for r in rows if "NewsCatcher" in r)
    assert fail.startswith("  FAIL  ") and "HTTP 401" in fail and "Invalid API key" in fail
    assert "fix: the key is wrong or expired: make a new one and save it with set.sh" in fail
    assert "  --    Perigon (news search and fact check)" in text and "no key (BUDDY_PERIGON_KEY)" in text
    assert "Plaid" not in text                                  # set and not tested: said once, with why, further down
    assert "  ??    Plaid (banks)" in "\n".join(keytest.extras(env, connect=lambda a: None))
    assert "  --    Plaid (banks)" in "\n".join(keytest.services({}, None, fetch))   # without a key it is still listed
    assert "Claude" not in text and "Serper (Google search" not in text              # those are tested directly, above
    assert "SECRET-12345" not in text and "plaid-id-12345" not in text               # names of settings, never values
    keyed = [n for n, e in gateway.CATALOG.items() if keytest._keys_of(e.get("auth") or {})
             and not set(keytest._keys_of(e["auth"])) <= keytest._tested_directly()]
    untested = sorted(n for n in keyed if not gateway.CATALOG[n].get("health"))
    assert untested == ["plaid"]                                                     # every other keyed service has a test


def test_the_report_counts_what_works_and_never_shows_a_key():
    from buddy import keytest
    env = {"BUDDY_TIINGO_KEY": "TI-SECRET-12345", "BUDDY_NEWSDATA_KEY": "ND-SECRET-12345",
           "BUDDY_EMAIL_ADDRESS": "owner@gmail.com", "BUDDY_EMAIL_APP_PASSWORD": "mail-app-password",
           "BUDDY_AEROAPI_KEY": "aero-secret-123"}

    def fetch(url, data=None, headers=None, *rest):
        if "tiingo" in url:
            return b'{"message": "ok"}'
        # a provider that quotes the key back in its refusal
        raise urllib.error.HTTPError(url, 401, "no", {}, io.BytesIO(b'{"message": "bad key ND-SECRET-12345"}'))

    class Box:
        def logout(self):
            pass
    rows = keytest.report(env, None, fetch, connect=lambda account: Box())
    text = "\n".join(rows)
    assert "SECRET-12345" not in text and "mail-app-password" not in text and "aero-secret" not in text
    assert "bad key [hidden]" in text                                                 # hidden even when quoted back
    assert "  OK    Mailbox 1: ow...@gmail.com" in text
    assert "  ??    FlightAware" in text and "every request is charged" in text      # never tested with money
    last = [r for r in rows if "working" in r][0]
    assert last.startswith("2 working, 1 NOT working, ") and "1 set but not tested" in last
    assert "  --    claude      no key (ANTHROPIC_API_KEY): required" in text       # the one key Buddy cannot do without
    marked = sum(1 for r in rows if r[2:6].strip() == "--")
    assert f"{marked} with no key" in last and "(free models are switched off" in text   # a note, not a key
    assert "For each FAIL, the line under it says what to do" in text

    def refused(account):
        raise OSError(b"[AUTHENTICATIONFAILED] Invalid credentials (Failure)")
    bad = "\n".join(keytest.extras(env, connect=refused))
    assert "  FAIL  Mailbox 1: ow...@gmail.com" in bad and "AUTHENTICATIONFAILED" in bad and "app password" in bad


def test_an_answer_sent_zipped_is_read(monkeypatch):
    from buddy import gateway

    class Reply:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self, n):
            return gzip.compress(b'{"tokensLeft": 20}')
    monkeypatch.setattr(gateway.urllib.request, "urlopen", lambda req, timeout=30: Reply())
    assert gateway._transport("https://api.keepa.example/token") == b'{"tokensLeft": 20}'


def test_the_server_gives_the_report_as_text_to_the_owner_only(monkeypatch):
    from fastapi.testclient import TestClient

    from buddy import keytest, server
    monkeypatch.setattr(server, "authorized", lambda t: t == "s3cret")
    asked_as = []

    def report(env, memory=None):
        asked_as.append(usage.current_turn.get())
        return ["  OK    Tiingo", "", "1 working, 0 NOT working."]
    from buddy import usage
    monkeypatch.setattr(keytest, "report", report)
    with TestClient(server.app) as client:
        assert client.get("/admin/keys").status_code == 401
        got = client.get("/admin/keys", headers={"Authorization": "Bearer s3cret"})
        assert got.status_code == 200 and got.text == "  OK    Tiingo\n\n1 working, 0 NOT working.\n"
    assert asked_as == ["owner:key test"]  # the owner's own request: the pause on background work does not stop it


# --- what the first run on the owner's server showed ----------------------------------------------------------------
def test_a_request_the_budget_stopped_is_not_called_a_bad_key(tmp_path, monkeypatch):
    """Seen live: Serper, Groq, Duffel and SerpApi all said FAIL, "can't reach it". Buddy's own budget pause had
    refused the requests; the keys were never tried."""
    from buddy import budgets, keytest
    from buddy.memory import Memory
    memory = Memory(str(tmp_path / "b.db"))
    env = {"BUDDY_SERPER_KEY": "serper-secret-1", "BUDDY_DUFFEL_TOKEN": "duffel-secret-1", "BUDDY_SERPAPI_KEY": "serpapi-secret-1"}

    def stopped(req, timeout=25, limit=20000):
        return 0, "BudgetStop: google.serper.dev: paid services are stopped, this month's $50 budget is reached."
    monkeypatch.setattr(keytest, "_send", stopped)

    def fetch(url, data=None, headers=None, *rest):
        raise budgets.BudgetStop("serpapi.com: paid services are stopped, this month's $50 budget is reached.")
    memory.set(budgets.HOLD_KEY, budgets.local_now().strftime("%Y-%m"))
    rows = keytest.report(env, memory, fetch, connect=lambda a: None)
    text = "\n".join(rows)
    for name in ("serper", "SerpApi (Google Trends)", "Duffel (flight fares)"):
        held = next(r for r in rows if name in r)
        assert held.startswith("  HOLD  ") and "the key was not tried" in held, held
    assert "FAIL" not in text and "can't reach it" not in text
    assert "0 NOT working" in text and "3 stopped by Buddy's own budget." in text
    note = next(r for r in rows if r.startswith("NOTE  "))
    assert "Background paid work is paused" in note and '"approve the pace"' in note and "Insights" in note
    assert "has no answer yet" in note
    memory.set(budgets.SAVER_KEY, budgets.local_now().strftime("%Y-%m"))
    assert "because you chose saver" in budgets.pause_said(memory)
    memory.set(budgets.HOLD_KEY, "")
    assert budgets.pause_said(memory) == "" and not [r for r in keytest.report({}, memory, fetch, connect=lambda a: None)
                                                      if r.startswith("NOTE")]


def test_the_connections_test_button_is_the_owners_own_request(monkeypatch):
    from fastapi.testclient import TestClient

    from buddy import budgets, gateway, server, usage
    monkeypatch.setattr(server, "authorized", lambda t: t == "s3cret")
    seen = []

    def health(name, memory=None, *rest):
        seen.append(usage.current_turn.get())
        if name == "serpapi":
            raise budgets.BudgetStop("serpapi.com: paid services are stopped, this month's $50 budget is reached.")
        return True, "ok"
    monkeypatch.setattr(gateway, "health", health)
    with TestClient(server.app) as client:
        ok = {"Authorization": "Bearer s3cret"}
        assert client.post("/admin/connections/test", json={"name": "serper"}, headers=ok).json()["ok"] is True
        got = client.post("/admin/connections/test", json={"name": "serpapi"}, headers=ok).json()
    assert seen == ["owner:connection test"] * 2
    assert got["ok"] is False and "Buddy's own budget stopped this request" in got["said"]


def test_free_models_are_tested_as_buddy_uses_them(tmp_path, monkeypatch):
    """Seen live: Groq (kept for speech only) and GitHub Models (not used any more) were sent a chat and marked
    FAIL, and Gemini timed out with nothing to say why."""
    from buddy import keytest
    from buddy.memory import Memory
    memory = Memory(str(tmp_path / "b.db"))
    memory.set("free:model:gemini", json.dumps({"model": "gemini-next-flash", "replaced": "gemini-flash-latest"}))
    for stop in ("error: TimeoutError", "error: TimeoutError", "at its free limit", "error: PrivacyBlocked"):
        memory.db.execute(
            "INSERT INTO model_calls (at, provider, model, purpose, input_tokens, output_tokens, cost_usd, seconds, ok, "
            "stop, request) VALUES (datetime('now'), 'gemini', 'm', 'chat', 0, 0, 0, 0.1, 0, ?, '')", (stop,))
    memory.db.execute(
        "INSERT INTO model_calls (at, provider, model, purpose, input_tokens, output_tokens, cost_usd, seconds, ok, "
        "stop, request) VALUES (datetime('now'), 'gemini', 'm', 'chat', 0, 0, 0, 0.1, 1, 'free', '')")
    memory.db.commit()
    env = {"BUDDY_FREE_MODELS": "1", "BUDDY_GROQ_KEY": "groq-secret-1", "BUDDY_GITHUB_MODELS_KEY": "github-secret-1",
           "BUDDY_GEMINI_KEY": "gemini-secret-1"}
    asked = []

    def send(req, timeout=25, limit=20000):
        asked.append((req.get_method(), req.full_url, req.data))
        if req.full_url.endswith("/models"):
            return 200, '{"data": []}'
        return 0, "TimeoutError: The read operation timed out"
    monkeypatch.setattr(keytest, "_send", send)
    rows = keytest.run(env, memory)
    text = "\n".join(rows)
    groq = next(r for r in rows if " groq " in r)
    assert groq.startswith("  OK    groq") and "speech to text only" in groq
    assert not [a for a in asked if "groq.com" in a[1] and a[0] == "POST"]          # no text is sent to Groq
    assert not [a for a in asked if "github" in a[1]] and "(github: a key is set, but Buddy does not use" in text
    chat = next(a for a in asked if "generativelanguage" in a[1] and a[0] == "POST")
    assert b"gemini-next-flash" in chat[2]                                         # the model Buddy switched to
    gemini = next(r for r in rows if " gemini " in r)
    assert gemini.startswith("  FAIL  gemini") and "no answer in 25 seconds" in gemini and "can't reach" not in gemini
    assert "the key is accepted (the provider's model list came back)" in text
    assert "Buddy's own log, last 30 days: 3 of 5 requests failed. Commonest: TimeoutError (2); at its free limit (1)" in text
    assert "secret-1" not in "\n".join(keytest._hidden(rows, env))
