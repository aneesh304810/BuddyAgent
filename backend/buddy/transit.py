"""Transit alerts for the user's commute: MTA (subway, buses, LIRR, Metro-North), NJ Transit, and PATH.

The user names the lines they ride and when ("the Q weekdays 8 to 9:30", "Hudson-Bergen Light Rail", "PATH
Newark-WTC from Journal Square"). During those times Buddy checks each line's official alerts every few minutes,
messages once when a delay, suspension or other change starts, and once when it clears. Any time, "is the Q
running?" gets the live answer. Read-only: Buddy only reads public alert feeds, it never books or buys anything.

Sources (official):
- MTA: the GTFS-realtime alerts feed as JSON, api-endpoint.mta.info (no key needed).
- PATH: the Port Authority's real-time arrivals (ridepath.json) and its PATH incidents feed (www.panynj.gov).
- NJ Transit: the RailData API (raildata.njtransit.com): getToken with the developer account's username and
  password (BUDDY_NJT_USERNAME / BUDDY_NJT_PASSWORD, from developer.njtransit.com), then getStationMSG for the
  service messages. The token is kept and reused (NJ Transit limits how often a new one can be made).
Only line and station names go to these services; nothing about the user.
"""

import json
import logging
import os
import re
import time
import uuid
from datetime import datetime, timezone

from .memory import Memory
from .tools import ToolError, local_now, require_https

log = logging.getLogger(__name__)

MTA_ALERTS = "https://api-endpoint.mta.info/Dataservice/mtagtfsfeeds/camsys%2Fall-alerts.json"
PATH_ARRIVALS = "https://www.panynj.gov/bin/portauthority/ridepath.json"
PATH_INCIDENTS = "https://www.panynj.gov/bin/portauthority/everbridge/incidents?status=All&department=Path"
NJT_BASE = "https://raildata.njtransit.com/api/TrainData/"
UA = "Buddy/1.0 (personal assistant; read-only)"
CACHE_SECONDS = 120
# NJ Transit's data feed agreement: say where the data came from, that it may not be real time (Buddy checks every
# few minutes), never that it's accurate or NJT-licensed; use the text as-is; stay well under 100,000 calls a day.
NJT_NOTE = "NJ Transit data, fetched by Buddy's server; may not be real time"
CHECK_MINUTES = 5

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS transit_watch (
    id INTEGER PRIMARY KEY,
    provider TEXT NOT NULL,              -- mta | njt | path
    line TEXT NOT NULL,                  -- as the user said it: "Q", "Hudson-Bergen Light Rail", "PATH Newark-WTC"
    route TEXT NOT NULL DEFAULT '',      -- MTA route id when there is one: Q, 1, M15
    station TEXT NOT NULL DEFAULT '',
    days TEXT NOT NULL DEFAULT 'weekdays',   -- weekdays | daily | weekends | mon,tue,...
    times TEXT NOT NULL DEFAULT '[]',    -- ["07:00-09:30", "16:30-19:00"]; [] = whenever Buddy is checking
    added_at TEXT NOT NULL,
    UNIQUE (provider, line)
);
CREATE TABLE IF NOT EXISTS transit_open (   -- alerts already told, until they clear
    key TEXT PRIMARY KEY, watch_id INTEGER NOT NULL, text TEXT NOT NULL, told_at TEXT NOT NULL
);
"""

NJT_LINES = {  # NJ Transit lines and the words their messages use
    "Hudson-Bergen Light Rail": ["hudson-bergen", "hudson bergen", "hblr", "light rail"],
    "Newark Light Rail": ["newark light rail", "nlr", "light rail"],
    "River LINE": ["river line", "riverline"],
    "Northeast Corridor": ["northeast corridor", "nec"],
    "North Jersey Coast Line": ["north jersey coast", "njcl"],
    "Morris & Essex Lines": ["morris & essex", "morris and essex", "m&e", "morristown line", "gladstone"],
    "Montclair-Boonton Line": ["montclair-boonton", "montclair boonton", "mobo"],
    "Raritan Valley Line": ["raritan valley", "rvl"],
    "Main/Bergen County Line": ["main line", "bergen county", "main/bergen"],
    "Pascack Valley Line": ["pascack valley"],
    "Port Jervis Line": ["port jervis"],
    "Atlantic City Rail Line": ["atlantic city"],
}
PATH_LINES = {"Newark-WTC": ["newark", "harrison", "wtc", "world trade"], "Hoboken-WTC": ["hoboken", "wtc"],
              "Journal Square-33rd St": ["journal square", "jsq", "33rd"], "Hoboken-33rd St": ["hoboken", "33rd"],
              "Journal Square-33rd St (via Hoboken)": ["journal square", "hoboken", "33rd"]}
PATH_STATIONS = {"NWK": "Newark", "HAR": "Harrison", "JSQ": "Journal Square", "GRV": "Grove Street",
                 "NEW": "Newport", "EXP": "Exchange Place", "HOB": "Hoboken", "WTC": "World Trade Center",
                 "CHR": "Christopher Street", "09S": "9th Street", "14S": "14th Street", "23S": "23rd Street",
                 "33S": "33rd Street"}
MTA_WORDS = {"lirr": "LI", "long island rail": "LI", "metro-north": "MNR", "metro north": "MNR"}
FILLER = re.compile(r"\b(the|train|trains|line|lines|branch|subway|bus|service|my|lirr|metro-?north|mta|nyc)\b", re.I)
DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


def _fetch(url: str, data: bytes | None = None, headers: dict | None = None, timeout: int = 30) -> bytes:
    import urllib.request
    req = urllib.request.Request(require_https(url), data=data, headers={"User-Agent": UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:  # https only  # nosec B310
        return r.read(20_000_000)


def _multipart(fields: dict) -> tuple[bytes, str]:
    """NJ Transit's API takes multipart/form-data."""
    boundary = uuid.uuid4().hex
    body = b"".join(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode()
                    for k, v in fields.items()) + f"--{boundary}--\r\n".encode()
    return body, f"multipart/form-data; boundary={boundary}"


def provider_of(line: str) -> str:
    low = line.lower()
    if re.search(r"\bpath\b|newark-wtc|hoboken-wtc|jsq-33|hob-33", low):
        return "path"
    if re.search(r"\bnj ?t(ransit)?\b|light rail|\bhblr\b|river ?line", low) or \
            any(w in low for words in NJT_LINES.values() for w in words if len(w) > 4):
        return "njt"
    return "mta"


def mta_route(line: str) -> str:
    """'Q train' -> 'Q', 'the 7' -> '7', 'M15 bus' -> 'M15', 'LIRR Port Washington' -> ''."""
    if re.search(r"lirr|long island|metro-?north", line, re.I):
        return ""
    rest = FILLER.sub(" ", line).split()
    if len(rest) == 1 and re.fullmatch(r"[A-Za-z]{0,3}\d{0,3}[A-Za-z]?", rest[0]) and len(rest[0]) <= 5:
        return rest[0].upper()
    return ""


def _text(t: dict | None) -> str:
    for tr in (t or {}).get("translation") or []:
        if tr.get("language", "en") == "en":
            return " ".join(str(tr.get("text", "")).split())
    return ""


def _strings(x, keys=("message", "description", "title", "subject", "text", "headline")) -> list[str]:
    """Every string under a key that looks like message text, anywhere in a JSON value (PATH's incident feed)."""
    out = []
    if isinstance(x, dict):
        for k, v in x.items():
            if isinstance(v, str) and any(w in k.lower() for w in keys) and v.strip():
                out.append(re.sub(r"<[^>]+>", " ", v))
            elif isinstance(v, (dict, list)):
                out += _strings(v, keys)
    elif isinstance(x, list):
        for v in x:
            out += _strings(v, keys)
    return [" ".join(s.split()) for s in out]


class Transit:
    def __init__(self, memory: Memory, env=None, fetch=None):
        self.memory = memory
        self.env = os.environ if env is None else env
        self.fetch = fetch or _fetch
        self._cache: dict[str, tuple[float, object]] = {}
        from .markets import API_CALLS_SQL  # noqa: PLC0415  (Insights counts every API call)
        memory.db.executescript(SCHEMA_SQL + API_CALLS_SQL)

    def setting(self, key: str) -> str:
        return (self.env.get(key) or "").strip()

    def _get(self, provider: str, url: str, data: bytes | None = None, headers: dict | None = None):
        hit = self._cache.get(url + (data.decode(errors="ignore")[-80:] if data else ""))
        if hit and time.monotonic() - hit[0] < CACHE_SECONDS:
            return hit[1]
        from .travel import log_call  # noqa: PLC0415
        started = time.monotonic()
        try:
            raw = self.fetch(url, data, headers)
            got = raw if isinstance(raw, (dict, list)) else json.loads(raw or b"null")
            log_call(self.memory, provider, url.split("?")[0][-60:], True, time.monotonic() - started, "transit", cost=0.0)
        except Exception as e:
            log_call(self.memory, provider, url.split("?")[0][-60:], False, time.monotonic() - started, "transit",
                     type(e).__name__, cost=0.0)
            raise
        self._cache[url + (data.decode(errors="ignore")[-80:] if data else "")] = (time.monotonic(), got)
        return got

    # --- watches -----------------------------------------------------------------------------------------
    def watches(self) -> list[dict]:
        rows = [dict(r) for r in self.memory.db.execute("SELECT * FROM transit_watch ORDER BY id")]
        for r in rows:
            r["times"] = json.loads(r["times"] or "[]")
        return rows

    def watch(self, line: str, station: str = "", days: str = "", times: list[str] | None = None) -> str:
        line = " ".join(str(line or "").split())[:80]
        if not line:
            raise ToolError("Which line? For example: the Q train, Hudson-Bergen Light Rail, PATH Newark-WTC.")
        provider = provider_of(line)
        spans = []
        for t in times or []:
            m = re.fullmatch(r"\s*(\d{1,2}):?(\d{2})?\s*-\s*(\d{1,2}):?(\d{2})?\s*", str(t))
            if not m:
                raise ToolError(f"Times look like 07:00-09:30, not {t!r}.")
            a, b = f"{int(m[1]):02d}:{m[2] or '00'}", f"{int(m[3]):02d}:{m[4] or '00'}"
            spans.append(f"{a}-{b}")
        days = (days or "weekdays").lower().strip()
        if days not in ("weekdays", "daily", "weekends") and not all(d[:3] in DAYS for d in re.split(r"[,\s]+", days) if d):
            raise ToolError("Days are weekdays, daily, weekends, or a list like mon,wed,fri.")
        if provider == "njt" and not (self.setting("BUDDY_NJT_USERNAME") and self.setting("BUDDY_NJT_PASSWORD")):
            need = (" NJ Transit's alerts need its free developer account: register at developer.njtransit.com, "
                    "then run sudo bash /opt/buddy/deploy/set.sh BUDDY_NJT_USERNAME and BUDDY_NJT_PASSWORD.")
        else:
            need = ""
        self.memory.db.execute(
            "INSERT INTO transit_watch (provider, line, route, station, days, times, added_at) VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT (provider, line) DO UPDATE SET station = excluded.station, days = excluded.days, "
            "times = excluded.times",
            (provider, line, mta_route(line) if provider == "mta" else "", station.strip()[:60], days,
             json.dumps(spans), datetime.now(timezone.utc).isoformat()))
        self.memory.db.commit()
        when = f"{days} {', '.join(spans)}" if spans else "whenever I'm checking"
        name = {"mta": "MTA", "njt": "NJ Transit", "path": "PATH"}[provider]
        return f"Watching {line} ({name}) {when}: I'll tell you when a delay or suspension starts, and when it clears.{need}"

    def unwatch(self, line: str) -> str:
        n = self.memory.db.execute("DELETE FROM transit_watch WHERE lower(line) = lower(?)", (line.strip(),)).rowcount
        self.memory.db.commit()
        return f"Stopped watching {line}." if n else f"I wasn't watching {line}."

    @staticmethod
    def active(w: dict, now: datetime | None = None) -> bool:
        now = now or local_now()
        day = DAYS[now.weekday()]
        days = w["days"]
        if days == "weekdays" and now.weekday() >= 5 or days == "weekends" and now.weekday() < 5:
            return False
        if days not in ("weekdays", "weekends", "daily") and day not in [d[:3] for d in re.split(r"[,\s]+", days)]:
            return False
        if not w["times"]:
            return True
        hm = now.strftime("%H:%M")
        return any(a <= hm <= b for a, b in (t.split("-") for t in w["times"]))

    # --- feeds -------------------------------------------------------------------------------------------
    def mta_alerts(self, w: dict) -> list[dict]:
        feed = self._get("mta", MTA_ALERTS)
        now = time.time()
        route = w.get("route") or ""
        agency = next((a for k, a in MTA_WORDS.items() if k in w["line"].lower()), "")
        words = [x.lower() for x in FILLER.sub(" ", w["line"]).split() if len(x) > 2]
        out = []
        for e in (feed or {}).get("entity") or []:
            a = e.get("alert") or {}
            periods = a.get("active_period") or [{}]
            if not any((p.get("start") or 0) <= now <= (p.get("end") or now + 1) for p in periods):
                continue
            ents = a.get("informed_entity") or []
            head, more = _text(a.get("header_text")), _text(a.get("description_text"))
            if route:
                hit = any(str(ie.get("route_id", "")).upper() == route for ie in ents)
            else:
                hit = (not agency or any(ie.get("agency_id") == agency for ie in ents)) and \
                    bool(words) and all(x in (head + " " + more).lower() for x in words)
            if hit:
                kind = (a.get("transit_realtime.mercury_alert") or {}).get("alert_type") or "Alert"
                out.append({"key": f"mta:{e.get('id')}", "kind": kind, "text": head, "more": more[:400]})
        return out

    def path_alerts(self, w: dict) -> list[dict]:
        got = self._get("path", PATH_INCIDENTS)
        items = (got or {}).get("data") if isinstance(got, dict) else got
        out = []
        for i, item in enumerate(items or []):
            text = " ".join(dict.fromkeys(_strings(item)))[:500]
            if text:
                key = str((item or {}).get("id") or (item or {}).get("incidentId") or hash(text)) if isinstance(item, dict) else str(i)
                out.append({"key": f"path:{key}", "kind": "PATH alert", "text": text, "more": ""})
        return out

    def path_arrivals(self, station: str) -> list[dict]:
        code = next((c for c, n in PATH_STATIONS.items() if station.lower() in (c.lower(), n.lower())
                     or (len(station) > 3 and station.lower() in n.lower())), "")
        if not code:
            return []
        got = self._get("path", PATH_ARRIVALS)
        res = next((r for r in (got or {}).get("results") or [] if r.get("consideredStation") == code), {})
        return [{"to": m.get("headSign", ""), "in": m.get("arrivalTimeMessage", ""), "direction": d.get("label", "")}
                for d in res.get("destinations") or [] for m in d.get("messages") or []]

    def njt_token(self, fresh: bool = False) -> str:
        user, pw = self.setting("BUDDY_NJT_USERNAME"), self.setting("BUDDY_NJT_PASSWORD")
        if not user or not pw:
            raise ToolError("NJ Transit needs its free developer account: register at developer.njtransit.com, then "
                            "run sudo bash /opt/buddy/deploy/set.sh BUDDY_NJT_USERNAME and BUDDY_NJT_PASSWORD.")
        saved = self.memory.get("transit:njt_token") or ""
        if saved and not fresh:
            return saved
        if self.memory.get("transit:njt_token_tried") == local_now().strftime("%Y-%m-%dT%H") and not saved:
            raise ToolError("NJ Transit didn't give a token this hour; I'll try again later.")
        self.memory.set("transit:njt_token_tried", local_now().strftime("%Y-%m-%dT%H"))  # new tokens are rationed
        body, ctype = _multipart({"username": user, "password": pw})
        got = self._get("njt", NJT_BASE + "getToken", body, {"Content-Type": ctype})
        token = (got or {}).get("UserToken") if (got or {}).get("Authenticated") in (True, "True", "true") else ""
        if not token:
            raise ToolError("NJ Transit didn't accept the developer username and password.")
        self.memory.set("transit:njt_token", token)
        return token

    def njt_messages(self) -> list[dict]:
        for attempt in (0, 1):
            body, ctype = _multipart({"token": self.njt_token(fresh=attempt == 1), "station": "", "line": ""})
            try:
                got = self._get("njt", NJT_BASE + "getStationMSG", body, {"Content-Type": ctype})
            except Exception as e:
                if attempt == 0 and getattr(e, "code", None) in (401, 403):
                    continue
                raise
            if isinstance(got, dict) and got.get("errorMessage"):  # an expired token
                if attempt == 0:
                    continue
                raise ToolError(f"NJ Transit: {got['errorMessage']}")
            return got if isinstance(got, list) else []
        return []

    def njt_alerts(self, w: dict) -> list[dict]:
        low = w["line"].lower()
        name = next((n for n, words in NJT_LINES.items() if n.lower() in low or any(x in low for x in words if len(x) > 4)),
                    "")
        words = NJT_LINES.get(name) or [x for x in FILLER.sub(" ", re.sub(r"\bnj ?t(ransit)?\b", " ", low)).split()
                                        if len(x) > 2]
        if name == "Hudson-Bergen Light Rail":
            words = ["hudson-bergen", "hudson bergen", "hblr"]
        out = []
        for m in self.njt_messages():
            scope = f"{m.get('MSG_LINE_SCOPE') or ''} {m.get('MSG_TEXT') or ''}".lower()
            if any(x in scope for x in words):
                out.append({"key": f"njt:{m.get('MSG_ID')}", "kind": m.get("MSG_TYPE") or "NJ Transit alert",
                            "text": str(m.get("MSG_TEXT") or "").strip(), "more": "",  # as-is (NJT's terms)
                            "url": m.get("MSG_URL") or ""})
        return out

    def alerts_for(self, w: dict) -> list[dict]:
        return {"mta": self.mta_alerts, "njt": self.njt_alerts, "path": self.path_alerts}[w["provider"]](w)

    # --- what the user sees ------------------------------------------------------------------------------
    def status(self, line: str = "") -> dict:
        targets = [w for w in self.watches() if not line or line.lower() in w["line"].lower()]
        if line and not targets:  # a line they don't watch: look it up all the same
            p = provider_of(line)
            targets = [{"id": 0, "provider": p, "line": line, "route": mta_route(line) if p == "mta" else "",
                        "station": "", "days": "daily", "times": []}]
        out = []
        for w in targets:
            item = {"line": w["line"], "provider": w["provider"]}
            try:
                item["alerts"] = self.alerts_for(w)
                item["ok"] = not any(re.search(r"suspend|delay|no service|not running|part suspended|reduced|shuttle",
                                               a["kind"] + " " + a["text"], re.I) for a in item["alerts"])
            except ToolError as e:
                item["error"] = str(e)
            except Exception as e:
                item["error"] = f"couldn't reach {w['provider'].upper()} ({type(e).__name__})"
            if w["provider"] == "path" and w.get("station"):
                try:
                    item["next_trains"] = self.path_arrivals(w["station"])[:6]
                except Exception:
                    pass
            out.append(item)
        return {"lines": out, "checked_at": local_now().strftime("%H:%M"),
                "note": ("From the official MTA, NJ Transit and PATH alert feeds. " + NJT_NOTE
                         if any(x["provider"] == "njt" for x in out) else
                         "From the official MTA, NJ Transit and PATH alert feeds.") if out else
                "No lines watched yet. Say which line you ride and when, e.g. 'watch the Q weekdays 8 to 9:30'."}

    def check(self, now: datetime | None = None) -> list[dict]:
        """For the lines being ridden now: new alerts, and alerts that have cleared. Each said once."""
        out = []
        for w in self.watches():
            if not self.active(w, now):
                continue
            try:
                current = self.alerts_for(w)
            except Exception:
                log.info("transit check %s failed", w["line"], exc_info=True)
                continue
            keys = {a["key"] for a in current}
            told = {r["key"]: r["text"] for r in self.memory.db.execute(
                "SELECT key, text FROM transit_open WHERE watch_id = ?", (w["id"],))}
            for a in current:
                if a["key"] not in told:
                    out.append({"kind": "start", "watch": w["line"], "key": a["key"], "watch_id": w["id"],
                                "text": f"{w['line']}: {a['text']}" + (f" {a['more'][:200]}" if a.get("more") and
                                                                      len(a["text"]) < 60 else "")
                                + (f" ({NJT_NOTE})" if w["provider"] == "njt" else "")})
            for key, text in told.items():
                if key not in keys:
                    out.append({"kind": "clear", "watch": w["line"], "key": key, "watch_id": w["id"],
                                "text": f"{w['line']}: that alert has cleared ({text[len(w['line']) + 2:][:90]}…)."})
        return out

    def told(self, alert: dict) -> None:
        if alert["kind"] == "start":
            self.memory.db.execute("INSERT OR REPLACE INTO transit_open (key, watch_id, text, told_at) VALUES (?, ?, ?, ?)",
                                   (alert["key"], alert["watch_id"], alert["text"][:300],
                                    datetime.now(timezone.utc).isoformat()))
        else:
            self.memory.db.execute("DELETE FROM transit_open WHERE key = ?", (alert["key"],))
        self.memory.db.commit()

    def due(self, now: datetime | None = None) -> bool:
        now = now or local_now()
        slot = now.strftime("%Y-%m-%dT%H:") + str(now.minute // CHECK_MINUTES)
        if self.memory.get("transit:checked") == slot or not any(self.active(w, now) for w in self.watches()):
            return False
        self.memory.set("transit:checked", slot)
        return True

    def test(self) -> str:
        lines = []
        for label, fn in (("MTA alerts", lambda: f"{len(self._get('mta', MTA_ALERTS).get('entity') or [])} alerts in the feed"),
                          ("PATH arrivals", lambda: f"{len(self._get('path', PATH_ARRIVALS).get('results') or [])} stations reporting"),
                          ("PATH incidents", lambda: f"{len(self._get('path', PATH_INCIDENTS).get('data') or [])} open incidents"),
                          ("NJ Transit", lambda: f"{len(self.njt_messages())} service messages")):
            try:
                lines.append(f"✓ {label}: {fn()}")
            except Exception as e:
                lines.append(f"✗ {label}: {e if isinstance(e, ToolError) else type(e).__name__}")
        return "Transit feeds:\n" + "\n".join(lines)


TRANSIT_TOOL = {
    "name": "transit",
    "description": "Live transit alerts for the user's commute, from the official MTA (subway, buses, LIRR, "
    "Metro-North), NJ Transit (trains and light rail) and PATH feeds. status (line?): is it running, any delays or "
    "suspensions now (any line, watched or not; PATH with a station also gives the next trains). watch (line, station?, "
    "days?, times?): watch a line they ride, e.g. line 'Q', days 'weekdays', times ['08:00-09:30','17:00-19:00']; Buddy "
    "messages when an alert starts and when it clears during those times. unwatch (line). list: watched lines. test: "
    "check each feed works. Read-only.",
    "input_schema": {"type": "object", "properties": {
        "action": {"type": "string", "enum": ["status", "watch", "unwatch", "list", "test"]},
        "line": {"type": "string"}, "station": {"type": "string"}, "days": {"type": "string"},
        "times": {"type": "array", "items": {"type": "string"}}},
        "required": ["action"], "additionalProperties": False},
}


def run_tool(memory: Memory, args: dict) -> str:
    t = Transit(memory)
    a = args.get("action")
    if a == "watch":
        return t.watch(args.get("line", ""), args.get("station") or "", args.get("days") or "", args.get("times"))
    if a == "unwatch":
        return t.unwatch(args.get("line", ""))
    if a == "list":
        return json.dumps(t.watches(), ensure_ascii=False)
    if a == "test":
        return t.test()
    return json.dumps(t.status(args.get("line") or ""), ensure_ascii=False)


# --- what's near the user: the Travel screen's alerts ---------------------------------------------------------
PATH_COORDS = {"NWK": (40.7342, -74.1644), "HAR": (40.7393, -74.1558), "JSQ": (40.7327, -74.0628),
               "GRV": (40.7197, -74.0430), "NEW": (40.7270, -74.0337), "EXP": (40.7163, -74.0326),
               "HOB": (40.7359, -74.0294), "WTC": (40.7126, -74.0099), "CHR": (40.7330, -74.0072),
               "09S": (40.7343, -73.9990), "14S": (40.7375, -73.9969), "23S": (40.7429, -73.9929),
               "33S": (40.7491, -73.9882)}
LIGHT_RAIL = {  # a few points along each NJ Transit light rail line
    "Hudson-Bergen Light Rail": [(40.6626, -74.1106), (40.7045, -74.0560), (40.7163, -74.0326), (40.7355, -74.0277),
                                 (40.7488, -74.0381), (40.7590, -74.0236), (40.7768, -74.0117), (40.7832, -74.0225),
                                 (40.7926, -74.0393), (40.7290, -74.0907)],
    "Newark Light Rail": [(40.7347, -74.1645), (40.7472, -74.1717), (40.7779, -74.1789), (40.7940, -74.1870)],
    "River LINE": [(40.2178, -74.7545), (40.0777, -74.8631), (39.9447, -75.1214), (40.0137, -74.9917)],
}
NYC_COUNTIES = ("new york county", "kings county", "queens county", "bronx county", "richmond county")
URGENT = re.compile(r"suspend|delay|no (scheduled )?service|not running|shuttle|stops? (skipped|bypass)|express|local", re.I)


def km(a: tuple, b: tuple) -> float:
    import math
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 6371 * 2 * math.asin(math.sqrt(h))


def systems_near(here: dict) -> dict:
    """Which transit applies where the user is: MTA in New York City, LIRR / Metro-North around it, PATH near its
    stations, NJ Transit in New Jersey, and each light rail line near its stops."""
    p = (here["lat"], here["lng"])
    state = (here.get("state") or "").lower()
    in_metro = 39.8 <= p[0] <= 41.4 and -75.6 <= p[1] <= -71.8
    nj = state == "new jersey" or (not state and in_metro and p[1] < -74.02)
    ny = state == "new york" or (not state and in_metro and p[1] >= -74.03)
    nyc = ny and ((here.get("county") or "").lower() in NYC_COUNTIES or
                  (40.49 <= p[0] <= 40.92 and -74.26 <= p[1] <= -73.70))
    side = [c for c in PATH_COORDS if (PATH_COORDS[c][1] < -74.02) == nj] if nj != ny else list(PATH_COORDS)
    path_stop = min(side or PATH_COORDS, key=lambda c: km(p, PATH_COORDS[c]))  # your side of the Hudson
    out = {"mta_subway": nyc, "lirr": ny and not nyc and p[1] > -73.75 and p[0] < 41.2,
           "metro_north": ny and not nyc and p[0] > 40.88 and p[1] < -73.4,
           "path": km(p, PATH_COORDS[path_stop]) <= 5.0, "path_station": path_stop, "njt": nj,
           "light_rail": [name for name, pts in LIGHT_RAIL.items() if min(km(p, q) for q in pts) <= 3.0]}
    out["path_km"] = round(km(p, PATH_COORDS[path_stop]), 1)
    return out


def _nearby_mta(t: "Transit", agency: str, label: str, limit: int = 5) -> dict:
    feed = t._get("mta", MTA_ALERTS)
    now = time.time()
    alerts = []
    for e in (feed or {}).get("entity") or []:
        a = e.get("alert") or {}
        if not any((p.get("start") or 0) <= now <= (p.get("end") or now + 1) for p in a.get("active_period") or [{}]):
            continue
        ents = a.get("informed_entity") or []
        if not any(ie.get("agency_id") == agency for ie in ents):
            continue
        kind = (a.get("transit_realtime.mercury_alert") or {}).get("alert_type") or "Alert"
        if kind.lower().startswith("planned") or not URGENT.search(kind):  # live problems, not weekend work
            continue
        routes = sorted({str(ie.get("route_id")) for ie in ents if ie.get("route_id")})
        alerts.append({"key": f"mta:{e.get('id')}", "kind": kind, "text": _text(a.get("header_text")), "routes": routes})
    rank = {"suspended": 0, "part suspended": 1, "delays": 2}
    alerts.sort(key=lambda x: rank.get(x["kind"].lower(), 3))
    return {"line": label, "provider": "mta", "alerts": alerts[:limit], "more": max(0, len(alerts) - limit),
            "ok": not alerts}


def nearby(memory: Memory, transit: "Transit | None" = None, location=None) -> dict:
    """The Travel screen's alerts: the lines you watch, plus what applies where you are right now."""
    from .location import Location  # noqa: PLC0415
    t = transit or Transit(memory)
    here = (location or Location(memory)).current()
    lines, seen = [], set()
    for x in t.status()["lines"] if t.watches() else []:
        x["why"] = "you watch this"
        lines.append(x)
        seen.add(x["line"].lower())
    where, near = "", {}
    if here:
        where = ", ".join(v for v in (here.get("area"), here.get("city")) if v)
        near = systems_near(here)

        def add(item: dict, why: str) -> None:
            if item["line"].lower() not in seen:
                item["why"] = why
                lines.append(item)
                seen.add(item["line"].lower())

        def safe(fn, line, provider):
            try:
                return fn()
            except ToolError as e:
                return {"line": line, "provider": provider, "alerts": [], "error": str(e)}
            except Exception as e:
                return {"line": line, "provider": provider, "alerts": [], "error": f"couldn't reach it ({type(e).__name__})"}
        for name in near["light_rail"]:
            add(safe(lambda n=name: {**t.status(n)["lines"][0]}, name, "njt"), "a light rail line near you")
        if near["path"]:
            code = near["path_station"]

            def path_item():
                alerts = t.path_alerts({"line": "PATH"})
                return {"line": "PATH", "provider": "path", "alerts": alerts, "ok": not alerts,
                        "station": PATH_STATIONS[code], "next_trains": t.path_arrivals(code)[:6]}
            add(safe(path_item, "PATH", "path"), f"{PATH_STATIONS[code]} station is {near['path_km']} km away")
        if near["mta_subway"]:
            add(safe(lambda: _nearby_mta(t, "MTASBWY", "NYC subway"), "NYC subway", "mta"), "you're in New York City")
        if near["lirr"]:
            add(safe(lambda: _nearby_mta(t, "LI", "LIRR"), "LIRR", "mta"), "you're on Long Island")
        if near["metro_north"]:
            add(safe(lambda: _nearby_mta(t, "MNR", "Metro-North"), "Metro-North", "mta"), "you're north of the city")
    njt = any(x["provider"] == "njt" for x in lines)
    note = "From the official MTA, NJ Transit and PATH feeds." + (f" {NJT_NOTE}." if njt else "")
    if not here:
        note = "Share your location (Settings → Share my location) to see alerts for where you are. " + note
    elif not lines:
        note = "No transit Buddy follows near you here. " + note
    return {"where": where, "lines": lines, "near": near, "checked_at": local_now().strftime("%H:%M"), "note": note}
