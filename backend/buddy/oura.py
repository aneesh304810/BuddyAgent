"""The owner's Oura Ring, through Buddy Connect (Health group) and Oura's API v2.

Read only. Each day: the Readiness, Sleep and Activity scores, the night's sleep (total, deep, REM, efficiency, HRV,
lowest heart rate, bedtime and wake time), temperature deviation, steps and active calories, stress and recovery
time, resilience, blood oxygen and workouts. Kept 90 days in the encrypted database, like the phone's Health Connect
data. Personal: only Claude sees it (never the free models), and nothing about the owner is sent anywhere else.

Linked once with deploy/oura.sh (Oura's sign-in, OAuth). Oura's refresh tokens work once: every renewal returns a new
one, which is saved here at once (the first comes from the server's settings, BUDDY_OURA_REFRESH_TOKEN). Running
oura.sh again starts over from the new link.
"""

import hashlib
import json
import re
import threading
import time
import urllib.parse
from datetime import datetime, timedelta, timezone

from . import gateway
from .memory import Memory
from .tools import local_now

API = "/v2/usercollection/"
TOKEN_URL = "https://api.ouraring.com/oauth/token"
TOKENS_KEY = "oura:tokens"     # {"seed", "refresh", "access", "until"}: the current (rotated) tokens
SYNC_KEY = "oura:synced"
PROFILE_KEY = "oura:profile"   # age, weight, height, sex from Oura (for calorie and protein targets)
KEEP_DAYS = 90
SYNC_EVERY_S = 3 * 3600
FIELDS = ("readiness", "sleep_score", "activity_score", "steps", "active_calories", "total_calories",
          "sleep_minutes", "deep_minutes", "rem_minutes", "sleep_efficiency", "hrv", "lowest_hr", "avg_sleep_hr",
          "temp_deviation", "stress_minutes", "recovery_minutes", "resilience", "spo2", "bedtime", "wake")
SCHEMA = """
CREATE TABLE IF NOT EXISTS oura_daily (          -- from the owner's Oura Ring; 90 days
    day TEXT PRIMARY KEY,
    readiness INTEGER, sleep_score INTEGER, activity_score INTEGER, steps INTEGER, active_calories INTEGER,
    total_calories INTEGER, sleep_minutes INTEGER, deep_minutes INTEGER, rem_minutes INTEGER,
    sleep_efficiency INTEGER, hrv INTEGER, lowest_hr INTEGER, avg_sleep_hr INTEGER, temp_deviation REAL,
    stress_minutes INTEGER, recovery_minutes INTEGER, resilience TEXT, spo2 REAL, bedtime TEXT, wake TEXT,
    workouts TEXT NOT NULL DEFAULT '[]', updated_at TEXT NOT NULL
);
"""
_lock = threading.Lock()  # one renewal at a time: a refresh token used twice is lost


def _ready(memory: Memory) -> None:
    memory.db.executescript(SCHEMA)


def linked(env=None) -> bool:
    return gateway.configured("oura", env)


def _seed(env) -> str:
    return hashlib.sha256(gateway._secret(env, "BUDDY_OURA_REFRESH_TOKEN").encode()).hexdigest()[:16]


def access_token(memory: Memory, env=None, fetch=None, fresh: bool = False) -> str:
    """A current access token: kept until a minute before it ends, then renewed with the refresh token, whose
    replacement is saved before anything else happens."""
    if not linked(env):
        raise gateway.ConnectError("oura", "The Oura Ring isn't linked yet: sudo bash /opt/buddy/deploy/oura.sh")
    with _lock:
        try:
            saved = json.loads(memory.get(TOKENS_KEY) or "{}")
        except ValueError:
            saved = {}
        if saved.get("seed") != _seed(env):  # oura.sh was run (again): start from the new link
            saved = {"seed": _seed(env), "refresh": gateway._secret(env, "BUDDY_OURA_REFRESH_TOKEN")}
        if saved.get("access") and saved.get("until", 0) > time.time() + 60 and not fresh:
            return saved["access"]
        body = urllib.parse.urlencode({
            "grant_type": "refresh_token", "refresh_token": saved["refresh"],
            "client_id": gateway._secret(env, "BUDDY_OURA_CLIENT_ID"),
            "client_secret": gateway._secret(env, "BUDDY_OURA_CLIENT_SECRET")}).encode()
        try:
            got = gateway.call_url(memory, "oura", [TOKEN_URL], data=body, endpoint="/oauth/token", fetch=fetch,
                                   headers={"Content-Type": "application/x-www-form-urlencoded"})
        except gateway.ConnectError as e:
            if e.status in (400, 401):
                raise gateway.ConnectError("oura", f"Oura no longer accepts Buddy's link ({e.reason or e.status}): "
                                                   "link it again with sudo bash /opt/buddy/deploy/oura.sh",
                                           e.status, e.reason) from e
            raise
        got = got or {}
        if not got.get("access_token"):
            raise gateway.ConnectError("oura", "Oura didn't give an access token.")
        saved.update(access=got["access_token"], until=time.time() + int(got.get("expires_in") or 3600),
                     refresh=got.get("refresh_token") or saved["refresh"])
        memory.set(TOKENS_KEY, json.dumps(saved))
        return saved["access"]


def _get(memory: Memory, what: str, params: dict | None = None, env=None, fetch=None):
    path = API + what + (("?" + urllib.parse.urlencode(params)) if params else "")
    for attempt in (0, 1):
        token = access_token(memory, env=env, fetch=fetch, fresh=attempt == 1)
        try:
            return gateway.call(memory, "oura", path, headers={"Authorization": f"Bearer {token}"}, env=env,
                                fetch=fetch, purpose="health", personal=True)
        except gateway.ConnectError as e:
            if e.status == 401 and attempt == 0:
                continue
            raise


def _collection(memory: Memory, what: str, start: str, end: str, env=None, fetch=None) -> list[dict]:
    """Every record of one kind between two days (following Oura's pages). A kind the link wasn't allowed to read
    (Oura answers 401/403 for a scope not granted) gives []: the rest still syncs."""
    params, out = {"start_date": start, "end_date": end}, []
    for _ in range(10):
        try:
            got = _get(memory, what, params, env=env, fetch=fetch) or {}
        except gateway.ConnectError as e:
            if e.status in (401, 403, 404):
                return out
            raise
        out += got.get("data") or []
        if not got.get("next_token"):
            break
        params = {**params, "next_token": got["next_token"]}
    return out


def _min(seconds) -> int | None:
    return None if seconds in (None, "") else round(float(seconds) / 60)


def _hm(iso: str) -> str:
    """'2026-11-10T23:41:00-05:00' -> '23:41' (the ring's own local time)."""
    m = re.search(r"T(\d\d:\d\d)", iso or "")
    return m.group(1) if m else ""


def days_from(records: dict[str, list[dict]]) -> dict[str, dict]:
    """Oura's collections -> one row per day."""
    out: dict[str, dict] = {}

    def row(day: str) -> dict:
        return out.setdefault(day, {"day": day, "workouts": []})
    for r in records.get("daily_readiness", []):
        d = row(r["day"])
        d["readiness"] = r.get("score")
        d["temp_deviation"] = r.get("temperature_deviation")
    for r in records.get("daily_sleep", []):
        row(r["day"])["sleep_score"] = r.get("score")
    for r in records.get("daily_activity", []):
        d = row(r["day"])
        d.update(activity_score=r.get("score"), steps=r.get("steps"), active_calories=r.get("active_calories"),
                 total_calories=r.get("total_calories"))
    for r in records.get("sleep", []):  # the main night only (naps are "sleep" or "late_nap")
        if r.get("type") not in ("long_sleep", None):
            continue
        d = row(r["day"])
        if (d.get("sleep_minutes") or 0) >= (_min(r.get("total_sleep_duration")) or 0):
            continue  # two long sleeps on one day: keep the longer
        d.update(sleep_minutes=_min(r.get("total_sleep_duration")), deep_minutes=_min(r.get("deep_sleep_duration")),
                 rem_minutes=_min(r.get("rem_sleep_duration")), sleep_efficiency=r.get("efficiency"),
                 hrv=r.get("average_hrv"), lowest_hr=r.get("lowest_heart_rate"),
                 avg_sleep_hr=round(r["average_heart_rate"]) if r.get("average_heart_rate") else None,
                 bedtime=_hm(r.get("bedtime_start")), wake=_hm(r.get("bedtime_end")))
    for r in records.get("daily_stress", []):
        row(r["day"]).update(stress_minutes=_min(r.get("stress_high")), recovery_minutes=_min(r.get("recovery_high")))
    for r in records.get("daily_resilience", []):
        row(r["day"])["resilience"] = r.get("level")
    for r in records.get("daily_spo2", []):
        row(r["day"])["spo2"] = (r.get("spo2_percentage") or {}).get("average")
    for r in records.get("workout", []):
        row(r["day"])["workouts"].append({
            "type": str(r.get("activity") or "workout").replace("_", " ")[:40],
            "minutes": _minutes_between(r.get("start_datetime"), r.get("end_datetime")),
            "intensity": r.get("intensity") or "", "calories": round(r["calories"]) if r.get("calories") else None})
    return out


def _minutes_between(a, b) -> int | None:
    try:
        return round((datetime.fromisoformat(b) - datetime.fromisoformat(a)).total_seconds() / 60)
    except (TypeError, ValueError):
        return None


KINDS = ("daily_readiness", "daily_sleep", "daily_activity", "sleep", "daily_stress", "daily_resilience",
         "daily_spo2", "workout")


def sync(memory: Memory, n: int = 14, env=None, fetch=None) -> int:
    """Read the last n days from Oura and keep them. Returns how many days came."""
    _ready(memory)
    today = local_now().date()
    start, end = (today - timedelta(days=n - 1)).isoformat(), (today + timedelta(days=1)).isoformat()
    records = {k: _collection(memory, k, start, end, env=env, fetch=fetch) for k in KINDS}
    rows = days_from(records)
    now = datetime.now(timezone.utc).isoformat()
    for day, d in rows.items():
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
            continue
        memory.db.execute(
            f"INSERT OR REPLACE INTO oura_daily (day, {', '.join(FIELDS)}, workouts, updated_at) "
            f"VALUES (?, {', '.join('?' for _ in FIELDS)}, ?, ?)",
            (day, *[d.get(f) for f in FIELDS], json.dumps(d["workouts"][:20]), now))
    memory.db.execute("DELETE FROM oura_daily WHERE day < ?", ((today - timedelta(days=KEEP_DAYS)).isoformat(),))
    memory.db.commit()
    memory.set(SYNC_KEY, now)
    return len(rows)


def sync_if_due(memory: Memory, env=None, fetch=None) -> bool:
    """A sync when it's been SYNC_EVERY_S since the last one (and Oura is linked). Never raises."""
    if not linked(env):
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
    except Exception as e:  # the next look tries again; Connections shows the error
        import logging  # noqa: PLC0415
        logging.getLogger(__name__).info("oura sync failed: %s", e)
        memory.set(SYNC_KEY, datetime.now(timezone.utc).isoformat())  # don't hammer a failing link
        return False


def check(memory: Memory, env=None, fetch=None) -> dict:
    """Buddy Connect's Test: Oura's personal info (age, weight, height), kept for the health plan's targets."""
    got = _get(memory, "personal_info", env=env, fetch=fetch) or {}
    profile = {k: got.get(k) for k in ("age", "weight", "height", "biological_sex") if got.get(k) is not None}
    if memory is not None and profile:
        memory.set(PROFILE_KEY, json.dumps(profile))
    return profile


def profile(memory: Memory) -> dict:
    try:
        return json.loads(memory.get(PROFILE_KEY) or "{}")
    except ValueError:
        return {}


def days(memory: Memory, n: int = 7) -> list[dict]:
    _ready(memory)
    since = (local_now().date() - timedelta(days=n - 1)).isoformat()
    rows = [dict(r) for r in memory.db.execute("SELECT * FROM oura_daily WHERE day >= ? ORDER BY day", (since,))]
    for r in rows:
        r["workouts"] = json.loads(r.pop("workouts") or "[]")
        r.pop("updated_at", None)
    return rows


def summary(memory: Memory, n: int = 7) -> dict:
    """The last n days and their averages, for Buddy (and the health plan)."""
    rows = days(memory, n)
    if not rows:
        return {}

    def avg(field):
        vals = [r[field] for r in rows if isinstance(r.get(field), (int, float))]
        return round(sum(vals) / len(vals), 1) if vals else None
    return {"days": len(rows), "today": rows[-1],
            **{f"avg_{f}": avg(f) for f in ("readiness", "sleep_score", "activity_score", "steps", "sleep_minutes",
                                            "hrv", "lowest_hr", "temp_deviation")},
            "daily": rows[-14:], "synced": memory.get(SYNC_KEY) or ""}


def forget(memory: Memory) -> None:
    _ready(memory)
    memory.db.execute("DELETE FROM oura_daily")
    memory.db.commit()
    for k in (SYNC_KEY, PROFILE_KEY):
        memory.set(k, "")
