// Buddy Deals: deals from the deal feeds and the deals inbox (products, flights, hotels), with each item's price
// history from the price book. Products can be grouped by category, brand and product; flights by route and
// airline; hotels by place and chain; each has a "crazy deals" view. Buddy never buys: links open the deal in your
// browser. Every piece of text is escaped, and only https links are made clickable.

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
const pct = (v) => Math.round(v * 100) + "%";
const plural = (n, word) => `${Number(n).toLocaleString()} ${word}${n === 1 ? "" : "s"}`;
const TABS = [["foryou", "For you"], ["product", "Products"], ["flight", "Flights"], ["hotel", "Hotels"], ["predicted", "Predicted"],
  ["sources", "Sources"], ["alerts", "Alert settings"]];
// The ways each kind of deal can be looked at: all of them, grouped, or only the crazy ones.
const VIEWS = {
  product: [["all", "All"], ["category", "By category"], ["brand", "By brand"], ["product", "By product"], ["crazy", "Crazy deals"]],
  flight: [["all", "All"], ["route", "By route"], ["airline", "By airline"], ["crazy", "Crazy deals"]],
  hotel: [["all", "All"], ["place", "By place"], ["chain", "By chain"], ["crazy", "Crazy deals"]],
};
const MANY = { category: "categories", brand: "brands", product: "products", route: "routes", airline: "airlines", place: "places", chain: "chains" };
const UNFILED = { brand: "Brand not recognised", airline: "Airline not named", chain: "No chain named", place: "Place not named" };
const CRAZY_NOTE = "A crazy deal is one of three: half price or better against the regular price the seller states; 30% or more under the usual price Buddy has seen for the same thing; or called a price mistake by its source. The biggest saving comes first.";
const state = {};
for (const kind of Object.keys(VIEWS)) {
  const kept = storageGet("dealsView:" + kind);
  state[kind] = { view: VIEWS[kind].some(([k]) => k === kept) ? kept : "all", group: null, label: "", count: 0, sort: "new" };
}
let calls = null;
let predicted = null;
let tab = TABS.some(([k]) => k === storageGet("dealsTab")) ? storageGet("dealsTab") : "foryou", data = null, timer = null;
let seq = 0;               // each render takes a number; an answer that arrives for an older render is dropped
const cache = new Map();   // answers for the grouped and sorted views, until the search or the days change

async function api(path, body) {
  const res = await fetch(path, body ? { method: "POST", headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" }, body: JSON.stringify(body) }
    : { headers: { Authorization: `Bearer ${token}` } });
  const out = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(out.detail || (res.status === 401 ? "Open Deals from the Buddy app." : `Error ${res.status}`));
  return out;
}
async function cached(path) {
  if (!cache.has(path)) cache.set(path, await api(path));
  return cache.get(path);
}
const scope = () => `days=${encodeURIComponent($("days").value)}&q=${encodeURIComponent($("q").value)}`;

function tabs() {
  $("tabs").innerHTML = TABS.map(([k, label]) => {
    const n = VIEWS[k] ? (data ? data.counts[k] || 0 : "") : "";
    return `<button class="chip${k === tab ? " on" : ""}" role="tab" aria-selected="${k === tab}" data-tab="${k}">${label}${n !== "" ? ` · ${n}` : ""}</button>`;
  }).join("");
  $("tabs").querySelectorAll("button").forEach((b) => b.onclick = () => { tab = b.dataset.tab; storageSet("dealsTab", tab); render(); });
}

// For you: Buddy's open buy-now-or-wait calls, one card shape for products, flights and hotels: the call, the
// chance, one line of why, then where it came from and its record.
async function renderForYou(mine) {
  try {
    calls = calls || await api("/api/deals/calls");
    if (mine !== seq) return;
    const card = (c) => `<section class="card"><div class="deal"><div class="what"><b>${esc(c.what)}</b>
        <small>${esc(c.kind)}${c.travel_date ? " · " + esc(c.travel_date) : ""}</small></div>
        <div class="price"><b>${money(c.price)}</b></div></div>
      <p style="margin:10px 0 6px"><span class="call ${c.wait ? "wait" : "go"}">${c.wait ? "◷" : "✓"} ${esc(c.call)}</span>
        <span class="muted">${c.wait ? Math.round(c.chance * 100) + "% chance of a lower price" : Math.round((1 - c.chance) * 100) + "% chance this is as low as it gets"}</span></p>
      <p class="muted" style="margin:0 0 6px">${esc(c.why)}</p>
      <small class="muted">Buddy's call on ${esc(c.made_on)} · ${esc(c.record)}</small></section>`;
    $("main").innerHTML = (calls.calls.length ? `<div class="two">${calls.calls.map(card).join("")}</div>`
      : `<p class="muted">No open calls. Ask Buddy "should I buy this now?" or "is this a good fare?" and the call shows here until it is settled.</p>`)
      + `<p class="muted">${esc(calls.note)} How these calls have turned out is in Intelligence, under Predictions.</p>`;
  } catch (e) { if (mine === seq) $("main").innerHTML = `<p class="err">${esc(e.message)}</p>`; }
}

// Predicted: what Buddy expects, under a heading for each kind (products again by category), and under it all
// what the list rests on, so a short list says why it is short.
const SECTIONS = [["sale", "📅 Sales coming up", ""],
  ["product", "🛍️ Products", "A product shows here once it has been on deal on two days at least 3 days apart."],
  ["brand", "🏷️ Brands", "A brand shows here when its deals in one category come back every 5 days or more. Brands with deals most days are listed at the bottom: there is nothing to wait for."],
  ["company", "🏬 Stores and brands, from the nightly learning", ""],
  ["flight", "✈️ Flights", "A route shows here once its fares were on deal on two days at least 3 days apart, or when you have an open book-or-wait call on it."],
  ["hotel", "🏨 Hotels", "A place shows here once its hotel deals were seen on two days at least 3 days apart, or when you have an open book-or-wait call there."]];

function restsOn(b) {
  if (!b) return "";
  const lines = [];
  if (b.error) lines.push(`<span class="err" style="padding:0">${esc(b.error)}</span>`);
  const kinds = [["product", "product"], ["flight", "flight"], ["hotel", "hotel"]].map(([k, word]) => {
    const v = (b.kinds || {})[k];
    return v ? `${plural(v.deals, word + " deal")} over ${plural(v.days, "day")} (since ${esc(v.first)})` : `no ${word} deals yet`;
  });
  if (!b.error) lines.push("In the price book: " + kinds.join("; ") + ".");
  const r = b.repeat || {};
  if (!b.error) lines.push(`On deal on two or more days: ${plural(r.product || 0, "product")}, ${plural(r.flight || 0, "route")}, ${plural(r.hotel || 0, "hotel place")}.`);
  if ((b.steady || []).length) lines.push("Brands with deals most days: " + b.steady.map(esc).join(", ") + ".");
  lines.push(b.learned_on ? `The nightly learning last ran on ${esc(b.learned_on)}${b.learning ? ": " + esc(b.learning) : ""}.`
    : `The nightly learning has not run yet${b.learning ? " (" + esc(b.learning) + ")" : ""}. The list above does not need it: it adds what the sales really brought in past years.`);
  return `<section class="card"><b>What this list rests on</b>${lines.map((l) => `<p class="muted" style="margin:6px 0 0">${l}</p>`).join("")}</section>`;
}

async function renderPredicted(mine) {
  try {
    predicted = predicted || await api("/api/deals/predicted");
    if (mine !== seq) return;
    const list = predicted.predictions || [];
    const card = (p) => `
      <section class="card"><div class="deal"><div class="what">
        <b>${esc(p.what)}</b>
        <small>${esc(p.why)}</small><br><span class="pill">${esc(p.confidence)} confidence</span></div>
        <div class="price"><b>${esc(p.when)}</b>${p.expect_price ? `<s style="text-decoration:none">about ${money(p.expect_price)}</s>` : ""}</div>
      </div></section>`;
    const cap = (s) => s.length <= 2 ? s.toUpperCase() : s.slice(0, 1).toUpperCase() + s.slice(1);
    const section = ([type, title, rule]) => {
      const mineOf = list.filter((p) => p.type === type);
      if (!mineOf.length) return rule ? `<h3>${title} · none yet</h3><p class="muted">${rule}</p>` : "";
      let body = mineOf.map(card).join("");
      if (type === "product") {   // products under their category, the biggest category first
        const by = new Map();
        mineOf.forEach((p) => { const c = p.category || "other"; by.set(c, [...(by.get(c) || []), p]); });
        body = [...by.entries()].sort((a, b) => b[1].length - a[1].length)
          .map(([c, ps]) => `<h4>${esc(cap(c))} · ${ps.length}</h4>${ps.map(card).join("")}`).join("");
      }
      return `<h3>${title} · ${mineOf.length}</h3>${body}`;
    };
    $("main").innerHTML = SECTIONS.map(section).join("") + restsOn(predicted.basis) +
      `<p class="muted">Rough, not promises. Say "watch for it" to Buddy to get an alert when a deal comes.</p>`;
  } catch (e) { if (mine === seq) $("main").innerHTML = `<p class="err">${esc(e.message)}</p>`; }
}

// Sources: where the deals come from. Each feed with what it said the last morning, feeds that can be added (a
// feed is read once before it is added), and the other ways deals arrive.
let sources = null, sourceNote = "";
async function renderSources(mine, fresh) {
  try {
    sources = fresh || sources || await api("/api/deals/sources");
    if (mine !== seq) return;
    const s = sources;
    const kind = (k) => k === "travel" ? `<span class="pill">flights and hotels</span>` : "";
    const feed = (f, i) => `<div class="src"><div class="what"><b>${esc(f.name)}</b> ${kind(f.kind)}<br>
        <small class="muted">${esc(f.said || (s.last_run ? "not read on the last morning" : "not read yet"))}</small></div>
        <button class="btn" data-remove="${i}">Remove</button></div>`;
    const more = (f, i) => `<div class="src"><div class="what"><b>${esc(f.name)}</b> ${kind(f.kind)}<br>
        <small class="muted">${esc(f.about)}</small></div><button class="btn" data-add="${i}">Add and test</button></div>`;
    const other = (o) => `<div class="src"><div class="what"><b>${esc(o.name)}</b> <span class="pill${o.on ? " low" : ""}">${o.on ? "on" : "not set up"}</span><br>
        <small class="muted">${esc(o.about)}</small></div></div>`;
    $("main").innerHTML = (sourceNote ? `<section class="card">${sourceNote}</section>` : "") + `
      <section class="card"><b>Feeds Buddy reads every morning</b><br>
        <small class="muted">${s.off ? "The deals collector is switched off." : s.last_run ? "Last read on " + esc(s.last_run) + (s.last_read ? " at " + esc(s.last_read) : "") + (s.reads > 1 ? ", " + s.reads + " times that day" : "") + "." : "Not read yet."}
          ${s.every_hours > 0 && s.every_hours < 24 ? `Read every ${Number(s.every_hours)} hours from 7 AM to 11 PM; each feed's count is the day's total.` : "Read once each morning."}
          A feed that fails 3 mornings running is paused and tried again each week.</small>
        ${s.feeds.map(feed).join("") || `<p class="muted">No feeds.</p>`}</section>
      <section class="card"><b>More feeds you can add</b><br>
        <small class="muted">Buddy reads a feed once before adding it, and says what it found. A feed that does not answer is not added.</small>
        ${s.suggested.map(more).join("") || `<p class="muted">You have added all the feeds Buddy knows of.</p>`}
        <div class="addrow"><input id="feedurl" type="url" placeholder="https://… (a site's RSS or Atom feed)" aria-label="Feed address">
          <select id="feedkind" aria-label="What the feed carries"><option value="product">Products</option><option value="travel">Flights and hotels</option></select>
          <button class="btn" id="feedadd">Add and test</button></div></section>
      <section class="card"><b>Other ways deals arrive</b>${s.other.map(other).join("")}</section>
      <p class="muted">Buddy reads only what sites publish for readers (feeds) and what is sent to you (email). It never scrapes store pages. For Black Friday and the holidays, the surest source is the stores', airlines' and hotel chains' own newsletters, sent to your deals inbox.</p>`;
    const add = async (url, name, kindOf, button) => {
      button.disabled = true; button.textContent = "Reading it…";
      try {
        const got = await api("/api/deals/sources/add", { url, name, kind: kindOf });
        const t = got.tried || {};
        sourceNote = `<b>Added.</b> <span class="muted">Read ${plural(t.items || 0, "item")} just now; ${t.deals || 0} would be kept as deals${(t.examples || []).length ? ", such as “" + esc(t.examples[0]) + "”" : ""}. It is read every morning from tomorrow.</span>`;
        if (seq === mine) renderSources(mine, got);
      } catch (e) {
        sourceNote = `<span class="err" style="padding:0">${esc(e.message)}</span>`;
        if (seq === mine) renderSources(mine, sources);
      }
    };
    $("main").querySelectorAll("[data-add]").forEach((b) => b.onclick = () => { const f = s.suggested[Number(b.dataset.add)]; add(f.url, f.name, f.kind, b); });
    $("feedadd").onclick = () => { const url = $("feedurl").value.trim(); if (url) add(url, "", $("feedkind").value, $("feedadd")); };
    $("main").querySelectorAll("[data-remove]").forEach((b) => b.onclick = async () => {
      if (b.dataset.sure !== "1") { b.dataset.sure = "1"; b.textContent = "Tap again to remove"; return; }
      try {
        const got = await api("/api/deals/sources/remove", { url: s.feeds[Number(b.dataset.remove)].url });
        sourceNote = `<span class="muted">${esc(got.removed)}</span>`;
        if (seq === mine) renderSources(mine, got);
      } catch (e) { sourceNote = `<span class="err" style="padding:0">${esc(e.message)}</span>`; if (seq === mine) renderSources(mine, sources); }
    });
  } catch (e) { if (mine === seq) $("main").innerHTML = `<p class="err">${esc(e.message)}</p>`; }
}

// Trained models: how close each kind of call is to having enough data, and whether a model is in use.
function modelCard(models) {
  if (!models) return "";
  const row = ([name, m]) => {
    const state_ = m.model_in_use ? "✅ model in use" : m.last_run ? `❌ last model ${esc(m.last_run.status)} (${esc(m.last_run.on)})` : "⏳ collecting";
    const test = m.last_run && m.last_run.tested_on ? ` · tested on ${m.last_run.tested_on} unseen` : "";
    return `<div>${esc(name)}: <b>${m.rows} of ${m.needed}</b> judged calls · ${state_}${test}</div>`;
  };
  return `<section class="card"><b>Trained models</b><br>
    <small>Each week Buddy checks whether a kind of call has enough judged results to train a model. A model is tested on calls it never saw and used only if it passes.</small>
    ${Object.entries(models).map(row).join("")}</section>`;
}

// Alert settings: a switch per shopping and travel category. Investing signals live on the Investments page.
async function renderAlerts(mine, fresh) {
  try {
    const s = fresh || await api("/api/deals/signals");
    if (mine !== seq) return;
    const sw = (c) => `<button class="chip${s.categories[c] ? " on" : ""}" data-cat="${esc(c)}" aria-pressed="${!!s.categories[c]}">${esc(c)}</button>`;
    const models = s.models ? Object.fromEntries(Object.entries(s.models).filter(([name]) => !/invest/i.test(name))) : null;
    $("main").innerHTML = `
      <section class="card"><b>Deal alerts on for</b><br><small>Tap a category to turn its deal alerts and buy-or-wait calls on or off.</small>
        <div class="chips" style="flex-wrap:wrap;margin-top:8px">${s.shopping.map(sw).join("")}</div></section>` + modelCard(models) +
      `<p class="muted">Buy and sell signals for stocks and funds, and Buddy's practice book, are on the Investments page under Signals and Practice.</p>`;
    $("main").querySelectorAll("[data-cat]").forEach((b) => b.onclick = async () => {
      try {
        const got = await api("/api/deals/signals/switch", { category: b.dataset.cat, on: !s.categories[b.dataset.cat] });
        if (mine === seq) renderAlerts(mine, { ...s, categories: got.categories });
      } catch (e) { $("main").insertAdjacentHTML("afterbegin", `<p class="err">${esc(e.message)}</p>`); }
    });
  } catch (e) { if (mine === seq) $("main").innerHTML = `<p class="err">${esc(e.message)}</p>`; }
}

// Why a list is empty: when the feeds were last read and what each one said.
function collectorNote() {
  const c = data && data.collector;
  if (!c) return "";
  if (c.off) return `<p class="muted">The morning deals collector is switched off. Ask Buddy to turn daily deals on.</p>`;
  if (!c.last_run) return `<p class="muted">The deal feeds have not been read yet. They are read each morning after ${Number(c.starts_at_hour) || 9}:00.</p>`;
  const lines = (c.feeds || []).map((f) => `<li>${esc(f)}</li>`).join("");
  return `<p class="muted">The deal feeds were last read on ${esc(c.last_run)}${c.new_deals === 0 ? ", and nothing new came from them" : ""}.</p>` + (lines ? `<ul class="muted">${lines}</ul>` : "");
}

// The row under the tabs: how to look at this kind of deal, and (for a list of deals) how to sort it.
function viewBar(withSort) {
  const st = state[tab];
  const chips = VIEWS[tab].map(([k, label]) => `<button class="chip${k === st.view ? " on" : ""}" data-view="${k}" aria-pressed="${k === st.view}">${label}</button>`).join("");
  const sort = withSort ? `<select id="sort" aria-label="Sort the deals"><option value="new">Newest first</option><option value="saving">Biggest saving first</option><option value="price">Lowest price first</option></select>` : "";
  const crumb = st.group === null ? "" : `<p class="crumb"><button class="link" id="back">‹ All ${MANY[st.view]}</button><b>${esc(st.label)}</b>${st.count ? ` · ${plural(st.count, "deal")}` : ""}</p>`;
  return `<div class="viewbar"><div class="chips">${chips}</div>${sort}</div>${crumb}`;
}
function wireBar() {
  const st = state[tab];
  $("main").querySelectorAll("[data-view]").forEach((b) => b.onclick = () => {
    st.view = b.dataset.view; st.group = null; storageSet("dealsView:" + tab, st.view); render();
  });
  if ($("sort")) { $("sort").value = st.sort; $("sort").onchange = () => { st.sort = $("sort").value; render(); }; }
  if ($("back")) $("back").onclick = () => { st.group = null; render(); };
}
function show(html) { $("main").innerHTML = html; wireBar(); }

function dealCard(d, i) {
  const hot = (d.crazy_why || []).map((w) => `<span class="pill hot">${esc(w)}</span>`).join("");
  return `
    <section class="card"><div class="deal">
      <div class="what">
        <b>${esc(d.title)}</b>
        <small>${esc(d.store || d.source)} · ${esc(d.seen_on)}${d.ends ? ` · ends ${esc(d.ends)}` : ""}</small><br>
        ${d.domain === "product" && (d.brand || d.category) ? `<small class="muted">${esc([d.brand, d.category].filter(Boolean).join(" · "))}${d.product ? ` · filed as “${esc(d.product)}”` : ""}</small><br>` : ""}
        ${d.domain === "flight" ? `<small class="muted">${esc([String(d.product || "").toUpperCase(), d.brand, d.category === "not stated" ? "cabin not stated" : d.category].filter(Boolean).join(" · "))}</small><br>` : ""}
        ${d.domain === "hotel" && (d.place || d.brand) ? `<small class="muted">${esc([d.place, d.brand].filter(Boolean).join(" · "))}</small><br>` : ""}
        ${hot}
        ${d.is_lowest ? `<span class="pill low">Lowest in ${esc(d.seen_days)} days Buddy has seen it</span>` : d.lowest_seen ? `<span class="pill">Lowest seen ${money(d.lowest_seen)}</span>` : d.seen_days <= 1 ? `<span class="pill">First time Buddy has seen this</span>` : ""}
        ${d.under && !d.crazy ? `<span class="pill">${pct(d.under)} under its usual ${money(d.usual_price)}</span>` : ""}
        ${d.usual && d.price ? `<span class="pill">Usual for ${esc(d.brand)} ${esc(d.category)} deals: ${money(d.usual.price)}</span>` : ""}
        ${d.off && !(d.crazy && d.off >= 0.5) ? `<span class="pill">${pct(d.off)} off</span>` : ""}
        <div>${safe(d.url) ? `<a class="btn" href="${esc(safe(d.url))}" target="_blank" rel="noopener noreferrer">Open deal</a>` : `<small class="muted">No link came with this one</small>`}
          <button class="btn" data-i="${i}">Price history</button></div>
      </div>
      <div class="price"><b>${money(d.price)}</b>${d.regular_price ? `<s>${money(d.regular_price)}</s>` : ""}</div>
    </div></section>`;
}

// The groups of one kind of deal: categories, brands, routes... Tap one for its deals.
async function renderGroups(mine) {
  const st = state[tab], kind = tab;
  show(viewBar(false) + `<p class="muted">Loading…</p>`);
  let got;
  try { got = await cached(`/api/deals/groups?domain=${kind}&by=${st.view}&${scope()}`); } catch (e) {
    if (mine === seq) show(viewBar(false) + `<p class="err">${esc(e.message)}</p>`);
    return;
  }
  if (mine !== seq) return;
  if (!got.groups.length) { show(viewBar(false) + `<p class="muted">No ${kind} deals to group in the days chosen.</p>` + collectorNote()); return; }
  const named = got.groups.filter((g) => g.name), rest = got.groups.filter((g) => !g.name);
  const groups = [...named, ...rest.map((g) => ({ ...g, label: UNFILED[st.view] || "Not filed" }))];
  show(viewBar(false) + `<section class="card groups">` + groups.map((g, i) => `
      <button class="grow" data-g="${i}"><span class="gtext"><span class="gname">${esc(g.label)}</span>
        <span class="gmeta">${plural(g.deals, "deal")}${g.lowest != null ? ` · from ${money(g.lowest)}` : ""}${g.best_off ? ` · up to ${pct(g.best_off)} off` : ""}</span></span>
        <span class="chev" aria-hidden="true">›</span></button>`).join("") + `</section>` +
    (got.more ? `<p class="muted">These are the ${groups.length} biggest. Search to find a smaller one.</p>` : "") +
    `<p class="muted">Buddy files each deal by the words in its headline, so a group can hold a deal that does not belong there.</p>`);
  $("main").querySelectorAll("[data-g]").forEach((b) => b.onclick = () => {
    const g = groups[Number(b.dataset.g)];
    st.group = g.name; st.label = g.label; st.count = g.deals; render();
  });
}

// A list of deals: all of a kind, one group's, or the crazy ones; sorted as chosen.
async function renderDeals(mine) {
  const st = state[tab], kind = tab, crazy = st.view === "crazy";
  const head = () => viewBar(!crazy);
  let list, total;
  if (st.view === "all" && st.sort === "new") {   // the list that came with the page
    if (!data) { show(head() + (loadError ? `<p class="err">${esc(loadError)}</p>` : `<p class="muted">Loading the deals…</p>`)); return; }
    list = (data.deals || []).filter((d) => d.domain === kind);
    total = (data.counts || {})[kind] || list.length;
  } else {
    show(head() + `<p class="muted">Loading…</p>`);
    let got;
    try {
      got = await cached(`/api/deals?domain=${kind}&${scope()}` + (st.group !== null ? `&by=${st.view}&group=${encodeURIComponent(st.group)}` : "")
        + (crazy ? "&crazy=1" : "") + (!crazy && st.sort !== "new" ? `&sort=${st.sort}` : ""));
    } catch (e) { if (mine === seq) show(head() + `<p class="err">${esc(e.message)}</p>`); return; }
    if (mine !== seq) return;
    list = got.deals || [];
    total = st.group !== null ? Math.max(st.count, list.length) : crazy ? (got.found || {})[kind] || list.length : (got.counts || {})[kind] || list.length;
  }
  if (!list.length) {
    show(head() + (crazy ? `<p class="muted">No crazy ${kind} deals in the days chosen.</p><p class="muted">${CRAZY_NOTE}</p>`
      : st.group !== null ? `<p class="muted">No deals in this group in the days chosen.</p>`
      : `<p class="muted">No ${kind} deals here yet. Deals arrive from the deal feeds every morning and from your deals inbox every night.</p>` + collectorNote()));
    return;
  }
  const more = total > list.length ? `<p class="muted">Showing ${st.sort === "new" && !crazy ? "the newest " : ""}${list.length} of ${total.toLocaleString()}. Search to narrow them down.</p>` : "";
  show(head() + (crazy ? `<p class="muted">${CRAZY_NOTE}</p>` : "") + more + list.map(dealCard).join("") +
    `<p class="muted">Prices are from the deal feeds and newsletters; check the price and seller before buying. Buddy never buys.</p>`);
  $("main").querySelectorAll("button[data-i]").forEach((b) => b.onclick = () => history_(list[Number(b.dataset.i)]));
}

function render() {
  tabs();
  const mine = ++seq;
  if (tab === "foryou") { renderForYou(mine); return; }
  if (tab === "predicted") { renderPredicted(mine); return; }
  if (tab === "sources") { renderSources(mine); return; }
  if (tab === "alerts") { renderAlerts(mine); return; }
  const st = state[tab];
  if (st.view !== "all" && st.view !== "crazy" && st.group === null) { renderGroups(mine); return; }
  renderDeals(mine);
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
    const h = await api(`/api/deals/history?item=${encodeURIComponent(d.product || d.item)}&domain=${encodeURIComponent(d.domain)}`);
    $("panel").innerHTML = `<button class="close" aria-label="Close">×</button><h2>${esc(d.title)}</h2>
      <p class="muted">${esc(d.domain === "flight" ? "Route " + d.item.toUpperCase() : d.domain === "hotel" ? d.item.replace("hotels:", "Hotels in ") : "Every price Buddy has seen")}:
        ${h.points.length} day${h.points.length === 1 ? "" : "s"}, lowest ${money(h.lowest)}</p>
      ${chartSvg(h.points)}
      <p class="muted">Ask Buddy "should I buy it now?" for a buy-or-wait call.</p>
      ${safe(d.url) ? `<a class="btn" href="${esc(safe(d.url))}" target="_blank" rel="noopener noreferrer">Open deal</a>` : `<small class="muted">No link came with this one</small>`}`;
    $("panel").querySelector(".close").onclick = () => $("sheet").classList.remove("open");
  } catch (e) { $("panel").innerHTML += `<p class="err">${esc(e.message)}</p>`; }
}
$("sheet").onclick = (e) => { if (e.target === $("sheet")) $("sheet").classList.remove("open"); };

let loadError = "";
async function load() {
  cache.clear();   // the search or the days changed: every grouped and sorted view is asked for again
  for (const kind of Object.keys(VIEWS)) state[kind].group = null;
  try {
    data = await api(`/api/deals?${scope()}`);
    loadError = "";
  } catch (e) { data = null; loadError = `The deals list could not be loaded. ${e.message}`; }
  render();
}
$("q").oninput = () => { clearTimeout(timer); timer = setTimeout(load, 350); };
$("days").onchange = load;
tabs();
load();
