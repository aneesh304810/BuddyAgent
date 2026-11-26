// Buddy Investments: trade strategy (ideas being watched, trades being tracked), market trends, the investment
// accounts Plaid reports, and the watchlist. Ideas, not financial advice: Buddy never places a trade.
// Every piece of text is escaped, and only https links are made clickable.

const params = new URLSearchParams(location.search);
function storageGet(k) { try { return localStorage.getItem(k); } catch { return null; } }
function storageSet(k, v) { try { localStorage.setItem(k, v); } catch {} }
if (params.get("token")) storageSet("buddyToken", params.get("token"));
const token = params.get("token") || storageGet("buddyToken") || "";
if (params.get("token") && storageGet("buddyToken") === token) history.replaceState(null, "", location.pathname);

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const safe = (u) => /^https:\/\/[^\s"'<>]+$/.test(String(u || "")) ? u : "";
// Four tabs: the old Strategy, Signals and Accounts are one Overview; market news is in Discover, under News.
const TABS = [["strategy", "Overview"], ["practice", "Trading"], ["themes", "Themes"], ["watch", "Watchlist"]];
let setupData = null, setupBusy = false, deskData = null, deskBusy = false, labNow = {};
let picksData = null, picksBusy = false, researchData = null, researchBusy = false, ideasData = null, ideasBusy = false;
let themesData = null, themesBusy = false, practiceData = null, practiceBusy = false, signalsData = null, signalsBusy = false;
let posData = null, posBusy = false;
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

// In the redesign Invest sits under Money's own row of sections, so it has no second row of tabs: Overview first,
// with Trading, Themes and Watchlist listed under it ("Also in Invest"), each opened full-page with "‹ Invest"
// and the phone's Back. ?tabs=1 brings back the row for one release.
const OLD_TABS = params.get("tabs") === "1";
const PARTS = [["practice", "Trading", "The strategies in use, closed trades and the strategy lab"],
  ["themes", "Themes", "Technology themes and the evidence behind each basket"], ["watch", "Watchlist", "The symbols you follow"]];
function openTab(t, push = true) {
  tab = t; storageSet("investTab", t);
  if (push && t !== "strategy") history.pushState({ tab: t }, "");
  render(); window.scrollTo(0, 0);
}
window.addEventListener("popstate", (e) => { if (!OLD_TABS) openTab((e.state && e.state.tab) || "strategy", false); });
function parts() {
  if (OLD_TABS) return;
  if (tab !== "strategy") {
    $("main").insertAdjacentHTML("afterbegin", `<p><button class="btn" id="part-back">‹ Invest</button></p>`);
    $("part-back").onclick = () => (history.state && history.state.tab ? history.back() : openTab("strategy", false));
    return;
  }
  $("main").insertAdjacentHTML("beforeend", card("Also in Invest", PARTS.map(([k, name, about]) =>
    `<button class="part" data-part="${k}"><span><b>${esc(name)}</b><small>${esc(about)}</small></span><span aria-hidden="true">›</span></button>`).join("")));
  document.querySelectorAll("[data-part]").forEach((b) => b.onclick = () => openTab(b.dataset.part));
}

function tabs() {
  if (!OLD_TABS) {  // no row; inside the app the header (title only, hidden there) goes too
    $("tabs").hidden = true;
    if (document.documentElement.dataset.embed) document.querySelector("header").hidden = true;
    return;
  }
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
    "Every symbol here is in the daily scan, on top of the usual list for each market.") + shortCard() + picksCards();
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
// The news desk: stories (not headlines) behind the companies Buddy is acting on, how far each can be trusted,
// and the record of paid checks: asked, or noted as "would have asked" while no paid source is set up.
async function loadDesk(body) {
  if (deskBusy) return;
  deskBusy = true;
  try { const out = await api("/api/invest/newsdesk", body); deskData = out.newsdesk; if (out.text) notice(out.text); }
  catch (e) { deskData = { companies: [], paid: {}, failed: e.message }; }
  deskBusy = false;
  if (tab === "practice") render();
}
function deskCard() {
  if (!deskData) { loadDesk(); return card("News behind the trades", `<p class="muted">Loading…</p>`); }
  const STARS = { 3: "●●● established outlet", 2: "●●○ press release or specialist", 1: "●○○ not rated" };
  const story = (s) => `<li>${tone(s.tone === "mixed" ? "" : s.tone)}${s.tone === "mixed" ? ` <span class="muted">◐ mixed</span>` : ""} · ${s.url ? `<a href="${safe(s.url)}" target="_blank" rel="noopener">${esc(s.title)}</a>` : esc(s.title)}
      <small class="muted">${esc(s.day)} · ${esc(s.reports)} report${s.reports === 1 ? "" : "s"} · ${esc(STARS[s.rating] || "")}${s.unclear ? " · unclear: " + esc(s.why_unclear.join("; ")) : ""}${s.check ? " · " + esc(s.check) : ""}</small></li>`;
  const companies = (deskData.companies || []).map((c) => `<div class="tx" style="display:block"><b>${esc(c.symbol)}</b> <small class="muted">${esc(c.matters)}${c.unclear ? ` · ${esc(c.unclear)} unclear` : ""}</small>
      <ul class="why">${c.stories.map(story).join("")}</ul></div>`).join("");
  const p = deskData.paid || {};
  const checks = (p.checks || []).map((c) => `<div class="row" style="cursor:default;align-items:flex-start"><span class="name" style="white-space:normal"><b>${esc(c.symbol)}</b> ${esc(c.story)}
      <small class="muted" style="display:block">${esc(c.day)} · ${esc(c.why)} · ${esc(c.result)}</small></span></div>`).join("");
  return card("News behind the trades", (deskData.failed ? `<p class="err">${esc(deskData.failed)}</p>` : "") +
      (companies || `<p class="muted">No company news yet for the companies Buddy is acting on. Headlines are searched nightly by company name.</p>`) +
      `<p><button class="btn" id="desk-review">Look for unclear stories now</button></p>`, deskData.note || "") +
    card("News fact checks", `<div class="kpis">${kpi(esc(p.settled_this_month || 0), "settled by the free model")}${kpi(esc(p.free_confirmed_this_month || 0), "confirmed by free news")}${kpi(esc(p.asked_this_month || 0), "sent to the last-resort services")}
        ${kpi(esc(p.would_have_asked_this_month || 0), "would have asked")}${kpi(esc(p.cap ?? "–"), "limit a month")}</div>
        <p class="note">Free news: ${(p.free_sources || []).map((x) => `${esc(x.title)}: ${x.set_up ? "key set" : "no key"}`).join(" · ")}</p>
        <p class="note">Last resort: ${(p.sources || []).map((x) => `${esc(x.title)}: ${x.set_up ? "key set" : "no key"}`).join(" · ")}</p>` + checks +
      `<div class="add"><input id="desk-cap" type="number" min="0" max="1000" value="${esc(p.cap ?? 30)}" aria-label="Fact checks a month"><button class="btn" id="desk-cap-set">Set the monthly limit</button></div>`,
      "An unclear story about a company Buddy is acting on goes first to the free AI model. If the model cannot settle it, Buddy looks it up in the free news sources it already has. Only a story neither settles goes to the last-resort services, up to the limit. " +
      (p.set_up ? "" : `No last-resort service has a key, so those stories are written down as would have asked. To add one: sudo bash /opt/buddy/deploy/set.sh ${((p.sources || [])[0] || {}).key || "BUDDY_NEWSDATA_KEY"}`));
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
      <span class="amt">${t.closed ? pct(t.change) : labNow[t.symbol] && t.entry ? `${pct(labNow[t.symbol] / t.entry - 1)}<small class="muted">now ${px(labNow[t.symbol])}</small>` : `<small class="muted">open</small>`}</span></div>`;
}

// Rules, research and tests live in one place, Intelligence > Strategies; the Trading tab keeps the trades.
function labCards(p) {
  const lab = p.lab;
  if (!lab) return "";
  return card("Rules, research and tests", `<p>Finding strategies, approving rules, replaying them on past prices, the ranking and the live monitor are all in <b>Intelligence → Strategies</b>.</p>
      <p><button class="btn solid" id="open-engine">Open Investment Intelligence</button> <button class="btn" id="lab-run">Run the lab now</button> <button class="btn" id="lab-refresh">Refresh</button></p>`, lab.note);
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

// --- Who holds the futures (CFTC, weekly) and short selling in the watchlist (FINRA, personal use only)
async function loadPositioning() {
  if (posBusy) return;
  posBusy = true;
  try { posData = await api("/api/invest/positioning"); } catch (e) { posData = { failed: e.message }; }
  posBusy = false;
  if (tab === "practice" || tab === "watch") render();
  if (posData && posData.reading) setTimeout(() => { posData = null; loadPositioning(); }, 5000);
}
async function readPositioning() {
  try { await api("/api/invest/positioning/read", {}); notice("Reading the CFTC report and FINRA's files: about a minute."); }
  catch (e) { notice(e.message); return; }
  posData = null; render();
}
const contracts = (n) => `${Math.abs(Math.round(n)).toLocaleString()} contracts`;
function readButton(failed) {
  if (posData && posData.reading) return `<p class="muted">Reading now…</p>`;
  return `<p>${failed ? `<span class="err" style="padding:0">${esc(failed)}</span> ` : ""}<button class="btn small" id="positioning-read">Read now</button></p>`;
}
function futuresCard() {
  if (!posData) { loadPositioning(); return card("Who holds the futures", `<p class="muted">Loading…</p>`); }
  if (posData.failed) return card("Who holds the futures", `<p class="err">${esc(posData.failed)}</p>`);
  const c = posData.cftc || {}, failed = ((posData.status || {}).cftc || {}).failed;
  if (!(c.groups || []).length) return card("Who holds the futures", `<p class="muted">${esc(c.empty || "Nothing read yet.")}</p>` + readButton(failed), esc(c.source));
  const row = (m) => `<div class="pos"><div class="posrow"><span class="name"><b>${esc(m.name)}</b>
      <small class="muted">${m.net ? `net ${m.net > 0 ? "long" : "short"} ${contracts(m.net)}` : "flat"}${m.net_pct != null ? ` · ${esc(m.net_pct)}% of open contracts` : ""}${m.week_change ? ` · ${m.week_change > 0 ? "+" : "−"}${Math.abs(Math.round(m.week_change)).toLocaleString()} this week` : ""}</small></span>
      ${m.standing ? `<span class="pill${m.standing.startsWith("near") ? " hot" : ""}">${esc(m.standing)}</span>` : ""}</div>
    ${m.rank != null ? `<div class="rank" role="img" aria-label="Rank ${esc(m.rank)} of 100 against the last ${esc(m.weeks)} weeks"><i style="left:${Math.max(0, Math.min(100, m.rank))}%"></i></div>` : ""}
    <details><summary>What it says</summary><p class="muted">${esc(m.reading)}</p></details></div>`;
  const near = (s) => (c.crowded || []).filter((x) => x.standing === s).map((x) => esc(x.name)).join(", ");
  const body = [["near a 3-year high", "Large speculators near their most long in 3 years"], ["near a 3-year low", "Near their least long in 3 years"]]
    .filter(([s]) => near(s)).map(([s, label]) => `<p><b>${label}:</b> ${near(s)}</p>`).join("");
  const groups = c.groups.map((g) => `<h3 class="group">${esc(g.title)}</h3>` + g.rows.map(row).join("") + (g.note ? `<p class="note">${esc(g.note)}</p>` : "")).join("");
  return card("Who holds the futures", body + groups + readButton(failed),
    `Positions on ${esc(c.as_of)}, published the Friday after. Each bar is where the large speculators' position ranks against the last 3 years: left is their least long, right their most long, and the shaded ends are crowded. ${esc(c.note)} Source: ${esc(c.source)}`);
}
function shortCard() {
  if (!posData) { loadPositioning(); return card("Short selling in your watchlist", `<p class="muted">Loading…</p>`); }
  if (posData.failed) return card("Short selling in your watchlist", `<p class="err">${esc(posData.failed)}</p>`);
  const f = posData.finra || {}, st = (posData.status || {}).finra || {};
  const failed = st.failed || ((st.volume || {}).failed) || "";
  const rows = (f.stocks || []).map((s) => `<div class="pos"><div class="posrow"><span class="name"><b>${esc(s.symbol)}</b>
      <small class="muted">${s.short_share != null ? `${(s.short_share * 100).toFixed(0)}% short${s.usual_share != null ? ` · usually ${(s.usual_share * 100).toFixed(0)}%` : ""}` : ""}${s.days_to_cover != null ? ` · ${Number(s.days_to_cover).toFixed(1)} days to cover` : ""}</small></span>
      ${(s.flags || []).map((x) => `<span class="pill hot">${esc(x)}</span>`).join(" ")}</div>
    <p class="muted">${esc(s.reading)}</p></div>`).join("");
  const waiting = (f.waiting || []).length ? `<p class="note">Not read yet for ${esc(f.waiting.join(", "))}: from the next files.</p>` : "";
  return card("Short selling in your watchlist", (rows || `<p class="muted">${esc(f.empty || "Nothing read yet.")}</p>`) + waiting + readButton(failed),
    `${esc(f.note)} ${esc(f.terms)}`);
}

function practiceTab() {
  if (!practiceData) { loadPractice(); return `<p class="muted">Loading the paper trades…</p>`; }
  if (practiceData.failed) return `<p class="err">${esc(practiceData.failed)}</p><button class="btn" id="practice-refresh">Try again</button>`;
  const p = practiceData.practice || {}, m = practiceData.markets;
  if (p.error) return card("Practice book", `<p class="muted">${esc(p.error)}</p>`) + marketsCard(m) + linksCard(m) + futuresCard();
  const mo = p.money || {}, o = p.overall;
  // All paper trading together first, then each book on its own line.
  const all = p.all || { books: [] };
  const gain = (v) => v == null ? "–" : `<span style="color:var(--${v > 0 ? "sev-good" : v < 0 ? "sev-high" : "muted"})">${v > 0 ? "+" : v < 0 ? "−" : ""}$${Math.abs(v).toLocaleString(undefined, { maximumFractionDigits: 0 })}</span>`;
  const bookRow = (b) => `<div class="tx" style="grid-template-columns:1fr auto"><span><b>${esc(b.name)}</b>
      <small>${esc(b.open)} open with $${Number(b.in_trades).toLocaleString(undefined, { maximumFractionDigits: 0 })} in them${b.open ? ` (${gain(b.open_gain)} so far)` : ""} · ${b.closed ? `${esc(b.closed)} closed, ${esc(b.won)} made money` : "none closed yet"}${b.name === "Signals book" ? ` · $${Number(mo.cash || 0).toLocaleString(undefined, { maximumFractionDigits: 0 })} cash unused` : ""}</small>
      <small>${esc(b.how)}</small></span><span class="amt">${b.closed ? gain(b.closed_result) : `<small class="muted">no result yet</small>`}</span></div>`;
  const summary = `<div class="kpis">${kpi(esc(all.open ?? 0), "paper trades open now")}${kpi("$" + Number(all.in_trades || 0).toLocaleString(undefined, { maximumFractionDigits: 0 }), "pretend money in them")}
    ${kpi(all.open ? gain(all.open_gain) : "–", "gain or loss on open trades" + (all.open_change != null ? ` (${(all.open_change * 100).toFixed(1)}%)` : ""))}
    ${kpi(esc(all.closed ?? 0), "trades closed")}${kpi(all.closed ? `${esc(all.won)} of ${esc(all.closed)}` : "–", "closed trades that made money")}
    ${kpi(all.closed ? gain(all.closed_result) : "–", "result of closed trades")}</div>
    ${all.open && all.priced < all.open ? `<p class="note">${esc(all.open - all.priced)} open trades have no price yet today, so they count as no gain and no loss.</p>` : ""}
    ${(all.books || []).map(bookRow).join("")}
    <p class="note">Open trades are valued at the latest daily close. ${all.closed ? "" : "Nothing has closed yet: a trade closes at its stop, its target or its time limit, which takes days to weeks. "}${mo.skipped_for_risk ? esc(mo.skipped_for_risk) + " signals were skipped by the risk rules. " : ""}Nothing real is bought.</p>`;
  // Every paper trade in one place: the Signals book's trades and the strategy lab's, each named by its rule.
  const lab = p.lab || { open: [], closed: [], strategies: [] };
  const sum = (k) => (lab.strategies || []).reduce((a, x) => a + (x[k] || 0), 0);
  const labLine = "";
  labNow = p.lab_last || {};
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
    card(`Bought and holding (${all.open ?? (p.open || []).length + lab.open.length})`, (all.open > (p.open || []).length + lab.open.length ? `<p class="note">Showing the latest ${(p.open || []).length + lab.open.length} of ${esc(all.open)}.</p>` : "") + both(p.open, lab.open, "No paper positions are open. The lab opens trades each weekday morning, or press Run the lab now below.")) +
    card(`Sold and judged (${(p.closed || []).length + lab.closed.length})`, both(p.closed, lab.closed, "Nothing has closed yet. A trade closes at its stop, its target, a sell signal, or its time limit.")) +
    ((p.skipped || []).length ? card("Signals not traded", list(p.skipped, "")) : "") +
    card("Day by day", log || `<p class="muted">No activity yet.</p>`) +
    deskCard() + setupCard() + strategiesCards(p) + labCards(p) +
    (p.strategies ? "" : learned(p)) + marketsCard(m) + linksCard(m) + futuresCard() +
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
  parts();
  document.querySelectorAll("[data-sigcat]").forEach((b) => b.onclick = () => switchSignal(b.dataset.sigcat));
  if ($("themes-refresh")) $("themes-refresh").onclick = () => { themesData = null; render(); };
  if ($("practice-refresh")) $("practice-refresh").onclick = () => { practiceData = null; render(); loadPractice(true); };
  if ($("positioning-read")) $("positioning-read").onclick = readPositioning;
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
  if ($("desk-review")) $("desk-review").onclick = () => loadDesk({ action: "review" });
  if ($("desk-cap-set")) $("desk-cap-set").onclick = () => loadDesk({ action: "limit", calls: Number($("desk-cap").value) });
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
  if ($("open-engine")) $("open-engine").onclick = () => { storageSet("intelTab", "strategies"); location.href = "intel.html"; };
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
  try { const out = await api("/api/invest/watch", { action, symbol }); data.watchlist = out.watchlist; posData = null; render(); notice(out.text); }
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
