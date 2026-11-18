// The Buddy XR Studio: build a glasses app, run it on each kind of glasses, and see where it would fail.
// Two ways in: "Build without code" (boxes to fill in and drag into order) and "Write code".
// Nothing is installed and nothing is sent anywhere: everything runs in this page, in this browser only.
//
// Code is only ever run when the person presses Run, and only code that is in the editor: what they typed or
// pasted, a starting point they picked, or their own draft kept in this browser. A link never carries code, so
// a link from someone else cannot run anything here. Paste only code you trust: it runs as part of this page.
// The no-code builder runs no code at all: its steps are plain data.

import * as XR from "../src/index.js";
import { mountSimulator } from "../simulator/sim.js";
import { TEMPLATES } from "./templates.js";
import { prompt } from "./reference.js";
import { mountBuilder } from "./builder-ui.js";

const LIB = new URL("../src/index.js", import.meta.url).href;
const DRAFT = "buddyxr-studio-draft", MODE = "buddyxr-studio-mode";
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const keep = { get(k) { try { return JSON.parse(localStorage.getItem(k) || "null"); } catch { return null; } },
  set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* private window: the draft just is not kept */ } },
  clear(k) { try { localStorage.removeItem(k); } catch { /* nothing to clear */ } } };

let sim = null, app = null, lastUrl = "", mode = "build", builder = null;

/** Turn the editor's text into an app: load it as a module, with "buddy-xr" pointing at the framework. */
async function load(code) {
  if (!/from\s+["']buddy-xr["']/.test(code)) throw new Error('The app must import its blocks: import { Screen, Text, defineApp } from "buddy-xr";');
  const other = code.match(/(?:from|import)\s*\(?\s*["'](?!buddy-xr["'])([^"']+)["']/);
  if (other) throw new Error(`Only "buddy-xr" can be imported in the Studio (found "${other[1]}").`);
  if (lastUrl) URL.revokeObjectURL(lastUrl);
  lastUrl = URL.createObjectURL(new Blob([code.replace(/(from\s+)["']buddy-xr["']/g, `$1"${LIB}"`)], { type: "text/javascript" }));
  const made = (await import(lastUrl)).default;
  if (!made || typeof made.view !== "function") throw new Error("The file must end with: export default defineApp({ ... })");
  return made;
}

function problem(text) { $("problem").textContent = text; $("problem").hidden = !text; }

function report() {
  const CLASSES = ["text", "color", "audio"];
  let found;
  try { found = XR.check(app); } catch (e) { $("check").innerHTML = `<p class="bad">The check could not run: ${esc(e.message)}</p>`; return; }
  const all = Object.keys(XR.PROFILES);
  const failing = all.filter((id) => !XR.works(found, id) && (XR.PROFILES[id].display || XR.PROFILES[id].speaker));
  const row = (id) => { const mine = found.filter((f) => f.profile === id);
    return `<div class="row"><b class="${XR.works(found, id) ? "ok" : "bad"}">${XR.works(found, id) ? "Works" : "Fails"}</b><span>${esc(XR.PROFILES[id].name)}</span></div>`
      + mine.map((f) => `<div class="finding ${f.level}">${esc(f.level === "info" ? "Note" : f.level === "warn" ? "Warning" : "Error")}: ${esc(f.text)}</div>`).join(""); };
  $("check").innerHTML = CLASSES.map(row).join("")
    + `<p class="sum">${failing.length ? `Fails on ${failing.length} of the ${all.length} kinds of glasses the framework knows.` : `Works on every kind of glasses that can show or say something (${all.length - 1} of ${all.length}; one kind can only listen).`}</p>`;
}

/** Put an app in the simulator and run the check on it. */
function show(made) {
  app = made;
  const start = sim ? sim.host.profile.id : "text";
  $("sim").innerHTML = ""; $("sim").className = "";
  try {
    sim = mountSimulator($("sim"), { makeApp: () => app, profiles: ["text", "color", "audio"], start,
      phrases: ["next", "select", "yes", "no", "back"], onError: (e) => problem(`The app stopped: ${e.message}`) });
  } catch (e) { sim = null; problem(`The app could not start: ${e.message}`); return; }
  report();
}

async function run() {
  problem("");
  const code = $("code").value;
  keep.set(DRAFT, { code, events: $("event").value });
  let made;
  try { made = await load(code); } catch (e) { problem(e.name === "SyntaxError" ? `The code has a mistake: ${e.message}` : e.message); return; }
  show(made);
}

function pick(id) {
  const t = TEMPLATES.find((x) => x.id === id) || TEMPLATES[0];
  $("code").value = t.code; $("event").value = t.events; $("template").value = t.id;
  return run();
}

function setMode(next) {
  mode = next === "code" ? "code" : "build";
  keep.set(MODE, mode);
  $("pane-build").hidden = mode !== "build"; $("pane-code").hidden = mode !== "code";
  $("outside-code").hidden = mode !== "code"; $("outside-build").hidden = mode !== "build";
  for (const b of document.querySelectorAll("[data-mode]")) { const on = b.dataset.mode === mode; b.classList.toggle("on", on); b.setAttribute("aria-selected", on); }
  problem("");
}

// --- write code -------------------------------------------------------------------------------------------------
$("template").innerHTML = TEMPLATES.map((t) => `<option value="${esc(t.id)}">${esc(t.name)}</option>`).join("");
$("template").onchange = (e) => pick(e.target.value);
$("run").onclick = run;
$("reset").onclick = () => { keep.clear(DRAFT); pick($("template").value); };
$("code").addEventListener("keydown", (e) => {
  if ((e.ctrlKey || e.metaKey) && e.key === "Enter") { e.preventDefault(); run(); }
  if (e.key === "Tab" && !e.shiftKey) { e.preventDefault(); const t = e.target, at = t.selectionStart; t.setRangeText("  ", at, t.selectionEnd, "end"); }
});
$("send").onclick = () => {
  if (!sim) return;
  let event;
  try { event = JSON.parse($("event").value); } catch { problem('The event must be JSON, for example: { "type": "parcel", "address": "9 Quay Street" }'); return; }
  problem(""); sim.run.notify(event);
};
$("save").onclick = () => {
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([$("code").value], { type: "text/javascript" })); a.download = "app.js";
  document.body.appendChild(a); a.click(); a.remove(); setTimeout(() => URL.revokeObjectURL(a.href), 1000);
};
$("copy").onclick = async () => {
  let text;
  try { text = prompt($("wanted").value); } catch (e) { $("copied").textContent = e.message; return; }
  try { await navigator.clipboard.writeText(text); $("copied").textContent = "Copied. Paste it into your assistant."; }
  catch {                                                  // no clipboard permission: put it where it can be copied by hand
    $("code").value = text; $("copied").textContent = "Could not copy by itself: the instructions are in the editor, select all and copy.";
  }
};

// --- build without code -----------------------------------------------------------------------------------------
$("message-send").onclick = () => { const text = $("message").value.trim(); if (sim && text) sim.run.notify({ text }); };
$("message").addEventListener("keydown", (e) => { if (e.key === "Enter") $("message-send").click(); });
$("launch-close").onclick = () => $("launch").close();

function startBuilder() {
  if (builder) return;
  builder = mountBuilder($("builder"), {
    onApp: (made) => { if (mode === "build") { problem(""); show(made); } },
    onNothing: () => { if (mode === "build") { $("sim").innerHTML = `<p class="hint" style="padding:20px">Fix what is marked on the left and the app will run here.</p>`; $("check").innerHTML = ""; sim = null; } },
  });
  builder.showCode.onclick = () => { $("code").value = builder.code(); $("event").value = '{ "text": "A message from the office." }'; setMode("code"); run(); };
  builder.launch.onclick = () => {
    const spec = builder.spec;
    $("launch-name").textContent = spec.name;
    $("launch-mail").href = "mailto:hello@buddylabs.io?subject=" + encodeURIComponent(`Launch list: ${spec.name}`)
      + "&body=" + encodeURIComponent(`I built "${spec.name}" (${spec.steps.length} steps) in the Buddy XR Studio and would like to launch it.\n\nMy kind of work:\nHow many people would use it:\nWhich glasses we have, if any:\n`);
    $("launch").showModal();
  };
}

for (const b of document.querySelectorAll("[data-mode]")) b.onclick = () => {
  setMode(b.dataset.mode);
  if (mode === "build") { startBuilder(); show(XR.buildApp(JSON.parse(JSON.stringify(builder.spec)))); } else if ($("code").value) run(); else pick("blank");
};

// A link may name a starting point (#step3) or the way in (#code, #build). It never carries code.
function route() {
  const asked = location.hash.slice(1), draft = keep.get(DRAFT);
  if (TEMPLATES.some((t) => t.id === asked)) { setMode("code"); return pick(asked); }
  if (asked === "code" || (asked !== "build" && keep.get(MODE) === "code")) {
    setMode("code");
    if (draft && typeof draft.code === "string") { $("code").value = draft.code; $("event").value = draft.events || ""; $("template").value = "blank"; return run(); }
    return pick("blank");
  }
  setMode("build"); startBuilder();
  if (builder && !XR.problems(builder.spec).length) show(XR.buildApp(JSON.parse(JSON.stringify(builder.spec))));
}
window.addEventListener("hashchange", route);
route();
