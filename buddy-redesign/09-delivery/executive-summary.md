# Executive summary

Buddy's redesign is designed, prototyped and tested on the prototype. The owner approved Stage 9 on 2026-11-26, and
it is going into the app and server pages one revertible step at a time (`07-engineering/migration-plan.md`).

**What changes for the user:** Buddy still opens on the conversation, now with the message box and mic on screen
at once, six quick actions (log a meal, log blood pressure, scan food, spending, bills, deals), what's waiting for
approval and today's briefing. Five destinations replace five places plus about 63 tab views: **Home, Health,
Money, Explore, Profile**, each with at most five sections and nothing deeper than a sheet. Health moves from four
levels deep under More to the bar, with its own one-tap entries, and stays outside the conversation and every AI
model.

**What was produced** (in `buddy-redesign/`):

| Area | Delivered | Level |
| --- | --- | --- |
| Discovery | Inventory of every capability and where it goes; constraints; current tap counts | done |
| Brand | Strategy and guidelines; three logo concepts; the chosen wordmark and "b" mark; lockups, mono, adaptive Android icon (SVG and VectorDrawable), favicon | designed, real SVG assets |
| Design system | Tokens for colour (light/dark, semantic, voice states, approval), type, spacing, radius, elevation, motion, layout; JSON, CSS variables, Kotlin; 50/50 contrast checks pass | designed, generated |
| Structure | Five destinations, 22 sections, navigation rules, names | designed, prototyped |
| Prototype | Every destination folded and open, light and dark, Free and Premium, loading/empty/error, voice states, approvals, health boundary | prototyped, 116/116 automated checks pass |
| Screens | 25 screenshots of the prototype (04-ui/screens) | rendered |
| Engineering | Architecture (control plane + one data plane per customer, health enclave, approval tokens, audit), Android and web specs, migration plan with flags | designed |
| Quality | Acceptance criteria with honest status, test cases for Stage 9 | documented; prototype tested |

**Decisions made (reversible):** the wordmark-derived mark; Money opens on an Overview; Health's mic replaced by
"Log BP" and "Scan"; secondary text darkened to #5E6B63 for contrast; multi-user as a shared control plane with
isolated per-customer data planes (keeps "one server per customer").

**Not done, and why:** no Figma file (no Figma access here: the prototype,
SVGs and tokens are the importable assets); no pricing (deliberately).

**Next:** migration steps 1 to 6 in order; decisions 6 to 11 in `open-decisions.md` stay open.
