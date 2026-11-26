// Buddy Health: your own numbers from the ring and the phone, worked out by fixed rules on your server. No AI
// model is given any of it. Today's readings, each measure against your own usual, habits, food and the plan.
// Every piece of text is escaped.

const params = new URLSearchParams(location.search);
function storageGet(k) { try { return localStorage.getItem(k); } catch { return null; } }
function storageSet(k, v) { try { localStorage.setItem(k, v); } catch {} }
if (params.get("theme")) storageSet("buddyTheme", params.get("theme") === "light" ? "light" : "default");
if ((params.get("theme") || storageGet("buddyTheme")) === "light") document.documentElement.dataset.theme = "light";
if (params.get("token")) storageSet("buddyToken", params.get("token"));
const token = params.get("token") || storageGet("buddyToken") || "";
if (params.get("token") && storageGet("buddyToken") === token) history.replaceState(null, "", location.pathname);

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
// Six sections; the ones with sub-tabs show them as a second, smaller row at the top of the page.
const SECTIONS = [
  ["today", "Today", []],
  ["trends", "Trends", [["measures", "Measures"], ["body", "Body & BP"], ["patterns", "Patterns"], ["mood", "Mood"]]],
  ["labs", "Labs", [["results", "Results"], ["add", "Add a report"], ["portals", "Portals"], ["checkups", "Checkups"]]],
  ["plan", "Plan", [["habits", "Progress"], ["food", "Food"], ["menu", "Menu"], ["goals", "Goals & habits"]]],
  ["learn", "Learn", [["research", "Research"], ["supplements", "Supplements"], ["medicines", "Medicines"]]],
  ["sources", "Sources", []],
];
const TABS = SECTIONS.map(([k, label]) => [k, label]);
const OLD = { habits: ["plan", "habits"], food: ["plan", "food"], patterns: ["trends", "patterns"] };  // earlier tab names
const subsOf = (t) => (SECTIONS.find(([k]) => k === t) || [0, 0, []])[2];
function subOf(t) {
  const subs = subsOf(t), kept = storageGet("healthSub:" + t);
  if (subs.some(([k]) => k === kept)) return kept;
  return t === "plan" && data && !data.plan ? "goals" : (subs[0] || [""])[0];  // no plan yet: open on setting one up
}
function showing(t, s) { return tab === t && (!s || subOf(t) === s); }
let patData = null, patAsk = false, patBusy = false;
let moodData = null, moodPick = { mood: null, stress: null, energy: null, tags: [] };
async function loadMood(body) {
  try { const out = await api("/api/health/mood", body); moodData = out; if (out.text && body) notice(out.text); }
  catch (e) { moodData = moodData || { failed: e.message }; if (body) notice(e.message); }
  if (tab === "today" || showing("trends", "mood")) render();
}
function moodHistory() {
  if (!moodData) { $("main").innerHTML = `<p class="muted">Loading…</p>`; loadMood(); return; }
  if (moodData.failed) { $("main").innerHTML = `<p class="err">${esc(moodData.failed)}</p>`; return; }
  const k = (label, v) => kpi(v == null ? "–" : esc(v), label);
  const rows = moodData.entries.map((e) => { const extra = [e.rested != null ? `rested ${e.rested}` : "", e.people ? `in touch: ${e.people}` : "",
      e.via === "checkin" ? "check-in" : ""].filter(Boolean).join(" · ");
    return `<div class="line"><div class="what">${esc(when(e.day))} · mood ${e.mood ?? "–"} · stress ${e.stress ?? "–"} · energy ${e.energy ?? "–"}
      ${extra ? `<small>${esc(extra)}</small>` : ""}
      ${e.tags.length || e.note ? `<small>${esc(e.tags.join(", "))}${e.tags.length && e.note ? " · " : ""}${esc(e.note)}</small>` : ""}</div>
      <button class="btn danger" data-mood-del="${esc(e.id)}" aria-label="Delete this entry">✕</button></div>`; }).join("");
  $("main").innerHTML = `<div class="kpis">${k("Mood, last 7 days", moodData.week.mood)}${k("Stress, last 7 days", moodData.week.stress)}${k("Energy, last 7 days", moodData.week.energy)}${moodData.week.rested != null ? k("Rested, last 7 days", moodData.week.rested) : ""}</div>`
    + card("Your entries", rows || `<p class="muted">No entries yet: add one on Today.</p>`,
      (moodData.tags.length ? "Most often: " + moodData.tags.slice(0, 4).map(([t, n]) => `${esc(t)} (${n})`).join(", ") + ". " : "") + esc(moodData.note))
    + card("Evening check-in in chat", `<div class="line"><div class="what">${moodData.checkin_on ? "On" : "Off"}<small>On your first chat after 6 pm, Buddy asks your mood, stress and energy, whether you've been in touch with people, how rested you feel and anything on your mind. Your answers are read by code and go to no AI model. Say "check in" any time; say "skip" to stop. Spoken answers pass through the speech recognizer first, like every spoken word.</small></div>
        <button class="btn" id="mood-checkin">${moodData.checkin_on ? "Turn off" : "Turn on"}</button></div>`)
    + (rows ? `<p>${sure ? `<button class="btn danger solid" id="mood-forget" data-sure="1">Yes, delete every entry</button>` : `<button class="btn danger" id="mood-forget">Delete every entry</button>`}</p>` : "");
  $("main").querySelectorAll("[data-mood-del]").forEach((b) => b.onclick = () => loadMood({ action: "delete", id: Number(b.dataset.moodDel) }));
  if ($("mood-checkin")) $("mood-checkin").onclick = () => loadMood({ action: "checkin", on: !moodData.checkin_on });
  if ($("mood-forget")) $("mood-forget").onclick = () => {
    if (!$("mood-forget").dataset.sure) { sure = true; render(); return; }
    sure = false; loadMood({ action: "forget", confirm: true });
  };
}
function moodCard() {
  if (!moodData) { loadMood(); return ""; }
  if (moodData.failed) return card("How are you today?", `<p class="err">${esc(moodData.failed)}</p>`);
  const scale = (k, low, high) => `<div class="line" style="flex-wrap:wrap"><div class="what">${k[0].toUpperCase() + k.slice(1)}<small>1 ${low} · 5 ${high}</small></div>
      <div class="chips" style="overflow:visible">${[1, 2, 3, 4, 5].map((n) => `<button class="chip${moodPick[k] === n ? " on" : ""}" data-mood-k="${k}" data-mood-v="${n}">${n}</button>`).join("")}</div></div>`;
  const tags = `<div class="chips" style="flex-wrap:wrap;overflow:visible;margin:8px 0">${moodData.choices.map((t) => `<button class="chip${moodPick.tags.includes(t) ? " on" : ""}" data-mood-tag="${esc(t)}">${esc(t)}</button>`).join("")}</div>`;
  const w = moodData.week, today = moodData.days.find((d) => d.day === moodData.today);
  const recent = w.mood != null || w.stress != null || w.energy != null
    ? `Last 7 days: mood ${w.mood ?? "–"} · stress ${w.stress ?? "–"} · energy ${w.energy ?? "–"}${today ? ` · ${today.entries} today` : ""}. Every entry: Trends → Mood.` : "";
  return card("How are you today?", scale("mood", "low", "great") + scale("stress", "calm", "very stressed") + scale("energy", "drained", "full of energy")
      + `<p class="muted" style="margin:8px 0 0">What was going on?</p>` + tags
      + `<input id="mood-note" maxlength="500" placeholder="A note for yourself (optional)" style="width:100%" value="${esc(moodPick.note || "")}">`
      + `<p><button class="btn solid" id="mood-save">Save</button></p>`
      + (recent ? `<p class="muted" style="margin:10px 0 0">${esc(recent)}</p>` : ""),
    esc(moodData.note));
}
let srcData = null, srcAsk = "";  // the Sources tab, and the delete or disconnect waiting for a second tap
let data = null, tab = TABS.some(([k]) => k === storageGet("healthTab")) ? storageGet("healthTab") : "today";
if (OLD[storageGet("healthTab")]) { [tab] = OLD[storageGet("healthTab")]; storageSet("healthSub:" + tab, OLD[storageGet("healthTab")][1]); }
let span = ["14", "30", "90"].includes(storageGet("healthDays")) ? storageGet("healthDays") : "30", sure = false;

async function api(path, body) {
  const res = await fetch(path, body ? { method: "POST", headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" }, body: JSON.stringify(body) }
    : { headers: { Authorization: `Bearer ${token}` } });
  const out = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(out.detail || (res.status === 401 ? "Open Health from the Buddy app." : `Error ${res.status}`));
  return out;
}
function notice(text) {  // no alert(): the app's WebView doesn't show them
  const n = $("notice"); n.textContent = text; n.style.display = "block";
  clearTimeout(notice.t); notice.t = setTimeout(() => { n.style.display = "none"; }, 5000);
}
const card = (title, body, foot) => `<section class="card"><h2>${title}</h2>${body}${foot ? `<p class="note">${foot}</p>` : ""}</section>`;
const kpi = (value, label) => `<div class="kpi"><b>${value}</b><span>${esc(label)}</span></div>`;
const num = (v) => v == null ? "–" : Number(v).toLocaleString(undefined, { maximumFractionDigits: 1 });
const when = (iso) => { const d = new Date(String(iso).length === 10 ? iso + "T12:00:00" : iso); return isNaN(d) ? "" : d.toLocaleDateString(undefined, { month: "short", day: "numeric" }); };
const ago = (iso) => { const d = new Date(iso); if (!iso || isNaN(d)) return "not yet"; const h = Math.round((Date.now() - d) / 36e5);
  return h < 1 ? "under an hour ago" : h < 48 ? `${h} hours ago` : `${Math.round(h / 24)} days ago`; };

// A band is good or not depending on which way is better for that measure; the word is always shown.
function pill(band, better) {
  if (!band) return `<span class="pill">not enough days yet</span>`;
  const kind = band === "about usual" || !better ? "" : (band === "above usual") === (better === "up") ? " good" : " warn";
  return `<span class="pill${kind}">${esc(band)}</span>`;
}

function tabs() {
  $("tabs").innerHTML = TABS.map(([k, label]) => `<button class="chip${k === tab ? " on" : ""}" role="tab" aria-selected="${k === tab}" data-tab="${k}">${label}</button>`).join("");
  $("tabs").querySelectorAll("button").forEach((b) => b.onclick = () => { tab = b.dataset.tab; sure = false; storageSet("healthTab", tab); render(); });
}
function subTabs() {
  const subs = subsOf(tab), now = subOf(tab);
  if (!subs.length) return;
  $("main").insertAdjacentHTML("afterbegin", `<div class="subtabs" role="tablist">${subs.map(([k, label]) =>
    `<button class="chip${k === now ? " on" : ""}" role="tab" aria-selected="${k === now}" data-sub="${k}">${label}</button>`).join("")}</div>`);
  $("main").querySelectorAll("[data-sub]").forEach((b) => b.onclick = () => { storageSet("healthSub:" + tab, b.dataset.sub); sure = false; render(); });
}

async function act(body, done) {
  try { const out = await api("/api/health/page", body); await load(); if (done) notice(typeof done === "function" ? done(out) : done); }
  catch (e) { notice(e.message); }
}

function sources() {
  const s = data.sources;
  return card("Where the numbers come from",
    `<div class="line"><div class="what">Oura Ring<small>${s.ring.connected ? "Read " + esc(ago(s.ring.read)) : "Not connected: More → Connections → Health → Oura Ring"}</small></div>
       ${s.ring.connected ? `<button class="btn" id="read">Read the ring now</button>` : ""}</div>
     <div class="line"><div class="what">Phone (Health Connect)<small>${s.phone.read ? "Sent " + esc(ago(s.phone.read)) : "Nothing sent yet"}</small></div></div>`,
    esc(data.privacy.note));
}

// --- Sources: where your health data comes from, each with its syncs, what it sends and how each value is obtained
const STATE = { working: ["Working", "good"], unused: ["Connected, nothing yet", ""], expired: ["Sign in again", "warn"],
  failing: ["Last sync failed", "warn"], missing_keys: ["Half set up", "warn"], not_set_up: ["Not connected", ""],
  needs_approval: ["Needs approval", ""] };
const KIND = { initial: "first sync", incremental: "update", phone: "sent by the phone", manual: "you asked" };
const values = (n) => `${esc(n)} value${n === 1 ? "" : "s"}`;
async function loadSources() {
  try { srcData = (await api("/api/health/sources")).sources; } catch (e) { srcData = { failed: e.message }; }
  if (tab === "sources") render();
}
async function sourceAct(action, connector, confirm) {
  try { const out = await api("/api/health/sources", { action, connector, confirm: !!confirm }); srcData = out.sources; srcAsk = ""; render(); notice(out.text); }
  catch (e) { notice(e.message); }
}
function syncLine(r) {
  if (!r) return "never";
  return `${esc(ago(r.finished_at || r.started_at))} (${esc(KIND[r.kind] || r.kind)}${r.ok ? `: ${values(r.records)}, ${esc(r.new_records)} new${r.reached ? `, up to ${esc(when(r.reached))}` : ""}` : ""})`;
}
function sourceCard(s) {
  const [word, kind] = STATE[s.state] || [s.state, ""];
  const failed = s.last && !s.last.ok ? `<p class="err" style="padding:4px 0">Last sync failed: ${esc(s.last.error)}</p>` : "";
  const connect = s.state === "not_set_up" && s.can_disconnect ? `<p class="muted">To connect it: ${s.connector === "mychart" ? "Health → Labs → Portals" : `More → Connections → Health → ${esc(s.title)} → Connect`}.</p>` : "";
  const expired = s.state === "expired" ? `<p class="err" style="padding:4px 0">${esc(s.expired)}. Connect it again: More → Connections → Health → ${esc(s.title)} → Reconnect.</p>` : "";
  const measures = s.measures.map((m) => `<div class="line"><div class="what">${esc(m.label)}<small>${esc(m.how)}${m.loinc ? ` · LOINC ${esc(m.loinc)}` : ""} · ${esc(m.unit_words)}</small></div><span class="muted">${esc(m.days)} days</span></div>`).join("");
  const hist = (s.history || []).map((r) => `<div class="line"><div class="what">${esc(when(r.started_at))} · ${esc(KIND[r.kind] || r.kind)}<small>${r.ok ? `${values(r.records)}, ${esc(r.new_records)} new` : r.finished_at ? "failed: " + esc(r.error) : "running"}</small></div></div>`).join("");
  const ask = (action, label) => srcAsk === `${action}:${s.connector}`
    ? `<button class="btn danger solid" data-src="${action}" data-conn="${esc(s.connector)}" data-sure="1">Yes, ${esc(label.toLowerCase())}</button> <button class="btn" data-src-cancel="1">Keep it</button>`
    : `<button class="btn danger" data-src="${action}" data-conn="${esc(s.connector)}">${esc(label)}</button>`;
  const buttons = [s.can_sync && s.state !== "not_set_up" ? `<button class="btn" data-src="sync" data-conn="${esc(s.connector)}">Sync now</button>` : "",
    s.records ? ask("forget", "Delete its data") : "", s.can_disconnect && s.state !== "not_set_up" ? ask("disconnect", "Disconnect") : ""].filter(Boolean).join(" ");
  return card(`${esc(s.title)} <span class="pill ${kind}">${esc(word)}</span>`,
    connect + expired + failed +
    `<p class="muted">Last sync: ${syncLine(s.last)}${s.last_ok && s.last && !s.last.ok ? ` · last good: ${syncLine(s.last_ok)}` : ""}</p>
     ${(s.apps || []).length ? `<p class="muted">Apps that wrote to it lately: ${esc(s.apps.join(", "))}</p>` : ""}
     <p class="muted">Held: ${values(s.records)}${s.records ? `, ${esc(when(s.first))} to ${esc(when(s.newest))}` : ""}, kept ${s.keep_days ? `${esc(s.keep_days)} days` : "until you delete it"}.</p>
     <details><summary class="muted" style="cursor:pointer">What it sends (${esc(s.measures.length)})</summary>${measures}</details>
     <details><summary class="muted" style="cursor:pointer">Sync history</summary>${hist || `<p class="muted">No syncs yet.</p>`}</details>
     ${buttons ? `<p style="margin:10px 0 0">${buttons}</p>` : ""}`,
    `${s.terms ? esc(s.terms) + " " : ""}${s.can_disconnect ? "" : esc(s.stop)}`);
}
let freshData = null, freshBusy = false;
async function loadFresh(check) {
  if (check) { freshBusy = true; render(); }
  try { freshData = await api("/api/freshness", check ? {} : undefined); }
  catch (e) { freshData = freshData || { failed: e.message }; if (check) notice(e.message); }
  freshBusy = false;
  if (tab === "sources") render();
}
const FRESH = { current: ["Up to date", "good"], changed: ["Newer version", "warn"], not_set_up: ["Needs a key", ""], unknown: ["Couldn't check", ""] };
function freshCard() {
  if (!freshData) { loadFresh(); return ""; }
  if (freshData.failed) return "";
  const pill = (s) => { const [t, c] = FRESH[s] || [s, ""]; return `<span class="pill ${c}">${esc(t)}</span>`; };
  const u = freshData.update || {};
  const update = u.status ? `<div class="line" style="align-items:flex-start"><div class="what">Buddy itself<small>${esc(u.detail)}</small>
      ${u.command ? `<code style="display:block;margin-top:4px">${esc(u.command)}</code>` : ""}</div>${pill(u.status)}</div>` : "";
  const rows = freshData.watches.map((w) => `<div class="line" style="align-items:flex-start"><div class="what">${esc(w.title)}
      <small>Buddy has: ${esc(w.have)}${w.via ? ` · watched through ${esc(w.via)}` : ""}</small><small>${esc(w.detail)}</small>
      ${(w.papers || []).filter((p) => p.items && p.items.length || w.key !== "uspstf").slice(0, 3).map((p) => `<small>${p.url ? `<a href="${esc(p.url)}" target="_blank" rel="noopener">${esc(p.title)}</a>` : esc(p.title)} ${esc(p.date || "")}</small>`).join("")}
      ${w.hint && w.status !== "changed" ? `<small>${esc(w.hint)}</small>` : ""}</div>${pill(w.status)}</div>`).join("");
  return card("Is Buddy up to date?", update + rows
    + `<p style="margin:10px 0 0"><button class="btn" id="fresh-check"${freshBusy ? " disabled" : ""}>${freshBusy ? "Checking…" : "Check now"}</button>
       <span class="muted">${freshData.checked ? "Last checked " + esc(when(freshData.checked)) : "Not checked yet"}</span></p>`, esc(freshData.note));
}
function renderSources() {
  if (!srcData) { $("main").innerHTML = `<p class="muted">Loading…</p>`; loadSources(); return; }
  if (srcData.failed) { $("main").innerHTML = `<p class="err">${esc(srcData.failed)}</p>`; return; }
  $("main").innerHTML = freshCard() + (data ? sources() : "") + srcData.map(sourceCard).join("") +
    `<p class="note">Each value is kept with where it came from and how it was obtained: measured by a device, estimated by the maker's software (sleep stages, calories, scores), entered by you, or a lab result. A ring is not a lab. All of it stays on your server, and no AI model is given any of it.</p>`;
  $("main").querySelectorAll("[data-src]").forEach((b) => b.onclick = () => {
    const action = b.dataset.src, conn = b.dataset.conn;
    if (action === "sync") { b.disabled = true; b.textContent = "Syncing…"; sourceAct("sync", conn); return; }
    if (!b.dataset.sure) { srcAsk = `${action}:${conn}`; render(); return; }
    sourceAct(action, conn, true);
  });
  $("main").querySelectorAll("[data-src-cancel]").forEach((b) => b.onclick = () => { srcAsk = ""; render(); });
  if ($("read")) $("read").onclick = () => { $("read").disabled = true; $("read").textContent = "Reading…"; act({ action: "read" }, (o) => `Read ${o.days} days from the ring.`); };
  if ($("fresh-check")) $("fresh-check").onclick = () => loadFresh(true);
}

// --- Patterns: what, in your own days, tends to come before a better or worse next morning (learned by code)
async function loadPatterns(body) {
  try { patData = (await api("/api/health/patterns", body)).latest || {}; } catch (e) { patData = { failed: e.message }; }
  patBusy = false;
  if (showing("trends", "patterns")) render();
}
function renderPatterns() {
  if (!patData) { $("main").innerHTML = `<p class="muted">Loading…</p>`; loadPatterns(); return; }
  if (patData.failed) { $("main").innerHTML = `<p class="err">${esc(patData.failed)}</p>`; return; }
  const p = patData;
  const learnBtn = `<button class="btn solid" id="pat-learn"${patBusy ? " disabled" : ""}>${patBusy ? "Learning…" : "Learn now"}</button>`;
  if (!p.at) { $("main").innerHTML = card("Patterns in your days", `<p class="muted">Nothing learned yet. Buddy learns once a week (Sunday night) from the readings it keeps, or now.</p><p>${learnBtn}</p>`); wirePatterns(); return; }
  const rows = (p.patterns || []).map((x) => `<div class="line"><div class="what">${esc(x.text)}</div><span class="pill ${x.better ? "good" : "warn"}">${x.better ? "better" : "worse"}</span></div>`).join("");
  const models = (p.models || []).map((x) => `<div class="line"><div class="what">${esc(x.text)}
      ${x.passed ? `<small>What weighs most: ${x.weights.filter((w) => Math.abs(w.per_spread) >= 0.05).slice(0, 3).map((w) => `more ${esc(w.factor)} → ${w.per_spread > 0 ? "higher" : "lower"}`).join("; ")}</small>` : ""}</div>
      <span class="pill ${x.passed ? "good" : ""}">${x.passed ? "counts" : "not yet"}</span></div>`).join("");
  $("main").innerHTML = card("What tends to come before your next morning",
      (p.needs ? `<p class="muted">${esc(p.needs)}</p>` : "") + (rows || `<p class="muted">No pattern clears the bar yet: at least 10 days on each side, a real difference, and the same way in 9 of 10 redraws.</p>`),
      `Learned ${esc(ago(p.at))} from ${esc(p.days)} days. ${esc(p.note)}`) +
    (models ? card("Can your next morning be predicted?", models, "A small model of your factors, learned on your older days and tested on the newest ones it never saw. It counts only if it beats always guessing your average by 5% or more.") : "") +
    `<p>${learnBtn} ${patAsk ? `<button class="btn danger solid" id="pat-forget" data-sure="1">Yes, forget it</button>` : `<button class="btn danger" id="pat-forget">Forget what was learned</button>`}</p>`;
  wirePatterns();
}
function wirePatterns() {
  if ($("pat-learn")) $("pat-learn").onclick = () => { patBusy = true; render(); loadPatterns({ action: "learn" }); };
  if ($("pat-forget")) $("pat-forget").onclick = () => {
    if (!$("pat-forget").dataset.sure) { patAsk = true; render(); return; }
    patAsk = false; loadPatterns({ action: "forget", confirm: true });
  };
}

// --- Labs: a report is read by code on your server (never an AI), you check every result, then it is kept
let labData = null, labDraft = null, labAsk = 0, labBusy = false, portalData = null, portalFound = null, portalAsk = "";
async function loadPortals() {
  try { portalData = await api("/api/health/mychart", { action: "list" }); } catch (e) { portalData = { failed: e.message }; }
  if (tab === "labs") render();
}
function portalCard() {
  if (!portalData) { loadPortals(); return ""; }
  if (portalData.failed) return card("Patient portals (MyChart)", `<p class="err">${esc(portalData.failed)}</p>`);
  if (!portalData.set_up) return card("Patient portals (MyChart)",
    `<p class="muted">To read results straight from MyChart, register a free patient app once at fhir.epic.com (Epic on FHIR) with this return address, then set its client ID on the server:</p>
     <p><code>${esc(portalData.back || "https://<your tailnet name>/connect/mychart/linked")}</code></p>
     <p><code>sudo bash /opt/buddy/deploy/set.sh BUDDY_EPIC_CLIENT_ID</code></p>`, "Steps in docs/HEALTH.md. Until then, download the report as a PDF and add it above.");
  const rows = (portalData.portals || []).map((p) => `<div class="line"><div class="what">${esc(p.name)}
      <small>${p.connected ? `Connected${p.lasting ? "" : " (signs in each time)"} · read ${esc(ago(p.synced))}` : "Not signed in"}</small></div>
      ${p.connected ? "" : `<button class="btn" data-portal-connect="${esc(p.base)}" data-name="${esc(p.name)}">Sign in</button>`}
      ${portalAsk === p.base ? `<button class="btn danger solid" data-portal-off="${esc(p.base)}" data-sure="1">Yes, disconnect</button>` : `<button class="btn danger" data-portal-off="${esc(p.base)}">Disconnect</button>`}</div>`).join("");
  const found = (portalFound || []).map((f) => `<div class="line"><div class="what">${esc(f.name)}</div>
      <button class="btn" data-portal-connect="${esc(f.base)}" data-name="${esc(f.name)}">Sign in</button></div>`).join("");
  return card("Patient portals (MyChart)",
    `${rows}${rows ? `<p><button class="btn" id="portal-sync">Read new results now</button></p>` : ""}
     <div class="add" style="display:flex;gap:8px;margin-top:8px"><input id="portal-q" placeholder="Your health system, e.g. Hackensack" aria-label="Health system" style="flex:1;min-width:0">
     <button class="btn" id="portal-find">Find</button></div>
     ${portalFound ? (found || `<p class="muted">No health system by that name in Epic's list.</p>`) : ""}`,
    "You sign in at your health system; Buddy reads your lab results (read only) once a day and they join your results below. Nothing goes to an AI model.");
}
function wirePortals() {
  if ($("portal-find")) $("portal-find").onclick = async () => {
    try { portalFound = (await api("/api/health/mychart", { action: "find", q: $("portal-q").value })).found; render(); } catch (e) { notice(e.message); }
  };
  $("main").querySelectorAll("[data-portal-connect]").forEach((b) => b.onclick = async () => {
    try {
      const out = await api("/api/health/mychart", { action: "connect", base: b.dataset.portalConnect, name: b.dataset.name });
      const w = window.open(out.url, "_blank", "noopener");
      if (!w) location.href = out.url;
      notice(`Sign in at ${out.name}, then come back here.`); portalData = null; portalFound = null;
    } catch (e) { notice(e.message); }
  });
  $("main").querySelectorAll("[data-portal-off]").forEach((b) => b.onclick = async () => {
    if (!b.dataset.sure) { portalAsk = b.dataset.portalOff; render(); return; }
    try { notice((await api("/api/health/mychart", { action: "disconnect", base: b.dataset.portalOff, confirm: true })).text); } catch (e) { notice(e.message); }
    portalAsk = ""; portalData = null; render();
  });
  if ($("portal-sync")) $("portal-sync").onclick = async () => {
    $("portal-sync").disabled = true; $("portal-sync").textContent = "Reading…";
    try { notice((await api("/api/health/mychart", { action: "sync" })).text); } catch (e) { notice(e.message); }
    labData = null; portalData = null; render();
  };
}
async function loadLabs() {
  try { labData = await api("/api/health/labs"); } catch (e) { labData = { failed: e.message }; }
  if (tab === "labs") render();
}
const range = (r) => r.ref_text || (r.ref_low != null && r.ref_high != null ? `${r.ref_low}-${r.ref_high}` : r.ref_low != null ? `≥ ${r.ref_low}` : r.ref_high != null ? `≤ ${r.ref_high}` : "no range");
const bandPill = (b) => `<span class="pill${/above|below|flagged/.test(b) ? " warn" : / within/.test(b) ? " good" : ""}">${esc(b)}</span>`;
function draftCard() {
  const d = labDraft;
  const rows = d.results.map((r, i) => `<div class="line" style="align-items:flex-start">
      <input type="checkbox" data-lab-tick="${i}" checked aria-label="Keep ${esc(r.label)}" style="margin-top:4px">
      <div class="what">${esc(r.label)}<small>On the report: ${esc(r.line)}</small>
        <div style="display:flex;gap:6px;flex-wrap:wrap;margin-top:6px">
          <input data-lab-value="${i}" value="${esc(r.value_text)}" aria-label="${esc(r.label)} result" style="width:78px">
          <input data-lab-unit="${i}" value="${esc(r.unit)}" placeholder="unit" aria-label="${esc(r.label)} unit" style="width:84px">
          <span class="muted" style="align-self:center">range ${esc(range(r))}${r.flag ? " · flag " + esc(r.flag) : ""}</span></div></div>
      ${bandPill(r.band)}</div>`).join("");
  return card("Check what was read",
    `<p class="muted">Code on your server read ${esc(d.results.length)} results from ${esc(d.file_name)}${d.lab ? ` (${esc(d.lab)})` : ""}. Untick anything wrong, correct a number or unit, then keep. Tests Buddy doesn't know are not read.</p>
     <label class="muted">Sample taken on <input type="date" id="lab-collected" value="${esc(d.collected)}"></label>
     ${rows || `<p class="muted">No test Buddy knows was found on this report.</p>`}
     <p style="margin:10px 0 0">${d.results.length ? `<button class="btn solid" id="lab-save">Keep the ticked results</button> ` : ""}<button class="btn" id="lab-cancel">Don't keep anything</button></p>`);
}
function renderLabs(part = "results") {
  if (!labData) { $("main").innerHTML = `<p class="muted">Loading…</p>`; loadLabs(); return; }
  if (labData.failed) { $("main").innerHTML = `<p class="err">${esc(labData.failed)}</p>`; return; }
  const upload = card("Add a lab report",
    `<p class="muted">Download the report as a PDF from Quest, Labcorp or your portal (MyChart), then add it here. It is read by code on your server, never by an AI, and nothing is kept until you check it. The report itself goes into your vault, encrypted.</p>
     <p><input type="file" id="lab-file" accept="application/pdf,.pdf" aria-label="Lab report PDF"></p>
     <p><button class="btn solid" id="lab-read"${labBusy ? " disabled" : ""}>${labBusy ? "Reading…" : "Read the report"}</button></p>`);
  const results = (labData.results || []).map((t) => `<div class="line"><div class="what">${esc(t.label)}${t.other ? ` <small style="display:inline">(${esc(t.latest.loinc ? "LOINC " + t.latest.loinc : "other")})</small>` : ""}
      <small>${esc(t.latest.value_text)} ${esc(t.latest.unit)} · range ${esc(range(t.latest))} · ${esc(when(t.latest.collected))}${t.latest.lab ? " · " + esc(t.latest.lab) : ""} · ${esc(t.direction)}</small>
      ${t.earlier.length ? `<small>Before: ${t.earlier.slice(0, 4).map((e) => `${esc(e.value_text)} ${esc(e.unit)} (${esc(when(e.collected))})`).join(", ")}</small>` : ""}</div>
      ${bandPill(t.latest.band)}</div>`).join("");
  const reports = (labData.reports || []).map((r) => `<div class="line"><div class="what">${esc(when(r.collected))}${r.lab ? " · " + esc(r.lab) : ""}
      <small>${esc(r.file_name)} · ${esc(r.results)} results</small></div>
      ${r.has_file ? `<button class="btn" data-lab-open="${esc(r.id)}">Open</button>` : ""}
      ${labAsk === r.id ? `<button class="btn danger solid" data-lab-delete="${esc(r.id)}" data-sure="1">Yes, delete</button>` : `<button class="btn danger" data-lab-delete="${esc(r.id)}">Delete</button>`}</div>`).join("");
  const rangesNote = `<p class="note">Lab ranges for Claude are ${labData.ranges_on ? "on: Claude may be told whether each test is below, within or above the lab's range, never a value" : "off: no AI model is told anything about your lab results"}. Changed only on the server: ${esc(labData.ranges_how)}.</p>`;
  if (part === "add") { $("main").innerHTML = (labDraft ? draftCard() : upload) + rangesNote; wireLabs(); return; }
  if (part === "portals") { $("main").innerHTML = portalCard() || `<p class="muted">Loading…</p>`; wirePortals(); return; }
  $("main").innerHTML = (labDraft ? `<p class="note">A report is waiting for you to check it: Add a report.</p>` : "") +
    card("Your results", results || `<p class="muted">No results kept yet. Add a report, or connect a patient portal.</p>`,
      "Below, within or above is against the lab's own reference range printed on the report. When a report prints no range, Buddy uses a typical adult range for that test (marked “typical”); labs set their own, so the lab's always wins. Not medical advice: talk to your doctor about any result.") +
    (reports ? card("Reports", reports) : "") + rangesNote;
  wireLabs();
}
function wireLabs() {
  if ($("lab-read")) $("lab-read").onclick = () => {
    const file = $("lab-file").files[0];
    if (!file) { notice("Choose the report's PDF first."); return; }
    if (file.size > 15000000) { notice("That file is over 15 MB."); return; }
    const reader = new FileReader();
    reader.onload = async () => {
      labBusy = true; render();
      try { labDraft = (await api("/api/health/labs", { action: "read", name: file.name, data: String(reader.result).split(",")[1] || "" })).draft; }
      catch (e) { notice(e.message); }
      labBusy = false; render();
    };
    reader.onerror = () => notice("That file could not be read.");
    reader.readAsDataURL(file);
  };
  if ($("lab-cancel")) $("lab-cancel").onclick = async () => {
    try { notice((await api("/api/health/labs", { action: "cancel", draft: labDraft.id })).text); } catch (e) { notice(e.message); }
    labDraft = null; render();
  };
  if ($("lab-save")) $("lab-save").onclick = async () => {
    const rows = labDraft.results.map((r, i) => ({ ...r, value_text: $("main").querySelector(`[data-lab-value="${i}"]`).value.trim(),
      unit: $("main").querySelector(`[data-lab-unit="${i}"]`).value.trim(), keep: $("main").querySelector(`[data-lab-tick="${i}"]`).checked }))
      .filter((r) => r.keep);
    try { const out = await api("/api/health/labs", { action: "save", draft: labDraft.id, results: rows, collected: $("lab-collected").value });
      labDraft = null; labData = null; srcData = null; storageSet("healthSub:labs", "results"); notice(out.text); render(); }
    catch (e) { notice(e.message); }
  };
  $("main").querySelectorAll("[data-lab-delete]").forEach((b) => b.onclick = async () => {
    const id = Number(b.dataset.labDelete);
    if (!b.dataset.sure) { labAsk = id; render(); return; }
    try { notice((await api("/api/health/labs", { action: "delete", report: id, confirm: true })).text); } catch (e) { notice(e.message); }
    labAsk = 0; labData = null; srcData = null; render();
  });
  $("main").querySelectorAll("[data-lab-open]").forEach((b) => b.onclick = async () => {
    try {
      const res = await fetch(`/api/health/labs/report/${Number(b.dataset.labOpen)}`, { headers: { Authorization: `Bearer ${token}` } });
      if (!res.ok) throw new Error("The report could not be opened.");
      window.open(URL.createObjectURL(await res.blob()), "_blank", "noopener");
    } catch (e) { notice(e.message); }
  });
}

function habitLine(h) {
  const value = h.value == null ? "" : h.metric === "steps" ? num(h.value) + " steps" : h.metric === "sleep_hours" ? num(h.value) + " h"
    : h.metric === "active_minutes" ? num(h.value) + " min" : h.metric === "workouts" ? num(h.value) + " this week" : String(h.value);
  const target = h.target == null || h.metric === "manual" ? "" : " of " + (typeof h.target === "number" ? num(h.target) : h.target);
  const state = h.done === true ? `<span class="pill good">✓ done</span>` : h.done === false ? `<span class="pill warn">not yet</span>` : `<span class="pill">no reading</span>`;
  return `<div class="line"><div class="what">${esc(h.text)}<small>${esc(value)}${value ? esc(target) : ""}</small></div>${state}
    ${h.done === true ? "" : `<button class="btn" data-habit="${esc(h.id)}">I did this</button>`}</div>`;
}

function renderToday() {
  const r = data.today.readings, k = [];
  if (r.readiness != null) k.push(kpi(num(r.readiness), "Readiness"));
  if (r.sleep_minutes != null) k.push(kpi(num(Math.round(r.sleep_minutes / 6) / 10) + " h", "Sleep"));
  if (r.sleep_score != null) k.push(kpi(num(r.sleep_score), "Sleep score"));
  if (r.hrv != null) k.push(kpi(num(r.hrv) + " ms", "Heart rate variability"));
  if (r.lowest_hr != null) k.push(kpi(num(r.lowest_hr) + " bpm", "Lowest night heart rate"));
  if (r.temp_deviation != null) k.push(kpi((r.temp_deviation > 0 ? "+" : "") + num(r.temp_deviation) + " °C", "Temperature against normal"));
  const means = data.today.means.length ? `<ul style="margin:0;padding-left:18px">${data.today.means.map((m) => `<li>${esc(m)}</li>`).join("")}</ul>`
    : `<p class="muted">Nothing stands out today.</p>`;
  const habits = data.today.habits.length ? data.today.habits.map(habitLine).join("")
    : `<p class="muted">${data.plan ? "No habits are due today." : "No plan yet. Set one up under Plan → Goals & habits and its habits show here."}</p>`;
  $("main").innerHTML = (k.length ? `<div class="kpis">${k.join("")}</div>` : card("Today", `<p class="muted">No readings yet for today or yesterday.</p>`))
    + (k.length ? card("What it means for today", means, "Fixed rules against your own week. Not medical advice.") : "")
    + nowCard() + weekCard() + bpQuick() + card("Today's habits", habits) + moodCard();
  wire();
  wireBody();
}

// --- your health now, the week's targets, blood pressure, weight and waist (bodylog, prevent, activity, healthnow)
let bodyData = null, bodyBusy = false, bpSaid = null, bodySure = false;
async function loadBody(body) {
  if (body) bodyBusy = true;
  try { const out = await api("/api/health/body", body); bodyData = out; if (body && body.action === "bp") bpSaid = out.said; }
  catch (e) { if (body) notice(e.message); else bodyData = bodyData || { failed: e.message }; }
  bodyBusy = false;
  if (tab === "today" || showing("trends", "body")) render();
}
const LEVEL = { ok: ["OK", "good"], watch: ["Watch", "warn"], act: ["Act", "bad"], none: ["Needs data", ""] };
const lvl = (l) => { const [t, c] = LEVEL[l] || [l, ""]; return `<span class="pill ${c}">${esc(t)}</span>`; };
function nowCard() {
  if (!bodyData) { loadBody(); return card("Your health now", `<p class="muted">Loading…</p>`); }
  if (bodyData.failed) return "";
  const n = bodyData.now;
  const rows = n.rows.map((r) => `<div class="line" style="align-items:flex-start"><div class="what">${esc(r.label)}${r.value ? ` <b>${esc(r.value)}</b>` : ""}<small>${esc(r.detail)}</small></div>${lvl(r.level)}</div>`).join("");
  const watch = n.watch.length ? `<p style="margin:10px 0 4px"><b>To watch</b></p>${n.watch.map((w) => `<p class="why" style="margin:2px 0">${lvl(w.level)} ${esc(w.label)}: ${esc(w.detail)}</p>`).join("")}` : "";
  const todo = n.todo.length ? `<p style="margin:10px 0 4px"><b>What to do</b></p>${n.todo.map((t) => `<p class="why" style="margin:2px 0">• ${esc(t.text)} <i>${esc(t.why)}</i></p>`).join("")}` : "";
  const tests = n.tests.length ? `<details style="margin-top:8px"><summary class="why" style="cursor:pointer">Tests to ask your doctor about (${n.tests.length})</summary>${n.tests.map((t) => `<p class="why" style="margin:4px 0">• ${esc(t.text)} <i>(${esc(t.why)})</i></p>`).join("")}</details>` : "";
  return card("Your health now", rows + watch + todo + tests + `<p style="margin:10px 0 0"><button class="btn" id="go-body">Blood pressure, weight and risk</button></p>`, esc(n.note));
}
function weekCard() {
  if (!bodyData || bodyData.failed || !bodyData.week.has_data) return "";
  const w = bodyData.week, ST = { met: ["Done", "good"], on_track: ["On track", "good"], behind: ["Behind", "warn"], open: ["Open", ""], no_data: ["No data", ""] };
  const rows = w.items.map((i) => { const [t, c] = ST[i.status] || [i.status, ""];
    const pct = i.key === "sleep" ? (i.goal ? i.done / i.goal : 0) : Math.min(1, i.goal ? i.done / i.goal : 0);
    return `<div class="line" style="align-items:flex-start"><div class="what">${esc(i.label)} <b>${num(i.done)} of ${num(i.goal)}</b> <small>${esc(i.unit)}</small>
      <div style="height:6px;background:var(--track);border-radius:3px;margin:4px 0"><div style="height:6px;width:${Math.round(pct * 100)}%;background:var(--accent);border-radius:3px"></div></div>
      <small>${esc(i.say)}</small>${i.key === "strength" ? `<small><button class="btn" id="strength-log">${i.noted_today ? "Undo: strength today" : "I did strength today"}</button></small>` : ""}</div><span class="pill ${c}">${esc(t)}</span></div>`; }).join("");
  return card("This week", rows + (w.easy ? `<p class="why">${esc(w.easy)}</p>` : ""), esc(w.note) + " Buddy says what's missing in the morning briefing and the evening check-in.");
}
function bpQuick() {
  const said = bpSaid ? (bpSaid.alert ? `<p class="${bpSaid.level === "crisis" ? "err" : "why"}" style="padding:8px 0;font-weight:600">${esc(bpSaid.alert)}</p>` : `<p class="why">Saved: ${esc(bpSaid.reading.systolic)}/${esc(bpSaid.reading.diastolic)} (${esc(bpSaid.reading.period)}).</p>`) : "";
  return card("Blood pressure", `<form class="f" id="bp-form"><input class="narrow" name="systolic" inputmode="numeric" placeholder="Top" aria-label="Top number (systolic)" required>
      <span>/</span><input class="narrow" name="diastolic" inputmode="numeric" placeholder="Bottom" aria-label="Bottom number (diastolic)" required>
      <input class="narrow" name="pulse" inputmode="numeric" placeholder="Pulse" aria-label="Pulse (optional)"><button class="btn solid"${bodyBusy ? " disabled" : ""}>Save</button></form>${said}`,
    "Two readings a minute apart, morning and evening. Kept on your server; no AI model sees them.");
}
function renderBody() {
  if (!bodyData) { $("main").innerHTML = `<p class="muted">Loading…</p>`; loadBody(); return; }
  if (bodyData.failed) { $("main").innerHTML = `<p class="err">${esc(bodyData.failed)}</p>`; return; }
  const bp = bodyData.bp, b = bodyData.body, r = bodyData.risk, t = bodyData.targets;
  const wk = bp.week ? `<div class="kpis">${kpi(`${bp.week.systolic}/${bp.week.diastolic}`, "This week's average")}${bp.morning ? kpi(`${bp.morning.systolic}/${bp.morning.diastolic}`, `Mornings (${bp.morning.count})`) : ""}${bp.evening ? kpi(`${bp.evening.systolic}/${bp.evening.diastolic}`, `Evenings (${bp.evening.count})`) : ""}${bp.week.pulse ? kpi(bp.week.pulse, "Pulse") : ""}</div>` : "";
  const cat = bp.enough ? `<p><b>${esc(bp.category.label)}</b>${bp.change != null ? ` · ${bp.change > 0 ? "+" : ""}${bp.change} on last week (top number)` : ""}</p><p class="why">${esc(bp.next)}</p>${bp.pattern ? `<p class="why">${esc(bp.pattern)}</p>` : ""}<p class="why">${esc(bp.target)}</p>` : `<p class="muted">${esc(bp.needed)}</p>`;
  const list = bp.readings.map((x) => `<div class="line"><div class="what">${esc(x.systolic)}/${esc(x.diastolic)}${x.pulse ? ` · pulse ${esc(x.pulse)}` : ""}<small>${esc(when(x.at))} ${esc(x.at.slice(11, 16))} · ${esc(x.period)}${x.source === "health_connect" ? " · from your cuff app" : ""}</small></div>${x.source === "typed" ? `<button class="btn" data-bp-del="${x.id}" aria-label="Delete this reading">Delete</button>` : ""}</div>`).join("");
  const howto = `<details><summary class="why" style="cursor:pointer">How to measure</summary>${bp.how_to.map((h) => `<p class="why" style="margin:2px 0">• ${esc(h)}</p>`).join("")}</details>`;
  const bpCard = card("Blood pressure", wk + cat + bpQuickForm() + howto + (list ? `<details><summary class="why" style="cursor:pointer">Readings (${bp.readings.length})</summary>${list}</details>` : ""), esc(bp.note));
  const bmi = b.bmi ? `<p><b>BMI ${esc(b.bmi.value)}</b> · ${esc(b.bmi.label)}</p><p class="why">A healthy weight for your height: ${num(b.bmi.healthy_kg[0])} to ${num(b.bmi.healthy_kg[1])} kg (${esc(b.bmi.why)}).</p>${b.trend ? `<p class="why">${esc(b.trend.text)}</p>` : ""}` : "";
  const waist = b.waist ? `<p><b>Waist ${num(b.waist.value)} cm</b> · ${esc(b.waist.label)} of ${esc(b.waist.cut)} cm (${esc(b.waist.why)})${b.waist.ratio ? ` · waist to height ${esc(b.waist.ratio.value)}: ${esc(b.waist.ratio.label)}` : ""}</p>` : "";
  const bodyCard = card("Weight and waist", (b.weight ? `<p>Weight <b>${num(b.weight.value)} kg</b> <span class="muted">${b.weight.day ? esc(when(b.weight.day)) : ""}${b.weight.source === "entered" ? " · typed" : ""}</span></p>` : "") + bmi + waist
    + (b.missing.length ? `<p class="muted">Still needed: ${esc(b.missing.join("; "))}</p>` : "")
    + `<form class="f" id="weight-form"><input class="narrow" name="value" inputmode="decimal" placeholder="Weight" aria-label="Weight" required><select name="unit" aria-label="Unit"><option value="kg">kg</option><option value="lb">lb</option></select><button class="btn solid">Save weight</button></form>
       <form class="f" id="waist-form" style="margin-top:6px"><input class="narrow" name="value" inputmode="decimal" placeholder="Waist" aria-label="Waist" required><select name="unit" aria-label="Unit"><option value="cm">cm</option><option value="in">in</option></select><button class="btn solid">Save waist</button></form>
       <p class="why">${esc(b.waist_how)}</p>`, esc(b.note));
  const i = r.inputs || {};
  const riskBody = r.status === "ok" ? `<div class="kpis">${kpi(`${r.cvd}%`, "Heart attack, stroke or heart failure in 10 years")}${kpi(`${r.ascvd}%`, "Heart attack or stroke")}</div><p><b>${esc(r.label)}</b></p>
      ${(r.moves || []).map((m) => `<p class="why">• With ${esc(m.if)}: ${esc(m.cvd)}% (${esc(m.less)} less)</p>`).join("")}
      <details><summary class="why" style="cursor:pointer">What it rests on</summary><p class="why">Age ${esc(i.age)}, ${esc(i.sex)}; total cholesterol ${esc(i.total_c)} and HDL ${esc(i.hdl)} mg/dL (${esc(i.lipids_at)}); top blood pressure number ${esc(i.sbp)} (${esc(i.sbp_how)}); eGFR ${esc(i.egfr)} (${esc(i.egfr_how)}); diabetes ${i.diabetes ? "yes" : "no"}; smoking ${i.smoker ? "yes" : "no"}; blood pressure medicine ${i.bp_meds ? "yes" : "no"}; statin ${i.statin ? "yes" : "no"}${i.asked_meds ? "" : " (tick them on Labs → Checkups if you take them)"}.</p></details>`
    : `<p class="muted">${esc(r.detail)}</p>`;
  const riskCard = card("10-year heart and stroke risk (PREVENT)", riskBody, esc(r.note));
  const tCard = card("Your targets", `<form class="f" id="targets-form"><label class="pick">Cardio <input class="narrow" name="cardio" inputmode="numeric" value="${esc(t.cardio)}"> min a week</label>
      <label class="pick">Strength <input class="narrow" name="strength" inputmode="numeric" value="${esc(t.strength)}"> a week</label>
      <label class="pick">Steps <input class="narrow" name="steps" inputmode="numeric" value="${esc(t.steps)}"> a day</label>
      <label class="pick">Sleep <input class="narrow" name="sleep_h" inputmode="decimal" value="${esc(t.sleep_h)}"> hours</label><button class="btn solid">Save</button></form>`,
    "Physical Activity Guidelines: 150 to 300 moderate minutes a week (a vigorous minute counts as two), strength on 2 or more days.");
  $("main").innerHTML = bpCard + bodyCard + riskCard + weekCard() + tCard
    + `<p><button class="btn danger" id="body-forget">${bodySure ? "Press again to delete them all" : "Delete everything typed here"}</button> <span class="muted">Blood pressure readings, typed weights and waists, risk estimates and strength sessions noted.</span></p>`;
  wireBody();
  $("body-forget").onclick = () => { if (!bodySure) { bodySure = true; render(); return; } bodySure = false; loadBody({ action: "forget" }); };
}
function bpQuickForm() {
  return `<form class="f" id="bp-form" style="margin:8px 0"><input class="narrow" name="systolic" inputmode="numeric" placeholder="Top" aria-label="Top number (systolic)" required>
    <span>/</span><input class="narrow" name="diastolic" inputmode="numeric" placeholder="Bottom" aria-label="Bottom number (diastolic)" required>
    <input class="narrow" name="pulse" inputmode="numeric" placeholder="Pulse" aria-label="Pulse (optional)"><button class="btn solid"${bodyBusy ? " disabled" : ""}>Save reading</button></form>
    ${bpSaid && bpSaid.alert ? `<p class="${bpSaid.level === "crisis" ? "err" : "why"}" style="padding:8px 0;font-weight:600">${esc(bpSaid.alert)}</p>` : ""}`;
}
function wireBody() {
  if ($("bp-form")) $("bp-form").onsubmit = (e) => { e.preventDefault(); const d = new FormData(e.target);
    loadBody({ action: "bp", systolic: d.get("systolic"), diastolic: d.get("diastolic"), pulse: d.get("pulse") }); };
  for (const what of ["weight", "waist"]) if ($(`${what}-form`)) $(`${what}-form`).onsubmit = (e) => { e.preventDefault(); const d = new FormData(e.target);
    loadBody({ action: what, value: d.get("value"), unit: d.get("unit") }); };
  if ($("targets-form")) $("targets-form").onsubmit = (e) => { e.preventDefault(); const d = new FormData(e.target);
    loadBody({ action: "targets", targets: Object.fromEntries(["cardio", "strength", "steps", "sleep_h"].map((k) => [k, d.get(k)])) }); };
  if ($("strength-log")) $("strength-log").onclick = () => { const on = !bodyData.week.items.find((i) => i.key === "strength").noted_today; loadBody({ action: "strength", on }); };
  $("main").querySelectorAll("[data-bp-del]").forEach((b) => b.onclick = () => loadBody({ action: "bp_delete", id: Number(b.dataset.bpDel) }));
  if ($("go-body")) $("go-body").onclick = () => { tab = "trends"; storageSet("healthTab", "trends"); storageSet("healthSub:trends", "body"); render(); };
}

function spark(m) {
  const pts = m.series.map((p, i) => [i, p.v]).filter((p) => p[1] != null);
  if (pts.length < 2) return `<p class="muted">Not enough days to draw yet.</p>`;
  const vals = pts.map((p) => p[1]).concat(m.usual == null ? [] : [m.usual]);
  const lo = Math.min(...vals), hi = Math.max(...vals), w = 300, h = 70, n = Math.max(m.series.length - 1, 1);
  const x = (i) => (i / n) * w, y = (v) => hi === lo ? h / 2 : 6 + (1 - (v - lo) / (hi - lo)) * (h - 12);
  const path = pts.map((p, i) => `${i ? "L" : "M"}${x(p[0]).toFixed(1)} ${y(p[1]).toFixed(1)}`).join(" ");
  const last = pts[pts.length - 1];
  return `<svg class="spark" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" role="img" aria-label="${esc(m.label)} over ${m.series.length} days: from ${num(lo)} to ${num(hi)}">
    ${m.usual == null ? "" : `<line class="u" x1="0" x2="${w}" y1="${y(m.usual).toFixed(1)}" y2="${y(m.usual).toFixed(1)}"/>`}
    <path class="l" d="${path}" vector-effect="non-scaling-stroke"/><circle cx="${x(last[0]).toFixed(1)}" cy="${y(last[1]).toFixed(1)}" r="3"/></svg>`;
}

function renderTrends() {
  const pick = `<div class="chips" style="margin:0 0 12px">${["14", "30", "90"].map((d) => `<button class="chip${d === span ? " on" : ""}" data-span="${d}">${d} days</button>`).join("")}</div>`;
  const cards = data.measures.map((m) => `<section class="card"><h2>${esc(m.label)}</h2>
      <div class="m-top"><b>${num(m.latest)}${m.unit ? " " + esc(m.unit) : ""}</b><span>${esc(when(m.latest_day))}</span></div>
      ${spark(m)}
      <div class="line"><div class="what">Latest</div>${pill(m.latest_band, m.better)}</div>
      <div class="line"><div class="what">This week<small>average ${num(m.week)}${m.unit ? " " + esc(m.unit) : ""}</small></div>${pill(m.week_band, m.better)}</div>
      <p class="note">${m.usual == null ? "Your usual needs seven earlier days of readings." : "Your usual: " + num(m.usual) + (m.unit ? " " + esc(m.unit) : "") + " (dashed line), from the days before this week."}</p></section>`).join("");
  $("main").innerHTML = pick + (cards ? `<div class="measures">${cards}</div>` : card("Trends", `<p class="muted">No readings yet. Connect the ring or allow the phone's health data and they show here.</p>`))
    + `<p class="note">${esc(data.note)}</p>`;
  $("main").querySelectorAll("[data-span]").forEach((b) => b.onclick = () => { span = b.dataset.span; storageSet("healthDays", span); load(); });
}

function renderHabits() {
  if (!data.plan) { $("main").innerHTML = card("Progress", `<p class="muted">No plan yet. Set one up under Goals & habits.</p>`); return; }
  const rows = (list) => list.map((h) => `<div class="line"><div class="what">${esc(h.habit)}
      <small>${h.of_days ? `${h.done_days} of ${h.of_days} ${h.per}s` : "no readings yet"}${h.streak > 1 ? ` · ${h.streak} in a row` : ""}</small>
      <div class="bar"><i style="width:${h.rate || 0}%"></i></div></div><div class="num">${h.rate == null ? "–" : h.rate + "%"}</div></div>`).join("");
  const s = data.progress.start, now = Object.fromEntries(data.measures.map((m) => [m.key, m.week]));
  const since = [["steps", "Steps", 1], ["sleep_minutes", "Sleep (h)", 60], ["readiness", "Readiness", 1], ["hrv", "Heart rate variability", 1], ["lowest_hr", "Lowest night heart rate", 1]]
    .filter(([k]) => s[k] != null && now[k] != null)
    .map(([k, label, div]) => `<div class="line"><div class="what">${label}<small>when the plan started: ${num(Math.round(s[k] / div * 10) / 10)}</small></div><div class="num">${num(now[k])} now</div></div>`).join("");
  $("main").innerHTML = card("Last 7 days", rows(data.progress.week)) + card("Last 30 days", rows(data.progress.month), "A day with no reading counts as neither done nor missed.")
    + (since ? card("Since the plan started", since, data.progress.since ? "Plan started " + esc(when(data.progress.since)) + ". Weekly averages." : "") : "");
}

function renderFood() {
  const f = data.food, t = f.targets;
  const k = [kpi(num(f.eaten.calories), t.calories ? `of ${num(t.calories)} calories` : "calories noted today"),
    kpi(num(f.eaten.protein_g) + " g", t.protein_g ? `of ${num(t.protein_g)} g protein` : "protein noted today")];
  if (f.left.calories != null) k.push(kpi(num(f.left.calories), "calories left"));
  if (f.left.protein_g != null) k.push(kpi(num(f.left.protein_g) + " g", "protein left"));
  if (f.eaten.fiber_g != null) k.push(kpi(num(Math.round(f.eaten.fiber_g)) + " g", t.fiber_g ? `of ${num(t.fiber_g)} g fibre` : "fibre"));
  if (f.eaten.sodium_mg != null) k.push(kpi(num(Math.round(f.eaten.sodium_mg)) + " mg", "sodium (under 2,300)"));
  const meals = f.recent.length ? f.recent.map((m) => `<div class="line"><div class="what">${esc(m.what)}<small>${esc(when(m.day))} ${esc(m.at)}</small></div>
      <div class="num">${m.calories == null ? "" : num(m.calories) + " cal"}${m.protein_g == null ? "" : `<small>${num(m.protein_g)} g protein</small>`}</div></div>`).join("")
    : `<p class="muted">No meals noted in the last week.</p>`;
  $("main").innerHTML = `<div class="kpis">${k.join("")}</div>` + foodLookup()
    + card("Note a meal", `<form id="meal"><div class="f"><input class="grow" name="what" placeholder="What you ate" aria-label="What you ate" required maxlength="200">
        <input class="narrow" name="calories" inputmode="numeric" placeholder="Calories" aria-label="Calories">
        <input class="narrow" name="protein_g" inputmode="numeric" placeholder="Protein g" aria-label="Protein in grams">
        <button class="btn solid">Add</button></div></form>`, esc(f.note))
    + card("Last 7 days", meals);
  wireFoodLookup();
  $("meal").onsubmit = (e) => { e.preventDefault(); const d = new FormData(e.target);
    act({ action: "meal", what: d.get("what"), calories: d.get("calories"), protein_g: d.get("protein_g") }, "Meal noted."); };
}

// --- facts looked up on request (USDA foods, NIH supplement labels, openFDA medicines): only the words typed go out
const facts = { food: null, supplement: null, label: null, medicine: null }, factQ = { food: "", supplement: "", medicine: "" };
let foodPick = -1, foodGrams = "";
const listText = (v) => (Array.isArray(v) ? v.join(", ") : String(v ?? ""));
// Does it fit? The server's code checks the picked food at this portion against the goal, the guidelines, your labs
// and your allergies (dietcheck.py), before it is added.
const fits = {};
const FIT = { fits: ["Fits your plan", "good"], watch: ["Fits, with care", "warn"], stop: ["Not for you", "bad"], partial: ["Not fully checked", ""] };
async function loadFit(f, grams) {
  const key = `${f.id}|${grams}`;
  if (fits[key]) return;
  fits[key] = { busy: true };
  try { fits[key] = await api("/api/health/fit", { what: f.name, per100: f.per100, grams, ingredients: f.ingredients || "", allergens: f.allergens || [] }); }
  catch (e) { fits[key] = { failed: e.message }; }
  if (tab === "plan") render();
}
function fitBox(f, grams) {
  const got = fits[`${f.id}|${grams}`];
  if (!got) { loadFit(f, grams); return `<p class="muted" style="margin:8px 0 0">Checking it against your plan…</p>`; }
  if (got.busy) return `<p class="muted" style="margin:8px 0 0">Checking it against your plan…</p>`;
  if (got.failed) return `<p class="why" style="margin:8px 0 0;color:var(--bad)">${esc(got.failed)}</p>`;
  const [word, cls] = FIT[got.verdict] || [got.headline, ""];
  const mark = { stop: "✕", watch: "!", good: "✓", info: "·" };
  const line = (l) => `<p class="why" style="margin:4px 0 0"><b aria-hidden="true">${mark[l.level] || "·"}</b> ${esc(l.text)}</p>`;
  const why = [...new Set([...got.lines, ...(got.goal ? [got.goal] : [])].map((l) => l.why))];
  return `<div class="fit" style="margin-top:8px"><span class="pill ${cls}">${esc(word)}</span>
    ${got.lines.map(line).join("")}
    ${got.goal ? `<p class="why" style="margin:8px 0 0"><b>Your goal:</b> ${esc(got.goal.text)}</p>` : ""}
    <details style="margin-top:6px"><summary class="why" style="cursor:pointer">What this rests on</summary>
      ${why.map((w) => `<p class="why" style="margin:4px 0 0">${esc(w)}</p>`).join("")}
      <p class="muted" style="margin:4px 0 0;font-size:12px">${esc(got.note)}</p></details></div>`;
}
async function lookUp(kind, q) {
  const slot = kind === "barcode" ? "food" : kind;  // a scanned pack's matches show in the food lookup
  facts[slot] = { busy: true };
  if (kind === "supplement") facts.label = null;
  if (slot === "food") foodPick = -1;
  render();
  try { facts[slot] = await api(`/api/health/facts?kind=${kind}&q=${encodeURIComponent(q)}`); }
  catch (e) { facts[slot] = { failed: e.message }; }
  if (kind === "barcode" && facts.food.foods && !facts.food.foods.length) facts.food = { failed: "That barcode isn't in USDA's or Open Food Facts' lists: look the food up by its name." };
  render();
}
const searchForm = (kind, placeholder) => `<form class="f" data-fact-form="${kind}"><input class="grow" name="q" maxlength="80" placeholder="${esc(placeholder)}" value="${esc(factQ[kind])}" aria-label="${esc(placeholder)}"><button class="btn solid">Look up</button></form>`;
function wireSearch() {
  $("main").querySelectorAll("[data-fact-form]").forEach((f) => f.onsubmit = (e) => {
    e.preventDefault(); const kind = f.dataset.factForm, q = f.q.value.trim();
    if (!q) return; factQ[kind] = q; if (kind === "food") foodPick = -1; lookUp(kind, q);
  });
}
const factState = (got) => !got ? "" : got.busy ? `<p class="muted">Looking…</p>` : got.failed ? `<p class="err">${esc(got.failed)}</p>` : null;
const per100 = (p) => [p.kcal != null ? `${num(p.kcal)} kcal` : "", p.protein_g != null ? `${num(p.protein_g)} g protein` : "",
  p.carbs_g != null ? `${num(p.carbs_g)} g carbs` : "", p.fat_g != null ? `${num(p.fat_g)} g fat` : ""].filter(Boolean).join(" · ");
function foodLookup() {
  const got = facts.food, state = factState(got);
  let body = state ?? "";
  if (state === null) {
    body = got.foods.length ? got.foods.map((f, i) => {
      const open = i === foodPick, grams = Number(foodGrams) || (f.portions[0] ? f.portions[0].grams : 100);
      const amt = { kcal: Math.round((f.per100.kcal || 0) * grams / 100), protein: Math.round((f.per100.protein_g || 0) * grams / 10) / 10 };
      return `<div class="line" style="align-items:flex-start"><div class="what">${esc(f.name)}<small>${esc(f.kind)}${f.brand ? " · " + esc(f.brand) : ""} · per 100 g: ${per100(f.per100)}</small>
        ${open ? `<div class="f" style="align-items:flex-end;margin-top:6px">
          ${f.portions.length ? `<select data-food-portion aria-label="Portion"><option value="">Grams</option>${f.portions.map((p) => `<option value="${p.grams}"${Number(foodGrams) === p.grams ? " selected" : ""}>${esc(p.label)} (${num(p.grams)} g)</option>`).join("")}</select>` : ""}
          <input class="narrow" data-food-grams inputmode="decimal" value="${esc(grams)}" aria-label="Grams"> g
          <button class="btn solid" data-food-add="${i}">Add ${num(amt.kcal)} kcal, ${num(amt.protein)} g protein</button></div>
          ${grams > 0 && grams <= 3000 ? fitBox(f, grams) : ""}` : ""}</div>
        ${open ? "" : `<button class="btn" data-food-pick="${i}">Use</button>`}</div>`;
    }).join("") : `<p class="muted">Nothing found: try fewer or plainer words.</p>`;
  }
  return card("Look up a food", searchForm("food", "A food, e.g. idli, banana, Greek yogurt") + body,
    got && got.source ? `${esc(got.source)}. Only the words you type are sent.` : "From USDA FoodData Central. Only the words you type are sent.");
}
function wireFoodLookup() {
  wireSearch();
  $("main").querySelectorAll("[data-food-pick]").forEach((b) => b.onclick = () => { foodPick = Number(b.dataset.foodPick); foodGrams = ""; render(); });
  const portion = $("main").querySelector("[data-food-portion]"), grams = $("main").querySelector("[data-food-grams]");
  if (portion) portion.onchange = () => { if (portion.value) { foodGrams = portion.value; render(); } };
  if (grams) grams.onchange = () => { foodGrams = grams.value; render(); };
  $("main").querySelectorAll("[data-food-add]").forEach((b) => b.onclick = () => {
    const f = facts.food.foods[Number(b.dataset.foodAdd)], g = Number(grams.value);
    if (!(g > 0 && g <= 3000)) { notice("An amount between 1 and 3,000 grams."); return; }
    const label = portion && portion.value && Number(portion.value) === g ? portion.selectedOptions[0].textContent : `${num(g)} g`;
    const of = (k) => (f.per100[k] == null ? null : Math.round(f.per100[k] * g) / 100);
    act({ action: "meal", what: `${f.name} (${label})`.slice(0, 200), calories: Math.round((f.per100.kcal || 0) * g / 100),
      protein_g: Math.round((f.per100.protein_g || 0) * g / 100), carbs_g: of("carbs_g"), fat_g: of("fat_g"),
      fiber_g: of("fiber_g"), sodium_mg: of("sodium_mg"), sat_fat_g: of("sat_fat_g"), sugar_g: of("sugar_g"),
      added_sugar_g: of("added_sugar_g") }, "Added to today's meals.");
    Object.keys(fits).forEach((k) => delete fits[k]);  // today's totals changed: check again next time
    foodPick = -1; foodGrams = "";
  });
}

// --- the research digest: new PubMed papers on the topics you choose, strongest evidence first (code only)
let resData = null, resEdit = false, resBusy = false;
const resAbs = {};
async function loadResearch(body) {
  if (body) { resBusy = true; render(); }
  try { const out = await api("/api/health/research", body); resData = out; if (body) { resEdit = false; notice(out.text); } }
  catch (e) { resData = resData || { failed: e.message }; if (body) notice(e.message); }
  resBusy = false;
  if (showing("learn", "research")) render();
}
async function openAbstract(pmid) {
  resAbs[pmid] = { busy: true }; render();
  try { resAbs[pmid] = await api(`/api/health/research?pmid=${encodeURIComponent(pmid)}`); }
  catch (e) { resAbs[pmid] = { failed: e.message }; }
  render();
}
function researchTopics() {
  const s = resData.settings;
  return card("Your topics", `<form id="res-form">
      ${Object.entries(resData.presets).map(([k, v]) => `<label class="pick" style="display:flex;border-radius:12px"><input type="checkbox" name="preset" value="${esc(k)}"${s.presets.includes(k) ? " checked" : ""}> ${esc(v)}</label>`).join("")}
      <label>Your own topics, one a line (up to five; plain words, e.g. "Lp(a) South Asian", "creatine older adults")</label>
      <textarea name="custom" maxlength="420">${esc(s.custom.join("\n"))}</textarea>
      <label class="pick"><input type="checkbox" name="strong"${s.strong ? " checked" : ""}> Strong evidence only (guidelines, reviews, randomised trials)</label>
      <label class="pick"><input type="checkbox" name="people"${s.people ? " checked" : ""}> Leave out animal-only studies</label>
      <p style="margin:12px 0 0"><button class="btn solid">Save and look</button>${s.presets.length || s.custom.length ? ` <button type="button" class="btn" id="res-cancel">Cancel</button>` : ""}</p></form>`,
    "Only these words are sent to PubMed. Buddy looks again every Sunday night.");
}
function paper(p) {
  const a = resAbs[p.pmid];
  const abs = !a ? "" : a.busy ? `<p class="why">Loading…</p>` : a.failed ? `<p class="err">${esc(a.failed)}</p>`
    : (a.sections.length ? a.sections.map((s) => `<p class="why">${s.label ? `<b>${esc(s.label)}:</b> ` : ""}${esc(s.text)}</p>`).join("") : `<p class="why">No abstract.</p>`);
  return `<div class="line"><div class="what"><a href="${esc(p.url)}" target="_blank" rel="noopener">${esc(p.title)}</a>
      <small>${esc(p.journal)} · ${esc(p.published)}${p.author ? " · " + esc(p.author) : ""}</small>
      <p style="margin:4px 0 0"><span class="pill ${p.rank <= 1 ? "good" : ""}">${esc(p.evidence)}</span>${p.new ? ` <span class="pill warn">New</span>` : ""}
      ${a ? "" : ` <button class="btn" data-abs="${esc(p.pmid)}">Abstract</button>`}</p>${a ? abs : ""}</div></div>`;
}
function renderResearch() {
  if (!resData) { $("main").innerHTML = `<p class="muted">Loading…</p>`; loadResearch(); return; }
  if (resData.failed) { $("main").innerHTML = `<p class="err">${esc(resData.failed)}</p>`; return; }
  const s = resData.settings, any = s.presets.length || s.custom.length;
  if (!any || resEdit) { $("main").innerHTML = researchTopics() + (resBusy ? `<p class="muted">Looking…</p>` : ""); wireResearch(); return; }
  const topics = resData.topics.map((t) => card(esc(t.label), t.papers.length ? t.papers.map(paper).join("")
    : `<p class="muted">Nothing ${s.strong ? "with strong evidence " : ""}in the last weeks.</p>`)).join("");
  $("main").innerHTML = `<p class="muted" style="margin:0 0 10px">${resData.ran ? `Looked ${esc(when(resData.ran))}` : "Not looked yet"} · <button class="btn" id="res-edit">Topics</button> <button class="btn" id="res-refresh"${resBusy ? " disabled" : ""}>${resBusy ? "Looking…" : "Refresh"}</button></p>`
    + topics + libraryCard() + `<p class="note">${esc(resData.note)}</p>`;
  wireResearch();
}
// The paper library: every paper Buddy found, from every source (PubMed, arXiv, Europe PMC, OpenAlex), kept.
let lib = null, libQ = "", libSrc = "", libOpen = false;
async function loadLibrary() {
  try { lib = await api(`/api/papers?q=${encodeURIComponent(libQ)}&source=${encodeURIComponent(libSrc)}`); }
  catch (e) { lib = { failed: e.message }; }
  if (showing("learn", "research")) render();
}
function libraryCard() {
  const head = `<button class="btn" id="lib-toggle">${libOpen ? "Hide" : "Open"} the paper library</button>`;
  if (!libOpen) return card("Paper library", `<p class="muted" style="margin:0 0 8px">Every paper Buddy finds, from every source, kept until you delete it.</p>${head}`);
  if (!lib) { loadLibrary(); return card("Paper library", `${head}<p class="muted">Loading…</p>`); }
  if (lib.failed) return card("Paper library", `${head}<p class="err">${esc(lib.failed)}</p>`);
  const srcs = Object.entries(lib.counts).map(([s, n]) => `<option value="${esc(s)}"${s === libSrc ? " selected" : ""}>${esc(s)} (${n})</option>`).join("");
  const rows = lib.papers.map((p) => `<div class="line" style="align-items:flex-start"><div class="what">${p.url ? `<a href="${esc(p.url)}" target="_blank" rel="noopener">${esc(p.title)}</a>` : esc(p.title)}
      <small>${esc([p.venue, p.published].filter(Boolean).join(" · "))}</small>
      <small>Found by ${esc(p.found_by.map((f) => f.why || f.source).join("; "))}</small>
      ${p.abstract ? `<details><summary class="why" style="cursor:pointer">Abstract</summary><p class="why">${esc(p.abstract)}</p></details>` : ""}</div>
      <button class="btn" data-lib-forget="${esc(p.key)}" aria-label="Delete this paper">Delete</button></div>`).join("");
  return card(`Paper library · ${lib.all}`, `${head}<form class="f" id="lib-form" style="margin-top:8px"><input class="grow" name="q" value="${esc(libQ)}" placeholder="Search titles and abstracts" aria-label="Search the library" maxlength="120">
      <select name="source" aria-label="Source"><option value="">All sources</option>${srcs}</select><button class="btn solid">Search</button></form>
    ${rows || `<p class="muted">${lib.all ? "Nothing matches." : "No papers yet: they're kept as Buddy finds them."}</p>`}
    ${lib.total > lib.papers.length ? `<p class="muted">Showing the newest ${lib.papers.length} of ${lib.total}.</p>` : ""}`, esc(lib.note));
}
function wireResearch() {
  if ($("res-form")) $("res-form").onsubmit = (e) => {
    e.preventDefault(); const d = new FormData(e.target);
    loadResearch({ action: "topics", presets: d.getAll("preset"), custom: String(d.get("custom") || "").split("\n").map((x) => x.trim()).filter(Boolean),
      strong: d.get("strong") === "on", people: d.get("people") === "on" });
  };
  if ($("res-cancel")) $("res-cancel").onclick = () => { resEdit = false; render(); };
  if ($("res-edit")) $("res-edit").onclick = () => { resEdit = true; render(); };
  if ($("res-refresh")) $("res-refresh").onclick = () => loadResearch({ action: "refresh" });
  $("main").querySelectorAll("[data-abs]").forEach((b) => b.onclick = () => openAbstract(b.dataset.abs));
  if ($("lib-toggle")) $("lib-toggle").onclick = () => { libOpen = !libOpen; render(); };
  if ($("lib-form")) $("lib-form").onsubmit = (e) => { e.preventDefault(); libQ = e.target.q.value.trim(); libSrc = e.target.source.value; lib = null; render(); };
  $("main").querySelectorAll("[data-lib-forget]").forEach((b) => b.onclick = async () => {
    try { await api("/api/papers", { action: "forget", key: b.dataset.libForget }); lib = null; render(); } catch (e) { notice(e.message); }
  });
}

function renderSupplements() {
  const got = facts.supplement, state = factState(got), lab = facts.label;
  let list = state ?? "";
  if (state === null) list = got.products.length ? got.products.map((p) => `<div class="line"><div class="what">${esc(p.name)}<small>${esc(p.brand)}${p.off_market ? " · no longer sold" : ""}${p.ingredients.length ? " · " + esc(p.ingredients.slice(0, 4).join(", ")) : ""}</small></div>
      <button class="btn" data-label="${esc(p.id)}">Label</button></div>`).join("") : `<p class="muted">Nothing found: try the brand and the main ingredient.</p>`;
  let detail = "";
  if (lab && (lab.busy || lab.failed)) detail = factState(lab);
  else if (lab) {
    const rows = lab.rows.map((r) => `<div class="line"><div class="what">${r.fact_sheet ? `<a href="${esc(r.fact_sheet)}" target="_blank" rel="noopener">${esc(r.name)}</a>` : esc(r.name)}
        <small>${num(r.amount)} ${esc(r.unit)} a serving${r.daily_value_pct != null ? ` · ${num(r.daily_value_pct)}% of the daily value` : ""}${r.upper ? ` · adult upper limit ${esc(r.upper)}` : ""}</small></div>
        ${r.upper_pct != null ? `<span class="pill ${r.upper_pct > 100 ? "warn" : ""}">${num(r.upper_pct)}% of the limit</span>` : ""}</div>`).join("");
    detail = card(esc(lab.name || "Label"), `<p class="why" style="margin:0">${esc(lab.brand)} · a serving is ${esc(lab.serving)}; up to ${num(lab.most_servings)} a day on the label${lab.off_market ? " · no longer sold" : ""}</p>`
      + (lab.warning ? `<p class="err">${esc(lab.warning)}</p>` : "") + rows
      + `<p class="why"><a href="${esc(lab.page)}" target="_blank" rel="noopener">The label in the NIH database</a> · the share of the limit is at the label's most servings a day.</p>`, esc(lab.note));
  }
  $("main").innerHTML = card("Look up a supplement", searchForm("supplement", "A product, e.g. Kirkland vitamin D3") + list,
    "From the NIH Office of Dietary Supplements' Label Database. Only the words you type are sent.") + detail;
  wireSearch();
  $("main").querySelectorAll("[data-label]").forEach((b) => b.onclick = () => lookUp("label", b.dataset.label));
}

function renderMedicines() {
  const got = facts.medicine, state = factState(got);
  let body = state ?? "";
  if (state === null) {
    const l = got.label || {}, part = (title, text, open) => text ? `<details${open ? " open" : ""}><summary style="cursor:pointer">${title}</summary><p class="why">${esc(text)}</p></details>` : "";
    body = (l.used_for || l.warnings) ? `<p style="margin:8px 0 4px"><b>${esc(l.name)}</b>${l.generic ? ` <span class="muted">(${esc(l.generic)})</span>` : ""}</p>`
      + part("What it is for", l.used_for, true) + part("Warnings", l.warnings) + part("Interactions", l.interactions) + part("Side effects", l.side_effects)
      + (got.recalls.length ? `<p class="muted" style="margin:10px 0 0">Recalls</p>` + got.recalls.map((r) => `<div class="line"><div class="what">${esc(r.product)}<small>${esc(r.date)} · ${esc(r.status)} · ${esc(r.reason)}</small></div></div>`).join("") : "")
      : `<p class="muted">No FDA label found: try the generic name.</p>`;
  }
  $("main").innerHTML = card("Look up a medicine", searchForm("medicine", "A medicine, e.g. metformin") + body,
    got && got.note ? esc(got.note) : "From openFDA, the US FDA's public drug labels. Only the words you type are sent.");
  wireSearch();
}

// --- the day's menu: targets worked out by code, meals from the dishes you eat, with USDA's numbers
let dietData = null, dietEdit = false;
const dietSwap = { b: 0, l: 0, d: 0, s: 0 };
async function loadDiet(body) {
  try {
    dietData = body ? await api("/api/health/diet", { ...body, swap: dietSwap })
      : await api(`/api/health/diet?${new URLSearchParams(dietSwap)}`);
    if (body) { dietEdit = false; notice(dietData.text); }
  } catch (e) { dietData = dietData || { failed: e.message }; if (body) notice(e.message); }
  if (body && body.action === "eat") await load();
  if (showing("plan", "menu")) render();
}
function dietPrefs() {
  const p = dietData.prefs, c = dietData.choices;
  const box = (name, k, label, on) => `<label class="pick"><input type="checkbox" name="${name}" value="${esc(k)}"${on ? " checked" : ""}> ${esc(label)}</label>`;
  const sel = (name, options, now) => `<select name="${name}">${Object.entries(options).map(([k, v]) => `<option value="${esc(k)}"${now === k ? " selected" : ""}>${esc(v)}</option>`).join("")}</select>`;
  return card("Your choices", `<form id="diet-form"><label>Weight goal</label>${sel("goal", c.goals, p.goal)}
      <label>Besides plant foods, you eat (leave both off for vegetarian)</label>${Object.entries(c.eats).map(([k, v]) => box("eat", k, v, p.eats.includes(k))).join("")}
      ${Object.entries(c.places).map(([place, title]) => `<label>${esc(title)}, cook from</label>${Object.entries(c.cuisines).map(([k, v]) => box(place, k, v, p.places[place].includes(k))).join("")}`).join("")}
      <label>Where you are</label>${sel("where", c.where, p.where)}
      <p style="margin:12px 0 0"><button class="btn solid">Save</button> <button type="button" class="btn" id="diet-cancel">Cancel</button></p></form>`);
}
function renderMenu() {
  if (!dietData) { $("main").innerHTML = `<p class="muted">Loading…</p>`; loadDiet(); return; }
  if (dietData.failed) { $("main").innerHTML = `<p class="err">${esc(dietData.failed)}</p>`; return; }
  if (dietEdit) { $("main").innerHTML = dietPrefs(); wireDiet(); return; }
  const t = dietData.targets, p = dietData.prefs, c = dietData.choices;
  const row = (label, value, how) => `<div class="line"><div class="what">${esc(label)}<small>${esc(how)}</small></div><div class="num">${value}</div></div>`;
  const targets = t.calories.value == null
    ? `<p class="muted">To work out your calories Buddy needs ${esc(t.missing.join(", ") || "a few days of readings")}.</p>`
    : row("Calories", num(t.calories.value), t.calories.how)
      + row("Protein", t.protein_g.low == null ? "–" : `${num(t.protein_g.low)}–${num(t.protein_g.high)} g`, t.protein_g.how)
      + row("Fibre", t.fiber_g.value == null ? "–" : `${num(t.fiber_g.value)} g or more`, t.fiber_g.how)
      + row("Sodium", "under 2,300 mg", t.sodium_mg.how)
      + row("Saturated fat", t.sat_fat_g.value == null ? "–" : `${num(t.sat_fat_g.value)} g or less`, t.sat_fat_g.how)
      + row("Added sugar", "none", t.added_sugar);
  const meals = dietData.meals.map((m) => card(`${esc(m.name)} · ${esc(m.cuisine)}`,
    m.items.map((i) => `<div class="line"><div class="what">${esc(i.name)}<small>${esc(i.amount)} · ${num(i.grams)} g${i.added_for ? " · added for protein" : ""}</small></div>
      <div class="num">${num(Math.round(i.kcal))} kcal<small>${num(Math.round(i.protein_g))} g protein</small></div></div>`).join("")
    + `<div class="line"><div class="what"><b>Meal</b><small>${num(Math.round(m.totals.carbs_g))} g carbs · ${num(Math.round(m.totals.fat_g))} g fat · ${num(Math.round(m.totals.fiber_g))} g fibre</small></div>
      <div class="num"><b>${num(Math.round(m.totals.kcal))} kcal</b><small>${num(Math.round(m.totals.protein_g))} g protein</small></div></div>
      <p style="margin:8px 0 0">${m.options > 1 ? `<button class="btn" data-diet-swap="${m.meal}">Another (${m.options} options)</button> ` : ""}<button class="btn solid" data-diet-eat="${m.meal}">I ate this</button></p>`)).join("");
  const checks = dietData.checks.map((x) => `<div class="line"><div class="what">${esc(x.what)}<small>target ${num(Math.round(x.target))}</small></div>
      <div class="num">${num(Math.round(x.got))}</div> <span class="pill ${x.ok ? "good" : "warn"}">${x.ok ? "On target" : "Off"}</span></div>`).join("");
  $("main").innerHTML = card("Your day's targets", targets + t.notes.map((n) => `<p class="why">${esc(n)}</p>`).join(""))
    + `<p class="muted" style="margin:0 0 10px">${esc(c.places[p.here])} (${esc(p.here_how)}): ${esc(p.cuisines.map((k) => c.cuisines[k]).join(", "))} · ${esc(c.goals[p.goal])} · ${esc(p.eats.length ? "Also " + p.eats.map((k) => c.eats[k].toLowerCase()).join(", ") : "Vegetarian")} · <button class="btn" id="diet-edit">Change</button></p>`
    + meals + card("The day", checks + dietData.tips.map((x) => `<p class="why">${esc(x)}</p>`).join(""), esc(`${dietData.source}. ${dietData.note}`));
  wireDiet();
}
function wireDiet() {
  if ($("diet-form")) $("diet-form").onsubmit = (e) => {
    e.preventDefault(); const d = new FormData(e.target);
    loadDiet({ action: "prefs", goal: d.get("goal"), eats: d.getAll("eat"), us: d.getAll("us"), india: d.getAll("india"), where: d.get("where") });
  };
  if ($("diet-cancel")) $("diet-cancel").onclick = () => { dietEdit = false; render(); };
  if ($("diet-edit")) $("diet-edit").onclick = () => { dietEdit = true; render(); };
  $("main").querySelectorAll("[data-diet-swap]").forEach((b) => b.onclick = () => { dietSwap[b.dataset.dietSwap] += 1; loadDiet(); });
  $("main").querySelectorAll("[data-diet-eat]").forEach((b) => b.onclick = () => { b.disabled = true; loadDiet({ action: "eat", meal: b.dataset.dietEat }); });
}

function habitRow(h) {
  const opts = Object.keys(data.metrics).map((k) => `<option value="${k}"${k === (h.metric || "manual") ? " selected" : ""}>${esc(k === "manual" ? "I tick it off" : k.replace("_", " "))}</option>`).join("");
  return `<div class="f habit"><input class="grow" name="text" placeholder="Habit (e.g. Walk 8,000 steps)" aria-label="Habit" value="${esc(h.text || "")}" maxlength="120">
    <select name="metric" aria-label="How it is tracked">${opts}</select>
    <input class="narrow" name="target" placeholder="Target" aria-label="Target" value="${esc(h.target ?? "")}">
    <select name="days" aria-label="Which days"><option value="daily">Every day</option><option value="weekdays"${h.days === "weekdays" ? " selected" : ""}>Weekdays</option></select></div>`;
}

function renderPlan() {
  const p = data.plan || { goals: [], habits: [{}, {}, {}], diet: {} };
  const tracked = Object.entries(data.metrics).map(([k, v]) => `<div class="line"><div class="what">${esc(k === "manual" ? "I tick it off" : k.replace("_", " "))}<small>${esc(k === "manual" ? "you tick it off on this page (water, stretching, no sugar...)" : v)}</small></div></div>`).join("");
  $("main").innerHTML = card(data.plan ? "Your plan" : "Set up a plan",
    `<form id="plan"><label for="goals">Goals, one a line</label><textarea id="goals" name="goals" placeholder="Sleep better&#10;More energy">${esc(p.goals.join("\n"))}</textarea>
      <label>Habits (up to 8). A tracked habit needs a target: a number, or a time like 22:45 for bedtime.</label>
      <div id="habits">${p.habits.map(habitRow).join("")}</div>
      <button type="button" class="btn" id="more">Add a habit</button>
      <label>Food targets a day (optional)</label>
      <div class="f"><input class="narrow" name="calories" inputmode="numeric" placeholder="Calories" aria-label="Calories a day" value="${esc(p.diet.calories ?? "")}">
        <input class="narrow" name="protein_g" inputmode="numeric" placeholder="Protein g" aria-label="Protein grams a day" value="${esc(p.diet.protein_g ?? "")}">
        <input class="grow" name="style" placeholder="Way of eating (e.g. vegetarian)" aria-label="Way of eating" value="${esc(p.diet.style ?? "")}" maxlength="80"></div>
      <label>Allergies and foods to avoid (each food you look up or scan is checked against them)</label>
      <div class="f"><input class="grow" name="allergies" placeholder="Allergies, e.g. peanuts, shellfish" aria-label="Allergies" value="${esc(listText(p.diet.allergies))}" maxlength="200">
        <input class="grow" name="avoid" placeholder="Foods to avoid, e.g. maida, fried food" aria-label="Foods to avoid" value="${esc(listText(p.diet.avoid))}" maxlength="200"></div>
      <div class="f" style="margin-top:12px"><button class="btn solid">Save the plan</button>
        ${data.plan ? `<button type="button" class="btn danger" id="forget">${sure ? "Press again to delete the plan, habit log and meals" : "Forget my health plan"}</button>` : ""}</div></form>`,
    "The plan is set here because Buddy no longer discusses health in conversation. It stays on your server.")
    + card("How a habit can be tracked", tracked);
  $("more").onclick = () => { if ($("habits").children.length < 8) $("habits").insertAdjacentHTML("beforeend", habitRow({})); };
  if ($("forget")) $("forget").onclick = () => { if (!sure) { sure = true; render(); return; } sure = false; act({ action: "forget" }, "The plan, habit log and meals were deleted."); };
  $("plan").onsubmit = (e) => {
    e.preventDefault();
    const d = new FormData(e.target), diet = {};
    for (const k of ["calories", "protein_g", "style"]) if (String(d.get(k) || "").trim()) diet[k] = String(d.get(k)).trim();
    for (const k of ["allergies", "avoid"]) diet[k] = String(d.get(k) || "").trim();  // sent even when emptied, to clear it
    const habits = [...$("habits").querySelectorAll(".habit")].map((row) => Object.fromEntries(["text", "metric", "target", "days"].map((k) => [k, row.querySelector(`[name=${k}]`).value.trim()])))
      .filter((h) => h.text);
    act({ action: "plan", plan: { goals: String(d.get("goals") || "").split("\n").map((g) => g.trim()).filter(Boolean), habits, diet: { ...p.diet, ...diet } } }, "Plan saved.");
  };
}

// --- Checkups: the screenings for you (fixed rules on your answers), what to ask about, and where your numbers sit
let chkData = null, chkEdit = false, chkAsk = false;
async function loadCheckups(body) {
  try { const out = await api("/api/health/checkups", body); chkData = out; if (body) { chkEdit = false; notice(out.text); } }
  catch (e) { chkData = chkData || { failed: e.message }; if (body) notice(e.message); }
  if (showing("labs", "checkups")) render();
}
const CHK_STATUS = { review: ["Under review", "warn"], due: ["Due", "warn"], ok: ["Up to date", "good"], decide: ["Your choice", ""], ask: ["Ask", ""],
  optional: ["Optional", ""], not_needed: ["Not needed", "good"], first: ["A1c first", ""] };
const chkPill = (s) => { const [t, c] = CHK_STATUS[s] || [s, ""]; return `<span class="pill ${c}">${esc(t)}</span>`; };
function chkForm() {
  const p = { ...chkData.suggested, ...chkData.profile }, c = chkData.choices;
  const box = (name, value, label, on) => `<label class="pick"><input type="checkbox" name="${name}" value="${esc(value)}"${on ? " checked" : ""}> ${esc(label)}</label>`;
  const radio = (name, value, label) => `<label class="pick"><input type="radio" name="${name}" value="${esc(value)}"${(p[name] || "") === value ? " checked" : ""}> ${esc(label)}</label>`;
  const groups = [["South Asian", ["south_asian"]], ["East and Southeast Asian", ["east_asian", "southeast_asian", "asian"]], ["Other", null]];
  const anc = groups.map(([title, keys]) => `<p class="why">${title}</p>` + c.ancestry.filter((a) => keys ? keys.includes(a.group)
    : !["south_asian", "east_asian", "southeast_asian", "asian"].includes(a.group)).map((a) => box("ancestry", a.key, a.label, (p.ancestry || []).includes(a.key))).join("")).join("");
  return card(chkData.filled ? "About you" : "A few answers first",
    `<form id="chk-form">
      <p class="muted" style="margin:0">Screenings depend on age, sex, ancestry and family history. ${Object.keys(chkData.suggested || {}).length ? "Age, sex and height are filled in from your Oura profile: check them." : ""}</p>
      <div class="f" style="align-items:flex-end"><label style="flex:1">Birth year<input name="birth_year" inputmode="numeric" maxlength="4" value="${esc(p.birth_year ?? "")}" style="width:100%"></label>
        <label style="flex:1">Height (cm)<input name="height_cm" inputmode="decimal" value="${esc(p.height_cm ?? "")}" style="width:100%"></label>
        <label style="flex:1">Waist (cm, if known)<input name="waist_cm" inputmode="decimal" value="${esc(p.waist_cm ?? "")}" style="width:100%"></label></div>
      <label>Sex at birth (the screening rules use it)</label>${radio("sex", "male", "Male")}${radio("sex", "female", "Female")}
      <label>Ancestry (pick all that apply)</label>${anc}
      <label>Born in, or lived over a year in, a country where hepatitis B or TB is common (most of Asia, India and China included; Africa; the Pacific Islands; parts of South America and Eastern Europe)</label>
      ${radio("born_high", "yes", "Yes")}${radio("born_high", "no", "No")}
      <label>Smoking</label>${radio("smoking", "never", "Never")}${radio("smoking", "former", "Used to")}${radio("smoking", "current", "Now")}
      <div class="f" style="align-items:flex-end"><label style="flex:1">Pack-years, if ever (packs a day × years)<input name="pack_years" inputmode="decimal" value="${esc(p.pack_years ?? "")}" style="width:100%"></label>
        <label style="flex:1">Year you quit<input name="quit_year" inputmode="numeric" maxlength="4" value="${esc(p.quit_year ?? "")}" style="width:100%"></label></div>
      <label>In your family (a parent, brother, sister or child)</label>${Object.entries(c.family).map(([k, v]) => box("family", k, v, (p.family || []).includes(k))).join("")}
      <label>You've been told you have</label>${Object.entries(c.conditions).map(([k, v]) => box("conditions", k, v, (p.conditions || []).includes(k))).join("")}
      ${box("postmenopausal", "1", "After menopause (women)", p.postmenopausal)}
      <label>Medicines (for the heart risk estimate)</label>${box("bp_meds", "1", "Taking blood pressure medicine", p.bp_meds)}${box("statin", "1", "Taking a statin", p.statin)}
      <label>Your health plan (only its kind, for the coverage notes)</label>
      <select name="plan">${Object.entries(c.plans).map(([k, v]) => `<option value="${esc(k)}"${(p.plan || "") === k ? " selected" : ""}>${esc(v)}</option>`).join("")}</select>
      <p style="margin:12px 0 0"><button class="btn solid">Save</button>${chkData.filled ? ` <button type="button" class="btn" id="chk-cancel">Cancel</button>` : ""}</p>
    </form>`, "Kept on your server only; no AI model is given any of it.");
}
function chkItem(i) {
  const methods = Object.entries(i.methods || {});
  const when = i.last ? `Last ${esc(i.last)}${i.next ? ` · next ${esc(i.next)}` : ""}` : (i.once ? "Once" : esc(i.often));
  return `<div class="line" style="align-items:flex-start"><div class="what">${esc(i.title)}<small>${esc(i.test)}</small><small>${when}</small>
      ${(i.review || []).map((r) => `<p class="why">${esc(r)} Its rule here may be out of date until Buddy's rules are updated.</p>`).join("")}
      <details><summary class="why" style="cursor:pointer">Details</summary>
        ${i.why.map((w) => `<p class="why">${esc(w)}</p>`).join("")}
        ${(i.uspstf_words || []).map((u) => `<p class="why"><b>The task force's words${u.grade ? ` (grade ${esc(u.grade)})` : ""}:</b> ${esc(u.text)} <i>US Preventive Services Task Force.</i></p>`).join("")}
        <p class="why">${esc(i.often)}. ${esc(i.cover_text)}. <a href="${esc(i.url)}" target="_blank" rel="noopener">The guideline</a>${i.grade ? ` (grade ${esc(i.grade)})` : ""}.</p>
        ${i.how ? `<p class="why">Last done: ${esc(i.last)} (${esc(i.how)}).</p>` : ""}
        <div class="f"><input type="month" data-chk-month="${esc(i.key)}" aria-label="Month it was done">
        ${methods.length ? `<select data-chk-method="${esc(i.key)}" aria-label="Which test">${methods.map(([k, v]) => `<option value="${esc(k)}">${esc(v)}</option>`).join("")}</select>` : ""}
        <button class="btn" data-chk-done="${esc(i.key)}">I had it then</button>${i.last && !String(i.how).startsWith("from") ? ` <button class="btn" data-chk-undo="${esc(i.key)}">Clear</button>` : ""}</div></details></div>
    ${chkPill(i.status)}</div>`;
}
function chkCompare(cmp) {
  if (!cmp.rows.length) return "";
  const n = (rank) => rank <= 2 ? "under 5" : rank >= 98 ? "over 95" : String(rank);
  const first = cmp.rows.find((r) => r.asian || r.all), who = (side) => first && first[side] ? first[side].who.replace(/^US /, "") : "";
  const rows = cmp.rows.map((r) => `<div class="line"><div class="what">${esc(r.label)} · ${num(r.value)} ${esc(r.unit)}<small>${esc(r.when)}</small></div>
      <div class="num">${r.asian ? `${n(r.asian.rank)}<small>Asian</small>` : ""}</div><div class="num">${r.all ? `${n(r.all.rank)}<small>all US</small>` : ""}</div></div>`).join("");
  return card("Compared with others",
    `<p class="why" style="margin:0 0 4px">How many in 100 have a lower value: ${first && first.asian ? `US ${esc(who("asian"))}, and ` : ""}all US ${esc(who("all").replace(/ of Asian ancestry$/, ""))}.</p>` + rows
      + cmp.cutoffs.map((c) => `<p class="why">${esc(c)}</p>`).join(""), esc(cmp.note));
}
function renderCheckups() {
  if (!chkData) { $("main").innerHTML = `<p class="muted">Loading…</p>`; loadCheckups(); return; }
  if (chkData.failed) { $("main").innerHTML = `<p class="err">${esc(chkData.failed)}</p>`; return; }
  if (!chkData.filled || chkEdit) { $("main").innerHTML = chkForm(); wireCheckups(); return; }
  const p = chkData.profile, age = new Date().getFullYear() - p.birth_year;
  const anc = chkData.choices.ancestry.filter((a) => (p.ancestry || []).includes(a.key)).map((a) => a.label).join(", ");
  const due = chkData.items.filter((i) => i.status === "due").length;
  const asks = chkData.asks.map((a) => `<div class="line" style="align-items:flex-start"><div class="what">${esc(a.title)}<p class="why">${esc(a.text)}</p><small>${esc(a.source)}</small></div>${chkPill(a.status)}</div>`).join("");
  $("main").innerHTML = `<p class="muted" style="margin:0 0 10px">About you: ${esc(age)}, ${esc(p.sex)}${anc ? ", " + esc(anc) : ""} · <button class="btn" id="chk-edit">Change</button></p>`
    + (chkData.items.some((i) => i.status === "review") ? card("Under review: newer than Buddy's rules", chkData.items.filter((i) => i.status === "review").map(chkItem).join("")) : "")
    + (due ? card(`Due · ${due}`, chkData.items.filter((i) => i.status === "due").map(chkItem).join("")) : "")
    + (chkData.items.some((i) => i.status === "decide" || i.status === "ask") ? card("To talk over with your doctor", chkData.items.filter((i) => i.status === "decide" || i.status === "ask").map(chkItem).join("")) : "")
    + (chkData.items.some((i) => i.status === "ok") ? card("Up to date", chkData.items.filter((i) => i.status === "ok").map(chkItem).join("")) : "")
    + card("Lp(a), glucose sensor, DNA: worth it?", asks)
    + chkCompare(chkData.compare)
    + `<p class="note">${esc(chkData.note)}</p>`
    + `<p>${chkAsk ? `<button class="btn danger solid" id="chk-forget" data-sure="1">Yes, delete my answers</button>` : `<button class="btn danger" id="chk-forget">Delete my answers</button>`}</p>`;
  wireCheckups();
}
function wireCheckups() {
  if ($("chk-form")) $("chk-form").onsubmit = (e) => {
    e.preventDefault();
    const d = new FormData(e.target), answers = {};
    for (const k of ["birth_year", "height_cm", "waist_cm", "sex", "born_high", "smoking", "pack_years", "quit_year", "plan"]) answers[k] = String(d.get(k) ?? "").trim();
    for (const k of ["ancestry", "family", "conditions"]) answers[k] = d.getAll(k);
    answers.postmenopausal = d.get("postmenopausal") === "1";
    answers.bp_meds = d.get("bp_meds") === "1"; answers.statin = d.get("statin") === "1";
    loadCheckups({ action: "profile", answers });
  };
  if ($("chk-cancel")) $("chk-cancel").onclick = () => { chkEdit = false; render(); };
  if ($("chk-edit")) $("chk-edit").onclick = () => { chkEdit = true; render(); };
  $("main").querySelectorAll("[data-chk-done]").forEach((b) => b.onclick = () => {
    const k = b.dataset.chkDone, month = $("main").querySelector(`[data-chk-month="${k}"]`).value;
    const m = $("main").querySelector(`[data-chk-method="${k}"]`);
    loadCheckups({ action: "done", key: k, month, method: m ? m.value : "" });
  });
  $("main").querySelectorAll("[data-chk-undo]").forEach((b) => b.onclick = () => loadCheckups({ action: "undo", key: b.dataset.chkUndo }));
  if ($("chk-forget")) $("chk-forget").onclick = () => {
    if (!$("chk-forget").dataset.sure) { chkAsk = true; render(); return; }
    chkAsk = false; loadCheckups({ action: "forget", confirm: true });
  };
}

function wireMood() {
  $("main").querySelectorAll("[data-mood-k]").forEach((b) => b.onclick = () => {
    const k = b.dataset.moodK, v = Number(b.dataset.moodV); moodPick[k] = moodPick[k] === v ? null : v; render(); });
  $("main").querySelectorAll("[data-mood-tag]").forEach((b) => b.onclick = () => {
    const t = b.dataset.moodTag; moodPick.tags = moodPick.tags.includes(t) ? moodPick.tags.filter((x) => x !== t) : [...moodPick.tags, t]; render(); });
  if ($("mood-note")) $("mood-note").oninput = (e) => { moodPick.note = e.target.value; };
  if ($("mood-save")) $("mood-save").onclick = () => {
    const body = { action: "add", ...moodPick, note: $("mood-note").value };
    moodPick = { mood: null, stress: null, energy: null, tags: [], note: "" };
    loadMood(body);
  };
}

function wire() {
  wireMood();
  $("main").querySelectorAll("[data-habit]").forEach((b) => b.onclick = () => act({ action: "habit", habit: b.dataset.habit }, "Ticked off."));
  if ($("read")) $("read").onclick = () => { $("read").disabled = true; $("read").textContent = "Reading…"; act({ action: "read" }, (o) => `Read ${o.days} days from the ring.`); };
}

const VIEWS = {
  today: renderToday, sources: renderSources,
  "trends/measures": renderTrends, "trends/body": renderBody, "trends/patterns": renderPatterns, "trends/mood": moodHistory,
  "labs/results": () => renderLabs("results"), "labs/add": () => renderLabs("add"), "labs/portals": () => renderLabs("portals"),
  "labs/checkups": renderCheckups,
  "plan/habits": renderHabits, "plan/food": renderFood, "plan/menu": renderMenu, "plan/goals": renderPlan,
  "learn/research": renderResearch, "learn/supplements": renderSupplements, "learn/medicines": renderMedicines,
};
function render() {
  tabs();
  const view = VIEWS[subsOf(tab).length ? `${tab}/${subOf(tab)}` : tab] || renderToday;
  view();
  subTabs();
}

async function load() {
  try { data = await api(`/api/health/page?days=${span}`); render(); }
  catch (e) { tabs(); $("main").innerHTML = `<p class="err">${esc(e.message)}</p>`; }
}
// The app's "Scan food" opens this page with the barcode it read, or the dish the owner tapped, on the phone: show
// the matches on Plan → Food. Only the number or the dish's name came from the phone; never a picture.
const scanned = (() => {  // read from the address as it arrived (the token line above may have cleared it since)
  const code = (params.get("scanbarcode") || "").replace(/\D/g, "");
  const dish = (params.get("scandish") || "").replace(/[^\p{L}\p{N} '&(),.-]/gu, "").trim().slice(0, 60);
  if (!code && !dish) return null;
  const rest = new URLSearchParams(location.search);
  if (rest.has("scanbarcode") || rest.has("scandish")) {  // a reload doesn't look it up again
    rest.delete("scanbarcode"); rest.delete("scandish"); rest.delete("t");
    history.replaceState(null, "", location.pathname + (String(rest) ? `?${rest}` : ""));
  }
  tab = "plan"; storageSet("healthTab", "plan"); storageSet("healthSub:plan", "food");
  return code ? { kind: "barcode", q: code } : { kind: "food", q: dish };
})();
load().then(() => { if (scanned) { factQ.food = scanned.q; lookUp(scanned.kind, scanned.q); } });
