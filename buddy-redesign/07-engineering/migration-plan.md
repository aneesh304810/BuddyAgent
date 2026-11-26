# Migration plan

Status: **in progress**. The owner approved steps 1 to 6 on 2026-11-26. Nothing is deleted or overwritten without
its step below; each step is a separate commit that CI builds and tests, and each can be reverted on its own.

| Step | What changes | User-visible | Reversible by | Checks | Status |
| --- | --- | --- | --- | --- | --- |
| 1. Tokens in, no visual change | Add `web/tokens.css` and `app.buddy.design.BuddyTokens`; nothing uses them yet | No | Revert the commit | Full test suite; app builds | implemented: `build_tokens.py` writes both; `test_design_tokens.py` fails on drift |
| 2. New icon and brand | Adaptive icon foreground; wordmark in the Home header | Icon, header | Revert | Icon renders in circle and squircle masks | implemented: launcher, themed and status-bar icons, wordmark on the conversation's header, web favicon; all from `build_logos.py`; the mark stays inside the 66 dp safe circle |
| 3. Theme swap | `Theme.kt` reads `BuddyTokens`; server pages link `tokens.css` and drop their own colour blocks | Colours, type | Revert | Contrast report; screenshot comparison of every page | implemented: `Pal` reads `BuddyTokens`; Look is Follow the phone (default), Light or Dark; every page links `tokens.css`, `theme.css` (the shared names) and `theme.js` (picks the look before drawing); page colours of their own (chart series, severities) stay; type unchanged until decision 8 |
| 4. Five destinations | Bar/rail becomes Home, Health, Money, Explore, Profile; old screens mapped (table below); old deep links redirect | Navigation | Feature flag `nav_v2` (on by default after a week) | Compose UI tests; every old screen reachable | implemented: on by default, with Settings → Layout to go back (the owner is the only user, so the week's trial is the switch); Money opens on Overview; Research is a short list in Explore; native bar and rail kept (no new library) |
| 5. Home conversation-first | Composer at the bottom, quick actions, Activity section, context panel on the open Fold | Home | Flag `home_v2` | Journey tests (taps) on the device | implemented: quick actions (Log a meal, Log BP, Scan food, Spending, Bills, Deals) above the conversation, one tap each; on the open Fold (840 dp and wider) they sit with approvals and today's reminders and bills in a panel beside the conversation; the same Layout switch as step 4 (no second flag); Activity stays on the bell; tap counts on the device still to measure |
| 6. Sections without inner tabs | Server pages take `section=`; Health regrouped (Today, Food, Body, Labs, Library); Deals, Intelligence, Security filters instead of tabs | Pages | Old page tabs kept behind `?tabs=1` for one release | Page tests; no horizontal scroll at 412 px | next |
| 7. Multi-user control plane | Identity, entitlements, provisioning service; data plane checks signed customer id | Sign-in, plans | Separate service; data plane accepts the old owner token until switched | Isolation tests between two data planes | later (decision 6) |

## Where each old screen goes (step 4)

| Old | New |
| --- | --- |
| Buddy | Home › Conversation |
| Alerts (bell, More) | Home › Activity |
| Money › Accounts & bills | Money › Overview and Bills |
| Money › Spending, Cards, Invest | Money › Spending, Cards, Invest |
| Money › Email | Profile › Connections › Email |
| Trips & Deals › Trips | Explore › Travel |
| Trips & Deals › Deals | Explore › Deals |
| Discover › News | Explore › News |
| Discover › Intelligence, Library, Reports | Explore › Research |
| More › Health | Health (bar item) |
| More › Maestro | Profile › AI |
| More › Connections | Profile › Connections |
| More › Security | Profile › Security (and Privacy Gateway in Profile › Privacy) |
| More › Settings | Profile › Account |

API contracts stay as they are; pages and screens call the same endpoints.
