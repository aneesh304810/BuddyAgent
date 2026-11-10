"""Buddy Connect: every outside API Buddy uses, in one catalog, called one way.

The catalog (CATALOG) says for each service: where it lives (with backup hosts), how it signs in (a key in a
header, or a username and password exchanged for a token), which settings hold its secrets, what a call costs,
how often it may be called, whether it may ever see the owner's personal data, and a small health check.

call() does the rest the same way for every service: adds the key or token (minting and reusing tokens), tries
the backup host when one can't be reached, retries once on a server hiccup, keeps to the rate limit, refuses
personal data for services that may not see it, logs every call (time, cost, error) for Insights, remembers the
last result for the Connections status, and turns failures into ConnectError with the service's own reason.

Services are grouped by area (GROUPS: travel and transit, finance and markets, shopping, search, news and trends, weather,
AI models) for the app and for find. Adding an API later: one CATALOG entry with its group; then call(memory, "name", "/path", ...). Stage 1 moved NJ Transit, MTA,
PATH and Serper here; stage 2 markets, trends and news, flights and weather. The free AI models keep their own
failover and report each result; Claude and Plaid keep their own clients and are listed for the status.
"""

import base64
import json
import logging
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone

from .tools import ToolError

log = logging.getLogger("buddy.connect")
UA = "Buddy/1.0 (personal assistant; read-only)"

# privacy: "public" services only ever get general requests (search words, a line name); "personal" ones may see
# the owner's data (and only with personal=True does a call say it carries any).
CATALOG: dict[str, dict] = {
    "serper": {"group": "Search", 
        "title": "Serper (Google search, news, places)", "hosts": ["https://google.serper.dev"],
        "auth": {"type": "header", "header": "X-API-KEY", "secret": "BUDDY_SERPER_KEY"},
        "cost": 0.001, "per_minute": 60, "privacy": "public", "used_for": "web, news and places search",
        "health": {"path": "/search", "json": {"q": "weather", "num": 1}}, "moved": True},
    "njt_rail": {"group": "Travel & transit", 
        "title": "NJ Transit trains (RailData)",
        "hosts": ["https://raildata.njtransit.com/api/TrainData", "https://raildata.njt.gov/api/TrainData"],
        "auth": {"type": "token", "login": "/getToken", "fields": {"username": "BUDDY_NJT_USERNAME",
                                                                   "password": "BUDDY_NJT_PASSWORD"},
                 "hours": 23, "memory_key": "transit:njt_token:rail"},
        "cost": 0.0, "per_minute": 30, "privacy": "public", "used_for": "NJ Transit train service messages",
        "health": {"login": True}, "moved": True},
    "njt_bus": {"group": "Travel & transit", 
        "title": "NJ Transit buses and light rail (GTFS-BUS)", "hosts": ["https://pcsdata.njtransit.com/api/GTFSG2"],
        "auth": {"type": "token", "login": "/authenticateUser",
                 "fields": {"username": "BUDDY_NJT_USERNAME", "password": "BUDDY_NJT_PASSWORD"},
                 "hours": 23, "memory_key": "transit:njt_token:gtfs_bus"},
        "cost": 0.0, "per_minute": 30, "privacy": "public", "used_for": "bus and light rail alerts",
        "health": {"login": True}, "moved": True},
    "mta": {"group": "Travel & transit", 
        "title": "MTA (subway, buses, LIRR, Metro-North)", "hosts": ["https://api-endpoint.mta.info"],
        "auth": {"type": "none"}, "cost": 0.0, "per_minute": 30, "privacy": "public",
        "used_for": "New York transit alerts",
        "health": {"path": "/Dataservice/mtagtfsfeeds/camsys%2Fall-alerts.json"}, "moved": True},
    "path": {"group": "Travel & transit", 
        "title": "PATH (Port Authority)", "hosts": ["https://www.panynj.gov"], "auth": {"type": "none"},
        "cost": 0.0, "per_minute": 30, "privacy": "public", "used_for": "PATH next trains and incidents",
        "health": {"path": "/bin/portauthority/ridepath.json"}, "moved": True},
    # Markets, trends and news (stage 2): the modules pass their own headers; Buddy Connect sends, logs, explains.
    "alpaca": {"group": "Finance & markets", "title": "Alpaca (market data)", "hosts": ["https://data.alpaca.markets"],
               "auth": {"type": "header", "header": "APCA-API-KEY-ID", "secret": "BUDDY_ALPACA_KEY_ID"},
               "cost": 0.0, "per_minute": 180, "privacy": "public", "used_for": "prices, news and trade ideas",
               "health": {"path": "/v2/stocks/snapshots?symbols=SPY", "headers_from": {
                   "APCA-API-KEY-ID": "BUDDY_ALPACA_KEY_ID", "APCA-API-SECRET-KEY": "BUDDY_ALPACA_SECRET"}},
               "moved": True},
    "reddit": {"group": "News & trends", "title": "Reddit", "hosts": ["https://www.reddit.com", "https://oauth.reddit.com"],
               "auth": {"type": "none"}, "cost": 0.0, "per_minute": 60, "privacy": "public",
               "used_for": "what people say about stocks and topics", "moved": True, "no_backup": True},
    "stocktwits": {"group": "Finance & markets", "title": "StockTwits", "hosts": ["https://api.stocktwits.com"], "auth": {"type": "none"},
                   "cost": 0.0, "per_minute": 60, "privacy": "public", "used_for": "trader sentiment", "moved": True},
    "yahoo": {"group": "Finance & markets", "title": "Yahoo Finance", "hosts": ["https://query1.finance.yahoo.com"], "auth": {"type": "none"},
              "cost": 0.0, "per_minute": 60, "privacy": "public", "used_for": "India market prices", "moved": True},
    "stooq": {"group": "Finance & markets", "title": "Stooq", "hosts": ["https://stooq.com"], "auth": {"type": "none"}, "cost": 0.0,
              "per_minute": 60, "privacy": "public", "used_for": "backup price history", "moved": True},
    "coinbase": {"group": "Finance & markets", "title": "Coinbase (crypto prices)", "hosts": ["https://api.exchange.coinbase.com"],
                 "auth": {"type": "none"}, "cost": 0.0, "per_minute": 60, "privacy": "public",
                 "used_for": "crypto prices", "moved": True},
    "github": {"group": "News & trends", "title": "GitHub (trending projects)", "hosts": ["https://api.github.com"], "auth": {"type": "none"},
               "cost": 0.0, "per_minute": 30, "privacy": "public", "used_for": "new projects on watched topics",
               "moved": True},
    "hackernews": {"group": "News & trends", "title": "Hacker News", "hosts": ["https://hn.algolia.com"], "auth": {"type": "none"},
                   "cost": 0.0, "per_minute": 60, "privacy": "public", "used_for": "tech discussions", "moved": True},
    "arxiv": {"group": "News & trends", "title": "arXiv (papers)", "hosts": ["https://export.arxiv.org"], "auth": {"type": "none"},
              "cost": 0.0, "per_minute": 20, "privacy": "public", "used_for": "new research papers", "moved": True},
    "serpapi": {"group": "News & trends", "title": "SerpApi (Google Trends)", "hosts": ["https://serpapi.com"],
                "auth": {"type": "param", "secret": "BUDDY_SERPAPI_KEY"}, "cost": 0.01, "per_minute": 30,
                "privacy": "public", "used_for": "rising searches", "moved": True},
    "airlabs": {"group": "Travel & transit", "title": "AirLabs (flights)", "hosts": ["https://airlabs.co"],
                "auth": {"type": "param", "secret": "BUDDY_AIRLABS_KEY"}, "cost": 0.0, "per_minute": 30,
                "privacy": "public", "used_for": "flight status", "moved": True},
    "openmeteo": {"group": "Weather", "title": "Open-Meteo (weather)",
                  "hosts": ["https://api.open-meteo.com", "https://geocoding-api.open-meteo.com"],
                  "auth": {"type": "none"}, "cost": 0.0, "per_minute": 60, "privacy": "public",
                  "used_for": "forecasts", "moved": True, "no_backup": True,
                  "health": {"path": "/v1/forecast?latitude=40.7&longitude=-74.0&current=temperature_2m"}},
    # Shopping: Amazon prices without scraping Amazon (amazon.py).
    "serper_shopping": {"group": "Shopping", "title": "Google Shopping (via Serper)",
                        "hosts": ["https://google.serper.dev"],
                        "auth": {"type": "header", "header": "X-API-KEY", "secret": "BUDDY_SERPER_KEY"},
                        "cost": 0.001, "per_minute": 60, "privacy": "public",
                        "used_for": "Amazon and store prices for price watches",
                        "health": {"path": "/shopping", "json": {"q": "usb c cable", "num": 1}}, "moved": True},
    "keepa": {"group": "Shopping", "title": "Keepa (Amazon price history)", "hosts": ["https://api.keepa.com"],
              "auth": {"type": "param", "secret": "BUDDY_KEEPA_KEY"}, "cost": 0.0, "per_minute": 20,
              "privacy": "public", "used_for": "Amazon's exact price and 90-day average (optional, paid plan)",
              "moved": True},
    # Health (health_data.py): the phone's own data stays on this server; the public ones get only a drug,
    # topic or product name.
    "health_connect": {"group": "Health", "title": "Phone health data (Health Connect)", "auth": {"type": "phone"},
                       "privacy": "personal", "used_for": "your steps, sleep, heart rate and workouts, from the phone",
                       "reports": True},
    "openfda": {"group": "Health", "title": "openFDA (medicines)", "hosts": ["https://api.fda.gov"],
                "auth": {"type": "none"}, "cost": 0.0, "per_minute": 40, "privacy": "public",
                "used_for": "what a medicine is for, warnings, interactions, recalls",
                "health": {"path": "/drug/label.json?search=openfda.generic_name:ibuprofen&limit=1"}, "moved": True},
    "medlineplus": {"group": "Health", "title": "MedlinePlus (NIH health topics)",
                    "hosts": ["https://wsearch.nlm.nih.gov"], "auth": {"type": "none"}, "cost": 0.0,
                    "per_minute": 40, "privacy": "public", "used_for": "conditions and lab tests explained",
                    "health": {"path": "/ws/query?db=healthTopics&term=sleep&retmax=1", "raw": True},
                    "moved": True},
    "openfoodfacts": {"group": "Health", "title": "Open Food Facts (nutrition)",
                      "hosts": ["https://world.openfoodfacts.org"], "auth": {"type": "none"}, "cost": 0.0,
                      "per_minute": 30, "privacy": "public", "used_for": "nutrition and allergens of food products",
                      "health": {"path": "/api/v2/product/3017624010701.json"}, "moved": True},
    # Home (nest.py): the owner's own thermostat, through Google's Smart Device Management API. Personal: commands
    # and readings about the owner's home, between Buddy and Google (which already holds them).
    "nest": {"group": "Home", "title": "Google Nest (thermostat)",
             "hosts": ["https://smartdevicemanagement.googleapis.com"],
             "auth": {"type": "oauth", "token_url": "https://oauth2.googleapis.com/token",
                      "fields": {"client_id": "BUDDY_NEST_CLIENT_ID", "client_secret": "BUDDY_NEST_CLIENT_SECRET",
                                 "refresh_token": "BUDDY_NEST_REFRESH_TOKEN", "project": "BUDDY_NEST_PROJECT_ID"}},
             "cost": 0.0, "per_minute": 10, "privacy": "personal",
             "used_for": "your thermostat: temperature, humidity, mode; changes only after your yes",
             "setup_command": "sudo bash /opt/buddy/deploy/nest.sh", "health": {"custom": "nest.devices"},
             "moved": True},
    # The free AI models keep their own failover (rests, model swaps) and report each result here.
    "groq": {"group": "AI models", "title": "Groq (free AI)", "auth": {"type": "header", "secret": "BUDDY_GROQ_KEY"},
             "privacy": "public", "used_for": "free models for general work", "reports": True},
    "cerebras": {"group": "AI models", "title": "Cerebras (free AI)", "auth": {"type": "header", "secret": "BUDDY_CEREBRAS_KEY"},
                 "privacy": "public", "used_for": "free models for general work", "reports": True},
    "gemini": {"group": "AI models", "title": "Gemini (free AI)", "auth": {"type": "header", "secret": "BUDDY_GEMINI_KEY"},
               "privacy": "public", "used_for": "free models for general work", "reports": True},
    "openrouter": {"group": "AI models", "title": "OpenRouter (free AI)", "auth": {"type": "header", "secret": "BUDDY_OPENROUTER_KEY"},
                   "privacy": "public", "used_for": "free models for general work", "reports": True},
    "mistral": {"group": "AI models", "title": "Mistral (free AI)", "auth": {"type": "header", "secret": "BUDDY_MISTRAL_KEY"},
                "privacy": "public", "used_for": "free models for general work", "reports": True},
    "github_models": {"group": "AI models", "title": "GitHub Models (free AI)", "auth": {"type": "header",
                                                                   "secret": "BUDDY_GITHUB_MODELS_KEY"},
                      "privacy": "public", "used_for": "free models for general work", "reports": True},
    # Listed for the status; they keep their own clients.
    "claude": {"group": "AI models", "title": "Claude (Anthropic)", "auth": {"type": "header", "secret": "ANTHROPIC_API_KEY"},
               "privacy": "personal", "used_for": "Buddy's thinking: everything personal goes only here"},
    # Phone calls (calls.py): Plivo places them; Deepgram hears and ElevenLabs speaks over Pipecat's live audio
    # links, which report how each call went here.
    "plivo": {"group": "Calls", "title": "Plivo (phone calls)", "hosts": ["https://api.plivo.com"],
              "auth": {"type": "basic", "fields": {"user": "BUDDY_PLIVO_AUTH_ID", "password": "BUDDY_PLIVO_AUTH_TOKEN",
                                                  "number": "BUDDY_PLIVO_NUMBER"}},
              "cost": 0.0, "per_minute": 60, "privacy": "personal", "no_backup": True,
              "used_for": "phone calls for you (appointments, questions, hold-and-connect); each one after your yes",
              "setup_command": "sudo bash /opt/buddy/deploy/calls.sh", "health": {"custom": "calls.plivo_health"}},
    "deepgram": {"group": "Calls", "title": "Deepgram (hears calls)", "hosts": ["https://api.deepgram.com"],
                 "auth": {"type": "header", "secret": "BUDDY_DEEPGRAM_KEY", "prefix": "Token "},
                 "cost": 0.0, "per_minute": 30, "privacy": "personal", "reports": True,
                 "used_for": "turns the other side's words into text on Buddy's calls (only while someone talks)",
                 "setup_command": "sudo bash /opt/buddy/deploy/calls.sh", "health": {"path": "/v1/projects"}},
    "elevenlabs": {"group": "Calls", "title": "ElevenLabs (Buddy's call voice)", "hosts": ["https://api.elevenlabs.io"],
                   "auth": {"type": "header", "header": "xi-api-key", "secret": "BUDDY_ELEVENLABS_KEY"},
                   "cost": 0.0, "per_minute": 30, "privacy": "personal", "reports": True,
                   "used_for": "the natural voice Buddy speaks with on calls",
                   "setup_command": "sudo bash /opt/buddy/deploy/calls.sh", "health": {"custom": "calls.voice_health"}},
    "plaid": {"group": "Finance & markets", "title": "Plaid (banks)", "auth": {"type": "header", "secret": "BUDDY_PLAID_CLIENT_ID"},
              "privacy": "personal", "used_for": "bank and card transactions (read-only)"},
}


GROUPS = ["Travel & transit", "Finance & markets", "Shopping", "Health", "Home", "Calls", "Search", "News & trends",
          "Weather", "AI models"]  # app order


def group_of(name: str) -> str:
    return CATALOG[name].get("group") or "Other"


class ConnectError(ToolError):
    """A call that failed: the service, the HTTP status (0 when it couldn't be reached) and its own reason."""

    def __init__(self, provider: str, message: str, status: int = 0, reason: str = ""):
        super().__init__(message)
        self.provider, self.status, self.reason = provider, status, reason
        self.code = status  # like urllib's HTTPError, for callers that check e.code


def _transport(url: str, data: bytes | None = None, headers: dict | None = None, method: str | None = None,
               timeout: int = 30) -> bytes:
    from .tools import require_https  # noqa: PLC0415
    req = urllib.request.Request(require_https(url), data=data, headers={"User-Agent": UA, **(headers or {})},
                                 method=method or None)
    with urllib.request.urlopen(req, timeout=timeout) as r:  # https only  # nosec B310
        return r.read(20_000_000)


def multipart(fields: dict) -> tuple[bytes, str]:
    boundary = uuid.uuid4().hex
    body = b"".join(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode()
                    for k, v in fields.items()) + f"--{boundary}--\r\n".encode()
    return body, f"multipart/form-data; boundary={boundary}"


def name_for(url: str) -> str:
    """The catalog service a full address belongs to."""
    for name, entry in CATALOG.items():
        if any(url.startswith(h) for h in entry.get("hosts") or []):
            return name
    host = urllib.parse.urlparse(url).netloc
    for name, entry in CATALOG.items():
        if any(urllib.parse.urlparse(h).netloc == host for h in entry.get("hosts") or []):
            return name
    raise ConnectError(host, f"{host} isn't in Buddy Connect's catalog.")


def _secret(env, name: str) -> str:
    return ((env if env is not None else os.environ).get(name) or "").strip()


def _secret_names(name: str) -> list[str]:
    auth = CATALOG[name].get("auth") or {}
    if auth.get("type") in ("token", "oauth", "basic"):
        return list(auth["fields"].values())
    return [auth["secret"]] if auth.get("secret") else []


def missing_keys(name: str, env=None) -> list[str]:
    """The settings still empty for a service that has some of its secrets but not all (half set up): those need
    the owner. A service with none of its secrets is simply not set up, and gives []."""
    names = _secret_names(name)
    empty = [n for n in names if not _secret(env, n)]
    return empty if 0 < len(empty) < len(names) else []


def configured(name: str, env=None) -> bool:
    auth = CATALOG[name].get("auth") or {}
    if auth.get("type") in ("token", "oauth", "basic"):
        return all(_secret(env, s) for s in auth["fields"].values())
    return not auth.get("secret") or bool(_secret(env, auth["secret"]))


_lock = threading.Lock()
_recent: dict[str, list[float]] = {}


def _rate_ok(name: str) -> bool:
    limit = CATALOG[name].get("per_minute")
    if not limit:
        return True
    now = time.monotonic()
    with _lock:
        calls = [t for t in _recent.get(name, []) if now - t < 60]
        if len(calls) >= limit:
            _recent[name] = calls
            return False
        calls.append(now)
        _recent[name] = calls
    return True


def _note(memory, name: str, ok: bool, endpoint: str, seconds: float, error: str = "", purpose: str = "") -> None:
    if memory is None:
        return
    from .travel import log_call  # noqa: PLC0415  (the api_calls table Insights reads)
    _table(memory)
    log_call(memory, name, endpoint, ok, seconds, purpose or "connect", error[:200],
             cost=CATALOG[name].get("cost", 0.0) if ok else 0.0)
    try:
        memory.set(f"connect:last:{name}", json.dumps({"ok": ok, "at": datetime.now(timezone.utc).isoformat(),
                                                       "error": error[:200]}))
    except Exception:
        pass


def _table(memory) -> None:
    from .markets import API_CALLS_SQL  # noqa: PLC0415
    memory.db.executescript(API_CALLS_SQL)


def report(memory, name: str, ok: bool, error: str = "") -> None:
    """A service that sends on its own (the free models) says how its last call went, for the status."""
    if memory is None or name not in CATALOG:
        return
    try:
        memory.set(f"connect:last:{name}", json.dumps({"ok": ok, "at": datetime.now(timezone.utc).isoformat(),
                                                       "error": error[:200]}))
    except Exception:
        pass


def reason_of(got) -> str:
    """What a service said when it refused, short and without tokens."""
    if isinstance(got, (bytes, bytearray)):
        got = got[:400].decode("utf-8", "replace")
    if isinstance(got, str):
        try:
            got = json.loads(got)
        except ValueError:
            return " ".join(got.split())[:200]
    if isinstance(got, dict):
        got = {k: v for k, v in got.items() if "token" not in k.lower()}
        text = " ".join(str(v) for k, v in got.items() if k.lower() in ("errormessage", "message", "error", "detail"))
        return " ".join((text or json.dumps(got)).split())[:200]
    return " ".join(str(got or "").split())[:200]


def call_url(memory, name: str, urls: list[str], *, data: bytes | None = None, headers: dict | None = None,
             raw: bool = False, fetch=None, purpose: str = "", personal: bool = False, endpoint: str = "",
             method: str = ""):
    """The one place outside calls go through: tries each url (a backup host only when one can't be reached),
    retries once on a 5xx or timeout, logs it, and returns JSON (or bytes with raw=True)."""
    entry = CATALOG.get(name)
    if entry is None:
        raise ConnectError(name, f"{name} isn't in Buddy Connect's catalog.")
    if personal and entry.get("privacy") != "personal":
        raise ConnectError(name, f"{entry['title']} may not see personal data, so I didn't send this there.")
    if not _rate_ok(name):
        raise ConnectError(name, f"{entry['title']}: too many calls this minute; try again shortly.", 429)
    fetch = fetch or _transport
    endpoint = endpoint or urllib.parse.urlparse(urls[0]).path[-60:]
    started = time.monotonic()
    last: Exception | None = None
    for url in urls:
        for attempt in (0, 1):
            try:
                body = fetch(url, data, headers, method) if method else fetch(url, data, headers)
            except urllib.error.HTTPError as e:
                said = reason_of(e.read(400) if hasattr(e, "read") else "")
                if e.code >= 500 and attempt == 0 and data is None and not method:  # reads only: never re-send
                    last = e
                    continue
                _note(memory, name, False, endpoint, time.monotonic() - started, f"HTTP {e.code}: {said}", purpose)
                raise ConnectError(name, f"{entry['title']} said HTTP {e.code}" + (f": {said}" if said else ""),
                                   e.code, said) from e
            except (urllib.error.URLError, OSError, TimeoutError) as e:
                last = e
                if attempt == 0 and isinstance(e, TimeoutError) and data is None and not method:
                    continue
                break  # can't reach this host: the next one, if any
            else:
                _note(memory, name, True, endpoint, time.monotonic() - started, purpose=purpose)
                if raw or isinstance(body, (dict, list)):
                    return body
                try:
                    return json.loads(body or b"null")
                except ValueError as e:
                    _note(memory, name, False, endpoint, 0, "not JSON", purpose)
                    raise ConnectError(name, f"{entry['title']} answered with something that isn't data.",
                                       200, reason_of(body)) from e
    _note(memory, name, False, endpoint, time.monotonic() - started, f"unreachable: {last}", purpose)
    raise ConnectError(name, f"{entry['title']} can't be reached right now.", 0, str(last)[:200])


def _redact(path: str) -> str:
    """What the logs keep of a path: Plivo's account id (it's in every path) is left out."""
    return re.sub(r"/Account/[^/]+", "/Account/~", path)


def call(memory, name: str, path: str = "", *, data: bytes | None = None, json_body=None, headers: dict | None = None,
         raw: bool = False, fetch=None, env=None, purpose: str = "", personal: bool = False, method: str = ""):
    """Call a catalog service by name and path: its hosts in order, with its key added."""
    entry = CATALOG[name]
    auth = entry.get("auth") or {}
    headers = dict(headers or {})
    if json_body is not None:
        data = json.dumps(json_body).encode()
        headers.setdefault("Content-Type", "application/json")
    if auth.get("type") == "header":
        key = _secret(env, auth["secret"])
        if not key:
            raise ConnectError(name, f"{entry['title']} isn't set up: sudo bash /opt/buddy/deploy/set.sh "
                                     f"{auth['secret']}")
        headers[auth.get("header", "Authorization")] = auth.get("prefix", "") + key
    elif auth.get("type") == "basic":
        user, password = _secret(env, auth["fields"]["user"]), _secret(env, auth["fields"]["password"])
        if not (user and password):
            raise ConnectError(name, f"{entry['title']} isn't set up: {setup_hint(name)}")
        headers["Authorization"] = "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()
    urls = [h + path for h in (entry["hosts"][:1] if entry.get("no_backup") else entry["hosts"])]
    return call_url(memory, name, urls, data=data, headers=headers, raw=raw, fetch=fetch, purpose=purpose,
                    personal=personal, endpoint=_redact(path)[:60], method=method)


def token(memory, name: str, *, fresh: bool = False, env=None, fetch=None) -> str:
    """A sign-in token for a username-and-password service: kept and reused for its lifetime, and a failed
    sign-in isn't retried within the hour (services limit how often tokens may be minted)."""
    entry = CATALOG[name]
    auth = entry["auth"]
    values = {field: _secret(env, setting) for field, setting in auth["fields"].items()}
    if not all(values.values()):
        settings = " and ".join(auth["fields"].values())
        raise ConnectError(name, f"{entry['title']} needs its login: sudo bash /opt/buddy/deploy/set.sh {settings}")
    key = auth.get("memory_key") or f"connect:token:{name}"
    try:
        saved = json.loads(memory.get(key) or "{}") if memory is not None else {}
    except ValueError:
        saved = {}
    young = saved.get("at") and datetime.fromisoformat(saved["at"]) > \
        datetime.now(timezone.utc) - timedelta(hours=auth.get("hours", 23))
    if saved.get("token") and young and not fresh:
        return saved["token"]
    if memory is not None:
        from .tools import local_now  # noqa: PLC0415
        hour = local_now().strftime("%Y-%m-%dT%H")
        tried = f"{key.replace('token', 'token_tried', 1)}"
        if memory.get(tried) == hour:
            if saved.get("token") and not fresh:
                return saved["token"]
            raise ConnectError(name, f"{entry['title']} didn't give a token this hour; I'll try again later.")
        memory.set(tried, hour)
    body, ctype = multipart(values)
    got = call_url(memory, name, [h + auth["login"] for h in entry["hosts"]], data=body,
                   headers={"Content-Type": ctype}, fetch=fetch, endpoint=auth["login"])
    value = (got or {}).get("UserToken") if isinstance(got, dict) and \
        got.get("Authenticated") in (True, "True", "true") else ""
    if not value:
        said = reason_of(got)
        raise ConnectError(name, f"{entry['title']} didn't accept the username and password"
                                 + (f"; it said: {said}" if said else "") + ".", 200, said)
    if memory is not None:
        memory.set(key, json.dumps({"token": value, "at": datetime.now(timezone.utc).isoformat()}))
    return value


def setup_hint(name: str) -> str:
    """The command that saves this service's secret(s) on the server ('' when it needs none)."""
    if CATALOG[name].get("setup_command"):
        return CATALOG[name]["setup_command"]
    auth = CATALOG[name].get("auth") or {}
    names = list(auth["fields"].values()) if auth.get("type") in ("token", "basic") else [auth["secret"]] \
        if auth.get("secret") else []
    return "; ".join(f"sudo bash /opt/buddy/deploy/set.sh {n}" for n in names)


def oauth_token(memory, name: str, *, env=None, fetch=None, fresh: bool = False) -> str:
    """An access token from a saved OAuth refresh token (Google's sign-in): kept until a minute before it expires,
    then renewed. The refresh token itself never leaves the server's settings."""
    entry = CATALOG[name]
    auth = entry["auth"]
    f = {k: _secret(env, v) for k, v in auth["fields"].items()}
    if not all(f.values()):
        raise ConnectError(name, f"{entry['title']} isn't linked yet: {setup_hint(name)}")
    key = f"connect:oauth:{name}"
    try:
        saved = json.loads(memory.get(key) or "{}") if memory is not None else {}
    except ValueError:
        saved = {}
    if saved.get("token") and saved.get("until", 0) > time.time() + 60 and not fresh:
        return saved["token"]
    body = urllib.parse.urlencode({"client_id": f["client_id"], "client_secret": f["client_secret"],
                                   "refresh_token": f["refresh_token"], "grant_type": "refresh_token"}).encode()
    try:
        got = call_url(memory, name, [auth["token_url"]], data=body, endpoint="/token", fetch=fetch,
                       headers={"Content-Type": "application/x-www-form-urlencoded"})
    except ConnectError as e:
        if e.status in (400, 401):
            raise ConnectError(name, f"Google no longer accepts Buddy's link to {entry['title']} ({e.reason}): "
                                     f"link it again with {setup_hint(name)}", e.status, e.reason) from e
        raise
    value = (got or {}).get("access_token", "")
    if not value:
        raise ConnectError(name, f"Google didn't give an access token for {entry['title']}.")
    if memory is not None:
        memory.set(key, json.dumps({"token": value, "until": time.time() + int(got.get("expires_in", 3600))}))
    return value


def health(name: str, memory=None, env=None, fetch=None) -> tuple[bool, str]:
    """One small real request (sign-in, or a cheap read) and what came back."""
    entry = CATALOG[name]
    check = entry.get("health")
    if not check:
        return False, "no health check yet (moves to Buddy Connect in a later stage)"
    if not configured(name, env):
        return False, "not set up"
    try:
        if check.get("custom"):
            import importlib  # noqa: PLC0415
            mod, fn = check["custom"].rsplit(".", 1)
            getattr(importlib.import_module(f"buddy.{mod}"), fn)(memory, env=env, fetch=fetch)
        elif check.get("login"):
            token(None, name, fresh=True, env=env, fetch=fetch)
        else:
            extra = {h: _secret(env, v) for h, v in (check.get("headers_from") or {}).items()}
            if extra:  # services whose key is more than one header: sent as given, not added by call()
                call_url(memory, name, [entry["hosts"][0] + check["path"]], headers=extra, fetch=fetch,
                         purpose="health")
            else:
                call(memory, name, check["path"], json_body=check.get("json"), env=env, fetch=fetch,
                     purpose="health", raw=bool(check.get("raw")))
        return True, "ok"
    except ConnectError as e:
        return False, str(e)


MODEL_NAMES = {"github_models": ("github",), "openrouter": ("openrouter", "deepseek")}  # model_calls' names


def status(memory, env=None) -> list[dict]:
    """Every service: set up or not, last result, calls and cost this month. The last result is the newest of
    what Buddy Connect noted and the service's real last call in the logs Buddy keeps (API calls; model calls for
    Claude and the free models), so services used before Buddy Connect existed show their true state too."""
    since = datetime.now(timezone.utc).replace(day=1, hour=0, minute=0, second=0, microsecond=0).isoformat()
    usage, latest = {}, {}
    try:
        _table(memory)
        for r in memory.db.execute("SELECT provider, COUNT(*) AS n, SUM(1 - ok) AS bad, SUM(cost_usd) AS usd "
                                   "FROM api_calls WHERE at >= ? GROUP BY provider", (since,)):
            usage[r["provider"]] = (r["n"], r["bad"] or 0, r["usd"] or 0.0)
        for r in memory.db.execute("SELECT provider, at, ok, error FROM api_calls WHERE id IN "
                                   "(SELECT MAX(id) FROM api_calls GROUP BY provider)"):
            latest[r["provider"]] = {"ok": bool(r["ok"]), "at": r["at"], "error": r["error"] or ""}
    except Exception:
        pass
    m_usage, m_latest = {}, {}  # Claude and the free models: their calls are in model_calls (own names)
    try:
        for r in memory.db.execute("SELECT provider, COUNT(*) AS n, SUM(1 - ok) AS bad, SUM(cost_usd) AS usd "
                                   "FROM model_calls WHERE at >= ? GROUP BY provider", (since,)):
            m_usage[r["provider"]] = (r["n"], r["bad"] or 0, r["usd"] or 0.0)
        for r in memory.db.execute("SELECT provider, at, ok, stop FROM model_calls WHERE id IN "
                                   "(SELECT MAX(id) FROM model_calls GROUP BY provider)"):
            m_latest[r["provider"]] = {"ok": bool(r["ok"]), "at": r["at"], "error": "" if r["ok"] else r["stop"] or ""}
    except Exception:
        pass
    out = []
    for name, entry in CATALOG.items():
        try:
            last = json.loads(memory.get(f"connect:last:{name}") or "{}")
        except ValueError:
            last = {}
        is_model = bool(entry.get("reports")) or name == "claude"
        aliases = MODEL_NAMES.get(name, (name,)) if is_model else (name,)
        src_latest, src_usage = (m_latest, m_usage) if is_model else (latest, usage)
        logged = [src_latest[n] for n in aliases if n in src_latest]
        newest = max(logged, key=lambda x: x["at"]) if logged else {}
        if newest and newest["at"] > last.get("at", ""):
            last = newest
        n, bad, usd = 0, 0, 0.0
        for alias in aliases:
            a, b, c = src_usage.get(alias, (0, 0, 0.0))
            n, bad, usd = n + a, bad + b, usd + c
        out.append({"name": name, "group": group_of(name), "title": entry["title"],
                    "used_for": entry.get("used_for", ""),
                    "privacy": entry.get("privacy", "public"), "set_up": configured(name, env),
                    "in_connect": bool(entry.get("moved") or entry.get("reports")), "last_ok": last.get("ok"),
                    "last_at": last.get("at", ""), "last_error": last.get("error", ""), "calls_this_month": n,
                    "failed_this_month": bad, "cost_this_month": round(usd, 4), "can_test": bool(entry.get("health")),
                    "setup": setup_hint(name), "missing_keys": missing_keys(name, env)})
    for row in out:
        row["state"] = state_of(row)
    return out


# How the Connections screen orders a service: what needs the owner first, then what works, then what's idle.
STATES = ["failing", "missing_keys", "working", "unused", "not_set_up"]
ATTENTION = ("failing", "missing_keys")


def state_of(row: dict) -> str:
    """One word for a status row: failing (set up, last call failed), missing_keys (half set up), working,
    unused (set up, never called) or not_set_up."""
    if row.get("missing_keys"):
        return "missing_keys"
    if not row.get("set_up"):
        return "not_set_up"
    if row.get("last_ok") is False:
        return "failing"
    return "working" if row.get("last_ok") else "unused"


def arrange(rows: list[dict]) -> list[dict]:
    """The status rows as the app shows them: "Needs attention" (failing or missing keys) on top, then each area in
    GROUPS order with the set-up services (working, then unused), then "Not set up" at the bottom. Every row is in
    exactly one section; within a section by state, then area, then title. Sections with nothing are left out."""
    order = {g: i for i, g in enumerate(GROUPS)}

    def key(r):
        st = r.get("state") or state_of(r)
        return (STATES.index(st), order.get(r.get("group"), len(GROUPS)), (r.get("title") or "").lower())

    attention, by_group, idle = [], {}, []
    for r in sorted(rows, key=key):
        st = r.get("state") or state_of(r)
        if st in ATTENTION:
            attention.append(r)
        elif st == "not_set_up":
            idle.append(r)
        else:
            by_group.setdefault(r.get("group") or "Other", []).append(r)
    sections = []
    if attention:
        sections.append({"title": "Needs attention", "kind": "attention", "names": [r["name"] for r in attention]})
    for g in sorted(by_group, key=lambda g: (order.get(g, len(GROUPS)), g)):
        sections.append({"title": g, "kind": "group", "names": [r["name"] for r in by_group[g]]})
    if idle:
        sections.append({"title": "Not set up", "kind": "not_set_up", "names": [r["name"] for r in idle]})
    return sections


TOOL = {
    "name": "connections",
    "description": "Buddy Connect: every outside service Buddy uses (search, transit feeds, markets, banks, flights, "
    "weather, free AI models...), whether it's set up, whether its last call worked and why not, and calls and cost "
    "this month. find: the services that can do a job (query, e.g. 'train delays', 'stock prices', 'flight status') "
    "with their state, and how to attach (set up) one that isn't; use it when a task needs an outside service you "
    "aren't sure is available, or a tool says a service isn't set up. list: all of them; test: one small real "
    "request to one service (name).",
    "input_schema": {"type": "object", "properties": {
        "action": {"type": "string", "enum": ["find", "list", "test"]},
        "query": {"type": "string", "description": "find: the job or the service, in a few words"},
        "name": {"type": "string", "description": "test: the service, e.g. njt_rail, njt_bus, serper, mta, path"}},
        "required": ["action"], "additionalProperties": False},
}


def find(query: str, memory=None, env=None) -> list[dict]:
    """The catalog services that match a job or a name, best first (words in the title and what it's used for)."""
    import re  # noqa: PLC0415
    words = [w for w in re.findall(r"[a-z0-9]+", (query or "").lower()) if len(w) > 2]
    rows = status(memory, env) if memory is not None else [
        {"name": n, "title": e["title"], "used_for": e.get("used_for", ""), "set_up": configured(n, env),
         "last_ok": None, "last_error": "", "setup": setup_hint(n), "group": group_of(n)}
        for n, e in CATALOG.items()]
    scored = []
    for r in rows:
        hay = f"{r['name']} {r['title']} {r['used_for']} {r['group']}".lower().replace("_", " ")
        score = sum(2 if re.search(rf"\b{re.escape(w)}", hay) else 0 for w in words)
        if score:
            scored.append((score, r))
    return [r for _, r in sorted(scored, key=lambda x: -x[0])]


def run_tool(memory, args: dict) -> str:
    if args.get("action") == "find":
        found = find(args.get("query", ""), memory)
        if not found:
            return ("No service in Buddy Connect does that yet. A new one can be added to its catalog (a code change: "
                    "offer to send it to Claude Code).")
        lines = []
        for r in found[:5]:
            if not r["set_up"]:
                lines.append(f"- {r['title']} ({r['used_for']}): not attached. To attach it, on the server: "
                             f"{r['setup'] or 'nothing to set'}; then sudo systemctl restart buddy")
            else:
                state = "working" if r["last_ok"] else (f"failing: {r['last_error']}" if r["last_ok"] is False
                                                        else "attached, not used yet")
                lines.append(f"- {r['title']} ({r['used_for']}): {state}")
        return "Services for that:\n" + "\n".join(lines)
    if args.get("action") == "test":
        name = (args.get("name") or "").strip()
        if name not in CATALOG:
            return f"No service called {name!r}. Known: {', '.join(CATALOG)}."
        ok, said = health(name, memory)
        return f"{CATALOG[name]['title']}: {'working' if ok else 'not working'} ({said})."
    rows = []
    every = status(memory)
    order = {g: i for i, g in enumerate(GROUPS)}
    group = ""
    for s in sorted(every, key=lambda x: (order.get(x["group"], len(GROUPS)), x["title"])):
        if s["group"] != group:
            group = s["group"]
            rows.append(f"{group}:")
        state = "not set up" if not s["set_up"] else ("working" if s["last_ok"] else
                                                      f"failing: {s['last_error']}" if s["last_ok"] is False
                                                      else "set up, not used yet")
        rows.append(f"  - {s['title']}: {state}; {s['calls_this_month']} calls this month"
                    + (f", ${s['cost_this_month']:.2f}" if s["cost_this_month"] else "")
                    + ("" if s["in_connect"] else " (moves to Buddy Connect later)"))
    return "Buddy Connect, every outside service:\n" + "\n".join(rows)
