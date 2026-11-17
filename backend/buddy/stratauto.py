"""Investment Intelligence on its own: each day it reads a few queued papers into rules, tests them, and pauses a
rule that is failing live. The owner still decides what trades.

  Read     the newest READ_A_DAY items in the research queue are read into rules (researchdesk.read). Each new rule
           also gets a second reading by another model job, which says whether the rule matches the document
           (stratrules.critique).
  Test     the new rules are replayed over past prices straight away, without being switched on.
  Keep     a rule that passed stays in the Library as proposed, waiting for the owner's approval. So does a rule
           that reads company figures, which have no history to test on. A rule that failed its test, or made too
           few trades to judge, is removed, and its paper in the queue says why.
  Pause    an approved rule that has lost to the market over PAUSE_AFTER or more live closed trades is paused.
           Only rules the owner added can be paused this way; the six built-in ones are reported instead.
  Tell     one message: what was read, what passed, what was paused.

What it never does: approve a rule, or start one trading. Reading uses the free model unless the owner chose
another; a strategy document is public text, so the Privacy Gateway lets it go to any model.
"""

import asyncio
import json
import logging

from .memory import Memory
from .tools import ToolError, local_now

log = logging.getLogger("buddy.stratauto")

READ_A_DAY, PAUSE_AFTER = 3, 20
DAY_KEY, LAST_KEY = "stratauto:day", "stratauto:last"


def due(memory: Memory, now=None) -> bool:
    now = now or local_now()
    return now.hour >= 9 and memory.get(DAY_KEY) != now.date().isoformat()


def retire(memory: Memory) -> dict:
    """Pause the owner's rules that are losing to the market live. Returns {"paused": [...], "failing_built_in": [...]}."""
    from . import stratrepo, stratrules  # noqa: PLC0415
    paused, built_in = [], []
    for s in stratrepo.view(memory)["strategies"]:
        if s["watch"]["state"] != "failing" or (s["live"].get("trades") or 0) < PAUSE_AFTER:
            continue
        try:
            stratrules.decide(memory, s["key"], "pause")
            paused.append({"name": s["name"], "why": s["watch"]["says"]})
        except ToolError:   # not one of the owner's rules: a built-in one, which has no pause
            built_in.append({"name": s["name"], "why": s["watch"]["says"]})
    return {"paused": paused, "failing_built_in": built_in}


async def daily(memory: Memory, ask=None, reader=None, fetch=None, data=None, critic=None, replay=None) -> dict:
    """One day's pass. Returns what happened, also kept for the page (LAST_KEY)."""
    from . import researchdesk, stratlab, stratrepo, stratrules  # noqa: PLC0415
    out = {"day": local_now().date().isoformat(), "read": 0, "no_rule": 0, "rules": [], "passed": [], "kept_untested": [],
           "removed": [], "paused": [], "failing_built_in": []}
    new: dict[str, dict] = {}
    for item in researchdesk.queue(memory, "new", limit=READ_A_DAY):
        out["read"] += 1
        try:
            rule = await researchdesk.read(memory, item["id"], fetch, ask, reader)
        except ToolError as e:
            out["no_rule"] += 1
            log.info("strategy auto: %r gave no rule (%s)", item["title"][:60], str(e)[:80])
            continue
        except Exception:
            out["no_rule"] += 1
            log.warning("strategy auto: reading %r failed", item["title"][:60], exc_info=True)
            continue
        new[rule["key"]] = item
        if critic is not None or ask is None:   # a second reading, by another model job
            try:
                await stratrules.critique(memory, rule["key"], f"{item['title']}\n\n{item['summary']}", critic)
            except Exception:
                log.info("strategy auto: no second reading for %s", rule["key"], exc_info=True)
    if new:
        try:
            await asyncio.to_thread(replay or stratlab.replay, memory, data, keys=list(new), proposed=True)
        except Exception:
            log.warning("strategy auto: the replay of new rules failed; they stay proposed and untested", exc_info=True)
        by = {s["key"]: s for s in stratrepo.view(memory)["strategies"]}
        for key, item in new.items():
            s = by.get(key)
            if s is None:
                continue
            out["rules"].append(s["name"])
            if s["verdict"] == "passed":
                out["passed"].append({"name": s["name"], "against_market": s["history"].get("average_vs_market"),
                                      "trades": s["history"].get("trades")})
            elif s["live_only"]:
                out["kept_untested"].append(s["name"])
            else:
                why = "; ".join(s["reasons"])[:280]
                stratrules.decide(memory, key, "remove")
                researchdesk._run(memory, "UPDATE research_queue SET rule_key = '', note = ? WHERE id = ?",
                                  (f"Read into a rule, tested, and removed: {why}", item["id"]))
                out["removed"].append({"name": s["name"], "why": why})
    out.update(retire(memory))
    memory.set(LAST_KEY, json.dumps(out))
    return out


def message(got: dict) -> str:
    """What to tell the owner, or "" when nothing worth a message happened."""
    if not (got["passed"] or got["paused"] or got["failing_built_in"] or got["kept_untested"]):
        return ""
    lines = []
    if got["read"]:
        lines.append(f"Read {got['read']} paper{'s' if got['read'] != 1 else ''} from the research queue: "
                     f"{len(got['rules'])} became rules, {len(got['removed'])} failed the test on past prices and "
                     f"were removed.")
    for p in got["passed"]:
        lines.append(f"✅ Passed on past prices: {p['name']}"
                     + (f" ({p['against_market']:+.1%} a trade against the market over {p['trades']} trades)"
                        if p.get("against_market") is not None else "") + ". Waiting for your approval.")
    for name in got["kept_untested"]:
        lines.append(f"🕒 Kept untested: {name}. It reads company figures, which have no history; it can only be judged live.")
    for p in got["paused"]:
        lines.append(f"⏸ Paused: {p['name']}. {p['why']}.")
    for p in got["failing_built_in"]:
        lines.append(f"⚠️ Failing live: {p['name']} (built in, so not paused). {p['why']}.")
    return ("📚 Investment Intelligence: " + " ".join(lines)
            + " Review in Intelligence → Strategies. Nothing trades until you approve it; paper trades only.")


def last(memory: Memory) -> dict:
    try:
        return json.loads(memory.get(LAST_KEY) or "{}")
    except ValueError:
        return {}
