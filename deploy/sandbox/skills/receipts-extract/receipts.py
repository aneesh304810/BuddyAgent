#!/usr/bin/env python3
"""Emails/messages -> receipts, bookings, bills and refunds as JSON rows. No network, no AI."""
import argparse
import csv
import json
import re

AMOUNT = re.compile(r"(?P<cur>R\$|S/\.?|US\$|[$₹€£]|\b(?:USD|INR|EUR|GBP|BRL|PEN)\b)\s?(?P<num>\d{1,3}(?:[,.]\d{3})*(?:[.,]\d{2})?|\d+(?:[.,]\d{2})?)"
                    r"|(?P<num2>\d{1,3}(?:[,.]\d{3})*(?:[.,]\d{2})?)\s?(?P<cur2>USD|INR|EUR|GBP|BRL|PEN)\b")
# The keyword in any case; the number itself in capitals or digits ("XKQ7PL", "112-9930"), not "confirmed".
REF = re.compile(r"\b(?i:order|booking|confirmation|reservation|invoice|reference|ref|pnr)\s*(?i:no\.?|number|#|id|code)?"
                 r"\s*[:#]?\s*([A-Z0-9][A-Z0-9-]{4,})\b")
KINDS = [("refund", r"refund|reversed|credited back"), ("booking", r"booking|reservation|itinerary|check-in|flight|hotel"),
         ("bill", r"bill|statement|due date|amount due|invoice"), ("receipt", r"receipt|order|payment|paid|purchase")]
SYMBOL = {"$": "USD", "US$": "USD", "₹": "INR", "€": "EUR", "£": "GBP", "R$": "BRL", "S/": "PEN", "S/.": "PEN"}


def number(text: str) -> float:
    t = text.replace(" ", "")
    if re.search(r",\d{2}$", t):  # 1.234,56
        t = t.replace(".", "").replace(",", ".")
    return float(t.replace(",", ""))


def messages(path: str) -> list[dict]:
    raw = open(path, encoding="utf-8").read()
    try:
        data = json.loads(raw)
        return data if isinstance(data, list) else data.get("messages", [])
    except json.JSONDecodeError:
        return [{"text": part} for part in re.split(r"\n-{3,}\n", raw) if part.strip()]


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("input")
    p.add_argument("--csv", default="")
    a = p.parse_args()
    rows = []
    for m in messages(a.input):
        text = " ".join(str(m.get(k, "")) for k in ("subject", "text", "body", "snippet"))
        amounts = []
        for hit in AMOUNT.finditer(text):
            cur = hit.group("cur") or hit.group("cur2")
            try:
                amounts.append((number(hit.group("num") or hit.group("num2")), SYMBOL.get(cur, cur)))
            except ValueError:
                continue
        kind = next((k for k, pat in KINDS if re.search(pat, text, re.I)), "")
        if not amounts and not kind:
            continue
        top = max(amounts, default=(None, ""))
        ref = REF.search(text)
        rows.append({"date": m.get("date", ""), "from": m.get("from", ""), "subject": str(m.get("subject", ""))[:120],
                     "kind": kind or "other", "amount": top[0], "currency": top[1],
                     "reference": ref.group(1) if ref else ""})
    if a.csv:
        with open(a.csv, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=["date", "from", "subject", "kind", "amount", "currency", "reference"])
            w.writeheader()
            w.writerows(rows)
    print(json.dumps({"found": len(rows), "rows": rows[:200]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
