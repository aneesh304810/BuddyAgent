// Buddy Search: one search over many providers (buddysearch.py). Every piece of text is escaped; only http(s) links
// are clickable; each result says where it came from and which other sources also had it.

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
const link = (url, text, cls = "") => /^https?:\/\//i.test(String(url || "")) ? `<a class="${cls}" href="${esc(url)}" target="_blank" rel="noopener noreferrer">${esc(text)}</a>` : `<span class="${cls}">${esc(text)}</span>`;
const ENGINES = [["", "Buddy decides"], ["web", "Web"], ["local", "Places"], ["products", "Products"], ["news", "News"], ["research", "Research"], ["internal", "Your own things"]];
let engine = storageGet("searchEngine") || "", found = null, busy = false, status = null, showStatus = false, summary = storageGet("searchSummary") === "1", sure = false;

async function api(path, body) {
  const res = await fetch(path, body ? { method: "POST", headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" }, body: JSON.stringify(body) }
    : { headers: { Authorization: `Bearer ${token}` } });
  const out = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(out.detail || (res.status === 401 ? "Open Search from the Buddy app." : `Error ${res.status}`));
  return out;
}

function chips() {
  $("engines").innerHTML = ENGINES.map(([k, label]) => `<button class="chip${k === engine ? " on" : ""}" data-engine="${k}">${esc(label)}</button>`).join("")
    + `<label class="chip" style="cursor:pointer"><input type="checkbox" id="summ"${summary ? " checked" : ""}> Short answer</label>`;
  $("engines").querySelectorAll("[data-engine]").forEach((b) => b.onclick = () => { engine = b.dataset.engine; storageSet("searchEngine", engine); chips(); });
  $("summ").onchange = (e) => { summary = e.target.checked; storageSet("searchSummary", summary ? "1" : "0"); };
}

const TITLES = { web: "Web", local: "Places", products: "Products and prices", news: "News", research: "Research", internal: "Your own things" };
function result(r) {
  const x = r.extra || {}, bits = [];
  if (x.category) bits.push(esc(x.category));
  if (x.rating != null) bits.push(`★ ${esc(x.rating)}${x.reviews ? ` (${esc(x.reviews)})` : ""}`);
  if (x.open_now === true) bits.push("open now"); else if (x.open_now === false) bits.push("closed now");
  if (x.price != null) bits.push(`<b>$${esc(Number(x.price).toFixed(2))}</b>${x.store ? " at " + esc(x.store) : ""}${x.on_sale ? " · on sale" : ""}`);
  if (x.address) bits.push(esc(x.address));
  if (x.hours) bits.push(`hours: ${esc(x.hours)}`);
  if (x.venue) bits.push(esc(x.venue));
  if (x.phone) bits.push(esc(x.phone));
  return `<div class="res">${link(r.url, r.title || r.url, "t")}
    <div class="meta">${r.site ? `<span>${esc(r.site)}</span>` : ""}${r.published ? `<span>${esc(String(r.published).slice(0, 10))}</span>` : ""}
      ${r.found_by.map((s) => `<span class="tag">${esc(s)}</span>`).join("")}${r.corroborated ? `<span class="tag good">found by ${r.found_by.length}</span>` : ""}${r.official ? `<span class="tag good">official source</span>` : ""}</div>
    ${bits.length ? `<div class="meta">${bits.join(" · ")}</div>` : ""}
    ${r.snippet ? `<div class="snip">${esc(r.snippet)}</div>` : ""}
    ${x.website && x.website !== r.url ? `<div class="meta">${link(x.website, "website")}</div>` : ""}</div>`;
}

function render() {
  chips();
  if (busy) { $("main").innerHTML = `<p class="muted">Searching…</p>`; return; }
  let html = "";
  if (found && found.failed) html += `<p class="err">${esc(found.failed)}</p>`;
  else if (found) {
    const s = found.summary;
    if (s) html += `<section class="card"><h2>Short answer</h2><p class="answer">${esc(s.answer)}</p>${s.sources.map((c) => `<div class="meta">[${c.n}] ${link(c.url, c.title)} <span class="tag">${esc(c.source)}</span></div>`).join("")}</section>`;
    const fails = found.providers.filter((p) => !p.ok);
    html += `<p class="muted">Looked in ${esc(found.engines.map((e) => TITLES[e]).join(", "))}${found.area ? ` around ${esc(found.area)}` : ""} · ${found.providers.filter((p) => p.ok).map((p) => esc(p.title) + (p.cached ? " (kept from earlier)" : "")).join(", ") || "no source answered"} · ${(found.took_ms / 1000).toFixed(1)} s</p>`;
    if (fails.length) html += `<p class="muted">Didn't answer: ${fails.map((p) => `${esc(p.title)} (${esc(p.error)})`).join("; ")}</p>`;
    for (const [e, rs] of Object.entries(found.results)) {
      html += `<section class="card"><h2>${esc(TITLES[e] || e)} · ${rs.length}</h2>${rs.length ? rs.map(result).join("") : `<p class="muted">Nothing found here.</p>`}</section>`;
    }
    if (found.attribution.length) html += `<p class="muted">${found.attribution.map(esc).join(" · ")}</p>`;
    html += `<p class="muted">${esc(found.note)}</p>`;
  }
  html += `<p><button class="btn" id="status-toggle">${showStatus ? "Hide" : "Show"} providers and history</button></p>`;
  if (showStatus) html += statusCard();
  $("main").innerHTML = html;
  if ($("status-toggle")) $("status-toggle").onclick = () => { showStatus = !showStatus; if (showStatus && !status) loadStatus(); render(); };
  $("main").querySelectorAll("[data-again]").forEach((b) => b.onclick = () => { $("q").value = b.dataset.again; run(); });
  $("main").querySelectorAll("[data-forget]").forEach((b) => b.onclick = () => forget(Number(b.dataset.forget)));
  if ($("forget-all")) $("forget-all").onclick = () => { if (!sure) { sure = true; render(); return; } sure = false; forget(0); };
}

function statusCard() {
  if (!status) return `<p class="muted">Loading…</p>`;
  if (status.failed) return `<p class="err">${esc(status.failed)}</p>`;
  const KEEP = { full: "Kept", links: "Links kept", id: "Place ID only", none: "Not copied" };
  const rows = status.providers.map((p) => `<tr><td>${esc(p.title)}<div class="muted">${esc(p.engine)}${p.fallback_only ? ", when the others can't" : ""}</div></td>
      <td class="${p.ready ? "ok" : "no"}">${p.ready ? "Ready" : "Needs a key"}</td>
      <td>${p.daily_limit != null ? `${esc(p.today)} of ${esc(p.daily_limit)} today` : "—"}<div class="muted">${esc(p.week_calls)} this week${p.week_failed ? `, <span class="bad">${esc(p.week_failed)} failed</span>` : ""}${p.avg_ms != null ? `, ${esc(p.avg_ms)} ms` : ""}</div></td>
      <td>${esc(KEEP[p.keeps] || p.keeps)}<div class="muted">${esc(p.keeps_why)}</div></td></tr>`).join("");
  const hist = status.history.map((h) => `<div class="res"><span class="t">${esc(h.query)}</span><div class="meta">${esc(h.at.replace("T", " ").slice(0, 16))} · ${esc(h.engines.map((e) => TITLES[e]).join(", "))} · ${esc(h.results)} results
      <button class="btn" data-again="${esc(h.query)}">Again</button><button class="btn" data-forget="${h.id}">Delete</button></div></div>`).join("");
  return `<section class="card"><h2>Providers</h2><table><tr><th>Provider</th><th></th><th>Use</th><th>Storage</th></tr>${rows}</table>
      <p class="muted">Keys are set on the server: ${Object.values(status.keys).map((k) => `<code>sudo bash /opt/buddy/deploy/set.sh ${esc(k)}</code>`).join(" ")}</p></section>
    <section class="card"><h2>Your searches</h2>${hist || `<p class="muted">None yet.</p>`}
      <p><button class="btn danger" id="forget-all">${sure ? "Press again to delete all" : "Delete the history and everything kept"}</button></p>
      <p class="muted">${esc(status.kept)} results kept by their providers' terms. Your searches stay on your server until you delete them.</p></section>`;
}

async function loadStatus() {
  try { status = await api("/api/search/status"); } catch (e) { status = { failed: e.message }; }
  render();
}
async function forget(id) {
  try { status = await api("/api/search", { action: "forget", id }); } catch (e) { status = { failed: e.message }; }
  render();
}
async function run() {
  const q = $("q").value.trim();
  if (!q) return;
  busy = true; render();
  try { found = await api(`/api/search?q=${encodeURIComponent(q)}${engine ? `&engines=${engine}` : ""}${summary ? "&summary=1" : ""}`); }
  catch (e) { found = { failed: e.message }; }
  busy = false; status = null; if (showStatus) loadStatus();
  render();
}
$("form").onsubmit = (e) => { e.preventDefault(); run(); };
if (params.get("q")) { $("q").value = params.get("q"); run(); } else render();
