"""The investor library: principles from the classic investors and books, each tagged with the situation it
applies to, so Buddy's signals and its committee can bring the relevant lesson to a decision.

Each entry is a principle in Buddy's own words (never a quotation), who it comes from, the work it is set out in,
the situations it speaks to, and the question it makes you ask. A situation is a set of plain tags worked out in
code from the numbers: the price (expensive, cheap, no_profits), the business (strong, weak), the crowd (crowd,
stretched), the trend (falling), debt (high_debt), earnings_soon, unproven (Buddy's own record is short), and
always.

Honesty about sources: these were written from general knowledge of well-known works and have NOT yet been
checked line by line against the books and letters. Each entry carries checked = False until someone has. They
are summaries of ideas, so the risk is a misplaced emphasis or chapter, not an invented quote, but treat the
attribution as "set out in this work", not as a citation to a page. Historical case studies (what an investor
actually bought, when, and what happened) are not here: those need original sources and are added only with one.
"""

import json
import re

from .tools import ToolError

P = [  # (key, investor, work, year, situations, principle, question)
    ("margin_of_safety", "Benjamin Graham", "The Intelligent Investor", 1949, ("expensive", "no_profits"),
     "Pay clearly less than a conservative estimate of what the business is worth, so that a mistake in the "
     "estimate or plain bad luck doesn't cost you the investment.",
     "If my estimate of its value is a third too high, do I still come out all right at this price?"),
    ("mr_market", "Benjamin Graham", "The Intelligent Investor", 1949, ("falling", "crowd"),
     "The market's daily price is an offer you may take or ignore, made by a partner whose mood swings; it tells "
     "you his mood, not the value of the business.",
     "Has the business changed, or only the mood about it?"),
    ("investing_vs_speculating", "Benjamin Graham", "The Intelligent Investor", 1949, ("no_profits", "crowd"),
     "An investment rests on analysis that promises safety of the money put in and a satisfactory return; "
     "anything else is speculation, which is allowed, but should be named, kept small and kept separate.",
     "Am I investing on analysis, or speculating on a price move? If speculating, how small is the stake?"),
    ("wonderful_at_fair", "Warren Buffett", "Berkshire Hathaway shareholder letters", 1989,
     ("strong", "weak_cheap"),
     "A very good business bought at a fair price usually does better over time than a mediocre business bought "
     "cheaply, because the good one keeps compounding and the cheap one keeps disappointing.",
     "Is this cheap because it is a poor business? Would I rather own the better one at a fuller price?"),
    ("greed_and_fear", "Warren Buffett", "Berkshire Hathaway shareholder letters", 1986,
     ("crowd", "stretched", "falling"),
     "Be wary when everyone is eager to buy and interested when everyone is afraid; the crowd's enthusiasm is "
     "already in the price.",
     "Who is left to buy after this much enthusiasm? Who is left to sell after this much fear?"),
    ("circle_of_competence", "Warren Buffett", "Berkshire Hathaway shareholder letters", 1996, ("always",),
     "Stay with businesses you can actually understand; knowing where your understanding ends matters more than "
     "how wide it is.",
     "Can I explain how this company makes money, and what could stop it, in two sentences?"),
    ("invert", "Charlie Munger", "Poor Charlie's Almanack", 2005, ("always", "crowd"),
     "Turn the problem around: list what would make this investment fail and check each one, and watch for the "
     "pull of doing something because many others are.",
     "What are the three most likely ways this goes wrong? Am I here because of evidence, or because of the crowd?"),
    ("scuttlebutt", "Philip Fisher", "Common Stocks and Uncommon Profits", 1958, ("strong",),
     "Learn about a company from the people around it (customers, suppliers, competitors, former staff), look "
     "for durable growth and able, honest management, and hold a truly outstanding company for a long time.",
     "What do its customers and competitors say? Is the growth durable, or one good product?"),
    ("know_what_you_own", "Peter Lynch", "One Up on Wall Street", 1989, ("always",),
     "Know what kind of company it is (slow grower, steady large company, fast grower, cyclical, turnaround, "
     "asset play) and the specific reason you expect it to do well; different kinds are judged differently.",
     "Which kind of company is this, and what is my one-paragraph reason for owning it?"),
    ("growth_at_a_price", "Peter Lynch", "One Up on Wall Street", 1989, ("expensive",),
     "Compare the price-to-earnings multiple with the growth rate: a multiple well above the growth rate means "
     "you are paying for growth that hasn't happened yet.",
     "How many years of this growth are already in the price? What if growth halves?"),
    ("cyclical_trap", "Peter Lynch", "One Up on Wall Street", 1989, ("cheap",),
     "For cyclical companies a low price-to-earnings multiple can mean profits are at their peak, not that the "
     "stock is cheap; they often look cheapest just before earnings fall.",
     "Is this business cyclical? Are these peak earnings?"),
    ("permanent_loss", "Seth Klarman", "Margin of Safety", 1991, ("expensive", "high_debt"),
     "Risk is the chance of losing money for good, not how much the price moves; when nothing is priced with a "
     "margin of safety, holding cash and waiting is a position too.",
     "What is the realistic worst case here, and could I lose this money permanently?"),
    ("second_level", "Howard Marks", "The Most Important Thing", 2011, ("crowd", "stretched", "expensive"),
     "It isn't enough that a company is good; ask what everyone already believes and whether the price reflects "
     "it. Risk is highest when people feel it is lowest.",
     "What does the price already assume, and why would I be right where the consensus is wrong?"),
    ("good_and_cheap", "Joel Greenblatt", "The Little Book That Beats the Market", 2005, ("cheap_strong",),
     "Buying good businesses (high return on capital) at bargain prices (high earnings yield), as a rule and "
     "across many stocks, has worked over long periods, but with stretches of years when it lags, which is why "
     "few stay with it.",
     "Am I prepared to hold a rule-based approach through two or three bad years?"),
    ("debt_cycles", "Ray Dalio", "Principles for Navigating Big Debt Crises", 2018, ("high_debt",),
     "Booms fed by borrowing feel safest near their end; debt that looks affordable when income is rising "
     "becomes the problem when income stops rising.",
     "Could this company service its debt through two bad years?"),
    ("reflexivity", "George Soros", "The Alchemy of Finance", 1987, ("crowd", "stretched"),
     "Rising prices can improve a company's real prospects for a while (cheap funding, attention, talent), which "
     "pushes prices higher still; the same loop runs in reverse, so trends overshoot both ways.",
     "Is the price rising on results, or are the results depending on the rising price?"),
    ("luck_and_skill", "Nassim Nicholas Taleb", "Fooled by Randomness", 2001, ("unproven",),
     "A short run of good results says little about skill; chance alone produces winning streaks, and the "
     "people who survived are the ones we hear from.",
     "How many independent calls is this record built on? Would it look this good by luck?"),
    ("the_index_bar", "Burton Malkiel", "A Random Walk Down Wall Street", 1973, ("always", "unproven"),
     "After costs, most people who pick stocks or time the market do worse than a low-cost fund holding the "
     "whole market, so any approach should be measured against that fund.",
     "Has this approach beaten simply holding the index, after costs, over a long enough time?"),
    ("cut_losses", "Edwin Lefèvre", "Reminiscences of a Stock Operator", 1923, ("falling",),
     "Decide before you buy where you are wrong, and get out when the price reaches it; hoping a loser comes "
     "back is how small losses become large ones.",
     "At what price is this idea wrong, and will I really sell there?"),
    ("size_the_bet", "Edward Thorp", "A Man for All Markets", 2017, ("always",),
     "How much you put on matters as much as what you buy: keep each position small against your real edge, so "
     "that a run of losses can't take you out of the game.",
     "If this goes to zero, how much of my money is gone?"),
    ("risk_first", "Jack Schwager", "Market Wizards", 1989, ("always", "falling"),
     "The successful traders interviewed used very different methods, but nearly all put controlling losses "
     "ahead of making gains.",
     "What is my exit if I am wrong, and is it written down?"),
]
PRINCIPLES = [{"key": k, "investor": who, "work": work, "year": year, "applies_when": list(when),
               "principle": text, "question": q, "checked": False} for k, who, work, year, when, text, q in P]
NOTE = ("Ideas summarised in Buddy's words from well-known works, not quotations, and not yet checked against "
        "the originals. They are questions to ask, not what any investor would say about this company today.")


def situation(read: dict | None = None, facts: dict | None = None, falling: bool = False,
              unproven: bool = False) -> set:
    """The tags for a decision, from the checklists' read ({"price", "business"}) and a signal's facts."""
    read, facts = read or {}, facts or {}
    tags = set()
    price, business = read.get("price"), read.get("business")
    if price == "expensive":
        tags.add("expensive")
    if price == "no profits to value":
        tags.add("no_profits")
    if price == "cheap":
        tags.add("cheap")
    if business == "strong":
        tags.add("strong")
    if price == "cheap" and business == "strong":
        tags.add("cheap_strong")
    if price == "cheap" and business == "weak":
        tags.add("weak_cheap")
    if (facts.get("times_usual") or 0) >= 3:
        tags.add("crowd")
    if (facts.get("rsi") or 0) >= 75:
        tags.add("stretched")
    if facts.get("earnings_on"):
        tags.add("earnings_soon")
    if (facts.get("debt_to_income") or 0) > 4:
        tags.add("high_debt")
    if falling:
        tags.add("falling")
    if unproven:
        tags.add("unproven")
    return tags


def lessons(tags: set, limit: int = 3, skip_always: bool = True) -> list[dict]:
    """The principles that speak to this situation, the most specific first (most tags matched)."""
    scored = []
    for i, p in enumerate(PRINCIPLES):
        hit = sum(2 if tag in ("cheap_strong", "weak_cheap") else 1  # a combined situation is the more specific
                  for tag in tags & set(p["applies_when"]))
        if hit:
            scored.append((-hit, i, p))
    if not scored and not skip_always:
        scored = [(0, i, p) for i, p in enumerate(PRINCIPLES) if "always" in p["applies_when"]]
    out, seen = [], set()
    for _, _, p in sorted(scored, key=lambda s: s[:2]):
        if p["investor"] in seen:  # one per investor, so three lessons are three points of view
            continue
        seen.add(p["investor"])
        out.append(p)
    return out[:limit]


def line(p: dict) -> str:
    """One sentence for a signal's reasons."""
    return f"a question from {p['investor']} ({p['work']}, {p['year']}): {p['question']}"


def search(query: str) -> list[dict]:
    words = re.findall(r"[a-z]{3,}", (query or "").lower())
    if not words:
        raise ToolError("Search for what? An investor, a book or an idea.")
    hits = [p for p in PRINCIPLES
            if all(any(w in str(p[f]).lower() for f in ("investor", "work", "principle", "question", "key"))
                   for w in words)]
    return hits or [p for p in PRINCIPLES if any(w in (p["investor"] + " " + p["work"]).lower() for w in words)]


TOOL = {
    "name": "investor_library",
    "description": (
        "Principles from the classic investors and books (Graham, Buffett, Munger, Fisher, Lynch, Klarman, Marks, "
        "Greenblatt, Dalio, Soros, Taleb, Malkiel, Lefèvre, Thorp, Schwager), each with the question it makes "
        "you ask. search (query: an investor, book or idea): matching principles. list: every principle by "
        "investor. Buddy's signals and its committee already bring the relevant ones to a decision. These are "
        "ideas summarised in Buddy's words, not quotations, and not yet checked against the originals: say so, "
        "never present one as a quote, and never say what an investor would do about a company today."),
    "input_schema": {"type": "object", "properties": {
        "action": {"type": "string", "enum": ["search", "list"]}, "query": {"type": "string"}},
        "required": ["action"], "additionalProperties": False},
}


def run_tool(memory, args: dict) -> str:
    if args.get("action") == "search":
        return json.dumps({"principles": search(args.get("query", "")), "note": NOTE})
    return json.dumps({"principles": [{k: p[k] for k in ("investor", "work", "year", "principle", "question")}
                                      for p in PRINCIPLES], "note": NOTE})
