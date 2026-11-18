# Buddy XR: one app, any glasses

Buddy XR is a framework for smart-glasses apps, in the `xr/` folder. An app is written once, in JavaScript or
TypeScript, and adapts to the glasses it runs on. It is to glasses what a cross-platform toolkit is to phones:
the app describes what the wearer should get, and the framework works out how on each device.

**State on 18 November 2026:** the framework, a simulator, a check tool and one app (Buddy Serve) exist and are
tested. Nothing has run on real glasses, and there is no publishing to any store. See "What is not built" below.

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

## 6. How it reaches real glasses (designed, not built)

The framework runs wherever JavaScript does. A **host** connects it to a device: it shows a frame, speaks a
sentence and reports input. Three hosts are planned:

1. **A phone app** holding the connection to the glasses through the open-source device layer xg.glass
   (Apache-2.0), which already has adapters for several makers. The framework runs in a web view and the two
   exchange JSON (`bridgeHost` in `src/hosts.js` defines the messages).
2. **A maker's plugin system**, where the maker's own app runs web apps.
3. **On the glasses themselves**, for glasses that run apps.

Each maker's store has its own account, packaging and review. The framework can prepare a package; the developer
submits it.

## What is not built

- Any host for real glasses; nothing has run on hardware.
- Packaging and publishing for any store.
- Speech recognition and speech output (hosts supply them).
- Measured display sizes: only one family's line count comes from its adapter's source; the rest are defaults.
- A point of sale behind Buddy Serve.
- The cloud services and billing discussed as the business model.
