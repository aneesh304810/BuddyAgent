"""Finance, phase C: the money advisor. Every number is worked out here, on the server; Claude only
puts the finished numbers into words. Advice only: Buddy never moves money, pays, or opens accounts.

  best card    each card's reward rates (told by the user, or a preset for well-known cards that the
               user confirms) -> the best card for each kind of spending, and what the last 90 days
               would have earned on the best card instead ("$34 more a quarter on groceries")
  payoff       cards carrying a balance: interest each month, and a plan for a monthly amount, highest
               APR first (avalanche) vs smallest balance first (snowball): months and total interest
  payday       due dates checked against paydays (due at least 3 days after one), autopay for the full
               statement balance on every card
  goals        emergency fund (months of spending), paying off a card, saving for something: progress
               from the bank feed or from what the user says
  monthly      on the 1st, a check-in report: last month's spending vs income, goals, rewards missed,
               interest paid, subscriptions, and the few things to do
"""

import calendar
import json
import os
import re
from datetime import date, datetime, timedelta, timezone

from .benefits import Benefits
from .finance import Finance, _day, has_money_data, purge_reports
from .memory import Memory
from .tools import ToolError, local_now

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS fin_rewards (
    card_id INTEGER NOT NULL,
    category TEXT NOT NULL,            -- dining, groceries, travel, flights, transit, gas, streaming, drugstores, online, other
    rate REAL NOT NULL,                -- points or percent back per dollar (1 point ~ 1 cent)
    note TEXT NOT NULL DEFAULT '',
    confirmed INTEGER NOT NULL DEFAULT 1,  -- 0 = a preset the user hasn't confirmed
    PRIMARY KEY (card_id, category)
);
CREATE TABLE IF NOT EXISTS fin_goals (
    id INTEGER PRIMARY KEY,
    kind TEXT NOT NULL,                -- emergency_fund | pay_off | save
    name TEXT NOT NULL,
    target REAL,                       -- amount; emergency_fund: months of spending
    current REAL,                      -- what the user said they have (when the bank feed can't tell)
    card_id INTEGER,                   -- pay_off: the card
    due TEXT,
    currency TEXT NOT NULL DEFAULT 'USD',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""

REWARD_CATEGORIES = ("dining", "groceries", "travel", "flights", "hotels", "transit", "gas", "streaming",
                     "drugstores", "online", "entertainment", "other")
# Spending categories (money.py) -> reward categories.
SPEND_TO_REWARD = {"food": "dining", "groceries": "groceries", "travel": "travel", "transport": "transit",
                   "entertainment": "entertainment", "subscriptions": "streaming", "health": "drugstores",
                   "shopping": "online", "bills": "other", "cash": None, "other": "other"}
# Published earn rates of well-known US cards (points or % per dollar). Issuers change these, caps
# apply, and some need activation: a preset is saved unconfirmed and the user is asked to check it.
PRESETS = {
    "amex gold": {"dining": 4, "groceries": 4, "flights": 3, "other": 1},
    "amex platinum": {"flights": 5, "hotels": 5, "other": 1},
    "blue cash preferred": {"groceries": 6, "streaming": 6, "transit": 3, "gas": 3, "other": 1},
    "blue cash everyday": {"groceries": 3, "online": 3, "gas": 3, "other": 1},
    "sapphire preferred": {"dining": 3, "streaming": 3, "groceries": 3, "travel": 2, "other": 1},
    "sapphire reserve": {"dining": 3, "travel": 3, "other": 1},
    "freedom unlimited": {"dining": 3, "drugstores": 3, "other": 1.5},
    "freedom flex": {"dining": 3, "drugstores": 3, "other": 1},
    "double cash": {"other": 2},
    "custom cash": {"other": 1},
    "venture x": {"other": 2},
    "venture": {"other": 2},
    "savor": {"dining": 3, "entertainment": 3, "streaming": 3, "groceries": 3, "other": 1},
    "quicksilver": {"other": 1.5},
    "active cash": {"other": 2},
    "apple card": {"other": 2},
    "discover it": {"other": 1},
    "bilt": {"dining": 3, "travel": 2, "other": 1},
}
PAYDAY_KEY = "finance:payday"      # e.g. {"kind": "biweekly", "anchor": "2026-11-06"} or {"kind": "monthly", "days": [1, 15]}
INCOME_KEY = "finance:income"      # {"amount": 6200, "currency": "USD"} a month, after tax
AUTOPAY_KEY = "finance:autopay"    # {card_id: "full" | "minimum" | "none"}
MONTHLY_AT = os.getenv("BUDDY_MONEY_MONTHLY", "1 09:00")   # day of month and time; "" = off
DISCLAIMER = ("Buddy's suggestions come from your own numbers; they aren't professional financial, tax or "
              "investment advice.")


class Advisor:
    def __init__(self, memory: Memory):
        self.memory = memory
        self.fin = Finance(memory)
        memory.db.executescript(SCHEMA_SQL)

    def _json(self, key: str, default):
        try:
            return json.loads(self.memory.get(key) or "null") or default
        except json.JSONDecodeError:
            return default

    def _card(self, what) -> dict:
        what = str(what).strip().lower()
        cards = self.fin.cards()
        match = next((c for c in cards if str(c["id"]) == what or (what and c["last4"] == re.sub(r"\D", "", what)[-4:]
                                                                    and re.sub(r"\D", "", what))), None) or next(
            (c for c in cards if what and (what in c["name"].lower() or c["name"].lower() in what)), None)
        if not match:
            raise ToolError(f"No card matching '{what}'. Cards come from statements, or add one with finance card.")
        return match

    # --- rewards and the best card -------------------------------------------------------------
    def set_reward(self, card, category: str, rate: float, note: str = "") -> str:
        c = self._card(card)
        category = category.strip().lower()
        if category not in REWARD_CATEGORIES:
            raise ToolError(f"Category: {', '.join(REWARD_CATEGORIES)}.")
        self.memory.db.execute("INSERT OR REPLACE INTO fin_rewards (card_id, category, rate, note, confirmed) "
                               "VALUES (?, ?, ?, ?, 1)", (c["id"], category, float(rate), note[:120]))
        self.memory.db.commit()
        return f"{c['name']}: {rate:g}x on {category}."

    def apply_presets(self) -> list[str]:
        """Cards whose name matches a well-known card get its published rates, unconfirmed."""
        added = []
        for c in self.fin.cards():
            if self.memory.db.execute("SELECT 1 FROM fin_rewards WHERE card_id = ?", (c["id"],)).fetchone():
                continue
            name = f"{c['name']} {c.get('issuer', '')}".lower().replace("american express", "amex")
            preset = next((rates for key, rates in sorted(PRESETS.items(), key=lambda kv: -len(kv[0])) if key in name),
                          None)
            if not preset:
                continue
            self.memory.db.executemany("INSERT OR IGNORE INTO fin_rewards (card_id, category, rate, confirmed) "
                                       "VALUES (?, ?, ?, 0)", [(c["id"], cat, r) for cat, r in preset.items()])
            added.append(c["name"])
        self.memory.db.commit()
        return added

    def confirm_rewards(self, card) -> str:
        c = self._card(card)
        self.memory.db.execute("UPDATE fin_rewards SET confirmed = 1 WHERE card_id = ?", (c["id"],))
        self.memory.db.commit()
        return f"Confirmed {c['name']}'s reward rates."

    def rewards(self) -> list[dict]:
        out = []
        for c in self.fin.cards():
            rates = {r["category"]: r["rate"] for r in self.memory.db.execute(
                "SELECT category, rate FROM fin_rewards WHERE card_id = ?", (c["id"],))}
            unconfirmed = bool(self.memory.db.execute(
                "SELECT 1 FROM fin_rewards WHERE card_id = ? AND confirmed = 0", (c["id"],)).fetchone())
            out.append({"card": c["name"], "last4": c["last4"], "rates": rates, "preset_to_confirm": unconfirmed})
        return out

    def _rate(self, rates: dict, category: str) -> float:
        if category in rates:
            return rates[category]
        if category in ("flights", "hotels") and "travel" in rates:
            return rates["travel"]
        return rates.get("other", 1.0 if rates else 0.0)

    def best_cards(self) -> dict:
        """The best card for each kind of spending (among cards with known rates)."""
        rated = [r for r in self.rewards() if r["rates"]]
        best = {}
        for category in REWARD_CATEGORIES:
            if not rated:
                break
            top = max(rated, key=lambda r: self._rate(r["rates"], category))
            best[category] = {"card": top["card"], "rate": self._rate(top["rates"], category)}
        return best

    def best_for(self, what: str) -> dict:
        """Which card for a purchase: 'groceries', 'Whole Foods', 'a flight'."""
        from .taste import category_of

        text = str(what).lower()
        category = next((c for c in REWARD_CATEGORIES if c in text or c.rstrip("s") in text), None)
        if not category:
            words = {"restaurant": "dining", "dinner": "dining", "lunch": "dining", "coffee": "dining", "bar": "dining",
                     "supermarket": "groceries", "grocery": "groceries", "whole foods": "groceries",
                     "trader joe": "groceries", "costco": "groceries", "flight": "flights", "airline": "flights",
                     "hotel": "hotels", "uber": "transit", "lyft": "transit", "train": "transit", "gas": "gas",
                     "netflix": "streaming", "spotify": "streaming", "pharmacy": "drugstores", "cvs": "drugstores",
                     "walgreens": "drugstores", "amazon": "online"}
            category = next((v for k, v in words.items() if k in text), None)
        if not category:
            spend = category_of(text)
            category = {"food": "dining", "hotels": "hotels", "travel": "travel"}.get(spend, "other")
        best = self.best_cards().get(category)
        if not best:
            return {"category": category, "card": None, "why": "No reward rates known yet: tell me your cards' "
                    "rates, or confirm the presets."}
        return {"category": category, **best}

    def missed_rewards(self, days: int = 90) -> list[dict]:
        """Spending on a card that earns less than the best one for that category, last `days` days."""
        rated = {r["card"]: r for r in self.rewards() if r["rates"]}
        if not rated:
            return []
        by_key = {}
        for c in self.fin.cards():
            by_key[c["last4"] or c["name"]] = c["name"]
            by_key[c["name"]] = c["name"]
        best = self.best_cards()
        since = (local_now().date() - timedelta(days=days)).isoformat()
        rows = self.memory.db.execute(
            "SELECT category, card, SUM(amount) AS spent, currency FROM transactions WHERE day >= ? AND amount > 0 "
            "AND currency = 'USD' GROUP BY category, card", (since,)).fetchall()
        out = []
        for r in rows:
            reward_cat = SPEND_TO_REWARD.get(r["category"])
            if not reward_cat or reward_cat not in best:
                continue
            used = by_key.get(r["card"] or "")
            used_rate = self._rate(rated[used]["rates"], reward_cat) if used in rated else 0.0
            top = best[reward_cat]
            gain = round(r["spent"] * (top["rate"] - used_rate) / 100, 2)
            if top["card"] != used and gain >= 2:
                out.append({"category": reward_cat, "spent": round(r["spent"], 2), "on": used or (r["card"] or "unknown"),
                            "earned_rate": used_rate, "best_card": top["card"], "best_rate": top["rate"],
                            "missed_about": gain, "days": days})
        return sorted(out, key=lambda m: -m["missed_about"])

    # --- paying off cards ---------------------------------------------------------------------
    def debts(self) -> list[dict]:
        out = []
        for c in self.fin.cards():
            balance = self.fin.balance_now(c)
            if not balance or balance <= 0:
                continue
            interest = self.memory.db.execute(
                "SELECT COALESCE(SUM(amount), 0) FROM transactions WHERE day >= ? AND card IN (?, ?) AND "
                "(lower(merchant) LIKE '%interest%' OR lower(merchant) LIKE '%finance charge%')",
                ((local_now().date() - timedelta(days=35)).isoformat(), c["last4"] or "-", c["name"])).fetchone()[0]
            carrying = interest > 0
            out.append({"card": c["name"], "balance": balance, "apr": c["apr"] or 0, "currency": c["currency"],
                        "minimum": c["minimum_due"] or 0, "carrying_balance": carrying, "interest_last_month": round(interest, 2),
                        "interest_per_month": round(balance * (c["apr"] or 0) / 1200, 2) if carrying else 0})
        return out

    def payoff_plan(self, monthly: float, debts: list[dict] | None = None) -> dict:
        """Months and total interest to clear the carried balances with `monthly` a month, two ways."""
        debts = [d for d in (debts if debts is not None else self.debts()) if d["carrying_balance"]]
        if not debts:
            return {"debts": [], "note": "No card is carrying a balance (no interest seen): pay each statement in full."}
        minimums = sum(max(d["minimum"], 25) for d in debts)
        if monthly < minimums:
            return {"debts": debts, "note": f"{monthly:,.0f} a month is less than the minimums ({minimums:,.0f})."}

        def simulate(order) -> tuple[int, float, list[str]]:
            bal = {d["card"]: d["balance"] for d in debts}
            apr = {d["card"]: d["apr"] for d in debts}
            minimum = {d["card"]: max(d["minimum"], 25) for d in debts}
            months, interest, cleared = 0, 0.0, []
            while any(b > 0.005 for b in bal.values()) and months < 600:
                months += 1
                for k in bal:
                    i = bal[k] * apr[k] / 1200
                    interest += i
                    bal[k] += i
                left = monthly
                for k in bal:  # minimums first
                    pay = min(bal[k], minimum[k])
                    bal[k] -= pay
                    left -= pay
                for k in order:  # the rest to the first card in the order
                    if left <= 0:
                        break
                    pay = min(bal[k], left)
                    bal[k] -= pay
                    left -= pay
                cleared += [k for k in order if bal[k] <= 0.005 and k not in cleared]
            return months, round(interest, 2), cleared

        avalanche = [d["card"] for d in sorted(debts, key=lambda d: -d["apr"])]
        snowball = [d["card"] for d in sorted(debts, key=lambda d: d["balance"])]
        a, s = simulate(avalanche), simulate(snowball)
        return {"monthly": monthly, "debts": debts,
                "highest_apr_first": {"order": avalanche, "months": a[0], "total_interest": a[1]},
                "smallest_balance_first": {"order": snowball, "months": s[0], "total_interest": s[1]},
                "saves_with_highest_apr_first": round(s[1] - a[1], 2)}

    # --- paydays and autopay -----------------------------------------------------------------
    def set_payday(self, kind: str, anchor: str = "", days: list[int] | None = None) -> str:
        if kind == "biweekly":
            _day(anchor)  # a recent payday, YYYY-MM-DD
            value = {"kind": "biweekly", "anchor": anchor[:10]}
        elif kind == "weekly":
            _day(anchor)
            value = {"kind": "weekly", "anchor": anchor[:10]}
        elif kind == "monthly":
            days = sorted({int(d) for d in (days or []) if 1 <= int(d) <= 31})
            if not days:
                raise ToolError("Which day(s) of the month? e.g. 1 and 15.")
            value = {"kind": "monthly", "days": days}
        else:
            raise ToolError("Payday: weekly or biweekly (with a recent payday's date), or monthly (with the days).")
        self.memory.set(PAYDAY_KEY, json.dumps(value))
        return "Saved your paydays."

    def paydays(self, start: date, end: date) -> list[date]:
        p = self._json(PAYDAY_KEY, {})
        out = []
        if p.get("kind") in ("weekly", "biweekly"):
            step = 7 if p["kind"] == "weekly" else 14
            d = _day(p["anchor"])
            while d > start:
                d -= timedelta(days=step)
            while d <= end:
                if d >= start:
                    out.append(d)
                d += timedelta(days=step)
        elif p.get("kind") == "monthly":
            y, m = start.year, start.month
            while date(y, m, 1) <= end:
                for day in p["days"]:
                    d = date(y, m, min(day, calendar.monthrange(y, m)[1]))
                    if start <= d <= end:
                        out.append(d)
                y, m = (y + 1, 1) if m == 12 else (y, m + 1)
        return sorted(out)

    def set_autopay(self, card, mode: str) -> str:
        if mode not in ("full", "minimum", "none"):
            raise ToolError("Autopay: full (statement balance), minimum, or none.")
        c = self._card(card)
        auto = self._json(AUTOPAY_KEY, {})
        auto[str(c["id"])] = mode
        self.memory.set(AUTOPAY_KEY, json.dumps(auto))
        return f"Noted: {c['name']} autopay {mode}."

    def payment_setup(self) -> list[dict]:
        """For each card: autopay, and whether the due date sits at least 3 days after a payday."""
        today = local_now().date()
        auto = self._json(AUTOPAY_KEY, {})
        have_paydays = bool(self._json(PAYDAY_KEY, {}))
        out = []
        for c in self.fin.cards():
            item = {"card": c["name"], "autopay": auto.get(str(c["id"]), "unknown"), "next_due": c["next_due"]}
            if have_paydays and c["next_due"]:
                due = _day(c["next_due"])
                before = [p for p in self.paydays(due - timedelta(days=16), due)]
                gap = (due - before[-1]).days if before else None
                item["days_after_payday"] = gap
                if gap is None or gap < 3:
                    after = [p for p in self.paydays(due - timedelta(days=16), due + timedelta(days=31)) if p > today]
                    better = (after[0] + timedelta(days=5)) if after else None
                    item["suggest"] = (f"Ask {c['name']} to move the due date to around day {better.day} of the month "
                                       "(most issuers let you change it online), about 5 days after a payday."
                                       if better else "Due date falls right before payday.")
            if item["autopay"] in ("unknown", "none", "minimum"):
                item["autopay_tip"] = "Set autopay to the full statement balance so you never pay interest or a late fee."
            out.append(item)
        return out

    # --- goals -------------------------------------------------------------------------------
    def add_goal(self, kind: str, name: str = "", target: float | None = None, card=None, due: str = "",
                 current: float | None = None, currency: str = "USD") -> str:
        if kind not in ("emergency_fund", "pay_off", "save"):
            raise ToolError("Goal kind: emergency_fund, pay_off or save.")
        card_id = self._card(card)["id"] if kind == "pay_off" else None
        if kind == "emergency_fund":
            target = target or 3  # months of spending
        if kind == "save" and not target:
            raise ToolError("How much is the goal?")
        if not name:
            name = (f"Emergency fund ({target:g} months)" if kind == "emergency_fund" else
                    f"Pay off {self._card(card)['name']}" if kind == "pay_off" else "Savings goal")
        now = datetime.now(timezone.utc).isoformat()
        self.memory.db.execute("INSERT INTO fin_goals (kind, name, target, current, card_id, due, currency, created_at, "
                               "updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
                               (kind, name[:80], target, current, card_id, due[:10] or None, currency.upper()[:3], now, now))
        self.memory.db.commit()
        return f"Goal saved: {name}."

    def update_goal(self, what, current: float) -> str:
        row = self._goal(what)
        self.memory.db.execute("UPDATE fin_goals SET current = ?, updated_at = ? WHERE id = ?",
                               (float(current), datetime.now(timezone.utc).isoformat(), row["id"]))
        self.memory.db.commit()
        return f"Updated {row['name']}."

    def remove_goal(self, what) -> str:
        row = self._goal(what)
        self.memory.db.execute("DELETE FROM fin_goals WHERE id = ?", (row["id"],))
        self.memory.db.commit()
        return f"Removed {row['name']}."

    def _goal(self, what):
        what = str(what).strip().lower()
        rows = self.memory.db.execute("SELECT * FROM fin_goals").fetchall()
        row = next((r for r in rows if str(r["id"]) == what), None) or next(
            (r for r in rows if what and (what in r["name"].lower() or what in r["kind"])), None)
        if not row:
            raise ToolError("No goal like that.")
        return row

    def monthly_spending(self, currency: str = "USD") -> float:
        """Average spending a month over the last three full months."""
        today = local_now().date()
        first = today.replace(day=1)
        start = (first - timedelta(days=1)).replace(day=1)
        for _ in range(2):
            start = (start - timedelta(days=1)).replace(day=1)
        total = self.memory.db.execute("SELECT COALESCE(SUM(amount), 0), MIN(day) FROM transactions WHERE day >= ? "
                                       "AND day < ? AND amount > 0 AND currency = ?",
                                       (start.isoformat(), first.isoformat(), currency)).fetchone()
        if not total[1]:
            return 0.0
        months = max(1, round((first - max(start, _day(total[1]))).days / 30.4))
        return round(total[0] / months, 2)

    def savings(self) -> float | None:
        try:
            rows = self.memory.db.execute("SELECT name, balance FROM bank_accounts WHERE balance > 0").fetchall()
        except Exception:
            return None
        found = [r["balance"] for r in rows if re.search(r"(?i)saving|reserve|money market|emergency", r["name"])]
        return round(sum(found), 2) if found else None

    def goals(self) -> list[dict]:
        out = []
        for g in self.memory.db.execute("SELECT * FROM fin_goals ORDER BY id"):
            item = {"id": g["id"], "kind": g["kind"], "name": g["name"], "due": g["due"] or "", "currency": g["currency"]}
            if g["kind"] == "emergency_fund":
                monthly = self.monthly_spending(g["currency"])
                target = round(monthly * g["target"], 2) if monthly else None
                have = g["current"] if g["current"] is not None else self.savings()
                item.update(target=target, have=have, months_target=g["target"],
                            months_covered=round(have / monthly, 1) if have is not None and monthly else None)
            elif g["kind"] == "pay_off":
                card = next((c for c in self.fin.cards() if c["id"] == g["card_id"]), None)
                item.update(card=card["name"] if card else "", left=self.fin.balance_now(card) if card else None)
            else:
                item.update(target=g["target"], have=g["current"])
            if item.get("target") and item.get("have") is not None:
                item["percent"] = min(100, round(100 * item["have"] / item["target"]))
                if g["due"] and item["have"] < item["target"]:
                    months_left = max(1, round((_day(g["due"]) - local_now().date()).days / 30.4))
                    item["save_a_month"] = round((item["target"] - item["have"]) / months_left, 2)
            out.append(item)
        return out

    # --- everything together -------------------------------------------------------------------
    def income(self) -> dict:
        return self._json(INCOME_KEY, {})

    def set_income(self, amount: float, currency: str = "USD") -> str:
        self.memory.set(INCOME_KEY, json.dumps({"amount": float(amount), "currency": currency.upper()[:3]}))
        return "Saved your monthly take-home pay."

    def advice(self) -> dict:
        """The whole picture as numbers, for Claude to turn into a few clear suggestions."""
        self.apply_presets()
        spend = self.monthly_spending()
        inc = self.income()
        debts = self.debts()
        return {"monthly_spending_avg": spend,
                "monthly_income": inc.get("amount"),
                "left_over_a_month": round(inc["amount"] - spend, 2) if inc.get("amount") and spend else None,
                "best_card_for": self.best_cards(), "rewards": self.rewards(),
                "missed_rewards_90_days": self.missed_rewards(),
                "cards_carrying_a_balance": [d for d in debts if d["carrying_balance"]],
                "payment_setup": self.payment_setup(), "goals": self.goals(),
                "subscriptions": self.fin.report(),
                "card_credits_running_out": Benefits(self.memory).plan(), "rule": DISCLAIMER}

    def month(self, today: date | None = None) -> dict:
        today = today or local_now().date()
        end = today.replace(day=1) - timedelta(days=1)
        start = end.replace(day=1)
        rows = self.memory.db.execute(
            "SELECT category, SUM(amount) AS total FROM transactions WHERE day BETWEEN ? AND ? AND amount > 0 "
            "AND currency = 'USD' GROUP BY category ORDER BY total DESC", (start.isoformat(), end.isoformat())).fetchall()
        spent = round(sum(r["total"] for r in rows), 2)
        interest = self.memory.db.execute(
            "SELECT COALESCE(SUM(amount), 0) FROM transactions WHERE day BETWEEN ? AND ? AND "
            "(lower(merchant) LIKE '%interest%' OR lower(merchant) LIKE '%finance charge%' OR lower(merchant) LIKE '%late fee%')",
            (start.isoformat(), end.isoformat())).fetchone()[0]
        inc = self.income()
        missed = self.missed_rewards(days=(end - start).days + 1)
        todo = []
        if interest:
            todo.append(f"Interest and fees cost {interest:,.2f} last month: pay statements in full (autopay full balance).")
        for p in self.payment_setup():
            if p.get("autopay_tip") and p["autopay"] == "unknown":
                todo.append(f"Check {p['card']} has autopay set to the full statement balance, and tell me.")
                break
        for p in self.payment_setup():
            if p.get("suggest"):
                todo.append(p["suggest"])
        if missed:
            m = missed[0]
            todo.append(f"Use {m['best_card']} for {m['category']}: about {m['missed_about']:,.2f} more back last month.")
        for g in self.goals():
            if g.get("save_a_month"):
                todo.append(f"{g['name']}: put aside about {g['save_a_month']:,.2f} this month to stay on track.")
        subs = self.fin.report()
        if subs["not_decided"]:
            todo.append(f"Decide on {len(subs['not_decided'])} subscriptions (Money screen).")
        return {"month": start.strftime("%B %Y"), "spent": spent, "by_category": {r["category"]: round(r["total"], 2)
                                                                                  for r in rows},
                "income": inc.get("amount"), "left_over": round(inc["amount"] - spent, 2) if inc.get("amount") else None,
                "interest_and_fees": round(interest, 2), "missed_rewards": missed, "goals": self.goals(),
                "subscriptions": subs["totals"], "todo": todo[:5]}

    def month_markdown(self, m: dict) -> str:
        lines = [f"# Money check-in: {m['month']}", ""]
        if m["todo"]:
            lines += ["## This month"] + [f"- {t}" for t in m["todo"]] + [""]
        lines.append("## Last month")
        lines.append(f"Spent {m['spent']:,.2f} USD" + (f" of {m['income']:,.2f} take-home, {m['left_over']:,.2f} left over"
                                                       if m["income"] else ""))
        if m["by_category"]:
            lines.append("By category: " + ", ".join(f"{k} {v:,.2f}" for k, v in m["by_category"].items()))
        if m["interest_and_fees"]:
            lines.append(f"Interest and fees: {m['interest_and_fees']:,.2f}")
        if m["goals"]:
            lines += ["", "## Goals"]
            for g in m["goals"]:
                if g["kind"] == "pay_off":
                    lines.append(f"- {g['name']}: {g['left'] or 0:,.2f} left on {g['card']}")
                else:
                    have = f"{g['have']:,.2f}" if g.get("have") is not None else "unknown (tell me)"
                    target = f"{g['target']:,.2f}" if g.get("target") else "?"
                    lines.append(f"- {g['name']}: {have} of {target}" + (f" ({g['percent']}%)" if "percent" in g else ""))
        if m["missed_rewards"]:
            lines += ["", "## Rewards missed"] + [
                f"- {x['category']}: {x['spent']:,.2f} on {x['on']} ({x['earned_rate']:g}x); {x['best_card']} gives "
                f"{x['best_rate']:g}x, about {x['missed_about']:,.2f} more" for x in m["missed_rewards"][:5]]
        if m["subscriptions"]:
            lines += ["", "## Subscriptions"] + [f"{t['monthly']:,.2f} {c} a month ({t['count']})"
                                                 for c, t in m["subscriptions"].items()]
        lines += ["", f"_{DISCLAIMER}_"]
        return "\n".join(lines)

    def save_month(self, today: date | None = None) -> tuple[int, str]:
        from .jobs import SCHEMA as JOBS_SCHEMA

        m = self.month(today)
        summary = (f"Your {m['month']} check-in: you spent {m['spent']:,.0f} dollars"
                   + (f", {m['left_over']:,.0f} left over from your pay" if m["left_over"] is not None else "") + ". "
                   + (m["todo"][0] + " " if m["todo"] else "") + "The full check-in is in Reports.")
        now = datetime.now(timezone.utc).isoformat()
        self.memory.db.executescript(JOBS_SCHEMA)
        purge_reports(self.memory)
        job_id = self.memory.db.execute(
            "INSERT INTO jobs (kind, title, params, status, created_at, started_at, finished_at, summary, report, findings, "
            "sources, delivered) VALUES ('money_monthly', ?, '{}', 'done', ?, ?, ?, ?, ?, ?, '[]', 1)",
            (f"Money check-in, {m['month']}", now, now, now, summary, self.month_markdown(m), json.dumps(m["todo"]))
        ).lastrowid
        self.memory.db.commit()
        return job_id, summary


def monthly_due(memory: Memory) -> bool:
    if not MONTHLY_AT.strip():
        return False
    try:
        day, at = MONTHLY_AT.split()
        hour, minute = map(int, at.split(":"))
    except ValueError:
        return False
    now = local_now()
    if now.day != int(day) or (now.hour, now.minute) < (hour, minute):
        return False
    return memory.get("finance:monthly") != now.strftime("%Y-%m") and has_money_data(memory)


ADVISOR_TOOL = {
    "name": "money_advisor",
    "description": "Advice from the user's own numbers (worked out on the server; you word it, briefly, and never "
    "move money). advice: the whole picture (spending vs income, best card per category, rewards missed in 90 days, "
    "cards carrying a balance, autopay and due dates vs paydays, goals, subscriptions). best_card (what: a category "
    "or shop: which card to use). rewards: each card's earn rates; set_reward (card, category, rate) when the user "
    "tells you; confirm_rewards (card) once they check a preset. payoff (amount a month: highest APR first vs "
    "smallest balance first, months and interest). payday (kind weekly|biweekly + anchor date, or monthly + days). "
    "income (amount a month after tax). autopay (card, mode full|minimum|none). goal_add (kind emergency_fund "
    "[target = months] | pay_off [card] | save [target, due]), goal_update (what, current), goal_remove (what), "
    "goals. monthly: make this month's check-in report now. review (period last_month | this_month | last_3_months | "
    "last_6_months | this_year | last_12_months): the detailed money review saved in Reports (where the money went "
    "against the usual, by category and type, trend, subscriptions, fees, cards, rewards, ways to save with yearly "
    "amounts, a suggested budget); say the headline and the top two ways to save.",
    "input_schema": {"type": "object", "properties": {
        "action": {"type": "string", "enum": ["advice", "best_card", "rewards", "set_reward", "confirm_rewards",
                                              "payoff", "payday", "income", "autopay", "goal_add", "goal_update",
                                              "goal_remove", "goals", "monthly", "review"]},
        "period": {"type": "string", "enum": ["last_month", "this_month", "last_3_months", "last_6_months",
                                               "this_year", "last_12_months"]},
        "what": {"type": "string"}, "card": {"type": "string"},
        "category": {"type": "string", "enum": list(REWARD_CATEGORIES)}, "rate": {"type": "number"},
        "amount": {"type": "number"}, "currency": {"type": "string"},
        "kind": {"type": "string", "enum": ["weekly", "biweekly", "monthly", "emergency_fund", "pay_off", "save"]},
        "anchor": {"type": "string", "description": "a recent payday, YYYY-MM-DD"},
        "days": {"type": "array", "items": {"type": "integer"}},
        "mode": {"type": "string", "enum": ["full", "minimum", "none"]},
        "name": {"type": "string"}, "target": {"type": "number"}, "current": {"type": "number"},
        "due": {"type": "string"}},
        "required": ["action"], "additionalProperties": False},
}


def run_tool(adv: Advisor, args: dict) -> str:
    a = args.get("action")
    if a == "advice":
        return json.dumps(adv.advice(), ensure_ascii=False)
    if a == "best_card":
        return json.dumps(adv.best_for(str(args.get("what") or args.get("category") or "")))
    if a == "rewards":
        adv.apply_presets()
        found = adv.rewards()
        return json.dumps(found) if found else "No cards yet."
    if a == "set_reward":
        return adv.set_reward(args.get("card") or "", str(args.get("category") or ""), float(args.get("rate") or 0))
    if a == "confirm_rewards":
        return adv.confirm_rewards(args.get("card") or "")
    if a == "payoff":
        return json.dumps(adv.payoff_plan(float(args.get("amount") or 0)), ensure_ascii=False)
    if a == "payday":
        return adv.set_payday(str(args.get("kind") or ""), str(args.get("anchor") or ""), args.get("days"))
    if a == "income":
        return adv.set_income(float(args.get("amount") or 0), str(args.get("currency") or "USD"))
    if a == "autopay":
        return adv.set_autopay(args.get("card") or "", str(args.get("mode") or ""))
    if a == "goal_add":
        return adv.add_goal(str(args.get("kind") or ""), str(args.get("name") or ""), args.get("target"),
                            args.get("card"), str(args.get("due") or ""), args.get("current"),
                            str(args.get("currency") or "USD"))
    if a == "goal_update":
        return adv.update_goal(args.get("what") or "", float(args.get("current") or 0))
    if a == "goal_remove":
        return adv.remove_goal(args.get("what") or "")
    if a == "goals":
        return json.dumps(adv.goals()) if adv.goals() else "No goals yet."
    if a == "review":
        from .review import Review  # here: review imports this module
        job_id, summary, r = Review(adv.memory).save(str(args.get("period") or "last_month"))
        return json.dumps({"saved_report": job_id, "summary": summary, "top_ways_to_save": r["todo"][:3],
                           "possible_yearly_savings": r["possible_yearly_savings"]}, ensure_ascii=False)
    if a == "monthly":
        job_id, summary = adv.save_month()
        return f"{summary} (Report {job_id} saved in Reports.)"
    raise ToolError("Unknown advisor action.")
