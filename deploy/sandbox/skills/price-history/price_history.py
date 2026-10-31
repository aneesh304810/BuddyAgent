#!/usr/bin/env python3
"""Prices over time -> lowest, typical, trend and a buy-or-wait signal as JSON. No network, no AI."""
import argparse
import json
import sys

import pandas as pd


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("input")
    p.add_argument("--target", type=float, default=None)
    a = p.parse_args()
    df = pd.read_csv(a.input)
    if "price" not in df or "seen_at" not in df:
        sys.exit(json.dumps({"error": "need seen_at and price columns"}))
    df["seen_at"] = pd.to_datetime(df["seen_at"], errors="coerce", utc=True)
    df["price"] = pd.to_numeric(df["price"], errors="coerce")
    df = df.dropna(subset=["seen_at", "price"]).sort_values("seen_at")
    if df.empty:
        sys.exit(json.dumps({"error": "no prices"}))
    r = lambda x: round(float(x), 2)  # noqa: E731
    latest = df.iloc[-1]
    low = df.loc[df["price"].idxmin()]
    days = max(1.0, (df["seen_at"].iloc[-1] - df["seen_at"].iloc[0]).total_seconds() / 86400)
    pct = round(100 * (df["price"] < latest["price"]).mean())
    recent = df[df["seen_at"] >= df["seen_at"].iloc[-1] - pd.Timedelta(days=14)]
    trend = 0.0
    if len(recent) >= 3:
        x = (recent["seen_at"] - recent["seen_at"].iloc[0]).dt.total_seconds() / (7 * 86400)
        if x.var() > 0:
            trend = float(((x - x.mean()) * (recent["price"] - recent["price"].mean())).sum() / ((x - x.mean()) ** 2).sum())
    signal = "typical"
    if (a.target is not None and latest["price"] <= a.target) or (len(df) >= 5 and pct <= 20):
        signal = "good price"
    elif trend > 0.02 * df["price"].median():
        signal = "rising"
    elif trend < -0.02 * df["price"].median():
        signal = "falling"
    print(json.dumps({
        "prices_seen": int(len(df)), "over_days": round(days), "currency": str(latest.get("currency", "") or ""),
        "latest": {"price": r(latest["price"]), "when": latest["seen_at"].strftime("%Y-%m-%d"),
                   "where": str(latest.get("where", "") or ""), "percentile": pct},
        "lowest": {"price": r(low["price"]), "when": low["seen_at"].strftime("%Y-%m-%d"), "where": str(low.get("where", "") or "")},
        "highest": r(df["price"].max()), "median": r(df["price"].median()),
        "trend_per_week": r(trend), "signal": signal,
        "note": "few prices seen yet: treat the signal as rough" if len(df) < 5 else "",
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
