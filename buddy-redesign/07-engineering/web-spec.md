# Web (server pages) specification

Status: **specified**; not implemented.

- **One stylesheet:** `web/tokens.css` (copied from `06-design-system/tokens.css` by the build script) linked first
  by every page; each page's own CSS keeps layout only and refers to `var(--b-…)` tokens. The 14 pages' duplicated
  colour blocks are removed.
- **Inter** from a self-hosted file under `web/fonts/` (no third-party font request from the owner's server pages);
  system fallback.
- **No tab rows inside pages:** each page reads `section=` from its address and shows only that section; the app's
  native section control switches sections. Opened in a browser without the app, a page shows a simple section menu.
- **No repeated titles:** the app header names the destination; the page starts with content.
- **Components** as small shared CSS classes in `tokens.css` (`.b-card`, `.b-tile`, `.b-chip--good|warn|bad|info|approval`,
  `.b-rests` for "What this rests on", `.b-approval`) matching the prototype.
- **States:** every data request shows a skeleton while loading, a designed empty state (what will appear and how to
  start) and an error that says what failed and offers Retry.
- **Accessibility:** 4.5:1 text contrast (checked), 48 px targets, focus rings, `prefers-reduced-motion`, labels on
  every control, no colour-only meaning (each status chip has a word).
- **Server-side visual changes ship with a deploy**, without an app release (as today).
- **API contracts:** unchanged. Pages call the same `/api/...` endpoints.
