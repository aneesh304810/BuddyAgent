"""Checkups: the screenings that apply to the owner by fixed rules (ancestry included), what is worth asking a doctor
about, and where their numbers sit among US adults."""

import pytest
from fastapi.testclient import TestClient

from buddy import healthrecords, labs, nhanesref, prevention
from buddy.memory import Memory
from buddy.tools import ToolError, local_now

YEAR = local_now().year


def _lab(m, test, value, unit, day="2026-03-02"):
    labs._ready(m)
    m.db.execute("INSERT INTO lab_results (report_id, source, test, printed, value, value_text, unit, band, collected) "
                 "VALUES (1, 'lab_upload', ?, ?, ?, ?, ?, 'within', ?)", (test, test, value, str(value), unit, day))
    m.db.commit()


def _me(m, **over):
    answers = {"birth_year": YEAR - 45, "sex": "male", "ancestry": ["indian_kerala"], "born_high": "yes",
               "smoking": "never", "family": ["diabetes"], "height_cm": 170, "plan": "private"} | over
    prevention.save_profile(m, answers)
    healthrecords.record(m, "health_connect", "2026-11-20", {"weight_kg": 70.5})  # BMI 24.4
    return {i["key"]: i for i in prevention.view(m)["items"]}


def test_answers_are_checked_and_anything_else_dropped(tmp_path):
    m = Memory(tmp_path / "b.db")
    p = prevention.save_profile(m, {"birth_year": 1850, "sex": "other", "ancestry": ["chinese", "martian"],
                                    "family": ["diabetes", "x"], "height_cm": 9, "plan": "gold", "postmenopausal": True})
    assert p["birth_year"] is None and p["sex"] == "" and p["ancestry"] == ["chinese"] and p["family"] == ["diabetes"]
    assert p["height_cm"] is None and p["plan"] == "" and p["postmenopausal"] is False
    assert prevention.view(m)["items"] == []  # nothing until age and sex are in


def test_south_asian_ancestry_and_birth_country_change_the_rules(tmp_path):
    m = Memory(tmp_path / "b.db")
    items = _me(m)
    diabetes = items["diabetes"]
    assert "Asian Americans from 23" in " ".join(diabetes["why"]) and diabetes["cover"] == "free_ask"
    assert diabetes["grade"] == "B" and "Kerala" in " ".join(diabetes["why"])
    assert "(South Asian or Filipino) is a risk enhancer in the 2026 ACC/AHA" in " ".join(items["cholesterol"]["why"])
    assert {"hep_b", "tb", "colorectal", "hep_c", "hiv", "blood_pressure"} <= set(items)
    assert not {"breast", "cervical", "lung", "aaa", "prostate", "bone"} & set(items)  # not for a 45-year-old man

    other = Memory(tmp_path / "c.db")  # the same man of White ancestry, born in the US: BMI 24.4 is under 25
    items = _me(other, ancestry=["white"], born_high="no")
    assert "every adult from 35" in " ".join(items["diabetes"]["why"]) and items["diabetes"]["grade"] == "ADA"
    assert not {"hep_b", "tb"} & set(items)


def test_women_older_smokers_and_family_history(tmp_path):
    m = Memory(tmp_path / "b.db")
    items = _me(m, sex="female", birth_year=YEAR - 50, family=["breast_ovarian"], postmenopausal=True)
    assert {"breast", "cervical", "brca", "bone"} <= set(items) and "prostate" not in items
    asks = {a["key"]: a for a in prevention.view(m)["asks"]}
    assert asks["dna"]["status"] == "ask" and "BRCA" in asks["dna"]["text"]

    smoker = Memory(tmp_path / "c.db")
    items = _me(smoker, birth_year=YEAR - 68, smoking="former", pack_years=25, quit_year=YEAR - 10)
    assert {"lung", "aaa", "prostate", "falls"} <= set(items) and items["prostate"]["status"] == "decide"


def test_done_dates_and_lab_results_set_what_is_due(tmp_path):
    m = Memory(tmp_path / "b.db")
    _me(m)
    assert prevention.mark_done(m, "colorectal", "2025-02", "colonoscopy") == "Noted."
    _lab(m, "hba1c", 5.9, "%")
    items = {i["key"]: i for i in prevention.view(m)["items"]}
    assert items["colorectal"]["status"] == "ok" and items["colorectal"]["next"] == "2035-02"
    assert items["diabetes"]["last"] == "2026-03" and items["diabetes"]["next"] == "2027-03"  # prediabetes: yearly
    assert items["hep_c"]["status"] == "due"
    with pytest.raises(ToolError, match="isn't one"):
        prevention.mark_done(m, "telepathy", "2025-01")
    with pytest.raises(ToolError, match="hasn't come"):
        prevention.mark_done(m, "hiv", "2999-01")
    prevention.unmark(m, "colorectal")
    assert {i["key"]: i for i in prevention.view(m)["items"]}["colorectal"]["status"] == "due"


@pytest.mark.parametrize("a1c,status,words", [(6.8, "ask", "diabetes range"), (5.9, "optional", "prediabetes"),
                                               (5.2, "not_needed", "normal range"), (None, "first", "A1c first")])
def test_a_glucose_sensor_only_when_the_numbers_say_so(tmp_path, a1c, status, words):
    m = Memory(tmp_path / "b.db")
    _me(m)
    if a1c is not None:
        _lab(m, "hba1c", a1c, "%")
    cgm = {a["key"]: a for a in prevention.view(m)["asks"]}["cgm"]
    assert cgm["status"] == status and (words in cgm["text"] or status == "first")


def test_a_very_high_ldl_points_to_familial_hypercholesterolemia(tmp_path):
    m = Memory(tmp_path / "b.db")
    _me(m)
    _lab(m, "ldl", 5.2, "mmol/L")  # 201 mg/dL
    _lab(m, "lpa", 160, "nmol/L")
    asks = {a["key"]: a for a in prevention.view(m)["asks"]}
    assert asks["dna"]["status"] == "ask" and "familial hypercholesterolemia" in asks["dna"]["text"]
    assert asks["lpa"]["status"] == "ok" and "That is high" in asks["lpa"]["text"]


def test_where_numbers_sit_among_us_adults_of_asian_ancestry(tmp_path):
    pcts = nhanesref.REF["asian|male|40-59"]["a1c"][1]
    assert prevention.rank(pcts[3], pcts) == 50 and prevention.rank(0, pcts) == 2 and prevention.rank(99, pcts) == 98
    m = Memory(tmp_path / "b.db")
    _me(m, waist_cm=92)
    _lab(m, "hba1c", 5.9, "%")
    cmp = prevention.view(m)["compare"]
    a1c = next(r for r in cmp["rows"] if r["measure"] == "a1c")
    assert a1c["asian"]["who"] == "US men aged 40-59 of Asian ancestry" and 0 < a1c["asian"]["rank"] < 100
    assert a1c["all"]["who"] == "US men aged 40-59"
    assert any("27.5" in c for c in cmp["cutoffs"]) and any("90 cm" in c for c in cmp["cutoffs"])
    assert "can't be split into Indian or Chinese" in cmp["note"]


def test_every_group_in_the_table_has_seven_rising_percentiles():
    for key, measures in nhanesref.REF.items():
        for measure, (people, pcts) in measures.items():
            assert people >= 30 and len(pcts) == 7 and list(pcts) == sorted(pcts), (key, measure)


def test_the_page_saves_notes_and_forgets(tmp_path, monkeypatch):
    from buddy import server
    monkeypatch.setattr(server, "memory", Memory(tmp_path / "b.db"))
    monkeypatch.setattr(server, "authorized", lambda t: t == "s3cret")
    client, auth = TestClient(server.app), {"Authorization": "Bearer s3cret"}
    assert client.get("/api/health/checkups", headers=auth).json()["filled"] is False
    out = client.post("/api/health/checkups", json={"action": "profile", "answers": {
        "birth_year": YEAR - 40, "sex": "female", "ancestry": ["chinese"]}}, headers=auth).json()
    assert out["text"] == "Saved." and out["filled"] and any(i["key"] == "breast" for i in out["items"])
    done = client.post("/api/health/checkups", json={"action": "done", "key": "breast", "month": "2026-01"},
                       headers=auth).json()
    assert next(i for i in done["items"] if i["key"] == "breast")["status"] == "ok"
    assert client.post("/api/health/checkups", json={"action": "done", "key": "breast", "month": "soon"},
                       headers=auth).status_code == 400
    assert client.post("/api/health/checkups", json={"action": "forget"}, headers=auth).status_code == 400
    gone = client.post("/api/health/checkups", json={"action": "forget", "confirm": True}, headers=auth).json()
    assert gone["filled"] is False
