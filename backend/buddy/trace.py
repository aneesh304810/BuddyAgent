"""The request trace: every model and paid API call Buddy made, in one Excel file to review.

  Summary      totals; where the money went by kind of work, by model and by hour; what a chat reply's cost is made of
               (fresh input, reading the cache, writing the cache, output); the 25 costliest calls
  Calls        one row per model call: time, provider, model, kind of work, tokens (fresh, cache read, cache write,
               output), its cost split into those four parts, seconds, failed or not, why it stopped, the tools it
               asked for, and short masked previews of what was asked and answered
  Outside APIs one row per paid or free API call (search, travel prices, transit...)

From the last cost reset (or the last N days). Previews are the same short, masked ones Insights shows; room speech
(other people's words) is never in them. Made by code, no AI: the trace itself costs nothing.
"""

import io
from datetime import datetime, timedelta, timezone

from .memory import Memory
from .tools import local_now

TEXT_START = ("=", "+", "-", "@")


def _safe(v):
    """A text cell that can never become a spreadsheet formula."""
    if isinstance(v, str):
        v = v.replace("\x00", "")[:400]
        return " " + v if v.startswith(TEXT_START) else v
    return v


def _price(model: str) -> tuple[float, float, float]:
    """(input, cache read, output) USD per million tokens, as Buddy's meter prices them."""
    from . import usage
    p_in, p_out = next((p for name, p in usage.PRICES.items() if model.startswith(name)), usage.FALLBACK_PRICE)
    read = next((p for name, p in usage.CACHE_READ.items() if model.startswith(name)), p_in * 0.1)
    return p_in, read, p_out


def parts(r: dict) -> tuple[float, float, float, float]:
    """A Claude call's cost split: (fresh input, cache read, cache write, output)."""
    if r["provider"] != "claude":
        return 0.0, 0.0, 0.0, float(r["cost_usd"] or 0)
    p_in, read, p_out = _price(r["model"])
    m = 1_000_000
    return (r["input_tokens"] * p_in / m, r["cache_read_tokens"] * read / m,
            r["cache_write_tokens"] * p_in * 1.25 / m, r["output_tokens"] * p_out / m)


def build(memory: Memory, since: str) -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill

    from .insights import purpose_name
    rows = [dict(r) for r in memory.db.execute("SELECT * FROM model_calls WHERE at >= ? ORDER BY at", (since,))]
    try:
        apis = [dict(r) for r in memory.db.execute("SELECT * FROM api_calls WHERE at >= ? ORDER BY at", (since,))]
    except Exception:
        apis = []
    tz = local_now().tzinfo

    def local(at: str) -> str:
        return datetime.fromisoformat(at).astimezone(tz).strftime("%Y-%m-%d %H:%M:%S")

    wb = Workbook()
    head, fill = Font(bold=True), PatternFill("solid", fgColor="E8EEF7")

    def sheet(ws, columns, data, widths=None):
        ws.append(columns)
        for c in ws[1]:
            c.font, c.fill = head, fill
        ws.freeze_panes = "A2"
        for row in data:
            ws.append([_safe(v) for v in row])
        for i, w in enumerate(widths or [], 1):
            ws.column_dimensions[ws.cell(1, i).column_letter].width = w

    # Calls
    calls = wb.active
    calls.title = "Calls"
    call_rows = []
    for r in rows:
        f_in, c_read, c_write, out = parts(r)
        call_rows.append([local(r["at"]), r["provider"], r["model"], purpose_name(r["purpose"]), r["purpose"],
                          r["turn"], r["input_tokens"], r["cache_read_tokens"], r["cache_write_tokens"],
                          r["output_tokens"], r["web_searches"], round(r["cost_usd"] or 0, 5), round(f_in, 5),
                          round(c_read, 5), round(c_write, 5), round(out, 5),
                          round(r["seconds"], 2) if r["seconds"] is not None else None,
                          "yes" if r["ok"] else "FAILED", r["stop"], r["tools"], r["request"], r["reply"],
                          r["verdict"]])
    sheet(calls, ["Time", "Provider", "Model", "Work", "Purpose code", "Turn", "Fresh input tokens",
                  "Cache read tokens", "Cache write tokens", "Output tokens", "Web searches", "Cost $",
                  "Fresh input $", "Cache read $", "Cache write $", "Output $", "Seconds", "OK", "Stopped / error",
                  "Tools asked", "Asked (preview)", "Answer (preview)", "Free-answer verdict"], call_rows,
          [19, 10, 22, 24, 16, 14, 10, 10, 10, 10, 8, 9, 9, 9, 9, 9, 8, 7, 24, 24, 50, 50, 12])

    # Summary
    s = wb.create_sheet("Summary", 0)
    total = sum(r["cost_usd"] or 0 for r in rows) + sum(a.get("cost_usd") or 0 for a in apis)
    s.append(["Request trace", f"from {local(since)} to {local_now():%Y-%m-%d %H:%M}"])
    s["A1"].font = Font(bold=True, size=14)
    s.append(["Total spent", round(total, 4)])
    s.append(["Model calls", len(rows), "failed", sum(1 for r in rows if not r["ok"])])
    s.append(["Outside API calls", len(apis)])
    s.append([])

    def table(title, columns, data):
        s.append([title])
        s.cell(s.max_row, 1).font = Font(bold=True, size=12)
        s.append(columns)
        for c in s[s.max_row]:
            c.font, c.fill = head, fill
        for row in data:
            s.append([_safe(v) for v in row])
        s.append([])

    def group(key):
        g: dict = {}
        for r in rows:
            e = g.setdefault(key(r), [0, 0.0])
            e[0] += 1
            e[1] += r["cost_usd"] or 0
        return sorted(g.items(), key=lambda kv: -kv[1][1])

    table("By kind of work", ["Work", "Calls", "Cost $", "Cost per call $", "Share"],
          [[purpose_name(k), n, round(c, 4), round(c / n, 5) if n else 0, f"{c / total:.0%}" if total else ""]
           for k, (n, c) in group(lambda r: r["purpose"])])
    table("By model", ["Provider · model", "Calls", "Cost $"],
          [[k, n, round(c, 4)] for k, (n, c) in group(lambda r: f"{r['provider']} · {r['model']}")])
    by_hour = group(lambda r: local(r["at"])[:13] + ":00")
    table("By hour (local)", ["Hour", "Calls", "Cost $"], [[k, n, round(c, 4)] for k, (n, c) in sorted(by_hour)])
    split = [sum(parts(r)[i] for r in rows if r["provider"] == "claude") for i in range(4)]
    claude_total = sum(split) or 1
    table("What Claude's cost is made of", ["Part", "Cost $", "Share", "What it means"], [
        ["Fresh input", round(split[0], 4), f"{split[0] / claude_total:.0%}", "new words sent (your message, tool results)"],
        ["Cache read", round(split[1], 4), f"{split[1] / claude_total:.0%}",
         "re-reading instructions and the conversation (cheap, 10% of input)"],
        ["Cache write", round(split[2], 4), f"{split[2] / claude_total:.0%}",
         "saving instructions and conversation to the cache (125% of input): high when the prompt keeps changing"],
        ["Output", round(split[3], 4), f"{split[3] / claude_total:.0%}", "words Claude wrote"]])
    top = sorted(rows, key=lambda r: -(r["cost_usd"] or 0))[:25]
    table("The 25 costliest calls", ["Time", "Work", "Model", "Cost $", "Cache write tokens", "Output tokens",
                                    "Asked (preview)"],
          [[local(r["at"]), purpose_name(r["purpose"]), r["model"], round(r["cost_usd"] or 0, 5),
            r["cache_write_tokens"], r["output_tokens"], r["request"]] for r in top])
    for col, w in zip("ABCDEFG", (34, 14, 24, 12, 16, 12, 60)):
        s.column_dimensions[col].width = w

    # Outside APIs
    sheet(wb.create_sheet("Outside APIs"), ["Time", "Service", "Endpoint", "Purpose", "OK", "Seconds", "Cost $", "Error"],
          [[local(a["at"]), a["provider"], a["endpoint"], a["purpose"], "yes" if a["ok"] else "FAILED", a["seconds"],
            a["cost_usd"], a["error"]] for a in apis], [19, 14, 30, 16, 7, 8, 9, 40])
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


def since_for(memory: Memory, days: int = 0, period: str = "") -> str:
    from . import budgets
    if period == "today":  # from local midnight
        return local_now().replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc).isoformat()
    if days:
        return (datetime.now(timezone.utc) - timedelta(days=max(1, min(days, 90)))).isoformat()
    return budgets.last_reset(memory) or (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()


def make(memory: Memory, days: int = 0, period: str = "") -> dict:
    """The trace as a file Buddy can show and link to. period "today", or days, or (default) since the last reset."""
    from . import makers
    data = build(memory, since_for(memory, days, period))
    return makers.save(memory, data, f"Buddy request trace {local_now():%Y-%m-%d %H%M}", "xlsx", "document")
