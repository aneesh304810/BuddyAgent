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
const TABS = [["today", "Today"], ["trends", "Trends"], ["habits", "Habits"], ["food", "Food"], ["plan", "Plan"]];
let data = null, tab = TABS.some(([k]) => k === storageGet("healthTab")) ? storageGet("healthTab") : "today";
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
    : `<p class="muted">${data.plan ? "No habits are due today." : "No plan yet. Set one up under Plan and its habits show here."}</p>`;
  $("main").innerHTML = (k.length ? `<div class="kpis">${k.join("")}</div>` : card("Today", `<p class="muted">No readings yet for today or yesterday.</p>`))
    + (k.length ? card("What it means for today", means, "Fixed rules against your own week. Not medical advice.") : "")
    + card("Today's habits", habits) + sources();
  wire();
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
  if (!data.plan) { $("main").innerHTML = card("Habits", `<p class="muted">No plan yet. Set one up under Plan.</p>`); return; }
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
  const meals = f.recent.length ? f.recent.map((m) => `<div class="line"><div class="what">${esc(m.what)}<small>${esc(when(m.day))} ${esc(m.at)}</small></div>
      <div class="num">${m.calories == null ? "" : num(m.calories) + " cal"}${m.protein_g == null ? "" : `<small>${num(m.protein_g)} g protein</small>`}</div></div>`).join("")
    : `<p class="muted">No meals noted in the last week.</p>`;
  $("main").innerHTML = `<div class="kpis">${k.join("")}</div>`
    + card("Note a meal", `<form id="meal"><div class="f"><input class="grow" name="what" placeholder="What you ate" aria-label="What you ate" required maxlength="200">
        <input class="narrow" name="calories" inputmode="numeric" placeholder="Calories" aria-label="Calories">
        <input class="narrow" name="protein_g" inputmode="numeric" placeholder="Protein g" aria-label="Protein in grams">
        <button class="btn solid">Add</button></div></form>`, esc(f.note))
    + card("Last 7 days", meals);
  $("meal").onsubmit = (e) => { e.preventDefault(); const d = new FormData(e.target);
    act({ action: "meal", what: d.get("what"), calories: d.get("calories"), protein_g: d.get("protein_g") }, "Meal noted."); };
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
      <div class="f" style="margin-top:12px"><button class="btn solid">Save the plan</button>
        ${data.plan ? `<button type="button" class="btn danger" id="forget">${sure ? "Press again to delete the plan, habit log and meals" : "Forget my health plan"}</button>` : ""}</div></form>`,
    "The plan is set here because Buddy no longer discusses health in conversation. It stays on your server.")
    + card("How a habit can be tracked", tracked);
  $("more").onclick = () => { if ($("habits").children.length < 8) $("habits").insertAdjacentHTML("beforeend", habitRow({})); };
  if ($("forget")) $("forget").onclick = () => { if (!sure) { sure = true; renderPlan(); return; } sure = false; act({ action: "forget" }, "The plan, habit log and meals were deleted."); };
  $("plan").onsubmit = (e) => {
    e.preventDefault();
    const d = new FormData(e.target), diet = {};
    for (const k of ["calories", "protein_g", "style"]) if (String(d.get(k) || "").trim()) diet[k] = String(d.get(k)).trim();
    const habits = [...$("habits").querySelectorAll(".habit")].map((row) => Object.fromEntries(["text", "metric", "target", "days"].map((k) => [k, row.querySelector(`[name=${k}]`).value.trim()])))
      .filter((h) => h.text);
    act({ action: "plan", plan: { goals: String(d.get("goals") || "").split("\n").map((g) => g.trim()).filter(Boolean), habits, diet: { ...p.diet, ...diet } } }, "Plan saved.");
  };
}

function wire() {
  $("main").querySelectorAll("[data-habit]").forEach((b) => b.onclick = () => act({ action: "habit", habit: b.dataset.habit }, "Ticked off."));
  if ($("read")) $("read").onclick = () => { $("read").disabled = true; $("read").textContent = "Reading…"; act({ action: "read" }, (o) => `Read ${o.days} days from the ring.`); };
}

function render() {
  tabs();
  ({ today: renderToday, trends: renderTrends, habits: renderHabits, food: renderFood, plan: renderPlan })[tab]();
}

async function load() {
  try { data = await api(`/api/health/page?days=${span}`); render(); }
  catch (e) { tabs(); $("main").innerHTML = `<p class="err">${esc(e.message)}</p>`; }
}
load();
