#!/usr/bin/env python3
"""Transactions -> subscriptions, price increases and duplicate charges as JSON. No network, no AI."""
import argparse
import json
import sys

import pandas as pd


def column(df: pd.DataFrame, *names: str) -> str | None:
    cols = {c.lower().strip(): c for c in df.columns}
    for n in names:
        if n in cols:
            return cols[n]
    return next((c for low, c in cols.items() if any(n in low for n in names)), None)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("input")
    a = p.parse_args()
    df = pd.read_excel(a.input) if a.input.endswith((".xlsx", ".xls")) else pd.read_csv(a.input)
    date = column(df, "date", "day", "posted")
    amount = column(df, "amount", "value")
    merchant = column(df, "merchant", "payee", "description", "name")
    if not (date and amount and merchant):
        sys.exit(json.dumps({"error": f"need date, amount and merchant columns; found {list(df.columns)}"}))
    df["_date"] = pd.to_datetime(df[date], errors="coerce")
    df["_amt"] = pd.to_numeric(df[amount].astype(str).str.replace(r"[^0-9.\-]", "", regex=True), errors="coerce")
    df = df.dropna(subset=["_date", "_amt"])
    sign = -1 if (df["_amt"] < 0).any() else 1
    df["_spend"] = df["_amt"] * sign
    df = df[df["_spend"] > 0].copy()
    df["_m"] = df[merchant].astype(str).str.strip().str.title()
    df["_month"] = df["_date"].dt.strftime("%Y-%m")
    r = lambda x: round(float(x), 2)  # noqa: E731
    subs, increases, duplicates = [], [], []
    for name, g in df.sort_values("_date").groupby("_m"):
        g = g.sort_values("_date")
        if g["_month"].nunique() < 2:
            continue
        usual = g["_spend"].median()
        steady = g[(g["_spend"] - usual).abs() <= 0.25 * usual]
        if steady["_month"].nunique() < 2:
            continue
        gaps = steady["_date"].diff().dt.days.dropna()
        every = gaps.median() if len(gaps) else 30
        per_year = 365 / every if every else 12
        latest, before = steady["_spend"].iloc[-1], (steady["_spend"].iloc[-2] if len(steady) > 1 else None)
        subs.append({"merchant": name, "usual": r(usual), "latest": r(latest),
                     "every_days": int(round(every)), "last_charged": steady["_date"].iloc[-1].strftime("%Y-%m-%d"),
                     "times": int(len(steady)), "yearly_cost": r(latest * per_year)})
        if before is not None and latest > before * 1.02:
            increases.append({"merchant": name, "from": r(before), "to": r(latest),
                              "since": steady["_date"].iloc[-1].strftime("%Y-%m-%d"),
                              "extra_per_year": r((latest - before) * per_year)})
        for (_, x), (_, y) in zip(g.iloc[:-1].iterrows(), g.iloc[1:].iterrows()):
            if abs(x["_spend"] - y["_spend"]) < 0.01 and (y["_date"] - x["_date"]).days <= 3:
                duplicates.append({"merchant": name, "amount": r(y["_spend"]), "dates": [
                    x["_date"].strftime("%Y-%m-%d"), y["_date"].strftime("%Y-%m-%d")]})
    subs.sort(key=lambda s: -s["yearly_cost"])
    yearly = sum(s["yearly_cost"] for s in subs)
    print(json.dumps({"subscriptions": subs[:40], "count": len(subs), "monthly_total": r(yearly / 12),
                      "yearly_total": r(yearly), "price_increases": increases, "duplicate_charges": duplicates[:20]},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
