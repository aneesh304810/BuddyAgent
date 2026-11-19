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

## Day-trade practice (`dayplan.py`)

Practice on paper, one US stock at a time. Nothing here can reach a broker. Tool: `day_trade_practice`.

| Step | What it does |
|---|---|
| Plan | The opening range is the first 15 minutes. The entry is the first 5-minute bar that closes beyond it. The stop (the middle of the range, or its far side) and the target (a multiple of that risk) are fixed beforehand. Out by 3:55 PM New York time whatever happens. |
| Replay | The rest of the day replayed 10,000 times from the stock's own 5-minute bars at the same time of day over its last sessions (up to 60, at least 20), and the same rule re-run on each of those real sessions: target first, stop first, or neither by the close. |
| Size | Whole shares so the stop costs no more than the limit for one trade, and the position no more than its share of the account. |
| Check | Pass or block: loss per trade, loss per day, trades per day, reward to risk, last entry time. Each reason is said in plain words. |
| Earnings | A plan is blocked on the session that reacts to an earnings report: a report before the open or during the session, or one after the previous close. A report after today's close is said, not blocked, because a day trade is out before it. From Finnhub's earnings calendar; with no Finnhub key there is no check. Off with the limit `block_on_earnings`. |
| Scan | Each weekday at 10:20 AM New York time, up to 30 stocks are checked for a close beyond the opening range, both ways: the ones in open practice trades and on the watchlists first, then fifteen of the most traded. Each hit gets its plan, 2,000 replays, size and check. Those that pass the limits are sent in one message, best replay first; when none passes, nothing is sent. Yesterday's kept trades are settled first. Off with the limit `morning_scan`, or by muting "the day-trade practice scan" in Alert settings. |

- **Source.** 5-minute bars from Alpaca's market data (`markets.Data.intraday`), through Buddy Connect. Only the
  ticker symbol is sent. It needs the Alpaca key already used for daily prices. On the free plan the bars cover
  every exchange but are about 15 minutes behind; the reply says so.
- **Practice trades.** `keep` records today's plan (after the owner's yes) in the price book table `day_trades`.
  After the close it is settled from that day's real bars, and `record` shows what the replays said beside what
  happened.
- **Limits.** Defaults: a $5,000 practice account, $50 a trade, $150 a day, 3 trades a day, a target at least equal
  to the risk, no entry after 3:00 PM, 2 cents a share for the spread. `limits` changes them.
- **What a replay is.** It is built from how much the stock has moved at each time of day. It says how far the
  price tends to travel, not which way, and it is not a prediction.
- **Where the method comes from.** It follows the "Time Machine" in day-trader-skill (MIT licence, copyright (c)
  2026 Jake Schincariol). The code is Buddy's own. It differs in three ways: official bars instead of an
  unofficial chart address; bars are drawn in runs of three from one past session, so a move's follow-through is
  kept; and a cost per share is taken off every outcome. The pack's broker, scanner and news parts were not
  brought in.
- **Not built.** No alert when a kept trade reaches its stop or target during the day, and no page in the app.
  The scan looks for one setup only (the opening-range close), and reads each stock's bars with one request.

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

**A page you point to.** In "Add a strategy from a document" you can give one page's address. Buddy fetches
that page once, at your request (https and public addresses only, every redirect checked), strips menus
and scripts, and reads the rest into a proposed rule that carries the link. Nothing is crawled or followed,
and the page is not stored. Pages behind a sign-in, or that load by script, cannot be read this way; paste
their text instead.

## News desk: stories, source ratings, and the paid slot

`newsdesk.py` sits between the free headlines and the trade figures.

- **Stories, not headlines.** Headlines about one company that share most of their words within three days are one
  story. Five outlets carrying one event count once in `f_good_news_14` / `f_bad_news_14`.
- **Source rating.** 3 = an established outlet, 2 = a press release or specialist outlet, 1 = not rated. A story takes
  the best rating among its reports. A low rating marks a story unclear; it still counts until a check says otherwise.
- **What a trade's figures hold.** A story the fact checks ran on and could not confirm is left out of the good and
  bad counts. Every trade also records `f_news_confirmed_14` (stories a rated outlet carries) and
  `f_news_unconfirmed_14` (stories left out), so a model can learn how much well-sourced news matters.
- **Unclear.** A story is unclear when only unrated outlets carry it, when its reports disagree on good or bad, or
  when it has no label yet.
- **A free model first.** An unclear story about a company Buddy is acting on (an open paper trade, an open lab trade,
  or a watchlist company) goes to the free model used for labelling headlines. It sees public headlines only. If it
  says the story is clear, nothing else is asked.
- **Free news next.** A story the free model could not settle (or got no answer on) is looked up in the free news
  sources Buddy already has (Finnhub, Marketaux, Alpha Vantage: whichever have a key; ticker symbols only). If a
  rated outlet carries the same story, that settles it.
- **Last resort.** Only a story free news could not confirm goes to a further news service to see who else reports
  it: NewsData.io (`BUDDY_NEWSDATA_KEY`), Perigon (`BUDDY_PERIGON_KEY`), NewsCatcher
  (`BUDDY_NEWSCATCHER_KEY`), whichever have a key, tried in that order; the next is tried when one fails. The search
  is narrow: the company's name AND two or three words that pin down the event, chosen by the free model (or taken
  from the headline), so one call returns that story's reports. Nothing else is sent. A matching report from a rated outlet is kept with the company's headlines, so the story's
  rating rises. With no key, the story is written to `news_checks` as "would have asked". A monthly limit (default 30)
  covers all three together.
- Runs once a day in the proactive loop; `GET/POST /api/invest/newsdesk`; shown in Investing → Trading as
  "News behind the trades" and "Paid news checks".

## Strategy repository (a part of Buddy Intelligence)

`stratrepo.py` is the catalogue over everything the strategy lab holds: the six built-in rules and every rule read
from a paper, a GitHub file or an uploaded document. It adds no second engine; it files, scores and gates.

- **Families.** Each rule is filed under a family (dual momentum, 52-week high, 10-month average and trend, momentum,
  quality, value, low volatility, betting against beta, quality minus junk, sector rotation, post-earnings drift,
  mean reversion, insider buying), with the research it goes back to. A rule nobody filed is placed by the first
  figure it reads.
- **Scorecard.** From Buddy's own trades (the replay on past prices, and live paper trades, kept apart): return a
  year, Sharpe ratio, worst fall, trades a year, days held, costs taken off, and the average trade against the
  market in rising markets, falling markets and the latest stretch. The "book" is the fixed stake times the most
  trades held at once; the figures compare rules with each other and are not what an account would have earned.
- **Gate.** Passed on past prices = at least 30 past trades, beat the market after costs, also in the latest 30% of
  the period, worst fall no deeper than 35%, positive Sharpe ratio. "Proven live" needs 30 live closed trades that
  beat the market. A rule that reads company figures has no history and can only be judged live.
- **Discover.** `discover()` reads a public-domain (CC0) directory of strategy papers and queues its arXiv papers
  from the sections daily prices can test. Intraday, options, crypto and AI-agent sections are left out, and the
  directory's reported returns are not kept. A paper becomes a rule only when the owner picks it from the queue.
- **Kept rules.** Daily prices only; nothing copied without a licence; downloaded code is never run; published
  returns are never trusted; costs come off every result. Known gap: the replay uses today's list of companies, not
  each year's, so the past looks better than it was. LEAN and vectorbt are not used.
- `GET/POST /api/intel/strategies`; shown as the Strategies tab on the Intelligence page.

## Honest testing and more daily-price figures

Adapted, as method, from published practice (including the Machine Learning for Trading repository, MIT); no outside
code is run.

- **A gap between learning and testing** (`train_model.py`). A trade still open when the test period began shares days
  with it, so it is left out of learning. The result reports how many rows were left out.
- **Walk-forward** (`train_model.py`). With 200 or more rows the model is also tested over four later periods in turn,
  each time learning only from what came before. The calls it liked must hold up in three of them.
- **The luck test** (`stratrepo.not_luck`). The more rules are tried, the better the best looks by chance. Each rule's
  monthly Sharpe ratio is judged against what the luckiest of that many rules would show (the deflated Sharpe ratio).
  A rule passes on past prices only at 95% or more.
- **Twelve more figures** (`stratlab._more`), available to rules and to the model: `ret_1`, `ret_12_1`, `above_low`,
  `months_up`, `vol_20`, `vol_change`, `range_20`, `band_width`, `up_days`, `skew_63`, `fall_63`, `traded`.
  Trades recorded before this change do not carry them; run the replay again so the history does.

## Product, category and brand (the price book)

`productid.py` reads a deal headline into the product it is about ("baseus s2"), its brand and its category
("security camera"). Rules only; the same headline always gives the same answer. Before this, a price was filed under
the whole headline, store and price included, so nothing ever had a history and every deal looked like the lowest.

- `observations` and `deals` carry `product`, `brand` and `category`. Older rows are filed the first time the price
  book is read (`pricebook.identify`). A multi-pack is its own product ("x2"), never compared with a single one.
- History, the lowest price and the chart look a product up by its filed name, so a price watch ("sony wh-1000xm6")
  and deals from different sources meet in one history.
- The Deals page calls a price the lowest only when the product has been seen on three different days, says "first
  time" for a single sighting, and shows the usual deal price for the brand and category once there are five deals.
- It is a best reading, not a catalogue: an unknown brand is taken from a capitalised first word, and a headline with
  no model code is filed under its first telling words.
- **Travel is filed the same way.** A flight: product = the route, `place` = the destination, brand = the airline,
  category = the cabin (economy, premium economy, business, first, or "not stated": never guessed). A hotel: product
  = the hotel or place searched, `place` = the place, brand = the chain. A fare is compared only within its cabin.
  Searched fares carry the cabin from the travel profile; emailed fares take the airline and cabin from the headline.
- **The engines read three levels** (`pricebook.levels`, `factors._levels`). Own: the product's own prices (a flight:
  the route in its cabin). Peers: the same brand in the same category (a flight: the airline in that cabin; a hotel:
  the chain). Group: the whole category (a flight: every route to the destination in that cabin; a hotel: the place).
  Each buy-or-wait call saves `f_own_days`, `f_vs_own_low`, `f_vs_own_usual`, `f_peers_dip`, `f_group_dip` and the
  product counts, so the trained models can learn from them. Prices of different products are never averaged; what
  is compared is how far each product's lowest price sat below its own usual price.
- **A product never seen before** is judged by its peers: "this brand's products of this kind have dipped 20% below
  their usual price" is a reason in the advice, with its own learned weight, when three or more such products exist.

## Investment Intelligence: one engine, one home

The strategy work is one named engine under Buddy Intelligence. It adds no database, scheduler or learning system: it
runs on the price book, the daily loop and the shared learning core.

- **On Predictions** it is the fourth engine ("Investment strategies", model domain `lab`): will a rule's next paper
  trade make money after costs.
- **Intelligence → Strategies is its home**, in six sections: Discover (research queue, paper list, GitHub, documents),
  Library (every rule by family, with approve, pause and remove), Test (the replay, what a rule must clear,
  yardsticks, stock lists), Rank (`rank`: proven live, then passed, then by the test against the market), Paper
  trading (totals and a link to every trade), Monitor (`stratrepo.watch`).
- **Monitor** compares a rule's live record with its test once it has 10 live closed trades: holding up, weaker than
  its test (ahead of the market but under half its tested margin), failing live (losing to the market), or too
  early. Buddy shows this and does not pause a rule by itself.
- **Money → Invest → Trading** keeps the trades, the combined figures, the news desk and the data panel, and links
  to the engine. The rule table, uploads and the research queue no longer appear there.

## The daily strategy pass (`stratauto.py`)

Once a day, after 9 am, Investment Intelligence works by itself:

- **Read.** The newest three items in the research queue are read into rules. Each new rule gets a second reading by
  the model set for another job (`stratrules.critique`): does the rule match the document, what differs, what is
  missing. It is shown beside the rule as an opinion and changes neither the rule nor its test. Uploads and page
  links get the same second reading.
- **Test.** The new rules are replayed on past prices at once, without being switched on (`replay(..., proposed=True)`).
- **Keep or remove.** A rule that passed stays proposed, waiting for the owner. A rule that reads company figures has
  no history and is kept untested. A rule that failed, or made too few trades to judge, is removed, and its paper in
  the queue says why.
- **Pause.** A rule the owner added that has lost to the market over 20 or more live closed trades is paused. The six
  built-in rules have no pause, so a failing one is reported and left running.
- **Tell.** One message, only when something passed, was kept untested, or was paused or is failing.

It never approves a rule and never starts one trading.

## Daily prices are kept (`pricestore.py`)

Daily stock prices used to be downloaded each time and thrown away; a replay fetched about five years of them on
every run. They are now kept in the price book (`daily_prices`: symbol, day, source, open, high, low, close, volume,
when fetched).

- The lab asks `pricestore.fetch`, not the provider. It downloads only what the store lacks (the days since the last
  one held, or the whole window for a symbol never seen), keeps it, and returns the bars from the store.
- A provider re-states past prices after a share split. When a newly fetched close for a day already held differs by
  more than 0.5%, that symbol's whole history is fetched again and replaced, and the change is written to
  `price_revisions`. The newest day held is never compared, since it may have been fetched before the close.
- Prices from different sources are kept apart and never mixed in one series. If the store fails, the provider's
  answer is used directly, as before.
- Not covered: companies that stopped trading before Buddy first asked, and which companies were in an index on a
  past date. There is one source at a time and no second source to cross-check against yet.
- Shown in Intelligence → Strategies → Test ("Daily prices kept on this server").

## Price cross-check (`pricecheck.py`)

Off until `BUDDY_TIINGO_KEY` is set. Then, once a day, 20 kept symbols (the ones checked longest ago first) are
compared against a second, independent source over the last 30 days of closes. A day more than 1% apart is a
disagreement, recorded with both prices and told to the owner. The newest day is left out, and a symbol whose shares
split inside the window is not compared, since one source adjusts for it and the other does not. A disagreement
means one of the two is wrong or they treat a company event differently: nothing is corrected automatically. Only
ticker symbols and dates are sent. Shown in Intelligence → Strategies → Test.
