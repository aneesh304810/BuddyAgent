"""Package tracking: from shipping emails and notifications (free, no key), and 17TRACK for live status.

Free path: shipping emails and phone notifications (Amazon, UPS, FedEx, USPS, DHL, store "your order shipped"
mails) are read on Buddy's server with text rules for tracking numbers and status (shipped, out for delivery,
delivered, delayed, exception). Each package gets the carrier's own tracking link.
17TRACK (optional, BUDDY_17TRACK_KEY, free tier at 17track.net): numbers are registered once and their live status
is checked every two hours while in transit (POST api.17track.net/track/v2.2/register and gettrackinfo, header
17token). 17TRACK's reply format is from its documentation; the first real check confirms it.

Said once each: out for delivery today, delivered (and, when the user is away from home, that it's waiting there),
delayed or a problem. Only tracking numbers go to 17TRACK. Delivered packages are dropped 30 days later.
"""

import json
import logging
import os
import re
import time
from datetime import datetime, timedelta, timezone

from .memory import Memory
from .tools import ToolError, require_https

log = logging.getLogger(__name__)

API = "https://api.17track.net/track/v2.2/"
CHECK_HOURS = 2
SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS packages (
    number TEXT PRIMARY KEY,
    carrier TEXT NOT NULL DEFAULT '',
    title TEXT NOT NULL DEFAULT '',       -- "Amazon: AirPods Pro", the email subject
    source TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'shipped',  -- shipped | in_transit | out_for_delivery | delivered | delayed | exception
    last_event TEXT NOT NULL DEFAULT '',
    eta TEXT NOT NULL DEFAULT '',
    told TEXT NOT NULL DEFAULT '[]',     -- statuses already said
    registered INTEGER NOT NULL DEFAULT 0,
    checked_at TEXT,
    updated_at TEXT NOT NULL,
    added_at TEXT NOT NULL
);
"""

SHIPPING = re.compile(r"(?i)\b(shipped|has shipped|on (its|the) way|tracking (number|#|id)|track (your )?(package|"
                      r"order|shipment)|out for delivery|delivered|arriving (today|tomorrow)|delivery (attempt|"
                      r"exception|update)|in transit|your (package|parcel|shipment))\b")
NUMBERS = [  # (carrier, pattern, needs the carrier named nearby)
    ("UPS", re.compile(r"\b1Z[0-9A-Z]{16}\b"), False),
    ("Amazon", re.compile(r"\bTBA\d{12}\b"), False),
    ("USPS", re.compile(r"\b(9[1-5]\d{18,20}|[A-Z]{2}\d{9}US)\b"), False),
    ("FedEx", re.compile(r"\b(\d{12}|\d{15}|\d{20})\b"), True),
    ("DHL", re.compile(r"\b(\d{10}|JD\d{18})\b"), True),
]
STATUS = [("delivered", re.compile(r"(?i)\b(was |has been |been )?delivered\b(?! by)|\bleft at (the )?(front door|"
                                   r"porch|mailroom|reception)")),
          ("out_for_delivery", re.compile(r"(?i)\bout for delivery\b|\barriving today\b")),
          ("exception", re.compile(r"(?i)\bdelivery (attempt(ed)?|exception|failed)\b|\bunable to deliver\b|"
                                   r"\baction required\b|\breturned to sender\b")),
          ("delayed", re.compile(r"(?i)\bdelayed\b|\brunning late\b|\bnew delivery date\b")),
          ("in_transit", re.compile(r"(?i)\bin transit\b|\bon (its|the) way\b")),
          ("shipped", re.compile(r"(?i)\bshipped\b"))]
RANK = {"shipped": 0, "in_transit": 1, "delayed": 2, "exception": 2, "out_for_delivery": 3, "delivered": 4}
TRACK17 = {"InfoReceived": "shipped", "InTransit": "in_transit", "OutForDelivery": "out_for_delivery",
           "AvailableForPickup": "out_for_delivery", "Delivered": "delivered", "DeliveryFailure": "exception",
           "Exception": "exception", "Expired": "exception", "NotFound": "shipped"}
LINKS = {"UPS": "https://www.ups.com/track?tracknum={n}", "FedEx": "https://www.fedex.com/fedextrack/?trknbr={n}",
         "USPS": "https://tools.usps.com/go/TrackConfirmAction?tLabels={n}",
         "DHL": "https://www.dhl.com/us-en/home/tracking.html?tracking-id={n}",
         "Amazon": "https://www.amazon.com/gp/your-account/order-history"}
SAY = {"out_for_delivery": "is out for delivery today", "delivered": "was delivered",
       "delayed": "is delayed", "exception": "has a delivery problem"}


def _fetch(url: str, data: bytes | None = None, headers: dict | None = None) -> bytes:
    import urllib.request  # noqa: PLC0415
    req = urllib.request.Request(require_https(url), data=data, headers={"User-Agent": "Buddy/1.0", **(headers or {})})
    with urllib.request.urlopen(req, timeout=25) as r:  # https only  # nosec B310
        return r.read(2_000_000)


def find_numbers(text: str) -> list[tuple[str, str]]:
    out = []
    low = text.lower()
    for carrier, pat, needs_name in NUMBERS:
        if needs_name and carrier.lower() not in low:
            continue
        for m in pat.finditer(text):
            n = m.group(0)
            if n not in [x for _, x in out]:
                out.append((carrier, n))
    return out[:5]


def status_in(text: str) -> str:
    for name, pat in STATUS:
        if pat.search(text):
            return name
    return ""


class Packages:
    def __init__(self, memory: Memory, env=None, fetch=None):
        self.memory = memory
        self.env = os.environ if env is None else env
        self.fetch = fetch or _fetch
        memory.db.executescript(SCHEMA_SQL)

    @property
    def key(self) -> str:
        return (self.env.get("BUDDY_17TRACK_KEY") or "").strip()

    def active(self) -> list[dict]:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        self.memory.db.execute("DELETE FROM packages WHERE status = 'delivered' AND updated_at < ?", (cutoff,))
        self.memory.db.execute("DELETE FROM packages WHERE updated_at < ?",  # no news for 60 days: dropped too
                               ((datetime.now(timezone.utc) - timedelta(days=60)).isoformat(),))
        rows = [dict(r) for r in self.memory.db.execute("SELECT * FROM packages ORDER BY updated_at DESC")]
        for r in rows:
            r["told"] = json.loads(r["told"] or "[]")
            r["link"] = LINKS.get(r["carrier"], "").format(n=r["number"])
        return rows

    def add(self, number: str, carrier: str = "", title: str = "", source: str = "told Buddy",
            status: str = "shipped") -> bool:
        number = re.sub(r"\s", "", number).upper()[:40]
        if len(number) < 8:
            raise ToolError("That doesn't look like a tracking number.")
        now = datetime.now(timezone.utc).isoformat()
        cur = self.memory.db.execute(
            "INSERT OR IGNORE INTO packages (number, carrier, title, source, status, updated_at, added_at) VALUES "
            "(?, ?, ?, ?, ?, ?, ?)", (number, carrier, title[:120], source[:100], status or "shipped", now, now))
        self.memory.db.commit()
        return cur.rowcount > 0

    def _set(self, number: str, status: str, event: str = "", eta: str = "") -> None:
        row = self.memory.db.execute("SELECT status FROM packages WHERE number = ?", (number,)).fetchone()
        if not row or not status:
            return
        if RANK.get(status, 0) < RANK.get(row["status"], 0) and status not in ("delayed", "exception"):
            return  # an older message doesn't undo a newer status
        self.memory.db.execute("UPDATE packages SET status = ?, last_event = CASE WHEN ? != '' THEN ? ELSE last_event "
                               "END, eta = CASE WHEN ? != '' THEN ? ELSE eta END, updated_at = ? WHERE number = ?",
                               (status, event, event[:200], eta, eta[:40], datetime.now(timezone.utc).isoformat(),
                                number))
        self.memory.db.commit()

    def scan(self) -> int:
        """New shipping emails and notifications: tracking numbers and status, by text rules (no AI, no key)."""
        last = int(self.memory.get("packages:last_record") or 0)
        try:
            rows = self.memory.db.execute(
                "SELECT id, kind, title, body, data FROM records WHERE kind IN ('email', 'notification') AND id > ? "
                "ORDER BY id LIMIT 500", (last,)).fetchall()
        except Exception:
            return 0
        found = 0
        known = {p["number"]: p for p in self.active()}
        for r in rows:
            last = max(last, r["id"])
            text = f"{r['title'] or ''}\n{r['body'] or ''}"[:5000]
            if not SHIPPING.search(text):
                continue
            meta = json.loads(r["data"] or "{}")
            who = meta.get("app_name") or (meta.get("from") or "").split("<")[0].strip()
            st = status_in(text)
            for carrier, number in find_numbers(text):
                if number not in known:
                    self.add(number, carrier, f"{who}: {r['title'] or ''}".strip(": ")[:120],
                             f"{'an email' if r['kind'] == 'email' else 'a notification'} from {who}", st or "shipped")
                    known[number] = {"number": number}
                    found += 1
                elif st:
                    self._set(number, st, (r["title"] or "")[:200])
            if st and not find_numbers(text):  # "Your Amazon package was delivered" with no number: match by sender
                for p in self.active():
                    if p["status"] != "delivered" and who and who.lower()[:6] in p["title"].lower():
                        self._set(p["number"], st, (r["title"] or "")[:200])
                        break
        self.memory.set("packages:last_record", str(last))
        return found

    # --- 17TRACK -------------------------------------------------------------------------------------------
    def _post(self, endpoint: str, body: list) -> dict:
        from .travel import log_call  # noqa: PLC0415
        started = time.monotonic()
        try:
            got = json.loads(self.fetch(API + endpoint, json.dumps(body).encode(),
                                        {"17token": self.key, "Content-Type": "application/json"}) or b"{}")
            ok = got.get("code") == 0
            log_call(self.memory, "17track", endpoint, ok, time.monotonic() - started, "packages", cost=0.0)
        except Exception as e:
            log_call(self.memory, "17track", endpoint, False, time.monotonic() - started, "packages",
                     type(e).__name__, cost=0.0)
            raise
        if got.get("code") != 0:
            msg = ((got.get("data") or {}).get("errors") or [{}])[0].get("message", "")
            raise ToolError("17TRACK didn't accept the key." if got.get("code") == 401 else f"17TRACK: {msg}")
        return got.get("data") or {}

    def refresh(self) -> int:
        """Live status from 17TRACK for packages not yet delivered, every CHECK_HOURS (needs BUDDY_17TRACK_KEY)."""
        if not self.key:
            return 0
        due_before = (datetime.now(timezone.utc) - timedelta(hours=CHECK_HOURS)).isoformat()
        todo = [p for p in self.active() if p["status"] != "delivered" and (not p["checked_at"] or
                                                                            p["checked_at"] < due_before)][:40]
        if not todo:
            return 0
        new = [p for p in todo if not p["registered"]]
        if new:
            self._post("register", [{"number": p["number"]} for p in new])
            self.memory.db.executemany("UPDATE packages SET registered = 1 WHERE number = ?",
                                       [(p["number"],) for p in new])
        data = self._post("gettrackinfo", [{"number": p["number"]} for p in todo])
        n = 0
        for a in data.get("accepted") or []:
            info = a.get("track_info") or {}
            latest = (info.get("latest_status") or {}).get("status", "")
            ev = info.get("latest_event") or {}
            eta = (((info.get("time_metrics") or {}).get("estimated_delivery_date") or {}).get("from") or "")[:10]
            event = " ".join(x for x in (ev.get("description"), ev.get("location")) if x)
            self._set(a.get("number", ""), TRACK17.get(latest, ""), event, eta)
            n += 1
        now = datetime.now(timezone.utc).isoformat()
        self.memory.db.executemany("UPDATE packages SET checked_at = ? WHERE number = ?",
                                   [(now, p["number"]) for p in todo])
        self.memory.db.commit()
        return n

    # --- what to say -----------------------------------------------------------------------------------------
    def news(self, away_from_home: bool = False) -> list[dict]:
        out = []
        for p in self.active():
            st = p["status"]
            if st in SAY and st not in p["told"]:
                what = p["title"] or f"{p['carrier']} package {p['number'][-6:]}"
                text = f"Your package ({what}) {SAY[st]}"
                if st == "delivered" and away_from_home:
                    text += "; it's waiting at home"
                if p["last_event"] and st != "delivered":
                    text += f": {p['last_event']}"
                if p["eta"] and st == "delayed":
                    text += f". New estimate: {p['eta']}"
                out.append({"number": p["number"], "status": st, "text": text + ".",
                            "urgent": st in ("exception",)})
        return out

    def told(self, item: dict) -> None:
        row = self.memory.db.execute("SELECT told FROM packages WHERE number = ?", (item["number"],)).fetchone()
        if row:
            told = json.loads(row["told"] or "[]") + [item["status"]]
            self.memory.db.execute("UPDATE packages SET told = ? WHERE number = ?", (json.dumps(told), item["number"]))
            self.memory.db.commit()

    def lines(self) -> list[str]:
        return [f"- package: {p['title'] or p['carrier']} ({p['carrier']} {p['number']}): {p['status'].replace('_', ' ')}"
                + (f", {p['last_event']}" if p["last_event"] else "") + (f", estimate {p['eta']}" if p["eta"] else "")
                for p in self.active() if p["status"] != "delivered"][:8]


PACKAGES_TOOL = {
    "name": "packages",
    "description": "The user's packages: from shipping emails and notifications (Amazon, UPS, FedEx, USPS, DHL), and "
    "17TRACK live status when its key is set. list (each with carrier, status, latest event, estimate and the "
    "carrier's tracking link), track (number, optional title: add one by hand), remove (number), test ('test package "
    "tracking').",
    "input_schema": {"type": "object", "properties": {
        "action": {"type": "string", "enum": ["list", "track", "remove", "test"]},
        "number": {"type": "string"}, "title": {"type": "string"}},
        "required": ["action"], "additionalProperties": False},
}


def run_tool(memory: Memory, args: dict) -> str:
    pk = Packages(memory)
    a = args.get("action")
    if a == "track":
        number = args.get("number") or ""
        carrier = (find_numbers(f"ups fedex usps dhl {number}") or [("", "")])[0][0]
        added = pk.add(number, carrier, args.get("title") or "")
        return ("Tracking it." if added else "Already tracking that one.") + (
            " Live status comes from 17TRACK every two hours." if pk.key else
            " I'll update it from the carrier's emails and notifications (add a free 17TRACK key for live status).")
    if a == "remove":
        n = memory.db.execute("DELETE FROM packages WHERE number = ?", (re.sub(r"\s", "", args.get("number") or "")
                                                                        .upper(),)).rowcount
        memory.db.commit()
        return "Removed." if n else "I wasn't tracking that number."
    if a == "test":
        if not pk.key:
            return ("Packages: reading shipping emails and notifications (free). For live status from every carrier, "
                    "add a free 17TRACK key: sudo bash /opt/buddy/deploy/set.sh BUDDY_17TRACK_KEY.")
        try:
            pk._post("gettrackinfo", [{"number": "1Z999AA10123456784"}])
            return "✓ 17TRACK: key works."
        except Exception as e:
            return f"✗ 17TRACK: {e}"
    items = pk.active()
    return json.dumps(items, ensure_ascii=False, default=str) if items else "No packages on the way."
