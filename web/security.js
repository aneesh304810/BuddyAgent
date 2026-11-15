// Buddy Security: the daily threat watch. The setup drawn layer by layer with each layer's level, what is open
// and what was fixed; charts of open findings and of found-versus-fixed per week; and every finding with why it
// matters, how it could be used against this server and the fix. Every piece of text is escaped.

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
const SEV = { critical: ["⛔", "Critical"], high: ["◆", "High"], medium: ["▲", "Medium"], low: ["ℹ", "Low"], good: ["✓", "Good"] };
const ORDER = ["critical", "high", "medium", "low"];
const TABS = [["overview", "Overview"], ["open", "Open"], ["fixed", "Fixed"], ["defences", "Defences"]];
let data = null, tab = TABS.some(([k]) => k === storageGet("securityTab")) ? storageGet("securityTab") : "overview", busy = false;

async function api(path, body) {
  const res = await fetch(path, body ? { method: "POST", headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" }, body: JSON.stringify(body) }
    : { headers: { Authorization: `Bearer ${token}` } });
  const out = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(out.detail || (res.status === 401 ? "Open Security from the Buddy app." : `Error ${res.status}`));
  return out;
}
function notice(text) {  // no alert(): the app's WebView doesn't show them
  const n = $("notice"); n.textContent = text; n.style.display = "block";
  clearTimeout(notice.t); notice.t = setTimeout(() => { n.style.display = "none"; }, 5000);
}
const card = (title, body, foot) => `<section class="card"><h2>${title}</h2>${body}${foot ? `<p class="note">${foot}</p>` : ""}</section>`;
const badge = (level) => `<span class="badge"><i style="background:var(--sev-${level})"></i>${SEV[level][0]} ${SEV[level][1]}</span>`;
const day = (iso) => { const d = new Date(String(iso).length === 10 ? iso + "T12:00:00" : iso); return isNaN(d) ? "" : d.toLocaleDateString(undefined, { month: "short", day: "numeric" }); };
const kpi = (value, label) => `<div class="kpi"><b>${value}</b><span>${esc(label)}</span></div>`;
const layerName = (key) => (data.layers.find((l) => l.key === key) || {}).name || key;

function tabs() {
  const n = { open: data.open.length, fixed: data.fixed.length };
  $("tabs").innerHTML = TABS.map(([k, label]) => `<button class="chip${k === tab ? " on" : ""}" role="tab" aria-selected="${k === tab}" data-tab="${k}">${label}${n[k] != null ? ` (${n[k]})` : ""}</button>`).join("");
  document.querySelectorAll("[data-tab]").forEach((b) => b.onclick = () => { tab = b.dataset.tab; storageSet("securityTab", tab); render(); });
}

// --- the setup, layer by layer -------------------------------------------------------------------------------
function node(key) {
  const l = data.layers.find((x) => x.key === key);
  const bits = [l.open ? `${l.open} open` : "nothing open", l.fixed ? `${l.fixed} fixed` : "", l.accepted ? `${l.accepted} accepted` : "",
                l.defences ? `${l.defences_on} of ${l.defences} defences on` : ""].filter(Boolean).join(" · ");
  return `<button class="node" data-layer="${esc(l.key)}" data-level="${esc(l.level)}" aria-label="${esc(l.name)}: ${SEV[l.level][1]}, ${esc(bits)}">
    <span class="top"><b>${esc(l.name)}</b>${badge(l.level)}</span>
    <small>${esc(bits)}</small>${l.exploited ? `<small><span class="hot">${l.exploited} being exploited now</span></small>` : ""}</button>`;
}
function diagram() {
  return `<div class="dia">
    <div class="tops"><div>${node("device")}</div><div class="actor">The internet<br><small>anyone</small></div></div>
    <div class="wire pair"><span>private and encrypted (Tailscale), with your access token</span><span>phone calls only, each request signed</span></div>
    ${node("network")}
    <div class="wire">only what the edge lets through</div>
    <div class="boundary"><span class="tag">Inside the server</span>
      ${node("server")}
      <div class="inner">${node("app")}${node("content")}${node("data")}${node("sandbox")}</div>
    </div>
    <div class="wire">encrypted calls out, through one gateway, with each service's own key</div>
    ${node("outside")}
  </div>`;
}

// --- charts --------------------------------------------------------------------------------------------------
function severityBar() {
  const total = data.open.length;
  const seg = total ? ORDER.filter((s) => data.counts[s]).map((s) =>
    `<span style="flex:${data.counts[s]};background:var(--sev-${s})" title="${SEV[s][1]}: ${data.counts[s]}"></span>`).join("") : `<span class="none"></span>`;
  const legend = ORDER.map((s) => `<span><i style="background:var(--sev-${s})"></i>${SEV[s][0]} ${SEV[s][1]}<b>${data.counts[s]}</b></span>`).join("");
  return card("Open findings by severity", `<div class="seg" role="img" aria-label="${esc(ORDER.map((s) => `${SEV[s][1]} ${data.counts[s]}`).join(", "))}">${seg}</div>
    <div class="legend">${legend}</div>`, total ? "" : "Nothing is open.");
}
function trendChart() {
  const t = data.trend, W = 640, H = 190, L = 26, B = 24, T = 14, plotH = H - B - T;
  const top = Math.max(1, ...t.map((w) => Math.max(w.found, w.fixed)));
  const step = (W - L) / t.length, bw = Math.min(22, step / 2 - 5);
  const y = (v) => T + plotH - (v / top) * plotH;
  const bar = (x, v, color) => v ? `<path d="M${x},${T + plotH} V${y(v) + 4} q0,-4 4,-4 h${bw - 8} q4,0 4,4 V${T + plotH} Z" fill="var(--${color})"/>
      <text class="val" x="${x + bw / 2}" y="${y(v) - 4}" text-anchor="middle">${v}</text>` : "";
  const ticks = [0, top].map((v) => `<line x1="${L}" x2="${W}" y1="${y(v)}" y2="${y(v)}" stroke="var(--grid)" stroke-width="1"/>
      <text x="${L - 6}" y="${y(v) + 4}" text-anchor="end">${v}</text>`).join("");
  const groups = t.map((w, i) => { const x = L + i * step + step / 2;
    return `<g>${bar(x - bw - 1, w.found, "found")}${bar(x + 1, w.fixed, "fixed")}
      <text x="${x}" y="${H - 6}" text-anchor="middle">${esc(day(w.week))}</text>
      <rect class="hit" data-i="${i}" x="${L + i * step}" y="0" width="${step}" height="${H}" fill="transparent"/></g>`; }).join("");
  const rows = t.map((w) => `<tr><td>Week of ${esc(day(w.week))}</td><td>${w.found}</td><td>${w.fixed}</td><td>${w.open_at_end}</td></tr>`).join("");
  return card("Found and fixed, week by week", `<div class="legend"><span><i style="background:var(--found)"></i>Found</span><span><i style="background:var(--fixed)"></i>Fixed</span></div>
    <div class="chart" id="trend"><svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Findings found and fixed per week, last ${t.length} weeks">${ticks}${groups}</svg><div class="tip" id="tip"></div></div>
    <details><summary>Show as a table</summary><table class="t"><tr><th>Week</th><th>Found</th><th>Fixed</th><th>Open at the end</th></tr>${rows}</table></details>`,
    t.every((w) => !w.found && !w.fixed) ? "Nothing found or fixed in these weeks yet." : "");
}
function wireTrend() {
  const box = $("trend"), tip = $("tip");
  if (!box) return;
  box.querySelectorAll(".hit").forEach((r) => {
    const show = (e) => { const w = data.trend[+r.dataset.i];
      tip.replaceChildren();
      const head = document.createElement("div"); head.textContent = `Week of ${day(w.week)}`; tip.append(head);
      for (const [k, label, color] of [["found", "found", "--found"], ["fixed", "fixed", "--fixed"]]) {
        const row = document.createElement("div"), key = document.createElement("i"), b = document.createElement("b");
        key.style.background = `var(${color})`; b.textContent = w[k]; row.append(key, b, document.createTextNode(` ${label}`)); tip.append(row); }
      const last = document.createElement("div"); last.textContent = `${w.open_at_end} open at the end`; tip.append(last);
      const b = box.getBoundingClientRect(); tip.style.display = "block";
      tip.style.left = Math.min(Math.max(0, e.clientX - b.left + 12), b.width - tip.offsetWidth) + "px";
      tip.style.top = Math.max(0, e.clientY - b.top - tip.offsetHeight - 8) + "px"; };
    r.addEventListener("pointermove", show); r.addEventListener("pointerdown", show);
    r.addEventListener("pointerleave", () => { tip.style.display = "none"; });
  });
}

// --- findings ------------------------------------------------------------------------------------------------
function finding(f, opened) {
  const closed = f.status !== "open";
  const meta = `${closed ? `<span class="badge"><i style="background:var(--sev-good)"></i>${f.status === "fixed" ? "✓ Fixed " + esc(day(f.closed_on)) : "Accepted"}</span>` : badge(f.severity)}
    ${f.exploited ? `<span class="hot">being exploited now</span>` : ""}
    <span class="muted">${esc(f.source)} · ${esc(layerName(f.layer))}</span>`;
  const part = (title, text) => text ? `<h4>${title}</h4><p>${esc(text)}</p>` : "";
  const facts = [f.component ? `Affects: ${f.component}${f.version ? " " + f.version : ""}` : "", f.fixed_in ? `Fixed in: ${f.fixed_in}` : "",
                 f.score != null ? `Published score: ${f.score} of 10` : "", f.chance_of_exploit_30_days ? `Chance of being exploited in the next 30 days: ${f.chance_of_exploit_30_days}` : "",
                 `First seen: ${day(f.first_seen)}`, (f.ids || []).length ? `Ids: ${f.ids.join(", ")}` : ""].filter(Boolean);
  return `<details class="find"${opened ? " open" : ""}><summary><div class="meta">${meta}</div>${esc(f.title)}</summary><div class="body">
    ${closed && f.note ? part(f.status === "fixed" ? "How it was fixed" : "Why it was accepted", f.note) : ""}
    ${part("Why it matters", f.why)}${part("How it could be used", f.exploit)}${part(closed ? "The fix" : "What to do", f.fix)}
    <h4>Details</h4><p class="ids">${facts.map(esc).join("<br>")}${safe(f.link) ? `<br><a href="${safe(f.link)}" target="_blank" rel="noopener noreferrer">Read the advisory</a>` : ""}</p>
    ${f.status === "open" ? `<div class="accept"><input placeholder="Accept this risk? Say why" maxlength="300" aria-label="Why this risk is acceptable" data-reason="${f.id}">
      <button class="btn" data-accept="${f.id}">Accept</button></div>` : ""}</div></details>`;
}
const findings = (rows, empty) => rows.length ? rows.map((f) => finding(f)).join("") : `<p class="muted">${empty}</p>`;
function guards(rows) {
  return rows.map((g) => `<div class="guard"><span class="mark" style="color:var(--${g.ok ? "sev-good" : "sev-high"})">${g.ok ? "✓" : "✗"}</span>
    <span><b>${esc(g.name)}</b>${g.ok ? "" : " (not on)"}<br><span class="muted">${esc(g.detail)}</span></span></div>`).join("");
}

function openLayer(key) {
  const l = data.layers.find((x) => x.key === key);
  const mine = (rows) => rows.filter((f) => f.layer === key);
  $("panel").innerHTML = `<button class="close" id="closesheet" aria-label="Close">×</button>
    <div class="crumbs">Security › ${esc(l.name)}</div>
    <h2>${esc(l.name)} ${badge(l.level)}</h2><p class="muted">${esc(l.what)}</p>
    <h3>Open (${l.open})</h3>${findings(mine(data.open), "Nothing is open in this layer.")}
    <h3>Defences in this layer</h3>${guards(data.defences.filter((g) => g.layer === key)) || `<p class="muted">None listed for this layer.</p>`}
    <h3>Fixed (${mine(data.fixed).length})</h3>${findings(mine(data.fixed), "Nothing has needed fixing here in the last 90 days.")}
    ${mine(data.accepted).length ? `<h3>Accepted</h3>${findings(mine(data.accepted), "")}` : ""}`;
  $("sheet").classList.add("open");
  $("closesheet").onclick = closeSheet; $("closesheet").focus();
  wire($("panel"));
}
function closeSheet() { $("sheet").classList.remove("open"); }

// --- tabs ----------------------------------------------------------------------------------------------------
function overview() {
  const level = data.worst || "good", scanned = data.last_scan;
  const sources = Object.entries(data.sources || {}).map(([k, v]) => `<div class="guard"><span class="mark" style="color:var(--${v === "ok" ? "sev-good" : "sev-medium"})">${v === "ok" ? "✓" : "▲"}</span>
    <span><b>${esc(k)}</b><br><span class="muted">${v === "ok" ? "checked" : esc(v)}</span></span></div>`).join("");
  return `<section class="card"><div class="status">${scanned ? badge(level) : ""}<b>${esc(data.status)}</b><span style="flex:1"></span>
      <button class="btn solid" id="scan"${busy ? " disabled" : ""}>${busy ? "Scanning…" : "Scan now"}</button></div>
      <p class="note">${scanned ? `Last scan ${esc(new Date(scanned).toLocaleString(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" }))} · ${esc(data.packages_checked)} packages checked. It runs by itself every day.` : "No scan has run yet. Tap Scan now."}
      ${data.patch_requested ? " A patch run has been requested and is in progress." : ""}</p></section>
    <div class="kpis" style="margin-bottom:12px">${kpi(data.open.length, "open")}${kpi(data.counts.critical + data.counts.high, "critical or high")}
      ${kpi(data.exploited_open, "being exploited now")}${kpi(data.fixed.length, "fixed in 90 days")}</div>
    ${card("Your setup, layer by layer", diagram(), "Tap a layer to see what is open, what protects it and what was fixed there. The colour bar and the label show the most serious open finding in that layer.")}
    ${severityBar()}${trendChart()}
    ${card("What was checked", sources || `<p class="muted">Nothing yet.</p>`, esc(data.limits))}`;
}
function render() {
  tabs();
  $("main").innerHTML = { overview,
    open: () => card(`Open (${data.open.length})`, findings(data.open, "Nothing is open."), "Most serious first. Tap one for why it matters, how it could be used and what to do.")
      + (data.accepted.length ? card("Risks you accepted", findings(data.accepted, "")) : ""),
    fixed: () => card(`Fixed in the last 90 days (${data.fixed.length})`, findings(data.fixed, "Nothing has needed fixing yet."), "A finding is marked fixed when a later scan no longer finds it.")
      + ((data.server_check.done || []).length ? card("What the weekly patch run did last", `<ul>${data.server_check.done.map((d) => `<li>${esc(d)}</li>`).join("")}</ul>`,
          data.server_check.at ? `Run on ${esc(day(data.server_check.at))}.` : "") : ""),
    defences: () => data.layers.map((l) => { const g = data.defences.filter((x) => x.layer === l.key);
      return g.length ? card(esc(l.name), guards(g)) : ""; }).join(""),
  }[tab]();
  wire($("main")); wireTrend();
  if ($("scan")) $("scan").onclick = scan;
}
function wire(root) {
  root.querySelectorAll("[data-layer]").forEach((b) => b.onclick = () => openLayer(b.dataset.layer));
  root.querySelectorAll("[data-accept]").forEach((b) => b.onclick = () => accept(b.dataset.accept, root.querySelector(`[data-reason="${b.dataset.accept}"]`).value));
}
async function scan() {
  if (busy) return;
  busy = true; render();
  try { const out = await api("/api/security/scan", {}); data = out;
    notice(out.new || out.fixed ? `${out.new} new, ${out.fixed} fixed.` : "Scan done: nothing changed."); }
  catch (e) { notice(e.message); }
  busy = false; render();
}
async function accept(id, reason) {
  try { const out = await api("/api/security/accept", { id: +id, reason }); data = out; closeSheet(); render(); notice("Marked as accepted."); }
  catch (e) { notice(e.message); }
}
$("sheet").onclick = (e) => { if (e.target === $("sheet")) closeSheet(); };
document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeSheet(); });
(async () => {
  try { data = await api("/api/security"); render(); }
  catch (e) { $("main").innerHTML = `<p class="err">${esc(e.message)}</p>`; }
})();
