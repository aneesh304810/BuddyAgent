#!/usr/bin/env python3
"""Calendar events -> free slots across time zones, and conflicts, as JSON. No network, no AI."""
import argparse
import json
from datetime import datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo


def parse(value: str) -> datetime:
    t = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return t if t.tzinfo else t.replace(tzinfo=ZoneInfo("UTC"))


def hhmm(value: str) -> time:
    h, m = value.split(":")
    return time(int(h), int(m))


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("input")
    p.add_argument("--tz", default="UTC")
    p.add_argument("--also", default="")
    p.add_argument("--days", type=int, default=7)
    p.add_argument("--from", dest="start", default="08:00")
    p.add_argument("--to", dest="end", default="21:00")
    p.add_argument("--their-from", default="08:00")
    p.add_argument("--their-to", default="22:00")
    p.add_argument("--min", type=int, default=30)
    p.add_argument("--now", default="")
    a = p.parse_args()
    mine = ZoneInfo(a.tz)
    theirs = ZoneInfo(a.also) if a.also else None
    data = json.loads(Path(a.input).read_text(encoding="utf-8"))
    events = sorted(((parse(e["start"]), parse(e.get("end") or e["start"]), str(e.get("title", "")))
                     for e in (data if isinstance(data, list) else data.get("events", []))), key=lambda e: e[0])
    now = parse(a.now) if a.now else datetime.now(mine)
    conflicts = [{"a": x[2], "b": y[2], "at": max(x[0], y[0]).astimezone(mine).strftime("%a %d %b %H:%M")}
                 for i, x in enumerate(events) for y in events[i + 1:] if y[0] < x[1] and x[0] < y[1]]
    slots, busy_by_day = [], {}
    for d in range(a.days):
        day = (now.astimezone(mine) + timedelta(days=d)).date()
        lo = datetime.combine(day, hhmm(a.start), mine)
        hi = datetime.combine(day, hhmm(a.end), mine)
        lo = max(lo, now.astimezone(mine)) if d == 0 else lo
        windows = [(lo, hi)]
        if theirs:  # also inside their waking hours (which may fall on their next or previous day)
            limited = []
            for w0, w1 in windows:
                for shift in (-1, 0, 1):
                    their_day = (w0.astimezone(theirs) + timedelta(days=shift)).date()
                    t0 = datetime.combine(their_day, hhmm(a.their_from), theirs)
                    t1 = datetime.combine(their_day, hhmm(a.their_to), theirs)
                    s, e = max(w0, t0), min(w1, t1)
                    if e > s:
                        limited.append((s, e))
            windows = limited
        busy = [(s, e) for s, e, _ in events if e > lo and s < hi]
        merged = []  # overlapping events count once
        for s, e in sorted((max(s, lo), min(e, hi)) for s, e in busy):
            if merged and s <= merged[-1][1]:
                merged[-1][1] = max(merged[-1][1], e)
            else:
                merged.append([s, e])
        busy_by_day[day.isoformat()] = round(sum((e - s).total_seconds() for s, e in merged) / 3600, 1)
        for w0, w1 in windows:
            cursor = w0
            for s, e in sorted(busy):
                if s > cursor and (s - cursor) >= timedelta(minutes=a.min) and s <= w1:
                    slots.append((cursor, min(s, w1)))
                cursor = max(cursor, e)
            if w1 - cursor >= timedelta(minutes=a.min):
                slots.append((cursor, w1))
    out = []
    for s, e in sorted(set(slots)):
        item = {"you": f"{s.astimezone(mine):%a %d %b %H:%M}-{e.astimezone(mine):%H:%M}",
                "minutes": int((e - s).total_seconds() // 60)}
        if theirs:
            item["them"] = f"{s.astimezone(theirs):%a %d %b %H:%M}-{e.astimezone(theirs):%H:%M}"
        out.append(item)
    print(json.dumps({"slots": out[:40], "conflicts": conflicts[:20],
                      "busiest_day": max(busy_by_day, key=busy_by_day.get) if busy_by_day else None,
                      "hours_busy_by_day": busy_by_day}, ensure_ascii=False))


if __name__ == "__main__":
    main()
