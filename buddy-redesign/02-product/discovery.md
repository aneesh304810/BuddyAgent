# Stage 1: Discovery

Status: **done** (research only; nothing in the product changed). Inspected 2026-11-26 from the repository on branch
`claude/personal-ai-voice-assistant-0zc5yh` (Android 0.3.183, server d908919), the
[UX brief](https://claude.ai/artifact/JyhwZ1482uMYZfTsP7Vx4W) and the
[screen map](https://claude.ai/artifact/CrwR5oghH6hDpjWPNqxfzJ).

## What Buddy is today, in numbers

| Part | Count | Where |
| --- | --- | --- |
| Android screens (Kotlin, Jetpack Compose) | 35 source files; 5 places, ~20 screens | `android/app/src/main/java/app/buddy/` |
| Server pages shown inside the app (WebView) | 14 HTML pages | `web/` |
| Server modules | 207 Python files | `backend/buddy/` |
| Automated tests | 199 test files, 1,716 tests passing | `backend/tests/` |
| Chat tools the assistant can use | 160+, in 20 modules | `backend/buddy/modules.py` |
| Tab views a user can land on | about 63 (5 places, their tabs and the sub-tabs of the server pages) | see the inventory |

## The structure today

Five places in a bottom bar (side rail from 600 dp): **Buddy** (conversation), **Money** (5 tabs, Invest has 4
more), **Trips & Deals** (2 tabs, Deals has 7 more), **Discover** (4 tabs, Intelligence has 6 more) and **More**
(Health with 6 sections and 16 sub-tabs; Maestro 3; Connections; Security 6; Alerts; Settings).

Correction to the earlier brief: Health has **16** sub-tabs, not 19 (Trends 4, Labs 4, Plan 5, Learn 3).

## Findings that shape the redesign

1. **The conversation already opens first.** The Buddy place is the app's start screen; the redesign keeps that and
   makes the composer the centre of it.
2. **Daily tasks are deepest.** Meals (4 a day), blood pressure (2 a day), today's health and the shop scan sit under
   More → Health, 3 to 4 taps from launch.
3. **About 63 tab views.** Many are second- and third-level tabs inside server pages (Deals 7, Intelligence 6,
   Security 6, Health 16).
4. **Two visual languages.** Native Compose screens (Material 3 chips, the `Pal` colours in `Theme.kt`) and server
   pages (each with its own CSS copy of similar tokens). Health repeats its title under the app header.
5. **Health is isolated by code, not by layout.** `privacygate.py` blocks health information from every AI model,
   Claude included; health is computed by rules (`dietcheck.py`, `foodverdict.py`, `bodylog.py`, `prevent.py`).
   The UI is the only way to work with health, so its screens carry more weight than usual.
6. **Approvals exist end to end.** `confirm_action` and the approval card in the conversation; nothing pays, books,
   transfers, submits, cancels, buys or trades without the owner's yes (server-side, not only in the UI).
7. **Explainability exists but varies.** "What this rests on" is used on Health checks and deal predictions, but
   not everywhere; sources show in several shapes.
8. **Single owner per server, already sold per customer.** `modules.py` has a "core" starting set for a new customer's
   server, module switches and a usage meter: the commercial model is one isolated server per customer, not a shared
   multi-tenant database.
9. **No notifications for new features** (owner's rule): alerts collect behind the bell; reminders ride the morning
   briefing and evening check-in.
10. **Server pages ship without an app release.** 14 pages are served by the owner's server; a deploy changes them.

## Constraints carried into every stage

| Constraint | Source | Effect on design |
| --- | --- | --- |
| Health never reaches any AI model | owner's rule; `privacygate.py` | Health is its own destination; nothing health-related in the conversation, briefing text from AI, or AI routing |
| Buddy never acts by itself | owner's rule; `confirm_action` | A reusable approval card with the exact action; the backend enforces it |
| No financial details filled into forms without asking | owner's rule | Form filling shows a review step |
| No notifications for new features | owner's rule | Notification settings exist but default off; the Activity feed replaces push |
| Ask before building | owner's rule | Stages 1 to 8 are design, prototype and specs; Stage 9 (changing the app) waits for approval |
| Names: Buddy, Buddy Labs, Maestro | owner's decisions | Kept; "MasterOS" not used |
| One server per customer | owner's decision | Multi-user design keeps per-customer data planes (07-engineering/architecture.md) |
| No scraping, official APIs only | owner's rule | Discovery features show their source and licence |
| Public website pages name no vendors | owner's rule | Brand guidelines repeat it |
| Fold 8: folded and open | hardware | Every layout in both shapes; 600 dp breakpoint checked against window size classes |

## Dependencies and technical limits found

- **No Figma access** in this environment: the design is delivered as SVG, JSON tokens, an HTML prototype and specs.
- **Fonts:** Inter is free (SIL OFL) and fine for web; Android stays on Roboto (system) to avoid shipping fonts.
- **Material 3 adaptive** (`NavigationSuiteScaffold`, `ListDetailPaneScaffold`, window size classes) is the planned
  Android base; the Google Maven repository is not reachable from this sandbox, so the Android changes are built by
  the project's CI (GitHub Actions), as today.
- **Server pages** are plain HTML, CSS and JavaScript with no build step: shared tokens arrive as one `tokens.css`.

The full capability inventory, mapped to the new structure, is in `capability-inventory.md`.
