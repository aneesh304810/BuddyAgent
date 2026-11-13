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
