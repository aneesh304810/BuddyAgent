"""Loading the trading engine's data in one go, and saying plainly what is loaded and what is not.

The strategy lab rests on several sources that each refresh on their own schedule. After a first install, or
when something looks empty, "Load everything" runs them one after another in the background:

  1  the recommended watchlist     from the Intelligence evidence
  2  today's lab trades            each rule's picks from daily prices
  3  the replay over past prices   every rule's record on about five years of history
  4  factor returns                the yardsticks from the Kenneth French data library
  5  the research queue            new papers from arXiv, and the notebooks of the open-source starting points
  6  news sentiment                when one of its two keys is set
  7  company figures               earnings surprise and ratios (the slow one: a few minutes)
  8  a first model                 trained in the sandbox when there are enough judged trades

Each step's result is kept: when it ran, whether it worked, and what it said, in the source's own words when
it failed. The status panel reads what is actually held (counts and dates), not what a step claimed, so an
empty source shows as empty even after a step that reported success.
"""

import asyncio
import json
import logging
import threading

from .memory import Memory
from .tools import local_now

log = logging.getLogger("buddy.labsetup")
STATE_KEY = "labsetup:state"
STALE_MINUTES = 25  # a load that has shown no progress for this long was cut off (a restart), not still running


def _state(memory: Memory) -> dict:
    try:
        return json.loads(memory.get(STATE_KEY) or "{}")
    except ValueError:
        return {}


def _running(state: dict) -> str:
    """The step in progress, or "" when none is, or when the last sign of progress is too old to be believed."""
    from datetime import datetime, timedelta  # noqa: PLC0415
    if not state.get("running"):
        return ""
    try:
        seen = datetime.fromisoformat(state.get("progress_at") or "")
    except ValueError:
        return ""
    return state["running"] if local_now() - seen < timedelta(minutes=STALE_MINUTES) else ""


def _picks(memory: Memory) -> str:
    from . import watchpicks  # noqa: PLC0415
    got = watchpicks.refresh(memory)
    return f"{len(got['added'])} added, {len(got['returned'])} returned, {len(got['archived'])} archived"


def _lab(memory: Memory) -> str:
    from . import stratlab  # noqa: PLC0415
    got = stratlab.run(memory)
    return got.get("note") or f"{sum(got['opened'].values())} opened, {got['closed']} closed"


def _replay(memory: Memory) -> str:
    from . import stratlab  # noqa: PLC0415
    got = stratlab.replay(memory)
    return got.get("note") or f"{sum(got['trades'].values()):,} finished trades from {got['from']} to {got['to']}"


def _factors(memory: Memory) -> str:
    from . import benchmarks  # noqa: PLC0415
    return f"{asyncio.run(benchmarks.refresh_factors(memory)):,} month-values held"


def _papers(memory: Memory) -> str:
    """New arXiv papers, and the notebooks of the licensed open-source repositories listed as starting points."""
    from . import researchdesk  # noqa: PLC0415
    said = []
    try:
        said.append(f"{researchdesk.arxiv_new(memory)} new arXiv papers")
    except Exception as e:
        said.append(f"arXiv failed ({type(e).__name__}: {str(e)[:80]})")
    files = failed = 0
    for start in researchdesk.STARTING_POINTS:
        try:
            files += researchdesk.github_add(memory, start["url"])["added"]
        except Exception as e:
            failed += 1
            log.info("setup: %s could not be queued (%s)", start["url"], type(e).__name__)
            why = f"{type(e).__name__}: {str(e)[:80]}"
    said.append(f"{files} notebooks and files from {len(researchdesk.STARTING_POINTS) - failed} open-source folders"
                + (f"; {failed} folders failed ({why})" if failed else ""))
    return "; ".join(said)


def _sentiment(memory: Memory) -> str:
    from . import newssent  # noqa: PLC0415
    if not newssent.configured():
        return "skipped: neither BUDDY_ALPHAVANTAGE_KEY nor BUDDY_MARKETAUX_KEY is set"
    got = newssent.refresh(memory)
    return f"asked {got['asked']}, kept {got['kept']}" + (f"; failed: {got['failed']}" if got["failed"] else "")


def _company(memory: Memory) -> str:
    from . import fundamentals, stratlab  # noqa: PLC0415
    got = fundamentals.refresh(memory, stratlab.universe(memory))
    return got.get("note") or f"asked {got['asked']}, kept {got['kept']}, failed {got['failed']}"


def _model(memory: Memory) -> str:
    from . import modeltrain  # noqa: PLC0415
    ready = modeltrain.readiness(memory, "lab")
    if not ready["enough"]:
        return f"skipped: {ready['rows']} of {ready['needed']} judged trades, with {ready['needed_of_each']} of each outcome needed"
    got = asyncio.run(modeltrain.train(memory, "lab"))
    return f"{got.get('status')}" + (f": {got['why']}" if got.get("why") else "")


STEPS = [("picks", "Recommended watchlist", _picks), ("lab", "Today's lab trades", _lab),
         ("replay", "Replay over past prices", _replay), ("factors", "Factor returns", _factors),
         ("papers", "Research queue", _papers), ("sentiment", "News sentiment", _sentiment),
         ("company", "Company figures", _company), ("model", "A first model", _model)]


def load_all(memory: Memory, steps=None) -> dict:
    """Run every step in order, keeping each result. One failing does not stop the rest."""
    state = _state(memory)
    for key, title, fn in steps or STEPS:
        state["running"], state["progress_at"] = title, local_now().isoformat(timespec="seconds")
        memory.set(STATE_KEY, json.dumps(state))
        try:
            said, ok = fn(memory), True
        except Exception as e:
            log.warning("setup: %s failed", key, exc_info=True)
            said, ok = f"{type(e).__name__}: {str(e)[:200]}", False
        state[key] = {"ok": ok and not str(said).startswith("skipped"), "skipped": str(said).startswith("skipped"),
                      "said": str(said), "at": local_now().isoformat(timespec="minutes")}
    state["running"] = ""
    state["finished"] = local_now().isoformat(timespec="minutes")
    memory.set(STATE_KEY, json.dumps(state))
    return state


def load_in_background(memory: Memory) -> str:
    if _running(_state(memory)):
        return "It is already loading."
    memory.set(STATE_KEY, json.dumps({**_state(memory), "running": "starting",
                                      "progress_at": local_now().isoformat(timespec="seconds")}))

    def work():
        try:
            load_all(memory)
        except Exception:
            log.exception("setup failed")
            memory.set(STATE_KEY, json.dumps({**_state(memory), "running": ""}))
    threading.Thread(target=work, name="lab-setup", daemon=True).start()
    return "Loading everything in the background. Company figures take a few minutes; press Refresh to follow it."


def _held(memory: Memory) -> dict:
    """What is actually held, read from the tables: {step: (is there anything, a line saying what)}."""
    from . import pricebook  # noqa: PLC0415
    def one(sql: str):
        try:
            return pricebook.run(memory, sql)[0]
        except Exception:
            return {}
    picks = one("SELECT COUNT(*) AS n FROM watch_picks WHERE status = 'active'")
    live = one("SELECT COUNT(*) AS n, MAX(opened) AS day FROM lab_trades WHERE NOT COALESCE(backfilled, FALSE)")
    past = one("SELECT COUNT(*) AS n, MIN(opened) AS a, MAX(closed) AS b FROM lab_trades WHERE backfilled")
    fac = one("SELECT COUNT(DISTINCT month) AS n, MAX(month) AS m FROM factor_returns")
    queue = one("SELECT COUNT(*) AS n FROM research_queue WHERE status = 'new'")
    mood = one("SELECT COUNT(DISTINCT symbol) AS n, MAX(day) AS day FROM news_sentiment")
    comp = one("SELECT COUNT(DISTINCT symbol) AS n, MAX(day) AS day FROM fundamentals")
    n = lambda r: int(r.get("n") or 0)  # noqa: E731
    out = {"picks": (n(picks) > 0, f"{n(picks)} companies recommended"),
           "lab": (n(live) > 0, f"{n(live)} live paper trades" + (f", the newest opened {live['day']}" if live.get("day") else "")),
           "replay": (n(past) > 0, f"{n(past):,} past trades" + (f", {past['a']} to {past['b']}" if n(past) else "")),
           "factors": (n(fac) > 0, f"{n(fac):,} months" + (f", up to {fac['m']}" if fac.get("m") else "")),
           "papers": (n(queue) > 0, f"{n(queue)} papers waiting for you to pick"),
           "sentiment": (n(mood) > 0, f"{n(mood)} companies scored" + (f", the newest {mood['day']}" if mood.get("day") else "")),
           "company": (n(comp) > 0, f"{n(comp)} companies" + (f", fetched {comp['day']}" if comp.get("day") else ""))}
    try:
        from . import modeltrain  # noqa: PLC0415
        ready, use = modeltrain.readiness(memory, "lab"), modeltrain.active(memory, "lab")
        out["model"] = (bool(use), ("a model is in use" if use else "no model in use")
                        + f"; {ready['rows']} of {ready['needed']} judged trades to learn from")
    except Exception:
        out["model"] = (False, "no model in use")
    return out


def status(memory: Memory) -> dict:
    """For the Trading tab: each source, what is held, and how its last load went."""
    from . import sandbox  # noqa: PLC0415
    state, held = _state(memory), _held(memory)
    rows = []
    for key, title, _ in STEPS:
        last, (have, line) = state.get(key) or {}, held.get(key, (False, ""))
        rows.append({"key": key, "title": title, "loaded": have, "holds": line,
                     "last": last.get("said", ""), "at": last.get("at", ""),
                     "state": "failed" if last and not last.get("ok") and not last.get("skipped") else
                              "loaded" if have else "skipped" if last.get("skipped") else "empty"})
    return {"rows": rows, "running": _running(state), "finished": state.get("finished") or "",
            "cut_off": bool(state.get("running")) and not _running(state),
            "sandbox": sandbox.available(),
            "sandbox_note": "" if sandbox.available() else
            "The sandbox is not set up, so files are read inside Buddy and no model can be trained: "
            "sudo bash /opt/buddy/deploy/sandbox.sh"}
