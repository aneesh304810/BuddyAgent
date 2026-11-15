// Buddy Orchestra: every AI provider Buddy can use, what each may see, which one does each job, what it has cost,
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
let modelData = null, modelBusy = false;

async function api(path, body) {
  const res = await fetch(path, body ? { method: "POST", headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" }, body: JSON.stringify(body) }
    : { headers: { Authorization: `Bearer ${token}` } });
  const out = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(out.detail || (res.status === 401 ? "Open Orchestra from the Buddy app." : `Error ${res.status}`));
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
  try { modelData = await api("/api/orchestra"); } catch (e) { modelData = { failed: e.message }; }
  modelBusy = false;
  render();
}
async function modelSet(body, button) {
  if (button) { button.disabled = true; button.textContent = "Working…"; }
  try { const out = await api("/api/orchestra", body); modelData = out; render(); notice(out.text); }
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
    card("Add a provider", add) +
    (missing ? card("Not set up", `<ul>${missing}</ul>`, "The model's id and price come from the provider's own pricing page. Buddy has none built in because they change.") : "") +
    `<p class="note">${esc(m.note)}</p>`;
}

function render() {
  $("main").innerHTML = models();
  document.querySelectorAll("[data-job]").forEach((el) => el.onchange = () => modelSet({ job: el.dataset.job, choice: el.value }));
  document.querySelectorAll("[data-remove]").forEach((b) => b.onclick = () => modelSet({ remove: b.dataset.remove }));
  if ($("p-add")) $("p-add").onclick = () => modelSet({ add: { name: $("p-name").value, base: $("p-base").value, model: $("p-model").value, price_in: Number($("p-in").value), price_out: Number($("p-out").value) } }, $("p-add"));
  if ($("capset")) $("capset").onclick = () => modelSet({ cap: Number($("cap").value) });
  if ($("testrun")) $("testrun").onclick = () => modelSet({ test: $("testpick").value }, $("testrun"));
}
render();
