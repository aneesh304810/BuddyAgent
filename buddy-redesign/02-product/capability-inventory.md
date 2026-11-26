# Capability inventory and where each capability goes

Every capability found in Stage 1, where it lives today and where it lives in the new structure. "Ask" means it is
also (or only) reached by asking Buddy in the conversation. Health capabilities are never reached through the
conversation: no AI model sees health information.

New structure: **Home** (Conversation · Activity), **Health** (Today · Food · Body · Labs · Library), **Money**
(Overview · Spending · Bills · Cards · Invest), **Explore** (For you · News · Deals · Travel · Research),
**Profile** (Account · AI · Connections · Privacy · Security). Details open as sheets (phone) or a side panel (open
Fold), never as a third level of tabs.

## Home

| Capability | Today | New place | Ask |
| --- | --- | --- | --- |
| Conversation (text, voice, Talk, Listen-in, earbuds, hands-free, interruption) | Buddy | Home › Conversation | yes |
| Today strip (next event, due this week) | Buddy | Home › Conversation, daily briefing | yes |
| Approval cards (calls, bookings, forms, payments) | Buddy (in the talk) | Home › Conversation and Home › Activity › Waiting for you | yes |
| Rich result cards (places, deals, reports, comparisons) | Buddy, Details screen | Home › Conversation; open Fold: side panel | yes |
| Alerts Buddy sent on its own, with thumbs and loudness | Bell → Alerts; More → Alerts | Home › Activity (feed); loudness in Profile › Account › Notifications | yes |
| Conversation history, recall | Buddy (scroll) | Home › Activity › History | yes |
| Attachments to the library | Buddy (📎) | Home composer; the file lands in Explore › Research › Library | yes |
| Reminders, tasks, goals (non-health), routines | Conversation only | Home › Activity › Reminders and tasks | yes |
| Search everywhere, reviews, best places nearby | Conversation (Search tab removed) | Home › Conversation | yes |
| Phone calls for the owner (reservations, questions) | Conversation | Home › Conversation, approval card first | yes |
| Making things (documents, charts, images, diagrams, forms) | Conversation | Home › Conversation | yes |
| Agents and skills, MCP servers | Conversation | Home › Conversation; list in Profile › AI | yes |
| Learning (English tutor, market school, morning tutor) | Conversation | Home › Conversation | yes |
| Glasses link (show, ask, choose) | Conversation | Home › Conversation; device in Profile › Connections | yes |

## Health (isolated: rules on the server, no AI model)

| Capability | Today | New place |
| --- | --- | --- |
| Ring tiles: readiness, sleep, HRV, lowest heart rate, temperature | Health › Today | Health › Today (summary); Body for trends |
| What it means for today; Your health now; this week's targets | Health › Today | Health › Today |
| Habits and goals | Health › Today; Plan › Progress; Plan › Goals & habits | Health › Today (habits) with a Goals sheet |
| Mood and stress journal | Health › Today; Trends › Mood | Health › Today (check-in) and Body › Mood trend |
| Blood pressure log, morning and evening | Health › Today; Trends › Body & BP | Header action "Log BP" (one tap from Health, two from Home); Health › Body |
| Weight, waist, BMI | Trends › Body & BP | Health › Body |
| 10-year heart and stroke risk (PREVENT) | Trends › Body & BP | Health › Body |
| Measures and patterns, next-morning model | Trends › Measures, Patterns | Health › Body › Trends |
| Meals by breakfast, lunch, snack, dinner; food lookup with How many; fit check | Plan › Food | Health › Food |
| Food scanner (barcode, dish, teach a dish) | Scan food button on Health only | Header action "Scan" in Health; Home quick action |
| Product verdict "Good for me?", products checked, compare | Plan › Groceries | Health › Food › Groceries |
| Day menus and diet targets | Plan › Menu | Health › Food › Menu and targets |
| Lab results, upload a report, MyChart portals, checkups | Labs (4 sub-tabs) | Health › Labs |
| Research digest, paper library, supplements, medicines | Learn (3 sub-tabs) | Health › Library |
| Where the numbers come from, delete or disconnect per source | Health › Sources | Health header "Privacy and sources" sheet; also listed in Profile › Privacy |
| Freshness watch (are Buddy's rules up to date?) | Health › Sources | Health › Library › Guidelines |

## Money

| Capability | Today | New place | Ask |
| --- | --- | --- | --- |
| Balances, accounts, bank links | Money › Accounts & bills | Money › Overview | yes |
| Spending by category, trend, by card, transfers, waste and leaks, budget | Money › Spending | Money › Spending | yes |
| Bills, subscriptions, cancel a subscription (with approval) | Money › Accounts & bills; conversation | Money › Bills | yes |
| Payments to people (Venmo, Zelle, PayPal, Cash App) | Money › Spending | Money › Spending › Transfers | yes |
| Cards, rewards guide, missed rewards, card offers | Money › Cards; Spending | Money › Cards | yes |
| Investments: accounts, watchlist, markets, signals, paper trading, futures and short-selling data, strategy lab | Money › Invest (4 sub-tabs); Intelligence › Strategies | Money › Invest (Portfolio, Watchlist, Research, Practice as filters, not tabs) | yes |
| Financial reports | Discover › Reports | Money › Overview › Reports (financial); Explore › Research › Reports (others) | yes |
| Email-derived money facts (receipts, bills, orders) | Money › Email | Used inside Spending and Bills; the email map itself in Profile › Connections › Email | yes |

## Explore

| Capability | Today | New place | Ask |
| --- | --- | --- | --- |
| Personalised news on chosen topics, trends, Reddit | Discover › News | Explore › News; highlights in Explore › For you | yes |
| Buddy Intelligence: convergence, sectors, sources, predictions | Discover › Intelligence (6 sub-tabs) | Explore › Research (one page with filters) | yes |
| Deals: for you, products, predicted, sources, alert settings | Trips & Deals › Deals (7 sub-tabs) | Explore › Deals (filters: products, flights, hotels; Predicted as a view) | yes |
| Price book, price history, watches, when to buy | Deals; conversation | Explore › Deals › item sheet | yes |
| Product comparison | Conversation | Home › Conversation card; Explore › Deals › compare | yes |
| Trips, flight and hotel search, transit alerts, flight status, rides, maps, weather | Trips & Deals › Trips | Explore › Travel | yes |
| Library (uploaded and shared files) | Discover › Library | Explore › Research › Library | yes |
| Reports Buddy wrote | Discover › Reports | Explore › Research › Reports | yes |
| Restaurants, entertainment, places | Conversation | Home › Conversation; saved picks in Explore › For you | yes |

## Profile

| Capability | Today | New place |
| --- | --- | --- |
| Server link, voice, language, the look, battery | More › Settings | Profile › Account |
| Notifications and alert loudness | Alerts page | Profile › Account › Notifications (off by default for new features) |
| Subscription and usage limits (new) | none | Profile › Account › Subscription |
| Personalisation, taste, reply style, memory | Conversation | Profile › Account › Personalisation (view and edit what Buddy knows) |
| Maestro: providers, which model does each job, AI spending, requests | More › Maestro (3 tabs) | Profile › AI |
| Buddy Connect: every outside service, state, test | More › Connections | Profile › Connections |
| Email map (kinds of email and what Buddy does) | Money › Email | Profile › Connections › Email |
| Modules on or off | server setting | Profile › AI › Features (shown, switched on the server) |
| Security watch: findings, fixed, services, defences | More › Security (6 tabs) | Profile › Security |
| Privacy Gateway: rules, what each model may receive, record | Security › Privacy Gateway; Maestro | Profile › Privacy |
| Data export, deletion, retention | spread over pages | Profile › Privacy (export, delete one source or everything) |

## Counts

| | Today | New |
| --- | --- | --- |
| Top-level places | 5 | 5 |
| Tab views (all levels) | about 63 | 22 sections |
| Deepest path to a daily task | 4 levels (More → Health → Plan → Food) | 2 (Health → Food), or 1 tap from Home quick actions |
| Visual languages | 2 (Compose and per-page CSS) | 1 (shared tokens) |
