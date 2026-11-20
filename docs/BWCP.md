# Buddy Wearable Contract (BWCP): draft 0.2

Written 20 November 2026. **A draft, not frozen.** Nothing on this page has run on a real device.
Version 0.2 adds the link between the glasses runtime and Buddy's server (section 2.9), which is built.

A proposal asked for a formal protocol between wearable devices and Buddy, named BWCP, made of twelve contracts.
This page does three things:

1. It writes down the contract the code already has (sections 1 and 2). That part is true of the code today.
2. It compares the twelve proposed contracts with the code (section 3).
3. It proposes the smallest additions for what is missing (section 4). **That part is a proposal. None of it is built.**

Section 5 compares the other layers the proposal names with what the repository has, and section 6 lists the
decisions that are the owner's to make.

## 1. The contract in one paragraph

An app never asks which brand of glasses the wearer has. It says what the wearer should get. The framework reads
the device's **profile** (what it can show, say, hear and report), decides how to deliver it, and applies the
privacy rule before anything leaves. An **adapter** (the code calls it a host) is the only part that knows the
device: it carries three commands to the device and reports six events back. The code is in `xr/src/`; the types
are in `xr/src/index.d.ts`.

## 2. What exists today (version 0.1)

### 2.1 The profile (what a device can do)

| Field | Values | Meaning |
|---|---|---|
| `display` | `null`, or `{ kind, lines, cols, images, proportional }` | `kind` is `"text"` or `"color"`. `proportional`: the font is not fixed-width |
| `speaker` | true or false | It can play speech to the wearer |
| `speakerPrivate` | true or false, optional | Only the wearer hears it |
| `mic` | true or false | It can hear the wearer |
| `camera` | true or false | It can take a photo |
| `input` | any of `"tap"`, `"double"`, `"long"`, `"swipe"` | Physical input the adapter reports |
| `route` | `"direct"`, `"maker-app"` or `"cloud"` | How content reaches the device. The privacy rule uses it |
| `id`, `name`, `about`, `source`, `tested` | text; `tested` is false everywhere | Which profile this is and where its facts came from |

Where a profile comes from:

- **Fixed tables** (`xr/src/profiles.js`): three classes (audio, text display, colour display) and ten product
  families. Their facts come from the makers' kits and an open-source device layer, not from measurements. Only one
  family's line count comes from its adapter's source; the rest are defaults.
- **Read from the device**, on one adapter only: `profileFromCapabilities` builds the profile from what a MentraOS
  session reports for the wearer's glasses.

How an app and a device are matched:

- An app may declare `needs: ["mic" | "camera" | "speaker"]`.
- `check(app)` runs the app against every profile and reports where it fails, where something is lost and where
  private lines are held back. `node bin/buddyxr.js check <app.js>` prints it.

### 2.2 Commands (Buddy to the device)

| Command | What it carries |
|---|---|
| `show(frame)` | One screen, already fitted to the display: `{ id, title, lines: [{ text, kind, focused, level?, value? }], page, pages, focusPage, image, withheld }` |
| `speak(text)` | One sentence to say |
| `capture()` | Take a photo. Optional: an adapter without it has no camera |

### 2.3 Events (the device to Buddy)

`{ type: "tap" | "double" | "long" | "swipe-forward" | "swipe-back" }` and `{ type: "voice", text }`.

They are read the same way on every device: tap or swipe forward is next, swipe back is previous, double tap
chooses, long press goes back. The words "next", "previous", "select", "back", "yes" and "no" do the same by
voice; any other words go to the app.

### 2.4 On the wire

The contract is the commands and events above. How they travel is the adapter's business:

- **A native app holding the connection** (`bridgeHost`) exchanges plain JSON:
  to the device side `{ op: "show", frame }`, `{ op: "speak", text }`, `{ op: "capture", id }`;
  from it `{ op: "input", event }`, `{ op: "captured", id, image | error }`.
  The native half is not written.
- **Halo** (`haloHost`) sends a whole screen as one Bluetooth message, split into packets the way the maker's kit
  does it, and receives the button and taps as message codes.
- **MentraOS** (`mentraHost`) and **one maker's plugin system** (`evenHubHost`) call those platforms' own SDKs.

These messages carry no protocol name, no version and no device identity.

### 2.5 Privacy (applied before anything reaches an adapter)

Every piece of content is `public`, `personal` or `health`. An app can mark content; it cannot switch the rule off
(`xr/src/privacy.js`).

| | In the lens | Spoken |
|---|---|---|
| public | Always | Always |
| personal | Yes. On a `cloud` route only if whoever deploys the app accepted that route | Only if the wearer chose it, and on a `cloud` route only if that route was accepted |
| health | Only on a `direct` route | Never |

What is held back is replaced with a fixed line ("Private. Open your phone."), and the count is reported.

### 2.6 For AI agents

Four tools, in the shape tool-calling models and the Model Context Protocol use (`xr/src/agent.js`):
`glasses_describe`, `glasses_show`, `glasses_ask`, `glasses_choose`. Every result says what actually reached the
wearer: `{ shown, spoken, withheld }`. They run inside the framework. No server offers them over a network yet.

### 2.7 Audio, between the phone and Buddy's server only

Talk mode streams the owner's speech from the Buddy phone app to the server (`backend/buddy/liveears.py`):
`{ type: "live", op: "hello" | "start" | "audio" | "end" | "cancel" | "bye" }` with 16 kHz, 16-bit mono audio, and
back `live_state`, `live_partial` and `heard`. It is not connected to any glasses.

### 2.8 Adapters

| Adapter | Reaches | Route | State |
|---|---|---|---|
| `memoryHost` | Nothing: keeps what was shown and said | the profile's | Used by the tests and the check |
| Simulator (`xr/simulator/sim.js`) | A picture of the lens in a browser | the profile's | Works |
| `bridgeHost` | A native app holding the connection | the profile's | The native half is not written |
| `evenHubHost` | One maker's glasses through its plugin system | maker-app | Written, not run on glasses |
| `mentraHost` | Whatever MentraOS supports | cloud | Written, not run on glasses |
| `haloHost` | Brilliant Labs Halo over Bluetooth | direct | Written, not run on glasses. No speech, hearing or camera yet |

### 2.9 The link to Buddy's server (version 0.2, built 20 November 2026)

The glasses runtime joins a Buddy server over one WebSocket, `/ws/glasses`, behind the same access check as the
phone's own connection. The runtime side is `xr/src/link.js`; the server side is `backend/buddy/glasslink.py`.
Tested on both sides and run end to end with the runtime in a browser. Not yet run with real glasses.

| From the runtime | Meaning |
|---|---|
| `{ op: "hello", protocol: "bwcp", version, device: { maker, model, id }, profile }` | The first message: who the device is and what it can do (the profile of 2.1, only its contract fields) |
| `{ op: "result", id, ok, result }` or `{ ..., ok: false, error }` | What a call did, with what reached the wearer: `{ shown, spoken, withheld }`, and the answer or choice |
| `{ op: "state", battery?, charging?, link?, worn? }` | Whatever the glasses report. Carried and kept for the session; no adapter reports it yet |
| `{ op: "said", text }` | The wearer's words that were not a command. Sent; the server does not act on it yet |
| `{ op: "problem", code, about }` | Something went wrong on the device or in the runtime |
| `{ op: "bye", why }` | The session is over |

| From the server | Meaning |
|---|---|
| `{ op: "welcome", version, session }` | The hello was accepted |
| `{ op: "refused", why }` | It was not: the wrong contract or version, or the Devices or Link module is off |
| `{ op: "call", id, tool, args }` | One of the four tools of 2.6, asked for by Buddy's brain |
| `{ op: "cancel", id }` | Nobody answered in time: take the question off the lens |

A side ignores an `op` it does not know. Nothing is assumed about a device that its hello did not say: a missing
capability is absent, and a missing or unknown route is treated as the strictest.

**The privacy rule is applied twice.** The runtime applies all of 2.5. The server applies the part that must hold
even if the runtime were wrong: content that may reach neither the lens nor the ear on the declared route is never
sent (health anywhere but a direct route; personal through another company's cloud).

**Where the runtime runs today.** In a page of the Buddy server, `/glasses.html`, opened in Chrome on the phone:
it can drive a preview lens on the screen, or a Halo over Bluetooth with the maker's own library. It is not inside
the Buddy app yet.

## 3. The twelve proposed contracts against the code

| Proposed contract | Today | Where |
|---|---|---|
| Capability | **Yes** | The profile (2.1). Read from the device on one adapter, fixed tables for the rest |
| Display | **Yes** | `show(frame)`; text, lists, alerts, progress, images with a text alternative |
| Interaction | **Yes** | The six events (2.3) |
| Command | **Yes** | Three commands (2.2) |
| Device identity | Partly | The hello carries maker, model and an id (2.9). No adapter reads a serial number or firmware from the device yet |
| Vision | Partly | One photo with `capture()`, on the bridge and MentraOS adapters. No video frames |
| Event | Partly | Input events, and `problem` on the link (2.9). No sensor events |
| Security | Partly | Privacy by route, checked on both sides of the link (2.5, 2.9), and the server's access check at the door. No per-capability permission from the wearer |
| Audio | No, on glasses | Adapters do not stream audio. The phone-to-server audio in 2.7 exists and could be reused |
| Context | No | The app has its own state and the profile. No location or session context |
| State | Partly | The link carries `state` (2.9). No adapter reports battery or connection yet |
| Lifecycle | Partly | Hello with a version, and goodbye (2.9). Pairing is left to the maker's kit. A dropped link starts again with a new hello; a session is not resumed |

Four exist, six are partly there, two are missing (audio on glasses, context).

## 4. Proposed additions

**Built in version 0.2 (section 2.9):** hello with identity, version and the profile (4.1); state, as a message the link carries (4.2); goodbye (4.3); problems (4.4). **Still proposals:** resuming a session (4.3), audio (4.5), and the wearer's permission for each capability (4.8).

The aim is the smallest set that covers the missing rows, in the style of the bridge messages in 2.4. Rules for
all of them: a side ignores an `op` it does not know and a field it does not know; nothing is assumed about a
device that its `hello` did not say.

**4.1 Hello: identity, version and capabilities in one message**

```json
{ "op": "hello", "protocol": "bwcp", "version": "0.1",
  "device": { "maker": "example", "model": "wearable-01", "id": "a stable id for this unit", "firmware": "1.4.2" },
  "profile": { "display": null, "speaker": true, "mic": true, "camera": true, "input": ["tap"], "route": "direct" } }
```

Buddy answers `{ "op": "welcome", "version": "0.1", "session": "s1" }`, or `{ "op": "refused", "why": "..." }`.
The profile in `hello` is the same object as in 2.1, so nothing new is invented for capabilities. A device that
changes (a clip-on display attached) sends `hello` again.

**4.2 State.** `{ "op": "state", "battery": 0.62, "charging": false, "link": "good" | "weak" | "lost", "worn": true }`.
Every field is optional: a device reports what it knows. The framework would use it for two things only: to say
"your glasses are nearly empty", and to stop believing the wearer saw something after the link was lost.

**4.3 Lifecycle.** `{ "op": "bye", "why": "off" | "low-battery" | "out-of-range" }`, and a `hello` carrying the
previous `session` to continue where the wearer was. Pairing and firmware upgrades stay with the maker's own kit:
Buddy does not replace them.

**4.4 Problems.** `{ "op": "problem", "code": "display-failed" | "capture-failed" | "busy" | "unsupported", "about": "..." }`,
replacing each adapter's own `onProblem`.

**4.5 Audio.** Reuse the Talk-mode messages in 2.7 rather than define a second audio protocol: `start`, `audio`,
`end`, `cancel` towards Buddy, and `heard` back. One addition is needed for interruption: `{ "op": "hush" }`, to stop
what is being spoken when the wearer starts talking.

**4.6 Vision.** Keep `capture` as it is. Video frames are left out until a use needs them: no app here uses them,
and a camera running in a room full of people is a legal question before it is a technical one.

**4.7 Context.** No new device messages. Who the wearer is and what session this is are known to Buddy, not to the
device. Location comes from the phone, under the rule Buddy already has (search services get an area label, never
coordinates).

**4.8 Security.** Three parts, two of which exist. The privacy rule (2.5) stays as it is. Who may connect is the
transport's job: Bluetooth pairing on the phone, and Buddy's existing access check for anything that reaches the
server. The new part is the wearer's permission for each capability: an app's `needs` would be shown to the wearer
once ("this app wants the camera") and refused capabilities removed from the profile the app sees.

What this draft deliberately does not propose: a certification programme, a registry of devices, or an SDK for
makers. Each needs a device that has run first.

## 5. The other layers in the proposal, against the repository

| Name in the proposal | What the repository has | Gap |
|---|---|---|
| Wearable connectivity | The adapters in 2.8 | No discovery or pairing of Buddy's own; none run on hardware |
| Contract and protocol layer | Sections 1 and 2 of this page | Section 4 |
| Wearable intelligence runtime | Buddy's conversation on the server (Claude with tools), and live hearing for calls and Talk mode in up to ten languages | **Not connected to the glasses framework.** The glasses side runs in a browser or a phone; Buddy's brain runs on the server; nothing joins them |
| "MasterOS": identity, privacy, security, policy | The Privacy Gateway (`docs/PRIVACY-GATEWAY.md`), `docs/SECURITY.md`, `docs/ACCESS_CONTROL.md` | The name is not in the repository. Everything is built for one owner |
| "Maestro": agent and workflow orchestration | **A different thing with the same name.** Here Maestro is the gateway for AI models: which provider does which job, what each may see, what it cost (`docs/MAESTRO.md`) | The names clash. See section 6 |
| "Central AI Orchestra": models, speech, vision | Closest to what the repository calls Maestro, plus the speech services used by calls and Talk mode | The name is not in the repository |
| Buddy Intelligence | Memory and the Intelligence Platform (`docs/INTELLIGENCE.md`) | Personal to one owner |
| Universal integration fabric | Connectors, read-only by rule (`docs/CONNECTORS.md`); Zapier through MCP, where anything that changes something waits for a yes (`docs/ZAPIER.md`); Buddy Connect for outside services and their keys | No contract for writing to a business system. No point of sale, CRM or ERP is connected |

Two gaps are larger than the protocol:

- **One owner.** Buddy has one owner, one memory and one set of keys. Serving a business means many wearers,
  roles, and one company's data kept apart from another's. None of that exists.
- **No real device.** Display sizes are mostly defaults, speech on glasses is not written, and no adapter has met
  hardware. A contract frozen before that would be frozen around guesses.

## 6. Decisions

The owner decided the architecture on 20 November 2026. The record is `docs/PLATFORM.md`. What it settles for this
page:

- BWCP is the name for the contract the glasses framework already has, not a second thing beside Buddy XR.
- Maestro stays the gateway for AI models.
- Version 1 is glasses only. Rings and watches stay connectors (data sources); their data is health data, and the
  standing rule sends health information to no AI model.
- Healthcare and medical records are out of scope.

Still open:

1. **When this contract becomes 1.0.** Proposed: after one device has run one app end to end. Until then it stays
   a draft, and the rest of section 4 stays a proposal.
2. **Who may see this page.** It is in the repository only. Nothing here is on the website.
