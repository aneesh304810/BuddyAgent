"""Buddy Labs' own documents in Buddy's library, so Buddy can answer questions about them.

On startup (and after every update, which restarts Buddy) every Markdown file in docs/company/
(policies: privacy, information security program, biometric privacy, how Buddy is secured) and
docs/ (setup, security, access control, keys, money, sandbox...) is put in the library, searchable
like the owner's own documents. A changed file replaces its old copy; a removed file is dropped.
The newest SOC readiness report is kept there too (compliance.py publishes it).

They're indexed as they are: no AI reads them to file them, so syncing costs nothing.
"""

import hashlib
import logging
import os
import re
from pathlib import Path

from .library import Library
from .memory import Memory

log = logging.getLogger(__name__)

DOCS = Path(os.getenv("BUDDY_DOCS_DIR", str(Path(__file__).resolve().parents[2] / "docs")))
NOTE = "company-doc:"
DOC_TYPE = "Buddy Labs document"
SKIP = {"README.md"}


def _title(text: str, fallback: str) -> str:
    m = re.search(r"^#\s+(.+)$", text, re.M)
    return (m.group(1).strip() if m else fallback)[:200]


def _summary(text: str) -> str:
    for para in re.split(r"\n\s*\n", text):
        p = para.strip()
        if p and not p.startswith(("#", "|", "```", "Source:")) and len(p) > 40:
            return re.sub(r"\s+", " ", p)[:600]
    return ""


def publish(memory: Memory, key: str, name: str, text: str) -> str | None:
    """Put one document in the library under `key`, replacing an older copy. Returns its id."""
    lib = Library(memory, understand=lambda *a: {})
    note = NOTE + key
    data = text.encode()
    digest = hashlib.sha256(data).hexdigest()
    old = memory.db.execute("SELECT id, sha256 FROM library_items WHERE note = ?", (note,)).fetchone()
    if old and old["sha256"] == digest:
        return old["id"]
    if old:
        lib.forget(old["id"])
    row = lib.add(name, data, note=note)
    if row.get("duplicate"):  # the owner uploaded the very same file: it's searchable already
        return row["id"]
    title = _title(text, Path(name).stem)
    lib._index(row["id"], f"{title} ({DOC_TYPE})\n\n{text}")
    lib._set(row["id"], title=title, summary=_summary(text), doc_type=DOC_TYPE, status="ready")
    return row["id"]


def files() -> dict[str, Path]:
    """key -> file: docs/company/*.md and docs/*.md."""
    out: dict[str, Path] = {}
    for folder, prefix in ((DOCS / "company", "company/"), (DOCS, "")):
        if folder.is_dir():
            for p in sorted(folder.glob("*.md")):
                if p.name not in SKIP:
                    out[prefix + p.name] = p
    return out


def sync(memory: Memory) -> dict:
    found = files()
    added = 0
    lib = Library(memory, understand=lambda *a: {})  # makes the library's tables
    for key, path in found.items():
        try:
            before = memory.db.execute("SELECT sha256 FROM library_items WHERE note = ?", (NOTE + key,)).fetchone()
            text = path.read_text(encoding="utf-8")
            publish(memory, key, path.name, text)
            if not before or before["sha256"] != hashlib.sha256(text.encode()).hexdigest():
                added += 1
        except Exception:
            log.exception("couldn't add %s to the library", key)
    gone = [r["id"] for r in memory.db.execute("SELECT id, note FROM library_items WHERE note LIKE ?", (NOTE + "%",))
            if r["note"][len(NOTE):] not in found and not r["note"][len(NOTE):].startswith("live/")]
    for item_id in gone:
        lib.forget(item_id)
    if added or gone:
        log.info("company documents: %d added or updated, %d removed", added, len(gone))
    return {"documents": len(found), "updated": added, "removed": len(gone)}


def listing(memory: Memory) -> list[dict]:
    Library(memory, understand=lambda *a: {})
    return [{"id": r["id"], "title": r["title"], "file": r["name"], "added": r["added_at"][:10]}
            for r in memory.db.execute("SELECT id, title, name, added_at FROM library_items WHERE note LIKE ? "
                                       "ORDER BY note", (NOTE + "%",))]
