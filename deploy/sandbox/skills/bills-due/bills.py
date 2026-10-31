#!/usr/bin/env python3
"""Emails -> bills with amounts and due dates as JSON. No network, no AI."""
import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "deadlines"))
from dates import find_dates, sent_date  # noqa: E402

BILL = re.compile(r"\b(amount due|total due|balance due|minimum (?:payment|due)|payment due|due date|statement|invoice|"
                  r"bill(?:ing)?|autopay|auto-pay|premium due|rent due)\b", re.I)
AMOUNT = re.compile(r"(R\$|S/\.?|US\$|[$₹€£]|\b(?:USD|INR|EUR|GBP|BRL|PEN)\b)\s?(\d{1,3}(?:[,.]\d{3})*(?:[.,]\d{2})?|\d+(?:[.,]\d{2})?)")
SYMBOL = {"$": "USD", "US$": "USD", "₹": "INR", "€": "EUR", "£": "GBP", "R$": "BRL", "S/": "PEN", "S/.": "PEN"}
DUE_NEAR = re.compile(r"\b(due|pay by|payment date|by)\b", re.I)


def number(text: str) -> float:
    t = text
    if re.search(r",\d{2}$", t):
        t = t.replace(".", "").replace(",", ".")
    return float(t.replace(",", ""))


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("input")
    p.add_argument("--dmy", action="store_true")
    p.add_argument("--today", default="")
    a = p.parse_args()
    today = date.fromisoformat(a.today) if a.today else date.today()
    data = json.loads(Path(a.input).read_text(encoding="utf-8"))
    bills = []
    for m in data if isinstance(data, list) else data.get("messages", []):
        text = re.sub(r"\s+", " ", " ".join(str(m.get(k, "")) for k in ("subject", "text", "body", "snippet")))
        if not BILL.search(text):
            continue
        sent = sent_date(m.get("date", "")) if m.get("date") else today
        amounts = []
        for hit in AMOUNT.finditer(text):
            near = text[max(0, hit.start() - 40):hit.start()].lower()
            weight = 2 if any(w in near for w in ("due", "balance", "total", "amount", "payment")) else 1
            if "minimum" in near:
                weight = 0  # the full amount, not the minimum
            try:
                amounts.append((weight, number(hit.group(2)), SYMBOL.get(hit.group(1), hit.group(1))))
            except ValueError:
                pass
        due = None
        for d, pos, _ in find_dates(text, sent, us_order=not a.dmy):
            if DUE_NEAR.search(text[max(0, pos - 40):pos]):
                due = d
                break
        best = max(amounts, default=None)
        bills.append({"from": m.get("from", ""), "subject": str(m.get("subject", ""))[:120],
                      "amount": best[1] if best else None, "currency": best[2] if best else "",
                      "due": due.isoformat() if due else None, "days_left": (due - today).days if due else None,
                      "autopay": bool(re.search(r"auto-?pay|automatic payment|will be (?:debited|charged)", text, re.I))})
    upcoming = sorted([b for b in bills if b["days_left"] is None or b["days_left"] >= 0],
                      key=lambda b: (b["due"] is None, b["due"] or ""))
    total = lambda days: {cur: round(sum(b["amount"] for b in upcoming if b["currency"] == cur and b["amount"]  # noqa: E731
                                         and b["days_left"] is not None and b["days_left"] <= days), 2)
                          for cur in {b["currency"] for b in upcoming if b["currency"]}}
    print(json.dumps({"bills": upcoming[:40], "overdue": [b for b in bills if (b["days_left"] or 0) < 0],
                      "due_in_7_days": total(7), "due_in_30_days": total(30)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
