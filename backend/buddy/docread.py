"""Reading a file Buddy was given or downloaded: the text is taken out in the sandbox, not in Buddy itself.

A PDF, spreadsheet or zip from the internet is the kind of file that can be built to attack whatever opens it.
So the file is handed to the sandbox (a throwaway container with no network), a small script there takes the
text out (sandbox_jobs/read_document.py), and only that text comes back. When the sandbox is not set up, the
same script runs inside Buddy as a fallback, and the result says so.

  read(name, data) -> {"text": ..., "where": "sandbox" | "buddy", "error": ""}
"""

import asyncio
import base64
import json
import logging
import re
import tempfile
from pathlib import Path

from .tools import ToolError

log = logging.getLogger("buddy.docread")
SCRIPT = Path(__file__).parent / "sandbox_jobs" / "read_document.py"
WORKSPACE = "document-reader"
KINDS = ("pdf", "docx", "xlsx", "xlsm", "zip", "html", "htm", "txt", "md", "csv", "text", "py", "cs", "json")
MAX_BYTES = 12_000_000
_one_at_a_time = asyncio.Lock()


def _safe_name(name: str) -> str:
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else "txt"
    if ext not in KINDS:
        raise ToolError("Buddy reads PDF, Word, Excel, web pages, zipped CSV and text files.")
    return "document." + ext


async def read(name: str, data: bytes, call=None) -> dict:
    if len(data) > MAX_BYTES:
        raise ToolError("That file is over 12 MB.")
    safe = _safe_name(name)
    from . import sandbox  # noqa: PLC0415
    if call is not None or sandbox.available():
        call = call or sandbox.call
        try:
            async with _one_at_a_time:
                await call("create", workspace=WORKSPACE)
                await call("run", workspace=WORKSPACE, command="rm -rf input text.json && mkdir -p input", network=False,
                           timeout=60)
                await call("write_b64", workspace=WORKSPACE, path="input/" + safe,
                           content_b64=base64.b64encode(data).decode())
                await call("write", workspace=WORKSPACE, path="read_document.py", content=SCRIPT.read_text())
                await call("run", workspace=WORKSPACE, command="python3 read_document.py .", network=False, timeout=180)
                got = json.loads(base64.b64decode((await call("fetch", workspace=WORKSPACE, path="text.json"))["base64"]))
            return {"text": str(got.get("text") or ""), "where": "sandbox", "error": str(got.get("error") or "")}
        except Exception as e:
            log.warning("docread: the sandbox could not read it (%s); reading inside Buddy", type(e).__name__)
    return await asyncio.to_thread(_inside, safe, data)


def _inside(safe: str, data: bytes) -> dict:
    from .sandbox_jobs import read_document  # noqa: PLC0415
    with tempfile.TemporaryDirectory() as folder:
        (Path(folder) / "input").mkdir()
        (Path(folder) / "input" / safe).write_bytes(data)
        read_document.main(folder)
        got = json.loads((Path(folder) / "text.json").read_text())
    return {"text": str(got.get("text") or ""), "where": "buddy", "error": str(got.get("error") or "")}


def rows(text: str) -> list[list[str]]:
    """A table that came back as text: lines split on tabs, else on commas."""
    out = []
    for line in text.splitlines():
        if line.strip():
            out.append([c.strip().strip('"') for c in (line.split("\t") if "\t" in line else re.split(r",(?=(?:[^\"]*\"[^\"]*\")*[^\"]*$)", line))])
    return out
