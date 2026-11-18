// A host for Even Realities glasses, through the maker's plugin system (Even Hub): a web page that runs inside
// the maker's phone app and draws on the glasses through its bridge. This is the first host for real glasses.
//
// NOT YET RUN ON GLASSES. It is written against the maker's published SDK (@evenrealities/even_hub_sdk 0.0.16,
// MIT) and tested here against a stand-in for its bridge. What is known and what is assumed:
//   known    the bridge calls used (createStartUpPageContainer, rebuildPageContainer, textContainerUpgrade,
//            onEvenHubEvent), the event codes, the 576 by 288 canvas, one container must capture events.
//   assumed  how many lines and characters fit. The glasses' font is not fixed-width and has no size setting,
//            so `lines` and `cols` in the profile are estimates until measured on a pair.
//   missing  the wearer's words. The bridge delivers microphone audio, not text: pass `transcribe` (audio in,
//            text out) to turn it into voice commands. Without it, input is touch only. There is no speaker.
//
// Use:   import { waitForEvenAppBridge } from "@evenrealities/even_hub_sdk";
//        runApp(app, evenHubHost(await waitForEvenAppBridge()));

import { FAMILIES } from "../profiles.js";

// the maker's event codes (OsEventTypeList) -> what the runtime understands
const EVENTS = { 0: "tap", 1: "swipe-back", 2: "swipe-forward", 3: "double", 9: "long",
  CLICK_EVENT: "tap", SCROLL_TOP_EVENT: "swipe-back", SCROLL_BOTTOM_EVENT: "swipe-forward", DOUBLE_CLICK_EVENT: "double", LONG_PRESS_EVENT: "long" };
const CANVAS = { width: 576, height: 288 };
const BOX = { containerID: 1, containerName: "buddy-xr" };

export function evenHubHost(bridge, { profile = FAMILIES["even-g2"], transcribe = null, onProblem = () => {} } = {}) {
  let listener = () => {}, started = false, queue = Promise.resolve(), last = "";
  const content = (f) => [f.title, ...f.lines.map((l) => l.text)].join("\n");

  // one call at a time, in order: a later frame must not overtake an earlier one
  const send = (text) => { queue = queue.then(async () => {
    if (text === last) return;
    if (!started) {
      const result = await bridge.createStartUpPageContainer({ containerTotalNum: 1,
        textObject: [{ ...BOX, xPosition: 0, yPosition: 0, width: CANVAS.width, height: CANVAS.height, content: text, isEventCapture: 1 }] });
      if (result !== 0 && result !== "success") throw new Error(`The glasses refused the page (result ${result}).`);
      started = true;
    } else if (!(await bridge.textContainerUpgrade({ ...BOX, content: text }))) throw new Error("The glasses did not take the update.");
    last = text;
  }).catch((e) => onProblem(e)); return queue; };

  bridge.onEvenHubEvent((event) => {
    if (!event) return;
    const touch = event.textEvent || event.listEvent || event.sysEvent;
    const type = touch && EVENTS[touch.eventType ?? 0];   // the maker omits the code for a plain click
    if (type && !(touch === event.sysEvent && touch.imuData)) listener({ type });
    if (event.audioEvent && transcribe) Promise.resolve(transcribe(event.audioEvent)).then((text) => { if (text) listener({ type: "voice", text }); }).catch(onProblem);
  });

  return { profile,
    show(frame) { send(content(frame)); },
    speak() {},                                          // no speaker on these glasses
    on(l) { listener = l; },
    /** Resolves when everything shown so far has reached the bridge. */
    settled: () => queue,
    /** Start the microphone so `transcribe` receives audio. The page must have been shown first. */
    async listen() { await queue; return transcribe ? bridge.audioControl(true) : false; },
  };
}
