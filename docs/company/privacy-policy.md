# Buddy Privacy and Data Retention Policy

Source: https://claude.ai/artifact/MJVGZ7pWQvi26tg1MS914P, revision 31

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
| Location (neighbourhood and city) | The owner's phone, only when location sharing is on | Latest only |
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
| Credit scores (score, bureau, scoring model, date) and credit report changes (hard inquiries, new or closed accounts, late payments, collections, address changes, fraud alerts: the date, the lender and a few words) | The owner's credit monitoring emails and phone notifications (such as Credit Karma, Chase Credit Journey, Amex MyCredit Guide, Experian), new cards seen at banks the owner links, and scores the owner tells Buddy | Read-only; Buddy never signs in to these services, and never freezes, disputes or applies for anything itself |

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
| Google Gemini (free tier) | General tasks with no personal data, such as summarising public web pages | General text only; personal or financial data is never sent |
| Serper | Web search | Search words only |
| DigitalOcean | Hosts the server | Encrypted data at rest |
| Tailscale | Private network between the phone and the server | Encrypted traffic it cannot read |
| Duffel, SerpApi, Travelpayouts | Flight offers, Google Flights and Hotels prices, cheapest travel dates | Airports or place, dates and number of travellers only |

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
| Credit scores and credit report changes | Until the owner asks Buddy to forget them |
| Uploaded documents and photos, with their text, summaries, facts and search index | Until the owner tells Buddy to forget them; forgetting deletes the file, its text, its index entries and the facts taken from it |
| People and things noted from the owner's data | Until the owner edits or deletes them |
| Preferences and dates being watched | Until the owner removes them (in the app under Library, What Buddy knows about you, or by asking Buddy) |
| Logged choices and daily conversation summaries | Until the owner deletes them; only the last 120 days of choices are used to learn preferences |
| Emails | 30 days |
| Phone calendar events | 14 days |
| Phone notifications | 7 days |
| Room transcripts (Listen-in mode) | 2 days, unless the owner saves a stretch |
| English-practice speech samples | 30 days |
| Location | Only the latest; treated as expired after 3 hours |
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
