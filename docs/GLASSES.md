# Smart glasses: Buddy on Brilliant Labs Halo (design, parked)

Status: **design only, nothing built.** Parked to revisit later. Decision still open: Kotlin (Android only) or
Flutter (Android + iPhone) for the phone side (see "Open decisions").

Mockups:
- "Buddy on Halo" canvas (personal Buddy): https://claude.ai/artifact/Btcqub1JN6GLMaJXLnkV2u
- "Buddy Serve & Buddy Watch on Halo" canvas: https://claude.ai/artifact/YZawpGyhPHt9oem3rDYLhH

## Why Halo

Brilliant Labs Halo has the camera, microphone, speakers, a small colour display, taps and a motion sensor, and it is
open. Its SDK is BSD-3 (commercial use allowed). Brilliant's firmware code is Apache-2.0; the Alif chip SDK parts
are licensed for Alif silicon only, which only matters if we change the firmware. Apps connect directly over Bluetooth
with no Noa app, Brilliant account or cloud. Price: about $299–349.

Facts from `brilliantlabsAR/halo-firmware` (PROTOCOL.md, BLE_SERVICES.md, LUA_RUNTIME.md) and `brilliant_sdk`:
- Chip: Alif Balletto B1 (Cortex-M55, BLE 5.3, Ethos-U55 NPU). Zephyr RTOS. Lua 5.4 on the glasses.
- Pairing: out of the box it bonds with the first phone that connects; up to 5 bonds, one connection at a time;
  new phones need the pairing window (hold the button 5 s, about 60 s window). PAIRING.md was a dead link.
- Camera: `frame.camera.capture({resolution = 640, quality = "HIGH"})`, JPEG. A video characteristic is listed in
  BLE_SERVICES.md but not in PROTOCOL.md (to test).
- Mic: `frame.microphone.start{encoder="pcm", sample_rate=8000, ...}`, or LC3 / LE Audio on the audio channel.
- Speaker: the phone streams PCM or LC3 to the Audio RX characteristic; the glasses hold about 8 KB, so pace it.
- Scripts persist in LittleFS (`/lfs/main.lua` runs at boot). Firmware updates over MCUboot (SMP), signed with the
  public development key by default.
- SDK languages: Python, Flutter (Android and iOS), Web Bluetooth. No Kotlin or Swift SDK.
- Not yet known: display size and colour depth, real frame rate, battery while streaming.

## First use cases (personal Buddy)

1. Tap the glasses, ask Buddy, hear the answer in the glasses, see a short card.
2. Glance cards Buddy sends on its own: next meeting, leave now, urgent email, packages, flights, reminders.
3. Calls Buddy answers for you, shown live (tap to take, shake to let Buddy handle).
4. "Needs your yes" on the lens, answered with a spoken yes (money always needs the spoken yes).
5. Later, with the camera: "What's this?", read, translate, scan a receipt, bill or business card into the library.
6. Later: restaurants and events while out, health nudges, live look (about 1 frame a second).

## Design

```
Halo: Brilliant firmware (unchanged) + our main.lua
  cards · taps · mic on/off · audio out · photo · battery
      ⇅ Bluetooth: Lua data channel + audio channels
Phone: Buddy app, new "halo" package
  HaloLink · HaloEars (a Hearing) · HaloVoice · LensCards · HaloScreen
      ⇅ existing /ws over Tailscale, "via": "glasses"
Server: same Conversation and tools; new glasses.py card maker; short-answer note for via="glasses"
```

Glasses (`main.lua`): single tap talk or stop, double tap photo (later), long press cancel; draws cards and clears
them after `ttl`; shows Listening / Thinking / speaking; starts and stops the mic; plays streamed audio; reports
battery.

Our messages on the Lua data channel (1 type byte + small JSON or binary):

| Phone → glasses | Glasses → phone |
|---|---|
| `0x10 CARD {k,t,l[],tone,ttl}` | `0x01 TAP {single\|double\|long}` |
| `0x11 CLEAR` | `0x04 BATTERY {pct, charging}` |
| `0x12 STATUS {listening\|thinking\|speaking\|idle}` | `0x05 READY {script version}` |
| `0x20 MIC {on\|off}` | `0x06 ERROR {what}` |
| `0x30 PHOTO {size}` (later) | `0x31 PHOTO_CHUNK` (later) |

Card: `{"k": "Mail · 2 new", "t": "Sam: offsite moved to 10 AM", "l": ["Reply 'works for me'?"], "tone":
"info|need|alert", "ttl": 8}`.

Phone (Android, Kotlin option):
- HaloLink: scan by the Lua service UUID, pair, remember, reconnect (Nordic Android BLE Library, BSD-3), install
  `main.lua`, frame messages to the MTU.
- HaloEars: implements the app's `Hearing`; Halo mic 8 kHz → 16 kHz, SileroVad for end of speech, clips sent the
  same way as ServerEars (Whisper on the server).
- HaloVoice: Android TTS to PCM, resampled, streamed in 20 ms pieces about 150 ms ahead (8 KB buffer). liblc3
  (Apache-2.0) only if raw PCM is too much for Bluetooth.
- LensCards: say → gist card, nudge → glance card, approval → amber "Needs your yes", cards → first result.
- HaloScreen: Connections → Halo glasses: pair or forget, battery, test card, what may show on the lens.
- Flow: IDLE → tap → LISTENING → THINKING → SPEAKING → IDLE; tap while speaking stops and listens; long press
  cancels; glasses lost → phone mic and speaker.

Server:
- `via="glasses"`: 1–3 short spoken sentences, no lists or links, long details sent to the phone.
- `glasses.py`: builds the card in code (kicker from the tool used, title from the first sentence, tone), sent as a
  `card` field on say, nudge and approval. Proactive nudges follow the lens switches and quiet rules.

## Privacy

- Mic only after a tap ("Listening…" on the lens); photos only on a double tap; nothing kept unless "save it".
- Only our server and Claude see anything (Buddy's personal-data rule); nothing goes to Brilliant.
- Pairing only through our app. Before any team or restaurant use: our own MCUboot signing key and a camera/mic
  light in our firmware.
- Nothing personal stored on the glasses; cards time out; money and health cards show titles only.
- No face recognition. Privacy policy gets a "Halo glasses" row before launch.

## Build order (about 2 weeks for 1–4)

1. Link: pairing without Noa, test card, tap, battery; measure display, mic quality, photo frame rate (2–3 days).
2. Talk: tap, ask, answer in the glasses, gist card (3–4 days).
3. Glance cards with quiet rules (2 days).
4. Approvals by voice, docs, privacy policy, tests (1–2 days).
5. Camera: "What's this?", scan into the library (about 3 days).

Testing: halo-emulator for `main.lua`; Kotlin unit tests (framing, pacing, card fitting, the flow); pytest (card
maker, via="glasses", quiet rules); a device checklist on the real glasses.

## Open decisions

- **Android only (Kotlin, inside the current app) or Android + iPhone (Flutter "Buddy Glasses" app with Brilliant's
  Flutter SDK)?** Buddy has no iPhone app; Safari can't use Web Bluetooth. iPhone also limits background mic and
  audio. Kotlin if it's just the Fold; Flutter if iPhone users (or Serve and Watch staff) matter.
- Camera resolution above 640 px, and whether the video stream exists.

## Buddy Serve and Buddy Watch (parked, later)

- Restaurant products from the website: Serve (a point of sale worn by servers), Watch (shift oversight for managers).
  Use cases, lens screens, the point-of-view mockups and the shift board are on the Serve & Watch canvas.
- Brand-neutral "Glass Bridge" (cards, say, taps, speech, capabilities) with drivers per brand: Brilliant SDK (Halo),
  self-hosted MentraOS (Even G2, Vuzix Z100, Mentra Live), or the open-source xg-glass SDK (Rokid, RayNeo, INMO…).
- Self-hosted open-source AI: faster-whisper or Parakeet, Piper or Kokoro, openWakeWord, Qwen or Llama for orders
  (or Claude under zero data retention).
- Table number: from the seating system and the server saying it, confirmed on the lens; NFC tags optional.
- Watch video: worn glasses at about 1 frame a second to a Watch box in the restaurant (Frigate, YOLO, OCR), events
  only, no audio, faces blurred, team-level not per person; fixed kitchen cameras if needed; Wi-Fi glasses (Mentra
  Live, Rokid, RayNeo X3 Pro) if Halo's rate or battery isn't enough.
- Glasses with display, camera, speaker and mic: RayNeo X3 Pro (~$1,099), INMO Air3 ($1,099), Rokid Glasses
  ($499–599), Halo ($299–349). Prices from public listings; get quotes.
- Privacy and law: consent for audio (tap-to-talk only), allergies deleted after the visit, no dining-room camera, no
  face recognition (BIPA), staff notice (NY, CT, DE), short retention; a lawyer per state before a pilot.
