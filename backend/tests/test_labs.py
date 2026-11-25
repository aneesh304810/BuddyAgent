"""Lab results: a PDF read by code (never an AI model), kept only after the owner checks it; each result against the
lab's own range; and lab ranges for Claude, only with their own switch on, of one exact form, never a value."""

import asyncio
import base64

import pytest
from fastapi.testclient import TestClient

from buddy import connecthub, healthrecords, labs, privacygate
from buddy.memory import Memory
from buddy.tools import ToolError


def pdf(lines):
    """A small real PDF with one line of text per entry (Helvetica)."""
    esc = lambda s: s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")  # noqa: E731
    body = "BT /F1 10 Tf 40 800 Td 12 TL " + " ".join(f"({esc(line)}) Tj T*" for line in lines) + " ET"
    objs = ["<< /Type /Catalog /Pages 2 0 R >>", "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 842] /Contents 4 0 R "
            "/Resources << /Font << /F1 5 0 R >> >> >>",
            f"<< /Length {len(body)} >>\nstream\n{body}\nendstream", "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    out, offsets = "%PDF-1.4\n", []
    for i, o in enumerate(objs, 1):
        offsets.append(len(out.encode("latin-1")))
        out += f"{i} 0 obj\n{o}\nendobj\n"
    xref = len(out.encode("latin-1"))
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n" + "".join(f"{o:010d} 00000 n \n" for o in offsets)
    return (out + f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n").encode("latin-1")


QUEST = ["Quest Diagnostics Incorporated", "Patient: JANE SAMPLE  DOB: 01/02/1980  Phone: 201 555 0100",
         "Collected: 11/02/2026 07:45", "Test Name In Range Out Of Range Reference Range Lab",
         "CHOLESTEROL, TOTAL 185 <200 mg/dL", "HDL CHOLESTEROL 52 > OR = 40 mg/dL",
         "TRIGLYCERIDES 98 <150 mg/dL", "LDL-CHOLESTEROL 132 H <100 mg/dL (calc)",
         "HEMOGLOBIN A1c 5.4 <5.7 % of total Hgb", "VITAMIN D,25-OH,TOTAL,IA 28 L 30-100 ng/mL",
         "GLUCOSE, URINE NEGATIVE NEGATIVE", "Address: 12 Main St Apt 4"]
LABCORP = ["Labcorp", "Date Collected: 03/15/2026",
           "Test Current Result and Flag Previous Result and Date Units Reference Interval",
           "Glucose 01 88 91 09/01/2025 mg/dL 70-99", "LDL Chol Calc (NIH) 01 118 High 132 09/01/2025 mg/dL 0-99",
           "eGFR 01 92 >59 mL/min/1.73", "TSH 02 2.150 0.450-4.500 uIU/mL", "Hemoglobin 01 14.2 13.0-17.7 g/dL"]


def test_quest_lines_are_read_by_code_with_flag_range_and_unit():
    got = labs.parse("\n".join(QUEST))
    by = {r["test"]: r for r in got["results"]}
    assert got["lab"] == "Quest Diagnostics" and got["collected"] == "2026-11-02"
    assert set(by) == {"cholesterol_total", "hdl", "triglycerides", "ldl", "hba1c", "vitamin_d"}  # urine glucose: not
    assert (by["ldl"]["value"], by["ldl"]["flag"], by["ldl"]["ref_high"], by["ldl"]["unit"]) == (132, "H", 100, "mg/dL")
    assert by["ldl"]["band"] == "above the lab's range" and by["hdl"]["ref_low"] == 40
    assert by["hdl"]["band"] == "within the lab's range" and by["hba1c"]["unit"] == "%"
    assert (by["vitamin_d"]["ref_low"], by["vitamin_d"]["ref_high"]) == (30, 100)
    assert by["vitamin_d"]["band"] == "below the lab's range"
    assert "JANE" not in str(got) and "555" not in str(got)  # the patient's details are not read


def test_labcorp_site_codes_and_previous_results_are_not_taken_for_the_result():
    got = labs.parse("\n".join(LABCORP))
    by = {r["test"]: r for r in got["results"]}
    assert got["lab"] == "Labcorp" and got["collected"] == "2026-03-15"
    assert by["glucose"]["value"] == 88 and (by["glucose"]["ref_low"], by["glucose"]["ref_high"]) == (70, 99)
    assert by["ldl"]["value"] == 118 and by["ldl"]["flag"] == "H" and by["ldl"]["band"] == "above the lab's range"
    assert by["egfr"]["ref_low"] == 59 and by["tsh"]["value"] == 2.15 and by["tsh"]["unit"] == "uIU/mL"
    assert by["hemoglobin"]["value"] == 14.2  # not taken for hemoglobin A1c


def test_names_match_exactly_so_lookalikes_are_not_taken():
    assert labs.test_of("Hemoglobin A1c") == "hba1c" and labs.test_of("HGB") == "hemoglobin"
    assert labs.test_of("Albumin/Globulin Ratio") == "" and labs.test_of("Iron Binding Capacity") == ""
    assert labs.test_of("Glucose, Urine") == "" and labs.test_of("HDL Cholesterol") == "hdl"
    assert labs.band_of(5.0, None, None, "") == "no range given" and labs.band_of(5.0, None, None, "A") == "flagged by the lab"


def _upload(m, lines, name="quest.pdf"):
    return asyncio.run(labs.read_report(m, name, pdf(lines)))


def test_nothing_is_kept_until_the_owner_confirms_and_only_what_they_ticked(tmp_path):
    m = Memory(tmp_path / "b.db")
    draft = _upload(m, QUEST)
    assert draft["collected"] == "2026-11-02" and len(draft["results"]) == 6 and "file" not in draft
    assert labs.results(m) == [] and labs.reports(m) == []
    ticked = [({**r, "value_text": "131"} if r["test"] == "ldl" else r)  # the owner corrected a misread
              for r in draft["results"] if r["test"] in ("ldl", "hdl")]
    assert labs.save(m, draft["id"], ticked, "2026-11-02") == "Kept 2 results from the report of 2026-11-02."
    by = {t["test"]: t for t in labs.results(m)}
    assert set(by) == {"ldl", "hdl"} and (by["ldl"]["latest"]["value"], by["hdl"]["latest"]["value"]) == (131, 52)
    report = labs.reports(m)[0]
    assert report["results"] == 2 and report["lab"] == "Quest Diagnostics" and report["has_file"]
    assert labs.report_file(m, report["id"])[1].startswith(b"%PDF")
    item = m.db.execute("SELECT * FROM library_items").fetchone()  # the report is in the vault
    assert item["vault"] == 1 and item["doc_type"] == labs.HEALTH_DOC and item["status"] == "stored"
    assert m.db.execute("SELECT COUNT(*) FROM library_chunks").fetchone()[0] == 0  # no text of it is kept
    rec = m.db.execute("SELECT * FROM health_records WHERE connector = 'lab_upload' AND measure = 'lab:ldl'").fetchone()
    assert rec["quality"] == "lab" and rec["at"] == "2026-11-02" and rec["source_ref"] == f"lab_reports:{report['id']}"
    assert connecthub.history(m, "lab_upload")[0]["kind"] == "upload"
    with pytest.raises(ToolError, match="no longer waiting"):
        labs.save(m, draft["id"], ticked, "2026-11-02")  # once only
    with pytest.raises(ToolError, match="already read"):
        _upload(m, QUEST)  # the same file again


def test_bad_uploads_are_refused_plainly(tmp_path):
    m = Memory(tmp_path / "b.db")
    with pytest.raises(ToolError, match="isn't a PDF"):
        asyncio.run(labs.read_report(m, "x.png", b"\x89PNG...."))
    draft = _upload(m, QUEST)
    with pytest.raises(ToolError, match="future"):
        labs.save(m, draft["id"], draft["results"], "2999-01-01")
    with pytest.raises(ToolError, match="No result was ticked"):
        labs.save(m, draft["id"], [], "2026-11-02")
    assert labs.cancel(m, draft["id"]).startswith("Nothing") and labs.reports(m) == []


def test_a_second_report_gives_the_direction_and_deleting_one_takes_its_records(tmp_path):
    m = Memory(tmp_path / "b.db")
    first = _upload(m, LABCORP, "labcorp.pdf")
    labs.save(m, first["id"], first["results"], "2026-03-15")
    second = _upload(m, QUEST)
    labs.save(m, second["id"], second["results"], "2026-11-02")
    ldl = {t["test"]: t for t in labs.results(m)}["ldl"]
    assert ldl["latest"]["value"] == 132 and ldl["earlier"][0]["value"] == 118 and ldl["direction"] == "up since the last test"
    brief = labs.brief(m).split("\n")
    assert brief[0] == labs.HEAD and "[lab range] LDL cholesterol: above the lab's range; up since the last test" in brief
    assert all(labs.allowed(line) for line in brief) and "132" not in "\n".join(brief)
    gone = labs.delete_report(m, labs.reports(m)[0]["id"])
    assert gone == "Deleted the report (and its copy in the vault) and its 6 results."
    assert m.db.execute("SELECT COUNT(*) FROM library_items").fetchone()[0] == 1
    assert m.db.execute("SELECT COUNT(*) FROM health_records WHERE source_ref LIKE 'lab_reports:%'").fetchone()[0] == 5
    view = {s["connector"]: s for s in healthrecords.sources(m)}["lab_upload"]
    assert view["state"] == "working" and view["keep_days"] is None and view["records"] == 5


def test_the_gateway_lets_a_lab_line_through_only_to_claude_and_only_with_the_switch_on():
    line = "[lab range] LDL cholesterol: above the lab's range; up since the last test"
    assert privacygate.health(line, "", ranges=True, lab_ranges=True) == []
    assert privacygate.health(line, "", ranges=True, lab_ranges=False)  # health ranges on is not lab ranges on
    assert privacygate.health(line)  # other models: never
    for bad in ("[lab range] LDL cholesterol: 132 mg/dL, above the lab's range; up since the last test",
                "[lab range] LDL cholesterol: above the lab's range; up since 11/02/2026",
                "[lab range] Jane's LDL cholesterol: above the lab's range; first result"):
        assert privacygate.health(bad, "", lab_ranges=True), bad
    assert labs.on({"BUDDY_LAB_RANGES": "on"}) and not labs.on({}) and not labs.on({"BUDDY_HEALTH_RANGES": "on"})


def test_the_health_tool_gives_claude_lab_ranges_only_when_switched_on(tmp_path, monkeypatch):
    m = Memory(tmp_path / "b.db")
    draft = _upload(m, QUEST)
    labs.save(m, draft["id"], draft["results"], "2026-11-02")
    monkeypatch.delenv("BUDDY_LAB_RANGES", raising=False)
    monkeypatch.delenv("BUDDY_HEALTH_RANGES", raising=False)
    got, withheld = privacygate.withhold("my_health", "LDL 132 mg/dL", m)
    assert withheld and "132" not in got and "[lab range]" not in got
    monkeypatch.setenv("BUDDY_LAB_RANGES", "on")
    got, withheld = privacygate.withhold("my_health", "LDL 132 mg/dL", m)
    assert withheld and got.startswith(labs.HEAD) and "132" not in got
    cleaned, cut = privacygate._clean(got, "")
    assert cut == 0 and cleaned == got  # each line is of the exact form
    assert privacygate.status(m)["lab_ranges"]["on"]


def test_the_labs_page_reads_saves_and_deletes(tmp_path, monkeypatch):
    from buddy import server
    monkeypatch.setattr(server, "memory", Memory(tmp_path / "b.db"))
    monkeypatch.setattr(server, "authorized", lambda t: t == "s3cret")
    client, auth = TestClient(server.app), {"Authorization": "Bearer s3cret"}
    data = base64.b64encode(pdf(QUEST)).decode()
    draft = client.post("/api/health/labs", json={"action": "read", "name": "q.pdf", "data": data}, headers=auth).json()["draft"]
    saved = client.post("/api/health/labs", json={"action": "save", "draft": draft["id"], "results": draft["results"],
                                                 "collected": draft["collected"]}, headers=auth).json()
    assert saved["text"].startswith("Kept 6 results")
    view = client.get("/api/health/labs", headers=auth).json()
    assert len(view["results"]) == 6 and view["ranges_on"] is False
    report = view["reports"][0]["id"]
    got = client.get(f"/api/health/labs/report/{report}", headers=auth)
    assert got.status_code == 200 and got.content.startswith(b"%PDF") and got.headers["cache-control"] == "no-store"
    assert client.post("/api/health/labs", json={"action": "delete", "report": report}, headers=auth).status_code == 400
    out = client.post("/api/health/labs", json={"action": "delete", "report": report, "confirm": True}, headers=auth).json()
    assert out["text"].startswith("Deleted the report")
    assert client.post("/api/health/labs", json={"action": "read", "data": "%%%"}, headers=auth).status_code == 400


def test_a_report_never_confirmed_is_dropped_after_a_day(tmp_path):
    import json as _json
    m = Memory(tmp_path / "b.db")
    draft = _upload(m, QUEST)
    held = _json.loads(m.get(labs.DRAFT_PREFIX + draft["id"]))
    m.set(labs.DRAFT_PREFIX + draft["id"], _json.dumps({**held, "until": "2000-01-01T00:00:00+00:00"}))
    assert labs.drop_expired(m) == 1 and m.get(labs.DRAFT_PREFIX + draft["id"]) == ""


def test_a_report_with_no_range_gets_the_typical_one_marked_as_typical():
    got = {r["test"]: r for r in labs.parse("\n".join(["Ferritin 18 ng/mL", "LDL Cholesterol 128 mg/dL",
                                                        "Glucose 5.2 mmol/L", "TSH 2.1 uIU/mL", "Sodium 141 mEq/L",
                                                        "Platelets 210 x10E3/uL", "B12 150 pg/mL"]), sex="female")["results"]}
    assert got["ldl"]["ref_source"] == "typical" and got["ldl"]["band"] == "above the typical range"
    assert got["ldl"]["ref_text"] == "typical <= 99"
    assert got["ferritin"]["band"] == "within the typical range" and got["ferritin"]["ref_low"] == 11  # women's range
    assert got["glucose"]["band"] == "within the typical range"  # the same range in mmol/L
    assert got["sodium"]["band"] == "within the typical range" and got["platelets"]["band"] == "within the typical range"
    assert got["b12"]["band"] == "below the typical range"
    assert got["tsh"]["ref_source"] == "" and got["tsh"]["band"] == "no range given"  # no typical range is guessed
    unknown = labs.parse("Ferritin 18 ng/mL", sex="")["results"][0]
    assert unknown["ref_source"] == "" and unknown["band"] == "no range given"  # differs by sex, and sex unknown


def test_the_labs_printed_range_and_flag_win_over_the_typical_one():
    printed = labs.parse("LDL Cholesterol 128 0-129 mg/dL")["results"][0]
    assert printed["ref_source"] == "lab" and printed["band"] == "within the lab's range"
    flagged = labs.parse("LDL Cholesterol 128 H mg/dL")["results"][0]
    assert flagged["ref_source"] == "lab" and flagged["band"] == "above the lab's range"
    odd_unit = labs.parse("LDL Cholesterol 3.3 mmol/L")["results"][0]
    assert odd_unit["band"] == "no range given"  # the table's unit only
    line = "[lab range] LDL cholesterol: above the typical range; first result"
    assert labs.allowed(line) and privacygate.health(line, "", lab_ranges=True) == []


def test_a_corrected_unit_brings_its_own_typical_range(tmp_path):
    m = Memory(tmp_path / "b.db")
    draft = _upload(m, ["Glucose 5.2 mmol/L"])
    row = {**draft["results"][0], "value_text": "94", "unit": "mg/dL"}
    labs.save(m, draft["id"], [row], "2026-11-02")
    latest = labs.results(m)[0]["latest"]
    assert (latest["ref_low"], latest["ref_high"], latest["ref_source"]) == (70, 99, "typical")
    assert latest["band"] == "within the typical range"


def test_the_library_tool_never_lists_or_opens_a_lab_report(tmp_path):
    from buddy import library
    m = Memory(tmp_path / "b.db")
    draft = _upload(m, QUEST)
    labs.save(m, draft["id"], draft["results"], "2026-11-02")
    lib = library.Library(m)
    vault_id = m.db.execute("SELECT vault_id FROM lab_reports").fetchone()["vault_id"]
    assert [i for i in lib.recent() if i["id"] == vault_id]  # the owner's own library screen shows it
    listed = asyncio.run(library.run_tool(lib, {"action": "list"}))
    assert vault_id not in listed
    for action in ("open", "unlock"):
        said = asyncio.run(library.run_tool(lib, {"action": action, "id": vault_id}))
        assert "health document" in said and "132" not in said
    assert lib.search("cholesterol") == []
