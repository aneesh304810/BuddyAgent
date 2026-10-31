"""The sandbox's data tools: plain scripts, run here the way the sandbox runs them."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

SKILLS = Path(__file__).resolve().parents[2] / "deploy" / "sandbox" / "skills"


def run(script: str, *args) -> dict:
    out = subprocess.run([sys.executable, str(SKILLS / script), *map(str, args)], capture_output=True, text=True,
                         timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def test_spending_summary(tmp_path):
    rows = ["Date,Description,Amount,Category"]
    for month in ("2026-07", "2026-08", "2026-09"):
        rows += [f"{month}-03,NETFLIX,-15.49,Subscriptions", f"{month}-10,Whole Foods,-120.00,Groceries",
                 f"{month}-15,Salary,3000.00,Income", f"{month}-20,Shell,-40.00,Fuel"]
    rows.append("2026-09-25,Whole Foods,-480.00,Groceries")  # unusual: 4x the usual
    f = tmp_path / "t.csv"
    f.write_text("\n".join(rows))
    out = run("spending-summary/spending.py", f)
    assert out["total_spent"] == round(3 * (15.49 + 120 + 40) + 480, 2) and out["money_in"] == 9000.0
    assert out["by_category"]["Groceries"] == 840.0
    assert {r["merchant"] for r in out["recurring"]} >= {"Netflix", "Whole Foods", "Shell"}
    assert out["unusual"][0]["amount"] == 480.0 and out["largest"][0]["merchant"] == "Whole Foods"


def test_table_query(tmp_path):
    f = tmp_path / "h.csv"
    f.write_text("city,country,price\nLima,Peru,90\nCusco,Peru,70\nLima,Peru,110\nKochi,India,40\n")
    assert run("table-query/table_query.py", f)["rows"] == 4
    spec = json.dumps({"where": [["country", "==", "Peru"]], "group_by": ["city"], "agg": {"price": "mean"},
                       "sort": ["-price"]})
    assert run("table-query/table_query.py", f, "--spec", spec)["rows"] == [{"city": "Lima", "price": 100.0},
                                                                           {"city": "Cusco", "price": 70.0}]


def test_receipts_extract(tmp_path):
    f = tmp_path / "m.json"
    f.write_text(json.dumps([
        {"date": "2026-10-01", "from": "Amazon", "subject": "Your order", "text": "Order #112-9930 total $45.20 paid"},
        {"date": "2026-10-02", "from": "LATAM", "subject": "Booking confirmed", "text": "Booking code: XKQ7PL. Total R$ 1.234,56"},
        {"date": "2026-10-03", "from": "Mom", "subject": "Dinner", "text": "See you Sunday!"},
    ]))
    out = run("receipts-extract/receipts.py", f)
    assert out["found"] == 2
    amazon, latam = out["rows"]
    assert (amazon["amount"], amazon["currency"], amazon["kind"]) == (45.2, "USD", "receipt")
    assert (latam["amount"], latam["currency"], latam["kind"], latam["reference"]) == (1234.56, "BRL", "booking", "XKQ7PL")


def test_doc_snippets(tmp_path):
    f = tmp_path / "a.txt"
    f.write_text(" ".join(["The hotel has a pool and a gym."] * 30) +
                 " Cancellation is free until 48 hours before arrival; after that a fee of one night applies. " +
                 " ".join(["Breakfast is served from seven."] * 30))
    out = run("doc-snippets/snippets.py", f, "--question", "what is the cancellation fee", "--top", "2")
    assert "Cancellation is free until 48 hours" in out["passages"][0]["text"]


def test_compare_offers(tmp_path):
    f = tmp_path / "o.json"
    f.write_text(json.dumps([
        {"name": "Cheap Air", "price": 250, "fees": {"bag": 60, "seat": 25}, "refundable": False},
        {"name": "LATAM", "price": 289, "fees": {"bag": 0}, "refundable": True},
        {"name": "Mid Air", "price": 270, "bag_fee": 20},
    ]))
    out = run("compare-offers/compare.py", f)
    assert [o["name"] for o in out["offers"]] == ["LATAM", "Mid Air", "Cheap Air"]
    assert out["cheapest"] == "LATAM" and out["headline_cheapest"] == "Cheap Air" and out["cheapest_refundable"] == "LATAM"


def test_pdf_extract(tmp_path):
    pytest.importorskip("pypdf")
    canvas = pytest.importorskip("reportlab.pdfgen.canvas")
    f = tmp_path / "c.pdf"
    c = canvas.Canvas(str(f))
    c.drawString(72, 720, "Rental agreement. Rent is 1200 per month.")
    c.showPage()
    c.drawString(72, 720, "Cancellation requires 30 days notice and a fee of 200.")
    c.save()
    out = run("pdf-extract/pdf_extract.py", f, "--find", "cancellation")
    assert out["pages"] == 2 and out["passages"][0]["page"] == 2 and "30 days" in out["passages"][0]["text"]
