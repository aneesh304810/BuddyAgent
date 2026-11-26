"""The owner's personal health model: what, in their own days, tends to come before a better or worse next day.

Learned on the owner's server by plain code (no AI model, nothing sent anywhere), once a week and when they ask,
from the readings Buddy keeps (until the owner deletes them, so this has history to learn from):

  what happened on a day   steps, active minutes, workouts, WHOOP strain, time under stress, and each habit of the
                           health plan the owner ticks off themselves (done or not)
  that night               bedtime (later or earlier)
  the next morning         readiness (Oura), recovery (WHOOP), sleep score, sleep time, HRV, resting heart rate,
                           lowest night heart rate

Two ways, both kept honest:
  Patterns   for each factor and next-morning measure: the days above the factor's middle value against the days
             below (or, for a habit, done against not ticked), the difference in the next morning's average, and how
             often that difference keeps its direction when the days are drawn again at random (200 times). A pattern
             is shown only with at least 10 days on each side, a difference of at least a quarter of the measure's
             usual spread, and the same direction in at least 90% of the redraws.
  A model    for each next-morning measure with at least 60 days: a small linear model (ridge regression) of the
             factors, learned on the oldest 70% of days and tested on the newest 30%, which it never saw. It counts
             only if its error on those days is at least 5% smaller than always guessing the average.

These are patterns in one person's days, not causes, and not medical advice: the page says so. Shown to the owner
only (Health → Trends → Patterns); no AI model is given them (the Privacy Gateway's health rule).
"""

import json
import logging
import random
from datetime import date, datetime, timedelta, timezone
from statistics import mean, median, pstdev

from .memory import Memory
from .tools import local_now

log = logging.getLogger(__name__)

LOOK_BACK, MIN_SIDE, MIN_MODEL_DAYS, DRAWS, KEEP_RUNS = 730, 10, 60, 200, 12
MIN_SHARE, MIN_EFFECT, MIN_GAIN, RIDGE = 0.9, 0.25, 0.05, 1.0
LATEST_KEY, WEEK_KEY = "healthmodel:latest", "healthmodel:week"
FACTORS = {  # field on day d -> words
    "steps": "steps", "active_minutes": "active minutes", "workouts": "workouts", "strain": "WHOOP strain",
    "stress_minutes": "time under stress", "mood": "mood (your journal)", "stress": "stress (your journal)",
    "energy": "energy (your journal)", "cardio_minutes": "cardio minutes", "strength": "a strength session",
    "sodium_mg": "sodium eaten (meals with numbers)",
}
NIGHT = {"bedtime": "a later bedtime"}  # taken from the next morning's row (the night before it)
OUTCOMES = {  # field on day d+1 -> (words, unit, which way is better)
    "readiness": ("readiness", "points", 1), "recovery": ("recovery", "points", 1),
    "sleep_score": ("sleep score", "points", 1), "sleep_minutes": ("sleep", "minutes", 1),
    "hrv": ("heart rate variability", "ms", 1), "resting_hr": ("resting heart rate", "beats a minute", -1),
    "lowest_hr": ("lowest night heart rate", "beats a minute", -1),
    "bp_sys_am": ("morning blood pressure (top number)", "mmHg", -1),
}
SCHEMA = """
CREATE TABLE IF NOT EXISTS health_model_runs (   -- what the personal model learned each time (no daily values)
    id INTEGER PRIMARY KEY, at TEXT NOT NULL, days INTEGER NOT NULL, result TEXT NOT NULL
);
"""


def _ready(memory: Memory) -> None:
    memory.db.executescript(SCHEMA)


def _minutes_after_six(hm) -> float | None:
    """'23:41' -> 341 (minutes after 6 pm; past midnight counts on: '00:30' -> 390)."""
    try:
        h, m = (int(x) for x in str(hm).split(":")[:2])
    except ValueError:
        return None
    total = h * 60 + m - 18 * 60
    return float(total if total >= -6 * 60 else total + 24 * 60)


def _habits(memory: Memory) -> dict[str, str]:
    """The plan's habits the owner ticks off themselves: id -> its words."""
    from . import coach  # noqa: PLC0415
    return {h["id"]: h.get("text") or h["id"] for h in (coach.plan(memory).get("habits") or [])
            if h.get("metric") == "manual"}


def _sodium(memory: Memory, days: int) -> dict[str, dict]:
    """Each day's sodium from the meals that came with their numbers (a lookup or the menu)."""
    from . import coach  # noqa: PLC0415
    coach._ready(memory)
    since = (local_now().date() - timedelta(days=days)).isoformat()
    return {r["day"]: {"sodium_mg": r["mg"]} for r in memory.db.execute(
        "SELECT day, SUM(sodium_mg) AS mg FROM coach_meals WHERE sodium_mg IS NOT NULL AND day >= ? GROUP BY day",
        (since,))}


def table(memory: Memory, days: int = LOOK_BACK) -> list[dict]:
    """One row per day with a next morning: the day's factors, that night's bedtime, the next morning's measures."""
    from . import coach, moodlog  # noqa: PLC0415
    data = coach._data(memory, days)
    for day, felt in moodlog.daily(memory, days).items():  # the journal's day averages, beside the devices' numbers
        data.setdefault(day, {}).update({k: felt[k] for k in moodlog.SCALES if felt.get(k) is not None})
    from . import activity, bodylog  # noqa: PLC0415  blood pressure, cardio and strength, and the meals' sodium
    for source in (bodylog.daily(memory, days), activity.daily(memory, days), _sodium(memory, days)):
        for day, values in source.items():
            data.setdefault(day, {}).update({k: v for k, v in values.items() if v is not None})
    habits = _habits(memory)
    ticked: dict[str, set] = {}
    started = ""
    if habits:
        coach._ready(memory)
        for r in memory.db.execute("SELECT day, habit FROM coach_log"):
            ticked.setdefault(r["day"], set()).add(r["habit"])
        started = min(ticked, default="")
    rows = []
    for day in sorted(data):
        nxt = (date.fromisoformat(day) + timedelta(days=1)).isoformat()
        if nxt not in data:
            continue
        d, n = data[day], data[nxt]
        row = {"day": day}
        for f in FACTORS:
            v = d.get(f)
            row[f] = float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None
        row["bedtime"] = _minutes_after_six(n.get("bedtime")) if n.get("bedtime") else None
        for hid in habits:
            row[f"habit:{hid}"] = (1.0 if hid in ticked.get(day, set()) else 0.0) if started and day >= started else None
        for f in OUTCOMES:
            v = n.get(f)
            row[f"next:{f}"] = float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None
        rows.append(row)
    return rows


def _label(factor: str, habits: dict) -> str:
    if factor.startswith("habit:"):
        return f'"{habits.get(factor[6:], factor[6:])}"'
    return FACTORS.get(factor) or NIGHT.get(factor) or factor


def patterns(rows: list[dict], habits: dict, seed: int = 7) -> list[dict]:
    """Each factor against each next-morning measure, kept only when it clears the bars in the module's notes."""
    rng = random.Random(seed)
    out = []
    factors = [*FACTORS, *NIGHT, *(f"habit:{h}" for h in habits)]
    for factor in factors:
        for outcome, (words, unit, better) in OUTCOMES.items():
            pairs = [(r[factor], r[f"next:{outcome}"]) for r in rows
                     if r.get(factor) is not None and r.get(f"next:{outcome}") is not None]
            if len(pairs) < 2 * MIN_SIDE:
                continue
            habit = factor.startswith("habit:")
            cut = 0.5 if habit else median(p[0] for p in pairs)
            high = [o for f, o in pairs if f > cut]
            low = [o for f, o in pairs if f <= cut]  # the middle value counts as low (a count like workouts is often 0)
            if len(high) < MIN_SIDE or len(low) < MIN_SIDE:
                continue
            spread = pstdev([o for _, o in pairs]) or 0.0
            diff = mean(high) - mean(low)
            if spread <= 0 or abs(diff) < MIN_EFFECT * spread:
                continue
            same = 0
            for _ in range(DRAWS):
                h = [rng.choice(high) for _ in high]
                lo = [rng.choice(low) for _ in low]
                same += (mean(h) - mean(lo) > 0) == (diff > 0)
            share = same / DRAWS
            if share < MIN_SHARE:
                continue
            good = (diff > 0) == (better > 0)
            label = _label(factor, habits)
            when = (f"On days you ticked {label}" if habit else
                    f"After a later bedtime (after {_clock(cut)})" if factor == "bedtime" else
                    f"After days with more {label} (over {_round(cut)})")
            out.append({"factor": factor, "outcome": outcome, "difference": round(diff, 1), "unit": unit,
                        "better": good, "days_high": len(high), "days_low": len(low), "share": round(share, 2),
                        "size": round(abs(diff) / spread, 2),
                        "text": f"{when}, your next {words} was {abs(diff):.1f} {unit} "
                                f"{'higher' if diff > 0 else 'lower'} on average "
                                f"({len(high)} days against {len(low)}; the same way in {share:.0%} of redraws)."})
    out.sort(key=lambda p: -p["size"])
    return out[:12]


def _round(v: float) -> str:
    return f"{v:,.0f}" if abs(v) >= 10 else f"{v:.1f}"


def _clock(minutes_after_six: float) -> str:
    total = (int(round(minutes_after_six)) + 18 * 60) % (24 * 60)
    return f"{total // 60:02d}:{total % 60:02d}"


def _solve(a: list[list[float]], b: list[float]) -> list[float]:
    """Gaussian elimination with partial pivoting (a small, ridge-regularised system)."""
    n = len(b)
    m = [row[:] + [b[i]] for i, row in enumerate(a)]
    for col in range(n):
        piv = max(range(col, n), key=lambda r: abs(m[r][col]))
        m[col], m[piv] = m[piv], m[col]
        if abs(m[col][col]) < 1e-12:
            continue
        for r in range(n):
            if r != col:
                f = m[r][col] / m[col][col]
                for c in range(col, n + 1):
                    m[r][c] -= f * m[col][c]
    return [m[i][n] / m[i][i] if abs(m[i][i]) > 1e-12 else 0.0 for i in range(n)]


def model(rows: list[dict], outcome: str, habits: dict) -> dict | None:
    """A ridge regression of one next-morning measure on the factors, tested on the newest 30% of days."""
    target = f"next:{outcome}"
    usable = [r for r in rows if r.get(target) is not None]
    if len(usable) < MIN_MODEL_DAYS:
        return None
    candidates = [*FACTORS, *NIGHT, *(f"habit:{h}" for h in habits)]
    feats = [f for f in candidates if sum(r.get(f) is not None for r in usable) >= 0.8 * len(usable)]
    if not feats:
        return None
    split = int(len(usable) * 0.7)
    train, test = usable[:split], usable[split:]
    fill = {f: median([r[f] for r in train if r.get(f) is not None] or [0.0]) for f in feats}
    mu = {f: mean([r.get(f) if r.get(f) is not None else fill[f] for r in train]) for f in feats}
    sd = {f: (pstdev([r.get(f) if r.get(f) is not None else fill[f] for r in train]) or 1.0) for f in feats}

    def x(r):
        return [((r.get(f) if r.get(f) is not None else fill[f]) - mu[f]) / sd[f] for f in feats]
    y_mean = mean(r[target] for r in train)
    xs, ys = [x(r) for r in train], [r[target] - y_mean for r in train]
    k = len(feats)
    xtx = [[sum(row[i] * row[j] for row in xs) + (RIDGE if i == j else 0.0) for j in range(k)] for i in range(k)]
    xty = [sum(row[i] * y for row, y in zip(xs, ys)) for i in range(k)]
    w = _solve(xtx, xty)
    guess = [y_mean + sum(wi * xi for wi, xi in zip(w, x(r))) for r in test]
    model_err = mean(abs(g - r[target]) for g, r in zip(guess, test))
    base_err = mean(abs(y_mean - r[target]) for r in test)
    gain = (base_err - model_err) / base_err if base_err else 0.0
    words, unit, better = OUTCOMES[outcome]
    weights = sorted(({"factor": _label(f, habits), "per_spread": round(wi, 2)} for f, wi in zip(feats, w)),
                     key=lambda z: -abs(z["per_spread"]))
    return {"outcome": outcome, "words": words, "unit": unit, "train_days": len(train), "test_days": len(test),
            "error": round(model_err, 2), "average_error": round(base_err, 2), "gain": round(gain, 3),
            "passed": gain >= MIN_GAIN, "weights": weights[:6],
            "text": (f"Your next {words}: on {len(test)} recent days it never saw, the model was off by "
                     f"{model_err:.1f} {unit} on average, against {base_err:.1f} for always guessing your average "
                     f"({gain:+.0%}). " + ("It counts." if gain >= MIN_GAIN else "Not better enough to count yet."))}


def learn(memory: Memory, today: date | None = None) -> dict:
    """Learn from the days held, keep the result (no daily values in it), return it."""
    today = today or local_now().date()
    rows = table(memory)
    habits = _habits(memory)
    days_with_next = sum(any(r.get(f"next:{o}") is not None for o in OUTCOMES) for r in rows)
    found = patterns(rows, habits)
    models = [m for m in (model(rows, o, habits) for o in OUTCOMES) if m]
    result = {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "days": days_with_next,
              "patterns": found, "models": models,
              "needs": (f"{days_with_next} days so far: patterns need at least {2 * MIN_SIDE} days with a next "
                        f"morning, and the model {MIN_MODEL_DAYS}.") if days_with_next < MIN_MODEL_DAYS else "",
              "note": "Patterns in your own days, not causes, and not medical advice. Learned on your server by code; "
                      "no AI model is given any of it."}
    _ready(memory)
    memory.db.execute("INSERT INTO health_model_runs (at, days, result) VALUES (?, ?, ?)",
                      (result["at"], days_with_next, json.dumps(result)))
    memory.db.execute("DELETE FROM health_model_runs WHERE id NOT IN (SELECT id FROM health_model_runs "
                      "ORDER BY id DESC LIMIT ?)", (KEEP_RUNS,))
    memory.db.commit()
    memory.set(LATEST_KEY, json.dumps(result))
    memory.set(WEEK_KEY, f"{today.isocalendar()[0]}-{today.isocalendar()[1]:02d}")
    return result


def latest(memory: Memory) -> dict:
    try:
        return json.loads(memory.get(LATEST_KEY) or "{}")
    except ValueError:
        return {}


def due(memory: Memory, now: datetime | None = None) -> bool:
    """Once a week, on Sunday night (or the first night after)."""
    now = now or local_now()
    week = f"{now.isocalendar()[0]}-{now.isocalendar()[1]:02d}"
    return memory.get(WEEK_KEY) != week and (now.weekday() == 6 or not memory.get(WEEK_KEY))


def forget(memory: Memory) -> None:
    _ready(memory)
    memory.db.execute("DELETE FROM health_model_runs")
    memory.db.commit()
    memory.set(LATEST_KEY, "")
