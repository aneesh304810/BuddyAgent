// Buddy News: the topics you picked, and each day what's trending on them as cards that open the source.
// Stories come from Buddy's server (Hacker News, Google News, Google Trends, arXiv, GitHub, StockTwits, Alpaca,
// Reddit); every piece of text is escaped, and only https links are made clickable.

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
let data = null, busy = false;

function ago(iso) {
  if (!iso) return "not fetched yet";
  const mins = Math.round((Date.now() - new Date(iso).getTime()) / 60000);
  if (mins < 60) return `${Math.max(1, mins)} min ago`;
  if (mins < 48 * 60) return `${Math.round(mins / 60)} h ago`;
  return `${Math.round(mins / 1440)} days ago`;
}

function notice(text) {  // no alert(): the app's WebView doesn't show them
  const n = $("notice"); n.textContent = text; n.style.display = "block";
  clearTimeout(notice.t); notice.t = setTimeout(() => { n.style.display = "none"; }, 3500);
}

async function api(path, body) {
  const res = await fetch(path, body ? { method: "POST", headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
    body: JSON.stringify(body) } : { headers: { Authorization: `Bearer ${token}` } });
  const out = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(out.detail || (res.status === 401 ? "Open News from the Buddy app." : `Error ${res.status}`));
  return out;
}

async function load() {
  try { data = await api("/api/news"); render(); }
  catch (e) { $("main").innerHTML = `<p class="err">${esc(e.message)}</p>`; }
}

const stories = [];  // what each 👍 / 👎 button rates
function story(s, topic) {
  const url = safe(s.url), i = stories.push({ ...s, topic }) - 1;
  const meta = (s.sources || []).map((x) => `<span class="src">${esc(x)}</span>`).join("") +
    (s.points ? `<span>${Number(s.points).toLocaleString()} points</span>` : "") +
    (s.comments ? `<span>${Number(s.comments).toLocaleString()} comments</span>` : "") +
    (s.because ? `<span>· you like ${esc(s.because.join(", "))}</span>` : "");
  const inner = `<b>${esc(s.title)}</b>${s.take ? `<p class="take">${esc(s.take)}</p>` : ""}`;
  return `<div class="story">${url ? `<a href="${esc(url)}" target="_blank" rel="noopener" style="color:inherit;text-decoration:none;display:block">${inner}</a>` : inner}
    <div class="meta">${meta}<span style="flex:1"></span>
      <button class="rate${s.rated === 1 ? " on" : ""}" data-rate="${i}" data-up="1" aria-label="Like: more like this">👍</button>
      <button class="rate${s.rated === -1 ? " on" : ""}" data-rate="${i}" data-up="0" aria-label="Don't like: less like this">👎</button></div></div>`;
}

async function rate(button) {
  const s = stories[Number(button.dataset.rate)], up = button.dataset.up === "1";
  const again = button.classList.contains("on");  // tapping the same one again clears it
  try {
    const out = await api("/api/news/feedback", { topic: s.topic, title: s.title, url: s.url, sources: s.sources, up: again ? null : up });
    button.parentElement.querySelectorAll(".rate").forEach((b) => b.classList.remove("on"));
    if (!again) button.classList.add("on");
    notice(again ? "Cleared." : out.text);
  } catch (e) { notice(e.message); }
}

function render() {
  const topics = data.topics || [];
  $("label").textContent = topics.length ? `· ${topics.length} topic${topics.length === 1 ? "" : "s"}` : "";
  $("topics").innerHTML = topics.map((t) => `<span class="topic">${esc(t.name)}<button aria-label="Remove ${esc(t.name)}" data-rm="${esc(t.name)}">✕</button></span>`).join("")
    || `<span class="muted">No topics yet: add one below.</span>`;
  stories.length = 0;
  $("sugg").innerHTML = (data.liked_suggestions || []).map((s) => `<button class="sugg" data-add="${esc(s)}" title="From stories you liked">♥ ${esc(s)}</button>`).join("") +
    (data.suggestions || []).slice(0, 10).map((s) => `<button class="sugg" data-add="${esc(s)}">+ ${esc(s)}</button>`).join("");
  $("main").innerHTML = (data.feed || []).map((f) => {
    const rising = (f.rising || []).slice(0, 5).map((r) => `${esc(r.query)} (${esc(r.value)})`).join(" · ");
    return `<section class="card"><div class="head"><h2>${esc(f.topic)}</h2>
        <button class="btn" data-refresh="${esc(f.topic)}">↻ Refresh</button></div>
      <p class="muted" style="margin:-6px 0 6px">${esc(ago(f.at))}${(f.sources || []).length ? " · " + esc(f.sources.join(", ")) : ""}</p>
      ${f.headline ? `<p class="headline"><b>${esc(f.headline)}</b></p>` : ""}
      ${(f.stories || []).length ? f.stories.slice(0, 8).map((x) => story(x, f.topic)).join("") :
        `<p class="muted">Nothing yet. Tap Refresh, or wait for the morning update.</p>`}
      ${rising ? `<p class="note"><b>Rising searches:</b> ${rising}</p>` : ""}
      ${f.debate ? `<p class="note"><b>Where people disagree:</b> ${esc(f.debate)}</p>` : ""}
    </section>`;
  }).join("") + `<p class="muted">Updated every morning; a message comes if a topic suddenly takes off. Summaries by a free
    model from public posts. Tap a story to open its source; 👍 and 👎 teach Buddy what you want more and less of${data.rated ? ` (${data.rated} rated so far)` : ""}.</p>`;
  document.querySelectorAll("[data-rm]").forEach((b) => b.onclick = () => change("remove", b.dataset.rm));
  document.querySelectorAll("[data-add]").forEach((b) => b.onclick = () => change("add", b.dataset.add));
  document.querySelectorAll("[data-refresh]").forEach((b) => b.onclick = () => refresh(b.dataset.refresh, b));
  document.querySelectorAll("[data-rate]").forEach((b) => b.onclick = () => rate(b));
}

async function change(action, name) {
  if (busy || !name.trim()) return;
  busy = true;
  notice(action === "add" ? `Adding ${name} and finding what's trending…` : `Removing ${name}…`);
  try { data = await api("/api/news/topics", { action, name }); render(); notice(data.text || "Saved."); $("newtopic").value = ""; }
  catch (e) { notice(e.message); }
  busy = false;
}

async function refresh(topic, button) {
  if (busy) return;
  busy = true; button.textContent = "Looking…"; button.disabled = true;
  try { data = await api("/api/news/refresh", { topic }); render(); notice(`${topic} is up to date.`); }
  catch (e) { notice(e.message); button.textContent = "↻ Refresh"; button.disabled = false; }
  busy = false;
}

$("addbtn").onclick = () => change("add", $("newtopic").value);
$("newtopic").addEventListener("keydown", (e) => { if (e.key === "Enter") change("add", $("newtopic").value); });
load();
