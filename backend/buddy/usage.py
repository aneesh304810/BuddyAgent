"""Track Claude token usage and cost, and enforce the monthly budget."""

from datetime import datetime, timezone

from .memory import Memory

# USD per million tokens: (input, output). Cache reads cost 0.1x input, cache writes 1.25x.
# Longer names first: prices are matched by prefix.
PRICES = {
    "claude-opus-5-5": (4.00, 20.00),
    "claude-opus-5": (5.00, 25.00),
    "claude-opus-4-8": (5.00, 25.00),
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-sonnet-5": (2.00, 10.00),
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
    server_tools = getattr(usage, "server_tool_use", None)
    searches = (getattr(server_tools, "web_search_requests", 0) or 0) if server_tools else 0
    return (
        usage.input_tokens * price_in
        + cache_read * price_in * 0.1
        + cache_write * price_in * 1.25
        + usage.output_tokens * price_out
    ) / 1_000_000 + searches * WEB_SEARCH_USD


def record(memory: Memory, model: str, usage) -> float:
    day = datetime.now(timezone.utc).date().isoformat()
    cost = cost_usd(model, usage)
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
    memory.db.commit()
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
