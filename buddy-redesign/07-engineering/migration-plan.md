# Migration plan

Status: **planned**. Nothing is deleted or overwritten without its step below; each step is a separate commit that
CI builds and tests, and each can be reverted on its own. Steps 1 to 6 need the owner's approval to start (Stage 9).

| Step | What changes | User-visible | Reversible by | Checks |
| --- | --- | --- | --- | --- |
| 1. Tokens in, no visual change | Add `web/tokens.css` and `app.buddy.design.BuddyTokens`; nothing uses them yet | No | Revert the commit | Full test suite; app builds |
| 2. New icon and brand | Adaptive icon foreground; wordmark in the Home header | Icon, header | Revert | Icon renders in circle and squircle masks |
| 3. Theme swap | `Theme.kt` reads `BuddyTokens`; server pages link `tokens.css` and drop their own colour blocks | Colours, type | Revert | Contrast report; screenshot comparison of every page |
| 4. Five destinations | Bar/rail becomes Home, Health, Money, Explore, Profile; old screens mapped (table below); old deep links redirect | Navigation | Feature flag `nav_v2` (on by default after a week) | Compose UI tests; every old screen reachable |
| 5. Home conversation-first | Composer at the bottom, quick actions, Activity section, context panel on the open Fold | Home | Flag `home_v2` | Journey tests (taps) on the device |
| 6. Sections without inner tabs | Server pages take `section=`; Health regrouped (Today, Food, Body, Labs, Library); Deals, Intelligence, Security filters instead of tabs | Pages | Old page tabs kept behind `?tabs=1` for one release | Page tests; no horizontal scroll at 412 px |
| 7. Multi-user control plane | Identity, entitlements, provisioning service; data plane checks signed customer id | Sign-in, plans | Separate service; data plane accepts the old owner token until switched | Isolation tests between two data planes |

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
