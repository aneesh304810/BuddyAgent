# Buddy Privacy and Data Retention Policy

Source: https://claude.ai/artifact/MJVGZ7pWQvi26tg1MS914P, revision 52

Nov 1, 2026 · @Aneesh

## Who this policy covers

Buddy is a personal assistant app built and run by Aneesh Nair for his own use. Aneesh is its only user and its only "consumer": no other people sign up, and no data about other customers is collected.

This policy describes what personal and financial data Buddy handles, how it is protected, who processes it, and how long it is kept.

## What data Buddy collects

Buddy collects only the owner's own data, and only what its features need.

| Data | Source | Access |
| --- | --- | --- |
| Bank and card transactions, account names and balances, income deposits, and transfers (including who they went to, and labels the owner gives them, such as rent or family support) | Plaid (Transactions and Balance products), or SimpleFIN Bridge | Read-only |
| Emails (bills, receipts, bookings) | The owner's email accounts, using an app password | Read-only |
| Calendar events | The owner's calendars and phone | Read-only |
| Phone notifications | The Buddy app on the owner's phone, with Android notification access | Read-only |
| Location (neighbourhood and city), a 60-day history of where the phone was, and the places learned from it (home, work, places the owner names) with the usual weekday leave time | The owner's phone, only when location sharing is on | The latest, for answers about where the owner is; the history, to learn home, work and usual times, to remind at the right time and place, and to hold non-urgent messages while driving |
| Voice clips and speech transcripts | The owner's phone microphone while Buddy is listening | Processed, not kept as audio |
| Voiceprint (for voice match) | Five sentences the owner reads once | Stored as numbers, no audio |
| Documents, photos, PDFs, spreadsheets, CSV and JSON files | Files the owner chooses to upload (Share to Buddy, or the paperclip in the app) | Stored encrypted; text, summaries, facts and search index kept |
| People and things mentioned in those files and in conversations | Pulled out of the owner's own data | Names, relationships and short notes |
| Preferences, choices and dates to act on | What the owner says they like, choices they make between options (such as Uber or Lyft after a price comparison), and dates found in their facts and documents | Short lines on the server; learned preferences are marked as learned |
| Subscriptions and credit card details (due and closing dates, balances, minimums, limits, APR) | Worked out from the owner's card transactions, card statement emails and statements they share, or read from Plaid (Liabilities) for banks the owner links | Card name and last 4 digits only; full card numbers are never stored |
| Money goals, take-home pay, paydays, autopay settings and card reward rates | What the owner tells Buddy (reward rates may start from published rates the owner confirms) | Used on the server to work out advice; Claude sees only the results it words |
| Travel searches, prices seen, routes the owner asks Buddy to watch, travel style and travel choices | The owner's searches; prices from Duffel, Google Flights and Hotels (SerpApi) and Travelpayouts; which offers the owner opens | Only places, dates and passenger counts are sent to these services; no names, documents or payment details |
| Credit card benefits (credits and perks, their amounts and reset dates) and a wish list of things the owner wants | What the owner tells Buddy; which credits were used is read from statement credits in the owner's own transactions | Stored on the server; Buddy never buys anything, it only suggests |
| Access log: the time, network address, tailnet sign-in name, path and result of each request to Buddy's server, including failed sign-ins and lockouts | Buddy's server, for every app, web and API request | Security monitoring and audit evidence only; never message content, request bodies or keys; stored encrypted |
| Mortgage and student loan details: balance, interest rate, next payment and due date, escrow, interest and principal paid this year, payoff date (never the property address) | Plaid (Liabilities), for the banks the owner links | Read-only; used for payment reminders and money reviews |
| Cancellation confirmations (the service, the date and the email's subject) and free-trial end dates and prices | Found in the owner's own emails | Kept on the server as proof for disputes and for trial reminders; Buddy never cancels or disputes anything itself |
| Credit scores (score, bureau, scoring model, date) and credit report changes (hard inquiries, new or closed accounts, late payments, collections, address changes, fraud alerts: the date, the lender and a few words); and, when the owner asks, a detailed credit report (each account's lender, type, opening month, limit, balance and payment record, inquiries, negative items and score factors; never names, addresses, birth date or ID numbers) | The owner's credit monitoring emails and phone notifications (such as Credit Karma, Chase Credit Journey, Amex MyCredit Guide, Experian); the screen of the owner's credit app, read only when the owner opens it and asks Buddy to read it; new cards seen at banks the owner links; and scores the owner tells Buddy | Read-only; Buddy never signs in to these services, and never freezes, disputes or applies for anything itself |
| Market watchlist, trade ideas Buddy found (setups with entry, stop and targets), and trades the owner tells Buddy they made (symbol, price, size, stop) | Buddy's daily market scan, and what the owner tells Buddy | Ideas only, never financial advice; Buddy never places, changes or cancels a trade |
| Commute lines the owner asks Buddy to watch (for example a subway line, PATH line or NJ Transit light rail), the station, and the days and times they ride; transit alerts already told | What the owner tells Buddy; the official MTA, NJ Transit and PATH alert feeds | Read-only; Buddy only reads public alerts and never books or buys anything |
| Flights the owner asks Buddy to track (flight number, date, an optional note) and their latest status (times, gates, delays, landing) | What the owner tells Buddy or a booking email the owner agrees to use; AirLabs and FlightAware flight status; the airline's own app notifications and emails about that flight (gate, new time, delay, boarding, cancellation), read on Buddy's server without AI | Read-only; Buddy never changes or books a flight |
| Upcoming plans found in the owner's emails, phone notifications and uploads (flights, hotel stays, reservations: dates, times, places, confirmation codes; bills due in notifications and uploaded statements: biller, amount, due date), lessons Buddy learns from the owner's corrections (where to look for an answer), and which reminders it has already given | Travel-looking emails, notifications and uploads, read once by Claude's quick model (never a free model); the owner's corrections in conversation | To answer questions about the owner's plans and to remind them at the right time and place (for example the evening before a flight, time to leave, at the airport). Flights found are tracked automatically; nothing is booked or changed |
| Buddy's own notes (findings it keeps after looking something up, with where each came from; finished reports), the advice it gave on decisions and how they turned out, and topics it had no source for | Buddy's conversations and tool results, read by Claude's quick model (never a free model); the owner's answers when Buddy asks how a decision went | So Buddy's knowledge grows beyond uploads, its answers are checked against what its tools returned, its advice improves from results, and it can suggest new sources the owner may want |
| Promises with a date (from what the owner says, and from emails or chats asking them to do something by a date), how often each source turned out right, and when the owner last heard from close people | The owner's conversation with Buddy; emails and notifications (read by Claude's quick model, never a free model); Buddy's own fact-checks | To remind the owner of promises, prefer the more reliable source when sources disagree, and suggest checking in with family; never sent anywhere |
| Packages on the way (tracking number, carrier, the shipping email's subject, status, latest event, estimated delivery) | Shipping emails and notifications, read on Buddy's server with text rules (no AI); numbers the owner gives; 17TRACK live status when its key is set | To tell the owner when a package is out for delivery, delivered, delayed or has a problem, and to answer where it is |
| Chat messages (WhatsApp, Messages, Telegram, Signal) as they appear in the phone's notifications; facts about people learned from them; replies the owner approves; the owner's auto-reply rules and a log of auto-replies sent | The phone's notifications (only with Notification access on); chats are read by Claude's quick model for memories (never a free model); replies go out through the chat app's own notification Reply, from the owner's account | To remember what matters about people and promises, and to reply for the owner: each reply only after their yes; automatic replies only by their own rules (their own words, signed as Buddy, never to groups or unknown numbers, at most once per chat every 2 hours) |

Buddy never collects bank passwords. Bank sign-in happens inside Plaid Link (or the SimpleFIN Bridge site), between the owner and the bank. Buddy cannot move money, make payments or change accounts.

## Consent

The owner gives consent for every data source himself, before any data is collected:

- **Bank data:** in Plaid Link's consent screen, separately for each bank he connects, choosing which accounts to share.
- **Phone data:** through Android's own permission screens for notifications, calendar, location and microphone, each of which can be switched off at any time.
- **Email and calendar:** by adding each account to Buddy himself.

Consent can be withdrawn at any time by unlinking the account (see Owner's controls).

## How the data is used

The data is used only to give the owner answers and reminders about his own life and money:

- Tracking spending by category, budgets and limit warnings
- Finding subscriptions, bills due and price increases
- Answering the owner's questions ("How much did I spend on food this month?")
- Reminders, calendar help and travel checks

The data is **never** sold, rented, shared for marketing, used for advertising, used to train AI models, or used for credit decisions. It is never shown to anyone other than the owner.

## Where data is stored and how it is protected

All data is stored on one private server owned by the owner (a DigitalOcean virtual server).

- **Encryption at rest:** the database is encrypted with SQLCipher; uploaded files are encrypted with AES-256-GCM using a key derived from the database key; keys and API secrets are sealed with systemd-creds and never stored in plain text.
- **Encryption in transit:** every connection uses HTTPS with TLS 1.2 or higher; traffic between the phone and the server also runs inside Tailscale's encrypted WireGuard tunnel.
- **Network access:** the server is reachable only over the owner's Tailscale private network; new devices must be approved by the owner.
- **Multi-factor authentication:** the Google account behind Tailscale uses 2-Step Verification (passkeys and Google prompt); the DigitalOcean account requires an authenticator app; the app also requires a secret access token.
- **Access:** only the owner. There are no employees, contractors or shared admin accounts.
- **Sensitive documents:** identity documents, medical records, tax returns and files with account numbers are kept in a vault: Buddy asks the owner before an AI reads them again, and opens them for 10 minutes at a time.
- **Search index:** passages of uploaded documents are indexed for keyword and meaning search inside the encrypted database; the meaning-search model runs on the owner's server, so this text is not sent anywhere to be indexed.
- **Code security:** every change is scanned automatically for vulnerable libraries (pip-audit) and unsafe code (bandit) before it is used.
- **Bank tokens:** Plaid access tokens are stored only in the encrypted database and are never shown on the phone or in logs.
- **Backups:** nightly, encrypted with an age key kept off the server; the database key and the uploaded files are included, so a backup can be restored only with that key.

## Service providers

Buddy uses a few service providers to work. Each sees only what its job needs, under its own privacy terms.

| Provider | What it does | What it sees |
| --- | --- | --- |
| Plaid | Connects the owner's banks, read-only | Bank data the owner chooses to share in Plaid Link |
| SimpleFIN Bridge | Alternative read-only bank connection | Transactions and balances of linked accounts |
| Anthropic (Claude) | Understands questions and writes answers; reads uploaded photos and scanned PDFs once to file them; reads a shared credit card statement once to fill the card calendar (card numbers masked first). Subscriptions are found by code on the server, and Claude only sees the finished list | The question and the data needed to answer it; an uploaded photo or scan when it is first filed, and a vault document only after the owner says yes; once a night, that day's requests and newly saved facts and documents (never vault documents) so Buddy can tidy its memory, merge duplicates and find dates to remind the owner about |
| Groq | Turns the owner's speech into text | Short voice clips, not stored by Buddy afterwards |
| Google Gemini (free tier) | General tasks with no personal data, such as summarising public web pages | General text only; personal or financial data is never sent. This includes public market notes (chart numbers, Reddit posts and headlines about a stock) and a card issuer's public pages with the card's name, to look up its published benefits |
| Serper | Web search | Search words only |
| DigitalOcean | Hosts the server | Encrypted data at rest |
| Tailscale | Private network between the phone and the server | Encrypted traffic it cannot read |
| Duffel, SerpApi, Travelpayouts | Flight offers, Google Flights and Hotels prices, cheapest travel dates | Airports or place, dates and number of travellers only |
| Alpaca (market data), Stooq, Coinbase, Yahoo Finance (public chart data), Reddit, StockTwits | Prices, charts, option prices and news for trade ideas; what people post about a stock or coin | Ticker symbols and search words only; never the owner's trades, holdings or account details. Buddy uses Alpaca's market data only and never places trades |
| MTA (api-endpoint.mta.info), NJ Transit (raildata.njt.gov for trains, pcsdata.njtransit.com for buses and light rail), Port Authority PATH (panynj.gov) | Live service alerts and next trains for the lines the owner rides | No personal data: MTA and PATH feeds are downloaded whole and filtered on Buddy's server. NJ Transit receives the owner's NJ Transit developer username and password, to issue an access token |
| AirLabs (airlabs.co) | Live status of the owner's tracked flights | Flight numbers only (for example AA1234); never the owner's name, booking or ticket details |
| FlightAware AeroAPI (aeroapi.flightaware.com) | A backup for the owner's tracked flights when other sources lack the gate, delay or status | Flight numbers only (for example AA1234); never the owner's name, booking or ticket details |
| OpenStreetMap Nominatim (nominatim.openstreetmap.org) | Turning the owner's shared location into a neighbourhood and city name, and finding an airport's position | The phone's shared coordinates (only when location sharing is on), airport codes, and the address of a calendar event with a place (to work out when to leave); never the owner's name or the event's details |
| Open-Meteo (open-meteo.com) | Weather forecasts where the owner is, for places they ask about, and at a trip's destination | Coordinates rounded to about 1 km, or a place name; never the owner's name or plans |
| 17TRACK (api.17track.net), optional | Live status of the owner's packages, when its key is set | Tracking numbers only |
| Hacker News (Algolia search), Google News (Serper), Google Trends (SerpApi), arXiv, GitHub | What people are discussing on the topics the owner watches or asks about: stories, headlines, rising searches, new papers and projects | The topic's search words only; never anything personal |

No provider receives data for its own marketing or advertising.

## Retention and deletion

Data is kept only as long as it is useful, then deleted automatically.

| Data | Kept for |
| --- | --- |
| Bank transactions and balances | 24 months, then deleted |
| Bank connection (Plaid access token) | Until the owner unlinks the bank; the token is then revoked at Plaid and the bank's data deleted within 30 days |
| Travel searches; prices seen; outside API call log | Searches 90 days; individual prices seen kept 90 days, then turned into summary price patterns (averages and ranges by route, month, booking window and weekday, with no individual searches) that are kept; API call log 400 days |
| Money goals, pay and payday settings (until the owner removes or changes them); weekly money reports (12 months), monthly check-ins (24 months) and detailed money reviews (24 months) | As listed; older reports are deleted automatically |
| Subscription list and card calendar | Until the owner removes them; the transactions behind them are deleted after 24 months |
| Card benefits and wish list | Until the owner removes them |
| Credit scores and credit report changes | Until the owner asks Buddy to forget them; detailed credit reports 24 months, then deleted automatically |
| Market watchlist, trade ideas and tracked trades; market scan reports | Watchlist and trades until the owner removes them; market scan reports 90 days, then deleted automatically |
| Watched commute lines and the transit alerts already told | Lines until the owner removes them; a told alert until it clears |
| Tracked flights and their status | Checked until the flight lands or is cancelled; deleted automatically 30 days after the flight date. The owner can remove one any time |
| Upcoming plans and lessons from corrections | Plans are deleted automatically 30 days after they end; lessons stay until the owner asks Buddy to forget them |
| Buddy's notes, decisions and source gaps | Time-sensitive findings and market or news reports: 30 days. Other notes and advice: until the owner asks Buddy to forget them ("forget the note about …") |
| Location history, learned places, promises and source reliability | Location history: 60 days, then deleted automatically; the owner can delete it and the saved places any time ("forget my location history"). Promises: until done or removed. Source reliability: counts only, kept |
| Budgets and cost watch reports (Buddy's own spending: costs by kind of work, never what was asked) | Budgets until the owner changes them; cost watch reports 60 days, then deleted automatically |
| Packages | Until 30 days after delivery (or 60 days with no news), then deleted automatically; the owner can remove one any time |
| Chat notifications, auto-reply rules and the auto-reply log | Notifications (chats included): 7 days. Auto-reply rules: until the owner removes them. Auto-reply log: 30 days. Facts learned about people: as Buddy's notes (see above) |
| Watched trend topics; trend digests and daily mention counts | Topics until the owner removes them; digests and counts 60 days, then deleted automatically |
| Uploaded documents and photos, with their text, summaries, facts and search index | Until the owner tells Buddy to forget them; forgetting deletes the file, its text, its index entries and the facts taken from it |
| People and things noted from the owner's data | Until the owner edits or deletes them |
| Preferences and dates being watched | Until the owner removes them (in the app under Library, What Buddy knows about you, or by asking Buddy) |
| Logged choices and daily conversation summaries | Until the owner deletes them; only the last 120 days of choices are used to learn preferences |
| Emails | 30 days |
| Phone calendar events | 14 days |
| Phone notifications | 7 days |
| Room transcripts (Listen-in mode) | 2 days, unless the owner saves a stretch |
| English-practice speech samples | 30 days |
| Location | The latest is treated as expired after 3 hours; the history is kept 60 days (see below) |
| Voice audio | Not stored; only the text is kept |
| Diagnostics (microphone levels, connection, background and server events; no audio and no words; can be switched off in Settings) | 30 days |
| Voice timing (how long each step took; no audio and no words) | 90 days |
| Voiceprint | Until the owner says "forget my voice" |
| Health data | 12 months |
| Backups | Last 14 nightly database backups; last 3 copies of the uploaded files |
| Access log (security) | 400 days, then deleted automatically |
| Security and SOC 2 readiness reports | 24 months |

If Buddy is shut down, the server and its encrypted database are deleted, and every bank connection is revoked at Plaid.

## Owner's controls, review and contact

The owner can, at any time:

- Unlink a bank in Buddy or at my.plaid.com, which revokes access and deletes its data as above
- Turn off any phone permission (notifications, calendar, location, microphone) in Android settings
- Ask Buddy to forget his voiceprint or delete saved transcripts
- Delete all data by deleting the server

This policy is reviewed every 12 months, and whenever Buddy starts collecting a new kind of data. Next review: November 2027.

Questions about this policy: privacy@buddylabs.io.
