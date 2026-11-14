# Buddy Privacy and Data Retention Policy

Source: https://claude.ai/artifact/MJVGZ7pWQvi26tg1MS914P, revision 89

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
| Location (neighbourhood, city, street address, and the shop or place the owner is in when OpenStreetMap names it, with a list of such places visited, for the routine), a 60-day history of where the phone was, and the places learned from it (home, work, places the owner names) with the weekday commute learned from it (usual leave and arrival times each way, and the PATH stations and light rail lines the phone passes) | The owner's phone, only when location sharing is on | The latest, for answers about where the owner is; the history, to learn home, work and usual times, to remind at the right time and place, and to hold non-urgent messages while driving |
| Voice clips and speech transcripts | The owner's phone microphone while Buddy is listening | Processed, not kept as audio |
| Voiceprint (for voice match) | Five sentences the owner reads once | Stored as numbers, no audio |
| The owner's own lines in conversations (conversation coaching) | Listen-in, only when the owner turns coaching on; voice match picks out the owner's own voice | Only the owner's words, for their English review; other people's words are never kept |
| The owner's thermostat: room temperature and humidity, mode, target, eco, heating or cooling | Google Nest, after the owner links it once and picks which thermostat to share | Read when asked or for a heads-up, not stored; changes only after the owner's yes; only Claude sees the readings |
| Daily health totals: steps, sleep, resting and average heart rate, active minutes, calories | Google Health Connect on the owner's phone, read by the Buddy app only after the owner allows each type | Kept on Buddy's server; only Claude sees them, never a free model; never sent anywhere else |
| Oura Ring daily data: Readiness, Sleep and Activity scores, sleep stages and times, HRV, heart rate, temperature deviation, steps, calories, stress, resilience, blood oxygen, workouts; and age, weight, height and sex from the Oura profile | Oura's API, after the owner links their Oura account once (read only) | Kept on Buddy's server; only Claude sees it, never a free model; never sent anywhere else |
| The owner's health plan: goals, habits, food preferences and allergies, habits ticked off, meals mentioned with estimated calories and protein | The owner, in conversation with Buddy | Kept on Buddy's server for tracking, nudges and suggestions; only Claude sees it, never a free model; coaching, not medical advice |
| Phone calls Buddy makes for the owner: who was called, the goal, a short outcome summary, and the facts a call may share (full name and callback number; date of birth and insurance only on appointment calls) | The owner asks Buddy to call and approves each call; the facts come from settings the owner saves on Buddy's server with the setup script | No recording and no transcript are kept: the words exist only while the call lasts. Claude decides what to say; Plivo carries the call, Deepgram turns the other side's speech into text and ElevenLabs speaks Buddy's words. Never card numbers, account numbers, passwords or Social Security numbers |
| Calls to the owner that they don't pick up, answered by Buddy: the caller's number, the name and message they leave, and whether it looked like a scam | The caller, once the owner has set their phone to forward unanswered calls to Buddy's number; Buddy also checks the number against what it already knows (saved facts, emails, calendar) | To take a message and tell the owner what matters. No recording and no transcript; only the message is kept. Buddy never tells a caller where the owner is or anything personal; unknown callers hear only general timing. Scam calls are only counted |
| The owner's phone contacts: names and phone numbers only (no emails, addresses, notes or photos); and callers Buddy saved after an important call from a number not in the contacts (name, number, a short note) | The Buddy app reads the contacts only after the owner allows it, and syncs them to the owner's server; saved callers come from the calls Buddy answered | To greet a caller by name, judge who's calling, and find a contact's number when the owner asks Buddy to message or reply to someone. Kept only on Buddy's server; never sent to free models or anywhere else. Scam and sales callers are never saved |
| The owner's learned daily routine: for weekdays, Saturdays and Sundays, when they're usually at home, commuting, at the office or at a regular place | Learned on Buddy's server each night from the location history the app shares, plus what the owner says about their routine | To know what the owner is doing now (calendar, location, routine). On a call Buddy answers, only callers in the contacts or saved by Buddy hear what the owner is doing and until when, never where; unknown callers hear only that they're busy |
| Meeting requests from calls Buddy answered (who, the time they picked, what it's about), and events Buddy adds to the phone's calendar | The caller picks one of the owner's free times; the owner says yes before anything is added (the app writes it, with the Calendar permission) | To set up meetings and call-backs. Callers hear only free times, and that the owner will confirm; nothing goes into the calendar without the owner's yes |
| The chat with Buddy (text of what the owner said and what Buddy replied or sent on its own) | The owner's conversation with Buddy | Kept on Buddy's server and searched there; only the few lines a search finds are sent, to Claude only (never a free model) |
| Documents, photos, PDFs, spreadsheets, CSV and JSON files | Files the owner chooses to upload (Share to Buddy, or the paperclip in the app) | Stored encrypted; text, summaries, facts and search index kept |
| People and things mentioned in those files and in conversations | Pulled out of the owner's own data | Names, relationships and short notes |
| Preferences, choices and dates to act on | What the owner says they like, choices they make between options (such as Uber or Lyft after a price comparison), and dates found in their facts and documents | Short lines on the server; learned preferences are marked as learned |
| Subscriptions and credit card details (due and closing dates, balances, minimums, limits, APR) | Worked out from the owner's card transactions, card statement emails and statements they share, or read from Plaid (Liabilities) for banks the owner links | Card name and last 4 digits only; full card numbers are never stored |
| Money goals, take-home pay, paydays, autopay settings and card reward rates | What the owner tells Buddy (reward rates may start from published rates the owner confirms) | Used on the server to work out advice; Claude sees only the results it words |
| Travel searches, prices seen, routes the owner asks Buddy to watch, travel style and travel choices | The owner's searches; prices from Duffel, Google Flights and Hotels (SerpApi) and Travelpayouts; which offers the owner opens | Only places, dates and passenger counts are sent to these services; no names, documents or payment details |
| Credit card benefits (credits and perks, their amounts and reset dates) and a wish list of things the owner wants | What the owner tells Buddy; which credits were used is read from statement credits in the owner's own transactions | Stored on the server; Buddy never buys anything, it only suggests |
| The owner's shopping profile (how they research and decide for each kind of purchase: trusted sources and brands, brands to avoid, patience and the discount that makes them buy, must-haves, budgets, stores, regrets, loyalty programs), their purchase history (shop, item, price, date, whether it was on sale, returns), things they said they're thinking of buying, deals they asked Buddy to watch for (the item and a target price), deals read from public deal feeds (kept six months) and which ones matched what the owner wants, the owner's verdicts on what they bought (happy, okay or regret, and why, when Buddy asks a few weeks later), and Buddy's buy-now-or-wait calls (item, price, the price worth waiting for, the date) with how each turned out | The owner's answers in a shopping interview and in conversation; order and return emails, read on Buddy's server with text rules (no AI); shopping and travel charges in the owner's own transactions | Kept on Buddy's server so advice on a purchase follows the owner's own way of deciding; only Claude sees it, never a free model; never sent to shops or price services. Buddy never buys anything |
| Deal emails and newsletters (store, airline and hotel deals, deal-site alerts) in a separate deals inbox the owner creates for them; and deal and price-drop notifications from shopping and travel apps on the owner's phone (other apps' notifications are never read for this) | The deals inbox, read by Buddy each night with an app password and with text rules (no AI); once read, newsletters older than two days are moved to that inbox's Trash (bulk mail only, never a personal email; the owner can turn this off); the phone's notifications, which the Buddy app already shares when Notification access is on, read hourly with text rules | Only the deals are kept (item, price, regular price, store, link, end date), in the price book, for the Deals page and price patterns; the emails themselves are not stored and nothing is sent anywhere. Buddy never opens store pages; the owner opens a deal's link in their own browser |
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
- **The price book (not encrypted):** market prices are kept in a separate file on the same server (pricebook.duckdb), not encrypted, so it can be analysed with other tools. It holds products, flight routes and travel dates searched, hotels and stay dates searched, deals from public feeds, product launch dates and prices, Buddy's buy-or-wait calls (the item, its price and how the call turned out), and the price patterns learned from all of it. It holds no names, contacts, messages, accounts or payments, but the routes, dates and items show what was searched or considered. Raw prices and deals are deleted after 90 days, once learned from; the learned patterns are kept. Each night the patterns are learned in Buddy's sandbox, an isolated container on the same server with no network access; nothing is sent to an outside service. The nightly backup includes an encrypted copy.
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
| Google Gemini (free tier) | General tasks with no personal data, such as summarising public web pages | General text only; personal or financial data is never sent. This includes public market notes (chart numbers, Reddit posts and headlines about a stock) and a card issuer's public pages with the card's name, to look up its published benefits; Buddy's own program code, for small fixes to itself (it holds none of the owner's data); and general factual claims overheard in Listen-in, rewritten without names or personal details, to check whether they are true |
| Other free AI models (GitHub Models, OpenRouter, Mistral, Groq, Cerebras), when the owner sets them up | The same general tasks as Gemini, so Buddy can use the best free model for each | The same rule: general text only, never personal or financial data |
| Google (Nest Smart Device Management API) | Reading and, after the owner's yes, changing the owner's Nest thermostat | Requests for the thermostat Google already manages for the owner; the sign-in tokens stay on Buddy's server |
| Oura (API v2) | Reading the owner's own ring data | Requests for the data Oura already holds for the owner; read only; the sign-in tokens stay on Buddy's server |
| Google (Places API) | Finding restaurants for the owner | The search words and a neighbourhood or city (for example "Italian in Chelsea, New York"); never the owner's exact location |
| Ticketmaster (Discovery API) | Finding concerts, sports and shows for the owner | The search words and a city; never the owner's exact location. Tickets are bought by the owner on Ticketmaster's own page or app |
| Deal feeds (Slickdeals, DealNews, deal subreddits on Reddit, and any deal site's feed the owner adds); Best Buy Products API; eBay Browse API | Reading public deal feeds once a day (only after each site's robots rules allow it), and today's prices for the things the owner wants, kept in Buddy's price history | Feed addresses, and product names for price checks; never the owner's profile, purchases or anything personal. Store pages are never scraped |
| Plivo | Placing the phone calls the owner approved, ringing the owner in on hold-and-connect calls, and answering calls the owner's phone forwards when they don't pick up | The numbers called and the call's audio while it lasts, which includes the facts the owner allowed for that call; Buddy doesn't turn on Plivo's call recording |
| Deepgram | Turning the other side's speech into text during a call | The call's audio, only while someone is talking; Buddy opts out of Deepgram's model improvement program |
| ElevenLabs | Buddy's spoken voice on calls | The words Buddy says on the call, which can include the facts the owner allowed for that call |
| openFDA (api.fda.gov), MedlinePlus (wsearch.nlm.nih.gov), Open Food Facts | Public health information the owner asks for: medicine labels and recalls, health topics, food nutrition | Only the medicine, topic or product name; never the owner's health data |
| Google Shopping (through Serper) and Keepa (optional), for Amazon prices | Checking an Amazon price the owner asks about or watches | Only the product's name or Amazon ID; never personal data. Amazon's own pages are never scraped |
| GitHub, and Claude Code running there (Anthropic's GitHub Action, on the owner's Claude subscription) | Fixing Buddy's own code: after the owner's yes, Buddy opens an issue in its private repository and Claude Code pushes a fix to a branch | Code instructions only (what to fix, where); never the owner's personal data, which Buddy refuses to put in an issue |
| Serper | Web search | Search words only |
| Websites Buddy visits in its own browser (and DuckDuckGo for its searches) | Looking things up and filling web forms the owner asks for, in a browser on Buddy's server | The sites see the server's address and what Buddy types; a form is sent only after the owner's yes; never passwords, payments or uploads |
| DigitalOcean | Hosts the server | Encrypted data at rest |
| Tailscale | Private network between the phone and the server | Encrypted traffic it cannot read |
| Duffel, SerpApi, Travelpayouts | Flight offers, Google Flights and Hotels prices, cheapest travel dates | Airports or place, dates and number of travellers only |
| Alpaca (market data), Stooq, Coinbase, Yahoo Finance (public chart data), Reddit, StockTwits | Prices, charts, option prices and news for trade ideas; what people post about a stock or coin | Ticker symbols and search words only; never the owner's trades, holdings or account details. Buddy uses Alpaca's market data only and never places trades |
| FRED (Federal Reserve Bank of St. Louis), EIA (US Energy Information Administration), SEC EDGAR | Interest rates, Treasury yields, inflation and jobs figures, oil and natural gas prices, and companies' yearly filings, for Market school (the owner's investing lessons) | Data series names and ticker symbols only; never the owner's holdings, trades or account details. SEC requires every app to name a contact in its requests, so SEC also receives the contact email the owner chooses for this, and nothing else |
| MTA (api-endpoint.mta.info), NJ Transit (raildata.njtransit.com for trains, pcsdata.njtransit.com for buses and light rail), Port Authority PATH (panynj.gov) | Live service alerts and next trains for the lines the owner rides | No personal data: MTA and PATH feeds are downloaded whole and filtered on Buddy's server. NJ Transit receives the owner's NJ Transit developer username and password, to issue an access token |
| AirLabs (airlabs.co) | Live status of the owner's tracked flights | Flight numbers only (for example AA1234); never the owner's name, booking or ticket details |
| FlightAware AeroAPI (aeroapi.flightaware.com) | A backup for the owner's tracked flights when other sources lack the gate, delay or status | Flight numbers only (for example AA1234); never the owner's name, booking or ticket details |
| OpenStreetMap Nominatim (nominatim.openstreetmap.org) | Turning the owner's shared location into a neighbourhood, city and street address (and the shop or place it's in), and finding an airport's position. The position is never sent to a search service | The phone's shared coordinates (only when location sharing is on), airport codes, and the address of a calendar event with a place (to work out when to leave); never the owner's name or the event's details |
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
| Budgets, cost watch reports and cost archives (Buddy's own spending: costs by kind of work, never what was asked) | Budgets until the owner changes them; cost watch reports 60 days and cost archives (made at a cost reset) 13 months, then deleted automatically |
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
| The owner's own conversation lines (conversation coaching) | 30 days; turning coaching off deletes them |
| Daily health totals from the phone | 90 days, then deleted automatically; turning the permission off in Health Connect stops new ones |
| Oura Ring daily data | 90 days, then deleted automatically; removing Buddy's access in the Oura account stops new ones |
| Health plan, habit log and meals | The plan until the owner changes it or says "forget my health plan"; the habit log and meals 90 days |
| Buddy's self-check of its own server (problems from its log, with numbers, emails and call words removed, sent to Claude for a diagnosis; memory and disk readings) | The last report and 7 days of readings |
| Phone call summaries (who was called, the goal, the outcome, minutes) | 180 days, then deleted automatically; no recordings or transcripts are ever kept |
| Messages from calls Buddy answered for the owner (caller's number, name, message) | 180 days, then deleted automatically; no recordings or transcripts are ever kept |
| Phone contacts (names and numbers) | Replaced by each sync from the phone (a contact deleted on the phone is deleted here too); turning "Share contacts" off in the app deletes the whole synced list from the server |
| Callers Buddy saved, and the learned routine | Saved callers until the owner asks Buddy to forget them; the routine is relearned nightly from the last 6 weeks of location history (which is kept 60 days) and is deleted with it |
| Meeting requests from calls | 14 days after the requested time, then deleted; an event the owner added stays in their calendar until they remove it |
| The chat with Buddy (the owner's words and Buddy's replies, as text; no audio, no tool results, no Listen-in whispers or room talk) | 30 days, then deleted automatically; kept on the server so Buddy can look up earlier parts of the chat instead of resending all of it |
| Browser task reports (steps, pages used, screenshots) | 90 days, then deleted automatically; the browser itself keeps nothing once the task ends |
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
