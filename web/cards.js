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
  return `<div class="credit" data-open="${ci}:${xi}"><div class="nm"><b>${esc(x.name)}${chips}</b><small>${money(x.amount)} ${esc(meta)}</small></div>${right}</div>`;
}

function render() {
  const d = data, t = d.totals;
  $("label").textContent = d.cards.length ? `· ${d.cards.length} card${d.cards.length === 1 ? "" : "s"}` : "";
  const parts = [];
  if (!d.cards.length) {
    $("main").innerHTML = card("No cards yet", `<p class="muted">Connect your bank in Buddy, or tell Buddy which cards you have. Then say "set up my card benefits".</p>`);
    return;
  }
  parts.push(`<div class="kpis" style="margin-bottom:12px">
    <div class="kpi"><span>Credits a year</span><b>${money(t.year_value)}</b></div>
    <div class="kpi"><span>Annual fees</span><b>${money(t.fees)}</b></div>
    <div class="kpi"><span>Used this year</span><b style="color:var(--good)">${money(t.used_this_year)}</b></div>
    <div class="kpi"><span>Left this period</span><b>${money(t.left_this_period)}</b></div></div>`);
  if (d.questions && d.questions.length) parts.push(card("Which card is this?", d.questions.map((q) => `<p class="muted">${esc(q)}</p>`).join("") +
    `<p class="muted">Tell Buddy, e.g. "0457 is my Sapphire Preferred".</p>`));
  if (d.ending_soon.length) {
    parts.push(card("Use these soon", d.ending_soon.map((x) => {
      const ci = d.cards.findIndex((c) => c.card === x.card), xi = d.cards[ci].credits.findIndex((y) => y.id === x.id);
      return `<div class="credit" data-open="${ci}:${xi}"><div class="nm"><b>${esc(x.name)}</b><small>${esc(x.card)}</small></div>
        ${progress(x)}<span class="rt"><b style="color:var(--text)">${money(x.left)}</b> left<br>${x.days_left === 0 ? "today" : x.days_left + " days"}</span></div>`;
    }).join("")));
  }
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
        `<p class="muted">${c.needs_name ? "Tell Buddy which card this is to see its benefits." : "No published credits saved yet: say \"set up my card benefits\"."}</p>`) +
      (untracked > 1 ? `<p style="margin:10px 0 0"><button class="btn solid" data-trackall="${ci}">Track all ${untracked}</button></p>` : "") +
      (c.perks.length ? `<p class="muted" style="margin:12px 0 0">Perks</p><div class="perks">${c.perks.map((p) => `<span>${esc(p)}</span>`).join("")}</div>` : "") +
      (c.earn_rates.length ? `<p class="muted" style="margin:12px 0 2px">Earns</p><p style="margin:0;font-size:13px">${c.earn_rates.map(esc).join(" · ")}</p>` : "") +
      (c.checked ? `<p class="muted" style="margin:12px 0 0;font-size:12px">From the issuer's site, checked ${esc(c.checked)}${c.sources.length ?
        " · " + c.sources.map((u) => `<a href="${esc(u)}" target="_blank" rel="noopener">${esc(new URL(u).hostname.replace("www.", ""))}</a>`).join(" ") : ""}.
        Issuers change benefits; your card's terms win.</p>` : "");
    parts.push(card(head, body));
  });
  $("main").innerHTML = parts.join("");
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
