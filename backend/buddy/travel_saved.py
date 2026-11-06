"""Saved travel searches: a flight or hotel search the user keeps, re-run every morning, with an alert on a deal.

"Save & track" on a search result keeps its exact query (route and dates, or place and nights). Each morning Buddy
runs it again live (Duffel, Google Flights / Hotels, Travelpayouts), compares the cheapest TRUE TOTAL (bags, seat,
fees included, the way the user travels) with the price when it was saved and the lowest since, and messages when:
  - it's at or under the user's target price, or
  - it dropped at least 5% (and at least $15) below the price when saved, or
  - it's a new low since saving.
Each drop is said once (it has to fall another 3% to be said again). A search ends after its travel date.
Every re-check is one search (SerpApi is paid per search), so at most MAX_SAVED are tracked, once a day each.
Buddy never books: the alert links to the fresh results to book yourself.
"""

import json
import logging
from datetime import date, datetime, timedelta, timezone

from .memory import Memory
from .tools import ToolError

log = logging.getLogger(__name__)

MAX_SAVED = 10
DROP = 0.05          # 5% under the price when saved
MIN_DROP = 15.0      # and at least this much (in the search's currency)
AGAIN = 0.97         # told again only after a further 3% drop
CHECK_HOURS = 20

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS travel_saved (
    id INTEGER PRIMARY KEY,
    kind TEXT NOT NULL,               -- flights | hotels
    query TEXT NOT NULL,              -- the exact search (JSON)
    title TEXT NOT NULL,
    currency TEXT NOT NULL,
    target REAL,                      -- tell me at or under this
    baseline REAL NOT NULL,           -- cheapest true total when saved
    best REAL NOT NULL,               -- lowest seen since
    last REAL,                        -- the latest check's cheapest
    last_offer TEXT NOT NULL DEFAULT '',   -- "Delta · 08:05→14:30 · nonstop"
    alerted REAL,                     -- the price last told
    history TEXT NOT NULL DEFAULT '[]',    -- [[day, price], ...]
    search_id TEXT NOT NULL DEFAULT '',    -- the latest results, to open
    checked_at TEXT,
    created_at TEXT NOT NULL
);
"""


def cheapest(result: dict) -> dict | None:
    """The lowest true total among real offers (not Duffel test fares, not Travelpayouts' cached hints)."""
    offers = [o for o in (result.get("offers") or []) if not o.get("test") and o.get("true_total")
              and o.get("source") != "travelpayouts"]
    return min(offers, key=lambda o: o["true_total"]) if offers else None


def travel_day(kind: str, q: dict) -> str:
    return q.get("depart") if kind == "flights" else q.get("check_in") or ""


def _money(v: float, cur: str) -> str:
    return f"${v:,.0f}" if cur == "USD" else f"{v:,.0f} {cur}"


class SavedSearches:
    def __init__(self, memory: Memory, travel=None):
        from .travel import Travel  # noqa: PLC0415
        self.memory = memory
        self.travel = travel or Travel(memory)
        memory.db.executescript(SCHEMA_SQL)

    def saved(self) -> list[dict]:
        out = []
        for r in self.memory.db.execute("SELECT * FROM travel_saved ORDER BY id"):
            d = dict(r)
            d["query"], d["history"] = json.loads(d["query"]), json.loads(d["history"] or "[]")
            d["day"] = travel_day(d["kind"], d["query"])
            d["change_pct"] = round(100 * (d["last"] / d["baseline"] - 1), 1) if d["last"] and d["baseline"] else None
            out.append(d)
        return out

    def save(self, search_id: str, target: float | None = None) -> dict:
        s = self.travel.search(search_id)  # ToolError if it's gone
        if s["kind"] not in ("flights", "hotels"):
            raise ToolError("Only flight and hotel searches can be tracked.")
        q = s["query"]
        if travel_day(s["kind"], q) < date.today().isoformat():
            raise ToolError("That trip's date has passed.")
        low = cheapest(s["results"])
        if low is None:
            raise ToolError("That search found no prices to track: try other dates first.")
        same = self.memory.db.execute("SELECT id FROM travel_saved WHERE kind = ? AND query = ?",
                                      (s["kind"], json.dumps(q))).fetchone()
        if same is None and self.memory.db.execute("SELECT COUNT(*) FROM travel_saved").fetchone()[0] >= MAX_SAVED:
            raise ToolError(f"You're tracking {MAX_SAVED} searches already (each re-check costs a search): "
                            "remove one first.")
        target = float(target) if target not in (None, "", 0) else None
        cur = q.get("currency") or low.get("currency") or "USD"
        now = datetime.now(timezone.utc).isoformat()
        hist = json.dumps([[date.today().isoformat(), round(low["true_total"], 2)]])
        if same:
            self.memory.db.execute("UPDATE travel_saved SET target = ?, search_id = ? WHERE id = ?",
                                   (target, search_id, same["id"]))
            sid = same["id"]
        else:
            sid = self.memory.db.execute(
                "INSERT INTO travel_saved (kind, query, title, currency, target, baseline, best, last, last_offer, "
                "history, search_id, checked_at, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (s["kind"], json.dumps(q), s["title"], cur, target, low["true_total"], low["true_total"],
                 low["true_total"], self._label(low), hist, search_id, now, now)).lastrowid
        self.memory.db.commit()
        item = next(x for x in self.saved() if x["id"] == sid)
        goal = f" or when it's {_money(target, cur)} or less" if target else ""
        item["text"] = (f"Tracking {s['title']}: {_money(low['true_total'], cur)} now. I'll check it every morning and "
                        f"tell you when it drops{goal}.")
        return item

    def remove(self, saved_id: int) -> str:
        n = self.memory.db.execute("DELETE FROM travel_saved WHERE id = ?", (int(saved_id),)).rowcount
        self.memory.db.commit()
        if not n:
            raise ToolError("No such saved search.")
        return "Stopped tracking it."

    @staticmethod
    def _label(o: dict) -> str:
        return " · ".join(x for x in (o.get("airline") or o.get("name") or "", o.get("summary") or "") if x)[:120]

    async def _rerun(self, s: dict) -> dict:
        q = s["query"]
        if s["kind"] == "hotels":
            return await self.travel.hotels(q.get("place", ""), q["check_in"], q["check_out"], q.get("adults") or 2,
                                            check_reviews=False)
        return await self.travel.flights(q["origin"], q["destination"], q["depart"], q.get("return") or "",
                                         q.get("adults") or 1)

    async def check(self, saved_id: int | None = None, force: bool = False) -> list[dict]:
        """Re-run each saved search that's due (once a day), keep the price, and return the deals to tell."""
        alerts = []
        today = date.today().isoformat()
        due_before = (datetime.now(timezone.utc) - timedelta(hours=CHECK_HOURS)).isoformat()
        for s in self.saved():
            if saved_id is not None and s["id"] != saved_id:
                continue
            if s["day"] < today:  # the trip's date has passed
                self.memory.db.execute("DELETE FROM travel_saved WHERE id = ?", (s["id"],))
                continue
            if not force and s["checked_at"] and s["checked_at"] > due_before:
                continue
            try:
                result = await self._rerun(s)
            except Exception as e:
                log.warning("saved search %s failed: %s", s["id"], e)
                continue
            now = datetime.now(timezone.utc).isoformat()
            low = cheapest(result)
            if low is None:
                self.memory.db.execute("UPDATE travel_saved SET checked_at = ? WHERE id = ?", (now, s["id"]))
                continue
            price, cur = round(low["true_total"], 2), s["currency"]
            hist = [h for h in s["history"] if h[0] != today][-59:] + [[today, price]]
            reasons = []
            if s["target"] and price <= s["target"]:
                reasons.append(f"at or under your {_money(s['target'], cur)} target")
            if price <= s["baseline"] * (1 - DROP) and s["baseline"] - price >= MIN_DROP:
                reasons.append(f"down {_money(s['baseline'] - price, cur)} "
                               f"({round(100 * (1 - price / s['baseline']))}%) since you saved it")
            if price <= s["best"] * AGAIN and s["best"] - price >= MIN_DROP:  # a real new low, not a $5 wobble
                reasons.append(f"the lowest since you saved it (was {_money(s['best'], cur)})")
            told_before = s["alerted"] is not None and price >= s["alerted"] * AGAIN
            if reasons and not told_before:
                alerts.append({"saved": s["id"], "price": price, "search_id": result.get("search_id", ""),
                               "text": f"Deal on your saved search {s['title']}: {_money(price, cur)} "
                                       f"({self._label(low)}), {'; '.join(reasons)}. Open Travel → Saved searches to "
                                       "see it and book. Prices can change fast."})
            self.memory.db.execute(
                "UPDATE travel_saved SET last = ?, best = MIN(best, ?), last_offer = ?, history = ?, search_id = ?, "
                "checked_at = ? WHERE id = ?",
                (price, price, self._label(low), json.dumps(hist), result.get("search_id") or s["search_id"], now,
                 s["id"]))
        self.memory.db.commit()
        return alerts

    def told(self, alert: dict) -> None:
        self.memory.db.execute("UPDATE travel_saved SET alerted = ? WHERE id = ?", (alert["price"], alert["saved"]))
        self.memory.db.commit()
