#!/usr/bin/env python3
"""Messages -> deadlines and action dates as JSON. No network, no AI."""
import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from dates import find_dates, sent_date  # noqa: E402

CUE = re.compile(r"\b(due|by|before|deadline|expire[sd]?|expiry|until|no later than|check[- ]?in|renew\w*|pay\w*|"
                 r"appointment|reminder|last day|ends?|closing|submit|cancel\w*|starts?|departs?|flight|meeting)\b", re.I)


def messages(path: str) -> list[dict]:
    raw = Path(path).read_text(encoding="utf-8")
    try:
        data = json.loads(raw)
        return data if isinstance(data, list) else data.get("messages", [])
    except json.JSONDecodeError:
        return [{"text": raw, "date": date.today().isoformat()}]


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("input")
    p.add_argument("--days", type=int, default=60)
    p.add_argument("--dmy", action="store_true")
    p.add_argument("--today", default="")
    a = p.parse_args()
    today = date.fromisoformat(a.today) if a.today else date.today()
    found = {}
    for m in messages(a.input):
        text = re.sub(r"\s+", " ", " ".join(str(m.get(k, "")) for k in ("subject", "text", "body", "snippet")))
        sent = sent_date(m.get("date", "")) if m.get("date") else today
        for d, pos, raw in find_dates(text, sent, us_order=not a.dmy):
            window = text[max(0, pos - 80):pos + len(raw) + 40]
            if not CUE.search(window):
                continue
            left = (d - today).days
            if left > a.days or left < -14:
                continue
            what = text[max(0, pos - 100):pos + len(raw) + 60].strip()
            key = (d, str(m.get("subject", ""))[:60])
            found.setdefault(key, {"date": d.isoformat(), "days_left": left, "what": what,
                                   "from": m.get("from", ""), "subject": str(m.get("subject", ""))[:120]})
    items = sorted(found.values(), key=lambda x: x["date"])
    print(json.dumps({"upcoming": [i for i in items if i["days_left"] >= 0][:50],
                      "overdue": [i for i in items if i["days_left"] < 0][:20]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
