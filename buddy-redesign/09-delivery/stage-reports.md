# Stage reports

Run on 2026-11-26. Levels: designed, prototyped, tested, implemented. Nothing below is implemented in the app.

| Stage | Delivered (files) | Decisions | Assumptions | Open issues | Tests |
| --- | --- | --- | --- | --- | --- |
| 1. Discovery | 02-product/discovery.md, capability-inventory.md | Keep the conversation as the start screen; Health to the bar | The frequencies in the earlier brief marked "to confirm" | Owner to confirm frequencies | Backend suite 1,716 passed (no code changed) |
| 2. Brand | 01-brand/brand-guidelines.md, build_logos.py, logos/*.svg (10), icons/* (8, incl. VectorDrawable) | Wordmark + "b" mark | Inter and Roboto are acceptable | Logo choice (decision 2) | Contact sheet rendered; mark legible at 16 px |
| 3. Structure | 03-ux/information-architecture.md, 02-product/user-journeys.md | 5 destinations, 22 sections, sheets for depth | Money Overview first | Decisions 3 to 5 | Tap counts checked in the prototype |
| 4. Home and conversation | Prototype Home: composer, voice states, quick actions, approvals, rich cards, Activity | Quick actions first on Home; Today and approvals move to the side panel on the open Fold | Scripted replies stand in for Buddy | none | 116/116 |
| 5. Five destinations | Prototype Health, Money, Explore, Profile with their sections | Health's mic replaced by Log BP and Scan | Example data | none | 116/116 |
| 6. Responsive | Folded (bar, sheets) and open (rail, side panel) | 600 dp breakpoint; 840 dp for the side panel | Fold 8 inner display about 900 dp wide open | Verify on the device | All checks in both shapes |
| 7. Design system | 06-design-system: tokens JSON/CSS/Kotlin, components.md, contrast-report.md | Secondary text #5E6B63 | none | Decision 9 | 50/50 contrast pairs |
| 8. Prototype | 05-prototype/prototype.html (published), 04-ui/screens (25 PNG) | Free/Premium and states as live toggles | none | none | 08-quality/prototype-test-results.md |
| 9. Engineering | 07-engineering specs and migration plan | Control plane + per-customer data plane | none | **Waiting for approval to change the app** | test-cases.md written |
| 10. Quality | 08-quality: acceptance criteria with status, test cases | none | none | Screen-reader pass on the real app | prototype 116/116; contrast 50/50 |
