#!/usr/bin/env python3
"""CSV/Excel -> filtered, grouped, totalled rows as JSON (pandas). No network, no AI."""
import argparse
import json

import pandas as pd

OPS = {"==": lambda s, v: s == v, "!=": lambda s, v: s != v, ">": lambda s, v: s > v, ">=": lambda s, v: s >= v,
       "<": lambda s, v: s < v, "<=": lambda s, v: s <= v,
       "contains": lambda s, v: s.astype(str).str.contains(str(v), case=False, na=False)}


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("input")
    p.add_argument("--spec", default="")
    p.add_argument("--sheet", default=0)
    a = p.parse_args()
    df = pd.read_excel(a.input, sheet_name=a.sheet) if a.input.endswith((".xlsx", ".xls")) else pd.read_csv(a.input)
    if not a.spec:
        print(json.dumps({"rows": len(df), "columns": {c: str(t) for c, t in df.dtypes.items()},
                          "first_rows": json.loads(df.head(5).to_json(orient="records", date_format="iso"))},
                         ensure_ascii=False))
        return
    spec = json.loads(a.spec)
    for col, op, value in spec.get("where", []):
        df = df[OPS[op](df[col], value)]
    if spec.get("group_by"):
        df = df.groupby(spec["group_by"]).agg(spec.get("agg") or "count").reset_index()
    elif spec.get("agg"):
        df = df.agg(spec["agg"]).to_frame("value").reset_index()
    if spec.get("columns"):
        df = df[spec["columns"]]
    for key in reversed(spec.get("sort", [])):
        df = df.sort_values(key.lstrip("-"), ascending=not key.startswith("-"), kind="stable")
    df = df.head(min(int(spec.get("limit", 50)), 200))
    print(json.dumps({"rows": json.loads(df.to_json(orient="records", date_format="iso"))}, ensure_ascii=False))


if __name__ == "__main__":
    main()
