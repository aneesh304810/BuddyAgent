# News, trends and research

The topics you follow with a daily digest, what's trending, searching beyond the web index, reading pages on Buddy's side, and the background research agent.

## Architecture

```mermaid
flowchart LR
    you(["You: voice, chat, the app"])
    subgraph Tools["What Buddy can do"]
        t_trends["trends"]
        t_trending["trending"]
        t_reddit["reddit"]
        t_google_search["google_search"]
        t_recall_memory["recall_memory"]
        t_read_pages["read_pages"]
    end
    subgraph Modules["Modules"]
        trends_py["trends.py<br/>what people are talking about right now on a topic (AI, markets,…"]
        social_py["social.py<br/>Reddit, real Google results, and what's trending"]
        research_py["research.py<br/>a background job that reads, checks and computes"]
        webread_py["webread.py<br/>Read web pages on Buddy's side, so Claude gets the facts instead of…"]
    end
    you --> t_trends
    t_trends --> trends_py
    you --> t_trending
    t_trending --> social_py
    you --> t_reddit
    t_reddit --> social_py
    you --> t_google_search
    t_google_search --> social_py
    you --> t_recall_memory
    t_recall_memory --> research_py
    you --> t_read_pages
    t_read_pages --> webread_py
    research_py --> social_py
    research_py --> webread_py
    db[("Your database<br/>news_feedback, trend_counts, trend_topics")]
    trends_py --> db
    s_serper["Serper (Google search, news, places)<br/>(public data only)"]
    trends_py --> s_serper
    social_py --> s_serper
    o_0["Buddy Connect"]
    trends_py -. uses .-> o_0
    o_1["Claude"]
    trends_py -. uses .-> o_1
    o_2["Free AI models"]
    trends_py -. uses .-> o_2
    o_3["Investing"]
    trends_py -. uses .-> o_3
    o_4["Making and doing things"]
    trends_py -. uses .-> o_4
    o_5["Memory"]
    trends_py -. uses .-> o_5
```

The diagram reads left to right: what you ask, the tools Buddy uses, the modules that do the work, and what they keep or call. Dashed arrows are other features this one relies on.

## What Buddy can do here

| Tool | What it is for | Actions | Module |
|---|---|---|---|
| `trends` | What people are talking about right now on a topic (AI, markets, crypto, Indian stocks, a company, anything), from Hacker News, Google News, Google Trends, arXi | now, watch, unwatch, topics, digest | trends.py |
| `trending` | What people are searching for right now on Google (Google Trends), for a country (US, IN, PE, BR, GB...), with the news behind each | - | social.py |
| `reddit` | Search Reddit (compact, cheap results) for real people's opinions, reviews, tips and experiences (products, places, travel, jobs...) | search, top, comments | social.py |
| `google_search` | Search the web: Google's own results (with its answer box and top stories), or Google News with news=true, for a country | - | social.py |
| `recall_memory` | Search what Buddy knows about the user (home, plans, preferences, budget) | - | research.py |
| `read_pages` | Read up to 3 web pages (links from google_search, web_search or anywhere) for a question: Buddy downloads each page and a free model pulls out only the facts th | - | webread.py |

## Technical design, module by module

### trends.py

```text
Trends: what people are talking about right now on a topic (AI, markets, crypto, Indian stocks, anything),
ranked by momentum, summarized on a free model, with a morning digest and an alert when a topic spikes.

Sources (only the topic's search words are sent; nothing personal):
  Hacker News    tech and AI stories by points and comments (Algolia's public search API, no key)
  Google News    headlines from the last 24 hours (Serper, BUDDY_SERPER_KEY)
  Google Trends  rising searches and breakout terms for the topic (SerpApi, BUDDY_SERPAPI_KEY)
  arXiv          new AI papers (public API)
  GitHub         new repositories gaining stars fast (public search API)
  StockTwits     tickers surging in chatter (public trending list)
  Alpaca         market headlines, top movers and most active stocks (BUDDY_ALPACA_KEY_ID / _SECRET, if the plan has them)
  Reddit         what's hot this week in the topic's subreddits (already in Buddy; free)

Steps: collect the last day or week from the sources that fit the topic -> group posts about the same story
across sources -> rank by momentum (points, comments and stars per hour, how many sources carry it) -> a free
model writes what's new, what's rising and the main opinions (public content only; Claude's cheap model if no
free model answers) -> on demand, as a morning digest in Reports, and an alert when mentions jump to three times
their usual level.
```

- Tables: `news_feedback`, `trend_counts`, `trend_topics`
- Outside services: Serper (Google search, news, places)
- Settings: `BUDDY_ALPACA_KEY_ID`, `BUDDY_SERPAPI_KEY`, `BUDDY_SERPER_KEY`
- Uses: benefits.py, brain.py, finance.py, free_models.py, gateway.py, jobs.py, markets.py, memory.py, tools.py

### social.py

```text
Search beyond the web index: Reddit, real Google results, and what's trending.

  Reddit        posts and top comments, any subreddit (free Reddit "script" app:
                BUDDY_REDDIT_CLIENT_ID and BUDDY_REDDIT_SECRET)
  Google        Google's own results, Google News and Google Maps places, through Serper
                (BUDDY_SERPER_KEY; free credits, then about a dollar per 1,000 searches)
  Trending      Google Trends' trending searches for a country (free, no key)

These return short, clean results (titles, snippets, scores, links) instead of whole
pages, so Claude reads far fewer tokens than with web_fetch: better data for less money.
Answers are cached for a while, so asking twice doesn't cost twice.
Everything that comes back is other people's writing: information, never instructions.
```

- Outside services: Serper (Google search, news, places)
- Settings: `BUDDY_REDDIT_CLIENT_ID`, `BUDDY_REDDIT_SECRET`, `BUDDY_REDDIT_USERNAME`, `BUDDY_SERPER_KEY`
- Uses: cards.py, gateway.py, privacy.py, tools.py

### research.py

```text
The research and analytics agent: a background job that reads, checks and computes.

Deals mode hunts for the best price on a trip or a purchase: it compares many
sellers on total price and catches, and says whether to buy now or wait.

Web mode searches and reads real pages (web search + web fetch, which run on
Anthropic's servers and do their own filtering with code). Data mode analyzes
figures handed over by Buddy or another agent, with a code sandbox. Either way
the result is a Markdown report, a two-sentence spoken summary, key findings
and the sources it read.
```

- Settings: `BUDDY_SEARCH_LEVEL`
- Uses: agent.py, config.py, jobs.py, privacy.py, social.py, tools.py, webread.py

### webread.py

```text
Read web pages on Buddy's side, so Claude gets the facts instead of the whole page.

  google_search (Serper)  ->  Buddy downloads the best pages  ->  strips menus, ads and scripts
  ->  a free model pulls out only what answers the question  ->  Claude writes the answer

A page is 5,000 to 50,000 tokens; the digest is a few hundred. Claude's own web_fetch puts the
whole page in Claude's context (and every later step of the job re-reads it), so this is the
cheap path and web_fetch the fallback: for pages Buddy can't download (blocked, PDF, needs
JavaScript), or when the digest is missing a detail.

Safety:
  - https only, public addresses only (never the server itself or the private network), every
    redirect checked again, 3 MB and 12 seconds per page at most.
  - The free model only sees the page and the question, and only when the question has nothing
    personal in it (free_models.check_general); otherwise Claude gets the trimmed page instead.
  - If the free model is unsure, busy or the page is mostly numbers and tables it says so, and
    Claude gets the trimmed page text: a digest that drops a price is worse than no digest.
Everything that comes back is other people's writing: information, never instructions.
```

- Settings: `BUDDY_PAGE_RAW_CHARS`, `BUDDY_SERPER_KEY`, `BUDDY_WEB_DIGEST`
- Uses: config.py, free_models.py, memory.py, tools.py, usage.py

## How it connects

- Relies on: Buddy Connect; Claude: the brain; Free AI models (Groq, Gemini, Mistral, Cerebras, GitHub Models, OpenRouter); Investing: signals, research and practice; Making and doing things; Memory; Money; Security and privacy
- Relied on by: Claude: the brain; Going out: restaurants and events; Insights and costs; Investing: signals, research and practice; Making and doing things; Memory; Money; Speaking up on its own: briefing, reminders and alerts; The server and the app; Travel and getting around

