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
  `technologies`, `technology?id=`, `convergences`, `organisation?name=`, `search?q=`.
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

## Which AI model does each job (bip_models.py)

Intelligence has an **AI models** tab. Most of the platform uses no AI: tagging, stages, convergences, the
patterns and the practice scoring are code. The jobs that do use a model each have their own setting.

| Job | What it does | Used today |
|---|---|---|
| Label headlines | good, bad or neutral; up to 120 a night | yes |
| Write up a convergence | what two technologies add together; about 5 a night | yes |
| Find the companies in a record | names the companies a headline is about | not yet |
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
