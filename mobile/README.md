# Phone apps

- **Android:** the native app lives in [`/android`](../android/README.md) (Kotlin).
  It replaces the web app on your Galaxy and adds screen-off listening, the
  earbud button, notifications and call awareness.
- **Web app:** [`/web`](../web) still works in any phone browser, handy for
  testing or on another device.
- **iPhone:** not planned for now.

Both apps speak the same WebSocket protocol, described at the top of
`backend/buddy/server.py`, so the server doesn't care which one you use.
