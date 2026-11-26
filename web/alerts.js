// Buddy Alerts: everything Buddy sent on its own, grouped by what it's about, with thumbs (Buddy learns which
// kinds matter to you), copy, and a level per kind. Every piece of text is escaped; only https links are clickable.

const params = new URLSearchParams(location.search);
function storageGet(k) { try { return localStorage.getItem(k); } catch { return null; } }
function storageSet(k, v) { try { localStorage.setItem(k, v); } catch {} }
if (params.get("token")) storageSet("buddyToken", params.get("token"));
const token = params.get("token") || storageGet("buddyToken") || "";
if (params.get("token") && storageGet("buddyToken") === token) history.replaceState(null, "", location.pathname);

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const LEVEL = { alert: "🔔 Pops up and is spoken", notify: "🔕 Silent notification", quiet: "💬 Chat only", muted: "🗄️ Alerts page only" };
let data = null, tab = storageGet("alertsTab") || "all";

async function api(path, body) {
  const res = await fetch(path, body ? { method: "POST", headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" }, body: JSON.stringify(body) }
    : { headers: { Authorization: `Bearer ${token}` } });
  const out = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(out.detail || (res.status === 401 ? "Open Alerts from the Buddy app." : `Error ${res.status}`));
  return out;
}

// **bold**, "- " bullets and https links, from escaped text only.
function rich(text) {
  const inline = (s) => esc(s).replace(/\*\*(.+?)\*\*/g, "<b>$1</b>")
    .replace(/https:\/\/[^\s<)]+/g, (u) => `<a href="${u}" target="_blank" rel="noopener noreferrer">${u.replace("https://", "").slice(0, 40)}</a>`);
  let html = "", list = false;
  for (const line of String(text || "").split("\n")) {
    const m = line.match(/^\s*[-*•]\s+(.*)/);
    if (m) { if (!list) { html += "<ul>"; list = true; } html += `<li>${inline(m[1])}</li>`; continue; }
    if (list) { html += "</ul>"; list = false; }
    if (line.trim()) html += `<div>${inline(line)}</div>`;
  }
  return html + (list ? "</ul>" : "");
}
const plain = (text) => String(text || "").replace(/\*\*(.+?)\*\*/g, "$1");
const when = (iso) => { const d = new Date(iso); return isNaN(d) ? "" : d.toLocaleString(undefined, { weekday: "short", hour: "numeric", minute: "2-digit", month: "short", day: "numeric" }); };

function tabs() {
  const all = [["all", "All", data.groups.reduce((n, g) => n + g.count, 0)], ...data.groups.map((g) => [g.category, g.category, g.count]), ["kinds", "⚙️ What matters", ""]];
  if (!all.some(([k]) => k === tab)) tab = "all";
  $("tabs").innerHTML = all.map(([k, label, n]) =>
    `<button class="chip${k === tab ? " on" : ""}" role="tab" aria-selected="${k === tab}" data-tab="${esc(k)}">${esc(label)}${n !== "" ? ` · ${n}` : ""}</button>`).join("");
  $("tabs").querySelectorAll("button").forEach((b) => b.onclick = () => { tab = b.dataset.tab; storageSet("alertsTab", tab); render(); });
}

function card(a) {
  return `<section class="card alert" data-id="${esc(a.id)}"><div class="body">${rich(a.text)}</div>
    <div class="meta"><span>${esc(when(a.at))}</span><span class="pill">${esc(a.name)}</span><span>${esc((LEVEL[a.level] || a.level).split(" ")[0])}</span>
      <span style="flex:1"></span>
      <button class="btn${a.rating === 1 ? " on" : ""}" data-rate="1" aria-label="This mattered" aria-pressed="${a.rating === 1}">👍</button>
      <button class="btn${a.rating === -1 ? " on" : ""}" data-rate="-1" aria-label="Not useful" aria-pressed="${a.rating === -1}">👎</button>
      <button class="btn" data-copy aria-label="Copy this message">Copy</button></div></section>`;
}

function renderKinds() {
  const by = {};
  data.topics.forEach((t) => (by[t.category] = by[t.category] || []).push(t));
  $("main").innerHTML = `<p class="muted">Each kind of message has a level. Your thumbs move it after three ratings; a level you pick here wins. Reminders, approvals, rides, urgent email, bills, security and calls never go below a silent notification.</p>` +
    Object.entries(by).map(([cat, kinds]) => `<h2>${esc(cat)}</h2><section class="card">${kinds.map((k) => `
      <div class="kind"><div><b>${esc(k.name)}</b><br><small>${esc(k.why)}${k.thumbs_up + k.thumbs_down ? ` · 👍 ${k.thumbs_up} 👎 ${k.thumbs_down}` : ""}</small></div>
        <select class="lvl" data-topic="${esc(k.topic)}" aria-label="Level for ${esc(k.name)}">
          ${data.levels.map((l) => `<option value="${l}"${l === k.level && k.why === "you set it" ? " selected" : ""}>${esc(LEVEL[l])}</option>`).join("")}
          <option value="learn"${k.why === "you set it" ? "" : " selected"}>Learn from my thumbs (now: ${esc(k.level)})</option>
        </select></div>`).join("")}</section>`).join("");
  $("main").querySelectorAll("select").forEach((s) => s.onchange = async () => {
    try { data.topics = (await api("/api/alerts/level", { topic: s.dataset.topic, level: s.value })).topics; renderKinds(); }
    catch (e) { $("main").insertAdjacentHTML("afterbegin", `<p class="err">${esc(e.message)}</p>`); }
  });
}

function render() {
  tabs();
  if (tab === "kinds") { renderKinds(); return; }
  const groups = tab === "all" ? data.groups : data.groups.filter((g) => g.category === tab);
  $("main").innerHTML = groups.length ? groups.map((g) => `<h2>${esc(g.category)} · ${g.count}</h2>${g.alerts.map(card).join("")}`).join("")
    : `<p class="muted">Nothing here yet. What Buddy tells you on its own shows up here, grouped by what it's about.</p>`;
  $("main").querySelectorAll(".alert").forEach((el) => {
    const a = data.groups.flatMap((g) => g.alerts).find((x) => x.id === el.dataset.id);
    el.querySelectorAll("[data-rate]").forEach((b) => b.onclick = async () => {
      const v = Number(b.dataset.rate), next = a.rating === v ? null : v;
      try { await api("/api/alerts/rate", { id: a.id, up: next === null ? null : next > 0 }); a.rating = next; data.topics = (await api("/api/alerts")).topics; render(); }
      catch (e) { b.textContent = "!"; b.title = e.message; }
    });
    el.querySelector("[data-copy]").onclick = async (ev) => {
      try { await navigator.clipboard.writeText(plain(a.text)); ev.target.textContent = "Copied"; }
      catch { const t = document.createElement("textarea"); t.value = plain(a.text); document.body.appendChild(t); t.select(); document.execCommand("copy"); t.remove(); ev.target.textContent = "Copied"; }
      setTimeout(() => { ev.target.textContent = "Copy"; }, 1500);
    };
  });
}

(async () => {
  try { data = await api("/api/alerts"); render(); }
  catch (e) { $("main").innerHTML = `<p class="err">${esc(e.message)}</p>`; }
})();
