"""The evening check-in: on the owner's first chat after 6 pm, Buddy asks how they are, and code reads the answers.

The owner chose this on 26 Nov 2026: a check-in run by code, with no AI model. On the first message they send Buddy
after 6 pm each day, Buddy answers that message as usual and then asks, in fixed words:

1. mood, stress and energy, each from 1 to 5 ("4, 2, 3", "mood 4 stress 2", or a word such as "good");
2. whether they have been in touch with friends or family (yes, a little, no);
3. how rested they feel, from 1 to 5;
4. anything on their mind, in a few words.

Each answer is read here, before the Privacy Gateway and before any model, and the whole check-in goes into the mood
journal (moodlog.py). Buddy then reads the day back in fixed sentences: what was noted, last night's sleep and
readiness, the week's average mood. Only a note that a check-in happened is kept in the conversation later turns send
to Claude. "Skip" ends it; a message that isn't an answer ends it too and is handled as a normal message (what was
already answered is kept). "Check in" starts it at any time. It never sends a notification: it asks only while the
owner is already talking to Buddy. Switched off or on from Health → Trends → Mood.

Spoken answers pass through a speech recognizer first, like every spoken word (the phone's, or Whisper); typed
answers go only to the owner's server.
"""

import json
import re
import time

from . import moodlog
from .memory import Memory
from .tools import local_now

STATE_KEY, ASKED_KEY = "checkin:state", "checkin:asked"
START_HOUR, IDLE_SECONDS = 18, 30 * 60
STEPS = ("scales", "people", "rested", "note")
QUESTIONS = {
    "scales": "Evening check-in: how are your mood, stress and energy today, each from 1 to 5? For example 4, 2, 3. "
              "Or say skip.",
    "people": "Have you been in touch with friends or family today? Yes, a little, or no.",
    "rested": "How rested do you feel, from 1 to 5?",
    "note": "Anything on your mind? A few words, or say no.",
}
AGAIN = {"scales": "From 1 to 5, please: mood, stress and energy. For example 4, 2, 3.",
         "rested": "From 1 to 5, please: 1 is drained, 5 is fully rested."}
SKIP = re.compile(r"^\s*(skip|skip it|stop|later|not now|no thanks|cancel|enough|never ?mind)\b", re.I)
START = re.compile(r"^\s*(?:let'?s\s+)?(?:do\s+)?(?:(?:the|a|my)\s+)?(?:evening\s+)?check[\s-]?in\s*[.!?]?\s*$", re.I)
LEAD = r"^\s*(?:i'?m |i am |feeling |feel |pretty |doing |quite |really |very |a bit |kind of |kinda )*"
MOOD_WORDS = {"awful": 1, "terrible": 1, "horrible": 1, "bad": 2, "low": 2, "down": 2, "sad": 2, "meh": 3,
              "so-so": 3, "so so": 3, "okay": 3, "ok": 3, "fine": 3, "alright": 3, "good": 4, "well": 4,
              "great": 5, "excellent": 5, "amazing": 5, "fantastic": 5, "wonderful": 5}
REST_WORDS = {"exhausted": 1, "drained": 1, "wiped out": 1, "tired": 2, "sleepy": 2, "okay": 3, "ok": 3, "fine": 3,
              "rested": 4, "good": 4, "well rested": 5, "refreshed": 5, "great": 5}
PEOPLE_WORDS = [  # checked in this order: "not much" before "no", "a little" before "yes"
    ("a little", r"\b(a little|a bit|some|not much|briefly|sort of|kind of|a few|texted)\b"),
    ("no", r"^\s*(no|nope|nah|not really|not today|none)\b|\b(alone|lonely|isolated|nobody|no one|no-one)\b"),
    ("yes", r"^\s*(yes|yeah|yep|yup|sure|of course|lots|a lot|plenty|definitely)\b|\b(called|met|saw|talked|spoke)\b"),
]
NOTHING = re.compile(r"^\s*(no|nope|nah|nothing|none|not really|that'?s (it|all)|all good|no thanks)\s*[.!]?\s*$", re.I)


def on(memory: Memory) -> bool:
    return memory.get(moodlog.CHECKIN_OFF_KEY) != "1"


def switch(memory: Memory, turn_on: bool) -> None:
    memory.set(moodlog.CHECKIN_OFF_KEY, "" if turn_on else "1")


def _state(memory: Memory) -> dict | None:
    try:
        s = json.loads(memory.get(STATE_KEY) or "null")
    except ValueError:
        return None
    if not isinstance(s, dict) or s.get("day") != local_now().date().isoformat() \
            or time.time() - float(s.get("at") or 0) > IDLE_SECONDS:
        return None
    return s


def _keep(memory: Memory, s: dict | None) -> None:
    memory.set(STATE_KEY, json.dumps({**s, "at": time.time()}) if s else "")


def due(memory: Memory) -> bool:
    """Offer it now: switched on, 6 pm or later, not yet offered today, and none running."""
    now = local_now()
    return on(memory) and now.hour >= START_HOUR and memory.get(ASKED_KEY) != now.date().isoformat() \
        and _state(memory) is None


def start(memory: Memory) -> str:
    memory.set(ASKED_KEY, local_now().date().isoformat())
    _keep(memory, {"day": local_now().date().isoformat(), "step": "scales", "answers": {}, "again": 0})
    return QUESTIONS["scales"]


def _word(text: str, words: dict) -> int | None:
    t = text.lower().strip()
    for w in sorted(words, key=len, reverse=True):
        if re.fullmatch(LEAD + re.escape(w) + r"(?: today)?(?:,? thanks| thank you)?\s*[.!]*\s*", t):
            return words[w]
    return None


def scales(text: str) -> dict | None:
    """Mood, stress and energy from an answer: labelled, in order, or one word for mood. None if it isn't one."""
    t = re.sub(r"\s*(?:/|out of)\s*5\b", "", text.lower())
    labelled = {k: int(v) for k, v in re.findall(r"\b(mood|stress|energy)\s*(?:is|was|:|=|of)?\s*([1-5])\b", t)}
    if labelled:
        return labelled
    numbers = [int(n) for n in re.findall(r"(?<![\d.])([1-5])(?![\d.])", t)]
    if numbers and not re.search(r"\d{2,}|[6-9]|0", re.sub(r"(?<![\d.])[1-5](?![\d.])", "", t)):
        return dict(zip(moodlog.SCALES, numbers[:3]))
    mood = _word(text, MOOD_WORDS)
    return {"mood": mood} if mood else None


def people(text: str) -> str | None:
    t = text.lower()
    for answer, pattern in PEOPLE_WORDS:
        if re.search(pattern, t):
            return answer
    return None


def rested(text: str) -> int | None:
    t = re.sub(r"\s*(?:/|out of)\s*5\b", "", text.lower())
    numbers = re.findall(r"(?<![\d.])([1-5])(?![\d.])", t)
    if len(numbers) == 1 and not re.search(r"\d{2,}|[6-9]|0", t.replace(numbers[0], "", 1)):
        return int(numbers[0])
    return _word(text, REST_WORDS)


def _about_me(text: str) -> bool:
    """Words about how they are, which aren't an answer to the question: kept as the note, not sent anywhere."""
    from . import privacygate  # noqa: PLC0415
    return privacygate.health(text)


def answer(memory: Memory, text: str) -> str | None:
    """Buddy's reply when the message belongs to the check-in (an answer, a skip, or "check in"); None when it
    doesn't, and then the message goes on as a normal one."""
    text = str(text or "").strip()
    s = _state(memory)
    if s is None:
        return start(memory) if START.match(text) else None
    a, step = s["answers"], s["step"]
    if SKIP.match(text):
        return _finish(memory, s, skipped=True)
    got = None
    if step == "scales":
        got = scales(text)
        if got:
            a.update(got)
    elif step == "people":
        got = people(text)
        if got:
            a["people"] = got
    elif step == "rested":
        got = rested(text)
        if got:
            a["rested"] = got
    elif step == "note":
        got = True
        if not NOTHING.match(text):
            a["note"] = (a.get("note", "") + " " + text).strip()[:500]
    if not got:
        if step in AGAIN and re.search(r"\d", text) and not s.get("again"):
            s["again"] = 1  # a number, but not 1 to 5: ask once more
            _keep(memory, s)
            return AGAIN[step]
        if _about_me(text):  # how they are, in their own words: kept as the note, and the question asked again
            a["note"] = (a.get("note", "") + " " + text).strip()[:500]
            if step == "note":
                return _finish(memory, s)
            s["again"] = 0
            _keep(memory, s)
            return "Noted. " + QUESTIONS[step].replace("Evening check-in: ", "").replace(" Or say skip.", "")
        _finish(memory, s, quiet=True)  # not an answer: keep what was said, and let the message go on
        return None
    following = STEPS[STEPS.index(step) + 1] if step != STEPS[-1] else None
    if following is None:
        return _finish(memory, s)
    s.update(step=following, again=0)
    _keep(memory, s)
    return QUESTIONS[following]


def _finish(memory: Memory, s: dict, skipped: bool = False, quiet: bool = False) -> str:
    _keep(memory, None)
    a = s["answers"]
    saved = False
    if any(a.get(k) for k in ("mood", "stress", "energy", "people", "rested", "note")):
        moodlog.add(memory, a.get("mood"), a.get("stress"), a.get("energy"), [], a.get("note", ""),
                    s["day"], a.get("people", ""), a.get("rested"), via="checkin")
        saved = True
    if quiet:
        return ""
    if skipped:
        return ("Okay, I'll leave it there; I kept what you told me. " if saved else "Okay, skipped. ") + \
            "Say check in whenever you like."
    return readback(memory, a)


def readback(memory: Memory, a: dict) -> str:
    """The day read back in fixed sentences, from the owner's own numbers."""
    parts = []
    scales_said = [f"{k} {a[k]}" for k in moodlog.SCALES if a.get(k)]
    if scales_said:
        parts.append("Noted in your journal: " + ", ".join(scales_said) + ".")
    else:
        parts.append("Noted in your journal.")
    try:
        from . import coach  # noqa: PLC0415
        body = coach.body(coach._data(memory, 14), local_now().date())
    except Exception:
        body = {}
    seen = []
    if body.get("sleep_minutes"):
        seen.append(f"{round(body['sleep_minutes'] / 60, 1)} hours of sleep last night")
    if body.get("readiness") is not None:
        seen.append(f"readiness {body['readiness']}")
    if seen:
        parts.append("Your ring had " + " and ".join(seen) + ".")
    week = moodlog.view(memory, 7)["week"]
    if week.get("mood") is not None:
        parts.append(f"Your mood this week averages {week['mood']}.")
    try:  # what's still open on the week's activity targets (code only)
        from . import activity  # noqa: PLC0415
        if (still := activity.evening_line(memory)):
            parts.append(still)
    except Exception:  # noqa: BLE001
        pass
    try:  # today's main meals not noted yet (code only)
        from . import coach  # noqa: PLC0415
        if (missing := coach.missing_line(memory)):
            parts.append(missing)
    except Exception:  # noqa: BLE001
        pass
    try:  # what went over on fats, sodium, added sugar or ultra-processed food today (code only)
        from . import scanmeal  # noqa: PLC0415
        if (over := scanmeal.evening_line(memory)):
            parts.append(over)
    except Exception:  # noqa: BLE001
        pass
    if (a.get("mood") and a["mood"] <= 2) or a.get("people") == "no":
        parts.append("Thanks for telling me. If days like this keep coming, talking to someone you trust or a "
                     "doctor helps, and if you ever feel unsafe, call or text 988 any time.")
    parts.append("Kept on your server only; no AI model sees it.")
    return " ".join(parts)
