# Company website: brief for Claude

The site lives in the private repo `aneesh304810/Buddywebsite` (static HTML; Cloudflare Pages deploys
every push to `main`). Everything in that repo is public on the web, so keep internal notes here
instead.

## Purpose

The site exists so banks, Plaid and partners can check that a real company stands behind Buddy, and
see how personal and financial data is handled. It is **not** a marketing site. **Don't reveal
product ideas or how features work.** Competitors and partners read it.

## What it may say

- Company: Buddy Labs (working name), United States. Contact email as on the site.
- Buddy: a personal assistant that helps people understand their own money (spending, bills, card
  due dates) and plan ahead. It gives information and suggestions only, and never moves money.
  Status: coming soon, in private testing.
- "More products are in development." No names, no descriptions.
- Bank connection through Plaid: read-only, sign-in inside Plaid, per-bank consent, disconnect at any
  time with data deleted. Include the not-affiliated notice.
- Compliance commitments, in general terms only:
  - consent;
  - no selling, ads or AI training;
  - AES-256 at rest and TLS 1.2+ in transit;
  - multi-factor authentication;
  - role-based, least-privilege access under a written policy;
  - periodic access reviews and deprovisioning;
  - patching and vulnerability scanning;
  - key rotation;
  - retention and deletion;
  - security contact.

## What it must not say

- Feature lists, how features work, comparisons, roadmaps, or future product details.
- Architecture or vendor details beyond Plaid: no database, network, hosting tool or AI vendor names,
  no model names, server names or file paths.
- Claims that aren't true today, or guarantees.

## Pages

Home, Security, Privacy Policy, Terms, Contact, 404. The privacy policy uses data categories, not
feature details. It must stay consistent with the "Buddy Privacy and Data Retention Policy" document,
and change when a new kind of data is collected.

## Waitlist

The home page form saves signups in Cloudflare KV (binding `WAITLIST`) through `functions/api/waitlist.js`. Export: `/api/waitlist?key=<WAITLIST_ADMIN_KEY>`. The privacy page has a Waitlist section; update it if the form asks for anything new.

## Trust Center (added 4 November 2026)

Public pages: trust, security, subprocessors, vulnerability-disclosure, biometric-privacy (plus privacy).
Every claim there must stay true. Before changing what Buddy does with data, update these pages.
Rows marked "In progress": penetration test, 6-month vulnerability assessment, SOC 2 Type I/II, DPAs.
Never write "SOC 2 compliant" or "certified" until a CPA firm issues a report.

Regenerate the site: `cd <Buddywebsite clone> && python3 <BuddyAgent>/docs/website-src/mksite.py <BuddyAgent>/docs/website-src/pages.py`
(writes every .html page; commit to Buddywebsite main, Cloudflare Pages deploys it).

## Buddy Connect (added 10 November 2026, at the owner's request)

- Public: a Buddy Connect card on the home page and `connect.html`, a gist only (one secure gateway, grouped by
  area, explained connections, privacy by rule, costs in the open). No vendor names or internals there.
- `/docs/` is the Buddy Connect API documentation (`apidocs.py`), for approved partners only, behind a password
  sign-in: `functions/docs/_middleware.js` and `functions/api/docs-login.js` in the site repo. The password is the
  Cloudflare Pages secret `DOCS_PASSWORD`, set only by the owner in the dashboard; it never goes in a repo, chat or
  page. Changing it signs everyone out. Ten tries an hour per visitor. `/docs/` is noindex and in robots.txt.
- The docs may name services and describe how Buddy Connect works (they're behind the sign-in), but never a server
  address, token, key, password or file path. Keep them in step with `backend/buddy/gateway.py`.
