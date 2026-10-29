"""The research and analytics agent: a background job that reads, checks and computes.

Web mode searches and reads real pages (web search + web fetch, which run on
Anthropic's servers and do their own filtering with code). Data mode analyzes
figures handed over by Buddy or another agent, with a code sandbox. Either way
the result is a Markdown report, a two-sentence spoken summary, key findings
and the sources it read.
"""

import json
import logging
import re
from dataclasses import dataclass

import anthropic

from .agent import fallback_kwargs
from .config import claude_client, settings
from .jobs import JobContext, JobFailed, JobResult
from .privacy import mask
from .tools import local_now

log = logging.getLogger(__name__)

client = claude_client()


@dataclass(frozen=True)
class Depth:
    effort: str
    max_tokens: int
    searches: int
    fetches: int
    max_steps: int  # API round trips: pauses of the server-side loop plus memory lookups


DEPTHS = {
    "quick": Depth(effort="medium", max_tokens=32_000, searches=5, fetches=6, max_steps=8),
    "deep": Depth(effort="high", max_tokens=64_000, searches=15, fetches=20, max_steps=20),
}

RECALL_TOOL = {
    "name": "recall_memory",
    "description": "Search what Buddy knows about the user (home, plans, preferences, budget). "
    "Use only when the answer depends on the user's own situation.",
    "input_schema": {
        "type": "object",
        "properties": {"query": {"type": "string"}},
        "required": ["query"],
        "additionalProperties": False,
    },
}

SYSTEM = """\
You are the research and analytics specialist on {user}'s personal AI team (the \
assistant they talk to is {assistant}). You work in the background on one job and \
write a report {user} reads on their phone; {assistant} reads a short summary aloud.

How to work:
{method}
- Use recall_memory only when the answer depends on {user}'s own situation.
- {user} lives and travels in the USA and South America and has ties to India: use \
the currency and units of the place in question.
- Today is {today}. Say how current each key number is.
- If something can't be confirmed, say so plainly rather than guessing.

The final report is your last message, in Markdown:
- Start with the answer in two or three sentences.
- Then short sections or a table with the details, comparisons and numbers.
- Then "Open questions", only if something couldn't be confirmed.
- End with "Sources": one line per page you actually read, as [title](url).
Plain words, no filler, as short as the question allows."""

WEB_METHOD = """\
- Break the question into a few sub-questions before searching.
- Search, then open and read the most relevant pages with web_fetch. Don't take \
facts or numbers from search snippets alone.
- Every key claim needs two independent sources. Prefer primary sources: official \
sites, the vendor's own pricing page, government data, filings.
- When sources disagree, say so and which one you trust and why."""

DATA_METHOD = """\
- The data to analyze is in the user message. Use code for every calculation, \
total and comparison; never do arithmetic in your head.
- Check the data first: missing values, duplicates, odd outliers. Mention what you \
had to clean.
- Show the key figures in tables; describe trends with numbers (amounts, \
percentages, months)."""

SUMMARY_SCHEMA = {
    "type": "object",
    "properties": {
        "spoken_summary": {"type": "string"},
        "key_findings": {"type": "array", "items": {"type": "string"}},
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
        "sources": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"title": {"type": "string"}, "url": {"type": "string"}},
                "required": ["title", "url"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["spoken_summary", "key_findings", "confidence", "sources"],
    "additionalProperties": False,
}

LINK = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)")


def _tools(mode: str, depth: Depth) -> list[dict]:
    if mode == "data":
        tools = [{"type": "code_execution_20260521", "name": "code_execution"}]
    else:
        # The 2026-02-09 web tools filter results with code themselves, so no
        # separate code execution tool alongside them.
        tools = [
            {"type": "web_search_20260209", "name": "web_search", "max_uses": depth.searches},
            {"type": "web_fetch_20260209", "name": "web_fetch", "max_uses": depth.fetches},
        ]
    return tools + [RECALL_TOOL]


def _system(mode: str) -> str:
    return SYSTEM.format(
        user=settings.user_name,
        assistant=settings.assistant_name,
        method=DATA_METHOD if mode == "data" else WEB_METHOD,
        today=local_now().strftime("%A %d %B %Y"),
    )


def _first_message(params: dict) -> str:
    question = mask(params["question"])
    if params.get("mode") == "data":
        return f"Question: {question}\n\nData:\n{mask(params.get('data', ''))}"
    return question


async def run_research(job: dict, ctx: JobContext) -> JobResult:
    params = job["params"]
    mode = params.get("mode", "web")
    depth = DEPTHS.get(params.get("depth", "quick"), DEPTHS["quick"])
    cap = settings.research_max_usd
    messages: list[dict] = [{"role": "user", "content": _first_message(params)}]
    response = None
    stopped_early = ""

    for _ in range(depth.max_steps):
        async with client.beta.messages.stream(
            model=settings.model,
            max_tokens=depth.max_tokens,
            system=_system(mode),
            tools=_tools(mode, depth),
            messages=messages,
            output_config={"effort": depth.effort},
            **fallback_kwargs(settings.model),
        ) as stream:
            response = await stream.get_final_message()
        ctx.record(response)

        if response.stop_reason == "refusal":
            raise JobFailed("Claude declined this research request.")
        messages.append({"role": "assistant", "content": response.content})

        if ctx.cost >= cap and response.stop_reason in ("pause_turn", "tool_use"):
            stopped_early = f"Stopped at the ${cap:.2f} per-job limit; this is what was found so far."
            break
        if response.stop_reason == "pause_turn":
            continue  # the server-side search loop wants another round; it resumes by itself
        if response.stop_reason == "tool_use":
            messages.append({"role": "user", "content": _run_tools(ctx, response.content)})
            continue
        if response.stop_reason == "max_tokens":
            stopped_early = "The report hit its length limit and may be cut short."
        break
    else:
        stopped_early = "Stopped after the maximum number of research rounds."

    report = _final_text(response)
    if not report:
        raise JobFailed(stopped_early or "The research finished without a report.")
    if stopped_early:
        report = f"_{stopped_early}_\n\n{report}"
    return await _summarize(ctx, params["question"], report)


def _run_tools(ctx: JobContext, content) -> list[dict]:
    results = []
    for block in content:
        if block.type != "tool_use":
            continue
        if block.name == "recall_memory":
            args = block.input if isinstance(block.input, dict) else json.loads(block.input)
            facts = ctx.memory.recall(str(args.get("query", "")))
            text, is_error = mask("\n".join(facts)) or "Nothing remembered about that.", False
        else:
            text, is_error = f"Unknown tool {block.name}.", True
        results.append({"type": "tool_result", "tool_use_id": block.id, "content": text, "is_error": is_error})
    return results


def _final_text(response) -> str:
    if response is None:
        return ""
    return "\n".join(b.text for b in response.content if b.type == "text").strip()


async def _summarize(ctx: JobContext, question: str, report: str) -> JobResult:
    """A small structured call turns the report into what Buddy says aloud."""
    try:
        response = await client.beta.messages.create(
            model=settings.model,
            max_tokens=4_000,
            output_config={
                "effort": "low",
                "format": {"type": "json_schema", "schema": SUMMARY_SCHEMA},
            },
            **fallback_kwargs(settings.model),
            messages=[{
                "role": "user",
                "content": (
                    "From this research report, give: spoken_summary, two short sentences a "
                    "friend would say aloud (no Markdown, no URLs, numbers said naturally); "
                    "key_findings, up to five one-line findings; confidence, how well the "
                    "sources settle the question (low if they conflict, are thin, old, or "
                    "leave the main question open); sources, every [title](url) "
                    f"in its Sources list.\n\nQuestion: {question}\n\nReport:\n{report}"
                ),
            }],
        )
        ctx.record(response)
        if response.stop_reason == "refusal":
            raise ValueError("refusal")
        data = json.loads("".join(b.text for b in response.content if b.type == "text"))
        return JobResult(
            summary=data["spoken_summary"].strip(),
            report=report,
            findings=[f.strip() for f in data["key_findings"]][:5],
            confidence=data.get("confidence", ""),
            sources=data["sources"],
        )
    except Exception as e:  # the report itself is done; fall back to plain extraction
        log.warning("research summary failed: %s", e)
        return JobResult(summary=_plain_summary(report), report=report, sources=_links(report))


def _plain_summary(report: str) -> str:
    text = LINK.sub(r"\1", report)
    text = re.sub(r"[#*_`>|]", "", text)
    sentences = re.split(r"(?<=[.!?])\s+", " ".join(text.split()))
    return " ".join(sentences[:2]).strip()


def _links(report: str) -> list[dict]:
    seen, links = set(), []
    for title, url in LINK.findall(report):
        if url not in seen:
            seen.add(url)
            links.append({"title": title, "url": url})
    return links
