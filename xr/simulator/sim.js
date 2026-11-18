// The Buddy XR simulator: run an app in the browser as it would appear on a pair of glasses. Pick the glasses,
// see what is in the lens, read what is said in the ear, and tap, swipe or speak as the wearer would.
//
//   mountSimulator(root, { makeApp, profiles, events, phrases, start })
//     makeApp()   returns a fresh app (called again on "Start again" and when the glasses change)
//     profiles    which glasses to offer: ids from PROFILES (default: the three classes)
//     events      [{ label, event }] buttons that send something from outside (run.notify)
//     phrases     things the wearer might say, offered as buttons
//
// This is a picture of the framework's output, not of any maker's optics: sizes and colours are approximate.

import { PROFILES, runApp } from "../src/index.js";

const CSS = `
.xrs { --ink:#0b1210; --line:rgba(127,227,196,.22); --glow:#7fe3c4; --dim:#8fa39c; --amber:#ffd08a; color:#e9f1ee;
  font:15px/1.45 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif; background:var(--ink); border:1px solid var(--line);
  border-radius:20px; padding:16px; max-width:100%; }
.xrs * { box-sizing:border-box; }
.xrs-top { display:flex; flex-wrap:wrap; gap:10px; align-items:center; margin-bottom:12px; }
.xrs-top label { color:var(--dim); font-size:13px; }
.xrs select, .xrs input[type=text] { background:#111c19; color:inherit; border:1px solid var(--line); border-radius:10px;
  padding:8px 10px; font:inherit; min-width:0; }
.xrs-about { color:var(--dim); font-size:13px; flex:1 1 220px; }
.xrs-grid { display:grid; grid-template-columns:minmax(0,1.5fr) minmax(0,1fr); gap:12px; }
@media (max-width:720px) { .xrs-grid { grid-template-columns:minmax(0,1fr); } }
.xrs-stage { position:relative; aspect-ratio:16/10; border-radius:16px; overflow:hidden; border:1px solid var(--line);
  background:radial-gradient(60% 50% at 22% 30%, rgba(255,196,120,.35), transparent 70%),
    radial-gradient(40% 40% at 78% 22%, rgba(255,226,170,.28), transparent 70%),
    radial-gradient(70% 60% at 60% 95%, rgba(64,96,86,.55), transparent 70%), linear-gradient(180deg,#2a2622,#151a18); }
.xrs-stage::after { content:""; position:absolute; inset:0; border-radius:16px; box-shadow:inset 0 0 0 10px rgba(0,0,0,.35), inset 0 0 90px rgba(0,0,0,.6); pointer-events:none; }
.xrs-tag { position:absolute; left:14px; bottom:12px; font-size:11.5px; letter-spacing:.06em; text-transform:uppercase; color:rgba(255,255,255,.55); z-index:2; }
.xrs-hud { position:absolute; right:6%; top:9%; width:56%; z-index:1; }
.xrs-hud.text { font:600 clamp(11px,1.9vw,15px)/1.5 ui-monospace,"SF Mono",Menlo,Consolas,monospace; color:var(--glow);
  text-shadow:0 0 8px rgba(127,227,196,.55); white-space:pre; }
.xrs-hud.text .t { opacity:.7; text-transform:uppercase; letter-spacing:.06em; }
.xrs-hud.text .on { color:#fff; }
.xrs-hud.text .alert { color:#fff; }
.xrs-hud.color { background:rgba(8,14,12,.72); border:1px solid rgba(127,227,196,.35); border-radius:14px; padding:12px 14px;
  backdrop-filter:blur(3px); font-size:clamp(12px,1.9vw,15px); }
.xrs-hud.color .t { font-weight:700; color:#fff; margin-bottom:6px; display:flex; justify-content:space-between; gap:8px; }
.xrs-hud.color .t small { color:var(--dim); font-weight:500; }
.xrs-hud.color .l { padding:2px 8px; border-radius:8px; white-space:pre-wrap; overflow-wrap:anywhere; }
.xrs-hud.color .l.choice { border:1px solid transparent; margin-top:3px; }
.xrs-hud.color .l.on { background:rgba(127,227,196,.18); border-color:rgba(127,227,196,.55); color:#fff; }
.xrs-hud.color .l.alert { background:rgba(127,227,196,.12); border:1px solid rgba(127,227,196,.45); color:#fff; font-weight:600; margin-bottom:4px; }
.xrs-hud.color .l.alert.warn { background:rgba(255,208,138,.1); border-color:rgba(255,208,138,.5); color:var(--amber); }
.xrs-hud.color .l.ask { font-weight:600; color:#fff; margin-top:4px; }
.xrs-hud.color .bar { height:6px; border-radius:3px; background:rgba(255,255,255,.14); margin:2px 8px 4px; }
.xrs-hud.color .bar i { display:block; height:100%; border-radius:3px; background:var(--glow); }
.xrs-hud.color .pic { border-radius:10px; border:1px dashed rgba(127,227,196,.45); padding:14px 10px; text-align:center; color:var(--dim); font-size:12.5px; margin-bottom:6px; }
.xrs-none { position:absolute; inset:0; display:flex; flex-direction:column; align-items:center; justify-content:center; text-align:center;
  padding:8% 10%; z-index:1; gap:10px; }
.xrs-none small { color:rgba(255,255,255,.6); font-size:12px; letter-spacing:.06em; text-transform:uppercase; }
.xrs-none p { margin:0; font-size:clamp(14px,2.4vw,19px); color:#fff; text-shadow:0 2px 12px rgba(0,0,0,.7); }
.xrs-side { display:flex; flex-direction:column; gap:12px; min-width:0; }
.xrs-box { border:1px solid var(--line); border-radius:14px; padding:12px; background:#0e1714; }
.xrs-box h3 { margin:0 0 8px; font-size:12px; letter-spacing:.06em; text-transform:uppercase; color:var(--dim); font-weight:600; }
.xrs-ear { max-height:150px; overflow:auto; display:flex; flex-direction:column; gap:6px; font-size:14px; }
.xrs-ear p { margin:0; color:var(--dim); } .xrs-ear p:last-child { color:#fff; }
.xrs-btns { display:flex; flex-wrap:wrap; gap:6px; }
.xrs button { font:inherit; font-size:13.5px; color:inherit; background:#13201c; border:1px solid var(--line); border-radius:999px;
  padding:7px 12px; cursor:pointer; }
.xrs button:hover, .xrs button:focus-visible { border-color:var(--glow); outline:none; }
.xrs button.main { background:var(--glow); color:#06110e; border-color:var(--glow); font-weight:600; }
.xrs button[disabled] { opacity:.4; cursor:default; }
.xrs-say { display:flex; gap:6px; margin-bottom:8px; } .xrs-say input { flex:1; }
.xrs-opt { display:flex; gap:8px; align-items:center; color:var(--dim); font-size:13px; margin-top:8px; }
.xrs-note { color:var(--dim); font-size:12.5px; margin:10px 2px 0; }
`;

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

export function mountSimulator(root, { makeApp, profiles = ["text", "color", "audio"], events = [], phrases = [], start } = {}) {
  if (!document.getElementById("xrs-css")) { const s = document.createElement("style"); s.id = "xrs-css"; s.textContent = CSS; document.head.appendChild(s); }
  let id = profiles.includes(start) ? start : profiles[0], run = null, frame = null, spoken = [], speakPersonal = false;

  root.classList.add("xrs");
  root.innerHTML = `
    <div class="xrs-top"><label for="xrs-device">Glasses</label>
      <select id="xrs-device">${profiles.map((p) => `<option value="${esc(p)}">${esc(PROFILES[p].name)}</option>`).join("")}</select>
      <span class="xrs-about" data-about></span><button data-reset>Start again</button></div>
    <div class="xrs-grid">
      <div class="xrs-stage" data-stage aria-live="polite"></div>
      <div class="xrs-side">
        <div class="xrs-box"><h3>In the ear</h3><div class="xrs-ear" data-ear></div></div>
        <div class="xrs-box"><h3>The wearer</h3>
          <div class="xrs-say"><input type="text" data-words placeholder="Say something" aria-label="Say something"><button class="main" data-speak>Say</button></div>
          <div class="xrs-btns" data-phrases></div>
          <div class="xrs-btns" data-touch style="margin-top:8px"></div>
          <label class="xrs-opt"><input type="checkbox" data-personal> Read personal details aloud</label></div>
        ${events.length ? `<div class="xrs-box"><h3>Meanwhile</h3><div class="xrs-btns" data-events></div></div>` : ""}
      </div>
    </div>
    <p class="xrs-note">A picture of what the framework sends to the glasses. Sizes and colours are approximate, and no real glasses are connected.</p>`;
  const $ = (sel) => root.querySelector(sel);

  const host = {
    get profile() { return PROFILES[id]; },
    show(f) { frame = f; draw(); },
    speak(text) { spoken.push(text); draw(); },
    on(listener) { host.listener = listener; },
  };

  function draw() {
    const p = PROFILES[id], stage = $("[data-stage]");
    if (!p.display) {
      stage.innerHTML = `<div class="xrs-none"><small>No display. The wearer hears</small><p>${esc(spoken[spoken.length - 1] || (p.speaker ? "…" : "nothing: these glasses cannot speak either"))}</p></div><span class="xrs-tag">Through the lens</span>`;
    } else if (frame) {
      const kind = p.display.kind;
      const line = (l) => {
        const cls = [l.kind === "item" || l.kind === "option" ? "choice" : l.kind, l.focused ? "on" : "", l.level === "warn" ? "warn" : ""].filter(Boolean).join(" ");
        if (kind === "text") return `<span class="${cls}">${esc(l.text)}</span>`;
        return `<div class="l ${cls}">${esc(l.text.replace(/^[› ] /, ""))}</div>${l.value != null ? `<div class="bar"><i style="width:${Math.round(l.value * 100)}%"></i></div>` : ""}`;
      };
      const [name, count] = frame.pages > 1 ? [frame.title.replace(/ \d+\/\d+$/, ""), `${frame.page + 1}/${frame.pages}`] : [frame.title, ""];
      stage.innerHTML = kind === "text"
        ? `<div class="xrs-hud text"><span class="t">${esc(frame.title)}</span>\n${frame.lines.map(line).join("\n")}</div><span class="xrs-tag">Through the lens</span>`
        : `<div class="xrs-hud color"><div class="t"><span>${esc(name)}</span><small>${esc(count)}</small></div>
            ${frame.image ? `<div class="pic">Picture: ${esc(frame.image.alt || frame.image.src)}</div>` : ""}${frame.lines.map(line).join("")}</div><span class="xrs-tag">Through the lens</span>`;
    }
    $("[data-ear]").innerHTML = !p.speaker ? `<p>These glasses have no speaker.</p>` : spoken.length ? spoken.slice(-6).map((s) => `<p>${esc(s)}</p>`).join("") : `<p>Nothing said yet.</p>`;
    $("[data-ear]").scrollTop = 1e6;
  }

  function begin() {
    if (run) run.stop();
    frame = null; spoken = [];
    const p = PROFILES[id];
    $("[data-about]").textContent = p.about;
    const touch = [["tap", "Tap", "next"], ["double", "Double tap", "choose"], ["long", "Long press", "back"]].filter(([t]) => p.input.includes(t))
      .concat(p.input.includes("swipe") ? [["swipe-back", "◀ Swipe", "previous"], ["swipe-forward", "Swipe ▶", "next"]] : []);
    $("[data-touch]").innerHTML = touch.length ? touch.map(([t, label, does]) => `<button data-touch-type="${t}" title="${does}">${label}</button>`).join("")
      : `<span style="color:var(--dim);font-size:13px">No tap or swipe on these: voice only.</span>`;
    $("[data-touch]").querySelectorAll("button").forEach((b) => b.onclick = () => host.listener({ type: b.dataset.touchType }));
    $("[data-words]").disabled = $("[data-speak]").disabled = !p.mic;
    run = runApp(makeApp(), host, { speakPersonal, onError: (e) => { spoken.push("(app error: " + e.message + ")"); draw(); } });
    draw();
  }

  const say = (text) => { if (text.trim()) host.listener({ type: "voice", text }); };
  $("[data-phrases]").innerHTML = phrases.map((p) => `<button data-phrase="${esc(p)}">“${esc(p)}”</button>`).join("");
  $("[data-phrases]").querySelectorAll("button").forEach((b) => b.onclick = () => say(b.dataset.phrase));
  $("[data-speak]").onclick = () => { say($("[data-words]").value); $("[data-words]").value = ""; };
  $("[data-words]").onkeydown = (e) => { if (e.key === "Enter") $("[data-speak]").click(); };
  $("[data-reset]").onclick = begin;
  $("#xrs-device").value = id;
  $("#xrs-device").onchange = (e) => { id = e.target.value; begin(); };
  $("[data-personal]").onchange = (e) => { speakPersonal = e.target.checked; begin(); };
  if (events.length) {
    $("[data-events]").innerHTML = events.map((ev, i) => `<button data-event="${i}">${esc(ev.label)}</button>`).join("");
    $("[data-events]").querySelectorAll("button").forEach((b) => b.onclick = () => run.notify(events[Number(b.dataset.event)].event));
  }
  begin();
  return { get run() { return run; }, pick(next) { id = next; $("#xrs-device").value = next; begin(); }, say, host };
}
