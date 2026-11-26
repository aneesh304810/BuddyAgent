# Roadmap and release plan

Phases follow the migration plan; each release is small, flagged where it changes navigation, and revertible.
Durations are left to the owner: they depend on review time, not on build effort alone.

1. **Release A: foundations.** Tokens in (no visual change); new icon; Home header wordmark. Gate: owner approves
   decisions 1 and 2. Checks: full test suite, icon in circle and squircle masks.
2. **Release B: one look.** Theme swap in the app; `tokens.css` on all 14 server pages; titles not repeated.
   Gate: screenshots of every page reviewed. Checks: contrast report, no sideways scroll at 412 px.
3. **Release C: five destinations** behind `nav_v2`. Old screens mapped; old links redirect. Gate: decisions 3 and 4.
   Checks: every old screen reachable; Back behaviour tests.
4. **Release D: Home conversation-first** behind `home_v2`: composer at the bottom, quick actions, Activity, context
   panel on the open Fold. Gate: journey tap counts measured on the Fold.
5. **Release E: sections without inner tabs.** Health regrouped; Deals, Intelligence and Security as filters.
   Gate: decision 5. Checks: page tests; health makes no AI request.
6. **Release F: accounts and plans.** Control plane (identity, entitlements, provisioning); Free and Premium; export
   and delete-everything flows. Gate: decisions 6 and 7. Checks: isolation tests between two data planes.

Each release ships the Android app through the existing CI (GitHub Actions builds the APK) and server pages through
the usual deploy (`sudo git pull && sudo bash deploy/update.sh`). Server-page changes need no app update.
