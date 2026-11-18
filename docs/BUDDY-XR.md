# Buddy XR: one app, any glasses

Buddy XR is a framework for smart-glasses apps, in the `xr/` folder. An app is written once, in JavaScript or
TypeScript, and adapts to the glasses it runs on. It is to glasses what a cross-platform toolkit is to phones:
the app describes what the wearer should get, and the framework works out how on each device.

**State on 18 November 2026:** the framework, a simulator, a check tool, tools for AI agents, one app (Buddy
Serve) and one host for real glasses exist and are tested here. Nothing has run on real glasses, and there is no
publishing to any store. See "What is not built" below.

**It is not the only one.** "One app on any glasses" is already offered by others (section 8). What this
framework does that we did not find elsewhere is in sections 3 and 6: privacy rules the app cannot switch off,
and tools for AI agents that report what actually reached the wearer.

## 1. The idea

Glasses on sale differ in what they can do:

| Class | What it has | What an app's screen becomes |
|---|---|---|
| Audio glasses | Speaker, microphone, camera. No display. | Sentences, read to the wearer |
| Text-display glasses | A few lines of one-colour text | Wrapped lines, cut into pages |
| Colour-display glasses | A colour display with images, speaker, camera | The same lines, styled, with pictures |

An app does not ask which glasses it is on. It returns a screen made of blocks, and the framework lays the same
screen out for the device.

## 2. Writing an app

```js
import { Alert, Ask, List, Screen, Stat, Text, defineApp } from "buddy-xr";

export default defineApp({
  name: "My app",
  state: { count: 0 },                       // where it starts
  view(state, ctx) {                         // the screen for a state
    return Screen({ title: "Counter" },
      Text(`Count is ${state.count}`),
      List(["Add one", "Reset"], { onSelect: (item, i, ctx) => ctx.set({ count: i === 0 ? state.count + 1 : 0 }) }));
  },
  onVoice(text, ctx) {},                     // words that were not a command
  onEvent(event, ctx) {},                    // something from outside: run.notify(event)
  onBack(ctx) {},                            // the wearer went back
  needs: ["mic"],                            // what it cannot work without
});
```

`ctx.set(patch)` changes the state and the screen is drawn again. `ctx.say(text)` speaks at once. `ctx.device` is
the profile, for the rare case an app must know.

### The blocks

| Block | On a display | In the ear (audio glasses) |
|---|---|---|
| `Screen({ id, title }, ...)` | The first line | Said first |
| `Text(text, { say })` | Wrapped lines | Said |
| `Stat(label, value)` | `Label: value` | Said |
| `List(items, { onSelect })` | One line each; the one in focus marked `›`; a `value` sits at the right edge | "3 choices. 1 of 3: ..." and, on moving, just the new one |
| `Progress(label, 0..1)` | `Label 60%` (a bar on colour displays) | "Label, 60%" |
| `Ask(question, { yes, no })` | The question and two choices | The question, then "Say yes or no." |
| `Alert(text, { level })` | A highlighted line | Always spoken where there is a speaker |
| `Image(src, { alt })` | The picture where the display shows them; `alt` elsewhere | `alt` |

On glasses with both a display and a speaker, only alerts, questions and text marked `say: true` are spoken. The
rest is in the lens.

### What the wearer does

| The wearer | Means |
|---|---|
| Tap, or swipe forward | Next (pages forward until the choices are in view, then moves between them) |
| Swipe back | Previous |
| Double tap | Choose |
| Long press | Back |
| Says "next", "previous", "select", "back", "yes", "no" | The same |
| Says anything else | Goes to the app's `onVoice` |

A screen always opens on its first page, so it is read from the top.

## 3. Privacy is in the framework

Any block (or a whole Screen) can be marked `privacy: "personal"` or `"health"`. The framework applies the rule
before anything reaches the glasses, and an app cannot switch it off.

| Level | Shown | Spoken |
|---|---|---|
| `public` (default) | Yes | Yes |
| `personal` | Yes: only the wearer sees the lens | Only if the wearer chose it (`speakPersonal`), because people nearby hear an open-ear speaker |
| `health` | Only on glasses reached directly from the phone, not through a maker's app | Never |

One route is stricter. Where everything passes through a third party's cloud (as on the MentraOS host, section
7), personal content is neither shown nor spoken unless whoever deploys the app accepts that route
(`allowCloud`), and health content is never sent.

What is held back is replaced with "Private. Open your phone." or "You have a private update. Check your phone.",
so the wearer knows something is there.

This rule decides what the framework sends. It cannot see what a maker's app or service does with content after
that, which is why health content is kept off glasses reached that way.

## 4. Testing without glasses

- **The simulator** (`npm run sim`): pick the glasses, see the lens, read what is said, tap, swipe or type what
  the wearer says, and send events from outside. It draws what the framework sends; sizes and colours are
  approximate.
- **The check** (`node bin/buddyxr.js check app.js`): runs the app on every profile and reports where it fails
  (nothing to show or say with, something to choose and no way to choose it, a missing camera) and where
  something is lost (a picture with no `alt`, private lines held back, a screen that takes several pages).
- **In tests**: `memoryHost(profile)` keeps every frame and sentence; `host.text` is the lens as text.

## 5. Buddy Serve

`xr/apps/serve/app.js`. A waiter says a table, then the order. It is matched against the menu:

- everything on the menu: the order is shown (or read back) with its total, and sent to the kitchen only when the
  waiter says yes;
- something not on the menu: "pancakes? Not on the menu. Order not sent." Nothing reaches the kitchen;
- a guest's allergy is marked personal: shown in the lens and sent to the kitchen, but not read out over the
  dining room;
- the floor (food ready, a guest wants the check) arrives as alerts, for that waiter's tables only, one at a time.

The order is read by fixed rules (quantities, menu names and their other names); no AI model is involved. The
kitchen is whatever `send(order)` is given: the demo keeps orders in memory, and there is no point of sale behind
it yet.

## 6. For AI agents

An agent does not write an app. It calls four tools (`AGENT_TOOLS` in `src/agent.js`, in the name, description
and input-schema shape that tool-calling models and the Model Context Protocol use):

| Tool | What it does | What comes back |
|---|---|---|
| `glasses_describe` | Says what the wearer's glasses can do | Display size or none, speaker, microphone, camera, input |
| `glasses_show` | A title, some lines, optionally an alert | What reached the wearer |
| `glasses_ask` | A yes-or-no question | The answer, and what reached the wearer |
| `glasses_choose` | A short list | The choice, and what reached the wearer |

```js
const session = agentSession(host, { name: "Concierge", onSay: (words) => sendToAgent(words) });
await session.call("glasses_show", { title: "Gate change", lines: ["UA 212 boards at B14"], alert: "Boarding in 20 min" });
const { answer } = await session.call("glasses_ask", { question: "Navigate there?" });
```

- The agent never formats for a device. The same call is lines in a lens or sentences in the ear.
- Each call may carry `privacy`. The framework applies the same rule as for an app, and the result says so:
  `{ shown: false, spoken: true, withheld: 1 }` means the wearer was only told to check their phone. An agent is
  not left believing the wearer saw something that was held back.
- Words the wearer says that are not a command go to `onSay`, for the agent.
- A question nobody answered is cancelled when the agent shows something else, the wearer goes back, or the
  session stops.

What is not here: a network server. `agentSession` runs in the same program as the host. Serving these tools
over the Model Context Protocol to an agent running elsewhere is a small wrapper that is not written yet.

## 7. How it reaches real glasses

The framework runs wherever JavaScript does. A **host** connects it to a device: it shows a frame, speaks a
sentence and reports input.

**Written, not yet run on glasses:** `evenHubHost` (`src/hosts/evenhub.js`), for one maker's glasses through its
plugin system, where a web page runs inside the maker's phone app. It is written against the maker's published
SDK and tested against a stand-in for it. It draws one text box over the whole 576 by 288 canvas and updates its
text; click, double click, scroll and long press become next, choose, previous and back. Three things are open:
how many lines and characters really fit (the font is not fixed-width, so the profile's numbers are estimates),
packaging it the way the maker's store wants, and the wearer's words, because the bridge delivers audio and not
text (pass `transcribe` to supply speech-to-text; without it, input is touch only).

**Written, not yet run on glasses:** `mentraHost` (`src/hosts/mentra.js`), on top of MentraOS, an open-source
glasses runtime (MIT). One host reaches every pair MentraOS supports. It is written against the published SDK;
the calls it makes are compiled against the SDK's own types, and it is tested against a stand-in for a session.

- A MentraOS app is a server that MentraOS's cloud calls when the wearer opens the app.
  `examples/mentra-server.js` is Buddy Serve as such a server. Running it needs a MentraOS developer account, a
  registered app with its key, and supported glasses.
- The profile is built from what the session reports for the wearer's glasses (display lines, speaker,
  microphone, camera, buttons), so those are the device's own figures. Characters a line is not reported and is
  a default.
- The wearer's words arrive as text, because MentraOS transcribes them: voice input works on this host.
- On glasses with one button, a short press is next and a long press chooses; back is by voice.
- Everything shown, said and heard passes through MentraOS's cloud. That is why this host's route is "cloud" and
  the stricter privacy rule applies. In the example, a guest's allergy is not sent to the glasses unless
  `ALLOW_CLOUD=yes` is set.

**Written, not yet run on glasses:** `haloHost` (`src/hosts/halo.js`), for Brilliant Labs Halo. These glasses are
reached from the phone over Bluetooth with nothing in between, so the route is "direct": personal details may be
shown, and health details too. It is written against the maker's developer kit (`brilliant_sdk`, BSD-3-Clause).

- The host copies a small script to the glasses as `buddy_xr.lua` and starts it. It does not replace `main.lua`,
  the glasses' own start-up script. The script draws the lines it is sent and reports the button and taps.
- A whole screen travels as one message, split into packets and acknowledged the way the kit does it.
- One press or tap moves to the next choice, two choose, and a long press or three taps go back.
- The display is round, 256 by 256 points, and the glasses' font is fixed-width and ASCII only. The host keeps
  the text inside the circle (10 lines of 26 characters, or 6 of 13 with `large: true`) and replaces signs the
  font lacks. How readable that is through the lens is not known until it is tried on a pair.
- It neither speaks nor listens yet. Pass `say` to speak another way, such as through the phone, and call
  `heard(text)` with words recognised elsewhere. No camera yet.
- Tests: `test/halo.test.js` uses a stand-in for the kit's Bluetooth class. `test/halo-lua.test.js` runs the
  script itself in a Lua interpreter against a stand-in for the device, with the packets the host really sends.
  It needs `npm install --no-save fengari@0.1.4` and is skipped without it. It has not been run in the maker's
  own Halo emulator.

```js
import { BrilliantBle } from "brilliant-ble";          // the maker's kit, in a page opened in Chrome
const ble = new BrilliantBle();
await ble.connect();                                   // the browser asks which glasses
runApp(app, haloHost(ble));
```

**Planned:**

1. **A phone app** holding the connection to the glasses through the open-source device layer xg.glass
   (Apache-2.0), which already has adapters for several makers. The framework runs in a web view and the two
   exchange JSON (`bridgeHost` in `src/hosts.js` defines the messages).
2. **A maker's plugin system**, where the maker's own app runs web apps.
3. **On the glasses themselves**, for glasses that run apps.

Each maker's store has its own account, packaging and review. The framework can prepare a package; the developer
submits it.

## 8. What already exists (looked up on 18 November 2026)

| Who | What they offer | How they earn |
|---|---|---|
| MentraOS (Mentra) | Open-source runtime, SDK and app store: "one app that runs on any pair of smart glasses". Reported $8M seed (2025), claims 4,000+ developers. A community server lets an agent show text on its glasses | The store: developer subscriptions and app sales; its own glasses |
| xg.glass | Open-source device layer: one API for display, audio, camera and microphone across several makers | Not a business |
| Extentos | A server for AI coding agents that helps them add glasses features to phone apps, with simulation | Not found |
| Each maker's own SDK | Apps for that maker's glasses only | Their store |
| Enterprise vendors (RealWear, Vuzix and software partners) | Rugged glasses with remote-assist and work-instruction software | Hardware plus software licences, by quote |

Sources: press coverage of Mentra's funding and MentraOS 2.0, the projects' own listings, and directory entries.
Figures are as reported, not checked by us.

What we did not find in any of them, and what this framework has:

- **Privacy the app cannot switch off**, decided by how private the content is and how it would reach the wearer
  (lens or ear, direct or through a maker's app).
- **An answer to "did the wearer actually get it?"** for an agent.
- **Audio-only glasses treated as equal**: the same screen is a spoken script, not a missing display.
- **A check that runs an app on every kind of glasses** and names what would be lost.

We looked at public pages only; any of these may exist inside those products without being documented.

## What is not built

- Anything run on real glasses. Three hosts are written and untested on hardware.
- A page or phone app that connects Halo and runs an app on it. The host exists; nothing opens the connection yet.
- Packaging and publishing for any store.
- Speech recognition and speech output (hosts supply them).
- Measured display sizes: only one family's line count comes from its adapter's source; the rest are defaults.
- A point of sale behind Buddy Serve.
- A server that offers the agent tools over the network.
- The cloud services and billing discussed as the business model.
