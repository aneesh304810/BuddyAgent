#!/usr/bin/env python3
"""Transactions (CSV or JSON) -> a spending summary as JSON. No network, no AI."""
import argparse
import json
import sys

import pandas as pd

NAMES = {"date": ["date", "day", "posted", "transaction date", "booking date"],
         "amount": ["amount", "value", "debit", "sum"],
         "merchant": ["merchant", "payee", "description", "name", "details", "narration"],
         "category": ["category", "type"], "currency": ["currency", "ccy"]}


def load(path: str) -> pd.DataFrame:
    if path.endswith(".json"):
        data = json.load(open(path))
        return pd.DataFrame(data["transactions"] if isinstance(data, dict) else data)
    return pd.read_excel(path) if path.endswith((".xlsx", ".xls")) else pd.read_csv(path)


def pick(df: pd.DataFrame, kind: str) -> str | None:
    cols = {c.lower().strip(): c for c in df.columns}
    for name in NAMES[kind]:
        if name in cols:
            return cols[name]
    return next((c for low, c in cols.items() if any(n in low for n in NAMES[kind])), None)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("input")
    p.add_argument("--currency", default="")
    a = p.parse_args()
    df = load(a.input)
    date, amount, merchant = pick(df, "date"), pick(df, "amount"), pick(df, "merchant")
    if not (date and amount):
        sys.exit(json.dumps({"error": f"need date and amount columns; found {list(df.columns)}"}))
    category, currency = pick(df, "category"), pick(df, "currency")
    df["_date"] = pd.to_datetime(df[date], errors="coerce")
    df["_amt"] = pd.to_numeric(df[amount].astype(str).str.replace(r"[^0-9.\-]", "", regex=True), errors="coerce")
    df = df.dropna(subset=["_date", "_amt"])
    if a.currency and currency:
        df = df[df[currency].astype(str).str.upper() == a.currency.upper()]
    # With both signs, the negative amounts are spending (debits); with only positive ones, the export
    # lists charges as positive (money in, if any, is then usually in its own file).
    spend_sign = -1 if (df["_amt"] < 0).any() else 1
    df["_spend"] = df["_amt"] * spend_sign
    out_df, in_df = df[df["_spend"] > 0].copy(), df[df["_spend"] < 0]
    out_df["_merchant"] = out_df[merchant].astype(str).str.strip().str.title() if merchant else "?"
    out_df["_cat"] = out_df[category].astype(str) if category else "uncategorised"
    out_df["_month"] = out_df["_date"].dt.strftime("%Y-%m")
    r = lambda x: round(float(x), 2)  # noqa: E731
    by_merchant = out_df.groupby("_merchant")["_spend"]
    recurring = [m for m, g in out_df.groupby("_merchant") if g["_month"].nunique() >= 3]
    unusual = []
    for _, row in out_df.iterrows():
        usual = by_merchant.median()[row["_merchant"]]
        if by_merchant.count()[row["_merchant"]] >= 3 and row["_spend"] > 3 * usual:
            unusual.append({"date": row["_date"].strftime("%Y-%m-%d"), "merchant": row["_merchant"],
                            "amount": r(row["_spend"]), "usual": r(usual)})
    print(json.dumps({
        "period": [df["_date"].min().strftime("%Y-%m-%d"), df["_date"].max().strftime("%Y-%m-%d")],
        "transactions": int(len(df)), "total_spent": r(out_df["_spend"].sum()), "money_in": r(-in_df["_spend"].sum()),
        "by_category": {k: r(v) for k, v in out_df.groupby("_cat")["_spend"].sum().sort_values(ascending=False).items()},
        "by_month": {k: r(v) for k, v in out_df.groupby("_month")["_spend"].sum().items()},
        "top_merchants": [{"merchant": k, "spent": r(v), "times": int(by_merchant.count()[k])}
                          for k, v in by_merchant.sum().sort_values(ascending=False).head(10).items()],
        "largest": [{"date": x["_date"].strftime("%Y-%m-%d"), "merchant": x["_merchant"], "amount": r(x["_spend"])}
                    for _, x in out_df.nlargest(5, "_spend").iterrows()],
        "recurring": [{"merchant": m, "typical": r(by_merchant.median()[m]), "months": int(
            out_df[out_df["_merchant"] == m]["_month"].nunique())} for m in recurring][:15],
        "unusual": unusual[:10],
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
