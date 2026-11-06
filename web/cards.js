// Buddy card benefits: every card, its credits (used / left / days left), and each credit's details.
// Every number comes from Buddy's server; bank and card text is always escaped.

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
const money = (v) => v == null ? "–" : (v < 0 ? "−" : "") +
  (Math.abs(v) >= 100 ? "$" + Math.round(Math.abs(v)).toLocaleString() : "$" + Math.abs(Number(v)).toFixed(Number(v) % 1 ? 2 : 0));
const day = (iso) => { if (!iso) return ""; const d = new Date(iso + "T12:00:00"); return d.toLocaleDateString(undefined, { month: "short", day: "numeric" }); };
const card = (title, body, extra = "") => `<section class="card" ${extra}><h2>${title}</h2>${body}</section>`;
const PERIOD = { monthly: "every month", quarterly: "every quarter", semiannual: "every 6 months", annual: "every year", once: "one time" };
let data = null;

async function api(path, body) {
  const res = await fetch(path, body ? { method: "POST", headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
    body: JSON.stringify(body) } : { headers: { Authorization: `Bearer ${token}` } });
  const out = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(out.detail || `Error ${res.status}`);
  return out;
}

async function load() {
  try { data = await api("/api/cards/dashboard"); render(); }
  catch (e) { $("main").innerHTML = `<p class="err">${esc(e.message)}</p>`; }
}

function progress(x) {
  const pct = x.amount ? Math.min(100, (100 * (x.used || 0)) / x.amount) : 0;
  const late = x.left > 0 && x.days_left <= 14;
  return `<span class="prog${late ? " warn" : ""}"><i style="width:${pct}%"></i></span>`;
}

function creditRow(c, x, ci, xi) {
  const meta = [PERIOD[x.period] || x.period, x.resets === "anniversary" ? "card anniversary" : ""].filter(Boolean).join(" · ");
  const chips = (x.enrollment ? '<span class="chip2">enroll first</span>' : "") +
    (x.tracked && x.left > 0 && x.days_left <= 14 ? `<span class="chip2 warn">${x.days_left}d left</span>` : "");
  const right = x.tracked
    ? `${progress(x)}<span class="rt">${money(x.left)} left<br>${x.days_left}d · ${esc(day(x.ends))}</span>`
    : `<span class="rt">${money(x.amount)}</span><button class="btn" data-track="${ci}:${xi}">+ Track</button>`;
  return `<div class="credit" data-open="${ci}:${xi}"><div class="nm"><b>${esc(x.name)}${chips}</b><small>${money(x.amount)} ${esc(meta)}</small>${evidence(x)}</div>${right}</div>`;
}

const TAB_KEY = "cardsTab";
let tab = storageGet(TAB_KEY) || "overview";

function evidence(x) {  // what the card's own statement (Plaid) says about this credit
  if (x.tracked) {
    const seen = (x.credits_seen || []).slice(-1)[0];
    if (seen) return `<span class="ev good">✓ ${money(seen.amount)} statement credit on ${esc(day(seen.day))}</span>`;
    if (x.maybe_pending) return `<span class="ev">You spent there; ${money(x.maybe_pending)} of credit may still post</span>`;
    if (x.used > 0) return `<span class="ev good">✓ ${money(x.used)} marked used</span>`;
    return x.statement_through ? `<span class="ev warn">Not used yet: no statement credit through ${esc(day(x.statement_through))}</span>` : "";
  }
  if ((x.seen || []).length) return `<span class="ev good">✓ Looks used: ${money(x.seen_used)} statement credit on ${esc(day(x.seen.slice(-1)[0].day))}</span>`;
  return x.statement_through ? `<span class="ev">No statement credit this period (through ${esc(day(x.statement_through))})</span>` : "";
}

function tabs() {
  const all = [["overview", "Overview"], ["category", "By category"], ["best", "Best card"], ["missed", "Missed rewards"], ["cards", "My cards"]];
  return `<div class="chips" style="margin:2px 0 12px">${all.map(([k, l]) => `<button class="chip${k === tab ? " on" : ""}" data-tab="${k}">${l}</button>`).join("")}</div>`;
}

function comingUp(d, limit) {
  const rows = d.coming_up.slice(0, limit);
  if (!rows.length) return card("Coming up", `<p class="muted">Nothing tracked is running out. ${d.cards.some((c) => c.credits.some((x) => !x.tracked)) ?
    "Track your credits (My cards → Track all) and Buddy shows each one here with its date." : ""}</p>`);
  return card("Coming up", `<p class="muted" style="margin:-6px 0 6px">Credits with money left, by the date they reset or expire</p>` + rows.map((x) => {
    const dt = new Date((x.ends || "") + "T12:00:00");
    const ci = d.cards.findIndex((c) => c.card === x.card), xi = ci >= 0 ? d.cards[ci].credits.findIndex((y) => y.id === x.id) : -1;
    return `<div class="soon" data-open="${ci}:${xi}"><div class="date"><b>${dt.toLocaleDateString(undefined, { month: "short" })}</b><span>${dt.getDate()}</span></div>
      <div class="what"><b>${money(x.year_value)} ${esc(x.name)}</b><span>${money(x.left)} ${x.days_left <= 31 ? "expiring" : "left"} · ${esc(x.card)}</span>${evidence(x)}</div>
      ${x.days_left <= 14 ? `<span class="chip2 warn">${x.days_left}d</span>` : ""}</div>`;
  }).join("") + (d.coming_up.length > limit ? `<p><button class="btn" data-tab="cards">Show all</button></p>` : ""));
}

function categoryTiles(d) {
  return card("Browse by category", `<p class="muted" style="margin:-6px 0 6px">Benefits you might be missing</p>` + d.browse.map((g) =>
    `<div class="cat" data-cat="${esc(g.category)}"><span class="icon">${g.icon}</span><div class="what"><b>${esc(g.category)}</b><span>${esc(g.preview)}</span></div>
      <div class="rt">${g.year_value ? money(g.year_value) + "/yr" : ""}${g.expiring ? `<br><span class="chip2 warn">${g.expiring} expiring</span>` : ""}</div></div>`).join(""));
}

function byCategory(d) {
  return d.browse.map((g) => card(`${g.icon} ${esc(g.category)} <span class="muted" style="font-weight:400">· ${g.count} benefit${g.count === 1 ? "" : "s"}</span>`,
    g.subs.map((s, si) => `<div class="sub" data-sub="${esc(g.category)}|${si}"><div class="what"><b>${esc(s.name)}</b>
        <span>${s.count} benefit${s.count === 1 ? "" : "s"} from ${s.cards} card${s.cards === 1 ? "" : "s"}</span>
        ${s.expiring ? `<span class="chip2 warn" style="margin:4px 0 0">${s.expiring} expiring</span>` : ""}</div>
      <div class="rt">${s.year_value ? `<b>${money(s.year_value)}</b><br><span class="muted">a year</span>` : ""}</div></div>`).join(""),
    `id="cat-${esc(g.category).replace(/[^A-Za-z]/g, "")}"`)).join("");
}

function openSub(d, cat, si) {
  const s = d.browse.find((g) => g.category === cat).subs[si];
  sheet(`<div class="crumbs">${esc(cat)}</div><h2 style="margin:0 0 8px">${esc(s.name)}</h2>` + s.items.map((x) => {
    const ci = d.cards.findIndex((c) => c.card === x.card);
    const cx = ci >= 0 && x.type === "credit" ? d.cards[ci].credits.find((y) => y.name === x.name) : null;
    return `<div class="sub" ${cx ? `data-open="${ci}:${d.cards[ci].credits.indexOf(cx)}"` : ""}><div class="what"><b>${esc(x.name)}</b>
      <span>${esc(x.card)}${x.type === "credit" ? ` · ${money(x.amount)} ${esc(PERIOD[x.period] || x.period || "")}` : " · perk"}</span>${cx ? evidence(cx) : ""}</div>
      <div class="rt">${x.tracked ? `${money(x.left)} left` : x.type === "credit" ? '<span class="muted">not tracked</span>' : ""}</div></div>`;
  }).join(""));
  $("panel").querySelectorAll("[data-open]").forEach((el) => el.onclick = () => {
    const [ci, xi] = el.dataset.open.split(":").map(Number); openCredit(d.cards[ci], d.cards[ci].credits[xi]);
  });
}

function bestCard(d) {
  if (!d.earn_guide.length) return card("Rewards earning guide", `<p class="muted">Buddy needs your cards' earn rates: say "set up my card benefits".</p>`);
  return card("Rewards earning guide", `<p class="muted" style="margin:-6px 0 6px">The best of your cards for each kind of spending.
    Points and miles counted at ${d.missed.point_cents || 1}¢ each.</p>` + d.earn_guide.map((g) => `<div class="guide">
      <div class="what"><b>${esc(g.label)}: ${esc(g.best.label)}</b><span>${esc(g.best.card)}${g.best.last4 ? " ••" + esc(g.best.last4) : ""}</span>
      <div class="pills">${g.ties.length ? `<span class="pill">tied with ${g.ties.length} other card${g.ties.length === 1 ? "" : "s"}</span>` : ""}
        ${g.limited.map((l) => `<span class="pill">${esc(l.card.split(" ").slice(-2).join(" "))}: up to ${esc(l.label)} (${esc(l.category)})</span>`).join("")}</div></div>
      <div class="rt"><b>${g.best.value.toFixed(g.best.value % 1 ? 1 : 0)}%</b></div></div>`).join(""));
}

function missed(d) {
  const m = d.missed;
  if (!m.items.length) return card("Missed rewards", `<p class="muted">No missed rewards in the last ${m.days} days: you used the best card, or Buddy needs your cards' earn rates and the bank feed.</p>`);
  return card(`Missed rewards · ${money(m.total)}`, `<p class="muted" style="margin:-6px 0 6px">In the last ${m.days} days, using your best card each time would have earned ${money(m.total)} more
    (about ${money(m.yearly)} a year).</p>` + m.items.map((x) => `<div class="miss">
      <div class="head"><b>${esc(x.merchant)}</b><span>${money(x.amount)} · ${esc(day(x.day))}</span></div>
      <div class="swap"><span>${x.used.value}%<br><small>${esc(x.used.card)}</small></span><i>→</i><span>${x.better.value}%<br><small>${esc(x.better.card)}</small></span>
        <b class="lost">${money(x.missed)}<br><small>missed</small></b></div></div>`).join(""));
}

function render() {
  const d = data, t = d.totals, s = d.summary || {};
  $("label").textContent = d.cards.length ? `· ${d.cards.length} card${d.cards.length === 1 ? "" : "s"}` : "";
  if (!d.cards.length) {
    $("main").innerHTML = card("No cards yet", `<p class="muted">Connect your bank in Buddy, or tell Buddy which cards you have. Then say "set up my card benefits".</p>`);
    return;
  }
  const parts = [`<div class="kpis" style="margin-bottom:10px">
    <div class="kpi"><span>Total benefits</span><b>${s.benefits ?? "–"}</b></div>
    <div class="kpi"><span>Credits a year</span><b>${money(t.year_value)}</b></div>
    <div class="kpi"><span>Used this year</span><b style="color:var(--good)">${money(t.used_this_year)}</b></div>
    <div class="kpi"><span>Annual fees</span><b>${money(t.fees)}</b></div>
    <div class="kpi"><span>Missed (30 days)</span><b style="color:${s.missed_30d ? "var(--warn)" : "var(--good)"}">${money(s.missed_30d || 0)}</b></div></div>`, tabs()];
  if (d.questions && d.questions.length) parts.push(card("Which card is this?", d.questions.map((q) => `<p class="muted">${esc(q)}</p>`).join("") +
    `<p class="muted">Tell Buddy, e.g. "0457 is my Sapphire Preferred".</p>`));
  if (tab === "overview") parts.push(comingUp(d, 5), categoryTiles(d), bestCard(d));
  else if (tab === "category") parts.push(byCategory(d));
  else if (tab === "best") parts.push(bestCard(d));
  else if (tab === "missed") parts.push(missed(d));
  else myCards(d, parts);
  parts.push(`<p class="muted">Used or not is read from your cards' own statements (Plaid): a statement credit that names the benefit counts.
    Benefits come from each issuer's website; your card's terms win.</p>`);
  $("main").innerHTML = parts.join("");
  document.querySelectorAll("[data-tab]").forEach((el) => el.onclick = () => { tab = el.dataset.tab; storageSet(TAB_KEY, tab); render(); scrollTo(0, 0); });
  document.querySelectorAll("[data-cat]").forEach((el) => el.onclick = () => {
    tab = "category"; storageSet(TAB_KEY, tab); render();
    const target = document.getElementById("cat-" + el.dataset.cat.replace(/[^A-Za-z]/g, "")); if (target) target.scrollIntoView();
  });
  document.querySelectorAll("[data-sub]").forEach((el) => el.onclick = () => { const [c, i] = el.dataset.sub.split("|"); openSub(d, c, Number(i)); });
  document.querySelectorAll("[data-open]").forEach((el) => el.onclick = (e) => {
    if (e.target.closest("button")) return;
    const [ci, xi] = el.dataset.open.split(":").map(Number);
    openCredit(d.cards[ci], d.cards[ci].credits[xi]);
  });
  document.querySelectorAll("[data-track]").forEach((el) => el.onclick = () => {
    const [ci, xi] = el.dataset.track.split(":").map(Number);
    track(d.cards[ci], [d.cards[ci].credits[xi]]);
  });
  document.querySelectorAll("[data-trackall]").forEach((el) => el.onclick = () => {
    const c = d.cards[Number(el.dataset.trackall)];
    track(c, c.credits.filter((x) => !x.tracked));
  });
}


function myCards(d, parts) {
  d.cards.forEach((c, ci) => {
    const tracked = c.credits.filter((x) => x.tracked).length, untracked = c.credits.length - tracked;
    const fee = c.annual_fee || 0, top = Math.max(fee, c.year_value, c.used_this_year, 1);
    const worth = fee ? `<p class="muted" style="margin:4px 0 0">Used this year ${money(c.used_this_year)} of a ${money(fee)} fee ·
        up to ${money(c.year_value)} in credits a year</p>
      <div class="worth"><i style="width:${(100 * c.used_this_year) / top}%"></i><span class="fee" style="left:${(100 * fee) / top}%"></span></div>
      <p class="muted" style="margin:0 0 8px;font-size:12px">${c.used_this_year >= fee ? "✓ The credits you've used already cover the fee." :
        `${money(fee - c.used_this_year)} more in credits and the fee pays for itself. The line marks the fee.`}</p>` : "";
    const head = `${esc(c.card)}${c.last4 ? ` <span class="muted">••${esc(c.last4)}</span>` : ""}`;
    const body = worth +
      (c.credits.length ? c.credits.map((x, xi) => creditRow(c, x, ci, xi)).join("") :
        `<p class="muted">${c.needs_name ? "Tell Buddy which card this is to see its benefits." : c.checked ?
          `No statement credits on this card${c.annual_fee ? "" : " and no annual fee"}: its value is what it earns and the protections below.` :
          "Not looked up yet: say \"set up my card benefits\"."}</p>`) +
      (untracked > 1 ? `<p style="margin:10px 0 0"><button class="btn solid" data-trackall="${ci}">Track all ${untracked}</button></p>` : "") +
      (c.perks.length ? `<p class="muted" style="margin:12px 0 0">Perks</p><div class="perks">${c.perks.map((p) => `<span>${esc(p)}</span>`).join("")}</div>` : "") +
      (c.earn_rates.length ? `<p class="muted" style="margin:12px 0 2px">Earns</p><p style="margin:0;font-size:13px">${c.earn_rates.map(esc).join(" · ")}</p>` : "") +
      (c.checked ? `<p class="muted" style="margin:12px 0 0;font-size:12px">From the issuer's site, checked ${esc(c.checked)}${c.sources.length ?
        " · " + c.sources.map((u) => `<a href="${esc(u)}" target="_blank" rel="noopener">${esc(new URL(u).hostname.replace("www.", ""))}</a>`).join(" ") : ""}.
        Issuers change benefits; your card's terms win.</p>` : "");
    parts.push(card(head, body));
  });
}

function notice(text) {
  let n = $("notice");
  if (!n) { n = document.createElement("div"); n.id = "notice"; document.body.appendChild(n); }
  n.textContent = text;
  n.style.cssText = "position:fixed;left:16px;right:16px;bottom:18px;z-index:20;background:var(--text);color:var(--bg);" +
    "padding:12px 14px;border-radius:12px;font-size:14px;max-width:868px;margin:0 auto";
  clearTimeout(notice.t); notice.t = setTimeout(() => n.remove(), 4500);
}

async function track(c, credits, anniversary = "") {
  // Credits that reset on the card's anniversary need its month and day: asked in the panel, not a pop-up.
  if (!anniversary && credits.some((x) => x.resets === "anniversary")) {
    sheet(`<div class="crumbs">${esc(c.card)}</div><h2 style="margin:0 0 6px">When did you open this card?</h2>
      <p class="muted">${credits.filter((x) => x.resets === "anniversary").map((x) => esc(x.name)).join(", ")} reset on your card anniversary.</p>
      <p><input id="anniv" placeholder="MM-DD, e.g. 03-15" inputmode="numeric" maxlength="5"
        style="font:inherit;padding:8px 10px;border-radius:10px;border:1px solid var(--line);background:var(--card);color:var(--text)"></p>
      <p style="display:flex;gap:8px"><button class="btn solid" id="annivok">Track</button><button class="btn" id="annivskip">Skip those</button></p>`);
    $("annivok").onclick = () => {
      const v = $("anniv").value.trim();
      if (!/^\d{2}-\d{2}$/.test(v)) { notice("Month and day like 03-15."); return; }
      $("sheet").classList.remove("open"); track(c, credits, v);
    };
    $("annivskip").onclick = () => {
      $("sheet").classList.remove("open");
      const rest = credits.filter((x) => x.resets !== "anniversary");
      if (rest.length) track(c, rest, "-");
    };
    return;
  }
  try {
    for (const x of credits) {
      await api("/api/cards/action", { action: "track", card: c.card, name: x.name, anniversary: anniversary === "-" ? "" : anniversary });
    }
    notice(`Tracking ${credits.length} credit${credits.length === 1 ? "" : "s"}. Buddy reminds you before each runs out.`);
    await load();
  } catch (e) { notice(e.message); }
}

function sheet(html) {
  $("panel").innerHTML = `<button class="close" aria-label="Close">×</button>${html}`;
  $("panel").querySelector(".close").onclick = () => $("sheet").classList.remove("open");
  $("sheet").classList.add("open");
}
$("sheet").onclick = (e) => { if (e.target.id === "sheet") $("sheet").classList.remove("open"); };

async function openCredit(c, x) {
  const head = `<div class="crumbs">${esc(c.card)}</div><h2 style="margin:0 0 4px">${esc(x.name)}</h2>`;
  if (!x.tracked) {
    sheet(head + `<p class="big">${money(x.amount)} <span class="muted" style="font-size:15px;font-weight:400">${esc(PERIOD[x.period] || x.period)}</span></p>
      ${x.enrollment ? '<p><span class="chip2 warn">Enroll in the issuer\'s app first</span></p>' : ""}
      ${x.note ? `<p class="muted">${esc(x.note)}</p>` : ""}
      <p class="muted">Worth up to ${money(x.year_value)} a year. Track it and Buddy shows what's left, reminds you before it runs out,
      and finds good ways to use it.</p>
      <p><button class="btn solid" id="trackone">Track it</button></p>
      ${x.source ? `<p class="muted" style="font-size:12px">Source: <a href="${esc(x.source)}" target="_blank" rel="noopener">${esc(new URL(x.source).hostname)}</a></p>` : ""}`);
    $("trackone").onclick = () => { $("sheet").classList.remove("open"); track(c, [x]); };
    return;
  }
  sheet(head + '<p class="muted">Loading…</p>');
  let v;
  try { v = await api(`/api/cards/credit/${x.id}`); } catch (e) { sheet(head + `<p class="err">${esc(e.message)}</p>`); return; }
  const pct = v.amount ? Math.min(100, (100 * v.used) / v.amount) : 0;
  const list = (title, rows, fn) => rows && rows.length ? `<p class="muted" style="margin:14px 0 4px">${title}</p>` + rows.map(fn).join("") : "";
  const tx = (r) => `<div class="tx"><span>${esc(day(r.day))}</span><span>${esc(r.merchant || r.note || "")}<small>${esc(r.card ? "••" + r.card : "")}</small></span><b>${money(r.amount)}</b></div>`;
  const top = Math.max(v.amount, ...v.history.map((h) => h.used), 1);
  const deals = v.deals && v.deals.deals ? v.deals.deals : [];
  sheet(head + `
    <p class="big">${money(v.left)} <span class="muted" style="font-size:15px;font-weight:400">left of ${money(v.amount)} · ${v.days_left === 0 ? "ends today" : v.days_left + " days left"}</span></p>
    <span class="prog${v.left > 0 && v.days_left <= 14 ? " warn" : ""}" style="display:block;height:12px"><i style="width:${pct}%"></i></span>
    <p class="muted" style="margin:8px 0 0">This period: ${esc(day(v.starts))} to ${esc(day(v.ends))} (${esc(PERIOD[v.period] || v.period)})</p>
    ${v.enrollment ? '<p><span class="chip2 warn">Make sure you\'ve enrolled in the issuer\'s app</span></p>' : ""}
    ${v.maybe_pending ? `<p class="muted">You spent there this period; ${money(v.maybe_pending)} of credit may still be on its way.</p>` : ""}
    ${v.reminders.length ? `<p class="muted">Buddy reminds you on ${v.reminders.map(day).join(", ")}.</p>` : ""}
    ${list("Statement credits that counted", v.statement_credits, tx)}
    ${list("You marked as used", v.marked_used, tx)}
    ${list("Purchases there this period", v.purchases, tx)}
    ${v.history.length ? `<p class="muted" style="margin:14px 0 2px">Earlier periods</p><div class="hist">${v.history.slice().reverse().map((h) =>
      `<div><i style="height:${Math.max(3, (52 * h.used) / top)}px"></i>${money(h.used)}<br>${esc(day(h.from))}</div>`).join("")}</div>` : ""}
    ${deals.length ? list(v.kind === "dining" ? "Places near you it covers" : v.kind === "hotel" ? "Hotels it covers" : "Good ways to use it", deals,
      (d) => `<div class="save"><span><a href="${esc(d.url)}" target="_blank" rel="noopener">${esc(d.item)}</a><br><span class="muted">${esc(d.note || "")}</span></span><b>${money(d.price)}</b></div>`) : ""}
    <p style="margin-top:16px;display:flex;gap:8px;flex-wrap:wrap">
      ${v.left > 0 ? '<button class="btn solid" id="used">I used it</button>' : ""}
      <button class="btn" id="untrack">Stop tracking</button></p>
    ${v.source ? `<p class="muted" style="font-size:12px">Source: <a href="${esc(v.source)}" target="_blank" rel="noopener">${esc(new URL(v.source).hostname)}</a></p>` : ""}`);
  const act = async (body) => {
    try { const r = await api("/api/cards/action", body); $("sheet").classList.remove("open"); notice(r.text); await load(); }
    catch (e) { notice(e.message); }
  };
  if ($("used")) $("used").onclick = () => act({ action: "used", id: v.id });
  $("untrack").onclick = () => act({ action: "untrack", id: v.id });
}

if (!token) $("main").innerHTML = '<p class="err">Open this page from the Buddy app (☰ → Card benefits).</p>';
else load();
