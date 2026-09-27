# Native app plan (iOS + Android)

The web app in `/web` works on both platforms today. The native app adds what a
browser can't do. It uses the **same WebSocket protocol** (described at the top of
`backend/buddy/server.py`), so the backend doesn't change.

**Stack:** Flutter (one codebase for both platforms).

| Capability | Android | iOS |
|---|---|---|
| Keep listening with the screen off | Foreground service with a mic notification | Background audio session (limited; Apple may reject always-on listening) |
| "Hey Buddy" wake word | On-device wake word engine (Porcupine / openWakeWord) | Only while the app is active or the audio session is live |
| Earbud press-and-hold opens Buddy | Register as the default Digital Assistant (`VoiceInteractionService`) | Not possible; use a Siri Shortcut: "Hey Siri, Buddy" |
| Read and send SMS / notifications | Yes, with permission (NotificationListenerService, SMS) | No: Shortcuts / iMessage only |
| Speech-to-text | Streamed to a cloud STT with automatic language detection and speaker labels | Same |

## Build order

1. Flutter shell: connect, tap to talk, play `say` sentences (the same features as the web app).
2. Cloud streaming STT and neural TTS (lower latency, multilingual, speaker labels).
3. Android foreground service, wake word and default-assistant registration.
4. iOS Siri Shortcut / App Intent entry point.
5. Push notifications for nudges when the app is closed.
