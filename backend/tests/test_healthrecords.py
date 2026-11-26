"""The owner's health values in one form with their source: standard units, the source's own value and unit kept,
how each was obtained (a ring is not a lab), LOINC only where it exactly matches, and the days held before."""

from datetime import timedelta

import pytest

from buddy import connecthub, gateway, health_data, healthrecords, oura
from buddy.memory import Memory
from buddy.tools import local_now

from .test_oura import DATA, ENV, TODAY, Oura, _connect


@pytest.fixture(autouse=True)
def _fresh_limits():
    gateway._recent.clear()


def _day(n):
    return (local_now().date() - timedelta(days=n)).isoformat()


def rows(m, connector):
    return {r["measure"]: dict(r) for r in m.db.execute("SELECT * FROM health_records WHERE connector = ?", (connector,))}


def test_phone_days_become_records_with_their_source_and_how_they_were_obtained(tmp_path):
    m = Memory(tmp_path / "b.db")
    health_data.ingest(m, {"days": [{"day": _day(0), "steps": 8123, "resting_hr": 58, "calories": 2210,
                                     "sleep_minutes": 412}]})
    got = rows(m, "health_connect")
    assert set(got) == {"steps", "resting_hr", "calories", "sleep_minutes"}  # empty fields are left out
    steps = got["steps"]
    assert steps["value"] == 8123 and steps["unit"] == "{steps}/d" and steps["original_unit"] == "steps"
    assert steps["loinc"] == "41950-7" and steps["quality"] == "measured" and steps["source_ref"] == f"health_daily:{_day(0)}"
    assert got["resting_hr"]["unit"] == "/min" and got["resting_hr"]["original_unit"] == "bpm"
    assert got["resting_hr"]["loinc"] == "40443-4"
    assert got["calories"]["loinc"] == "41979-6" and got["calories"]["quality"] == "estimated"
    assert got["sleep_minutes"]["quality"] == "estimated" and got["sleep_minutes"]["loinc"] == ""
    sync = connecthub.history(m, "health_connect")[0]
    assert sync["kind"] == "phone" and sync["records"] == 4 and sync["new_records"] == 4 and steps["sync_id"] == sync["id"]


def test_a_corrected_day_replaces_the_value_and_counts_as_not_new(tmp_path):
    m = Memory(tmp_path / "b.db")
    health_data.ingest(m, {"days": [{"day": _day(1), "steps": 5000}]})
    health_data.ingest(m, {"days": [{"day": _day(1), "steps": 9000}]})
    assert rows(m, "health_connect")["steps"]["value"] == 9000
    assert [h["new_records"] for h in connecthub.history(m, "health_connect")] == [0, 1]


def test_the_first_oura_sync_reads_a_year_and_keeps_scores_as_estimates(tmp_path):
    m = Memory(tmp_path / "b.db")
    net = Oura(DATA, refuse={"daily_resilience", "daily_spo2"})
    _connect(m, net)
    oura.sync(m, env=ENV, fetch=net)
    first = [u for u in net.calls if "daily_readiness" in u][0]
    assert f"start_date={(local_now().date() - timedelta(days=oura.FIRST_DAYS - 1)).isoformat()}" in first
    got = rows(m, "oura")
    assert got["readiness"]["value"] == 64 and got["readiness"]["quality"] == "estimated"
    assert got["readiness"]["unit"] == "{score}" and got["calories"]["value"] == 2300  # Oura's total calories
    assert got["hrv"]["loinc"] == "" and got["hrv"]["quality"] == "measured"  # RMSSD: not LOINC's SDNN code
    assert got["temp_deviation"]["original_unit"] == "°C" and got["temp_deviation"]["unit"] == "Cel"
    assert connecthub.history(m, "oura")[0]["kind"] == "initial"
    net.calls.clear()
    oura.sync(m, env=ENV, fetch=net)
    later = [u for u in net.calls if "daily_readiness" in u][0]
    assert f"start_date={(local_now().date() - timedelta(days=13)).isoformat()}" in later
    assert connecthub.history(m, "oura")[0]["kind"] == "incremental" and connecthub.history(m, "oura")[0]["new_records"] == 0


def test_every_mapped_field_has_a_measure_and_the_loinc_codes_are_the_checked_ones():
    for connector, fields in healthrecords.FROM.items():
        for field, (measure, unit) in fields.items():
            assert measure in healthrecords.MEASURES and unit, (connector, field)
    codes = {m: v[2] for m, v in healthrecords.MEASURES.items() if v[2]}
    assert codes == {"steps": "41950-7", "resting_hr": "40443-4", "calories": "41979-6",  # HL7 physical activity
                     "respiratory_rate": "9279-1", "weight": "29463-7", "bp_systolic": "8480-6",  # FHIR vital signs
                     "bp_diastolic": "8462-4"}
    assert {v[3] for v in healthrecords.MEASURES.values()} <= set(healthrecords.QUALITY)


def test_days_held_before_are_filled_in_once(tmp_path):
    m = Memory(tmp_path / "b.db")
    health_data._ready(m)
    m.db.execute("INSERT INTO health_daily (day, steps, resting_hr, workouts, updated_at) VALUES (?, 7000, 61, '[]', 'x')",
                 (_day(3),))
    m.db.commit()
    assert healthrecords.backfill(m) == 2 and healthrecords.backfill(m) == 0
    assert rows(m, "health_connect")["steps"]["sync_id"] is None  # from before the sync history existed


def test_old_records_follow_their_source_out(tmp_path):
    m = Memory(tmp_path / "b.db")
    health_data.ingest(m, {"days": [{"day": _day(0), "steps": 1}, {"day": _day(80), "steps": 2}]})
    healthrecords._ready(m)
    m.db.execute("UPDATE health_records SET at = ? WHERE value = 2", (_day(800),))  # past the two years
    m.db.commit()
    assert healthrecords.prune(m, "health_connect") == 1


def test_the_sources_view_has_counts_and_dates_but_no_values(tmp_path):
    m = Memory(tmp_path / "b.db")
    health_data.ingest(m, {"days": [{"day": TODAY, "steps": 8123, "resting_hr": 58}]})
    view = {s["connector"]: s for s in healthrecords.sources(m, ENV)}
    phone = view["health_connect"]
    assert phone["records"] == 2 and phone["newest"] == TODAY and phone["keep_days"] == 730
    assert phone["last"]["ok"] == 1 and not phone["can_sync"] and "Health Connect" in phone["stop"]
    assert {x["measure"]: x["days"] for x in phone["measures"]}["steps"] == 1
    assert "8123" not in str(phone) and "58.0" not in str(phone)
    assert view["oura"]["state"] == "not_set_up" and view["oura"]["records"] == 0


def test_how_long_readings_are_kept_is_one_setting():
    assert healthrecords.keep_days({}) == 730  # two years when unset
    assert healthrecords.keep_days({"BUDDY_HEALTH_KEEP_DAYS": "365"}) == 365
    assert healthrecords.keep_days({"BUDDY_HEALTH_KEEP_DAYS": "5"}) == 30 and healthrecords.keep_days(
        {"BUDDY_HEALTH_KEEP_DAYS": "lots"}) == 730


def test_the_phone_sends_what_garmin_fitbit_and_other_apps_wrote_and_which_apps(tmp_path):
    m = Memory(tmp_path / "b.db")
    health_data.ingest(m, {"days": [{"day": TODAY, "steps": 9000, "hrv": 41.5, "spo2": 97.2, "weight_kg": 78.4,
                                     "bp_systolic": 121, "bp_diastolic": 79, "glucose_mgdl": 96, "vo2max": 44.1,
                                     "respiratory_rate": 14.2, "body_fat": "21.5",
                                     "apps": ["com.garmin.android.apps.connectmobile", "com.fitbit.FitbitMobile",
                                              "bad name; drop table"]}]})
    got = rows(m, "health_connect")
    assert got["weight"]["value"] == 78.4 and got["weight"]["loinc"] == "29463-7" and got["weight"]["unit"] == "kg"
    assert got["bp_systolic"]["loinc"] == "8480-6" and got["bp_diastolic"]["value"] == 79
    assert got["hrv_any"]["value"] == 41.5 and got["vo2max"]["quality"] == "estimated" and got["body_fat"]["value"] == 21.5
    phone = {s["connector"]: s for s in healthrecords.sources(m, ENV)}["health_connect"]
    assert phone["apps"] == ["Garmin Connect", "Fitbit (and Pixel Watch)"]  # an odd package name is dropped
    day = m.db.execute("SELECT weight_kg, apps FROM health_daily").fetchone()
    assert day["weight_kg"] == 78.4 and "garmin" in day["apps"]
