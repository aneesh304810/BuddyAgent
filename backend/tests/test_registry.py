"""The sandbox's tool registry, install guard and skills (deploy/sandbox)."""

import datetime as dt
import hashlib
import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from buddy import registry

LIB = Path(__file__).resolve().parents[2] / "deploy" / "sandbox"


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(path.parent))
    spec.loader.exec_module(mod)
    return mod


guard = load("pkgguard", LIB / "guard" / "pkgguard.py")
REG = yaml.safe_load((LIB / "registry" / "tools.yaml").read_text())


def pypi(days_old):
    born = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days_old)).isoformat()
    return lambda url: {"releases": {"1.0": [{"upload_time_iso_8601": born}]}}


def test_every_registry_tool_is_complete_and_pinned():
    names = set()
    for t in REG["tools"]:
        assert t["name"] not in names and t["purpose"] and t["tags"]
        names.add(t["name"])
        if t["kind"] in ("pip", "npm"):
            assert t["package"] and t["version"][0].isdigit()
        if t["kind"] == "binary":
            assert t["url"].startswith("https://") and len(t["sha256"]) == 64


def test_registry_tools_install_pinned():
    spec, reason, vetted = guard.check("pip", "duckdb", REG)
    assert (spec, reason, vetted) == ("duckdb==1.1.3", "", True)
    assert guard.check("npm", "prettier", REG)[0] == "prettier@3.3.3"


def test_guard_refuses_lookalikes_young_packages_and_links():
    assert "misspelling of the popular 'requests'" in guard.check("pip", "reqeusts", REG)[1]
    assert "first published 5 days ago" in guard.check("pip", "shinynewlib", REG, fetch=pypi(5))[1]
    assert guard.check("pip", "oldsolidlib", REG, fetch=pypi(900))[1] == ""
    assert "links or archives" in guard.check("pip", "https://evil.example/x.whl", REG)[1]
    assert "couldn't be found" in guard.check("pip", "nope", REG, fetch=lambda u: (_ for _ in ()).throw(OSError()))[1]
    assert guard.check("pip", "requests", REG)[1] == ""  # popular names are fine


def test_guard_reads_install_commands():
    assert guard.packages("pip", ["install", "-q", "pandas==2.2", "rich"]) == (0, ["pandas==2.2", "rich"])
    assert guard.packages("pip", ["list"]) == (-1, [])
    assert "://another-package-index" in guard.packages("pip", ["install", "-i", "https://x", "foo"])[1]
    assert guard.packages("npm", ["i", "-g", "marked"]) == (0, ["marked"])


def test_binary_downloads_must_match_their_fingerprint(monkeypatch, tmp_path):
    inst = load("install_tool", LIB / "guard" / "install_tool.py")
    monkeypatch.setattr(inst, "BIN", tmp_path)

    class Resp:
        def __init__(self, data):
            self.data = data

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return self.data
    monkeypatch.setattr(inst.urllib.request, "urlopen", lambda url, timeout: Resp(b"tampered"))
    tool = {"name": "thing", "kind": "binary", "url": "https://x.example/thing", "sha256": "0" * 64}
    assert "REFUSED" in inst.install(tool) and not (tmp_path / "thing").exists()
    tool["sha256"] = hashlib.sha256(b"tampered").hexdigest()
    assert "installed" in inst.install(tool) and (tmp_path / "thing").exists()


def test_find_tools_finds_skills_and_tools():
    found = registry.search("excel report with a chart from a csv")
    assert found["skills"][0]["name"] == "excel-report" and "excel_report.py" in found["skills"][0]["how"]
    assert any(t["name"] == "xlsxwriter" for t in found["tools"])
    assert "pdfplumber" in [t["name"] for t in registry.search("extract tables from pdf statements")["tools"]]
    assert "excel-report" in registry.catalogue()


pd = pytest.importorskip("pandas")


def test_skills_run(tmp_path):
    csv = tmp_path / "spend.csv"
    csv.write_text("category,amount,month\nfood,40,Jan\ntravel,25,Jan\nfood,10,Feb\nfun,5,Feb\n")
    run = lambda *a: subprocess.run([sys.executable, *map(str, a)], capture_output=True, text=True, cwd=tmp_path)
    r = run(LIB / "skills/excel-report/excel_report.py", csv, tmp_path / "r.xlsx", "--group-by", "category",
            "--sum", "amount", "--title", "Spend")
    assert r.returncode == 0, r.stderr
    summary = pd.read_excel(tmp_path / "r.xlsx", sheet_name="Summary", skiprows=2)
    assert summary.iloc[0].tolist() == ["food", 50] and summary.iloc[-1].tolist() == ["Total", 80]
    r = run(LIB / "skills/chart/chart.py", csv, tmp_path / "c.png", "--x", "category", "--y", "amount")
    assert r.returncode == 0 and (tmp_path / "c.png").read_bytes()[:4] == b"\x89PNG"
    (tmp_path / "r.md").write_text("# Title\nSome **bold** text.\n- one\n- two\n| a | b |\n|---|---|\n| 1 | 2 |\n"
                                   "![chart](c.png)\n")
    r = run(LIB / "skills/pdf-report/pdf_report.py", tmp_path / "r.md", tmp_path / "r.pdf", "--title", "Report")
    assert r.returncode == 0, r.stderr
    assert (tmp_path / "r.pdf").read_bytes()[:4] == b"%PDF"
