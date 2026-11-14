"""Learns price patterns from Buddy's price book. Runs inside the sandbox, offline (no network), on files Buddy wrote
into input/: observations.csv.gz, items.csv.gz (item -> kind), launches.csv.gz, predictions.csv.gz and events.json
(the sale windows for the years in the data). Writes output/patterns.json for Buddy to keep.

Everything is plain statistics with pandas, robust to small and messy data (medians and quantiles, each item
weighted equally, nothing reported without enough items behind it):
  event_discounts  per kind of product and sale (and year): how far prices really fell against the item's own
                   30-day median before the sale
  drop_odds        per kind: the chance that a price falls 10% or more within 14, 30 and 60 days of a given day
  lifecycle        per kind: price as a share of the launch price, by age since launch
  flights          per route: price against days before departure (the cheapest booking window), and by weekday
  hotels           per place: the weekend premium and the price by month
  calibration      how Buddy's own buy-or-wait calls turned out, per decision, against the odds it gave

Standalone on purpose: only pandas and numpy, no Buddy code, so it runs in any sandbox workspace.
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

MIN_ITEMS = 3          # a pattern needs at least this many different items (or routes, places) behind it
MIN_FLIGHT_ROWS = 20
AGE_BUCKETS = [0, 60, 180, 365, 730, 100000]
DAYS_BEFORE = [0, 8, 22, 61, 121, 100000]
DAYS_BEFORE_NAMES = ["0-7", "8-21", "22-60", "61-120", "121+"]


def _read(folder: Path, name: str) -> pd.DataFrame:
    path = folder / f"{name}.csv.gz"
    return pd.read_csv(path) if path.exists() and path.stat().st_size > 0 else pd.DataFrame()


def _q(s: pd.Series) -> dict:
    return {"median": round(float(s.median()), 4), "p25": round(float(s.quantile(0.25)), 4),
            "p75": round(float(s.quantile(0.75)), 4)}


def daily_products(obs: pd.DataFrame, items: pd.DataFrame) -> pd.DataFrame:
    """One price a day per product (the day's lowest), with its kind."""
    p = obs[obs["domain"] == "product"].copy()
    if p.empty:
        return p
    p["day"] = pd.to_datetime(p["seen_at"]).dt.normalize()
    p = p.groupby(["item", "day"], as_index=False)["price"].min()
    kinds = items.set_index("item")["kind"] if not items.empty else pd.Series(dtype=str)
    p["kind"] = p["item"].map(kinds).fillna("other")
    return p


def event_discounts(daily: pd.DataFrame, events: list[dict]) -> dict:
    out: dict = {}
    if daily.empty:
        return out
    for e in events:
        start, end = pd.Timestamp(e["start"]), pd.Timestamp(e["end"])
        for kind in e["for"]:
            sub = daily[daily["kind"] == kind]
            found = []
            for _, g in sub.groupby("item"):
                before = g[(g["day"] >= start - pd.Timedelta(days=30)) & (g["day"] < start)]["price"]
                during = g[(g["day"] >= start) & (g["day"] <= end + pd.Timedelta(days=1))]["price"]
                if len(before) >= 3 and len(during) >= 1:
                    found.append(1 - during.min() / before.median())
            if len(found) >= MIN_ITEMS:  # one result per sale and year, so later runs can merge without counting twice
                out.setdefault(kind, {})[f"{e['name']}|{start.year}"] = {"samples": found, "event": e["name"],
                                                                         "year": int(start.year)}
    for kind, evs in out.items():
        for name, v in evs.items():
            s = pd.Series(v.pop("samples")).clip(-0.5, 0.9)
            evs[name] = {**_q(s), "items": int(len(s)), "event": v["event"], "year": v["year"]}
    return out


def drop_odds(daily: pd.DataFrame, horizons=(14, 30, 60), depth: float = 0.10) -> dict:
    out: dict = {}
    if daily.empty:
        return out
    for kind, sub in daily.groupby("kind"):
        per_h: dict = {}
        for h in horizons:
            item_rates = []
            for _, g in sub.groupby("item"):
                g = g.sort_values("day").set_index("day")["price"]
                if len(g) < 5 or (g.index[-1] - g.index[0]).days < h + 7:
                    continue
                hits = total = 0
                for day, price in g.items():
                    if day + pd.Timedelta(days=h) > g.index[-1]:
                        break
                    future = g[(g.index > day) & (g.index <= day + pd.Timedelta(days=h))]
                    if future.empty:
                        continue
                    total += 1
                    hits += int(future.min() <= price * (1 - depth))
                if total:
                    item_rates.append(hits / total)
            if len(item_rates) >= MIN_ITEMS:
                per_h[str(h)] = {"chance": round(float(np.mean(item_rates)), 3), "items": len(item_rates)}
        if per_h:
            out[kind] = per_h
    return out


def lifecycle(daily: pd.DataFrame, launches: pd.DataFrame) -> dict:
    out: dict = {}
    if daily.empty or launches.empty:
        return out
    lz = launches.dropna(subset=["launch_date", "launch_price"])
    lz = lz[lz["launch_price"] > 0]
    m = daily.merge(lz[["item", "launch_date", "launch_price", "kind"]].rename(columns={"kind": "lkind"}), on="item")
    if m.empty:
        return out
    m["age"] = (m["day"] - pd.to_datetime(m["launch_date"])).dt.days
    m = m[m["age"] >= 0]
    m["share"] = m["price"] / m["launch_price"]
    m["bucket"] = pd.cut(m["age"], AGE_BUCKETS, right=False, labels=False)
    for (kind, bucket), g in m.groupby(["lkind", "bucket"]):
        per_item = g.groupby("item")["share"].median()
        if len(per_item) >= MIN_ITEMS:
            out.setdefault(kind, {})[str(int(bucket))] = {"share_of_launch_price": round(float(per_item.median()), 3),
                                                         "products": int(len(per_item))}
    return out


def flights(obs: pd.DataFrame) -> dict:
    out: dict = {}
    f = obs[(obs["domain"] == "flight") & obs["travel_date"].notna()].copy()
    if f.empty:
        return out
    f["seen"] = pd.to_datetime(f["seen_at"]).dt.normalize()
    f["travel"] = pd.to_datetime(f["travel_date"])
    f["days_before"] = (f["travel"] - f["seen"]).dt.days
    f = f[f["days_before"] >= 0]
    for route, g in f.groupby("item"):
        if len(g) < MIN_FLIGHT_ROWS:
            continue
        base = g["price"].median()
        g = g.assign(bucket=pd.cut(g["days_before"], DAYS_BEFORE, right=False, labels=DAYS_BEFORE_NAMES))
        by = g.groupby("bucket", observed=True)["price"].median() / base
        weekday = g.groupby(g["travel"].dt.day_name())["price"].median() / base
        out[route] = {"by_days_before": {str(k): round(float(v), 3) for k, v in by.items()},
                      "cheapest_window": str(by.idxmin()) if len(by) else "",
                      "by_weekday": {k: round(float(v), 3) for k, v in weekday.items()},
                      "median": round(float(base), 2), "prices": int(len(g))}
    return out


def hotels(obs: pd.DataFrame) -> dict:
    out: dict = {}
    h = obs[(obs["domain"] == "hotel") & obs["travel_date"].notna()].copy()
    if h.empty:
        return out
    h["checkin"] = pd.to_datetime(h["travel_date"])
    for place, g in h.groupby("item"):
        if len(g) < MIN_FLIGHT_ROWS:
            continue
        base = g["price"].median()
        weekend = g[g["checkin"].dt.dayofweek.isin([4, 5])]["price"].median()
        weekday = g[~g["checkin"].dt.dayofweek.isin([4, 5])]["price"].median()
        months = g.groupby(g["checkin"].dt.month)["price"].median() / base
        out[place] = {"weekend_premium": round(float(weekend / weekday - 1), 3)
                      if weekend == weekend and weekday == weekday and weekday else None,
                      "by_month": {str(k): round(float(v), 3) for k, v in months.items()},
                      "median": round(float(base), 2), "prices": int(len(g))}
    return out


def calibration(preds: pd.DataFrame) -> dict:
    out: dict = {}
    if preds.empty:
        return out
    judged = preds[preds["outcome"].isin(["right", "wrong"])]
    for decision, g in judged.groupby("decision"):
        out[decision] = {"right": round(float((g["outcome"] == "right").mean()), 3),
                         "odds_given": round(float(g["chance"].mean()), 3), "calls": int(len(g))}
    return out


def deal_rhythms(deals: pd.DataFrame) -> dict:
    """item_deals: each model's deal dates, lowest prices and depths (merged across runs by Buddy); store_rhythm: per
    store and kind, how many deals, how often (median days between deal days), which weekday, how deep; deal_reasons:
    per kind, how many deals each reason explains."""
    out = {"item_deals": {}, "store_rhythm": {}, "deal_reasons": {}}
    if deals.empty:
        return out
    d = deals.dropna(subset=["price"]).copy()
    d["day"] = pd.to_datetime(d["seen_on"])
    d["depth"] = (1 - d["price"] / d["regular_price"]).where(d["regular_price"] > d["price"])
    for model, g in d.groupby("model"):
        days = g.groupby("day").agg(price=("price", "min"), depth=("depth", "max")).reset_index()
        out["item_deals"][str(model)[:80]] = {
            "dates": [x.date().isoformat() for x in days["day"]], "prices": [round(float(p), 2) for p in days["price"]],
            "depths": [round(float(x), 3) if x == x else 0 for x in days["depth"]],
            "stores": sorted(set(str(s) for s in g["store"].dropna()))[:5]}
    for (store, kind), g in d.groupby(["store", "kind"]):
        days = sorted(g["day"].unique())
        if len(days) < 3:
            continue
        gaps = pd.Series(pd.to_datetime(days)).diff().dt.days.dropna()
        out["store_rhythm"][f"{store}|{kind}"] = {
            "kind": kind, "deals": int(len(g)), "every_days": float(gaps.median()) if len(gaps) else None,
            "weekday": pd.Series(pd.to_datetime(days)).dt.day_name().mode().iloc[0],
            "depth": round(float(g["depth"].median()), 3) if g["depth"].notna().any() else None}
    for (kind, reason), g in d.groupby(["kind", "reason"]):
        out["deal_reasons"].setdefault(kind, {})[reason] = {"deals": int(len(g))}
    return out


def main(base: str = ".") -> dict:
    root = Path(base)
    folder = root / "input"
    obs, items = _read(folder, "observations"), _read(folder, "items")
    launches, preds = _read(folder, "launches"), _read(folder, "predictions")
    deals = _read(folder, "deals")
    events = json.loads((folder / "events.json").read_text()) if (folder / "events.json").exists() else []
    daily = daily_products(obs, items) if not obs.empty else pd.DataFrame()
    patterns = {
        "fitted_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "prices": int(len(obs)), "products": int(daily["item"].nunique()) if not daily.empty else 0,
        "event_discounts": event_discounts(daily, events), "drop_odds": drop_odds(daily),
        "lifecycle": lifecycle(daily, launches), "flights": flights(obs) if not obs.empty else {},
        "hotels": hotels(obs) if not obs.empty else {}, "calibration": calibration(preds),
        **deal_rhythms(deals),
    }
    (root / "output").mkdir(exist_ok=True)
    (root / "output" / "patterns.json").write_text(json.dumps(patterns, indent=1))
    return patterns


if __name__ == "__main__":
    got = main(sys.argv[1] if len(sys.argv) > 1 else ".")
    print(json.dumps({k: (len(v) if isinstance(v, dict) else v) for k, v in got.items()}))
