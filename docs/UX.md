# Buddy's design standard

Every screen in Buddy, native or web, is held to this. Where a rule and a feature conflict, the rule wins
unless the owner decides otherwise.

## The principle

**One Buddy, simple on the surface, with the detail one tap away.** You talk to Buddy and see what matters
today. Models, providers, sources and logs exist, but you see them only when you ask or when something goes
wrong.

## The eight rules

1. **Conversation first.** Anything can be asked or done from the conversation. Screens are for looking,
   not for finding features. There is one conversation, on the Buddy place; the other places have a
   microphone button that talks to the same one.
2. **One screen, one purpose.** Each screen answers one question, with the most important thing at the top.
3. **Five places, always reachable.** Nothing used daily is more than two taps away, and the bar is within
   thumb reach.
4. **Answer first, detail on request.** Show the call, then one line of why. Evidence, models and cost open
   on a tap.
5. **Always say where it came from and how fresh it is.** Every signal, deal and prediction carries its
   source, its date and its record.
6. **Privacy you can see.** Mark what stays with Claude, and show what each outside service receives.
7. **Never leave you guessing.** Every action shows that it worked, is still running, or failed and why. A
   fallback is stated, not hidden.
8. **The same parts everywhere.** One card, one chip, one header and one set of colours, so every area
   feels like the same app.

## The five places

| Place | What lives there |
|---|---|
| **Buddy** | The conversation, and what needs you today |
| **Money** | Accounts and bills, Spending, Cards, Invest |
| **Trips & Deals** | Travel and deals, with the buy-now-or-wait calls |
| **Discover** | News, Intelligence, Library, Reports |
| **More** | How Buddy runs (Maestro, Connections, Security) and your setup (Alerts, Settings) |

Alerts open from the bell in the header on every screen. The phone's Back button goes up one level, then
to the conversation.

## Foundations

- **Material Design 3** for layout, spacing, touch targets and navigation.
- **Accessibility (WCAG 2.2, level AA):** text contrast of 4.5 to 1, text that scales with the phone's
  setting, touch targets of at least 48 points, a spoken label on every icon-only button, reduced motion.
- **The ten usability heuristics**, used as the checklist when a screen is reviewed.

## Parts

- **Icons** are line shapes drawn in the app (Nav.kt), in the theme's colour. No emoji as icons.
- **Colours** come from `Pal` (Theme.kt) in the app and the matching variables in the web pages. A state is
  never shown by colour alone: it also has an icon or a word.
- **Cards** have rounded corners of 12 to 18 points. **Chips** select a tab or a period.
- **The prediction card** is the same for investing, flights, hotels and products: the call, the chance or
  the record, one line of why, and a source line with its date.

## What is built, and what is not yet

Built: the bar with the five places, the header with Back and the bell, the microphone on the other places,
Trips & Deals, Discover and More, Invest as a tab of Money with four tabs of its own (Overview, Trading,
Themes, Watchlist), and "For you" in Deals: Buddy's open buy-now-or-wait calls for products, flights and
hotels in one card shape (the call, the chance, one line of why, then the date of the call and the record
of calls of its kind). Investment themes use the same card for each evidence pattern that fired.

The Today strip sits above the conversation: the next reminder or appointment, and the next bill or card
payment due within seven days, each with where it came from. It shows nothing when there is nothing. The
bell carries the number of alerts sent since Alerts was last opened; silenced kinds are not counted. Both
read `/api/today`, which uses no AI model and makes no outside call.

Trading (the old Practice tab) shows the strategies in use, what the closed trades have changed, the
patterns still only being measured, and what each paper trade used from Reddit, the price, company facts,
news and the Intelligence evidence.

Under each reply a small line says how it was answered: which AI answered, whether a free model helped
with a public part, and the tools used. It is built from the calls logged for that turn, and is left out
when nothing was logged.

On a screen 600 points wide or more (the Fold opened, a tablet) the five places move from the bar at the
bottom to a rail down the left side, and the buy-now-or-wait cards in Deals sit two across.

Trading also shows the strategy lab: six rule-based strategies that paper-trade every weekday, each with
its record against the market, and the trades it bought and sold. Each rule shows two records, live paper trades and
the same rule replayed over past prices. "Add a strategy from a document" takes an upload or pasted text,
shows the rule Buddy read from it, and starts testing it only after you approve. Below it are the research queue (papers and
code Buddy may fetch, each waiting for you to pick it), the yardsticks, and the dated stock lists.

Overview shows "Ideas from the strategy lab" in the shared card shape: the call (Stronger, Worth a look,
Unproven), one line of why, each rule that picked it with its record, then the source and date.

Trading opens with every paper trade in one place: the summary counts the Signals book's trades and the
strategy lab's together, "Bought and holding" and "Sold and judged" list both (each lab trade named by its
rule), and the day-by-day log merges them. The data panel, the strategies and the lab's table come after.

Not yet: source and freshness lines everywhere.

## Trading: News behind the trades

Each company Buddy is acting on shows its stories (one line per event, with the number of reports, a three-dot source
rating, and why a story is unclear). "News fact checks" shows how many unclear stories the free model settled, how
many the free news sources confirmed, how many went to a last-resort news service this month, how many would have been, which services have a key, and the
monthly limit, which can be changed there.
