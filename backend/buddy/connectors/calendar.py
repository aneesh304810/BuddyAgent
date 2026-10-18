"""Calendar connector: Google Calendar (or Outlook, iCloud) through the private
iCal address. Read-only by nature, and needs no developer setup.

Google Calendar: Settings → your calendar → Integrate calendar → "Secret address
in iCal format". Put one or more of these, comma-separated, in
BUDDY_CALENDAR_ICS_URLS.
"""

import asyncio
import urllib.request
from datetime import date, datetime, time, timedelta

import icalendar
import recurring_ical_events

from ..tools import user_tz
from .base import Connector, Record

LOOK_BACK_DAYS = 1
LOOK_AHEAD_DAYS = 30
MAX_BYTES = 5_000_000


def _when(value) -> tuple[datetime, bool]:
    """An event time as an aware datetime, plus whether it was an all-day date."""
    if isinstance(value, datetime):
        return (value if value.tzinfo else value.replace(tzinfo=user_tz())), False
    if isinstance(value, date):
        return datetime.combine(value, time(0, 0), tzinfo=user_tz()), True
    raise ValueError(f"unexpected date {value!r}")


def parse_ics(raw: bytes, start: datetime, end: datetime) -> list[Record]:
    calendar = icalendar.Calendar.from_ical(raw)
    records = []
    for event in recurring_ical_events.of(calendar).between(start, end):
        if str(event.get("STATUS", "")).upper() == "CANCELLED":
            continue
        begins, all_day = _when(event.decoded("DTSTART"))
        ends = _when(event.decoded("DTEND"))[0] if event.get("DTEND") else None
        uid = str(event.get("UID", "")) or str(event.get("SUMMARY", ""))
        where = str(event.get("LOCATION", "") or "")
        notes = str(event.get("DESCRIPTION", "") or "")[:500]
        records.append(Record(
            external_id=f"{uid}@{begins.isoformat()}",  # one record per occurrence
            kind="event",
            title=str(event.get("SUMMARY", "") or "(no title)"),
            body="\n".join(part for part in (f"Where: {where}" if where else "", notes) if part),
            start_at=begins,
            end_at=ends,
            data={"all_day": all_day, "location": where},
        ))
    return records


class CalendarConnector(Connector):
    name = "calendar"
    title = "Calendar"
    description = "Your upcoming events, from the calendar's private iCal address."
    required = {"BUDDY_CALENDAR_ICS_URLS": "secret iCal address(es), comma-separated"}
    every_minutes = 15
    snapshot = True
    keep_days = LOOK_AHEAD_DAYS

    def urls(self) -> list[str]:
        urls = []
        for url in self.setting("BUDDY_CALENDAR_ICS_URLS").split(","):
            url = url.strip()
            if url.startswith("webcal://"):
                url = "https://" + url[len("webcal://"):]
            if url:
                if not url.startswith("https://"):
                    raise ValueError("Calendar addresses must start with https:// or webcal://")
                urls.append(url)
        return urls

    async def fetch(self) -> list[Record]:
        now = datetime.now(user_tz())
        start, end = now - timedelta(days=LOOK_BACK_DAYS), now + timedelta(days=LOOK_AHEAD_DAYS)
        records: list[Record] = []
        for url in self.urls():
            raw = await asyncio.to_thread(_download, url)
            records += parse_ics(raw, start, end)
        return records


def _download(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "Buddy/1.0"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read(MAX_BYTES)
