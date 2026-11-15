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

function everythingCard(e) {
  if (!e || e.requests == null) return "";
  const rows = (list) => (list || []).map((r) => `<tr><td>${esc(r.name)}</td><td>${esc(r.requests)}</td><td>$${Number(r.usd).toFixed(2)}</td></tr>`).join("");
  return card("All of Buddy's AI use", `<div class="kpis">${kpi("$" + Number(e.month_usd).toFixed(2), "spent this month")}${kpi(esc(e.requests), `requests in ${e.days} days`)}
      ${kpi(esc(e.claude_requests), "answered by Claude")}${kpi(esc(e.free_requests), "answered by free models")}
      ${kpi("$" + Number(e.saved_by_router_usd).toFixed(2), "saved by using a cheaper Claude tier")}${kpi("$" + Number(e.saved_by_free_usd).toFixed(2), "saved by free models")}</div>
    <h3>Where it went</h3><div style="overflow-x:auto"><table class="grid"><tr><th>What for</th><th>Requests</th><th>Cost</th></tr>${rows(e.by_purpose)}</table></div>
    <h3>By model</h3><div style="overflow-x:auto"><table class="grid"><tr><th>Model</th><th>Requests</th><th>Cost</th></tr>${rows(e.by_model)}</table></div>`,
    "This covers the main conversation and every other feature, not only the jobs chosen below. The full detail, request by request, is on the Usage tab.");
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
    everythingCard(m.everything) + card("Providers", prov) + card("Which model does each job", jobs, "Most of the platform uses no AI: tagging, stages, convergences and the practice scoring are code.") +
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

// --- Spending: totals and trend, which part of Buddy spends it, and the drill-down to single requests --------
let view = params.get("view") === "usage" ? "usage" : "providers", inApp = params.has("view");
let spend = null, spendBusy = false, spendDays = 30, spendComponent = "", spendPurpose = "";
async function loadSpend() {
  if (spendBusy) return;
  spendBusy = true;
  try { spend = await api(`/api/maestro/usage?days=${spendDays}&component=${encodeURIComponent(spendComponent)}&purpose=${encodeURIComponent(spendPurpose)}`); }
  catch (e) { spend = { failed: e.message }; }
  spendBusy = false; render();
}
const dollars = (v, d) => "$" + Number(v || 0).toFixed(d != null ? d : v >= 100 ? 0 : 2);
const share = (v) => Math.round((v || 0) * 100) + "%";
function dayChart(daysList) {
  const W = 640, H = 170, L = 8, B = 22, T = 16, plotH = H - B - T, top = Math.max(0.0001, ...daysList.map((d) => d.usd));
  const step = (W - L) / daysList.length, bw = Math.max(2, Math.min(22, step - 3));
  const bars = daysList.map((d, i) => { const x = L + i * step + (step - bw) / 2, h = (d.usd / top) * plotH;
    return `<g>${d.usd ? `<rect x="${x}" y="${T + plotH - h}" width="${bw}" height="${Math.max(1, h)}" rx="2" fill="var(--accent, #3987e5)"/>` : ""}
      ${i % Math.ceil(daysList.length / 6) === 0 ? `<text x="${x + bw / 2}" y="${H - 6}" text-anchor="middle">${esc(d.day.slice(5))}</text>` : ""}
      <rect class="hit" data-day="${i}" x="${L + i * step}" y="0" width="${step}" height="${H}" fill="transparent"/></g>`; }).join("");
  return `<div class="chart" id="spendchart"><svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Spending per day">
    <line x1="${L}" x2="${W}" y1="${T + plotH}" y2="${T + plotH}" stroke="var(--line)"/>${bars}</svg><div class="tip" id="spendtip"></div></div>`;
}
const barRow = (label, sub, g, attr) => `<button class="rowbtn" ${attr || "disabled"}><span class="rl"><b>${esc(label)}</b>${sub ? `<small>${esc(sub)}</small>` : ""}</span>
    <span class="rv">${dollars(g.usd)}<small>${share(g.share)} · ${esc(g.requests)} requests${g.failed ? ` · ${esc(g.failed)} failed` : ""}</small></span>
    <span class="rb"><i style="width:${Math.max(1, Math.round((g.share || 0) * 100))}%"></i></span></button>`;
function spending() {
  if (!spend) { loadSpend(); return `<p class="muted">Loading…</p>`; }
  if (spend.failed) return `<p class="err">${esc(spend.failed)}</p>`;
  const t = spend.totals, m = spend.month, sc = spend.scope, change = m.last_month_usd ? (m.heading_for_usd / m.last_month_usd - 1) : null;
  const periods = spend.periods.map((d) => `<button class="chip${d === spend.days ? " on" : ""}" data-days="${d}">${d} days</button>`).join("");
  const crumbs = `<div class="crumbs"><button class="link" data-up="all">All of Buddy</button>${sc.component ? ` › <button class="link" data-up="component">${esc((spend.purposes ? sc.purpose ? "" : sc.title : "") || componentTitle(sc.component))}</button>` : ""}${sc.purpose ? ` › <b>${esc(sc.title)}</b>` : ""}</div>`;
  const summary = card("AI spending", `<div class="chips">${periods}</div><div class="kpis" style="margin-top:10px">
      ${kpi(dollars(m.usd), "this month so far")}${kpi(dollars(m.heading_for_usd), "heading for by month end")}
      ${kpi(dollars(m.last_month_usd), "last month")}${kpi(change == null ? "–" : (change > 0 ? "▲ " : change < 0 ? "▼ " : "") + Math.abs(Math.round(change * 100)) + "%", "this month's pace against last month")}
      ${kpi(dollars(t.usd), `in the last ${spend.days} days`)}${kpi(esc(t.requests), "requests")}
      ${kpi(dollars(t.per_request, 4), "an average request")}${kpi(share(t.requests ? t.failed / t.requests : 0), "failed")}
      ${kpi(share(t.free_share), "answered by a free model")}</div>`,
    `Day ${esc(m.day)} of ${esc(m.days_in_month)}. "Heading for" is this month's spending so far at the same daily pace to the end of the month: arithmetic, not a prediction.`);
  const scopeCard = card(sc.component ? esc(sc.title) : "Spending per day", `${crumbs}${sc.covers ? `<p class="muted">${esc(sc.covers)}</p>` : ""}
      ${sc.component ? `<div class="kpis">${kpi(dollars(sc.usd), `in ${spend.days} days`)}${kpi(share(sc.share), "of all spending")}${kpi(esc(sc.requests), "requests")}${kpi(dollars(sc.per_request, 4), "an average request")}${kpi(sc.median_seconds == null ? "–" : sc.median_seconds + "s", "typical time")}</div>` : ""}
      ${dayChart(spend.by_day)}`);
  const parts = spend.components ? card("Which part of Buddy spends it", spend.components.length ? spend.components.map((c) => barRow(c.title, c.covers, c, `data-component="${esc(c.id)}"`)).join("") : `<p class="muted">No requests in this period yet.</p>`, "Tap a part to see what inside it spends.")
    : card(`Inside ${esc(componentTitle(sc.component))}`, (spend.purposes || []).map((p) => barRow(p.name, p.purpose, p, `data-purpose="${esc(p.purpose)}"`)).join("") || `<p class="muted">Nothing here in this period.</p>`, "Tap a line to see its models and latest requests. Shares are of this part.");
  const models_ = card("By model", `<div style="overflow-x:auto"><table class="grid"><tr><th>Model</th><th>Cost</th><th>Share</th><th>Requests</th><th>Average</th></tr>${spend.by_model.map((g) => `<tr><td>${esc(g.name)}</td><td>${dollars(g.usd)}</td><td>${share(g.share)}</td><td>${esc(g.requests)}</td><td>${dollars(g.per_request, 4)}</td></tr>`).join("")}</table></div>`)
    + card("By provider", spend.by_provider.map((g) => barRow(g.name, "", g)).join("") || `<p class="muted">Nothing in this period.</p>`);
  const reqs = spend.requests ? card(`Latest requests (${spend.requests.length})`, `<div style="overflow-x:auto"><table class="grid"><tr><th>When</th><th>Model</th><th>Cost</th><th>Time</th><th>Tokens</th></tr>${spend.requests.map((r) => `<tr><td>${esc(new Date(r.at).toLocaleString(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" }))}${r.ok ? "" : `<br><small style="color:var(--sev-high)">✗ ${esc(r.stop || "failed")}</small>`}${r.preview ? `<br><small class="muted">${esc(r.preview)}</small>` : ""}</td><td>${esc(r.model)}</td><td>${dollars(r.usd, 4)}</td><td>${r.seconds == null ? "–" : esc(Number(r.seconds).toFixed(1)) + "s"}</td><td>${esc(r.tokens_in)} in<br>${esc(r.tokens_out)} out</td></tr>`).join("")}</table></div>`, "Previews are short and have personal details masked. The log keeps 90 days.") : "";
  return summary + scopeCard + parts + reqs + models_;
}
const componentTitle = (id) => ((spend && (spend.components || []).find((c) => c.id === id)) || {}).title || (spend && spend.scope.component === id && !spend.scope.purpose ? spend.scope.title : spendTitles[id] || id);
const spendTitles = {};

function render() {
  if (spend && spend.components) spend.components.forEach((c) => { spendTitles[c.id] = c.title; });
  const switcher = inApp ? "" : `<div class="chips" style="margin-bottom:10px"><button class="chip${view === "providers" ? " on" : ""}" data-view="providers">Providers &amp; models</button><button class="chip${view === "usage" ? " on" : ""}" data-view="usage">Spending</button></div>`;
  $("main").innerHTML = switcher + (view === "usage" ? spending() : models());
  document.querySelectorAll("[data-view]").forEach((b) => b.onclick = () => { view = b.dataset.view; render(); });
  document.querySelectorAll("[data-days]").forEach((b) => b.onclick = () => { spendDays = Number(b.dataset.days); spend = null; render(); });
  document.querySelectorAll("[data-component]").forEach((b) => b.onclick = () => { spendComponent = b.dataset.component; spendPurpose = ""; spend = null; render(); scrollTo(0, 0); });
  document.querySelectorAll("[data-purpose]").forEach((b) => b.onclick = () => { spendPurpose = b.dataset.purpose; spend = null; render(); scrollTo(0, 0); });
  document.querySelectorAll("[data-up]").forEach((b) => b.onclick = () => { if (b.dataset.up === "all") spendComponent = ""; spendPurpose = ""; spend = null; render(); });
  const chart = $("spendchart"), tip = $("spendtip");
  if (chart) chart.querySelectorAll(".hit").forEach((r) => {
    const show = () => { const d = spend.by_day[+r.dataset.day]; tip.textContent = `${d.day}: ${dollars(d.usd, 4)} · ${d.requests} requests`; tip.style.display = "block"; };
    r.onmouseenter = show; r.onclick = show; r.onmouseleave = () => { tip.style.display = "none"; };
  });
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
