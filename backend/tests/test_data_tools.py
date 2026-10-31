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


def test_subscriptions(tmp_path):
    rows = ["date,merchant,amount,currency,category"]
    for month, netflix in (("07", 15.49), ("08", 15.49), ("09", 17.99)):
        rows += [f"2026-{month}-03,NETFLIX,-{netflix},USD,Subs", f"2026-{month}-12,Spotify,-11.99,USD,Subs"]
    rows += ["2026-08-13,Spotify,-11.99,USD,Subs", "2026-07-20,Whole Foods,-120,USD,Groceries",
             "2026-08-02,Whole Foods,-35,USD,Groceries", "2026-09-15,Salary,3000,USD,Income"]
    f = tmp_path / "t.csv"
    f.write_text("\n".join(rows))
    out = run("subscriptions/subscriptions.py", f)
    assert [s["merchant"] for s in out["subscriptions"]] == ["Netflix", "Spotify"]  # not groceries, not salary
    assert out["price_increases"][0]["merchant"] == "Netflix" and out["price_increases"][0]["to"] == 17.99
    assert out["duplicate_charges"][0]["dates"] == ["2026-08-12", "2026-08-13"]


MAIL = [{"date": "2026-10-28", "from": "Chase", "subject": "Your statement is ready",
         "text": "New balance $1,234.50. Minimum payment due $35.00. Payment due date: Nov 20, 2026."},
        {"date": "2026-10-29", "from": "FPL", "subject": "Your bill", "text": "Amount due: $142.17 due by 11/05. AutoPay is on."},
        {"date": "2026-10-30", "from": "DMV", "subject": "Renewal", "text": "Please renew your registration by Friday."},
        {"date": "2026-10-30", "from": "Mom", "subject": "Hi", "text": "See you Sunday"}]


def test_deadlines(tmp_path):
    f = tmp_path / "m.json"
    f.write_text(json.dumps(MAIL))
    out = run("deadlines/deadlines.py", f, "--today", "2026-10-31")
    assert [(d["from"], d["date"]) for d in out["upcoming"]] == [("FPL", "2026-11-05"), ("DMV", "2026-11-06"),
                                                                ("Chase", "2026-11-20")]  # "Sunday" isn't a deadline
    assert run("deadlines/deadlines.py", f, "--today", "2026-11-10")["overdue"]


def test_bills_due(tmp_path):
    f = tmp_path / "m.json"
    f.write_text(json.dumps(MAIL))
    out = run("bills-due/bills.py", f, "--today", "2026-10-31")
    fpl, chase = out["bills"]
    assert (fpl["amount"], fpl["due"], fpl["autopay"]) == (142.17, "2026-11-05", True)
    assert chase["amount"] == 1234.5  # the balance, not the minimum
    assert out["due_in_7_days"] == {"USD": 142.17} and out["due_in_30_days"] == {"USD": 1376.67}


def test_itinerary_check(tmp_path):
    f = tmp_path / "trip.json"
    f.write_text(json.dumps({"legs": [
        {"ref": "CM1", "from": "MIA", "to": "PTY", "depart": "2026-11-10T22:30", "depart_tz": "America/New_York",
         "arrive": "2026-11-11T01:10", "arrive_tz": "America/Panama"},
        {"ref": "CM2", "from": "PTY", "to": "LIM", "depart": "2026-11-11T01:50", "depart_tz": "America/Panama",
         "arrive": "2026-11-11T05:20", "arrive_tz": "America/Lima"},
        {"from": "LIM", "to": "CUZ", "depart": "2026-11-13T09:10", "depart_tz": "America/Lima",
         "arrive": "2026-11-13T10:30", "arrive_tz": "America/Lima"}],
        "hotels": [{"name": "Lima Inn", "check_in": "2026-11-11", "check_out": "2026-11-12", "tz": "America/Lima"}]}))
    out = run("itinerary-check/itinerary.py", f)
    assert [t["what"].split()[1] for t in out["timeline"]] == ["CM1", "CM2", "Lima", "LIM-CUZ"]
    why = " | ".join(p["what"] + ": " + p["why"] for p in out["problems"])
    assert "CM1 -> CM2: tight connection: 40 min" in why and "night of Thu 12 Nov" in why and not out["looks_fine"]


def test_price_history(tmp_path):
    f = tmp_path / "p.csv"
    f.write_text("seen_at,price,currency,where\n2026-09-01,420,USD,LATAM\n2026-09-15,399,USD,LATAM\n"
                 "2026-10-01,450,USD,Avianca\n2026-10-10,470,USD,LATAM\n2026-10-20,480,USD,LATAM\n2026-10-30,360,USD,Copa\n")
    out = run("price-history/price_history.py", f)
    assert out["latest"]["price"] == 360 and out["lowest"]["where"] == "Copa" and out["signal"] == "good price"
    f.write_text("seen_at,price\n2026-10-01,300\n2026-10-08,300\n2026-10-20,330\n2026-10-25,360\n2026-10-30,390\n")
    assert run("price-history/price_history.py", f)["signal"] == "rising"


def test_free_slots(tmp_path):
    f = tmp_path / "c.json"
    f.write_text(json.dumps([
        {"title": "Standup", "start": "2026-11-02T09:00:00-05:00", "end": "2026-11-02T09:30:00-05:00"},
        {"title": "Dentist", "start": "2026-11-02T09:15:00-05:00", "end": "2026-11-02T10:00:00-05:00"},
        {"title": "Lunch", "start": "2026-11-02T12:00:00-05:00", "end": "2026-11-02T13:00:00-05:00"}]))
    out = run("free-slots/free_slots.py", f, "--tz", "America/New_York", "--also", "Asia/Kolkata", "--days", "1",
              "--now", "2026-11-02T07:00:00-05:00", "--their-from", "07:00", "--their-to", "23:00")
    assert [s["you"] for s in out["slots"]] == ["Mon 02 Nov 08:00-09:00", "Mon 02 Nov 10:00-12:00",
                                                "Mon 02 Nov 20:30-21:00"]  # 13:00-20:30 is night in India
    assert out["slots"][0]["them"] == "Mon 02 Nov 18:30-19:30"
    assert out["conflicts"][0]["b"] == "Dentist" and out["hours_busy_by_day"] == {"2026-11-02": 2.0}
