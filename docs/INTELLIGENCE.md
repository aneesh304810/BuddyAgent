# Buddy Intelligence Platform

A shared intelligence layer inside Buddy, the way Buddy Connect is the shared layer for outside services. It
collects public evidence about technologies, works out which are forming into industries and which are
converging, and the rest of Buddy reads from it. Open it from the app menu: **🧭 Intelligence**.

## What it does

- **Follows sectors.** Ten are defined, with 54 technologies between them. Four are switched on to start:
  artificial intelligence, cybersecurity, AI infrastructure and semiconductors, and energy. The others
  (robotics, biomedical, fintech, cloud, startups, space and advanced computing) can be switched on from the
  Sectors tab or by saying "follow robotics".
- **Collects evidence every night** from free, official or first-hand sources, through Buddy Connect:

  | Source | Kind of evidence | Standing | What it means |
  |---|---|---|---|
  | arXiv, Europe PMC, OpenAlex | published research | reported | its authors' claim; not yet proven in use |
  | ClinicalTrials.gov | official record | verified | a registered study exists; nothing about the result |
  | SEC EDGAR full-text search | company disclosure | verified | the company's own statement in a filing |
  | SEC Form D | company disclosure | verified | a private company told the SEC it is raising money |
  | USAspending.gov | official record | verified | a US federal contract or grant was awarded |
  | NIH RePORTER, NSF, SBIR.gov | official record | verified | a US research grant was awarded; funded research, not a product |
  | GitHub | code published | verified | working code exists; not proof anyone uses it |
  | GDELT | reported | reported | a news report; check the outlet and what it cites |
  | Hacker News | reported | reported | a story people discussed; check what it links to |

  Only a technology's search phrase is sent. No paid databases, nothing scraped, no keys needed for any of them.
  **Standing** says how far a record can be relied on. *Verified* means the record itself exists in an official
  register (what it claims is still its author's claim). *Reported* means published by its authors or a third
  party. What the platform works out from many records (stages, convergences) is marked *inferred*, and
  anything an AI writes is *interpretation*. One never silently becomes another.
- **Counts an organisation once.** "Oklo Inc. (OKLO)" in a filing and "OKLO INC" in a grant are the same
  organisation: names are matched after dropping case, punctuation, tickers and company endings. This is name
  matching only, so two different organisations with the same name would be merged, and a parent and its
  subsidiary are not.
- **Finds convergence.** Two technologies from different sectors that keep appearing in the same records, for
  example small modular reactors and AI data centers. Ranked by how many records join them, how many kinds of
  evidence, and whether it is growing.
- **Gives each a stage, by fixed rules, and shows the rule.** Only papers: scientific research. Working code:
  prototype. A registered study or a government award: pilot deployment. Companies writing about it in
  filings: early commercialization. Many companies: scaling. A long-established field is marked established.
- **Says when an industry may be forming:** three or more independent kinds of evidence for one technology all
  rising in the same month. You are told once.
- **Watches the sectors you don't follow.** Once a week each gets a light check (recent filings and stories
  only). When a sector's movement jumps against its own earlier weeks, Buddy tells you once and suggests
  following it, so something starting there isn't missed.
- **Keeps interpretation apart from evidence.** For the strongest convergences a free AI model writes what the
  pair adds up to, from the record titles only. It is shown in its own box marked "AI interpretation, not
  evidence" and never changes a stage or a rank.

## How Buddy uses it

Buddy answers from it: "what industries are emerging?", "what is converging with AI?", "who is working on
small modular reactors?", "how mature is fusion?", "follow robotics". It is the background for investing and
technology questions, and Buddy says what kind of evidence each statement rests on.

## The API for other agents and apps

A small read-only API, so another AI agent or app can use the platform without touching anything else in Buddy.

- **Key:** set one with `sudo bash /opt/buddy/deploy/set.sh BUDDY_INTEL_API_KEY` (at least 32 characters). It
  opens this API only: not your data, not the settings, not the rest of Buddy. Your own access token works too.
- **Endpoints** (all `GET`, JSON, `Authorization: Bearer <key>`): `/api/intel/v1/describe`, `overview`,
  `technologies`, `technology?id=`, `convergences`, `organisation?name=`, `relationships?name=` (or `?id=`), `search?q=`.
- **describe** returns the endpoints, the sectors and technology ids, the stages, what each evidence label
  means and how to read the data, so an agent can learn the API from one call.
- **Where it runs:** on your own Buddy server, reachable where that server is (your private network). There is
  no hosted public service yet.

The public product page is `intelligence.html` on the website; the API documentation is `docs/intelligence.html`
there, behind the docs sign-in.

## What it is not

- Not investment advice, a forecast or a recommendation. A rising count of records is not a judgement that
  there is an opportunity.
- No market sizes: Buddy can't verify one, so it doesn't compute one.
- The sources are US-heavy (SEC, US federal awards) and only cover what they publish. Private funding rounds
  are not in them.
- A stage is read from the records Buddy has collected, not from a market study. It needs a few nights of
  records before growth and convergence mean much.
- Tagging is by each technology's names and terms, in code, so a record that uses unusual wording is missed
  and a passing mention is counted.

## Privacy and cost

Everything in it is public data, kept in Buddy's public store (the DuckDB file beside the database), apart
from your own data, so free models may read it. The sources are free; the only AI cost is the short
interpretation for up to five convergences a night, on a free model when one answers.

## Planned next

Companies in depth (funding filings, institutional holdings, financials), transparent research scores, weekly
reports, a Themes view on the Investments page, and a graph view of the convergences.

## Themes: the evidence joined to the market (themes.py)

Investments has a **Themes** tab, and Buddy News shows a short "the evidence changed" card when something new
is found. Ask "which themes touch my watchlist" or "does this pattern work".

- **Themes.** Each technology with the public companies its SEC filings name (the ticker is in the filing),
  its stage and growth, and which of those companies are on your watchlist, in your trade ideas or in the
  practice portfolio. That matching is plain code. No model sees your lists.
- **Patterns.** Fixed rules that notice a change in the evidence for a technology:

  | Pattern | Rule |
  |---|---|
  | Several kinds rising together | three or more kinds of evidence each at 3 or more records and 1.5 times the 30 days before |
  | A jump in company filings | 3 or more in 30 days and at least double the 30 days before |
  | A jump in news and discussion | 5 or more in 14 days and at least double the 14 days before |
  | A jump in good news | 3 or more reports labelled good in 14 days, at least double the 14 days before, and more good than bad |
  | A jump in bad news | 3 or more reports labelled bad in 14 days, at least double the 14 days before, and more bad than good |
  | A jump in research | 5 or more papers in 30 days and at least double the 30 days before |
  | A large award or grant | $10 million or more in the last 30 days |
  | A run of private fundraising | 3 or more notices in 30 days |

  When a rule fires for a technology that has public companies, an event is written with that day's closing
  prices. The same pattern is written at most once per technology in any 30 days.
- **News is read, not only counted.** Each night a free model labels every new headline about a technology
  or one of its companies as good, bad or neutral for it (up to 120 a night; headlines are public). The label
  is shown beside the headline so you can check it. It is made from the headline alone and is sometimes
  wrong. A headline the model gives no usable answer for stays unlabelled and is asked again another night.
- **News about the companies.** The news is also searched by company name for up to 15 companies a night
  (those on your lists first). Only headlines that name the company are kept. A company with a very short
  name is not searched, because the results would be about other things.
- **Joined with Signals.** Each company shows whether Buddy's Signals practice book has an open buy or a
  mark to avoid on it, and since when.
- **Practice baskets.** Every event is measured as pretend money split equally across up to five of the
  companies, after 30 and after 90 days, against the S&P 500 fund (SPY) over the same days. The practice
  strategy is the first pattern only: $10,000 of pretend money per basket, closed after 90 days. Nothing is
  bought, and no costs or taxes are taken off.
- **The record, and when Buddy speaks.** For each pattern the tab shows how many baskets finished, how many
  beat the market, and the middle result. Buddy says which way a pattern has leaned only after 30 finished
  baskets: up (60% or more beat the market and the middle result is above it), down (40% or fewer and below
  it), or no clear edge. Before that it says "not enough history".

**What this cannot do.** It is a record of the past, not a prediction. More evidence about a technology does
not mean its shares rise, and the price often moves before the records do. Events close together in time are
not independent tests, so 30 baskets in one hot quarter prove less than 30 spread over years. Only companies
that file with the SEC and have a ticker can be in a basket, so private companies, which are often the most
interesting ones, are left out. Results start counting from the day this is deployed; nothing is back-filled,
because records collected today for past dates would make the past look better than it was. It says nothing
about whether the whole market goes up or down.

## Which AI model does each job (maestro.py)

The app has an **Maestro** screen (see MAESTRO.md). Most of the platform uses no AI: tagging, stages, convergences, the
patterns and the practice scoring are code. The jobs that do use a model each have their own setting.

| Job | What it does | Used today |
|---|---|---|
| Label headlines | good, bad or neutral; up to 120 a night | yes |
| Write up a convergence | what two technologies add together; about 5 a night | yes |
| Find the companies in a record | the companies a headline names and the relationships it states; up to 100 a night | yes |
| Analyse a company or theme | evidence for and against, on request | not yet |
| Hard research questions | the strongest model, on request | not yet |

- **Choices.** Free models first with Claude's cheap model behind them (the default), Claude's cheap, mid or
  strongest model, and two optional OpenAI choices (a volume model and an analysis model).
- **OpenAI is optional and off until you set it up.** It needs three settings per choice, each entered with
  `sudo bash /opt/buddy/deploy/set.sh NAME`: `BUDDY_OPENAI_KEY`; `BUDDY_OPENAI_CHEAP_MODEL` (the model's id
  from OpenAI's model page); `BUDDY_OPENAI_CHEAP_PRICE` (input and output price in dollars per million tokens,
  for example `0.10,0.50`, from OpenAI's pricing page). The analysis choice uses `BUDDY_OPENAI_ANALYSIS_MODEL`
  and `BUDDY_OPENAI_ANALYSIS_PRICE`. No id or price is built in, because they change and Buddy cannot check
  them. Only public records are sent to it, and it is used for nothing else in Buddy.
- **A monthly limit.** Paid choices stop at a dollar limit you set ($5 to start). At the limit, a job goes
  back to the free choice until the month ends. Spending is shown per job.
- **A fallback.** If the chosen model fails or gives an unusable answer, the free choice is tried.
- **A test before you switch.** Any choice can be run on 48 headlines that each have a known right label
  (18 good, 17 bad, 13 neutral). The tab shows how many it got right, what it cost and how long it took.
  The headlines were written for the test, not taken from the news, so use the result as a guide and also
  look at the real labels on the Themes tab.

## Three prediction engines on one core (factors.py, modeltrain.py)

Intelligence has a **Predictions** tab. Ask "how are your predictions doing".

Buddy has one learning core and three engines on it. Each engine asks its own question, keeps its own reasons
and learns only from how its own calls turned out.

| Engine | The question | Its models |
|---|---|---|
| Investing | Will this beat the market? | investing signals (weeks); theme baskets (90 days) |
| Flights and hotels | Book now or wait: will the price fall 8% or more before the trip? | flight calls; hotel calls |
| Shopping | Buy now or wait: will the price fall 8% or more within your patience? | shopping calls |

- **The core (already in Buddy, now shared by all five models).** Every call is saved with its reasons and
  later judged against what happened. Once a model has 200 judged calls with at least 30 of each outcome, a
  model is trained in the sandbox on the oldest 70%, tested on the newest 30% it never saw, and used only if
  it beats always saying the average and is right at least 5 points more often on the calls it liked. A model
  that fails is not used. This runs weekly.
- **The shared signals library.** Each call is now also saved with what Buddy's other sources know:

  | Engine | Added to each call |
  |---|---|
  | Investing | the Intelligence evidence for the company's technologies (records in 90 days, growth, kinds, stage), good and bad headlines in 14 days, evidence patterns in 30 days. Reddit mentions and tone, price trend, insiders and analysts were already there |
  | Flights and hotels | the season for the travel month and for now (government price data), oil's 30-day move (flights), flight or hotel deals in your inbox and the feeds in 30 days against the 30 before, deals naming the route or place, days until travel |
  | Shopping | deals for the item in 90 days from your inbox, from Reddit and from the feeds, the category's season now and the gain from its best month ahead |

  These are numbers worked out in code from what Buddy already stores. They do not change a call by
  themselves: a model uses them only after passing its test.
- **The theme model.** A new model learns from the evidence events: which pattern fired, and a snapshot of
  the evidence that day. The outcome is whether the basket beat the market over the next 90 days. When it is
  in use, each open event on the Themes tab shows the model's odds.
- **History.** "Load the last 30 months" asks the sources that can answer about a past date (company
  filings, fundraising notices, federal awards, scholarly works, NSF grants), then replays the rules month
  by month using only records dated up to each day, with that day's prices and what followed. These events
  are marked as from history. It takes a while and runs in the background.
- **Daily checks.** Saved flight and hotel searches are re-run each morning (up to 20), and products with an
  open call get a price check (up to 15 a day). Each is a paid search.

**Limits.** The history only knows companies that exist today and that filed with a ticker, so it is
slightly flattering. News cannot be loaded for the past, so the good-news and bad-news patterns have no
history. Sources return at most 25 records per question, so past months are thinner than they really were.
Flights, hotels and shopping have no history to load: they learn from calls made from now on, which takes
months. A model may fail its test, and then Buddy keeps using its simpler rules and says so.

## What a model must do before Buddy trusts it, and after

Reaching 200 judged calls only earns a model a test. To be used it must pass every one of these on the
newest 30% of calls, which it never saw:

| Test | Applies to |
|---|---|
| 40 or more unseen calls, and 15 or more it liked (gave 55% or better) | every model |
| Its odds beat always saying the average (a lower Brier score, by 0.005 or more) | every model |
| The calls it liked came true at least 5 points more often than calls overall | every model |
| Its odds mean what they say: grouped by the odds it gave, the share that came true is within 10 points on average | every model |
| More accurate than a simple rule, "companies already rising keep rising", by 2 points or more | theme baskets |
| Tested in both rising and falling markets (10 or more calls from each), and the calls it liked held up in both | theme baskets, investing signals |

The Predictions tab shows each test with a tick or a cross, the calibration table and the results by market
direction.

- **After it is in use.** Every week Buddy checks the model on calls judged since it was trained. Once it has
  liked 20 or more of them, those must still come true more often than calls overall. If not, the model is
  retired automatically, you are told why, and the simpler rules take over until a new model passes.
- **Costs.** A theme basket has 0.2% taken off its return for buying and selling before it is compared with
  the market, in the scorecard, the pattern records and the model's training.
- **When a record became public.** An award or grant can be dated before it was published. In the rules and
  in the replay of history, these count as known only 30 days after their own date. Every evidence record
  also keeps the day Buddy first saw it.
- **Is it really running?** The top of the Predictions tab lists each job (nightly collection, sources,
  headline labels, company news, events, history, weekly training, buy-or-wait calls) as running, needs a
  look, or not yet, read from what the jobs on your server wrote down. Passing tests only show the code can
  run.

**What cannot be fixed, only disclosed.** The list of 54 technologies was chosen today, knowing which ones
came to matter. Results from loaded history therefore look better than a list chosen back then would have.
Events written from switch-on forward do not have this problem, which is why the live record after
promotion matters more than the history test.

## Companies and relationships (bip_links.py)

Open a technology in Intelligence to see **Who is connected to whom**, or ask "who funds Oklo" or "who
supplies whom in small modular reactors".

- **From official records (code, no AI; verified).** A federal award names the agency and the recipient. An
  NIH, NSF or small-business grant names the funder and the organisation. Each becomes "agency funds
  organisation", with the record as its proof.
- **From headlines (an AI reads the headline; reported).** Each night the model chosen for the "Find the
  companies in a record" job reads up to 100 new headlines, twelve at a time, and gives the companies each
  names and any relationship the headline states: funds, supplies, is a customer of, partners with,
  acquires, invests in, licenses to, competes with.
- **Checked in code before anything is kept.** Every name must appear in the headline itself as whole
  words, the kind must be one of the eight, and the two sides must differ. A company the headline does not
  contain is dropped, so the model cannot add one from its own memory. A headline is read once; a headline
  the model gave no usable answer for is tried again another night.
- **Where it shows up.** The technology view and the organisation view list the relationships. On the Themes
  tab, a company now also gets the technology headlines that name it, with their good or bad labels, and a
  count of relationships. The investing engine saves "relationships naming the company in 90 days" with
  each signal. The agent API has `relationships?name=` and `relationships?id=`.

**Limits.** A reported relationship is what one headline said, not a checked fact, and a headline can be
wrong or out of date. Names are matched by their normalised form, so two different organisations with the
same name are merged and a parent and its subsidiary are not. Only headlines are read, not the full
article or filing, so most real supplier and customer relationships will not be found this way. Official
funding links cover US agencies only.

## The recommended watchlist

Under your own watchlist (Money → Invest → Watchlist) Buddy keeps a second list from its market
intelligence, refreshed each night after the evidence scan (`watchpicks.py`).

- **Recommended.** A public company that a technology's filings name, where the company was named in a
  filing in the last 30 days or one of the technology's evidence patterns fired in the last 30 days. Each
  one says why and since when, and is grouped by sector, then technology. The strongest 30 are listed and
  are read by the daily scan next to your own symbols.
- **Archive.** A recommended company with nothing new for 30 days is moved to the archive, grouped the
  same way, with the dates it was recommended. It is not scanned. If its evidence picks up, it comes back
  by itself.
- **Dismissed.** One you removed with ✕. It is not recommended again unless you restore it.

Add puts a recommended company on your own watchlist. No AI model is used and no outside call is made to
build the list; it reads the public evidence already collected. A recommendation is a reason to look, not
advice to buy, and nothing is ever bought.

## Paper trades from the evidence

The Signals practice book used to open a paper trade only when a symbol had a spike of attention on Reddit,
so with no Reddit data nothing traded. Each weekday morning Buddy now also judges up to 15 companies from
the recommended watchlist and your own US symbols on the Intelligence evidence instead of the crowd:

- **Up:** the technology's records grew 25% or more on the 90 days before, or one of its patterns fired in
  30 days, and the news is not mostly bad.
- **Down:** two or more headlines read as bad in 14 days, and more bad than good.

That lean is added to the price trend, insiders and analysts exactly as the Reddit tone is. A paper buy
needs the evidence and the trend to agree; the trend alone opens nothing. "The Intelligence evidence" is
re-weighed from closed trades like the other reasons. Nothing real is ever bought or sold.

## The strategy lab

Paper trading that does not wait for Reddit or for evidence (`stratlab.py`). Each weekday morning six
well-known rules read the daily prices of about a hundred large US stocks and funds, and each opens its own
paper trades: trend following, momentum, buy the dip, breakout, sector rotation and insider buying. Every
trade is $1,000 of pretend money with a stop, a target and a time limit, and is compared with the S&P 500
fund over the same days. The figures each trade was opened on are kept, so a model can later learn which
conditions went with the wins. A rule is called unproven until 30 of its trades have closed. The Trading tab
shows each rule's record and every trade bought and sold; "Run the lab now" runs it without waiting for the
morning. No AI decides a trade and nothing real is ever bought.

### History, and strategies you add

**History.** Every rule in the lab is also replayed over the past five years of daily prices, one day at a
time, each day seeing only the prices up to that day. Those trades are kept apart from the live paper
trades and shown in their own column, so a rule can be judged without waiting months. Two things flatter
the past and the page says so: the list of companies is today's (those that failed are missing), and no
trading costs are taken off. The replay runs by itself the first time the lab runs, and from "Replay over
past prices" in the Trading tab.

**Your own strategies** (`stratrules.py`). In the Trading tab, upload a document that describes a strategy
(PDF, Word, web page, text) or paste its text. The server takes the text out, one model call writes the
strategy as a rule, and the rule is shown as proposed with anything it could not translate. After you
approve it, it is replayed over past prices and paper-trades every weekday with the built-in rules.

The rule can only be a short list of conditions on figures Buddy measures from daily prices (RSI, distance
from the 20, 50 and 200-day averages, returns over a week to a year, distance from the one-year high, new
highs, volume, daily range), an optional ranking, and a time limit. A document cannot add code: anything
outside that form is dropped, and what the strategy needs that Buddy cannot measure is listed back to you.
A document with nothing personal in it may be read by a free model; anything personal goes to Claude only.

### Where strategies and data come from

**What Buddy fetches by program** (only sources that offer it):

- **arXiv.** Every Sunday Buddy asks arXiv's own API for new quantitative finance papers and queues those
  whose title or abstract looks like a testable strategy (`researchdesk.py`). Nothing is read by a model until
  you pick one in the Trading tab.
- **GitHub.** Paste a link to a public file or folder. Buddy reads the repository's licence first and
  refuses a repository that states none.
- **Kenneth French data library.** The monthly returns of the market, size, value and momentum since 1926,
  fetched once a month (`benchmarks.py`). They are a yardstick for a rule's results, not a signal.
- **Fund holdings files**, as dated stock lists. Off until you switch it on, because the provider's terms
  were not checked. Uploading a holdings file you downloaded always works. Each list is kept with its date,
  so past membership builds up from the day you start, and one list can be chosen as the lab's universe.
- **Alpha Vantage and Marketaux** news sentiment (`newssent.py`), each with a free key
  (`BUDDY_ALPHAVANTAGE_KEY`, `BUDDY_MARKETAUX_KEY`). At most 12 companies a day from each. The score is kept
  with each paper trade and shown; no trade is decided on it alone.

**What you download yourself and upload:** papers from SSRN and journals, catalogue entries, blog articles
and your notes on books. Their terms allow a person to read or download, not a program.

**Every file is opened in the sandbox** (`docread.py`): a throwaway container with no network takes the
text out and only the text comes back. When the sandbox is not set up the same script runs inside Buddy,
and the result says which was used.

### Costs, splits and the model

Every lab result has 0.2% taken off for buying and selling. A rule's history is also shown three ways: by
the market when the trade opened (the S&P 500 fund above or below its 200-day average), for the earlier 70%
of the period against the latest 30%, and year by year, with a plain verdict. Closed lab trades, live and
replayed, are rows for the shared learning core under the domain "strategy lab trades": the model learns
which figures, rules and market conditions went with trades that made money after costs. It is trained in
the sandbox, tested on the newest 30% of trades it never saw, and used only if it passes; then each new lab
trade shows its odds. The rules keep trading either way.

### More figures a rule may read

- **Beta and volatility**, worked out from daily prices: how much a stock moves with the S&P 500 over a
  year, and its yearly swing over three months. These exist for every past day, so rules on them can be
  replayed.
- **Company figures** (`fundamentals.py`), from Finnhub once a week for the lab's companies: the latest
  earnings surprise, return on equity, gross margin, profit growth, price against profits and book value,
  and debt against equity. Only ticker symbols go out. Buddy holds only today's values of these, so a rule
  that uses one is paper-traded from now on and cannot be replayed over past prices; the rule's card says so.

Each added rule also says how much of the strategy it holds (all, part or little of it), judged from what
was left out and how much text there was to read. It is a prompt to check the rule against the document,
not a guarantee.

### Ideas from the lab

Money, Invest, Overview shows what the lab's rules picked in the last five days, one card per symbol,
strongest first. A pick counts as **backed** only when the rule that made it beat the market after costs on
at least 30 past trades. Two backed rules, or one backed rule the trained model also likes (55% or more),
make it "Stronger"; one backed rule is "Worth a look"; anything else is shown as "Unproven". Each card lists
the rules that picked it with their record. On a weekday when there are new Stronger ideas Buddy sends one
message. These are ideas to look at, not advice, and Buddy places no trade.

### Loading it all, and seeing what is loaded

The Trading tab opens with "Data for the trading engine": one line per source (recommended watchlist, lab
trades, the replay, factor returns, research queue, news sentiment, company figures, the model) saying what
is actually held, with counts and dates, and how its last load went, in the source's own words when it
failed. "Load everything now" runs them one after another in the background (`labsetup.py`); one failing
does not stop the rest. Everything also refreshes on its own schedule, so the button is for a first
install or when something is empty.

### Rules from the papers you supplied

Eleven SSRN papers were read for the rule each tests, and seven rules were written in the lab's form
(`strategy_seeds.py`): Faber's 10-month average, nearness to the 52-week high, momentum with rising volume,
low beta without lottery-like jumps, earnings surprise drift, the past-year return in a weak market, and
profitable and growing companies. Papers that test the same idea share one rule: three test the 52-week
high and three test the drift after earnings. They load once as proposed and trade only after you approve
each one. Two papers gave no rule of their own (a critique of Faber, and one that only re-tests the
52-week-high rule on indices), and the page lists them with the reason. The papers are not stored, only a rule in Buddy's own words and a
link. Every rule holds part of its paper, and what was left out is listed on it.

Five widely used rules with no single source are proposed alongside the paper rules, so they can be tested
on the same prices and costs: the 50-day average above the 200-day, MACD trend, dual momentum, low
volatility, and quality at a fair price. With the six built-in rules that covers the usual list (momentum,
moving-average crossover, RSI mean reversion, MACD, breakout, quality and value, dual momentum, low
volatility).

**Open-source research.** Two public repositories were checked for a licence and read: QuantConnect's
research notebooks (Apache-2.0) and the Machine Learning for Trading book's code (MIT). The research queue
offers folders of each as starting points, and Buddy now reads notebooks (their text and code, never their
stored outputs). Two rules were written from the QuantConnect notebooks: one spread below the 30-day
average, and lowest price against profits. The Machine Learning for Trading repository is a method for
building and testing models, not a set of buy rules, so it yields ideas for the figures a model learns
from; the same holds for the pairs-trading, Kalman-filter and neural-network notebooks, which the lab's
rule form cannot express.
