"""Credit score and credit history watch: the score over time, and an alert for anything new on the report.

Where the facts come from (there is no consumer API to the credit bureaus, so Buddy reads what the free
monitoring services already send the user, and adds what it can see itself):
  - credit monitoring emails and phone notifications (the Credit Karma app's "your score changed" or "new
    inquiry" pushes, read by the phone's notification access): a score update or an alert from Experian, TransUnion, Equifax, Credit Karma,
    Chase Credit Journey, Amex MyCredit Guide, Capital One CreditWise, Discover Scorecard, myFICO and similar.
    Each one found in the synced email (CREDIT_EMAIL) is read once by Claude, which returns the score (bureau,
    model, date) and any events: hard inquiries, new accounts, late payments, collections, public records,
    address or name changes, closed accounts, limit changes, a fraud alert or freeze.
  - the user telling Buddy ("my Experian FICO is 782") or a screenshot of their credit app (add)
  - the connected cards (Plaid): a credit card that appears at a bank already connected is a new account; how
    much of the limits is used (finance.credit_use), the biggest thing they control
Alerts (proactive loop): a new hard inquiry, new account, late payment, collection, public record, address
change or fraud alert, each once, asking "was this you?", with the official freeze and dispute links if not;
and a score drop of DROP_ALERT points or more. Buddy never freezes, disputes or applies for anything itself.
Kept: fin_credit_scores, fin_credit_events (the email's record id, never the whole email).
"""

import json
import logging
import re
from datetime import date, datetime, timezone

from .memory import Memory
from .tools import ToolError, local_now

log = logging.getLogger(__name__)

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS fin_credit_scores (
    id INTEGER PRIMARY KEY,
    day TEXT NOT NULL,                  -- the date the score is as of
    score INTEGER NOT NULL,
    bureau TEXT NOT NULL DEFAULT '',    -- Experian | TransUnion | Equifax | '' (not said)
    model TEXT NOT NULL DEFAULT '',     -- FICO 8 | VantageScore 3.0 | ...
    source TEXT NOT NULL DEFAULT '',    -- "Chase Credit Journey email", "you told me"
    record_id INTEGER,
    created_at TEXT NOT NULL,
    UNIQUE (day, bureau, model, score)
);
CREATE TABLE IF NOT EXISTS fin_credit_events (
    id INTEGER PRIMARY KEY,
    day TEXT NOT NULL,
    kind TEXT NOT NULL,                 -- see EVENT_KINDS
    lender TEXT NOT NULL DEFAULT '',
    bureau TEXT NOT NULL DEFAULT '',
    detail TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT '',
    record_id INTEGER,
    status TEXT NOT NULL DEFAULT 'new', -- new | told | mine (the user said it was them) | not_mine
    created_at TEXT NOT NULL,
    UNIQUE (day, kind, lender, bureau)
);
"""
EVENT_KINDS = {
    "hard_inquiry": "hard inquiry", "new_account": "new account", "late_payment": "late payment",
    "collection": "collection", "public_record": "public record", "address_change": "address change",
    "name_change": "name change", "closed_account": "account closed", "limit_change": "credit limit change",
    "fraud_alert": "fraud alert or freeze", "balance_change": "big balance change", "other": "change",
}
ALERT_KINDS = {"hard_inquiry", "new_account", "late_payment", "collection", "public_record", "address_change",
               "name_change", "fraud_alert"}
DROP_ALERT = 20
CREDIT_EMAIL = re.compile(
    r"(?i)(experian|transunion|equifax|credit ?karma|myfico|\bfico\b|creditwise|credit journey|mycredit guide|"
    r"credit sesame|scorecard|credit score|credit report|hard inquiry|credit inquiry|new inquiry|credit alert|"
    r"credit monitoring|new account (?:was )?opened|reported to the credit)")
FREEZE = ("Freeze your credit for free at all three bureaus: experian.com/freeze, transunion.com/credit-freeze, "
          "equifax.com/personal/credit-report-services/credit-freeze. Your free reports: annualcreditreport.com; "
          "disputes go to the bureau that shows it.")
EXTRACT_PROMPT = """This email or phone notification reached {user}. If it is from a credit monitoring service or a credit bureau (a credit
score update or an alert about their credit report), report what it says; a marketing email that only advertises
a card or a loan is not one. Report only what the email states, never a guess.
Email subject: {subject}
From: {sender}
Received: {received}
Text:
{body}
Answer with one line of JSON only:
{{"credit_email": true, "score": 0 or null, "bureau": "Experian|TransUnion|Equifax|" , "model": "e.g. FICO 8 or
VantageScore 3.0, or empty", "as_of": "YYYY-MM-DD or empty", "service": "e.g. Chase Credit Journey",
"events": [{{"kind": "hard_inquiry|new_account|late_payment|collection|public_record|address_change|name_change|
closed_account|limit_change|fraud_alert|balance_change|other", "day": "YYYY-MM-DD or empty", "lender": "who",
"detail": "a few words"}}]}}"""


async def _ask_claude(memory: Memory, prompt: str) -> dict | None:
    from . import usage
    from .agent import fallback_kwargs
    from .benefits import _last_json
    from .config import claude_client, settings

    model = settings.fast_model
    import time
    started = time.monotonic()
    response = await claude_client().beta.messages.create(model=model, max_tokens=1_000,
                                                          messages=[{"role": "user", "content": prompt}],
                                                          **fallback_kwargs(model))
    if hasattr(response, "usage"):
        usage.record(memory, response.model, response.usage, purpose="credit_email", response=response,
                     started=started)
    return _last_json("\n".join(b.text for b in response.content if getattr(b, "type", "") == "text"))


def _day(v, fallback: str) -> str:
    try:
        return date.fromisoformat(str(v or "")[:10]).isoformat()
    except ValueError:
        return fallback


class Credit:
    def __init__(self, memory: Memory, ask=None):
        self.memory = memory
        self.ask = ask or _ask_claude
        memory.db.executescript(SCHEMA_SQL)

    # --- what's known -------------------------------------------------------------------------
    def add_score(self, score, bureau: str = "", model: str = "", day: str = "", source: str = "you told me",
                  record_id: int | None = None) -> str:
        try:
            score = int(score)
        except (TypeError, ValueError):
            raise ToolError("What's the score? A number from 300 to 850.") from None
        if not 300 <= score <= 900:
            raise ToolError("A credit score is between 300 and 850 (some models go to 900).")
        bureau = next((b for b in ("Experian", "TransUnion", "Equifax") if b.lower() in str(bureau).lower()), "")
        today = local_now().date().isoformat()
        prev = self.latest(bureau)
        self.memory.db.execute(
            "INSERT OR IGNORE INTO fin_credit_scores (day, score, bureau, model, source, record_id, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)", (_day(day, today), score, bureau, str(model or "")[:40],
                                             str(source)[:60], record_id, datetime.now(timezone.utc).isoformat()))
        self.memory.db.commit()
        change = f" ({score - prev['score']:+d} since {prev['day']})" if prev and prev["day"] <= _day(day, today) else ""
        return f"Saved: {score}{' on ' + bureau if bureau else ''}{' (' + model + ')' if model else ''}{change}."

    def add_event(self, kind: str, day: str = "", lender: str = "", bureau: str = "", detail: str = "",
                  source: str = "", record_id: int | None = None) -> bool:
        kind = kind if kind in EVENT_KINDS else "other"
        bureau = next((b for b in ("Experian", "TransUnion", "Equifax") if b.lower() in str(bureau).lower()), "")
        cur = self.memory.db.execute(
            "INSERT OR IGNORE INTO fin_credit_events (day, kind, lender, bureau, detail, source, record_id, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (_day(day, local_now().date().isoformat()), kind, " ".join(str(lender or "").split())[:60], bureau,
             str(detail or "")[:160], str(source)[:60], record_id, datetime.now(timezone.utc).isoformat()))
        self.memory.db.commit()
        return bool(cur.rowcount)

    def latest(self, bureau: str = "") -> dict | None:
        sql, args = "SELECT * FROM fin_credit_scores", []
        if bureau:
            sql, args = sql + " WHERE bureau = ?", [bureau]
        row = self.memory.db.execute(sql + " ORDER BY day DESC, id DESC LIMIT 1", args).fetchone()
        return dict(row) if row else None

    def scores(self) -> list[dict]:
        return [dict(r) for r in self.memory.db.execute("SELECT day, score, bureau, model, source FROM fin_credit_scores "
                                                        "ORDER BY day")]

    def events(self, limit: int = 30) -> list[dict]:
        return [dict(r) | {"label": EVENT_KINDS.get(r["kind"], r["kind"])} for r in self.memory.db.execute(
            "SELECT id, day, kind, lender, bureau, detail, source, status FROM fin_credit_events ORDER BY day DESC, "
            "id DESC LIMIT ?", (limit,))]

    def mark(self, event_id, status: str) -> str:
        if status not in ("mine", "not_mine"):
            raise ToolError("Say whether it was them (mine) or not (not_mine).")
        row = self.memory.db.execute("SELECT * FROM fin_credit_events WHERE id = ?", (event_id,)).fetchone()
        if not row:
            raise ToolError("No credit event like that.")
        self.memory.db.execute("UPDATE fin_credit_events SET status = ? WHERE id = ?", (status, event_id))
        self.memory.db.commit()
        what = f"the {EVENT_KINDS.get(row['kind'], row['kind'])}{' from ' + row['lender'] if row['lender'] else ''}"
        if status == "mine":
            return f"Noted: {what} was you."
        return (f"Noted: {what} wasn't you. Act today: {FREEZE} Also call {row['lender'] or 'the lender'} to report "
                "the application as fraud, and file at identitytheft.gov. I can draft the dispute letter.")

    # --- reading the monitoring emails ---------------------------------------------------------
    async def scan_emails(self, limit: int = 10) -> list[str]:
        """Credit emails synced since the last look: read once each. Returns what to tell the user."""
        last = int(self.memory.get("credit:last_record") or 0)
        try:
            rows = [dict(r) for r in self.memory.db.execute(
                "SELECT id, kind, title, body, data, start_at FROM records WHERE kind IN ('email', 'notification') AND id > ? "
                "ORDER BY id",
                (last,))]
        except Exception:
            return []  # no email connected yet
        todo = []
        for r in rows:
            meta = json.loads(r["data"] or "{}")
            sender = meta.get("from") or meta.get("app_name") or meta.get("app") or ""
            if CREDIT_EMAIL.search(f"{sender} {r['title']}") or CREDIT_EMAIL.search((r["body"] or "")[:400]):
                todo.append((r, sender))
        done_to = last
        for r, sender in todo[:limit]:
            try:
                got = await self.ask(self.memory, EXTRACT_PROMPT.format(
                    user="the user", subject=r["title"][:200], sender=sender[:120], received=(r["start_at"] or "")[:10],
                    body=(r["body"] or "")[:4000]))
            except Exception:
                log.exception("credit email read failed")
                break  # try again next time from here
            self._save_email(r, got or {})
            done_to = r["id"]
        if len(todo) <= limit:
            done_to = max([done_to] + [r["id"] for r in rows])
        self.memory.set("credit:last_record", str(done_to))
        return []

    def _save_email(self, r: dict, got: dict) -> None:
        if not got.get("credit_email"):
            return
        received = _day(r.get("start_at"), local_now().date().isoformat())
        source = (str(got.get("service") or "") or "credit monitoring")[:40] + \
            (" notification" if r.get("kind") == "notification" else " email")
        if got.get("score"):
            try:
                self.add_score(got["score"], got.get("bureau") or "", got.get("model") or "",
                               _day(got.get("as_of"), received), source, r["id"])
            except ToolError:
                pass
        for e in (got.get("events") or [])[:10]:
            if isinstance(e, dict):
                self.add_event(str(e.get("kind") or "other"), _day(e.get("day"), received), e.get("lender") or "",
                               got.get("bureau") or "", e.get("detail") or "", source, r["id"])

    # --- what Buddy sees itself ---------------------------------------------------------------
    def watch_connected_cards(self) -> None:
        """A credit card that shows up at a bank already connected is a new account (the first look only learns)."""
        try:
            rows = self.memory.db.execute(
                "SELECT a.account_id, a.name, a.mask, i.institution, i.added_at FROM plaid_accounts a JOIN plaid_items i "
                "ON i.item_id = a.item_id WHERE a.type = 'credit' AND COALESCE(i.env, 'production') != 'sandbox'"
            ).fetchall()
        except Exception:
            return
        seen = set(json.loads(self.memory.get("credit:seen_cards") or "[]"))
        first = not self.memory.get("credit:seen_cards")
        for r in rows:
            if r["account_id"] in seen:
                continue
            seen.add(r["account_id"])
            joined = str(r["added_at"] or "")[:10]
            if not first and joined and joined < local_now().date().isoformat():
                self.add_event("new_account", "", f"{r['institution']} {r['name']}".strip(), "",
                               f"a new card ••{r['mask']} appeared at a bank you'd connected", "your bank connection")
        self.memory.set("credit:seen_cards", json.dumps(sorted(seen)))

    # --- alerts -------------------------------------------------------------------------------
    def alerts(self) -> list[dict]:
        out = []
        for e in self.memory.db.execute("SELECT * FROM fin_credit_events WHERE status = 'new' ORDER BY day").fetchall():
            if e["kind"] not in ALERT_KINDS:
                self.memory.db.execute("UPDATE fin_credit_events SET status = 'told' WHERE id = ?", (e["id"],))
                continue
            what = EVENT_KINDS[e["kind"]]
            who = f" from {e['lender']}" if e["lender"] else ""
            where = f" on your {e['bureau']} report" if e["bureau"] else " on your credit report"
            spoken = f"Heads up: a new {what}{who}{where}, dated {_nice(e['day'])}. Was this you?"
            shown = [f"**New {what}{who}**", f"- {where.strip().capitalize()}, {_nice(e['day'])}"
                     + (f": {e['detail']}" if e["detail"] else ""), f"- Seen in: {e['source'] or 'your credit report'}",
                     "If it was you, say \"that was me\". If not: " + FREEZE]
            out.append({"text": spoken + " || " + "\n".join(shown), "event": e["id"]})
        self.memory.db.commit()
        out += self._score_drop()
        return out

    def _score_drop(self) -> list[dict]:
        out = []
        for bureau in {r["bureau"] for r in self.memory.db.execute("SELECT DISTINCT bureau FROM fin_credit_scores")}:
            rows = self.memory.db.execute("SELECT * FROM fin_credit_scores WHERE bureau = ? ORDER BY day DESC, id DESC "
                                          "LIMIT 2", (bureau,)).fetchall()
            if len(rows) < 2 or rows[1]["score"] - rows[0]["score"] < DROP_ALERT:
                continue
            mark = f"credit:drop:{rows[0]['id']}"
            if self.memory.get(mark):
                continue
            drop = rows[1]["score"] - rows[0]["score"]
            out.append({"text": f"Your {bureau + ' ' if bureau else ''}credit score dropped {drop} points to "
                                f"{rows[0]['score']} ({_nice(rows[0]['day'])}). Common reasons: a card balance reported "
                                "high, a new inquiry or account, or a late payment. Want me to look at what changed?",
                        "mark": mark})
        return out

    def told(self, alert: dict) -> None:
        if alert.get("event"):
            self.memory.db.execute("UPDATE fin_credit_events SET status = 'told' WHERE id = ? AND status = 'new'",
                                   (alert["event"],))
            self.memory.db.commit()
        if alert.get("mark"):
            self.memory.set(alert["mark"], local_now().date().isoformat())

    # --- the money dashboard ------------------------------------------------------------------
    def dashboard(self) -> dict:
        from .finance import Finance  # noqa: PLC0415
        use = []
        try:
            use = Finance(self.memory).credit_use()
        except Exception:
            pass
        owed = sum(c["balance_now"] for c in use)
        limits = sum(c["credit_limit"] for c in use)
        latest = {}
        for s in self.scores():
            latest[s["bureau"] or "Score"] = s
        return {"latest": sorted(latest.values(), key=lambda s: s["day"], reverse=True),
                "history": self.scores()[-24:], "events": self.events(12),
                "utilization": round(100 * owed / limits) if limits else None,
                "cards": [{"name": c["name"], "used": c["used_now"]} for c in use],
                "how": "Scores and alerts come from your credit monitoring emails (Chase Credit Journey, Amex MyCredit "
                       "Guide, Credit Karma, Experian...) or what you tell Buddy."}


def _nice(day: str) -> str:
    try:
        d = date.fromisoformat(day)
        return f"{d:%b} {d.day}, {d.year}"
    except ValueError:
        return day


CREDIT_TOOL = {
    "name": "credit",
    "description": "The user's credit score and credit report changes. overview: latest score per bureau, history, "
    "recent events (hard inquiries, new accounts, late payments...), card utilization. add_score (score, bureau, "
    "model, day): when they tell you a score or show their credit app. it_was_me / not_me (event_id): their answer "
    "to a 'was this you?' alert; not_me gives the freeze and dispute steps. forget: delete every score and event "
    "Buddy kept (only when they ask). Scores come from their free credit "
    "monitoring emails (Chase Credit Journey, Amex MyCredit Guide, Credit Karma, Experian, CreditWise...): if none "
    "has arrived, suggest turning on one they already have. You never freeze, dispute or apply for anything.",
    "input_schema": {"type": "object", "properties": {
        "action": {"type": "string", "enum": ["overview", "add_score", "it_was_me", "not_me", "forget"]},
        "score": {"type": "integer"}, "bureau": {"type": "string"}, "model": {"type": "string"},
        "day": {"type": "string"}, "event_id": {"type": "integer"}},
        "required": ["action"], "additionalProperties": False},
}


def run_tool(memory: Memory, args: dict) -> str:
    c = Credit(memory)
    a = args.get("action")
    if a == "add_score":
        return c.add_score(args.get("score"), args.get("bureau") or "", args.get("model") or "", args.get("day") or "")
    if a in ("it_was_me", "not_me"):
        return c.mark(args.get("event_id"), "mine" if a == "it_was_me" else "not_mine")
    if a == "forget":
        n = memory.db.execute("DELETE FROM fin_credit_scores").rowcount + memory.db.execute(
            "DELETE FROM fin_credit_events").rowcount
        memory.db.commit()
        return f"Deleted {n} saved credit score{'s' if n != 1 else ''} and events. New ones are kept as they arrive."
    return json.dumps(c.dashboard(), ensure_ascii=False, default=str)
