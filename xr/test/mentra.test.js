import test from "node:test";
import assert from "node:assert/strict";
import { agentSession, allowed, mentraHost, profileFromCapabilities, runApp } from "../src/index.js";
import { demoServe } from "../apps/serve/app.js";

// what MentraOS's SDK (2.1.29) reports for two of the glasses it supports
const G1 = { modelName: "Even Realities G1", hasCamera: false, camera: null, hasDisplay: true,
  display: { count: 2, isColor: false, color: "green", canDisplayBitmap: true, resolution: { width: 640, height: 200 }, maxTextLines: 5 },
  hasMicrophone: true, microphone: { count: 1 }, hasSpeaker: false, speaker: null, hasButton: false, button: null };
const LIVE = { modelName: "Mentra Live", hasCamera: true, camera: {}, hasDisplay: false, display: null, hasMicrophone: true, microphone: { count: 1 },
  hasSpeaker: true, speaker: { count: 1, isPrivate: false }, hasButton: true, button: { count: 1, buttons: [{ type: "press", events: ["press"] }] } };

/** Stands in for a MentraOS session: records what the app did and lets a test act as the wearer. */
function fakeSession(capabilities) {
  const on = {}, did = [], stopped = [];
  const sub = (name) => (handler) => { on[name] = handler; return () => stopped.push(name); };
  return { capabilities, did, stopped, on,
    events: { onTranscription: sub("words"), onButtonPress: sub("button"), onTouchEvent: sub("touch") },
    layouts: { showTextWall: (text) => did.push(["show", text]) },
    audio: { speak: async (text) => { did.push(["speak", text]); return { success: true }; } },
    camera: { requestPhoto: async () => ({ mimeType: "image/jpeg", size: 3 }) } };
}

test("the profile is what the session says the glasses can do", () => {
  const g1 = profileFromCapabilities(G1), live = profileFromCapabilities(LIVE);
  assert.equal(g1.name, "Even Realities G1 (through MentraOS)");
  assert.deepEqual(g1.display, { kind: "text", lines: 5, cols: 36, images: false, proportional: true });
  assert.deepEqual([g1.speaker, g1.mic, g1.camera, g1.input, g1.route], [false, true, false, [], "cloud"]);
  assert.equal(live.display, null);
  assert.deepEqual([live.speaker, live.speakerPrivate, live.mic, live.camera, live.input], [true, false, true, true, ["tap", "double"]]);
  assert.equal(profileFromCapabilities(null).display, null);            // a session with no report yet: nothing assumed
  assert.equal(profileFromCapabilities(G1, { cols: 28 }).display.cols, 28);
});

test("on display glasses the app is a text wall, and the wearer's words drive it", async () => {
  const session = fakeSession(G1);
  const host = mentraHost(session);
  const run = runApp(demoServe().app, host);
  assert.deepEqual(session.did, [["show", "Buddy Serve 1/2\nSay a table number to start an\norder.\n› Table 1 · 2 guests\n  Table 2 · 4 guests"]]);
  session.on.words({ text: "table two", isFinal: false });              // still being said: ignored
  assert.equal(run.state.screen, "home");
  session.on.words({ text: " Table two. ", isFinal: true });
  assert.equal(session.did.at(-1)[1], "Table 2\nSay the order.");
  session.on.words({ text: "two lattes", isFinal: true });
  assert.ok(session.did.at(-1)[1].includes("2× Latte  $9.00") && session.did.at(-1)[1].includes("Send to the kitchen?\n› Send"));
  session.on.touch({ gesture_name: "forward_swipe" });                  // next: the other choice, on the next page
  assert.ok(session.did.at(-1)[1].endsWith("2/2\n› Start again"));
  session.on.touch({ gesture_name: "backward_swipe" });
  assert.ok(session.did.at(-1)[1].includes("› Send"));
  const shown = session.did.length;
  session.on.touch({ gesture_name: "something_new" });                  // a gesture we have no meaning for: ignored
  assert.equal(session.did.length, shown);
  session.on.words({ text: "yes", isFinal: true });
  await new Promise((r) => setTimeout(r, 0));
  assert.equal(run.state.screen, "sent");
  assert.equal(session.did.filter((d) => d[0] === "speak").length, 0);  // these glasses have no speaker
});

test("on audio glasses with one button: short press is next, long press chooses", () => {
  const session = fakeSession(LIVE);
  const run = runApp(demoServe().app, mentraHost(session));
  assert.deepEqual(session.did, [["speak", "Buddy Serve. Say a table number to start an order. 3 choices. 1 of 3: Table 1, 2 guests."]]);
  session.on.button({ buttonId: "main", pressType: "short" });
  assert.deepEqual(session.did.at(-1), ["speak", "2 of 3: Table 2, 4 guests."]);
  session.on.button({ buttonId: "main", pressType: "long" });
  assert.equal(run.state.table, 2);
  assert.deepEqual(session.did.at(-1), ["speak", "Table 2. Say the order."]);
});

test("through a third party's cloud, personal details are held back unless the deployer accepts that route", async () => {
  const p = profileFromCapabilities(G1);
  assert.equal(allowed("personal", "display", p), false);
  assert.equal(allowed("personal", "display", p, { allowCloud: true }), true);
  assert.equal(allowed("health", "display", p, { allowCloud: true }), false);      // never, whatever is accepted
  assert.equal(allowed("personal", "speech", profileFromCapabilities(LIVE), { allowCloud: true }), false);   // still not said aloud
  assert.equal(allowed("public", "display", p), true);

  const order = (options) => { const s = fakeSession(G1); const run = runApp(demoServe().app, mentraHost(s), options);
    s.on.words({ text: "table one", isFinal: true }); s.on.words({ text: "a latte, peanut allergy", isFinal: true }); return [s, run]; };
  const [closed, a] = order({});
  assert.ok(closed.did.at(-1)[1].includes("Private. Open your phone.") && !closed.did.some((d) => d[1].includes("peanut")));
  assert.equal(a.withheld.display, 1);
  const [open] = order({ allowCloud: true });
  assert.ok(open.did.at(-1)[1].includes("Allergy: peanut"));

  const agent = agentSession(mentraHost(fakeSession(G1)));
  assert.deepEqual(await agent.call("glasses_show", { title: "Reminder", lines: ["Evening tablet"], privacy: "health" }), { shown: true, spoken: false, withheld: 1 });
});

test("a failing call to the glasses is reported, the camera is asked only where there is one, and close stops listening", async () => {
  const session = fakeSession(LIVE);
  session.audio.speak = async () => { throw new Error("glasses offline"); };
  const problems = [];
  const host = mentraHost(session, { onProblem: (e) => problems.push(e.message) });
  const run = runApp(demoServe().app, host);
  await new Promise((r) => setTimeout(r, 0));
  assert.deepEqual(problems, ["glasses offline"]);
  assert.equal((await host.capture()).mimeType, "image/jpeg");
  await assert.rejects(mentraHost(fakeSession(G1)).capture(), /no camera/);
  host.close();
  assert.deepEqual(session.stopped.sort(), ["button", "touch", "words"]);
  session.on.button({ pressType: "long" });
  assert.equal(run.state.screen, "home");                                // nothing reaches the app after close
});
