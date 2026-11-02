# Diagnostics (experience health)

Events and measurements from the phone and the server, to find what makes Buddy feel slow or deaf:
the microphone, Android putting the app to sleep, the network, or the server. **Never audio, never
words**: only times, levels (dB), error codes and device states. Kept 30 days. On by default.

Where to look: **Insights → Experience health** (tips, microphone, network, background, speech to text,
server, slowest moments), **Settings → Test my setup** (a 15-second check with fixes), and
**Export CSV / JSON** in Insights.

## What's recorded

| Phone | Server |
|---|---|
| Each clip: mic, noise dB, voice dB, voice over background (SNR), distortion, length; clips thrown away as too short | Speech to text: engine, time, confidence, Groq fallbacks; clips waiting in line |
| Mic: device added/removed, Bluetooth drops, mic busy, recorder failures | Phone connections and session length |
| Recognizer errors, Buddy's own voice heard back | Slow or failed replies with CPU load and free memory |
| App foreground/background, service started/stopped/restarted by Android, battery saver, Doze | |
| Network type and changes, disconnects, reconnect time, ping every minute | |

## Turning it off

- **App:** Settings → *Collect diagnostics* off (stops collecting and clears what's waiting on the phone).
- **Server:** `sudo bash /opt/buddy/deploy/set.sh BUDDY_DIAGNOSTICS` → `0`. The server stops storing
  and tells the app to stop sending. Insights hides the section.

## Removing it completely

Everything is in `backend/buddy/diagnostics.py` and `android/.../Diagnostics.kt`, plus one-line hooks,
all added in a single commit, so it can be reverted in one step:

```
git log --oneline --grep "Diagnostics:"     # find the commit
git revert <that commit>
```

The stored events are in the `diag_events` table; after a revert they're simply unused (or delete
them: `DELETE FROM diag_events`). Voice timings (`voice_timings`, from the earlier voice change)
are separate and stay.
