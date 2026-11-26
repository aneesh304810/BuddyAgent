# Search: better sources, cleaner data, lower cost

## Buddy Search: one search over everything

One search over many providers, so Buddy depends on no single search company. Ask in plain words; Buddy works out
where to look, asks the right providers at the same time, merges what they found and shows where each result came
from. In the app: **Discover → Search**. In conversation: ask Buddy to search, and it uses the same engine
(`search_everywhere`).

### What it searches

| Engine | Providers (in order) | Needs |
|---|---|---|
| Web | Brave Search and Tavily together; Google (Serper) when neither answers | Brave and/or Tavily key; Serper key for the fallback |
| Places | Google Places and OpenStreetMap together (Google Maps through Serper without a Places key) | Places key (optional); OpenStreetMap needs none |
| Products and prices | Google Shopping (Serper), Best Buy, eBay, your own price book, and Brave for the pages around them | the store keys you already set |
| News | Brave News and your connected news services | Brave key; news services as set up |
| Research | Tavily, Europe PMC, OpenAlex, arXiv, and your paper library | Tavily key; the others need none |
| Your own things | Your documents, what Buddy remembers, email, messages and calendar from your connected apps, your saved papers | nothing; never leaves your server |

Buddy decides from the words: "Italian restaurant in Hoboken" is Places in Hoboken; "cheapest iPhone 16" is
Products; "latest market news" is News and Web; "research on longevity" is Research and Web; "the Etihad booking in
my email" is Your own things. You can also pick the engine above the results.

### How results are put together

- **Together:** the chosen providers are asked at the same time; one that is slow (15 s), failing or out of today's
  allowance is skipped and its engine's fallback used.
- **One form:** every result has its title, link, snippet, date, the provider that found it, and for places the
  address, rating, hours and phone, for products the price and store.
- **Merged:** the same page (however it was linked: tracking parameters, `www.` and fragments ignored), the same
  place (its name and position within about 100 m, or its name and street) or the same paper (its PubMed number,
  DOI or arXiv number) is one result. Each source fills what the others lacked: Google's rating beside
  OpenStreetMap's opening hours.
- **Ranked:** reciprocal rank fusion across the providers, raised for each extra source that found it ("found by
  2"), for official sources (government, universities, the WHO, NIH, CDC, major journals, Reuters, AP) and for news
  from the last two days. Places: also by their reviews, the rating pulled towards an ordinary 4.2 when few
  people gave it, so a 4.6 from 5,000 people ranks above a 5.0 from 4 (a place without a rating counts as
  ordinary); a very high rating from fewer than 30 reviews is marked "few reviews". Products: offers with a price
  first, cheapest first.
- **Short answer** (optional): two to four sentences with numbered sources, from the top results. For web,
  news and research, any model in Maestro (public text only, checked by the Privacy Gateway; the job is
  "Summarise search results"); with your own things, Claude only.

### Reviews

Every place and product result has a **Reviews** button. Reviews are fetched only when you tap it, so a search
doesn't spend a review lookup on every result. In conversation, ask "what do people say about …" and Buddy uses
the same check (`check_reviews`) for one or two options you are weighing.

- **Warning signs** (worked out by code, not a model): a very high rating (4.7 or more) from fewer than 30
  reviews; sources a star or more apart (Google's rating, Best Buy's, and ratings that review sites state in
  their snippets, such as "4.5/5" or "8/10"); the same complaint from two or more sources (for places: fees and
  surprise charges, rude or slow service, cleanliness, people falling ill, scams, noise, long waits; for products:
  breaking or stopping working, returns and warranty trouble, battery, overheating, customer service, quality
  control).
- **Good, not so good, and catches:** up to four of each, every point with the numbers of the snippets it rests
  on, so you can check it. A model in Maestro writes it ("Sum up reviews"), from public snippets only; a snippet
  that reads like someone's health is left out, and if anything personal is in them they go to Claude only.
- **Google's own reviews** (with a Google Places key, for a Google Places result): up to five, word for word, with
  each author and "Reviews from Google", and a link to all of them on Google Maps. As Google's terms ask, they are
  not cached, not kept, not summarised by any model and not counted for warning signs; each tap uses one of the
  day's 25 Google Places lookups.
- **What people say elsewhere:** Reddit threads, review sites (for places TripAdvisor, Yelp, Time Out, Eater, The
  Infatuation, Michelin, OpenTable and others; for products Wirecutter, RTINGS, Consumer Reports, CNET, The Verge,
  PCMag and others) and complaint threads, each as the snippet a search shows with its link. Through Serper (three
  of the day's searches) or, without Serper, Brave Search (two); Reddit's own API when it is connected. For a
  product, Best Buy's customer rating too (with the Best Buy key).
- **Your own verdicts:** if you bought something with a similar name and told Buddy how it went (happy, okay or
  regret), it is shown. It is never sent to any model.

No scraping: Buddy reads only official APIs and the snippets search results show, never a review site's own pages,
and Yelp's API is not used. Only the name, and for a place an area name, goes to the search providers. What they
found is held for an hour, so a second tap costs nothing, and then dropped.

### Costs and limits

Each paid provider has a daily allowance that spreads its free monthly use over the month (Brave 40, Tavily 30,
Google Places 25, Serper 60 searches a day); past it, that provider is skipped until tomorrow. Change one with
`sudo bash /opt/buddy/deploy/set.sh BUDDY_SEARCH_CAP_BRAVE` (or `_TAVILY`, `_GOOGLE_PLACES`, `_SERPER`). The
Search page's **Providers** table shows each one: ready or needing a key, today's use against its allowance, the
week's searches, failures and speed, and what it may keep.

### Keys

Sign up yourself (Buddy can't): Brave Search API at brave.com/search/api, Tavily at tavily.com. Then, on the
server:

```
sudo bash /opt/buddy/deploy/set.sh BUDDY_BRAVE_KEY
sudo bash /opt/buddy/deploy/set.sh BUDDY_TAVILY_KEY
```

OpenStreetMap needs no key; Buddy follows its usage policy (at most one Nominatim request a second, every answer
cached for a week, an identifying user agent) and shows its attribution.

### Privacy and what is kept

- Providers get your search words and, for places, an area name (a town or the neighbourhood the phone last
  shared), never your position. A search of your own things never leaves your server.
- What is kept follows each provider's terms: a link and title for every web result; Google Places: only the place
  ID (Google's terms); Brave: links only, unless your plan includes storage rights
  (`sudo bash /opt/buddy/deploy/set.sh BUDDY_BRAVE_STORAGE_RIGHTS`, value 1); OpenStreetMap: kept, with its
  attribution (ODbL); papers: kept, and also in your paper library; your own things: never copied. Anything else
  is held in a short cache (Brave and Serper an hour, Tavily a day, news 30 minutes) and then dropped; Google
  Places answers are not cached at all, as Google's terms ask. One provider's details are never kept under
  another's terms.
- Your searches are kept until you delete them (one at a time, or the history, the cache and everything kept, on
  the Search page).

### Not in this version

Exa (semantic search) was left out for now; scheduled searches, monitoring and reports by search agents are the
next phase.

## The sources Buddy Search and the agents draw on


Buddy picks the cheapest source that gives a good answer. Compact sources return clean
results (titles, snippets, scores, links) instead of whole pages, so Claude reads a few
hundred tokens instead of tens of thousands: better data for less money.

| Source | Gives | Cost per use | Setup |
|---|---|---|---|
| **Google Trends** | What's being searched right now, per country, with the news behind it | Free | None: always on |
| **Reddit** | Real people's opinions, reviews and tips; top posts and comments of any subreddit | Free (Reddit's personal-use API) | 2 minutes, below |
| **Google results, News and Maps places** (via Serper) | Google's own ranking, answer box, top stories; places with ratings, prices and hours | About $0.001 | 2 minutes, below |
| **News services** (NewsCatcher, NewsData.io, Perigon, Marketaux, then GDELT) | Headlines on a topic when Google News can't answer | Free within each service's monthly allowance | A key for any of them; GDELT needs none |
| Claude's web search | Any page on the web | About $0.01 plus the results it reads | None |
| Reading a whole page | Everything on one page | Its length in tokens (1–3 cents) | None |

Results are cached (Google 15 minutes, Reddit and trends 30 minutes), so asking twice
doesn't cost twice. The research and deals agents use these sources too, for example to
check what real buyers say on Reddit before recommending a product.

## Places that fit your taste
Restaurants, cafes, bars and nightlife, spas, hotels, shops and things to do: Buddy searches
Google Maps (ratings, number of reviews, price level, opening hours, address; needs the
Serper key below) and picks the three that suit **you**, each with a line on why.

Every recommendation is checked across channels:
- **Google Maps**: rating, number of reviews, price level. Places are ranked so a 4.6 from 5,000
  people beats a 5.0 from 4.
- **Reddit**: threads that mention the place (with Reddit connected), for the real vibe and tips
  ("go before 1 pm", "overpriced since the new owners").
- **Review sites** through Google: TripAdvisor, Yelp, Time Out, Eater, The Infatuation, Michelin,
  Booking.com, Zomato, OpenTable, TheFork and more.

Only the best five are checked, with short snippets, so a search costs about a cent. For a big
decision (a hotel for a week, an expensive dinner), ask for a deal hunt instead: the research
agent reads full reviews and compares in depth.

**What places don't advertise.** For each category Buddy has a checklist of usual catches and
looks for them in reviews, Reddit and complaint threads, then tells you plainly:
- restaurants: service charge or cover, minimum spend, cash only, tourist menus and prices
- nightlife: cover charge, minimum spend or bottle service, dress code and door policy, drink
  prices, the real crowd, safety late at night
- spas: expected tips, add-ons pushed during the treatment, table time shorter than advertised
- hotels: resort, city and cleaning fees, parking and breakfast extra, noise, renovations,
  rooms unlike the photos, deposits
- a suspiciously perfect rating from very few reviews

Deal hunts for flights and hotels compare the **true total** (bags, seats, booking and resort
fees, taxes at checkout, currency-conversion markups) and the **cancellation rules** (refundable
or not, until when, cash or credit, booking direct versus third-party sites), and price watches
report the biggest catch with every drop.

**Near me**: turn on *Settings → Share my location* in the Android app and say "near me".

It knows your taste from:
- **what you say**: *"I love rooftop bars"*, *"no chains"*, *"I'm vegetarian"*, *"boutique hotels,
  mid-range"*, *"that spa was amazing"*, saved as you talk (ask *"what's my taste in food?"*)
- **where you go**: places in your life log (Maps Timeline, trips, stays)
- **where you spend**: restaurants and shops from your money records

Try: *"Find me a quiet cocktail bar in Barranco tonight"*, *"A good deep tissue massage near
Brickell"*, *"Boutique hotel in Cusco under 150 dollars"*, *"Dinner in Kochi, something like the
places I like"*. Say where you are if Buddy doesn't know.

## How freely Buddy searches

| `BUDDY_SEARCH_LEVEL` | Chat: searches / pages per answer | Quick job | Deep job | For |
|---|---|---|---|---|
| `thrifty` (default) | 3 / 0 | 5 / 6 | 15 / 20 | Lowest cost |
| `generous` | 6 / 4 | 10 / 12 | 30 / 40 | Thorough answers, a few cents more |
| `max` | 10 / 8 | 20 / 25 | 50 / 60 | Most complete answers; quality over cost |

```
sudo bash /opt/buddy/deploy/set.sh BUDDY_SEARCH_LEVEL      # type: generous  (or max)
```
Your monthly budget, per-job cap ($5) and approvals for anything over 50 cents still apply.

## Connect Reddit (free)
1. In Chrome on your phone, open **reddit.com/prefs/apps** (log in; tap ⋮ → Desktop site).
2. Tap **create another app**: name `Buddy`, choose **script**, redirect URI `http://localhost:8080`, **create app**.
3. The **client ID** is the short code under the app's name; the **secret** is next to "secret".
4. On the server:
   ```
   sudo bash /opt/buddy/deploy/set.sh BUDDY_REDDIT_CLIENT_ID
   sudo bash /opt/buddy/deploy/set.sh BUDDY_REDDIT_SECRET
   sudo bash /opt/buddy/deploy/set.sh BUDDY_REDDIT_USERNAME   # your Reddit username (Reddit asks apps to name their owner)
   ```
Try: *"What does Reddit say about Cusco in December?"*, *"Top posts in r/personalfinance this week"*.

## Connect Google results (Serper)
1. Open **serper.dev**, sign up (free starting credits), and copy your **API key**.
2. `sudo bash /opt/buddy/deploy/set.sh BUDDY_SERPER_KEY`

Try: *"Google the best ceviche in Lima"*, *"Any news about Copa Libertadores?"*.

## When Google News can't answer (`newsfeed.py`)
A news search (`google_search` with `news=true`, and the news part of a watched topic) goes to Google News
through Serper first. When Serper is not set up, fails, or says it is out of credits, the same call asks the
news services that have a key, in this order, and the first with headlines answers:

| Order | Service | Key |
|---|---|---|
| 1 | NewsCatcher (its News API, `api.newscatcherapi.com/news-api`; a key for the older v3 API is refused) | `BUDDY_NEWSCATCHER_KEY` |
| 2 | NewsData.io | `BUDDY_NEWSDATA_KEY` |
| 3 | Perigon | `BUDDY_PERIGON_KEY` |
| 4 | Marketaux | `BUDDY_MARKETAUX_KEY` |
| 5 | GDELT | none (one search every five seconds; refused again and again, it is left alone longer each time: 15 minutes, then an hour, 4 hours, up to a day) |

- The answer says which service it came from (`answered_by`) and why Serper and any earlier service didn't
  answer (`fallback_reason`, `also_failed`). A watched topic lists the same reasons under "Not reached".
- A watched topic also falls back when Google News answers but has nothing on the topic.
- A service that says it is out of credits, or refuses its key, is left out for an hour, Serper included.
- Each service is sent the search words only.
- A plain web search is not turned into a news search: when Serper fails, Buddy is told to use Claude's own
  web search. Web search failing three times pauses web search for half an hour, not news.
- **Allowances.** NewsData.io, Perigon and NewsCatcher are the same services the news fact check uses
  (docs/INTELLIGENCE.md), and their free plans are small. The fact check keeps its own monthly limit; news
  search has none beyond Buddy Connect's per-minute limits, so a month with Serper out of credits uses them
  faster. When one runs out, the next is asked.

## X (Twitter)
Not included: X's official API, including trends, is paid (its entry plans cost hundreds of
dollars a month), and reading X without the API breaks its terms. Two options if you want
it: xAI's Grok API, which can search X, or the X API itself. Ask Buddy to build an agent
for either (*"build an agent for X trends with my xAI key"*); Google Trends and Reddit
cover most of what's trending meanwhile.
