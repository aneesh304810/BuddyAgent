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
const TABS = [["foryou", "For you"], ["product", "Products"], ["flight", "Flights"], ["hotel", "Hotels"], ["predicted", "Predicted"], ["alerts", "Alert settings"]];
let calls = null;
let predicted = null;
let tab = TABS.some(([k]) => k === storageGet("dealsTab")) ? storageGet("dealsTab") : "foryou", data = null, timer = null;

async function api(path, body) {
  const res = await fetch(path, body ? { method: "POST", headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" }, body: JSON.stringify(body) }
    : { headers: { Authorization: `Bearer ${token}` } });
  const out = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(out.detail || (res.status === 401 ? "Open Deals from the Buddy app." : `Error ${res.status}`));
  return out;
}

function tabs() {
  $("tabs").innerHTML = TABS.map(([k, label]) => {
    const n = k === "predicted" || k === "alerts" || k === "foryou" ? "" : data ? data.counts[k] || 0 : "";
    return `<button class="chip${k === tab ? " on" : ""}" role="tab" aria-selected="${k === tab}" data-tab="${k}">${label}${n !== "" ? ` · ${n}` : ""}</button>`;
  }).join("");
  $("tabs").querySelectorAll("button").forEach((b) => b.onclick = () => { tab = b.dataset.tab; storageSet("dealsTab", tab); render(); });
}

// For you: Buddy's open buy-now-or-wait calls, one card shape for products, flights and hotels: the call, the
// chance, one line of why, then where it came from and its record.
async function renderForYou() {
  try {
    calls = calls || await api("/api/deals/calls");
    const card = (c) => `<section class="card"><div class="deal"><div class="what"><b>${esc(c.what)}</b>
        <small>${esc(c.kind)}${c.travel_date ? " · " + esc(c.travel_date) : ""}</small></div>
        <div class="price"><b>${money(c.price)}</b></div></div>
      <p style="margin:10px 0 6px"><span class="call ${c.wait ? "wait" : "go"}">${c.wait ? "◷" : "✓"} ${esc(c.call)}</span>
        <span class="muted">${c.wait ? Math.round(c.chance * 100) + "% chance of a lower price" : Math.round((1 - c.chance) * 100) + "% chance this is as low as it gets"}</span></p>
      <p class="muted" style="margin:0 0 6px">${esc(c.why)}</p>
      <small class="muted">Buddy's call on ${esc(c.made_on)} · ${esc(c.record)}</small></section>`;
    $("main").innerHTML = (calls.calls.length ? calls.calls.map(card).join("")
      : `<p class="muted">No open calls. Ask Buddy "should I buy this now?" or "is this a good fare?" and the call shows here until it is settled.</p>`)
      + `<p class="muted">${esc(calls.note)} How these calls have turned out is in Intelligence, under Predictions.</p>`;
  } catch (e) { $("main").innerHTML = `<p class="err">${esc(e.message)}</p>`; }
}

async function renderPredicted() {
  try {
    predicted = predicted || (await api("/api/deals/predicted")).predictions;
    const icon = { product: "🛍️", company: "🏬", sale: "📅", flight: "✈️", hotel: "🏨" };
    $("main").innerHTML = (predicted.length ? predicted.map((p) => `
      <section class="card"><div class="deal"><div class="what">
        <b>${icon[p.type] || ""} ${esc(p.what)}</b>
        <small>${esc(p.why)}</small><br><span class="pill">${esc(p.confidence)} confidence</span></div>
        <div class="price"><b>${esc(p.when)}</b>${p.expect_price ? `<s style="text-decoration:none">about ${money(p.expect_price)}</s>` : ""}</div>
      </div></section>`).join("") : `<p class="muted">No predictions yet. Buddy needs to see a product, a flight route or a hotel place on deal at least twice, or a company's deals on three days, before it can predict the next one.</p>`) +
      `<p class="muted">Predicted from the deal patterns Buddy has learned, the sales calendar, and for flights and hotels: your open book-or-wait calls, routes and places whose deals keep coming back, and the cheapest time to book that Buddy has seen. Rough, not promises. Say "watch for it" to Buddy to get an alert.</p>`;
  } catch (e) { $("main").innerHTML = `<p class="err">${esc(e.message)}</p>`; }
}

// Trained models: how close each kind of call is to having enough data, and whether a model is in use.
function modelCard(models) {
  if (!models) return "";
  const row = ([name, m]) => {
    const state = m.model_in_use ? "✅ model in use" : m.last_run ? `❌ last model ${esc(m.last_run.status)} (${esc(m.last_run.on)})` : "⏳ collecting";
    const test = m.last_run && m.last_run.tested_on ? ` · tested on ${m.last_run.tested_on} unseen` : "";
    return `<div>${esc(name)}: <b>${m.rows} of ${m.needed}</b> judged calls · ${state}${test}</div>`;
  };
  return `<section class="card"><b>Trained models</b><br>
    <small>Each week Buddy checks whether a kind of call has enough judged results to train a model. A model is tested on calls it never saw and used only if it passes.</small>
    ${Object.entries(models).map(row).join("")}</section>`;
}

// Alert settings: a switch per shopping and travel category. Investing signals live on the Investments page.
async function renderAlerts(fresh) {
  try {
    const s = fresh || await api("/api/deals/signals");
    const sw = (c) => `<button class="chip${s.categories[c] ? " on" : ""}" data-cat="${esc(c)}" aria-pressed="${!!s.categories[c]}">${esc(c)}</button>`;
    const models = s.models ? Object.fromEntries(Object.entries(s.models).filter(([name]) => !/invest/i.test(name))) : null;
    $("main").innerHTML = `
      <section class="card"><b>Deal alerts on for</b><br><small>Tap a category to turn its deal alerts and buy-or-wait calls on or off.</small>
        <div class="chips" style="flex-wrap:wrap;margin-top:8px">${s.shopping.map(sw).join("")}</div></section>` + modelCard(models) +
      `<p class="muted">Buy and sell signals for stocks and funds, and Buddy's practice book, are on the Investments page under Signals and Practice.</p>`;
    $("main").querySelectorAll("[data-cat]").forEach((b) => b.onclick = async () => {
      try {
        const got = await api("/api/deals/signals/switch", { category: b.dataset.cat, on: !s.categories[b.dataset.cat] });
        renderAlerts({ ...s, categories: got.categories });
      } catch (e) { $("main").insertAdjacentHTML("afterbegin", `<p class="err">${esc(e.message)}</p>`); }
    });
  } catch (e) { $("main").innerHTML = `<p class="err">${esc(e.message)}</p>`; }
}

function render() {
  tabs();
  if (tab === "foryou") { renderForYou(); return; }
  if (tab === "predicted") { renderPredicted(); return; }
  if (tab === "alerts") { renderAlerts(); return; }
  const list = (data?.deals || []).filter((d) => d.domain === tab);
  if (!list.length) {
    $("main").innerHTML = `<p class="muted">No ${tab === "product" ? "product" : tab} deals here yet. Deals arrive from the deal feeds every morning and from your deals inbox every night.</p>`;
    return;
  }
  const total = (data.counts || {})[tab] || list.length;
  const more = total > list.length ? `<p class="muted">Showing the newest ${list.length} of ${total.toLocaleString()}. Search to narrow them down.</p>` : "";
  $("main").innerHTML = more + list.map((d, i) => `
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
