// Runs the script that haloHost copies to the glasses, in a Lua interpreter, against a stand-in for the device's
// functions. It needs the "fengari" package (Lua in JavaScript), which the framework itself does not depend on:
//   npm install --no-save fengari@0.1.4
// Without it these tests are skipped. This is not the maker's emulator and proves nothing about real glasses: it
// shows the script is valid Lua and does what the host expects with the packets the host really sends.

import test from "node:test";
import assert from "node:assert/strict";
import { HALO, HALO_APP, haloHost, runApp } from "../src/index.js";
import { demo } from "../apps/visit/app.js";

const fengari = await import("fengari").then((m) => m.default, () => null);
const skip = fengari ? false : "the fengari package is not installed";

// The device, as far as the script uses it. Every 0.02 second sleep runs the next step of the test; when the
// steps run out, sleep fails the way it does on the glasses when the phone sends a break.
const DEVICE = String.raw`
local log, sent, printed, cb = {}, {}, {}, {}
local function hex(b) return (b:gsub('.', function(c) return string.format('%02X ', c:byte()) end)) end
local function unhex(h) return (h:gsub('%x%x', function(x) return string.char(tonumber(x, 16)) end)) end
frame = {
  display = {
    clear = function(c) log[#log + 1] = 'clear ' .. c end,
    set_font = function(id, size) log[#log + 1] = 'font ' .. id .. ' ' .. size end,
    text = function(t, x, y, color) log[#log + 1] = string.format('text %d,%d %06X %s', x, y, color, t) end,
  },
  bluetooth = {
    send = function(b) sent[#sent + 1] = hex(b) end,
    receive_callback = function(f) cb.data = f end,
  },
  button = { single = function(f) cb.single = f end, double = function(f) cb.double = f end, long = function(f) cb.long = f end },
  imu = { tap_callback = function(f) cb.tap = f end },
  sleep = function(s)
    if s < 0.01 then return end
    local step = table.remove(STEPS, 1)
    if step == nil then error('break', 0) end
    step(cb, unhex)
  end,
}
print = function(s) printed[#printed + 1] = tostring(s) end
local run, err = load(APP, 'buddy_xr')
if not run then return 'syntax: ' .. tostring(err) end
run()
return table.concat(log, '\n') .. '\n==\n' .. table.concat(sent, '|') .. '\n==\n' .. table.concat(printed, '\n')
`;

/** Runs the script with these steps (Lua function bodies, given cb and unhex) and returns what it did. */
function onDevice(steps, app = HALO_APP) {
  const { lua, lauxlib, lualib, to_luastring } = fengari;
  const L = lauxlib.luaL_newstate();
  lualib.luaL_openlibs(L);
  lua.lua_pushstring(L, to_luastring(app));
  lua.lua_setglobal(L, to_luastring("APP"));
  const program = `STEPS = {\n${steps.map((s) => `function(cb, unhex) ${s} end,`).join("\n")}\n}\n${DEVICE}`;
  if (lauxlib.luaL_dostring(L, to_luastring(program)) !== 0) throw new Error(lua.lua_tojsstring(L, -1));
  const [drawn, sent, printed] = lua.lua_tojsstring(L, -1).split("\n==\n");
  return { drawn: drawn ? drawn.split("\n") : [], sent: sent ? sent.split("|").map((s) => s.trim()) : [], printed: printed ? printed.split("\n") : [] };
}

const hex = (bytes) => Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
/** One message as the maker's kit splits it: the first packet carries the length, each packet starts with the code. */
function packets(code, payload, max = 24) {
  const out = [Uint8Array.from([code, payload.length >> 8, payload.length & 0xff, ...payload.subarray(0, max - 3)])];
  for (let at = max - 3; at < payload.length; at += max - 1) out.push(Uint8Array.from([code, ...payload.subarray(at, at + max - 1)]));
  return out;
}
const receive = (packet) => `cb.data(unhex('${hex(packet)}'))`;

/** The bytes the real host sends for the first screen of the site-visit example. */
async function firstScreen() {
  let payload = null;
  const ble = { async sendBreakSignal() {}, async sendResetSignal() {}, async uploadFileFromString() {}, async sendLua() { return HALO.ready; },
    async sendMessage(code, bytes) { payload ??= bytes; }, setDataResponseHandler() {}, setPrintResponseHandler() {} };
  const host = haloHost(ble);
  runApp(demo().app, host).stop();
  await host.settled();
  return payload;
}

test("the script on the glasses draws the screen the host sends, packet by packet", { skip }, async () => {
  const payload = await firstScreen();
  const parts = packets(HALO.frame, payload);
  assert.ok(parts.length >= 4);                                             // it really is split
  const { drawn, sent, printed } = onDevice([...parts.map(receive), ""]);   // one more turn of the loop to draw it

  assert.equal(printed[0], "buddy-xr ready");
  assert.deepEqual(sent, parts.map(() => "01 00 00"));                      // every packet acknowledged, nothing else sent
  const [first, ...lines] = new TextDecoder().decode(payload).split("\n");
  assert.equal(first, "25,69,12,8");
  const COLOR = { 0: "FFFFFF", 1: "7FE3C4", 2: "FFE066", 3: "FFA500" };
  const expected = ["clear 0"];
  lines.forEach((line, row) => { if (line.length > 1) expected.push(`font ${line[0] === "0" ? 0 : 1} 8`, `text 25,${69 + row * 12} ${COLOR[line[0]]} ${line.slice(1)}`); });
  expected.push("clear 0");                                                 // the break at the end clears the display
  assert.deepEqual(drawn, expected);
  assert.ok(drawn.includes("text 25,69 7FE3C4 Unit 4B - Boiler service"));  // the title, on the first row, in bold
  assert.match(printed.at(-1), /^buddy-xr stopped: break/);
});

test("the script reports the button and taps the way the maker's kit does", { skip }, () => {
  const { sent } = onDevice(["cb.single()", "cb.double()", "cb.long()", "cb.tap('single')", "cb.tap('double')", "cb.tap('triple')", "cb.tap()"]);
  assert.deepEqual(sent, ["0B 01", "0B 02", "0B 03", "09 01", "09 02", "09 03", "09 01"]);
});

test("the script ignores what is not a screen, drops a damaged one, and stops with a reason on a bad one", { skip }, () => {
  const text = new TextEncoder().encode("25,69,12,8\n1Hello\n\n2> Yes");
  const other = onDevice([receive(Uint8Array.from([0x0a, 0, 2, 65, 66])), ""]);
  assert.deepEqual(other.sent, ["01 00 00"]);                               // acknowledged, so the phone is not left waiting
  assert.deepEqual(other.drawn, ["clear 0"]);                               // nothing drawn before the final clear

  const long = packets(HALO.frame, text, 12);
  long[0][2] -= 3;                                                          // the first packet promises fewer bytes than arrive
  const damaged = onDevice([...long.map(receive), "", ...packets(HALO.frame, text).map(receive), ""]);
  assert.ok(damaged.drawn.includes("text 25,69 7FE3C4 Hello"));             // the damaged one was dropped; the next one was drawn
  assert.equal(damaged.drawn.filter((l) => l.startsWith("text")).length, 2);
  assert.ok(damaged.drawn.includes("text 25,93 FFE066 > Yes"));             // the empty row was skipped but kept its place

  const bad = onDevice([...packets(HALO.frame, new TextEncoder().encode("no first line")).map(receive), "", ""]);
  assert.match(bad.printed.at(-1), /^buddy-xr stopped: .*without its first line/);
  assert.equal(bad.drawn.at(-1), "clear 0");

  const short = onDevice([receive(Uint8Array.from([HALO.frame])), ""]);     // too short to hold a length
  assert.deepEqual(short.sent, ["01 00 01"]);                               // refused, and said so
});
