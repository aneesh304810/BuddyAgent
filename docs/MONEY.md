# Money: where Buddy's numbers come from

| Source | What it gives | Cost | Where it works |
|---|---|---|---|
| **Bank feed (SimpleFIN Bridge)** — recommended | Every transaction and every balance, straight from the banks and card issuers, read-only | About $15 a year (or $1.50 a month) to SimpleFIN | Most US banks and cards (Chase, Amex, Citi, Capital One, Bank of America, credit unions...) |
| **Bank and card emails** | Purchases from alert emails, plus **bills and due dates** from statement emails | Nothing extra: read during email sorting | Anywhere, including banks in Peru, Brazil and India that email alerts |
| **By voice** | Cash and anything else | Nothing | Anywhere |

With the bank feed on, Buddy takes transactions only from the feed (exact, nothing missed)
and uses emails just for bills, so nothing is counted twice. Card payments and transfers
between your own accounts are skipped automatically.

## Connect the bank feed (about 10 minutes, all on your phone)

1. In Chrome, open **bridge.simplefin.org**, create an account and subscribe.
2. Tap **Connect a bank** (or **New connection**) and log in to each bank or card. This
   happens on SimpleFIN's site through MX; Buddy never sees your bank passwords.
3. Tap **New app connection** (or **Get setup token**), name it `Buddy`, and copy the
   **setup token** (a long string of letters).
4. On the server (`ssh root@buddy.<your-tailnet>.ts.net`):
   ```
   sudo bash /opt/buddy/deploy/bank.sh
   ```
   Paste the token when asked (it's hidden). It works once: it's exchanged for a read-only
   access link that stays on your server.
5. Ask Buddy: *"What are my account balances?"*, *"How much did I spend this month?"*

Buddy fetches the last 30 days straight away, then every 6 hours (SimpleFIN allows up to 24
updates a day). Each new shop name is sorted into a category once by the cheap model and
remembered, a fraction of a cent. Add the subscription to your cost report:
`sudo bash /opt/buddy/deploy/set.sh BUDDY_FIXED_COSTS` → `SimpleFIN=1.25`.

To disconnect: delete the app connection on bridge.simplefin.org (that revokes the link),
or `sudo bash /opt/buddy/deploy/set.sh BUDDY_SIMPLEFIN_URL` and press Enter on an empty value.

## Other countries and other services

| Service | Why it isn't the default |
|---|---|
| **Plaid**, **Teller**, **Yodlee**, **MX** directly | Built for companies: developer accounts, approval and compliance reviews before real bank data. SimpleFIN resells MX to individuals. |
| **Belvo**, **Pluggy** (Brazil, Mexico, Colombia), **Prometeo** (Peru, LatAm) | Business-only contracts today. Bank emails and phone notifications cover these banks meanwhile. |
| **India's Account Aggregator** network (Setu, Finvu, OneMoney) | Licensed for regulated businesses; personal API access isn't offered. Use bank alert emails or SMS (via phone notifications). |
| **Monarch**, **Copilot**, **YNAB** | Apps with their own bank links; no official API for your own data. |

If one of these opens personal access, Buddy's agent builder can connect it.

## Card credits: reminders and deals

- Reminders before a credit with money left runs out: 60, 30, 14, 7 and 2 days ahead (monthly credits such as Uber
  Cash: 7 and 2 days, since they are always within a month of their end).
- About a month before a credit ends, Buddy searches in the background (a few cents each, up to 3 a day, once per
  credit period) for sales running now at that store and items that the credit covers, starting with your wish list.
  The 1-month reminder carries what it found, with links. Buddy never buys anything.
- Dining credits (Resy and similar): named restaurants near you that take the credit, matched to your food tastes,
  with a typical price and a booking link (needs Share my location, or Buddy says so in the reminder). Hotel credits
  (Fine Hotels + Resorts, The Hotel Collection): named hotels at your watched trip destinations first, else an easy
  drive away. Ride, airline, streaming and similar credits get reminders only.
- "Find deals for my Saks credit" runs the search right away. Turn the background search off with `BUDDY_CREDIT_DEALS=0`.

## Market school: learn investing from real data

Every weekday from 8 AM New York time Buddy sends a short lesson: what moved (the big indexes, sectors,
commodities and bonds) and the likely reason, one idea explained simply (picked for that day's news, or the next
one you haven't had), and a theme worth learning about. The full lesson, with tables, is in Reports. Ask any time:
"teach me today's market", "how are the themes doing?", "what are rates and inflation now?", "show me Apple's
numbers from its filings", "explain expense ratios". Turn it off with "turn off market school".

It's education, not advice: Buddy never trades and never tells you what to buy.

Free data to connect (about 5 minutes, each optional; only series names and tickers go out):

1. **FRED** (rates, Treasury yields, inflation, jobs): make a free account at fred.stlouisfed.org, open
   *My Account → API Keys*, request a key, then `sudo bash /opt/buddy/deploy/set.sh BUDDY_FRED_KEY`.
2. **EIA** (oil and natural gas prices): register at eia.gov/opendata (the key arrives by email), then
   `sudo bash /opt/buddy/deploy/set.sh BUDDY_EIA_KEY`.
3. **SEC EDGAR** (company filings): no account, but SEC asks every app to name a contact email in its requests.
   Pick one (it goes only to SEC): `sudo bash /opt/buddy/deploy/set.sh BUDDY_SEC_CONTACT`.

Without them the lesson still works from prices alone. Commodities (gold, silver, oil, gas, copper, farm goods,
through their US funds) are also in the daily trade-idea scan now, and the scan covers US stocks and crypto;
say "scan Indian stocks too" to add India back.

## Shopping: your way of buying

Buddy learns how you research and decide, separately for electronics, home, travel and clothing (most people
decide each differently), and uses it whenever you ask about a purchase.

- **The interview:** say "let's do my shopping interview". Buddy asks a couple of questions at a time by voice
  (what you check first, whose reviews you trust, brands you like and avoid, how long you'll wait for a deal and
  what makes you buy now, must-haves, budgets, regrets) and you can stop any time and carry on later. Say "show my
  shopping profile" to hear it, or just mention a preference in passing ("I never buy Samsung appliances").
- **Your purchases:** order and return emails are read every hour by text rules (no AI) into a lasting purchase
  history (email itself is only kept 30 days): shop, item, price, whether it was on sale. With the bank feed's
  shopping and travel charges, Buddy learns your usual shops, typical spend, how often you buy on sale and what
  you return. Tell Buddy "I'm thinking of buying a new vacuum" and it notes the day, so when the order arrives it
  learns how long you take to decide.

- **Research your way:** say "I'm thinking of buying new headphones" or "which vacuum should I get?". Buddy hands
  it to the research agent with your profile: your trusted sources first, your must-haves as filters, brands you
  avoid left out, your budget and stores, your past regrets and returns. In a few minutes you get "my pick for
  you" with the reasons in your own terms, the best 3 in a table, why not the others, and buy now or wait with a
  target price (the same cost as a research job: you're asked first if it's over your approval amount). Your
  profile is never put into web searches.

- **Buy now or wait:** ask "should I buy this now?" (or it comes after a "thinking of buying" report). Worked out
  in code from the prices Buddy has seen, the US sales calendar for that kind of thing (Black Friday and Cyber
  Monday, Prime Day and Prime Big Deal Days, holiday weekends for appliances and mattresses, back to school for
  laptops, end-of-season clearance for clothes, the Super Bowl for TVs), a newer model coming, and your own
  patience and target discount. You get the call, the chance of a better price, the price worth waiting for and
  until when. The sale discounts are rough rules of thumb, so every call is kept and checked later against the
  prices Buddy saw (a price watch) or what you paid (the order email): ask "how good are your buy-or-wait calls?"
  for the track record. Flights and hotels stay with the travel tools.

- **After you buy:** two to six weeks after an order (a week for clothes; not for small things under $40,
  trips, or anything you returned), Buddy asks once, in the daytime and at most one a day: "how's the new vacuum
  working out?" Say happy, okay or regret it and why. A regret goes into your profile so the next research
  watches for the same problem, and if the order is recent Buddy reminds you to check the return window.
- **Price history:** every Amazon price Buddy looks up is kept, so its history for the item grows. With a Keepa
  key (`sudo bash /opt/buddy/deploy/set.sh BUDDY_KEEPA_KEY`, a paid plan at keepa.com) buy-or-wait also uses
  Amazon's 90-day, 180-day and yearly averages. ShopSavvy (other stores) isn't connected yet: its documentation
  wasn't reachable when this was built, so it waits until it can be built against the real API.

- **Daily deals collector:** every morning from 9 Buddy reads deal sites' public feeds (Slickdeals front page and
  popular, DealNews, r/buildapcsales, r/deals, r/frugalmalefashion; add any other: "add the deal feed
  https://..."), checking each site's robots rules first and skipping what they disallow. It also checks today's
  price for everything you want (things you're thinking of buying, price watches, your wish list) at Google
  Shopping, Best Buy and eBay, and keeps every price, so real history builds up for buy-or-wait. A deal that
  names something you want, a new low (5% under the best seen) or a real sale is told that morning (up to three);
  the rest that fit your kinds and brands (never brands you avoid) are in Reports as "Deals for you". Ask "any
  deals today?", "find deals on 1440p monitors", or "how did the deal feeds do?". Store pages are never scraped.
  Free keys that add sources (about 10 minutes):
  - **Best Buy:** developer.bestbuy.com → get a key → `sudo bash /opt/buddy/deploy/set.sh BUDDY_BESTBUY_KEY`
  - **eBay:** developer.ebay.com → create a production keyset → `sudo bash /opt/buddy/deploy/set.sh
    BUDDY_EBAY_CLIENT_ID` and `... BUDDY_EBAY_CLIENT_SECRET`
  Google Shopping uses the Serper key Buddy already has (about $0.001 a check).

- **The price book and learned patterns:** every price Buddy sees (products, flights, hotels) and every deal goes
  into a separate database file next to Buddy's, `pricebook.duckdb` (DuckDB, built for history queries; not
  encrypted, as it holds market data only, though the routes, dates and items show what was searched). Each
  night at 02:30 Buddy hands it to the sandbox, which learns, offline: real sale discounts per kind of product
  and per sale and year, the odds of a 10% drop within 14, 30 and 60 days, price against months since launch
  (new launches hold their price; replaced models drop), flight fares against days before departure per route
  (the cheapest booking window), hotel weekend and month patterns, and how Buddy's own calls did. The patterns are
  kept for good and merged month by month; raw prices and deals are deleted after 90 days, but only once they've
  been learned from. Buy-or-wait prefers what was learned (and says from how many items) over its rules of
  thumb. Ask "what patterns have you learned?", "learn the price patterns now", "show the price history of X",
  or "export the price book" (CSV or Parquet, for your own analysis). Needs the sandbox (deploy/sandbox.sh);
  without it raw prices are simply kept until it's set up.

- **The deals inbox and the Deals page:** make a separate free email account just for deals (Gmail is fine: turn
  on 2-step verification and create an app password), subscribe it to deal alerts and newsletters (Slickdeals deal
  alerts for your keywords, Best Buy, Target, Walmart, Costco and Amazon newsletters, DealNews, Brad's Deals,
  airline fare sales, hotel and booking-site sales), then
  `sudo bash /opt/buddy/deploy/set.sh BUDDY_DEALS_EMAIL_ADDRESS` and `... BUDDY_DEALS_EMAIL_APP_PASSWORD`. Every
  night at 1 AM Buddy reads the new emails there (read-only, code only: no AI cost), pulls out each deal (product,
  flight or hotel; price, regular price, store, link, end date) and keeps it in the price book, where the nightly
  pattern learning uses it. See them all on the **Deals page** (☰ → Deals): products, flights and hotels, search,
  "lowest Buddy has seen" marks, % off, and each item's price history chart; tap "Open deal" to open it in your
  browser. Buddy never opens store pages itself. "Read the deals inbox now" runs it on demand.

- **Why deals happen, and an alert when the next one comes:** every deal Buddy reads gets a reason (a holiday
  sale, clearance or end of season, a new model, a flash deal, a coupon, a member sale, the store's weekly sale,
  or just a price drop). The nightly learning finds the rhythms: each product's deal dates and prices (kept for
  good, so "on deal about every 45 days, usually ~$299" survives the 90-day deletion), and per store and kind how
  often deals come, on which weekday and how deep, and what usually causes them. Ask "when will the Sony XM6 go
  on sale again?" for the next likely deals (its own rhythm, the coming sales with the discounts really seen, its
  life cycle), each with a date window, expected price and why; "why do TVs go on sale?" for the pattern; "watch
  for it" (or "alert me under $280") and Buddy tells you in the morning when a matching deal or price shows up in
  the feeds, the deals inbox or the daily price checks. Every Monday: "deals to watch for this month".

- **Deal notifications from your phone's apps:** with Notification access on in the Buddy app, price-drop and
  deal notifications from shopping and travel apps (Amazon, Best Buy, Target, Walmart, Costco, eBay, Home Depot,
  Lowe's, Menards, Ace, Harbor Freight, Tractor Supply, Northern Tool, Google Shopping, Slickdeals, Hopper, Google
  Flights, Kayak, airlines, Booking, Expedia, hotel chains...) are read every hour by text rules into the price
  book, like the deals inbox. Order, delivery, payment and account notifications are skipped, and other apps'
  notifications are never read for this. Turn on price-drop and deal notifications in each app (and "track
  price" on the items you care about). Tools and building materials count as home: their sales (Spring Black
  Friday in April, Father's Day tool deals, Memorial Day, Labor Day, Black Friday) are on the calendar, and
  r/ToolDeals is among the feeds.

- **Deal playbooks and predictions up front:** the nightly learning also builds a playbook per company, each
  store and each brand: how often it runs deals, on which weekday, how deep, why (weekly sale, coupon, holiday,
  clearance...), its busiest months and what it discounts. Ask "how does Best Buy do deals?" or "when does Sony
  discount?". "What deals do you predict?" (or the Deals page's 🔮 Predicted tab) lists every predicted deal for
  the next 45 days, soonest first: products with a deal rhythm (seen on deal at least twice), companies' next
  deal days (seen on at least three days) and the coming sales, each with its reason and confidence.

- **The catalog (followed without asking):** every product seen on deal on two or more days in the last 60 joins
  a catalog (up to 150, `BUDDY_CATALOG_SIZE`); about 40 of them a day (`BUDDY_CATALOG_DAILY`, roughly $1.20 a month
  of Google Shopping checks) get a price check in rotation, so each has a steady price history between deals.
  Flight routes that keep appearing in fare emails and notifications (up to 10) are followed daily for this month
  and the next two through Travelpayouts (free; needs `BUDDY_TRAVELPAYOUTS_TOKEN`); you may get "cheap fare"
  messages for them: say "stop watching JFK-LIM" to drop one. Hotels have no free daily source: their patterns
  come from hotel emails, app notifications and your own hotel searches.

All of it stays on your server; only Claude sees it. Buddy still never buys or pays.
