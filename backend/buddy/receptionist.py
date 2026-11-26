"""Buddy as the owner's receptionist on the calls they miss (calls.py answers them): who the caller is to the owner,
what that caller may be told, what Buddy remembers about them, and what follows the call.

What a caller may hear is decided here, in code, before Claude sees anything (the owner's choices):
- The calendar, by who's calling. Family (a phone contact the owner told Buddy is family) hears what's on and until
  when, with its name; other phone contacts hear what kind of thing it is ("a meeting", "an appointment") and until
  when; anyone else only when the owner is busy and when free. Never where, who with, or the name of a health, money
  or legal appointment.
- Travel (flights, hotels, trips) shows only as busy, and the owner's email is never read for a call, until the
  owner says what may be shared: "callers can know I'm in India from Dec 3 to Dec 14" keeps a sharing note, for
  everyone, contacts or family, until a date (share_note). Callers can't change any of this.
- The caller's own history: their past calls Buddy answered, follow-ups still open, how they like to be reached.
  The relationship comes only from the owner's own data (what they told Buddy), never from what a caller says.
- A caller ID the carrier marked as not verified is treated as an unknown caller (it may be spoofed).

After a real message: a follow-up task in the owner's tasks ("Call Priya back: school pickup", due today when
urgent, else tomorrow) and the caller's profile updated. Transfer: only urgent calls from phone contacts, never between
10 pm and 7 am unless family. Kept on this server; no AI model but Claude (on the call itself) sees any of it.
"""

import json
import re
from datetime import date, datetime, timedelta, timezone

from .memory import Memory
from .tools import ToolError, local_now, user_tz

SCHEMA = """
CREATE TABLE IF NOT EXISTS caller_profiles (
    number TEXT PRIMARY KEY,                 -- the last 10 digits (contacts.key)
    relationship TEXT NOT NULL DEFAULT '',   -- set by the owner ("Priya is my wife"), never by a caller
    said TEXT NOT NULL DEFAULT '',           -- who the caller said they are (unverified, for the owner only)
    prefers TEXT NOT NULL DEFAULT '[]',      -- how they like to be reached ("texts", "after 6 pm")
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS caller_followups (
    id INTEGER PRIMARY KEY, number TEXT NOT NULL, task_id INTEGER NOT NULL, call_id INTEGER NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS share_notes (
    id INTEGER PRIMARY KEY, text TEXT NOT NULL, who TEXT NOT NULL, until TEXT NOT NULL, created_at TEXT NOT NULL
);
"""
FAMILY = {"wife", "husband", "partner", "spouse", "son", "daughter", "kid", "child", "mom", "mum", "mother", "dad",
          "father", "brother", "sister", "grandma", "grandmother", "grandpa", "grandfather", "aunt", "uncle", "cousin",
          "niece", "nephew", "mother-in-law", "father-in-law", "sister-in-law", "brother-in-law", "fiance", "fiancee",
          "fiancé", "fiancée", "parents", "amma", "achan", "appa"}
OTHER_RELATIONS = {"boss", "manager", "colleague", "coworker", "friend", "best friend", "neighbour", "neighbor",
                   "doctor", "dentist", "accountant", "lawyer", "landlord", "nanny", "babysitter", "assistant",
                   "client", "teacher"}
RELATION = re.compile(r"\b(?:is|'s|are)\s+(?:my|our)\s+(" + "|".join(
    sorted((re.escape(w) for w in FAMILY | OTHER_RELATIONS), key=len, reverse=True)) + r")\b", re.I)
LEVELS = ("everyone", "contacts", "family")       # who a sharing note is for, widest first
TIERS = ("family", "contact", "other")
PRIVATE = re.compile(r"\b(doctor|dr\.?|dentist|dental|clinic|hospital|therapy|therapist|counsel+ing|psychiatr\w*|"
                     r"physio\w*|lab work|blood ?test|scan|mri|x-?ray|surgery|pharmacy|medical|health|ob-?gyn|"
                     r"bank|loan|mortgage|tax(es)?|accountant|cpa|financial|advisor|broker|lawyer|attorney|"
                     r"court|legal|divorce|insurance)\b", re.I)
TRAVEL = re.compile(r"\b(flight|fly(ing)?|airport|airline|boarding|layover|depart(ure)?|arrival|hotel|check-?in|"
                    r"check-?out|airbnb|resort|trip|travel\w*|vacation|holiday|itinerary|cruise|rental car|"
                    r"train to|bus to|pnr|[A-Z]{2}\s?\d{2,4})\b")
KINDS = ((re.compile(r"\b(lunch|dinner|breakfast|brunch|coffee)\b", re.I), "a meal out"),
         (re.compile(r"\b(gym|workout|run|yoga|swim|tennis|cricket|golf|class)\b", re.I), "a workout or class"),
         (re.compile(r"\b(call|zoom|teams|meet|webinar|interview|1:1|one-on-one|sync|standup|stand-up|review)\b",
                     re.I), "a call or meeting"))
NIGHT = (22, 7)                                   # no transfers from 10 pm to 7 am, unless family


def _ready(memory: Memory) -> None:
    memory.db.executescript(SCHEMA)


def _key(number: str) -> str:
    from .contacts import key  # noqa: PLC0415
    return key(number)


# --- who the caller is ----------------------------------------------------------------------------------------
def relationship(memory: Memory, number: str, name: str) -> str:
    """Who the caller is to the owner, from the owner's own data: what the owner set for this caller, else what they
    told Buddy ("Priya is my wife"). Never from what a caller says. '' when not known."""
    _ready(memory)
    row = memory.db.execute("SELECT relationship FROM caller_profiles WHERE number = ?", (_key(number),)).fetchone()
    if row and row["relationship"]:
        return row["relationship"]
    first = (name or "").split()[0] if (name or "").split() else ""
    if len(first) < 2:
        return ""
    try:
        rows = memory.db.execute("SELECT text FROM facts WHERE text LIKE ? ORDER BY id DESC LIMIT 30",
                                 (f"%{first}%",)).fetchall()
    except Exception:  # no facts yet
        return ""
    for r in rows:
        text = str(r["text"] or "")
        for m in RELATION.finditer(text):
            before = text[:m.start()]
            if re.search(rf"\b{re.escape(first)}\b[^.;]{{0,40}}$", before, re.I):
                return m.group(1).lower()
    return ""


def tier(memory: Memory, call: dict, name: str, how: str) -> str:
    """family | contact | other: a phone contact the owner calls family, another phone contact, anyone else. A
    caller ID the carrier marked not verified counts as anyone else."""
    stir = str(call.get("windows") or "").lower()
    if how != "contact" or "not verified" in stir or "failed" in stir:
        return "other"
    return "family" if relationship(memory, call["number"], name) in FAMILY else "contact"


# --- what this caller may hear about the owner's time ------------------------------------------------------------
def _events(memory: Memory, now: datetime, days: int = 2) -> list[dict]:
    """Calendar events going on now or starting before the end of tomorrow: start, end, title, all-day."""
    try:
        rows = memory.db.execute(
            "SELECT title, start_at, end_at, data FROM records WHERE kind = 'event' AND start_at <= ? AND "
            "COALESCE(end_at, start_at) >= ? ORDER BY start_at",
            ((now + timedelta(days=days)).astimezone(timezone.utc).isoformat(),
             (now - timedelta(hours=12)).astimezone(timezone.utc).isoformat())).fetchall()
    except Exception:  # no calendar synced
        return []
    out = []
    end_of = datetime.combine(now.date() + timedelta(days=days - 1), datetime.max.time(), now.tzinfo)
    for r in rows:
        try:
            start = datetime.fromisoformat(r["start_at"]).astimezone(now.tzinfo)
            end = datetime.fromisoformat(r["end_at"]).astimezone(now.tzinfo) if r["end_at"] else start + timedelta(hours=1)
        except (TypeError, ValueError):
            continue
        try:
            all_day = bool(json.loads(r["data"] or "{}").get("all_day"))
        except (ValueError, AttributeError):
            all_day = False
        if end <= now or start > end_of:
            continue
        out.append({"title": " ".join(str(r["title"] or "").split())[:80], "start": start, "end": end,
                    "all_day": all_day})
    return out


def _time(t: datetime) -> str:
    return t.strftime("%I:%M %p").lstrip("0").replace(":00 ", " ").lower()


def _span(e: dict, now: datetime) -> str:
    day = "today" if e["start"].date() == now.date() else e["start"].strftime("%A")
    if e["all_day"]:
        return f"{day}, all day"
    if e["start"] <= now:
        return f"now, until {_time(e['end'])}"
    return f"{day} {_time(e['start'])} to {_time(e['end'])}"


def _kind(title: str) -> str:
    if PRIVATE.search(title):
        return "an appointment"
    return next((label for rx, label in KINDS if rx.search(title)), "a meeting")


def calendar_lines(memory: Memory, who: str, now: datetime | None = None) -> list[str]:
    """The owner's next two days as this caller may hear them: family the names (but not a health, money or legal
    appointment's), contacts the kind of thing, anyone else busy and free only. Travel is only busy. No places."""
    now = now or datetime.now(user_tz())
    out = []
    for e in _events(memory, now):
        span = _span(e, now)
        if TRAVEL.search(e["title"]) or who == "other":
            out.append(f"- busy {span}")
        elif who == "family":
            out.append(f"- {span}: {'an appointment' if PRIVATE.search(e['title']) else e['title']}")
        else:
            out.append(f"- {span}: {_kind(e['title'])}")
    return out


# --- what the owner said callers may know -----------------------------------------------------------------------
def share_note(memory: Memory, text: str, who: str = "everyone", until: str = "") -> dict:
    """The owner: "callers can know I'm in India from Dec 3 to Dec 14". Kept until `until` (YYYY-MM-DD; a month
    when not given), for everyone, contacts or family."""
    text = " ".join(str(text or "").split())[:200]
    if len(text) < 3:
        raise ToolError("What may callers be told?")
    who = who if who in LEVELS else "everyone"
    try:
        end = date.fromisoformat(str(until)[:10]) if until else local_now().date() + timedelta(days=30)
    except ValueError:
        raise ToolError("Until when? Like 2026-12-14.") from None
    if end < local_now().date():
        raise ToolError("That date has passed.")
    _ready(memory)
    cur = memory.db.execute("INSERT INTO share_notes (text, who, until, created_at) VALUES (?, ?, ?, ?)",
                            (text, who, end.isoformat(), datetime.now(timezone.utc).isoformat()))
    memory.db.commit()
    return {"id": cur.lastrowid, "text": text, "who": who, "until": end.isoformat()}


def share_notes(memory: Memory, today: date | None = None) -> list[dict]:
    """The sharing notes still in date."""
    _ready(memory)
    today = today or local_now().date()
    return [dict(r) for r in memory.db.execute("SELECT id, text, who, until FROM share_notes WHERE until >= ? "
                                               "ORDER BY id", (today.isoformat(),))]


def unshare(memory: Memory, note_id: int | None = None) -> int:
    _ready(memory)
    cur = memory.db.execute("DELETE FROM share_notes WHERE id = ?", (int(note_id),)) if note_id else \
        memory.db.execute("DELETE FROM share_notes")
    memory.db.commit()
    return cur.rowcount


def _may(level: str, who: str) -> bool:
    return level == "everyone" or (level == "contacts" and who in ("family", "contact")) or \
        (level == "family" and who == "family")


def notes_for(memory: Memory, who: str) -> list[str]:
    return [f"- {n['text']} (until {n['until']})" for n in share_notes(memory) if _may(n["who"], who)]


# --- the caller's own history ------------------------------------------------------------------------------------
def profile(memory: Memory, number: str) -> dict:
    _ready(memory)
    row = memory.db.execute("SELECT * FROM caller_profiles WHERE number = ?", (_key(number),)).fetchone()
    if not row:
        return {"relationship": "", "said": "", "prefers": []}
    try:
        prefers = [str(p) for p in json.loads(row["prefers"] or "[]")][:6]
    except ValueError:
        prefers = []
    return {"relationship": row["relationship"], "said": row["said"], "prefers": prefers}


def set_relationship(memory: Memory, number: str, relationship_: str) -> None:
    """The owner: "this number is my brother"."""
    _ready(memory)
    k = _key(number)
    if len(k) < 7:
        raise ToolError("Which number?")
    memory.db.execute("INSERT INTO caller_profiles (number, relationship, updated_at) VALUES (?, ?, ?) "
                      "ON CONFLICT(number) DO UPDATE SET relationship = excluded.relationship, "
                      "updated_at = excluded.updated_at",
                      (k, " ".join(str(relationship_ or "").lower().split())[:40], datetime.now(timezone.utc).isoformat()))
    memory.db.commit()


def forget(memory: Memory, number: str) -> None:
    """The owner: forget this caller (their preferences, relationship and follow-up links; tasks stay)."""
    _ready(memory)
    memory.db.execute("DELETE FROM caller_profiles WHERE number = ?", (_key(number),))
    memory.db.execute("DELETE FROM caller_followups WHERE number = ?", (_key(number),))
    memory.db.commit()


def history(memory: Memory, number: str, before_id: int, limit: int = 3) -> list[str]:
    """Their earlier calls Buddy answered, newest first: when, and what they wanted."""
    try:
        rows = memory.db.execute(
            "SELECT created_at, outcome FROM phone_calls WHERE kind = 'screen' AND number = ? AND id < ? AND "
            "outcome != '' AND windows != 'scam' ORDER BY id DESC LIMIT ?", (number, int(before_id), limit)).fetchall()
    except Exception:
        return []
    out = []
    for r in rows:
        try:
            when = datetime.fromisoformat(r["created_at"]).astimezone(user_tz()).strftime("%a %b %d")
        except ValueError:
            when = ""
        out.append(f"- {when}: {' '.join(str(r['outcome']).split())[:200]}")
    return out


def open_followups(memory: Memory, number: str) -> list[str]:
    """Follow-ups from their earlier calls the owner hasn't ticked off."""
    _ready(memory)
    try:
        rows = memory.db.execute(
            "SELECT t.text, t.due FROM caller_followups f JOIN tasks t ON t.id = f.task_id WHERE f.number = ? AND "
            "t.done_at IS NULL ORDER BY f.id DESC LIMIT 3", (_key(number),)).fetchall()
    except Exception:  # no tasks table yet
        return []
    return [f"- {r['text']}" + (f" (due {r['due']})" if r["due"] else "") for r in rows]


def caller_lines(memory: Memory, call: dict, name: str, who: str) -> str:
    """What Buddy knows about this caller, for Claude on the call: theirs to hear, nobody else's."""
    p = profile(memory, call["number"])
    rel = relationship(memory, call["number"], name) if who != "other" else ""
    lines = []
    if rel:
        lines.append(f"- to the owner: their {rel} (from the owner)")
    if p["prefers"]:
        lines.append("- how they like to be reached: " + "; ".join(p["prefers"]))
    past = history(memory, call["number"], call["id"])
    if past:
        lines.append("- their earlier calls Buddy answered:\n" + "\n".join("  " + x for x in past))
    open_ = open_followups(memory, call["number"])
    if open_:
        lines.append("- still on the owner's list from their earlier call:\n" + "\n".join("  " + x for x in open_))
    return "\n".join(lines)


# --- after the call -----------------------------------------------------------------------------------------------
def after_call(memory: Memory, call: dict, move: dict) -> int:
    """A real message: a follow-up task for the owner and the caller's profile kept up to date. The task's id, or
    0 (scams, sales and calls without a message get none)."""
    if move.get("priority") not in ("urgent", "normal") or not (move.get("outcome") or "").strip():
        return 0
    _ready(memory)
    k = _key(call["number"])
    prefers = [" ".join(str(p).split())[:60] for p in (move.get("prefers") or []) if str(p).strip()][:3]
    said = " ".join(str(move.get("said") or "").split())[:60]
    if len(k) >= 7 and (prefers or said):
        old = profile(memory, call["number"])
        merged = list(dict.fromkeys(prefers + old["prefers"]))[:6]
        memory.db.execute("INSERT INTO caller_profiles (number, said, prefers, updated_at) VALUES (?, ?, ?, ?) "
                          "ON CONFLICT(number) DO UPDATE SET said = CASE WHEN excluded.said != '' THEN excluded.said "
                          "ELSE caller_profiles.said END, prefers = excluded.prefers, updated_at = excluded.updated_at",
                          (k, said, json.dumps(merged), datetime.now(timezone.utc).isoformat()))
        memory.db.commit()
    from .organiser import Organiser  # noqa: PLC0415
    who = move.get("caller") or call.get("who") or call["number"]
    reason = " ".join(str(move.get("reason") or "").split())[:80] or " ".join(str(move["outcome"]).split())[:80]
    due = local_now().date() + timedelta(days=0 if move.get("priority") == "urgent" else 1)
    task = Organiser(memory).add(f"Call {who} back ({move.get('callback') or call['number']}): {reason}",
                                 due=due.isoformat(), priority="high" if move.get("priority") == "urgent" else "normal")
    memory.db.execute("INSERT INTO caller_followups (number, task_id, call_id, created_at) VALUES (?, ?, ?, ?)",
                      (k, int(task["id"]), int(call["id"]), datetime.now(timezone.utc).isoformat()))
    memory.db.commit()
    return int(task["id"])


def may_transfer(memory: Memory, who: str, owner_phone: str, now: datetime | None = None) -> bool:
    """Put an urgent caller through to the owner: phone contacts only, not at night unless family."""
    if not owner_phone or who == "other":
        return False
    hour = (now or local_now()).hour
    night = hour >= NIGHT[0] or hour < NIGHT[1]
    return who == "family" or not night
