# Acceptance criteria: status

Each criterion from the brief with its honest status today. Levels: **designed** (specified or drawn),
**prototyped** (works in the HTML prototype), **tested** (checked automatically), **implemented** (in the real app or
server). Stage 9 was approved on 2026-11-26; this table moves a row to implemented only when the app or a server page
ships it (see `07-engineering/migration-plan.md`).

| # | Criterion | Status | Evidence |
| --- | --- | --- | --- |
| 1 | Buddy opens directly into chat | prototyped, tested | prototype-test-results.md: "opens on Home › Conversation" (4 configurations). The current app already opens on the conversation |
| 2 | Composer immediately accessible | prototyped, tested | "composer and mic visible without scrolling" |
| 3 | Text and voice interactions work | prototyped, tested (scripted replies) | Voice states listening → thinking → speaking → interrupted; text send. Real voice exists in the app today |
| 4 | Five destinations, unified experience | prototyped, tested; implemented (step 4) | Every destination renders from the same tokens and components |
| 5 | Existing capabilities accounted for | designed | 02-product/capability-inventory.md maps every capability |
| 6 | Folded and unfolded layouts | prototyped, tested | All checks run in both shapes |
| 7 | Light and dark themes | prototyped, tested; implemented (step 3) in the app and on all 14 server pages | All checks run in both themes; contrast report |
| 8 | At most two levels of navigation | prototyped, tested | "two levels: every destination has 2 to 5 sections" |
| 9 | Daily tasks in two taps or fewer | prototyped, tested | Meal 1, BP 1, today's health 1, scan 1, bills 1, news 1, approval 1, spending 2 |
| 10 | Native and web share foundations | designed; tokens implemented (step 1) | One token source generating CSS and Kotlin; web-spec.md, android-spec.md |
| 11 | Accessibility validated | partly: contrast tested; targets and labels designed | contrast-report.md (50/50); screen-reader pass on the real app is pending |
| 12 | Health isolated from general AI | implemented today; prototyped; tested in the prototype | `privacygate.py` blocks health for every model today; the prototype turns health typed in the conversation away |
| 13 | Financial actions require approval | implemented today (`confirm_action`); approval card prototyped and tested; exact-action tokens designed | architecture.md |
| 14 | Judgements disclose evidence | prototyped | "What this rests on" on verdicts, places, deals, risks, health status |
| 15 | Free and Premium defined | designed, prototyped | Plans sheet; entitlements in architecture.md; no prices invented |
| 16 | Multi-user architecture documented | designed | architecture.md |
| 17 | Working prototype available | prototyped, tested | 05-prototype/prototype.html; published page |
| 18 | Engineering handoff complete | designed | 07-engineering/*, components.md, tokens |
| 19 | Regression tests documented and run | tested | Prototype journeys 116/116; backend suite 1,719 passed, including the token drift and contrast tests |
