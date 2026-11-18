// A host for Brilliant Labs Halo: a round colour display of 256 by 256 points, a button, tap, microphones and
// bone-conduction speakers, reached from the phone over Bluetooth with nothing in between (route "direct").
//
// NOT YET RUN ON GLASSES. It is written against the maker's developer kit (brilliant_sdk, BSD-3-Clause, read at
// its 4 November 2026 state) and tested here against a stand-in for its Bluetooth class and a stand-in for the
// device's Lua functions. What is known and what is assumed:
//   known    the calls used on the kit's BrilliantBle (sendBreakSignal, sendResetSignal, uploadFileFromString,
//            sendLua, sendMessage, setDataResponseHandler, setPrintResponseHandler); how a message is split into
//            packets and acknowledged; the device functions the script uses (frame.display.clear, set_font and
//            text, frame.button.single, double and long, frame.imu.tap_callback, frame.bluetooth.send and
//            receive_callback, frame.sleep); the font: Dogica, fixed width, 8 points a character, ASCII only.
//   assumed  how much is readable. The grid (10 lines of 26 characters, or 6 of 13 with `large`) is worked out
//            from the display's size so the text stays inside the circle. Nobody has looked at it through the lens.
//   missing  the wearer's words and speech. The kit can stream the microphone and play sound; this host does
//            neither yet. Pass `say` to speak some other way (the phone, for example) and call `heard(text)` with
//            words recognised elsewhere. No camera yet.
//
// Use, in a page opened in Chrome (Web Bluetooth):
//   import { BrilliantBle } from "brilliant-ble";
//   const ble = new BrilliantBle(); await ble.connect();
//   const host = haloHost(ble); runApp(app, host);
//
// The script below is copied to the glasses as buddy_xr.lua and started with require. It does not replace
// main.lua, the glasses' own start-up script.

import { FAMILIES } from "../profiles.js";

export const HALO = { frame: 0x20, click: 0x0b, tap: 0x09, file: "buddy_xr.lua", module: "buddy_xr", ready: "buddy-xr ready" };

// The text grids, placed so that the block's corners stay inside the round display (radius 128).
const GRIDS = {
  small: { size: 8, pitch: 12, lines: 10, cols: 26, x: 25, y: 69 },
  large: { size: 16, pitch: 22, lines: 6, cols: 13, x: 25, y: 63 },
};

// What runs on the glasses. One message (code 0x20) carries a whole screen: a first line "x,y,pitch,size", then
// one line of text per row, each starting with a style digit (0 text, 1 title, 2 the choice in focus, 3 alert).
// Each packet is acknowledged the way the kit expects (01 00 00, or 01 00 01 when it could not be read).
export const HALO_APP = String.raw`-- Buddy XR on Halo: draws the lines the phone sends, and reports the button and taps.
local FRAME, CLICK, TAP = 0x20, 0x0B, 0x09
local COLOR = { ['0'] = 0xFFFFFF, ['1'] = 0x7FE3C4, ['2'] = 0xFFE066, ['3'] = 0xFFA500 }
local BOLD = { ['1'] = true, ['2'] = true, ['3'] = true }
local KIND = { single = 1, double = 2, triple = 3 }
local parts, got, size = nil, 0, 0
local pending = nil

local function reply(bytes)
  while not pcall(frame.bluetooth.send, bytes) do
    frame.sleep(0.0025)
  end
end

frame.bluetooth.receive_callback(function(data)
  local ok = pcall(function()
    if string.byte(data, 1) ~= FRAME then
      return
    end
    if parts == nil then
      size = string.byte(data, 2) << 8 | string.byte(data, 3)
      parts = { string.sub(data, 4) }
      got = #data - 3
    else
      parts[#parts + 1] = string.sub(data, 2)
      got = got + #data - 1
    end
    if got >= size then
      if got == size then
        pending = table.concat(parts)
      end
      parts = nil
    end
  end)
  if ok then
    reply('\x01\x00\x00')
  else
    parts = nil
    reply('\x01\x00\x01')
  end
end)

local function draw(text)
  local x, y, pitch, fsize = string.match(text, '^(%d+),(%d+),(%d+),(%d+)\n')
  if not x then
    error('a screen arrived without its first line')
  end
  x, y, pitch, fsize = tonumber(x), tonumber(y), tonumber(pitch), tonumber(fsize)
  frame.display.clear(0)
  local row = 0
  for line in string.gmatch(string.sub(text, string.find(text, '\n', 1, true) + 1) .. '\n', '(.-)\n') do
    local style, body = string.sub(line, 1, 1), string.sub(line, 2)
    if #body > 0 then
      frame.display.set_font(BOLD[style] and 1 or 0, fsize)
      frame.display.text(body, x, y + row * pitch, COLOR[style] or COLOR['0'])
    end
    row = row + 1
  end
end

local function event(flag, code)
  pcall(frame.bluetooth.send, string.char(flag, code))
end

frame.button.single(function() event(CLICK, 1) end)
frame.button.double(function() event(CLICK, 2) end)
frame.button.long(function() event(CLICK, 3) end)
frame.imu.tap_callback(function(kind) event(TAP, KIND[kind] or 1) end)

print('buddy-xr ready')

while true do
  local ok, err = pcall(function()
    if pending then
      local text = pending
      pending = nil
      draw(text)
    end
    frame.sleep(0.02)
  end)
  if not ok then
    print('buddy-xr stopped: ' .. tostring(err))
    break
  end
end
frame.display.clear(0)
`;

// The glasses' font has the printable ASCII characters and nothing else.
const PLAIN = { "›": ">", "‹": "<", "·": "-", "•": "*", "–": "-", "—": "-", "“": '"', "”": '"', "‘": "'", "’": "'",
  "…": "...", "×": "x", "✓": "+", "→": "->", "←": "<-", "°": " deg", "€": "EUR ", "£": "GBP " };

/** Text the glasses can draw: known signs replaced, accents dropped, anything else shown as "?". */
export function ascii(text) {
  return String(text).replace(/[^\x20-\x7e]/g, (c) => PLAIN[c] ?? c).normalize("NFKD").replace(/[̀-ͯ]/g, "")
    .replace(/[\x00-\x1f\x7f]/g, " ").replace(/[^\x20-\x7e]/g, "?");
}

const STYLE = (line) => (line.kind === "alert" ? "3" : line.focused ? "2" : "0");
const EVENTS = { 1: "tap", 2: "double", 3: "long" };   // one press or tap: next. Two: choose. A long press or three taps: back.

/**
 * @param ble      the kit's BrilliantBle, already connected
 * @param options  large: bigger text, fewer lines. taps: also take taps on the frame (default true).
 *                 say(text): speaks to the wearer some other way; without it nothing is spoken.
 *                 hears: true when the wearer's words will be passed in with heard().
 */
export function haloHost(ble, { profile = FAMILIES.halo, large = false, taps = true, say = null, hears = false, onProblem = () => {} } = {}) {
  const grid = large ? GRIDS.large : GRIDS.small;
  // only what this host can do today: it shows text and takes the button and taps
  const own = { ...profile, display: { ...profile.display, lines: grid.lines, cols: grid.cols, images: false },
    speaker: Boolean(say), mic: Boolean(hears), camera: false };
  let listener = () => {}, started = false, closed = false, queue = Promise.resolve(), last = "";

  const fit = (text) => ascii(text).slice(0, grid.cols).trimEnd();
  const content = (f) => [`${grid.x},${grid.y},${grid.pitch},${grid.size}`, "1" + fit(f.title), ...f.lines.map((l) => STYLE(l) + fit(l.text))].join("\n");

  async function start() {
    await ble.sendBreakSignal();            // stop whatever is running, restart the device's Lua, stop its start-up script
    await ble.sendResetSignal();
    await ble.sendBreakSignal();
    await ble.uploadFileFromString(HALO_APP, HALO.file);
    const said = await ble.sendLua(`require('${HALO.module}')`, { awaitPrint: true });
    if (!String(said ?? "").includes(HALO.ready)) throw new Error(`The glasses did not start Buddy XR. They answered: ${String(said ?? "nothing").trim()}`);
    started = true;
  }

  // one screen at a time, in order: a later screen must not overtake an earlier one
  const send = (text) => { queue = queue.then(async () => {
    if (closed || text === last) return;
    if (!started) await start();
    await ble.sendMessage(HALO.frame, new TextEncoder().encode(text));
    last = text;
  }).catch((e) => { started = false; last = ""; onProblem(e); }); return queue; };

  ble.setDataResponseHandler((data) => {
    if (closed || !data || data.length < 2) return;
    const type = (data[0] === HALO.click || (taps && data[0] === HALO.tap)) && EVENTS[data[1]];
    if (type) listener({ type });           // anything else, an acknowledgement for example, is not the wearer
  });
  if (ble.setPrintResponseHandler) ble.setPrintResponseHandler((line) => {
    // the script only prints again when it stops: the next screen starts it afresh
    if (!closed && String(line).startsWith("buddy-xr stopped")) { started = false; last = ""; onProblem(new Error(String(line).trim())); }
  });

  return { profile: own,
    show(frame) { send(content(frame)); },
    speak(text) { if (say) Promise.resolve().then(() => say(text)).catch(onProblem); },
    on(l) { listener = l; },
    /** The wearer's words, recognised somewhere else (the phone's microphone, for example). */
    heard(text) { if (!closed && text) listener({ type: "voice", text: String(text) }); },
    /** Resolves when everything shown so far has reached the glasses, or has failed and been reported. */
    settled: () => queue,
    /** Stop the script on the glasses and clear the display. */
    async close() { await queue; closed = true; if (started) { started = false; await ble.sendBreakSignal(); } },
  };
}
