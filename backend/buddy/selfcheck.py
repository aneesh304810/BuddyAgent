"""Buddy looks at its own server: its log, memory, CPU and disk, and says what's wrong and how to fix it.

Once a week, Friday morning (SELFCHECK_DAY, SELFCHECK_HOUR), over the past week (memory and disk are read every
hour in between, locally and free, so the report covers the whole week):
  log      the past week of Buddy's own service log (journalctl -u buddy; the service is in the systemd-journal group
           for this), only problem lines: crashes and out-of-memory kills, tracebacks, errors, warnings, call audio
           stutters. Call words (the "heard:"/"decided:" lines of call debugging) are skipped, numbers, emails and
           account-like strings are masked. Grouped by cause ("mail sorting: BudgetStop, 40 times").
  server   memory (and swap), load, disk, from this machine; the DigitalOcean droplet's size and price with a
           read-only token (BUDDY_DO_TOKEN, droplet:read), so a resize can be suggested. Buddy never resizes,
           reboots or changes the server: the owner does that in DigitalOcean.
  diagnose only when a new kind of problem appears, one small Claude call (the quick model) explains each in plain
           words with the fix; code problems come with a brief for Claude Code (code instructions only), which the
           owner sends with "send it to Claude" (claude_code_fix asks first, then self-deploy tests and rolls back).
  tell     the weekly report as one message (outside quiet hours); each problem at most once a week.
Kept: the last report and 7 days of server samples. Nothing leaves the server except the masked lines to Claude.
"""

import json
import logging
import os
import re
import shutil
import subprocess  # nosec B404: journalctl only, fixed arguments
import urllib.request
from datetime import datetime, timedelta, timezone

from .memory import Memory
from .tools import require_https

log = logging.getLogger(__name__)
SELFCHECK_DAY = 4     # Friday (Monday is 0)
SELFCHECK_HOUR = 9    # local time, or the first hourly look after it
SELFCHECK_HOURS = 7 * 24  # the window it reads: the past week
LAST_RUN_KEY = "selfcheck:last_run"
REPORT_KEY = "selfcheck:last"
SAMPLES_KEY = "selfcheck:samples"   # [{at, mem_used_pct, swap_used_pct, load, disk_used_pct}], 7 days
TOLD_KEY = "selfcheck:told"         # {signature: iso time}: each problem told at most once a day
PROBLEM = re.compile(r"Traceback|Error|ERROR|Exception|WARNING|killed by the OOM|oom-kill|Failed with result|"
                     r"held up|failed|refused|timed out|BudgetStop", re.I)
PRIVATE = re.compile(r" (heard|decided): ")  # call words while debugging: never read
MASKS = [(re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+"), "<email>"), (re.compile(r"\+?\d[\d\s().-]{7,}\d"), "<number>"),
         (re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b"), "<id>"),
         (re.compile(r"\b(sk|pk|key|tok)[-_][A-Za-z0-9_-]{8,}"), "<secret>")]


def _clean(text: str) -> str:
    for rx, sub in MASKS:
        text = rx.sub(sub, text)
    try:
        from .privacy import mask  # noqa: PLC0415
        text = mask(text)
    except Exception:
        pass
    return text[:300]


def read_log(hours: float = SELFCHECK_HOURS, run=None) -> list[tuple[str, str]]:
    """(time, message) of Buddy's log lines in the last hours ([] if the log can't be read)."""
    run = run or subprocess.run
    try:
        got = run(["journalctl", "-u", "buddy", "--since", f"-{int(hours * 60)}min", "-o", "json", "--no-pager",
                   "-n", "300000"], capture_output=True, text=True, timeout=30, check=False)  # nosec B603 B607
    except (OSError, subprocess.TimeoutExpired) as e:
        log.info("self-check: log not readable: %s", e)
        return []
    out = []
    for line in (got.stdout or "").splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        msg = row.get("MESSAGE")
        if isinstance(msg, list):  # binary messages come as byte lists
            msg = bytes(msg).decode("utf-8", "replace")
        ts = row.get("__REALTIME_TIMESTAMP", "")
        at = datetime.fromtimestamp(int(ts) / 1e6, timezone.utc).isoformat() if str(ts).isdigit() else ""
        out.append((at, str(msg or "")))
    return out


def _kind(msg: str) -> str:
    m = msg.lower()
    if "oom" in m and "kill" in m:
        return "out of memory"
    if "failed with result" in m or "main process exited" in m:
        return "crash"
    if "held up" in m:
        return "call audio stutter"
    if "budgetstop" in m:
        return "budget pause"
    if "traceback" in m or "error" in m or "exception" in m:
        return "error"
    return "warning"


def _signature(msg: str, where: str) -> str:
    text = re.sub(r"\d+(\.\d+)?", "#", msg)
    text = re.sub(r"\s+", " ", text).strip()[:120]
    return f"{where} | {text}" if where else text


def problems(lines: list[tuple[str, str]]) -> list[dict]:
    """Problem lines grouped by cause, most frequent first. A traceback becomes one problem: its last line (the
    error) at its innermost Buddy file and line."""
    groups: dict[str, dict] = {}
    where, trace = "", False

    def add(at: str, msg: str, kind: str, place: str) -> None:
        sig = _signature(msg, place)
        g = groups.setdefault(sig, {"signature": sig, "kind": kind, "count": 0, "first": at, "last": at,
                                    "sample": _clean(msg), "where": place})
        g["count"] += 1
        g["last"] = at or g["last"]
    for at, msg in lines:
        if PRIVATE.search(msg):
            continue
        body = re.sub(r"^\S+ \S+ \S+ ", "", msg) if re.match(r"^\d{4}-\d\d-\d\d \d", msg) else msg
        if body.startswith("Traceback"):
            trace, where = True, ""
            continue
        if trace:
            m = re.search(r'File "[^"]*/(buddy/[\w/]+\.py)", line (\d+), in (\w+)', body)
            if m:
                where = f"{m.group(1)}:{m.group(3)}"
                continue
            if body.startswith(" "):
                continue
            trace = False
            add(at, body, _kind(body) if "budgetstop" in body.lower() else "error", where)
            continue
        if PROBLEM.search(body) and not body.startswith(("INFO:", "DEBUG")) and "| DEBUG" not in body:
            add(at, body, _kind(body), "")
    order = {"out of memory": 0, "crash": 1, "error": 2, "call audio stutter": 3, "budget pause": 4, "warning": 5}
    return sorted(groups.values(), key=lambda g: (order.get(g["kind"], 9), -g["count"]))[:12]


def server_now() -> dict:
    """This machine right now: memory and swap use, load per CPU, disk use."""
    info = {}
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                k, v = line.split(":", 1)
                info[k] = int(v.split()[0])
    except OSError:
        pass
    total, avail = info.get("MemTotal", 0), info.get("MemAvailable", 0)
    swap, swap_free = info.get("SwapTotal", 0), info.get("SwapFree", 0)
    disk = shutil.disk_usage("/")
    try:
        load = os.getloadavg()[0] / max(os.cpu_count() or 1, 1)
    except OSError:
        load = 0.0
    return {"memory_gb": round(total / 1048576, 1), "mem_used_pct": round(100 * (1 - avail / total)) if total else None,
            "swap_gb": round(swap / 1048576, 1), "swap_used_pct": round(100 * (1 - swap_free / swap)) if swap else None,
            "load_per_cpu": round(load, 2), "cpus": os.cpu_count(),
            "disk_used_pct": round(100 * disk.used / disk.total), "disk_free_gb": round(disk.free / 1e9, 1)}


def _remember(memory: Memory, now: dict) -> list[dict]:
    try:
        samples = json.loads(memory.get(SAMPLES_KEY) or "[]")
    except ValueError:
        samples = []
    cut = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    samples = [s for s in samples if s["at"] >= cut]
    samples.append({"at": datetime.now(timezone.utc).isoformat(), **{k: now.get(k) for k in (
        "mem_used_pct", "swap_used_pct", "load_per_cpu", "disk_used_pct")}})
    memory.set(SAMPLES_KEY, json.dumps(samples[-24 * 7 * 2:]))
    return samples


def droplet(env=None, fetch=None) -> dict:
    """This server's DigitalOcean size and price (read-only token), or {}."""
    token = (env or os.environ).get("BUDDY_DO_TOKEN", "").strip()
    if not token:
        return {}

    def get(url):
        if fetch:
            return fetch(url)
        req = urllib.request.Request(require_https(url), headers={"Authorization": f"Bearer {token}"})
        with urllib.request.urlopen(req, timeout=15) as r:  # https only  # nosec B310
            return json.loads(r.read())
    try:
        host = os.uname().nodename
        found = get("https://api.digitalocean.com/v2/droplets?per_page=50").get("droplets", [])
        for d in found:
            if d.get("name") == host or len(found) == 1:  # this machine (or the account's only droplet)
                size = d.get("size") or {}
                return {"name": d.get("name"), "size": d.get("size_slug"), "memory_mb": size.get("memory"),
                        "vcpus": size.get("vcpus"), "price_monthly": size.get("price_monthly"),
                        "region": (d.get("region") or {}).get("slug")}
    except Exception as e:  # no droplet:read on the token, or the API is down
        log.info("self-check: droplet info unavailable: %s", e)
    return {}


def advice(now: dict, samples: list[dict], found: list[dict], drop: dict) -> list[str]:
    """Plain rules about the server (no AI needed)."""
    out = []
    high = [s for s in samples if (s.get("mem_used_pct") or 0) >= 90]
    if any(p["kind"] == "out of memory" for p in found) or len(high) >= 4:
        size = f" (now {drop['memory_mb'] // 1024} GB, ${drop['price_monthly']}/month)" if drop.get("memory_mb") else ""
        out.append(f"Memory is too tight{size}: Buddy was killed or nearly so. Resize the droplet to the next size up "
                   "in DigitalOcean (Droplet, Resize, CPU and RAM only). Buddy never resizes it itself.")
    if now.get("swap_gb") == 0 and (now.get("mem_used_pct") or 0) >= 80:
        out.append("There's no swap: add 2 GB as a safety net (fallocate, mkswap, swapon; see the chat).")
    if (now.get("disk_used_pct") or 0) >= 85:
        out.append(f"Disk is {now['disk_used_pct']}% full ({now['disk_free_gb']} GB free): old backups or Docker "
                   "images may need clearing.")
    if (now.get("load_per_cpu") or 0) >= 1.5:
        out.append("The CPU is overloaded: calls will stutter. A bigger droplet (more CPUs) helps.")
    return out


DIAG_PROMPT = """You are Buddy's own maintenance engineer. Buddy is a personal assistant (FastAPI, Python, Pipecat
for phone calls) on a small Ubuntu server. Below: problems from its own log, grouped, and the server's state.
For each problem write: what it means in one plain sentence; the fix (a setting, an action for the owner, or a code
change); and, only for a code change, a brief for Claude Code: what's wrong, which file and function, what done looks
like. Code instructions only: never personal details. Answer JSON: {"problems": [{"signature": "...", "meaning":
"...", "fix": "...", "code_fix": true|false, "brief": "..."}]}"""


def diagnose(found: list[dict], now: dict, create=None, memory=None) -> list[dict]:
    """One quick Claude call for the problems (masked lines only). Falls back to plain text if Claude is paused."""
    if not found:
        return []
    try:
        if create is None:
            from .config import claude_client, settings  # noqa: PLC0415
            client = claude_client()

            def create(**kw):
                return client.messages.create(model=settings.fast_model, **kw)
        body = json.dumps({"problems": [{k: p[k] for k in ("signature", "kind", "count", "sample", "where")}
                                        for p in found], "server": now})
        resp = create(max_tokens=1500, system=DIAG_PROMPT, thinking={"type": "disabled"},
                      output_config={"effort": "low"}, messages=[{"role": "user", "content": body}])
        if memory is not None and hasattr(resp, "usage"):  # shows in Insights as "Self-check"
            from . import usage  # noqa: PLC0415
            usage.record(memory, resp.model, resp.usage, purpose="selfcheck", response=resp)
        text = "".join(getattr(b, "text", "") for b in resp.content)
        got = json.loads(text[text.find("{"): text.rfind("}") + 1])["problems"]
        by = {g.get("signature"): g for g in got if isinstance(g, dict)}
        return [{**p, **{k: by.get(p["signature"], {}).get(k, "") for k in ("meaning", "fix", "brief")},
                 "code_fix": bool(by.get(p["signature"], {}).get("code_fix"))} for p in found]
    except Exception as e:  # BudgetStop (background paused), offline: the grouped problems still go out
        log.info("self-check: no diagnosis (%s)", type(e).__name__)
        return [{**p, "meaning": "", "fix": "", "brief": "", "code_fix": False} for p in found]


def due(memory: Memory, now: datetime | None = None) -> bool:
    """Friday from SELFCHECK_HOUR, once that day."""
    from .tools import local_now  # noqa: PLC0415
    now = now or local_now()
    return (now.weekday() == SELFCHECK_DAY and now.hour >= SELFCHECK_HOUR
            and memory.get(LAST_RUN_KEY) != now.date().isoformat())


def sample(memory: Memory) -> list[dict]:
    """An hourly reading of memory, swap, load and disk (local, free), for the weekly report."""
    return _remember(memory, server_now())


def check(memory: Memory, hours: float = SELFCHECK_HOURS, run=None, create=None, env=None, fetch=None) -> dict:
    found = problems(read_log(hours, run))
    now = server_now()
    samples = _remember(memory, now)
    drop = droplet(env, fetch)
    try:
        told = json.loads(memory.get(TOLD_KEY) or "{}")
    except ValueError:
        told = {}
    week_ago = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    new = [p for p in found if told.get(p["signature"], "") < week_ago]
    report = {"at": datetime.now(timezone.utc).isoformat(), "hours": hours, "server": now, "droplet": drop,
              "advice": advice(now, samples, found, drop),
              "problems": diagnose(new, now, create, memory) + [{**p, "seen_before": True} for p in found if p not in new]}
    memory.set(REPORT_KEY, json.dumps(report))
    return report


def message(report: dict) -> tuple[str, bool]:
    """What to tell the owner about this check ('' when nothing new), and whether it's urgent."""
    fresh = [p for p in report["problems"] if not p.get("seen_before")]
    if not fresh and not report["advice"]:
        return "", False
    urgent = any(p["kind"] in ("out of memory", "crash") for p in fresh)
    lines = []
    for p in fresh[:5]:
        line = f"{p['kind'].capitalize()}: {p['meaning'] or p['sample']} ({p['count']}x)"
        if p.get("fix"):
            line += f" Fix: {p['fix']}"
        if p.get("code_fix"):
            line += " Say \"send it to Claude\" for a code fix."
        lines.append(line)
    lines += report["advice"][:2]
    head = "🔧 Buddy found problems on its server" if urgent else "🔧 Buddy's self-check"
    return f"{head}: " + " ".join(lines), urgent


async def run(memory: Memory, deliver, quiet=lambda: False) -> None:
    """The background loop: an hourly reading of the server (free), and the weekly check on Friday morning."""
    import asyncio  # noqa: PLC0415
    from .tools import local_now  # noqa: PLC0415
    await asyncio.sleep(60)
    while True:
        try:
            await asyncio.to_thread(sample, memory)
            if not due(memory) or quiet():
                await asyncio.sleep(3600)
                continue
            report = await asyncio.to_thread(check, memory)
            memory.set(LAST_RUN_KEY, local_now().date().isoformat())
            text, _urgent = message(report)
            text = text.replace("🔧 Buddy's self-check", "🔧 Buddy's weekly self-check").replace(
                "🔧 Buddy found problems on its server", "🔧 Buddy's weekly self-check found problems") or \
                "🔧 Buddy's weekly self-check: no problems on the server this week."
            if await deliver("health", text):
                try:
                    told = json.loads(memory.get(TOLD_KEY) or "{}")
                except ValueError:
                    told = {}
                stamp = datetime.now(timezone.utc).isoformat()
                told.update({p["signature"]: stamp for p in report["problems"] if not p.get("seen_before")})
                cut = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
                memory.set(TOLD_KEY, json.dumps({k: v for k, v in told.items() if v >= cut}))
        except Exception:
            log.exception("self-check failed")
        await asyncio.sleep(3600)


TOOL = {
    "name": "self_check",
    "description": (
        "Buddy's own server: problems in its log (crashes, out of memory, repeated errors, call audio stutters), "
        "grouped by cause and explained, with the fix; and the server's memory, swap, load, disk and DigitalOcean "
        "size and price. It runs by itself every Friday morning over the past week. status: the last check; run: check now (past week). For a problem with a code fix, offer to send its "
        "brief to Claude Code (claude_code_fix send, which asks the owner first; self-deploy then tests and rolls "
        "back). Never claim you resized or rebooted the server: the owner does that in DigitalOcean."),
    "input_schema": {"type": "object", "properties": {"action": {"type": "string", "enum": ["status", "run"]}},
                     "required": ["action"], "additionalProperties": False},
}


def run_tool(memory: Memory, args: dict) -> str:
    if args.get("action") == "run" or not memory.get(REPORT_KEY):
        return json.dumps(check(memory))
    return memory.get(REPORT_KEY)

