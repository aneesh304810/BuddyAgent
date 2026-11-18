import test from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { CLASSES, KINDS, WITHHELD, advice, buildApp, check, memoryHost, problems, runApp, toCode, works } from "../src/index.js";
import { EMPTY, INDUSTRIES } from "../studio/industries.js";

const WALK = INDUSTRIES.find((i) => i.id === "construction").spec;
const double = { type: "double" }, tap = { type: "tap" }, long = { type: "long" };

test("a description becomes an app that shows one step at a time", () => {
  const host = memoryHost(CLASSES.color);
  const run = runApp(buildApp(WALK), host);
  assert.equal(host.text, "Site safety walk · 1 of 6\nDone 0%\nStart at gate 2. Hard hats and boots\nbeyond this point.\n› Next");
  host.input(double);                                                   // Next
  assert.deepEqual(host.last.lines.slice(1).map((l) => l.text), ["Check the scaffold tags are in date.", "› Done", "  Could not do it"]);
  host.input(tap); host.input(double);                                  // Could not do it
  assert.equal(host.spoken.at(-1), "Are the edge barriers in place on every open floor? Say yes or no.");
  host.voice("no");
  assert.equal(host.last.lines[0].text, "Stop work near the edge and tell the");   // what to do when the answer is no
  assert.equal(host.last.lines[0].level, "warn");
  host.voice("yes");                                                    // walkways clear
  assert.ok(!host.text.includes("Stop work"));                          // the warning does not follow them around
  host.input(tap); host.input(double);                                  // weather: the second choice
  host.voice("Loose cable by the north stair.");
  assert.equal(run.state.done, true);
  assert.deepEqual(run.state.answers.map((a) => a.value), ["seen", "problem", "no", "yes", "Wet", "Loose cable by the north stair."]);
  assert.deepEqual(host.last.lines.map((l) => l.text), ["Walk complete.", "2 to follow up, 1 note.", "Check the scaffold tags are in date.",
    "Are the edge barriers in place on", "every open floor?", "› Start again"]);
  host.input(double);
  assert.deepEqual(run.state, { at: 0, answers: [], news: "", warn: "", done: false });
});

test("on audio glasses each step is spoken, and going back changes an answer", () => {
  const host = memoryHost(CLASSES.audio);
  const run = runApp(buildApp(WALK), host);
  assert.equal(host.spoken[0], "Site safety walk, 1 of 6. Done, 0%. Start at gate 2. Hard hats and boots beyond this point. 1 choice. 1 of 1: Next.");
  host.voice("select"); host.voice("select");                           // Next, then Done
  assert.equal(run.state.answers[1].value, "done");
  host.voice("back");
  assert.equal(run.state.at, 1);
  host.voice("next"); host.voice("select");                             // this time: Could not do it
  assert.equal(run.state.answers[1].value, "problem");
  host.voice("something that is not a command");
  assert.equal(host.spoken.at(-1), "Say next, select, yes, no or back.");
  host.input(long); host.input(long); host.input(long);                 // back past the start stays at the start
  assert.equal(run.state.at, 0);
});

test("a private step follows the privacy rule, and a message from outside is shown on the current step", () => {
  const care = INDUSTRIES.find((i) => i.id === "homecare").spec;
  const ear = memoryHost(CLASSES.audio), lens = memoryHost(CLASSES.text), through = memoryHost(CLASSES.color);
  runApp(buildApp(care), ear); const run = runApp(buildApp(care), lens); runApp(buildApp(care), through);
  assert.ok(ear.spoken[0].includes(WITHHELD.speech) && !ear.spoken[0].includes("Okafor"));
  assert.ok(lens.text.includes("Mrs Okafor"));                           // personal: shown in the lens
  lens.input(double);
  assert.ok(lens.text.includes("Has she eaten today?"));                 // health: these glasses are reached directly
  through.input(double);
  assert.ok(through.text.includes(WITHHELD.display) && !through.text.includes("eaten"));   // not through a maker's app
  run.notify({ text: "The pharmacy delivery has arrived." });
  assert.equal(lens.last.lines[0].text, "The pharmacy delivery has");
  lens.voice("yes");
  assert.ok(!lens.text.includes("pharmacy"));
});

test("what is wrong with a description is said in plain words", () => {
  assert.deepEqual(problems(null), ["There is nothing to build yet."]);
  assert.deepEqual(problems({ name: " ", steps: [] }), ["Give the app a name.", "Add at least one step."]);
  assert.deepEqual(problems({ name: "A", steps: [{ kind: "do", text: "" }, { kind: "choose", text: "Pick", options: ["One", " "] }, { kind: "fly", text: "x" },
    { kind: "info", text: "x", privacy: "secret" }] }),
    ["Step 1: write what it says.", "Step 2: give at least two things to choose from.", "Step 3: choose what kind of step it is.", "Step 4: privacy must be public, personal or health."]);
  assert.throws(() => buildApp({ name: "", steps: [] }), /Give the app a name/);
  assert.deepEqual(advice(WALK), []);
  const tips = advice({ name: "A very long name for a small lens", steps: [{ kind: "do", text: "x".repeat(140) }, { kind: "choose", text: "Pick", options: "abcdefgh".split("") }] });
  assert.equal(tips.length, 3);
  assert.deepEqual(Object.keys(KINDS), ["info", "do", "ask", "choose", "note"]);
});

test("the answers are handed over at the end", () => {
  const got = [];
  const host = memoryHost(CLASSES.text);
  runApp(buildApp({ name: "Two steps", steps: [{ kind: "do", text: "First" }, { kind: "ask", text: "Second?" }] }, { onFinish: (a) => got.push(a) }), host);
  host.input(double);
  assert.deepEqual(got, []);
  host.voice("no");
  assert.deepEqual(got[0], [{ step: 1, kind: "do", text: "First", value: "done" }, { step: 2, kind: "ask", text: "Second?", value: "no" }]);
  assert.ok(host.text.includes("0 to follow up, 0 notes."));              // a plain "no" with nothing to do about it is not a problem
});

test("every industry starting point builds, and works on every kind of glasses that can talk to the wearer", () => {
  assert.equal(new Set(INDUSTRIES.map((i) => i.id)).size, INDUSTRIES.length);
  assert.ok(!INDUSTRIES.some((i) => /restaurant|kitchen|waiter/i.test(JSON.stringify(i))));
  for (const { id, spec } of [...INDUSTRIES, { id: "empty", spec: EMPTY }]) {
    assert.deepEqual(problems(spec), [], id);
    const found = check(buildApp(spec));
    for (const kind of ["text", "color", "audio"]) assert.ok(works(found, kind), `${id} on ${kind}`);
  }
});

test("the app can be handed to a developer as a file that runs", async () => {
  const code = toCode(WALK);
  assert.ok(code.startsWith('import { buildApp } from "buddy-xr";\n\nexport default buildApp({'));
  const dir = mkdtempSync(join(tmpdir(), "buddyxr-"));
  try {
    const lib = pathToFileURL(fileURLToPath(new URL("../src/index.js", import.meta.url))).href;
    writeFileSync(join(dir, "app.mjs"), code.replace('"buddy-xr"', JSON.stringify(lib)));
    const app = (await import(pathToFileURL(join(dir, "app.mjs")).href)).default;
    const host = memoryHost(CLASSES.text);
    runApp(app, host);
    assert.ok(host.text.startsWith("Site safety walk"));
  } finally { rmSync(dir, { recursive: true, force: true }); }
});
