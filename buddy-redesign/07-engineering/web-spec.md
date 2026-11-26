# Web (server pages) specification

Status: **being implemented** (Stage 9). As built so far: one token source on every page (`tokens.css`, the
shared names in `theme.css`, the look picked by `theme.js`); no repeated titles inside the app (`?embed=1` hides
the page's own title); Health's five sections with no second row (step 6a, below). Fonts wait for decision 8.

- **One stylesheet:** `web/tokens.css` (copied from `06-design-system/tokens.css` by the build script) linked first
  by every page; each page's own CSS keeps layout only and refers to `var(--b-…)` tokens. The 14 pages' duplicated
  colour blocks are removed.
- **Inter** from a self-hosted file under `web/fonts/` (no third-party font request from the owner's server pages);
  system fallback.
- **No tab rows inside pages:** each page reads `section=` from its address and shows only that section; the app's
  native section control switches sections. Opened in a browser without the app, a page shows a simple section menu.
  *As built for Health (step 6a):* the page is the whole destination, so its one row is the five sections (Today,
  Food, Body, Labs, Library) with no row above it in the app; each section opens on its main view and lists the
  rest under "Also in …", opened full-page with a "‹ Food" button and the phone's Back. `?section=` opens one
  directly; `?tabs=1` brings back the earlier six tabs with their second row for one release.
- **No repeated titles:** the app header names the destination; the page starts with content.
- **Components** as small shared CSS classes in `tokens.css` (`.b-card`, `.b-tile`, `.b-chip--good|warn|bad|info|approval`,
  `.b-rests` for "What this rests on", `.b-approval`) matching the prototype.
- **States:** every data request shows a skeleton while loading, a designed empty state (what will appear and how to
  start) and an error that says what failed and offers Retry.
- **Accessibility:** 4.5:1 text contrast (checked), 48 px targets, focus rings, `prefers-reduced-motion`, labels on
  every control, no colour-only meaning (each status chip has a word).
- **Server-side visual changes ship with a deploy**, without an app release (as today).
- **API contracts:** unchanged. Pages call the same `/api/...` endpoints.
