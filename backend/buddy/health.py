"""The daily health check: Buddy compares how it did lately against the week before.

Every morning (before the briefing) it looks at the last day and the last 7 days against
the 7 days before, and writes down what got worse and what to do about it:

  speed         time to the first word and the whole reply, the filler, slow tools
  reliability   failed replies, too many steps, models failing or at their free limit
  accuracy      free answers Claude had to fix or redo, your thumbs down, hand-overs
  cost          cost per reply, cache reuse, the month's forecast against the budget
  reconciliation  the per-request log agrees with the money tally (nothing unlogged)

Reports are kept (GET /admin/health) so trends show over months, and the findings say
which setting or part of the code to look at. Some fixes happen on their own: a free
model Claude keeps correcting is tried later for that kind of task (free_models.order).
"""

import asyncio
import json
import logging
import os
from datetime import datetime, timedelta, timezone

from . import usage
from .config import settings
from .insights import _percentile, _quality, purpose_name, speed, turn_rows
from .memory import Memory
from .tools import local_now

log = logging.getLogger(__name__)

RUN_AT_HOUR = 5          # local time; the morning briefing can mention what it found
# The daily digest in the chat: the whole analysis in plain words, with recommendations.
DIGEST_AT_HOUR = int(os.getenv("BUDDY_HEALTH_DIGEST_AT", "9"))
DIGEST_ON = os.getenv("BUDDY_HEALTH_DIGEST", "1") != "0"
CHECK_EVERY_SECONDS = 600
KEEP_DAYS = 365
MIN_SAMPLE = 8           # fewer than this and a rate says nothing
# Written weekly by the root maintenance job (deploy/maintain.py): patches applied, security findings.
SECURITY_REPORT = os.getenv("BUDDY_SECURITY_REPORT", "/var/lib/buddy/security.json")


def _calls(memory: Memory, since: datetime, until: datetime) -> list[dict]:
    return [dict(r) for r in memory.db.execute(
        "SELECT * FROM model_calls WHERE at >= ? AND at < ?", (since.isoformat(), until.isoformat()))]


def _rate(part: int, whole: int) -> float | None:
    return round(part / whole, 2) if whole >= MIN_SAMPLE else None


def _window(memory: Memory, since: datetime, until: datetime) -> dict:
    calls = _calls(memory, since, until)
    turns = turn_rows(memory, since.isoformat(), until.isoformat())
    s = speed(turns)
    replies = len(turns)
    prompt = sum(r["input_tokens"] + r["cache_read_tokens"] + r["cache_write_tokens"] for r in calls)
    claude_cost = sum(r["cost_usd"] for r in calls if r["provider"] == "claude")
    chat_cost = sum(r["cost_usd"] for r in calls if r["purpose"] == "chat")
    return {
        "replies": replies,
        "first_word_median": s["first_word_median"],
        "first_word_p95": s["first_word_p95"],
        "total_p95": s["total_p95"],
        "filler_rate": s["filler_rate"] if replies >= MIN_SAMPLE else None,
        "failed_reply_rate": _rate(sum(1 for t in turns if t["outcome"].startswith("error")
                                       or t["outcome"] in ("too_many_steps", "silent")), replies),
        "handed_over_rate": s["handed_over_rate"] if replies >= MIN_SAMPLE else None,
        "slowest_tools": s["slowest_tools"][:3],
        "cost_usd": round(claude_cost, 4),
        "chat_cost_per_reply": round(chat_cost / replies, 4) if replies else None,
        "cache_hit": round(sum(r["cache_read_tokens"] for r in calls) / prompt, 2) if prompt else None,
        "quality": _quality(calls),
        "providers": _providers(calls),
        "purposes": _purposes(calls),
    }


def _providers(calls: list[dict]) -> dict:
    out = {}
    for name in {r["provider"] for r in calls}:
        rs = [r for r in calls if r["provider"] == name]
        limited = sum(1 for r in rs if "free limit" in r["stop"])
        failed = sum(1 for r in rs if not r["ok"]) - limited
        out[name] = {"calls": len(rs), "failure_rate": _rate(failed, len(rs)),
                     "limit_rate": _rate(limited, len(rs)),
                     "median_seconds": _percentile([r["seconds"] for r in rs if r["seconds"] is not None], 0.5)}
        if name != "claude":
            for kind in {r["purpose"] for r in rs}:
                q = _quality([r for r in rs if r["purpose"] == kind])
                if q["reviewed"]:
                    out[name].setdefault("accuracy", {})[kind] = {"reviewed": q["reviewed"],
                                                                  "accuracy": q["accuracy"], "usable": q["usable"]}
    return out


def _purposes(calls: list[dict]) -> dict:
    out = {}
    for name in {r["purpose"] for r in calls}:
        q = _quality([r for r in calls if r["purpose"] == name])
        if q["thumbs_up"] + q["thumbs_down"]:
            out[name] = {"up": q["thumbs_up"], "down": q["thumbs_down"]}
    return out


def _worse(now: float | None, before: float | None, by: float) -> bool:
    """`now` is at least `by` (e.g. 0.3 = 30%) higher than `before`."""
    return now is not None and before is not None and before > 0 and now >= before * (1 + by)


def _examples(memory: Memory, since: datetime, sql: str, args=(), limit: int = 3) -> list[str]:
    rows = memory.db.execute(sql + " LIMIT ?", (since.isoformat(), *args, limit)).fetchall()
    return [r[0] for r in rows if r[0]]


def check(memory: Memory, now: datetime | None = None, finops_report: dict | None = None) -> dict:
    """Compare the last day and 7 days with the 7 days before; return findings, most urgent first."""
    now = now or datetime.now(timezone.utc)
    day = _window(memory, now - timedelta(days=1), now)
    week = _window(memory, now - timedelta(days=7), now)
    before = _window(memory, now - timedelta(days=14), now - timedelta(days=7))
    findings: list[dict] = []

    def find(severity, area, title, detail, fix):
        findings.append({"severity": severity, "area": area, "title": title, "detail": detail, "fix": fix})

    # --- speed -------------------------------------------------------------
    fw, fw_before = week["first_word_median"], before["first_word_median"]
    slow = ", ".join(f"{t['tool']} ({t['total_median']}s)" for t in week["slowest_tools"]) or "none stand out"
    if fw is not None and week["replies"] >= MIN_SAMPLE and (fw > 3.5 or _worse(fw, fw_before, 0.3)):
        find("act" if fw > 5 else "watch", "speed", f"First words take {fw}s (typical)",
             f"The week before: {fw_before or '–'}s. Slowest replies used: {slow}.",
             "Run deploy/trace.sh while talking to see where the time goes. Slow tools: cache their results "
             "or narrow them; long prompts: check cache reuse below.")
    if week["total_p95"] and week["total_p95"] > 25 and week["replies"] >= MIN_SAMPLE:
        find("watch", "speed", f"The slowest replies take {week['total_p95']}s",
             f"Slowest replies used: {slow}.",
             "Long replies usually mean web searches or several tool rounds; lower BUDDY_SEARCH_LEVEL or "
             "move the work into a background job.")
    if week["filler_rate"] and week["filler_rate"] > 0.3:
        find("watch", "speed", f"'One sec' was needed in {int(week['filler_rate'] * 100)}% of replies",
             "Buddy said nothing for 2.5 seconds that often.",
             "Same as above: find the slow tools; the router may also be picking the main model too often.")

    # --- reliability ---------------------------------------------------------
    if week["failed_reply_rate"] and week["failed_reply_rate"] > 0.05:
        examples = _examples(memory, now - timedelta(days=7),
                             "SELECT outcome || ' (' || tools || ')' FROM turns WHERE at >= ? AND outcome != 'ok' "
                             "AND outcome != 'interrupted' ORDER BY at DESC")
        find("act", "reliability", f"{int(week['failed_reply_rate'] * 100)}% of replies failed",
             "Recent: " + ("; ".join(examples) or "–"),
             "See the server log (journalctl -u buddy) around those times; 'too_many_steps' means a tool "
             "kept failing and Claude kept retrying it; 'silent' means a reply ended without words (the log "
             "line 'reply ended without words' says why).")
    for name, p in week["providers"].items():
        if p["failure_rate"] and p["failure_rate"] > 0.2:
            setting = f"BUDDY_{name.upper()}_MODEL" if name != "claude" else "your Anthropic key and status page"
            find("act", "reliability", f"{name} fails {int(p['failure_rate'] * 100)}% of the time",
                 f"{p['calls']} calls this week.",
                 f"Check its key and model name ({setting}); providers rename free models often.")
        if p["limit_rate"] and p["limit_rate"] > 0.3:
            find("watch", "reliability", f"{name} is at its free limit {int(p['limit_rate'] * 100)}% of the time",
                 "Buddy moves on to the next model, which can be slower or weaker.",
                 "Add another free provider's key, or a few dollars of credit where that lifts the limit.")

    # --- accuracy ------------------------------------------------------------
    for name, p in week["providers"].items():
        for kind, a in p.get("accuracy", {}).items():
            if a["reviewed"] >= MIN_SAMPLE and a["usable"] is not None and a["usable"] < 0.7:
                find("act", "accuracy", f"{name} gets {purpose_name(kind).lower()} wrong too often",
                     f"Claude used only {int((a['accuracy'] or 0) * 100)}% as given and had to redo "
                     f"{int((1 - a['usable']) * 100)}% ({a['reviewed']} checked).",
                     f"Buddy now tries {name} later for this kind of task. To go further, change "
                     f"BUDDY_{name.upper()}_MODEL or remove its key.")
    q = week["quality"]
    free_answers = q["reviewed"] + q["not_reviewed"]
    if free_answers >= MIN_SAMPLE and q["not_reviewed"] / free_answers > 0.3:
        find("watch", "accuracy", f"{q['not_reviewed']} free answers weren't reviewed",
             "Claude didn't record a verdict, so their accuracy is unknown.",
             "The review step is in free_models.run_general_tool (the 'review' note) and the "
             "free_answer_verdict tool; check the persona's general_task line.")
    votes = q["thumbs_up"] + q["thumbs_down"]
    if votes >= 5 and q["thumbs_down"] / votes > 0.3:
        worst = sorted(week["purposes"].items(), key=lambda kv: -kv[1]["down"])[:2]
        examples = _examples(memory, now - timedelta(days=7),
                             "SELECT request || ' → ' || reply FROM model_calls WHERE at >= ? AND rating = -1 "
                             "AND purpose = 'chat' AND request != '' ORDER BY at DESC")
        find("act", "accuracy", f"You gave {q['thumbs_down']} of {votes} replies a thumbs down",
             "Mostly: " + ", ".join(f"{purpose_name(k)} ({v['down']})" for k, v in worst)
             + (". Recent: " + " | ".join(examples) if examples else ""),
             "Read them in Insights → Latest requests (👎). Wrong facts: let it search more "
             "(BUDDY_SEARCH_LEVEL); wrong tone or length: the persona; wrong model: the router rules.")
    if week["handed_over_rate"] and week["handed_over_rate"] > 0.25:
        examples = _examples(memory, now - timedelta(days=7),
                             "SELECT c.request FROM turns t JOIN model_calls c ON c.turn = t.turn "
                             "WHERE t.at >= ? AND t.first_model != t.final_model AND c.purpose = 'chat' "
                             "AND c.request != '' GROUP BY t.turn ORDER BY t.at DESC")
        find("watch", "accuracy", f"The quick model handed over {int(week['handed_over_rate'] * 100)}% of replies",
             "Each hand-over costs an extra step. Recent: " + (" | ".join(examples) or "–"),
             "Add words from these questions to NEEDS_SONNET in buddy/router.py so they go straight to "
             "the main model.")

    # --- cost --------------------------------------------------------------------
    cpr, cpr_before = week["chat_cost_per_reply"], before["chat_cost_per_reply"]
    if _worse(cpr, cpr_before, 0.4) and week["replies"] >= MIN_SAMPLE:
        find("watch", "cost", f"Each reply costs ${cpr:.4f}, up from ${cpr_before:.4f}",
             f"Cache reuse: {week['cache_hit']} (before: {before['cache_hit']}).",
             "Usually a longer prompt (new tools, a longer persona) or less caching; check Insights → "
             "Each model for input tokens.")
    if week["cache_hit"] is not None and week["cache_hit"] < 0.5 and week["replies"] >= MIN_SAMPLE:
        find("watch", "cost", f"Only {int(week['cache_hit'] * 100)}% of prompt tokens came from cache",
             "Cached tokens cost a tenth as much.",
             "Something in the prompt changes every turn (a timestamp, a tool list); keep changing parts at the end.")
    if finops_report and finops_report.get("over_budget"):
        find("act", "cost", "This month is heading over budget",
             f"Forecast ${finops_report.get('forecast_usd', 0):.2f} against "
             f"${finops_report.get('all_in_budget_usd') or settings.monthly_budget_usd:.0f}.",
             "See Costs; the biggest lever is usually the expert and research, then search level.")

    # --- reconciliation: every dollar in the tally is in the per-request log ---------
    # Whole UTC days both cover (the tally is daily; the log started on some day).
    first = memory.db.execute("SELECT MIN(at) AS a FROM model_calls").fetchone()["a"]
    start = max((now - timedelta(days=7)).date(), (datetime.fromisoformat(first).date() + timedelta(days=1))
                if first else now.date())
    end = now.date()  # today isn't over yet
    tally = memory.db.execute("SELECT COALESCE(SUM(cost_usd), 0) AS c FROM usage WHERE day >= ? AND day < ?",
                              (start.isoformat(), end.isoformat())).fetchone()["c"]
    logged = memory.db.execute("SELECT COALESCE(SUM(cost_usd), 0) AS c FROM model_calls WHERE provider = 'claude' "
                               "AND at >= ? AND at < ?", (start.isoformat(), end.isoformat())).fetchone()["c"]
    gap = abs(tally - logged)
    reconciliation = {"from": start.isoformat(), "to": end.isoformat(), "tally_usd": round(tally, 4),
                      "logged_usd": round(logged, 4), "gap_usd": round(gap, 4)}
    if gap > max(0.05, 0.03 * max(tally, logged)):
        find("watch", "reconciliation", f"The money tally and the request log differ by ${gap:.2f}",
             f"Money tally ${tally:.2f}, request log ${logged:.2f} ({start} to {end}).",
             "Some code calls Claude without usage.record, or records it twice: search for "
             "client.beta.messages and check each records its usage once.")

    # --- sandbox: tools used that aren't in the registry yet ---------------------------------
    try:
        unvetted = [dict(r) for r in memory.db.execute(
            "SELECT package, kind, uses FROM sandbox_installs WHERE vetted = 0 AND uses >= 2 ORDER BY uses DESC LIMIT 8")]
    except Exception:  # no tasks yet
        unvetted = []
    if unvetted:
        find("watch", f"{len(unvetted)} tools keep being installed that aren't in Buddy's registry",
             ", ".join(f"{u['package']} ({u['kind']}, {u['uses']}x)" for u in unvetted),
             "Review them and add the good ones to deploy/sandbox/registry/tools.yaml with a pinned version "
             "(ask me to do it), so tasks get them vetted and faster.")

    # --- security: from the weekly root check (patches, keys, network, logs) -------------
    security = security_report(now)
    findings.extend(security["findings"])

    order = {"act": 0, "watch": 1}
    findings.sort(key=lambda f: order.get(f["severity"], 2))
    status = "act" if any(f["severity"] == "act" for f in findings) else ("watch" if findings else "good")
    return {"at": now.isoformat(), "status": status, "findings": findings,
            "metrics": {"last_day": day, "last_7_days": week, "previous_7_days": before,
                        "reconciliation": reconciliation,
                        "security": {k: security[k] for k in ("at", "status", "done")}}}


def security_report(now: datetime, path: str | None = None) -> dict:
    """The weekly security check's findings, or a finding that it isn't running."""
    empty = {"at": None, "status": "unknown", "done": [], "findings": []}
    try:
        with open(path or SECURITY_REPORT) as f:
            report = json.load(f)
    except FileNotFoundError:
        if os.path.isdir("/etc/buddy"):  # on the server, the weekly check should have written it
            empty["findings"] = [{"severity": "watch", "area": "security",
                                  "title": "The weekly security check hasn't run yet",
                                  "detail": "No patching or security report found.",
                                  "fix": "sudo bash /opt/buddy/deploy/update.sh (it switches the weekly check on), "
                                         "or run it now: sudo bash /opt/buddy/deploy/security.sh check"}]
        return empty
    except (OSError, json.JSONDecodeError):
        return empty
    findings = [f for f in report.get("findings", []) if f.get("severity") in ("act", "watch")]
    try:
        age = (now - datetime.fromisoformat(report["at"])).days
    except (KeyError, ValueError, TypeError):
        age = 99
    if age > 10:
        findings.append({"severity": "act", "area": "security", "title": f"The weekly security check last ran {age} days ago",
                         "detail": "Patches and checks may be missing.",
                         "fix": "systemctl status buddy-maintain.timer; run it now: sudo bash /opt/buddy/deploy/security.sh check"})
    return {"at": report.get("at"), "status": report.get("status", "unknown"), "done": report.get("done", []),
            "findings": findings}


def save(memory: Memory, report: dict) -> int:
    cursor = memory.db.execute(
        "INSERT INTO health_reports (at, status, findings, metrics) VALUES (?, ?, ?, ?)",
        (report["at"], report["status"], json.dumps(report["findings"]), json.dumps(report["metrics"])))
    cutoff = (datetime.now(timezone.utc) - timedelta(days=KEEP_DAYS)).isoformat()
    memory.db.execute("DELETE FROM health_reports WHERE at < ?", (cutoff,))
    memory.db.commit()
    return cursor.lastrowid


def latest(memory: Memory) -> dict | None:
    row = memory.db.execute("SELECT * FROM health_reports ORDER BY id DESC LIMIT 1").fetchone()
    if not row:
        return None
    return {"id": row["id"], "at": row["at"], "status": row["status"], "findings": json.loads(row["findings"]),
            "metrics": json.loads(row["metrics"])}


def history(memory: Memory, limit: int = 60) -> list[dict]:
    """One line per report: the trend of the main numbers over time."""
    out = []
    for row in memory.db.execute("SELECT * FROM health_reports ORDER BY id DESC LIMIT ?", (limit,)):
        week = json.loads(row["metrics"]).get("last_7_days", {})
        q = week.get("quality", {})
        out.append({"id": row["id"], "at": row["at"], "status": row["status"],
                    "findings": len(json.loads(row["findings"])),
                    "first_word_median": week.get("first_word_median"), "total_p95": week.get("total_p95"),
                    "failed_reply_rate": week.get("failed_reply_rate"), "accuracy": q.get("accuracy"),
                    "satisfaction": q.get("satisfaction"), "cost_usd": week.get("cost_usd"),
                    "cache_hit": week.get("cache_hit")})
    return out


def morning_line(memory: Memory) -> str:
    """For the briefing: only when today's check found something to act on."""
    report = latest(memory)
    if not report or report["status"] != "act" or report["at"][:10] != datetime.now(timezone.utc).date().isoformat():
        return ""
    return "My own health check flagged: " + "; ".join(f["title"] for f in report["findings"]
                                                        if f["severity"] == "act")[:300]


def due(memory: Memory) -> bool:
    today = local_now().date().isoformat()
    return local_now().hour >= RUN_AT_HOUR and memory.get("health:last_run") != today


def digest_facts(memory: Memory, report: dict) -> dict:
    """The numbers the digest talks about: the check, plus the week's Insights."""
    from .insights import report as insights_report

    week = insights_report(memory, days=7, recent=1)
    t, sp = week["totals"], week["speed"]
    before = report["metrics"]["previous_7_days"]
    return {
        "status": report["status"],
        "findings": [{k: f[k] for k in ("severity", "title", "detail", "fix")} for f in report["findings"]],
        "this_week": {"replies": sp["replies"], "first_words_s": sp["first_word_median"],
                      "whole_reply_s": sp["total_median"], "model_spend_usd": t["cost_usd"],
                      "saved_by_routing_usd": round(t["saved_by_router_usd"] + t["saved_by_free_usd"], 2),
                      "free_model_calls": t["free_requests"], "free_answers_right": t["accuracy"],
                      "your_rating": t["satisfaction"], "thumbs": [t["thumbs_up"], t["thumbs_down"]],
                      "cache_reuse": t["cache_hit"]},
        "week_before": {"replies": before["replies"], "first_words_s": before["first_word_median"],
                        "model_spend_usd": before["cost_usd"]},
        "security_patches_applied_this_week": report["metrics"].get("security", {}).get("done", []),
        "biggest_costs": [{"what": g["label"], "usd": g["cost_usd"]} for g in week["by_purpose"][:3]],
        "models": [{"model": g["model"], "requests": g["requests"], "usd": g["cost_usd"], "typical_s": g["median_seconds"],
                    "right": g["accuracy"]} for g in week["by_model"][:6]],
    }


def plain_digest(report: dict) -> str:
    """Without Claude (it failed or is over budget): the headline and what to fix."""
    if not report["findings"]:
        return "Daily check: all good. Nothing got slower, less accurate or dearer than last week."
    act = [f for f in report["findings"] if f["severity"] == "act"] or report["findings"]
    return "Daily check: " + "; ".join(f"{f['title']} (fix: {f['fix'].split('.')[0]})" for f in act[:3]) + "."


async def digest_text(memory: Memory, report: dict) -> str:
    from .agent import compose
    from .persona import build_system_prompt

    facts = digest_facts(memory, report)
    text = await compose(
        build_system_prompt(memory.recall(""), memory.open_goals()),
        "Write your daily health report for the user, as a short chat message they'll read and hear. "
        "Plain words, no jargon, no tables. Start with one line: how you did this week, compared with the "
        "week before. Then up to three recommendations, most useful first, each with what to do in a few "
        "words (a setting to change, something to try, or 'nothing needed'). Mention money saved by "
        "routing and accuracy of free models only if there's something worth saying. If everything is "
        "fine, say so in two sentences. End with: 'Tap for the full picture.' Facts (JSON): "
        + json.dumps(facts, ensure_ascii=False),
        memory, label="health_digest",
    )
    return text or plain_digest(report)


def digest_due(memory: Memory) -> bool:
    from .connectors.phone import in_quiet_hours

    today = local_now().date().isoformat()
    return (DIGEST_ON and memory.get("health:digest_pending") is not None and local_now().hour >= DIGEST_AT_HOUR
            and memory.get("health:digest_sent") != today and not in_quiet_hours())


async def send_digest(memory: Memory, deliver) -> bool:
    report = latest(memory)
    if not report:
        return False
    try:
        text = await digest_text(memory, report)
    except Exception:
        log.exception("health digest: writing failed")
        text = plain_digest(report)
    if not await deliver("health", text, status=report["status"]):  # no phone connected yet: try later
        return False
    memory.set("health:digest_sent", local_now().date().isoformat())
    memory.db.execute("DELETE FROM kv WHERE key = 'health:digest_pending'")
    memory.db.commit()
    return True


async def run(memory: Memory, finops=None, deliver=None) -> None:
    """Runs the check once a day, early in the morning, then sends the digest to the chat."""
    while True:
        try:
            if deliver is not None and digest_due(memory):
                await send_digest(memory, deliver)
            if due(memory):
                report = None
                if finops is not None:
                    try:
                        report = await finops.report()
                    except Exception:
                        log.info("health check: costs unavailable")
                saved = check(memory, finops_report=report)
                saved["id"] = save(memory, saved)
                memory.set("health:last_run", local_now().date().isoformat())
                memory.set("health:digest_pending", str(saved.get("id", "")))
                log.info("health check: %s, %d findings", saved["status"], len(saved["findings"]))
        except Exception:
            log.exception("health check failed")
        await asyncio.sleep(CHECK_EVERY_SECONDS)


HEALTH_TOOL = {
    "name": "health_report",
    "description": "Buddy's own daily health check: how fast, accurate, reliable and costly it has been lately "
    "compared with the week before, and what to fix. Use when the user asks how you're doing or performing.",
    "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
}


def run_health_tool(memory: Memory) -> str:
    report = latest(memory)
    if not report:
        report = check(memory)
    week = report["metrics"]["last_7_days"]
    return json.dumps({"status": report["status"], "checked_at": report["at"], "findings": report["findings"],
                       "first_word_median_seconds": week.get("first_word_median"),
                       "free_answer_accuracy": week.get("quality", {}).get("accuracy"),
                       "your_rating": week.get("quality", {}).get("satisfaction")}, ensure_ascii=False)
