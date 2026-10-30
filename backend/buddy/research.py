"""The research and analytics agent: a background job that reads, checks and computes.

Deals mode hunts for the best price on a trip or a purchase: it compares many
sellers on total price and catches, and says whether to buy now or wait.

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
from .config import claude_client, search_caps, settings
from .jobs import JobContext, JobFailed, JobResult
from .privacy import mask
from .social import SOCIAL_TOOL_NAMES, Social, run_social_tool, social_tools
from .tools import ToolError, local_now, web_fetch
from .webread import READ_TOOL, PageReader, google_and_read, run_read_tool

log = logging.getLogger(__name__)

client = claude_client()


@dataclass(frozen=True)
class Depth:
    effort: str
    max_tokens: int
    searches: int
    fetches: int
    max_steps: int  # API round trips: pauses of the server-side loop plus memory lookups


def depths() -> dict[str, Depth]:
    caps = search_caps()  # BUDDY_SEARCH_LEVEL: thrifty, generous or max
    return {
        "quick": Depth(effort="medium", max_tokens=32_000, searches=caps["quick_search"],
                       fetches=caps["quick_fetch"], max_steps=8 + caps["quick_search"] // 5),
        "deep": Depth(effort="high", max_tokens=64_000, searches=caps["deep_search"],
                      fetches=caps["deep_fetch"], max_steps=20 + caps["deep_search"] // 10),
    }


DEPTHS = depths()

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
- If you have them: reddit for real people's experiences and reviews, google_search for \
Google's own ranking or local results, trending for what's happening right now.
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
- Search (google_search first when you have it: cheaper, Google's ranking), then read \
the most relevant pages with read_pages: a free model pulls out the facts, so you can \
read more sources for less. Use web_fetch only for pages read_pages can't open, PDFs, \
or when you need a page's exact wording. Don't take facts or numbers from snippets alone.
- Every key claim needs two independent sources. Prefer primary sources: official \
sites, the vendor's own pricing page, government data, filings.
- When sources disagree, say so and which one you trust and why."""

DEALS_METHOD = """\
- This is a deal hunt ({category}). Find the best real options {user} can buy or book \
now: compare at least five across different sellers, airlines, hotels or booking sites, \
not just the first results. Use recall_memory for their home airport, sizes, brands, \
budget and preferences when it matters.
- Compare the TRUE total, not the headline price. Dig out the hidden costs sellers \
bury in the small print and add them in:
  - Flights: checked and cabin bags, seat selection, basic-economy limits (no carry-on, \
last to board, no changes), change and cancellation fees, airport and booking fees, \
card or payment surcharges, long or overnight layovers (and needing a transit visa), \
self-transfer itineraries where a missed connection is your problem.
  - Hotels and stays: resort, destination, city, cleaning and service fees, taxes shown \
only at checkout, parking, breakfast, Wi-Fi, deposits or card holds, extra-guest fees.
  - Shopping: shipping, import duty, restocking fees, warranty that isn't valid locally.
  - Everywhere: "pay in local currency" conversion markups (dynamic currency conversion).
- Check the cancellation and change rules for every option: refundable or not, the \
deadline, refund as cash or only credit, and whether a cheap third-party site (OTA) \
makes changes and refunds hard compared with booking direct. Look for complaints about \
the seller on Reddit and review sites (refunds not paid, bookings lost, overbooking).
- Check coupon codes, cashback, card offers, bundles, refurbished or open-box, nearby \
dates or airports, and whether booking direct is cheaper.
- Check the catches: seller and shop trust, reviews, warranty, stopovers and layover times.
- Look for price history (for example Google Flights' price insights, camelcamelcamel, \
Keepa) and say whether to buy now or wait, and why.
- Read the actual offer pages (read_pages with full=true keeps every price and fee; \
web_fetch when it can't open one); prices in snippets are often stale.
- In the report: a table of the best options with columns what, headline price, hidden \
fees, TRUE total, cancellation (refundable? until when?), where, link; then "Best value", \
"Cheapest true total" and, if different, "Best quality"; then "Watch out for" (the catches \
in plain words); then "Buy now or wait?" with a target price worth watching for."""

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
            web_fetch(depth.fetches),
        ]
        tools += social_tools(Social())  # Reddit, Google and trends, when connected
        tools.append(READ_TOOL)  # pages read by Buddy and digested by a free model: far fewer tokens
    return tools + [RECALL_TOOL]


def _system(mode: str, category: str = "") -> str:
    if mode == "data":
        method = DATA_METHOD
    elif mode == "deals":
        method = DEALS_METHOD.format(user=settings.user_name, category=category or "shopping")
    else:
        method = WEB_METHOD
    return SYSTEM.format(
        user=settings.user_name,
        assistant=settings.assistant_name,
        method=method,
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
    depth = depths().get(params.get("depth", "quick"), depths()["quick"])
    cap = settings.research_max_usd
    messages: list[dict] = [{"role": "user", "content": _first_message(params)}]
    response = None
    stopped_early = ""

    for _ in range(depth.max_steps):
        async with client.beta.messages.stream(
            model=settings.model,
            max_tokens=depth.max_tokens,
            system=_system(mode, params.get("category", "")),
            tools=_tools(mode, depth),
            messages=messages,
            output_config={"effort": depth.effort},
            cache_control={"type": "ephemeral"},  # each step re-sends the pages found so far: read them from cache
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
            messages.append({"role": "user", "content": await _run_tools(ctx, response.content)})
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


async def _run_tools(ctx: JobContext, content) -> list[dict]:
    results = []
    for block in content:
        if block.type != "tool_use":
            continue
        args = block.input if isinstance(block.input, dict) else json.loads(block.input)
        if block.name == "recall_memory":
            facts = ctx.memory.recall(str(args.get("query", "")))
            text, is_error = mask("\n".join(facts)) or "Nothing remembered about that.", False
        elif block.name in SOCIAL_TOOL_NAMES | {"read_pages"}:
            try:
                if block.name == "read_pages":
                    text = await run_read_tool(PageReader(ctx.memory), args)
                elif block.name == "google_search":
                    text = await google_and_read(Social(), PageReader(ctx.memory), args, run_social_tool)
                else:
                    text = await run_social_tool(Social(), block.name, args)
                is_error = False
            except ToolError as e:
                text, is_error = str(e), True
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
