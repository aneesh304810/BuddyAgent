"""The investment journal and the committee: write a thesis down before acting, have it argued from both sides,
and check it later against what happened. Entries are only ever added, never changed or deleted, so old reasoning
can be judged honestly.

  thesis     why the owner thinks a company is worth owning (or avoiding), what they expect, and what would prove
             them wrong, with that day's price and checklist read kept beside it
  note       anything added later (a new filing, a change of mind): a new dated entry
  review     the thesis against today: the price then and now, what the checklists said then and say now; the
             conclusion is added as a new entry and the original stays as written
  committee  everything Buddy has on a company in one place (checklists from its filings, the price trend, what
             Reddit says, Finnhub's facts, which followed funds reported holding it, the journal so far), for
             Claude to argue the strongest case for, the strongest case against, and what would disprove each

The journal is the owner's own thinking: it stays in Buddy's main (encrypted) store and only Claude reads it.
Research and education, not advice; Buddy never trades.
"""

import json
import logging
from datetime import datetime, timezone

from .memory import Memory
from .tools import ToolError

log = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS inv_journal (
    id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, symbol TEXT NOT NULL, kind TEXT NOT NULL,
    text TEXT NOT NULL, expects TEXT DEFAULT '', invalidates TEXT DEFAULT '', price REAL, snapshot TEXT DEFAULT '{}'
);
"""
FRAME = (
    "Act as an independent investment committee, not as the owner's friend. From the material here only: "
    "(1) the strongest case FOR, (2) the strongest case AGAINST, (3) a neutral reading, (4) for each side, the "
    "specific evidence that would prove it wrong, (5) what is missing or unreliable in this material, and (6) how "
    "it compares with what the owner wrote in the journal, if anything, and (7) put the questions in "
    "lessons_to_apply to this company and answer each from the material (they are ideas summarised from the "
    "works named, not quotations). Separate facts (with their date) from "
    "estimates and opinions. Reddit is opinion; 13F holdings are weeks old; filings are yearly. Do not agree with "
    "the owner to be agreeable, do not invent figures, quotes or investor opinions, and do not say what any "
    "named investor would do today. End by saying this is research, not advice.")


def _ready(memory: Memory) -> None:
    memory.db.executescript(SCHEMA)


def _symbol(symbol: str) -> str:
    s = (symbol or "").strip().upper().lstrip("$")
    if not s or len(s) > 8:
        raise ToolError("Which company? Give its ticker.")
    return s


def _add(memory: Memory, symbol: str, kind: str, text: str, expects: str = "", invalidates: str = "",
         price: float | None = None, snapshot: dict | None = None, now: datetime | None = None) -> int:
    _ready(memory)
    text = " ".join((text or "").split())
    if not text:
        raise ToolError("What should the entry say?")
    cur = memory.db.execute(
        "INSERT INTO inv_journal (at, symbol, kind, text, expects, invalidates, price, snapshot) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ((now or datetime.now(timezone.utc)).isoformat(timespec="seconds"), _symbol(symbol), kind, text[:4000],
         " ".join((expects or "").split())[:1000], " ".join((invalidates or "").split())[:1000], price,
         json.dumps(snapshot or {})))
    memory.db.commit()
    return cur.lastrowid


def thesis(memory: Memory, symbol: str, text: str, expects: str = "", invalidates: str = "",
           price: float | None = None, read: dict | None = None, now: datetime | None = None) -> dict:
    """Write a thesis down. `read`: the checklists' read that day ({"price", "business"}), kept beside it."""
    if not (invalidates or "").strip():
        raise ToolError("A thesis needs what would prove it wrong. Ask the owner: what would make you change "
                        "your mind?")
    n = _add(memory, symbol, "thesis", text, expects, invalidates, price, {"read": read} if read else None, now)
    return {"saved": n, "symbol": _symbol(symbol), "note": "Kept as written. Later entries are added; this one "
                                                           "is never changed."}


def note(memory: Memory, symbol: str, text: str, price: float | None = None, now: datetime | None = None) -> dict:
    return {"saved": _add(memory, symbol, "note", text, price=price, now=now), "symbol": _symbol(symbol)}


def entries(memory: Memory, symbol: str = "") -> list[dict]:
    _ready(memory)
    q = "SELECT * FROM inv_journal" + (" WHERE symbol = ?" if symbol else "") + " ORDER BY id"
    out = []
    for r in memory.db.execute(q, ((_symbol(symbol),) if symbol else ())):
        d = dict(r)
        d["snapshot"] = json.loads(d.get("snapshot") or "{}")
        out.append(d)
    return out


def review(memory: Memory, symbol: str, price: float | None = None, read: dict | None = None,
           conclusion: str = "", now: datetime | None = None) -> dict:
    """Each thesis on a symbol against today. With `conclusion`, that is added as a new entry."""
    symbol = _symbol(symbol)
    all_ = entries(memory, symbol)
    theses = [e for e in all_ if e["kind"] == "thesis"]
    if not theses:
        raise ToolError(f"There is no thesis on {symbol} in the journal yet.")
    out = []
    for t in theses:
        then = (t["snapshot"] or {}).get("read") or {}
        out.append({"written": t["at"][:10], "thesis": t["text"], "expected": t["expects"],
                    "would_prove_it_wrong": t["invalidates"], "price_then": t["price"], "price_now": price,
                    "change": round(price / t["price"] - 1, 4) if price and t["price"] else None,
                    "checklists_then": then or None, "checklists_now": read,
                    "checklists_changed": bool(then and read and then != read)})
    saved = _add(memory, symbol, "review", conclusion, price=price, snapshot={"read": read} if read else None,
                 now=now) if (conclusion or "").strip() else None
    return {"symbol": symbol, "theses": out, "later_entries": [
        {"at": e["at"][:10], "kind": e["kind"], "text": e["text"]} for e in all_ if e["kind"] != "thesis"],
        "review_saved": saved,
        "note": "Judge the reasoning, not only the price: was what was expected what happened, and did any of "
                "the 'would prove it wrong' conditions occur? Old entries stay as written."}


def committee(memory: Memory, symbol: str, data=None, env=None, fetch=None) -> dict:
    """Everything Buddy has on a company, for Claude to argue both sides. Each part is fetched on its own."""
    from . import finnhub, funds, lenses, signals  # noqa: PLC0415
    from .markets import Data, rsi, sma  # noqa: PLC0415
    symbol = _symbol(symbol)
    out: dict = {"symbol": symbol, "missing": []}

    def part(name, fn):
        try:
            out[name] = fn()
        except Exception as e:
            out["missing"].append(f"{name}: {str(e)[:100]}")

    price = above = None
    try:
        bars = (data or Data(memory)).bars("us", [symbol], days=400).get(symbol) or []
        closes = [b["c"] for b in bars]
        if len(closes) >= 60:
            price, year = closes[-1], closes[-252:]
            above = price / sma(closes, 200) - 1 if len(closes) >= 200 else None
            out["price"] = {"as_of": bars[-1]["d"], "price": round(price, 2),
                            "vs_50_day_average": round(price / sma(closes, 50) - 1, 3),
                            "vs_200_day_average": round(above, 3) if above is not None else None,
                            "rsi": round((rsi(closes) or [50.0])[-1]), "year_high": round(max(year), 2),
                            "year_low": round(min(year), 2), "below_year_high": round(price / max(year) - 1, 3),
                            "change_1_year": round(price / year[0] - 1, 3)}
        else:
            out["missing"].append("price: too little price history")
    except Exception as e:
        out["missing"].append(f"price: {str(e)[:100]}")
    part("checklists", lambda: lenses.checklists(memory, symbol, price, above, None, env, fetch))
    part("reddit_30_days", lambda: signals.about(memory, symbol, 30))
    if finnhub.configured(env):
        part("finnhub", lambda: finnhub.facts(memory, symbol, env, fetch))
    else:
        out["missing"].append("finnhub: no key set (earnings date, insiders, analysts)")
    name = (out.get("checklists") or {}).get("company") or ""
    if name:
        part("funds", lambda: funds.who_holds(memory, name))
    part("journal", lambda: [{"at": e["at"][:10], "kind": e["kind"], "text": e["text"], "expects": e["expects"],
                              "would_prove_it_wrong": e["invalidates"], "price": e["price"]}
                             for e in entries(memory, symbol)])
    try:  # the library's principles that speak to this company's situation, as questions to put to it
        from . import investors as library  # noqa: PLC0415
        read = (out.get("checklists") or {}).get("read")
        p = out.get("price") or {}
        crowd = max((t["times_usual"] or 0 for t in signals.trending(memory, kind="ticker", days=1, limit=40)
                     if t["key"] == symbol), default=0)
        tags = library.situation(read, {"rsi": p.get("rsi"), "times_usual": crowd,
                                        "earnings_on": (out.get("finnhub") or {}).get("earnings_on")},
                                 falling=(p.get("vs_50_day_average") or 0) < -0.05)
        out["lessons_to_apply"] = [{k: x[k] for k in ("investor", "work", "year", "principle", "question")}
                                   for x in library.lessons(tags | {"always"}, 5, skip_always=False)]
        out["lessons_note"] = library.NOTE
    except Exception as e:
        out["missing"].append(f"lessons: {str(e)[:100]}")
    out["how_to_answer"] = FRAME
    return out


TOOL = {
    "name": "investment_journal",
    "description": (
        "The owner's investment journal and research committee. committee (symbol): gathers everything Buddy has "
        "on a company (investor checklists from its filings, price trend, Reddit, Finnhub facts, which followed "
        "funds reported holding it, the journal so far) and tells you how to argue it: strongest case for, "
        "strongest against, neutral, what would disprove each. Use it whenever the owner brings a stock idea or "
        "asks 'should I buy X', BEFORE giving a view. thesis (symbol, text, expects, invalidates): write down why "
        "they think it's worth owning, what they expect, and what would prove them wrong (required: ask for it). "
        "note (symbol, text): add a dated entry. review (symbol, conclusion?): each thesis against today's price "
        "and checklists; a conclusion is saved as a new entry. list (symbol?): the entries. Entries are never "
        "changed or deleted. Research and education, not advice; Buddy never trades."),
    "input_schema": {"type": "object", "properties": {
        "action": {"type": "string", "enum": ["committee", "thesis", "note", "review", "list"]},
        "symbol": {"type": "string"}, "text": {"type": "string"}, "expects": {"type": "string"},
        "invalidates": {"type": "string"}, "conclusion": {"type": "string"}},
        "required": ["action"], "additionalProperties": False},
}


def _today(memory: Memory, symbol: str) -> tuple[float | None, dict | None]:
    """Today's price and checklist read, for an entry's snapshot. (None, None) when they can't be had."""
    from . import lenses  # noqa: PLC0415
    from .markets import Data  # noqa: PLC0415
    try:
        closes = [b["c"] for b in Data(memory).bars("us", [symbol], days=30).get(symbol) or []]
        price = closes[-1] if closes else None
    except Exception:
        price = None
    try:
        read = lenses.checklists(memory, symbol, price)["read"] if price else None
    except Exception:
        read = None
    return price, read


def run_tool(memory: Memory, args: dict) -> str:
    a = args.get("action")
    if a == "list":
        return json.dumps([{k: e[k] for k in ("at", "symbol", "kind", "text", "expects", "invalidates", "price")}
                           for e in entries(memory, args.get("symbol") or "")])
    symbol = _symbol(args.get("symbol", ""))
    if a == "committee":
        return json.dumps(committee(memory, symbol), default=str)
    price, read = _today(memory, symbol)
    if a == "thesis":
        return json.dumps(thesis(memory, symbol, args.get("text", ""), args.get("expects", ""),
                                 args.get("invalidates", ""), price, read))
    if a == "note":
        return json.dumps(note(memory, symbol, args.get("text", ""), price))
    if a == "review":
        return json.dumps(review(memory, symbol, price, read, args.get("conclusion", "")))
    raise ToolError("Use committee, thesis, note, review or list.")
