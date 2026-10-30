#!/usr/bin/env python3
"""CSV -> a clean chart PNG."""
import argparse

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402

PALETTE = ["#4C78DB", "#F2A93B", "#3DB389", "#D55E5E", "#8E6BD8", "#5BB8D9"]


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("data")
    p.add_argument("out")
    p.add_argument("--x", required=True)
    p.add_argument("--y", action="append", required=True)
    p.add_argument("--type", choices=["bar", "line", "pie"], default="bar")
    p.add_argument("--title", default="")
    a = p.parse_args()
    df = pd.read_csv(a.data)
    fig, ax = plt.subplots(figsize=(9, 5), dpi=150)
    if a.type == "pie":
        ax.pie(df[a.y[0]], labels=df[a.x], colors=PALETTE, autopct="%1.0f%%", startangle=90,
               wedgeprops={"edgecolor": "white"})
        ax.axis("equal")
    else:
        df.plot(x=a.x, y=a.y, kind=a.type, ax=ax, color=PALETTE[:len(a.y)], legend=len(a.y) > 1,
                **({"marker": "o", "linewidth": 2} if a.type == "line" else {"width": 0.8}))
        ax.spines[["top", "right"]].set_visible(False)
        ax.grid(axis="y", alpha=0.25)
        ax.set_xlabel("")
    ax.set_title(a.title, loc="left", fontweight="bold")
    fig.tight_layout()
    fig.savefig(a.out, facecolor="white")
    print(f"Wrote {a.out}")


if __name__ == "__main__":
    main()
