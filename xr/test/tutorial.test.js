import test from "node:test";
import assert from "node:assert/strict";
import { CLASSES, PROFILES, check, memoryHost, runApp, works } from "../src/index.js";
import { STEPS, step1, step2, step3, step4, step5 } from "../apps/tutorial/steps.js";

const on = (make, kind) => { const host = memoryHost(CLASSES[kind]); const run = runApp(make(), host); return [host, run]; };
const double = { type: "double" }, tap = { type: "tap" };

test("step 1: one screen, as a line in the lens or a sentence in the ear", () => {
  assert.equal(on(step1, "text")[0].text, "Delivery round\n3 parcels left today.");
  assert.deepEqual(on(step1, "audio")[0].spoken, ["Delivery round. 3 parcels left today."]);
});

test("step 2: choosing a parcel takes it off the list", () => {
  const [lens, run] = on(step2, "text");
  assert.equal(lens.text, "Delivery round\n3 parcels left today.\n› 12 Mill Lane\n  4 Station Road\n  The Old Bakery");
  lens.input(tap); lens.input(double);
  assert.deepEqual(run.state.delivered, ["4 Station Road"]);
  assert.equal(lens.text, "Delivery round\n2 parcels left today.\n  12 Mill Lane\n› The Old Bakery");
  const [ear] = on(step2, "audio");
  ear.voice("next");
  assert.equal(ear.spoken.at(-1), "2 of 3: 4 Station Road.");
});

test("step 3: a parcel opens to a question; yes delivers it, no and back leave it", () => {
  const [lens, run] = on(step3, "text");
  lens.input(double);
  assert.equal(lens.text, "12 Mill Lane\nDelivered?\n› Yes\n  No");
  lens.voice("no");
  assert.deepEqual(run.state, { delivered: [], open: null });
  lens.input(double); lens.input({ type: "long" });                    // back
  assert.equal(run.state.open, null);
  lens.input(double); lens.voice("yes");
  assert.deepEqual(run.state.delivered, ["12 Mill Lane"]);
  const [ear] = on(step3, "audio");
  ear.voice("select");
  assert.equal(ear.spoken.at(-1), "12 Mill Lane. Delivered? Say yes or no.");
});

test("step 4: the gate code is shown in the lens and not read out", () => {
  const [lens] = on(step4, "text");
  assert.equal(lens.last.lines[0].text, "Done 0%");
  lens.input(double);
  assert.equal(lens.text, "12 Mill Lane\nGate code 2580\nDelivered?\n› Yes\n  No");
  const [ear, run] = on(step4, "audio");
  ear.voice("select");
  assert.equal(ear.spoken.at(-1), "12 Mill Lane. You have a private update. Check your phone. Delivered? Say yes or no.");
  assert.equal(run.withheld.speech, 1);
  ear.voice("yes"); ear.voice("select");                               // the next parcel has no code: nothing held back
  assert.equal(ear.spoken.at(-1), "4 Station Road. Delivered? Say yes or no.");
});

test("step 5: the depot adds a parcel, and 'skip' moves the open one to the end", () => {
  const [lens, run] = on(step5, "color");
  run.notify({ type: "parcel", address: "9 Quay Street" });
  run.notify({ type: "weather" });                                     // not for this app
  assert.equal(lens.last.lines[0].text, "New parcel: 9 Quay Street");
  assert.equal(lens.spoken.at(-1), "New parcel: 9 Quay Street.");      // an alert is spoken where there is a speaker
  assert.equal(run.state.parcels.length, 4);
  lens.input(double);
  lens.voice("skip this one");
  assert.deepEqual(run.state.parcels, ["4 Station Road", "The Old Bakery", "9 Quay Street", "12 Mill Lane"]);
  assert.equal(run.state.news, "");
  lens.voice("what is the weather");
  assert.equal(lens.spoken.at(-1), "Say next, select, yes, no or skip.");
});

test("every step works on every kind of glasses that can talk to the wearer", () => {
  for (const make of STEPS) {
    const found = check(make());
    assert.deepEqual(Object.keys(PROFILES).filter((id) => !works(found, id)), ["omi"]);
  }
});
