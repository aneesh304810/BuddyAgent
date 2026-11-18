import test from "node:test";
import assert from "node:assert/strict";
import { CLASSES, PROFILES, check, memoryHost, runApp, works } from "../src/index.js";
import { createVisit } from "../apps/visit/app.js";

test("the example job is worked through on a display: tick the steps, then close it", () => {
  const host = memoryHost(CLASSES.color);
  const run = runApp(createVisit(), host);
  assert.deepEqual(host.last.lines.map((l) => l.text), ["Door code 4471", "Done 0%", "› Isolate the power", "  Check the pressure", "  Replace the filter", "  Photograph the gauge"]);
  assert.deepEqual(host.spoken, []);                                    // nothing here must be said aloud
  for (let i = 0; i < 4; i++) host.input({ type: "double" });
  assert.equal(host.last.lines[1].value, 1);
  assert.equal(host.spoken.at(-1), "Close the job? Say yes or no.");
  host.voice("no");                                                     // not yet: the last step comes back
  assert.equal(run.state.done.length, 3);
  host.input({ type: "double" });
  host.voice("note the valve is worn.");
  assert.ok(host.text.includes("Note: the valve is worn"));
  host.voice("yes");
  assert.equal(host.text, "Job closed\nUnit 4B · Boiler service. 4 steps\ndone, 1 note.\n› Next job");
  host.input({ type: "double" });
  assert.deepEqual(run.state, { done: [], notes: [], news: "", closed: false });
});

test("on audio glasses the door code is not read out, and news from outside is", () => {
  const host = memoryHost(CLASSES.audio);
  const run = runApp(createVisit(), host);
  assert.equal(host.spoken[0], "Unit 4B, Boiler service. You have a private update. Check your phone. Done, 0%. 4 choices. 1 of 4: Isolate the power.");
  assert.deepEqual(run.withheld, { display: 0, speech: 1 });
  run.notify({ text: "The part has arrived at the van." });
  assert.ok(host.spoken.at(-1).startsWith("Unit 4B, Boiler service. The part has arrived at the van. Got it? Say yes or no."));
  host.voice("yes");
  assert.equal(run.state.news, "");
  host.voice("what time is it");
  assert.equal(host.spoken.at(-1), "Say next, select, or note and what to write down.");
  const told = memoryHost(CLASSES.audio);
  runApp(createVisit(), told, { speakPersonal: true });
  assert.ok(told.spoken[0].includes("Door code 4471."));
});

test("the example works on every kind of glasses that can talk to the wearer", () => {
  const found = check(createVisit());
  assert.deepEqual(Object.keys(PROFILES).filter((id) => !works(found, id)), ["omi"]);
});
