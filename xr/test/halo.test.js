import test from "node:test";
import assert from "node:assert/strict";
import { Alert, Ask, FAMILIES, HALO, HALO_APP, List, Screen, Stat, Text, defineApp, haloHost, runApp } from "../src/index.js";
import { ascii } from "../src/hosts/halo.js";
import { demo } from "../apps/visit/app.js";

/** Stands in for the maker's BrilliantBle: records the calls, and lets a test send what the glasses would send. */
function fakeBle({ answer = HALO.ready, failMessages = 0 } = {}) {
  const calls = [];
  let onData = () => {}, onPrint = () => {};
  return { calls, press: (...bytes) => onData(Uint8Array.from(bytes)), print: (line) => onPrint(line),
    screens: () => calls.filter((c) => c[0] === "message").map((c) => new TextDecoder().decode(c[2]).split("\n")),
    async sendBreakSignal() { calls.push(["break"]); },
    async sendResetSignal() { calls.push(["reset"]); },
    async uploadFileFromString(content, name) { calls.push(["upload", name, content]); },
    async sendLua(text, options) { calls.push(["lua", text, options]); return answer; },
    async sendMessage(code, payload) { if (failMessages-- > 0) throw new Error("Device didn't acknowledge message data within 5000ms."); calls.push(["message", code, payload]); },
    setDataResponseHandler(h) { onData = h; },
    setPrintResponseHandler(h) { onPrint = h; } };
}

test("the Halo host starts its script once, then sends each screen as one message of plain lines", async () => {
  const ble = fakeBle();
  const host = haloHost(ble);
  const run = runApp(demo().app, host);
  await host.settled();
  assert.deepEqual(ble.calls.slice(0, 5).map((c) => c[0]), ["break", "reset", "break", "upload", "lua"]);
  assert.deepEqual(ble.calls[3].slice(1), ["buddy_xr.lua", HALO_APP]);      // its own file: the glasses' main.lua is left alone
  assert.deepEqual(ble.calls[4].slice(1), ["require('buddy_xr')", { awaitPrint: true }]);
  assert.equal(ble.calls[5][1], 0x20);
  const [first, title, ...lines] = ble.screens()[0];
  assert.equal(first, "25,69,12,8");                                        // where the text block sits, and the font size
  assert.equal(title, "1Unit 4B - Boiler service");
  assert.ok(lines.includes("0Door code 4471"));                             // personal, and shown: nothing sits between phone and glasses
  assert.ok(lines.some((l) => l.startsWith("2> ")));                        // the choice in focus
  for (const l of [title, ...lines]) assert.ok(/^[0-3][\x20-\x7e]{0,26}$/.test(l), l);
  assert.ok(lines.length <= 9);

  const before = ble.screens().length;
  ble.press(HALO.click, 1);                                                 // a press: the next choice
  await host.settled();
  assert.equal(ble.screens().length, before + 1);
  assert.equal(ble.calls.filter((c) => c[0] === "upload").length, 1);
  ble.press(0x01, 0x00, 0x00);                                              // an acknowledgement is not the wearer
  ble.press(HALO.click, 9);
  ble.press(HALO.click);
  await host.settled();
  assert.equal(ble.screens().length, before + 1);
  run.stop();
});

test("the button and taps on Halo choose, and a long press goes back", async () => {
  const app = defineApp({ name: "Pick", state: { picked: "", back: 0 },
    view: (s) => Screen({ title: "Pick" }, Text(`Picked: ${s.picked || "nothing"}`), List(["Tea", "Coffee"], { onSelect: (item, i, ctx) => ctx.set({ picked: item.label }) })),
    onBack: (ctx) => ctx.set({ back: ctx.state.back + 1 }) });
  const ble = fakeBle();
  const host = haloHost(ble);
  const run = runApp(app, host);
  ble.press(HALO.click, 1);      // next: Coffee
  ble.press(HALO.click, 2);      // choose
  assert.equal(run.state.picked, "Coffee");
  ble.press(HALO.tap, 1);        // a tap: next, round to Tea
  ble.press(HALO.tap, 2);        // two taps: choose
  assert.equal(run.state.picked, "Tea");
  ble.press(HALO.click, 3);      // a long press: back
  ble.press(HALO.tap, 3);        // three taps: back
  assert.equal(run.state.back, 2);
  await host.settled();
  assert.deepEqual(ble.screens().at(-1).slice(1), ["1Pick", "0Picked: Tea", "2> Tea", "0  Coffee"]);

  const still = fakeBle(), quiet = haloHost(still, { taps: false });
  const other = runApp(app, quiet);
  still.press(HALO.tap, 1); still.press(HALO.tap, 2); still.press(HALO.tap, 3);
  assert.deepEqual([other.state.picked, other.state.back], ["", 0]);       // taps switched off: nothing moved and nothing was chosen
  still.press(HALO.click, 2);
  assert.equal(other.state.picked, "Tea");          // the button still works, and the focus was still on the first choice
});

test("on Halo, personal and health details are shown and never spoken", async () => {
  const app = defineApp({ name: "Private", view: () => Screen({ title: "Today" },
    Alert("Leave by 9"), Text("Door code 4471", { privacy: "personal" }), Stat("Resting heart rate", "58", { privacy: "health" })) });
  const said = [], ble = fakeBle();
  const host = haloHost(ble, { say: (t) => said.push(t) });
  assert.equal(host.profile.speaker, true);
  assert.equal(host.profile.route, "direct");
  const run = runApp(app, host);
  await host.settled();
  await new Promise((r) => setTimeout(r, 0));
  const lines = ble.screens()[0];
  assert.ok(lines.includes("3Leave by 9") && lines.includes("0Door code 4471") && lines.includes("0Resting heart rate: 58"));
  const heard = said.join(" ");
  assert.ok(heard.includes("Leave by 9"));
  assert.ok(!heard.includes("4471") && !heard.includes("58"), heard);
  run.stop();

  const silent = haloHost(fakeBle());                 // nothing to speak with: the framework is told so
  assert.deepEqual([silent.profile.speaker, silent.profile.mic, silent.profile.camera, silent.profile.display.images], [false, false, false, false]);
  assert.equal(FAMILIES.halo.speaker, true);          // the glasses have speakers; this host does not use them yet
});

test("the Halo host says what went wrong and starts afresh on the next screen", async () => {
  const problems = [];
  const refused = fakeBle({ answer: "attempt to call a nil value" });
  const host = haloHost(refused, { onProblem: (e) => problems.push(e.message) });
  const run = runApp(demo().app, host);
  await host.settled();
  assert.match(problems[0], /did not start Buddy XR.*nil value/);
  assert.equal(refused.screens().length, 0);
  run.stop();

  const flaky = fakeBle({ failMessages: 1 }), errors = [];
  const second = haloHost(flaky, { onProblem: (e) => errors.push(e.message) });
  const app = defineApp({ name: "N", state: { n: 0 }, view: (s) => Screen({ title: "Count" }, Text(`n is ${s.n}`), Ask("More?", { yes: (ctx) => ctx.set({ n: ctx.state.n + 1 }) })) });
  const counting = runApp(app, second);
  await second.settled();
  assert.match(errors[0], /didn't acknowledge/);
  flaky.press(HALO.click, 2);                         // yes
  await second.settled();
  assert.equal(counting.state.n, 1);
  assert.equal(flaky.calls.filter((c) => c[0] === "upload").length, 2);     // the script was sent again
  assert.ok(flaky.screens().at(-1).includes("0n is 1"));

  flaky.print("buddy-xr stopped: out of memory");     // the script stopped by itself
  assert.match(errors[1], /stopped: out of memory/);
  flaky.press(HALO.click, 2);
  await second.settled();
  assert.equal(flaky.calls.filter((c) => c[0] === "upload").length, 3);

  await second.close();
  assert.equal(flaky.calls.at(-1)[0], "break");
  const count = flaky.calls.length;
  flaky.press(HALO.click, 2);
  await second.settled();
  assert.equal(flaky.calls.length, count);            // closed: nothing more is sent
});

test("Halo text is plain ASCII, with the larger size and spoken words as options", async () => {
  assert.equal(ascii("› Café · 20°C — “ok”… ×2 ✓"), '> Cafe - 20 degC - "ok"... x2 +');
  assert.equal(ascii("日本\tline\nbreak"), "?? line break");
  assert.ok(/^[\x09\x0a\x20-\x7e]+$/.test(HALO_APP));                       // the script itself is ASCII

  const ble = fakeBle(), host = haloHost(ble, { large: true, hears: true });
  assert.deepEqual([host.profile.display.lines, host.profile.display.cols, host.profile.mic], [6, 13, true]);
  const app = defineApp({ name: "V", state: { words: "" }, view: (s) => Screen({ title: "Listening to the wearer" }, Text(s.words || "Say something")),
    onVoice: (words, ctx) => ctx.set({ words }) });
  const run = runApp(app, host);
  host.heard("order two teas");
  await host.settled();
  assert.equal(run.state.words, "order two teas");
  const screen = ble.screens().at(-1);
  assert.equal(screen[0], "25,63,22,16");
  assert.equal(screen[1], "1Listening to");                                 // cut to the 13 characters that fit
  for (const l of screen.slice(1)) assert.ok(l.length <= 14, l);
});
