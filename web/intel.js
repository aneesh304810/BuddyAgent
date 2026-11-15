// Buddy Intelligence Platform: emerging technologies by sector with the stage the evidence supports, which
// technologies are converging across sectors, anything forming, and the sources. Evidence and interpretation are
// kept apart: every record shows what kind of evidence it is. Every piece of text is escaped.

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
const safe = (u) => /^https:\/\/[^\s"'<>]+$/.test(String(u || "")) ? u : "";
const TABS = [["overview", "Overview"], ["convergence", "Convergence"], ["sectors", "Sectors"], ["sources", "Sources"], ["predict", "Predictions"], ["models", "AI models"]];
let predictData = null, predictBusy = false;
let modelData = null, modelBusy = false;
const KIND = { research: "papers", trial: "clinical studies", filing: "company filings", award: "federal awards", grant: "research grants", funding: "fundraising notices", code: "code projects", news: "news reports", discussion: "stories" };
let data = null, tab = TABS.some(([k]) => k === storageGet("intelTab")) ? storageGet("intelTab") : "overview";

async function api(path, body) {
  const res = await fetch(path, body ? { method: "POST", headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" }, body: JSON.stringify(body) }
    : { headers: { Authorization: `Bearer ${token}` } });
  const out = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(out.detail || (res.status === 401 ? "Open Intelligence from the Buddy app." : `Error ${res.status}`));
  return out;
}
function notice(text) {
  const n = $("notice"); n.textContent = text; n.style.display = "block";
  clearTimeout(notice.t); notice.t = setTimeout(() => { n.style.display = "none"; }, 5000);
}
const card = (title, body, foot) => `<section class="card"><h2>${title}</h2>${body}${foot ? `<p class="note">${foot}</p>` : ""}</section>`;
const kpi = (value, label) => `<div class="kpi"><b>${value}</b><span>${esc(label)}</span></div>`;
const day = (iso) => { const d = new Date(String(iso).length === 10 ? iso + "T12:00:00" : iso); return isNaN(d) ? "" : d.toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" }); };
const growth = (g) => g == null ? "new" : `${g > 0 ? "▲" : g < 0 ? "▼" : "•"} ${Math.abs(Math.round(g * 100))}%`;
const usd = (v) => v >= 1e9 ? `$${(v / 1e9).toFixed(1)}B` : v >= 1e6 ? `$${(v / 1e6).toFixed(1)}M` : `$${Math.round(v).toLocaleString()}`;
// The stage is a step on an ordered scale: shown as filled steps with its name.
function stage(name) {
  const i = Math.max(0, data.stages.indexOf(name));
  const steps = data.stages.slice(1).map((_, n) => `<i class="${n < i ? "on" : ""}"></i>`).join("");
  return `<span class="stage" title="Step ${i} of ${data.stages.length - 1}"><span class="steps" aria-hidden="true">${steps}</span>${esc(name)}</span>`;
}
const kinds = (k) => Object.entries(k || {}).map(([name, n]) => `<span class="chipk">${n} ${esc(KIND[name] || name)}</span>`).join("");
function evidence(e) {
  const url = safe(e.url), title = url ? `<a href="${esc(url)}" target="_blank" rel="noopener noreferrer">${esc(e.title)}</a>` : esc(e.title);
  const extra = e.kind === "award" && e.amount ? ` · ${usd(e.amount)}` : "";
  return `<div class="ev"><span class="chipk" title="${esc(e.means)}">${esc(e.evidence)}</span> ${title}
    <small>${esc(e.source)} · ${esc(day(e.day))}${e.who ? " · " + esc(e.who) : ""}${extra}</small></div>`;
}

function tabs() {
  const n = { convergence: data.convergences.length };
  $("tabs").innerHTML = TABS.map(([k, label]) => `<button class="chip${k === tab ? " on" : ""}" role="tab" aria-selected="${k === tab}" data-tab="${k}">${label}${n[k] != null ? ` (${n[k]})` : ""}</button>`).join("");
  document.querySelectorAll("[data-tab]").forEach((b) => b.onclick = () => { tab = b.dataset.tab; storageSet("intelTab", tab); render(); });
}

function convergence(c, opened) {
  const ai = c.interpretation ? `<div class="ai"><span class="lbl">AI interpretation, not evidence</span>
      <b>${esc(c.interpretation.name)}</b><br>${esc(c.interpretation.how)}
      ${c.interpretation.problem ? `<br><span class="muted">Addresses: ${esc(c.interpretation.problem)}</span>` : ""}
      ${c.interpretation.watch ? `<br><span class="muted">Watch for: ${esc(c.interpretation.watch)}</span>` : ""}</div>` : "";
  return `<div class="conv"><h3>${esc(c.a)} × ${esc(c.b)}</h3>
    <div class="muted">${esc(c.sectors.join(" + "))}</div>
    <div class="kinds">${stage(c.stage)}<span class="num">${esc(c.signals_90_days)} shared records in 90 days · ${esc(growth(c.growth))}</span></div>
    <div class="muted">Why this stage: ${esc(c.why)}.</div><div class="kinds">${kinds(c.kinds)}</div>${ai}
    ${c.companies.length ? `<div class="muted">In the records: ${esc(c.companies.slice(0, 8).join(", "))}</div>` : ""}
    <details${opened ? " open" : ""}><summary>The records (${c.evidence.length} newest)</summary>${c.evidence.map(evidence).join("")}</details></div>`;
}
const techRow = (t) => `<button class="rowt" data-tech="${esc(t.id)}"><b>${esc(t.name)}</b><span class="num">${esc(t.signals_90_days)} · ${esc(growth(t.growth))}</span>
  <small>${stage(t.stage)}</small></button>`;

function overview() {
  const on = data.sectors.filter((s) => s.on), off = data.sectors.filter((s) => !s.on);
  const forming = (data.forming || []).map((f) => `<div class="conv"><h3>${esc(f.name)}</h3><div class="muted">${esc(f.sector_name)}</div>
    <div class="muted">Rising together (30 days before to the last 30): ${esc(f.rising.join("; "))}</div></div>`).join("");
  const moving = off.filter((s) => s.movement && s.movement.before != null && s.movement.now >= 8 && s.movement.now >= 1.5 * Math.max(s.movement.before, 1));
  const movers = moving.map((s) => `<div class="sector" style="padding:8px 0;border-top:1px solid var(--line)"><span><b>${esc(s.name)}</b><br>
    <span class="muted">${esc(s.movement.now)} recent filings and stories, against ${esc(s.movement.before)} the week before</span></span>
    <button class="btn solid" data-sector="${esc(s.id)}" data-on="1">Follow</button></div>`).join("");
  const scanned = data.last_run ? `Last collected ${esc(day(data.last_run))} · ${esc(data.new_last_run)} new records` : "Not collected yet. It runs every night; tap Collect now to start.";
  return `<section class="card"><div class="sector"><b>Buddy Intelligence Platform</b><button class="btn solid" id="scan">Collect now</button></div>
      <p class="note">${scanned}. Following ${on.map((s) => esc(s.name)).join(", ")}.</p></section>
    <div class="kpis" style="margin-bottom:12px">${kpi(Number(data.signals_total).toLocaleString(), "public records")}${kpi(data.technologies.length, "technologies followed")}
      ${kpi(data.convergences.length, "convergences")}${kpi((data.forming || []).length, "forming now")}</div>
    ${movers ? card("A lot is moving in sectors you don't follow", movers, "From the light weekly check on sectors that are switched off. Follow one and Buddy collects its full evidence from tonight. A rising count of records is not a judgement that there is an opportunity.") : ""}
    ${forming ? card("An industry may be forming", forming, "Three or more independent kinds of evidence rising at the same time. Evidence moving together, not a forecast.") : ""}
    ${card("Strongest convergences", data.convergences.length ? data.convergences.slice(0, 4).map((c) => convergence(c)).join("")
        : `<p class="muted">None yet. A convergence appears when technologies from two sectors keep showing up in the same records.</p>`,
      "Two technologies from different sectors that share public records, ranked by how many, how many kinds, and whether it is growing.")}
    ${card("Technologies by evidence", data.technologies.filter((t) => t.signals_90_days).slice(0, 12).map(techRow).join("") || `<p class="muted">No records yet.</p>`,
      "Records in the last 90 days and the change against the 90 before. Tap one for its evidence.")}
    <p class="note">${esc(data.note)}</p>`;
}
function sectors() {
  return data.sectors.map((s) => {
    const mine = data.technologies.filter((t) => t.sector === s.id);
    const move = !s.on && s.movement ? `Weekly check: ${s.movement.now} recent filings and stories${s.movement.before != null ? `, ${s.movement.before} the week before` : ""}.` : "";
    return `<section class="card"><div class="sector"><h2 style="margin:0">${esc(s.name)}</h2>
        <button class="btn${s.on ? "" : " solid"}" data-sector="${esc(s.id)}" data-on="${s.on ? "0" : "1"}">${s.on ? "Following" : "Follow"}</button></div>
      <p class="muted" style="margin:4px 0 6px">${s.on ? `${esc(s.signals_90_days)} records in 90 days across ${esc(s.technologies)} technologies` : `Not followed: ${esc(s.technologies)} technologies defined. ${esc(move)}`}</p>
      ${s.on ? mine.map(techRow).join("") : ""}</section>`;
  }).join("");
}
function sources() {
  return card("Where the evidence comes from", data.sources.map((s) => `<div class="guard"><span class="mark" style="color:var(--${s.state === "working" ? "sev-good" : s.state === "failing" ? "sev-high" : "sev-low"})">${s.state === "working" ? "✓" : s.state === "failing" ? "✗" : "•"}</span>
      <span><b>${esc(s.title)}</b> <span class="chipk">${esc(s.evidence)}</span><br><span class="muted">${esc(s.means)}.</span><br>
      <span class="muted">${s.state === "working" ? `Working · ${esc(s.found_last_run)} records read last run` : s.state === "failing" ? `Failing: ${esc(s.detail)}` : "Not run yet (its sectors may be switched off)"}</span></span></div>`).join(""),
    "All free, official or first-hand, and read through Buddy Connect. Only a technology's search phrase is sent. No paid databases, nothing scraped.");
}

// --- Predictions: the three engines, what each learns from, and whether a tested model is in use
async function loadPredict() {
  if (predictBusy) return;
  predictBusy = true;
  try { predictData = await api("/api/intel/predict"); } catch (e) { predictData = { failed: e.message }; }
  predictBusy = false;
  if (tab === "predict") render();
}
async function loadHistory(button) {
  button.disabled = true; button.textContent = "Starting…";
  try { const out = await api("/api/intel/predict", { history: true }); predictData = out; render(); notice(out.text); }
  catch (e) { notice(e.message); render(); }
}
function modelRow(m) {
  const bar = Math.min(100, Math.round(100 * m.judged / m.needed));
  const state = m.in_use ? `<span style="color:var(--sev-good)">✓ model in use</span>` : m.last_status === "rejected" ? `<span style="color:var(--sev-high)">✗ last model did not pass its test</span>`
    : m.enough ? `<span class="muted">• enough to train; next weekly run</span>` : `<span class="muted">○ learning: not enough judged calls yet</span>`;
  const tested = m.tested_on ? `<br><span class="muted">Tested on ${esc(m.tested_on)} calls it had never seen: ${Math.round((m.right_overall || 0) * 100)}% right overall${m.right_when_liked != null ? `, ${Math.round(m.right_when_liked * 100)}% on the ones it liked` : ""}</span>` : "";
  return `<div class="guard"><span><b>${esc(m.name[0].toUpperCase() + m.name.slice(1))}</b> · ${state}<br>
    <span class="muted">${esc(m.judged)} of ${esc(m.needed)} judged calls (${esc(m.of_each_outcome[0])} went one way, ${esc(m.of_each_outcome[1])} the other)</span>
    <div style="height:6px;border-radius:3px;background:var(--line);margin:6px 0"><div style="height:6px;border-radius:3px;width:${bar}%;background:var(--accent, #3987e5)"></div></div>${tested}</span></div>`;
}
function predict() {
  if (!predictData) { loadPredict(); return `<p class="muted">Loading…</p>`; }
  if (predictData.failed) return `<p class="err">${esc(predictData.failed)}</p>`;
  const h = predictData.history || {};
  const history = h.state === "done" ? `Loaded ${esc(h.records)} past records and ${esc(h.events)} past events with their results, covering ${esc(h.months)} months.`
    : h.state === "collecting" ? `Loading now: reached ${esc(h.reached || "the start")}, ${esc(h.new || 0)} records so far. Buddy will message when it is done.`
    : h.state === "failed" ? `The last load failed: ${esc(h.why || "")}` : "Not loaded yet. Until it is, the theme model learns only from events written since it was switched on.";
  return predictData.engines.map((e) => card(esc(e.title), `<p class="muted" style="margin-top:0">${esc(e.asks)}</p>
      ${e.models.map(modelRow).join("")}
      <p class="note" style="margin-bottom:4px">Learns from</p><ul style="margin:0 0 0 18px;padding:0">${e.learns_from.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>`,
      `${esc(e.open_calls)} calls open and waiting for their outcome${e.watched != null ? ` · ${esc(e.watched)} saved searches checked daily` : ""}`)).join("") +
    card("History for the theme model", `<p>${history}</p><p><button class="btn" id="history"${h.state === "collecting" ? " disabled" : ""}>${h.state === "done" ? "Load again" : "Load the last 30 months"}</button></p>`,
      "Asks the official dated sources (company filings, fundraising notices, federal awards, scholarly works, NSF grants) about past dates, then replays the rules month by month with only what was known then and that day's prices. It takes a while. It is slightly flattering: it only knows companies that exist today, and news cannot be loaded for the past.") +
    `<p class="note">${esc(predictData.note)}</p>`;
}

// --- AI models: which model does each job, the month's spending and limit, and a test to compare them
async function loadModels() {
  if (modelBusy) return;
  modelBusy = true;
  try { modelData = await api("/api/intel/models"); } catch (e) { modelData = { failed: e.message }; }
  modelBusy = false;
  if (tab === "models") render();
}
async function modelSet(body, button) {
  if (button) { button.disabled = true; button.textContent = "Working…"; }
  try { const out = await api("/api/intel/models", body); modelData = out; render(); notice(out.text); }
  catch (e) { notice(e.message); render(); }
}
function models() {
  if (!modelData) { loadModels(); return `<p class="muted">Loading…</p>`; }
  if (modelData.failed) return `<p class="err">${esc(modelData.failed)}</p>`;
  const m = modelData, sp = m.spending, ready = m.choices.filter((c) => c.ready);
  const options = (picked) => ready.map((c) => `<option value="${esc(c.id)}"${c.id === picked ? " selected" : ""}>${esc(c.title)}</option>`).join("");
  const jobs = m.jobs.map((j) => `<div class="guard"><span><b>${esc(j.title)}</b> ${j.in_use ? "" : `<span class="chipk">not used yet</span>`}<br><span class="muted">${esc(j.does)}</span><br>
      <select data-job="${esc(j.id)}" aria-label="Model for ${esc(j.title)}">${options(j.choice)}</select></span></div>`).join("");
  const spent = Object.entries(sp.jobs).map(([job, r]) => `<div class="guard"><span><b>${esc(job)}</b>: $${Number(r.usd).toFixed(4)} <span class="muted">in ${esc(r.calls)} calls</span></span></div>`).join("");
  const used = sp.cap ? Math.min(100, Math.round(100 * sp.total / sp.cap)) : 0;
  const tests = (m.tests || []).map((t) => `<tr><td><b>${esc(t.title)}</b><br><small class="muted">${esc(day(t.at))}</small></td><td>${esc(t.right)} of ${esc(t.headlines)} (${Math.round(t.share * 100)}%)</td><td>$${Number(t.usd).toFixed(4)}</td><td>${esc(t.seconds)}s</td></tr>`).join("");
  const missing = m.choices.filter((c) => !c.ready).map((c) => `<li><b>${esc(c.title)}</b>: ${esc(c.setup)}</li>`).join("");
  return card("Which model does each job", jobs, "Most of the platform uses no AI: tagging, stages, convergences and the practice scoring are code.") +
    card(`Spending this month (${esc(sp.month)})`, `<div class="kpis">${kpi("$" + Number(sp.total).toFixed(2), "spent on paid models")}${kpi("$" + Number(sp.cap).toFixed(2), "monthly limit")}${kpi(used + "%", "of the limit used")}</div>
      ${spent || `<p class="muted">Nothing spent yet.</p>`}
      <p style="margin-top:10px"><label>Monthly limit in dollars <input id="cap" type="number" min="0" max="500" step="1" value="${esc(sp.cap)}" style="width:90px"></label> <button class="btn" id="capset">Save</button></p>`,
      "At the limit, a job on a paid model goes back to the free choice until the month ends.") +
    card(`Test a model on ${esc(m.test_size)} headlines`, `<p><select id="testpick" aria-label="Model to test">${options("")}</select> <button class="btn" id="testrun">Run the test</button></p>
      ${tests ? `<div style="overflow-x:auto"><table class="grid"><tr><th>Model</th><th>Right</th><th>Cost</th><th>Time</th></tr>${tests}</table></div>` : `<p class="muted">No tests run yet. Run one before moving a job to a different model.</p>`}`,
      "Each headline has a known right label. A paid model's test costs a fraction of a cent and counts toward the limit.") +
    (missing ? card("Not set up", `<ul>${missing}</ul>`, "The model's id and price come from the provider's own pricing page. Buddy has none built in because they change.") : "") +
    `<p class="note">${esc(m.note)}</p>`;
}

async function openTech(id) {
  $("panel").innerHTML = `<p class="muted">Loading…</p>`; $("sheet").classList.add("open");
  let t;
  try { t = await api(`/api/intel/technology?id=${encodeURIComponent(id)}`); }
  catch (e) { $("panel").innerHTML = `<p class="err">${esc(e.message)}</p>`; return; }
  const W = 640, H = 170, L = 26, B = 22, T = 14, plotH = H - B - T, totals = t.months.map((m) => Object.keys(KIND).reduce((n, k) => n + (m[k] || 0), 0));
  const top = Math.max(1, ...totals), step = (W - L) / t.months.length, bw = Math.min(26, step - 8);
  const y = (v) => T + plotH - (v / top) * plotH;
  const bars = t.months.map((m, i) => { const x = L + i * step + (step - bw) / 2, v = totals[i];
    return `<g>${v ? `<path d="M${x},${T + plotH} V${y(v) + 4} q0,-4 4,-4 h${bw - 8} q4,0 4,4 V${T + plotH} Z" fill="var(--accent)"/><text class="val" x="${x + bw / 2}" y="${y(v) - 4}" text-anchor="middle">${v}</text>` : ""}
      ${i % 2 === 0 ? `<text x="${x + bw / 2}" y="${H - 6}" text-anchor="middle">${esc(m.month.slice(5))}/${esc(m.month.slice(2, 4))}</text>` : ""}
      <rect class="hit" data-i="${i}" x="${L + i * step}" y="0" width="${step}" height="${H}" fill="transparent"/></g>`; }).join("");
  const byKind = Object.keys(KIND).filter((k) => (t.evidence[k] || []).length).map((k) => `<h3>${esc(KIND[k][0].toUpperCase() + KIND[k].slice(1))}</h3>${t.evidence[k].map(evidence).join("")}`).join("");
  $("panel").innerHTML = `<button class="close" id="closesheet" aria-label="Close">×</button>
    <div class="crumbs">Intelligence › ${esc(t.sector)}</div><h2>${esc(t.name)}</h2>
    <div class="kinds">${stage(t.stage)}<span class="num">${esc(t.signals_90_days)} records in 90 days · ${esc(growth(t.growth))}</span></div>
    <p class="muted">Why this stage: ${esc(t.why)}.</p><div class="kinds">${kinds(t.kinds)}</div>
    <h3>Records per month</h3><div class="chart" id="months"><svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Records per month, last 12 months">
      <line x1="${L}" x2="${W}" y1="${T + plotH}" y2="${T + plotH}" stroke="var(--line)"/>${bars}</svg><div class="tip" id="tip"></div></div>
    ${t.linked.length ? `<h3>Shows up together with</h3><div class="kinds">${t.linked.map((l) => `<button class="chip" data-tech="${esc(l.id)}">${esc(l.name)} · ${esc(l.shared)}</button>`).join("")}</div>` : ""}
    ${t.organisations.length ? `<h3>Organisations in the records</h3>${t.organisations.slice(0, 12).map((o) => `<div class="ev">${esc(o.name)}<small>${esc(o.records)} record${o.records === 1 ? "" : "s"}: ${esc(o.kinds.join(", "))}</small></div>`).join("")}` : ""}
    ${byKind || `<p class="muted">No records collected for this technology yet.</p>`}`;
  $("closesheet").onclick = closeSheet; $("closesheet").focus();
  const box = $("months"), tip = $("tip");
  box.querySelectorAll(".hit").forEach((r) => {
    const show = (e) => { const m = t.months[+r.dataset.i]; tip.replaceChildren();
      const head = document.createElement("b"); head.textContent = m.month; tip.append(head);
      for (const k of Object.keys(KIND)) if (m[k]) { const row = document.createElement("div"); row.textContent = `${m[k]} ${KIND[k]}`; tip.append(row); }
      if (tip.childElementCount === 1) { const row = document.createElement("div"); row.textContent = "no records"; tip.append(row); }
      const b = box.getBoundingClientRect(); tip.style.display = "block";
      tip.style.left = Math.min(Math.max(0, e.clientX - b.left + 12), b.width - tip.offsetWidth) + "px";
      tip.style.top = Math.max(0, e.clientY - b.top - tip.offsetHeight - 8) + "px"; };
    r.addEventListener("pointermove", show); r.addEventListener("pointerdown", show);
    r.addEventListener("pointerleave", () => { tip.style.display = "none"; });
  });
  $("panel").querySelectorAll("[data-tech]").forEach((b) => b.onclick = () => openTech(b.dataset.tech));
}
function closeSheet() { $("sheet").classList.remove("open"); }

function render() {
  tabs();
  $("main").innerHTML = { overview, sectors, sources, models, predict,
    convergence: () => card("Technologies converging across sectors", data.convergences.length ? data.convergences.map((c, i) => convergence(c, i === 0)).join("")
      : `<p class="muted">None yet. It needs a few nights of records.</p>`, "The stage and rank come from the records by fixed rules. The interpretation, where there is one, is written by an AI from those records only and is marked as such.") }[tab]();
  document.querySelectorAll("#main [data-tech]").forEach((b) => b.onclick = () => openTech(b.dataset.tech));
  document.querySelectorAll("[data-sector]").forEach((b) => b.onclick = () => follow(b.dataset.sector, b.dataset.on === "1"));
  if ($("scan")) $("scan").onclick = scan;
  document.querySelectorAll("[data-job]").forEach((el) => el.onchange = () => modelSet({ job: el.dataset.job, choice: el.value }));
  if ($("history")) $("history").onclick = () => loadHistory($("history"));
  if ($("capset")) $("capset").onclick = () => modelSet({ cap: Number($("cap").value) });
  if ($("testrun")) $("testrun").onclick = () => modelSet({ test: $("testpick").value }, $("testrun"));
}
async function follow(sector, on) {
  try { data = await api("/api/intel/switch", { sector, on }); render(); notice(on ? "Following. Its evidence fills in from tonight's collection." : "No longer followed. It still gets a light weekly check."); }
  catch (e) { notice(e.message); }
}
async function scan() {
  try { notice((await api("/api/intel/scan", {})).text); } catch (e) { notice(e.message); }
}
$("sheet").onclick = (e) => { if (e.target === $("sheet")) closeSheet(); };
document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeSheet(); });
(async () => {
  try { data = await api("/api/intel"); render(); }
  catch (e) { $("main").innerHTML = `<p class="err">${esc(e.message)}</p>`; }
})();
