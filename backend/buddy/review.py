"""The money review: a detailed report of where the money went, what changed, and how to save.

  "Give me a full money report" / "how can I save money?" -> money_advisor review (period: last_month,
  this_month, last_3_months, last_6_months, this_year, last_12_months). Saved in Reports; Buddy says the
  headline and the top savings.

Every number is worked out here from the user's own data (Plaid transactions and income, cards, subscriptions,
reward rates, card credits); nothing leaves the server to make it, and Claude only words the result. Amounts
are in USD (other currencies are counted separately and mentioned). Suggestions only: Buddy never moves money.

Sections: headline (in, out, left, savings rate) · needs / wants / fees split · categories against your usual,
with the bank's finer labels and top shops · six-month trend · biggest and unusual charges · subscriptions ·
fees and interest · cards · rewards missed and card credits running out · savings opportunities, each with a
yearly dollar amount · a suggested monthly budget · the few things to do first.
"""

import json
import logging
import re
import statistics
import time
from datetime import date, datetime, timedelta, timezone

from . import usage
from .advisor import DISCLAIMER, Advisor
from .config import claude_client, settings
from .benefits import Benefits
from .finance import INTEREST, Finance, merchant_key, purge_reports
from .memory import Memory
from .tools import ToolError, local_now

log = logging.getLogger(__name__)
client = claude_client()

PERIODS = ("last_month", "this_month", "last_3_months", "last_6_months", "this_year", "last_12_months")
NEEDS = ("housing", "groceries", "bills", "loans", "health", "transport")
HOUSE = ("housing", "bills")  # rent or mortgage, home improvement, utilities
DELIVERY = re.compile(r"(?i)\b(doordash|uber ?eats|grubhub|postmates|instacart|caviar|seamless|gopuff)\b")
WANTS = ("food", "shopping", "entertainment", "travel", "subscriptions", "cash", "other")
LABELS = {"housing": "Housing (rent, mortgage, home)", "loans": "Loan payments", "food": "Eating out", "groceries": "Groceries", "transport": "Transport", "travel": "Travel",
          "shopping": "Shopping", "bills": "Bills and utilities", "health": "Health and personal care",
          "entertainment": "Entertainment", "subscriptions": "Subscriptions", "cash": "Cash",
          "fees": "Fees and interest", "other": "Other"}
DAYS_PER_MONTH = 30.44
MIN_OPPORTUNITY = 60          # dollars a year: smaller savings aren't worth a line
DINING_SHARE = 0.10           # eating out above this share of income (or $300 a month) gets a suggestion
LABEL_SQL = """
CREATE TABLE IF NOT EXISTS fin_transfer_labels (
    who TEXT PRIMARY KEY,             -- the recipient or service, lower case: "neha sharma", "remitly"
    label TEXT NOT NULL,              -- rent | family_support | savings | anything the user says
    note TEXT NOT NULL DEFAULT ''     -- "parents' maintenance"
);
"""
LABEL_NAMES = {"rent": "Rent", "family_support": "Family support", "savings": "Savings"}
CANCELLABLE = ("subscriptions", "entertainment")  # recurring charges you could cancel: services, not shops


def _months_back(d: date, n: int) -> date:
    m = d.month - 1 - n
    return date(d.year + m // 12, m % 12 + 1, 1)


def calendar_months(period: str) -> int | None:
    """Whole calendar months in a period (None for one still running)."""
    return {"last_month": 1, "last_3_months": 3, "last_6_months": 6, "last_12_months": 12}.get(period)


def period_bounds(period: str, today: date) -> tuple[date, date, str]:
    first = today.replace(day=1)
    if period == "this_month":
        return first, today, f"{today:%B %Y} so far"
    if period == "last_month":
        end = first - timedelta(days=1)
        return end.replace(day=1), end, f"{end:%B %Y}"
    if period in ("last_3_months", "last_6_months", "last_12_months"):
        n = int(period.split("_")[1])
        start = _months_back(first, n)
        return start, first - timedelta(days=1), f"{start:%b %Y} to {(first - timedelta(days=1)):%b %Y}"
    if period == "this_year":
        return date(today.year, 1, 1), today, f"{today.year} so far"
    raise ToolError(f"Pick a period: {', '.join(PERIODS)}.")


def _money(v: float) -> str:
    return f"${v:,.0f}" if abs(v) >= 100 else f"${v:,.2f}"


class Review:
    def __init__(self, memory: Memory):
        self.memory = memory
        self.fin = Finance(memory)
        self.adv = Advisor(memory)
        memory.db.executescript(LABEL_SQL)

    # --- transfer labels ("the Zelle to Neha is rent", "Remitly is my parents' maintenance") ----------------------
    def labels(self) -> dict[str, dict]:
        return {r["who"]: dict(r) for r in self.memory.db.execute("SELECT * FROM fin_transfer_labels")}

    def label_transfer(self, who: str, label: str, note: str = "") -> str:
        who, label = (who or "").strip().lower(), (label or "").strip().lower().replace(" ", "_")
        if not who or not label:
            raise ToolError("Say who the transfers go to and what they are, e.g. rent or family_support.")
        try:
            known = [r["recipient"] for r in self.memory.db.execute("SELECT DISTINCT recipient FROM fin_transfers")]
        except Exception:
            known = []
        if who in ("remit", "remits", "remittance", "remittances", "money sent abroad", "sent abroad", "abroad"):
            match, key = "all remittances", "*abroad*"  # every service: Remitly, Wise, Western Union...
        else:
            match = next((k for k in known if k.lower() == who), None) or next(
                (k for k in known if who in k.lower()), None)
            key = (match or who).lower()
        self.memory.db.execute("INSERT OR REPLACE INTO fin_transfer_labels (who, label, note) VALUES (?, ?, ?)",
                               (key, label, note.strip()[:80]))
        self.memory.db.commit()
        shown = LABEL_NAMES.get(label, label.replace("_", " "))
        how = {"rent": "counted as housing from now on, in your spending and house expenses",
               "family_support": "shown as family support, never as waste or something to cut"}.get(
            label, "shown with that label in your reviews")
        return f"Transfers to {match or who.title()} are {shown}" + (f" ({note.strip()})" if note.strip() else "") + \
            f": {how}."

    def _label_for(self, recipient: str, name: str, labels: dict, kind: str = "") -> dict | None:
        rec, raw = recipient.lower(), name.lower()
        return labels.get(rec) or next((v for k, v in labels.items() if not k.startswith("*") and (k in rec or k in raw)),
                                       None) or (labels.get("*abroad*") if kind == "abroad" else None)

    def _rent_rows(self, start: date, end: date) -> list[dict]:
        """Transfers the user said are rent, as housing spending rows (so they count everywhere rent should)."""
        labels = {k: v for k, v in self.labels().items() if v["label"] == "rent"}
        if not labels:
            return []
        try:
            rows = self.memory.db.execute(
                "SELECT day, amount, currency, name, recipient, account, kind FROM fin_transfers WHERE day BETWEEN ? AND ?",
                (start.isoformat(), end.isoformat())).fetchall()
        except Exception:
            return []
        out = []
        for r in rows:
            lab = self._label_for(r["recipient"], r["name"], labels, r["kind"])
            if lab:
                note = f" ({lab['note']})" if lab.get("note") else ""
                out.append({"day": r["day"], "amount": r["amount"], "currency": r["currency"],
                            "merchant": f"Rent to {r['recipient']}{note}", "category": "housing", "card": r["account"],
                            "detail": "Rent"})
        return out

    # --- the numbers --------------------------------------------------------------------------------
    def _rows(self, start: date, end: date) -> list[dict]:
        return [dict(r) for r in self.memory.db.execute(
            "SELECT day, amount, currency, merchant, category, card, COALESCE(detail, '') AS detail FROM transactions "
            "WHERE day BETWEEN ? AND ? AND amount > 0", (start.isoformat(), end.isoformat()))] + self._rent_rows(start, end)

    def _income(self, start: date, end: date) -> float | None:
        try:
            got = self.memory.db.execute(
                "SELECT COALESCE(SUM(amount), 0), COUNT(*) FROM fin_income WHERE day BETWEEN ? AND ? AND currency = 'USD'",
                (start.isoformat(), end.isoformat())).fetchone()
        except Exception:  # no bank linked yet
            got = (0, 0)
        if got[1]:
            return round(got[0], 2)
        told = self.adv.income()  # what the user told Buddy, per month
        if told.get("amount") and told.get("currency", "USD") == "USD":
            return round(told["amount"] * ((end - start).days + 1) / DAYS_PER_MONTH, 2)
        return None

    @staticmethod
    def _is_fee(r: dict) -> bool:
        return r["category"] == "fees" or bool(INTEREST.search(r["merchant"]))

    def compute(self, period: str = "last_month", today: date | None = None) -> dict:
        today = today or local_now().date()
        start, end, label = period_bounds(period, today)
        days = (end - start).days + 1
        months = calendar_months(period) or days / DAYS_PER_MONTH
        rows = self._rows(start, end)
        usd = [r for r in rows if r["currency"] == "USD"]
        other_currencies = sorted({r["currency"] for r in rows} - {"USD"})
        # baseline: the whole calendar months just before (as many as the period, at least 3), from the first
        # month with any data, so a short history isn't diluted by empty months
        n_base = max(3, calendar_months(period) or round(months))
        base_start = _months_back(start.replace(day=1), n_base)
        base = [r for r in self._rows(base_start, start.replace(day=1) - timedelta(days=1)) if r["currency"] == "USD"]
        base_first = min((r["day"] for r in base), default=None)
        if base_first:
            f = date.fromisoformat(base_first)
            base_months = (start.year - f.year) * 12 + start.month - f.month
        else:
            base_months = 0

        spent = round(sum(r["amount"] for r in usd), 2)
        income = self._income(start, end)
        left = round(income - spent, 2) if income is not None else None
        rate = round(100 * left / income, 1) if income else None

        fees = [r for r in usd if self._is_fee(r)]
        cats: dict[str, dict] = {}
        for r in usd:
            cat = "fees" if self._is_fee(r) else r["category"]
            c = cats.setdefault(cat, {"total": 0.0, "count": 0, "details": {}, "merchants": {}})
            c["total"] += r["amount"]
            c["count"] += 1
            if r["detail"]:
                c["details"][r["detail"]] = c["details"].get(r["detail"], 0) + r["amount"]
            c["merchants"][r["merchant"]] = c["merchants"].get(r["merchant"], 0) + r["amount"]
        base_by_cat: dict[str, float] = {}
        for r in base:
            cat = "fees" if self._is_fee(r) else r["category"]
            base_by_cat[cat] = base_by_cat.get(cat, 0) + r["amount"]
        categories = []
        for cat, c in sorted(cats.items(), key=lambda kv: -kv[1]["total"]):
            per_month = c["total"] / months
            usual = base_by_cat.get(cat, 0) / base_months if base_months >= 1 else None
            categories.append({
                "category": cat, "label": LABELS.get(cat, cat.title()), "total": round(c["total"], 2),
                "per_month": round(per_month, 2), "share": round(100 * c["total"] / spent, 1) if spent else 0,
                "count": c["count"], "usual_per_month": round(usual, 2) if usual is not None else None,
                "change": round(100 * (per_month / usual - 1)) if usual else None,
                "group": "needs" if cat in NEEDS else "fees" if cat == "fees" else "wants",
                "top_details": sorted(((k, round(v, 2)) for k, v in c["details"].items()), key=lambda kv: -kv[1])[:3],
                "top_merchants": sorted(((k, round(v, 2)) for k, v in c["merchants"].items()), key=lambda kv: -kv[1])[:3]})
        split = {g: round(sum(c["total"] for c in categories if c["group"] == g), 2) for g in ("needs", "wants", "fees")}

        merchants: dict[str, list] = {}
        for r in usd:
            m = merchants.setdefault(r["merchant"], [0.0, 0])
            m[0] += r["amount"]
            m[1] += 1
        top_merchants = [{"merchant": k, "total": round(v[0], 2), "count": v[1]}
                         for k, v in sorted(merchants.items(), key=lambda kv: -kv[1][0])[:10]]
        biggest = sorted(usd, key=lambda r: -r["amount"])[:5]
        by_cat_amounts: dict[str, list[float]] = {}
        for r in base + usd:
            by_cat_amounts.setdefault(r["category"], []).append(r["amount"])
        unusual = [r for r in usd if r["amount"] >= 100 and len(by_cat_amounts.get(r["category"], [])) >= 5
                   and r["amount"] >= 3 * statistics.median(by_cat_amounts[r["category"]])]
        unusual = sorted(unusual, key=lambda r: -r["amount"])[:5]

        trend = []
        first = today.replace(day=1)
        for n in range(5, -1, -1):
            m_start = _months_back(first, n)
            m_end = (_months_back(first, n - 1) - timedelta(days=1)) if n else today
            m_rows = [r for r in self._rows(m_start, m_end) if r["currency"] == "USD"]
            trend.append({"month": f"{m_start:%b %Y}" + (" (so far)" if not n else ""),
                          "spent": round(sum(r["amount"] for r in m_rows), 2), "income": self._income(m_start, m_end)})

        subs = self.fin.subscriptions()["subscriptions"]
        recurring = [s for s in subs if s["active"] and s["status"] not in ("cancel", "cancelled")
                     and s["currency"] == "USD"]
        kind = self._sub_categories()
        active = [s for s in recurring if kind.get(merchant_key(s["merchant"]), "subscriptions") in CANCELLABLE]
        bills = [s for s in recurring if s not in active]
        subs_yearly = round(sum(s["yearly_cost"] for s in active), 2)
        undecided = [s for s in active if s["status"] in ("new", "unsure")]
        price_up = [s for s in active if any(f.startswith("price up") for f in s["flags"])]

        debts = [d for d in self.adv.debts() if d["currency"] == "USD"]
        cards = self.fin.credit_use() or []
        missed = self.adv.missed_rewards(days=days)
        credits = [p for p in Benefits(self.memory).plan(today) if p["currency"] == "USD"]

        names = self._account_names()
        by_card: dict[str, dict] = {}
        for r in usd:
            key = r["card"] or "unknown"
            c = by_card.setdefault(key, {"total": 0.0, "categories": {}})
            c["total"] += r["amount"]
            cat = "fees" if self._is_fee(r) else r["category"]
            c["categories"][cat] = c["categories"].get(cat, 0) + r["amount"]
        cards_used = [{"card": names.get(k, f"••{k}" if k.isdigit() else k), "total": round(v["total"], 2),
                       "top": [(LABELS.get(c, c), round(a, 2)) for c, a in
                               sorted(v["categories"].items(), key=lambda kv: -kv[1])[:3]]}
                      for k, v in sorted(by_card.items(), key=lambda kv: -kv[1]["total"])]
        house: dict[str, float] = {}
        for r in usd:
            if r["category"] in HOUSE:
                k = names.get(r["card"], f"••{r['card']}" if r["card"].isdigit() else r["card"] or "unknown")
                house[k] = house.get(k, 0) + r["amount"]
        transfers = self._transfers(start, end, names)
        if left is not None:
            left = round(income - spent - transfers["to_others"], 2)
            rate = round(100 * left / income, 1) if income else None
        waste = self._waste(usd, fees, recurring, active, price_up, undecided, debts, months)
        opportunities = self._opportunities(categories, months, income, fees, undecided, price_up, debts, missed,
                                            credits, today)
        budget = self._budget(categories)
        return {"period": period, "label": label, "from": start.isoformat(), "to": end.isoformat(), "days": days,
                "spent": spent, "income": income, "left_over": left, "savings_rate": rate,
                "other_currencies": other_currencies, "split": split, "categories": categories,
                "top_merchants": top_merchants, "biggest": biggest, "unusual": unusual, "trend": trend,
                "subscriptions": {"count": len(active), "yearly": subs_yearly, "monthly": round(subs_yearly / 12, 2),
                                  "undecided": [s["merchant"] for s in undecided],
                                  "price_up": [f"{s['merchant']} ({next(f for f in s['flags'] if f.startswith('price up'))})"
                                               for s in price_up],
                                  "list": [{"merchant": s["merchant"], "amount": s["amount"], "cadence": s["cadence"],
                                            "yearly": s["yearly_cost"], "status": s["status"]} for s in active],
                                  "recurring_bills": [{"merchant": s["merchant"], "amount": s["amount"],
                                                       "cadence": s["cadence"]} for s in bills]},
                "fees": {"total": round(sum(r["amount"] for r in fees), 2),
                         "items": [{"day": r["day"], "merchant": r["merchant"], "amount": r["amount"]} for r in fees][:10]},
                "cards_used": cards_used,
                "house_by_card": sorted(((k, round(v, 2)) for k, v in house.items()), key=lambda kv: -kv[1]),
                "transfers": transfers, "waste": waste,
                "subscription_tracker": [{"service": s["merchant"], "amount": s["amount"], "billing": s["cadence"],
                                          "yearly": s["yearly_cost"], "last": s["last_charge"], "next": s["next_charge"],
                                          "cards": [names.get(c, c) for c in s["cards"]], "status": s["status"],
                                          "flags": s["flags"]} for s in active],
                "debts": debts, "cards": cards or [{**c, "balance_now": self.fin.balance_now(c)} for c in self.fin.cards()], "missed_rewards": missed, "credits": credits,
                "opportunities": opportunities,
                "possible_yearly_savings": round(sum(o["yearly"] for o in opportunities), 2),
                "budget": budget, "todo": [o["do"] for o in opportunities[:5]], "rule": DISCLAIMER}

    def _account_names(self) -> dict[str, str]:
        """'1004' -> 'Amex Gold Card ••1004', from the linked accounts and known cards."""
        names: dict[str, str] = {}
        for sql in ("SELECT name, mask FROM plaid_accounts", "SELECT name, last4 AS mask FROM fin_cards"):
            try:
                for r in self.memory.db.execute(sql):
                    if r["mask"]:
                        names.setdefault(r["mask"], f"{r['name']} ••{r['mask']}")
            except Exception:
                pass
        return names

    def _transfers(self, start: date, end: date, names: dict) -> dict:
        try:
            rows = [dict(r) for r in self.memory.db.execute(
                "SELECT day, amount, name, kind, recipient, account FROM fin_transfers WHERE day BETWEEN ? AND ? "
                "AND currency = 'USD' ORDER BY day", (start.isoformat(), end.isoformat()))]
        except Exception:
            rows = []
        labels = self.labels()
        out = {"own": 0.0, "person": 0.0, "abroad": 0.0, "other": 0.0, "family": 0.0}
        people: dict[tuple, list] = {}
        for r in rows:
            lab = self._label_for(r["recipient"], r["name"], labels, r["kind"])
            if lab and lab["label"] == "rent":
                continue  # counted as housing spending
            kind = "family" if lab and lab["label"] == "family_support" else \
                "own" if lab and lab["label"] == "savings" else r["kind"]
            out[kind] = out.get(kind, 0) + r["amount"]
            if kind != "own":
                p = people.setdefault((kind, r["recipient"], (lab or {}).get("note", "")), [0.0, 0, set()])
                p[0] += r["amount"]
                p[1] += 1
                p[2].add(names.get(r["account"], f"••{r['account']}" if r["account"] else "an account"))
        recipients = [{"kind": k, "to": who, "note": note, "total": round(v[0], 2), "count": v[1], "from": sorted(v[2])}
                      for (k, who, note), v in sorted(people.items(), key=lambda kv: -kv[1][0])]
        return {"to_savings": round(out["own"], 2), "to_people": round(out["person"], 2),
                "abroad": round(out["abroad"], 2), "other": round(out["other"], 2), "family": round(out["family"], 2),
                "to_others": round(out["person"] + out["abroad"] + out["other"] + out["family"], 2),
                "recipients": recipients[:12], "questions": self._transfer_questions(labels)}

    def _transfer_questions(self, labels: dict) -> list[str]:
        """Regular transfers to someone Buddy doesn't know: ask what they are instead of guessing."""
        since = (local_now().date() - timedelta(days=125)).isoformat()
        try:
            rows = self.memory.db.execute(
                "SELECT recipient, name, kind, amount, substr(day, 1, 7) AS month FROM fin_transfers "
                "WHERE day >= ? AND kind IN ('person', 'abroad', 'other')", (since,)).fetchall()
        except Exception:
            return []
        groups: dict[str, list] = {}
        for r in rows:
            if not self._label_for(r["recipient"], r["name"], labels, r["kind"]):
                groups.setdefault(r["recipient"], []).append(r)
        out = []
        for who, rs in groups.items():
            months = {r["month"] for r in rs}
            typical = statistics.median(r["amount"] for r in rs)
            steady = sum(abs(r["amount"] - typical) <= 0.1 * typical for r in rs) >= 0.7 * len(rs)
            if len(months) >= 2 and steady and typical >= 300:
                guess = "your parents' or family support" if rs[0]["kind"] == "abroad" else "your rent"
                out.append(f"Is the {_money(typical)} you send to {who} every month {guess}? Tell me and I'll "
                           "count it the right way.")
        return out[:3]

    def _waste(self, usd, fees, recurring, active, price_up, undecided, debts, months) -> list[dict]:
        """Money that bought nothing, or less than it could: each line with what it cost this period."""
        out = []
        kinds: dict[str, float] = {}
        for r in fees:
            label = r["detail"] or ("Interest" if "interest" in r["merchant"].lower() else r["merchant"].title())
            kinds[label] = kinds.get(label, 0) + r["amount"]
        for label, amt in sorted(kinds.items(), key=lambda kv: -kv[1]):
            why = "interest on a balance carried over: pay statements in full" if "interest" in label.lower() \
                else "paid for nothing; usually avoidable"
            out.append({"what": f"Fee: {label}", "amount": round(amt, 2), "why": why})
        if not any("interest" in k.lower() for k in kinds):  # no interest charge seen yet: the estimate
            for d in debts:
                if d["carrying_balance"] and d["interest_per_month"]:
                    out.append({"what": f"Interest on {d['card']}", "amount": round(d["interest_per_month"] * months, 2),
                                "why": f"{d['apr']:g}% APR on {_money(d['balance'])}"})
        for s in active:
            if "charged on more than one card" in s["flags"]:
                out.append({"what": f"{s['merchant']} on more than one card", "amount": s["amount"],
                            "why": "possibly paying twice"})
        streaming = [s for s in active if self._sub_categories().get(merchant_key(s["merchant"])) == "subscriptions"]
        if len(streaming) >= 3:
            total = sum(s["yearly_cost"] for s in streaming) / 12 * months
            out.append({"what": f"{len(streaming)} subscription services at once", "amount": round(total, 2),
                        "why": ", ".join(s["merchant"] for s in streaming[:5]) + ": rotate them, keep one or two"})
        for s in price_up:
            out.append({"what": f"{s['merchant']} price rise", "amount": s["amount"],
                        "why": next(f for f in s["flags"] if f.startswith("price up"))})
        delivery = sum(r["amount"] for r in usd if DELIVERY.search(r["merchant"]))
        if delivery >= 40:
            out.append({"what": "Delivery apps", "amount": round(delivery, 2),
                        "why": "delivery and service fees on top of the food; pick up when you can"})
        small = [r for r in usd if r["amount"] < 10 and r["category"] in ("food", "shopping", "other")]
        if len(small) >= 15:
            out.append({"what": f"{len(small)} small purchases under $10", "amount": round(sum(r["amount"] for r in small), 2),
                        "why": "they add up quietly"})
        return sorted(out, key=lambda w: -w["amount"])

    def _sub_categories(self) -> dict[str, str]:
        if getattr(self, "_subcat", None) is None:
            self._subcat = self._sub_categories_uncached()
        return self._subcat

    def _sub_categories_uncached(self) -> dict[str, str]:
        """Each recurring merchant's usual category (its charges' most common one), to tell services you could
        cancel (streaming, apps) from bills you can't (rent, utilities) and everyday shops."""
        counts: dict[str, dict[str, int]] = {}
        since = (local_now().date() - timedelta(days=400)).isoformat()
        for r in self.memory.db.execute("SELECT merchant, category FROM transactions WHERE day >= ? AND amount > 0",
                                        (since,)):
            c = counts.setdefault(merchant_key(r["merchant"]), {})
            c[r["category"]] = c.get(r["category"], 0) + 1
        return {k: max(v, key=v.get) for k, v in counts.items()}

    def _opportunities(self, categories, months, income, fees, undecided, price_up, debts, missed, credits,
                       today) -> list[dict]:
        ops = []
        if undecided:
            yearly = sum(s["yearly_cost"] for s in undecided)
            names = ", ".join(s["merchant"] for s in undecided[:4]) + ("…" if len(undecided) > 4 else "")
            ops.append({"what": "Subscriptions you haven't decided on", "yearly": round(yearly, 2),
                        "do": f"Keep or cancel {len(undecided)} subscription{'s' if len(undecided) > 1 else ''} "
                              f"({names}): up to {_money(yearly)} a year if you cancel "
                              f"{'them' if len(undecided) > 1 else 'it'} (Money screen)."})
        for s in price_up:
            prev = next(f for f in s["flags"] if f.startswith("price up"))
            try:
                old = float(prev.rsplit(" ", 1)[1])
            except ValueError:
                continue
            extra = (s["amount"] - old) * (s["yearly_cost"] / s["amount"] if s["amount"] else 12)
            ops.append({"what": f"{s['merchant']} price rise", "yearly": round(extra, 2),
                        "do": f"{s['merchant']} went up to {_money(s['amount'])} ({prev}): about {_money(extra)} more a "
                              "year. Ask for the old price, switch plans, or cancel."})
        carrying = any(d["carrying_balance"] and d["interest_per_month"] for d in debts)
        is_interest = lambda r: "interest" in (r["detail"] + " " + r["merchant"]).lower()  # noqa: E731
        # with a card carrying a balance, its interest is its own line below: count it once
        fee_total = sum(r["amount"] for r in fees if not (carrying and is_interest(r)))
        if fee_total:
            yearly = fee_total / months * 12
            what = "Fees" if carrying else "Fees and interest"
            ops.append({"what": what, "yearly": round(yearly, 2),
                        "do": f"You paid {_money(fee_total)} in {what.lower()}: about {_money(yearly)} a year. "
                              "Autopay the full statement balance and ask the bank to waive fees."})
        for d in debts:
            if d["carrying_balance"] and d["interest_per_month"]:
                ops.append({"what": f"Interest on {d['card']}", "yearly": round(d["interest_per_month"] * 12, 2),
                            "do": f"{d['card']} carries {_money(d['balance'])} at {d['apr']:g}% APR, about "
                                  f"{_money(d['interest_per_month'] * 12)} interest a year. Pay it down first (ask me "
                                  "for a payoff plan)."})
        for m in missed[:3]:
            yearly = m["missed_about"] * 365 / max(1, m["days"])
            ops.append({"what": f"Rewards on {m['category']}", "yearly": round(yearly, 2),
                        "do": f"Use {m['best_card']} for {m['category']} ({m['best_rate']:g}x instead of "
                              f"{m['earned_rate']:g}x): about {_money(yearly)} more back a year."})
        for c in categories:
            if c["group"] != "wants" or not c["usual_per_month"] or c["change"] is None:
                continue
            extra = c["per_month"] - c["usual_per_month"]
            if c["change"] >= 15 and extra >= 50:
                ops.append({"what": f"{c['label']} above your usual", "yearly": round(extra * 12, 2),
                            "do": f"{c['label']} is {c['change']}% above your usual ({_money(c['per_month'])} vs "
                                  f"{_money(c['usual_per_month'])} a month). Back to usual saves about "
                                  f"{_money(extra * 12)} a year."})
        dining = next((c for c in categories if c["category"] == "food"), None)
        if dining and not any(o["what"].startswith("Eating out") for o in ops):
            limit = DINING_SHARE * income / months if income else 300
            if dining["per_month"] > limit:
                save = dining["per_month"] * 0.25 * 12
                ops.append({"what": "Eating out", "yearly": round(save, 2),
                            "do": f"Eating out is {_money(dining['per_month'])} a month. Cooking a few more meals "
                                  f"(a quarter less) saves about {_money(save)} a year."})
        for p in credits:
            if p["left"] > 0:
                ops.append({"what": f"{p['benefit']} running out", "yearly": round(p["left"], 2),
                            "do": f"Use your {p['benefit']} before {p['ends']}: {_money(p['left'])} left that you'd "
                                  "otherwise lose."})
        ops = [o for o in ops if o["yearly"] >= MIN_OPPORTUNITY or "running out" in o["what"]]
        # one saving, one line: a price rise inside a category that's above usual is the same money
        above = {o["what"].replace(" above your usual", "") for o in ops if o["what"].endswith("above your usual")}
        cat_of = self._sub_categories()
        ops = [o for o in ops if not (o["what"].endswith("price rise") and LABELS.get(
            cat_of.get(merchant_key(o["what"].replace(" price rise", "")), ""), "") in above)]
        return sorted(ops, key=lambda o: -o["yearly"])

    @staticmethod
    def _budget(categories) -> list[dict]:
        out = []
        for c in categories:
            if c["group"] == "fees":
                target = 0.0
            else:
                ref = min(c["per_month"], c["usual_per_month"]) if c["usual_per_month"] else c["per_month"]
                target = ref if c["group"] == "needs" else ref * 0.9
            out.append({"category": c["category"], "label": c["label"], "now": c["per_month"],
                        "budget": round(target / 10) * 10 if target >= 100 else round(target)})
        return out

    # --- the report --------------------------------------------------------------------------------
    def markdown(self, r: dict) -> str:
        L = [f"# Money review: {r['label']}", ""]
        head = f"Spent **{_money(r['spent'])}**"
        if r["income"] is not None:
            head += f" of **{_money(r['income'])}** coming in"
            head += f", **{_money(r['left_over'])} left over** ({r['savings_rate']:g}% saved)" if r["left_over"] >= 0 \
                else f", **{_money(-r['left_over'])} more than came in**"
        L += [head + ".", ""]
        t = r["transfers"]
        if r["income"] is not None or any(t[k] for k in ("to_savings", "to_people", "abroad", "other")):
            L += ["## Money in and out", "| What | Amount |", "| --- | --- |"]
            if r["income"] is not None:
                L.append(f"| Came in | {_money(r['income'])} |")
            L.append(f"| Spent | {_money(r['spent'])} |")
            for key, name in (("family", "Family support"), ("to_people", "Sent to people"), ("abroad", "Sent abroad"),
                              ("other", "Other transfers"), ("to_savings", "Moved to your savings or investments")):
                if t[key]:
                    L.append(f"| {name} | {_money(t[key])} |")
            if r["left_over"] is not None:
                L.append(f"| Left{' (including savings)' if t['to_savings'] else ''} | {_money(r['left_over'])} |")
            L.append("")
        if r.get("notes"):
            L += ["## Advisor's review", r["notes"].strip(), ""]
        if r["transfers"]["questions"]:
            L += ["## Questions for you"] + [f"- {q}" for q in r["transfers"]["questions"]] + [""]
        if r["possible_yearly_savings"] and r.get("notes"):  # the advisor's recommendations already say what to do
            L += [f"You could save about **{_money(r['possible_yearly_savings'])} a year** (see Ways to save).", ""]
        elif r["possible_yearly_savings"]:
            L += [f"You could save about **{_money(r['possible_yearly_savings'])} a year**. Start here:", ""]
            L += [f"{i}. {t}" for i, t in enumerate(r["todo"], 1)] + [""]
        if r["spent"]:
            s = r["split"]
            L += ["## Needs, wants and fees", "| Group | Amount | Share |", "| --- | --- | --- |"]
            for g, name in (("needs", "Needs"), ("wants", "Wants"), ("fees", "Fees and interest")):
                if s[g]:
                    L.append(f"| {name} | {_money(s[g])} | {round(100 * s[g] / r['spent'])}% |")
            L.append("")
            L += ["## Where the money went", "| Category | Per month | Usual | Change |", "| --- | --- | --- | --- |"]
            for c in r["categories"]:
                usual = _money(c["usual_per_month"]) if c["usual_per_month"] else "-"
                change = f"{c['change']:+d}%" if c["change"] is not None else "new"
                L.append(f"| {c['label']} | {_money(c['per_month'])} | {usual} | {change} |")
            L.append("")
            for c in r["categories"][:6]:
                bits = []
                if c["top_details"]:
                    bits.append("by type: " + ", ".join(f"{k} {_money(v)}" for k, v in c["top_details"]))
                if c["top_merchants"]:
                    bits.append("top: " + ", ".join(f"{k} {_money(v)}" for k, v in c["top_merchants"]))
                L.append(f"- **{c['label']}** {_money(c['total'])} ({c['share']:g}%, {c['count']} "
                         f"charge{'s' if c['count'] != 1 else ''}); "
                         + "; ".join(bits))
            L.append("")
        if r["cards_used"]:
            L += ["## By card and account", "| Card or account | Spent | Mostly |", "| --- | --- | --- |"]
            L += [f"| {c['card']} | {_money(c['total'])} | " + ", ".join(f"{k} {_money(v)}" for k, v in c["top"][:2]) + " |"
                  for c in r["cards_used"]] + [""]
        if r["house_by_card"]:
            L += ["House expenses (rent or mortgage, home, utilities) were paid with: " + ", ".join(
                f"{k} {_money(v)}" for k, v in r["house_by_card"]) + ".", ""]
        if t["recipients"] or t["to_savings"]:  # rent paid by transfer is under housing above
            L += ["## Transfers"]
            names = {"person": "to", "abroad": "abroad via", "other": "to", "family": "family support via"}
            L += [f"- {names.get(x['kind'], 'to')} **{x['to']}**" + (f" ({x['note']})" if x["note"] else "")
                  + f": {_money(x['total'])} in {x['count']} transfer{'s' if x['count'] != 1 else ''}, from "
                  f"{', '.join(x['from'])}" for x in t["recipients"]]
            if t["to_savings"]:
                L.append(f"- to your own savings or investments: {_money(t['to_savings'])}")
            L.append("")
        if r["waste"]:
            L += ["## Waste and leaks", "| What | Cost | Why |", "| --- | --- | --- |"]
            L += [f"| {w['what']} | {_money(w['amount'])} | {w['why']} |" for w in r["waste"]] + [""]
        L += ["## Six-month trend", "| Month | Spent | In |", "| --- | --- | --- |"]
        L += [f"| {t['month']} | {_money(t['spent'])} | {_money(t['income']) if t['income'] is not None else '-'} |"
              for t in r["trend"]] + [""]
        if r["top_merchants"]:
            L += ["## Top places"] + [f"- {m['merchant']}: {_money(m['total'])} ({m['count']}×)"
                                      for m in r["top_merchants"]] + [""]
        if r["biggest"]:
            L += ["## Biggest charges"] + [f"- {b['day']}: {b['merchant']} {_money(b['amount'])}" for b in r["biggest"]]
            if r["unusual"]:
                L += ["", "Unusual for you (3× your usual in that category): " + ", ".join(
                    f"{u['merchant']} {_money(u['amount'])} on {u['day']}" for u in r["unusual"])]
            L.append("")
        s = r["subscriptions"]
        L += ["## Subscription tracker", f"{s['count']} active: {_money(s['monthly'])} a month, {_money(s['yearly'])} a year."
              if s["count"] else "None found."]
        if r["subscription_tracker"]:
            L += ["| Service | Amount | Next | Card |", "| --- | --- | --- | --- |"]
            for x in r["subscription_tracker"][:15]:
                note = " (to decide)" if x["status"] in ("new", "unsure") else ""
                L.append(f"| {x['service']}{note} | {_money(x['amount'])} {x['billing']} | {x['next']} | "
                         f"{', '.join(x['cards']) or '-'} |")
        if s["price_up"]:
            L.append("Price rises: " + ", ".join(s["price_up"]))
        if s["recurring_bills"]:
            L.append("Other regular charges (bills and everyday places, not counted as subscriptions): " + ", ".join(
                f"{b['merchant']} {_money(b['amount'])} {b['cadence']}" for b in s["recurring_bills"][:8]))
        L.append("")
        if r["fees"]["total"]:
            L += ["## Fees and interest", f"{_money(r['fees']['total'])} this period."]
            L += [f"- {f['day']}: {f['merchant']} {_money(f['amount'])}" for f in r["fees"]["items"]] + [""]
        card_lines = []
        for c in r["cards"]:
            bits = [f"owes {_money(c['balance_now'])}" if c.get("balance_now") is not None else "",
                    f"{c['used_now']}% of the limit" if c.get("used_now") is not None else "",
                    f"{c['apr']:g}% APR" if c.get("apr") else "", f"due {c['next_due']}" if c.get("next_due") else ""]
            if any(bits):
                card_lines.append(f"- {c['name']}" + (f" ••{c['last4']}" if c.get("last4") else "") + ": "
                                  + ", ".join(b for b in bits if b))
        if card_lines:
            L += ["## Cards"] + card_lines + [""]
        if r["missed_rewards"]:
            L += ["## Rewards missed"] + [f"- {m['category']}: {_money(m['spent'])} on {m['on']}; {m['best_card']} "
                                          f"would have earned about {_money(m['missed_about'])} more"
                                          for m in r["missed_rewards"][:5]] + [""]
        if r["credits"]:
            L += ["## Card credits running out"] + [f"- {p['benefit']}: {_money(p['left'])} left until {p['ends']}"
                                                    for p in r["credits"]] + [""]
        if r["opportunities"]:
            L += ["## Ways to save", "| Where | A year |", "| --- | --- |"]
            L += [f"| {o['what']} | {_money(o['yearly'])} |" for o in r["opportunities"]]
            L += [f"| **Total** | **{_money(r['possible_yearly_savings'])}** |", ""]
        if r["budget"]:
            L += ["## Suggested monthly budget", "| Category | Now | Budget |", "| --- | --- | --- |"]
            L += [f"| {b['label']} | {_money(b['now'])} | {_money(b['budget'])} |" for b in r["budget"]]
            L += ["", "Needs stay at your usual; wants are 10% under your usual; fees aim for zero. "
                      "Say \"set my eating out limit to 300\" to have Buddy watch any of these.", ""]
        if r["other_currencies"]:
            L += [f"Also spent in {', '.join(r['other_currencies'])} (not included above).", ""]
        L += [f"_{r['rule']}_"]
        return "\n".join(L)

    def summary(self, r: dict) -> str:
        bits = [f"Your money review for {r['label']}: you spent {_money(r['spent'])}"]
        if r["left_over"] is not None:
            bits[0] += (f" and kept {_money(r['left_over'])}, {r['savings_rate']:g}% of what came in"
                        if r["left_over"] >= 0 else f", {_money(-r['left_over'])} more than came in")
        bits[0] += "."
        if r["categories"]:
            top = r["categories"][0]
            bits.append(f"The most went to {top['label'].lower()}, {_money(top['total'])}.")
        if r["possible_yearly_savings"]:
            bits.append(f"I found about {_money(r['possible_yearly_savings'])} a year you could save; the first: "
                        f"{r['todo'][0]}")
        bits.append("The full review is in Reports.")
        return " ".join(bits)

    async def notes(self, r: dict) -> str:
        """The advisor's review: Claude reads the finished numbers like a CPA reviewing the books and writes the
        commentary. Only numbers given here may appear; nothing new is worked out by the model."""
        facts = {k: r[k] for k in ("label", "spent", "income", "left_over", "savings_rate", "split", "transfers",
                                   "waste", "possible_yearly_savings", "budget", "house_by_card", "cards_used")}
        facts["categories"] = [{k: c[k] for k in ("label", "per_month", "usual_per_month", "change", "share",
                                                  "top_details", "top_merchants")} for c in r["categories"][:10]]
        facts["subscriptions"] = {k: r["subscriptions"][k] for k in ("count", "monthly", "yearly", "undecided", "price_up")}
        facts["opportunities"] = r["opportunities"][:8]
        facts["cards"] = [{k: c.get(k) for k in ("name", "balance_now", "used_now", "apr", "next_due")} for c in r["cards"]]
        facts["trend"] = r["trend"]
        system = ("You are a careful personal financial planner and CPA reviewing a client's month for them. Write the "
                  "review in plain, warm, direct language, second person. Use ONLY the numbers in the data; never invent "
                  "or recompute figures, and never name investment products. Markdown with exactly these parts: "
                  "**Overall** (2-3 sentences: how the period went, the savings rate, the trend), **What stands out** "
                  "(3-5 bullets: where the money went, changes against usual, transfers to people or abroad, which "
                  "card carries what), **Waste and risks** (bullets: fees, interest, duplicates, high card use, "
                  "spending above income), **Recommendations** (a numbered list of 3-6, most valuable first, each "
                  "with its yearly dollar amount from the data and one concrete next step). Under 380 words. "
                  "Transfers to people, family support and money sent abroad are the client's choices: describe them "
                  "neutrally and respectfully, never as waste or something to cut. Rent paid by transfer is housing.")
        from .agent import fallback_kwargs  # noqa: PLC0415 (agent imports review)

        started = time.monotonic()
        try:
            response = await client.beta.messages.create(
                model=settings.chat_model, max_tokens=1800, system=system,
                output_config={"effort": "medium"}, **fallback_kwargs(settings.chat_model),
                messages=[{"role": "user", "content": "The client's numbers:\n" + json.dumps(facts, default=str)}])
        except Exception:
            log.exception("money review: advisor notes failed")
            return ""
        if hasattr(response, "usage"):
            usage.record(self.memory, response.model, response.usage, purpose="money_review", response=response,
                         started=started)
        if getattr(response, "stop_reason", "") == "refusal":
            return ""
        return "".join(b.text for b in response.content if getattr(b, "type", "") == "text").strip()

    async def save_full(self, period: str = "last_month", today: date | None = None) -> tuple[int, str, dict]:
        """compute + the advisor's review + save: what the chat tool and the monthly run use."""
        r = self.compute(period, today)
        if r["spent"] or r["income"]:
            r["notes"] = await self.notes(r)
        return self.save(period, today, computed=r)

    def save(self, period: str = "last_month", today: date | None = None, computed: dict | None = None) -> tuple[int, str, dict]:
        from .jobs import SCHEMA as JOBS_SCHEMA

        r = computed or self.compute(period, today)
        if not r["spent"] and not r["income"]:
            raise ToolError("No transactions in that period yet. Connect a bank (say \"connect my bank\") or try "
                            "another period.")
        summary = self.summary(r)
        now = datetime.now(timezone.utc).isoformat()
        self.memory.db.executescript(JOBS_SCHEMA)
        purge_reports(self.memory)
        job_id = self.memory.db.execute(
            "INSERT INTO jobs (kind, title, params, status, created_at, started_at, finished_at, summary, report, findings, "
            "sources, delivered) VALUES ('money_review', ?, ?, 'done', ?, ?, ?, ?, ?, ?, '[]', 1)",
            (f"Money review, {r['label']}", json.dumps({"period": period}), now, now, now, summary, self.markdown(r),
             json.dumps(r["todo"]))).lastrowid
        self.memory.db.commit()
        return job_id, summary, r


async def review_tool(memory: Memory, args: dict) -> str:
    job_id, summary, r = await Review(memory).save_full(str(args.get("period") or "last_month"))
    return json.dumps({"saved_report": job_id, "summary": summary, "top_ways_to_save": r["todo"][:3],
                       "possible_yearly_savings": r["possible_yearly_savings"],
                       "transfers": {k: r["transfers"][k] for k in ("to_people", "abroad", "family", "to_savings")},
                       "ask_the_user": r["transfers"]["questions"]},
                      ensure_ascii=False)
