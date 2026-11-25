// Your email: the kinds of email you get, grouped from the senders and subjects only (no email is opened, no AI),
// and what Buddy does with each kind. Choose how important each group or sender is, and what Buddy takes from it.

const params = new URLSearchParams(location.search);
if (params.get("theme")) storageSet("buddyTheme", params.get("theme") === "light" ? "light" : "default");
if ((params.get("theme") || storageGet("buddyTheme")) === "light") document.documentElement.dataset.theme = "light";
function storageGet(k) { try { return localStorage.getItem(k); } catch { return null; } }
function storageSet(k, v) { try { localStorage.setItem(k, v); } catch {} }
if (params.get("token")) storageSet("buddyToken", params.get("token"));
const token = params.get("token") || storageGet("buddyToken") || "";
if (params.get("token") && storageGet("buddyToken") === token) history.replaceState(null, "", location.pathname);

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const day = (iso) => iso ? new Date(iso).toLocaleDateString(undefined, { month: "short", day: "numeric" }) : "";
let map = null, open = {}, polling = null;

async function api(path, body) {
  const res = await fetch(path, body === undefined ? { headers: { Authorization: `Bearer ${token}` } }
    : { method: "POST", headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" }, body: JSON.stringify(body) });
  if (!res.ok) {
    let msg = res.status === 401 ? "This page needs your Buddy link: open it from the Buddy app." : `Error ${res.status}`;
    try { msg = (await res.json()).detail || msg; } catch {}
    throw new Error(msg);
  }
  return res.json();
}

async function load() {
  try { map = await api("/api/mail/map"); render(); }
  catch (e) { $("main").innerHTML = `<p class="err">${esc(e.message)}</p>`; }
  const scanning = map && map.status && map.status.state === "scanning";
  clearTimeout(polling);
  if (scanning) polling = setTimeout(load, 3000);
}

async function scan() {
  try { await api("/api/mail/map/scan", {}); } catch (e) { alert(e.message); }
  load();
}

async function choose(body) {
  try { await api("/api/mail/map/choose", body); } catch (e) { alert(e.message); return; }
  load();
}

const card = (title, inner, note = "") =>
  `<section class="card"><h2>${title}</h2>${inner}${note ? `<p class="muted">${note}</p>` : ""}</section>`;

function importanceChips(target, now) {
  const opts = [["", "Buddy decides"], ...Object.entries(map.importance)];
  return `<div class="chips">${opts.map(([k, label]) =>
    `<button class="chip${(now || "") === k ? " on" : ""}" data-target="${esc(target)}" data-importance="${esc(k)}">${esc(label)}</button>`).join("")}</div>`;
}

function senderRow(s, category) {
  const imp = [["", "Like its group"], ...Object.entries(map.importance)].map(([k, label]) =>
    `<option value="${esc(k)}"${(s.importance || "") === k ? " selected" : ""}>${esc(label)}</option>`).join("");
  const cats = Object.entries(map.category_titles).map(([k, label]) =>
    `<option value="${esc(k)}"${k === category ? " selected" : ""}>${esc(label)}</option>`).join("");
  return `<div class="sender">
    <span class="who">${esc(s.name || s.address)}${s.is_new ? '<span class="new">new</span>' : ""}<small> ${esc(s.address)}</small></span>
    <span class="muted">${esc(s.emails)} · ${esc(day(s.last_at))}</span>
    ${s.subjects.length ? `<span class="subj">“${esc(s.subjects[0])}”</span>` : ""}
    <span class="pick"><select data-sender="${esc(s.address)}" data-field="importance" aria-label="How important">${imp}</select>
      <select data-sender="${esc(s.address)}" data-field="category" aria-label="Group">${cats}</select></span></div>`;
}

function render() {
  const m = map, st = m.status || {}, scanning = st.state === "scanning";
  const parts = [];
  const when = m.scanned_at ? `Last scan ${esc(day(m.scanned_at))}, the last ${esc(m.days)} days.` : "Not scanned yet.";
  const said = scanning ? "Scanning your email (who sent it and the subject only)…"
    : st.state === "failed" ? `The scan failed: ${esc(st.why || "")}`
    : st.failed && st.failed.length ? `Some accounts could not be read: ${esc(st.failed.join("; "))}` : when;
  parts.push(`<div class="top" style="margin-bottom:12px"><button class="btn${m.scanned_at ? " ghost" : ""}" id="scan"${scanning ? " disabled" : ""}>
    ${m.scanned_at ? "Scan again" : "Scan my email"}</button><span class="muted">${said}</span></div>`);
  if (!m.categories.length) {
    parts.push(card("How it works", `<p class="muted">Buddy looks at the last ${esc(m.days)} days of each of your email accounts: who
      sent each email, its subject and its date. No email is opened, nothing in your mailbox changes, and no AI reads it:
      rules on your server sort each sender into a group. Then you choose how important each group is and what Buddy does with it.</p>`));
    $("main").innerHTML = parts.join("");
    wire();
    return;
  }
  if (m.new.length) parts.push(card(`New senders · ${m.new.length}`, m.new.map((s) =>
    `<div class="row" style="cursor:default"><span class="name">${esc(s.name || s.address)}<small class="muted"> ${esc(s.address)}</small></span>
      <span class="tag">${esc(s.title)}</span></div>`).join(""), "Seen since your last scan. Find them in their group below to choose for them."));
  for (const c of m.categories) {
    const shown = open[c.key] ? c.senders : c.senders.slice(0, 5);
    const uses = c.uses.length ? `<p class="label">What Buddy takes from it</p><div class="chips">${c.uses.map((u) =>
      `<button class="chip${u.on ? " on" : ""}" data-target="category:${esc(c.key)}" data-use="${esc(u.key)}" data-on="${u.on ? "0" : "1"}">${u.on ? "✓ " : ""}${esc(u.title)}</button>`).join("")}</div>` : "";
    parts.push(card(`${esc(c.title)} · ${esc(c.senders_count)} sender${c.senders_count === 1 ? "" : "s"}, ${esc(c.emails)} emails`,
      `<p class="muted" style="margin-top:-4px">${esc(c.does)}</p><p class="label">How important</p>${importanceChips("category:" + c.key, c.importance)}
       ${uses}<p class="label">Senders</p>${shown.map((s) => senderRow(s, c.key)).join("")}
       ${c.senders.length > 5 ? `<button class="more" data-open="${esc(c.key)}">${open[c.key] ? "Show fewer" : `Show all ${esc(c.senders.length)}`}</button>` : ""}`));
  }
  parts.push(`<p class="muted">"Tell me now" makes Buddy tell you when such an email arrives; "Ignore" means it is never mentioned.
    A choice for one sender wins over its group. Nothing here deletes or moves email; "Always keep in the inbox" only tells
    the organizer to leave a sender's mail where it is.</p>
    <p><button class="btn ghost" id="forget">Delete the email map</button></p>`);
  $("main").innerHTML = parts.join("");
  wire();
}

function wire() {
  const b = $("scan");
  if (b) b.onclick = () => { b.disabled = true; scan(); };
  const f = $("forget");
  if (f) f.onclick = async () => {
    if (!confirm("Delete the email map and your choices? Your email itself is not touched.")) return;
    try { await api("/api/mail/map/forget", {}); } catch (e) { alert(e.message); }
    load();
  };
  document.querySelectorAll("[data-importance]").forEach((el) => el.onclick = () =>
    choose({ target: el.dataset.target, importance: el.dataset.importance }));
  document.querySelectorAll("[data-use]").forEach((el) => el.onclick = () =>
    choose({ target: el.dataset.target, use: el.dataset.use, on: el.dataset.on === "1" }));
  document.querySelectorAll("[data-open]").forEach((el) => el.onclick = () => { open[el.dataset.open] = !open[el.dataset.open]; render(); });
  document.querySelectorAll("select[data-sender]").forEach((el) => el.onchange = () =>
    choose({ target: "sender:" + el.dataset.sender, [el.dataset.field]: el.value }));
}

load();
