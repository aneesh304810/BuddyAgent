"""Lab results from the owner's patient portals (MyChart and other Epic sites) over SMART on FHIR: the owner signs
in at their health system, Buddy reads their lab results (read only), and they join the Labs tab beside uploaded
reports.

What has to be set up once (docs/HEALTH.md): a free patient app registered at fhir.epic.com ("Epic on FHIR"), with
this server's return address (https://<your tailnet name>/connect/mychart/linked) and read access to Patient,
Observation and DiagnosticReport; its client ID goes into Buddy with
sudo bash /opt/buddy/deploy/set.sh BUDDY_EPIC_CLIENT_ID. Epic decides when a new app's production client ID works
at each health system; until then a sign-in fails at the health system's page, and Buddy says so.

How it works (the SMART App Launch "standalone" flow):
  1. The owner picks their health system (Epic's public directory of patient endpoints, or its FHIR address).
  2. Buddy reads the system's /.well-known/smart-configuration, makes a one-time state and a PKCE secret, and gives
     the sign-in link (scopes: launch/patient, openid, fhirUser, offline_access, read of Patient, Observation and
     DiagnosticReport).
  3. The health system sends the browser back to /connect/mychart/linked; the code is swapped for tokens, kept in
     the encrypted database. With a refresh token Buddy reads new results once a day by itself; without one (the
     health system decides), each read after the first hour needs the owner to sign in again: shown as "Sign in
     again" (connecthub) until they do.
  4. Lab Observations (category laboratory) are read page by page: the test (with its LOINC code, given by the
     health system, not guessed), the value and unit (UCUM), the reference range, the flag (H, L, A), the date.
     Results with no number (a "negative", a comment) are not kept. Each is kept once (by the health system's own
     id) in labs.py's tables, as a health record (quality "lab"), and shown on the Labs tab.

Nothing here is given to an AI model; only the lab ranges switch (labs.py) lets Claude have a test's range word.
"""

import base64
import hashlib
import json
import logging
import re
import secrets
import time
import urllib.parse
from datetime import datetime, timedelta, timezone

from . import gateway
from .memory import Memory

log = logging.getLogger(__name__)

NAME = "mychart"
CLIENT_SETTING = "BUDDY_EPIC_CLIENT_ID"
DIRECTORY = "https://open.epic.com/Endpoints/R4"
SCOPES = ("launch/patient openid fhirUser offline_access patient/Patient.read patient/Observation.read "
          "patient/DiagnosticReport.read")
ORGS_KEY, LINK_KEY, DIR_KEY = "mychart:orgs", "mychart:link", "mychart:directory"
LINK_MINUTES, SYNC_EVERY_S, MAX_PAGES = 15, 24 * 3600, 30
FHIR = {"Accept": "application/fhir+json"}
FLAGS = {"H": "H", "HH": "H", "HU": "H", ">": "H", "L": "L", "LL": "L", "LU": "L", "<": "L", "A": "A", "AA": "A"}


def _orgs(memory: Memory) -> dict:
    try:
        return json.loads(memory.get(ORGS_KEY) or "{}") if memory is not None else {}
    except ValueError:
        return {}


def _save_orgs(memory: Memory, orgs: dict) -> None:
    memory.set(ORGS_KEY, json.dumps(orgs))


def linked(memory: Memory, env=None) -> bool:
    return gateway.configured(NAME, env) and any(o.get("access") or o.get("refresh") for o in _orgs(memory).values())


def redirect_uri(memory: Memory) -> str:
    base = (memory.get("server:base_url") or "").rstrip("/") + "/connect/mychart/linked" if memory is not None else ""
    return base if base.startswith("https://") else ""


def _base(url: str) -> str:
    url = str(url or "").strip().rstrip("/")
    if not re.fullmatch(r"https://[A-Za-z0-9.\-]+(?::\d+)?(?:/[\w.\-~%/]*)?", url):
        raise gateway.ConnectError(NAME, "That isn't a health system's FHIR address (it starts with https://).")
    return url


def _get(memory: Memory, url: str, token: str = "", fetch=None, purpose: str = "lab results"):
    headers = dict(FHIR)
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return gateway.call_url(memory, NAME, [url], headers=headers, fetch=fetch, purpose=purpose, personal=bool(token),
                            endpoint=urllib.parse.urlparse(url).path[-60:])


# --- finding the owner's health system ------------------------------------------------------------------------------
def directory(memory: Memory, query: str, fetch=None) -> list[dict]:
    """Health systems in Epic's public directory whose name has every word of the query: [{name, base}]."""
    words = [w for w in re.findall(r"\w+", str(query or "").lower()) if len(w) > 1][:6]
    if not words:
        return []
    try:
        cached = json.loads(memory.get(DIR_KEY) or "{}")
    except ValueError:
        cached = {}
    if cached.get("at", 0) > time.time() - 7 * 86400 and cached.get("list"):
        entries = cached["list"]
    else:
        got = gateway.call_url(memory, NAME, [DIRECTORY], headers={"Accept": "application/json"}, fetch=fetch,
                               purpose="directory") or {}
        entries = []
        for e in got.get("entry") or []:  # a FHIR Bundle of Endpoint resources
            r = (e or {}).get("resource") or {}
            if r.get("address") and r.get("name"):
                entries.append({"name": str(r["name"])[:120], "base": str(r["address"]).rstrip("/")})
        for e in got.get("Entries") or []:  # the older list shape
            if e.get("FHIRPatientFacingURI") and e.get("OrganizationName"):
                entries.append({"name": str(e["OrganizationName"])[:120], "base": str(e["FHIRPatientFacingURI"]).rstrip("/")})
        if entries:
            memory.set(DIR_KEY, json.dumps({"at": time.time(), "list": entries}))
    return [e for e in entries if all(w in e["name"].lower() for w in words)][:20]


def smart_config(memory: Memory, base: str, fetch=None) -> dict:
    got = _get(memory, _base(base) + "/.well-known/smart-configuration", fetch=fetch, purpose="sign-in setup") or {}
    if not (got.get("authorization_endpoint") and got.get("token_endpoint")):
        raise gateway.ConnectError(NAME, "That health system didn't say where to sign in (no SMART configuration).")
    for k in ("authorization_endpoint", "token_endpoint"):
        _base(got[k])  # https only
    return got


# --- signing in -----------------------------------------------------------------------------------------------------
def start_link(memory: Memory, env=None, base: str = "", name: str = "", fetch=None) -> dict:
    """The sign-in link for one health system, valid LINK_MINUTES. Called from the Labs tab with the system chosen;
    from the Connections screen it reuses the last one chosen."""
    if not gateway.configured(NAME, env):
        raise gateway.ConnectError(NAME, "MyChart isn't set up on this Buddy yet: register a patient app at "
                                         "fhir.epic.com, then sudo bash /opt/buddy/deploy/set.sh BUDDY_EPIC_CLIENT_ID")
    orgs = _orgs(memory)
    if not base:
        last = max(orgs.values(), key=lambda o: o.get("chosen_at", ""), default=None)
        if not last:
            raise gateway.ConnectError(NAME, "Choose your health system first: Health → Labs → Connect a patient portal.")
        base, name = last["base"], last.get("name", "")
    base = _base(base)
    back = redirect_uri(memory)
    if not back:
        raise gateway.ConnectError(NAME, "Buddy doesn't know its own address yet: open the Buddy app once, then try again.")
    config = smart_config(memory, base, fetch)
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    state = secrets.token_urlsafe(24)
    orgs.setdefault(base, {"base": base})
    orgs[base].update(name=str(name or orgs[base].get("name") or urllib.parse.urlparse(base).hostname)[:120],
                      chosen_at=datetime.now(timezone.utc).isoformat(), token_endpoint=config["token_endpoint"])
    _save_orgs(memory, orgs)
    memory.set(LINK_KEY, json.dumps({"state": state, "verifier": verifier, "base": base, "back": back,
                                     "until": time.time() + LINK_MINUTES * 60}))
    q = {"response_type": "code", "client_id": gateway._secret(env, CLIENT_SETTING), "redirect_uri": back,
         "scope": SCOPES, "state": state, "aud": base, "code_challenge": challenge, "code_challenge_method": "S256"}
    return {"url": config["authorization_endpoint"] + "?" + urllib.parse.urlencode(q), "minutes": LINK_MINUTES,
            "name": orgs[base]["name"]}


def _token(memory: Memory, org: dict, form: dict, env=None, fetch=None) -> dict:
    form = {**form, "client_id": gateway._secret(env, CLIENT_SETTING)}
    got = gateway.call_url(memory, NAME, [org["token_endpoint"]], data=urllib.parse.urlencode(form).encode(),
                           headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"},
                           endpoint="/token", fetch=fetch, purpose="sign-in") or {}
    if not got.get("access_token"):
        raise gateway.ConnectError(NAME, f"{org.get('name') or 'The health system'} didn't give an access token.")
    return got


def finish_link(memory: Memory, code: str, state: str, env=None, fetch=None) -> dict:
    """The health system sent the browser back: check the state, swap the code (with the PKCE secret) for tokens."""
    from . import connecthub  # noqa: PLC0415
    try:
        link = json.loads(memory.get(LINK_KEY) or "{}")
    except ValueError:
        link = {}
    if not (link.get("state") and state and secrets.compare_digest(str(state), link["state"])
            and link.get("until", 0) > time.time()):
        raise gateway.ConnectError(NAME, "This sign-in link expired or was already used: start again from Buddy.")
    memory.set(LINK_KEY, "")  # once only
    if not code:
        raise gateway.ConnectError(NAME, "The health system didn't send a code back (was access allowed?).")
    orgs = _orgs(memory)
    org = orgs.get(link["base"])
    if not org:
        raise gateway.ConnectError(NAME, "Choose your health system again: it was removed meanwhile.")
    got = _token(memory, org, {"grant_type": "authorization_code", "code": code, "redirect_uri": link["back"],
                               "code_verifier": link["verifier"]}, env, fetch)
    if not got.get("patient"):
        raise gateway.ConnectError(NAME, "The health system didn't say whose records these are (no patient).")
    org.update(access=got["access_token"], refresh=got.get("refresh_token", ""), patient=str(got["patient"]),
               until=time.time() + int(got.get("expires_in") or 3600), scope=str(got.get("scope") or "")[:400],
               linked_at=datetime.now(timezone.utc).isoformat(), synced_until="")
    _save_orgs(memory, orgs)
    connecthub.set_expired(memory, NAME, "")
    return {"linked": True, "name": org.get("name", "")}


def _access(memory: Memory, base: str, env=None, fetch=None) -> str:
    """A current access token for one health system: renewed with its refresh token when there is one; otherwise,
    once it has run out, the owner signs in again (marked expired)."""
    from . import connecthub  # noqa: PLC0415
    orgs = _orgs(memory)
    org = orgs[base]
    if org.get("access") and org.get("until", 0) > time.time() + 60:
        return org["access"]
    if not org.get("refresh"):
        connecthub.set_expired(memory, NAME, f"{org.get('name') or 'The health system'} needs you to sign in again "
                                             "to read new results (it gave Buddy no lasting sign-in)")
        raise gateway.ConnectError(NAME, f"Sign in to {org.get('name') or 'the health system'} again: Health → Labs.", 401)
    try:
        got = _token(memory, org, {"grant_type": "refresh_token", "refresh_token": org["refresh"]}, env, fetch)
    except gateway.ConnectError as e:
        if e.status in (400, 401):
            connecthub.set_expired(memory, NAME, f"{org.get('name') or 'The health system'} no longer accepts "
                                                 "Buddy's sign-in")
        raise
    org.update(access=got["access_token"], refresh=got.get("refresh_token") or org["refresh"],
               until=time.time() + int(got.get("expires_in") or 3600))
    _save_orgs(memory, orgs)
    return org["access"]


# --- reading lab results --------------------------------------------------------------------------------------------
def _loinc(observation: dict) -> str:
    for c in ((observation.get("code") or {}).get("coding") or []):
        if str(c.get("system") or "").rstrip("/") == "http://loinc.org" and c.get("code"):
            return str(c["code"])[:20]
    return ""


def result_of(observation: dict) -> dict | None:
    """One lab Observation as a result, or None (no number, cancelled, or not a lab result)."""
    from . import labs  # noqa: PLC0415
    if observation.get("resourceType") != "Observation" or observation.get("status") in (
            "cancelled", "entered-in-error", "registered"):
        return None
    quantity = observation.get("valueQuantity") or {}
    if not isinstance(quantity.get("value"), (int, float)) or isinstance(quantity.get("value"), bool):
        return None
    code = observation.get("code") or {}
    printed = str(code.get("text") or next((c.get("display") for c in code.get("coding") or [] if c.get("display")), "")
                  or "Lab result")[:80]
    when = str(observation.get("effectiveDateTime") or (observation.get("effectivePeriod") or {}).get("start")
               or observation.get("issued") or "")[:10]
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", when):
        return None
    ref = (observation.get("referenceRange") or [{}])[0] or {}
    low, high = ((ref.get(side) or {}).get("value") for side in ("low", "high"))
    low = low if isinstance(low, (int, float)) and not isinstance(low, bool) else None
    high = high if isinstance(high, (int, float)) and not isinstance(high, bool) else None
    flag = ""
    for interp in observation.get("interpretation") or []:
        for c in (interp or {}).get("coding") or []:
            flag = flag or FLAGS.get(str(c.get("code") or "").upper(), "")
    unit = str(quantity.get("unit") or quantity.get("code") or "")[:20]
    value = float(quantity["value"])
    loinc = _loinc(observation)
    test = labs.test_of(printed) or (f"loinc:{loinc}" if loinc else "")  # a test Buddy has no name for: by its code
    ref_text = str(ref.get("text") or (f"{low}-{high}" if low is not None and high is not None else
                                       f">= {low}" if low is not None else f"<= {high}" if high is not None else ""))
    return {"source_id": str(observation.get("id") or "")[:80], "test": test, "printed": printed, "value": value,
            "value_text": f"{value:g}", "unit": unit, "ref_low": low, "ref_high": high, "ref_text": ref_text[:40],
            "ref_source": "lab" if (low is not None or high is not None) else "", "flag": flag,
            "band": labs.band_of(value, low, high, flag), "collected": when, "loinc": loinc}


def sync(memory: Memory, base: str = "", env=None, fetch=None, kind: str = "") -> dict:
    """Read new lab results from one health system (or all), keep each once. {name: kept}."""
    from . import connecthub, healthrecords, labs  # noqa: PLC0415
    out = {}
    for org_base, org in list(_orgs(memory).items()):
        if (base and org_base != base) or not (org.get("access") or org.get("refresh")):
            continue
        first = not org.get("synced_until")
        with connecthub.syncing(memory, NAME, kind or ("initial" if first else "incremental")) as got:
            token = _access(memory, org_base, env, fetch)
            q = {"patient": org["patient"], "category": "laboratory", "_count": "100"}
            if not first:  # a week back, for results the lab corrected
                q["date"] = "ge" + (datetime.fromisoformat(org["synced_until"]) - timedelta(days=7)).date().isoformat()
            url, pages, kept = f"{org_base}/Observation?{urllib.parse.urlencode(q)}", 0, 0
            labs._ready(memory)
            while url and pages < MAX_PAGES:
                bundle = _get(memory, url, token, fetch) or {}
                pages += 1
                for entry in bundle.get("entry") or []:
                    r = result_of((entry or {}).get("resource") or {})
                    if not r or not r["test"]:
                        continue
                    got["records"] += 1
                    if labs.keep_portal_result(memory, NAME, org.get("name", ""), r):
                        kept += 1
                        healthrecords.record_lab(memory, NAME, r["collected"], r["test"], r["value"], r["unit"],
                                                 r["value_text"], r["loinc"], f"{NAME}:{r['source_id']}", got["id"])
                        got["reached"] = max(got["reached"], r["collected"])
                nxt = next((ln.get("url") for ln in bundle.get("link") or [] if ln.get("relation") == "next"), "")
                url = nxt if nxt.startswith(org_base) else ""
            got["new"] = kept
        orgs = _orgs(memory)
        if org_base in orgs:
            orgs[org_base]["synced_until"] = datetime.now(timezone.utc).isoformat()
            _save_orgs(memory, orgs)
        out[org.get("name") or org_base] = kept
    return out


def sync_if_due(memory: Memory, env=None, fetch=None) -> bool:
    """Once a day per health system (and right after signing in). Never raises."""
    if not linked(memory, env):
        return False
    try:
        last = datetime.fromisoformat(memory.get("mychart:synced") or "2000-01-01T00:00:00+00:00")
    except ValueError:
        last = datetime(2000, 1, 1, tzinfo=timezone.utc)
    fresh = any(not o.get("synced_until") for o in _orgs(memory).values() if o.get("access") or o.get("refresh"))
    if not fresh and (datetime.now(timezone.utc) - last).total_seconds() < SYNC_EVERY_S:
        return False
    memory.set("mychart:synced", datetime.now(timezone.utc).isoformat())
    try:
        sync(memory, env=env, fetch=fetch)
        return True
    except Exception as e:  # shown on the Sources tab and as "Sign in again" when that's the cause
        log.info("mychart sync failed: %s", type(e).__name__)
        return False


def sync_now(memory: Memory, env=None, fetch=None) -> str:
    if not linked(memory, env):
        return "No patient portal is connected: Health → Labs → Connect a patient portal."
    got = sync(memory, env=env, fetch=fetch, kind="manual")
    return "Read " + ", ".join(f"{n} new results from {name}" for name, n in got.items()) + "." if got else "Nothing to read."


def portals(memory: Memory) -> list[dict]:
    """The health systems chosen, for the Labs tab: name, connected or not, last read. No tokens."""
    return [{"base": b, "name": o.get("name", ""), "connected": bool(o.get("access") or o.get("refresh")),
             "lasting": bool(o.get("refresh")), "linked_at": o.get("linked_at", ""), "synced": o.get("synced_until", "")}
            for b, o in _orgs(memory).items()]


def disconnect(memory: Memory, base: str = "") -> str:
    """Forget Buddy's sign-in at one health system (or all). Its results stay until deleted (connecthub.forget)."""
    orgs = _orgs(memory)
    gone = [orgs[b].get("name") or b for b in list(orgs) if not base or b == base]
    for b in [b for b in list(orgs) if not base or b == base]:
        orgs.pop(b, None)
    _save_orgs(memory, orgs)
    memory.set(LINK_KEY, "")
    return ("Disconnected from " + ", ".join(gone) + ".") if gone else "No patient portal was connected."


def forget(memory: Memory) -> None:
    """Every result read from patient portals (connecthub.forget also takes their health records and history)."""
    from . import labs  # noqa: PLC0415
    labs._ready(memory)
    memory.db.execute("DELETE FROM lab_results WHERE source = ?", (NAME,))
    memory.db.commit()


def check(memory, env=None, fetch=None) -> None:
    """Buddy Connect's test: the sign-in works for each connected health system."""
    for base in _orgs(memory):
        _access(memory, base, env, fetch)
