#!/usr/bin/env python3
"""Bookings -> a checked travel timeline as JSON. No network, no AI."""
import argparse
import json
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

MIN_CONNECTION = {False: 60, True: 90}  # minutes: domestic, international
LONG_LAYOVER = 6 * 60


def when(value: str, tz: str | None) -> datetime:
    t = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if t.tzinfo is None:
        t = t.replace(tzinfo=ZoneInfo(tz or "UTC"))
    return t


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("input")
    a = p.parse_args()
    data = json.loads(Path(a.input).read_text(encoding="utf-8"))
    items = data if isinstance(data, list) else (data.get("bookings") or
                                                 data.get("legs", []) + [{**h, "type": "hotel"} for h in data.get("hotels", [])])
    legs, hotels, problems, timeline = [], [], [], []
    for b in items:
        if b.get("type", "flight") in ("hotel", "stay", "airbnb"):
            hotels.append({**b, "_in": date.fromisoformat(str(b["check_in"])[:10]),
                           "_out": date.fromisoformat(str(b["check_out"])[:10])})
        else:
            legs.append({**b, "name": b.get("name") or b.get("ref") or f"{b.get('from', '')}-{b.get('to', '')}",
                         "_dep": when(b["depart"], b.get("depart_tz")), "_arr": when(b["arrive"], b.get("arrive_tz"))})
    legs.sort(key=lambda x: x["_dep"])
    hotels.sort(key=lambda x: x["_in"])
    for leg in legs:
        hours = (leg["_arr"] - leg["_dep"]).total_seconds() / 3600
        timeline.append({"when": leg["_dep"].strftime("%a %d %b %H:%M"), "what": f"{leg.get('type', 'flight')} "
                         f"{leg['name']} {leg.get('from', '')} -> {leg.get('to', '')}".strip(),
                         "arrive": leg["_arr"].strftime("%a %d %b %H:%M") + " local", "hours": round(hours, 1), "_t": leg["_dep"]})
        if hours <= 0:
            problems.append({"what": leg.get("name", ""), "why": "arrives before it departs: check the times and time zones"})
        if leg["_dep"].hour < 6:
            problems.append({"what": leg.get("name", ""), "why": f"early start: departs {leg['_dep'].strftime('%H:%M')} local"})
        if leg["_arr"].date() > leg["_dep"].date():
            problems.append({"what": leg.get("name", ""), "why": "overnight: arrives the next day"})
    for x, y in zip(legs, legs[1:]):
        gap = (y["_dep"] - x["_arr"]).total_seconds() / 60
        same_place = str(x.get("to", "")).upper() == str(y.get("from", "")).upper()
        intl = bool(x.get("international") or y.get("international"))
        if gap < 0:
            problems.append({"what": f"{x.get('name', '')} -> {y.get('name', '')}", "why": "overlap: the next one leaves "
                             "before this one arrives"})
        elif same_place and gap < MIN_CONNECTION[intl]:
            problems.append({"what": f"{x.get('name', '')} -> {y.get('name', '')}", "why": f"tight connection: {int(gap)} "
                             f"min at {x.get('to', '')} (allow {MIN_CONNECTION[intl]}+" + ("" if intl else ", 90+ if it's international") + ")"})
        elif same_place and gap > LONG_LAYOVER and gap < 24 * 60:
            problems.append({"what": f"{x.get('name', '')} -> {y.get('name', '')}", "why": f"long layover: {gap / 60:.1f} h "
                             f"at {x.get('to', '')}"})
        elif not same_place and gap < 24 * 60:
            problems.append({"what": f"{x.get('name', '')} -> {y.get('name', '')}", "why": f"arrive at {x.get('to', '')} "
                             f"but the next leaves from {y.get('from', '')}: self-transfer between airports"})
    for h in hotels:
        timeline.append({"when": h["_in"].strftime("%a %d %b"), "what": f"hotel {h.get('name', '')} in "
                         f"{h.get('city', '')} until {h['_out'].strftime('%a %d %b')}".replace(" in  ", " "),
                         "_t": datetime.combine(h["_in"], datetime.min.time().replace(hour=15), ZoneInfo(h.get("tz") or "UTC"))})
    for x, y in zip(hotels, hotels[1:]):
        if y["_in"] < x["_out"]:
            problems.append({"what": f"{x.get('name', '')} / {y.get('name', '')}", "why": "two hotels booked for the same night"})
        elif y["_in"] > x["_out"]:
            problems.append({"what": f"{x['_out']} to {y['_in']}", "why": f"{(y['_in'] - x['_out']).days} night(s) with no hotel"})
    # A night away with no hotel: arriving somewhere and leaving again a later day, no booking covering it.
    for x, y in zip(legs, legs[1:]):
        night = x["_arr"].date()
        while night < y["_dep"].date():
            if not any(h["_in"] <= night < h["_out"] for h in hotels) and hotels:
                problems.append({"what": f"night of {night.strftime('%a %d %b')} in {x.get('to', '')}",
                                 "why": "no hotel booked for this night"})
            night += timedelta(days=1)
    timeline.sort(key=lambda t: t.pop("_t"))
    print(json.dumps({"legs": len(legs), "hotels": len(hotels), "timeline": timeline, "problems": problems,
                      "looks_fine": not problems}, ensure_ascii=False))


if __name__ == "__main__":
    main()
