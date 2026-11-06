// Buddy Investments: trade strategy (ideas being watched, trades being tracked), market trends, the investment
// accounts Plaid reports, and the watchlist. Ideas, not financial advice: Buddy never places a trade.
// Every piece of text is escaped, and only https links are made clickable.

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
const TABS = [["strategy", "Strategy"], ["trends", "Trends"], ["accounts", "Accounts"], ["watch", "Watchlist"]];
let data = null, tab = TABS.some(([k]) => k === storageGet("investTab")) ? storageGet("investTab") : "strategy", busy = false;

const px = (v, m) => v == null ? "–" : (m === "india" ? "₹" : "$") +
  (Math.abs(v) >= 1000 ? Number(v).toLocaleString(undefined, { maximumFractionDigits: 0 }) :
   Math.abs(v) >= 1 ? Number(v).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 }) : Number(v).toPrecision(3));
const MARKET = { us: "US", crypto: "crypto", india: "India" };
// US and international markets are kept apart everywhere (crypto trades round the clock, worldwide: its own group)
const GROUPS = [["us", "🇺🇸 US market"], ["india", "🌏 International market"], ["crypto", "🪙 Crypto"]];
const TOPIC_GROUP = { markets: "us", india: "india", crypto: "crypto" };
function grouped(items, groupOf, draw, empty) {
  const html = GROUPS.map(([g, label]) => {
    const mine = items.filter((x) => groupOf(x) === g);
    return mine.length ? `<h3 class="group">${label}</h3>` + mine.map(draw).join("") : "";
  }).join("");
  return html || empty;
}
const usd = (v) => "$" + Number(v || 0).toLocaleString(undefined, { maximumFractionDigits: 0 });
const card = (title, body, foot) => `<section class="card"><h2>${title}</h2>${body}${foot ? `<p class="note">${foot}</p>` : ""}</section>`;

function notice(text) {  // no alert(): the app's WebView doesn't show them
  const n = $("notice"); n.textContent = text; n.style.display = "block";
  clearTimeout(notice.t); notice.t = setTimeout(() => { n.style.display = "none"; }, 4000);
}

async function api(path, body) {
  const res = await fetch(path, body ? { method: "POST", headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
    body: JSON.stringify(body) } : { headers: { Authorization: `Bearer ${token}` } });
  const out = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(out.detail || (res.status === 401 ? "Open Investments from the Buddy app." : `Error ${res.status}`));
  return out;
}

async function load() {
  try { data = await api("/api/invest"); render(); }
  catch (e) { $("main").innerHTML = `<p class="err">${esc(e.message)}</p>`; }
}

function tabs() {
  $("tabs").innerHTML = TABS.map(([k, label]) => `<button class="chip${k === tab ? " on" : ""}" role="tab" aria-selected="${k === tab}" data-tab="${k}">${label}</button>`).join("");
  document.querySelectorAll("[data-tab]").forEach((b) => b.onclick = () => { tab = b.dataset.tab; storageSet("investTab", tab); render(); });
}

function levels(x, m) {
  return `<div class="levels"><div>Entry<b>${px(x.entry, m)}</b></div><div>Stop<b>${px(x.stop, m)}</b></div>
    <div>Target 1<b>${px(x.t1, m)}</b></div><div>Target 2<b>${px(x.t2, m)}</b></div></div>`;
}

function strategy() {
  const s = data.strategy;
  const trade = (p) => `<div class="idea"><div class="top"><b>${esc(p.symbol)}</b>
      <span class="pill">bought ${esc(p.opened)}${p.shares ? ` · ${esc(p.shares)} shares` : ""}</span><span style="flex:1"></span>
      <button class="btn small" data-sold="${esc(p.symbol)}">I sold it</button></div>${levels(p, p.market)}</div>`;
  const idea = (x) => {
    const d = x.data || {}, o = d.option;
    return `<div class="idea"><div class="top"><b>${esc(x.symbol)}</b><span class="pill${x.status === "triggered" ? " hot" : ""}">${x.status === "triggered" ? "triggered" : "watching"}</span>
        <span class="muted">${esc(d.label || x.pattern)} · closed ${px(x.close, x.market)} · ${esc(x.day)}</span></div>
      ${levels(x, x.market)}
      ${d.take ? `<p style="margin:8px 0 0;font-size:14px">${esc(d.take)}</p>` : ""}
      ${d.sentiment ? `<p class="note">Chatter: ${esc(d.sentiment)}${d.risks ? ` · Risks: ${esc(d.risks)}` : ""}</p>` : ""}
      ${o ? `<p class="note">Option idea: ${esc(o.expires)} ${px(o.strike, x.market)} call, about ${px(o.mid, x.market)}${o.delta != null ? `, delta ${esc(o.delta)}` : ""}</p>` : ""}
    </div>`;
  };
  const pos = grouped(s.positions, (p) => p.market, trade, "");
  const ideas = grouped(s.setups, (x) => x.market, idea, "");
  const how = `How it works: every weekday after the US close Buddy scans ${esc((data.settings.markets || []).map((m) => MARKET[m] || m).join(", ") || "the markets")} plus your watchlist
    for breakouts, pullbacks and other patterns, and checks Reddit, StockTwits and news. In market hours it checks the ideas every
    15 minutes and tells you when one triggers. Say "I bought NVDA at 182" and Buddy watches the stop and targets for you.`;
  return card("Your trades", pos || `<p class="muted">No trades tracked. Tell Buddy when you buy one.</p>`) +
    card("Setups being watched", ideas || `<p class="muted">No setups right now. The next scan runs after the US close.</p>`,
      `Last scan: ${esc(s.last_scan || "not yet")}. The full plan for each is in Reports (Market scan).`) +
    card("Check a symbol", `<div class="add"><input id="sym" placeholder="AAPL, BTC, RELIANCE…" maxlength="20" aria-label="Symbol">
      <button class="btn solid" id="analyze">Check</button></div><div id="result"></div>`, how) +
    `<p class="muted">${esc(s.note)}</p>`;
}

function analysis(a) {
  const m = a.market, trend = a.close > (a.sma50 || 0) && (a.sma50 || 0) > (a.sma200 || 0) ? "uptrend" :
    a.close < (a.sma50 || Infinity) && (a.sma50 || 0) < (a.sma200 || Infinity) ? "downtrend" : "mixed";
  const setups = (a.setups || []).map((x) => `<div class="idea"><div class="top"><b>${esc(x.label || x.pattern)}</b></div>${levels(x, m)}</div>`).join("");
  const news = (a.news || []).slice(0, 4).map((n) => {
    const u = safe(n.url || n.link); const inner = `<b>${esc(n.headline || n.title)}</b><small>${esc(n.source || "")}</small>`;
    return u ? `<a class="story" href="${esc(u)}" target="_blank" rel="noopener">${inner}</a>` : `<div class="story">${inner}</div>`;
  }).join("");
  const r = a.reddit || {}, st = a.stocktwits || {};
  return `<div class="idea"><div class="top"><b>${esc(a.symbol)}</b><span class="muted">${px(a.close, m)} on ${esc(a.day)}</span>
      <span class="pill">${trend}</span>${a.rsi != null ? `<span class="pill">RSI ${esc(a.rsi)}</span>` : ""}</div>
    <div class="levels"><div>1 month<b>${esc(a.change_1m)}%</b></div><div>3 months<b>${esc(a.change_3m)}%</b></div>
      <div>52w high<b>${px(a.high_52w, m)}</b></div><div>52w low<b>${px(a.low_52w, m)}</b></div></div>
    <p class="note">20-day ${px(a.sma20, m)} · 50-day ${px(a.sma50, m)} · 200-day ${px(a.sma200, m)}
      ${r.posts ? ` · ${esc(r.posts)} Reddit posts this week` : ""}${st.messages ? ` · ${esc(st.messages)} StockTwits messages${st.bullish || st.bearish ? ` (${esc(st.bullish || 0)} bullish, ${esc(st.bearish || 0)} bearish)` : ""}` : ""}</p></div>
    ${setups ? `<p class="muted" style="margin:10px 0 0">Setups now</p>${setups}` : `<p class="note">No setup on the chart right now.</p>`}
    ${news ? `<p class="muted" style="margin:10px 0 0">News</p>${news}` : ""}
    <p class="note">${esc(a.note)}</p>`;
}

function trendsTab() {
  const t = data.trends;
  if (!t.feed.length) return card("Market trends", `<p class="muted">No market topics yet. Add one in News, for example US markets, Crypto or Nvidia.</p>`);
  const kind = Object.fromEntries(t.topics.map((x) => [x.name, x.kind]));
  return grouped(t.feed, (f) => TOPIC_GROUP[kind[f.topic] || f.kind], (f) => {
    const stories = (f.stories || []).slice(0, 6).map((s) => {
      const u = safe(s.url), inner = `<b>${esc(s.title)}</b>${s.take ? `<p>${esc(s.take)}</p>` : ""}<small>${esc((s.sources || []).join(" · "))}</small>`;
      return u ? `<a class="story" href="${esc(u)}" target="_blank" rel="noopener">${inner}</a>` : `<div class="story">${inner}</div>`;
    }).join("");
    const rising = (f.rising || []).slice(0, 5).map((r) => esc(r.query)).join(" · ");
    return `<section class="card"><div class="top" style="display:flex;justify-content:space-between;align-items:baseline;gap:8px">
        <h2>${esc(f.topic)}</h2><button class="btn small" data-refresh="${esc(f.topic)}">↻ Refresh</button></div>
      ${f.headline ? `<p style="margin:0 0 6px"><b>${esc(f.headline)}</b></p>` : ""}
      ${stories || `<p class="muted">Nothing yet. Tap Refresh, or wait for the morning update.</p>`}
      ${rising ? `<p class="note"><b>Rising searches:</b> ${rising}</p>` : ""}
      ${f.debate ? `<p class="note"><b>Where people disagree:</b> ${esc(f.debate)}</p>` : ""}</section>`;
  }, "") + `<p class="muted">Market topics from your News tab. Add or remove topics, and 👍 / 👎 stories, in News.</p>`;
}

function accountsTab() {
  const a = data.accounts;
  if (!a.connected) return card("Investment accounts", `<p class="muted">No bank or brokerage connected yet. Connect one from Money → Connect a bank.</p>`);
  const rows = a.accounts.map((x) => `<div class="row" style="cursor:default"><span class="name" style="white-space:normal">${esc(x.institution)} · ${esc(x.name)}
      <small class="muted">${x.last4 ? "••" + esc(x.last4) : ""} ${esc(x.type || "")}</small></span>
      <span class="amt"><b>${x.balance == null ? "–" : usd(x.balance)}</b>${x.as_of ? `<br><small class="muted">${esc(x.as_of)}</small>` : ""}</span></div>`).join("");
  const moved = a.moved_in.map((m) => `<div class="row" style="cursor:default"><span class="name" style="white-space:normal">${esc(m.recipient || m.name)}
      <small class="muted">${esc(m.day)}${m.account ? " · from ••" + esc(m.account) : ""}</small></span><span class="amt">${usd(m.amount)}</span></div>`).join("");
  return `<div class="kpis" style="margin-bottom:12px"><div class="kpi"><b>${usd(a.total)}</b><span>Investments total</span></div>
      <div class="kpi"><b>${a.accounts.length}</b><span>Accounts</span></div>
      <div class="kpi"><b>${usd(a.moved_in_year)}</b><span>Put in this year</span></div></div>` +
    card("Investment accounts", rows || `<p class="muted">None of your connected accounts is a brokerage or retirement account.</p>`, esc(a.note)) +
    card("Money moved into investments", moved || `<p class="muted">No transfers to investments in the last year.</p>`,
      "From your bank transactions (Plaid): transfers to brokerage and retirement accounts.");
}

function watchTab() {
  const rows = grouped(data.watchlist, (w) => w.market, (w) => `<div class="row" style="cursor:default"><span class="name" style="white-space:normal"><b>${esc(w.symbol)}</b>
      <small class="muted"> ${esc(MARKET[w.market] || w.market)}${w.note ? " · " + esc(w.note) : ""}</small></span>
      <span class="amt">${w.price != null ? px(w.price, w.market) : `<small class="muted">no price</small>`}</span>
      <button class="btn small" data-check="${esc(w.symbol)}">Check</button>
      <button class="btn small" data-unwatch="${esc(w.symbol)}" aria-label="Remove ${esc(w.symbol)}">✕</button></div>`, "");
  return card("Watchlist", `<div class="add"><input id="newsym" placeholder="Add a symbol: NVDA, ETH, TCS…" maxlength="20" aria-label="Symbol to watch">
      <button class="btn solid" id="watchbtn">Add</button></div>` + (rows || `<p class="muted">Nothing on your watchlist yet.</p>`),
    "Every symbol here is in the daily scan, on top of the usual list for each market.");
}

function render() {
  tabs();
  $("main").innerHTML = { strategy, trends: trendsTab, accounts: accountsTab, watch: watchTab }[tab]();
  document.querySelectorAll("[data-sold]").forEach((b) => b.onclick = () => sold(b.dataset.sold));
  document.querySelectorAll("[data-unwatch]").forEach((b) => b.onclick = () => watch("remove", b.dataset.unwatch));
  document.querySelectorAll("[data-check]").forEach((b) => b.onclick = () => { tab = "strategy"; render(); $("sym").value = b.dataset.check; analyze(); });
  document.querySelectorAll("[data-refresh]").forEach((b) => b.onclick = () => refresh(b.dataset.refresh, b));
  if ($("analyze")) { $("analyze").onclick = analyze; $("sym").onkeydown = (e) => { if (e.key === "Enter") analyze(); }; }
  if ($("watchbtn")) { $("watchbtn").onclick = () => watch("add", $("newsym").value); $("newsym").onkeydown = (e) => { if (e.key === "Enter") watch("add", $("newsym").value); }; }
}

async function analyze() {
  const symbol = $("sym").value.trim();
  if (busy || !symbol) return;
  busy = true; $("result").innerHTML = `<p class="muted">Looking at ${esc(symbol.toUpperCase())}…</p>`;
  try { $("result").innerHTML = analysis(await api("/api/invest/analyze", { symbol })); }
  catch (e) { $("result").innerHTML = `<p class="err">${esc(e.message)}</p>`; }
  busy = false;
}

async function watch(action, symbol) {
  if (busy || !String(symbol || "").trim()) return;
  busy = true;
  try { const out = await api("/api/invest/watch", { action, symbol }); data.watchlist = out.watchlist; render(); notice(out.text); }
  catch (e) { notice(e.message); }
  busy = false;
}

async function sold(symbol) {
  if (busy) return;
  busy = true;
  try { notice((await api("/api/invest/sold", { symbol })).text); data = await api("/api/invest"); render(); }
  catch (e) { notice(e.message); }
  busy = false;
}

async function refresh(topic, button) {
  if (busy) return;
  busy = true; button.textContent = "Looking…"; button.disabled = true;
  try { await api("/api/news/refresh", { topic }); data = await api("/api/invest"); render(); notice(`${topic} is up to date.`); }
  catch (e) { notice(e.message); button.textContent = "↻ Refresh"; button.disabled = false; }
  busy = false;
}

load();
