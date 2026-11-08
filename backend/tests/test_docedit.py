"""Documents: PDF and Excel made from an outline; Word, Excel and PDF edited into a new copy."""

import asyncio
import io
import json

import pytest

from buddy import docedit, makers
from buddy.memory import Memory
from buddy.tools import ToolError


@pytest.fixture
def mem(tmp_path, monkeypatch):
    monkeypatch.setattr(makers, "FILES_DIR", tmp_path)
    monkeypatch.setattr(makers, "_show", lambda card: "c1")
    return Memory(":memory:")


def test_a_pdf_and_a_spreadsheet_with_a_formula_are_made():
    pdf = docedit.build_pdf({"title": "Trip plan", "sections": [
        {"heading": "Day 1", "paragraphs": ["Fly to Lima"], "bullets": ["Hotel in Miraflores"],
         "table": {"columns": ["Item", "Cost"], "rows": [["Flight", "420"]]}}]})
    assert pdf.startswith(b"%PDF")
    from openpyxl import load_workbook
    xlsx = docedit.build_xlsx({"sheets": [{"name": "Budget", "columns": ["Item", "Cost"],
                                           "rows": [["Rent", "1800"], ["Food", "450.5"], ["Total", "=SUM(B2:B3)"]]}]})
    ws = load_workbook(io.BytesIO(xlsx))["Budget"]
    assert ws["B2"].value == 1800 and ws["B3"].value == 450.5 and ws["B4"].value == "=SUM(B2:B3)"
    with pytest.raises(ToolError):
        docedit.build_xlsx({"sheets": [{"rows": [["=WEBSERVICE(\"http://x\")"]]}]})


def test_editing_a_word_file_makes_an_edited_copy(mem):
    from docx import Document
    doc = Document()
    doc.add_paragraph("Dear Sir, the rent is $1,800.")
    doc.add_paragraph("Draft: remove me")
    buf = io.BytesIO()
    doc.save(buf)
    original = makers.save(mem, buf.getvalue(), "Letter", "docx", "document")
    out = json.loads(asyncio.run(docedit.run_edit_tool(mem, {"source": original["id"], "ops": [
        {"op": "replace", "find": "$1,800", "with": "$1,900"}, {"op": "remove", "containing": "Draft"},
        {"op": "add_section", "heading": "Notes", "bullets": ["Paid on the 1st"]}]})))
    path, _ = makers.find(mem, out["file_id"])
    text = "\n".join(p.text for p in Document(str(path)).paragraphs)
    assert "$1,900" in text and "Draft" not in text and "Paid on the 1st" in text
    assert "$1,800" in "\n".join(p.text for p in Document(str(makers.find(mem, original["id"])[0])).paragraphs)


def test_excel_cells_and_rows_are_edited(mem):
    data = docedit.build_xlsx({"sheets": [{"name": "S", "columns": ["A", "B"], "rows": [["x", "1"]]}]})
    f = makers.save(mem, data, "Sheet", "xlsx", "document")
    out = json.loads(asyncio.run(docedit.run_edit_tool(mem, {"source": f["id"], "ops": [
        {"op": "set", "cells": {"B3": "=B2*2"}}, {"op": "add_rows", "rows": [["y", "5"]]}]})))
    from openpyxl import load_workbook
    ws = load_workbook(str(makers.find(mem, out["file_id"])[0]))["S"]
    assert ws["B3"].value == "=B2*2" and ws.max_row >= 3
