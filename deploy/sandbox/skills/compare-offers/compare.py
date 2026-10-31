#!/usr/bin/env python3
"""Offers -> compared by true total (price + fees) as JSON. No network, no AI."""
import argparse
import csv
import json


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("input")
    p.add_argument("--csv", default="")
    a = p.parse_args()
    data = json.load(open(a.input, encoding="utf-8"))
    offers = data if isinstance(data, list) else data.get("offers", [])
    rows = []
    for o in offers:
        fees = {k: float(v) for k, v in (o.get("fees") or {}).items() if isinstance(v, (int, float))}
        fees.update({k: float(v) for k, v in o.items() if k.endswith("_fee") and isinstance(v, (int, float))})
        price = float(o.get("price") or 0)
        rows.append({"name": o.get("name", "?"), "currency": o.get("currency", ""), "price": round(price, 2),
                     "fees": round(sum(fees.values()), 2), "fee_detail": fees, "true_total": round(price + sum(fees.values()), 2),
                     "refundable": bool(o.get("refundable")), "link": o.get("link", "")})
    rows.sort(key=lambda r: r["true_total"])
    refundable = [r for r in rows if r["refundable"]]
    if a.csv:
        with open(a.csv, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=["name", "currency", "price", "fees", "true_total", "refundable", "link"],
                               extrasaction="ignore")
            w.writeheader()
            w.writerows(rows)
    print(json.dumps({"offers": rows, "cheapest": rows[0]["name"] if rows else None,
                      "cheapest_refundable": refundable[0]["name"] if refundable else None,
                      "headline_cheapest": min(rows, key=lambda r: r["price"])["name"] if rows else None},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
