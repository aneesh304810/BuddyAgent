"""Cost watch: Buddy's daily look at its own spending, the patterns that waste money, and where in its code they are.

Every morning (code only: no AI, so the analysis itself costs nothing) it reads yesterday's model and API calls against
the 7 days before and looks for the patterns behind the bugs that have cost money before:

  loop          one kind of background work calling far more often than it's useful (the briefing that was rewritten
                every 15 seconds while the phone was away; the market scan that re-ran all evening)
  repeat        the same request sent again and again (an answer thrown away and asked for again)
  spike         a kind of work costing 3x or more its usual day
  big model     a premium model writing short background text a quick model would do as well
  no cache      big prompts sent again and again without prompt caching
  cut off       answers stopping at the token limit (paid for, then often redone)
  failing       paid calls failing (paid for, nothing used) and free models failing (work falls to paid ones)
  duplicates    the same report saved many times in a day

Each finding has its cost, an estimated monthly saving, a recommendation, and where it lives in the code (file:line,
found by searching the running code for the purpose's name). The report lands in Reports, a short message goes out
in the morning, and a ready brief lets Buddy fix itself: a code task on its own repo (tests run, pushed to a branch
only after the user's yes; never deployed by Buddy).
"""

import hashlib
import json
import logging
import re
from datetime import datetime, timedelta, timezone

from .memory import Memory
from .tools import local_now

log = logging.getLogger(__name__)

PREMIUM = ("claude-opus", "claude-sonnet", "claude-fable")
BACKGROUND = {"briefing", "reminder", "evening_wrap", "health_digest", "market_scan", "markets:take", "trends", "web_digest", "deals", "lifelog",
              "library", "think", "connect", "card_lookup", "credit_email", "credit_deals", "bankfeed", "mail",
              "money_review", "card_credit_links", "finance:statement", "credit_report"}
# Kinds of work that hold none of the user's data, which free models do first (Claude only when none answers well).
PUBLIC_WORK = {"markets:take", "trends", "web_digest", "health_digest", "card_lookup", "credit_deals",
               "card_credit_links"}
LOOP_CALLS_PER_DAY = 96          # background work more often than every 15 minutes, all day
REPEAT_MIN = 5                   # the same request this many times in a day
SPIKE_X, SPIKE_MIN_USD = 3.0, 0.50
BIG_PROMPT, LOW_CACHE = 8_000, 0.3
SHORT_OUTPUT = 400
HAIKU_SHARE = 0.05               # a quick model costs about this share of a premium one for the same tokens
REPORT_KEEP_DAYS = 60


def _rows(memory: Memory, since: str, until: str) -> list[dict]:
    try:
        return [dict(r) for r in memory.db.execute(
            "SELECT at, provider, model, purpose, input_tokens, output_tokens, cache_read_tokens, cost_usd, ok, stop, "
            "request FROM model_calls WHERE at >= ? AND at < ?", (since, until))]
    except Exception:
        return []


def _api_rows(memory: Memory, since: str, until: str) -> list[dict]:
    try:
        return [dict(r) for r in memory.db.execute(
            "SELECT at, provider, endpoint, ok, cost_usd FROM api_calls WHERE at >= ? AND at < ?", (since, until))]
    except Exception:
        return []


def _by(rows: list[dict], key) -> dict:
    out: dict = {}
    for r in rows:
        e = out.setdefault(key(r), {"calls": 0, "usd": 0.0, "input": 0, "output": 0, "cache": 0, "failed": 0,
                                    "cut": 0, "models": {}})
        e["calls"] += 1
        e["usd"] += float(r.get("cost_usd") or 0)
        e["input"] += int(r.get("input_tokens") or 0)
        e["output"] += int(r.get("output_tokens") or 0)
        e["cache"] += int(r.get("cache_read_tokens") or 0)
        e["failed"] += 0 if r.get("ok", 1) else 1
        e["cut"] += 1 if r.get("stop") == "max_tokens" else 0
        if r.get("model"):
            e["models"][r["model"]] = e["models"].get(r["model"], 0) + 1
    return out


def where_in_code(purpose: str, limit: int = 3) -> list[str]:
    """file:line of the code that does this kind of work (the running checkout): where the calls are recorded under
    that purpose first, then where its name appears."""
    from . import selfcode  # noqa: PLC0415
    name = purpose.split(":")[0]
    exact = re.compile(r"(purpose|label|kind)\s*=\s*[\"']" + re.escape(purpose) + r"[\"']")
    loose = re.compile(r"[\"']" + re.escape(name) + r"[\"':]")
    first, then = [], []
    try:
        for rel, path in selfcode._files():
            rel = str(rel)
            if not rel.startswith("backend/buddy/") or not rel.endswith(".py") or \
                    rel.endswith(("/costwatch.py", "/budgets.py", "/insights.py", "/finops.py")):
                continue
            for n, line in enumerate(path.read_text(errors="ignore").splitlines(), 1):
                if exact.search(line):
                    first.append(f"{rel}:{n}")
                elif loose.search(line):
                    then.append(f"{rel}:{n}")
    except Exception:
        log.debug("code search failed", exc_info=True)
    return (first + then)[:limit]


def analyse(memory: Memory, day: datetime | None = None, start: datetime | None = None,
            end: datetime | None = None) -> dict:
    """Yesterday (or the day before `day`) against the 7 days before it; or any window start..end (default now),
    e.g. since the cost reset, against the 7 days before it."""
    if start is None:
        end = (day or datetime.now(timezone.utc)).astimezone(timezone.utc).replace(hour=0, minute=0, second=0,
                                                                                      microsecond=0)
        start = end - timedelta(days=1)
    else:
        end = end or datetime.now(timezone.utc)
    week = start - timedelta(days=7)
    per = max((end - start).total_seconds() / 86400, 1 / 24)  # the window in days
    k = 30 / per  # the window's spend -> a month's
    rows = _rows(memory, start.isoformat(), end.isoformat())
    before = _rows(memory, week.isoformat(), start.isoformat())
    apis = _api_rows(memory, start.isoformat(), end.isoformat())
    today = _by(rows, lambda r: r["purpose"] or "chat")
    usual = {k: {**v, "usd": v["usd"] / 7, "calls": v["calls"] / 7}
             for k, v in _by(before, lambda r: r["purpose"] or "chat").items()}
    total = sum(v["usd"] for v in today.values()) + sum(float(a["cost_usd"] or 0) for a in apis)
    usual_total = sum(v["usd"] for v in usual.values())
    findings = []

    def find(kind: str, purpose: str, cost: float, saving_month: float, what: str, fix: str) -> None:
        findings.append({"kind": kind, "purpose": purpose, "cost_usd": round(cost, 2),
                         "saving_month_usd": round(saving_month, 2), "what": what, "fix": fix,
                         "code": where_in_code(purpose) if purpose else []})

    for purpose, e in today.items():
        u = usual.get(purpose, {"usd": 0.0, "calls": 0})
        background = purpose in BACKGROUND or purpose.startswith(("free:", "markets"))
        if background and e["calls"] / per >= LOOP_CALLS_PER_DAY:
            find("loop", purpose, e["usd"], e["usd"] * k * 0.9,
                 f"{purpose}: {e['calls'] / per:.0f} calls a day (${e['usd'] / per:.2f} a day), more than one every 15 "
                 "minutes",
                 "Look for a loop that redoes the work on every pass: write it once and keep the result until it's "
                 "used, mark it done before the slow part, and do nothing while no phone is connected.")
        if e["usd"] / per >= SPIKE_MIN_USD and u["usd"] and e["usd"] / per >= u["usd"] * SPIKE_X:
            find("spike", purpose, e["usd"], (e["usd"] / per - u["usd"]) * 30,
                 f"{purpose}: ${e['usd'] / per:.2f} a day, {e['usd'] / per / u['usd']:.0f}x its usual ${u['usd']:.2f} a day "
                 f"({e['calls']} calls vs about {u['calls']:.0f})",
                 "Find what changed: a new trigger, a retry without a limit, or a date check that never matches.")
        premium = sum(n for m, n in e["models"].items() if m.startswith(PREMIUM))
        if background and premium and e["usd"] >= 0.2 and e["output"] / max(e["calls"], 1) < SHORT_OUTPUT:
            find("big model", purpose, e["usd"], e["usd"] * k * (1 - HAIKU_SHARE),
                 f"{purpose}: a premium model wrote short background text ({e['output'] // max(e['calls'], 1)} "
                 f"words-ish a call, ${e['usd']:.2f})",
                 "Use the quick model (Haiku) for this: short, routine writing doesn't need the main model.")
        if e["calls"] >= 10 and e["input"] / e["calls"] >= BIG_PROMPT and \
                e["cache"] / max(e["input"] + e["cache"], 1) < LOW_CACHE and e["usd"] >= 0.2:
            find("no cache", purpose, e["usd"], e["usd"] * k * 0.5,
                 f"{purpose}: {e['input'] // e['calls']:,} prompt tokens a call, little of it cached",
                 "Put the unchanging part first (instructions, tools) with cache_control, and the changing part last.")
        if e["cut"] >= 3:
            find("cut off", purpose, e["usd"] * e["cut"] / e["calls"], e["usd"] * e["cut"] / e["calls"] * k,
                 f"{purpose}: {e['cut']} answers stopped at the token limit",
                 "Raise max_tokens for this, or ask for a shorter answer, so it isn't paid for and redone.")
        if e["failed"] >= 5 and e["failed"] / e["calls"] >= 0.3:
            find("failing", purpose, 0.0, 0.0,
                 f"{purpose}: {e['failed']} of {e['calls']} calls failed",
                 "Find the error (Insights shows it); failing free models push work to paid ones, failing paid ones "
                 "are paid for nothing.")

    repeats: dict = {}
    for r in rows:
        if r["request"]:
            key = (r["purpose"], hashlib.sha1(r["request"].encode()).hexdigest())
            e = repeats.setdefault(key, {"n": 0, "usd": 0.0})
            e["n"] += 1
            e["usd"] += float(r["cost_usd"] or 0)
    for (purpose, _), e in sorted(repeats.items(), key=lambda kv: -kv[1]["usd"]):
        if e["n"] >= REPEAT_MIN and e["usd"] >= 0.05 and not any(f["purpose"] == purpose and f["kind"] == "loop"
                                                                    for f in findings):
            find("repeat", purpose, e["usd"], e["usd"] * k * (1 - 1 / e["n"]),
                 f"{purpose}: the same request sent {e['n']} times (${e['usd']:.2f})",
                 "Keep the first answer and reuse it, or find why it's asked again.")

    by_api = _by([{**a, "purpose": a["provider"]} for a in apis], lambda r: r["provider"])
    for provider, e in by_api.items():
        if e["failed"] >= 10 and e["failed"] / e["calls"] >= 0.3:
            find("failing", provider, e["usd"], 0.0, f"{provider}: {e['failed']} of {e['calls']} calls failed",
                 "Check its key and limits; stop retrying it until it's fixed.")

    try:
        for r in memory.db.execute("SELECT title, COUNT(*) AS n FROM jobs WHERE created_at >= ? AND created_at < ? "
                                   "GROUP BY kind, title HAVING n >= 3", (start.isoformat(), end.isoformat())):
            find("duplicates", "", 0.0, 0.0, f"\"{r['title']}\" was saved {r['n']} times",
                 "A report should be saved once a day: mark it done before running, and replace rather than add.")
    except Exception:
        pass

    try:
        import time as _time  # noqa: PLC0415
        for r in memory.db.execute("SELECT key, value FROM kv WHERE key LIKE 'free:rest:%'"):
            if float(r["value"] or 0) > _time.time():
                name = r["key"].split(":", 2)[2]
                find("failing", f"free:{name}", 0.0, 0.0, f"the free model {name} keeps failing and is resting; "
                     "its work goes to other free models or to paid Claude meanwhile",
                     f"Check {name}'s key and quota (GitHub Models: a fine-grained token with Models: read).")
    except Exception:
        pass

    # Work that holds none of the user's data, done by paid Claude: the free models failed or weren't set up.
    for purpose, e in today.items():
        if purpose not in PUBLIC_WORK or e["usd"] < 0.05:
            continue
        paid = sum(n for m, n in e["models"].items() if m.startswith("claude"))
        if paid:
            find("could be free", purpose, e["usd"], e["usd"] / per * 30,
                 f"{purpose}: {paid} of {e['calls']} calls went to paid Claude, though it holds none of your data",
                 "The free models failed or aren't set up for this: fix the failing ones (see 'failing') or add a "
                 "free key (Gemini, GitHub Models, OpenRouter), and it goes back to free.")

    findings.sort(key=lambda f: -f["saving_month_usd"])
    top = sorted(({"purpose": k, "usd": round(v["usd"], 2), "calls": v["calls"],
                   "usual_usd": round(usual.get(k, {}).get("usd", 0.0) * per, 2)} for k, v in today.items()),
                 key=lambda x: -x["usd"])[:8]
    same_day = per <= 1.01 and start.hour == 0 and start.minute == 0
    label = start.date().isoformat() if same_day else \
        f"{start.astimezone(local_now().tzinfo):%b %-d %H:%M} – {end.astimezone(local_now().tzinfo):%b %-d %H:%M}"
    return {"day": label, "days": round(per, 2), "total_usd": round(total, 2), "usual_usd": round(usual_total * per, 2),
            "top": top, "findings": findings,
            "saving_month_usd": round(sum(f["saving_month_usd"] for f in findings), 2)}


def _when(report: dict) -> str:
    return "Yesterday" if " – " not in report["day"] else f"From {report['day']}"


def archive(memory: Memory, start: datetime, end: datetime) -> int | None:
    """Before a cost reset: what the period cost, by kind of work, provider and model, and what failed, kept in
    Reports (the reset only changes what the budgets count; no call record is deleted)."""
    rows = _rows(memory, start.isoformat(), end.isoformat())
    apis = _api_rows(memory, start.isoformat(), end.isoformat())
    if not rows and not apis:
        return None
    from .jobs import SCHEMA as JOBS_SCHEMA  # noqa: PLC0415
    by_purpose = sorted(_by(rows, lambda r: r["purpose"] or "chat").items(), key=lambda kv: -kv[1]["usd"])
    by_provider = sorted(_by(rows, lambda r: f"{r['provider']} · {r['model']}").items(), key=lambda kv: -kv[1]["usd"])
    by_api = sorted(_by([{**a, "purpose": a["provider"]} for a in apis], lambda r: r["provider"]).items(),
                    key=lambda kv: -kv[1]["usd"])
    total = sum(v["usd"] for _, v in by_purpose) + sum(v["usd"] for _, v in by_api)
    tz = local_now().tzinfo
    span = f"{start.astimezone(tz):%b %-d} – {end.astimezone(tz):%b %-d, %Y}"
    md = [f"# Cost archive, {span}", "",
          f"Before the cost reset on {end.astimezone(tz):%b %-d at %H:%M}: **${total:.2f}** on AI models and paid "
          f"APIs, {len(rows)} model requests ({sum(v['failed'] for _, v in by_purpose)} failed). Fixed costs (server, "
          "plans) are in the cost report.", "", "## By kind of work", "",
          "| Work | Cost | Requests | Failed |", "|---|---|---|---|"]
    md += [f"| {k} | ${v['usd']:.2f} | {v['calls']} | {v['failed']} |" for k, v in by_purpose]
    md += ["", "## By model", "", "| Model | Cost | Requests | Failed |", "|---|---|---|---|"]
    md += [f"| {k} | ${v['usd']:.2f} | {v['calls']} | {v['failed']} |" for k, v in by_provider]
    if by_api:
        md += ["", "## Outside APIs", "", "| Service | Cost | Calls | Failed |", "|---|---|---|---|"]
        md += [f"| {k} | ${v['usd']:.2f} | {v['calls']} | {v['failed']} |" for k, v in by_api]
    found = analyse(memory, start=start, end=end)["findings"]
    if found:
        md += ["", "## What was wasting money then", ""] + [f"- [{f['kind']}] {f['what']}" for f in found]
    memory.db.executescript(JOBS_SCHEMA)
    now = datetime.now(timezone.utc).isoformat()
    summary = f"Cost archive {span}: ${total:.2f} on AI models and paid APIs, {len(rows)} requests."
    job_id = memory.db.execute(
        "INSERT INTO jobs (kind, title, params, status, created_at, started_at, finished_at, summary, report, findings, "
        "sources, delivered) VALUES ('cost_archive', ?, '{}', 'done', ?, ?, ?, ?, ?, '[]', '[]', 1)",
        (f"Cost archive, {span}", now, now, now, summary, "\n".join(md))).lastrowid
    memory.db.commit()
    return job_id


def since_reset(memory: Memory) -> dict | None:
    """The cost watch from the last cost reset to now: what's expensive and what's failing since the fix."""
    from . import budgets  # noqa: PLC0415
    reset = budgets.last_reset(memory)
    if not reset:
        return None
    return analyse(memory, start=datetime.fromisoformat(reset), end=datetime.now(timezone.utc) + timedelta(minutes=1))


def fix_brief(report: dict) -> str:
    """The brief for a code task that fixes the money-wasting patterns found (Buddy fixing itself)."""
    lines = [f"Cost watch found these money-wasting patterns on {report['day']}. For each: read the code shown, find "
             "the root cause, fix it with a test that proves the waste is gone, keep behaviour the same otherwise."]
    for f in report["findings"]:
        if f["kind"] in ("duplicates",) and not f["code"]:
            pass
        lines.append(f"- [{f['kind']}] {f['what']}. Fix: {f['fix']}" + (f" Code: {', '.join(f['code'])}." if f["code"]
                                                                          else ""))
    return "\n".join(lines)


def markdown(report: dict, budget_lines: list[str]) -> str:
    out = [f"# Cost watch, {report['day']}", "",
           f"{_when(report)} Buddy spent **${report['total_usd']:.2f}** on AI models and paid APIs "
           f"(usual for that long, from the week before: ${report['usual_usd']:.2f})."]
    out += [""] + budget_lines if budget_lines else []
    out += ["", "## Where it went", "", "| Work | Spent | Calls | Usual |", "|---|---|---|---|"]
    out += [f"| {t['purpose']} | ${t['usd']:.2f} | {t['calls']} | ${t['usual_usd']:.2f} |" for t in report["top"]]
    out += ["", "## Money wasted, and how to fix it", ""]
    if not report["findings"]:
        out.append("Nothing wasteful found: no loops, repeats, spikes or failing services.")
    for i, f in enumerate(report["findings"], 1):
        out += [f"### {i}. {f['kind'].title()}: {f['what']}", "",
                f"- **Fix:** {f['fix']}",
                f"- **Saves:** about ${f['saving_month_usd']:.2f} a month" if f["saving_month_usd"] else
                "- **Saves:** stops waste (cost not measurable)"]
        if f["code"]:
            out.append(f"- **Code:** {', '.join(f['code'])}")
        out.append("")
    if report["findings"]:
        out += ["## Let Buddy fix it", "",
                "Say \"fix the cost bugs\": Buddy starts a code task on its own repo with the brief below, runs the "
                "tests, and pushes a branch only after your yes. It never deploys itself.", "", "```",
                fix_brief(report), "```"]
    out += ["", "## Keeping cost down", "",
            "- The Claude cutoff and the whole-Buddy budget stop spending at their limits (Insights, Budgets).",
            "- Background work pauses when the forecast passes a budget, until you approve the pace.",
            "- Short background writing (nudges, scan notes) is cheapest on the quick model.",
            "- Free models take general questions; when one keeps failing its work falls to paid models: fix its key."]
    return "\n".join(out)


def message(report: dict) -> str:
    text = f"Cost watch: {_when(report).lower()} ${report['total_usd']:.2f} (usual ${report['usual_usd']:.2f})."
    if report["findings"]:
        f = report["findings"][0]
        text += (f" {len(report['findings'])} thing{'s' if len(report['findings']) != 1 else ''} wasting money; "
                 f"the biggest: {f['what']}.")
        if report["saving_month_usd"]:
            text += f" Fixing them saves about ${report['saving_month_usd']:.0f} a month."
        text += " Full report in Reports; say \"fix the cost bugs\" and I'll fix myself (with your yes before pushing)."
    else:
        text += " Nothing wasteful found."
    return text


def save(memory: Memory, report: dict, budget_lines: list[str]) -> int:
    from .jobs import SCHEMA as JOBS_SCHEMA  # noqa: PLC0415
    memory.db.executescript(JOBS_SCHEMA)
    now = datetime.now(timezone.utc).isoformat()
    title = f"Cost watch, {report['day']}"
    memory.db.execute("DELETE FROM jobs WHERE kind = 'cost_watch' AND (title = ? OR created_at < ?)",
                      (title, (datetime.now(timezone.utc) - timedelta(days=REPORT_KEEP_DAYS)).isoformat()))
    job_id = memory.db.execute(
        "INSERT INTO jobs (kind, title, params, status, created_at, started_at, finished_at, summary, report, findings, "
        "sources, delivered) VALUES ('cost_watch', ?, '{}', 'done', ?, ?, ?, ?, ?, ?, '[]', 1)",
        (title, now, now, now, message(report), markdown(report, budget_lines),
         json.dumps([f["what"] for f in report["findings"]]))).lastrowid
    memory.set("costwatch:latest", json.dumps({"id": job_id, "brief": fix_brief(report) if report["findings"] else ""}))
    memory.db.commit()
    return job_id


def due(memory: Memory) -> bool:
    """Once a day, from 7 AM local."""
    now = local_now()
    return now.hour >= 7 and memory.get("costwatch:last") != now.date().isoformat()


def budget_lines(memory: Memory) -> list[str]:
    from . import budgets  # noqa: PLC0415
    try:
        p = budgets.predict(memory)
    except Exception:
        return []
    out = []
    for l in p["limits"]:
        name = "Claude" if l["which"] == "claude" else "The whole of Buddy"
        out.append(f"- {name}: ${l['spent_usd']:.2f} so far, heading for **${l['forecast_usd']:.2f}** against "
                   f"${l['cap_usd']:.0f}" + (f", reached around {l['reach_on']}" if l["reach_on"] else "")
                   + (" ⚠️" if l["over"] else "") + ".")
    return out


async def run_daily(memory: Memory, deliver) -> None:
    if not due(memory):
        return
    memory.set("costwatch:last", local_now().date().isoformat())
    report = analyse(memory)
    save(memory, report, budget_lines(memory))
    if report["findings"] or report["total_usd"] >= 0.5:
        await deliver("money", message(report))


COSTWATCH_TOOL = {
    "name": "cost_watch",
    "description": "Buddy's own cost analysis: yesterday's spend on AI models and paid APIs against a usual day, and "
    "the patterns wasting money (loops, repeated requests, spikes, a premium model for short background text, big "
    "prompts without caching, answers cut off, failing services, duplicate reports), each with its cost, the monthly "
    "saving, the fix and where it is in the code (file:line). analyse (day?: YYYY-MM-DD, default yesterday). "
    "since_reset: the same from the last cost reset to now (what's expensive and failing since the fix; the costs "
    "before it are in a 'Cost archive' report). trace (period: today | since_reset, or days): the request trace, an "
    "Excel file of every model and API call with what each cost and why (cache, output...), plus summaries; it "
    "appears in the chat. "
    "fix_brief: the brief for fixing them; when the user asks to fix the cost bugs, pass it to buddy_code fix.",
    "input_schema": {"type": "object", "properties": {
        "action": {"type": "string", "enum": ["analyse", "since_reset", "fix_brief", "trace"]}, "day": {"type": "string"},
        "period": {"type": "string", "enum": ["today", "since_reset"]}, "days": {"type": "integer"}},
        "required": ["action"], "additionalProperties": False},
}


def run_tool(memory: Memory, args: dict) -> str:
    if args.get("action") == "trace":
        from . import makers, trace  # noqa: PLC0415
        file = trace.make(memory, int(args.get("days") or 0), str(args.get("period") or ""))
        card = makers._show(makers._card(file, "Request trace", "every call, its cost and why"))
        return json.dumps({"made": file["name"], "file_id": file["id"], "card": card,
                           "note": "It's in the chat; say what the Summary sheet shows in two lines."})
    if args.get("action") == "since_reset":
        r = since_reset(memory)
        if r is None:
            return "There's been no cost reset yet. Say \"start my budget fresh\" to start one."
        return json.dumps(r, ensure_ascii=False)
    if args.get("action") == "fix_brief":
        latest = json.loads(memory.get("costwatch:latest") or "{}")
        brief = latest.get("brief") or fix_brief(analyse(memory))
        return brief if "- [" in brief else "Nothing wasteful found to fix."
    day = None
    if args.get("day"):
        try:
            day = datetime.fromisoformat(str(args["day"])).replace(tzinfo=timezone.utc) + timedelta(days=1)
        except ValueError:
            pass
    report = analyse(memory, day)
    return json.dumps({**report, "budgets": budget_lines(memory)}, ensure_ascii=False)
