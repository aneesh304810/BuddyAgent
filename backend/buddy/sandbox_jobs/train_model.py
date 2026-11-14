"""Train and test a small model on Buddy's judged calls. Runs in the sandbox, offline; plain Python, no libraries.

Input  input/rows.json   {"domain": "...", "rows": [{"when": "YYYY-MM-DD", "x": {feature: number}, "y": 0 or 1}]}
Output output/model.json the model (a logistic regression: one weight per feature), how it did on calls it had
                         not seen, and whether it earned its place.

How it is kept honest:
  - The rows are sorted by date. The model learns from the oldest 70% and is tested on the newest 30%, which it
    never saw: the same order things happen in real life (no peeking at the future).
  - It is compared with the simplest possible rival: always saying the average rate. A model that can't beat
    that on unseen calls has learned nothing.
  - It is promoted only if, on the unseen calls, its probabilities are better than that rival's (Brier score
    lower by 0.005 or more) AND the calls it likes (55% or more) turned out right at least 5 points more often
    than calls overall, with 15 or more such calls and 40 or more unseen calls in all.
  - A small penalty on large weights (L2) keeps it from memorising noise.
"""

import json
import math
import sys
from pathlib import Path

TEST_SHARE, LIKES, MIN_TEST, MIN_LIKED = 0.3, 0.55, 40, 15
BRIER_GAIN, RATE_GAIN = 0.005, 0.05


def _sig(z: float) -> float:
    return 1 / (1 + math.exp(-max(-30.0, min(30.0, z))))


def fit(rows: list[dict], steps: int = 600, rate: float = 0.2, l2: float = 0.5) -> dict:
    """Standardise, then gradient descent on the log loss with an L2 penalty."""
    names = sorted({k for r in rows for k, v in r["x"].items() if isinstance(v, (int, float))})
    n = len(rows)
    mean = {k: sum(float(r["x"].get(k) or 0) for r in rows) / n for k in names}
    std = {k: math.sqrt(sum((float(r["x"].get(k) or 0) - mean[k]) ** 2 for r in rows) / n) for k in names}
    names = [k for k in names if std[k] > 1e-9]  # a feature that never varies teaches nothing
    xs = [[(float(r["x"].get(k) or 0) - mean[k]) / std[k] for k in names] for r in rows]
    ys = [int(r["y"]) for r in rows]
    w, b = [0.0] * len(names), 0.0
    for _ in range(steps):
        gw, gb = [0.0] * len(names), 0.0
        for x, y in zip(xs, ys):
            err = _sig(b + sum(wi * xi for wi, xi in zip(w, x))) - y
            gb += err
            for i, xi in enumerate(x):
                gw[i] += err * xi
        b -= rate * gb / n
        w = [wi - rate * (gw[i] / n + l2 * wi / n) for i, wi in enumerate(w)]
    return {"features": names, "mean": {k: mean[k] for k in names}, "std": {k: std[k] for k in names},
            "weights": dict(zip(names, (round(v, 5) for v in w))), "bias": round(b, 5)}


def predict(model: dict, x: dict) -> float:
    z = model["bias"] + sum(model["weights"][k] * ((float(x.get(k) or 0) - model["mean"][k]) / model["std"][k])
                            for k in model["features"])
    return _sig(z)


def evaluate(rows: list[dict]) -> dict:
    rows = sorted(rows, key=lambda r: r["when"])
    cut = int(len(rows) * (1 - TEST_SHARE))
    train, test = rows[:cut], rows[cut:]
    if len(train) < 20 or not test or len({r["y"] for r in train}) < 2:
        return {"status": "not enough", "why": "too few rows, or every outcome was the same", "rows": len(rows)}
    model = fit(train)
    base = sum(r["y"] for r in train) / len(train)
    ps = [predict(model, r["x"]) for r in test]
    ys = [r["y"] for r in test]
    brier = sum((p - y) ** 2 for p, y in zip(ps, ys)) / len(ys)
    rival = sum((base - y) ** 2 for y in ys) / len(ys)
    liked = [y for p, y in zip(ps, ys) if p >= LIKES]
    overall = sum(ys) / len(ys)
    liked_rate = sum(liked) / len(liked) if liked else None
    checks = {
        f"{MIN_TEST} or more unseen calls": len(test) >= MIN_TEST,
        "its odds beat always saying the average": rival - brier >= BRIER_GAIN,
        f"{MIN_LIKED} or more calls it liked": len(liked) >= MIN_LIKED,
        "the calls it liked were right 5 points more often": liked_rate is not None and liked_rate - overall >= RATE_GAIN,
    }
    return {"status": "promoted" if all(checks.values()) else "rejected", "model": model, "rows": len(rows),
            "learned_from": len(train), "tested_on": len(test), "first": rows[0]["when"], "last": rows[-1]["when"],
            "right_overall": round(overall, 3), "liked": len(liked),
            "right_when_liked": round(liked_rate, 3) if liked_rate is not None else None,
            "brier": round(brier, 4), "brier_of_the_average": round(rival, 4),
            "accuracy": round(sum((p >= 0.5) == bool(y) for p, y in zip(ps, ys)) / len(ys), 3),
            "checks": checks,
            "strongest": sorted(model["weights"].items(), key=lambda kv: -abs(kv[1]))[:6]}


def main(folder: str) -> dict:
    root = Path(folder)
    data = json.loads((root / "input" / "rows.json").read_text())
    out = {"domain": data.get("domain", ""), **evaluate(data.get("rows") or [])}
    (root / "output").mkdir(exist_ok=True)
    (root / "output" / "model.json").write_text(json.dumps(out))
    return out


if __name__ == "__main__":
    got = main(sys.argv[1] if len(sys.argv) > 1 else ".")
    print(got.get("status"), got.get("rows"))
