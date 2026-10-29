"""FinOps: what running Buddy costs, all in, this month.

Each provider is one cost line with a month-to-date figure, an end-of-month
forecast and where the number came from:

  Claude        Buddy's own meter of every call (always), or Anthropic's cost
                report when an Admin API key is set (organizations only)
  DigitalOcean  the live month-to-date bill (read-only API token), or the plan price
  Zapier        the plan price, plus Buddy's own count of Zapier calls against the
                plan's task allowance (Zapier has no billing API)
  Fixed costs   anything else you pay monthly (BUDDY_FIXED_COSTS)

Network answers are cached for 30 minutes; the providers ask not to be polled hard.
"""

import asyncio
import calendar
import json
import logging
import os
import time
import urllib.parse
import urllib.request
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone

from . import usage
from .memory import Memory
from .tools import local_now

log = logging.getLogger(__name__)

CACHE_SECONDS = 30 * 60
ZAPIER_TASKS_PER_CALL = 2


@dataclass
class CostLine:
    provider: str
    month_to_date: float
    forecast: float          # expected total for the whole month
    source: str              # live | buddy meter | plan price | fixed
    note: str = ""


def _month_progress() -> tuple[float, int]:
    """(fraction of the month gone, days in month), in the user's time zone."""
    now = local_now()
    days = calendar.monthrange(now.year, now.month)[1]
    elapsed = (now.day - 1) + (now.hour * 3600 + now.minute * 60 + now.second) / 86400
    return max(elapsed / days, 1 / (days * 24)), days


def _project(month_to_date: float) -> float:
    fraction, _ = _month_progress()
    return round(month_to_date / fraction, 2)


def _parse_fixed(spec: str) -> list[tuple[str, float]]:
    """'Backblaze=0.10, Domain=1.25' -> [("Backblaze", 0.1), ("Domain", 1.25)]"""
    items = []
    for part in spec.split(","):
        if "=" in part:
            name, amount = part.split("=", 1)
            try:
                items.append((name.strip(), float(amount.strip().lstrip("$"))))
            except ValueError:
                log.warning("ignoring fixed cost %r", part)
    return items


def _get_json(url: str, headers: dict[str, str]) -> dict:
    request = urllib.request.Request(url, headers={"User-Agent": "Buddy/1.0", **headers})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read())


class FinOps:
    def __init__(self, memory: Memory, env: Mapping[str, str] | None = None, fetch=_get_json):
        self.memory = memory
        self.env = os.environ if env is None else env
        self.fetch = fetch
        self._cache: tuple[float, dict] | None = None

    def setting(self, key: str, default: str = "") -> str:
        return (self.env.get(key) or default).strip()

    # --- providers ---------------------------------------------------------
    async def claude(self) -> CostLine:
        metered = usage.month_to_date(self.memory)
        key = self.setting("BUDDY_ANTHROPIC_ADMIN_KEY")
        if key:
            try:
                live = await asyncio.to_thread(self._anthropic_cost, key)
                return CostLine("Claude", round(live, 2), _project(live), "live",
                                f"Anthropic cost report; Buddy's own meter says ${metered:.2f}")
            except Exception as e:
                log.warning("Anthropic cost report failed: %s", e)
                return CostLine("Claude", round(metered, 2), _project(metered), "buddy meter",
                                f"Anthropic cost report unavailable ({type(e).__name__})")
        return CostLine("Claude", round(metered, 2), _project(metered), "buddy meter",
                        "every call Buddy made, at list prices")

    def _anthropic_cost(self, key: str) -> float:
        """Month-to-date USD from GET /v1/organizations/cost_report (amounts are cents)."""
        now = datetime.now(timezone.utc)
        start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        params = {"starting_at": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
                  "ending_at": (now + timedelta(days=1)).strftime("%Y-%m-%dT00:00:00Z"),
                  "limit": "31"}
        total_cents, page = 0.0, None
        for _ in range(10):
            query = dict(params, **({"page": page} if page else {}))
            data = self.fetch(
                "https://api.anthropic.com/v1/organizations/cost_report?" + urllib.parse.urlencode(query),
                {"x-api-key": key, "anthropic-version": "2023-06-01"},
            )
            for bucket in data.get("data", []):
                for result in bucket.get("results", []):
                    total_cents += float(result.get("amount", 0) or 0)
            if not data.get("has_more"):
                break
            page = data.get("next_page")
        return total_cents / 100

    async def digitalocean(self) -> CostLine:
        token = self.setting("BUDDY_DO_TOKEN")
        plan = float(self.setting("BUDDY_COST_SERVER_USD", "12"))
        if token:
            try:
                data = await asyncio.to_thread(
                    self.fetch, "https://api.digitalocean.com/v2/customers/my/balance",
                    {"Authorization": f"Bearer {token}"},
                )
                mtd = float(data.get("month_to_date_usage", 0) or 0)
                return CostLine("DigitalOcean", round(mtd, 2), max(_project(mtd), mtd), "live",
                                "droplet and anything else on the account")
            except Exception as e:
                log.warning("DigitalOcean balance failed: %s", e)
                note = f"live bill unavailable ({type(e).__name__}); showing the plan price"
        else:
            note = "plan price (add BUDDY_DO_TOKEN for the live bill)"
        fraction, _ = _month_progress()
        return CostLine("DigitalOcean", round(plan * fraction, 2), plan, "plan price", note)

    def zapier(self) -> CostLine | None:
        plan = float(self.setting("BUDDY_ZAPIER_PLAN_USD", "0"))
        allowance = int(self.setting("BUDDY_ZAPIER_TASKS_PER_MONTH", "100"))
        since = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        try:
            calls = self.memory.db.execute(
                "SELECT COUNT(*) AS n FROM audit WHERE agent = 'zapier' AND status = 'ok' AND ts >= ?",
                (since,),
            ).fetchone()["n"]
        except Exception:
            calls = 0
        if not calls and not plan:
            return None  # not in use
        tasks = calls * ZAPIER_TASKS_PER_CALL
        fraction, _ = _month_progress()
        return CostLine("Zapier", round(plan * fraction, 2), plan, "plan price",
                        f"about {tasks} of {allowance} tasks used in the last 30 days ({calls} calls)")

    def fixed(self) -> list[CostLine]:
        fraction, _ = _month_progress()
        return [CostLine(name, round(amount * fraction, 2), amount, "fixed")
                for name, amount in _parse_fixed(self.setting("BUDDY_FIXED_COSTS"))]

    # --- the report --------------------------------------------------------
    async def report(self, fresh: bool = False) -> dict:
        if not fresh and self._cache and time.time() - self._cache[0] < CACHE_SECONDS:
            return self._cache[1]
        lines = [await self.claude(), await self.digitalocean()]
        zap = self.zapier()
        if zap:
            lines.append(zap)
        lines += self.fixed()
        mtd = round(sum(l.month_to_date for l in lines), 2)
        forecast = round(sum(l.forecast for l in lines), 2)
        budget = float(self.setting("BUDDY_ALLIN_BUDGET_USD", "0"))
        _, days = _month_progress()
        report = {
            "month": local_now().strftime("%B %Y"),
            "month_to_date_usd": mtd,
            "forecast_usd": forecast,
            "all_in_budget_usd": budget or None,
            "over_budget": bool(budget and forecast > budget),
            "lines": [asdict(l) for l in lines],
            "claude_by_purpose": self.claude_by_purpose(),
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }
        self._cache = (time.time(), report)
        return report

    def claude_by_purpose(self) -> dict:
        """Where Claude money went this month: listen-in, expert, research, the rest (chat)."""
        month = local_now().strftime("%Y-%m")
        rows = self.memory.db.execute(
            "SELECT key, value FROM kv WHERE key LIKE 'spend:%' AND key LIKE ?", (f"%:{month}-%",)
        ).fetchall()
        totals: dict[str, float] = {}
        for row in rows:
            parts = row["key"].split(":")
            purpose = parts[1] if len(parts) == 3 else "all"
            totals[purpose] = totals.get(purpose, 0.0) + float(row["value"])
        try:
            research = self.memory.db.execute(
                "SELECT COALESCE(SUM(cost_usd), 0) AS s FROM jobs WHERE created_at LIKE ?", (f"{month}%",)
            ).fetchone()["s"]
        except Exception:
            research = 0.0
        named = {k: round(v, 2) for k, v in totals.items() if k != "all"}
        named["research"] = round(float(research), 2)
        named["chat_and_other"] = round(max(totals.get("all", 0.0) - sum(named.values()), 0.0), 2)
        searches = self.memory.db.execute(
            "SELECT COALESCE(SUM(CAST(value AS REAL)), 0) AS n FROM kv WHERE key LIKE ?", (f"searches:{month}-%",)
        ).fetchone()["n"]
        named["web_searches"] = {"count": int(searches), "usd_included_above": round(searches * usage.WEB_SEARCH_USD, 2)}
        return named

    def spoken_summary(self, report: dict) -> str:
        line = f"Buddy has cost ${report['month_to_date_usd']:.2f} so far this month, heading for about ${report['forecast_usd']:.0f}"
        if report["all_in_budget_usd"]:
            line += (", over your all-in budget." if report["over_budget"]
                     else f", within your ${report['all_in_budget_usd']:.0f} budget.")
        return line + "."


COST_TOOL = {
    "name": "cost_report",
    "description": "What running Buddy costs this month, all in: Claude, the DigitalOcean server, "
    "Zapier and fixed costs, each with month-to-date, end-of-month forecast and where the number "
    "comes from, plus where Claude money went (chat, Listen-in, expert, research). Use for 'how much "
    "do you cost?', 'what's my bill?'. spending_status is only today's Claude allowances.",
    "input_schema": {
        "type": "object",
        "properties": {"fresh": {"type": "boolean", "description": "Skip the 30-minute cache"}},
        "additionalProperties": False,
    },
}
