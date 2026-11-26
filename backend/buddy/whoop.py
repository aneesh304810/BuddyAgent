"""The owner's WHOOP band, through Buddy Connect (Health group) and WHOOP's developer API (v2). Read only.

Each day: recovery (score, resting heart rate, HRV, blood oxygen, skin temperature), the day's strain, energy and
heart rate, the main sleep (total, deep "slow wave", REM, light, efficiency, performance, respiratory rate) and
workouts. Kept like the other health readings (healthrecords.keep_days), in the encrypted database, and also as
health records with their source. No AI model is given any of it (the Privacy Gateway's health rule).

Two steps, as for the Oura Ring:
  once per server  register an app at developer.whoop.com with this server's return address
                   (https://<tailnet name>/connect/whoop/linked) and run deploy/whoop.sh (client ID and secret).
  each user        Connections > Health > WHOOP > Connect: a sign-in link with a one-time state (15 minutes); WHOOP
                   sends the browser back to this server's own page, which swaps the code for tokens (with the
                   "offline" scope, a refresh token, so Buddy reads new days by itself every few hours).

Built from WHOOP's published v2 API (records under "records", pages by "next_token", up to 25 at a time) and tested
against a made-up WHOOP, not a real band: the first real sync is the true test, and a field WHOOP names differently
is simply left empty.
"""

import json
import logging
import re
import secrets
import threading
import time
import urllib.parse
from datetime import datetime, timedelta, timezone

from . import gateway
from .memory import Memory
from .tools import local_now

log = logging.getLogger(__name__)

NAME = "whoop"
API = "https://api.prod.whoop.com/developer"
AUTHORIZE_URL = "https://api.prod.whoop.com/oauth/oauth2/auth"
TOKEN_URL = "https://api.prod.whoop.com/oauth/oauth2/token"
SCOPES = "offline read:recovery read:cycles read:sleep read:workout read:profile read:body_measurement"
TOKENS_KEY, LINK_KEY, SYNC_KEY = "whoop:tokens", "whoop:link", "whoop:synced"
LINK_MINUTES, SYNC_EVERY_S, FIRST_DAYS, LATER_DAYS, MAX_PAGES = 15, 3 * 3600, 365, 14, 60
NOT_LINKED = "WHOOP isn't connected yet: Connections > Health > WHOOP > Connect"
FIELDS = ("recovery", "resting_hr", "hrv", "spo2", "skin_temp", "strain", "calories", "avg_hr", "max_hr",
          "sleep_minutes", "deep_minutes", "rem_minutes", "light_minutes", "sleep_efficiency", "sleep_performance",
          "respiratory_rate")
SCHEMA = """
CREATE TABLE IF NOT EXISTS whoop_daily (          -- from the owner's WHOOP band
    day TEXT PRIMARY KEY,
    recovery REAL, resting_hr REAL, hrv REAL, spo2 REAL, skin_temp REAL, strain REAL, calories REAL, avg_hr REAL,
    max_hr REAL, sleep_minutes INTEGER, deep_minutes INTEGER, rem_minutes INTEGER, light_minutes INTEGER,
    sleep_efficiency REAL, sleep_performance REAL, respiratory_rate REAL,
    workouts TEXT NOT NULL DEFAULT '[]', updated_at TEXT NOT NULL
);
"""
_lock = threading.Lock()


def _ready(memory: Memory) -> None:
    memory.db.executescript(SCHEMA)


def _tokens(memory: Memory) -> dict:
    try:
        return json.loads(memory.get(TOKENS_KEY) or "{}") if memory is not None else {}
    except ValueError:
        return {}


def linked(memory: Memory, env=None) -> bool:
    return gateway.configured(NAME, env) and bool(_tokens(memory).get("refresh") or _tokens(memory).get("access"))


def redirect_uri(memory: Memory, env=None) -> str:
    base = gateway._secret(env, "BUDDY_WHOOP_REDIRECT") or \
        ((memory.get("server:base_url") or "").rstrip("/") + "/connect/whoop/linked" if memory is not None else "")
    return base if base.startswith("https://") else ""


def start_link(memory: Memory, env=None) -> dict:
    if not gateway.configured(NAME, env):
        raise gateway.ConnectError(NAME, "WHOOP isn't available on this Buddy yet: whoever runs the server sets it "
                                         "up once with sudo bash /opt/buddy/deploy/whoop.sh")
    back = redirect_uri(memory, env)
    if not back:
        raise gateway.ConnectError(NAME, "Buddy doesn't know its own address yet: open the Buddy app once, then try again.")
    state = secrets.token_urlsafe(24)
    memory.set(LINK_KEY, json.dumps({"state": state, "until": time.time() + LINK_MINUTES * 60, "back": back}))
    q = {"response_type": "code", "client_id": gateway._secret(env, "BUDDY_WHOOP_CLIENT_ID"), "redirect_uri": back,
         "scope": SCOPES, "state": state}
    return {"url": AUTHORIZE_URL + "?" + urllib.parse.urlencode(q), "minutes": LINK_MINUTES}


def _token(memory: Memory, form: dict, env=None, fetch=None) -> dict:
    form = {**form, "client_id": gateway._secret(env, "BUDDY_WHOOP_CLIENT_ID"),
            "client_secret": gateway._secret(env, "BUDDY_WHOOP_CLIENT_SECRET")}
    got = gateway.call_url(memory, NAME, [TOKEN_URL], data=urllib.parse.urlencode(form).encode(), endpoint="/oauth/token",
                           headers={"Content-Type": "application/x-www-form-urlencoded"}, fetch=fetch) or {}
    if not got.get("access_token"):
        raise gateway.ConnectError(NAME, "WHOOP didn't give an access token.")
    return got


def finish_link(memory: Memory, code: str, state: str, env=None, fetch=None) -> dict:
    from . import connecthub  # noqa: PLC0415
    try:
        link = json.loads(memory.get(LINK_KEY) or "{}")
    except ValueError:
        link = {}
    if not (link.get("state") and state and secrets.compare_digest(str(state), link["state"])
            and link.get("until", 0) > time.time()):
        raise gateway.ConnectError(NAME, "This sign-in link expired or was already used: ask Buddy for a new one.")
    memory.set(LINK_KEY, "")
    if not code:
        raise gateway.ConnectError(NAME, "WHOOP didn't send a code back (was access allowed?).")
    got = _token(memory, {"grant_type": "authorization_code", "code": code,
                          "redirect_uri": link.get("back") or redirect_uri(memory, env)}, env, fetch)
    with _lock:
        memory.set(TOKENS_KEY, json.dumps({"refresh": got.get("refresh_token", ""), "access": got["access_token"],
                                           "until": time.time() + int(got.get("expires_in") or 3600)}))
        memory.set(SYNC_KEY, "")
    connecthub.set_expired(memory, NAME, "")
    return {"linked": True}


def access_token(memory: Memory, env=None, fetch=None, fresh: bool = False) -> str:
    from . import connecthub  # noqa: PLC0415
    if not linked(memory, env):
        raise gateway.ConnectError(NAME, NOT_LINKED)
    with _lock:
        saved = _tokens(memory)
        if saved.get("access") and saved.get("until", 0) > time.time() + 60 and not fresh:
            return saved["access"]
        if not saved.get("refresh"):
            connecthub.set_expired(memory, NAME, "WHOOP gave Buddy no lasting sign-in")
            raise gateway.ConnectError(NAME, "Connect WHOOP again: Connections > Health > WHOOP > Connect", 401)
        try:
            got = _token(memory, {"grant_type": "refresh_token", "refresh_token": saved["refresh"], "scope": "offline"},
                         env, fetch)
        except gateway.ConnectError as e:
            if e.status in (400, 401):
                connecthub.set_expired(memory, NAME, f"WHOOP no longer accepts Buddy's link ({e.reason or e.status})")
            raise
        saved.update(access=got["access_token"], until=time.time() + int(got.get("expires_in") or 3600),
                     refresh=got.get("refresh_token") or saved["refresh"])
        memory.set(TOKENS_KEY, json.dumps(saved))
        return saved["access"]


def _get(memory: Memory, path: str, params: dict, env=None, fetch=None):
    url = API + path + "?" + urllib.parse.urlencode(params)
    for attempt in (0, 1):
        token = access_token(memory, env=env, fetch=fetch, fresh=attempt == 1)
        try:
            return gateway.call_url(memory, NAME, [url], headers={"Authorization": f"Bearer {token}"}, fetch=fetch,
                                    purpose="health", personal=True, endpoint=path)
        except gateway.ConnectError as e:
            if e.status == 401 and attempt == 0:
                continue
            raise


def _collection(memory: Memory, path: str, start: datetime, end: datetime, env=None, fetch=None) -> list[dict]:
    """Every record of one kind between two moments, page by page (25 a page). A kind the link may not read
    (401/403) gives []."""
    params, out = {"start": start.isoformat().replace("+00:00", "Z"), "end": end.isoformat().replace("+00:00", "Z"),
                   "limit": 25}, []
    for _ in range(MAX_PAGES):
        try:
            got = _get(memory, path, params, env, fetch) or {}
        except gateway.ConnectError as e:
            if e.status in (401, 403, 404):
                return out
            raise
        out += [r for r in got.get("records") or [] if isinstance(r, dict)]
        if not got.get("next_token"):
            break
        params = {**params, "nextToken": got["next_token"]}
    return out


def _local_day(iso: str, offset: str = "") -> str:
    """The day a WHOOP time falls on where the owner was ("-05:00" is WHOOP's timezone_offset)."""
    try:
        moment = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    except ValueError:
        return ""
    m = re.fullmatch(r"([+-])(\d\d):(\d\d)", str(offset or ""))
    if m:
        shift = timedelta(hours=int(m.group(2)), minutes=int(m.group(3))) * (1 if m.group(1) == "+" else -1)
        return (moment.astimezone(timezone.utc) + shift).date().isoformat()
    return moment.date().isoformat()


def _min(ms) -> int | None:
    try:
        return round(float(ms) / 60000)
    except (TypeError, ValueError):
        return None


def _num(v) -> float | None:
    return round(float(v), 2) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def days_from(records: dict[str, list[dict]]) -> dict[str, dict]:
    """WHOOP's records -> one row a day: recovery and sleep on the day you woke, strain on the day it began."""
    out: dict[str, dict] = {}

    def row(day: str) -> dict:
        return out.setdefault(day, {"day": day, "workouts": []})
    sleeps = {}
    for s in records.get("sleep", []):
        if s.get("nap") or s.get("score_state") not in (None, "SCORED"):
            continue
        day = _local_day(s.get("end"), s.get("timezone_offset"))
        score = s.get("score") or {}
        stages = score.get("stage_summary") or {}
        asleep = sum(_min(stages.get(k)) or 0 for k in ("total_light_sleep_time_milli", "total_slow_wave_sleep_time_milli",
                                                         "total_rem_sleep_time_milli"))
        if not day or not asleep or (out.get(day, {}).get("sleep_minutes") or 0) >= asleep:
            continue  # no stages, or two main sleeps on a day: keep the longer
        sleeps[s.get("id")] = day
        row(day).update(sleep_minutes=asleep or None, deep_minutes=_min(stages.get("total_slow_wave_sleep_time_milli")),
                        rem_minutes=_min(stages.get("total_rem_sleep_time_milli")),
                        light_minutes=_min(stages.get("total_light_sleep_time_milli")),
                        sleep_efficiency=_num(score.get("sleep_efficiency_percentage")),
                        sleep_performance=_num(score.get("sleep_performance_percentage")),
                        respiratory_rate=_num(score.get("respiratory_rate")))
    for r in records.get("recovery", []):
        if r.get("score_state") not in (None, "SCORED"):
            continue
        score = r.get("score") or {}
        day = sleeps.get(r.get("sleep_id")) or _local_day(r.get("created_at"))
        if day:
            row(day).update(recovery=_num(score.get("recovery_score")), resting_hr=_num(score.get("resting_heart_rate")),
                            hrv=_num(score.get("hrv_rmssd_milli")), spo2=_num(score.get("spo2_percentage")),
                            skin_temp=_num(score.get("skin_temp_celsius")))
    for c in records.get("cycle", []):
        score = c.get("score") or {}
        day = _local_day(c.get("start"), c.get("timezone_offset"))
        if day and c.get("score_state") in (None, "SCORED"):
            kj = _num(score.get("kilojoule"))
            row(day).update(strain=_num(score.get("strain")), calories=round(kj / 4.184) if kj else None,
                            avg_hr=_num(score.get("average_heart_rate")), max_hr=_num(score.get("max_heart_rate")))
    for w in records.get("workout", []):
        day = _local_day(w.get("start"), w.get("timezone_offset"))
        if day:
            minutes = None
            try:
                minutes = round((datetime.fromisoformat(str(w["end"]).replace("Z", "+00:00"))
                                 - datetime.fromisoformat(str(w["start"]).replace("Z", "+00:00"))).total_seconds() / 60)
            except (KeyError, TypeError, ValueError):
                pass
            row(day)["workouts"].append({"type": str(w.get("sport_name") or "workout").replace("_", " ")[:40],
                                         "minutes": minutes, "strain": _num((w.get("score") or {}).get("strain"))})
    return out


def sync(memory: Memory, n: int | None = None, env=None, fetch=None, kind: str = "") -> int:
    """Read the last n days (a year the first time, then 14, as WHOOP rescores recent days) and keep them."""
    from . import connecthub, healthrecords  # noqa: PLC0415
    _ready(memory)
    first = memory.db.execute("SELECT 1 FROM whoop_daily LIMIT 1").fetchone() is None
    n = n or (FIRST_DAYS if first else LATER_DAYS)
    end = datetime.now(timezone.utc) + timedelta(hours=1)
    start = end - timedelta(days=n + 1)
    with connecthub.syncing(memory, NAME, kind or ("initial" if first else "incremental")) as got:
        records = {k: _collection(memory, p, start, end, env, fetch) for k, p in (
            ("sleep", "/v2/activity/sleep"), ("recovery", "/v2/recovery"), ("cycle", "/v2/cycle"),
            ("workout", "/v2/activity/workout"))}
        rows = days_from(records)
        now = datetime.now(timezone.utc).isoformat()
        for day, d in rows.items():
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
                continue
            memory.db.execute(
                f"INSERT OR REPLACE INTO whoop_daily (day, {', '.join(FIELDS)}, workouts, updated_at) "
                f"VALUES (?, {', '.join('?' for _ in FIELDS)}, ?, ?)",
                (day, *[d.get(f) for f in FIELDS], json.dumps(d["workouts"][:20]), now))
            n_rec, n_new = healthrecords.record(memory, NAME, day, d, got["id"])
            got["records"] += n_rec
            got["new"] += n_new
            got["reached"] = max(got["reached"], day)
        memory.db.execute("DELETE FROM whoop_daily WHERE day < ?",
                          ((local_now().date() - timedelta(days=healthrecords.keep_days())).isoformat(),))
        memory.db.commit()
        healthrecords.prune(memory, NAME)
    memory.set(SYNC_KEY, now)
    return len(rows)


def sync_if_due(memory: Memory, env=None, fetch=None) -> bool:
    if not linked(memory, env):
        return False
    try:
        last = datetime.fromisoformat(memory.get(SYNC_KEY) or "2000-01-01T00:00:00+00:00")
    except ValueError:
        last = datetime(2000, 1, 1, tzinfo=timezone.utc)
    if (datetime.now(timezone.utc) - last).total_seconds() < SYNC_EVERY_S:
        return False
    try:
        sync(memory, env=env, fetch=fetch)
        return True
    except Exception as e:
        log.info("whoop sync failed: %s", type(e).__name__)
        memory.set(SYNC_KEY, datetime.now(timezone.utc).isoformat())
        return False


def sync_now(memory: Memory, env=None, fetch=None) -> str:
    if not linked(memory, env):
        return NOT_LINKED + "."
    return f"Synced {sync(memory, env=env, fetch=fetch, kind='manual')} days from WHOOP."


def days(memory: Memory, n: int = 7) -> list[dict]:
    _ready(memory)
    since = (local_now().date() - timedelta(days=n - 1)).isoformat()
    rows = [dict(r) for r in memory.db.execute("SELECT * FROM whoop_daily WHERE day >= ? ORDER BY day", (since,))]
    for r in rows:
        r["workouts"] = json.loads(r.pop("workouts") or "[]")
        r.pop("updated_at", None)
    return rows


def disconnect(memory: Memory) -> str:
    with _lock:
        memory.set(TOKENS_KEY, "")
        memory.set(LINK_KEY, "")
    return "WHOOP is disconnected from Buddy."


def forget(memory: Memory) -> None:
    _ready(memory)
    memory.db.execute("DELETE FROM whoop_daily")
    memory.db.commit()
    memory.set(SYNC_KEY, "")


def check(memory: Memory, env=None, fetch=None) -> dict:
    """Buddy Connect's Test: the profile answers (only that it does: nothing of it is kept)."""
    got = _get(memory, "/v2/user/profile/basic", {}, env, fetch) or {}
    return {"ok": bool(got)}
