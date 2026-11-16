"""Trained models, when there is enough to learn from: each week Buddy counts its judged calls; once a domain has
enough, it trains a small model in the sandbox, tests it on calls the model never saw, and uses it only if it
earned its place. Until then, and whenever a model fails its test, the simple learned weights stay in charge.

  Domains     investing (the practice book's closed paper trades: did the trade win?), theme (the Intelligence
              Platform's evidence events: did the basket beat the market over the next 90 days?), product, flight, hotel
              (the judged buy-or-wait and book-or-wait calls: did the price then come down 8% or more?)
  Enough      200 judged rows with at least 30 of each outcome. A retrain needs 50 new rows.
  Training    sandbox_jobs/train_model.py, offline, in the sandbox: a logistic regression on the reasons and
              figures kept with each call; learns from the oldest 70%, is tested on the newest 30%.
  Promotion   only if, on the unseen calls, its odds beat always saying the average AND the calls it liked were
              right at least 5 points more often than calls overall. The test results are kept with every run.
  Use         a promoted investing model adds its odds to each signal and turns a buy it gives under 45% into a
              caution; a promoted shopping or travel model is averaged with the rule-based chance. If the latest
              run for a domain was rejected, no model is used for it (an old model that no longer passes is
              retired).

The owner is told the first time a domain has enough data, and after every training: what was tested, on how
many calls, and whether it is now in use. A model is one more opinion with a track record; Buddy never trades.
"""

import base64
import json
import logging
from datetime import datetime
from pathlib import Path

from .memory import Memory
from .tools import local_now

log = logging.getLogger(__name__)

DOMAINS = ("lab", "investing", "theme", "product", "flight", "hotel")
MIN_ROWS, MIN_EACH, RETRAIN_NEW = 200, 30, 50
WORKSPACE = "model-training"
SCRIPT = Path(__file__).parent / "sandbox_jobs" / "train_model.py"
WEEK_KEY = "modeltrain:week"
NAMES = {"lab": "strategy lab trades", "investing": "investing signals", "theme": "theme baskets", "product": "shopping calls", "flight": "flight calls",
         "hotel": "hotel calls"}


def _ready(memory: Memory) -> None:
    from . import pricebook  # noqa: PLC0415
    pricebook.run(memory, "CREATE SEQUENCE IF NOT EXISTS model_id")
    pricebook.run(memory, "CREATE TABLE IF NOT EXISTS models (id BIGINT PRIMARY KEY DEFAULT nextval('model_id'), "
                          "domain VARCHAR, trained_on DATE, n_rows INTEGER, status VARCHAR, result VARCHAR)")


def rows(memory: Memory, domain: str) -> list[dict]:
    """The judged rows for a domain: [{"when", "x": features, "y": 1 or 0}]."""
    from . import pricebook, simbook, whentobuy  # noqa: PLC0415
    if domain == "lab":  # did a rule-based paper trade (live or replayed over past prices) make money after costs?
        from . import stratlab  # noqa: PLC0415
        return stratlab.model_rows(memory)
    if domain == "theme":  # did the basket beat the market over the 90 days after the evidence changed?
        from . import themes  # noqa: PLC0415
        return [{"when": e["day"], "x": themes.features_of(e), "y": int(e["r90"] - themes.COST > e["s90"]),
                 "regime": "up" if e["s90"] > 0 else "down",  # what the market did over the same 90 days
                 **({"rival": int(e["features"]["f_momentum_90"] > 0)} if "f_momentum_90" in e["features"] else {})}
                for e in reversed(themes._events(memory)) if e["r90"] is not None and e["s90"] is not None]
    if domain == "investing":
        simbook._ready(memory)
        got = pricebook.run(memory, "SELECT closed, side, ret, spy_ret, parts, read, features FROM sim_trades WHERE "
                                    "status = 'closed' AND ret IS NOT NULL ORDER BY closed")
        return [{"when": str(t["closed"]), "x": simbook.features_of(t),
                 "y": int((t["ret"] > 0) if t["side"] == "long" else (t["ret"] < 0)),
                 **({"regime": "up" if t["spy_ret"] > 0 else "down"} if t.get("spy_ret") is not None else {})}
                for t in got]
    whentobuy._ready(memory)
    got = pricebook.run(memory, "SELECT made_on, kind, price, best_seen, parts, factors FROM predictions WHERE "
                                "COALESCE(domain, 'product') = ? AND outcome IN ('right', 'wrong') AND best_seen IS "
                                "NOT NULL AND parts IS NOT NULL ORDER BY made_on", (domain,))
    return [{"when": str(p["made_on"]), "x": whentobuy.features_of(json.loads(p["parts"] or "{}"), p["kind"],
                                                               json.loads(p.get("factors") or "{}")),
             "y": int(p["best_seen"] <= p["price"] * whentobuy.DROPPED)} for p in got]


def latest(memory: Memory, domain: str) -> dict | None:
    from . import pricebook  # noqa: PLC0415
    _ready(memory)
    got = pricebook.run(memory, "SELECT trained_on, n_rows, status, result FROM models WHERE domain = ? "
                                "ORDER BY id DESC LIMIT 1", (domain,))
    return {**got[0], "trained_on": str(got[0]["trained_on"]), "result": json.loads(got[0]["result"])} if got else None


def readiness(memory: Memory, domain: str) -> dict:
    data = rows(memory, domain)
    yes = sum(r["y"] for r in data)
    last = latest(memory, domain)
    enough = len(data) >= MIN_ROWS and min(yes, len(data) - yes) >= MIN_EACH
    return {"rows": len(data), "needed": MIN_ROWS, "of_each_outcome": [yes, len(data) - yes], "needed_of_each": MIN_EACH,
            "enough": enough, "last_trained_on_rows": last["n_rows"] if last else 0,
            "train_now": enough and (not last or len(data) - last["n_rows"] >= RETRAIN_NEW)}


async def train(memory: Memory, domain: str, call=None) -> dict:
    """Train and test in the sandbox, keep the run. Returns {"status": promoted | rejected | not enough | waiting
    | failed, ...}."""
    from . import pricebook, sandbox  # noqa: PLC0415
    call = call or sandbox.call
    if call is sandbox.call and not sandbox.available():
        return {"status": "waiting", "why": "the sandbox isn't set up (deploy/sandbox.sh)"}
    data = rows(memory, domain)
    await call("create", workspace=WORKSPACE)
    await call("run", workspace=WORKSPACE, command="rm -rf input output && mkdir -p input output", network=False,
               timeout=60)
    await call("write_b64", workspace=WORKSPACE, path="input/rows.json",
               content_b64=base64.b64encode(json.dumps({"domain": domain, "rows": data}).encode()).decode())
    await call("write", workspace=WORKSPACE, path="train_model.py", content=SCRIPT.read_text())
    ran = await call("run", workspace=WORKSPACE, command="python3 train_model.py .", network=False, timeout=900)
    try:
        got = await call("fetch", workspace=WORKSPACE, path="model.json")
        result = json.loads(base64.b64decode(got["base64"]))
    except Exception as e:
        return {"status": "failed", "why": f"the trainer produced no model ({e}). "
                                           + str((ran or {}).get("output", ""))[-300:]}
    _ready(memory)
    pricebook.run(memory, "INSERT INTO models (domain, trained_on, n_rows, status, result) VALUES (?, ?, ?, ?, ?)",
                  (domain, local_now().date().isoformat(), len(data), result.get("status", "failed"),
                   json.dumps(result)))
    return result


def active(memory: Memory, domain: str) -> dict | None:
    """The model in use for a domain: the latest run, only if it was promoted."""
    try:
        last = latest(memory, domain)
    except Exception:
        return None
    return last["result"] if last and last["status"] == "promoted" else None


def predict(memory: Memory, domain: str, x: dict) -> dict | None:
    """{"p": the model's odds, "tested_on", "right_when_liked"} for these features, or None with no model in use."""
    m = active(memory, domain)
    if not m:
        return None
    from .sandbox_jobs import train_model  # noqa: PLC0415  the same arithmetic the trainer tested
    return {"p": round(train_model.predict(m["model"], x), 3), "tested_on": m["tested_on"],
            "right_when_liked": m["right_when_liked"], "right_overall": m["right_overall"]}


LIVE_MIN_LIKED = 20  # calls a model liked since it went into use, before its live record can retire it


def live(memory: Memory, domain: str) -> dict | None:
    """How the model in use has done on calls judged since it was trained (calls it never learned from)."""
    last = latest(memory, domain)
    if not last or last["status"] != "promoted":
        return None
    from .sandbox_jobs import train_model  # noqa: PLC0415
    model, tested_to = last["result"]["model"], last["result"].get("last") or last["trained_on"]
    since = [r for r in rows(memory, domain) if r["when"] > tested_to]
    ps = [train_model.predict(model, r["x"]) for r in since]
    liked = [r["y"] for p, r in zip(ps, since) if p >= train_model.LIKES]
    return {"calls": len(since), "right_overall": round(sum(r["y"] for r in since) / len(since), 3) if since else None,
            "liked": len(liked), "right_when_liked": round(sum(liked) / len(liked), 3) if liked else None,
            "promised": last["result"].get("right_when_liked"), "since": tested_to}


def monitor(memory: Memory) -> list[str]:
    """Retire a model whose live record shows no edge: once it has liked 20 or more calls since it went into
    use, those must have come true more often than calls overall. Returns the messages for the owner."""
    from . import pricebook  # noqa: PLC0415
    out = []
    for domain in DOMAINS:
        try:
            got = live(memory, domain)
        except Exception:
            log.exception("monitoring the %s model failed", domain)
            continue
        if not got or got["liked"] < LIVE_MIN_LIKED or got["right_when_liked"] > got["right_overall"]:
            continue
        why = (f"since it went into use it liked {got['liked']} calls and {got['right_when_liked']:.0%} came true, "
               f"no better than the {got['right_overall']:.0%} of all {got['calls']} calls")
        pricebook.run(memory, "INSERT INTO models (domain, trained_on, n_rows, status, result) VALUES (?, ?, ?, ?, ?)",
                      (domain, local_now().date().isoformat(), latest(memory, domain)["n_rows"], "retired",
                       json.dumps({"status": "retired", "why": why, "live": got})))
        out.append(f"🧪 I stopped using my model for {NAMES[domain]}: {why}. My usual rules are back in charge, "
                   "and I'll train again when there is enough new data.")
    return out


def message(domain: str, r: dict) -> str:
    name = NAMES[domain]
    if r["status"] not in ("promoted", "rejected"):
        return f"🧪 I tried to train a model on my {name} but couldn't: {r.get('why', r['status'])}."
    liked = (f"{r['right_when_liked']:.0%} right on the {r['liked']} it liked" if r.get("right_when_liked") is not None
             else "it liked none of them")
    checks = [f"- {'✅' if ok else '❌'} {what}" for what, ok in r["checks"].items()]
    used = ("**It passed, so I'm using it** next to my usual reasons." if r["status"] == "promoted" else
            "**It didn't pass, so I'm not using it.** My usual weighted reasons stay in charge; I'll try again "
            f"after {RETRAIN_NEW} more judged calls.")
    shown = "\n".join([f"**🧪 Model trained on my {name}**",
                       f"- Learned from {r['learned_from']} judged calls, tested on the {r['tested_on']} newest it "
                       f"had never seen ({r['first']} to {r['last']})",
                       f"- On those: {liked}, against {r['right_overall']:.0%} right overall", *checks, used,
                       "A model is one more opinion with a track record, not a promise. Buddy never trades."])
    return (f"🧪 I had enough data to train a model on my {name}. "
            + ("It passed its test and is now in use." if r["status"] == "promoted" else "It didn't pass its test, "
               "so I'm not using it.") + f" || {shown}")


def due(memory: Memory, now: datetime | None = None) -> bool:
    now = now or local_now()
    year, week, _ = now.isocalendar()
    return now.hour >= 3 and memory.get(WEEK_KEY) != f"{year}-{week}"


async def weekly(memory: Memory, call=None) -> list[str]:
    """Train every domain that has enough new data. Returns the messages for the owner."""
    out = monitor(memory)  # first: has a model in use stopped earning its place?
    for domain in DOMAINS:
        try:
            if not readiness(memory, domain)["train_now"]:
                continue
            got = await train(memory, domain, call)
            if got["status"] == "waiting":
                key = f"modeltrain:told_waiting:{domain}"
                if not memory.get(key):  # said once, not every week
                    memory.set(key, "1")
                    out.append(f"🧪 I now have enough judged {NAMES[domain]} to train a model, but the sandbox "
                               "isn't set up: sudo bash /opt/buddy/deploy/sandbox.sh")
                continue
            out.append(message(domain, got))
        except Exception:
            log.exception("model training for %s failed", domain)
    return out


def status(memory: Memory) -> dict:
    out = {}
    for d in DOMAINS:
        last = latest(memory, d)
        r = (last or {}).get("result") or {}
        out[NAMES[d]] = {**readiness(memory, d), "model_in_use": bool(last and last["status"] == "promoted"),
                         "live": live(memory, d),
                         "last_run": ({"on": last["trained_on"], "status": last["status"],
                                       **{k: r.get(k) for k in ("learned_from", "tested_on", "right_overall", "liked",
                                                                "right_when_liked", "checks", "strongest", "why",
                                                                "brier", "brier_of_the_average", "calibration",
                                                                "simple_rule_accuracy", "by_market")}}
                                      if last else None)}
    return {"domains": out, "note": f"A model is trained once a domain has {MIN_ROWS} judged calls with {MIN_EACH} "
                                    "of each outcome, tested on the newest 30% it never saw, and used only if it "
                                    "passes. Until then the simple learned weights decide."}
