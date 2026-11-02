# Instructions for Claude: build the company website

Paste everything below the line into Claude (Claude Code or claude.ai). It works alone or to change
the site in this folder.

---

You are building the public website for an AI company. Its first product is **Buddy**, a personal
voice assistant. The site must be good enough for **Plaid's production review**: Plaid checks that a
real company stands behind the app, that the product is described honestly, and that there is a
privacy policy covering bank data.

## Company facts (use exactly these; never invent others)

- Company name: **Buddy Labs** (working name; it may change, so keep it easy to replace)
- Founder: Aneesh Nair, United States
- Contact email: aneeshnair304810@gmail.com (to be replaced by an address on the company domain)
- Products: Buddy (first product); more AI products later
- Status: Buddy is in **private use and testing**. Public sign-up is not open. Never claim users,
  customers, revenue, funding, awards, partners or certifications that don't exist.

## What Buddy does (the only features to describe)

- Money: spending by category, budgets, subscriptions and price rises, card due dates, minimums,
  limits and APR, a weekly report and a monthly check-in
- Email and calendar: important emails flagged, bills and receipts found, rules the user approves,
  reminders
- Travel: compares flight and hotel prices from several sources, flags hidden fees and fine print,
  and links out. The user books on the provider's site.
- Voice first: phone and headset, with clear "I heard you" cues
- It asks for the user's yes before sending, buying, booking or changing anything

## Bank data and Plaid (must appear, worded accurately)

- Banks connect through Plaid and are **read-only**. Buddy can't move money or make payments.
- Users sign in to their bank inside Plaid Link. Buddy never sees or stores bank passwords.
- Users choose which accounts to share, and can unlink in Buddy or at my.plaid.com. Access is then
  revoked and that bank's data deleted within 30 days.
- Link to Plaid's End User Privacy Policy.

## Pages

1. **Home**: what the company does, its principles (no selling data, no ads, no training on user
   data, the user approves actions, read-only bank access, data minimization), its products and an
   honest status note.
2. **Buddy**: features, plus how bank connection with Plaid works.
3. **Security**:
   - encryption at rest (SQLCipher, AES-256-GCM, sealed keys) and in transit (TLS 1.2+, WireGuard);
   - private network with no public ports;
   - multi-factor authentication;
   - role-based least-privilege access under a written access control policy;
   - quarterly access reviews and automated offboarding;
   - daily patching and weekly security checks;
   - code scanning;
   - key rotation every 90 days;
   - AI safety (approval in code; untrusted content treated as information);
   - how to report a security issue.
4. **Privacy Policy**:
   - data collected and its sources;
   - consent;
   - use;
   - protection;
   - service providers (Plaid, Anthropic, Groq, Google Gemini and Serper for general text only,
     Duffel/SerpApi/Travelpayouts, DigitalOcean, Tailscale);
   - the retention table (bank data 24 months, emails 30 days, calendar 14 days, notifications
     7 days, travel 90 days then patterns only, voice audio not stored, backups the last 14 nightly);
   - user rights (access, correct, export, delete within 30 days);
   - under 18 not allowed;
   - changes;
   - contact.

   It must stay consistent with the owner's "Buddy Privacy and Data Retention Policy" document.
5. **Terms of Use**: invitation-only access, not financial advice, read-only accounts and user-made
   bookings, third-party services, as-is disclaimer, changes, contact.
6. **Contact**: general, privacy requests and security, plus the location (United States).
7. A **404** page.

## Technical requirements

- Plain static HTML and one CSS file. No build step, no JavaScript needed, no trackers, analytics or
  cookies, and no external fonts or scripts. This keeps the privacy policy true.
- Every page has: a `<title>`, a meta description, a viewport meta, a favicon, the same header
  navigation, and a footer linking Privacy, Terms, Security and Contact.
- Responsive from 360px up, with no horizontal scrolling. Tables scroll inside their own box on
  phones. Light and dark mode come from `prefers-color-scheme`.
- Accessible: semantic headings, readable contrast, visible focus states, `aria-current` on the
  active nav link.
- Never publish internal details: no server hostnames, IP addresses, file paths, key names or
  tailnet names.
- Keep the company name and email easy to change. `rename.sh "New Name" new@email` replaces them
  everywhere.

## Style

Calm, trustworthy fintech look. Plenty of white space, one green brand colour, rounded cards and
short plain sentences. No stock photos, no hype and no made-up numbers.

## When done

Check every page at 390px and 1280px wide, in both light and dark mode, and confirm no page
scrolls sideways. List anything you couldn't make accurate from the facts above, and don't guess.
