# Instructions for Claude: build the company website

Paste everything below the line into Claude (Claude Code or claude.ai). It works alone or to change
the site in this folder.

---

You are building the public website for **Buddy Labs**, an AI company that makes AI products. Its
first product is **Buddy**, a personal AI assistant. The site introduces the company, shows why Buddy
is different from the other apps on the market, explains every feature clearly, and builds trust
with clear security and privacy pages.

## Company facts (use exactly these; never invent others)

- Company name: **Buddy Labs** (working name; keep it easy to replace)
- Founder: Aneesh Nair, United States
- Contact email: aneeshnair304810@gmail.com (to be replaced by an address on the company domain)
- Products: Buddy first, more AI products later
- Status: Buddy is in **private beta**, and people can ask for early access. Don't invent user
  numbers, customers, revenue, funding, awards, press, partners or certifications.

## The big message: why Buddy is different

**One app for all your apps, memories and money.** Most apps do one job and forget you between
visits. Buddy does all the jobs, knows you, and keeps getting better.

1. **One app instead of twenty**: email, calendar, reminders, to-dos, budgets, subscriptions,
   card due dates, travel deals, research, notes and translation, in one conversation.
2. **A memory that knows you**:
   - remembers facts, documents, people and preferences, with sources;
   - finds them by meaning, not just keywords;
   - tidies its memory every night (merges duplicates, flags facts that disagree, finds dates to
     remind you about).
3. **Grows with what you need (self-improving)**:
   - Ask for something it can't do yet. Buddy researches the service, builds the new ability,
     tests it in an isolated sandbox, and runs safety checks plus a second AI's review.
   - It switches the ability on only after your yes.
   - Fixes to Buddy's own code go to the developer for review; Buddy never releases changes to
     itself on its own.
   - Describe it exactly like this, never as code that changes itself unsupervised.
4. **Learns your taste**: from what you say and the choices you make (Uber or Lyft, which flights
   you open, where you go and spend). Learned preferences are marked so you can correct them.
5. **Does the work, you decide**: it drafts, compares, fills in and prepares. Sending, buying,
   booking and paying always wait for your yes, checked in code.
6. **Private by design**:
   - a private, encrypted server;
   - data never sold, no ads, no AI training on it;
   - bank access read-only.

Include a comparison table of "Typical single-purpose apps" vs "Buddy" covering: scope, memory, new
features, how you use it, advice, and your data. Don't name or criticize specific competitors.

## Every feature (Buddy page)

- **Talk, hands-free**:
  - conversation that keeps listening between turns; interrupt any time;
  - voice match, so only your voice counts in Talk mode;
  - clear "I heard you" cues (sound and vibration).
- **Memory and documents**:
  - share documents, photos, PDFs and spreadsheets;
  - facts, people and dates are pulled out and answered from later;
  - sensitive documents stay in a vault that asks before opening.
- **Money**:
  - spending by category; budgets with warnings;
  - subscriptions and price rises;
  - card due dates, minimums, limits and APR; the best card for rewards;
  - goals, a weekly report and a monthly check-in;
  - banks connect read-only through Plaid.
- **Inbox and calendar**:
  - all accounts sorted by priority; urgent mail flagged;
  - bills and receipts found;
  - rules and weekly label suggestions you approve;
  - reminders and a morning briefing.
- **Travel and deals**:
  - flights and hotels compared across several sources on the total price, hidden fees and fine print;
  - route and price watching; it learns what fares are normal;
  - you book on the provider's site.
- **Places and rides**:
  - restaurants, hotels and activities picked for your taste and checked against reviews;
  - Uber or Lyft opens with the drop-off filled in.
- **Research and reports**:
  - background research with sources and a spoken summary;
  - charts, diagrams, Word documents and slide decks.
- **Languages**: Listen-in gives the gist of conversations in a language you don't know; an
  English coach gives tips.
- **Organiser and forms**:
  - to-dos, goals and check-ins by voice;
  - drafts answers to web forms; you check them and press Submit.
- **Your other apps**: thousands of apps through standard connectors. Lookups just happen;
  changes ask first.

## Pages

1. **Home**:
   - hero ("One app for all your apps, memories and money");
   - "Why Buddy is different" (the 6 points);
   - the comparison table;
   - "What we believe";
   - an early-access note.
2. **Buddy**:
   - every feature;
   - "How Buddy improves itself" (you ask → it builds → it checks → you approve), plus learning
     your preferences, the nightly memory tidy-up and the economical choice of AI model;
   - how bank connection works (Plaid, read-only, no passwords seen, unlink any time, data deleted).
3. **Security**:
   - encryption at rest and in transit;
   - a private network with no public ports;
   - multi-factor authentication;
   - role-based, least-privilege access under a written policy;
   - quarterly access reviews and automated offboarding;
   - daily patching and weekly checks;
   - code scanning;
   - key rotation every 90 days;
   - AI safety;
   - how to report a security issue.
4. **Privacy Policy**:
   - data collected and its sources;
   - consent;
   - use;
   - protection;
   - service providers;
   - the retention table;
   - rights (access, correct, export, delete within 30 days);
   - under 18 not allowed;
   - changes;
   - contact.

   It must stay consistent with the "Buddy Privacy and Data Retention Policy" document.
5. **Terms of Use**: private beta, by invitation; not financial advice; read-only accounts and
   user-made bookings; third-party services; as-is disclaimer; changes; contact.
6. **Contact**: general, early access, privacy requests and security.
7. **404** page.

## Technical requirements

- Plain static HTML and one CSS file. No build step, no trackers, analytics or cookies, and no
  external fonts or scripts. This keeps the privacy policy true.
- Every page has: a `<title>`, a meta description, a viewport meta, a favicon, the same header
  navigation, and a footer linking Privacy, Terms, Security and Contact.
- Responsive from 360px up, with no horizontal page scrolling. Tables scroll inside their own box.
  Light and dark mode come from `prefers-color-scheme`.
- Accessible: semantic headings, good contrast, visible focus, `aria-current` on the active link.
- Never publish internal details: no server hostnames, IP addresses, file paths, key names or
  network names.
- `rename.sh "New Name" new@email` changes the company name and email everywhere.

## Style

Warm, confident and trustworthy. Plenty of white space, one green brand colour, rounded cards and
short plain sentences that talk to "you". No stock photos, no hype and no made-up numbers.

## When done

Check every page at 390px and 1280px wide, in light and dark mode, and confirm no page scrolls
sideways. List anything you couldn't make accurate from the facts above, and don't guess.
