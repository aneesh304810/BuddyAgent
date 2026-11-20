// The Glasses page: the glasses runtime, running in this browser, joined to this Buddy server (xr/src/link.js).
//
//   glasses  <->  this page (fits to lens or ear, applies the privacy rule)  <->  /ws/glasses  <->  Buddy's brain
//
// A "preview" is a lens drawn on this page, to try the link without a pair. Halo is reached over Bluetooth from
// this browser with the maker's own library, loaded only when asked for. NOT YET TRIED WITH A REAL PAIR.

import { CLASSES, connectLink, haloHost } from "/xr/src/index.js";

const $ = (id) => document.getElementById(id);
const KIT = "/xr/vendor/brilliant-ble/brilliant-ble.es.js";

// --- the access token: the same one the Buddy page remembers --------------------------------------------------
const params = new URLSearchParams(location.search);
function storageGet(k) { try { return localStorage.getItem(k); } catch { return null; } }
function storageSet(k, v) { try { localStorage.setItem(k, v); } catch { /* private window */ } }
if (params.get("token")) storageSet("buddyToken", params.get("token"));
const token = params.get("token") || storageGet("buddyToken") || "";
if (params.get("token") && storageGet("buddyToken") === token) history.replaceState(null, "", location.pathname);

let link = null, host = null, ble = null, wake = null;

function tell(text, mood = "") { const s = $("status"); s.textContent = text; s.className = "status" + (mood ? " " + mood : ""); }

// --- what the wearer gets, drawn here -------------------------------------------------------------------------
function drawFrame(frame) {
  const lens = $("lens");
  lens.textContent = "";
  const add = (cls, text) => { const d = document.createElement("div"); d.className = cls; d.textContent = text; lens.appendChild(d); };
  if (frame.title) add("title", frame.title);
  for (const line of frame.lines) add("line" + (line.focused ? " focus" : "") + (line.kind === "alert" ? " alert" : ""), line.text);
  if (frame.pages > 1) add("pages", `Page ${frame.page + 1} of ${frame.pages}`);
}
function drawSpoken(text) {
  const li = document.createElement("li");
  li.textContent = text;
  $("ear").prepend(li);
  while ($("ear").children.length > 6) $("ear").lastChild.remove();
}
function clearLens(note) {
  $("lens").textContent = "";
  const d = document.createElement("div"); d.className = "empty"; d.textContent = note; $("lens").appendChild(d);
  $("ear").textContent = "";
}

/** A lens on this page. It is reached directly: nothing passes through anyone else. */
function previewHost(kind) {
  let listener = () => {};
  return { profile: { ...CLASSES[kind], name: `Preview (${CLASSES[kind].name.toLowerCase()})`, route: "direct" },
    show: drawFrame, speak: drawSpoken, on(l) { listener = l; }, input(event) { listener(event); } };
}

/** Real glasses, with what is sent to them also drawn here so it can be checked. */
function mirrored(real) {
  return { profile: real.profile, show(f) { drawFrame(f); real.show(f); }, speak(t) { drawSpoken(t); real.speak(t); },
    on(l) { real.on(l); }, input() {}, close: () => (real.close ? real.close() : undefined) };
}

function deviceId() {
  let id = storageGet("buddyGlassesId");
  if (!id) { id = "preview-" + Math.random().toString(36).slice(2, 8); storageSet("buddyGlassesId", id); }
  return id;
}

// --- connecting -----------------------------------------------------------------------------------------------
function busy(on) { $("connect").disabled = on; $("kind").disabled = on || Boolean(link); }
function shown(connected) {
  $("connect").hidden = connected; $("disconnect").hidden = !connected; $("kind").disabled = connected;
  $("lensCard").hidden = !connected; $("testCard").hidden = !connected;
  $("touch").hidden = connected && $("kind").value === "halo";       // on real glasses the wearer taps the glasses
}

async function connect() {
  if (link) return;
  const kind = $("kind").value;
  busy(true);
  let device;
  try {
    if (kind === "halo") {
      if (!navigator.bluetooth) throw new Error("This browser has no Bluetooth for web pages. Use Chrome on Android or on a computer.");
      tell("Choose your glasses in the list the browser shows.");
      const { BrilliantBle } = await import(KIT);
      const next = new BrilliantBle();
      const name = (await next.connect()) || "Halo";
      next.setDisconnectHandler(() => { if (ble === next) disconnect("The glasses disconnected.", "out-of-range"); });
      ble = next;
      host = mirrored(haloHost(next, { onProblem: (e) => { if (link) link.problem("device", e.message); tell(`The glasses reported a problem: ${e.message}`, "bad"); } }));
      device = { maker: "brilliant", model: "halo", id: name };
    } else {
      host = previewHost(kind);
      device = { maker: "buddy", model: `preview ${kind}`, id: deviceId() };
    }
  } catch (e) {
    host = null; ble = null; busy(false);
    return tell(e && e.name === "NotFoundError" ? "No glasses were chosen." : `Could not connect to the glasses: ${e.message}`, "bad");
  }
  clearLens("Nothing yet. Ask Buddy to show something on your glasses, or press Test.");
  tell("Joining Buddy…");
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const socket = new WebSocket(`${proto}://${location.host}/ws/glasses?token=${encodeURIComponent(token)}`);
  socket.addEventListener("close", (e) => { if (e.code === 4401) tell("Buddy did not accept this page: open your Buddy link once in this browser, then come back.", "bad"); });
  link = connectLink(host, socket, { device, name: "Buddy", onState: (what, detail) => {
    if (what === "connected") { tell(`Connected: ${device.maker} ${device.model} is joined to Buddy.`, "good"); shown(true); keepAwake(); }
    else if (what === "refused") { tell(`Buddy refused: ${detail}`, "bad"); cleanup(); }
    else if (what === "closed" && link) { tell("Disconnected from Buddy.", "bad"); cleanup(); }
  } });
  busy(false);
}

function cleanup() {
  const old = host;
  link = null; host = null;
  if (old && old.close) Promise.resolve(old.close()).catch(() => {});
  if (ble) { const gone = ble; ble = null; Promise.resolve().then(() => gone.disconnect()).catch(() => {}); }
  if (wake) { wake.release().catch(() => {}); wake = null; }
  shown(false); busy(false);
}

function disconnect(note = "Not connected.", why = "off") {
  const old = link;
  link = null;
  if (old) old.close(why);
  cleanup();
  tell(note);
}

async function keepAwake() {
  try { if (navigator.wakeLock) wake = await navigator.wakeLock.request("screen"); } catch { /* not allowed: the page still works */ }
}

// --- the page's buttons ---------------------------------------------------------------------------------------
$("connect").addEventListener("click", connect);
$("disconnect").addEventListener("click", () => disconnect());
$("kind").addEventListener("change", () => {
  $("about").textContent = $("kind").value === "halo"
    ? "Brilliant Labs Halo, over Bluetooth from this device. Needs Chrome on Android or on a computer. Not yet tried on a real pair."
    : "A preview is a picture of the lens on this screen. It lets you try the link to Buddy without a pair of glasses.";
});
for (const button of document.querySelectorAll("[data-input]"))
  button.addEventListener("click", () => { if (host && host.input) host.input({ type: button.dataset.input }); });
function say() {
  const words = $("words").value.trim();
  if (words && host && host.input) host.input({ type: "voice", text: words });
  $("words").value = "";
}
$("say").addEventListener("click", say);
$("words").addEventListener("keydown", (e) => { if (e.key === "Enter") say(); });
$("test").addEventListener("click", async () => {
  $("testResult").textContent = "Sending…";
  try {
    const res = await fetch("/admin/glasses/test", { method: "POST", headers: { Authorization: `Bearer ${token}` } });
    const got = await res.json();
    $("testResult").textContent = !res.ok ? `Buddy answered with an error (${res.status}).`
      : got.ok ? `Buddy sent it. Shown: ${got.shown ? "yes" : "no"}. Spoken: ${got.spoken ? "yes" : "no"}. Held back: ${got.withheld}.`
      : got.why;
  } catch (e) { $("testResult").textContent = `Could not reach Buddy: ${e.message}`; }
});
window.addEventListener("pagehide", () => { if (link) link.close("off"); });
