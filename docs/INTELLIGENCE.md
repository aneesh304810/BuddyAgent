# Buddy Intelligence Platform

A shared intelligence layer inside Buddy, the way Buddy Connect is the shared layer for outside services. It
collects public evidence about technologies, works out which are forming into industries and which are
converging, and the rest of Buddy reads from it. Open it from the app menu: **🧭 Intelligence**.

## What it does

- **Follows sectors.** Ten are defined, with 54 technologies between them. Four are switched on to start:
  artificial intelligence, cybersecurity, AI infrastructure and semiconductors, and energy. The others
  (robotics, biomedical, fintech, cloud, startups, space and advanced computing) can be switched on from the
  Sectors tab or by saying "follow robotics".
- **Collects evidence every night** from free, official or first-hand sources, through Buddy Connect:

  | Source | Kind of evidence | What it means |
  |---|---|---|
  | arXiv, Europe PMC | published research | its authors' claim; not yet proven in use |
  | ClinicalTrials.gov | official record | a registered study exists; nothing about the result |
  | SEC EDGAR full-text search | company disclosure | the company's own statement in a filing |
  | USAspending.gov | official record | a US federal contract or grant was awarded |
  | GitHub | code published | working code exists; not proof anyone uses it |
  | Hacker News | reported | a story people discussed; check what it links to |

  Only a technology's search phrase is sent. No paid databases, nothing scraped.
- **Finds convergence.** Two technologies from different sectors that keep appearing in the same records, for
  example small modular reactors and AI data centers. Ranked by how many records join them, how many kinds of
  evidence, and whether it is growing.
- **Gives each a stage, by fixed rules, and shows the rule.** Only papers: scientific research. Working code:
  prototype. A registered study or a government award: pilot deployment. Companies writing about it in
  filings: early commercialization. Many companies: scaling. A long-established field is marked established.
- **Says when an industry may be forming:** three or more independent kinds of evidence for one technology all
  rising in the same month. You are told once.
- **Watches the sectors you don't follow.** Once a week each gets a light check (recent filings and stories
  only). When a sector's movement jumps against its own earlier weeks, Buddy tells you once and suggests
  following it, so something starting there isn't missed.
- **Keeps interpretation apart from evidence.** For the strongest convergences a free AI model writes what the
  pair adds up to, from the record titles only. It is shown in its own box marked "AI interpretation, not
  evidence" and never changes a stage or a rank.

## How Buddy uses it

Buddy answers from it: "what industries are emerging?", "what is converging with AI?", "who is working on
small modular reactors?", "how mature is fusion?", "follow robotics". It is the background for investing and
technology questions, and Buddy says what kind of evidence each statement rests on.

## The API for other agents and apps

A small read-only API, so another AI agent or app can use the platform without touching anything else in Buddy.

- **Key:** set one with `sudo bash /opt/buddy/deploy/set.sh BUDDY_INTEL_API_KEY` (at least 32 characters). It
  opens this API only: not your data, not the settings, not the rest of Buddy. Your own access token works too.
- **Endpoints** (all `GET`, JSON, `Authorization: Bearer <key>`): `/api/intel/v1/describe`, `overview`,
  `technologies`, `technology?id=`, `convergences`, `organisation?name=`, `search?q=`.
- **describe** returns the endpoints, the sectors and technology ids, the stages, what each evidence label
  means and how to read the data, so an agent can learn the API from one call.
- **Where it runs:** on your own Buddy server, reachable where that server is (your private network). There is
  no hosted public service yet.

The public product page is `intelligence.html` on the website; the API documentation is `docs/intelligence.html`
there, behind the docs sign-in.

## What it is not

- Not investment advice, a forecast or a recommendation. A rising count of records is not a judgement that
  there is an opportunity.
- No market sizes: Buddy can't verify one, so it doesn't compute one.
- The sources are US-heavy (SEC, US federal awards) and only cover what they publish. Private funding rounds
  are not in them.
- A stage is read from the records Buddy has collected, not from a market study. It needs a few nights of
  records before growth and convergence mean much.
- Tagging is by each technology's names and terms, in code, so a record that uses unusual wording is missed
  and a passing mention is counted.

## Privacy and cost

Everything in it is public data, kept in Buddy's public store (the DuckDB file beside the database), apart
from your own data, so free models may read it. The sources are free; the only AI cost is the short
interpretation for up to five convergences a night, on a free model when one answers.

## Planned next

Companies in depth (funding filings, institutional holdings, financials), transparent research scores, weekly
reports, a Themes view on the Investments page, and a graph view of the convergences.
