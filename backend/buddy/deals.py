"""Price watches: Buddy keeps checking a flight, hotel or product in the background
and tells the user when the price drops to their target (or by 10% or more).

Each check is one short web search by the chat model (a few cents), so watches
are capped (MAX_WATCHES), checked at most every few hours, and expire after
their days run out. Creating one asks first, with the monthly cost in words.
"""

import asyncio
import time
import json
import logging
import re
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta, timezone

from . import cards
from . import usage
from .config import claude_client, settings
from .memory import Memory
from .tools import ToolError, local_now

log = logging.getLogger(__name__)
client = claude_client()

MAX_WATCHES = 5
CHECK_COST_USD = 0.06           # one check: a few searches and a short answer
TICK_SECONDS = 10 * 60
DROP_SHARE = 0.10               # tell the user about a drop of 10% or more

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS price_watches (
    id INTEGER PRIMARY KEY,
    what TEXT NOT NULL,
    target REAL,                     -- tell me at or below this; NULL = any big drop
    currency TEXT NOT NULL,
    every_hours INTEGER NOT NULL,
    until TEXT NOT NULL,             -- UTC ISO: stops after this
    best_price REAL,                 -- lowest seen so far
    last_price REAL,
    last_where TEXT NOT NULL DEFAULT '',
    last_url TEXT NOT NULL DEFAULT '',
    last_checked TEXT,
    told_price REAL,                 -- price at the last alert, so the same deal isn't repeated
    created_at TEXT NOT NULL
);
"""

CHECK_PROMPT = """\
Find the current lowest real price for this, for {user}: {what}
Currency: {currency}. Today is {today}. Search, open the most promising offer pages, \
and use the TRUE total: taxes, booking and resort fees, baggage, shipping, whatever \
the seller adds before payment. In "note" give the biggest catch in a few words \
(non-refundable, basic economy with no bag, resort fee extra, third-party seller...). \
Finish with one line of JSON and nothing after it:
{{"found": true, "price": 123.45, "where": "seller or airline", "url": "https://...", "note": "one short catch"}}
or {{"found": false, "price": 0, "where": "", "url": "", "note": "why not"}}"""

JSON_LINE = re.compile(r'\{[^{}]*"found"[^{}]*\}', re.S)


def monthly_cost(every_hours: int) -> float:
    return round(CHECK_COST_USD * 24 / every_hours * 30, 2)


class PriceWatcher:
    def __init__(self, memory: Memory, notify: Callable[..., Awaitable[bool]] | None = None):
        self.memory = memory
        self.notify = notify
        memory.db.executescript(SCHEMA_SQL)

    def add(self, what: str, target: float | None, currency: str = "USD", every_hours: int = 24,
            days: int = 30) -> dict:
        what = what.strip()
        if not what:
            raise ToolError("What should I watch?")
        if len(self.active()) >= MAX_WATCHES:
            raise ToolError(f"You already have {MAX_WATCHES} price watches; stop one first.")
        every_hours = max(6, min(int(every_hours or 24), 168))
        until = datetime.now(timezone.utc) + timedelta(days=max(1, min(int(days or 30), 90)))
        cur = self.memory.db.execute(
            "INSERT INTO price_watches (what, target, currency, every_hours, until, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (what, target if target and target > 0 else None, (currency or "USD").upper()[:3], every_hours,
             until.isoformat(), datetime.now(timezone.utc).isoformat()),
        )
        self.memory.db.commit()
        return self.get(cur.lastrowid)

    def get(self, watch_id: int) -> dict:
        row = self.memory.db.execute("SELECT * FROM price_watches WHERE id = ?", (watch_id,)).fetchone()
        if not row:
            raise ToolError(f"No price watch {watch_id}.")
        return dict(row)

    def active(self) -> list[dict]:
        now = datetime.now(timezone.utc).isoformat()
        return [dict(r) for r in self.memory.db.execute(
            "SELECT * FROM price_watches WHERE until > ? ORDER BY id", (now,))]

    def remove(self, watch_id: int) -> bool:
        cur = self.memory.db.execute("DELETE FROM price_watches WHERE id = ?", (watch_id,))
        self.memory.db.commit()
        return cur.rowcount > 0

    def due(self) -> list[dict]:
        now = datetime.now(timezone.utc)
        return [w for w in self.active() if not w["last_checked"]
                or datetime.fromisoformat(w["last_checked"]) + timedelta(hours=w["every_hours"]) <= now]

    # --- checking --------------------------------------------------------
    async def run(self) -> None:
        while True:
            for watch in self.due():
                try:
                    await self.check(watch)
                except Exception:
                    log.exception("price watch %s failed; trying again next time", watch["id"])
                    self._checked(watch["id"])  # don't hammer a failing search
            await asyncio.sleep(TICK_SECONDS)

    def _checked(self, watch_id: int, **fields) -> None:
        fields["last_checked"] = datetime.now(timezone.utc).isoformat()
        sets = ", ".join(f"{k} = ?" for k in fields)
        self.memory.db.execute(f"UPDATE price_watches SET {sets} WHERE id = ?", (*fields.values(), watch_id))
        self.memory.db.commit()

    def _over_budget(self) -> bool:
        from .thrift import Thrift
        return usage.month_to_date(self.memory) >= Thrift(self.memory).month_budget()

    async def check(self, watch: dict) -> str | None:
        """Look up the price now. Returns what was told to the user, if anything."""
        if self._over_budget():
            return None
        found = await self._look_up(watch)
        if not found or not found.get("found") or not found.get("price"):
            self._checked(watch["id"])
            return None
        price = float(found["price"])
        best = min(price, watch["best_price"] or price)
        self._checked(watch["id"], last_price=price, best_price=best, last_where=str(found.get("where", ""))[:80],
                      last_url=str(found.get("url", ""))[:300])
        told, target = watch["told_price"], watch["target"]
        reference = told or watch["last_price"]
        hit_target = target is not None and price <= target and (told is None or price < told)
        big_drop = reference is not None and price <= reference * (1 - DROP_SHARE)
        if not (hit_target or big_drop):
            return None
        text = (f"Price drop on {watch['what']}: {price:.2f} {watch['currency']} at {found.get('where') or 'one seller'}"
                + (f", at or under your target of {target:.0f}." if hit_target else f", down from {reference:.2f}.")
                + (f" Catch: {found['note']}." if found.get("note") else ""))
        self._checked(watch["id"], told_price=price)
        if self.notify:
            await self.notify("deal", text)
        return text

    async def _look_up(self, watch: dict) -> dict | None:
        from .agent import fallback_kwargs  # here: agent imports this module

        model = settings.chat_model
        prompt = CHECK_PROMPT.format(user=settings.user_name, what=watch["what"], currency=watch["currency"],
                                     today=local_now().strftime("%d %B %Y"))
        messages = [{"role": "user", "content": prompt}]
        response = None
        for _ in range(4):  # the server-side search loop may pause and resume
            started = time.monotonic()
            response = await client.beta.messages.create(
                model=model, max_tokens=2_000, messages=messages,
                tools=[{"type": "web_search_20260209", "name": "web_search", "max_uses": 3},
                       {"type": "web_fetch_20260209", "name": "web_fetch", "max_uses": 3}],
                output_config={"effort": "low"},
                **fallback_kwargs(model),
            )
            if hasattr(response, "usage"):
                usage.record(self.memory, response.model, response.usage, purpose="deals", response=response, started=started)
            if response.stop_reason != "pause_turn":
                break
            messages.append({"role": "assistant", "content": response.content})
        text = "\n".join(b.text for b in response.content if getattr(b, "type", "") == "text")
        match = None
        for match in JSON_LINE.finditer(text):
            pass  # the last JSON line is the answer
        try:
            return json.loads(match.group(0)) if match else None
        except json.JSONDecodeError:
            return None


WATCH_TOOLS = [
    {
        "name": "watch_price",
        "description": "Keep checking a flight, hotel or product in the background and tell the user when "
        "it drops to `target` (or by 10% or more). Describe it exactly (route, dates, model, size). "
        "Each check costs a few cents, so this asks the user first with the monthly cost.",
        "input_schema": {
            "type": "object",
            "properties": {
                "what": {"type": "string"},
                "target": {"type": "number", "description": "Tell me at or below this price"},
                "currency": {"type": "string"},
                "every_hours": {"type": "integer", "description": "How often to check, 6-168; default 24"},
                "days": {"type": "integer", "description": "Stop after this many days; default 30"},
            },
            "required": ["what"],
            "additionalProperties": False,
        },
    },
    {
        "name": "price_watches",
        "description": "List the price watches with the latest and lowest prices seen, or stop one with stop_id.",
        "input_schema": {
            "type": "object",
            "properties": {"stop_id": {"type": "integer"}},
            "additionalProperties": False,
        },
    },
]
WATCH_TOOL_NAMES = {t["name"] for t in WATCH_TOOLS}


async def run_watch_tool(watcher: PriceWatcher, name: str, args: dict, thrift=None) -> str:
    if name == "price_watches":
        if args.get("stop_id"):
            return "Stopped." if watcher.remove(int(args["stop_id"])) else "No such watch."
        keys = ("id", "what", "target", "currency", "every_hours", "until", "last_price", "best_price",
                "last_where", "last_url", "last_checked")
        watches = [{k: w[k] for k in keys} for w in watcher.active()]
        return json.dumps(cards.tag(watches, cards.watch_card)) if watches else "No price watches."
    if name == "watch_price":
        every = max(6, min(int(args.get("every_hours") or 24), 168))
        target = args.get("target")
        currency = args.get("currency", "USD")
        cost = monthly_cost(every)
        how_often = "once a day" if every == 24 else f"every {every} hours"

        async def start() -> str:
            w = watcher.add(args["what"], target, currency, every, args.get("days", 30))
            return f"Watching it (watch {w['id']}). I'll tell you when the price drops."

        if thrift is not None:
            return thrift.ask_action(
                f"Watching \"{args['what']}\" {how_often} costs about {cost:.2f} dollars a month. Start it?", start)
        return await start()
    raise ToolError(f"Unknown tool {name}")
