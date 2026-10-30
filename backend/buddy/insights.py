"""Insights: every model request Buddy made, what it was for, what it cost and how fast it came back.

Built from the model_calls log (one row per request; see usage.log_call). Previews are short
and masked; the log keeps 90 days.
"""

from datetime import datetime, timedelta, timezone

from . import usage, webread
from .config import settings
from .memory import Memory
from .tools import local_now

PURPOSE_NAMES = {
    "chat": "Talking with you",
    "chat_free": "Talking with you (free model)",
    "web_digest": "Reading web pages (free model)",
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


def _quality(rows: list[dict]) -> dict:
    """Accuracy of free answers (Claude's review) and your thumbs, for a set of calls."""
    free = [r for r in rows if r["provider"] != "claude"]
    verdicts = {v: sum(1 for r in free if r["verdict"] == v) for v in (*usage.VERDICTS, "failed_check")}
    reviewed = verdicts["used"] + verdicts["fixed"] + verdicts["redone"]
    turns: dict[str, int] = {}
    for r in rows:
        if r["turn"] and r["rating"] is not None:
            turns[r["turn"]] = r["rating"]  # one vote per reply, however many calls it took
    up, down = sum(1 for v in turns.values() if v > 0), sum(1 for v in turns.values() if v < 0)
    return {
        "reviewed": reviewed,
        "used_as_is": verdicts["used"],
        "fixed": verdicts["fixed"],
        "redone": verdicts["redone"],
        "failed_check": verdicts["failed_check"],
        "not_reviewed": sum(1 for r in free if r["ok"] and not r["verdict"]),
        "accuracy": round(verdicts["used"] / reviewed, 2) if reviewed else None,      # right as it came
        "usable": round((verdicts["used"] + verdicts["fixed"]) / reviewed, 2) if reviewed else None,
        "thumbs_up": up,
        "thumbs_down": down,
        "satisfaction": round(up / (up + down), 2) if up + down else None,
    }


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
            **_quality(rs),
        })
    return sorted(out, key=lambda g: (-g["cost_usd"], -g["requests"]))


def turn_rows(memory: Memory, since: str, until: str = "9999") -> list[dict]:
    return [dict(r) for r in memory.db.execute(
        "SELECT * FROM turns WHERE at >= ? AND at < ? ORDER BY at", (since, until))]


def speed(turns: list[dict]) -> dict:
    """How replies felt from your side: time to the first word, the whole reply, and what slowed them."""
    first = [t["first_word_seconds"] for t in turns if t["first_word_seconds"] is not None and t["outcome"] == "ok"]
    total = [t["total_seconds"] for t in turns if t["outcome"] == "ok"]
    outcomes: dict[str, int] = {}
    for t in turns:
        key = "error" if t["outcome"].startswith("error") else t["outcome"]
        outcomes[key] = outcomes.get(key, 0) + 1
    routes: dict[str, list[dict]] = {}
    for t in turns:
        name = t["first_model"] if t["first_model"] == t["final_model"] else f"{t['first_model']} → {t['final_model']}"
        routes.setdefault(name, []).append(t)
    tools: dict[str, list[float]] = {}
    for t in turns:
        for tool in filter(None, (x.strip() for x in t["tools"].split(","))):
            tools.setdefault(tool, []).append(t["total_seconds"])
    n = len(turns)
    return {
        "replies": n,
        "first_word_median": _percentile(first, 0.5),
        "first_word_p95": _percentile(first, 0.95),
        "total_median": _percentile(total, 0.5),
        "total_p95": _percentile(total, 0.95),
        "filler_rate": round(sum(t["filler"] for t in turns) / n, 2) if n else None,
        "avg_rounds": round(sum(t["rounds"] for t in turns) / n, 1) if n else None,
        "handed_over_rate": round(sum(1 for t in turns if t["first_model"] != t["final_model"]) / n, 2) if n else None,
        "outcomes": outcomes,
        "by_route": sorted(({
            "route": name, "replies": len(ts),
            "first_word_median": _percentile([t["first_word_seconds"] for t in ts
                                              if t["first_word_seconds"] is not None], 0.5),
            "total_median": _percentile([t["total_seconds"] for t in ts], 0.5),
        } for name, ts in routes.items()), key=lambda r: -r["replies"]),
        # Replies that used a tool, and how long they took: the slow ones are worth fixing first.
        "slowest_tools": sorted(({"tool": name, "replies": len(v), "total_median": _percentile(v, 0.5)}
                                 for name, v in tools.items() if name != "free_answer_verdict"),
                                key=lambda r: -(r["total_median"] or 0))[:8],
    }


SERVER_TOOLS = ("web_search", "web_fetch", "code_execution")


def free_models_overview(memory: Memory, by_model: list[dict]) -> dict:
    """Every free provider: set up or not, what it's first choice for, and how it did."""
    import os

    from .free_models import BEST_FOR, PROVIDERS, FreeModels

    free = FreeModels(memory)
    enabled = free.enabled()
    stats = {g["provider"]: g for g in by_model if g["provider"] != "claude"}
    rows = []
    for name, p in PROVIDERS.items():
        g = stats.get(name, {})
        rows.append({
            "name": name, "model": free.model_of(name), "good_at": p["good_at"],
            "key_set": bool((os.environ.get(p["key"]) or "").strip()), "on": name in enabled,
            "first_choice_for": [k.replace("_", " ") for k, order in BEST_FOR.items() if order[0] == name],
            "requests": g.get("requests", 0), "failed": g.get("failed", 0), "median_seconds": g.get("median_seconds"),
            "accuracy": g.get("accuracy"), "usable": g.get("usable"),
        })
    switched_on = (os.environ.get("BUDDY_FREE_MODELS") or "").strip() == "1"
    note = ("" if enabled else "Free models are off: set BUDDY_FREE_MODELS to 1 with set.sh." if not switched_on
            else "No free-model key is set yet (docs/MODELS.md).")
    return {"on": bool(enabled), "direct_answers": bool(enabled) and settings.free_direct, "note": note,
            "providers": sorted(rows, key=lambda r: (not r["on"], -r["requests"]))}


def tools_overview(memory: Memory, since: str, rows: list[dict]) -> list[dict]:
    """Tools and agents used for replies: Buddy's own, agents (Zapier, Gmail, built ones) and
    Claude's server tools (web search, page fetching, code). Calls, failures, time, extra cost."""
    import os

    calls = [dict(r) for r in memory.db.execute("SELECT * FROM tool_calls WHERE at >= ?", (since,))]
    plan = float(os.environ.get("BUDDY_ZAPIER_PLAN_USD") or 0)
    tasks = int(os.environ.get("BUDDY_ZAPIER_TASKS_PER_MONTH") or 0)
    per_task = plan / tasks if plan and tasks else 0.0
    groups: dict[tuple[str, str], list[dict]] = {}
    for c in calls:
        groups.setdefault((c["agent"], c["tool"]), []).append(c)
    out = []
    for (agent, tool), cs in groups.items():
        times = [c["seconds"] for c in cs if c["seconds"] is not None]
        errors = [c["error"] for c in cs if not c["ok"] and c["error"]]
        out.append({"agent": agent, "tool": tool.split("__", 1)[-1], "calls": len(cs),
                    "failed": sum(1 for c in cs if not c["ok"]), "median_seconds": _percentile(times, 0.5),
                    "extra_usd": round(len(cs) * per_task, 4) if agent == "zapier" else 0.0,
                    "last_error": errors[-1] if errors else ""})
    searches = sum(r["web_searches"] for r in rows)
    for tool in SERVER_TOOLS:
        n = sum(1 for r in rows if tool in (r["tools"] or "").split(", "))
        if n or (tool == "web_search" and searches):
            out.append({"agent": "claude", "tool": tool, "calls": searches if tool == "web_search" else n,
                        "failed": 0, "median_seconds": None,
                        "extra_usd": round(searches * usage.WEB_SEARCH_USD, 4) if tool == "web_search" else 0.0,
                        "last_error": ""})
    return sorted(out, key=lambda t: -t["calls"])


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

    turns = turn_rows(memory, since)
    for t in turns:
        day = (datetime.fromisoformat(t["at"]) + offset).date().isoformat()
        if day in per_day:
            per_day[day].setdefault("_first", []).append(t["first_word_seconds"])
    for d in per_day.values():
        d["first_word_median"] = _percentile([x for x in d.pop("_first", []) if x is not None], 0.5)

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
            **_quality(rows),
        },
        "speed": speed(turns),
        "free_models": free_models_overview(memory, by_model),
        "tools": tools_overview(memory, since, rows),
        "web": webread.web_overview(memory),
        "by_model": by_model,
        "by_purpose": by_purpose,
        "by_day": list(per_day.values()),
        "recent": [{
            "at": r["at"], "provider": r["provider"], "model": r["model"], "purpose": r["purpose"],
            "purpose_label": purpose_name(r["purpose"]), "input_tokens": r["input_tokens"],
            "output_tokens": r["output_tokens"], "cache_read_tokens": r["cache_read_tokens"],
            "cost_usd": round(r["cost_usd"], 5), "seconds": r["seconds"], "ok": bool(r["ok"]), "stop": r["stop"],
            "tools": r["tools"], "request": r["request"], "reply": r["reply"], "verdict": r["verdict"],
            "rating": r["rating"],
        } for r in reversed(picked[-max(1, min(recent, 300)):])],
    }


RATE_TOOL = {
    "name": "rate_answer",
    "description": "The user judges your previous answer by voice (\"that was wrong\", \"great answer\", \"that "
    "didn't help\"): record it as a thumbs up or down for the models that worked on it, shown in Insights.",
    "input_schema": {
        "type": "object",
        "properties": {"good": {"type": "boolean", "description": "true for a thumbs up"}},
        "required": ["good"],
        "additionalProperties": False,
    },
}
