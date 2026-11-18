<!-- Made by tools/site.mjs from studio/reference.js. Do not edit by hand. -->
# Buddy XR: instructions for Claude Code

Buddy XR apps run on smart glasses. One app must work on glasses with a small text display, glasses with a colour
display, and glasses with only a speaker. The app describes WHAT the wearer should get; the framework decides how.
Never write code for a particular brand or screen size.

## The shape of an app (one file, plain JavaScript, ES module)

```js
import { Alert, Ask, List, Progress, Screen, Stat, Text, defineApp } from "../../src/index.js";

export default defineApp({
  name: "My app",                       // required
  state: { count: 0 },                  // where the app starts; plain data only
  needs: ["mic"],                       // optional: "mic", "camera" or "speaker" if the app cannot work without it
  view(state, ctx) {                    // required: returns ONE Screen for the current state
    return Screen({ title: "My app" },
      Text(`Chosen ${state.count} times.`),
      List(["Choose me", "Or me"], { onSelect: (item, index, ctx) => ctx.set({ count: state.count + 1 }) }));
  },
  onVoice(words, ctx) {},               // optional: words the wearer said that were not a command
  onEvent(event, ctx) {},               // optional: something sent from outside
  onBack(ctx) {},                       // optional: the wearer went back
});
```

## Blocks (the only things a Screen may contain)

- Screen({ title, id?, privacy? }, ...blocks): one screen. Give a different id or title when it is a different screen.
- Text(text, { say?, privacy? }): a sentence. say: true also speaks it on glasses that have a display and a speaker.
- Stat(label, value, { privacy? }): "Label: value" on one line.
- List(items, { onSelect?, privacy? }): items are strings or { label, detail?, value?, privacy? }.
  onSelect(item, index, ctx) makes it something to choose from; item.label is the text.
- Progress(label, fraction): fraction is 0 to 1.
- Ask(question, { yes, no, yesLabel?, noLabel? }): yes and no are functions (ctx) => ...
- Alert(text, { level? }): must not be missed; always spoken where there is a speaker. level is "info" or "warn".
- Image(src, { alt }): always give alt; glasses without pictures get the alt text.

A block may be left out with a condition: `state.news && Alert(state.news)`.

## ctx

- ctx.state: the current state.
- ctx.set(patch) or ctx.set((state) => patch): change the state. The screen is produced again. Never mutate state.
- ctx.say(text): speak now.

## What the wearer can do (already handled; do not re-implement)

Tap or "next": next choice. Swipe back or "previous". Double tap or "select": choose. Long press or "back": onBack.
"yes" and "no" answer an Ask. Any other words go to onVoice.

## Privacy (required)

Mark anything about a person: privacy: "personal" for names with details, addresses, codes, phone numbers, money;
privacy: "health" for anything about someone's health. Personal content is shown in the lens but not read aloud.
Health content is shown only on glasses reached directly from the phone and is never spoken. Do not try to work
around this, and do not put private details into a title or an Alert.

## Rules

1. Import only from "../../src/index.js". No other imports, no network calls, no timers, no DOM, no storage.
2. The file ends with `export default defineApp({ ... })`.
3. Keep text short: a lens may show five lines of about thirty characters.
4. Every screen must make sense when only heard.
5. State is plain data (strings, numbers, booleans, arrays, objects).

## Working in this folder

- In this repository an app imports the framework by path, as above, from `apps/<name>/app.js`. Outside it, the
  import is `"buddy-xr"`.
- Start an app with `node bin/buddyxr.js new <name>`: it creates the app and a test.
- After every change run `node --test` and `node bin/buddyxr.js check <app.js>`. Both must pass before you say
  the app works. The check lists every kind of glasses; an error there is a real failure.
- Test with `memoryHost(CLASSES.text)` and `memoryHost(CLASSES.audio)`: assert the exact lines (`host.text`) and
  the exact sentences (`host.spoken`). See `test/tutorial.test.js` for the pattern.
- To look at an app: `node bin/buddyxr.js sim`, then open the Studio at /studio/.
- Nothing here has run on real glasses. Do not claim that an app works on a device; say it passes the tests and
  the check.
