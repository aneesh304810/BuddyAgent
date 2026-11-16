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
// Four tabs: the old Strategy, Signals and Accounts are one Overview; market news is in Discover, under News.
const TABS = [["strategy", "Overview"], ["practice", "Trading"], ["themes", "Themes"], ["watch", "Watchlist"]];
let setupData = null, setupBusy = false;
let picksData = null, picksBusy = false, researchData = null, researchBusy = false, ideasData = null, ideasBusy = false;
let themesData = null, themesBusy = false, practiceData = null, practiceBusy = false, signalsData = null, signalsBusy = false;
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

// Buddy's recommended watchlist: picked each night from the Intelligence evidence, grouped by sector and
// technology; quiet ones move to the archive and come back if their evidence picks up.
async function loadPicks(body) {
  if (picksBusy) return;
  picksBusy = true;
  try { picksData = await api("/api/invest/picks", body); }
  catch (e) { picksData = { ...(picksData || {}), failed: e.message }; }
  picksBusy = false;
  if (tab === "watch") render();
}
function picksGroups(groups, archived) {
  return (groups || []).map((g) => `<details${archived ? "" : " open"}><summary><b>${esc(g.sector)}</b> <small class="muted">${esc(g.count)}</small></summary>` +
    g.industries.map((i) => `<h3 class="group" style="margin:8px 0 2px">${esc(i.industry)}</h3>` + i.picks.map((k) =>
      `<div class="row" style="cursor:default;align-items:flex-start"><span class="name" style="white-space:normal"><b>${esc(k.symbol)}</b> <small class="muted">${esc(k.name)}${k.yours ? " · on your list" : ""}</small>
        <small class="muted" style="display:block">${archived ? `Recommended ${esc(k.first_seen)} to ${esc(k.last_active)} · archived ${esc(k.archived_on)}` : `${esc(k.why)} · since ${esc(k.first_seen)}`}</small></span>
        <button class="btn small" data-check="${esc(k.symbol)}">Check</button>
        ${archived ? `<button class="btn small" data-pick="restore" data-sym="${esc(k.symbol)}">Restore</button>`
          : `${k.yours ? "" : `<button class="btn small" data-keep="${esc(k.symbol)}" aria-label="Add ${esc(k.symbol)} to your watchlist">Add</button>`}
             <button class="btn small" data-pick="dismiss" data-sym="${esc(k.symbol)}" aria-label="Stop recommending ${esc(k.symbol)}">✕</button>`}</div>`).join("")).join("") + `</details>`).join("");
}
function picksCards() {
  if (!picksData) { loadPicks(); return card("Recommended by Buddy", `<p class="muted">Loading…</p>`); }
  const d = picksData;
  const failed = d.failed ? `<p class="err">${esc(d.failed)}</p>` : "";
  if (!d.recommended) return card("Recommended by Buddy", failed + `<button class="btn" id="picks-refresh">Try again</button>`);
  return card(`Recommended by Buddy (${esc(d.recommended_count)})`, failed + (picksGroups(d.recommended, false) ||
      `<p class="muted">Nothing recommended yet. Buddy adds companies here each night as its market intelligence finds new filings and evidence patterns.</p>`) +
      `<p><button class="btn" id="picks-refresh">Refresh from the evidence now</button></p>`, d.rules + " These are scanned daily with your own symbols.") +
    (d.archive_count ? card(`Archive (${esc(d.archive_count)})`, picksGroups(d.archive, true), "Nothing new for 30 days. Kept here by sector and technology; not scanned.") : "") +
    ((d.dismissed || []).length ? card("Dismissed", d.dismissed.map((x) => `<button class="chip" data-pick="restore" data-sym="${esc(x)}" title="Recommend ${esc(x)} again">${esc(x)} ↺</button>`).join(" "),
      "Tap one to let Buddy recommend it again.") : "");
}

function watchTab() {
  const rows = grouped(data.watchlist, (w) => w.market, (w) => `<div class="row" style="cursor:default"><span class="name" style="white-space:normal"><b>${esc(w.symbol)}</b>
      <small class="muted"> ${esc(MARKET[w.market] || w.market)}${w.note ? " · " + esc(w.note) : ""}</small></span>
      <span class="amt">${w.price != null ? px(w.price, w.market) : `<small class="muted">no price</small>`}</span>
      <button class="btn small" data-check="${esc(w.symbol)}">Check</button>
      <button class="btn small" data-unwatch="${esc(w.symbol)}" aria-label="Remove ${esc(w.symbol)}">✕</button></div>`, "");
  return card("Watchlist", `<div class="add"><input id="newsym" placeholder="Add a symbol: NVDA, ETH, TCS…" maxlength="20" aria-label="Symbol to watch">
      <button class="btn solid" id="watchbtn">Add</button></div>` + (rows || `<p class="muted">Nothing on your watchlist yet.</p>`),
    "Every symbol here is in the daily scan, on top of the usual list for each market.") + picksCards();
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
    <p class="muted" style="margin:8px 0 2px"><b>Why ${t.why_skipped ? "it was signalled" : buy ? "it was bought" : "Buddy said avoid"}</b>${t.strategy ? ` <span class="pill">${esc(t.strategy)} strategy</span>` : ""}</p>${why(t.because)}${usedRows(t.used)}${tail}</div>`;
}

// What a trade was decided on, one line per source, with the way it pointed (a sign and a word, not colour alone).
const WAY = { up: ["▲ pointed up", "gain"], down: ["▼ pointed down", "loss"], none: ["● no lean", "muted"] };
function usedRows(used) {
  if (!used || !used.length) return "";
  return `<p class="muted" style="margin:8px 0 2px"><b>What it used</b></p>` + used.map((u) =>
    `<div class="tx" style="grid-template-columns:104px 1fr auto"><span><b>${esc(u.source)}</b></span><span>${esc(u.says)}</span>
      <span class="${(WAY[u.way] || WAY.none)[1]}">${(WAY[u.way] || WAY.none)[0]}</span></div>`).join("");
}

// The strategies the paper books follow, what the trades have changed, and the patterns still only being measured.
function strategiesCards(p) {
  const s = p.strategies;
  if (!s) return "";
  const one = (x) => `<div class="idea"><div class="top"><b>${esc(x.name)}</b><span class="pill">${esc(x.trades)} ${x.key === "themes" ? "baskets" : "closed trades"}</span></div>
    <p style="margin:6px 0">${esc(x.rule)}</p>
    <p class="muted" style="margin:0 0 6px"><b>Reads:</b> ${x.uses.map(esc).join(" · ")}</p>
    <p class="muted" style="margin:0 0 6px"><b>Record:</b> ${esc(String(x.record).replace(/\.$/, ""))}${x.key === "themes" && x.finished ? `. Paper profit ${money(x.profit)} against ${money(x.market_profit)} for the market${x.from_history ? `; ${esc(x.from_history)} baskets are replayed from history` : ""}` : ""}.</p>
    ${(x.inputs || []).map((i) => `<div class="row" style="cursor:default"><span class="name fixed" style="flex:1">${esc(i.name)}</span>
      <span class="muted">${i.trades ? `${esc(i.won)} of ${esc(i.trades)} won` : "no trades yet"}</span><span class="amt">×${esc(Number(i.weight).toFixed(2))}</span></div>`).join("")}</div>`;
  const testing = (s.testing || []).map((t) => `<div class="row" style="cursor:default" title="${esc(t.rule)}"><span class="name fixed">${esc(t.name)}</span>
      <span class="muted" style="flex:1">${esc(t.says)}</span><span class="amt">${esc(t.seen)} seen</span></div>`).join("");
  return card("Strategies in use", s.following.map(one).join("")) +
    card("What it is learning", `<ul style="margin:0;padding-left:18px">${(s.learning || []).map((l) => `<li>${esc(l)}</li>`).join("")}</ul>`) +
    (testing ? card("New strategies being tested", testing + `<p class="note">${esc(s.note)}</p>`) : "");
}

// The research desk: papers and code Buddy may fetch (arXiv, licensed GitHub files), the factor yardsticks, and
// dated stock lists. Nothing is read by a model until you pick it.
async function loadResearch(body) {
  if (researchBusy) return;
  researchBusy = true;
  try {
    const out = await api("/api/invest/research", body);
    researchData = { research: out.research, benchmarks: out.benchmarks };
    if (out.lab && practiceData && practiceData.practice) practiceData.practice.lab = out.lab;
    if (out.text) notice(out.text);
  } catch (e) { researchData = { ...(researchData || {}), failed: e.message }; notice(e.message); }
  researchBusy = false;
  if (tab === "practice") render();
}
function researchCards() {
  if (!researchData) { loadResearch(); return card("Research queue", `<p class="muted">Loading…</p>`); }
  const r = researchData.research, b = researchData.benchmarks;
  if (!r || !b) return card("Research queue", `<p class="err">${esc(researchData.failed || "Could not load.")}</p><button class="btn" id="research-reload">Try again</button>`);
  const item = (x) => `<div class="tx" style="display:block"><b>${x.url ? `<a href="${safe(x.url)}" target="_blank" rel="noopener">${esc(x.title)}</a>` : esc(x.title)}</b>
      <small class="muted">${esc(x.source)} · ${esc(x.day)}${x.licence ? " · " + esc(x.licence) : ""}</small>
      <p class="muted" style="margin:4px 0">${esc((x.summary || "").slice(0, 260))}${(x.summary || "").length > 260 ? "…" : ""}</p>
      ${x.status === "new" ? `<button class="btn small" data-research="read" data-id="${esc(x.id)}">Read into a rule</button> <button class="btn small" data-research="dismiss" data-id="${esc(x.id)}">Dismiss</button>`
        : `<small class="muted">${x.rule_key ? "Read: see the proposed rule above" : "Read: " + esc(x.note || "no testable rule found")}</small>`}</div>`;
  const queue = card(`Research queue (${(r.new || []).length})`,
    ((r.new || []).map(item).join("") || `<p class="muted">Nothing waiting. Buddy searches arXiv's quantitative finance papers every Sunday.</p>`) +
    ((r.read || []).length ? `<details><summary>Already read (${r.read.length})</summary>${r.read.map(item).join("")}</details>` : "") +
    `<p><button class="btn" id="research-search">Search arXiv now</button></p>
     <div class="add"><input id="research-url" placeholder="https://github.com/owner/name/tree/main/folder" aria-label="GitHub link"><button class="btn" id="research-github">Add from GitHub</button></div>
     ${(r.starting_points || []).length ? `<p class="muted" style="margin:8px 0 4px">Starting points (public, with a stated licence):</p>` + r.starting_points.map((x) => `<button class="chip" data-start="${esc(x.url)}" style="margin:2px">${esc(x.title)}</button>`).join("") : ""}`,
    r.note + (r.last_search ? " Last search: " + r.last_search + "." : ""));
  const f = b.factors || {};
  const frows = (f.rows || []).map((x) => `<div class="row" style="cursor:default"><span class="name fixed" style="flex:1"><b>${esc(x.name)}</b> <small class="muted">${esc(x.what)}</small></span>
      <span class="amt">${pct(x.last_12_months)} <small class="muted">last 12 months</small><br>${pct(x.average_year)} <small class="muted">a year, since ${esc(f.from || "")}</small></span></div>`).join("");
  const yard = card("Yardsticks: what plain styles returned", (frows || `<p class="muted">Not fetched yet.</p>`) +
    `<p><button class="btn" id="research-factors">Fetch the factor returns</button> <button class="btn" id="research-company">Fetch company figures now</button></p>`,
    (f.note || "") + " Company figures (earnings surprise, margins, debt) are fetched weekly for the lab's companies; fetching now takes a few minutes.");
  const lrows = (b.lists || []).map((l) => `<div class="row" style="cursor:default"><span class="name" style="white-space:normal"><b>${esc(l.title || l.name)}</b>
      <small class="muted">${esc(l.symbols)} symbols · ${esc(l.day)} · ${esc(l.versions)} dated version${l.versions === 1 ? "" : "s"}${l.in_use ? " · used by the lab" : ""}</small></span>
      <button class="btn small" data-uselist="${l.in_use ? "" : esc(l.name)}">${l.in_use ? "Stop using" : "Use in the lab"}</button></div>`).join("");
  const presets = (b.presets || []).map((x) => `<button class="btn small" data-fetchlist="${esc(x.name)}"${b.holdings_on ? "" : " disabled"}>Fetch ${esc(x.title)}</button>`).join(" ");
  const lists = card("Stock lists", (lrows || `<p class="muted">No lists yet. The lab uses its built-in list of about a hundred large US stocks and funds.</p>`) +
    `<p class="muted">Upload a holdings or index file (CSV or Excel with a Ticker column):</p>
     <div class="add"><input id="list-name" placeholder="List name, e.g. nasdaq100" maxlength="30" aria-label="List name"><input type="file" id="list-file" accept=".csv,.xlsx,.txt" aria-label="Holdings file"><button class="btn" id="list-upload">Upload</button></div>
     <p><label><input type="checkbox" id="holdings-on"${b.holdings_on ? " checked" : ""}> Let Buddy fetch fund holdings files weekly</label></p><p>${presets}</p>`,
    "Each list is kept with its date, so past membership builds up from today. Fetching fund files is off until you switch it on, because the provider's terms were not checked for you; uploading a file you downloaded yourself always works.");
  return queue + yard + lists;
}

// The data behind the trading engine: what each source holds now, how its last load went, and one button to load all.
async function loadSetup(run) {
  if (setupBusy) return;
  setupBusy = true;
  try { const out = await api("/api/invest/lab/setup", run ? {} : undefined); setupData = out.setup; if (out.text) notice(out.text); }
  catch (e) { setupData = { rows: [], failed: e.message }; }
  setupBusy = false;
  if (tab === "practice") render();
}
function setupCard() {
  if (!setupData) { loadSetup(); return card("Data for the trading engine", `<p class="muted">Loading…</p>`); }
  const MARK = { loaded: ["✓ loaded", "gain"], failed: ["✕ failed", "loss"], skipped: ["– skipped", "muted"], empty: ["○ empty", "muted"] };
  const rows = (setupData.rows || []).map((r) => `<div class="row" style="cursor:default;align-items:flex-start"><span class="name" style="white-space:normal"><b>${esc(r.title)}</b>
      <small class="muted" style="display:block">${esc(r.holds)}${r.last ? ` · last load${r.at ? " " + esc(r.at) : ""}: ${esc(r.last)}` : ""}</small></span>
      <span class="${(MARK[r.state] || MARK.empty)[1]}" style="white-space:nowrap">${(MARK[r.state] || MARK.empty)[0]}</span></div>`).join("");
  return card("Data for the trading engine", (setupData.failed ? `<p class="err">${esc(setupData.failed)}</p>` : "") + rows +
    (setupData.running ? `<p class="note">Loading runs one source at a time, top to bottom, and takes several minutes in all. Lines still marked empty have not been reached yet. Press Refresh to follow it.</p>` : "") +
    (setupData.cut_off ? `<p class="note">The last load was cut off before it finished (a restart does that). Press Load everything now to run it again.</p>` : "") +
    (setupData.sandbox_note ? `<p class="note">${esc(setupData.sandbox_note)}</p>` : "") +
    `<p><button class="btn solid" id="setup-run"${setupData.running ? " disabled" : ""}>${setupData.running ? "Loading: " + esc(setupData.running) + "…" : "Load everything now"}</button> <button class="btn" id="setup-refresh">Refresh</button></p>`,
    "Each line shows what is actually held. Everything here also refreshes on its own schedule; this button is for the first time, or when something is empty.");
}

// One paper trade made by a lab rule: the symbol, the rule, what happened, and why it was picked.
function labRow(t) {
  return `<div class="tx" style="grid-template-columns:92px 1fr auto"><span><b>${esc(t.symbol)}</b><small>${esc(t.strategy)}</small></span>
      <span>${t.closed ? `Bought ${esc(t.opened)} at ${px(t.entry)}, sold ${esc(t.closed)} at ${px(t.exit)}: ${esc(t.why_closed)}` : `Bought ${esc(t.opened)} at ${px(t.entry)}; stop ${px(t.stop)}, target ${px(t.target)}`}
        <small>${esc(t.why)}</small></span>
      <span class="amt">${t.closed ? pct(t.change) : `<small class="muted">open</small>`}</span></div>`;
}

// The strategy lab: well-known rules that each paper-trade every weekday, with their record against the market.
function labCards(p) {
  const lab = p.lab;
  if (!lab) return "";
  const vs = (r) => !r || !r.closed ? `<small class="muted">none</small>` : `${esc(r.won)} of ${esc(r.closed)} made money<br>${pct(r.average_result)} a trade
      <small class="muted">market ${r.average_vs_market == null ? "–" : pct(r.average_result - r.average_vs_market)}</small>`;
  const part = (label, r) => r && r.closed ? `<br><small class="muted">${label}: ${pct(r.average_vs_market)} against the market (${esc(r.closed)})</small>` : "";
  const splits = (h) => !h || !h.verdict ? "" : `<br><small><b>${esc(h.verdict)}</b></small>${part("rising market", h.rising_market)}${part("falling market", h.falling_market)}${part("earlier 70%", h.earlier)}${part("latest 30%", h.latest)}
      <details><summary><small class="muted">year by year</small></summary>${(h.by_year || []).map((y) => `<small class="muted">${esc(y.year)}: ${pct(y.average_vs_market)} against the market (${esc(y.closed)})</small>`).join("<br>")}</details>`;
  const rows = lab.strategies.map((s) => `<tr><td><b>${esc(s.name)}</b>${s.yours ? ` <span class="pill">yours</span>` : ""}<br><small class="muted">${esc(s.rule)} Reads: ${esc(s.reads)}; held up to ${esc(s.hold_days)} days.</small></td>
      <td>${esc(s.open)} open<br>${vs(s)}<br><small class="muted">${esc(s.says)}</small></td><td>${vs(s.history)}${splits(s.history)}</td></tr>`).join("");
  const rp = lab.replay || {};
  const replayLine = rp.state === "running" ? "A replay over past prices is running; refresh in a minute or two."
    : rp.state === "failed" ? "The last replay failed: " + (rp.why || "")
    : rp.state === "done" ? `History covers ${rp.from} to ${rp.to}: ${Number(rp.trades).toLocaleString()} finished trades, replayed ${rp.at}.`
    : "History has not been replayed yet.";
  const added = (lab.added || []).map((r) => `<div class="idea"><div class="top"><b>${esc(r.name)}</b><span class="pill">${esc(r.status)}</span>
      <span class="muted">${r.source_url ? `<a href="${safe(r.source_url)}" target="_blank" rel="noopener">${esc(r.source || "source")}</a>` : esc(r.source || "pasted text")}${r.licence ? " · " + esc(r.licence) : ""}</span></div>
      <p style="margin:6px 0">${esc(r.summary)}</p><p class="muted" style="margin:0 0 6px"><b>The rule Buddy will test:</b> ${esc(r.in_words)}</p>
      ${r.needs.length ? `<p class="muted" style="margin:0 0 6px"><b>Left out, because Buddy cannot measure it:</b> ${r.needs.map(esc).join("; ")}</p>` : ""}
      ${r.captures ? `<p class="muted" style="margin:0 0 6px"><b>How much of the strategy this rule holds:</b> ${esc(r.captures)}. Read the rule against the document before you approve.</p>` : ""}
      ${(r.live_only || []).length ? `<p class="muted" style="margin:0 0 6px"><b>No history for:</b> ${r.live_only.map(esc).join(", ")}. Buddy has only today's values of these, so this rule can be paper-traded from now but not replayed over past prices.</p>` : ""}
      <p>${r.status !== "active" ? `<button class="btn solid" data-rule="approve" data-key="${esc(r.key)}">Approve and start testing</button> ` : `<button class="btn" data-rule="pause" data-key="${esc(r.key)}">Pause</button> `}
        <button class="btn" data-rule="remove" data-key="${esc(r.key)}">Remove</button></p></div>`).join("");
  const upload = `<p class="muted">Upload a document that describes a strategy (PDF, Word, web page or text), or paste the text. Buddy writes it as a rule and shows it here. Nothing trades until you approve it.</p>
      <p><input type="file" id="lab-file" accept=".pdf,.docx,.html,.htm,.txt,.md" aria-label="Strategy document"> <button class="btn solid" id="lab-upload">Upload and read</button></p>
      <textarea id="lab-text" rows="4" style="width:100%;box-sizing:border-box;background:var(--bg);color:inherit;border:1px solid var(--line);border-radius:10px;padding:8px" placeholder="Or paste the strategy's text here" aria-label="Strategy text"></textarea>
      <p><button class="btn" id="lab-paste">Read the pasted text</button> <span class="muted" id="lab-status"></span></p>
      <div class="add"><input id="lab-url" placeholder="Or a page's address: https://…" aria-label="Page address"><button class="btn" id="lab-link">Read this page</button></div>
      <p class="muted">Buddy fetches that one page, once, and keeps only the rule and the link. Pages that need a sign-in or load by script cannot be read this way: paste their text instead.</p>`;
  return card("Strategy lab: rules that trade every weekday",
      `<div style="overflow-x:auto"><table class="grid"><tr><th>Rule</th><th>Live paper trades</th><th>On past prices</th></tr>${rows}</table></div>
       <p class="note">${esc(replayLine)} ${esc(lab.history_note || "")}</p>
       <p><button class="btn" id="lab-run">Run the lab now</button> <button class="btn" id="lab-replay">Replay over past prices</button> <button class="btn" id="lab-refresh">Refresh</button></p>`, lab.note) +
    card("Add a strategy from a document", upload + added + ((lab.not_rules || []).length ? `<details><summary>Papers you gave that hold no rule of their own (${lab.not_rules.length})</summary>${lab.not_rules.map((n) =>
      `<p class="muted"><b><a href="${safe(n.source_url)}" target="_blank" rel="noopener">${esc(n.source)}</a></b><br>${esc(n.why)}</p>`).join("")}</details>` : "")) + researchCards() +
    "";
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
  if (!practiceData) { loadPractice(); return `<p class="muted">Loading the paper trades…</p>`; }
  if (practiceData.failed) return `<p class="err">${esc(practiceData.failed)}</p><button class="btn" id="practice-refresh">Try again</button>`;
  const p = practiceData.practice || {}, m = practiceData.markets;
  if (p.error) return card("Practice book", `<p class="muted">${esc(p.error)}</p>`) + marketsCard(m) + linksCard(m);
  const mo = p.money || {}, o = p.overall;
  const summary = `<div class="kpis">${kpi(usd(mo.value_now), "book value now")}${kpi(pct((mo.value_now || 0) / (mo.started_with || 1) - 1, 2), "since $" + Number(mo.started_with || 0).toLocaleString())}
    ${kpi(o ? `${esc(o.won)} of ${esc(o.trades)}` : "–", "calls right")}${kpi(pct(mo.worst_drawdown_pct), "worst fall from a peak")}
    ${kpi(usd(mo.cash), "cash not in positions")}${kpi(usd(mo.open_risk), "at risk in open trades")}</div>
    <p class="note">${esc(p.note || "")} ${mo.skipped_for_risk ? esc(mo.skipped_for_risk) + " signals were skipped by the risk rules. " : ""}${esc(mo.rules || "")}.</p>`;
  // Every paper trade in one place: the Signals book's trades and the strategy lab's, each named by its rule.
  const lab = p.lab || { open: [], closed: [], strategies: [] };
  const sum = (k) => (lab.strategies || []).reduce((a, x) => a + (x[k] || 0), 0);
  const labLine = lab.strategies.length ? `<div class="kpis">${kpi(esc((p.open || []).length + sum("open")), "paper trades open now")}
      ${kpi(esc((p.closed || []).length + sum("closed")), "closed so far")}${kpi(sum("closed") ? `${esc(sum("won"))} of ${esc(sum("closed"))}` : "–", "lab trades that made money")}
      ${kpi(money(sum("closed") ? sum("profit") : null), "lab result after costs")}</div>
      <p class="note">${esc(sum("open"))} of the open trades were opened by the strategy lab's rules at $1,000 each; they are counted here, apart from the $${Number(mo.started_with || 0).toLocaleString()} Signals book above.</p>` : "";
  const days = {};
  (p.log || []).forEach((d) => { days[d.day] = (days[d.day] || []).concat(d.events); });
  lab.open.concat(lab.closed).forEach((t) => {
    (days[t.opened] = days[t.opened] || []).push({ what: "Bought", symbol: t.symbol, price: t.entry, why: t.strategy + ": " + t.why });
    if (t.closed) (days[t.closed] = days[t.closed] || []).push({ what: "Sold", symbol: t.symbol, price: t.exit, why: `${t.strategy}: ${t.why_closed} (${(t.change * 100).toFixed(1)}%)` });
  });
  const log = Object.keys(days).sort().reverse().slice(0, 30).map((day) => `<h3 class="group">${esc(day)}</h3>` + days[day].map((e) =>
    `<div class="tx"><span><b>${esc(e.what)}</b></span><span>${esc(e.symbol)}<small>${esc(e.why)}</small></span><span class="amt">${px(e.price)}</span></div>`).join("")).join("");
  const both = (rows, labRows, empty) => (rows && rows.length) || labRows.length
    ? (rows || []).map(tradeCard).join("") + (labRows.length ? `<h3 class="group" style="margin:10px 0 4px">From the strategy lab's rules</h3>` + labRows.map(labRow).join("") : "")
    : `<p class="muted">${empty}</p>`;
  const list = (rows, empty) => rows && rows.length ? rows.map(tradeCard).join("") : `<p class="muted">${empty}</p>`;
  return card("Buddy's paper trading", summary + labLine) +
    card(`Bought and holding (${(p.open || []).length + lab.open.length})`, both(p.open, lab.open, "No paper positions are open. The lab opens trades each weekday morning, or press Run the lab now below.")) +
    card(`Sold and judged (${(p.closed || []).length + lab.closed.length})`, both(p.closed, lab.closed, "Nothing has closed yet. A trade closes at its stop, its target, a sell signal, or its time limit.")) +
    ((p.skipped || []).length ? card("Signals not traded", list(p.skipped, "")) : "") +
    card("Day by day", log || `<p class="muted">No activity yet.</p>`) +
    setupCard() + strategiesCards(p) + labCards(p) +
    (p.strategies ? "" : learned(p)) + marketsCard(m) + linksCard(m) +
    `<p class="note">${esc(p.limits || "")} Theme baskets are listed under Themes.</p><p><button class="btn" id="practice-refresh">Refresh</button></p>`;
}

// --- Themes: the Intelligence evidence joined to public companies, with practice baskets against the market
async function loadThemes() {
  if (themesBusy) return;
  themesBusy = true;
  try { themesData = await api("/api/invest/themes"); }
  catch (e) { themesData = { failed: e.message }; }
  themesBusy = false;
  if (tab === "themes") render();
}
const LEAN = { up: "▲ has beaten the market so far", down: "▼ has trailed the market so far", flat: "● no clear edge so far", none: "○ not enough history" };
const TONE = { good: ["▲ good", "gain"], bad: ["▼ bad", "loss"], neutral: ["● neutral", "muted"] };
const tone = (t) => TONE[t] ? `<span class="${TONE[t][1]}" title="Labelled by an AI from the headline; check it">${TONE[t][0]}</span>` : `<span class="muted">○ not labelled yet</span>`;
const headline = (x) => `<li>${tone(x.tone)} · ${x.url ? `<a href="${safe(x.url)}" target="_blank" rel="noopener">${esc(x.title)}</a>` : esc(x.title)} <small class="muted">${esc(x.day)}${x.outlet ? " · " + esc(x.outlet) : ""}</small></li>`;
const against = (a, b) => a == null ? "–" : `${pct(a)} <small class="muted">market ${pct(b)}</small>`;
function themeCard(t) {
  const companies = (t.companies || []).map((c) => `<button class="chip" data-check="${esc(c.symbol)}" title="${esc(c.name)}: ${esc(c.records)} filings">${esc(c.symbol)}${c.signal ? (c.signal.kind === "buy" ? " ▲" : " ▼") : ""}</button>`).join("");
  const signals = (t.companies || []).filter((c) => c.signal).map((c) => `<b>${esc(c.symbol)}</b>: ${c.signal.kind === "buy" ? "▲ open buy" : "▼ marked to avoid"} in the Signals practice book since ${esc(c.signal.since)}`).join("; ");
  const n = t.news_14_days || {}, counted = (n.good || 0) + (n.bad || 0) + (n.neutral || 0) + (n.unlabelled || 0);
  const newsLine = counted ? `<p class="note">News in 14 days: <span class="gain">▲ ${esc(n.good || 0)} good</span> · <span class="loss">▼ ${esc(n.bad || 0)} bad</span> · ● ${esc(n.neutral || 0)} neutral${n.unlabelled ? ` · ○ ${esc(n.unlabelled)} not labelled yet` : ""}</p>` : "";
  const techNews = (t.headlines || []).map(headline).join("");
  const coNews = (t.companies || []).filter((c) => (c.news || []).length).map((c) => `<li><b>${esc(c.symbol)}</b><ul class="why">${c.news.map(headline).join("")}</ul></li>`).join("");
  const mine = (t.yours || []).map((y) => `<b>${esc(y.symbol)}</b> (${esc(y.where)})`).join(", ");
  // The same card shape as the buy-now-or-wait calls in Deals: the call, the record, one line of why, the source.
  const CALL = { up: ["▲ Leans up", "go"], down: ["▼ Leans down", "stop"], flat: ["● No clear edge", "wait"], none: ["○ Too early to call", "wait"] };
  const active = (t.active || []).map((a) => `<div style="margin:8px 0"><span class="call ${(CALL[a.lean] || CALL.none)[1]}">${(CALL[a.lean] || CALL.none)[0]}</span>
      <span class="muted">${esc(a.record)}</span>
      <p class="muted" style="margin:6px 0 2px">${esc(a.title)} for ${esc(t.name)}.</p>
      <small class="muted">Buddy Intelligence evidence · fired in the last 30 days · measured against the market, not a forecast</small></div>`).join("");
  return `<div class="tx" style="display:block"><b>${esc(t.name)}</b> <small class="muted">${esc(t.sector_name)} · ${esc(t.stage)} · ${esc(t.signals_90_days)} records in 90 days${t.growth == null ? "" : ", " + (t.growth > 0 ? "+" : "") + Math.round(t.growth * 100) + "% on the 90 before"}</small>
    ${mine ? `<p class="note">On your lists: ${mine}</p>` : ""}
    ${companies ? `<div class="chips" style="flex-wrap:wrap;margin-top:6px">${companies}</div>` : `<p class="note">No public company named in its filings yet.</p>`}
    ${signals ? `<p class="note">${signals}</p>` : ""}
    ${active}
    ${newsLine}${techNews ? `<ul class="why">${techNews}</ul>` : ""}
    ${coNews ? `<p class="note">News about its companies</p><ul class="why">${coNews}</ul>` : ""}</div>`;
}
function themeEvent(e) {
  const proof = (e.evidence || []).slice(0, 3).map((x) => x.tone ? headline(x) : `<li>${x.url ? `<a href="${safe(x.url)}" target="_blank" rel="noopener">${esc(x.title)}</a>` : esc(x.title)} <small class="muted">${esc(x.day)}</small></li>`).join("");
  return `<div class="tx" style="display:block"><b>${esc(e.name)}</b>: ${esc(e.title.toLowerCase())} <small class="muted">${esc(e.day)} · ${esc(e.why)}</small>
    <p class="note">Basket: ${esc((e.symbols || []).join(", "))}. After 30 days ${against(e.after_30, e.market_30)}; after 90 days ${against(e.after_90, e.market_90)}${e.so_far == null ? "" : `; so far (${esc(e.days)} days) ${against(e.so_far, e.market_so_far)}`}</p>
    ${proof ? `<ul class="why">${proof}</ul>` : ""}</div>`;
}
function themesTab() {
  if (!themesData) { loadThemes(); return `<p class="muted">Loading themes…</p>`; }
  if (themesData.failed) return `<p class="err">${esc(themesData.failed)}</p><button class="btn" id="themes-refresh">Try again</button>`;
  const d = themesData, p = d.practice || {};
  const cell = (r) => r.finished ? `${esc(r.beat_market)} of ${esc(r.finished)} beat the market, middle ${pct(r.middle_against_market)}<br><small class="muted">${esc(LEAN[r.lean] || "")}</small>` : `<small class="muted">none finished</small>`;
  const rows = (d.patterns || []).map((r) => `<tr><td><b>${esc(r.title)}</b><br><small class="muted">${esc(r.rule)}</small></td><td>${esc(r.events)}</td><td>${cell(r.days_30)}</td><td>${cell(r.days_90)}</td></tr>`).join("");
  const score = `<div class="kpis">${kpi(esc(p.baskets || 0), "practice baskets")}${kpi(esc(p.open || 0), "still open")}${kpi(p.finished ? `${esc(p.beat_market)} of ${esc(p.finished)}` : "–", "beat the market")}
    ${kpi(money(p.finished ? p.profit : null), "practice profit")}${kpi(money(p.finished ? p.market_profit : null), "same money in the market")}</div>
    <p class="note">${esc(p.rule || "")} ${esc(p.says || "")}</p>`;
  const mine = (d.themes || []).filter((t) => (t.yours || []).length), rest = (d.themes || []).filter((t) => !(t.yours || []).length);
  return card("Practice: does acting on the evidence beat the market?", score) +
    card("What followed each kind of evidence change", rows ? `<div style="overflow-x:auto"><table class="grid"><tr><th>Pattern</th><th>Events</th><th>After 30 days</th><th>After 90 days</th></tr>${rows}</table></div>` : "",
      `Buddy says which way a pattern has leaned only after ${esc(d.needed)} finished baskets. Events close together are not independent tests. Good, bad and neutral labels on headlines are written by an AI from the headline alone and are sometimes wrong.`) +
    (mine.length ? card("Themes touching what you follow", mine.map(themeCard).join("")) : "") +
    card(`Themes (${rest.length})`, rest.length ? rest.slice(0, 30).map(themeCard).join("") : `<p class="muted">No evidence collected yet. It fills in after the nightly Intelligence run.</p>`,
      d.yours ? "" : "Nothing is on your watchlist yet, so no theme can be matched to it.") +
    card(`Evidence changes (${(d.events || []).length})`, (d.events || []).length ? d.events.map(themeEvent).join("") : `<p class="muted">None yet. An event is written when a rule above fires for a technology whose filings name a public company.</p>`) +
    `<p class="note">${esc(d.note || "")}</p><p><button class="btn" id="themes-refresh">Refresh</button></p>`;
}

// --- Signals: buy and sell signals for stocks and funds, a switch per category, and Buddy's own record
async function loadSignals() {
  if (signalsBusy) return;
  signalsBusy = true;
  try { signalsData = await api("/api/deals/signals"); }
  catch (e) { signalsData = { failed: e.message }; }
  signalsBusy = false;
  if (tab === "strategy") render();
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
        "A signal is scored a week later; a category is unproven until 20 are scored. How the signals did as paper trades is under Trading.")
    + card("Recent signals", list, s.note)
    + (s.trending.length ? card("Most talked about last night", `<p class="muted">${s.trending.map((t) => `${esc(t.key)} (${esc(t.mentions)}${t.times_usual ? `, ${esc(t.times_usual)}× usual` : ""})`).join(" · ")}</p>`, "Attention, not advice.") : "");
}
async function switchSignal(category) {
  try { const got = await api("/api/deals/signals/switch", { category, on: !signalsData.categories[category] });
    signalsData = { ...signalsData, categories: got.categories }; render(); }
  catch (e) { notice(e.message); }
}

// Overview: today's strategy, then the buy and sell signals, then the investment accounts.
// Ideas from the strategy lab, in the same card shape as the buy-now-or-wait calls: the call, one line of why,
// then each rule that picked it with its record. Backed by a past record, or plainly marked unproven.
async function loadIdeas() {
  if (ideasBusy) return;
  ideasBusy = true;
  try { ideasData = (await api("/api/invest/lab")).ideas || { ideas: [] }; }
  catch (e) { ideasData = { ideas: [], failed: e.message }; }
  ideasBusy = false;
  if (tab === "strategy") render();
}
function ideasCard() {
  if (!ideasData) { loadIdeas(); return card("Ideas from the strategy lab", `<p class="muted">Loading…</p>`); }
  const CALLS = { "Stronger": ["▲ Stronger", "go"], "Worth a look": ["● Worth a look", "wait"], "Unproven": ["○ Unproven", "wait"] };
  const one = (i) => `<div class="idea"><div class="top"><b>${esc(i.symbol)}</b><span class="call ${(CALLS[i.call] || CALLS.Unproven)[1]}">${(CALLS[i.call] || CALLS.Unproven)[0]}</span>
      <span class="muted">${px(i.price)} when picked · stop ${px(i.stop)} · target ${px(i.target)}</span></div>
      <p class="muted" style="margin:6px 0">${esc(i.line)}.</p>
      <ul class="why">${i.rules.map((r) => `<li><b>${esc(r.rule)}</b>${r.backed ? "" : " (unproven)"}: ${esc(r.why)} <small class="muted">${esc(r.record)}</small></li>`).join("")}</ul>
      <small class="muted">Strategy lab · picked ${esc(i.since)}</small> <button class="btn small" data-check="${esc(i.symbol)}">Check</button></div>`;
  const body = ideasData.failed ? `<p class="err">${esc(ideasData.failed)}</p>`
    : (ideasData.ideas || []).length ? ideasData.ideas.map(one).join("")
    : `<p class="muted">No ideas yet. The lab's rules pick each weekday morning; replay them over past prices in Trading so each pick can show its record.</p>`;
  return card("Ideas from the strategy lab", body, ideasData.note || "");
}

function overviewTab() {
  return strategy() + ideasCard() + `<h2 class="group" style="margin:18px 0 8px">Signals</h2>` + signalsTab()
    + `<h2 class="group" style="margin:18px 0 8px">Accounts</h2>` + accountsTab()
    + `<p class="muted">Market news and trends are in Discover, under News.</p>`;
}

function render() {
  tabs();
  $("main").innerHTML = { strategy: overviewTab, practice: practiceTab, themes: themesTab, watch: watchTab }[tab]();
  document.querySelectorAll("[data-sigcat]").forEach((b) => b.onclick = () => switchSignal(b.dataset.sigcat));
  if ($("themes-refresh")) $("themes-refresh").onclick = () => { themesData = null; render(); };
  if ($("practice-refresh")) $("practice-refresh").onclick = () => { practiceData = null; render(); loadPractice(true); };
  document.querySelectorAll("[data-sold]").forEach((b) => b.onclick = () => sold(b.dataset.sold));
  document.querySelectorAll("[data-unwatch]").forEach((b) => b.onclick = () => watch("remove", b.dataset.unwatch));
  document.querySelectorAll("[data-pick]").forEach((b) => b.onclick = () => loadPicks({ action: b.dataset.pick, symbol: b.dataset.sym }));
  document.querySelectorAll("[data-keep]").forEach((b) => b.onclick = async () => { await watch("add", b.dataset.keep); picksData = null; render(); });
  if ($("picks-refresh")) $("picks-refresh").onclick = () => loadPicks({ action: "refresh" });
  const labCall = async (path, body, status) => {
    if ($("lab-status")) $("lab-status").textContent = status || "";
    try { const out = await api(path, body); practiceData.practice.lab = out.lab; render(); if (out.text) notice(out.text); return out; }
    catch (e) { render(); notice(e.message); }
  };
  if ($("setup-run")) $("setup-run").onclick = () => loadSetup(true);
  if ($("setup-refresh")) $("setup-refresh").onclick = () => { practiceData = null; researchData = null; ideasData = null; setupData = null; render(); };
  if ($("research-reload")) $("research-reload").onclick = () => { researchData = null; render(); };
  if ($("research-search")) $("research-search").onclick = () => loadResearch({ action: "search" });
  if ($("research-factors")) $("research-factors").onclick = () => loadResearch({ action: "factors" });
  if ($("research-company")) $("research-company").onclick = () => { $("research-company").disabled = true; $("research-company").textContent = "Fetching…"; loadResearch({ action: "company_figures" }); };
  document.querySelectorAll("[data-start]").forEach((b) => b.onclick = () => loadResearch({ action: "github", url: b.dataset.start }));
  if ($("research-github")) $("research-github").onclick = () => { const url = $("research-url").value.trim(); if (url) loadResearch({ action: "github", url }); };
  document.querySelectorAll("[data-research]").forEach((b) => b.onclick = () => { b.disabled = true; b.textContent = b.dataset.research === "read" ? "Reading…" : "…";
    loadResearch({ action: b.dataset.research, id: Number(b.dataset.id) }); });
  document.querySelectorAll("[data-uselist]").forEach((b) => b.onclick = () => loadResearch({ action: "use_list", name: b.dataset.uselist }));
  document.querySelectorAll("[data-fetchlist]").forEach((b) => b.onclick = () => loadResearch({ action: "fetch_list", name: b.dataset.fetchlist }));
  if ($("holdings-on")) $("holdings-on").onchange = () => loadResearch({ action: "holdings", on: $("holdings-on").checked });
  if ($("list-upload")) $("list-upload").onclick = () => {
    const file = $("list-file").files[0], name = $("list-name").value.trim();
    if (!file || !name) { notice("Give the list a name and choose a file."); return; }
    const reader = new FileReader();
    reader.onload = () => loadResearch({ action: "upload_list", name, filename: file.name, data: String(reader.result).split(",")[1] || "" });
    reader.readAsDataURL(file);
  };
  if ($("lab-replay")) $("lab-replay").onclick = () => labCall("/api/invest/lab/replay", {});
  if ($("lab-refresh")) $("lab-refresh").onclick = () => labCall("/api/invest/lab");
  document.querySelectorAll("[data-rule]").forEach((b) => b.onclick = () => labCall("/api/invest/lab/rule", { key: b.dataset.key, action: b.dataset.rule }));
  if ($("lab-paste")) $("lab-paste").onclick = () => { const text = $("lab-text").value.trim(); if (text) labCall("/api/invest/lab/upload", { name: "pasted text", text }, "Reading…"); };
  if ($("lab-link")) $("lab-link").onclick = () => { const url = $("lab-url").value.trim(); if (url) labCall("/api/invest/lab/upload", { url }, "Reading the page…"); };
  if ($("lab-upload")) $("lab-upload").onclick = () => {
    const file = $("lab-file").files[0];
    if (!file) { notice("Choose a file first."); return; }
    if (file.size > 8000000) { notice("That file is over 8 MB."); return; }
    const reader = new FileReader();
    reader.onload = () => labCall("/api/invest/lab/upload", { name: file.name, data: String(reader.result).split(",")[1] || "" }, "Reading " + file.name + "…");
    reader.onerror = () => notice("That file could not be read.");
    reader.readAsDataURL(file);
  };
  if ($("lab-run")) $("lab-run").onclick = async () => {
    $("lab-run").disabled = true; $("lab-run").textContent = "Running…";
    try { const out = await api("/api/invest/lab", {}); practiceData.practice.lab = out.lab; render();
      const n = Object.values(out.ran.opened || {}).reduce((a, b) => a + b, 0);
      notice(out.said || out.ran.note || `${n} opened, ${out.ran.closed} closed`); }
    catch (e) { notice(e.message); render(); }
  };
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
