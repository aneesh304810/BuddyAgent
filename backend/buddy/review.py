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
import statistics
from datetime import date, datetime, timedelta, timezone

from .advisor import DISCLAIMER, Advisor
from .benefits import Benefits
from .finance import INTEREST, Finance, merchant_key, purge_reports
from .memory import Memory
from .tools import ToolError, local_now

PERIODS = ("last_month", "this_month", "last_3_months", "last_6_months", "this_year", "last_12_months")
NEEDS = ("groceries", "bills", "health", "transport")
WANTS = ("food", "shopping", "entertainment", "travel", "subscriptions", "cash", "other")
LABELS = {"food": "Eating out", "groceries": "Groceries", "transport": "Transport", "travel": "Travel",
          "shopping": "Shopping", "bills": "Bills and utilities", "health": "Health and personal care",
          "entertainment": "Entertainment", "subscriptions": "Subscriptions", "cash": "Cash",
          "fees": "Fees and interest", "other": "Other"}
DAYS_PER_MONTH = 30.44
MIN_OPPORTUNITY = 60          # dollars a year: smaller savings aren't worth a line
DINING_SHARE = 0.10           # eating out above this share of income (or $300 a month) gets a suggestion
CANCELLABLE = ("subscriptions", "entertainment", "other", "shopping")  # recurring charges you could cancel


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

    # --- the numbers --------------------------------------------------------------------------------
    def _rows(self, start: date, end: date) -> list[dict]:
        return [dict(r) for r in self.memory.db.execute(
            "SELECT day, amount, currency, merchant, category, card, COALESCE(detail, '') AS detail FROM transactions "
            "WHERE day BETWEEN ? AND ? AND amount > 0", (start.isoformat(), end.isoformat()))]

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
                "debts": debts, "cards": cards or [{**c, "balance_now": self.fin.balance_now(c)} for c in self.fin.cards()], "missed_rewards": missed, "credits": credits,
                "opportunities": opportunities,
                "possible_yearly_savings": round(sum(o["yearly"] for o in opportunities), 2),
                "budget": budget, "todo": [o["do"] for o in opportunities[:5]], "rule": DISCLAIMER}

    def _sub_categories(self) -> dict[str, str]:
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
        fee_total = sum(r["amount"] for r in fees)
        if fee_total:
            yearly = fee_total / months * 12
            ops.append({"what": "Fees and interest", "yearly": round(yearly, 2),
                        "do": f"You paid {_money(fee_total)} in fees and interest: about {_money(yearly)} a year. "
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
        if r["possible_yearly_savings"]:
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
        L += ["## Subscriptions", f"{s['count']} active: {_money(s['monthly'])} a month, {_money(s['yearly'])} a year."
              if s["count"] else "None found."]
        L += [f"- {x['merchant']}: {_money(x['amount'])} {x['cadence']} ({_money(x['yearly'])} a year)"
              + (" · to decide" if x["status"] in ("new", "unsure") else "") for x in s["list"][:12]]
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

    def save(self, period: str = "last_month", today: date | None = None) -> tuple[int, str, dict]:
        from .jobs import SCHEMA as JOBS_SCHEMA

        r = self.compute(period, today)
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
