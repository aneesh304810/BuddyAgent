"""Today: the short strip at the top of the app's home screen, and the number on the bell.

Three things, read from what Buddy already keeps on the server, with no AI model and no outside call:

  next    the next thing with a time on it: a reminder, or an appointment found in your email (7 days ahead)
  due     the next bill or card payment due within 7 days
  alerts  how many messages Buddy sent on its own since you last opened the Alerts screen (silenced kinds
          are not counted)
  use     what to use up in My kitchen: the product with the nearest date on its pack, within two days or past
          (groceries.py)

Each part says where it came from. A part with nothing to show is left out; a part that fails is left out
and logged, so the strip never blocks the conversation.
"""

import logging
from datetime import date, datetime, timedelta, timezone

from .memory import Memory
from .tools import local_now, user_tz

log = logging.getLogger("buddy.today")
SEEN_KEY = "today:alerts_seen"
DAYS = 7


def _day_words(day: date, today: date) -> str:
    gap = (day - today).days
    return "Today" if gap == 0 else "Tomorrow" if gap == 1 else f"{day:%A}" if gap < 7 else f"{day:%b %d}"


def _next(memory: Memory, now: datetime) -> dict | None:
    today, found = now.date(), []
    until = now + timedelta(days=DAYS)
    for r in memory.upcoming_reminders():
        at = datetime.fromisoformat(r["due_at"]).astimezone(user_tz())
        if now - timedelta(hours=1) <= at <= until:
            found.append((at, {"title": r["text"], "when": f"{_day_words(at.date(), today)}, {at:%I:%M %p}".replace(", 0", ", "),
                               "source": "Your reminder"}))
    try:
        from . import lifemail  # noqa: PLC0415
        for r in lifemail.upcoming(memory, DAYS):
            if r["kind"] != "appointment":
                continue
            day = date.fromisoformat(r["day"])
            at = datetime.combine(day, datetime.min.time(), tzinfo=user_tz()) + timedelta(hours=23, minutes=59)
            found.append((at, {"title": r["title"], "when": _day_words(day, today) + (f", {r['time']}" if r["time"] else ""),
                               "source": "From your email"}))
    except Exception as e:
        log.info("today: email appointments unavailable (%s)", type(e).__name__)
    return min(found, key=lambda f: f[0])[1] if found else None


def _due(memory: Memory, now: datetime) -> dict | None:
    today, found = now.date(), []
    until = today + timedelta(days=DAYS)
    try:
        from .finance import Finance  # noqa: PLC0415
        fin = Finance(memory)
        names = set()
        for c in fin.cards():
            if c.get("next_due") and c.get("statement_balance") and today <= date.fromisoformat(c["next_due"]) <= until:
                names.add(c["name"].lower())
                found.append((c["next_due"], f"{c['name']} payment", c["statement_balance"], "Your card statement"))
        for b in fin.money.open_bills(DAYS):
            if b["biller"].lower() not in names:
                found.append((b["due_date"], f"{b['biller']} bill", b["amount"], "From your email"))
    except Exception as e:
        log.info("today: bills unavailable (%s)", type(e).__name__)
    if not found:
        return None
    day, title, amount, source = min(found, key=lambda f: f[0])
    when = date.fromisoformat(day)
    return {"title": title, "when": "Overdue" if when < today else _day_words(when, today), "amount": amount,
            "more": len(found) - 1, "source": source}


def _alerts(memory: Memory, now: datetime) -> dict:
    from . import attention  # noqa: PLC0415
    attention._ready(memory)
    seen = memory.get(SEEN_KEY) or (now.astimezone(timezone.utc) - timedelta(days=1)).isoformat()
    n = memory.db.execute("SELECT COUNT(*) AS n FROM alerts WHERE at > ? AND level IN ('notify', 'alert')",
                          (seen,)).fetchone()["n"]
    return {"new": int(n), "since": seen}


def _use(memory: Memory, now: datetime) -> dict | None:
    from . import groceries  # noqa: PLC0415
    soon = groceries.use_soon(memory, now)
    if not soon:
        return None
    first = soon[0]
    more = f" and {len(soon) - 1} more" if len(soon) > 1 else ""
    return {"title": groceries._label(first) + more, "when": first["when"], "source": "My kitchen"}


def strip(memory: Memory, now: datetime | None = None) -> dict:
    now = now or local_now()
    out = {"as_of": now.isoformat(timespec="minutes")}
    for name, fn in (("next", _next), ("due", _due), ("alerts", _alerts), ("use", _use)):
        try:
            out[name] = fn(memory, now)
        except Exception:
            log.warning("today: %s failed", name, exc_info=True)
            out[name] = {"new": 0} if name == "alerts" else None
    return out


def seen(memory: Memory, now: datetime | None = None) -> dict:
    """The owner opened the Alerts screen: the bell starts counting again from now."""
    memory.set(SEEN_KEY, (now or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat())
    return {"new": 0}
