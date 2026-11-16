"""Train and test a small model on Buddy's judged calls. Runs in the sandbox, offline; plain Python, no libraries.

Input  input/rows.json   {"domain": "...", "rows": [{"when": "YYYY-MM-DD", "x": {feature: number}, "y": 0 or 1}]}
                         A row may also carry "rival" (0 or 1: what a simple rule would have guessed) and
                         "regime" ("up" or "down": what the market did over the row's outcome window).
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
  - Its odds must mean what they say: across the unseen calls, grouped by the odds it gave, the share that came
    true must sit within 10 points of those odds on average (calibration).
  - Where the rows carry a simple rule's guess (for themes: "companies already rising keep rising"), the model
    must be more accurate than that rule on the unseen calls, by 2 points or more.
  - Where the rows carry the market's direction, the unseen calls must include 10 or more from rising markets
    and 10 or more from falling ones, and the calls it liked must not do worse than calls overall in either.
    A model tested only in a rising market has not been tested.
  - A small penalty on large weights (L2) keeps it from memorising noise.
  - A gap between learning and testing. A row that was still open when the first test row began (it carries
    "opened") shares days with the test period, so it is dropped from learning: otherwise the model would be
    tested partly on what it had already seen.
  - Walk-forward. With 200 or more rows the test is repeated over four later periods in turn, each time learning
    only from what came before it. The calls it liked must have done at least as well as calls overall in three
    of the periods that had enough of them: one lucky stretch is not a pass.
"""

import json
import math
import sys
from pathlib import Path

TEST_SHARE, LIKES, MIN_TEST, MIN_LIKED = 0.3, 0.55, 40, 15
BRIER_GAIN, RATE_GAIN = 0.005, 0.05
CALIBRATION, RIVAL_GAIN, MIN_REGIME = 0.10, 0.02, 10
BUCKETS = ((0.0, 0.4), (0.4, 0.5), (0.5, 0.6), (0.6, 1.01))
WALK_MIN_ROWS, WALK_PERIODS, WALK_LIKED, WALK_PASS = 200, 4, 5, 3


def before(train: list[dict], test: list[dict]) -> list[dict]:
    """The training rows that had finished before the test period began (see the note above: the gap)."""
    starts = [r["opened"] for r in test if r.get("opened")]
    if not starts:
        return train
    start = min(starts)
    return [r for r in train if r["when"] < start]


def walk_forward(rows: list[dict]) -> dict:
    """Four later periods, each tested by a model that learned only from the rows before it."""
    n, out = len(rows), []
    edges = [int(n * (0.4 + 0.15 * i)) for i in range(WALK_PERIODS + 1)]
    for a, b in zip(edges, edges[1:]):
        test = rows[a:b]
        train = before(rows[:a], test)
        if len(train) < 20 or not test or len({r["y"] for r in train}) < 2:
            continue
        model = fit(train, steps=300)
        ps = [predict(model, r["x"]) for r in test]
        liked = [r["y"] for p, r in zip(ps, test) if p >= LIKES]
        overall = sum(r["y"] for r in test) / len(test)
        out.append({"from": test[0]["when"], "to": test[-1]["when"], "calls": len(test), "right": round(overall, 3),
                    "liked": len(liked), "right_when_liked": round(sum(liked) / len(liked), 3) if liked else None,
                    "held": len(liked) >= WALK_LIKED and sum(liked) / len(liked) >= overall})
    judged = [p for p in out if p["liked"] >= WALK_LIKED]
    return {"periods": out, "judged": len(judged), "held": sum(1 for p in judged if p["held"])}


def calibration(ps: list[float], ys: list[int]) -> dict:
    """Grouped by the odds given: how many, the average odds, and the share that came true. gap: the average
    distance between the two, weighted by how many calls are in each group."""
    table = []
    for low, high in BUCKETS:
        group = [(p, y) for p, y in zip(ps, ys) if low <= p < high]
        if group:
            table.append({"odds": f"{low:.0%} to {min(high, 1):.0%}", "calls": len(group),
                          "said": round(sum(p for p, _ in group) / len(group), 3),
                          "came_true": round(sum(y for _, y in group) / len(group), 3)})
    gap = sum(t["calls"] * abs(t["said"] - t["came_true"]) for t in table) / len(ys) if ys else 1.0
    return {"table": table, "gap": round(gap, 3)}


def regimes(test: list[dict], ps: list[float]) -> dict:
    """Per market direction: unseen calls, how often they came true, and how often the liked ones did."""
    out = {}
    for name in ("up", "down"):
        group = [(p, r["y"]) for p, r in zip(ps, test) if r.get("regime") == name]
        liked = [y for p, y in group if p >= LIKES]
        out[name] = {"calls": len(group), "right": round(sum(y for _, y in group) / len(group), 3) if group else None,
                     "liked": len(liked), "right_when_liked": round(sum(liked) / len(liked), 3) if liked else None}
    return out


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
    dropped = len(train)
    train = before(train, test)
    dropped -= len(train)
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
    cal = calibration(ps, ys)
    checks["its odds mean what they say (within 10 points)"] = cal["gap"] <= CALIBRATION
    accuracy = sum((p >= 0.5) == bool(y) for p, y in zip(ps, ys)) / len(ys)
    extra = {"calibration": cal}
    if any("rival" in r for r in test):
        rival_right = sum(int(r.get("rival", 0)) == r["y"] for r in test) / len(test)
        extra["simple_rule_accuracy"] = round(rival_right, 3)
        checks["more accurate than the simple rule by 2 points"] = accuracy - rival_right >= RIVAL_GAIN
    if any(r.get("regime") for r in test):
        by = regimes(test, ps)
        extra["by_market"] = by
        checks[f"tested in rising and falling markets ({MIN_REGIME} or more of each)"] = all(
            by[name]["calls"] >= MIN_REGIME for name in by)
        checks["the calls it liked held up in both"] = all(
            b["right_when_liked"] is None or b["right_when_liked"] >= b["right"] for b in by.values())
    extra["left_out_for_the_gap"] = dropped
    if len(rows) >= WALK_MIN_ROWS:
        walk = walk_forward(rows)
        extra["walk_forward"] = walk
        checks[f"held up in {WALK_PASS} of {WALK_PERIODS} later periods, each tested only on what came after"] = (
            walk["judged"] >= WALK_PASS and walk["held"] >= WALK_PASS)
    return {"status": "promoted" if all(checks.values()) else "rejected", "model": model, "rows": len(rows), **extra,
            "learned_from": len(train), "tested_on": len(test), "first": rows[0]["when"], "last": rows[-1]["when"],
            "right_overall": round(overall, 3), "liked": len(liked),
            "right_when_liked": round(liked_rate, 3) if liked_rate is not None else None,
            "brier": round(brier, 4), "brier_of_the_average": round(rival, 4),
            "accuracy": round(accuracy, 3),
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
