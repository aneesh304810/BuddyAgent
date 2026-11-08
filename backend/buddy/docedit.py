"""Documents Buddy makes and edits: Word, PDF and Excel, beside the Word and PowerPoint in makers.py.

  make_document (format pdf | xlsx)   a PDF from the same outline as a Word document; a spreadsheet from sheets
                                      (columns, rows, formulas such as =SUM(B2:B9))
  edit_document                       change a file the user gave Buddy (library) or one Buddy made (file id):
      Word   replace text, add a section, remove paragraphs containing some words
      Excel  set cells (values or formulas), add rows, add or rename a sheet
      PDF    fill its form fields, merge other PDFs after it, keep only some pages, rotate pages
    The original is never changed: the edited copy is a new file, shown in the chat as a card.

Claude gives structured content and Buddy builds the file in code: nothing here runs code from a model. Files in
the user's locked (vault) library stay locked until the user unlocks them.
"""

import io
import json
import re

from .memory import Memory
from .tools import ToolError

MAX_ROWS, MAX_OPS = 5000, 200
CELL = re.compile(r"^[A-Z]{1,3}[1-9][0-9]{0,6}$")


# --- making ---------------------------------------------------------------------------------------------------
def build_pdf(spec: dict) -> bytes:
    """A clean PDF from an outline: title, sections with headings, paragraphs, bullets and a table."""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib.units import cm
    from reportlab.platypus import ListFlowable, ListItem, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
    from xml.sax.saxutils import escape

    styles = getSampleStyleSheet()
    out = io.BytesIO()
    doc = SimpleDocTemplate(out, pagesize=A4, leftMargin=2 * cm, rightMargin=2 * cm, topMargin=2 * cm,
                            bottomMargin=2 * cm, title=str(spec.get("title", "Document")))
    flow = [Paragraph(escape(str(spec.get("title", "Document"))), styles["Title"])]
    if spec.get("subtitle"):
        flow.append(Paragraph(escape(str(spec["subtitle"])), styles["Italic"]))
    for section in spec.get("sections", [])[:80]:
        if section.get("heading"):
            flow += [Spacer(1, 8), Paragraph(escape(str(section["heading"])), styles["Heading2"])]
        flow += [Paragraph(escape(str(p)), styles["BodyText"]) for p in section.get("paragraphs", [])]
        if section.get("bullets"):
            flow.append(ListFlowable([ListItem(Paragraph(escape(str(b)), styles["BodyText"]))
                                      for b in section["bullets"]], bulletType="bullet"))
        table = section.get("table") or {}
        if table.get("columns"):
            data = [[str(c) for c in table["columns"]]] + [[str(v) for v in r] for r in table.get("rows", [])[:500]]
            t = Table(data, repeatRows=1)
            t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E8EEF7")),
                                   ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#B0B7C3")),
                                   ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"), ("FONTSIZE", (0, 0), (-1, -1), 9)]))
            flow += [Spacer(1, 6), t]
    doc.build(flow)
    return out.getvalue()


def _value(v):
    """A cell value: numbers stay numbers, '=...' stays a formula."""
    if isinstance(v, (int, float)) or v is None:
        return v
    s = str(v)
    if s.startswith("="):
        if re.search(r"(?i)\b(WEBSERVICE|FILTERXML|HYPERLINK|CALL|REGISTER|EXEC)\s*\(|https?:", s):
            raise ToolError("That formula reaches outside the sheet; only calculations are allowed.")
        return s
    try:
        return float(s) if re.fullmatch(r"-?\d+\.\d+", s) else int(s) if re.fullmatch(r"-?\d{1,15}", s) else s
    except ValueError:
        return s


def build_xlsx(spec: dict) -> bytes:
    """Sheets with a bold header row, rows (values or =formulas), sensible column widths and a frozen header."""
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill

    wb = Workbook()
    wb.remove(wb.active)
    sheets = spec.get("sheets") or [{"name": spec.get("title", "Sheet1"), "columns": [], "rows": []}]
    for sh in sheets[:20]:
        ws = wb.create_sheet(str(sh.get("name") or "Sheet")[:31])
        cols = [str(c) for c in sh.get("columns", [])]
        if cols:
            ws.append(cols)
            for c in ws[1]:
                c.font, c.fill = Font(bold=True), PatternFill("solid", fgColor="E8EEF7")
            ws.freeze_panes = "A2"
        for row in sh.get("rows", [])[:MAX_ROWS]:
            ws.append([_value(v) for v in row])
        for i, col in enumerate(ws.columns, 1):
            width = max((len(str(c.value)) for c in col if c.value is not None), default=8)
            ws.column_dimensions[col[0].column_letter].width = min(max(width + 2, 8), 60)
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


# --- editing --------------------------------------------------------------------------------------------------
def load(memory: Memory, source: str) -> tuple[bytes, str, str]:
    """(bytes, extension, name) of a library item (its id) or a file Buddy made (its file id)."""
    from . import library, makers
    found = makers.find(memory, source)
    if found:
        path, row = found
        return path.read_bytes(), row["ext"], row["name"]
    row = library._row(memory, source)
    if row["vault"] and not library.Library(memory).unlocked(source):
        raise ToolError("That file is in your locked library: unlock it first (library unlock).")
    data, row = library.original(memory, source)
    return data, row["ext"].lower().lstrip("."), row["name"]


def edit_docx(data: bytes, ops: list[dict]) -> tuple[bytes, list[str]]:
    from docx import Document
    doc = Document(io.BytesIO(data))
    done = []
    for op in ops[:MAX_OPS]:
        kind = op.get("op")
        if kind == "replace":
            old, new, n = str(op.get("find", "")), str(op.get("with", "")), 0
            if not old:
                continue
            for p in _paragraphs(doc):
                if old in p.text:
                    n += _replace_in_paragraph(p, old, new)
            done.append(f"replaced \"{old[:40]}\" {n} time{'s' if n != 1 else ''}")
        elif kind == "add_section":
            if op.get("heading"):
                doc.add_heading(str(op["heading"]), level=1)
            for para in op.get("paragraphs", []):
                doc.add_paragraph(str(para))
            for b in op.get("bullets", []):
                doc.add_paragraph(str(b), style="List Bullet")
            done.append(f"added section \"{str(op.get('heading', ''))[:40]}\"")
        elif kind == "remove":
            words, n = str(op.get("containing", "")), 0
            for p in list(doc.paragraphs):
                if words and words in p.text:
                    p._element.getparent().remove(p._element)
                    n += 1
            done.append(f"removed {n} paragraph{'s' if n != 1 else ''} containing \"{words[:40]}\"")
        else:
            raise ToolError(f"Word edits: replace, add_section, remove (not {kind}).")
    out = io.BytesIO()
    doc.save(out)
    return out.getvalue(), done


def _paragraphs(doc):
    yield from doc.paragraphs
    for table in doc.tables:
        for row in table.rows:
            for cell in row.cells:
                yield from cell.paragraphs


def _replace_in_paragraph(p, old: str, new: str) -> int:
    """Replace text keeping the formatting of the run it starts in (Word splits text into runs)."""
    n = p.text.count(old)
    if not n:
        return 0
    for run in p.runs:  # simple case: inside one run
        if old in run.text:
            run.text = run.text.replace(old, new)
    if old in p.text:  # spread over runs: rebuild the text in the first run
        text = p.text.replace(old, new)
        for i, run in enumerate(p.runs):
            run.text = text if i == 0 else ""
    return n


def edit_xlsx(data: bytes, ops: list[dict]) -> tuple[bytes, list[str]]:
    from openpyxl import load_workbook
    wb = load_workbook(io.BytesIO(data))
    done = []
    for op in ops[:MAX_OPS]:
        kind = op.get("op")
        ws = wb[op["sheet"]] if op.get("sheet") in wb.sheetnames else wb.active
        if kind == "set":
            cells = op.get("cells") or {}
            for ref, v in list(cells.items())[:2000]:
                if not CELL.match(str(ref).upper()):
                    raise ToolError(f"{ref} isn't a cell like B7.")
                ws[str(ref).upper()] = _value(v)
            done.append(f"set {len(cells)} cell{'s' if len(cells) != 1 else ''} in {ws.title}")
        elif kind == "add_rows":
            rows = op.get("rows") or []
            for row in rows[:MAX_ROWS]:
                ws.append([_value(v) for v in row])
            done.append(f"added {len(rows)} row{'s' if len(rows) != 1 else ''} to {ws.title}")
        elif kind == "add_sheet":
            new = wb.create_sheet(str(op.get("name") or "Sheet")[:31])
            if op.get("columns"):
                new.append([str(c) for c in op["columns"]])
            for row in (op.get("rows") or [])[:MAX_ROWS]:
                new.append([_value(v) for v in row])
            done.append(f"added sheet {new.title}")
        elif kind == "rename_sheet":
            old = ws.title
            ws.title = str(op.get("name") or ws.title)[:31]
            done.append(f"renamed {old} to {ws.title}")
        else:
            raise ToolError(f"Excel edits: set, add_rows, add_sheet, rename_sheet (not {kind}).")
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue(), done


def pdf_fields(data: bytes) -> dict:
    from pypdf import PdfReader
    reader = PdfReader(io.BytesIO(data))
    return {k: (v.get("/V") if isinstance(v, dict) else None) for k, v in (reader.get_fields() or {}).items()}


def edit_pdf(memory: Memory, data: bytes, ops: list[dict]) -> tuple[bytes, list[str]]:
    from pypdf import PdfReader, PdfWriter
    reader = PdfReader(io.BytesIO(data))
    writer = PdfWriter()
    writer.append(reader)
    done = []
    for op in ops[:MAX_OPS]:
        kind = op.get("op")
        if kind == "fill":
            values = {str(k): str(v) for k, v in (op.get("fields") or {}).items()}
            known = set((reader.get_fields() or {}).keys())
            unknown = [k for k in values if k not in known]
            if unknown:
                raise ToolError(f"No such field: {', '.join(unknown[:5])}. The form's fields: {', '.join(sorted(known))[:600]}")
            for page in writer.pages:
                writer.update_page_form_field_values(page, values, auto_regenerate=False)
            writer.set_need_appearances_writer(True)
            done.append(f"filled {len(values)} field{'s' if len(values) != 1 else ''}")
        elif kind == "merge":
            for src in (op.get("sources") or [])[:20]:
                more, ext, name = load(memory, str(src))
                if ext != "pdf":
                    raise ToolError(f"{name} isn't a PDF.")
                writer.append(PdfReader(io.BytesIO(more)))
                done.append(f"added {name}")
        elif kind in ("keep_pages", "rotate"):
            pages = [int(p) for p in op.get("pages") or []]
            if any(p < 1 or p > len(writer.pages) for p in pages):
                raise ToolError(f"Pages go from 1 to {len(writer.pages)}.")
            if kind == "keep_pages":
                kept = PdfWriter()
                for p in pages:
                    kept.add_page(writer.pages[p - 1])
                writer = kept
                done.append(f"kept pages {', '.join(map(str, pages))}")
            else:
                for p in pages or range(1, len(writer.pages) + 1):
                    writer.pages[p - 1].rotate(int(op.get("degrees", 90)))
                done.append("rotated pages")
        else:
            raise ToolError(f"PDF edits: fill, merge, keep_pages, rotate (not {kind}).")
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue(), done


EDIT_TOOL = {
    "name": "edit_document",
    "description": "Edit a Word (docx), Excel (xlsx) or PDF file: one the user gave you (its library id) or one you "
    "made (its file id). The original stays as it is; the edited copy appears in the chat. Word ops: "
    "{op: replace, find, with} | {op: add_section, heading, paragraphs, bullets} | {op: remove, containing}. "
    "Excel ops: {op: set, sheet?, cells: {\"B2\": 5, \"B10\": \"=SUM(B2:B9)\"}} | {op: add_rows, sheet?, rows} | "
    "{op: add_sheet, name, columns, rows} | {op: rename_sheet, sheet?, name}. PDF ops: {op: fill, fields: "
    "{name: value}} (fields first lists the form's field names) | {op: merge, sources: [ids]} | "
    "{op: keep_pages, pages: [1,3]} | {op: rotate, pages, degrees}. To read a file's text first, use the library.",
    "input_schema": {"type": "object", "properties": {
        "source": {"type": "string", "description": "Library item id or file id"},
        "fields": {"type": "boolean", "description": "PDF: only list its form fields"},
        "ops": {"type": "array", "items": {"type": "object"}},
        "title": {"type": "string", "description": "Name for the edited copy"}},
        "required": ["source"], "additionalProperties": False},
}


async def run_edit_tool(memory: Memory, args: dict) -> str:
    import asyncio

    from . import makers
    data, ext, name = load(memory, str(args.get("source", "")))
    if args.get("fields"):
        if ext != "pdf":
            raise ToolError("Only PDFs have form fields.")
        return json.dumps(await asyncio.to_thread(pdf_fields, data), ensure_ascii=False, default=str)
    ops = args.get("ops") or []
    if not ops:
        raise ToolError("What should change? Give ops.")
    if ext == "docx":
        new, done = await asyncio.to_thread(edit_docx, data, ops)
    elif ext == "xlsx":
        new, done = await asyncio.to_thread(edit_xlsx, data, ops)
    elif ext == "pdf":
        new, done = await asyncio.to_thread(edit_pdf, memory, data, ops)
    else:
        raise ToolError(f"I can edit Word (docx), Excel (xlsx) and PDF files, not .{ext}.")
    title = str(args.get("title") or re.sub(r"\.\w+$", "", name) + " (edited)")
    file = makers.save(memory, new, title, ext, "document")
    card_id = makers._show(makers._card(file, title, "; ".join(done)[:200]))
    return json.dumps({"made": file["name"], "file_id": file["id"], "card": card_id, "changes": done,
                       "note": "The edited copy is in the chat; the original is unchanged."})
