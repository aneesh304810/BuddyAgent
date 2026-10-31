"""Finding dates in text, relative to the day a message was sent (shared by deadlines and bills-due)."""
import re
from datetime import date, datetime, timedelta

MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov",
                                      "dec"], 1)}
DAYS = {d: i for i, d in enumerate(["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"])}
MONTH_NAME = r"(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?"
PATTERNS = [
    (re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b"), "iso"),
    (re.compile(rf"\b{MONTH_NAME}\s+(\d{{1,2}})(?:st|nd|rd|th)?(?:,?\s+(\d{{4}}))?", re.I), "mdy"),
    (re.compile(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+(?:of\s+)?{MONTH_NAME}(?:,?\s+(\d{{4}}))?", re.I), "dmy"),
    (re.compile(r"\b(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?\b"), "slash"),
    (re.compile(r"\b(today|tonight|tomorrow)\b", re.I), "word"),
    (re.compile(r"\b(?:next|this|on|by|before|until)\s+(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b",
                re.I), "weekday"),
    (re.compile(r"\bin\s+(\d{1,2})\s+(day|week)s?\b", re.I), "in"),
]


def _year(y: str | None, m: int, d: int, sent: date) -> int:
    if y:
        return int(y) + (2000 if len(y) == 2 else 0)
    guess = date(sent.year, m, d)
    return sent.year + 1 if guess < sent - timedelta(days=60) else sent.year  # "Jan 5" written in December


def find_dates(text: str, sent: date, us_order: bool = True) -> list[tuple[date, int, str]]:
    """(date, position, matched text) for each date in the text."""
    out = []
    for pattern, kind in PATTERNS:
        for m in pattern.finditer(text):
            try:
                if kind == "iso":
                    d = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
                elif kind == "mdy":
                    mo, dd = MONTHS[m.group(1)[:3].lower()], int(m.group(2))
                    d = date(_year(m.group(3), mo, dd, sent), mo, dd)
                elif kind == "dmy":
                    dd, mo = int(m.group(1)), MONTHS[m.group(2)[:3].lower()]
                    d = date(_year(m.group(3), mo, dd, sent), mo, dd)
                elif kind == "slash":
                    a, b = int(m.group(1)), int(m.group(2))
                    mo, dd = (a, b) if us_order else (b, a)
                    d = date(_year(m.group(3), mo, dd, sent), mo, dd)
                elif kind == "word":
                    d = sent + timedelta(days=0 if m.group(1).lower() in ("today", "tonight") else 1)
                elif kind == "weekday":
                    ahead = (DAYS[m.group(1).lower()] - sent.weekday()) % 7 or 7
                    d = sent + timedelta(days=ahead)
                else:
                    n = int(m.group(1)) * (7 if m.group(2).lower() == "week" else 1)
                    d = sent + timedelta(days=n)
            except (ValueError, KeyError):
                continue
            out.append((d, m.start(), m.group(0)))
    return sorted(out, key=lambda x: x[1])


def sent_date(value: str) -> date:
    for fmt in (None,):
        try:
            return datetime.fromisoformat(str(value).replace("Z", "+00:00")).date()
        except ValueError:
            pass
    return date.today()
