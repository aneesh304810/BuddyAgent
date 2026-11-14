"""The morning tutors: a ten-minute investing lesson and a short English lesson, every morning.

  Investing (about ten minutes read aloud, every day)
    1. Today's markets: what moved and the likely reason (Market school's numbers; weekends recap the week)
    2. The idea of the day: one concept, explained simply and tied to the numbers
    3. What Buddy did and how: yesterday's signals with their reasons, the paper trades it opened and closed,
       walked through step by step with the arithmetic (how the stop was set, how the position was sized, what
       the result means), and what the results have taught it so far
    4. A lesson from the masters: one principle from the investor library and the question it makes you ask
    5. Check yourself: two questions with their answers
    Written from Buddy's own numbers only (public market data and its practice book: nothing personal), so a
    free model writes it when one is set up, else Claude's quick model; with neither (or the budget paused) the
    lesson is assembled from the numbers in code, shorter but complete. Kept in Reports.

  English (about five minutes, every day)
    From the owner's own sentences to Buddy in the last two days and what the speech engine struggled to hear:
    three corrections (what you said, better, why), one grammar point they illustrate, three words to practise
    saying (how to say each), one phrase to upgrade, and a sentence to say aloud. Their own words are personal,
    so only Claude reads them (never a free model). With too few sentences, or no model, a general tip from a
    built-in list and the words the speech engine misheard. The evening review (tutor.py) stays as it is.

Each arrives as a short spoken line with the full lesson on screen; "Listen" under it reads it aloud. Say "turn
off the English lesson", "investing lesson at 7", or "give me today's investing lesson".
"""

import json
import logging
import os
import time
from datetime import date, datetime, timedelta, timezone

from .config import settings
from .memory import Memory
from .tools import ToolError, local_now

log = logging.getLogger(__name__)

KINDS = ("investing", "english")
AT_HOUR = int(os.getenv("BUDDY_MORNING_TUTOR_AT", "8"))
WORDS = 1300  # about ten minutes read aloud

INVEST_PROMPT = """You are a patient, clear teacher. Write this morning's investing lesson for one adult learner
who is new to investing, to be READ ALOUD in about ten minutes (about {words} words). Use ONLY the facts below: no
other numbers, news, forecasts or quotations, and no advice to buy or sell anything. Explain every term the first
time in one plain sentence. Teach how things are done, not just what happened: when you describe what Buddy did,
walk through one real example from the facts step by step, including the arithmetic.

Use exactly these five sections, each starting with its title in bold on its own line (**Title**), then plain
spoken paragraphs (no lists, tables or symbols except in the last section):
**Today's markets** (what moved and the likely reason)
**The idea of the day** ("{concept}": explained simply, tied to today's numbers, with an everyday comparison)
**What Buddy did and how** (its signals and paper trades from the facts: why each was made, how the stop and the
position size were worked out, what the result teaches; if it did nothing, explain why doing nothing is a decision)
**A lesson from the masters** (the principle given, in your words, and how its question applies to today)
**Check yourself** (two questions as "- Q: ..." then "- A: ..." lines)
The principle is an idea summarised from the named work: never present it as a quotation.

Facts:
{facts}"""

ENGLISH_SCHEMA = {
    "type": "object",
    "properties": {
        "corrections": {"type": "array", "items": {"type": "object", "properties": {
            "you_said": {"type": "string"}, "better": {"type": "string"}, "why": {"type": "string"}},
            "required": ["you_said", "better", "why"], "additionalProperties": False}},
        "grammar": {"type": "object", "properties": {
            "title": {"type": "string"}, "rule": {"type": "string"},
            "examples": {"type": "array", "items": {"type": "string"}}},
            "required": ["title", "rule", "examples"], "additionalProperties": False},
        "pronunciation": {"type": "array", "items": {"type": "object", "properties": {
            "word": {"type": "string"}, "say_it": {"type": "string"}, "tip": {"type": "string"}},
            "required": ["word", "say_it", "tip"], "additionalProperties": False}},
        "upgrade": {"type": "object", "properties": {
            "instead_of": {"type": "string"}, "say": {"type": "string"}, "why": {"type": "string"}},
            "required": ["instead_of", "say", "why"], "additionalProperties": False},
        "practice": {"type": "string"},
    },
    "required": ["corrections", "grammar", "pronunciation", "upgrade", "practice"], "additionalProperties": False,
}
ENGLISH_PROMPT = """You are a warm, practical English tutor for {user}, who speaks English as an additional
language. From their own recent sentences to their assistant (typed or spoken), write this morning's short
lesson. Typing slips and speech-recognition errors (missing letters, run-together words, odd spellings) are NOT
mistakes: look only at real grammar, word choice and phrasing.
- corrections: the three most useful, from their sentences: what they said (shortened to the relevant part),
  the better version, and why in one plain sentence.
- grammar: the one rule those corrections point to: a short title, the rule in two plain sentences, two examples.
- pronunciation: three words to practise saying: prefer the words the speech engine struggled to hear (below);
  else useful words from their sentences that learners often mispronounce. say_it: a simple respelling with the
  stressed part in capitals (e.g. "COM-fuh-tuh-bul"); tip: one short line on the sound to get right.
- upgrade: one phrase they use often and a more natural way to say it.
- practice: one sentence to say aloud that uses the grammar point and one of the words.
Be encouraging and specific. Never mention anything private in their sentences beyond the words needed.

Words the speech engine struggled to hear: {unclear}

Their sentences:
{sentences}"""

TIPS = [  # used when there's too little to review, or no model: (title, rule, examples)
    ("Articles: a, an, the", "Use 'a' or 'an' the first time you mention one thing, and 'the' once we both know "
     "which one you mean.", ["I bought a phone. The phone is fast.", "Can you send me the report we discussed?"]),
    ("Present perfect for results now", "Use 'have' plus the past participle for something that happened and "
     "still matters now; use the simple past when you say when it happened.",
     ["I have finished the report.", "I finished the report yesterday."]),
    ("Questions need a helper verb", "In a question, the helper verb (do, does, did, is, can) comes before the "
     "subject.", ["How does this work?", "Did you check the price?"]),
    ("Much, many, a lot of", "Use 'many' with things you can count, 'much' with things you can't, and 'a lot "
     "of' with either in everyday speech.", ["How many alerts did I get?", "How much time do we need?"]),
    ("Prepositions of time", "Use 'at' for a clock time, 'on' for a day or date, and 'in' for a month, year or "
     "part of the day.", ["at 8 o'clock", "on Monday, in the morning"]),
    ("I want you to...", "After want, need or would like, use the person and then 'to' plus the verb.",
     ["I want you to check this.", "I need it to run every night."]),
    ("Its and it's", "'It's' is short for 'it is' or 'it has'; 'its' shows belonging.",
     ["It's ready.", "The app lost its connection."]),
]


def on(memory: Memory, kind: str) -> bool:
    return memory.get(f"tutor:morning:{kind}") != "0"


def hour(memory: Memory, kind: str) -> int:
    try:
        return max(4, min(12, int(memory.get(f"tutor:morning:{kind}:at") or AT_HOUR)))
    except ValueError:
        return AT_HOUR


def due(memory: Memory, kind: str, now: datetime | None = None) -> bool:
    now = now or local_now()
    return on(memory, kind) and now.hour >= hour(memory, kind) and \
        memory.get(f"tutor:morning:{kind}:day") != now.date().isoformat()


def mark(memory: Memory, kind: str, now: datetime | None = None) -> None:
    memory.set(f"tutor:morning:{kind}:day", (now or local_now()).date().isoformat())


def _keep(memory: Memory, kind: str, title: str, summary: str, report: str) -> int:
    """Into Reports, like the other lessons."""
    from .finance import purge_reports  # noqa: PLC0415
    from .jobs import SCHEMA as JOBS_SCHEMA  # noqa: PLC0415
    now = datetime.now(timezone.utc).isoformat()
    memory.db.executescript(JOBS_SCHEMA)
    purge_reports(memory)
    memory.db.execute("DELETE FROM jobs WHERE kind = ? AND title = ?", (kind, title))
    job_id = memory.db.execute(
        "INSERT INTO jobs (kind, title, params, status, created_at, started_at, finished_at, summary, report, findings, "
        "sources, delivered) VALUES (?, ?, '{}', 'done', ?, ?, ?, ?, ?, '[]', '[]', 1)",
        (kind, title, now, now, now, summary[:600], report)).lastrowid
    memory.db.commit()
    return job_id


# --- the investing lesson -------------------------------------------------------------------------------------
def engine_facts(memory: Memory, today: date) -> dict:
    """What Buddy's own engine did since yesterday, and what it has learned: for 'what Buddy did and how'."""
    from . import pricebook, recommend, simbook  # noqa: PLC0415
    out: dict = {"rules": "each paper trade risks 1% of a $100,000 pretend book; the stop is twice the stock's "
                          "average daily range (between 4% and 12%); the target is twice the stop distance; a trade "
                          "is closed after 28 days; open positions together may risk at most 6%"}
    since = (today - timedelta(days=3 if today.weekday() == 0 else 1)).isoformat()
    try:
        out["signals"] = [{"day": s["day"], "symbol": s["key"], "call": s["action"], "why": s["reasons"][:4],
                           "price": s["price_then"], "result_so_far": s["status"]}
                          for s in recommend.recent(memory, 4, today) if s["day"] >= since][:3]
        simbook._ready(memory)
        out["paper_trades_opened"] = [
            {"symbol": t["symbol"], "kind": "buy" if t["side"] == "long" else "avoid (no money on it)",
             "entry": t["entry"], "stop": t["stop"], "target": t["target"], "shares": t["shares"],
             "money_at_risk": t["risk"], "position_value": round(t["shares"] * t["entry"], 2)}
            for t in pricebook.run(memory, "SELECT * FROM sim_trades WHERE opened >= ? AND status != 'skipped' "
                                           "ORDER BY id LIMIT 3", (since,))]
        out["paper_trades_closed"] = [
            {"symbol": t["symbol"], "entry": t["entry"], "exit": t["exit"], "why_closed": t["why"],
             "result_pct": round(t["ret"] * 100, 1),
             "in_units_of_risk": round(t["shares"] * (t["exit"] - t["entry"]) / t["risk"], 1) if t["risk"] else None,
             "sp500_fund_same_days_pct": round(t["spy_ret"] * 100, 1) if t["spy_ret"] is not None else None}
            for t in pricebook.run(memory, "SELECT * FROM sim_trades WHERE closed >= ? AND status = 'closed' "
                                           "ORDER BY id LIMIT 3", (since,))]
        s = simbook.stats(memory)
        out["practice_book"] = {"value": s["money"]["equity"], "closed_trades": s["closed"],
                                "buys": s["buys"], "worst_fall_pct": s["money"]["worst_drawdown_pct"],
                                "open_positions": len(s["open"])}
        out["what_each_reason_has_earned"] = {k: v for k, v in s["weights"].items() if isinstance(v, dict)}
    except Exception:
        log.info("morning tutor: engine facts failed", exc_info=True)
    try:
        from . import modeltrain  # noqa: PLC0415
        out["model_training"] = {name: f"{d['rows']} of {d['needed']} judged calls"
                                 + (", model in use" if d["model_in_use"] else "")
                                 for name, d in modeltrain.status(memory)["domains"].items()}
    except Exception:
        pass
    return out


def invest_facts(memory: Memory, today: date | None = None, market=None) -> dict:
    from . import investors, market_school  # noqa: PLC0415
    today = today or local_now().date()
    try:
        m = market(memory) if market else market_school.lesson_data(memory)
    except Exception as e:
        log.info("morning tutor: market data failed: %s", e)
        m = {}
    p = investors.PRINCIPLES[today.toordinal() % len(investors.PRINCIPLES)]
    return {"day": today.isoformat(), "weekday": today.strftime("%A"), "market_open_today": today.weekday() < 5,
            "markets": {k: m.get(k) for k in ("indexes", "sectors_best", "sectors_worst", "commodities", "bonds",
                                              "macro") if m.get(k)},
            "theme_to_watch": (m.get("themes") or [None])[0],
            "concept": m.get("concept") or {"key": "", "title": "Risk before reward",
                                           "text": "Decide how much you can lose on an idea before thinking about "
                                                   "how much you might make."},
            "buddy": engine_facts(memory, today),
            "principle": {k: p[k] for k in ("investor", "work", "year", "principle", "question")}}


def invest_plain(f: dict) -> str:
    """The lesson from the numbers alone: shorter, but every section is there."""
    from . import market_school  # noqa: PLC0415
    b, c, p = f["buddy"], f["concept"], f["principle"]
    try:
        markets = market_school.plain({"indexes": {}, "sectors_best": [], "sectors_worst": [], "commodities": {},
                                       "bonds": {}, "macro": {}, "themes": [], **f["markets"],
                                       "concept": {"key": "", "title": c["title"], "text": ""}})
    except Exception:
        markets = ""
    did = []
    for s in b.get("signals") or []:
        did.append(f"{s['symbol']}: a {s['call']} call at ${s['price']:,.2f}, because " + "; ".join(s["why"][:2]) + ".")
    for t in b.get("paper_trades_opened") or []:
        if t["shares"]:
            did.append(f"Paper {t['kind']} of {t['symbol']} at ${t['entry']:,.2f}. The stop is ${t['stop']:,.2f}, so "
                       f"each share risks ${t['entry'] - t['stop']:,.2f}. Risking ${t['money_at_risk']:,.0f} (1% of "
                       f"the book) divided by that gives {t['shares']:,.0f} shares, worth ${t['position_value']:,.0f}.")
    for t in b.get("paper_trades_closed") or []:
        did.append(f"Closed {t['symbol']}: {t['why_closed']}, {t['result_pct']:+.1f}%"
                   + (f" ({t['in_units_of_risk']:+.1f} times the amount risked)" if t["in_units_of_risk"] is not None
                      else "") + (f"; the S&P 500 fund did {t['sp500_fund_same_days_pct']:+.1f}% over the same days."
                                  if t["sp500_fund_same_days_pct"] is not None else "."))
    book = b.get("practice_book") or {}
    did.append((f"The practice book stands at ${book['value']:,.0f} after {book['closed_trades']} closed trades."
                if book else "") + " " + b["rules"].capitalize() + ".")
    if not (b.get("signals") or b.get("paper_trades_opened") or b.get("paper_trades_closed")):
        did.insert(0, "Buddy made no new signal: nothing got unusual attention with the trend and the tone agreeing. "
                      "Not trading is a decision too, and usually the right one.")
    return "\n".join([
        "**Today's markets**", markets or "No market numbers were available this morning.",
        "**The idea of the day**", f"{c['title']}. {c['text']}",
        "**What Buddy did and how**", " ".join(x for x in did if x.strip()),
        "**A lesson from the masters**",
        f"From {p['investor']} ({p['work']}, {p['year']}), in Buddy's words: {p['principle']} Ask yourself: {p['question']}",
        "**Check yourself**",
        "- Q: If a share costs $100 and the stop is at $93, how many shares risk $1,000?",
        "- A: Each share risks $7, so 1,000 divided by 7 is about 142 shares.",
        f"- Q: {p['question']}", "- A: There is no single answer: write yours down and look at it again in a month.",
    ])


async def invest_write(memory: Memory, f: dict) -> tuple[str, str]:
    """(the lesson, who wrote it): a free model, else Claude's quick model, else from the numbers."""
    prompt = INVEST_PROMPT.format(words=WORDS, concept=f["concept"]["title"],
                                  facts=json.dumps(f, ensure_ascii=False, default=str))
    try:
        from .free_models import FreeModels, NeedsClaude  # noqa: PLC0415
        free = FreeModels(memory)
        if free.enabled():
            try:
                got = await free.run("brainstorm", prompt, system="You write clear spoken lessons from given facts "
                                     "only.", purpose="morning_tutor", claude_fallback=False, check_text=False)
                if _whole(got["answer"]):
                    return got["answer"].strip(), "a free model"
            except NeedsClaude:
                pass
    except Exception as e:
        log.info("morning tutor: free model: %s", e)
    try:
        from . import usage  # noqa: PLC0415
        from .agent import fallback_kwargs  # noqa: PLC0415
        from .config import claude_client  # noqa: PLC0415
        model = settings.fast_model or settings.chat_model
        started = time.monotonic()
        r = await claude_client().beta.messages.create(model=model, max_tokens=4000,
                                                       messages=[{"role": "user", "content": prompt}],
                                                       **fallback_kwargs(model))
        if hasattr(r, "usage"):
            usage.record(memory, r.model, r.usage, purpose="morning_tutor", response=r, started=started)
        text = "\n".join(b.text for b in r.content if getattr(b, "type", "") == "text")
        if _whole(text):
            return text.strip(), "Claude"
    except Exception as e:  # the budget hold, no key, a refusal: the numbers still teach
        log.info("morning tutor: Claude: %s", e)
    return invest_plain(f), "Buddy's numbers (no model was available)"


def _whole(text: str) -> bool:
    """A model's lesson is used only if every section is there and it is a real length."""
    return len((text or "").split()) >= 500 and all(
        f"**{s}**" in text for s in ("Today's markets", "The idea of the day", "What Buddy did and how",
                                     "A lesson from the masters", "Check yourself"))


async def investing(memory: Memory, today: date | None = None, market=None, write=None) -> str:
    """Today's investing lesson as 'spoken || shown', kept in Reports."""
    f = invest_facts(memory, today, market)
    lesson, by = await (write or invest_write)(memory, f)
    key = f["concept"].get("key")
    if key:  # the idea counts as taught, so Market school moves on to the next
        from . import market_school  # noqa: PLC0415
        taught = [k for k in market_school._taught(memory) if k != key] + [key]
        memory.set(market_school.TAUGHT_KEY, json.dumps(taught[-60:]))
    minutes = max(1, round(len(lesson.split()) / 140))
    foot = (f"About {minutes} minutes read aloud; written by {by} from Buddy's own numbers. Education, not advice: "
            "Buddy never trades.")
    title = f"Investing lesson, {date.fromisoformat(f['day']):%b %d, %Y}"
    _keep(memory, "market_school", title, f"Today's idea: {f['concept']['title']}.", f"# {title}\n\n{lesson}\n\n{foot}")
    return (f"🎓 Your investing lesson is ready: about {minutes} minutes on {f['concept']['title'].lower()}, and what "
            f"I did yesterday and how. Tap Listen to hear it. || **🎓 Investing lesson · {f['weekday']}**\n{lesson}\n{foot}")


# --- the English lesson ---------------------------------------------------------------------------------------
async def english_write(memory: Memory, said: list[str], unclear: list[str]) -> dict | None:
    """Claude's quick model on the owner's own sentences (personal: never a free model). None when it can't."""
    try:
        from . import usage  # noqa: PLC0415
        from .agent import fallback_kwargs  # noqa: PLC0415
        from .config import claude_client  # noqa: PLC0415
        model = settings.fast_model or settings.chat_model
        started = time.monotonic()
        r = await claude_client().beta.messages.create(
            model=model, max_tokens=2500,
            output_config={"effort": "low", "format": {"type": "json_schema", "schema": ENGLISH_SCHEMA}},
            messages=[{"role": "user", "content": ENGLISH_PROMPT.format(
                user=settings.user_name, unclear=", ".join(unclear) or "none recorded",
                sentences="\n".join(f"- {s}" for s in said[-120:]))}],
            **fallback_kwargs(model))
        if hasattr(r, "usage"):
            usage.record(memory, r.model, r.usage, purpose="morning_tutor", response=r, started=started)
        if r.stop_reason == "refusal":
            return None
        return json.loads("".join(b.text for b in r.content if b.type == "text"))
    except Exception as e:
        log.info("morning tutor: English lesson: %s", e)
        return None


def english_plain(today: date, unclear: list[str]) -> dict:
    title, rule, examples = TIPS[today.toordinal() % len(TIPS)]
    words = [w.split(" (")[0] for w in unclear[:3]]
    return {"corrections": [], "grammar": {"title": title, "rule": rule, "examples": examples},
            "pronunciation": [{"word": w, "say_it": "", "tip": "The speech engine had trouble hearing this one: say "
                                                               "it slowly, one part at a time, then at full speed."}
                              for w in words],
            "upgrade": None, "practice": examples[0]}


def english_render(e: dict, weekday: str, by: str) -> str:
    out = [f"**🗣️ English lesson · {weekday}**"]
    if e["corrections"]:
        out.append("**From what you said**")
        out += [f"- You said: {c['you_said']}\n  - Better: **{c['better']}**\n  - Why: {c['why']}"
                for c in e["corrections"][:3]]
    g = e["grammar"]
    out += [f"**Grammar: {g['title']}**", g["rule"], *[f"- {x}" for x in g["examples"][:3]]]
    if e["pronunciation"]:
        out.append("**Say these aloud**")
        out += [f"- **{p['word']}**" + (f": {p['say_it']}" if p["say_it"] else "") + f". {p['tip']}"
                for p in e["pronunciation"][:3]]
    if e.get("upgrade"):
        u = e["upgrade"]
        out += ["**A more natural phrase**", f"- Instead of \"{u['instead_of']}\", say **\"{u['say']}\"**. {u['why']}"]
    out += ["**Practice**", f"Say this to me and I'll tell you how it sounded: \"{e['practice']}\"", by]
    return "\n".join(out)


async def english(memory: Memory, today: date | None = None, write=None) -> str:
    """Today's English lesson as 'spoken || shown', kept in Reports."""
    from . import tutor  # noqa: PLC0415
    today = today or local_now().date()
    said = tutor.sentences(memory, 2)
    unclear = (tutor.speech_summary(memory, 7) or {}).get("unclear_words") or []
    got = await (write or english_write)(memory, said, unclear) if len(said) >= 5 else None
    by = ("From your own sentences in the last two days; only Claude read them." if got else
          "A general tip today: " + ("there weren't enough of your sentences to review."
                                     if len(said) < 5 else "no model was available to review your sentences."))
    e = got or english_plain(today, unclear)
    shown = english_render(e, today.strftime("%A"), by)
    title = f"English lesson, {today:%b %d, %Y}"
    _keep(memory, "tutor", title, f"Today's grammar point: {e['grammar']['title']}.", f"# {title}\n\n{shown}")
    return (f"🗣️ Your English lesson is ready: {e['grammar']['title'].lower()}"
            + (f", {len(e['corrections'])} fixes from what you said" if e["corrections"] else "")
            + (f" and {len(e['pronunciation'])} words to practise" if e["pronunciation"] else "")
            + f". Tap Listen to hear it. || {shown}")


# --- the tool -------------------------------------------------------------------------------------------------
TOOL = {
    "name": "morning_tutor",
    "description": (
        "The owner's two morning lessons. investing: about ten minutes on today's markets, one idea, what Buddy's "
        "signal engine and practice book did yesterday and how (worked through with the arithmetic), a principle "
        "from the investor library and two check questions. english: about five minutes from their own sentences: "
        "three corrections, a grammar point, three words to practise saying, a phrase to upgrade and a sentence "
        "to say aloud. now (which): give today's lesson now; show the lesson text as it is and offer to read it. "
        "on / off (which: investing, english or both). time (which, hour: 4 to 12, local). status. Both are "
        "education; the investing lesson is never advice."),
    "input_schema": {"type": "object", "properties": {
        "action": {"type": "string", "enum": ["now", "on", "off", "time", "status"]},
        "which": {"type": "string", "enum": ["investing", "english", "both"]}, "hour": {"type": "integer"}},
        "required": ["action"], "additionalProperties": False},
}


async def run_tool(memory: Memory, args: dict) -> str:
    a, which = args.get("action"), args.get("which") or "both"
    kinds = KINDS if which == "both" else (which,)
    if a in ("on", "off"):
        for k in kinds:
            memory.set(f"tutor:morning:{k}", "1" if a == "on" else "0")
        return f"The morning {' and '.join(kinds)} lesson{'s are' if len(kinds) > 1 else ' is'} {a}."
    if a == "time":
        h = args.get("hour")
        if not isinstance(h, int) or not 4 <= h <= 12:
            raise ToolError("What hour, between 4 and 12 in the morning?")
        for k in kinds:
            memory.set(f"tutor:morning:{k}:at", str(h))
        return f"The morning {' and '.join(kinds)} lesson{'s' if len(kinds) > 1 else ''} will come at {h}:00."
    if a == "now":
        if which == "both":
            raise ToolError("Which lesson: investing or English?")
        text = await (investing(memory) if which == "investing" else english(memory))
        return json.dumps({"lesson": text.split(" || ", 1)[-1], "note": "Show it as it is. It is also in Reports."})
    return json.dumps({k: {"on": on(memory, k), "at": f"{hour(memory, k)}:00",
                           "last": memory.get(f"tutor:morning:{k}:day") or "never"} for k in KINDS})
