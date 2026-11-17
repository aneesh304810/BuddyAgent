"""Health as ranges: the owner's health measurements turned into "below, about or above your usual" by plain rules.

Why. The Privacy Gateway sends no health information to any AI model. With that alone Buddy cannot say anything
about the owner's sleep or recovery. This is the middle the owner chose on 17 November 2026: when they switch it on
from the server, Claude (and only Claude) may be given ranges and trends, never the values behind them.

What a model receives, and nothing else:

    [health range] sleep duration: latest below usual; week about usual; trend falling; 5 of 7 days below usual, 0 above

    latest   the most recent day against the owner's own usual (the middle value of the earlier days)
    week     the last seven days' average against the same usual
    trend    the last three days against the four before them
    n of 7   how many of the last seven days were below, and above, the usual

No value, unit, date, name, age or place is in it. "Usual" is the owner's own, so the line does not even say
whether a number is high or low for people in general.

How it stays that way. Every line is built here from a fixed list of measures and a fixed list of words, and the
Privacy Gateway lets a line through only if it matches that exact form (LINE). Text that merely starts with the
marker but says anything else is treated as health information and cut like any other.

What it does not hide: that the owner tracks these measures, and which way each is going. And the request still
goes out under the owner's own account, so the provider can tell all of it comes from one person
(docs/PRIVACY-GATEWAY.md). Free text about health (symptoms, medicines, food) is never sent, in this mode or any.
"""

import os
import re
from statistics import median, pstdev

MARK = "[health range]"
SETTING = "BUDDY_HEALTH_RANGES"   # "on": Claude may be given ranges. Anything else, or unset: nothing about health.
MEASURES = {  # the field in the day's numbers -> the words a model sees
    "sleep_minutes": "sleep duration", "sleep_score": "sleep score", "deep_minutes": "deep sleep",
    "rem_minutes": "REM sleep", "readiness": "readiness", "hrv": "heart rate variability",
    "lowest_hr": "lowest night heart rate", "resting_hr": "resting heart rate", "temp_deviation": "body temperature",
    "activity_score": "activity score", "steps": "steps", "active_minutes": "active minutes",
    "stress_minutes": "time under stress",
}
BANDS = ("below usual", "about usual", "above usual", "no reading")
TRENDS = ("rising", "falling", "steady", "unknown")
HEAD = (f"{MARK} Ranges against the owner's own usual, worked out by rules. No values, dates or identity are "
        "included, and none are available: do not ask for them or guess them.")
NONE = f"{MARK} Not enough days of readings yet to say what is usual."
LINE = re.compile(r"^\[health range\] (?:" + "|".join(re.escape(v) for v in MEASURES.values()) + r"): latest (?:"
                  + "|".join(BANDS) + r"); week (?:" + "|".join(BANDS) + r"); trend (?:" + "|".join(TRENDS)
                  + r"); [0-7] of 7 days below usual, [0-7] above$")
MIN_BASELINE, WEEK, LOOK_BACK = 7, 7, 60


def on(env=None) -> bool:
    """Has the owner switched ranges on, on the server? (sudo bash /opt/buddy/deploy/set.sh BUDDY_HEALTH_RANGES)"""
    return ((env if env is not None else os.environ).get(SETTING) or "").strip().lower() == "on"


def allowed(line: str) -> bool:
    """Is this line exactly one of the range lines this module writes? The gateway asks this of every marked line."""
    line = str(line or "").strip()
    return line in (HEAD, NONE) or bool(LINE.match(line))


def _band(value, usual: float, spread: float) -> str:
    if value is None:
        return "no reading"
    edge = max(spread * 0.6, abs(usual) * 0.04, 1e-9)   # nearer than this to the usual is "about usual"
    return "below usual" if value < usual - edge else "above usual" if value > usual + edge else "about usual"


def lines(days: dict[str, dict]) -> list[str]:
    """The range lines for the day's numbers ({"YYYY-MM-DD": {field: value}}, any order)."""
    order = sorted(days)
    out = []
    for field, label in MEASURES.items():
        series = [days[d].get(field) for d in order]
        series = [v if isinstance(v, (int, float)) and not isinstance(v, bool) else None for v in series]
        week, before = series[-WEEK:], [v for v in series[:-WEEK] if v is not None]
        seen = [v for v in week if v is not None]
        if len(before) < MIN_BASELINE or not seen:
            continue
        usual, spread = median(before), pstdev(before)
        recent, earlier = [v for v in week[-3:] if v is not None], [v for v in week[:-3] if v is not None]
        if recent and earlier:
            gap, edge = sum(recent) / len(recent) - sum(earlier) / len(earlier), max(spread * 0.4, abs(usual) * 0.03, 1e-9)
            trend = "rising" if gap > edge else "falling" if gap < -edge else "steady"
        else:
            trend = "unknown"
        bands = [_band(v, usual, spread) for v in seen]
        out.append(f"{MARK} {label}: latest {_band(week[-1], usual, spread)}; week "
                   f"{_band(sum(seen) / len(seen), usual, spread)}; trend {trend}; "
                   f"{bands.count('below usual')} of 7 days below usual, {bands.count('above usual')} above")
    return out


def brief(memory) -> str:
    """What Claude may be given about the owner's health when ranges are on: the head line and one line a measure."""
    from . import coach  # noqa: PLC0415  the merged daily numbers from the ring and the phone
    got = lines(coach._data(memory, LOOK_BACK))
    return "\n".join([HEAD, *got]) if got else NONE
