

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
    assert "  ??    Plaid" in text and "no test" in text                              # set, and nothing to test it with
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
    assert "  OK    Mailbox ow...@gmail.com" in text
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
    assert "  FAIL  Mailbox ow...@gmail.com" in bad and "AUTHENTICATIONFAILED" in bad and "app password" in bad


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
    monkeypatch.setattr(keytest, "report", lambda env, memory=None: ["  OK    Tiingo", "", "1 working, 0 NOT working."])
    with TestClient(server.app) as client:
        assert client.get("/admin/keys").status_code == 401
        got = client.get("/admin/keys", headers={"Authorization": "Bearer s3cret"})
        assert got.status_code == 200 and got.text == "  OK    Tiingo\n\n1 working, 0 NOT working.\n"
