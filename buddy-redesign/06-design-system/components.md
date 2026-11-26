# Components, accessibility and responsive rules

Status: **designed and prototyped** (every component below is live in `../05-prototype/prototype.html`); not yet in
the app. Tokens: `design-tokens.json`, `tokens.css`, `BuddyTokens.kt`.

## Components

| Component | Anatomy | States | Rules |
| --- | --- | --- | --- |
| App header | Title (or the Buddy mark + wordmark on Home), up to two actions | default | One title per screen; no title repeated inside the page |
| Bottom bar / rail | 5 items, icon + label | selected (soft green pill), default | Labels always shown; 72 dp bar, 80 dp rail; same order folded and open |
| Section control | Up to 5 pills under the title | current (ink fill), default | The only second level; scrolls sideways if it must, never wraps to two rows |
| Conversation composer | Attach (+), text field, mic/send button; suggestions above; voice row | idle, typing (send), listening, thinking, speaking, interrupted, error | Visible on Home without scrolling; mic 48 dp; Enter sends |
| Voice state row | Dot + label + Talk / Listen-in / Hands-free toggles | idle, listening (teal, pulsing ring), thinking (violet), speaking (amber), interrupted, error (red) | Colour, icon and words together; no animation with reduced motion |
| Message bubble | Text; Buddy's may hold a rich card | user, Buddy | User right in primary; Buddy left on surface |
| Rich result card | Title, items, map/table/sparkline, "What this rests on", actions | default, selected (opens the detail panel) | Examples: places (3 picks with map), comparison table, deal with price history, money summary, news |
| Summary tile | Big number, label | default | Tabular numbers; units in the label |
| Status chip | Word + colour | good, warn, bad, info, approval, premium | Never colour alone |
| Verdict card | Verdict chip, serving line, Good list, Watch list ("for you" marks), "What this rests on", actions | good, sometimes, skip | Health only; worked out by rules |
| **Approval card** | "Needs your approval" chip; the exact action and amount; To, From, When, Risk; sources and rules; Approve (amount), Reject, Cancel | compact (Review), full | Shows exactly what will run; Approve names the amount; the server enforces (architecture.md) |
| **What this rests on** | Disclosure with Rule, Source, Your data, As of, Confidence | collapsed, open | On every judgement, warning, prediction and recommendation |
| Sheet / detail panel | Grab handle, title, close, body | open | Folded: bottom sheet over a scrim; open Fold: right panel; Back closes |
| Toast | One line | info | Confirms an action in plain words ("Logged 128/82 this morning") |
| Empty state | Icon, what will appear, how to start, one button | | Every list and section has one |
| Loading | Skeleton blocks the shape of the content | | No spinners for page loads |
| Error | What failed, what wasn't lost, Retry, a way to check | | Never a bare error code |
| Premium marker | "Premium" chip, one line, See plans | free plan only | Never on privacy, security, export or deletion |

## Accessibility

- Contrast: all 50 text and control pairings pass WCAG AA in both themes (`contrast-report.md`).
- Touch targets 48 dp minimum (bar items 56 dp tall; icon buttons 44 to 48 dp; rows 48 dp).
- Every control has a text label or `aria-label`; the voice row is a live region; dialogs are labelled.
- Status never by colour alone: chips carry words; voice states carry words and an icon.
- Reduced motion: the listening pulse and skeleton shimmer stop.
- Text scales to 200% without losing content (sections reflow; tables scroll inside their own box).

## Responsive

| Width | Navigation | Layout |
| --- | --- | --- |
| under 600 dp (Fold folded, phones) | bottom bar | one column; details in bottom sheets |
| 600 to 840 dp | rail | one column + detail panel when opened |
| 840 dp and up (Fold open) | rail | content column + 360 to 380 dp side panel; Home: conversation + Today panel |
