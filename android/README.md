# Buddy for Android

The phone side of Buddy: ears and mouth. All the thinking, memory and agents
live on your private server; this app connects to it over Tailscale.

## What it does

| Feature | How |
|---|---|
| No tapping | Put your earbuds in and Buddy starts Listen-in by itself; say "Hey Buddy, ..." to talk to it. Take them out and it stops. (Settings → When earbuds connect.) |
| Talk to Buddy | Tap the orb, or tap your earbud (play/pause). Hands-free keeps listening after each reply. |
| Interrupt | Just start talking over Buddy. |
| Works with the screen off | Runs as a foreground service with a "Buddy" notification (Android requires it). |
| Listen-in | Works out the room's language by itself (Android 14+, or pick one) and the microphone: the phone's, or the earbuds' while the phone is in your pocket. Hears the room in the language you pick, and whispers translations, "they're talking about you" and fact checks. |
| Share my location | Optional (Settings): sends your location to your server when you open or talk to Buddy, so "a spa near me" works. Only the latest is kept; "approximate" shares neighbourhood level. |
| Approve / Decline | When Buddy asks before sending, booking, cleaning up or spending, tap Approve or Decline in the app or right on the notification. |
| Reminders and nudges | Spoken in your earbuds and shown as notifications. |
| Calls | Pauses itself during any call (phone, WhatsApp, Teams, Meet) and resumes afterwards. Android doesn't let apps hear call audio. |
| Typing | Type to Buddy when you can't talk. |
| Travel | Sends your time zone on every connect, so reminders follow you. |

## Install on your Galaxy

1. Install **Tailscale** from the Play Store and log in with the same account as the server.
2. On the phone, open the repo on GitHub → **Releases** → the newest **Buddy for Android** →
   tap the `.apk` file.
3. Android asks to allow installs from your browser: allow it once.
4. Open Buddy → **Settings** → paste the link the server setup printed
   (`https://<server>.<tailnet>.ts.net/?token=…`) → **Save and connect**.
5. Allow the microphone and notifications.
6. Tap **Allow Buddy to run in the background**, or Samsung will put it to sleep.
7. If your earbud tap doesn't reach Buddy, open Buddy once more after playing music
   elsewhere: Android sends the earbud button to the app that played audio last.

Every push to `android/` builds a new APK automatically (GitHub Actions, workflow
"Android app"). Install the new one over the old.

## Privacy

- The server token is encrypted on the phone with a key in the Android Keystore,
  and the app is excluded from cloud backup.
- Only HTTPS links are accepted; traffic also runs inside Tailscale.
- The app needs no access to contacts, call logs, SMS or location. Call detection
  watches the audio mode, not your calls.
- Speech recognition uses the phone's built-in Google service by default. For more
  privacy, install Whisper on your server (`sudo bash /opt/buddy/deploy/whisper.sh`)
  and choose Settings → Speech recognition → Your server: the phone then sends short
  recordings only to your own server.

## Signing key (optional)

CI signs builds with a debug key it keeps in its cache, so updates install over
each other. If there are no builds for a week the cache expires, the key
changes, and Android asks you to uninstall before installing the next build
(you then paste the server link again).

To sign with a key only you hold, run this once in Termux on your phone
(`pkg install openjdk-17` first):

```
keytool -genkeypair -keystore buddy.jks -alias buddy -keyalg RSA -keysize 2048 -validity 10000
base64 -w0 buddy.jks > buddy.jks.b64
cat buddy.jks.b64      # long-press to copy for the GitHub secret below
```

In GitHub → the repo → Settings → Secrets and variables → Actions, add
`BUDDY_KEYSTORE_B64` (contents of `buddy.jks.b64`), `BUDDY_KEYSTORE_PASSWORD`,
`BUDDY_KEY_ALIAS` (`buddy`) and `BUDDY_KEY_PASSWORD` (GitHub's Settings pages work in
Chrome on the phone). Keep the base64 text in your password manager. Switching keys
needs one uninstall.

## Building locally

Android Studio (or the Android SDK command-line tools with platform 35), then
from this folder: `./gradlew assembleRelease`. Tests: `./gradlew testDebugUnitTest`.

## Next

- Speaker labels ("who said what") in Listen-in.
- "Hey Buddy" wake word, on the phone.
- Call Co-pilot: live advice on calls made through Buddy's own number.
- Galaxy Watch companion for quiet nudges.
