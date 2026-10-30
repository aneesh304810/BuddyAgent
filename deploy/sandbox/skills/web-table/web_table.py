#!/usr/bin/env python3
"""Web page -> CSV of its HTML tables (pandas.read_html)."""
import argparse
import io
from pathlib import Path

import pandas as pd
import requests


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("url")
    p.add_argument("out")
    p.add_argument("--index", type=int, default=0)
    p.add_argument("--all", action="store_true")
    a = p.parse_args()
    if not a.url.startswith(("https://", "http://")):
        raise SystemExit("Give a web address.")
    html = requests.get(a.url, timeout=30, headers={"User-Agent": "Mozilla/5.0 (compatible; Buddy)"}).text
    tables = pd.read_html(io.StringIO(html))
    if not tables:
        raise SystemExit("No tables on that page.")
    for i, t in enumerate(tables):
        print(f"[{i}] {t.shape[0]} rows: {', '.join(map(str, t.columns[:8]))}")
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    if a.all:
        for i, t in enumerate(tables):
            t.to_csv(out.with_name(f"table-{i}.csv"), index=False)
        print(f"Wrote {len(tables)} tables next to {out}")
    else:
        tables[a.index].to_csv(out, index=False)
        print(f"Wrote table {a.index} to {out}")


if __name__ == "__main__":
    main()
