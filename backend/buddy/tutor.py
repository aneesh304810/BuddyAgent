"""English tutor: Buddy reviews how you speak and write to it, and helps you improve.

  - Every sentence you say or type to Buddy is kept for 30 days (in Buddy's encrypted database;
    it stays on Claude, never a free model).
  - Each evening (BUDDY_TUTOR_AT, default 20:00) when you've said enough that day, a review
    arrives in Reports with a notification: corrections (what you said, better, why), the
    patterns behind them, useful words, tips and a short practice. Ask any time: "how was my
    English today / this week?".
  - Live tips (say "turn on English tips"): after an answer, one short tip when a sentence of
    yours had a clear mistake.

Pronunciation and pace: when the server's speech engine (Whisper) hears you in Talk mode, it
scores how sure it was of each word and how fast you spoke. Only those numbers and the words it
struggled with are kept (never audio). Words it keeps struggling with point to pronunciation
worth practising; the review covers them, your pace and your tone. The phone's own recogniser
doesn't share audio, so this builds up with the "Server" or "Automatic" speech engine.

Speech recognition makes its own mistakes (missing words, odd spellings, run-together words when
you talk fast): the tutor ignores those and looks only at real grammar, word choice and phrasing.
"""

import asyncio
import json
import logging
import os
from datetime import datetime, timedelta, timezone

from .config import claude_client, settings
from .jobs import JobContext, JobFailed, JobResult
from .memory import Memory
from .privacy import mask
from .tools import ToolError, local_now

log = logging.getLogger(__name__)
client = claude_client()

ON = os.getenv("BUDDY_TUTOR", "1") != "0"
REVIEW_AT_HOUR = int(os.getenv("BUDDY_TUTOR_AT", "20"))
MIN_SENTENCES = 8
KEEP_DAYS = 30
SCHEMA = """CREATE TABLE IF NOT EXISTS utterances (
    id INTEGER PRIMARY KEY, at TEXT NOT NULL, text TEXT NOT NULL, via TEXT NOT NULL)"""
SPEECH_SCHEMA = """CREATE TABLE IF NOT EXISTS speech_samples (
    id INTEGER PRIMARY KEY, at TEXT NOT NULL, words INTEGER NOT NULL, seconds REAL NOT NULL,
    clarity REAL NOT NULL, unclear TEXT NOT NULL, lang TEXT NOT NULL DEFAULT '')"""
LIVE_KEY = "tutor:live"
LIVE_NOTE = ("\n[English tips are on: after your answer, if my sentence above had a clear grammar or word-choice "
             "mistake (not a speech-recognition slip), add one short friendly line: \"English tip: say ... "
             "instead of ...\". Nothing if it was fine.]")

REVIEW_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "level": {"type": "string", "description": "e.g. B2 (upper intermediate)"},
        "score": {"type": "integer", "description": "1-10 for this period"},
        "strengths": {"type": "array", "items": {"type": "string"}},
        "corrections": {"type": "array", "items": {"type": "object", "properties": {
            "you_said": {"type": "string"}, "better": {"type": "string"}, "why": {"type": "string"}},
            "required": ["you_said", "better", "why"], "additionalProperties": False}},
        "patterns": {"type": "array", "items": {"type": "object", "properties": {
            "pattern": {"type": "string"}, "rule": {"type": "string"}, "example": {"type": "string"}},
            "required": ["pattern", "rule", "example"], "additionalProperties": False}},
        "vocabulary": {"type": "array", "items": {"type": "object", "properties": {
            "instead_of": {"type": "string"}, "try": {"type": "string"}, "example": {"type": "string"}},
            "required": ["instead_of", "try", "example"], "additionalProperties": False}},
        "pronunciation": {"type": "array", "description": "Only from the speech data given; empty if none",
                          "items": {"type": "object", "properties": {
                              "word": {"type": "string"}, "say_it": {"type": "string", "description":
                                                                     "how to say it, simple respelling"},
                              "tip": {"type": "string"}},
                              "required": ["word", "say_it", "tip"], "additionalProperties": False}},
        "pace": {"type": "string", "description": "About speaking speed, from the speech data; empty if none"},
        "tone": {"type": "string", "description": "How they come across (polite, direct, clear) and how to sound "
                 "more natural or friendly when it matters"},
        "tips": {"type": "array", "items": {"type": "string"}},
        "practice": {"type": "array", "items": {"type": "string"}, "description": "3 short exercises"},
    },
    "required": ["summary", "level", "score", "strengths", "corrections", "patterns", "vocabulary",
                 "pronunciation", "pace", "tone", "tips", "practice"],
    "additionalProperties": False,
}

PROMPT = """You are a warm, practical English tutor for {user}, who speaks English as an additional language
and talks to their voice assistant through speech recognition (and sometimes typing).

Review the sentences below ({period}). Rules:
- Ignore speech-recognition slips: missing or doubled words, homophones (there/their), odd spellings
  of names, run-together words, missing punctuation. Only correct what {user} would really have said
  wrong: grammar, tense, articles, prepositions, word choice, word order, unnatural phrasing.
- Short voice commands ("remind me 6pm call mum") are fine as they are: don't correct telegraphic style.
- Pick the corrections that teach most (up to 12), most common pattern first. Keep {user}'s meaning.
- Be encouraging and specific. Plain words, no jargon.

- Pronunciation: use only the speech data below (words the speech engine was unsure of, many times
  over, are likely pronounced unclearly; names and rare words can be unclear for other reasons). For
  each, say how to pronounce it in simple respelling (e.g. "comfortable: KUMF-ter-bul") and one tip.
  Pace: natural conversational English is about 2.5 to 3 words a second; much faster is harder to
  follow and to recognise. Tone: from the sentences, how they come across and how to sound natural.

Speech data: {speech}

Sentences, oldest first:
{sentences}"""


def log_utterance(memory: Memory, text: str, via: str) -> None:
    if not ON or via != "user" or len(text.split()) < 3:
        return  # only Talk mode: anyone in the room can say "Hey Buddy"
    now = datetime.now(timezone.utc)
    memory.db.execute(SCHEMA)
    memory.db.execute("INSERT INTO utterances (at, text, via) VALUES (?, ?, ?)", (now.isoformat(), mask(text)[:500], via))
    memory.db.execute("DELETE FROM utterances WHERE at < ?", ((now - timedelta(days=KEEP_DAYS)).isoformat(),))
    memory.db.commit()


def log_speech(memory: Memory, stats: dict, lang: str = "") -> None:
    """Whisper's word confidence and pace for one thing said in Talk mode (no audio, no text)."""
    if not ON or not stats.get("words"):
        return
    now = datetime.now(timezone.utc)
    memory.db.execute(SPEECH_SCHEMA)
    memory.db.execute("INSERT INTO speech_samples (at, words, seconds, clarity, unclear, lang) VALUES (?, ?, ?, ?, ?, ?)",
                      (now.isoformat(), int(stats["words"]), float(stats["seconds"]), float(stats["clarity"]),
                       json.dumps(stats.get("unclear", [])), lang[:8]))
    memory.db.execute("DELETE FROM speech_samples WHERE at < ?", ((now - timedelta(days=KEEP_DAYS)).isoformat(),))
    memory.db.commit()


def speech_summary(memory: Memory, days: int) -> dict:
    """Pace, clarity and the words the speech engine struggled with most, over a period."""
    memory.db.execute(SPEECH_SCHEMA)
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    rows = [dict(r) for r in memory.db.execute("SELECT * FROM speech_samples WHERE at >= ?", (since,))]
    if not rows:
        return {}
    words, seconds = sum(r["words"] for r in rows), sum(r["seconds"] for r in rows)
    counts: dict[str, int] = {}
    for r in rows:
        for w in json.loads(r["unclear"]):
            counts[w.lower()] = counts.get(w.lower(), 0) + 1
    return {"utterances": len(rows), "words_per_second": round(words / seconds, 2) if seconds else None,
            "clarity": round(sum(r["clarity"] * r["words"] for r in rows) / words, 2) if words else None,
            "unclear_words": [f"{w} ({n}x)" for w, n in sorted(counts.items(), key=lambda kv: -kv[1])[:15]]}


def sentences(memory: Memory, days: int) -> list[str]:
    memory.db.execute(SCHEMA)
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    return [r["text"] for r in memory.db.execute("SELECT text FROM utterances WHERE at >= ? ORDER BY at", (since,))]


def live(memory: Memory) -> bool:
    return memory.get(LIVE_KEY) == "1"


def render(review: dict, period: str) -> str:
    lines = [f"# Your English: {period}", "", f"**{review['summary']}**", "",
             f"Level: {review['level']} · Score: {review['score']}/10", ""]
    if review["strengths"]:
        lines += ["## What you did well", *[f"- {s}" for s in review["strengths"]], ""]
    if review["corrections"]:
        lines += ["## Corrections", "| You said | Better | Why |", "|---|---|---|",
                  *[f"| {c['you_said']} | **{c['better']}** | {c['why']} |" for c in review["corrections"]], ""]
    if review["patterns"]:
        lines += ["## Patterns to work on"]
        for p in review["patterns"]:
            lines += [f"- **{p['pattern']}**: {p['rule']} _Example:_ {p['example']}"]
        lines.append("")
    if review["vocabulary"]:
        lines += ["## Words to try", *[f"- Instead of *{v['instead_of']}*, try **{v['try']}**: {v['example']}"
                                       for v in review["vocabulary"]], ""]
    if review.get("pronunciation"):
        lines += ["## Pronunciation", "| Word | Say it | Tip |", "|---|---|---|",
                  *[f"| {p['word']} | **{p['say_it']}** | {p['tip']} |" for p in review["pronunciation"]], ""]
    if review.get("pace"):
        lines += ["## Pace", review["pace"], ""]
    if review.get("tone"):
        lines += ["## Tone", review["tone"], ""]
    if review["tips"]:
        lines += ["## Tips", *[f"- {t}" for t in review["tips"]], ""]
    if review["practice"]:
        lines += ["## Practice", *[f"{i}. {p}" for i, p in enumerate(review["practice"], 1)]]
    return "\n".join(lines)


async def run_review(job: dict, ctx: JobContext) -> JobResult:
    from .agent import fallback_kwargs

    days = int(job["params"].get("days", 1))
    period = "today" if days == 1 else f"the last {days} days"
    said = sentences(ctx.memory, days)
    if len(said) < 3:
        raise JobFailed(f"There's not enough to review yet ({len(said)} sentences {period}). Talk to me a bit more!")
    spoken = speech_summary(ctx.memory, days)
    model = settings.fast_model or settings.chat_model
    response = await client.beta.messages.create(
        model=model, max_tokens=6000,
        output_config={"effort": "low", "format": {"type": "json_schema", "schema": REVIEW_SCHEMA}},
        messages=[{"role": "user", "content": PROMPT.format(
            user=settings.user_name, period=period, sentences="\n".join(f"- {s}" for s in said[-300:]),
            speech=json.dumps(spoken) if spoken else "none (the phone's own recogniser was used)")}],
        **fallback_kwargs(model),
    )
    ctx.record(response)
    if response.stop_reason == "refusal":
        raise JobFailed("The review couldn't be written.")
    review = json.loads("".join(b.text for b in response.content if b.type == "text"))
    summary = f"Score {review['score']}/10 ({review['level']}). {review['summary']}"
    if review["corrections"]:
        c = review["corrections"][0]
        summary += f" Top fix: say \"{c['better']}\" instead of \"{c['you_said']}\"."
    return JobResult(summary=summary[:600], report=render(review, period), findings=["tutor"])


def review_due(memory: Memory) -> bool:
    today = local_now().date().isoformat()
    return (ON and local_now().hour >= REVIEW_AT_HOUR and memory.get("tutor:last_review") != today
            and len(sentences(memory, 1)) >= MIN_SENTENCES)


async def run(memory: Memory, jobs) -> None:
    """Starts the evening review once a day, when there's enough to review."""
    while True:
        try:
            if review_due(memory):
                memory.set("tutor:last_review", local_now().date().isoformat())
                jobs.submit("tutor", f"English review, {local_now():%A %d %B}", {"days": 1})
        except Exception:
            log.exception("tutor review failed to start")
        await asyncio.sleep(600)


TUTOR_TOOL = {
    "name": "english_tutor",
    "description": "The user's English tutor. review: a review of how they spoke and wrote to you (corrections, "
    "patterns, words, tips, practice), today or over several days; it arrives in Reports. tips_on / tips_off: "
    "a short live tip after your answers when they make a mistake. status: what's on.",
    "input_schema": {"type": "object", "properties": {
        "action": {"type": "string", "enum": ["review", "tips_on", "tips_off", "status"]},
        "days": {"type": "integer", "description": "For review: 1 = today, 7 = this week"}},
        "required": ["action"], "additionalProperties": False},
}


def run_tutor_tool(memory: Memory, jobs, args: dict) -> str:
    action = args.get("action")
    if action == "tips_on":
        memory.set(LIVE_KEY, "1")
        return "English tips are on: one short tip after an answer when a sentence has a clear mistake."
    if action == "tips_off":
        memory.set(LIVE_KEY, "0")
        return "English tips are off."
    if action == "status":
        return json.dumps({"daily_review": ON, "review_at": f"{REVIEW_AT_HOUR}:00", "live_tips": live(memory),
                           "sentences_today": len(sentences(memory, 1)),
                           "sentences_this_week": len(sentences(memory, 7))})
    if action == "review":
        if jobs is None:
            raise ToolError("Reviews need the background jobs.")
        days = max(1, min(int(args.get("days") or 1), 30))
        job_id = jobs.submit("tutor", f"English review, {'today' if days == 1 else f'last {days} days'}", {"days": days})
        return f"Started your English review (job {job_id}); it'll be in Reports in a minute."
    raise ToolError("Actions: review, tips_on, tips_off, status.")
