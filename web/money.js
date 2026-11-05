// Buddy money dashboard: one period at a time, every number from the server; tap anything to drill down.
// Category -> type -> shop -> each transaction; also by card or account, month, and transfer recipient.

const params = new URLSearchParams(location.search);
// The app's look (Settings → Look): light is the buddylabs.io website's style; remembered for next time.
if (params.get("theme")) storageSet("buddyTheme", params.get("theme") === "light" ? "light" : "default");
if ((params.get("theme") || storageGet("buddyTheme")) === "light") document.documentElement.dataset.theme = "light";
function storageGet(k) { try { return localStorage.getItem(k); } catch { return null; } }
function storageSet(k, v) { try { localStorage.setItem(k, v); } catch {} }
if (params.get("token")) storageSet("buddyToken", params.get("token"));
const token = params.get("token") || storageGet("buddyToken") || "";
if (params.get("token") && storageGet("buddyToken") === token) history.replaceState(null, "", location.pathname);

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const money = (v) => v == null ? "–" : (v < 0 ? "−" : "") +
  (Math.abs(v) >= 100 ? "$" + Math.round(Math.abs(v)).toLocaleString() : "$" + Math.abs(Number(v)).toFixed(2));
const COLORS = { needs: "var(--needs)", wants: "var(--wants)", fees: "var(--fees)", family: "var(--family)",
  sent: "var(--sent)", save: "var(--save)", left: "var(--left)" };
let data = null, period = storageGet("moneyPeriod") || "last_month";

async function api(path) {
  const res = await fetch(path, { headers: { Authorization: `Bearer ${token}` } });
  if (!res.ok) {
    let msg = res.status === 401 ? "This page needs your Buddy link: open it from the Buddy app." : `Error ${res.status}`;
    try { msg = (await res.json()).detail || msg; } catch {}
    throw new Error(msg);
  }
  return res.json();
}

async function load(p) {
  period = p; storageSet("moneyPeriod", p);
  $("main").innerHTML = '<p class="muted">Loading…</p>';
  try { data = await api(`/api/money/dashboard?period=${encodeURIComponent(p)}`); render(); }
  catch (e) { $("main").innerHTML = `<p class="err">${esc(e.message)}</p>`; }
}

function periodsBar() {
  const all = data ? data.periods.map((x) => [x.id, x.label]) : [["last_month", "Last month"]];
  if (period.startsWith("month:") && !all.some(([id]) => id === period)) all.unshift([period, data ? data.label : period]);
  $("periods").innerHTML = all.map(([id, label]) =>
    `<button class="chip${id === period ? " on" : ""}" data-p="${esc(id)}">${esc(label)}</button>`).join("");
  $("periods").querySelectorAll(".chip").forEach((b) => b.onclick = () => load(b.dataset.p));
}

function card(title, inner, note = "") {
  return `<section class="card"><h2>${esc(title)}</h2>${inner}${note ? `<p class="muted">${note}</p>` : ""}</section>`;
}

function barRow(name, amount, max, opts = {}) {
  const pct = max ? Math.max(2, (100 * amount) / max) : 0;
  const tick = opts.usual && max ? `<i class="tick" style="left:${Math.min(100, (100 * opts.usual) / max)}%"></i>` : "";
  const change = opts.change == null ? "" : `<span class="${opts.change > 10 ? "up" : opts.change < -10 ? "down" : "muted"}">${opts.change > 0 ? "+" : ""}${opts.change}%</span>`;
  return `<div class="row" ${opts.attrs || ""}><span class="name fixed">${esc(name)}</span>
    <span class="barwrap"><span class="bar" style="width:${pct}%;background:${opts.color || "var(--accent)"};display:block"></span>${tick}</span>
    <span class="amt">${money(amount)}</span>${change}</div>`;
}

function render() {
  const d = data;
  $("label").textContent = "· " + d.label;
  periodsBar();
  const t = d.transfers, parts = [];
  // KPIs
  parts.push(`<div class="kpis" style="margin-bottom:12px">
    <div class="kpi"><span>Came in</span><b>${money(d.income)}</b></div>
    <div class="kpi"><span>Spent</span><b>${money(d.spent)}</b></div>
    <div class="kpi"><span>Kept (with savings)</span><b>${money(d.left_over)}</b></div>
    <div class="kpi"><span>Saved</span><b>${d.savings_rate == null ? "–" : d.savings_rate + "%"}</b></div>
    <div class="kpi"><span>Could save a year</span><b style="color:var(--good)">${money(d.possible_yearly_savings)}</b></div></div>`);
  // all the connected accounts and net worth
  const acc = d.accounts;
  if (acc && acc.accounts) {
    const g = acc.groups, tt = acc.totals;
    const row = (x, right) => `<div class="row" style="cursor:default"><span class="name">${esc(x.name)}${x.last4 ? " ••" + esc(x.last4) : ""}
      <small class="muted"> ${esc(x.institution || "")}</small></span><span class="amt">${right}</span></div>`;
    const sect = (title, xs, fn) => xs && xs.length ? `<p class="muted" style="margin:10px 0 2px">${title}</p>` + xs.map(fn).join("") : "";
    parts.push(card(`Your accounts · net worth ${money(tt.net_worth)}`,
      `<div class="kpis" style="margin-bottom:6px">
        <div class="kpi"><span>Cash</span><b>${money(tt.cash)}</b></div>
        <div class="kpi"><span>Investments</span><b>${money(tt.investments)}</b></div>
        <div class="kpi"><span>Cards owed</span><b>${money(tt.cards_owed)}</b></div>
        <div class="kpi"><span>Loans owed</span><b>${money(tt.loans_owed)}</b></div></div>` +
      sect("Cash", g.cash, (x) => row(x, money(x.balance))) +
      sect("Investments", g.investments, (x) => row(x, money(x.balance))) +
      sect("Cards", g.cards, (x) => row(x, money(x.owed) + " owed")) +
      sect("Mortgages and loans", g.loans, (x) => row(x, money(x.owed) + " owed") +
        ((x.rate != null || x.next_payment) ? `<p class="muted" style="margin:-4px 0 6px">${[x.rate != null ? x.rate + "% " + esc(x.rate_type || "") : "",
          x.next_payment ? "next payment " + money(x.next_payment) + (x.next_due ? " on " + esc(x.next_due) : "") : ""].filter(Boolean).join(" · ")}</p>` : "")) +
      (acc.note ? `<p class="muted" style="margin-top:8px">${esc(acc.note)}</p>` : "")));
  }
  // trade ideas being watched and trades being tracked (ideas, not advice)
  const mk = d.markets;
  if (mk && (mk.setups.length || mk.positions.length)) parts.push(marketsCard(mk));
  // credit score and anything new on the credit report
  const cr = d.credit;
  if (cr) parts.push(creditCard(cr));
  // cash flow of the accounts the pay lands in
  const cf = d.cash_flow;
  if (cf && (cf.in.total || cf.out.total)) {
    const line = (label, v, strong) => v ? `<div class="row" style="cursor:default"><span class="name">${strong ? "<b>" + esc(label) + "</b>" : esc(label)}</span>
      <span class="amt">${strong ? "<b>" + money(v) + "</b>" : money(v)}</span></div>` : "";
    const o = cf.out;
    parts.push(card(`Cash flow · ${cf.left >= 0 ? "kept" : "short"} ${money(Math.abs(cf.left))}`,
      `<p class="muted" style="margin:0 0 2px">Came in</p>` + line("Salary", cf.in.salary) + line("Other income", cf.in.other) +
      line("Total in", cf.in.total, true) +
      `<p class="muted" style="margin:10px 0 2px">Went out</p>` +
      line("Credit card bills", o.card_payments) + line("Housing (mortgage, rent)", o.housing) +
      line("Family and money sent abroad", o.family) + line("Sent to people (Zelle and others)", o.people) +
      line("To savings and investments", o.savings) + line("Debit card and account spending", o.debit_spending) +
      line("Total out", o.total, true) +
      (cf.card_bills && cf.card_bills.length ? `<p class="muted" style="margin-top:8px">Card bills paid: ${cf.card_bills.map((c) =>
        esc(c.card) + " " + money(c.paid)).join(" · ")}</p>` : "")));
  }
  // money flow
  const flow = [["Needs", d.split.needs, "needs"], ["Wants", d.split.wants, "wants"], ["Fees", d.split.fees, "fees"],
    ["Family support", t.family, "family"], ["Sent to people and abroad", t.to_people + t.abroad + t.other, "sent"],
    ["Savings", t.to_savings, "save"]];
  const used = flow.reduce((s, f) => s + (f[1] || 0), 0);
  const total = d.income && d.income > used ? d.income : used;
  if (d.income && d.income > used) flow.push(["Not spent", d.income - used, "left"]);
  parts.push(card("Where your money went", `<div class="stack">${flow.filter((f) => f[1] > 0).map((f) =>
      `<span title="${esc(f[0])}" style="width:${(100 * f[1]) / total}%;background:${COLORS[f[2]]}"></span>`).join("")}</div>
    <div class="legend">${flow.filter((f) => f[1] > 0).map((f) => `<span><i style="background:${COLORS[f[2]]}"></i>${esc(f[0])} ${money(f[1])}</span>`).join("")}</div>`));
  // categories
  const maxCat = Math.max(...d.categories.map((c) => Math.max(c.total, c.usual_per_month || 0)), 1);
  parts.push(card("Spending by category", d.categories.map((c, i) => barRow(c.label, c.total, maxCat,
      { usual: c.usual_per_month, change: c.change, color: COLORS[c.group] || "var(--accent)", attrs: `data-cat="${i}"` })).join("")
      || '<p class="muted">No spending in this period.</p>', "The thin mark = your usual month. Tap a category to drill down."));
  // trend
  parts.push(card("Six-month trend", trendSvg(d.trend), "Tap a month to open it."));
  // cards and accounts
  const maxAcc = Math.max(...d.cards_used.map((c) => c.total), 1);
  parts.push(card("By card and account", d.cards_used.map((c, i) => barRow(c.card, c.total, maxAcc, { attrs: `data-acc="${i}"` })).join("")
      || '<p class="muted">No card spending.</p>',
    d.house_by_card.length ? "House expenses paid with: " + d.house_by_card.map(([k, v]) => `${esc(k)} ${money(v)}`).join(", ") : ""));
  // transfers
  if (t.recipients.length || t.to_savings) {
    const kinds = { person: "to a person", abroad: "abroad", family: "family support", other: "transfer" };
    parts.push(card("Transfers", t.recipients.map((x, i) => `<div class="row" data-rec="${i}"><span class="name">${esc(x.to)}
        ${x.note ? `<span class="muted"> · ${esc(x.note)}</span>` : ""}</span><span class="tag">${esc(kinds[x.kind] || x.kind)}</span>
        <span class="amt">${money(x.total)}</span></div>`).join("") +
      (t.to_savings ? `<div class="row" data-sav="1"><span class="name">Your savings and investments</span><span class="tag">savings</span><span class="amt">${money(t.to_savings)}</span></div>` : ""),
      t.questions.length ? "Buddy asks: " + t.questions.map(esc).join(" ") : ""));
  }
  // subscriptions
  const subs = d.subscription_tracker;
  parts.push(card(`Subscriptions · ${money(d.subscriptions.monthly)} a month`, subs.map((s) => `<div class="row" data-merchant="${esc(s.service)}">
      <span class="name">${esc(s.service)}${["new", "unsure"].includes(s.status) ? ' <span class="tag">to decide</span>' : ""}</span>
      <span class="muted">next ${esc(s.next)}</span><span class="amt">${money(s.amount)}</span></div>`).join("") || '<p class="muted">None found.</p>',
    d.subscriptions.price_up.length ? "Price rises: " + d.subscriptions.price_up.map(esc).join(", ") : ""));
  // waste and ways to save
  if (d.waste.length) parts.push(card("Waste and leaks", d.waste.map((w) => `<div class="row"><span class="name" style="white-space:normal">${esc(w.what)}
      <span class="muted"> · ${esc(w.why)}</span></span><span class="amt" style="color:var(--bad)">${money(w.amount)}</span></div>`).join("")));
  if (d.opportunities.length) parts.push(card(`Ways to save · ${money(d.possible_yearly_savings)} a year`,
    d.opportunities.map((o) => `<div class="save"><b>${money(o.yearly)}</b><span>${esc(o.do)}</span></div>`).join("")));
  // budget
  const maxB = Math.max(...d.budget.map((b) => Math.max(b.now, b.budget)), 1);
  parts.push(card("This month against a suggested budget", d.budget.map((b) =>
      barRow(b.label, b.now, maxB, { usual: b.budget, color: b.now > b.budget * 1.05 ? "var(--warn)" : "var(--good)" })).join(""),
    "The thin mark = suggested budget. " + esc(d.rule)));
  $("main").innerHTML = parts.join("");
  // drill-down handlers
  document.querySelectorAll("[data-cat]").forEach((el) => el.onclick = () => {
    const c = d.categories[+el.dataset.cat]; drill([{ label: c.label, q: { category: c.category } }]);
  });
  document.querySelectorAll("[data-acc]").forEach((el) => el.onclick = () => {
    const c = d.cards_used[+el.dataset.acc]; drill([{ label: c.card, q: { card: c.key } }]);
  });
  document.querySelectorAll("[data-rec]").forEach((el) => el.onclick = () => {
    const x = t.recipients[+el.dataset.rec]; drill([{ label: x.to, q: { recipient: x.to }, transfers: true }]);
  });
  document.querySelectorAll("[data-sav]").forEach((el) => el.onclick = () =>
    drill([{ label: "Savings and investments", q: { transfer: "own" }, transfers: true }]));
  document.querySelectorAll("[data-merchant]").forEach((el) => el.onclick = () =>
    drill([{ label: el.dataset.merchant, q: { merchant: el.dataset.merchant } }]));
  document.querySelectorAll("[data-month]").forEach((el) => el.onclick = () => load(el.dataset.month));
}

function marketsCard(mk) {
  const px = (v) => v >= 1 ? Number(v).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 }) : Number(v).toPrecision(3);
  const pos = mk.positions.map((p) => `<div class="row" style="cursor:default"><span class="name" style="white-space:normal">
    <b>${esc(p.symbol)}</b> <small class="muted">bought ${esc(p.opened)}</small><br>
    <small>from ${px(p.entry)} · stop ${px(p.stop)} · targets ${px(p.t1)} / ${px(p.t2)}</small></span>
    <span class="amt"><small class="muted">tracking</small></span></div>`).join("");
  const ideas = mk.setups.map((s) => `<div class="row" style="cursor:default"><span class="name" style="white-space:normal">
    <b>${esc(s.symbol)}</b> <small class="muted">${esc((s.data && s.data.label) || s.pattern)}${s.data && s.data.sentiment ? " · chatter " + esc(s.data.sentiment) : ""}</small><br>
    <small>entry ${px(s.entry)} · stop ${px(s.stop)} · targets ${px(s.t1)} / ${px(s.t2)}</small></span>
    <span class="amt"><small class="${s.status === "triggered" ? "down" : "muted"}">${s.status === "triggered" ? "triggered" : "watching"}</small></span></div>`).join("");
  return card("Trade ideas", (pos ? `<p class="muted" style="margin:0 0 2px">Your trades</p>` + pos : "") +
    (ideas ? `<p class="muted" style="margin:10px 0 2px">Setups being watched</p>` + ideas : ""),
    esc(mk.note) + " The full plan for each is in Reports (Market scan).");
}

function creditCard(cr) {
  const band = (v) => v >= 800 ? ["Exceptional", "var(--good)"] : v >= 740 ? ["Very good", "var(--good)"] :
    v >= 670 ? ["Good", "var(--accent)"] : v >= 580 ? ["Fair", "var(--warn)"] : ["Poor", "var(--bad)"];
  const scores = cr.latest.map((s) => {
    const [name, color] = band(s.score), pct = Math.max(0, Math.min(100, (100 * (s.score - 300)) / 550));
    return `<div class="row" style="cursor:default"><span class="name">${esc(s.bureau || "Score")}
      <small class="muted"> ${esc([s.model, s.day].filter(Boolean).join(" · "))}</small></span>
      <span class="barwrap"><span class="bar" style="width:${pct}%;background:${color};display:block"></span></span>
      <span class="amt"><b>${s.score}</b> <small style="color:${color}">${name}</small></span></div>`;
  }).join("");
  const h = cr.history;
  let spark = "";
  if (h.length > 1) {
    const w = 320, ht = 60, lo = Math.min(...h.map((x) => x.score)) - 10, hi = Math.max(...h.map((x) => x.score)) + 10;
    const pts = h.map((x, i) => `${(i * w) / (h.length - 1)},${ht - ((x.score - lo) * ht) / (hi - lo || 1)}`).join(" ");
    spark = `<svg viewBox="0 0 ${w} ${ht}" style="width:100%;height:60px;margin:6px 0" role="img" aria-label="Score history">
      <polyline points="${pts}" fill="none" stroke="var(--accent)" stroke-width="2"/></svg>`;
  }
  const flag = (e) => e.status === "mine" ? "✓ you" : e.status === "not_mine" ? "⚠ not you" :
    ["hard_inquiry", "new_account", "late_payment", "collection", "public_record", "address_change", "fraud_alert"].includes(e.kind) ? "check" : "";
  const events = cr.events.map((e) => `<div class="row" style="cursor:default"><span class="name" style="white-space:normal">
    <b>${esc(e.label)}</b>${e.lender ? " · " + esc(e.lender) : ""}<small class="muted"> ${esc([e.bureau, e.day].filter(Boolean).join(" · "))}</small></span>
    <span class="amt"><small class="${e.status === "not_mine" ? "up" : "muted"}">${flag(e)}</small></span></div>`).join("");
  const util = cr.utilization == null ? "" : `<div class="kpis" style="margin:8px 0"><div class="kpi"><span>Card limits used</span>
    <b style="color:${cr.utilization < 10 ? "var(--good)" : cr.utilization < 30 ? "var(--accent)" : "var(--bad)"}">${cr.utilization}%</b></div>
    ${cr.cards.slice(0, 3).map((c) => `<div class="kpi"><span>${esc(c.name)}</span><b>${c.used}%</b></div>`).join("")}</div>`;
  const empty = !cr.latest.length && !cr.events.length;
  return card(cr.latest.length ? `Credit · ${cr.latest[0].score}` : "Credit",
    (empty ? `<p class="muted">No score yet. Turn on the free score emails or notifications in an app you already have
      (Credit Karma, Chase Credit Journey, Amex MyCredit Guide), or tell Buddy your score.</p>` : scores + spark) + util +
    (events ? `<p class="muted" style="margin:10px 0 2px">Recent changes on your report</p>` + events : ""),
    "Under 30% of your limits used helps the score; under 10% is best. " + esc(cr.how));
}

function trendSvg(trend) {
  const w = 340, h = 150, pad = 26, n = trend.length, bw = (w - pad) / n - 10;
  const max = Math.max(...trend.map((t) => Math.max(t.spent, t.income || 0)), 1);
  const y = (v) => h - 20 - ((h - 34) * v) / max;
  const bars = trend.map((t, i) => {
    const x = pad + i * ((w - pad) / n) + 5, on = t.id === period;
    return `<g data-month="${esc(t.id)}" style="cursor:pointer"><rect x="${x}" y="${y(t.spent)}" width="${bw}" height="${h - 20 - y(t.spent)}"
      rx="4" fill="${on ? "var(--accent)" : "var(--spent)"}"/><text x="${x + bw / 2}" y="${h - 6}" text-anchor="middle">${esc(t.month.slice(0, 3))}</text>
      <rect x="${x - 4}" y="0" width="${bw + 8}" height="${h}" fill="transparent"/></g>`;
  }).join("");
  const pts = trend.map((t, i) => t.income == null ? null : `${pad + i * ((w - pad) / n) + 5 + bw / 2},${y(t.income)}`).filter(Boolean);
  const line = pts.length > 1 ? `<polyline points="${pts.join(" ")}" fill="none" stroke="var(--good)" stroke-width="2"/>` : "";
  const dots = pts.map((p) => `<circle cx="${p.split(",")[0]}" cy="${p.split(",")[1]}" r="3" fill="var(--good)"/>`).join("");
  return `<svg viewBox="0 0 ${w} ${h}" width="100%" role="img" aria-label="Spending by month">
    <text x="0" y="12">${money(max)}</text>${bars}${line}${dots}</svg>
    <div class="legend"><span><i style="background:var(--spent)"></i>Spent</span><span><i style="background:var(--good)"></i>Came in</span></div>`;
}

// --- drill-down: a stack of levels (category -> type -> shop -> transactions) -------------------------------------
async function drill(stack) {
  $("sheet").classList.add("open");
  const level = stack[stack.length - 1], q = Object.assign({}, ...stack.map((s) => s.q));
  const qs = new URLSearchParams({ start: data.from, end: data.to, ...q });
  const crumbs = [`<a data-depth="-1">${esc(data.label)}</a>`].concat(stack.map((s, i) =>
    i === stack.length - 1 ? `<b>${esc(s.label)}</b>` : `<a data-depth="${i}">${esc(s.label)}</a>`)).join(" › ");
  $("panel").innerHTML = `<button class="close" aria-label="Close">×</button><div class="crumbs">${crumbs}</div><p class="muted">Loading…</p>`;
  wirePanel(stack);
  let got;
  try { got = await api(`/api/money/transactions?${qs}`); }
  catch (e) { $("panel").querySelector("p").outerHTML = `<p class="err">${esc(e.message)}</p>`; return; }
  const rows = got.rows, parts = [`<h2>${esc(level.label)} · ${money(got.total)} <span class="muted">(${got.count})</span></h2>`];
  if (!stack.some((s) => s.transfers)) {
    const group = (key) => { const m = {}; rows.forEach((r) => { const k = r[key] || "Other"; m[k] = (m[k] || 0) + r.amount; });
      return Object.entries(m).sort((a, b) => b[1] - a[1]); };
    // the next level down: by type, then by place; a level with only one group is skipped
    const keys = [];
    if (q.category && !q.detail) keys.push("detail");
    if (!q.merchant) keys.push("what");
    for (const key of keys) {
      const groups = group(key);
      if (groups.length > 1) {
        const max = groups[0][1];
        parts.push(`<p class="muted">${key === "detail" ? "By type" : "By place"} · tap to go deeper</p>` +
          groups.map(([k, v], i) => barRow(k, v, max, { attrs: `data-next="${i}"` })).join(""));
        level._groups = groups; level._key = key;
        break;
      }
    }
  }
  parts.push(`<p class="muted" style="margin-top:14px">Transactions</p>` + rows.map((r) => `<div class="tx"><span class="muted">${esc(r.day.slice(5))}</span>
    <span>${esc(r.what)}<small>${esc([r.detail || r.to, r.account].filter(Boolean).join(" · "))}</small></span>
    <span class="amt">${money(r.amount)}</span></div>`).join("") + (got.count > rows.length ? `<p class="muted">Showing ${rows.length} of ${got.count}.</p>` : ""));
  $("panel").innerHTML = `<button class="close" aria-label="Close">×</button><div class="crumbs">${crumbs}</div>` + parts.join("");
  wirePanel(stack);
  $("panel").querySelectorAll("[data-next]").forEach((el) => el.onclick = () => {
    const [k] = level._groups[+el.dataset.next];
    drill(stack.concat([{ label: k, q: { [level._key === "what" ? "merchant" : "detail"]: k } }]));
  });
}

function wirePanel(stack) {
  $("panel").querySelector(".close").onclick = () => $("sheet").classList.remove("open");
  $("panel").querySelectorAll("[data-depth]").forEach((a) => a.onclick = () => {
    const depth = +a.dataset.depth;
    if (depth < 0) $("sheet").classList.remove("open"); else drill(stack.slice(0, depth + 1));
  });
}
$("sheet").onclick = (e) => { if (e.target === $("sheet")) $("sheet").classList.remove("open"); };

if (!token) $("main").innerHTML = '<p class="err">Open this page from the Buddy app (☰ → Money dashboard).</p>';
else load(period);
