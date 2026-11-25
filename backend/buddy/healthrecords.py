"""The owner's health values in one form, each with where it came from: the common record every health source
writes into (the Oura Ring and the phone's Health Connect now; lab results and other devices next).

Each record keeps:
  connector, measure, at     which source, which measure (MEASURES), which day (or time, for lab results)
  value, unit                the value in a standard unit (UCUM: /min, ms, min, kcal/d, %, Cel)
  original_value/unit        the value and unit as the source gave them
  quality                    how it was obtained: measured by a device, estimated by the maker's software (sleep
                             stages, calories, scores), entered by the owner, or a lab result. A ring is not a lab.
  loinc                      the LOINC code, only where one exactly matches what the source measures (checked
                             against LOINC and HL7's physical activity value set); blank otherwise. Oura's HRV is
                             RMSSD, so it does not take LOINC's SDNN code.
  source_ref                 the source's own row it came from (its table and day): kept beside it, unchanged
  imported_at, sync_id       when, and in which sync (connecthub's sync history)

Readers (the health plan, the Health page) still read the sources' own tables; this is the shared form lab
results will be read in. Wearable records follow their source: 90 days. Everything stays on the owner's server
and no AI model is given it (the Privacy Gateway's health rule).
"""

import json
import logging
from datetime import datetime, timedelta, timezone

from .memory import Memory
from .tools import local_now

log = logging.getLogger(__name__)

MEASURED, ESTIMATED, ENTERED, LAB = "measured", "estimated", "entered", "lab"
QUALITY = {MEASURED: "measured by a device", ESTIMATED: "estimated by the maker's software",
           ENTERED: "entered by you", LAB: "a lab result"}
MEASURES = {  # measure -> (label, UCUM unit, LOINC where it exactly matches, how it's obtained)
    "steps": ("Steps", "{steps}/d", "41950-7", MEASURED),  # Number of steps in 24 hour, Measured
    "resting_hr": ("Resting heart rate", "/min", "40443-4", MEASURED),  # Heart rate --resting
    "avg_hr": ("Average heart rate", "/min", "", MEASURED),
    "lowest_hr": ("Lowest heart rate in sleep", "/min", "", MEASURED),
    "avg_sleep_hr": ("Average heart rate in sleep", "/min", "", MEASURED),
    "hrv": ("Heart rate variability in sleep (RMSSD)", "ms", "", MEASURED),
    "temp_deviation": ("Skin temperature against your usual", "Cel", "", MEASURED),
    "spo2": ("Blood oxygen in sleep", "%", "", MEASURED),
    "calories": ("Calories burned", "kcal/d", "41979-6", ESTIMATED),  # Calories burned in 24 hour, Calculated
    "active_calories": ("Active calories", "kcal/d", "", ESTIMATED),
    "active_minutes": ("Active minutes", "min", "", ESTIMATED),
    "sleep_minutes": ("Sleep", "min", "", ESTIMATED),
    "deep_minutes": ("Deep sleep", "min", "", ESTIMATED),
    "rem_minutes": ("REM sleep", "min", "", ESTIMATED),
    "sleep_efficiency": ("Sleep efficiency", "%", "", ESTIMATED),
    "stress_minutes": ("Stressed time", "min", "", ESTIMATED),
    "recovery_minutes": ("Recovery time", "min", "", ESTIMATED),
    "readiness": ("Readiness score", "{score}", "", ESTIMATED),
    "sleep_score": ("Sleep score", "{score}", "", ESTIMATED),
    "activity_score": ("Activity score", "{score}", "", ESTIMATED),
}
FROM = {  # connector -> {its field: (measure, its own unit)}
    "health_connect": {"steps": ("steps", "steps"), "sleep_minutes": ("sleep_minutes", "minutes"),
                       "resting_hr": ("resting_hr", "bpm"), "avg_hr": ("avg_hr", "bpm"),
                       "active_minutes": ("active_minutes", "minutes"), "calories": ("calories", "kcal")},
    "oura": {"readiness": ("readiness", "score"), "sleep_score": ("sleep_score", "score"),
             "activity_score": ("activity_score", "score"), "steps": ("steps", "steps"),
             "active_calories": ("active_calories", "kcal"), "total_calories": ("calories", "kcal"),
             "sleep_minutes": ("sleep_minutes", "minutes"), "deep_minutes": ("deep_minutes", "minutes"),
             "rem_minutes": ("rem_minutes", "minutes"), "sleep_efficiency": ("sleep_efficiency", "%"),
             "hrv": ("hrv", "ms"), "lowest_hr": ("lowest_hr", "bpm"), "avg_sleep_hr": ("avg_sleep_hr", "bpm"),
             "temp_deviation": ("temp_deviation", "°C"), "stress_minutes": ("stress_minutes", "minutes"),
             "recovery_minutes": ("recovery_minutes", "minutes"), "spo2": ("spo2", "%")},
}
UNIT_WORDS = {"{steps}/d": "steps a day", "/min": "beats a minute", "ms": "milliseconds", "Cel": "°C", "%": "%",
              "kcal/d": "kcal a day", "min": "minutes", "{score}": "a score out of 100"}  # the UCUM units, said plainly
TABLES = {"health_connect": "health_daily", "oura": "oura_daily"}  # each source's own table, kept unchanged
TITLES = {"health_connect": "Phone health data (Health Connect)", "oura": "Oura Ring"}
KEEP_DAYS = {"health_connect": 90, "oura": 90}
BACKFILL_KEY = "healthrecords:backfilled_v1"
SCHEMA = """
CREATE TABLE IF NOT EXISTS health_records (      -- the owner's health values in one form, with their source
    id INTEGER PRIMARY KEY,
    connector TEXT NOT NULL, measure TEXT NOT NULL, at TEXT NOT NULL,
    value REAL NOT NULL, unit TEXT NOT NULL, original_value TEXT NOT NULL, original_unit TEXT NOT NULL,
    quality TEXT NOT NULL, loinc TEXT NOT NULL DEFAULT '', source_ref TEXT NOT NULL,
    imported_at TEXT NOT NULL, sync_id INTEGER,
    UNIQUE (connector, measure, at)
);
"""


def _ready(memory: Memory) -> None:
    memory.db.executescript(SCHEMA)


def _number(v) -> float | None:
    if isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f and abs(f) < 1e9 else None


def record(memory: Memory, connector: str, day: str, values: dict, sync_id: int | None = None) -> tuple[int, int]:
    """One day of a source's values, as (records, new). Fields the source doesn't map, and empty values, are left
    out; a value the source corrects later replaces the one held."""
    _ready(memory)
    fields = FROM[connector]
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    n = new = 0
    for field, (measure, their_unit) in fields.items():
        value = _number(values.get(field))
        if value is None:
            continue
        _, unit, loinc, quality = MEASURES[measure]
        held = memory.db.execute("SELECT 1 FROM health_records WHERE connector = ? AND measure = ? AND at = ?",
                                 (connector, measure, day)).fetchone()
        memory.db.execute(
            "INSERT INTO health_records (connector, measure, at, value, unit, original_value, original_unit, quality, "
            "loinc, source_ref, imported_at, sync_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(connector, measure, at) DO UPDATE SET value = excluded.value, "
            "original_value = excluded.original_value, imported_at = excluded.imported_at, sync_id = excluded.sync_id",
            (connector, measure, day, value, unit, json.dumps(values.get(field)), their_unit, quality, loinc,
             f"{TABLES[connector]}:{day}", now, sync_id))
        n += 1
        new += held is None
    memory.db.commit()
    return n, new


def prune(memory: Memory, connector: str) -> int:
    _ready(memory)
    cutoff = (local_now().date() - timedelta(days=KEEP_DAYS.get(connector, 90))).isoformat()
    n = memory.db.execute("DELETE FROM health_records WHERE connector = ? AND at < ?", (connector, cutoff)).rowcount
    memory.db.commit()
    return n


def forget(memory: Memory, connector: str) -> int:
    _ready(memory)
    n = memory.db.execute("DELETE FROM health_records WHERE connector = ?", (connector,)).rowcount
    memory.db.commit()
    return n


def backfill(memory: Memory) -> int:
    """Once: the days the sources already held, before this form existed."""
    if memory.get(BACKFILL_KEY):
        return 0
    _ready(memory)
    n = 0
    for connector, table in TABLES.items():
        exists = memory.db.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)).fetchone()
        if not exists:
            continue
        for row in memory.db.execute(f"SELECT * FROM {table}").fetchall():  # nosec B608: our own table names only
            n += record(memory, connector, row["day"], dict(row))[0]
    memory.set(BACKFILL_KEY, datetime.now(timezone.utc).isoformat())
    return n


def sources(memory: Memory, env=None) -> list[dict]:
    """Where the owner's health data comes from: each source's state, its syncs, what it sends (with how each
    measure is obtained), how much is held, how long it's kept, and its terms. Counts and dates, no values."""
    from . import connecthub, gateway  # noqa: PLC0415
    backfill(memory)
    rows = {r["name"]: r for r in gateway.status(memory, env) if r["name"] in FROM}
    out = []
    for connector in FROM:
        held = {r["measure"]: dict(r) for r in memory.db.execute(
            "SELECT measure, COUNT(*) AS n, MIN(at) AS first, MAX(at) AS last FROM health_records WHERE connector = ? "
            "GROUP BY measure", (connector,))}
        row, hub = rows.get(connector, {}), connecthub.SOURCES[connector]
        measures = []
        for measure in dict.fromkeys(m for m, _ in FROM[connector].values()):
            label, unit, loinc, quality = MEASURES[measure]
            measures.append({"measure": measure, "label": label, "unit": unit, "unit_words": UNIT_WORDS.get(unit, unit),
                             "loinc": loinc, "quality": quality,
                             "how": QUALITY[quality], "days": (held.get(measure) or {}).get("n", 0)})
        out.append({"connector": connector, "title": TITLES[connector], "state": row.get("state", "not_set_up"),
                    "expired": connecthub.expired(memory, connector), "last_error": row.get("last_error", ""),
                    **connecthub.last(memory, connector), "history": connecthub.history(memory, connector, 8),
                    "measures": measures, "records": sum(h["n"] for h in held.values()),
                    "first": min((h["first"] for h in held.values()), default=""),
                    "newest": max((h["last"] for h in held.values()), default=""),
                    "keep_days": KEEP_DAYS[connector], "terms": (gateway.CATALOG.get(connector) or {}).get("terms", ""),
                    "can_sync": hub["sync"], "can_disconnect": hub["disconnect"], "stop": hub["stop"]})
    return out
