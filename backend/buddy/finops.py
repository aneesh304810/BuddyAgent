"""FinOps: what running Buddy costs, all in, this month.

Each provider is one cost line with a month-to-date figure, an end-of-month
forecast and where the number came from:

  Claude        Buddy's own meter of every call (always), or Anthropic's cost
                report when an Admin API key is set (organizations only)
  Server        DigitalOcean's live month-to-date bill (read-only API token), or the
                monthly price of whatever host you use; no line if neither is set
  Zapier        the plan price, plus Buddy's own count of Zapier calls against the
                plan's task allowance (Zapier has no billing API)
  Plaid         production banks connected x your per-bank monthly price, plus priced
                per-call products (Plaid has no billing API); sandbox is free
  AI models     speech to text (Groq) and the free-tier models (Gemini, OpenRouter, GitHub
                Models...): Buddy's meter of every call
  Outside APIs  web search (Serper), flight and hotel prices (SerpApi, Duffel, Travelpayouts):
                every call logged with its price
  Free services the ones Buddy uses at no charge (Tailscale, Cloudflare, GitHub, OpenStreetMap, Hacker News,
                arXiv, StockTwits, Reddit, Alpaca's free data, Gemini and OpenRouter free models), listed so the
                picture is complete
  Not set       a service that costs money but whose price Buddy can't see (server, Plaid, the domain) shows
                "price not set" with the one setting that fills it in
  Fixed costs   anything else you pay monthly (BUDDY_FIXED_COSTS)

Network answers are cached for 30 minutes; the providers ask not to be polled hard.
"""

import asyncio
import calendar
import contextlib
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
from .tools import local_now, require_https

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
    request = urllib.request.Request(require_https(url), headers={"User-Agent": "Buddy/1.0", **headers})
    with urllib.request.urlopen(request, timeout=30) as response:  # https only (require_https)  # nosec B310
        return json.loads(response.read())


def _money(value) -> float:
    try:
        return round(float(value), 2)
    except (TypeError, ValueError):
        return 0.0


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

    async def server(self) -> CostLine | None:
        """The machine Buddy runs on: DigitalOcean's live bill, or any host's monthly price."""
        token = self.setting("BUDDY_DO_TOKEN")
        name = self.setting("BUDDY_SERVER_PROVIDER", "DigitalOcean" if token else "Server")
        price = self.setting("BUDDY_COST_SERVER_USD")
        if token:
            try:
                data = await asyncio.to_thread(
                    self.fetch, "https://api.digitalocean.com/v2/customers/my/balance",
                    {"Authorization": f"Bearer {token}"},
                )
                mtd = float(data.get("month_to_date_usage", 0) or 0)
                note = "droplet and anything else on the account"
                balance = float(data.get("account_balance", 0) or 0)
                if balance < 0:  # DigitalOcean shows unused credit as a negative balance
                    note += f"; ${-balance:.2f} of credit left, used before your card is charged"
                return CostLine(name, round(mtd, 2), max(_project(mtd), mtd), "live", note)
            except Exception as e:
                log.warning("DigitalOcean balance failed: %s", e)
                note = f"live bill unavailable ({type(e).__name__}); showing the plan price"
        elif not price:
            return CostLine("Server (DigitalOcean)", 0.0, 0.0, "price not set",
                            "the machine Buddy runs on. For the live bill add a read-only DigitalOcean token "
                            "(set.sh BUDDY_DO_TOKEN), or its monthly price (set.sh BUDDY_COST_SERVER_USD)")
        else:
            note = "monthly price you set"
        plan = float(price or 0)
        fraction, _ = _month_progress()
        return CostLine(name, round(plan * fraction, 2), plan, "plan price", note)

    async def digitalocean_invoices(self) -> dict | None:
        """Last month's invoice total and both months by product (droplets, backups...)."""
        token = self.setting("BUDDY_DO_TOKEN")
        if not token:
            return None
        headers = {"Authorization": f"Bearer {token}"}
        base = "https://api.digitalocean.com/v2/customers/my/invoices"
        try:
            listing = await asyncio.to_thread(self.fetch, base, headers)
        except Exception as e:
            log.warning("DigitalOcean invoices failed: %s", e)
            return {"error": f"invoices unavailable ({type(e).__name__})"}

        async def month(invoice: dict | None) -> dict | None:
            if not invoice or not invoice.get("invoice_uuid"):
                return None
            out = {"period": invoice.get("invoice_period", ""), "total": _money(invoice.get("amount"))}
            try:
                summary = await asyncio.to_thread(self.fetch, f"{base}/{invoice['invoice_uuid']}/summary", headers)
            except Exception:
                return out  # the total alone still helps
            items = (summary.get("product_charges") or {}).get("items") or []
            out["by_product"] = [{"product": i.get("name", ""), "amount": _money(i.get("amount"))}
                                 for i in items if _money(i.get("amount"))]
            for key, label in (("taxes", "taxes"), ("credits_and_adjustments", "credits")):
                amount = _money((summary.get(key) or {}).get("amount"))
                if amount:
                    out[label] = amount
            return out

        invoices = sorted(listing.get("invoices") or [], key=lambda i: i.get("invoice_period", ""), reverse=True)
        result = {"last_month": await month(invoices[0] if invoices else None),
                  "this_month_so_far": await month(listing.get("invoice_preview"))}
        if len(invoices) > 1:
            result["month_before"] = {"period": invoices[1].get("invoice_period", ""),
                                      "total": _money(invoices[1].get("amount"))}
        return result

    def routing(self) -> dict:
        """This month: turns answered by the quick or main Claude model, and free-model calls."""
        month = local_now().strftime("%Y-%m")
        counts = {}
        for r in self.memory.db.execute("SELECT key, value FROM kv WHERE key LIKE 'count:%' AND key LIKE ?",
                                        (f"%:{month}",)):
            _, group, name, _ = r["key"].split(":", 3) if r["key"].count(":") >= 3 else (None, "", "", "")
            if group in ("router", "free"):
                counts.setdefault(group, {})[name] = int(float(r["value"]))
        return {"claude_turns": counts.get("router", {}), "free_model_calls": counts.get("free", {})}

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
            return CostLine("Zapier", 0.0, 0.0, "free", "not in use: no Zapier calls and no plan price set "
                            "(set.sh BUDDY_ZAPIER_PLAN_USD if you're on a paid plan)")
        tasks = calls * ZAPIER_TASKS_PER_CALL
        fraction, _ = _month_progress()
        return CostLine("Zapier", round(plan * fraction, 2), plan, "plan price",
                        f"about {tasks} of {allowance} tasks used in the last 30 days ({calls} calls)")

    def _month_start(self) -> str:
        first = local_now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        return first.astimezone(timezone.utc).isoformat()

    def _api_calls(self) -> dict[str, dict]:
        """This month's outside API calls by provider: calls, failures, priced cost, endpoints."""
        out: dict[str, dict] = {}
        try:
            rows = self.memory.db.execute(
                "SELECT provider, purpose, endpoint, ok, cost_usd FROM api_calls WHERE at >= ?", (self._month_start(),))
        except Exception:
            return out
        for r in rows:
            e = out.setdefault(r["provider"], {"calls": 0, "failed": 0, "cost": 0.0, "sandbox": 0, "endpoints": {}})
            e["calls"] += 1
            e["failed"] += 0 if r["ok"] else 1
            e["cost"] += float(r["cost_usd"] or 0)
            e["sandbox"] += 1 if r["purpose"] == "sandbox" else 0
            e["endpoints"][r["endpoint"]] = e["endpoints"].get(r["endpoint"], 0) + 1
        return out

    def plaid(self, calls: dict | None = None) -> CostLine | None:
        """Plaid bills Transactions and Liabilities per connected bank (Item) a month, and some products per
        call. There's no billing API: Buddy counts the banks and calls and uses your prices from the Plaid
        dashboard (Plans page; actual usage on the Production Usage page). Subscriptions aren't pro-rated: a bank
        connected on any day of the month is billed the whole month."""
        try:
            rows = self.memory.db.execute("SELECT env FROM plaid_items").fetchall()
        except Exception:
            rows = []
        live = sum(1 for r in rows if (r["env"] or "production") != "sandbox")
        sandbox = len(rows) - live
        info = (calls or {}).get("plaid", {"calls": 0, "cost": 0.0, "sandbox": 0})
        if not rows and not info["calls"]:
            return None
        per_bank = self.setting("BUDDY_PLAID_COST_PER_BANK_USD")
        fraction, _ = _month_progress()
        monthly = float(per_bank or 0) * live
        mtd = monthly + info["cost"]  # not pro-rated: the month's subscription is owed from its first day
        note = (f"{live} bank{'s' if live != 1 else ''} connected (production)"
                + (f", {sandbox} sandbox (free)" if sandbox else "") + f"; {info['calls']} calls this month")
        if per_bank:
            note += f"; ${float(per_bank):.2f} per bank a month"
            source = "your price"
        else:
            note += (". Add your per-bank price (Transactions + Liabilities a month, from the Plaid dashboard's Plans "
                     "page; 0 on the free Trial plan): BUDDY_PLAID_COST_PER_BANK_USD")
            source = "price not set"
        return CostLine("Plaid", round(mtd, 2), round(monthly + _project(info["cost"]), 2), source, note)

    API_NAMES = {"serper": "Serper (web search, Google News)", "serpapi": "SerpApi (Google Flights, Hotels, Trends)",
                 "duffel": "Duffel (flight offers)", "travelpayouts": "Travelpayouts (cheapest dates)",
                 "alpaca": "Alpaca (market data)", "reddit": "Reddit", "stocktwits": "StockTwits",
                 "hackernews": "Hacker News (Algolia search)", "arxiv": "arXiv", "github": "GitHub search",
                 "coinbase": "Coinbase (crypto prices)", "stooq": "Stooq (daily prices)",
                 "yahoo": "Yahoo Finance (Indian stocks)", "openrouter_list": "OpenRouter model list",
                 "mta": "MTA alerts (subway, buses, LIRR, Metro-North)", "path": "PATH (arrivals and alerts)",
                 "njt": "NJ Transit (train and light rail alerts)", "airlabs": "AirLabs (flight status)",
                 "flightaware": "FlightAware AeroAPI (flight status backup)", "openmeteo": "Open-Meteo (weather)",
                 "17track": "17TRACK (package tracking)"}
    # Free APIs: shown as free whatever they're called (no per-call price).
    FREE_APIS = {"alpaca": "free market data plan (paper account)", "reddit": "free public search",
                 "stocktwits": "free public API", "hackernews": "free, no key", "arxiv": "free public API",
                 "github": "free public search", "coinbase": "free public prices", "stooq": "free daily prices",
                 "yahoo": "free public chart data", "mta": "free, no key", "path": "free public feed",
                 "njt": "free developer account", "airlabs": "free plan",
                 "openmeteo": "free, no key", "17track": "free plan"}

    def outside_apis(self, calls: dict) -> list[CostLine]:
        lines = []
        for name, e in sorted(calls.items()):
            if name == "plaid":
                continue
            note = f"{e['calls']} calls this month" + (f", {e['failed']} failed" if e["failed"] else "")
            if name in self.FREE_APIS and not e["cost"]:
                lines.append(CostLine(self.API_NAMES.get(name, name.title()), 0.0, 0.0, "free",
                                      f"{note}; {self.FREE_APIS[name]}"))
                continue
            if not e["cost"]:
                note += "; free tier or no per-call price set"
            lines.append(CostLine(self.API_NAMES.get(name, name.title()), round(e["cost"], 2), _project(e["cost"]),
                                  "buddy meter", note))
        return lines

    MODEL_NAMES = {"groq": "Groq (speech to text and quick models)", "gemini": "Google Gemini",
                   "openrouter": "OpenRouter", "github": "GitHub Models", "deepseek": "DeepSeek (OpenRouter)",
                   "ollama": "Local models"}

    def other_models(self) -> list[CostLine]:
        """AI providers other than Claude (Claude is its own line), from Buddy's meter of every call."""
        try:
            rows = self.memory.db.execute(
                "SELECT provider, COUNT(*) AS n, COALESCE(SUM(cost_usd), 0) AS cost, "
                "SUM(CASE WHEN purpose = 'speech' THEN 1 ELSE 0 END) AS speech FROM model_calls "
                "WHERE at >= ? AND provider != 'claude' GROUP BY provider", (self._month_start(),)).fetchall()
        except Exception:
            return []
        lines = []
        for r in rows:
            note = f"{r['n']} calls this month" + (f" ({r['speech']} speech to text)" if r["speech"] else "")
            if not r["cost"]:
                note += "; free tier"
            lines.append(CostLine(self.MODEL_NAMES.get(r["provider"], r["provider"].title()), round(r["cost"], 2),
                                  _project(r["cost"]), "buddy meter" if r["cost"] else "free", note))
        return lines

    FREE = [("Tailscale", "private network between your phone and the server", "Personal plan"),
            ("Cloudflare", "buddylabs.io website (Pages), waitlist and email routing", "free plan"),
            ("GitHub", "code, app builds (Actions) and security scans", "free plan"),
            ("OpenStreetMap", "addresses for places and rides", "free, with attribution"),
            ("Hacker News (Algolia search)", "trends and the News tab", "free, no key"),
            ("arXiv", "new AI papers for trends", "free public API"),
            ("StockTwits", "trending tickers and chatter", "free public API"),
            ("Reddit", "what people are saying (trends, News, markets)", "free public search; Google as fallback"),
            ("Alpaca (market data)", "stock, crypto and option prices, market news", "free plan (paper account)"),
            ("Google Gemini", "free AI model: general answers, images, decks", "free tier (AI Studio key)"),
            ("OpenRouter", "free AI models, picked per task daily", "free models only (:free)"),
            ("MTA alerts (subway, buses, LIRR, Metro-North)", "commute alerts", "free, no key"),
            ("PATH (arrivals and alerts)", "commute alerts and next trains", "free public feed"),
            ("NJ Transit (train and light rail alerts)", "commute alerts", "free developer account"),
            ("AirLabs (flight status)", "your flights: delays, gates, landing", "free plan (about 1,000 calls a month)"),
            ("Open-Meteo (weather)", "forecasts here and at trip destinations", "free, no key"),
            ("17TRACK (package tracking)", "live package status (optional)", "free plan")]
    # FlightAware AeroAPI is pay per query: it shows as calls this month (price per your FlightAware plan).

    def free_services(self, named: set[str]) -> list[CostLine]:
        """Services Buddy relies on at no charge; a line already shown (a fixed cost, or one with calls this month)
        replaces its entry here."""
        return [CostLine(name, 0.0, 0.0, "free", f"{what}; {plan}") for name, what, plan in self.FREE
                if name.lower() not in named]

    def domain(self, named: set[str]) -> list[CostLine]:
        """The website's domain renews once a year: shown until its price is added as a fixed cost."""
        if any("domain" in n for n in named):
            return []
        return [CostLine("Domain (buddylabs.io)", 0.0, 0.0, "price not set",
                         "renews yearly at your registrar; add its price per month, e.g. set.sh BUDDY_FIXED_COSTS "
                         "with Domain=1.25 (a $15 a year domain)")]

    def fixed(self) -> list[CostLine]:
        fraction, _ = _month_progress()
        return [CostLine(name, round(amount * fraction, 2), amount, "fixed")
                for name, amount in _parse_fixed(self.setting("BUDDY_FIXED_COSTS"))]

    # --- the report --------------------------------------------------------
    async def report(self, fresh: bool = False) -> dict:
        if not fresh and self._cache and time.time() - self._cache[0] < CACHE_SECONDS:
            return self._cache[1]
        calls = self._api_calls()
        lines = [await self.claude()]
        lines += [line for line in (await self.server(), self.zapier(), self.plaid(calls)) if line]
        lines += self.other_models() + self.outside_apis(calls)
        fixed = self.fixed()
        lines += fixed
        named = {line.provider.lower() for line in lines}
        lines += self.domain(named) + self.free_services(named)
        with contextlib.suppress(Exception):  # the whole-Buddy switch counts committed costs in full (budgets.py)
            from .budgets import save_committed  # noqa: PLC0415
            save_committed(self.memory, lines)
        mtd = round(sum(l.month_to_date for l in lines), 2)
        forecast = round(sum(l.forecast for l in lines), 2)
        try:  # set in the app or by voice (budgets.py), else BUDDY_ALLIN_BUDGET_USD
            from .budgets import all_in_budget  # noqa: PLC0415
            budget = all_in_budget(self.memory) if self.memory.get("budget:all_in_usd") else \
                float(self.setting("BUDDY_ALLIN_BUDGET_USD", "0"))
        except Exception:
            budget = float(self.setting("BUDDY_ALLIN_BUDGET_USD", "0"))
        _, days = _month_progress()
        report = {
            "month": local_now().strftime("%B %Y"),
            "month_to_date_usd": mtd,
            "forecast_usd": forecast,
            "forecast_yearly_usd": round(forecast * 12, 2),
            "price_not_set": [line.provider for line in lines if line.source == "price not set"],
            "all_in_budget_usd": budget or None,
            "over_budget": bool(budget and forecast > budget),
            "lines": [asdict(l) for l in lines],
            "claude_by_purpose": self.claude_by_purpose(),
            "agents": self.by_agent(),
            "digitalocean_invoices": await self.digitalocean_invoices(),
            "routing": self.routing(),
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

    def by_agent(self) -> list[dict]:
        """Each agent's calls this month and what they cost outside Claude, where known.

        Claude's own tokens for agent work are already in claude_by_purpose (chat)."""
        month = local_now().strftime("%Y-%m")
        try:
            rows = self.memory.db.execute(
                "SELECT agent, COUNT(*) AS calls FROM audit WHERE status IN ('ok', 'approved') "
                "AND ts LIKE ? GROUP BY agent ORDER BY calls DESC", (f"{month}%",)
            ).fetchall()
        except Exception:
            return []
        zapier_plan = float(self.setting("BUDDY_ZAPIER_PLAN_USD", "0"))
        out = []
        for row in rows:
            entry = {"agent": row["agent"], "calls_this_month": row["calls"], "external_cost_usd": 0.0, "note": ""}
            if row["agent"] == "zapier":
                entry["external_cost_usd"] = round(zapier_plan * _month_progress()[0], 2)
                entry["note"] = f"about {row['calls'] * ZAPIER_TASKS_PER_CALL} Zapier tasks"
            elif row["agent"] == "gmail":
                entry["note"] = "free (your Gmail)"
            out.append(entry)
        return out

    def spoken_summary(self, report: dict) -> str:
        line = f"Buddy has cost ${report['month_to_date_usd']:.2f} so far this month, heading for about ${report['forecast_usd']:.0f}"
        if report["all_in_budget_usd"]:
            line += (", over your all-in budget" if report["over_budget"]
                     else f", within your ${report['all_in_budget_usd']:.0f} budget")
        line += "."
        last = (report.get("digitalocean_invoices") or {}).get("last_month")
        if last and last.get("total"):
            line += f" Last month's server invoice was ${last['total']:.2f}."
        return line


COST_TOOL = {
    "name": "cost_report",
    "description": "What running Buddy costs this month, all in, service by service: Claude, the server "
    "(DigitalOcean), Zapier, Plaid (banks connected and calls), speech to text and other AI models, web search and "
    "travel price APIs, fixed costs, and the free services Buddy uses, each with month-to-date, end-of-month forecast and where the number "
    "comes from, plus where Claude money went (chat, Listen-in, expert, research), and DigitalOcean's "
    "invoices: last month's total and both months by product (droplets, backups...). Use for 'how much "
    "do you cost?', 'what's my bill?', 'what was last month's server bill?'. spending_status is only today's Claude allowances.",
    "input_schema": {
        "type": "object",
        "properties": {"fresh": {"type": "boolean", "description": "Skip the 30-minute cache"}},
        "additionalProperties": False,
    },
}
