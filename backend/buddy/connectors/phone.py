"""Phone connectors: what the Buddy app on each of your phones sends in.

Unlike the other connectors these are pushed, not pulled: the app on a phone
sends its notifications as they arrive, and its calendar (including a work
calendar synced to the phone) every half hour. Every Android phone running the
Buddy app and connected to your server shows up, labelled with its name.
"""

from datetime import datetime, timezone

from ..config import settings
from .base import Connector, Record


def _when(value: str) -> datetime:
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return datetime.now(timezone.utc)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


class NotificationsConnector(Connector):
    name = "notifications"
    title = "Phone notifications"
    description = "Notifications from the apps on your phones (Teams, WhatsApp, bank alerts...)."
    every_minutes = 0   # pushed by the phone app
    snapshot = False
    keep_days = 7

    @staticmethod
    def to_record(msg: dict) -> Record:
        device = str(msg.get("device", "phone"))[:40]
        app = str(msg.get("app_name") or msg.get("app") or "app")[:40]
        title = str(msg.get("title", ""))[:200]
        posted = _when(msg.get("posted_at", ""))
        return Record(
            external_id=f"{device}|{msg.get('key', '')}|{posted.isoformat()}",
            kind="notification",
            title=f"{app}: {title}" if title else app,
            body=f"[{device}] {str(msg.get('text', ''))[:1500]}",
            start_at=posted,
            data={"app": str(msg.get("app", ""))[:120], "app_name": app, "device": device},
        )


class PhoneCalendarConnector(Connector):
    name = "phone_calendar"
    title = "Phone calendars"
    description = "Calendars on your phones, including a work calendar synced by Outlook or Teams."
    every_minutes = 0   # pushed by the phone app
    snapshot = False    # the server replaces each phone's events itself (phones differ)
    keep_days = 14

    @staticmethod
    def to_records(msg: dict) -> list[Record]:
        device = str(msg.get("device", "phone"))[:40]
        records = []
        for e in msg.get("events", [])[:500]:
            busy_only = bool(e.get("busy_only"))
            calendar = str(e.get("calendar", ""))[:80]
            start = _when(e.get("start", ""))
            records.append(Record(
                external_id=f"{device}|{e.get('id', '')}|{start.isoformat()}",
                kind="event",
                title="Busy" if busy_only else str(e.get("title", "") or "(no title)")[:200],
                body=f"[{device}] {calendar}" + ("" if busy_only else f"\n{str(e.get('location', ''))[:200]}"),
                start_at=start,
                end_at=_when(e["end"]) if e.get("end") else None,
                data={"calendar": calendar, "device": device, "busy_only": busy_only,
                      "all_day": bool(e.get("all_day"))},
            ))
        return records


def should_announce(msg: dict) -> bool:
    """Read this notification aloud? Only apps you chose, outside quiet hours."""
    wanted = {a.strip().lower() for a in settings.announce_apps.split(",") if a.strip()}
    app = str(msg.get("app", "")).lower()
    name = str(msg.get("app_name", "")).lower()
    if not wanted or not (app in wanted or name in wanted):
        return False
    return not in_quiet_hours()


def in_quiet_hours() -> bool:
    from ..tools import local_now

    span = settings.quiet_hours.strip()
    if not span or "-" not in span:
        return False
    start, end = (tuple(map(int, t.split(":"))) for t in span.split("-", 1))
    now = local_now()
    current = (now.hour, now.minute)
    return start <= current < end if start < end else current >= start or current < end
