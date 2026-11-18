# Buddy XR

Write a smart-glasses app once. It adapts to the glasses it runs on: lines in the lens where there is a display,
sentences in the ear where there is not.

An app says **what** to tell the wearer. The framework decides **how**, from what the glasses can do.

```js
import { Ask, List, Screen, Text, defineApp } from "buddy-xr";

export default defineApp({
  name: "Gate change",
  state: { going: false },
  view: (s) => Screen({ title: "Gate change" },
    Text("Flight UA 212 now boards at B14"),
    List(["Walk: 9 min", "Boards: 18:40"]),
    !s.going && Ask("Navigate there?", { yes: (ctx) => ctx.set({ going: true }) })),
});
```

On text-display glasses that is five lines in the lens. On audio glasses the wearer hears "Gate change. Flight UA
212 now boards at B14. Walk: 9 min. Boards: 18:40. Navigate there? Say yes or no."

## Try it

Node 20 or later. There is nothing to install: the framework has no dependencies.

```
cd xr
npm test                 # the framework's tests and Buddy Serve's
npm run sim              # the simulator at http://localhost:8770/simulator/ and the Studio at /studio/
npm run check            # Buddy Serve on every kind of glasses
node bin/buddyxr.js glasses
node bin/buddyxr.js new my-app   # a folder with a working app and its test
```

## What is here

| Path | What it is |
|---|---|
| `src/widgets.js` | The blocks an app is written in: Screen, Text, Stat, List, Progress, Ask, Alert, Image |
| `src/profiles.js` | What each kind of glasses can do: three classes, and nine product families |
| `src/layout.js` | A screen as lines for a display: wrapped, paged, the choice in focus marked |
| `src/speech.js` | A screen as sentences for the ear |
| `src/privacy.js` | What may be shown or spoken, by how private it is |
| `src/runtime.js` | Runs an app on a host: state, focus, paging, input |
| `src/hosts/core.js` | A host for tests, and the message bridge to a native app |
| `src/hosts/evenhub.js` | A host for one maker's glasses, through its plugin system. Not yet run on glasses |
| `src/hosts/mentra.js` | A host on top of MentraOS, an open-source glasses runtime. Not yet run on glasses |
| `examples/mentra-server.js` | Buddy Serve as a MentraOS app server |
| `src/agent.js` | Four tools that let an AI agent show, ask and offer a choice on the glasses |
| `src/check.js` | Runs an app on every kind of glasses and says where it would not work |
| `src/index.d.ts` | Types, for apps written in TypeScript |
| `simulator/` | The simulator |
| `apps/serve/` | Buddy Serve: a waiter says the order, it is checked against the menu, then sent |
| `studio/` | The Studio: write an app in the browser, run it on each kind of glasses, see the check |
| `CLAUDE.md` | What Claude Code reads to build apps here: the blocks, the privacy rule, how to test |
| `templates/github/` | Two GitHub workflows: check every change; let Claude Code build apps on your own account |
| `tools/site.mjs` | Builds the Studio's starting points, CLAUDE.md and the website tutorial from the tested code |
| `bin/buddyxr.js` | The command line: `new`, `check`, `glasses`, `sim` |

The full guide is `docs/BUDDY-XR.md` in this repository.

## What is not here yet

- **No real glasses.** Nothing has run on hardware. The product families carry what the open-source device layer
  ([xg.glass](https://github.com/hkust-spark/xg-glass-sdk), Apache-2.0) declares for its adapters; display sizes
  other than one are defaults, not measurements.
- **Two hosts for real glasses, untested.** `evenHubHost` (one maker's plugin system) and `mentraHost` (on top of
  MentraOS) are written against the published SDKs and tested against stand-ins. Neither has run on a pair.
- **No native host.** `bridgeHost` defines the messages; the Android app that would hold the connection and pass
  them to the glasses is not written.
- **No packaging or publishing** for any maker's store.
- **No speech recognition.** A host supplies the wearer's words as text; the simulator takes them typed.
- **Buddy Serve has no point of sale.** The demo kitchen keeps orders in memory.
