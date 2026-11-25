"""Lab results: reports the owner uploads (Quest, Labcorp, a hospital portal's PDF), read by code, never by an AI
model, and kept only after the owner has checked what was read.

How a report is read.
  1. The PDF's text is taken out in the sandbox (docread: a throwaway container with no network), not in Buddy.
  2. Code picks out the lines that name a test Buddy knows (TESTS: about 45 common blood tests, matched by the
     names Quest, Labcorp and hospital portals print), and on each line the result, the lab's flag (H, L), the
     reference range ("70-99", "<200", "> OR = 40") and the unit. Lines with other tests are not read.
  3. The owner sees what was read beside the report's own words and ticks, corrects or removes each result, and
     sets the date the sample was taken (read from the report when it says "Collected"). Only then is anything kept.

When a report prints no range for a test (and no flag), Buddy fills in a typical adult range (TYPICAL), only when
the report's unit is the table's unit, and for tests whose range differs for men and women only when the owner's
sex is known (from the Oura profile). It is always called "the typical range", never "the lab's range": labs set
their own ranges, and the lab's printed range wins whenever there is one.

What is kept: the report itself in the vault (library.py: encrypted on disk with a key derived from the database
key, covered by the backup; marked as a health document, so Buddy's library tool never lists, opens or unlocks it
for an AI model, and it has no searchable text) and each confirmed result: the test, the name printed on the
report, the value, the unit, the range (the lab's, or the typical one) and the lab's flag, whether it is below,
within or above that range, the date, and that the owner confirmed it. Each result is also a health record
(healthrecords: quality "lab"). Kept until the owner deletes the report. A test's LOINC code is not guessed from
a printed name (the same name can be different tests); results that arrive from a portal with their code keep it.

Lab ranges for Claude (a separate switch, off until the owner turns it on, on the server:
sudo bash /opt/buddy/deploy/set.sh BUDDY_LAB_RANGES). When on, and only for Claude, the health tool's result may
carry one line a test, of exactly this form and nothing else:

    [lab range] LDL cholesterol: above the lab's range; up since the last test

No value, unit, date, lab or identity is in it. The Privacy Gateway lets a line through only if it matches that
form exactly (allowed); everything else about lab results stays on the server, as all health information does.
It does reveal which tests the owner has had and whether each is in range: that is the trade-off of the switch.
"""

import base64
import hashlib
import json
import os
import re
import secrets
from datetime import date, datetime, timedelta, timezone

from .memory import Memory
from .tools import ToolError, local_now

SOURCE = "lab_upload"
MAX_BYTES = 15_000_000
DRAFT_PREFIX, DRAFT_HOURS = "labs:draft:", 24
TESTS = {  # key -> (the words Claude may see, the printed names it matches, lowercased)
    "glucose": ("fasting glucose", r"(?:fasting )?glucose(?:,? (?:serum|plasma|fasting))?(?: fasting)?"),
    "hba1c": ("hemoglobin A1c", r"(?:hemoglobin a1c|hgb a1c|hba1c|a1c)(?: \(hba1c\))?"),
    "insulin": ("insulin", r"insulin(?:,? fasting)?"),
    "cholesterol_total": ("total cholesterol", r"cholesterol,? total|total cholesterol|cholesterol"),
    "ldl": ("LDL cholesterol", r"ldl[- ]?(?:cholesterol|chol)?(?: calc(?:ulated)?)?(?: \(nih\))?|ldl[- ]c(?:alc)?"),
    "hdl": ("HDL cholesterol", r"hdl[- ]?(?:cholesterol|chol)?|hdl[- ]c"),
    "triglycerides": ("triglycerides", r"triglycerides?"),
    "non_hdl": ("non-HDL cholesterol", r"non[- ]hdl(?: cholesterol| chol)?"),
    "apob": ("apolipoprotein B", r"(?:apolipoprotein b|apo ?b)(?:-100)?"),
    "lpa": ("lipoprotein(a)", r"lipoprotein ?\(a\)|lp\(a\)"),
    "hs_crp": ("hs-CRP", r"hs[- ]?crp|c[- ]reactive protein,? (?:high sensitivity|cardiac)"),
    "tsh": ("TSH", r"tsh|thyroid stimulating hormone"),
    "free_t4": ("free T4", r"free t4|t4,? free(?: \(direct\))?"),
    "vitamin_d": ("vitamin D", r"vitamin d,? ?25[- ]?(?:oh|hydroxy)[\w ,\-()]*"),
    "b12": ("vitamin B12", r"(?:vitamin )?b12"),
    "folate": ("folate", r"folate(?:,? serum)?"),
    "ferritin": ("ferritin", r"ferritin"),
    "iron": ("iron", r"iron(?:,? total)?"),
    "creatinine": ("creatinine", r"creatinine"),
    "egfr": ("eGFR", r"egfr[\w ,\-()/.]*"),
    "bun": ("urea nitrogen (BUN)", r"bun|urea nitrogen(?: \(bun\))?"),
    "uric_acid": ("uric acid", r"uric acid"),
    "alt": ("ALT", r"alt(?: \(sgpt\))?|alanine aminotransferase"),
    "ast": ("AST", r"ast(?: \(sgot\))?|aspartate aminotransferase"),
    "alk_phos": ("alkaline phosphatase", r"alkaline phosphatase|alk phos"),
    "ggt": ("GGT", r"ggt|gamma[- ]glutamyl ?transferase"),
    "bilirubin": ("total bilirubin", r"bilirubin,? total|total bilirubin"),
    "albumin": ("albumin", r"albumin"),
    "protein_total": ("total protein", r"protein,? total|total protein"),
    "sodium": ("sodium", r"sodium"),
    "potassium": ("potassium", r"potassium"),
    "chloride": ("chloride", r"chloride"),
    "co2": ("carbon dioxide", r"carbon dioxide,? total|co2"),
    "calcium": ("calcium", r"calcium"),
    "magnesium": ("magnesium", r"magnesium"),
    "wbc": ("white blood cells", r"wbc|white blood cell count"),
    "rbc": ("red blood cells", r"rbc|red blood cell count"),
    "hemoglobin": ("hemoglobin", r"hemoglobin|hgb"),
    "hematocrit": ("hematocrit", r"hematocrit|hct"),
    "platelets": ("platelets", r"platelets?(?: count)?"),
    "testosterone": ("testosterone", r"testosterone(?:,? total)?"),
    "psa": ("PSA", r"psa(?:,? total)?"),
    "homocysteine": ("homocysteine", r"homocysteine"),
    "cortisol": ("cortisol", r"cortisol"),
}
_NAMES = [(key, re.compile(rf"^(?:{pattern})$")) for key, (_, pattern) in TESTS.items()]
VALUE = re.compile(r"^[<>]?\d+(?:\.\d+)?$")
SITE_CODE = re.compile(r"^0\d$")  # Labcorp prints the testing site's code (01, 02) between a test and its result
FLAGS = {"h": "H", "high": "H", "hh": "H", "l": "L", "low": "L", "ll": "L", "a": "A", "abnormal": "A", "*": "A",
         "critical": "A"}
UNIT = re.compile(r"(?<![\w/])(mg/dL|g/dL|mmol/L|[uµ]mol/L|nmol/L|pmol/L|ng/mL|ng/dL|pg/mL|[uµ]g/dL|mcg/dL|mIU/L|"
                  r"[uµ]IU/mL|mIU/mL|IU/L|U/L|mEq/L|mg/L|g/L|x10E3/uL|x10E6/uL|Thousand/uL|Million/uL|K/uL|M/uL|"
                  r"10\*3/uL|10\*6/uL|fL|pg|%|mL/min/1\.73(?:m2)?)(?![\w/])", re.I)
DATE = re.compile(r"\b\d{1,2}/\d{1,2}/\d{2,4}\b")
BETWEEN = re.compile(r"(?<![\d.])(\d+(?:\.\d+)?)\s*-\s*(\d+(?:\.\d+)?)(?![\d.])")
BELOW = re.compile(r"(?:<\s*or\s*=|<=|≤|<)\s*(\d+(?:\.\d+)?)", re.I)
ABOVE = re.compile(r"(?:>\s*or\s*=|>=|≥|>)\s*(\d+(?:\.\d+)?)", re.I)
COLLECTED = re.compile(r"(?:date collected|collection date|collected(?: date)?(?:/time)?|specimen collected|"
                       r"date of service)\s*[:\-]?\s*(\d{1,2}/\d{1,2}/\d{2,4})", re.I)
BANDS = ("below the lab's range", "within the lab's range", "above the lab's range", "flagged by the lab",
         "no range given", "below the typical range", "within the typical range", "above the typical range")
HEALTH_DOC = "health record"  # the library's type for a lab report: shown to the owner, never to an AI model
COUNTS = ("x10e3/ul", "k/ul", "thousand/ul", "10*3/ul")
SAME_UNIT = {"meq/l": "mmol/l", "iu/l": "u/l", "uiu/ml": "miu/l", "µiu/ml": "miu/l", "µmol/l": "umol/l",
             "mcg/dl": "ug/dl", "µg/dl": "ug/dl", **{c: "x10e3/ul" for c in COUNTS},
             "m/ul": "x10e6/ul", "million/ul": "x10e6/ul", "10*6/ul": "x10e6/ul", "ml/min/1.73m2": "ml/min/1.73"}
# Typical adult ranges, used only when a report prints none: (unit, low, high, for whom). Guideline cut-offs where
# there is one (fasting glucose and A1c: ADA; cholesterol: NCEP ATP III; hs-CRP: AHA/CDC risk groups), otherwise
# the usual adult ranges given on MedlinePlus (NIH). Labs differ; the page and the lines say "typical range".
TYPICAL = {
    "glucose": [("mg/dl", 70, 99, ""), ("mmol/l", 3.9, 5.5, "")],
    "hba1c": [("%", None, 5.6, "")],
    "cholesterol_total": [("mg/dl", None, 199, "")],
    "ldl": [("mg/dl", None, 99, "")],
    "hdl": [("mg/dl", 40, None, "male"), ("mg/dl", 50, None, "female")],
    "triglycerides": [("mg/dl", None, 149, "")],
    "non_hdl": [("mg/dl", None, 129, "")],
    "lpa": [("mg/dl", None, 29, ""), ("nmol/l", None, 74, "")],
    "hs_crp": [("mg/l", None, 3.0, "")],
    "vitamin_d": [("ng/ml", 30, 100, "")],
    "b12": [("pg/ml", 200, 900, "")],
    "folate": [("ng/ml", 2.7, 17.0, "")],
    "ferritin": [("ng/ml", 24, 336, "male"), ("ng/ml", 11, 307, "female")],
    "iron": [("ug/dl", 60, 170, "")],
    "creatinine": [("mg/dl", 0.7, 1.3, "male"), ("mg/dl", 0.6, 1.1, "female")],
    "egfr": [("ml/min/1.73", 60, None, "")],
    "bun": [("mg/dl", 6, 20, "")],
    "uric_acid": [("mg/dl", 3.5, 7.2, "")],
    "ast": [("u/l", 8, 33, "")],
    "alk_phos": [("u/l", 44, 147, "")],
    "ggt": [("u/l", 5, 40, "")],
    "bilirubin": [("mg/dl", 0.1, 1.2, "")],
    "albumin": [("g/dl", 3.4, 5.4, "")],
    "protein_total": [("g/dl", 6.0, 8.3, "")],
    "sodium": [("mmol/l", 135, 145, "")],
    "potassium": [("mmol/l", 3.7, 5.2, "")],
    "chloride": [("mmol/l", 96, 106, "")],
    "co2": [("mmol/l", 23, 29, "")],
    "calcium": [("mg/dl", 8.5, 10.2, "")],
    "magnesium": [("mg/dl", 1.7, 2.2, "")],
    "wbc": [("x10e3/ul", 4.5, 11.0, "")],
    "platelets": [("x10e3/ul", 150, 400, "")],
    "rbc": [("x10e6/ul", 4.7, 6.1, "male"), ("x10e6/ul", 4.2, 5.4, "female")],
    "hemoglobin": [("g/dl", 13.8, 17.2, "male"), ("g/dl", 12.1, 15.1, "female")],
    "hematocrit": [("%", 40.7, 50.3, "male"), ("%", 36.1, 44.3, "female")],
    "homocysteine": [("umol/l", None, 15, "")],
    "psa": [("ng/ml", None, 4.0, "male")],
}


def _unit_key(unit: str) -> str:
    u = str(unit or "").strip().lower()
    return SAME_UNIT.get(u, u)


def typical(test: str, unit: str, sex: str = "") -> tuple | None:
    """A typical adult range (low, high) for a test in this unit, or None (unknown unit, or sex needed and unknown)."""
    want = _unit_key(unit)
    for their_unit, low, high, who in TYPICAL.get(test, []):
        if their_unit == want and (not who or who == str(sex or "").lower()):
            return low, high
    return None


def typical_text(low, high) -> str:
    return ("typical " + (f"{low}-{high}" if low is not None and high is not None else
                          f">= {low}" if low is not None else f"<= {high}"))
DIRECTIONS = ("first result", "up since the last test", "down since the last test",
              "about the same as the last test", "not comparable with the last test")
SCHEMA = """
CREATE TABLE IF NOT EXISTS lab_reports (          -- reports the owner uploaded, kept until they delete them
    id INTEGER PRIMARY KEY, source TEXT NOT NULL, file_name TEXT NOT NULL, sha256 TEXT NOT NULL UNIQUE,
    lab TEXT NOT NULL DEFAULT '', collected TEXT NOT NULL, uploaded_at TEXT NOT NULL, size INTEGER NOT NULL,
    vault_id TEXT                                  -- the file, in the library's vault (encrypted on disk)
);
CREATE TABLE IF NOT EXISTS lab_results (          -- each result the owner confirmed
    id INTEGER PRIMARY KEY, report_id INTEGER NOT NULL, source TEXT NOT NULL, test TEXT NOT NULL,
    printed TEXT NOT NULL, value REAL NOT NULL, value_text TEXT NOT NULL, unit TEXT NOT NULL DEFAULT '',
    ref_low REAL, ref_high REAL, ref_text TEXT NOT NULL DEFAULT '', ref_source TEXT NOT NULL DEFAULT '',
    flag TEXT NOT NULL DEFAULT '',
    band TEXT NOT NULL, collected TEXT NOT NULL, loinc TEXT NOT NULL DEFAULT '', confirmed INTEGER NOT NULL DEFAULT 1
);
"""

# --- for Claude, only when the owner switches lab ranges on -----------------------------------------------------
MARK = "[lab range]"
SETTING = "BUDDY_LAB_RANGES"
HEAD = (f"{MARK} Lab results against each lab's own reference range, worked out by rules. No values, units, dates, "
        "labs or identity are included, and none are available: do not ask for them or guess them.")
NONE = f"{MARK} No lab results yet."
LINE = re.compile(r"^\[lab range\] (?:" + "|".join(re.escape(label) for label, _ in TESTS.values()) + r"): (?:"
                  + "|".join(re.escape(b) for b in BANDS) + r"); (?:" + "|".join(re.escape(d) for d in DIRECTIONS)
                  + r")$")


def on(env=None) -> bool:
    return ((env if env is not None else os.environ).get(SETTING) or "").strip().lower() == "on"


def allowed(line: str) -> bool:
    line = str(line or "").strip()
    return line in (HEAD, NONE) or bool(LINE.match(line))


def _ready(memory: Memory) -> None:
    memory.db.executescript(SCHEMA)


# --- reading a report -------------------------------------------------------------------------------------------
def test_of(printed: str) -> str:
    """The test a printed name is, or ""."""
    name = re.sub(r"\s+", " ", re.sub(r"[*†‡]+", " ", printed.lower())).strip(" ,:-")
    return next((key for key, pattern in _NAMES if pattern.match(name)), "")


def _number(text: str) -> float | None:
    try:
        return float(str(text).lstrip("<>"))
    except ValueError:
        return None


def band_of(value: float, low: float | None, high: float | None, flag: str = "", source: str = "lab") -> str:
    """Below, within or above the range: the lab's, or (source "typical") the typical one Buddy filled in."""
    first = 5 if source == "typical" else 0
    if low is not None and value < low:
        return BANDS[first]
    if high is not None and value > high:
        return BANDS[first + 2 if first == 0 else 7]
    if low is not None or high is not None:
        return BANDS[first + 1 if first == 0 else 6]
    return {"H": BANDS[2], "L": BANDS[0], "A": BANDS[3]}.get(flag, BANDS[4])


def fill_typical(row: dict, sex: str = "") -> dict:
    """A result with no range and no flag from the lab gets the typical range for its unit, when there is one."""
    if row.get("ref_low") is not None or row.get("ref_high") is not None or row.get("flag"):
        return {**row, "ref_source": row.get("ref_source") or "lab"}
    got = typical(row["test"], row.get("unit", ""), sex)
    if not got:
        return {**row, "ref_source": ""}
    low, high = got
    return {**row, "ref_low": low, "ref_high": high, "ref_text": typical_text(low, high), "ref_source": "typical",
            "band": band_of(row["value"], low, high, "", "typical")}


def parse_line(line: str) -> dict | None:
    """One result from one line of a report, or None when the line names no test Buddy knows."""
    tokens = re.sub(r"\s+", " ", str(line or "")).strip().split(" ")
    for i, token in enumerate(tokens):
        if not VALUE.match(token) or i == 0:
            continue
        test = test_of(" ".join(tokens[:i]))
        if not test:
            continue
        j = i
        if SITE_CODE.match(token) and j + 1 < len(tokens) and VALUE.match(tokens[j + 1]):
            j += 1
        value_text = tokens[j]
        rest = tokens[j + 1:]
        flag = ""
        if rest and rest[0].lower() in FLAGS:
            flag, rest = FLAGS[rest[0].lower()], rest[1:]
        tail = DATE.sub(" ", " ".join(rest))  # a previous result's date is not a range
        unit = (UNIT.search(tail).group(1) if UNIT.search(tail) else "")
        low = high = None
        ref = ""
        if (m := BETWEEN.search(tail)):
            low, high, ref = float(m.group(1)), float(m.group(2)), m.group(0)
        elif (m := ABOVE.search(tail)):
            low, ref = float(m.group(1)), m.group(0)
        elif (m := BELOW.search(tail)):
            high, ref = float(m.group(1)), m.group(0)
        value = _number(value_text)
        if value is None:
            return None
        return {"test": test, "label": TESTS[test][0], "printed": " ".join(tokens[:i])[:80], "value": value,
                "value_text": value_text, "unit": unit, "ref_low": low, "ref_high": high, "ref_text": ref.strip(),
                "ref_source": "lab" if ref else "", "flag": flag, "band": band_of(value, low, high, flag),
                "line": " ".join(tokens)[:200]}
    return None


def _iso(us_date: str) -> str:
    month, day, year = (int(x) for x in us_date.split("/"))
    year += 2000 if year < 100 else 0
    try:
        return date(year, month, day).isoformat()
    except ValueError:
        return ""


def parse(text: str, sex: str = "") -> dict:
    """Every known test on a report (the first time each appears), the date collected and the lab, as read; a
    result with no printed range gets the typical one where there is one."""
    results, seen = [], set()
    for line in str(text or "").splitlines():
        got = parse_line(line)
        if got and got["test"] not in seen:
            seen.add(got["test"])
            results.append(fill_typical(got, sex))
    collected = COLLECTED.search(text or "")
    lab = ("Quest Diagnostics" if re.search(r"quest diagnostics", text or "", re.I) else
           "Labcorp" if re.search(r"\blab ?corp\b", text or "", re.I) else "")
    return {"results": results, "collected": _iso(collected.group(1)) if collected else "", "lab": lab}


def drop_expired(memory: Memory) -> int:
    """Reports read but never confirmed: dropped after DRAFT_HOURS (with the file they carry)."""
    now, n = datetime.now(timezone.utc).isoformat(), 0
    for row in memory.db.execute("SELECT key, value FROM kv WHERE key LIKE ? AND value != ''",
                                 (DRAFT_PREFIX + "%",)).fetchall():
        try:
            until = json.loads(row["value"]).get("until", "")
        except ValueError:
            until = ""
        if until < now:
            memory.set(row["key"], "")
            n += 1
    return n


async def read_report(memory: Memory, name: str, data: bytes, call=None) -> dict:
    """Read an uploaded report (text taken out in the sandbox) into a draft for the owner to check. Nothing is
    kept until they confirm."""
    from . import docread  # noqa: PLC0415
    drop_expired(memory)
    if not data or len(data) > MAX_BYTES:
        raise ToolError("Upload a PDF of up to 15 MB.")
    if not data.startswith(b"%PDF"):
        raise ToolError("That isn't a PDF. Download the report as a PDF from the lab's or the portal's site.")
    _ready(memory)
    digest = hashlib.sha256(data).hexdigest()
    held = memory.db.execute("SELECT collected FROM lab_reports WHERE sha256 = ?", (digest,)).fetchone()
    if held:
        raise ToolError(f"This report was already read (the sample taken {held['collected']}).")
    got = await docread.read(name or "report.pdf", data, call=call)
    if got.get("error") or not (got.get("text") or "").strip():
        raise ToolError("No text could be taken out of this PDF. If it is a photo or a scan, download the "
                        "lab's own PDF instead.")
    found = parse(got["text"], _sex(memory))
    draft_id = secrets.token_hex(8)
    draft = {"id": draft_id, "file_name": str(name or "report.pdf")[:120], "sha256": digest, "lab": found["lab"],
             "collected": found["collected"], "results": found["results"], "where": got.get("where", ""),
             "file": base64.b64encode(data).decode(), "until": (datetime.now(timezone.utc)
                                                                + timedelta(hours=DRAFT_HOURS)).isoformat()}
    memory.set(DRAFT_PREFIX + draft_id, json.dumps(draft))
    return {k: v for k, v in draft.items() if k != "file"}


def _sex(memory: Memory) -> str:
    """The owner's sex as Oura gave it, for the ranges that differ ("" when unknown)."""
    try:
        from . import oura  # noqa: PLC0415
        return str(oura.profile(memory).get("biological_sex") or "").lower()
    except Exception:
        return ""


def _draft(memory: Memory, draft_id: str) -> dict:
    if not re.fullmatch(r"[0-9a-f]{16}", str(draft_id or "")):
        raise ToolError("That report is no longer waiting: upload it again.")
    try:
        draft = json.loads(memory.get(DRAFT_PREFIX + draft_id) or "{}")
    except ValueError:
        draft = {}
    if not draft or draft.get("until", "") < datetime.now(timezone.utc).isoformat():
        raise ToolError("That report is no longer waiting: upload it again.")
    return draft


def cancel(memory: Memory, draft_id: str) -> str:
    if re.fullmatch(r"[0-9a-f]{16}", str(draft_id or "")):
        memory.set(DRAFT_PREFIX + draft_id, "")
    return "Nothing from that report was kept."


def _clean_row(row: dict, sex: str = "") -> dict | None:
    """A result as the owner sent it back: the value and unit they may have corrected, the range worked out again
    (a typical range follows the unit they set)."""
    test = str(row.get("test") or "")
    value = _number(str(row.get("value_text") if row.get("value_text") not in (None, "") else row.get("value")))
    if test not in TESTS or value is None:
        return None
    flag = str(row.get("flag") or "").upper()[:1]
    flag = flag if flag in ("H", "L", "A") else ""
    out = {"test": test, "printed": str(row.get("printed") or TESTS[test][0])[:80], "value": value,
           "value_text": str(row.get("value_text") or value)[:20], "unit": str(row.get("unit") or "")[:20], "flag": flag}
    if row.get("ref_source") == "lab":
        low, high = (_number(str(row.get(k))) if row.get(k) not in (None, "") else None for k in ("ref_low", "ref_high"))
        return {**out, "ref_low": low, "ref_high": high, "ref_text": str(row.get("ref_text") or "")[:40],
                "ref_source": "lab", "band": band_of(value, low, high, flag)}
    return fill_typical({**out, "ref_low": None, "ref_high": None, "ref_text": "", "band": band_of(value, None, None, flag)},
                        sex)


def save(memory: Memory, draft_id: str, rows: list[dict], collected: str, keep_file: bool = True) -> str:
    """Keep the results the owner confirmed (only those), the report itself, and their health records."""
    from . import connecthub, healthrecords  # noqa: PLC0415
    draft = _draft(memory, draft_id)
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(collected or "")) or collected > local_now().date().isoformat():
        raise ToolError("Set the date the sample was taken (not a future date).")
    sex = _sex(memory)
    kept = [r for r in (_clean_row(x, sex) for x in rows or []) if r]
    if not kept:
        raise ToolError("No result was ticked: nothing was kept.")
    _ready(memory)
    raw = base64.b64decode(draft["file"])
    vault_id = _to_vault(memory, raw, collected, draft.get("lab", "")) if keep_file else None
    with connecthub.syncing(memory, SOURCE, "upload") as got:
        cur = memory.db.execute(
            "INSERT INTO lab_reports (source, file_name, sha256, lab, collected, uploaded_at, size, vault_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (SOURCE, draft["file_name"], draft["sha256"], draft.get("lab", ""), collected,
             datetime.now(timezone.utc).isoformat(timespec="seconds"), len(raw), vault_id))
        report_id = int(cur.lastrowid)
        for r in kept:
            memory.db.execute(
                "INSERT INTO lab_results (report_id, source, test, printed, value, value_text, unit, ref_low, "
                "ref_high, ref_text, ref_source, flag, band, collected) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (report_id, SOURCE, r["test"], r["printed"], r["value"], r["value_text"], r["unit"], r["ref_low"],
                 r["ref_high"], r["ref_text"], r.get("ref_source") or "", r["flag"], r["band"], collected))
            n, new = healthrecords.record_lab(memory, SOURCE, collected, r["test"], r["value"], r["unit"],
                                              r["value_text"], "", f"lab_reports:{report_id}", got["id"])
            got["records"] += n
            got["new"] += new
        got["reached"] = collected
        memory.db.commit()
    memory.set(DRAFT_PREFIX + draft_id, "")
    return f"Kept {len(kept)} results from the report of {collected}."


def _to_vault(memory: Memory, raw: bytes, collected: str, lab: str) -> str:
    """The report into the library's vault: encrypted on disk, marked a health document (kept from the AI's view),
    and not read: no text is taken into the library, so nothing of it can be searched or summarised."""
    from .library import Library  # noqa: PLC0415
    item = Library(memory).add(f"Lab report {collected}.pdf", raw, note="Lab report: read by code on the Health "
                               "page, never by an AI model", vault=True)
    memory.db.execute("UPDATE library_items SET title = ?, doc_type = ?, status = 'stored', vault = 1, summary = ? "
                      "WHERE id = ?", (f"Lab report {collected}" + (f" ({lab})" if lab else ""), HEALTH_DOC,
                                       "A lab report, kept in the vault. Its results are on the Health page's Labs tab; "
                                       "no AI model reads it.", item["id"]))
    memory.db.commit()
    return item["id"]


# --- what is held --------------------------------------------------------------------------------------------------
def _direction(now: dict, before: dict | None) -> str:
    if before is None:
        return DIRECTIONS[0]
    if (now["unit"] or "").lower() != (before["unit"] or "").lower():
        return DIRECTIONS[4]
    base = abs(before["value"]) or 1.0
    change = (now["value"] - before["value"]) / base
    return DIRECTIONS[1] if change > 0.05 else DIRECTIONS[2] if change < -0.05 else DIRECTIONS[3]


def results(memory: Memory) -> list[dict]:
    """Each test held, newest result first, with every earlier one and the direction since the one before."""
    _ready(memory)
    rows = [dict(r) for r in memory.db.execute(
        "SELECT r.*, p.lab FROM lab_results r LEFT JOIN lab_reports p ON p.id = r.report_id "
        "ORDER BY r.test, r.collected, r.id")]
    by: dict[str, list[dict]] = {}
    for r in rows:
        by.setdefault(r["test"], []).append(r)
    out = []
    for test in TESTS:
        seen = by.get(test)
        if not seen:
            continue
        latest = seen[-1]
        out.append({"test": test, "label": TESTS[test][0], "latest": latest, "earlier": seen[:-1][::-1],
                    "direction": _direction(latest, seen[-2] if len(seen) > 1 else None)})
    return out


def reports(memory: Memory) -> list[dict]:
    _ready(memory)
    drop_expired(memory)
    return [dict(r) for r in memory.db.execute(
        "SELECT p.id, p.source, p.file_name, p.lab, p.collected, p.uploaded_at, p.size, p.vault_id IS NOT NULL AS has_file, "
        "COUNT(r.id) AS results FROM lab_reports p LEFT JOIN lab_results r ON r.report_id = p.id "
        "GROUP BY p.id ORDER BY p.collected DESC, p.id DESC")]


def report_file(memory: Memory, report_id: int) -> tuple[str, bytes] | None:
    """The report from the vault, for the owner's own Open on the Health page."""
    from . import library  # noqa: PLC0415
    _ready(memory)
    row = memory.db.execute("SELECT file_name, vault_id FROM lab_reports WHERE id = ?", (int(report_id),)).fetchone()
    if not row or not row["vault_id"]:
        return None
    try:
        return row["file_name"], library.original(memory, row["vault_id"])[0]
    except Exception:
        return None


def _out_of_vault(memory: Memory, vault_ids: list[str]) -> None:
    from .library import Library  # noqa: PLC0415
    for vault_id in vault_ids:
        try:
            Library(memory).forget(vault_id)
        except Exception:  # already gone from the library
            continue


def delete_report(memory: Memory, report_id: int) -> str:
    from . import healthrecords  # noqa: PLC0415
    _ready(memory)
    healthrecords._ready(memory)
    report_id = int(report_id)
    held = memory.db.execute("SELECT vault_id FROM lab_reports WHERE id = ?", (report_id,)).fetchone()
    _out_of_vault(memory, [held["vault_id"]] if held and held["vault_id"] else [])
    memory.db.execute("DELETE FROM health_records WHERE connector = ? AND source_ref = ?",
                      (SOURCE, f"lab_reports:{report_id}"))
    n = memory.db.execute("DELETE FROM lab_results WHERE report_id = ?", (report_id,)).rowcount
    gone = memory.db.execute("DELETE FROM lab_reports WHERE id = ?", (report_id,)).rowcount
    memory.db.commit()
    return f"Deleted the report (and its copy in the vault) and its {n} results." if gone else "That report wasn't found."


def forget(memory: Memory) -> None:
    """Every uploaded report and result (connecthub.forget also takes out their health records and history)."""
    _ready(memory)
    _out_of_vault(memory, [r["vault_id"] for r in memory.db.execute(
        "SELECT vault_id FROM lab_reports WHERE source = ? AND vault_id IS NOT NULL", (SOURCE,))])
    memory.db.execute("DELETE FROM lab_results WHERE source = ?", (SOURCE,))
    memory.db.execute("DELETE FROM lab_reports WHERE source = ?", (SOURCE,))
    memory.db.commit()


def brief(memory: Memory) -> str:
    """The lab range lines for Claude (only used when the owner has switched lab ranges on)."""
    held = results(memory)
    if not held:
        return NONE
    return "\n".join([HEAD, *(f"{MARK} {t['label']}: {t['latest']['band']}; {t['direction']}" for t in held)])
