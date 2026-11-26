# Android specification

Status: **specified**; not implemented (Stage 9 waits for approval). Kotlin, Jetpack Compose, Material 3 foundations
with Buddy's own theme.

## Theme

- `BuddyTokens.kt` (generated from `06-design-system/build_tokens.py`) becomes `app.buddy.design`. A `BuddyTheme`
  composable maps the tokens onto `MaterialTheme` (colour scheme, typography, shapes) and exposes the semantic
  colours (success, warning, critical, info, listening, speaking, thinking, approval) through a `LocalBuddyColors`.
- Replaces `Pal` in `Theme.kt`. Light and dark follow the system; the Settings choice overrides.
- Typography: Roboto (system), the token scale in sp; `fontFeatureSettings = "tnum"` for numbers.

## Adaptive navigation

- `NavigationSuiteScaffold` (material3-adaptive-navigation-suite) shows the five destinations as a bottom bar or a
  rail from the window size class (compact width: bar; medium and expanded: rail). The 600 dp breakpoint is the
  compact/medium boundary; check it on the Fold 8's inner display in both postures.
- Destinations as a type-safe Navigation Compose graph: `Home`, `Health`, `Money`, `Explore`, `Profile`, each with a
  `section` argument. Back: sheet → default section → Home → exit (`BackHandler`).
- Expanded width: `ListDetailPaneScaffold` for Home (conversation + context panel), Money, Explore and Health
  (list + detail). Sheets on compact become the detail pane on expanded.

## Screens: native versus server page

| Screen | Today | Plan |
| --- | --- | --- |
| Home (conversation, composer, voice, approvals, quick actions) | Native | Native, rebuilt on the theme |
| Health sections | Server page (`health.html`) | Server page restyled with `tokens.css`; quick sheets (log BP, log a meal, scan) native so they open in one tap from Home |
| Money, Explore sections | Mixed | Server pages restyled; native shell (header, section control) around them |
| Profile | Mixed | Native list screens; Maestro and Security as restyled server pages |

A native section control drives server pages through a `section=` parameter, so a page never shows its own tab row
(no second row of tabs, no repeated title).

## Components to build

Conversation composer (text, attach, mic, suggestions), voice state row (idle, listening, thinking, speaking,
interrupted, error; colour + label + icon), message bubbles, rich result cards (places, comparison, deal, money,
news), approval card, "What this rests on" disclosure, summary tile, verdict chip, section control, sheet/detail
pane, empty/loading/error states. Specs: `../06-design-system/components.md`.

## Voice

Keep `BuddyService` and the Talk/Listen-in engine; the redesign changes presentation only: one mic button with
state colours from the tokens, the state label always visible, the earbud hint in idle, interruption by tapping the
mic while Buddy speaks (exists today).

## App icon

Replace `ic_launcher_foreground.xml` with `01-brand/icons/ic_launcher_foreground.xml`; `buddy_icon_bg` stays
#1F6F5C; the monochrome layer uses the same foreground.

## Testing

Compose UI tests for: launch opens Home with the composer visible; each quick action opens its sheet in one tap;
approval card buttons call the approval API with the card's id only; Health never calls the conversation API.
Screenshot tests for each destination in compact and expanded widths, light and dark.
