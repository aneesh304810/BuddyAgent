import test from "node:test";
import assert from "node:assert/strict";
import { AGENT_TOOLS, CLASSES, FAMILIES, agentSession, evenHubHost, memoryHost, runApp } from "../src/index.js";
import { demoServe } from "../apps/serve/app.js";

/** Stands in for the maker's bridge: records the calls and lets a test send events the way the glasses would. */
function fakeBridge({ refuse = false } = {}) {
  const calls = [];
  let handler = () => {};
  return { calls, emit: (e) => handler(e),
    async createStartUpPageContainer(c) { calls.push(["create", c]); return refuse ? 1 : 0; },
    async textContainerUpgrade(c) { calls.push(["update", c]); return true; },
    async audioControl(on) { calls.push(["audio", on]); return true; },
    onEvenHubEvent(cb) { handler = cb; return () => {}; } };
}

test("the plugin host creates one full-canvas text box, then only updates its text", async () => {
  const bridge = fakeBridge();
  const host = evenHubHost(bridge);
  const run = runApp(demoServe().app, host);
  await host.settled();
  assert.equal(bridge.calls.length, 1);
  const [op, page] = bridge.calls[0];
  assert.equal(op, "create");
  assert.equal(page.containerTotalNum, 1);
  assert.deepEqual({ ...page.textObject[0], content: "" }, { containerID: 1, containerName: "buddy-xr", xPosition: 0, yPosition: 0, width: 576, height: 288, content: "", isEventCapture: 1 });
  assert.equal(page.textObject[0].content, "Buddy Serve\nSay a table number to start an order.\n› Table 1 · 2 guests\n  Table 2 · 4 guests\n  Table 4 · 3 guests");

  bridge.emit({ textEvent: { containerID: 1 } });                      // a plain click arrives with no event code
  bridge.emit({ textEvent: { eventType: 3 } });                        // double click: choose
  await host.settled();
  assert.equal(run.state.table, 2);
  assert.deepEqual(bridge.calls.slice(1).map((c) => c[0]), ["update", "update"]);
  assert.deepEqual(bridge.calls.at(-1)[1], { containerID: 1, containerName: "buddy-xr", content: "Table 2\nSay the order." });
  bridge.emit({ sysEvent: { eventType: "LONG_PRESS_EVENT" } });        // back
  bridge.emit({ sysEvent: { eventType: 10 } });                        // the release is not an input
  bridge.emit({ sysEvent: { eventType: 8, imuData: { x: 1 } } });      // nor is a motion report
  bridge.emit(null);
  await host.settled();
  assert.equal(run.state.screen, "home");
});

test("a value is not lined up with spaces where the font is not fixed-width", async () => {
  const bridge = fakeBridge();
  const host = evenHubHost(bridge);
  const run = runApp(demoServe().app, host);
  run.input({ type: "voice", text: "table one" });
  run.input({ type: "voice", text: "two lattes" });
  await host.settled();
  assert.ok(bridge.calls.at(-1)[1].content.includes("2× Latte  $9.00\n"));
  assert.equal(FAMILIES["even-g2"].display.proportional, true);
  assert.equal(FAMILIES["even-g2"].speaker, false);
});

test("microphone audio becomes voice input only when something turns it into text", async () => {
  const quiet = fakeBridge(), a = evenHubHost(quiet);
  const first = runApp(demoServe().app, a);
  quiet.emit({ audioEvent: { audioPcm: new Uint8Array(4) } });
  assert.equal(await a.listen(), false);                               // nothing to transcribe with: the microphone stays off
  assert.equal(first.state.screen, "home");

  const bridge = fakeBridge();
  const host = evenHubHost(bridge, { transcribe: async (audio) => audio.audioPcm.length ? "table four" : "" });
  const run = runApp(demoServe().app, host);
  assert.equal(await host.listen(), true);
  assert.deepEqual(bridge.calls.at(-1), ["audio", true]);
  bridge.emit({ audioEvent: { audioPcm: new Uint8Array(4) } });
  bridge.emit({ audioEvent: { audioPcm: new Uint8Array(0) } });        // silence: no words
  await new Promise((r) => setTimeout(r, 0));
  assert.equal(run.state.table, 4);
});

test("a page the glasses refuse is reported, not hidden", async () => {
  const problems = [];
  const host = evenHubHost(fakeBridge({ refuse: true }), { onProblem: (e) => problems.push(e.message) });
  runApp(demoServe().app, host);
  await host.settled();
  assert.deepEqual(problems, ["The glasses refused the page (result 1)."]);
});

test("an agent shows, asks and offers a choice through four tools, and is told what reached the wearer", async () => {
  assert.deepEqual(AGENT_TOOLS.map((t) => t.name), ["glasses_describe", "glasses_show", "glasses_ask", "glasses_choose"]);
  for (const t of AGENT_TOOLS) assert.ok(t.description && t.inputSchema.type === "object" && t.inputSchema.additionalProperties === false);

  const host = memoryHost(CLASSES.text);
  const heard = [];
  const session = agentSession(host, { name: "Concierge", onSay: (w) => heard.push(w) });
  assert.deepEqual(await session.call("glasses_describe"), { name: "Text-display glasses", display: { lines: 5, characters: 30, pictures: false },
    speaker: false, microphone: true, camera: false, input: ["tap", "double", "long"] });
  assert.deepEqual(await session.call("glasses_show", { title: "Gate change", lines: ["UA 212 boards at B14", "  ", "Walk: 9 min"], alert: "Boarding in 20 min" }),
    { shown: true, spoken: false, withheld: 0 });
  assert.equal(host.text, "Gate change\nBoarding in 20 min\nUA 212 boards at B14\nWalk: 9 min");

  const asked = session.call("glasses_ask", { question: "Navigate there?" });
  assert.equal(host.text, "Concierge\nNavigate there?\n› Yes\n  No");
  host.voice("how far is it");                                         // not a command: it goes to the agent
  assert.deepEqual(heard, ["how far is it"]);
  host.voice("yes");
  assert.deepEqual(await asked, { answer: "yes", shown: true, spoken: false, withheld: 0 });

  const chosen = session.call("glasses_choose", { title: "Lounge", options: ["Polaris", "United Club", "Skip"] });
  host.input({ type: "tap" }); host.input({ type: "double" });
  assert.deepEqual(await chosen, { choice: "United Club", index: 1, shown: true, spoken: false, withheld: 0 });
});

test("an agent's private content follows the same rule as an app's, and the agent is told it was held back", async () => {
  const ear = memoryHost(CLASSES.audio);
  const session = agentSession(ear);
  const got = await session.call("glasses_show", { title: "Reminder", lines: ["Take the evening tablet"], privacy: "health" });
  assert.deepEqual(got, { shown: false, spoken: true, withheld: 1 });
  assert.equal(ear.spoken.at(-1), "Reminder. You have a private update. Check your phone.");
  await assert.rejects(session.call("glasses_show", { title: "x", privacy: "secret" }), /privacy must be one of/);
  await assert.rejects(session.call("glasses_delete"), /Unknown tool/);
  await assert.rejects(session.call("glasses_choose", { title: "x", options: ["only one"] }), /at least two/);
  await assert.rejects(session.call("glasses_ask", {}), /needs a question/);
});

test("a question nobody answered is cancelled when the agent moves on, goes back or stops", async () => {
  const host = memoryHost(CLASSES.color);
  const session = agentSession(host);
  const first = session.call("glasses_ask", { question: "Still there?" });
  await session.call("glasses_show", { title: "Never mind" });
  assert.equal((await first).cancelled, true);
  const second = session.call("glasses_ask", { question: "Again?" });
  host.input({ type: "long" });
  assert.equal((await second).cancelled, true);
  const third = session.call("glasses_ask", { question: "Once more?" });
  session.stop();
  assert.equal((await third).cancelled, true);
});
