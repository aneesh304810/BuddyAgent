# Buddy Intelligence Platform

A shared layer, like Buddy Connect, that collects public evidence on emerging technologies (papers, clinical studies, company filings, federal awards, code, discussion), finds which are converging across sectors and what stage each is at, and that Buddy's other features read from.

## Architecture

```mermaid
flowchart LR
    you(["You: voice, chat, the app"])
    subgraph Tools["What Buddy can do"]
        t_intelligence["intelligence"]
    end
    subgraph Modules["Modules"]
        bip_py["bip.py<br/>a shared intelligence layer inside Buddy, the way Buddy Connect is…"]
        bip_sources_py["bip_sources.py<br/>where its evidence comes from"]
        themes_py["themes.py<br/>the Intelligence Platform's evidence joined to the market, and tested…"]
        bip_models_py["bip_models.py<br/>Which AI model does each job of the Intelligence Platform, what it…"]
        factors_py["factors.py<br/>facts any of Buddy's prediction engines can look up when it makes a…"]
        bip_links_py["bip_links.py<br/>the companies a record names, and the relationships between…"]
    end
    you --> t_intelligence
    t_intelligence --> bip_py
    bip_py --> bip_links_py
    bip_py --> bip_models_py
    bip_py --> bip_sources_py
    bip_py --> factors_py
    bip_py --> themes_py
    themes_py --> bip_py
    themes_py --> bip_links_py
    themes_py --> bip_models_py
    themes_py --> bip_sources_py
    bip_models_py --> themes_py
    factors_py --> bip_py
    factors_py --> bip_links_py
    factors_py --> bip_sources_py
    factors_py --> themes_py
    bip_links_py --> bip_py
    bip_links_py --> bip_models_py
    bip_links_py --> bip_sources_py
    db[("Your database<br/>bip_extracted, bip_links, bip_mentions, bip_notes, bip_signals, theme_events, theme_news, theme_tone")]
    bip_py --> db
    themes_py --> db
    bip_links_py --> db
    s_nsf["NSF (research grants)<br/>(public data only)"]
    bip_sources_py --> s_nsf
    s_openai["OpenAI (paid AI, Intelligence Platform only)<br/>(public data only)"]
    bip_models_py --> s_openai
    o_0["Buddy Connect"]
    bip_py -. uses .-> o_0
    o_1["Claude"]
    bip_py -. uses .-> o_1
    o_2["Free AI models"]
    bip_py -. uses .-> o_2
    o_3["Investing"]
    bip_py -. uses .-> o_3
    o_4["Learning"]
    bip_py -. uses .-> o_4
    o_5["Memory"]
    bip_py -. uses .-> o_5
```

The diagram reads left to right: what you ask, the tools Buddy uses, the modules that do the work, and what they keep or call. Dashed arrows are other features this one relies on.

## What Buddy can do here

| Tool | What it is for | Actions | Module |
|---|---|---|---|
| `intelligence` | The Buddy Intelligence Platform: public evidence about emerging technologies and industries (research papers, registered clinical studies, company filings, US f | overview, technology, organisation, search, switch, scan, relationships, themes, patterns, predictions, models, set_model, set_limit, test_model | bip.py |

## Technical design, module by module

### bip.py

```text
Buddy Intelligence Platform (BIP): a shared intelligence layer inside Buddy, the way Buddy Connect is the
shared layer for outside services. It collects public evidence about technologies, works out which are forming
into industries and which are converging, and Buddy's features (Investments, News, Security, the briefing, the
tutor) read from it through the functions at the bottom of this file instead of each gathering their own.

  The map         ten sectors, each with its technologies (SECTORS). Four are switched on to start: AI,
                  cybersecurity, AI infrastructure and semiconductors, and energy. The others are defined and
                  can be switched on; each night costs only free API calls.
  Evidence        dated signals from free official and first-hand sources (bip_sources.py): research papers,
                  registered clinical studies, company filings, US federal awards and research grants,
                  private fundraising notices, new code, news and discussion. Every signal keeps its source,
                  its link, the date it happened and the date Buddy first saw it, what kind of evidence it
                  is, and its standing: verified (an official record exists) or reported (someone's claim).
  One name        the same organisation written different ways ("Oklo Inc. (OKLO)", "OKLO INC") is counted
                  once, by a normalised name.
  Tagging         each signal is tagged with the technologies it mentions, by their names and terms, in code.
  Convergence     two technologies from different sectors that keep appearing in the same signals. Ranked by
                  how many signals join them lately, how many kinds of evidence, and whether it is growing.
  Maturity        a stage for each technology and each convergence, from the evidence collected, by fixed
                  rules: only papers = scientific research; working code = prototype; a registered study or a
                  government award = pilot; companies writing about it in filings = early commercialization;
                  many companies = scaling. The rule that fired is shown. A sector that is long established
                  is marked so, not "discovered".
  Formation       an alert, once, when three or more independent kinds of evidence for a technology are all
                  rising at the same time.
  Pulse           the sectors that are switched off are not ignored: once a week each of their technologies
                  gets a light check (recent company filings and discussion only). When a sector's movement
                  jumps against its own earlier weeks, Buddy says so once and suggests switching it on, so
                  something starting there isn't missed.
  Interpretation  for the strongest convergences a free model writes what the pair adds up to. It is stored
                  and shown as "AI interpretation", apart from the evidence, and never changes a stage or a
                  rank.

What it is not: market sizes are not computed (Buddy can't verify one); nothing here is investment advice or a
forecast; a stage is read from the evidence Buddy has, which is US-heavy (SEC, US federal awards) and limited to
what these sources publish. Everything is public data, kept in Buddy's public store (the DuckDB file beside the
database), never mixed with the owner's data, so free models may read it.
```

- Tables: `bip_notes`, `bip_signals`
- Uses: bip_links.py, bip_models.py, bip_sources.py, factors.py, memory.py, pricebook.py, themes.py, tools.py

### bip_sources.py

```text
Buddy Intelligence Platform: where its evidence comes from.

Every source is free, official or first-hand, and read through Buddy Connect (gateway.py), so each call is
rate-limited, logged and can be switched off. Only a technology's search phrase is sent; nothing about the owner.

  research     arXiv (preprints) and Europe PMC (biomedical papers): work published by its authors
  trials       ClinicalTrials.gov: registered clinical studies, with sponsor and phase
  filings      SEC EDGAR full-text search: company filings (8-K, 10-K, S-1) that mention the technology
  awards       USAspending.gov: US federal contracts and grants that mention it, with recipient and amount
  code         GitHub: new public repositories about it
  discussion   Hacker News: stories about it that drew real attention
  news         GDELT: news coverage of it worldwide (the report's title, outlet and date)
  research     OpenAlex: scholarly works across every field, beyond the two sources above
  grants       NIH RePORTER, NSF and SBIR.gov: US research grants, including those to small companies
  funding      SEC Form D: a private company's own notice that it is raising money

What each kind of evidence is worth is fixed here, in EVIDENCE, and shown beside every item: an official record
is a fact that the record exists; a paper is its authors' claim; a filing is the company's own statement; a
story is a report. None of it is Buddy's opinion.

Not used: paid databases (Crunchbase, PitchBook, CB Insights) and anything that would need scraping.
A source that fails is recorded and skipped; the others still run.
```

- Outside services: NSF (research grants)
- Uses: funds.py, gateway.py

### themes.py

```text
Themes: the Intelligence Platform's evidence joined to the market, and tested with practice money.

  Themes     each technology the platform follows, with the public companies its filings name, how its evidence
             is moving, and which of those companies are on the owner's watchlist or in their trade ideas and
             practice portfolio. Matching is plain code: no model sees the owner's lists.
  Patterns   fixed rules that notice a change in the evidence (PATTERNS below): several kinds rising at once,
             a jump in company filings, a jump in news, a jump in research, a large award or grant, a run of
             private fundraising notices. Each time a rule fires for a technology that has public companies, an
             event is written with that day's closing prices.
  News       news is read, not only counted. A free model labels each headline about a technology or one of its
             companies as good, bad or neutral for it (headlines are public; the label is shown beside the
             headline so it can be checked, and it will sometimes be wrong). Good and bad news each have their
             own pattern and their own record. News about the companies themselves is searched by company name.
  Signals    each company also shows whether Buddy's Signals practice book has an open buy or avoid on it.
  Practice   every event is a practice basket: pretend money split equally across up to five of the companies,
             measured after 30 and after 90 days against the S&P 500 fund (SPY) over the same days. Nothing is
             bought. No costs or taxes are taken off, so real results would be a little worse.
  Record     per pattern: how many baskets have finished, how many beat the market, the middle result. Until a
             pattern has MIN_EVENTS finished baskets Buddy says only that there is not enough history. After
             that it says which way the pattern has leaned so far. It is a record of the past, never a promise,
             and events close together in time are not independent tests.

Stored in the public-data store beside the evidence: these are public prices and Buddy's own practice results.
```

- Tables: `theme_events`, `theme_news`, `theme_tone`
- Uses: bip.py, bip_links.py, bip_models.py, bip_sources.py, markets.py, memory.py, modeltrain.py, paper.py, tools.py

### bip_models.py

```text
Which AI model does each job of the Intelligence Platform, what it has cost, and a test to compare them.

Most of the platform uses no AI at all: tagging, stages, convergences, the patterns and the practice scoring are
code. The jobs that do use a model (JOBS) each have their own setting, so the cheapest model that does a job
well can be used for it and a dearer one kept for the few jobs that need it.

  Choices   free           Buddy's free models first, the cheap Claude model if none answers (the default)
            claude:fast    Claude's cheap model      claude:chat   its mid model      claude:deep   its strongest
            openai:cheap   an OpenAI model for volume work       openai:analysis   one for deeper analysis
            The OpenAI choices are there only when the owner has set its key (BUDDY_OPENAI_KEY), the model's id
            and its price (BUDDY_OPENAI_CHEAP_MODEL and _PRICE, BUDDY_OPENAI_ANALYSIS_MODEL and _PRICE; a price
            is "input,output" in US dollars per million tokens, from OpenAI's own pricing page). No id or price
            is built in, because they change and Buddy cannot check them.
  Limit     a monthly spending limit in dollars for the paid choices (CAP_KEY, $5 unless changed). At the limit
            a job goes back to the free choice until the month ends. Spending is counted per job.
  Test      a fixed set of headlines with the right label beside each (TEST). Any choice can be run on it to see
            how often it agrees, how long it took and what it cost, before a job is switched to it. The set was
            written for this purpose, not taken from the news, so treat a result as a guide and look at the
            labels on the Themes tab as well.

Only public records go through here (headlines, titles of papers and filings). Nothing about the owner does,
so a provider other than Claude is allowed for these jobs and for nothing else in Buddy.
```

- Outside services: OpenAI (paid AI, Intelligence Platform only)
- Settings: `BUDDY_OPENAI_ANALYSIS`, `BUDDY_OPENAI_ANALYSIS_MODEL`, `BUDDY_OPENAI_CHEAP`, `BUDDY_OPENAI_CHEAP_MODEL`, `BUDDY_OPENAI_KEY`
- Uses: agent.py, benefits.py, config.py, free_models.py, gateway.py, memory.py, themes.py, tools.py, usage.py

### factors.py

```text
The shared signals library: facts any of Buddy's prediction engines can look up when it makes a call.

Buddy has three engines that learn from how their own calls turned out (modeltrain.py trains and tests a model for
each once it has enough judged calls): investing (signals and themes), travel (flight and hotel book-or-wait
calls) and shopping (buy-or-wait calls). Each engine keeps its own reasons. This library adds what the others
already know, so a call is saved with more to learn from:

  investing   what the Intelligence Platform holds on the technologies whose filings name the company: how many
              records in 90 days and how fast that is growing, how many kinds of evidence, the stage, good and
              bad headlines in 14 days (the technology's and the company's own), and evidence patterns that
              fired in the last 30 days
  travel      the season for the month of travel and for now (government price data for air fares and hotels),
              oil's move over 30 days (fuel feeds into fares), how many flight or hotel deals reached the inbox
              and feeds in 30 days against the 30 before, deals that name this route or place, days until travel
  shopping    deals for this item in 90 days from the inbox, from Reddit and from the feeds, the category's
              season now and the gain from its best month ahead

Every factor is a number, named f_..., worked out in code from what Buddy already stores (no AI, and no new
outside call except oil, once a day, when its free key is set). A factor that can't be worked out is left out, and
the trainer treats a missing factor as zero. Factors never decide a call themselves: they are saved with it, and
a model uses them only after it has passed its test on calls it never saw.

Only public data is read here: evidence, headlines, prices, deals. Nothing about the owner goes anywhere.
```

- Uses: bip.py, bip_links.py, bip_sources.py, market_school.py, memory.py, modeltrain.py, pricebook.py, seasons.py, themes.py, tools.py

### bip_links.py

```text
Who is connected to whom: the companies a record names, and the relationships between organisations.

The evidence store knows that two technologies share records. This adds the organisations: which companies a
headline is about, and who funds, supplies, buys from, partners with, acquires, invests in, licenses to or
competes with whom.

  From official records (code, no AI; standing: verified)
      A federal award names the agency and the recipient; an NIH, NSF or small-business grant names the funder
      and the organisation. Each becomes "agency funds organisation", with the record as its proof.
  From headlines (an AI model reads the headline; standing: reported)
      The model chosen for the 'extract' job (bip_models.py) is given headlines, a batch at a time, and asked
      for the companies each names and any relationship the headline states. Its answer is checked in code
      before anything is kept: every name must appear in the headline itself, the relationship must be one of
      the eight kinds, and the two sides must differ. A name the headline does not contain is dropped, so the
      model cannot add a company from its own memory. A headline is read once.

Every relationship keeps the record it came from, its date and its standing, so "A supplies B" can always be
traced to one headline or one award. A relationship from a headline is what was reported, not a checked fact,
and a single headline can be wrong. Only public records are read; nothing about the owner is involved.
```

- Tables: `bip_extracted`, `bip_links`, `bip_mentions`
- Uses: bip.py, bip_models.py, bip_sources.py, memory.py, tools.py

## How it connects

- Relies on: Buddy Connect; Claude: the brain; Free AI models (Groq, Gemini, Mistral, Cerebras, GitHub Models, OpenRouter); Investing: signals, research and practice; Learning: the English and investing tutors; Memory; Money; Shopping and deals; The price book, pattern learning and trained models
- Relied on by: Claude: the brain; Investing: signals, research and practice; Shopping and deals; Speaking up on its own: briefing, reminders and alerts; The price book, pattern learning and trained models; The server and the app; Travel and getting around

