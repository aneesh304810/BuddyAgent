"""Buddy's maps of the user's world, beyond where information is (reasoning.SOURCES): who, where, when, why, and
how far to trust it. Each map is built from data Buddy already has; the conversation gets the parts that matter
for the question (context_lines), and Moments-style nudges use them to speak at the right time.

1. Places and routines: where the user goes (home, work, named places) and when (usual weekday leave time),
   learned from the location history the app shares. A commute check before the usual leave time.
2. Situation: right now: at home / at work / travelling / driving / in a meeting / quiet hours. Non-urgent nudges
   wait while driving or in a meeting.
3. Source trust: how often each source turned out right (fact-checks that held, the user's corrections). When
   sources disagree, the more reliable one wins and Buddy says so.
4. Goals and priorities: the user's goals (general and money) and stated priorities, weighed in every decision.
5. Commitments: promises with a date ("I'll send it Monday", "call Amma Sunday") from conversation, emails and
   chats; a reminder the day before and on the day.
6. People: who they are, how to reach them, when the user last heard from them; a nudge when a close family
   member hasn't been heard from in three weeks.
7. Money flow: which account pays each bill (learned from past payments); a heads-up when an account is short of
   what will come out of it.
8. Skills and costs: for each outside service and model, how often it works and what it costs this month, so Buddy
   picks the reliable, cheap path and says when a paid one is worth it.
"""

import json
import logging
import re
import statistics
from datetime import date, datetime, timedelta, timezone

from .memory import Memory

log = logging.getLogger(__name__)

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS map_location_log (    -- where the phone was (when sharing is on); kept 60 days
    at TEXT NOT NULL, lat REAL NOT NULL, lng REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS map_location_log_at ON map_location_log (at);
CREATE TABLE IF NOT EXISTS map_places (
    name TEXT PRIMARY KEY,             -- home | work | gym | Amma's house ...
    lat REAL NOT NULL, lng REAL NOT NULL,
    radius_km REAL NOT NULL DEFAULT 0.3,
    learned INTEGER NOT NULL DEFAULT 1,   -- 1 = worked out from the history, 0 = the user named it
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS map_source_trust (
    source TEXT PRIMARY KEY, right INTEGER NOT NULL DEFAULT 0, wrong INTEGER NOT NULL DEFAULT 0, updated_at TEXT
);
CREATE TABLE IF NOT EXISTS map_commitments (
    id INTEGER PRIMARY KEY,
    what TEXT NOT NULL, who TEXT NOT NULL DEFAULT '', due TEXT NOT NULL,   -- YYYY-MM-DD
    source TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'open',   -- open | done
    created_at TEXT NOT NULL, UNIQUE (what, due)
);
"""

FAMILY = re.compile(r"(?i)\b(mother|mom|mum|amma|ammachi|father|dad|appa|achan|wife|husband|son|daughter|sister|"
                    r"brother|grandma|grandpa|parent)\b")
PROMISE = re.compile(r"(?i)\b(i'?ll|i will|i'm going to|i have to|i need to|i promised( to)?|i told \w+ i'?d)\s+"
                     r"(.{3,80}?)\s+(by|on|before|this|next|tomorrow|tonight)\b\s*([\w ,]{0,25})")
WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


def _km(a, b) -> float:
    from .transit import km  # noqa: PLC0415
    return km(a, b)


class Maps:
    def __init__(self, memory: Memory):
        self.memory = memory
        memory.db.executescript(SCHEMA_SQL)

    # 1. places and routines --------------------------------------------------------------------------------
    def log_location(self, lat: float, lng: float, at: datetime | None = None) -> None:
        at = at or datetime.now(timezone.utc)
        self.memory.db.execute("INSERT INTO map_location_log (at, lat, lng) VALUES (?, ?, ?)",
                               (at.isoformat(), round(lat, 5), round(lng, 5)))
        self.memory.db.execute("DELETE FROM map_location_log WHERE at < ?", ((at - timedelta(days=60)).isoformat(),))
        self.memory.db.commit()

    def _history(self, days: int = 60) -> list[tuple[datetime, float, float]]:
        from .tools import local_now  # noqa: PLC0415
        tz = local_now().tzinfo
        since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        return [(datetime.fromisoformat(r["at"]).astimezone(tz), r["lat"], r["lng"]) for r in self.memory.db.execute(
            "SELECT at, lat, lng FROM map_location_log WHERE at >= ? ORDER BY at", (since,))]

    @staticmethod
    def _cluster(points: list[tuple[float, float]]) -> tuple[float, float] | None:
        """The spot most points sit close to (within 300 m), as its average."""
        best, best_n = None, 0
        for p in points[:400]:
            near = [q for q in points if _km(p, q) <= 0.3]
            if len(near) > best_n:
                best, best_n = near, len(near)
        if not best or best_n < 3:
            return None
        return (statistics.fmean(q[0] for q in best), statistics.fmean(q[1] for q in best))

    def learn_places(self) -> dict:
        """Home: where the phone is at night. Work: where it is weekday working hours, away from home."""
        hist = self._history()
        found = {}
        night = [(la, ln) for t, la, ln in hist if t.hour < 6 or t.hour >= 22]
        home = self._cluster(night)
        if home:
            found["home"] = home
        day = [(la, ln) for t, la, ln in hist if t.weekday() < 5 and 10 <= t.hour < 16
               and (not home or _km((la, ln), home) > 1)]
        work = self._cluster(day)
        if work:
            found["work"] = work
        now = datetime.now(timezone.utc).isoformat()
        for name, (la, ln) in found.items():
            if not self.memory.db.execute("SELECT 1 FROM map_places WHERE name = ? AND learned = 0", (name,)).fetchone():
                self.memory.db.execute("INSERT OR REPLACE INTO map_places (name, lat, lng, learned, updated_at) "
                                       "VALUES (?, ?, ?, 1, ?)", (name, la, ln, now))
        self.memory.db.commit()
        return found

    def name_place(self, name: str, lat: float, lng: float, radius_km: float = 0.3) -> str:
        name = " ".join(str(name).split())[:40].lower()
        self.memory.db.execute("INSERT OR REPLACE INTO map_places (name, lat, lng, radius_km, learned, updated_at) "
                               "VALUES (?, ?, ?, ?, 0, ?)", (name, lat, lng, radius_km,
                                                             datetime.now(timezone.utc).isoformat()))
        self.memory.db.commit()
        return f"Saved this spot as {name}."

    def forget_location(self) -> str:
        n = self.memory.db.execute("DELETE FROM map_location_log").rowcount
        p = self.memory.db.execute("DELETE FROM map_places").rowcount
        self.memory.db.commit()
        self.memory.set("routine:week", "")  # the routine learned from it goes too
        self.memory.set("location:shops", "")  # and the shops it was seen in
        return f"Deleted the location history ({n} points) and {p} saved place{'s' if p != 1 else ''}."

    def places(self) -> list[dict]:
        return [dict(r) for r in self.memory.db.execute("SELECT * FROM map_places ORDER BY name")]

    def place_at(self, lat: float, lng: float) -> str:
        for p in self.places():
            if _km((lat, lng), (p["lat"], p["lng"])) <= max(p["radius_km"], 0.3):
                return p["name"]
        return ""

    def usual_leave(self) -> str | None:
        """Weekdays: the usual time the phone first leaves home in the morning (median, HH:MM)."""
        home = next((p for p in self.places() if p["name"] == "home"), None)
        if not home:
            return None
        by_day: dict[date, list] = {}
        for t, la, ln in self._history(30):
            if t.weekday() < 5 and 5 <= t.hour < 12:
                by_day.setdefault(t.date(), []).append((t, _km((la, ln), (home["lat"], home["lng"])) > 0.5))
        leaves = []
        for pts in by_day.values():
            pts.sort()
            for (t0, away0), (t1, away1) in zip(pts, pts[1:]):
                if not away0 and away1:
                    leaves.append(t1.hour * 60 + t1.minute)
                    break
        if len(leaves) < 3:
            return None
        m = int(statistics.median(leaves))
        return f"{m // 60:02d}:{m % 60:02d}"

    # 2. situation -----------------------------------------------------------------------------------------
    def situation(self, now: datetime | None = None) -> dict:
        from .tools import local_now  # noqa: PLC0415
        now = now or local_now()
        hist = self._history(1)
        out = {"place": "", "moving_kmh": None, "driving": False, "travelling": False, "in_meeting": "",
               "quiet": False}
        if hist:
            t, la, ln = hist[-1]
            out["place"] = self.place_at(la, ln)
            if len(hist) >= 2:
                t0, la0, ln0 = hist[-2]
                hours = (t - t0).total_seconds() / 3600
                if 0 < hours <= 0.5:
                    out["moving_kmh"] = round(_km((la0, ln0), (la, ln)) / hours)
                    out["driving"] = out["moving_kmh"] >= 25
            home = next((p for p in self.places() if p["name"] == "home"), None)
            out["travelling"] = bool(home and _km((la, ln), (home["lat"], home["lng"])) > 100)
        try:
            from .connectors.phone import in_quiet_hours  # noqa: PLC0415
            out["quiet"] = in_quiet_hours()
        except Exception:
            pass
        try:
            from .connect import Moments  # noqa: PLC0415
            for e in Moments(self.memory, geocode=lambda q: None)._events(now.date()):
                if e["start"] <= now.replace(tzinfo=None) <= (e["end"] or e["start"] + timedelta(hours=1)):
                    out["in_meeting"] = e["title"]
        except Exception:
            pass
        return out

    def hold_non_urgent(self, now: datetime | None = None) -> bool:
        s = self.situation(now)
        return bool(s["driving"] or s["in_meeting"])

    # 3. source trust ---------------------------------------------------------------------------------------
    def trust(self, source: str, right: bool) -> None:
        col = "right" if right else "wrong"
        self.memory.db.execute(
            f"INSERT INTO map_source_trust (source, {col}, updated_at) VALUES (?, 1, ?) ON CONFLICT (source) DO "
            f"UPDATE SET {col} = {col} + 1, updated_at = excluded.updated_at",
            (source[:40], datetime.now(timezone.utc).isoformat()))
        self.memory.db.commit()

    def trust_scores(self, min_seen: int = 5) -> dict[str, int]:
        return {r["source"]: round(100 * (r["right"] + 1) / (r["right"] + r["wrong"] + 2))
                for r in self.memory.db.execute("SELECT * FROM map_source_trust")
                if r["right"] + r["wrong"] >= min_seen}

    # 4. goals and priorities -------------------------------------------------------------------------------
    def goals(self) -> list[str]:
        out = []
        try:
            out += [r["text"] for r in self.memory.db.execute("SELECT text FROM goals WHERE done = 0 ORDER BY id")]
        except Exception:
            pass
        try:
            for r in self.memory.db.execute("SELECT kind, name, target, due, currency FROM fin_goals ORDER BY id"):
                out.append(f"{r['name']} ({r['kind'].replace('_', ' ')}" + (f", {r['target']:,.0f} {r['currency']}"
                                                                            if r["target"] else "")
                           + (f", by {r['due']}" if r["due"] else "") + ")")
        except Exception:
            pass
        try:
            from .brain import Brain  # noqa: PLC0415
            out += [f"priority: {p['text']}" for p in Brain(self.memory, embed=lambda t: None).preferences("priorit")]
        except Exception:
            pass
        return out[:10]

    # 5. commitments ----------------------------------------------------------------------------------------
    def add_commitment(self, what: str, due: str, who: str = "", source: str = "") -> bool:
        try:
            due = date.fromisoformat(due[:10]).isoformat()
        except ValueError:
            return False
        cur = self.memory.db.execute(
            "INSERT OR IGNORE INTO map_commitments (what, who, due, source, created_at) VALUES (?, ?, ?, ?, ?)",
            (" ".join(what.split())[:160], who[:60], due, source[:80], datetime.now(timezone.utc).isoformat()))
        self.memory.db.commit()
        return cur.rowcount > 0

    def commitments(self, days: int = 14) -> list[dict]:
        until = (date.today() + timedelta(days=days)).isoformat()
        return [dict(r) for r in self.memory.db.execute(
            "SELECT * FROM map_commitments WHERE status = 'open' AND due <= ? AND due >= ? ORDER BY due",
            (until, (date.today() - timedelta(days=2)).isoformat()))]

    def done_commitment(self, what: str) -> str:
        n = self.memory.db.execute("UPDATE map_commitments SET status = 'done' WHERE status = 'open' AND "
                                   "lower(what) LIKE ?", (f"%{what.lower().strip()}%",)).rowcount
        self.memory.db.commit()
        return "Marked done." if n else "No open promise like that."

    def promise_in(self, text: str, today: date | None = None) -> dict | None:
        """'I'll send Priya the deck by Friday' -> a commitment with a date, when the date is clear."""
        m = PROMISE.search(text)
        if not m:
            return None
        today = today or date.today()
        when = f"{m.group(4)} {m.group(5)}".strip().lower()
        due = None
        if "tomorrow" in when:
            due = today + timedelta(days=1)
        elif "tonight" in when or when.startswith("today"):
            due = today
        else:
            for i, wd in enumerate(WEEKDAYS):
                if wd in when:
                    ahead = (i - today.weekday()) % 7 or 7
                    due = today + timedelta(days=ahead + (7 if "next" in when and ahead < 7 else 0))
                    break
        if not due:
            return None
        return {"what": m.group(3).strip(" ,."), "due": due.isoformat()}

    # 6. people ---------------------------------------------------------------------------------------------
    def last_heard(self, name: str, days: int = 90) -> str | None:
        since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        key = name.split()[0].lower()
        if len(key) < 3:
            return None
        try:
            r = self.memory.db.execute(
                "SELECT MAX(start_at) AS at FROM records WHERE kind IN ('email', 'notification') AND start_at >= ? "
                "AND (lower(title) LIKE ? OR lower(data) LIKE ?)", (since, f"%{key}%", f"%{key}%")).fetchone()
        except Exception:
            return None
        return r["at"] if r and r["at"] else None

    def people_lines(self, question: str) -> list[str]:
        out = []
        try:
            rows = self.memory.db.execute("SELECT name, relation, notes FROM entities WHERE kind = 'person' "
                                          "ORDER BY updated_at DESC LIMIT 300").fetchall()
        except Exception:
            return []
        low = question.lower()
        for r in rows:
            if r["name"].lower() in low or (r["relation"] and r["relation"].lower() in low):
                last = self.last_heard(r["name"])
                out.append(f"- person: {r['name']}" + (f" ({r['relation']})" if r["relation"] else "") +
                           (f"; last heard from {last[:10]}" if last else "; not heard from in 90 days") +
                           (f"; {r['notes'][:160]}" if r["notes"] else ""))
        return out[:3]

    def quiet_family(self, days: int = 21) -> list[dict]:
        try:
            rows = self.memory.db.execute("SELECT name, relation FROM entities WHERE kind = 'person'").fetchall()
        except Exception:
            return []
        out = []
        for r in rows:
            if not FAMILY.search(f"{r['relation']} {r['name']}"):
                continue
            last = self.last_heard(r["name"], days=120)
            if last and datetime.fromisoformat(last) < datetime.now(timezone.utc) - timedelta(days=days):
                out.append({"name": r["name"], "relation": r["relation"], "last": last[:10]})
        return out

    # 7. money flow -----------------------------------------------------------------------------------------
    def paying_account(self, biller: str) -> str:
        """The account (last 4) that paid this biller most recently, from past transactions."""
        key = re.sub(r"[^a-z0-9 ]", "", biller.lower()).split()
        if not key:
            return ""
        try:
            r = self.memory.db.execute("SELECT card FROM transactions WHERE lower(merchant) LIKE ? AND card != '' "
                                       "ORDER BY day DESC LIMIT 1", (f"%{key[0]}%",)).fetchone()
        except Exception:
            return ""
        return r["card"] if r else ""

    def shortfalls(self, days: int = 7) -> list[dict]:
        """Accounts that will pay bills soon and hold less than those bills."""
        try:
            from .money import Money  # noqa: PLC0415
            from .plaid import Plaid  # noqa: PLC0415
            bills = Money(self.memory).open_bills(days)
            cash = {a["last4"]: a for a in Plaid(self.memory, env={}).accounts_overview()["groups"].get("cash", [])
                    if a.get("last4")}
        except Exception:
            return []
        by_acct: dict[str, list] = {}
        for b in bills:
            acct = self.paying_account(b["biller"])
            if acct in cash:
                by_acct.setdefault(acct, []).append(b)
        out = []
        for acct, bs in by_acct.items():
            need = round(sum(b["amount"] for b in bs), 2)
            have = cash[acct].get("balance") or 0
            if have < need:
                out.append({"account": f"{cash[acct]['name']} ••{acct}", "need": need, "have": have,
                            "bills": bs, "by": max(b["due_date"] for b in bs)})
        return out

    # 8. skills and costs -----------------------------------------------------------------------------------
    def skills(self, days: int = 30) -> list[dict]:
        since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        out = []
        try:
            for r in self.memory.db.execute(
                    "SELECT provider, COUNT(*) AS n, SUM(ok) AS ok, COALESCE(SUM(cost_usd), 0) AS cost FROM api_calls "
                    "WHERE at >= ? GROUP BY provider ORDER BY n DESC", (since,)):
                out.append({"service": r["provider"], "calls": r["n"], "works": round(100 * (r["ok"] or 0) / r["n"]),
                            "cost_usd": round(r["cost"], 2)})
        except Exception:
            pass
        return out

    # what the conversation gets ------------------------------------------------------------------------------
    def context_lines(self, question: str, sources: list[str] | None = None, decision: bool = False) -> list[str]:
        lines = []
        s = self.situation()
        now_bits = [b for b in ((f"at {s['place']}" if s["place"] else ""), ("driving" if s["driving"] else ""),
                                ("travelling" if s["travelling"] else ""),
                                (f"in '{s['in_meeting']}'" if s["in_meeting"] else "")) if b]
        if now_bits:
            lines.append("Right now the user is " + ", ".join(now_bits) + " (keep it short if driving).")
        trust = self.trust_scores()
        if trust and sources:
            relevant = {k: v for k, v in trust.items() if any(k.startswith(x) or x.startswith(k) for x in sources)}
            if relevant:
                lines.append("How often each source has been right: " +
                             ", ".join(f"{k} {v}%" for k, v in sorted(relevant.items(), key=lambda kv: -kv[1])) +
                             ". When they disagree, prefer the more reliable one and say so.")
        if decision:
            goals = self.goals()
            if goals:
                lines.append("The user's goals and priorities (weigh them): " + "; ".join(goals))
        lines += self.people_lines(question)
        if re.search(r"(?i)\b(promise|promised|owe|told .* i'?d|commit|follow up|send|call)\b", question):
            for c in self.commitments():
                lines.append(f"- promise: {c['what']}" + (f" (to {c['who']})" if c["who"] else "") + f", due {c['due']}")
        if re.search(r"(?i)\b(bill|pay|account|checking|savings|short|afford)\b", question):
            for f in self.shortfalls():
                lines.append(f"- money flow: {f['account']} pays ${f['need']:,.0f} in bills by {f['by']} and holds "
                             f"${f['have']:,.0f}")
        if re.search(r"(?i)\b(cost|costs|paid service|api|which service|reliable)\b", question):
            sk = self.skills()
            if sk:
                lines.append("Services this month: " + ", ".join(f"{x['service']} {x['calls']} calls, {x['works']}% "
                                                               f"worked, ${x['cost_usd']}" for x in sk[:8]))
        return lines

    # nudges --------------------------------------------------------------------------------------------------
    def nudges(self, now: datetime | None = None) -> list[dict]:
        """What these maps make worth saying now (each said once by the caller's key)."""
        from .tools import local_now  # noqa: PLC0415
        now = now or local_now().replace(tzinfo=None)
        out = []
        leave = self.usual_leave()
        from .commute import routine as commute_routine  # noqa: PLC0415
        if commute_routine(self.memory):
            leave = None  # the learned commute gives its own heads-up (commute.py): not twice
        if leave and now.weekday() < 5:
            h, m = map(int, leave.split(":"))
            at = now.replace(hour=h, minute=m, second=0, microsecond=0)
            if at - timedelta(minutes=30) <= now <= at:
                try:
                    from .transit import Transit  # noqa: PLC0415
                    bad = [x for x in Transit(self.memory).status()["lines"] if x.get("alerts") and not x.get("ok", True)]
                except Exception:
                    bad = []
                if bad:
                    x = bad[0]
                    out.append({"key": f"commute:{now.date()}", "urgent": True,
                                "text": f"Before you leave (you usually head out about {leave}): {x['line']}: "
                                        f"{x['alerts'][0]['text'][:200]}"})
        for c in self.commitments(1):
            day_label = "today" if c["due"] == now.date().isoformat() else "tomorrow"
            out.append({"key": f"promise:{c['id']}:{day_label}", "urgent": False,
                        "text": f"You said you'd {c['what']}" + (f" (for {c['who']})" if c["who"] else "") +
                                f", due {day_label}. Done? Tell me and I'll mark it."})
        if now.hour >= 10 and now.weekday() == 5:  # Saturdays: family you haven't heard from
            for p in self.quiet_family()[:2]:
                out.append({"key": f"family:{p['name']}:{now.isocalendar()[1] // 2}", "urgent": False,
                            "text": f"It's been a while since you heard from {p['name']} (last {p['last']}). "
                                    "Maybe a call this weekend?"})
        if now.hour >= 9:
            for f in self.shortfalls():
                names = ", ".join(f"{b['biller']} ${b['amount']:,.0f}" for b in f["bills"][:3])
                out.append({"key": f"flow:{f['account']}:{f['by']}:{int(f['need'])}", "urgent": False,
                            "text": f"{f['account']} pays {names} by {date.fromisoformat(f['by']):%b %-d} "
                                    f"(${f['need']:,.0f}) and holds ${f['have']:,.0f}. Move money in before then?"})
        return out


MAPS_TOOL = {
    "name": "buddy_maps",
    "description": "Buddy's maps of the user's world. places (home, work and named places, learned from location), "
    "name_place (name: call where the user is now 'gym', 'Amma's house'...), routine (usual weekday leave time), "
    "situation (now: place, driving, travelling, in a meeting), trust (how often each source was right), goals "
    "(goals and priorities), commitments (promises with dates), add_commitment (what, due YYYY-MM-DD, who?), "
    "done_commitment (what), people (close people not heard from lately), money_flow (accounts short of the bills "
    "they'll pay), skills (each outside service: calls, how often it worked, cost this month), forget_location (delete "
    "the location history and saved places; ask first).",
    "input_schema": {"type": "object", "properties": {
        "action": {"type": "string", "enum": ["places", "name_place", "routine", "situation", "trust", "goals",
                                              "commitments", "add_commitment", "done_commitment", "people",
                                              "money_flow", "skills", "forget_location"]},
        "name": {"type": "string"}, "what": {"type": "string"}, "due": {"type": "string"}, "who": {"type": "string"}},
        "required": ["action"], "additionalProperties": False},
}


def run_tool(memory: Memory, args: dict) -> str:
    m = Maps(memory)
    a = args.get("action")
    if a == "name_place":
        from .location import Location  # noqa: PLC0415
        here = Location(memory).current()
        if not here:
            return "I don't know where you are: turn on Settings -> Share my location in the app."
        return m.name_place(args.get("name") or "", here["lat"], here["lng"])
    if a == "add_commitment":
        ok = m.add_commitment(args.get("what") or "", args.get("due") or "", args.get("who") or "", "told Buddy")
        return "Noted: I'll remind you the day before and on the day." if ok else "Give the date as YYYY-MM-DD."
    if a == "done_commitment":
        return m.done_commitment(args.get("what") or "")
    if a == "forget_location":
        return m.forget_location()
    out = {"places": m.places, "routine": lambda: {"usual_weekday_leave": m.usual_leave()}, "situation": m.situation,
           "trust": m.trust_scores, "goals": m.goals, "commitments": m.commitments, "people": m.quiet_family,
           "money_flow": m.shortfalls, "skills": m.skills}.get(a)
    return json.dumps(out() if out else {"error": "unknown action"}, default=str, ensure_ascii=False)
