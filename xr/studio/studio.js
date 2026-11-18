// The Buddy XR Studio: write a glasses app in the browser, run it on each kind of glasses, and see where it would
// fail. Nothing is installed and nothing is sent anywhere: the code runs in this page, in this browser only.
//
// Code is only ever run when the person presses Run, and only code that is in the editor: what they typed or
// pasted, a starting point they picked, or their own draft kept in this browser. A link never carries code, so
// a link from someone else cannot run anything here. Paste only code you trust: it runs as part of this page.

import * as XR from "../src/index.js";
import { mountSimulator } from "../simulator/sim.js";
import { TEMPLATES } from "./templates.js";
import { prompt } from "./reference.js";

const LIB = new URL("../src/index.js", import.meta.url).href;
const DRAFT = "buddyxr-studio-draft";
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const keep = { get() { try { return JSON.parse(localStorage.getItem(DRAFT) || "null"); } catch { return null; } },
  set(v) { try { localStorage.setItem(DRAFT, JSON.stringify(v)); } catch { /* private window: the draft just is not kept */ } },
  clear() { try { localStorage.removeItem(DRAFT); } catch { /* nothing to clear */ } } };

let sim = null, app = null, lastUrl = "";

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

async function run() {
  problem("");
  const code = $("code").value;
  keep.set({ code, events: $("event").value });
  try { app = await load(code); } catch (e) { problem(e.name === "SyntaxError" ? `The code has a mistake: ${e.message}` : e.message); return; }
  const start = sim ? sim.host.profile.id : "text";
  $("sim").innerHTML = ""; $("sim").className = "";
  try {
    sim = mountSimulator($("sim"), { makeApp: () => app, profiles: ["text", "color", "audio"], start,
      phrases: ["next", "select", "yes", "no", "back"], onError: (e) => problem(`The app stopped: ${e.message}`) });
  } catch (e) { problem(`The app could not start: ${e.message}`); return; }
  report();
}

function pick(id) {
  const t = TEMPLATES.find((x) => x.id === id) || TEMPLATES[0];
  $("code").value = t.code; $("event").value = t.events; $("template").value = t.id;
  run();
}

$("template").innerHTML = TEMPLATES.map((t) => `<option value="${esc(t.id)}">${esc(t.name)}</option>`).join("");
$("template").onchange = (e) => pick(e.target.value);
$("run").onclick = run;
$("reset").onclick = () => { keep.clear(); pick($("template").value); };
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

// A link may name a starting point (#step3). It never carries code.
const asked = location.hash.slice(1), draft = keep.get();
if (TEMPLATES.some((t) => t.id === asked)) pick(asked);
else if (draft && typeof draft.code === "string") { $("code").value = draft.code; $("event").value = draft.events || ""; $("template").value = "blank"; run(); }
else pick("blank");
