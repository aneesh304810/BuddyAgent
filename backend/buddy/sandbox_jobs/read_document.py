"""Take the text out of one downloaded or uploaded file. Runs offline in the sandbox, away from Buddy.

    python3 read_document.py <folder>      reads <folder>/input/<the one file>, writes <folder>/text.json

PDF, Word, a web page, a spreadsheet (rows as tab-separated lines), a zip holding CSV or text (its first such
file), or plain text. The output is capped, so a file built to be enormous cannot fill the disk or memory.
"""

import json
import sys
import zipfile
from pathlib import Path

LIMIT = 600_000  # characters kept


def read(path: Path) -> str:
    ext = path.suffix.lower().lstrip(".")
    if ext == "pdf":
        from pypdf import PdfReader
        out, size = [], 0
        for i, page in enumerate(PdfReader(str(path)).pages[:300]):
            text = (page.extract_text() or "").strip()
            out.append(f"[page {i + 1}]\n{text}")
            size += len(text)
            if size > LIMIT:
                break
        return "\n\n".join(out)
    if ext == "docx":
        import docx
        d = docx.Document(str(path))
        parts = [p.text for p in d.paragraphs if p.text.strip()]
        for t in d.tables:
            parts += [" | ".join(c.text.strip() for c in row.cells) for row in t.rows]
        return "\n".join(parts)
    if ext in ("xlsx", "xlsm"):
        import openpyxl
        book = openpyxl.load_workbook(str(path), read_only=True, data_only=True)
        lines = []
        for sheet in book.worksheets[:5]:
            for row in sheet.iter_rows(values_only=True, max_row=20000):
                lines.append("\t".join("" if v is None else str(v) for v in row))
                if len(lines) > 40000:
                    break
        return "\n".join(lines)
    if ext == "zip":
        with zipfile.ZipFile(str(path)) as z:
            for info in z.infolist():
                if info.filename.lower().endswith((".csv", ".txt")) and info.file_size <= 20_000_000:
                    return z.read(info).decode("utf-8", "replace")
        return ""
    if ext == "ipynb":  # a notebook: its text and code cells, never its stored outputs
        import json as _json
        cells = _json.loads(path.read_bytes().decode("utf-8", "replace")).get("cells") or []
        parts = []
        for c in cells[:400]:
            src = c.get("source")
            text = "".join(src) if isinstance(src, list) else str(src or "")
            if c.get("cell_type") in ("markdown", "code") and text.strip():
                parts.append(("" if c.get("cell_type") == "markdown" else "CODE:\n") + text.strip())
        return "\n\n".join(parts)
    if ext in ("html", "htm"):
        import re
        raw = path.read_bytes().decode("utf-8", "replace")
        raw = re.sub(r"(?is)<(script|style|head)\b.*?</\1>", " ", raw)
        return re.sub(r"\s+", " ", re.sub(r"(?s)<[^>]+>", " ", raw))
    return path.read_bytes().decode("utf-8", "replace")


def main(folder: str) -> None:
    files = [p for p in (Path(folder) / "input").iterdir() if p.is_file()]
    try:
        out = {"text": read(files[0])[:LIMIT] if files else "", "error": "" if files else "no file"}
    except Exception as e:  # a broken or hostile file: say so, never crash the caller
        out = {"text": "", "error": f"{type(e).__name__}: {str(e)[:200]}"}
    (Path(folder) / "text.json").write_text(json.dumps(out))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else ".")
