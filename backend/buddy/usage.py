"""Track Claude token usage and cost, and enforce the monthly budget."""

import time
from datetime import datetime, timedelta, timezone

from .memory import Memory
from .tools import local_now

# USD per million tokens: (input, output). Cache reads cost 0.1x input, cache writes 1.25x.
# Longer names first: prices are matched by prefix.
PRICES = {
    "claude-opus-5-5": (4.00, 20.00),
    "claude-opus-5": (5.00, 25.00),
    "claude-opus-4-8": (5.00, 25.00),
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-sonnet-5": (2.00, 10.00),
    "claude-haiku-5-5": (0.10, 0.50),  # prompts up to 100K tokens; Buddy's are far smaller
    "claude-haiku-4-5": (1.00, 5.00),
}
FALLBACK_PRICE = PRICES["claude-opus-5"]  # unknown model: assume the dearest current one
WEB_SEARCH_USD = 0.01  # per search ($10 per 1,000); web fetch is not metered


def cost_usd(model: str, usage) -> float:
    price_in, price_out = next(
        (p for name, p in PRICES.items() if model.startswith(name)), FALLBACK_PRICE
    )
    cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
    cache_write = getattr(usage, "cache_creation_input_tokens", 0) or 0
    searches = web_searches(usage)
    return (
        usage.input_tokens * price_in
        + cache_read * price_in * 0.1
        + cache_write * price_in * 1.25
        + usage.output_tokens * price_out
    ) / 1_000_000 + searches * WEB_SEARCH_USD


def _add(memory: Memory, key: str, amount: float) -> None:
    memory.db.execute(
        "INSERT INTO kv (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET "
        "value = CAST(CAST(value AS REAL) + CAST(excluded.value AS REAL) AS TEXT)",
        (key, str(amount)),
    )


def spent_on(memory: Memory, day: str, purpose: str = "") -> float:
    """Spend on a local calendar day (YYYY-MM-DD), optionally for one purpose."""
    value = memory.get(f"spend:{purpose + ':' if purpose else ''}{day}")
    return float(value) if value else 0.0


def web_searches(usage) -> int:
    server_tools = getattr(usage, "server_tool_use", None)
    return (getattr(server_tools, "web_search_requests", 0) or 0) if server_tools else 0


KEEP_CALLS_DAYS = 90
PREVIEW_CHARS = 280


def _preview(text: str) -> str:
    from .privacy import mask

    text = " ".join((text or "").split())
    return mask(text[:PREVIEW_CHARS * 2])[:PREVIEW_CHARS]


def log_call(memory: Memory, *, provider: str, model: str, purpose: str, input_tokens: int = 0,
             output_tokens: int = 0, cache_read: int = 0, cache_write: int = 0, searches: int = 0,
             cost: float = 0.0, seconds: float | None = None, ok: bool = True, stop: str = "",
             tools: str = "", request: str = "", reply: str = "") -> None:
    """One row per model request, for the Insights dashboard. Previews are short and masked."""
    now = datetime.now(timezone.utc)
    memory.db.execute(
        """INSERT INTO model_calls (at, provider, model, purpose, input_tokens, output_tokens, cache_read_tokens,
             cache_write_tokens, web_searches, cost_usd, seconds, ok, stop, tools, request, reply)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (now.isoformat(), provider, model, purpose or "chat", input_tokens, output_tokens, cache_read, cache_write,
         searches, cost, None if seconds is None else round(seconds, 2), 1 if ok else 0, stop[:200], tools[:200],
         _preview(request), _preview(reply)),
    )
    memory.db.execute("DELETE FROM model_calls WHERE at < ?", ((now - timedelta(days=KEEP_CALLS_DAYS)).isoformat(),))
    memory.db.commit()


def _reply_parts(response) -> tuple[str, str, str]:
    """(text, tools asked for, stop reason) from a Claude response."""
    text, tools = [], []
    for block in getattr(response, "content", None) or []:
        kind = getattr(block, "type", "")
        if kind == "text":
            text.append(block.text)
        elif kind in ("tool_use", "server_tool_use", "mcp_tool_use"):
            tools.append(getattr(block, "name", kind))
    return " ".join(text), ", ".join(dict.fromkeys(tools)), str(getattr(response, "stop_reason", "") or "")


def record(memory: Memory, model: str, usage, purpose: str = "", *, response=None, started: float | None = None,
           request: str = "", label: str = "") -> float:
    """Log one response's cost. `purpose` (e.g. "listen") gets its own daily tally.
    `label` names it on the Insights dashboard when it differs from `purpose`; `started`
    (time.monotonic() before the call) gives the answer time."""
    day = datetime.now(timezone.utc).date().isoformat()
    cost = cost_usd(model, usage)
    # Daily allowances follow the user's own midnight, wherever they are.
    local_day = local_now().date().isoformat()
    _add(memory, f"spend:{local_day}", cost)
    if purpose:
        _add(memory, f"spend:{purpose}:{local_day}", cost)
    if searches := web_searches(usage):
        # Not a "spend:" key: searches are part of the purposes above, counted for FinOps.
        _add(memory, f"searches:{local_day}", searches)
    memory.db.execute(
        """INSERT INTO usage (day, model, input_tokens, output_tokens,
                              cache_read_tokens, cache_write_tokens, cost_usd)
           VALUES (?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(day, model) DO UPDATE SET
             input_tokens = input_tokens + excluded.input_tokens,
             output_tokens = output_tokens + excluded.output_tokens,
             cache_read_tokens = cache_read_tokens + excluded.cache_read_tokens,
             cache_write_tokens = cache_write_tokens + excluded.cache_write_tokens,
             cost_usd = cost_usd + excluded.cost_usd""",
        (
            day,
            model,
            usage.input_tokens,
            usage.output_tokens,
            getattr(usage, "cache_read_input_tokens", 0) or 0,
            getattr(usage, "cache_creation_input_tokens", 0) or 0,
            cost,
        ),
    )
    text, tools, stop = _reply_parts(response)
    log_call(memory, provider="claude", model=model, purpose=label or purpose or "chat",
             input_tokens=usage.input_tokens, output_tokens=usage.output_tokens,
             cache_read=getattr(usage, "cache_read_input_tokens", 0) or 0,
             cache_write=getattr(usage, "cache_creation_input_tokens", 0) or 0, searches=searches, cost=cost,
             seconds=None if started is None else time.monotonic() - started, stop=stop, tools=tools,
             request=request, reply=text)
    return cost


def month_to_date(memory: Memory) -> float:
    month = datetime.now(timezone.utc).strftime("%Y-%m")
    row = memory.db.execute(
        "SELECT COALESCE(SUM(cost_usd), 0) AS total FROM usage WHERE day LIKE ?", (f"{month}-%",)
    ).fetchone()
    return float(row["total"])


def summary(memory: Memory) -> list[dict]:
    rows = memory.db.execute(
        "SELECT substr(day, 1, 7) AS month, model, SUM(input_tokens) AS input_tokens, "
        "SUM(output_tokens) AS output_tokens, SUM(cache_read_tokens) AS cache_read_tokens, "
        "ROUND(SUM(cost_usd), 4) AS cost_usd FROM usage GROUP BY month, model ORDER BY month DESC"
    )
    return [dict(r) for r in rows]


def count(memory: Memory, name: str, n: int = 1) -> None:
    """A monthly counter (router choices, free-model calls...) for the cost report."""
    month = local_now().strftime("%Y-%m")
    _add(memory, f"count:{name}:{month}", n)
