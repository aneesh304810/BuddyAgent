"""Privacy Gateway: the one check every request to an AI model passes before it leaves this server.

The rules (the owner's decision, 17 November 2026):

  Health   the owner's health information goes to NO model, Claude included: measurements (sleep, readiness,
           HRV, heart rate, temperature, weight, lab values), symptoms, conditions, medicines, allergies, what
           was eaten, health goals, and anything worked out from them. Taking the name off does not make it safe.
  Personal everything else about the owner (who they are, mail, money, documents, and any key or password in
           the text) goes to Claude ONLY. No other model, free or paid, sees it; and nothing falls back to another
           model after a refusal.
  Public   anything else may go to any model the owner has set up.
  Unsure   if this check fails or cannot decide, the request is refused (fail closed).
  Ranges   the things the owner can change, and only on the server: when they switch health ranges on
           (healthranges.py), Claude, and only Claude, may be given health as ranges against the owner's own usual
           ("sleep duration: latest below usual"), never a value; when they switch lab ranges on (labs.py, its own
           switch), Claude, and only Claude, may be given each lab test against the lab's own range ("LDL
           cholesterol: above the lab's range; up since the last test"), never a value. Only lines of those exact
           forms pass; everything above still holds.

Where it is enforced (the only doors to a model):
  Claude       config.claude_client(): every request, streamed or not, passes sanitize_request just before it
               is sent. Health information found anywhere in it (the instructions, the memory lines, an old turn
               of the conversation, a tool's result) is cut out and replaced by a marker; the rest goes on. The
               request is cut, not refused, so one remembered health fact cannot stop Buddy working for good.
  Other models free_models._post and maestro.ask_with: authorize, which refuses the whole request unless it is
               public.
  Health tools agent.py: what a health tool returns is shown to the owner on a card and withheld from the model
               (HEALTH_TOOLS), so it never becomes part of a request in the first place.
  The chat     agent.reply: a message about the owner's own health is answered by Buddy itself and never sent on.

How health is recognised. Two ways, because neither is enough alone:
  by source    a health tool's result is health information whatever it looks like. This is the strong control.
  by content   rules over the text: a health measure with a number beside it; the owner (I, my, "the user", their
               name) beside a symptom, condition, medicine or test; the field names health data is stored under.
               No AI model takes part in the decision.
A question that names no person ("what does HDL cholesterol mean?") is public and may be asked.

What this cannot do, said plainly: content rules miss things no rule was written for (a symptom described in
unusual words, a number with no label), and they sometimes stop something harmless. Speech is turned into text
by a speech model before any rule can read it, so spoken words reach that model first. Both are listed in
docs/PRIVACY-GATEWAY.md under "Not covered".

The record. Every decision is kept as: when, which part of Buddy asked, which model, the level, the kinds of
information found, what was decided and why, and a reference number. Never the content, and never a value.
"""

import json
import logging
import re
import secrets
from collections import deque
from datetime import datetime, timezone

log = logging.getLogger("buddy.privacygate")

PUBLIC, INTERNAL, CONFIDENTIAL, RESTRICTED = 0, 1, 2, 3
LEVELS = {0: "Public", 1: "Internal", 2: "Confidential", 3: "Restricted"}
CLAUDE = "claude"
HEALTH_TOOLS = ("my_health", "health_plan")   # what these return is never given to a model
WITHHELD = ("[Withheld by the Privacy Gateway: this is the owner's health information. It was shown to them on "
            "their screen and is not available to you. Do not guess at it or ask them to type it; say that it is on "
            "their screen.]")
BLOCKED_HEALTH = ("Privacy Gateway blocked this request. It contains personal health information, and Buddy does not "
                  "send health information to any AI model. Your information stays inside Buddy: your numbers, "
                  "habits and plan are on the Health page (More, then Health). You can still "
                  "ask a general question that is not about you (for example, what a test or a medicine is).")
REMOVED = "[Removed by the Privacy Gateway: health information, which is not sent to any AI model.]"
BLOCKED_PERSONAL = ("Privacy Gateway blocked this request. It contains personal details, which go to Claude only and "
                    "to no other AI model.")
BLOCKED_UNSURE = ("Privacy Gateway blocked this request because it could not be checked. Nothing was sent.")


class PrivacyBlocked(Exception):
    """A request was refused. `kind`: health | personal | unsure. The message is safe to show."""

    def __init__(self, message: str, kind: str, ref: str = ""):
        super().__init__(message)
        self.kind, self.ref = kind, ref


# --- recognising health information -----------------------------------------------------------------------------
OWNER = r"(?:\bi\b|\bi['’]?(?:m|ve|d|ll)\b|\bmy\b|\bme\b|\bmine\b|\bmyself\b|\bthe (?:user|owner)(?:['’]s)?\b|\b(?:user|owner)['’]s\b|\btheir own\b)"
MEASURES = (r"hrv|heart[- ]rate variability|(?:resting|lowest|average|avg)[ _]?(?:heart[ _-]?rate|hr|pulse)|heart[ _-]?rate|"
            r"\bpulse\b|readiness(?:[ _]score)?|sleep[ _]score|activity[ _]score|sleep[ _](?:minutes|duration|efficiency|"
            r"latency)|(?:deep|rem|light)[ _](?:sleep|minutes)|temp(?:erature)?[ _]deviation|body temperature|"
            r"blood[ _]?pressure|\bsystolic|\bdiastolic|blood[ _]?(?:sugar|glucose|oxygen)|\bglucose\b|\bspo2\b|"
            r"\ba1c\b|hba1c|cholesterol|\bldl\b|\bhdl\b|triglycerides|\btsh\b|ferritin|hemoglobin|haemoglobin|"
            r"creatinine|\begfr\b|vitamin [bd]\d*|testosterone|\bcortisol\b|\bbmi\b|body[ _]fat|"
            r"(?:body )?weight|\bstress[ _]minutes|resilience|respiratory[ _]rate|breathing rate|vo2")
FIELDS = (r"oura_ring|sleep_minutes|resting_hr|lowest_hr|avg_hr|temp_deviation|sleep_score|activity_score|"
          r"deep_minutes|rem_minutes|stress_minutes|active_calories|total_calories|eaten_today|left_today|"
          r"next_meal|hrv_balance|spo2_percentage")
CONDITIONS = (r"symptoms?|diagnos\w+|headaches?|migraines?|fever|nause\w+|vomit\w*|dizz\w+|rash|cough\w*|"
              r"chest pain|back pain|joint pain|stomach ?ache|in pain|insomnia|can['’]?t sleep|anxiety|depress\w+|"
              r"panic attacks?|therap(?:y|ist)|psychiatr\w+|allerg\w+|asthma|diabet\w+|hypertension|"
              r"high blood pressure|arthritis|cancer|tumou?r|infection|pregnan\w+|miscarr\w+|menstru\w+|"
              r"my period|surgery|operation on|biopsy|blood (?:test|work|panel)|lab (?:results?|report|work)|"
              r"biomarkers?|medical (?:history|record|report)s?|medications?|prescri\w+|antibiotics?|"
              r"insulin|metformin|statins?|antidepressants?|supplements?|\d+ ?mg\b|my doctor|my cardiologist|"
              r"calories (?:today|eaten)|i ate\b(?! (?:at|in|out|there|with|here)\b)|i weigh\b|lose weight|gain weight|my diet")
NUMBER = r"-?\d{1,4}(?:[.,]\d+)?"
MEASURED = re.compile(rf"(?:{MEASURES})\b[^\n.;]{{0,24}}?{NUMBER}|{NUMBER}\s*(?:bpm|mg/dl|mmol/l|mmhg|ng/ml|ms hrv)\b", re.I)
FIELD = re.compile(rf"[\"']?(?:{FIELDS})[\"']?\s*[:=]", re.I)
STORED = re.compile(rf"[\"'](?:readiness|hrv|steps|bedtime|resilience)[\"']\s*:\s*(?:{NUMBER}|\")", re.I)
ABOUT_OWNER = re.compile(rf"{OWNER}[^\n.;?!]{{0,70}}?\b(?:{CONDITIONS}|{MEASURES})\b|"
                         rf"\b(?:{CONDITIONS})\b[^\n.;?!]{{0,40}}?{OWNER}", re.I)
SELF = re.compile(r"\b(?:i weigh|i(?:['’]ve| have)? (?:ate|eaten|been eating)(?! (?:at|in|out|there|with|here)\b)|i (?:take|took|am taking|['’]m taking)\b[^\n.;]{0,30}"
                  r"(?:mg|pills?|tablets?|capsules?|medic\w+|vitamins?|supplements?)|my (?:doctor|cardiologist|therapist|"
                  r"psychiatrist|dermatologist|physician|diet|weight|sleep|pulse|blood)\b|i(?:['’]m| am) (?:pregnant|"
                  r"diabetic|allergic|asthmatic|depressed|sick|ill)\b|i (?:feel|felt|am feeling|['’]m feeling) "
                  r"(?:sick|ill|dizzy|nauseous|faint|feverish|depressed|anxious))", re.I)
NOT_HEALTH = re.compile(r"\b(?:my|the) (?:code|app|server|build|test|tests|laptop|battery|car|account|budget|"
                        r"portfolio|order|flight|trip)\b", re.I)


def health(text: str, owner_name: str = "", ranges: bool = False, lab_ranges: bool = False) -> list[str]:
    """Why a text is the owner's health information ([] when it is not). The reasons name the kind of thing found,
    never the thing itself. ranges / lab_ranges: the request is bound for Claude and the owner has switched health
    ranges / lab ranges on, so a line that is exactly such a range line (healthranges.allowed, labs.allowed) is let
    through; any other marked line is not."""
    from . import healthranges, labs  # noqa: PLC0415
    text = str(text or "")
    found = []
    if healthranges.MARK in text or labs.MARK in text:
        kept = []
        for line in text.split("\n"):
            if healthranges.MARK in line:
                if not (ranges and healthranges.allowed(line)):
                    found.append("health ranges, which go to Claude only, and only when the owner has switched them on")
            elif labs.MARK in line:
                if not (lab_ranges and labs.allowed(line)):
                    found.append("lab ranges, which go to Claude only, and only when the owner has switched them on")
            else:
                kept.append(line)
        text = "\n".join(kept)
    if FIELD.search(text) or STORED.search(text):
        found.append("fields from the health records")
    if MEASURED.search(text):
        found.append("a health measurement with its value")
    about = ABOUT_OWNER.search(text)
    if not about and owner_name and len(owner_name) >= 3:
        about = re.search(rf"\b{re.escape(owner_name)}\b[^\n.;?!]{{0,70}}?\b(?:{CONDITIONS}|{MEASURES})\b", text, re.I)
    if about and not (NOT_HEALTH.search(about.group(0)) and not re.search(CONDITIONS, about.group(0), re.I)):
        found.append("a statement about the owner's own health")
    elif SELF.search(text):
        found.append("a statement about the owner's own health")
    return found


# --- other kinds --------------------------------------------------------------------------------------------------
ADDRESS = re.compile(r"\b\d{1,6}\s+(?:[A-Z][a-z]+\s){1,4}(?:Street|St|Avenue|Ave|Road|Rd|Boulevard|Blvd|Lane|Ln|Drive|Dr|"
                     r"Court|Ct|Way|Place|Pl|Terrace|Circle)\b\.?", re.I)
SSN = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
BORN = re.compile(r"\b(?:born|date of birth|dob|birthday)\b[^\n]{0,20}\d", re.I)
ID_DOC = re.compile(r"\b(?:passport|driver'?s? licen[cs]e|national id|social security)\b[^\n]{0,24}[A-Z0-9]{5,}", re.I)


def personal(text: str, memory=None) -> list[str]:
    """Why a text is personal (identifies the owner or is theirs). Reuses the checks the free models already had."""
    from . import free_models  # noqa: PLC0415
    text, found = str(text or ""), []
    if free_models.EMAIL.search(text):
        found.append("an email address")
    if free_models.PHONE.search(text):
        found.append("a phone number")
    if free_models.mask(text) != text or SSN.search(text) or ID_DOC.search(text):
        found.append("a card, account or identity number")
    if ADDRESS.search(text):
        found.append("a street address")
    if BORN.search(text):
        found.append("a date of birth")
    if memory is not None:
        try:
            if free_models._mentions_memory(text, memory):
                found.append("details from the owner's memory")
        except Exception:
            found.append("details that could not be checked against the owner's memory")
    return found


def secret(text: str) -> list[str]:
    from . import free_models  # noqa: PLC0415
    return ["a key, password or token"] if any(p.search(str(text or "")) for p in free_models.SECRETS) else []


def text_of(payload, depth: int = 0) -> str:
    """Every piece of text in a payload (a string, or the nested lists and dicts of a model request). Keys are
    kept, since a field's name can itself say what the value is. Tool definitions are skipped by the caller."""
    if payload is None or depth > 12:
        return ""
    if isinstance(payload, str):
        return payload
    if isinstance(payload, (int, float, bool)):
        return str(payload)
    if isinstance(payload, dict):
        return "\n".join(f"{k}: {text_of(v, depth + 1)}" if isinstance(v, (str, int, float, bool))
                         else f"{k}:\n{text_of(v, depth + 1)}" for k, v in payload.items()
                         if k not in ("signature", "cache_control", "media_type"))
    if isinstance(payload, (list, tuple)):
        return "\n".join(text_of(v, depth + 1) for v in payload)
    return str(payload)


def classify(payload, memory=None, owner_name: str = "") -> dict:
    """{"level": 0-3, "name", "kinds": [...], "reasons": [...]}. Health and secrets are Restricted, other personal
    details Confidential, the rest Public. Reasons say what kind of thing was found, never the thing."""
    text = text_of(payload)
    kinds, reasons = [], []
    for kind, found in (("health", health(text, owner_name)), ("personal", personal(text, memory) + secret(text))):
        if found:
            kinds.append(kind)
            reasons += found
    level = RESTRICTED if "health" in kinds else CONFIDENTIAL if kinds else PUBLIC
    return {"level": level, "name": LEVELS[level], "kinds": kinds, "reasons": reasons}


# --- the record -----------------------------------------------------------------------------------------------------
_memory = None
_pending: deque = deque(maxlen=500)
SCHEMA = """CREATE TABLE IF NOT EXISTS privacy_events (
    id INTEGER PRIMARY KEY, at TEXT NOT NULL, ref TEXT NOT NULL, source TEXT NOT NULL, provider TEXT NOT NULL,
    level INTEGER NOT NULL, kinds TEXT NOT NULL, action TEXT NOT NULL, reason TEXT NOT NULL)"""
KEEP_EVENTS = 5000


def attach(memory) -> None:
    """Give the gateway the database to keep its record in (the server does this once when it starts)."""
    global _memory
    _memory = memory
    try:
        memory.db.execute(SCHEMA)
        while _pending:
            _write(memory, _pending.popleft())
    except Exception:
        log.warning("privacy: the record could not be opened", exc_info=True)


def _write(memory, e: dict) -> None:
    memory.db.execute("INSERT INTO privacy_events (at, ref, source, provider, level, kinds, action, reason) VALUES "
                      "(?, ?, ?, ?, ?, ?, ?, ?)", (e["at"], e["ref"], e["source"], e["provider"], e["level"],
                                                   ",".join(e["kinds"]), e["action"], e["reason"]))
    memory.db.execute("DELETE FROM privacy_events WHERE id <= (SELECT MAX(id) FROM privacy_events) - ?", (KEEP_EVENTS,))
    memory.db.commit()


def record(source: str, provider: str, got: dict, action: str, reason: str, ref: str = "") -> str:
    """Keep one decision. Only what kind of thing was found is kept; an approved public request is counted, not
    listed, so the record does not fill with them."""
    ref = ref or secrets.token_hex(5)
    event = {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "ref": ref, "source": source[:60],
             "provider": provider[:40], "level": int(got.get("level", RESTRICTED)), "kinds": list(got.get("kinds") or []),
             "action": action, "reason": reason[:200]}
    _counts[action] = _counts.get(action, 0) + 1
    if action == "approved" and not event["kinds"]:
        return ref
    try:
        if _memory is not None:
            _write(_memory, event)
        else:
            _pending.append(event)
    except Exception:
        log.warning("privacy: a decision could not be recorded (%s %s)", action, provider, exc_info=True)
    if action == "blocked":
        log.warning("privacy: blocked a request to %s from %s (%s) ref %s", provider, source, ", ".join(event["kinds"]) or "unsure", ref)
    return ref


_counts: dict[str, int] = {}


# --- the decision -------------------------------------------------------------------------------------------------
def authorize(provider: str, payload, source: str = "", memory=None, owner_name: str = "") -> dict:
    """May this payload go to this model? Returns the classification when it may; raises PrivacyBlocked when it may
    not. provider: "claude", or the name of any other model. Any failure in here refuses the request."""
    name = CLAUDE if str(provider or "").lower().startswith("claude") else str(provider or "unknown")
    try:
        got = classify(payload, memory if memory is not None else _memory, owner_name or _owner())
    except Exception as e:
        ref = record(source, name, {"level": RESTRICTED, "kinds": []}, "blocked", f"the check failed ({type(e).__name__})")
        raise PrivacyBlocked(BLOCKED_UNSURE, "unsure", ref) from e
    if "health" in got["kinds"]:
        ref = record(source, name, got, "blocked", "; ".join(got["reasons"]))
        raise PrivacyBlocked(BLOCKED_HEALTH, "health", ref)
    if "personal" in got["kinds"] and name != CLAUDE:
        ref = record(source, name, got, "blocked", "; ".join(got["reasons"]))
        raise PrivacyBlocked(BLOCKED_PERSONAL, "personal", ref)
    record(source, name, got, "approved", "; ".join(got["reasons"]) or "nothing personal found")
    return got


def authorize_request(provider: str, body, source: str = "") -> dict:
    """The same for a whole model request (the JSON about to be sent): its instructions and its messages are checked;
    the tool definitions are not, since they are Buddy's own fixed text and describe health tools by name."""
    if not isinstance(body, dict):
        return authorize(provider, body, source)
    parts = {k: v for k, v in body.items() if k in ("system", "messages", "prompt", "input", "instructions")}
    return authorize(provider, parts, source or "a model request")


def _clean(text: str, owner_name: str) -> tuple[str, int]:
    """A text with every line that is the owner's health information replaced by the marker. (text, lines cut)"""
    if not isinstance(text, str) or not text:
        return text, 0
    from . import healthranges, labs  # noqa: PLC0415
    out, cut, ranges, lab_ranges = [], 0, healthranges.on(), labs.on()
    for line in text.split("\n"):
        if health(line, owner_name, ranges, lab_ranges):
            cut += 1
            if not out or out[-1] != REMOVED:
                out.append(REMOVED)
        else:
            out.append(line)
    return "\n".join(out), cut


def _clean_blocks(content, owner_name: str):
    """The same for a message's content: a string, or a list of blocks (text, a tool's result, a tool call)."""
    if isinstance(content, str):
        return _clean(content, owner_name)
    if not isinstance(content, list):
        return content, 0
    out, cut = [], 0
    for block in content:
        if not isinstance(block, dict):
            out.append(block)
            continue
        block = dict(block)
        if isinstance(block.get("text"), str):
            block["text"], n = _clean(block["text"], owner_name)
            cut += n
        if block.get("type") == "tool_result":
            block["content"], n = _clean_blocks(block.get("content"), owner_name)
            cut += n
        if block.get("type") == "tool_use" and health(text_of(block.get("input")), owner_name):
            block["input"], cut = {"withheld": REMOVED}, cut + 1
        out.append(block)
    return out, cut


def sanitize_request(body: dict, source: str = "", provider: str = CLAUDE) -> int:
    """Cut the owner's health information out of a Claude request, in place, before it is sent: from the
    instructions, every message, and every tool result. Returns how many pieces were cut. Any failure refuses the
    request. Pictures and files inside a request cannot be read by these rules (see "Not covered")."""
    try:
        name, cut = _owner(), 0
        if isinstance(body.get("system"), (str, list)):
            body["system"], n = _clean_blocks(body["system"], name)
            cut += n
        messages = []
        for m in body.get("messages") or []:
            if isinstance(m, dict) and "content" in m:
                m = dict(m)
                m["content"], n = _clean_blocks(m["content"], name)
                cut += n
            messages.append(m)
        if "messages" in body:
            body["messages"] = messages
    except Exception as e:
        ref = record(source or "a Claude request", provider, {"level": RESTRICTED, "kinds": []}, "blocked",
                     f"the check failed ({type(e).__name__})")
        raise PrivacyBlocked(BLOCKED_UNSURE, "unsure", ref) from e
    if cut:
        record(source or "a Claude request", provider, {"level": RESTRICTED, "kinds": ["health"]}, "sanitized",
               f"{cut} piece{'s' if cut != 1 else ''} of health information cut out before sending")
    else:
        _counts["approved"] = _counts.get("approved", 0) + 1
    return cut


def _owner() -> str:
    try:
        return str(_memory.get("owner:name") or "").split()[0] if _memory is not None else ""
    except Exception:
        return ""


def withhold(tool: str, result: str, memory=None) -> tuple[str, bool]:
    """What a tool's result becomes before a model may see it: a health tool's result is withheld whole (by source,
    whatever it looks like). Returns (what the model gets, whether it was withheld). When the owner has switched
    health ranges on, the health-data tool gives the model the ranges instead of nothing; never the values."""
    from . import healthranges, labs  # noqa: PLC0415
    if tool == "my_health" and memory is not None and (healthranges.on() or labs.on()):
        parts = []
        for switched, make, what in ((healthranges.on(), healthranges.brief, "ranges against the owner's own usual"),
                                     (labs.on(), labs.brief, "lab results against each lab's own range")):
            if not switched:
                continue
            try:
                got = make(memory)
            except Exception:
                log.warning("privacy: the %s could not be worked out; left out", what, exc_info=True)
                continue
            if got:
                parts.append(got)
                record(f"tool: {tool}", CLAUDE, {"level": RESTRICTED, "kinds": ["health"]}, "generalised",
                       f"{what} were given to Claude; the values stayed on the server")
        if parts:
            return "\n".join(parts), True
    if tool in HEALTH_TOOLS:
        record(f"tool: {tool}", "any model", {"level": RESTRICTED, "kinds": ["health"]}, "withheld",
               "a health tool's result is shown to the owner and kept from every model")
        return WITHHELD, True
    return result, False


# --- for the Privacy Gateway page ------------------------------------------------------------------------------------
POLICY = [
    {"rule": "Health information goes to no AI model, Claude included", "mandatory": True},
    {"rule": "Other personal details, and any key or password, go to Claude only", "mandatory": True},
    {"rule": "A refused request is never retried on another model", "mandatory": True},
    {"rule": "If the check fails, the request is refused", "mandatory": True},
    {"rule": "The record keeps no content, only what kind of thing was found", "mandatory": True},
]


def status(memory=None, limit: int = 50) -> dict:
    memory = memory if memory is not None else _memory
    events, totals = [], {}
    try:
        memory.db.execute(SCHEMA)
        events = [dict(r) for r in memory.db.execute(
            "SELECT at, ref, source, provider, level, kinds, action, reason FROM privacy_events ORDER BY id DESC LIMIT ?", (limit,))]
        totals = {r["action"]: r["n"] for r in memory.db.execute(
            "SELECT action, COUNT(*) AS n FROM privacy_events GROUP BY action")}
    except Exception:
        log.info("privacy: no record to read", exc_info=True)
    for e in events:
        e["level_name"] = LEVELS.get(e["level"], "Restricted")
    from . import healthranges, labs  # noqa: PLC0415
    return {"enabled": True, "can_be_switched_off": False, "policy": POLICY, "health_tools": list(HEALTH_TOOLS),
            "lab_ranges": {"on": labs.on(),
                           "what": "Claude may be given each lab test as below, within or above the lab's own range "
                                   "and which way it moved since the last test, never a value, a unit, a date or "
                                   "your identity. No other model gets them.",
                           "how": f"Changed only on the server: sudo bash /opt/buddy/deploy/set.sh {labs.SETTING}, "
                                  "then type on (or off)."},
            "health_ranges": {"on": healthranges.on(), "generalised": totals.get("generalised", 0),
                              "what": "Claude may be given your health as ranges against your own usual (below, about "
                                      "or above), never a value, a date or your identity. No other model gets them.",
                              "how": f"Changed only on the server: sudo bash /opt/buddy/deploy/set.sh "
                                     f"{healthranges.SETTING}, then type on (or off)."},
            "blocked": totals.get("blocked", 0), "withheld": totals.get("withheld", 0),
            "sanitized": totals.get("sanitized", 0),
            "approved_with_personal_details": totals.get("approved", 0),
            "approved_since_restart": _counts.get("approved", 0), "events": events,
            "note": "Health is recognised by where it comes from (the health tools) and by rules over the text. Rules "
                    "can miss an unusual wording and can stop something harmless. Spoken words reach a speech model "
                    "before any rule can read them."}


def dumps(status_: dict) -> str:
    return json.dumps(status_, default=str)
