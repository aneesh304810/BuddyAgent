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
// Keep the token out of the address bar and browser history once it's remembered.
if (params.get("token") && storageGet("buddyToken") === token) history.replaceState(null, "", location.pathname);

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

// Result cards under a reply: photo, rating, why, the catches, and links to open.
const KIND_ICON = { place: "📍", stay: "🏠", flight: "✈️", travel: "🧳", news: "📰", finance: "📈", money: "💰", deal: "🏷️", reddit: "💬", link: "🔗", image: "🖼️", file: "📄" };

function showCards(cards) {
  const row = document.createElement("div");
  row.className = "cards";
  cards.forEach((c) => {
    const card = document.createElement("div");
    card.className = "card";
    if (c.image) {
      const img = document.createElement("img");
      img.src = c.image; img.alt = c.title || ""; img.loading = "lazy"; img.referrerPolicy = "no-referrer";
      img.onerror = () => img.remove();
      if (c.kind === "image") { img.className = "picture"; img.onclick = () => window.open(c.url, "_blank", "noopener"); }
      card.appendChild(img);
    }
    const body = document.createElement("div");
    body.className = "card-body";
    const add = (tag, cls, text) => { if (!text) return null; const e = document.createElement(tag); e.className = cls; e.textContent = text; body.appendChild(e); return e; };
    add("div", "card-title", `${KIND_ICON[c.kind] || "🔗"} ${c.title || ""}`);
    const rating = c.rating ? `★ ${c.rating}${c.reviews_count ? ` (${Number(c.reviews_count).toLocaleString()})` : ""}` : "";
    add("div", "card-meta", [rating, c.price].filter(Boolean).join(" · "));
    add("div", "card-sub", c.subtitle);
    add("div", "card-note", c.note);
    (c.rows || []).slice(0, 6).forEach((r) => add("div", "card-sub", `${r.label}: ${r.value}`));
    add("div", "card-sub", c.address);
    add("div", "card-text", c.text);
    const details = [...(c.catches || []).map((q) => ({ ...q, warn: true })), ...(c.reviews || [])];
    if (c.caution || details.length) {
      const more = document.createElement("details");
      const sum = document.createElement("summary");
      sum.textContent = (c.caution || (c.catches || []).length) ? "⚠️ Catches and reviews" : "Reviews";
      more.appendChild(sum);
      if (c.caution) { const p = document.createElement("p"); p.className = "warn"; p.textContent = c.caution; more.appendChild(p); }
      details.forEach((q) => {
        const p = document.createElement(q.url ? "a" : "p");
        if (q.url) { p.href = q.url; p.target = "_blank"; p.rel = "noopener noreferrer"; }
        p.className = q.warn ? "quote warn" : "quote";
        p.textContent = `“${q.says}” — ${q.from}`;
        more.appendChild(p);
      });
      body.appendChild(more);
    }
    const links = document.createElement("div");
    links.className = "card-links";
    const link = (label, url) => {
      if (!url || !url.startsWith("https://")) return;
      const a = document.createElement("a");
      a.href = url; a.target = "_blank"; a.rel = "noopener noreferrer"; a.textContent = label;
      links.appendChild(a);
    };
    link("Map", c.maps_url);
    link({ place: "Website", image: "Full size", file: c.ext === "pptx" ? "Open in PowerPoint" : "Open in Word" }[c.kind] || "Open", c.url);
    body.appendChild(links);
    card.appendChild(body);
    row.appendChild(card);
  });
  const anchor = buddyLine || logEl.lastElementChild;
  if (anchor) anchor.after(row); else logEl.appendChild(row);
  logEl.scrollTop = logEl.scrollHeight;
}

// Thumbs on a reply: shown per model and purpose in Insights. Tap again to take it back.
function addThumbs(line, turn) {
  const row = document.createElement("div");
  row.className = "thumbs";
  let rating = null;
  [[1, "\u{1F44D}"], [-1, "\u{1F44E}"]].forEach(([value, icon]) => {
    const b = document.createElement("button");
    b.textContent = icon;
    b.title = value > 0 ? "Good answer" : "Not good";
    b.onclick = () => {
      rating = rating === value ? null : value;
      if (!send({ type: "rate", turn, up: rating === null ? null : rating > 0 })) return;
      row.querySelectorAll("button").forEach((x) => x.classList.remove("on"));
      if (rating !== null) b.classList.add("on");
    };
    row.appendChild(b);
  });
  line.after(row);
}

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
    } else if (msg.type === "cards") {
      showCards(msg.cards || []);
    } else if (msg.type === "done") {
      if (buddyLine && msg.turn) addThumbs(buddyLine, msg.turn);
      buddyLine = null;
    } else if (msg.type === "nudge") {
      const line = addLine(msg.text, "buddy nudge");
      if (msg.job_id) addReportButton(line, msg.job_id);
      if (msg.kind === "health") {
        const btn = document.createElement("button");
        btn.className = "link";
        btn.textContent = "Open Insights";
        btn.onclick = () => showInsights();
        line.after(btn);
      }
      chime();
      setTimeout(() => speak(msg.text), 450);
    } else if (msg.type === "whisper") {
      const label = { translation: "🌐", about_you: "👀", correction: "💡", answer: "💭", budget: "💰" }[msg.kind] || "";
      addLine(`${label} ${msg.text}`, "buddy whisper");
      speak(msg.text, true);
    } else if (msg.type === "error") {
      addLine(msg.text, "error");
    } else if (msg.type === "thinking") {
      statusEl.textContent = "Got it, thinking…";
    } else if (msg.type === "approval") {
      showApproval(msg.question);
      speak(msg.text);
    }
  };
}

// Buddy asks before sending, booking or spending: one tap answers.
function showApproval(question) {
  const line = addLine(question, "buddy approval");
  const row = document.createElement("div");
  row.className = "approval";
  for (const [label, reply, cls] of [["Approve", "Yes, go ahead.", "yes"], ["Decline", "No, don't do it.", "no"]]) {
    const b = document.createElement("button");
    b.textContent = label;
    b.className = cls;
    b.onclick = () => { row.remove(); sendUser(reply); };
    row.appendChild(b);
  }
  line.appendChild(row);
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

// Typing works too: in a quiet office, or when speech recognition isn't available.
document.getElementById("typeForm").addEventListener("submit", (e) => {
  e.preventDefault();
  const box = document.getElementById("typeBox");
  const text = box.value.trim();
  if (text) { sendUser(text); box.value = ""; }
});

function interrupt() {
  stopSpeaking();
  muted = true;
  buddyLine = null;
  send({ type: "interrupt" });
}

// --- background job reports ------------------------------------------------
const reportDlg = $("report");

async function api(path, options = {}) {
  const res = await fetch(path, { ...options, headers: { Authorization: `Bearer ${token}` } });
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

// --- life log: import Google Timeline / Takeout, Airbnb and other exports ------
$("importBtn").onclick = () => $("importFile").click();
$("importFile").onchange = async (e) => {
  const file = e.target.files[0];
  e.target.value = "";
  if (!file) return;
  addLine(`Importing ${file.name} into your life log…`, "buddy nudge");
  try {
    const res = await fetch(`/api/import?name=${encodeURIComponent(file.name)}`, {
      method: "POST", headers: { Authorization: `Bearer ${token}` }, body: file,
    });
    const r = await res.json();
    if (!res.ok) throw new Error(r.detail || `error ${res.status}`);
    const kinds = Object.entries(r.by_kind).map(([k, n]) => `${n} ${k}${n === 1 ? "" : "s"}`).join(", ");
    addLine(r.added ? `Added ${r.added} to your life log (${kinds}). Ask me "where was I last March?"`
      : "Nothing new found in that file.", "buddy nudge");
  } catch (err) { addLine(`Couldn't import it (${err.message}).`, "error"); }
};

// --- what Buddy costs to run, all in (FinOps) -----------------------------------
const usd = (n) => `$${Number(n || 0).toFixed(2)}`;

function costTable(rows, head) {
  const table = document.createElement("table");
  table.className = "costs";
  const tr = (cells, tag) => {
    const row = document.createElement("tr");
    cells.forEach((c) => { const cell = document.createElement(tag); cell.textContent = c; row.appendChild(cell); });
    return row;
  };
  table.appendChild(tr(head, "th"));
  rows.forEach((r) => table.appendChild(tr(r, "td")));
  return table;
}

$("costsBtn").onclick = async () => {
  try {
    const r = await api("/admin/costs?fresh=true");
    $("reportTitle").textContent = `Costs · ${r.month}`;
    const budget = r.all_in_budget_usd ? ` · budget ${usd(r.all_in_budget_usd)}${r.over_budget ? " (over!)" : ""}` : "";
    $("reportSummary").textContent = `${usd(r.month_to_date_usd)} so far, heading for about ${usd(r.forecast_usd)}${budget}`;
    const body = $("reportBody");
    body.replaceChildren();
    const h = (t) => { const e = document.createElement("h3"); e.textContent = t; body.appendChild(e); };
    h("By provider");
    body.appendChild(costTable(r.lines.map((l) => [l.provider, usd(l.month_to_date), usd(l.forecast), l.source]),
      ["", "So far", "Month", "From"]));
    h("Claude, by what it did");
    const p = r.claude_by_purpose || {};
    const names = { chat_and_other: "Chat and agents", listen: "Listen-in", think: "Let me think", expert: "Expert", research: "Research", mail: "Email sorting", deals: "Price watches", lifelog: "Life log imports" };
    body.appendChild(costTable(Object.keys(names).filter((k) => k in p).map((k) => [names[k], usd(p[k])]), ["", "So far"]));
    if (p.web_searches) {
      const note = document.createElement("p");
      note.textContent = `Includes ${p.web_searches.count} web searches (${usd(p.web_searches.usd_included_above)}).`;
      body.appendChild(note);
    }
    const inv = r.digitalocean_invoices;
    if (inv && (inv.last_month || inv.this_month_so_far)) {
      h("DigitalOcean invoices");
      const months = [["This month so far", inv.this_month_so_far], ["Last month", inv.last_month]].filter(([, m]) => m);
      body.appendChild(costTable(months.map(([label, m]) => [`${label} (${m.period})`, usd(m.total)]), ["", "Total"]));
      for (const [label, m] of months) {
        if (!(m.by_product || []).length) continue;
        const rows = m.by_product.map((p) => [p.product, usd(p.amount)]);
        if (m.credits) rows.push(["Credits", usd(m.credits)]);
        if (m.taxes) rows.push(["Taxes", usd(m.taxes)]);
        h(`${label}: by product`);
        body.appendChild(costTable(rows, ["", "Amount"]));
      }
      if (inv.month_before) {
        const note = document.createElement("p");
        note.textContent = `The month before (${inv.month_before.period}): ${usd(inv.month_before.total)}.`;
        body.appendChild(note);
      }
    }
    if ((r.agents || []).length) {
      h("Agents");
      body.appendChild(costTable(r.agents.map((a) => [a.agent, String(a.calls_this_month), usd(a.external_cost_usd), a.note]),
        ["", "Calls", "Extra cost", ""]));
    }
    const route = r.routing || {};
    const turns = route.claude_turns || {};
    const free = route.free_model_calls || {};
    if (Object.keys(turns).length || Object.keys(free).length) {
      h("Which model answered");
      const turnNames = { quick: "Claude quick (Haiku)", main: "Claude main (Sonnet)", handed_over: "Quick handed to main" };
      const rows = Object.entries(turns).map(([k, n]) => [turnNames[k] || k, String(n)]);
      Object.entries(free).forEach(([k, n]) => rows.push([k === "fallback_claude" ? "Free busy, Claude did it" : `Free: ${k}`, String(n)]));
      body.appendChild(costTable(rows, ["", "This month"]));
    }
    if (!reportDlg.open) reportDlg.showModal();
  } catch (e) { addLine(`Couldn't load costs (${e.message}).`, "error"); }
};

const secs = (v) => (v == null ? "–" : `${Number(v).toFixed(1)}s`);
const tok = (n) => (n >= 1e6 ? `${(n / 1e6).toFixed(1)}M` : n >= 1e3 ? `${(n / 1e3).toFixed(1)}K` : String(n || 0));
const money = (n) => (n > 0 && n < 0.01 ? `$${Number(n).toFixed(4)}` : usd(n));
const insightsView = { days: 7, purpose: "", model: "" };

function quality(g) {
  const parts = [];
  const pct = (v) => (v == null ? "–" : `${Math.round(v * 100)}%`);
  if (g.reviewed) parts.push(`right as given ${pct(g.accuracy)}, usable ${pct(g.usable)} (${g.reviewed} checked)`);
  if (g.failed_check) parts.push(`${g.failed_check} failed the auto-check`);
  if (g.thumbs_up + g.thumbs_down) parts.push(`\u{1F44D} ${g.thumbs_up} \u{1F44E} ${g.thumbs_down}`);
  return parts.length ? " · " + parts.join(" · ") : "";
}

function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text != null) e.textContent = text;
  return e;
}

async function showInsights() {
  const v = insightsView;
  const q = new URLSearchParams({ days: v.days, recent: 100, purpose: v.purpose, model: v.model });
  let r;
  try { r = await api(`/admin/insights?${q}`); } catch (e) { addLine(`Couldn't load insights (${e.message}).`, "error"); return; }
  const t = r.totals;
  $("reportTitle").textContent = `Insights · last ${r.days === 1 ? "day" : `${r.days} days`}`;
  $("reportSummary").textContent = `${t.requests} model requests, ${money(t.cost_usd)}`;
  const body = $("reportBody");
  body.replaceChildren();
  const h = (text) => body.appendChild(el("h3", "", text));

  const ranges = el("div", "ranges");
  [[1, "Today"], [7, "7 days"], [30, "30 days"], [90, "90 days"]].forEach(([n, label]) => {
    const b = el("button", "link", label);
    if (v.days === n) b.style.color = "var(--text)";
    b.onclick = () => { v.days = n; showInsights(); };
    ranges.appendChild(b);
  });
  body.appendChild(ranges);

  const tiles = el("div", "tiles");
  const tile = (label, value, note) => {
    const d = el("div", "tile");
    d.append(el("span", "", label), el("b", "", value));
    if (note) d.appendChild(el("small", "", note));
    tiles.appendChild(d);
  };
  tile("Model spend", money(t.cost_usd));
  tile("Requests", String(t.requests), t.failed ? `${t.failed} failed` : "");
  tile("Typical reply", secs(t.chat_median_seconds), `slowest 5%: ${secs(t.p95_seconds)}`);
  tile("Free-model calls", String(t.free_requests), `of ${t.requests}`);
  tile("Cache reuse", t.cache_hit == null ? "–" : `${Math.round(t.cache_hit * 100)}%`, "of prompt tokens");
  tile("Saved by routing", money(t.saved_by_router_usd + t.saved_by_free_usd), "quick + free models");
  const pct = (v) => (v == null ? "–" : `${Math.round(v * 100)}%`);
  tile("Free answers right", pct(t.accuracy), `usable ${pct(t.usable)} · ${t.reviewed} checked by Claude`);
  tile("Your rating", pct(t.satisfaction), `\u{1F44D} ${t.thumbs_up}  \u{1F44E} ${t.thumbs_down}`);
  body.appendChild(tiles);

  h("How it's doing");
  const healthBox = el("div", "");
  body.appendChild(healthBox);
  const drawHealth = (data) => {
    healthBox.replaceChildren();
    const latest = data && data.latest;
    const label = { good: "All good", watch: "Worth watching", act: "Needs a fix" }[latest && latest.status] || "No check yet";
    const head = el("p", "", `${label}${latest ? ` · checked ${latest.at.slice(0, 16).replace("T", " ")} UTC` : ""}  `);
    const run = el("button", "link", "Check now");
    run.onclick = async () => {
      run.textContent = "Checking…";
      try { await api("/admin/health/run", { method: "POST" }); drawHealth(await api("/admin/health?history=14")); }
      catch (e) { run.textContent = `Failed (${e.message})`; }
    };
    head.appendChild(run);
    healthBox.appendChild(head);
    if (latest && !latest.findings.length) healthBox.appendChild(el("p", "", "Nothing got worse than the week before."));
    (latest ? latest.findings : []).forEach((f) => {
      const row = el("div", "call");
      row.appendChild(el("div", "", `${f.severity === "act" ? "●" : "○"} ${f.title}`));
      row.appendChild(el("div", "meta", f.area));
      row.appendChild(el("div", "more", `${f.detail}\nFix: ${f.fix}`));
      row.onclick = () => row.classList.toggle("open");
      healthBox.appendChild(row);
    });
    const past = (data && data.history) || [];
    if (past.length > 1) {
      healthBox.appendChild(costTable(past.slice(0, 7).map((x) => [x.at.slice(0, 10), x.status, secs(x.first_word_median),
        pct(x.accuracy), pct(x.satisfaction), money(x.cost_usd)]), ["Check", "", "First words", "Free right", "You", "Week $"]));
    }
  };
  api("/admin/health?history=14").then(drawHealth).catch(() => drawHealth(null));

  const sp = r.speed || {};
  h("Speed (from your side)");
  const speedTiles = el("div", "tiles");
  const stile = (label, value, note) => {
    const d = el("div", "tile");
    d.append(el("span", "", label), el("b", "", value));
    if (note) d.appendChild(el("small", "", note));
    speedTiles.appendChild(d);
  };
  const out = sp.outcomes || {};
  stile("First words", secs(sp.first_word_median), `slowest 5%: ${secs(sp.first_word_p95)}`);
  stile("Whole reply", secs(sp.total_median), `slowest 5%: ${secs(sp.total_p95)}`);
  stile("Replies", String(sp.replies || 0), `${(out.error || 0) + (out.too_many_steps || 0) + (out.refusal || 0) + (out.silent || 0)} failed · ${out.interrupted || 0} interrupted`);
  stile("Needed 'one sec'", pct(sp.filler_rate), `handed over ${pct(sp.handed_over_rate)} · ${sp.avg_rounds || 0} steps`);
  body.appendChild(speedTiles);
  if ((sp.by_route || []).length) {
    body.appendChild(costTable(sp.by_route.map((x) => [x.route, String(x.replies), secs(x.first_word_median), secs(x.total_median)]),
      ["Route", "Replies", "First words", "Whole"]));
  }
  if ((sp.slowest_tools || []).length) {
    body.appendChild(costTable(sp.slowest_tools.map((x) => [x.tool, String(x.replies), secs(x.total_median)]),
      ["Slowest replies used", "Replies", "Typical"]));
  }
  const exportRow = el("p", "", "Download for analysis: ");
  [["calls", "every request"], ["turns", "reply timings"]].forEach(([table, label]) => {
    const b = el("button", "link", label);
    b.onclick = async () => {
      const res = await fetch(`/admin/insights/export?table=${table}&days=${v.days}`, { headers: { Authorization: `Bearer ${token}` } });
      const a = document.createElement("a");
      a.href = URL.createObjectURL(await res.blob());
      a.download = `buddy-${table}.csv`;
      a.click();
    };
    exportRow.appendChild(b);
  });
  body.appendChild(exportRow);

  h("Spend per day");
  const dayNote = el("p", "", "");
  const bars = el("div", "days");
  const max = Math.max(...r.by_day.map((d) => d.cost_usd), 1e-9);
  const pick = (d, bar) => {
    dayNote.textContent = `${d.day}: ${money(d.cost_usd)} · ${d.requests} requests (${d.free_requests} free)`;
    bars.querySelectorAll(".on").forEach((b) => b.classList.remove("on"));
    bar.classList.add("on");
  };
  r.by_day.forEach((d) => {
    const bar = el("div");
    bar.style.height = `${Math.max(2, (d.cost_usd / max) * 100)}%`;
    bar.title = `${d.day}: ${money(d.cost_usd)}, ${d.requests} requests`;
    bar.onmouseenter = bar.onclick = () => pick(d, bar);
    bars.appendChild(bar);
  });
  body.append(dayNote, bars);
  if (r.by_day.length) pick(r.by_day[r.by_day.length - 1], bars.lastChild);

  const breakdown = (rows, label, detail, key, active, set, measure) => {
    const top = Math.max(...rows.map(measure), 1e-9);
    rows.forEach((g) => {
      const row = el("div", "call");
      if (active === key(g)) row.style.background = "var(--bg)";
      const head = el("div", "", "");
      const isFree = !g.cost_usd && ((g.provider && g.provider !== "claude") || String(g.name).startsWith("general:"));
      head.append(el("span", "", label(g)), el("span", "", `  ${isFree ? "free" : money(g.cost_usd)}`));
      const share = el("div", "share");
      const fill = el("i");
      fill.style.width = `${Math.max(1, (measure(g) / top) * 100)}%`;
      share.appendChild(fill);
      row.append(head, share, el("div", "meta", detail(g)));
      row.onclick = () => { set(active === key(g) ? "" : key(g)); showInsights(); };
      body.appendChild(row);
    });
  };
  h("What it was for");
  breakdown(r.by_purpose, (g) => g.label,
    (g) => `${g.requests} requests · ${secs(g.median_seconds)} typical · ` +
      Object.entries(g.models).map(([m, n]) => `${m} ×${n}`).join(", ") + quality(g),
    (g) => g.name, v.purpose, (k) => { v.purpose = k; v.model = ""; }, (g) => g.cost_usd);
  h("Each model");
  breakdown(r.by_model, (g) => `${g.model} (${g.provider})`,
    (g) => `${g.requests} requests${g.failed ? ` · ${g.failed} failed` : ""} · ${secs(g.median_seconds)} typical, ` +
      `${secs(g.p95_seconds)} slowest · ${tok(g.input_tokens)} in / ${tok(g.output_tokens)} out` +
      (g.web_searches ? ` · ${g.web_searches} searches` : "") + quality(g),
    (g) => g.model, v.model, (k) => { v.model = k; v.purpose = ""; }, (g) => g.requests);

  const fm = r.free_models;
  if (fm) {
    h("Free models");
    if (fm.note) body.appendChild(el("p", "", fm.note));
    if (fm.direct_answers) body.appendChild(el("p", "", "Simple general questions (\"what is…\", \"how do you say…\") go straight to these; anything personal stays on Claude."));
    body.appendChild(costTable(fm.providers.map((p) => [
      `${p.on ? "●" : "○"} ${p.name}`, p.model, p.on ? "on" : (p.key_set ? "key set, off" : "no key"),
      (p.first_choice_for || []).join(", "), String(p.requests), secs(p.median_seconds),
      p.accuracy == null ? "–" : `${Math.round(p.accuracy * 100)}%`]),
      ["", "Model", "", "First choice for", "Requests", "Typical", "Right"]));
  }
  h("Tools and agents");
  if (!(r.tools || []).length) body.appendChild(el("p", "", "No tools used in this period."));
  else body.appendChild(costTable(r.tools.map((t) => [
    t.tool.replace(/_/g, " "), { buddy: "Buddy", claude: "Claude (server)" }[t.agent] || `${t.agent} agent`,
    String(t.calls), t.failed ? String(t.failed) : "", secs(t.median_seconds),
    t.extra_usd ? money(t.extra_usd) : "", t.last_error || ""]),
    ["Tool", "Who", "Calls", "Failed", "Typical", "Extra $", "Last error"]));

  const filter = [v.purpose, v.model].filter(Boolean).join(" · ");
  h(filter ? `Latest requests: ${filter} (tap it above to clear)` : "Latest requests");
  if (!r.recent.length) body.appendChild(el("p", "", "Nothing yet in this period."));
  r.recent.forEach((c) => {
    const row = el("div", `call${c.ok ? "" : " failed"}`);
    row.appendChild(el("div", "", `${c.purpose_label} · ${c.provider === "claude" ? money(c.cost_usd) : "free"} · ${secs(c.seconds)}`));
    row.appendChild(el("div", "meta", `${c.at.slice(5, 19).replace("T", " ")} UTC · ${c.model}${c.ok ? "" : ` · ${c.stop}`}`));
    const more = [`Tokens: ${tok(c.input_tokens)} in, ${tok(c.output_tokens)} out` +
      (c.cache_read_tokens ? `, ${tok(c.cache_read_tokens)} from cache` : "")];
    if (c.tools) more.push(`Used: ${c.tools}`);
    const verdict = { used: "Claude used it as given", fixed: "Claude fixed it", redone: "Claude redid it", failed_check: "failed the auto-check" }[c.verdict];
    if (verdict) more.push(`Review: ${verdict}`);
    if (c.rating != null) more.push(`You: ${c.rating > 0 ? "\u{1F44D}" : "\u{1F44E}"}`);
    if (c.request) more.push(`Asked: ${c.request}`);
    if (c.reply) more.push(`Answered: ${c.reply}`);
    row.appendChild(el("div", "more", more.join("\n")));
    row.onclick = () => row.classList.toggle("open");
    body.appendChild(row);
  });
  if (!reportDlg.open) reportDlg.showModal();
}

$("insightsBtn").onclick = () => showInsights();

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
