"""Insights: every model request Buddy made, what it was for, what it cost and how fast it came back.

Built from the model_calls log (one row per request; see usage.log_call). Previews are short
and masked; the log keeps 90 days.
"""

from datetime import datetime, timedelta, timezone

from . import usage
from .memory import Memory
from .tools import local_now

PURPOSE_NAMES = {
    "chat": "Talking with you",
    "listen": "Listen-in",
    "addressing": "Was that for me?",
    "think": "Let me think",
    "expert": "Expert",
    "research": "Research",
    "deals": "Price watches",
    "mail": "Email sorting",
    "bankfeed": "Bank categories",
    "lifelog": "Life log imports",
    "briefing": "Briefings and nudges",
    "build_agent": "Agent builder",
}


def purpose_name(purpose: str) -> str:
    if purpose.startswith("general:"):
        return "General task: " + purpose.split(":", 1)[1].replace("_", " ")
    return PURPOSE_NAMES.get(purpose, purpose.replace("_", " ").capitalize())


def _percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    values = sorted(values)
    return round(values[min(len(values) - 1, int(q * len(values)))], 2)


def _price_in_out(model: str) -> tuple[float, float]:
    return next((p for name, p in usage.PRICES.items() if model.startswith(name)), usage.FALLBACK_PRICE)


def _group(rows: list[dict], key) -> list[dict]:
    groups: dict[str, list[dict]] = {}
    for r in rows:
        groups.setdefault(key(r), []).append(r)
    out = []
    for name, rs in groups.items():
        times = [r["seconds"] for r in rs if r["seconds"] is not None and r["ok"]]
        prompt = sum(r["input_tokens"] + r["cache_read_tokens"] + r["cache_write_tokens"] for r in rs)
        out.append({
            "name": name,
            "requests": len(rs),
            "failed": sum(1 for r in rs if not r["ok"]),
            "cost_usd": round(sum(r["cost_usd"] for r in rs), 4),
            "input_tokens": sum(r["input_tokens"] for r in rs),
            "output_tokens": sum(r["output_tokens"] for r in rs),
            "cache_read_tokens": sum(r["cache_read_tokens"] for r in rs),
            "cache_hit": round(sum(r["cache_read_tokens"] for r in rs) / prompt, 2) if prompt else None,
            "web_searches": sum(r["web_searches"] for r in rs),
            "median_seconds": _percentile(times, 0.5),
            "p95_seconds": _percentile(times, 0.95),
        })
    return sorted(out, key=lambda g: (-g["cost_usd"], -g["requests"]))


def report(memory: Memory, days: int = 7, recent: int = 60, model: str = "", purpose: str = "") -> dict:
    days = max(1, min(int(days), usage.KEEP_CALLS_DAYS))
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    rows = [dict(r) for r in memory.db.execute("SELECT * FROM model_calls WHERE at >= ? ORDER BY at", (since,))]

    by_model = _group(rows, lambda r: f"{r['provider']} · {r['model']}")
    for g in by_model:
        g["provider"], g["model"] = g["name"].split(" · ", 1)
    by_purpose = _group(rows, lambda r: r["purpose"])
    for g in by_purpose:
        g["label"] = purpose_name(g["name"])
        g["models"] = {}
        for r in rows:
            if r["purpose"] == g["name"]:
                g["models"][r["model"]] = g["models"].get(r["model"], 0) + 1

    # Per local day, oldest first, with empty days filled in so the chart has no gaps.
    offset = local_now().utcoffset() or timedelta(0)
    per_day: dict[str, dict] = {}
    for i in range(days - 1, -1, -1):
        day = (local_now() - timedelta(days=i)).date().isoformat()
        per_day[day] = {"day": day, "requests": 0, "cost_usd": 0.0, "free_requests": 0}
    for r in rows:
        day = (datetime.fromisoformat(r["at"]) + offset).date().isoformat()
        if day in per_day:
            d = per_day[day]
            d["requests"] += 1
            d["cost_usd"] = round(d["cost_usd"] + r["cost_usd"], 4)
            d["free_requests"] += r["provider"] != "claude"

    # What the cheap routes saved, priced against the model they stood in for.
    sonnet_in, sonnet_out = _price_in_out("claude-sonnet-5-5")
    haiku_in, haiku_out = _price_in_out("claude-haiku-5-5")
    saved_by_router = sum(
        (r["input_tokens"] * (sonnet_in - haiku_in) + r["output_tokens"] * (sonnet_out - haiku_out)) / 1_000_000
        for r in rows if r["purpose"] == "chat" and r["model"].startswith("claude-haiku"))
    saved_by_free = sum((r["input_tokens"] * haiku_in + r["output_tokens"] * haiku_out) / 1_000_000
                        for r in rows if r["provider"] != "claude" and r["ok"])

    times = [r["seconds"] for r in rows if r["seconds"] is not None and r["ok"]]
    chat_times = [r["seconds"] for r in rows if r["purpose"] == "chat" and r["seconds"] is not None]
    prompt = sum(r["input_tokens"] + r["cache_read_tokens"] + r["cache_write_tokens"] for r in rows)

    picked = [r for r in rows if (not model or r["model"] == model) and (not purpose or r["purpose"] == purpose)]
    return {
        "days": days,
        "totals": {
            "requests": len(rows),
            "failed": sum(1 for r in rows if not r["ok"]),
            "cost_usd": round(sum(r["cost_usd"] for r in rows), 4),
            "claude_requests": sum(1 for r in rows if r["provider"] == "claude"),
            "free_requests": sum(1 for r in rows if r["provider"] != "claude"),
            "input_tokens": sum(r["input_tokens"] for r in rows),
            "output_tokens": sum(r["output_tokens"] for r in rows),
            "cache_hit": round(sum(r["cache_read_tokens"] for r in rows) / prompt, 2) if prompt else None,
            "web_searches": sum(r["web_searches"] for r in rows),
            "median_seconds": _percentile(times, 0.5),
            "p95_seconds": _percentile(times, 0.95),
            "chat_median_seconds": _percentile(chat_times, 0.5),
            "saved_by_router_usd": round(saved_by_router, 4),
            "saved_by_free_usd": round(saved_by_free, 4),
        },
        "by_model": by_model,
        "by_purpose": by_purpose,
        "by_day": list(per_day.values()),
        "recent": [{
            "at": r["at"], "provider": r["provider"], "model": r["model"], "purpose": r["purpose"],
            "purpose_label": purpose_name(r["purpose"]), "input_tokens": r["input_tokens"],
            "output_tokens": r["output_tokens"], "cache_read_tokens": r["cache_read_tokens"],
            "cost_usd": round(r["cost_usd"], 5), "seconds": r["seconds"], "ok": bool(r["ok"]), "stop": r["stop"],
            "tools": r["tools"], "request": r["request"], "reply": r["reply"],
        } for r in reversed(picked[-max(1, min(recent, 300)):])],
    }
