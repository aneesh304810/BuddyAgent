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

Messages for one caller ("if Kyle calls, tell him the work is due end of day and I'll email"): the owner's words,
tidied by Claude and approved by the owner before they're kept, for one person, until a day (a week when not
said). Public: given to whoever says they're that person. Private: given only when this code, not the AI, has
confirmed the call comes from that person's number in the owner's phone contacts AND the carrier verified the
caller ID (STIR/SHAKEN); otherwise the call's AI is never given the words. Given once, then deleted; the owner is
told in the app when and how it was checked, the caller's reply becomes a task, and one never given is reported
when it lapses.

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
CREATE TABLE IF NOT EXISTS caller_messages (   -- the owner's message for one person, given once
    id INTEGER PRIMARY KEY, name TEXT NOT NULL, numbers TEXT NOT NULL DEFAULT '[]', text TEXT NOT NULL,
    private INTEGER NOT NULL DEFAULT 1, until TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS caller_message_offers (  -- which messages a call was cleared to carry, and how
    call_id INTEGER NOT NULL, message_id INTEGER NOT NULL, how TEXT NOT NULL, PRIMARY KEY (call_id, message_id)
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


# --- messages for one caller ------------------------------------------------------------------------------------
MESSAGE_DAYS = 7
CHECKED = "their number in your contacts and the carrier's check of the caller ID"
PUBLIC = "a public message: no check"


def leave_message(memory: Memory, who: str, text: str, private: bool = True, until: str = "") -> dict:
    """The owner's message for one person. Private ones need that person in the phone's contacts (their numbers are
    what a call is checked against). Until: YYYY-MM-DD, the last day it may be given (a week when not said)."""
    from . import contacts  # noqa: PLC0415
    who, text = " ".join(str(who or "").split())[:80], " ".join(str(text or "").split())[:500]
    if len(text) < 3:
        raise ToolError("What should the message say?")
    if not who:
        raise ToolError("Who is it for?")
    people = [p for p in contacts.find(memory, who, limit=8) if p["how"] == "contact"]
    exact = [p for p in people if p["name"].lower() == who.lower()]
    people = exact or people
    if private:
        if not people:
            raise ToolError(f"{who} isn't in your phone's contacts, so Buddy can't check it's really them. Save the "
                            "number in your contacts (the app shares them with Buddy), or make the message public.")
        if len({p["name"] for p in people}) > 1:
            raise ToolError("Which one: " + ", ".join(sorted({p["name"] for p in people})) + "?")
    name = people[0]["name"] if len({p["name"] for p in people}) == 1 else who
    numbers = sorted({_key(n) for p in people if p["name"] == name for n in p["numbers"] if len(_key(n)) >= 7})
    if private and not numbers:
        raise ToolError(f"{name} has no phone number in your contacts.")
    try:
        end = date.fromisoformat(str(until)[:10]) if until else local_now().date() + timedelta(days=MESSAGE_DAYS)
    except ValueError:
        raise ToolError("Until when? Like 2026-12-04.") from None
    if end < local_now().date():
        raise ToolError("That date has passed.")
    _ready(memory)
    cur = memory.db.execute("INSERT INTO caller_messages (name, numbers, text, private, until, created_at) "
                            "VALUES (?, ?, ?, ?, ?, ?)", (name, json.dumps(numbers), text,
                                                          int(bool(private)), end.isoformat(),
                                                          datetime.now(timezone.utc).isoformat()))
    memory.db.commit()
    return {"id": cur.lastrowid, "name": name, "text": text, "private": bool(private), "until": end.isoformat(),
            "numbers": len(numbers)}


def messages(memory: Memory, today: date | None = None) -> list[dict]:
    """The messages still waiting (in date)."""
    _ready(memory)
    today = today or local_now().date()
    return [dict(r) | {"private": bool(r["private"]), "numbers": json.loads(r["numbers"] or "[]")}
            for r in memory.db.execute("SELECT * FROM caller_messages WHERE until >= ? ORDER BY id",
                                       (today.isoformat(),))]


def cancel_message(memory: Memory, message_id: int | None = None, who: str = "") -> int:
    _ready(memory)
    if message_id:
        cur = memory.db.execute("DELETE FROM caller_messages WHERE id = ?", (int(message_id),))
    elif who:
        cur = memory.db.execute("DELETE FROM caller_messages WHERE lower(name) LIKE ?", (f"%{who.lower()}%",))
    else:
        raise ToolError("Which message?")
    memory.db.commit()
    return cur.rowcount


def _verified(call: dict) -> bool:
    """The carrier's STIR/SHAKEN check said the caller ID is real (not merely absent or 'not applicable')."""
    stir = str(call.get("windows") or "").lower()
    return "verified" in stir and "not verified" not in stir


def offer(memory: Memory, call: dict) -> list[dict]:
    """What this call may carry, decided by code before the call's AI sees anything: every public message (for
    whoever says they're that person), and a private one only when the call comes from that person's number in the
    contacts and the carrier verified the caller ID. What was offered is recorded, so only those can be delivered."""
    k = _key(call.get("number") or "")
    out = []
    for m in messages(memory):
        if not m["private"]:
            out.append(m | {"how": PUBLIC})
        elif len(k) >= 7 and k in m["numbers"] and _verified(call):
            out.append(m | {"how": CHECKED})
    if out and call.get("id"):
        memory.db.executemany("INSERT OR IGNORE INTO caller_message_offers (call_id, message_id, how) VALUES (?, ?, ?)",
                              [(int(call["id"]), m["id"], m["how"]) for m in out])
        memory.db.commit()
    return out


def message_lines(offered: list[dict], first: str, call: dict | None = None) -> str:
    """The call prompt's part about messages (only what this call was cleared to carry). When the call is from that
    person's number, Buddy greets them by name and says it first, unasked, like a person who knows who's calling."""
    if not offered:
        return ""
    k = _key((call or {}).get("number") or "")
    lines = [f"Messages {first} left for one person, in case {first} couldn't pick up. Say it in the words given "
             "(join it smoothly into what you say, fixing nothing and adding nothing), then ask if they'd like to "
             "reply, and take the reply. Put its number in \"delivered\" once you've said it in full, and what they "
             "said back in \"reply\"."]
    for m in offered:
        theirs = len(k) >= 7 and k in m.get("numbers", [])
        if m["private"] or theirs:
            check = (f"Buddy's code confirmed this call is from their number in {first}'s contacts and the carrier "
                     "verified the caller ID" if m["private"] else f"this call is from their number in {first}'s contacts")
            lines.append(f"- Message {m['id']}, for {m['name']}: {check}, so it's them. Greet them warmly by first "
                         "name, and right after their hello tell them, unasked, before anything else, something like "
                         f"\"Hey {m['name'].split()[0]}! {first} asked me to tell you: {m['text']}\"")
        else:
            lines.append(f"- Message {m['id']}, for {m['name']} (public): give it only if the caller says they're "
                         f"{m['name'].split()[0]} (\"Oh hey! {first} asked me to tell you: ...\"); never to anyone "
                         f"else, and never say it exists to anyone else: \"{m['text']}\"")
    return "\n".join(lines)


def delivered(memory: Memory, call: dict, move: dict) -> str:
    """After the call: each message the call was cleared to carry and the AI said it gave is deleted (given once),
    the caller's reply becomes a task, and the owner is told how it was checked. A private message whose person
    called without the carrier's check is said to be still waiting."""
    _ready(memory)
    said = {int(x) for x in (move.get("delivered") or []) if str(x).isdigit()}
    rows = {r["message_id"]: r["how"] for r in memory.db.execute(
        "SELECT message_id, how FROM caller_message_offers WHERE call_id = ?", (int(call.get("id") or 0),))}
    notes, gave = [], []
    for mid, how in rows.items():
        m = memory.db.execute("SELECT * FROM caller_messages WHERE id = ?", (mid,)).fetchone()
        if not m or mid not in said:
            continue
        gave.append(m["name"])
        memory.db.execute("DELETE FROM caller_messages WHERE id = ?", (mid,))
        short = m["text"] if len(m["text"]) <= 90 else m["text"][:87] + "..."
        notes.append(f"✉️ I gave your message to {m['name']} (checked by {how}): \"{short}\"")
    reply = " ".join(str(move.get("reply") or "").split())[:300]
    if gave and reply:
        from .organiser import Organiser  # noqa: PLC0415
        Organiser(memory).add(f"{gave[0]} replied to your message: {reply}", due=local_now().date().isoformat())
        notes.append(f"Their reply is in your tasks: \"{reply}\"")
    k = _key(call.get("number") or "")
    if len(k) >= 7 and not _verified(call):
        for m in messages(memory):
            if m["private"] and k in m["numbers"] and m["id"] not in rows:
                notes.append(f"🔒 {m['name']} called, but the carrier couldn't confirm the caller ID, so I didn't give "
                             "your private message; it's still waiting.")
    memory.db.execute("DELETE FROM caller_message_offers WHERE call_id = ?", (int(call.get("id") or 0),))
    memory.db.commit()
    return " ".join(notes)


def lapsed(memory: Memory, today: date | None = None) -> list[str]:
    """Messages past their last day without being given: removed, and the owner told (in the app)."""
    _ready(memory)
    today = today or local_now().date()
    rows = [dict(r) for r in memory.db.execute("SELECT * FROM caller_messages WHERE until < ?", (today.isoformat(),))]
    for r in rows:
        memory.db.execute("DELETE FROM caller_messages WHERE id = ?", (r["id"],))
    memory.db.commit()
    return [f"✉️ Your message for {r['name']} wasn't given: they didn't call by {r['until']}, so I've removed it: "
            f"\"{r['text'][:90]}\"" for r in rows]


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
