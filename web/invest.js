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
const TABS = [["strategy", "Strategy"], ["signals", "Signals"], ["practice", "Practice"], ["trends", "Trends"], ["accounts", "Accounts"], ["watch", "Watchlist"]];
let practiceData = null, practiceBusy = false, signalsData = null, signalsBusy = false;
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

// --- Practice: Buddy's paper trades (what was bought and sold, and why), the day across markets, crypto links
const pct = (v, d = 1) => v == null ? "–" : `<span class="${v > 0 ? "gain" : v < 0 ? "loss" : ""}">${v > 0 ? "+" : ""}${(v * 100).toFixed(d)}%</span>`;
const money = (v) => v == null ? "–" : `<span class="${v > 0 ? "gain" : v < 0 ? "loss" : ""}">${v < 0 ? "−" : v > 0 ? "+" : ""}$${Math.abs(v).toLocaleString(undefined, { maximumFractionDigits: 0 })}</span>`;
const why = (list) => `<ul class="why">${(list || []).map((r) => `<li>${esc(r)}</li>`).join("")}</ul>`;
const kpi = (value, label) => `<div class="kpi"><b>${value}</b><span>${esc(label)}</span></div>`;

async function loadPractice(fresh) {
  if (practiceBusy) return;
  practiceBusy = true;
  try { practiceData = await api("/api/invest/practice" + (fresh ? "?fresh=1" : "")); }
  catch (e) { practiceData = { failed: e.message }; }
  practiceBusy = false;
  if (tab === "practice") render();
}

function tradeCard(t) {
  const buy = t.kind === "buy";
  const head = `<div class="top"><b>${esc(t.symbol)}</b><span class="pill">${buy ? "bought" : "avoid"} ${esc(t.opened)}</span>
      <span class="muted">${esc(t.category)}${t.checklists ? " · checklists: " + esc(t.checklists) : ""}</span></div>`;
  let cells, tail = "";
  if (t.closed) {
    cells = [[buy ? "Bought at" : "Price then", px(t.entry)], [buy ? "Sold at" : "Price after", px(t.exit)], ["Result", pct(t.change)],
             [buy ? "Paper profit" : "Call was", buy ? money(t.profit) : (t.won ? "right" : "wrong")]];
    tail = `<p class="muted"><b>${buy ? "Sold" : "Judged"} ${esc(t.closed)}</b> after ${esc(t.days_held)} days: ${esc(t.why_closed)}.
      ${t.r != null ? ` That is ${t.r > 0 ? "+" : ""}${esc(t.r)} times the amount risked.` : ""}
      ${t.spy_change != null ? ` The S&amp;P 500 moved ${pct(t.spy_change)} over the same days.` : ""}</p>`;
  } else if (t.why_skipped) {
    cells = [["Price", px(t.entry)], ["Stop would be", px(t.stop)], ["Target would be", px(t.target)], ["Traded", "no"]];
    tail = `<p class="muted"><b>Skipped:</b> ${esc(t.why_skipped)}.</p>`;
  } else {
    cells = [[buy ? "Bought at" : "Price then", px(t.entry)], ["Now", t.price != null ? px(t.price) : "–"], ["So far", pct(t.change)],
             buy ? ["Paper profit", money(t.profit)] : ["Wrong above", px(t.stop)]];
    tail = `<p class="muted">${buy ? `${esc(Number(t.shares).toFixed(2))} shares, ${usd(t.cost)} of the book, risking ${usd(t.risk)}. Sells at ${px(t.stop)} (stop) or ${px(t.target)} (target), or after 28 days.`
      : `Right if it falls to ${px(t.target)}, wrong if it rises to ${px(t.stop)}.`} Held ${esc(t.days_held)} days.</p>`;
  }
  return `<div class="idea">${head}<div class="levels">${cells.map(([k, v]) => `<div>${esc(k)}<b>${v}</b></div>`).join("")}</div>
    <p class="muted" style="margin:8px 0 2px"><b>Why ${t.why_skipped ? "it was signalled" : buy ? "it was bought" : "Buddy said avoid"}</b></p>${why(t.because)}${tail}</div>`;
}

function learned(p) {
  const NAMES = { tone: "What people say", trend: "Price trend", insiders: "Insiders", analysts: "Analysts" };
  const w = p.weights || {};
  const rows = Object.keys(NAMES).map((k) => {
    const x = w[k] || {};
    return `<div class="row" style="cursor:default"><span class="name">${NAMES[k]}</span>
      <span class="muted">${x.trades ? `${esc(x.won ?? 0)} of ${esc(x.trades)} won` : "no trades yet"}${x.note ? " · " + esc(x.note) : ""}</span>
      <span class="amt">×${esc(Number(x.weight ?? 1).toFixed(2))}</span></div>`;
  }).join("");
  const m = p.model;
  const model = !m ? "" : `<p class="muted" style="margin-top:10px"><b>Trained model:</b> ${m.model_in_use ? "in use" : "not in use yet"}.
    ${esc(m.rows)} of ${esc(m.needed)} judged signals collected${m.last_run ? `; last training ${esc(m.last_run.on)}: ${esc(m.last_run.status)}` : ""}.</p>`;
  return card("What the trades have taught", rows + model,
    "Each reason's weight in the signal score: above 1 means it has kept being right, below 1 that it hasn't. Nothing moves before 15 closed trades.");
}

function marketsCard(m) {
  if (!m || m.error) return card("Markets today", `<p class="muted">${esc((m && m.error) || "No market data yet.")}</p>`);
  const groups = (m.groups || []).map((g) => `<h3 class="group">${esc(g.title)}</h3>
    <div class="mk head"><span></span><span>Day</span><span>Week</span><span>Month</span></div>` +
    g.rows.map((r) => `<div class="mk"><span><b>${esc(r.symbol)}</b> <small class="muted">${esc(r.name)}</small></span>
      <span>${pct(r.day)}</span><span>${pct(r.week)}</span><span>${pct(r.month)}</span></div>`).join("")).join("");
  const problems = (m.problems || []).map((x) => `<p class="err" style="padding:4px 0">${esc(x)}.</p>`).join("");
  return card("Markets today", (m.today ? `<p>${esc(m.today)}</p>` : "") + problems + (groups || `<p class="muted">No prices could be read.</p>`),
    m.as_of ? `Daily prices as of ${esc(m.as_of)}. Commodities and bonds are shown through the funds that track them.` : "");
}

function linksCard(m) {
  if (!m || !(m.links || []).length) return "";
  const bar = (r) => r == null ? `<span class="muted">–</span>` :
    `<span class="corr"><i style="${r >= 0 ? "left:50%" : `left:${50 + r * 50}%`};width:${Math.abs(r) * 50}%;background:var(${r >= 0 ? "--good" : "--bad"})"></i></span><span class="amt">${r > 0 ? "+" : ""}${r.toFixed(2)}</span>`;
  const coins = [...new Set(m.links.map((l) => l.coin))];
  const body = coins.map((c) => `<h3 class="group">${esc(c[0].toUpperCase() + c.slice(1))} and…</h3>` +
    m.links.filter((l) => l.coin === c).map((l) => `<div class="lk"><div class="lkrow"><span class="name">${esc(l.with)}</span>${bar(l.same_day_30)}</div>
      <p class="muted">${esc(l.reading)}</p></div>`).join("")).join("");
  return card("How crypto and the markets move together", body, m.note);
}

function practiceTab() {
  if (!practiceData) { loadPractice(); return `<p class="muted">Loading the practice book…</p>`; }
  if (practiceData.failed) return `<p class="err">${esc(practiceData.failed)}</p><button class="btn" id="practice-refresh">Try again</button>`;
  const p = practiceData.practice || {}, m = practiceData.markets;
  if (p.error) return card("Practice book", `<p class="muted">${esc(p.error)}</p>`) + marketsCard(m) + linksCard(m);
  const mo = p.money || {}, o = p.overall;
  const summary = `<div class="kpis">${kpi(usd(mo.value_now), "book value now")}${kpi(pct((mo.value_now || 0) / (mo.started_with || 1) - 1, 2), "since $" + Number(mo.started_with || 0).toLocaleString())}
    ${kpi(o ? `${esc(o.won)} of ${esc(o.trades)}` : "–", "calls right")}${kpi(pct(mo.worst_drawdown_pct), "worst fall from a peak")}
    ${kpi(usd(mo.cash), "cash not in positions")}${kpi(usd(mo.open_risk), "at risk in open trades")}</div>
    <p class="note">${esc(p.note || "")} ${mo.skipped_for_risk ? esc(mo.skipped_for_risk) + " signals were skipped by the risk rules. " : ""}${esc(mo.rules || "")}.</p>`;
  const log = (p.log || []).map((d) => `<h3 class="group">${esc(d.day)}</h3>` + d.events.map((e) =>
    `<div class="tx"><span><b>${esc(e.what)}</b></span><span>${esc(e.symbol)}<small>${esc(e.why)}</small></span><span class="amt">${px(e.price)}</span></div>`).join("")).join("");
  const list = (rows, empty) => rows && rows.length ? rows.map(tradeCard).join("") : `<p class="muted">${empty}</p>`;
  return card("Buddy's practice book", summary) +
    card(`Open now (${(p.open || []).length})`, list(p.open, "No paper positions are open. They open when Buddy makes a buy or sell signal.")) +
    card(`Closed trades (${(p.closed || []).length})`, list(p.closed, "Nothing has closed yet. A trade closes at its stop, its target, a sell signal, or after 28 days.")) +
    ((p.skipped || []).length ? card("Signals not traded", list(p.skipped, "")) : "") +
    card("Day by day", log || `<p class="muted">No activity yet.</p>`) +
    learned(p) + marketsCard(m) + linksCard(m) +
    `<p class="note">${esc(p.limits || "")}</p><p><button class="btn" id="practice-refresh">Refresh</button></p>`;
}

// --- Signals: buy and sell signals for stocks and funds, a switch per category, and Buddy's own record
async function loadSignals() {
  if (signalsBusy) return;
  signalsBusy = true;
  try { signalsData = await api("/api/deals/signals"); }
  catch (e) { signalsData = { failed: e.message }; }
  signalsBusy = false;
  if (tab === "signals") render();
}
function signalsTab() {
  if (!signalsData) { loadSignals(); return `<p class="muted">Loading signals…</p>`; }
  const s = signalsData;
  if (s.failed) return `<p class="err">${esc(s.failed)}</p>`;
  const sw = (c) => `<button class="chip${s.categories[c] ? " on" : ""}" data-sigcat="${esc(c)}" aria-pressed="${!!s.categories[c]}">${esc(c)}</button>`;
  const mark = { right: "✅ right", wrong: "❌ wrong", flat: "➖ flat", open: "⏳ open" };
  const rec = (c) => { const r = s.record[c]; return `<span class="pill">${esc(c)}: ${r.scored ? `${r.right} right of ${r.scored}` : "none scored yet"}${r.proven ? "" : " · unproven"}</span>`; };
  const m = s.models ? Object.entries(s.models).find(([name]) => /invest/i.test(name)) : null;
  const list = s.signals.length ? s.signals.map((g) => `<div class="idea"><div class="top">
      <b>${g.action === "buy" ? "📈" : g.action === "sell" ? "📉" : "⚠️"} ${esc(g.key)}</b>
      <span class="pill${g.action === "buy" ? " hot" : ""}">${esc(g.action === "sell" ? "sell or avoid" : g.action)}</span>
      <span class="muted">${esc(g.category)} · ${esc(g.day)} · ${px(g.price_then)}${g.price_after ? ` → ${px(g.price_after)}` : ""}</span>
      <span class="pill">${esc(mark[g.status] || g.status)}</span></div>
      ${why(g.reasons)}</div>`).join("")
    : `<p class="muted">No signals yet. Buddy makes them on weekday mornings when a stock or fund gets unusual attention and its price trend and the tone agree.</p>`;
  return card("Signals on for", `<p class="muted">Tap a category to turn its buy and sell signals on or off.</p><div class="chips" style="flex-wrap:wrap">${s.investing.map(sw).join("")}</div>`)
    + card("Buddy's record", `<div style="display:flex;flex-wrap:wrap;gap:6px">${s.investing.map(rec).join("")}</div>`
        + (m ? `<p class="muted" style="margin-top:10px"><b>Trained model:</b> ${m[1].model_in_use ? "in use" : "not in use yet"}; ${esc(m[1].rows)} of ${esc(m[1].needed)} judged signals collected.</p>` : ""),
        "A signal is scored a week later; a category is unproven until 20 are scored. How the signals did as paper trades is under Practice.")
    + card("Recent signals", list, s.note)
    + (s.trending.length ? card("Most talked about last night", `<p class="muted">${s.trending.map((t) => `${esc(t.key)} (${esc(t.mentions)}${t.times_usual ? `, ${esc(t.times_usual)}× usual` : ""})`).join(" · ")}</p>`, "Attention, not advice.") : "");
}
async function switchSignal(category) {
  try { const got = await api("/api/deals/signals/switch", { category, on: !signalsData.categories[category] });
    signalsData = { ...signalsData, categories: got.categories }; render(); }
  catch (e) { notice(e.message); }
}

function render() {
  tabs();
  $("main").innerHTML = { strategy, signals: signalsTab, practice: practiceTab, trends: trendsTab, accounts: accountsTab, watch: watchTab }[tab]();
  document.querySelectorAll("[data-sigcat]").forEach((b) => b.onclick = () => switchSignal(b.dataset.sigcat));
  if ($("practice-refresh")) $("practice-refresh").onclick = () => { practiceData = null; render(); loadPractice(true); };
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
