"""Buddy Connect's Health group.

Personal: daily totals the Buddy app reads from Google Health Connect on the phone (steps, sleep, resting and
average heart rate, active minutes, workouts), with the owner's permission, sent to this server only. Kept 90 days.
They are the owner's data: only Claude sees them (the tool isn't one of the public ones, so a turn that used it
can't go to a free model), and nothing here is sent anywhere.

Public: openFDA (drug labels and recalls), MedlinePlus (NIH health topics) and Open Food Facts (nutrition), through
Buddy Connect. Only the drug, topic or product goes out, never anything about the owner. General information, not
medical advice: the tool says so.
"""

import json
import re
import urllib.parse
import xml.etree.ElementTree as ET  # nosec B405: MedlinePlus' own https feed, parsed without entities
from datetime import datetime, timedelta, timezone

from . import gateway
from .memory import Memory
from .tools import ToolError, local_now

KEEP_DAYS = 90
SYNC_KEY = "health:synced"
FIELDS = ("steps", "sleep_minutes", "resting_hr", "avg_hr", "active_minutes", "calories")

SCHEMA = """
CREATE TABLE IF NOT EXISTS health_daily (       -- from Health Connect on the phone, owner's permission; 90 days
    day TEXT PRIMARY KEY,                       -- local date
    steps INTEGER, sleep_minutes INTEGER, resting_hr INTEGER, avg_hr INTEGER, active_minutes INTEGER,
    calories INTEGER, workouts TEXT NOT NULL DEFAULT '[]', updated_at TEXT NOT NULL
);
"""


def _ready(memory: Memory) -> None:
    memory.db.executescript(SCHEMA)


def _int(v):
    try:
        return None if v is None or v == "" else int(round(float(v)))
    except (TypeError, ValueError):
        return None


def ingest(memory: Memory, payload: dict) -> int:
    """Days from the app: [{"day": "2026-11-10", "steps": 8123, "sleep_minutes": 412, ...}]. Returns how many."""
    _ready(memory)
    n = 0
    now = datetime.now(timezone.utc).isoformat()
    for d in (payload or {}).get("days") or []:
        day = str(d.get("day") or "")
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
            continue
        workouts = [{"type": str(w.get("type", ""))[:40], "minutes": _int(w.get("minutes"))}
                    for w in (d.get("workouts") or [])[:20] if isinstance(w, dict)]
        values = [_int(d.get(f)) for f in FIELDS]
        memory.db.execute(
            "INSERT INTO health_daily (day, steps, sleep_minutes, resting_hr, avg_hr, active_minutes, calories, "
            "workouts, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(day) DO UPDATE SET "
            "steps = excluded.steps, sleep_minutes = excluded.sleep_minutes, resting_hr = excluded.resting_hr, "
            "avg_hr = excluded.avg_hr, active_minutes = excluded.active_minutes, calories = excluded.calories, "
            "workouts = excluded.workouts, updated_at = excluded.updated_at",
            (day, *values, json.dumps(workouts), now))
        n += 1
    cutoff = (local_now().date() - timedelta(days=KEEP_DAYS)).isoformat()
    memory.db.execute("DELETE FROM health_daily WHERE day < ?", (cutoff,))
    memory.db.commit()
    memory.set(SYNC_KEY, now)
    gateway.report(memory, "health_connect", True)
    return n


def days(memory: Memory, n: int = 7) -> list[dict]:
    _ready(memory)
    since = (local_now().date() - timedelta(days=n - 1)).isoformat()
    rows = [dict(r) for r in memory.db.execute("SELECT * FROM health_daily WHERE day >= ? ORDER BY day", (since,))]
    for r in rows:
        r["workouts"] = json.loads(r["workouts"] or "[]")
    return rows


def summary(memory: Memory, n: int = 7) -> dict:
    rows = days(memory, n)

    def avg(field):
        vals = [r[field] for r in rows if r.get(field)]
        return round(sum(vals) / len(vals)) if vals else None
    out = {"days": len(rows), "from": rows[0]["day"] if rows else "", "to": rows[-1]["day"] if rows else "",
           **{f"avg_{f}": avg(f) for f in FIELDS},
           "workouts": [{"day": r["day"], **w} for r in rows for w in r["workouts"]][-10:],
           "daily": [{k: r[k] for k in ("day", *FIELDS)} for r in rows][-14:],
           "synced": memory.get(SYNC_KEY) or ""}
    return out


# --- public health information through Buddy Connect --------------------------------------------------------
def _q(text: str) -> str:
    return re.sub(r"[^\w\s-]", " ", text or "").strip()[:80]


def drug(memory, name: str, fetch=None) -> dict:
    term = _q(name)
    if not term:
        raise ToolError("Which medicine?")
    search = f'openfda.brand_name:"{term}"+openfda.generic_name:"{term}"'
    got = gateway.call(memory, "openfda", "/drug/label.json?search=" + urllib.parse.quote(search, safe=":+")
                       + "&limit=1", fetch=fetch, purpose="health info")
    label = ((got or {}).get("results") or [{}])[0]

    def first(key, limit=700):
        v = label.get(key) or []
        return " ".join(str(v[0]).split())[:limit] if v else ""
    of = label.get("openfda") or {}
    return {"name": ", ".join((of.get("brand_name") or [term])[:2]),
            "generic": ", ".join((of.get("generic_name") or [])[:2]),
            "used_for": first("indications_and_usage"), "warnings": first("warnings") or first("boxed_warning"),
            "side_effects": first("adverse_reactions", 500), "interactions": first("drug_interactions", 500),
            "source": "openFDA drug label (US FDA)"}


def recalls(memory, name: str, fetch=None) -> list[dict]:
    term = _q(name)
    got = gateway.call(memory, "openfda", "/drug/enforcement.json?search=" +
                       urllib.parse.quote(f'product_description:"{term}"', safe=":") + "&sort=report_date:desc&limit=3",
                       fetch=fetch, purpose="health info")
    return [{"date": r.get("report_date", ""), "reason": " ".join(str(r.get("reason_for_recall", "")).split())[:300],
             "status": r.get("status", ""), "product": " ".join(str(r.get("product_description", "")).split())[:200]}
            for r in (got or {}).get("results") or []]


def topic(memory, term: str, fetch=None) -> dict:
    words = _q(term)
    if not words:
        raise ToolError("Which health topic?")
    raw = gateway.call(memory, "medlineplus", "/ws/query?" + urllib.parse.urlencode(
        {"db": "healthTopics", "term": words, "retmax": 1}), raw=True, fetch=fetch, purpose="health info")
    text = raw.decode("utf-8", "replace") if isinstance(raw, (bytes, bytearray)) else str(raw)
    if "<!ENTITY" in text or "<!DOCTYPE" in text:  # never parse entity declarations
        raise ToolError("MedlinePlus sent something unexpected.")
    doc = ET.fromstring(text).find(".//document")  # nosec B314: checked above, no entities or doctype
    if doc is None:
        return {"found": False}
    fields = {c.get("name"): "".join(c.itertext()) for c in doc.findall("content")}
    clean = lambda s: " ".join(re.sub(r"<[^>]+>", " ", s or "").split())  # noqa: E731
    return {"found": True, "title": clean(fields.get("title")), "summary": clean(fields.get("FullSummary"))[:1500],
            "url": doc.get("url", ""), "source": "MedlinePlus (NIH, U.S. National Library of Medicine)"}


def food(memory, what: str, fetch=None) -> dict:
    code = re.sub(r"\D", "", what or "")
    if len(code) >= 8 and len(code) == len((what or "").strip()):
        got = gateway.call(memory, "openfoodfacts", f"/api/v2/product/{code}.json", fetch=fetch, purpose="health info")
        product = (got or {}).get("product") or {}
    else:
        got = gateway.call(memory, "openfoodfacts", "/cgi/search.pl?" + urllib.parse.urlencode(
            {"search_terms": _q(what), "json": 1, "page_size": 1, "search_simple": 1}), fetch=fetch,
            purpose="health info")
        product = ((got or {}).get("products") or [{}])[0]
    if not product:
        return {"found": False}
    n = product.get("nutriments") or {}
    return {"found": True, "name": product.get("product_name") or "", "brand": product.get("brands") or "",
            "per_100g": {k: n.get(f"{k}_100g") for k in ("energy-kcal", "sugars", "fat", "saturated-fat", "salt",
                                                          "proteins", "fiber") if n.get(f"{k}_100g") is not None},
            "nutriscore": product.get("nutriscore_grade") or "", "allergens": product.get("allergens_tags") or [],
            "source": "Open Food Facts"}


MY_HEALTH_TOOL = {
    "name": "my_health",
    "description": "The user's own health data from their phone (Google Health Connect, read by the Buddy app with "
    "their permission): daily steps, sleep, resting and average heart rate, active minutes, calories and workouts, "
    "and the averages over the last days. Personal: answer from it plainly, never as medical advice.",
    "input_schema": {"type": "object", "properties": {
        "days": {"type": "integer", "description": "How many days back (1-90, default 7)"}},
        "additionalProperties": False},
}

HEALTH_INFO_TOOL = {
    "name": "health_info",
    "description": "Public health information through Buddy Connect: drug (what a medicine is for, warnings, side "
    "effects, interactions, from the US FDA label), recalls (FDA drug recalls), topic (a condition or lab test "
    "explained by MedlinePlus, NIH), food (nutrition and allergens by barcode or product name, Open Food Facts). "
    "Only the drug, topic or product is sent. General information, not medical advice: say so, and suggest asking "
    "a doctor or pharmacist for decisions.",
    "input_schema": {"type": "object", "properties": {
        "action": {"type": "string", "enum": ["drug", "recalls", "topic", "food"]},
        "query": {"type": "string"}}, "required": ["action", "query"], "additionalProperties": False},
}


def run_my_health(memory: Memory, args: dict) -> str:
    n = max(1, min(int(args.get("days") or 7), KEEP_DAYS))
    s = summary(memory, n)
    if not s["days"]:
        return ("No health data from the phone yet. In the Buddy app: Menu > Connections > Health > Phone health "
                "data > Connect, and allow steps, sleep and heart rate in Health Connect.")
    return json.dumps(s)


def run_health_info(memory: Memory, args: dict) -> str:
    try:
        return _health_info(memory, args)
    except gateway.ConnectError as e:
        if e.status == 404:  # openFDA answers 404 when nothing matches
            return f"Nothing found for {args.get('query', '')!r} there."
        raise


def _health_info(memory: Memory, args: dict) -> str:
    action, query = args.get("action"), args.get("query", "")
    note = " (General information, not medical advice.)"
    if action == "drug":
        return json.dumps(drug(memory, query)) + note
    if action == "recalls":
        found = recalls(memory, query)
        return (json.dumps(found) if found else f"No FDA drug recalls found for {query}.") + note
    if action == "topic":
        return json.dumps(topic(memory, query)) + note
    if action == "food":
        return json.dumps(food(memory, query))
    raise ToolError("Use drug, recalls, topic or food.")
