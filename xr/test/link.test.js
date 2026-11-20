import test from "node:test";
import assert from "node:assert/strict";
import { CLASSES, LINK, connectLink, described, memoryHost } from "../src/index.js";

/** Stands in for a WebSocket: keeps what was sent, and lets a test speak as the server. */
function fakeSocket() {
  const s = { readyState: 0, sent: [], closed: false,
    send(textMessage) { s.sent.push(JSON.parse(textMessage)); },
    close() { s.closed = true; if (s.onclose) s.onclose(); },
    open() { s.readyState = 1; s.onopen(); },
    server(message) { s.onmessage({ data: JSON.stringify(message) }); } };
  return s;
}
const settle = () => new Promise((done) => setImmediate(done));
const DEVICE = { maker: "brilliant", model: "halo", id: "Halo 4F2A" };

test("the link says hello with what the glasses can do, and nothing else about them", () => {
  const host = memoryHost(CLASSES.text), socket = fakeSocket();
  const states = [];
  const link = connectLink(host, socket, { device: DEVICE, onState: (what) => states.push(what) });
  assert.deepEqual(socket.sent, []);                                   // nothing before the socket is open
  socket.open();
  assert.deepEqual(socket.sent, [{ op: "hello", protocol: "bwcp", version: LINK.version, device: DEVICE,
    profile: { name: "Text-display glasses", display: { kind: "text", lines: 5, cols: 30, images: false }, speaker: false, mic: true,
      camera: false, input: ["tap", "double", "long"], route: "direct" } }]);
  assert.deepEqual(Object.keys(described(CLASSES.text)).sort(), ["camera", "display", "input", "mic", "name", "route", "speaker"]);
  assert.equal(link.connected, false);                                 // not until the server says welcome
  socket.server({ op: "welcome", version: "0.2", session: "s1" });
  assert.equal(link.connected, true);
  assert.deepEqual(states, ["connected"]);
});

test("the server shows, asks and offers a choice, and gets back what reached the wearer", async () => {
  const host = memoryHost(CLASSES.text), socket = fakeSocket();
  connectLink(host, socket, { device: DEVICE });
  socket.open(); socket.server({ op: "welcome", version: "0.2", session: "s1" });
  const results = () => socket.sent.filter((m) => m.op === "result");

  socket.server({ op: "call", id: 1, tool: "glasses_show", args: { title: "Gate change", lines: ["UA 212 boards at B14"] } });
  await settle();
  assert.equal(host.text, "Gate change\nUA 212 boards at B14");
  assert.deepEqual(results().at(-1), { op: "result", id: 1, ok: true, result: { shown: true, spoken: false, withheld: 0 } });

  socket.server({ op: "call", id: 2, tool: "glasses_ask", args: { question: "Navigate there?" } });
  await settle();
  assert.equal(results().length, 1);                                   // still waiting for the wearer
  host.voice("yes");
  await settle();
  assert.deepEqual(results().at(-1), { op: "result", id: 2, ok: true, result: { answer: "yes", shown: true, spoken: false, withheld: 0 } });

  socket.server({ op: "call", id: 3, tool: "glasses_choose", args: { title: "Lounge", options: ["Polaris", "United Club"] } });
  await settle();
  host.input({ type: "tap" }); host.input({ type: "double" });
  await settle();
  assert.deepEqual(results().at(-1).result, { choice: "United Club", index: 1, shown: true, spoken: false, withheld: 0 });

  socket.server({ op: "call", id: 4, tool: "glasses_delete", args: {} });
  await settle();
  assert.deepEqual(results().at(-1), { op: "result", id: 4, ok: false, error: 'Unknown tool "glasses_delete".' });
});

test("the privacy rule is applied on this side, and the server is told what was held back", async () => {
  const ear = memoryHost(CLASSES.audio), socket = fakeSocket();
  connectLink(ear, socket, { device: DEVICE });
  socket.open();
  socket.server({ op: "call", id: 1, tool: "glasses_show", args: { title: "Door", lines: ["The code is 4471"], privacy: "personal" } });
  await settle();
  assert.ok(!ear.spoken.join(" ").includes("4471"));                   // a personal detail is not read out to the room
  assert.deepEqual(socket.sent.at(-1).result, { shown: false, spoken: true, withheld: 1 });
});

test("a question the server stops waiting for is taken off the lens", async () => {
  const host = memoryHost(CLASSES.text), socket = fakeSocket();
  connectLink(host, socket, { device: DEVICE });
  socket.open();
  socket.server({ op: "call", id: 7, tool: "glasses_ask", args: { question: "Send it?" } });
  await settle();
  assert.ok(host.text.includes("Send it?"));
  socket.server({ op: "cancel", id: 99 });                             // not a question it is waiting on: ignored
  await settle();
  assert.ok(host.text.includes("Send it?"));
  socket.server({ op: "cancel", id: 7 });
  await settle();
  assert.ok(!host.text.includes("Send it?"));
  const result = socket.sent.find((m) => m.op === "result" && m.id === 7);
  assert.equal(result.ok, true);
  assert.equal(result.result.cancelled, true);
});

test("what the wearer says, the state of the glasses and goodbye all cross the link", () => {
  const host = memoryHost(CLASSES.text), socket = fakeSocket();
  const states = [];
  const link = connectLink(host, socket, { device: DEVICE, onState: (what, detail) => states.push([what, detail]) });
  socket.open();
  host.voice("how far is the gate");                                   // not a command: it goes to Buddy
  link.state({ battery: 0.62, link: "good" });
  link.problem("capture-failed", "the camera did not answer");
  socket.server({ op: "something-new", x: 1 });                        // an op this side does not know: ignored
  socket.server("not json");
  assert.deepEqual(socket.sent.slice(1), [{ op: "said", text: "how far is the gate" }, { op: "state", battery: 0.62, link: "good" },
    { op: "problem", code: "capture-failed", about: "the camera did not answer" }]);
  link.close("low-battery");
  assert.deepEqual(socket.sent.at(-1), { op: "bye", why: "low-battery" });
  assert.equal(socket.closed, true);
  assert.equal(link.connected, false);
  assert.deepEqual(states, [["closed", undefined]]);
});

test("a server that refuses is not talked to again", () => {
  const host = memoryHost(CLASSES.text), socket = fakeSocket();
  const states = [];
  const link = connectLink(host, socket, { device: DEVICE, onState: (what, detail) => states.push([what, detail]) });
  socket.open();
  socket.server({ op: "refused", why: "The Devices module is switched off on this server." });
  assert.deepEqual(states[0], ["refused", "The Devices module is switched off on this server."]);
  assert.equal(socket.closed, true);
  const sentBefore = socket.sent.length;
  link.state({ battery: 0.5 });
  assert.equal(socket.sent.length, sentBefore);
  assert.equal(link.connected, false);
});
