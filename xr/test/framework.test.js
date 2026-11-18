import test from "node:test";
import assert from "node:assert/strict";
import { Alert, Ask, CLASSES, FAMILIES, Image, List, PROFILES, Progress, Screen, Stat, Text, WITHHELD, allowed, bridgeHost, check,
  defineApp, memoryHost, profile, runApp, works, wrap } from "../src/index.js";

const tap = { type: "tap" }, double = { type: "double" }, long = { type: "long" };

function counter() {
  return defineApp({ name: "Counter", state: { n: 0, picked: "" },
    view: (s) => Screen({ title: "Counter" }, Text(`Count is ${s.n}`),
      List(["Add one", "Add ten", "Reset"], { onSelect: (item, i, c) => c.set({ n: [s.n + 1, s.n + 10, 0][i], picked: item.label }) })) });
}

test("text is wrapped to the width of the lens, and a word longer than the lens is cut", () => {
  assert.deepEqual(wrap("the quick brown fox", 10), ["the quick", "brown fox"]);
  assert.deepEqual(wrap("supercalifragilistic day", 8), ["supercal", "ifragili", "stic day"]);
  assert.deepEqual(wrap("", 8), [""]);
});

test("the same app is lines on a display and sentences on audio glasses", () => {
  const lens = memoryHost(CLASSES.text), ear = memoryHost(CLASSES.audio);
  runApp(counter(), lens); runApp(counter(), ear);
  assert.equal(lens.text, "Counter\nCount is 0\n› Add one\n  Add ten\n  Reset");
  assert.deepEqual(lens.spoken, []);                                   // no speaker on these
  assert.deepEqual(ear.frames, []);                                    // no display on these
  assert.deepEqual(ear.spoken, ["Counter. Count is 0. 3 choices. 1 of 3: Add one."]);
});

test("tap moves, double tap chooses, and moving on audio glasses says only the new choice", () => {
  const lens = memoryHost(CLASSES.text), ear = memoryHost(CLASSES.audio);
  const a = runApp(counter(), lens), b = runApp(counter(), ear);
  lens.input(tap);
  assert.equal(lens.last.lines[2].text, "› Add ten");
  lens.input(double);
  assert.equal(a.state.n, 10);
  assert.equal(lens.last.lines[0].text, "Count is 10");
  assert.equal(lens.last.lines[2].focused, true);                      // the same screen: focus stays where it was
  lens.input(tap); lens.input(tap);                                    // past the end: round to the first
  assert.equal(lens.last.lines[1].text, "› Add one");
  ear.voice("next");
  assert.equal(ear.spoken.at(-1), "2 of 3: Add ten.");
  ear.voice("Select.");
  assert.equal(b.state.n, 10);
  assert.equal(ear.spoken.at(-1), "Counter. Count is 10. 3 choices. 2 of 3: Add ten.");
});

test("a long screen is cut into pages and the title says which one", () => {
  const app = defineApp({ name: "Long", view: () => Screen({ title: "Notes" }, Text("one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen twenty twenty-one twenty-two twenty-three twenty-four")) });
  const host = memoryHost(CLASSES.text);
  const run = runApp(app, host);
  assert.equal(host.last.title, "Notes 1/2");
  assert.equal(host.last.lines.length, 4);
  host.input(tap);
  assert.equal(host.last.title, "Notes 2/2");
  host.input(tap);
  assert.equal(run.frame.page, 1);                                     // no page three
  host.input({ type: "swipe-back" });
  assert.equal(run.frame.page, 0);
});

test("a question is answered by voice or by choosing, and is spoken where there is a speaker", () => {
  const make = () => defineApp({ name: "Ask", state: { answer: "" },
    view: (s) => Screen({ title: "Gate" }, s.answer ? Text(`You said ${s.answer}`) : Ask("Navigate there?", { yes: (c) => c.set({ answer: "yes" }), no: (c) => c.set({ answer: "no" }) })) });
  const color = memoryHost(CLASSES.color), text = memoryHost(CLASSES.text);
  const a = runApp(make(), color), b = runApp(make(), text);
  assert.deepEqual(color.spoken, ["Navigate there? Say yes or no."]);
  assert.equal(color.text, "Gate\nNavigate there?\n› Yes\n  No");
  color.voice("no");
  assert.equal(a.state.answer, "no");
  text.input(double);
  assert.equal(b.state.answer, "yes");
});

test("personal content is shown but not spoken, and health content only goes direct and is never spoken", () => {
  assert.equal(allowed("personal", "display", CLASSES.audio), true);
  assert.equal(allowed("personal", "speech", CLASSES.audio), false);
  assert.equal(allowed("personal", "speech", CLASSES.audio, { speakPersonal: true }), true);
  assert.equal(allowed("health", "display", CLASSES.text), true);       // phone straight to the glasses
  assert.equal(allowed("health", "display", CLASSES.color), false);     // through the maker's app
  assert.equal(allowed("health", "speech", CLASSES.text, { speakPersonal: true }), false);
  assert.throws(() => allowed("secret", "display", CLASSES.text), /Unknown privacy level/);

  const app = defineApp({ name: "Private", view: () => Screen({ title: "Today" }, Text("Gate B14"), Text("Seat 2 has a peanut allergy", { privacy: "personal" }),
    Text("Resting heart rate is up", { privacy: "health" })) });
  const ear = memoryHost(CLASSES.audio), color = memoryHost(CLASSES.color), text = memoryHost(CLASSES.text);
  const a = runApp(app, ear), b = runApp(app, color); runApp(app, text);
  assert.deepEqual(ear.spoken, [`Today. Gate B14. ${WITHHELD.speech}`]);   // said once, not once a line
  assert.deepEqual(a.withheld, { display: 0, speech: 2 });
  assert.ok(color.text.includes("peanut allergy") && !color.text.includes("heart rate") && color.text.includes(WITHHELD.display));
  assert.equal(b.withheld.display, 1);
  assert.ok(text.text.includes("heart rate"));
  const chosen = memoryHost(CLASSES.audio);
  runApp(app, chosen, { speakPersonal: true });
  assert.ok(chosen.spoken[0].includes("peanut allergy") && !chosen.spoken[0].includes("heart rate"));
});

test("an alert is always spoken where there is a speaker; other text only when the app asks", () => {
  const app = defineApp({ name: "Alert", view: () => Screen({ title: "Floor" }, Alert("Table 4: food is ready"), Text("Quiet line"), Text("Loud line", { say: true }),
    Stat("Total", "$21.00"), Progress("Prep", 0.6)) });
  const host = memoryHost(CLASSES.color);
  runApp(app, host);
  assert.deepEqual(host.spoken, ["Table 4: food is ready. Loud line."]);
  assert.deepEqual(host.last.lines.map((l) => l.text), ["Table 4: food is ready", "Quiet line", "Loud line", "Total: $21.00", "Prep 60%"]);
  assert.equal(host.last.lines[0].kind, "alert");
  assert.equal(host.last.lines[4].value, 0.6);
});

test("a picture is a picture where the display has them and its alt text everywhere else", () => {
  const app = defineApp({ name: "Pic", view: () => Screen({ title: "Dish" }, Image("dish.png", { alt: "Avocado toast on rye" })) });
  const color = memoryHost(CLASSES.color), text = memoryHost(CLASSES.text), ear = memoryHost(CLASSES.audio);
  runApp(app, color); runApp(app, text); runApp(app, ear);
  assert.deepEqual(color.last.image, { src: "dish.png", alt: "Avocado toast on rye" });
  assert.deepEqual(color.last.lines, []);
  assert.equal(text.text, "Dish\nAvocado toast on rye");
  assert.deepEqual(ear.spoken, ["Dish. Avocado toast on rye."]);
});

test("words that are not a command go to the app, events arrive, and back is the app's to handle", () => {
  const app = defineApp({ name: "Events", state: { heard: "", event: "", back: 0 },
    view: (s) => Screen({ title: "E" }, Text(`${s.heard}|${s.event}|${s.back}`)),
    onVoice: (text, c) => c.set({ heard: text }), onEvent: (e, c) => c.set({ event: e.type }), onBack: (c) => c.set((s) => ({ back: s.back + 1 })) });
  const host = memoryHost(CLASSES.text);
  const run = runApp(app, host);
  host.voice("two lattes"); run.notify({ type: "ready" }); host.input(long); host.voice("back");
  assert.equal(host.last.lines[0].text, "two lattes|ready|2");
  run.stop(); host.voice("ignored");
  assert.equal(run.state.heard, "two lattes");
});

test("a failing handler is reported, not swallowed", async () => {
  const app = defineApp({ name: "Fails", view: () => Screen({ title: "F" }, List(["Go"], { onSelect: async () => { throw new Error("kitchen is down"); } })) });
  const seen = [];
  const host = memoryHost(CLASSES.text);
  const run = runApp(app, host, { onError: (e) => seen.push(e.message) });
  host.input(double);
  await new Promise((r) => setTimeout(r, 0));
  assert.deepEqual(seen, ["kitchen is down"]);
  assert.equal(run.errors.length, 1);
  assert.throws(() => defineApp({ name: "x" }), /needs a view/);
  assert.throws(() => runApp(defineApp({ name: "Bad", view: () => Text("no screen") }), memoryHost(CLASSES.text)), /must return a Screen/);
});

test("every profile is complete, and the families keep what their adapters declare", () => {
  for (const [id, p] of Object.entries(PROFILES)) {
    assert.equal(p.id, id);
    assert.ok(p.name && p.about && Array.isArray(p.input) && ["direct", "maker-app", "cloud"].includes(p.route), id);
    if (p.display) assert.ok(p.display.lines >= 2 && p.display.cols >= 16 && ["text", "color"].includes(p.display.kind), id);
  }
  for (const f of Object.values(FAMILIES)) assert.equal(f.tested, false);   // nothing has run on hardware yet
  assert.equal(FAMILIES.meta.display, null);
  assert.equal(FAMILIES.even.camera, false);
  assert.equal(FAMILIES.even.display.lines, 5);
  assert.equal(FAMILIES.omi.speaker, false);
  assert.throws(() => profile("nope"), /Unknown glasses profile/);
});

test("the check says where an app would not work or would lose something", () => {
  const app = defineApp({ name: "Check", needs: ["camera"], view: () => Screen({ title: "C" }, Image("a.png"), Text("Allergy: nuts", { privacy: "personal" }),
    List(["One"], { onSelect: () => {} })) });
  const found = check(app);
  const of = (id) => found.filter((f) => f.profile === id).map((f) => `${f.level}: ${f.text}`);
  assert.deepEqual(of("omi"), ["error: No display and no speaker: the app cannot tell the wearer anything."]);
  assert.ok(of("even").includes("error: The app needs a camera, which these glasses do not have."));
  assert.ok(of("text").some((t) => t.startsWith("warn: An Image has no alt text")));
  assert.ok(!of("color").some((t) => t.includes("alt text")));               // it shows the picture
  assert.ok(of("audio").some((t) => t.startsWith("info: 1 private line is not spoken here")));
  assert.equal(works(found, "color"), true);
  assert.equal(works(found, "omi"), false);
  assert.deepEqual(check(counter(), { profiles: ["text", "audio", "color"] }), []);
});

test("the bridge sends frames and speech as plain messages and takes input back", async () => {
  const sent = [];
  const host = bridgeHost(CLASSES.color, (m) => sent.push(JSON.parse(JSON.stringify(m))));
  const run = runApp(counter(), host);
  assert.deepEqual(sent.map((m) => m.op), ["show"]);
  assert.equal(sent[0].frame.title, "Counter");
  host.receive({ op: "input", event: { type: "double" } });
  assert.equal(run.state.n, 1);
  const photo = host.capture();
  assert.deepEqual(sent.at(-1), { op: "capture", id: 1 });
  host.receive({ op: "captured", id: 1, image: "data:image/jpeg;base64,AAAA" });
  assert.equal(await photo, "data:image/jpeg;base64,AAAA");
  const failed = host.capture();
  host.receive({ op: "captured", id: 2, error: "camera busy" });
  await assert.rejects(failed, /camera busy/);
  host.receive(null);
});
