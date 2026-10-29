# Search: better sources, cleaner data, lower cost

Buddy picks the cheapest source that gives a good answer. Compact sources return clean
results (titles, snippets, scores, links) instead of whole pages, so Claude reads a few
hundred tokens instead of tens of thousands: better data for less money.

| Source | Gives | Cost per use | Setup |
|---|---|---|---|
| **Google Trends** | What's being searched right now, per country, with the news behind it | Free | None: always on |
| **Reddit** | Real people's opinions, reviews and tips; top posts and comments of any subreddit | Free (Reddit's personal-use API) | 2 minutes, below |
| **Google results, News and Maps places** (via Serper) | Google's own ranking, answer box, top stories; places with ratings, prices and hours | About $0.001 | 2 minutes, below |
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
   ```
Try: *"What does Reddit say about Cusco in December?"*, *"Top posts in r/personalfinance this week"*.

## Connect Google results (Serper)
1. Open **serper.dev**, sign up (free starting credits), and copy your **API key**.
2. `sudo bash /opt/buddy/deploy/set.sh BUDDY_SERPER_KEY`

Try: *"Google the best ceviche in Lima"*, *"Any news about Copa Libertadores?"*.

## X (Twitter)
Not included: X's official API, including trends, is paid (its entry plans cost hundreds of
dollars a month), and reading X without the API breaks its terms. Two options if you want
it: xAI's Grok API, which can search X, or the X API itself. Ask Buddy to build an agent
for either (*"build an agent for X trends with my xAI key"*); Google Trends and Reddit
cover most of what's trending meanwhile.
