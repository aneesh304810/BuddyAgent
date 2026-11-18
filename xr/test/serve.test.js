import test from "node:test";
import assert from "node:assert/strict";
import { CLASSES, check, memoryHost, runApp, works, PROFILES } from "../src/index.js";
import { DEMO_MENU, demoServe, parseOrder, total } from "../apps/serve/app.js";

const flush = () => new Promise((r) => setTimeout(r, 0));

test("an order is read the way people say it", () => {
  const got = parseOrder("Two lattes and an avocado toast, no nuts, she is allergic to peanuts", DEMO_MENU);
  assert.deepEqual(got.items, [{ name: "Latte", qty: 2, price: 4.5 }, { name: "Avocado Toast", qty: 1, price: 12 }]);
  assert.deepEqual(got.notes, ["no nuts"]);
  assert.deepEqual(got.allergies, ["peanuts"]);
  assert.equal(total(got.items), 21);
  assert.deepEqual(parseOrder("3 OJ, a latte and one latte", DEMO_MENU).items, [{ name: "Orange Juice", qty: 3, price: 5 }, { name: "Latte", qty: 2, price: 4.5 }]);
  assert.deepEqual(parseOrder("can I get eggs benny please", DEMO_MENU).items, [{ name: "Eggs Benedict", qty: 1, price: 14 }]);
  assert.deepEqual(parseOrder("two pancakes and a latte", DEMO_MENU).unknown, ["pancakes"]);
});

test("on a display: say the table, say the order, check it, send it", async () => {
  const { app, kitchen } = demoServe();
  const host = memoryHost(CLASSES.text);
  const run = runApp(app, host);
  assert.equal(host.text, "Buddy Serve 1/2\nSay a table number to start an\norder.\n› Table 1 · 2 guests\n  Table 2 · 4 guests");
  host.voice("table one");
  assert.equal(host.text, "Table 1\nSay the order.");
  host.voice("two lattes and an avocado toast, allergic to peanuts");
  assert.deepEqual(host.last.lines.map((l) => l.text), ["2× Latte                 $9.00", "1× Avocado Toast        $12.00", "Allergy: peanuts", "Total: $21.00"]);
  assert.equal(host.last.title, "Table 1 · check 1/2");
  assert.deepEqual(kitchen, []);                                       // nothing is sent before the waiter says so
  host.voice("yes");
  await flush();
  assert.deepEqual(kitchen, [{ table: 1, items: [{ name: "Latte", qty: 2, price: 4.5 }, { name: "Avocado Toast", qty: 1, price: 12 }], unknown: [], notes: [], allergies: ["peanuts"] }]);
  assert.equal(host.text, "Table 1 · sent\n2× Latte, 1× Avocado Toast ·\n$21.00\n› Done");
  host.input({ type: "double" });
  assert.equal(run.state.screen, "home");
});

test("something not on the menu stops the order at the table", async () => {
  const { app, kitchen } = demoServe();
  const host = memoryHost(CLASSES.color);
  runApp(app, host);
  host.input({ type: "double" });                                      // the first table, by choosing it
  host.voice("two pancakes and a latte");
  assert.equal(host.text, "Table 1\npancakes? Not on the menu.\nOrder not sent. Say it again.");
  assert.equal(host.spoken.at(-1), "pancakes? Not on the menu. Order not sent. Say it again.");
  host.voice("a latte");
  host.voice("no");                                                    // start again
  assert.equal(host.text, "Table 1\nSay the order.");
  await flush();
  assert.deepEqual(kitchen, []);
});

test("on audio glasses the order is read back, and the guest's allergy is not read out", async () => {
  const { app, kitchen } = demoServe();
  const host = memoryHost(CLASSES.audio);
  runApp(app, host);
  assert.equal(host.spoken[0], "Buddy Serve. Say a table number to start an order. 3 choices. 1 of 3: Table 1, 2 guests.");
  host.voice("table 2");
  assert.equal(host.spoken.at(-1), "Table 2. Say the order.");
  host.voice("an espresso and a croissant, peanut allergy");
  assert.equal(host.spoken.at(-1), "Table 2, check. 1 Espresso, $3.00. 1 Croissant, $4.00. You have a private update. Check your phone. Total: $7.00. Send to the kitchen? Say yes or no.");
  host.voice("yes");
  await flush();
  assert.equal(kitchen[0].allergies[0], "peanut");                     // the kitchen still gets it
  assert.equal(host.spoken.at(-1), "Table 2, sent. 1 Espresso, 1 Croissant, $7.00. 1 choice. 1 of 1: Done.");
});

test("the floor reaches the waiter for their own tables only, one thing at a time", () => {
  const { app } = demoServe();
  const host = memoryHost(CLASSES.color);
  const run = runApp(app, host);
  run.notify({ type: "ready", table: 9 });                             // someone else's table
  assert.equal(run.state.floor.length, 0);
  run.notify({ type: "ready", table: 4 });
  run.notify({ type: "check", table: 2 });
  assert.equal(host.last.lines[0].text, "Table 4: food is ready.");
  assert.equal(host.spoken.at(-1), "Table 4: food is ready. Got it? Say yes or no.");
  host.voice("yes");
  assert.equal(host.last.lines[0].text, "Table 2 wants the check.");
  assert.equal(host.last.lines[0].level, "warn");
  host.voice("no");                                                    // later: it stays
  assert.equal(run.state.floor.length, 1);
  host.voice("yes");
  assert.equal(run.state.floor.length, 0);
  host.voice("table seven");
  assert.equal(host.spoken.at(-1), "That is not one of your tables.");
});

test("Buddy Serve works on every kind of glasses that can talk to the wearer", () => {
  const { app } = demoServe();
  const found = check(app, { states: [{ screen: "confirm", table: 1, order: parseOrder("a latte, peanut allergy", DEMO_MENU) }] });
  const failing = Object.keys(PROFILES).filter((id) => !works(found, id));
  assert.deepEqual(failing, ["omi"]);                                  // it can only listen
});
