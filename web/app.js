// Buddy voice client: browser speech recognition in, speech synthesis out.
// Audio routes through whatever the phone is using — your earbuds when connected.

const $ = (id) => document.getElementById(id);
const orb = $("orb"), statusEl = $("status"), logEl = $("log"), connEl = $("conn");
const handsfree = $("handsfree"), langSel = $("lang");
const mode = () => document.querySelector('input[name="mode"]:checked').value;

// --- token: open the page once as /?token=XYZ and it is remembered ---------
const params = new URLSearchParams(location.search);
function storageGet(k) { try { return localStorage.getItem(k); } catch { return null; } }
function storageSet(k, v) { try { localStorage.setItem(k, v); } catch {} }
if (params.get("token")) storageSet("buddyToken", params.get("token"));
const token = params.get("token") || storageGet("buddyToken") || "";

// --- transcript ------------------------------------------------------------
function addLine(text, cls) {
  const div = document.createElement("div");
  div.className = "msg " + cls;
  div.textContent = text;
  logEl.appendChild(div);
  logEl.scrollTop = logEl.scrollHeight;
  return div;
}
let buddyLine = null; // current reply bubble, extended sentence by sentence

// --- speaking --------------------------------------------------------------
const synth = window.speechSynthesis;
let pending = 0;      // utterances queued or playing
let muted = false;    // after an interrupt, drop leftover sentences of the old reply

function pickVoice() {
  const voices = synth.getVoices();
  return voices.find((v) => /en/i.test(v.lang) && /natural|neural|enhanced|premium|google/i.test(v.name))
      || voices.find((v) => /^en/i.test(v.lang)) || null;
}

function speak(text, quiet = false) {
  const u = new SpeechSynthesisUtterance(text);
  const voice = pickVoice();
  if (voice) u.voice = voice;
  u.rate = quiet ? 1.15 : 1.05;
  u.volume = quiet ? 0.6 : 1;
  pending++;
  refresh();
  u.onend = u.onerror = () => { pending = Math.max(0, pending - 1); refresh(); };
  synth.speak(u);
}

function stopSpeaking() {
  synth.cancel();
  pending = 0;
  refresh();
}

function chime() {
  try {
    const ctx = new (window.AudioContext || window.webkitAudioContext)();
    [880, 1320].forEach((f, i) => {
      const o = ctx.createOscillator(), g = ctx.createGain();
      o.frequency.value = f;
      g.gain.setValueAtTime(0.0001, ctx.currentTime + i * 0.12);
      g.gain.exponentialRampToValueAtTime(0.2, ctx.currentTime + i * 0.12 + 0.02);
      g.gain.exponentialRampToValueAtTime(0.0001, ctx.currentTime + i * 0.12 + 0.25);
      o.connect(g).connect(ctx.destination);
      o.start(ctx.currentTime + i * 0.12);
      o.stop(ctx.currentTime + i * 0.12 + 0.3);
    });
  } catch {}
}

// --- listening -------------------------------------------------------------
const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
let rec = null, listening = false, activeMode = "talk";

function startListening() {
  if (!Recognition) { statusEl.textContent = "This browser can't do speech recognition. Try Chrome or Safari."; return; }
  listening = true;
  rec = new Recognition();
  activeMode = mode();
  const listenIn = activeMode === "listen";
  rec.lang = listenIn ? langSel.value : (navigator.language || "en-US");
  rec.continuous = true;
  rec.interimResults = true;

  rec.onresult = (e) => {
    for (let i = e.resultIndex; i < e.results.length; i++) {
      const r = e.results[i], text = r[0].transcript.trim();
      if (!text) continue;
      if (listenIn) {
        // Other people talking: send it for analysis, never barge in on whispers.
        if (r.isFinal) send({ type: "ambient", text, lang: rec.lang });
        continue;
      }
      // Barge-in: talking over Buddy stops it. Needs a few words so stray
      // noise (or Buddy's own voice on a loudspeaker) doesn't trigger it.
      if (!r.isFinal && pending > 0 && text.split(/\s+/).length >= 3) interrupt();
      if (r.isFinal) {
        sendUser(text);
        if (!handsfree.checked) stopListening();
      }
    }
  };
  rec.onerror = (e) => {
    if (e.error === "not-allowed") { statusEl.textContent = "Microphone permission denied."; stopListening(); }
  };
  // Browsers end recognition after silence; keep it alive in hands-free mode.
  rec.onend = () => { if (listening) setTimeout(() => listening && rec && safeStart(), 250); };
  safeStart();
  refresh();
}
function safeStart() { try { rec.start(); } catch {} }

function stopListening() {
  if (listening && activeMode === "listen") send({ type: "ambient_stop" });
  listening = false;
  if (rec) { rec.onend = null; try { rec.stop(); } catch {} rec = null; }
  refresh();
}

// --- server connection -----------------------------------------------------
let ws = null, retry = 1000;

function connect() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  ws = new WebSocket(`${proto}://${location.host}/ws?token=${encodeURIComponent(token)}`);
  ws.onopen = () => {
    retry = 1000; connEl.textContent = "online"; connEl.classList.add("on");
    // Buddy follows you across time zones (US, South America, India…).
    ws.send(JSON.stringify({ type: "hello", timezone: Intl.DateTimeFormat().resolvedOptions().timeZone }));
  };
  ws.onclose = (e) => {
    connEl.textContent = e.code === 4401 ? "bad token" : "offline";
    connEl.classList.remove("on");
    if (e.code !== 4401) setTimeout(connect, retry = Math.min(retry * 2, 15000));
  };
  ws.onmessage = (e) => {
    const msg = JSON.parse(e.data);
    if (msg.type === "say" && !muted) {
      if (!buddyLine) buddyLine = addLine("", "buddy");
      buddyLine.textContent += (buddyLine.textContent ? " " : "") + msg.text;
      speak(msg.text);
    } else if (msg.type === "done") {
      buddyLine = null;
    } else if (msg.type === "nudge") {
      const line = addLine(msg.text, "buddy nudge");
      if (msg.job_id) addReportButton(line, msg.job_id);
      chime();
      setTimeout(() => speak(msg.text), 450);
    } else if (msg.type === "whisper") {
      const label = { translation: "🌐", about_you: "👀", correction: "💡", answer: "💭", budget: "💰" }[msg.kind] || "";
      addLine(`${label} ${msg.text}`, "buddy whisper");
      speak(msg.text, true);
    } else if (msg.type === "error") {
      addLine(msg.text, "error");
      speak(msg.text);
    }
  };
}

function send(obj) {
  if (ws && ws.readyState === WebSocket.OPEN) { ws.send(JSON.stringify(obj)); return true; }
  statusEl.textContent = "Not connected — retrying…";
  return false;
}

function sendUser(text) {
  stopSpeaking();
  muted = false;
  buddyLine = null;
  addLine(text, "user");
  send({ type: "user", text });
}

function interrupt() {
  stopSpeaking();
  muted = true;
  buddyLine = null;
  send({ type: "interrupt" });
}

// --- background job reports ------------------------------------------------
const reportDlg = $("report");

async function api(path) {
  const res = await fetch(path, { headers: { Authorization: `Bearer ${token}` } });
  if (!res.ok) throw new Error(res.status === 401 ? "bad token" : `error ${res.status}`);
  return res.json();
}

function addReportButton(line, id) {
  const btn = document.createElement("button");
  btn.className = "link";
  btn.textContent = "Open report";
  btn.onclick = () => openReport(id);
  line.appendChild(btn);
}

// Minimal, safe Markdown: headings, bullets, tables as text, links. Never innerHTML.
function renderMarkdown(md, into) {
  into.replaceChildren();
  let list = null;
  for (const raw of md.split("\n")) {
    const line = raw.trimEnd();
    if (!line.trim()) { list = null; continue; }
    const heading = line.match(/^#{1,6}\s+(.*)/);
    const bullet = line.match(/^\s*(?:[-*]|\d+\.)\s+(.*)/);
    let el;
    if (heading) { el = document.createElement("h3"); list = null; inline(heading[1], el); }
    else if (bullet) {
      if (!list) { list = document.createElement("ul"); into.appendChild(list); }
      el = document.createElement("li"); inline(bullet[1], el); list.appendChild(el); continue;
    } else if (line.startsWith("|")) {
      if (/^\|[\s:|-]+\|$/.test(line)) continue; // table divider row
      el = document.createElement("pre"); el.textContent = line.split("|").slice(1, -1).map((c) => c.trim()).join("  ·  ");
      list = null;
    } else { el = document.createElement("p"); list = null; inline(line, el); }
    into.appendChild(el);
  }
}

function inline(text, el) {
  const link = /\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g;
  let last = 0, m;
  const clean = (t) => t.replace(/\*\*|__|`/g, "");
  while ((m = link.exec(text))) {
    el.append(clean(text.slice(last, m.index)));
    const a = document.createElement("a");
    a.href = m[2]; a.textContent = m[1]; a.target = "_blank"; a.rel = "noopener noreferrer";
    el.append(a);
    last = m.index + m[0].length;
  }
  el.append(clean(text.slice(last)));
}

async function openReport(id) {
  try {
    const job = await api(`/api/jobs/${encodeURIComponent(id)}`);
    $("reportTitle").textContent = job.title;
    $("reportSummary").textContent = job.status === "done" ? (job.summary || "") : `${job.status}: ${job.error || ""}`;
    renderMarkdown(job.report || "", $("reportBody"));
    if (!reportDlg.open) reportDlg.showModal();
  } catch (e) { addLine(`Couldn't open the report (${e.message}).`, "error"); }
}

$("reportsBtn").onclick = async () => {
  try {
    const list = await api("/api/jobs");
    $("reportTitle").textContent = "Reports";
    $("reportSummary").textContent = list.length ? "" : "No research yet. Ask Buddy to research something.";
    const body = $("reportBody");
    body.replaceChildren();
    for (const job of list) {
      const row = document.createElement("div");
      row.className = "job";
      const title = document.createElement("div");
      title.textContent = job.title;
      const meta = document.createElement("small");
      meta.textContent = `${job.status} · ${new Date(job.created_at).toLocaleString()}`;
      row.append(title, meta);
      row.onclick = () => openReport(job.id);
      body.appendChild(row);
    }
    if (!reportDlg.open) reportDlg.showModal();
  } catch (e) { addLine(`Couldn't load reports (${e.message}).`, "error"); }
};

// --- UI --------------------------------------------------------------------
function refresh() {
  const listenIn = mode() === "listen";
  orb.classList.toggle("listening", listening && pending === 0);
  orb.classList.toggle("speaking", pending > 0);
  $("handsfreeRow").hidden = listenIn;
  $("langRow").hidden = !listenIn;
  statusEl.textContent = pending > 0 ? (listenIn ? "Whispering…" : "Buddy is talking — just speak to interrupt")
    : listening ? (listenIn ? "Listening to the room — say “Buddy, …” to ask me something" : "Listening…")
    : listenIn ? "Tap to start listening to the room" : "Tap the orb and start talking";
}

// Switching mode or room language restarts the microphone with the new setting.
function restartIfListening() {
  if (listening) { stopListening(); startListening(); } else refresh();
}
document.querySelectorAll('input[name="mode"]').forEach((el) => el.addEventListener("change", () => {
  if (listening) { stopListening(); } // stop in the old mode first
  refresh();
}));
langSel.addEventListener("change", restartIfListening);

let unlocked = false;
orb.addEventListener("click", () => {
  // iOS only allows speech after a user gesture; prime it on the first tap.
  if (!unlocked) { synth.speak(new SpeechSynthesisUtterance("")); unlocked = true; }
  if (pending > 0) { interrupt(); if (!listening) startListening(); return; }
  listening ? stopListening() : startListening();
});

synth.onvoiceschanged = () => {};
connect();
refresh();
