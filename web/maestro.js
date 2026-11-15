// Buddy Maestro: every AI provider Buddy can use, what each may see, which one does each job, what it has cost,
// a test to compare them, and a form to add another. Every piece of text is escaped.

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
let modelData = null, modelBusy = false, finder = null, finderTask = "sort", finderQuery = "", finderBusy = false;
const safe = (u) => /^https:\/\/[^\s"'<>]+$/.test(String(u || "")) ? u : "";

async function api(path, body) {
  const res = await fetch(path, body ? { method: "POST", headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" }, body: JSON.stringify(body) }
    : { headers: { Authorization: `Bearer ${token}` } });
  const out = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(out.detail || (res.status === 401 ? "Open Maestro from the Buddy app." : `Error ${res.status}`));
  return out;
}
function notice(text) {
  const n = $("notice"); n.textContent = text; n.style.display = "block";
  clearTimeout(notice.t); notice.t = setTimeout(() => { n.style.display = "none"; }, 6000);
}
const card = (title, body, foot) => `<section class="card"><h2>${title}</h2>${body}${foot ? `<p class="note">${foot}</p>` : ""}</section>`;
const kpi = (value, label) => `<div class="kpi"><b>${value}</b><span>${esc(label)}</span></div>`;
const day = (iso) => { const d = new Date(String(iso).length === 10 ? iso + "T12:00:00" : iso); return isNaN(d) ? "" : d.toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" }); };

async function loadModels() {
  if (modelBusy) return;
  modelBusy = true;
  try { modelData = await api("/api/maestro"); } catch (e) { modelData = { failed: e.message }; }
  modelBusy = false;
  render();
}
async function modelSet(body, button) {
  if (button) { button.disabled = true; button.textContent = "Working…"; }
  try { const out = await api("/api/maestro", body); modelData = out; render(); notice(out.text); }
  catch (e) { notice(e.message); render(); }
}
// --- the model finder: what suits a task, with prices from a public list ------------------------------------
async function loadFinder() {
  if (finderBusy) return;
  finderBusy = true;
  try { finder = await api(`/api/maestro/models?task=${encodeURIComponent(finderTask)}&q=${encodeURIComponent(finderQuery)}&limit=15`); }
  catch (e) { finder = { failed: e.message }; }
  finderBusy = false; render();
}
const price = (v) => v === 0 ? "free" : "$" + (v < 1 ? v.toFixed(2) : v.toFixed(v < 10 ? 2 : 0));
function finderCard(tasks) {
  const chips = (tasks || []).map((t) => `<button class="chip${t.id === finderTask ? " on" : ""}" data-task="${esc(t.id)}">${esc(t.title)}</button>`).join("");
  const search = `<p><input id="f-q" placeholder="Search by name, e.g. mistral or llama" aria-label="Search models" value="${esc(finderQuery)}" style="width:100%"></p>`;
  let body = `<p class="muted">Loading…</p>`;
  if (!finder) loadFinder();
  else if (finder.failed) body = `<p class="err">${esc(finder.failed)}</p>`;
  else if (finder.task === "personal") body = `<p><b>${esc(finder.note)}</b></p><p class="muted">Claude's models in use: ${esc((finder.claude || []).join(", "))}</p>`;
  else {
    const rows = (finder.models || []).map((m) => `<tr><td><b>${esc(m.name)}</b><br><small class="muted">${esc(m.maker)} · ${esc(m.id)}</small><br><small style="color:var(--${m.ready ? "sev-good" : "sev-low"})">${m.ready ? "✓" : "•"} ${esc(m.via)}</small></td>
      <td>${esc(price(m.in))}</td><td>${esc(price(m.out))}</td><td>${m.context ? esc(Math.round(m.context / 1000)) + "k" : "–"}</td></tr>`).join("");
    body = `${finder.advice ? `<p>${esc(finder.advice)}</p>` : ""}
      ${rows ? `<div style="overflow-x:auto"><table class="grid"><tr><th>Model</th><th>In</th><th>Out</th><th>Fits</th></tr>${rows}</table></div>` : `<p class="muted">No model in the list matches.</p>`}
      <p class="note">${esc(finder.found || 0)} match. ${esc(finder.note || "")} Prices are US dollars per million tokens${finder.at ? ", from a public price list read on " + esc(day(finder.at)) : ""}; check the maker's own page before relying on one. "Fits" is how much text the model takes at once.</p>`;
  }
  return card("Find a model for a task", `<div class="chips" style="flex-wrap:wrap">${chips}</div>${search}${body}<p><button class="btn" id="f-refresh">Refresh the price list</button></p>`);
}
function setupCard(rows) {
  return card("Get a key and set up a provider", (rows || []).map((r) => `<div class="guard"><span class="mark" style="color:var(--${r.set_up ? "sev-good" : "sev-low"})">${r.set_up ? "✓" : "○"}</span>
      <span><b>${esc(r.title)}</b> <span class="chipk">${r.set_up ? "key is set" : "no key yet"}</span><br><span class="muted">${esc(r.free)}</span><br>
      ${safe(r.page) ? `1. <a href="${safe(r.page)}" target="_blank" rel="noopener noreferrer">Open its key page</a> and create a key yourself.<br>` : ""}
      ${safe(r.page) ? "2" : "1"}. On the server, run: <code>${esc(r.command)}</code><br>
      <button class="btn" data-check="${esc(r.id)}" style="margin-top:6px">Check the key</button></span></div>`).join(""),
    "Buddy cannot sign up for you or create a key: each provider needs you to make the account and accept its terms. The check asks the provider to list its models with the key that is set; the key is never shown.");
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
  const STATE = { working: ["✓", "sev-good", "working"], failing: ["✗", "sev-high", "failing"], unused: ["•", "sev-low", "set up, not used yet"], not_set_up: ["○", "sev-low", "not set up"], missing_keys: ["!", "sev-high", "half set up"] };
  const prov = (m.providers || []).map((p) => { const st = p.off ? ["⏻", "sev-low", "switched off"] : STATE[p.state] || (p.set_up ? STATE.unused : STATE.not_set_up);
    return `<div class="guard"><span class="mark" style="color:var(--${st[1]})">${st[0]}</span><span><b>${esc(p.title)}</b> <span class="chipk">${esc(st[2])}</span> <span class="chipk">${esc(p.paid)}</span><br>
      <span class="muted">${esc(p.role)}</span><br><span style="color:var(--${p.may_see_personal ? "sev-good" : "sev-low"})">${p.may_see_personal ? "🔒 May see your personal data" : "Public work only: never sees anything about you"}</span>
      ${p.detail ? `<br><span class="muted">Last problem: ${esc(p.detail)}</span>` : ""}${p.added ? `<br><button class="btn" data-remove="${esc(p.added)}">Remove</button>` : ""}</span></div>`; }).join("");
  const add = `<p class="muted">Any provider that speaks the common chat format can be added. It is used only for public work, and only for the jobs you assign to it. Its key is entered on the server with the hidden prompt, never here.</p>
    <p><input id="p-name" placeholder="Short name, e.g. together" aria-label="Short name" style="width:100%"></p>
    <p><input id="p-base" placeholder="Address, e.g. https://api.example.com/v1" aria-label="Address" style="width:100%"></p>
    <p><input id="p-model" placeholder="Model id, from the provider's page" aria-label="Model id" style="width:100%"></p>
    <p><label>Price per million tokens: in <input id="p-in" type="number" min="0" step="0.01" value="0" style="width:80px"></label> <label>out <input id="p-out" type="number" min="0" step="0.01" value="0" style="width:80px"></label></p>
    <p><button class="btn" id="p-add">Add provider</button></p>`;
  return card("The privacy rule", `<p><b>${esc(m.rule || "")}</b></p>`, "Checked in code in one place. Text meant to be public is also checked for personal details before it leaves, and goes to Claude if it has any.") +
    card("Providers", prov) + card("Which model does each job", jobs, "Most of the platform uses no AI: tagging, stages, convergences and the practice scoring are code.") +
    card(`Spending this month (${esc(sp.month)})`, `<div class="kpis">${kpi("$" + Number(sp.total).toFixed(2), "spent on paid models")}${kpi("$" + Number(sp.cap).toFixed(2), "monthly limit")}${kpi(used + "%", "of the limit used")}</div>
      ${spent || `<p class="muted">Nothing spent yet.</p>`}
      <p style="margin-top:10px"><label>Monthly limit in dollars <input id="cap" type="number" min="0" max="500" step="1" value="${esc(sp.cap)}" style="width:90px"></label> <button class="btn" id="capset">Save</button></p>`,
      "At the limit, a job on a paid model goes back to the free choice until the month ends.") +
    card(`Test a model on ${esc(m.test_size)} headlines`, `<p><select id="testpick" aria-label="Model to test">${options("")}</select> <button class="btn" id="testrun">Run the test</button></p>
      ${tests ? `<div style="overflow-x:auto"><table class="grid"><tr><th>Model</th><th>Right</th><th>Cost</th><th>Time</th></tr>${tests}</table></div>` : `<p class="muted">No tests run yet. Run one before moving a job to a different model.</p>`}`,
      "Each headline has a known right label. A paid model's test costs a fraction of a cent and counts toward the limit.") +
    finderCard(m.finder_tasks) + setupCard(m.setup) +
    card("Add a provider", add) +
    (missing ? card("Not set up", `<ul>${missing}</ul>`, "The model's id and price come from the provider's own pricing page. Buddy has none built in because they change.") : "") +
    `<p class="note">${esc(m.note)}</p>`;
}

function render() {
  $("main").innerHTML = models();
  document.querySelectorAll("[data-job]").forEach((el) => el.onchange = () => modelSet({ job: el.dataset.job, choice: el.value }));
  document.querySelectorAll("[data-remove]").forEach((b) => b.onclick = () => modelSet({ remove: b.dataset.remove }));
  if ($("p-add")) $("p-add").onclick = () => modelSet({ add: { name: $("p-name").value, base: $("p-base").value, model: $("p-model").value, price_in: Number($("p-in").value), price_out: Number($("p-out").value) } }, $("p-add"));
  document.querySelectorAll("[data-task]").forEach((b) => b.onclick = () => { finderTask = b.dataset.task; finder = null; render(); });
  document.querySelectorAll("[data-check]").forEach((b) => b.onclick = () => modelSet({ check: b.dataset.check }, b));
  if ($("f-q")) $("f-q").onchange = () => { finderQuery = $("f-q").value; finderTask = finderQuery ? "" : finderTask; finder = null; render(); };
  if ($("f-refresh")) $("f-refresh").onclick = async () => { await modelSet({ refresh: true }, $("f-refresh")); finder = null; render(); };
  if ($("capset")) $("capset").onclick = () => modelSet({ cap: Number($("cap").value) });
  if ($("testrun")) $("testrun").onclick = () => modelSet({ test: $("testpick").value }, $("testrun"));
}
render();
