"""Reading files: in the sandbox when there is one, the same script inside Buddy when not, and the caps."""

import asyncio
import base64
import io
import json
import zipfile

import pytest

from buddy import docread
from buddy.sandbox_jobs import read_document
from buddy.tools import ToolError


def test_text_comes_out_inside_buddy_when_there_is_no_sandbox():
    got = asyncio.run(docread.read("notes.txt", b"Buy at a 52-week high."))
    assert got == {"text": "Buy at a 52-week high.", "where": "buddy", "error": ""}
    z = io.BytesIO()
    with zipfile.ZipFile(z, "w") as f:
        f.writestr("readme.pdf", "x")
        f.writestr("F-F_Factors.CSV", "192607, 2.96, -2.56\n")
    assert asyncio.run(docread.read("data.zip", z.getvalue()))["text"].startswith("192607")
    html = asyncio.run(docread.read("page.html", b"<html><script>evil()</script><p>Momentum works.</p></html>"))["text"]
    assert "Momentum works." in html and "evil" not in html
    assert asyncio.run(docread.read("broken.pdf", b"not a pdf"))["error"]  # a broken file is reported, not raised
    with pytest.raises(ToolError):
        asyncio.run(docread.read("program.exe", b"MZ"))
    with pytest.raises(ToolError):
        asyncio.run(docread.read("big.txt", b"x" * (docread.MAX_BYTES + 1)))


def test_the_sandbox_reads_it_when_there_is_one_with_the_network_off():
    calls = []

    async def call(op, **args):
        calls.append((op, args))
        if op == "fetch":
            return {"base64": base64.b64encode(json.dumps({"text": "from the sandbox", "error": ""}).encode()).decode()}
        return {}

    got = asyncio.run(docread.read("paper.pdf", b"%PDF-1.4", call))
    assert got["where"] == "sandbox" and got["text"] == "from the sandbox"
    assert [op for op, _ in calls] == ["create", "run", "write_b64", "write", "run", "fetch"]
    assert all(a["network"] is False for op, a in calls if op == "run")
    assert calls[2][1]["path"] == "input/document.pdf"  # the file's own name never reaches the sandbox


def test_a_table_as_text_is_split_into_rows():
    assert docread.rows('Ticker,Name\nAAPL,"Apple, Inc."\n\nMSFT\tMicrosoft') == [["Ticker", "Name"], ["AAPL", "Apple, Inc."], ["MSFT", "Microsoft"]]
    assert read_document.LIMIT == 600_000


def test_a_notebook_gives_its_text_and_code_but_never_its_stored_outputs():
    nb = json.dumps({"cells": [{"cell_type": "markdown", "source": ["### Mean Reversion\n", "Buy what fell one deviation."]},
                               {"cell_type": "code", "source": "z = (p - p.mean()) / p.std()",
                                "outputs": [{"text": "SECRET OUTPUT " * 50}]},
                               {"cell_type": "raw", "source": "ignored"}]}).encode()
    got = asyncio.run(docread.read("01 Mean Reversion.ipynb", nb))
    assert "Buy what fell one deviation." in got["text"] and "CODE:\nz = (p - p.mean())" in got["text"]
    assert "SECRET OUTPUT" not in got["text"] and "ignored" not in got["text"] and got["error"] == ""
