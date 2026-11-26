# Buddy by Buddy Labs: redesign

Brand, product structure, design system, a working prototype and the engineering plan for Buddy's redesign.
Conversation first; five destinations (Home, Health, Money, Explore, Profile); health kept out of every AI model;
nothing acts without the owner's approval.

**Status:** designed, prototyped and tested on the prototype. The Android app and server pages are unchanged until
the owner approves Stage 9 (`09-delivery/open-decisions.md`).

| Folder | What's in it |
| --- | --- |
| `01-brand/` | Guidelines, three logo concepts, the chosen logo, app icons, favicon; `build_logos.py` makes every file |
| `02-product/` | Discovery, the capability inventory mapped to the new structure, 20 journeys with tap counts |
| `03-ux/` | Navigation map, sections, rules and names |
| `04-ui/screens/` | 25 rendered screens: every destination folded and open, light and dark, plus sheets |
| `05-prototype/` | `index.html`: open it in a browser (works offline except the web font); `prototype.html` is the same page for publishing |
| `06-design-system/` | Tokens (JSON, CSS, Kotlin), contrast report, components; `build_tokens.py` makes them |
| `07-engineering/` | Architecture and data boundaries, Android and web specs, migration plan |
| `08-quality/` | Acceptance criteria with status, test cases, the prototype test and its results |
| `09-delivery/` | Executive summary, roadmap and release plan, open decisions, stage reports |

Rebuild everything:

```
python3 buddy-redesign/01-brand/build_logos.py
python3 buddy-redesign/06-design-system/build_tokens.py
python3 buddy-redesign/05-prototype/make_index.py
NODE_PATH=$(npm root -g) node buddy-redesign/08-quality/prototype.test.js
```
