// Buddy Deals: deals from the deal feeds and the deals inbox (products, flights, hotels), with each item's price
// history from the price book. Buddy never buys: links open the deal in your browser. Every piece of text is
// escaped, and only https links are made clickable.

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
const money = (v) => v == null ? "–" : "$" + Number(v).toLocaleString(undefined, { minimumFractionDigits: v % 1 ? 2 : 0, maximumFractionDigits: 2 });
const TABS = [["product", "🛍️ Products"], ["flight", "✈️ Flights"], ["hotel", "🏨 Hotels"]];
let tab = TABS.some(([k]) => k === storageGet("dealsTab")) ? storageGet("dealsTab") : "product", data = null, timer = null;

async function api(path) {
  const res = await fetch(path, { headers: { Authorization: `Bearer ${token}` } });
  const out = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(out.detail || (res.status === 401 ? "Open Deals from the Buddy app." : `Error ${res.status}`));
  return out;
}

function tabs() {
  $("tabs").innerHTML = TABS.map(([k, label]) => {
    const n = data ? data.counts[k] || 0 : "";
    return `<button class="chip${k === tab ? " on" : ""}" role="tab" aria-selected="${k === tab}" data-tab="${k}">${label}${n !== "" ? ` · ${n}` : ""}</button>`;
  }).join("");
  $("tabs").querySelectorAll("button").forEach((b) => b.onclick = () => { tab = b.dataset.tab; storageSet("dealsTab", tab); render(); });
}

function render() {
  tabs();
  const list = (data?.deals || []).filter((d) => d.domain === tab);
  if (!list.length) {
    $("main").innerHTML = `<p class="muted">No ${tab === "product" ? "product" : tab} deals here yet. Deals arrive from the deal feeds every morning and from your deals inbox every night.</p>`;
    return;
  }
  $("main").innerHTML = list.map((d, i) => `
    <section class="card"><div class="deal">
      <div class="what">
        <b>${esc(d.title)}</b>
        <small>${esc(d.store || d.source)} · ${esc(d.seen_on)}${d.ends ? ` · ends ${esc(d.ends)}` : ""}</small><br>
        ${d.is_lowest ? `<span class="pill low">Lowest Buddy has seen</span>` : d.lowest_seen ? `<span class="pill">Lowest seen ${money(d.lowest_seen)}</span>` : ""}
        ${d.off ? `<span class="pill">${Math.round(d.off * 100)}% off</span>` : ""}
        <div>${safe(d.url) ? `<a class="btn" href="${esc(safe(d.url))}" target="_blank" rel="noopener noreferrer">Open deal</a>` : ""}
          <button class="btn" data-i="${i}">Price history</button></div>
      </div>
      <div class="price"><b>${money(d.price)}</b>${d.regular_price ? `<s>${money(d.regular_price)}</s>` : ""}</div>
    </div></section>`).join("") +
    `<p class="muted">Prices are from the deal feeds and newsletters; check the price and seller before buying. Buddy never buys.</p>`;
  $("main").querySelectorAll("button[data-i]").forEach((b) => b.onclick = () => history_(list[Number(b.dataset.i)]));
}

function chartSvg(points) {
  if (points.length < 2) return `<p class="muted">Only ${points.length} price so far: the chart fills in as Buddy keeps seeing it.</p>`;
  const w = 600, h = 160, pad = 28, ps = points.map((p) => p.price), lo = Math.min(...ps), hi = Math.max(...ps);
  const x = (i) => pad + i * (w - pad * 2) / (points.length - 1);
  const y = (v) => hi === lo ? h / 2 : h - pad - (v - lo) * (h - pad * 2) / (hi - lo);
  const line = points.map((p, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(p.price).toFixed(1)}`).join(" ");
  return `<svg viewBox="0 0 ${w} ${h}" role="img" aria-label="Price history from ${money(lo)} to ${money(hi)}">
    <path d="${line}" fill="none" stroke="var(--accent)" stroke-width="2.5"/>
    <text x="${pad}" y="14">${money(hi)}</text><text x="${pad}" y="${h - 6}">${money(lo)}</text>
    <text x="${w - pad}" y="${h - 6}" text-anchor="end">${esc(points[points.length - 1].day)}</text>
    <text x="${pad + 60}" y="${h - 6}">${esc(points[0].day)}</text></svg>`;
}

async function history_(d) {
  $("panel").innerHTML = `<button class="close" aria-label="Close">×</button><h2>${esc(d.title)}</h2><p class="muted">Loading the price history…</p>`;
  $("sheet").classList.add("open");
  $("panel").querySelector(".close").onclick = () => $("sheet").classList.remove("open");
  try {
    const h = await api(`/api/deals/history?item=${encodeURIComponent(d.item)}&domain=${encodeURIComponent(d.domain)}`);
    $("panel").innerHTML = `<button class="close" aria-label="Close">×</button><h2>${esc(d.title)}</h2>
      <p class="muted">${esc(d.domain === "flight" ? "Route " + d.item.toUpperCase() : d.domain === "hotel" ? d.item.replace("hotels:", "Hotels in ") : "Every price Buddy has seen")}:
        ${h.points.length} day${h.points.length === 1 ? "" : "s"}, lowest ${money(h.lowest)}</p>
      ${chartSvg(h.points)}
      <p class="muted">Ask Buddy "should I buy it now?" for a buy-or-wait call.</p>
      ${safe(d.url) ? `<a class="btn" href="${esc(safe(d.url))}" target="_blank" rel="noopener noreferrer">Open deal</a>` : ""}`;
    $("panel").querySelector(".close").onclick = () => $("sheet").classList.remove("open");
  } catch (e) { $("panel").innerHTML += `<p class="err">${esc(e.message)}</p>`; }
}
$("sheet").onclick = (e) => { if (e.target === $("sheet")) $("sheet").classList.remove("open"); };

async function load() {
  try {
    data = await api(`/api/deals?days=${encodeURIComponent($("days").value)}&q=${encodeURIComponent($("q").value)}`);
    render();
  } catch (e) { $("main").innerHTML = `<p class="err">${esc(e.message)}</p>`; }
}
$("q").oninput = () => { clearTimeout(timer); timer = setTimeout(load, 350); };
$("days").onchange = load;
tabs();
load();
