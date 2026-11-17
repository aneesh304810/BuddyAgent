// Buddy Intelligence Platform: emerging technologies by sector with the stage the evidence supports, which
// technologies are converging across sectors, anything forming, and the sources. Evidence and interpretation are
// kept apart: every record shows what kind of evidence it is. Every piece of text is escaped.

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
const TABS = [["overview", "Overview"], ["convergence", "Convergence"], ["sectors", "Sectors"], ["sources", "Sources"], ["predict", "Predictions"], ["strategies", "Strategies"]];
let predictData = null, predictBusy = false, repoData = null, repoBusy = false, repoFamily = "";
const KIND = { research: "papers", trial: "clinical studies", filing: "company filings", award: "federal awards", grant: "research grants", funding: "fundraising notices", code: "code projects", news: "news reports", discussion: "stories" };
let data = null, tab = TABS.some(([k]) => k === storageGet("intelTab")) ? storageGet("intelTab") : "overview";

async function api(path, body) {
  const res = await fetch(path, body ? { method: "POST", headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" }, body: JSON.stringify(body) }
    : { headers: { Authorization: `Bearer ${token}` } });
  const out = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(out.detail || (res.status === 401 ? "Open Intelligence from the Buddy app." : `Error ${res.status}`));
  return out;
}
function notice(text) {
  const n = $("notice"); n.textContent = text; n.style.display = "block";
  clearTimeout(notice.t); notice.t = setTimeout(() => { n.style.display = "none"; }, 5000);
}
const card = (title, body, foot) => `<section class="card"><h2>${title}</h2>${body}${foot ? `<p class="note">${foot}</p>` : ""}</section>`;
const kpi = (value, label) => `<div class="kpi"><b>${value}</b><span>${esc(label)}</span></div>`;
const day = (iso) => { const d = new Date(String(iso).length === 10 ? iso + "T12:00:00" : iso); return isNaN(d) ? "" : d.toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" }); };
const growth = (g) => g == null ? "new" : `${g > 0 ? "▲" : g < 0 ? "▼" : "•"} ${Math.abs(Math.round(g * 100))}%`;
const usd = (v) => v >= 1e9 ? `$${(v / 1e9).toFixed(1)}B` : v >= 1e6 ? `$${(v / 1e6).toFixed(1)}M` : `$${Math.round(v).toLocaleString()}`;
// The stage is a step on an ordered scale: shown as filled steps with its name.
function stage(name) {
  const i = Math.max(0, data.stages.indexOf(name));
  const steps = data.stages.slice(1).map((_, n) => `<i class="${n < i ? "on" : ""}"></i>`).join("");
  return `<span class="stage" title="Step ${i} of ${data.stages.length - 1}"><span class="steps" aria-hidden="true">${steps}</span>${esc(name)}</span>`;
}
const kinds = (k) => Object.entries(k || {}).map(([name, n]) => `<span class="chipk">${n} ${esc(KIND[name] || name)}</span>`).join("");
function evidence(e) {
  const url = safe(e.url), title = url ? `<a href="${esc(url)}" target="_blank" rel="noopener noreferrer">${esc(e.title)}</a>` : esc(e.title);
  const extra = e.kind === "award" && e.amount ? ` · ${usd(e.amount)}` : "";
  return `<div class="ev"><span class="chipk" title="${esc(e.means)}">${esc(e.evidence)}</span> ${title}
    <small>${esc(e.source)} · ${esc(day(e.day))}${e.who ? " · " + esc(e.who) : ""}${extra}</small></div>`;
}

function tabs() {
  const n = { convergence: data.convergences.length };
  $("tabs").innerHTML = TABS.map(([k, label]) => `<button class="chip${k === tab ? " on" : ""}" role="tab" aria-selected="${k === tab}" data-tab="${k}">${label}${n[k] != null ? ` (${n[k]})` : ""}</button>`).join("");
  document.querySelectorAll("[data-tab]").forEach((b) => b.onclick = () => { tab = b.dataset.tab; storageSet("intelTab", tab); render(); });
}

function convergence(c, opened) {
  const ai = c.interpretation ? `<div class="ai"><span class="lbl">AI interpretation, not evidence</span>
      <b>${esc(c.interpretation.name)}</b><br>${esc(c.interpretation.how)}
      ${c.interpretation.problem ? `<br><span class="muted">Addresses: ${esc(c.interpretation.problem)}</span>` : ""}
      ${c.interpretation.watch ? `<br><span class="muted">Watch for: ${esc(c.interpretation.watch)}</span>` : ""}</div>` : "";
  return `<div class="conv"><h3>${esc(c.a)} × ${esc(c.b)}</h3>
    <div class="muted">${esc(c.sectors.join(" + "))}</div>
    <div class="kinds">${stage(c.stage)}<span class="num">${esc(c.signals_90_days)} shared records in 90 days · ${esc(growth(c.growth))}</span></div>
    <div class="muted">Why this stage: ${esc(c.why)}.</div><div class="kinds">${kinds(c.kinds)}</div>${ai}
    ${c.companies.length ? `<div class="muted">In the records: ${esc(c.companies.slice(0, 8).join(", "))}</div>` : ""}
    <details${opened ? " open" : ""}><summary>The records (${c.evidence.length} newest)</summary>${c.evidence.map(evidence).join("")}</details></div>`;
}
const techRow = (t) => `<button class="rowt" data-tech="${esc(t.id)}"><b>${esc(t.name)}</b><span class="num">${esc(t.signals_90_days)} · ${esc(growth(t.growth))}</span>
  <small>${stage(t.stage)}</small></button>`;

function overview() {
  const on = data.sectors.filter((s) => s.on), off = data.sectors.filter((s) => !s.on);
  const forming = (data.forming || []).map((f) => `<div class="conv"><h3>${esc(f.name)}</h3><div class="muted">${esc(f.sector_name)}</div>
    <div class="muted">Rising together (30 days before to the last 30): ${esc(f.rising.join("; "))}</div></div>`).join("");
  const moving = off.filter((s) => s.movement && s.movement.before != null && s.movement.now >= 8 && s.movement.now >= 1.5 * Math.max(s.movement.before, 1));
  const movers = moving.map((s) => `<div class="sector" style="padding:8px 0;border-top:1px solid var(--line)"><span><b>${esc(s.name)}</b><br>
    <span class="muted">${esc(s.movement.now)} recent filings and stories, against ${esc(s.movement.before)} the week before</span></span>
    <button class="btn solid" data-sector="${esc(s.id)}" data-on="1">Follow</button></div>`).join("");
  const scanned = data.last_run ? `Last collected ${esc(day(data.last_run))} · ${esc(data.new_last_run)} new records` : "Not collected yet. It runs every night; tap Collect now to start.";
  return `<section class="card"><div class="sector"><b>Buddy Intelligence Platform</b><button class="btn solid" id="scan">Collect now</button></div>
      <p class="note">${scanned}. Following ${on.map((s) => esc(s.name)).join(", ")}.</p></section>
    <div class="kpis" style="margin-bottom:12px">${kpi(Number(data.signals_total).toLocaleString(), "public records")}${kpi(data.technologies.length, "technologies followed")}
      ${kpi(data.convergences.length, "convergences")}${kpi((data.forming || []).length, "forming now")}</div>
    ${movers ? card("A lot is moving in sectors you don't follow", movers, "From the light weekly check on sectors that are switched off. Follow one and Buddy collects its full evidence from tonight. A rising count of records is not a judgement that there is an opportunity.") : ""}
    ${forming ? card("An industry may be forming", forming, "Three or more independent kinds of evidence rising at the same time. Evidence moving together, not a forecast.") : ""}
    ${card("Strongest convergences", data.convergences.length ? data.convergences.slice(0, 4).map((c) => convergence(c)).join("")
        : `<p class="muted">None yet. A convergence appears when technologies from two sectors keep showing up in the same records.</p>`,
      "Two technologies from different sectors that share public records, ranked by how many, how many kinds, and whether it is growing.")}
    ${card("Technologies by evidence", data.technologies.filter((t) => t.signals_90_days).slice(0, 12).map(techRow).join("") || `<p class="muted">No records yet.</p>`,
      "Records in the last 90 days and the change against the 90 before. Tap one for its evidence.")}
    <p class="note">${esc(data.note)}</p>`;
}
function sectors() {
  return data.sectors.map((s) => {
    const mine = data.technologies.filter((t) => t.sector === s.id);
    const move = !s.on && s.movement ? `Weekly check: ${s.movement.now} recent filings and stories${s.movement.before != null ? `, ${s.movement.before} the week before` : ""}.` : "";
    return `<section class="card"><div class="sector"><h2 style="margin:0">${esc(s.name)}</h2>
        <button class="btn${s.on ? "" : " solid"}" data-sector="${esc(s.id)}" data-on="${s.on ? "0" : "1"}">${s.on ? "Following" : "Follow"}</button></div>
      <p class="muted" style="margin:4px 0 6px">${s.on ? `${esc(s.signals_90_days)} records in 90 days across ${esc(s.technologies)} technologies` : `Not followed: ${esc(s.technologies)} technologies defined. ${esc(move)}`}</p>
      ${s.on ? mine.map(techRow).join("") : ""}</section>`;
  }).join("");
}
function sources() {
  return card("Where the evidence comes from", data.sources.map((s) => `<div class="guard"><span class="mark" style="color:var(--${s.state === "working" ? "sev-good" : s.state === "failing" ? "sev-high" : "sev-low"})">${s.state === "working" ? "✓" : s.state === "failing" ? "✗" : "•"}</span>
      <span><b>${esc(s.title)}</b> <span class="chipk">${esc(s.evidence)}</span><br><span class="muted">${esc(s.means)}.</span><br>
      <span class="muted">${s.state === "working" ? `Working · ${esc(s.found_last_run)} records read last run` : s.state === "failing" ? `Failing: ${esc(s.detail)}` : "Not run yet (its sectors may be switched off)"}</span></span></div>`).join(""),
    "All free, official or first-hand, and read through Buddy Connect. Only a technology's search phrase is sent. No paid databases, nothing scraped.");
}

// --- Strategies: the repository of collected investment strategies, by family, with Buddy's own test of each
async function loadRepo(body) {
  if (repoBusy) return;
  repoBusy = true;
  try { const out = await api("/api/intel/strategies", body); repoData = out; if (out.text) notice(out.text); }
  catch (e) { if (repoData) notice(e.message); else repoData = { failed: e.message }; }
  repoBusy = false;
  if (tab === "strategies") render();
}
const share = (v) => v == null ? "–" : `${v > 0 ? "+" : ""}${(v * 100).toFixed(1)}%`;
// Investment Intelligence: one engine, six sections. Discover (find strategies), Library (what is held), Test
// (replay on past prices), Rank, Paper trading, Monitor (is it doing live what it did in its test).
const SECTIONS = [["discover", "Discover"], ["library", "Library"], ["test", "Test"], ["rank", "Rank"], ["paper", "Paper trading"], ["monitor", "Monitor"]];
let labData = null, researchData = null, deskBusy = false,
  section = SECTIONS.some(([k]) => k === storageGet("intelSection")) ? storageGet("intelSection") : "library";
async function loadDesk() {
  if (deskBusy) return;
  deskBusy = true;
  try { labData = (await api("/api/invest/lab")).lab; } catch (e) { labData = { failed: e.message, added: [], strategies: [] }; }
  try { const r = await api("/api/invest/research"); researchData = { research: r.research, benchmarks: r.benchmarks }; }
  catch (e) { researchData = { failed: e.message }; }
  deskBusy = false;
  if (tab === "strategies") render();
}
// One call for every button in the engine: sends it, takes back whichever parts changed, and says what happened.
async function desk(path, body, button, busyText) {
  if (button) { button.disabled = true; if (busyText) button.textContent = busyText; }
  try {
    const out = await api(path, body);
    if (out.lab) labData = out.lab;
    if (out.research) researchData = { research: out.research, benchmarks: out.benchmarks };
    if (out.strategies && out.families) repoData = out;
    else { try { repoData = await api("/api/intel/strategies"); } catch {} }
    if (out.text || out.said) notice(out.text || out.said);
    else if (out.ran) notice(out.ran.note || "The lab ran.");
  } catch (e) { notice(e.message); }
  render();
}
const MARK = { passed: ["✓", "sev-good"], failed: ["✕", "sev-high"] };
const score = (h, label) => h && h.trades ? `<div class="kpis" style="margin:6px 0">${kpi(share(h.yearly_return), label + ": return a year")}${kpi(esc(h.sharpe ?? "–"), "Sharpe ratio")}
    ${kpi(share(-h.worst_fall), "worst fall")}${kpi(share(h.average_vs_market), "a trade against the market")}</div>
    <p class="muted" style="margin:0">${esc(h.trades)} trades from ${esc(h.from)} to ${esc(h.to)}, ${esc(h.won)} made money · ${esc(h.trades_a_year)} trades a year, held ${esc(h.days_held)} days on average · $${esc(Number(h.cost_paid).toLocaleString())} of costs taken off · book of $${esc(Number(h.book).toLocaleString())}</p>` : "";

function discoverSection() {
  const r = (researchData || {}).research, c = repoData.counts;
  const item = (x) => `<div class="guard"><span style="min-width:0;flex:1"><b>${safe(x.url) ? `<a href="${safe(x.url)}" target="_blank" rel="noopener">${esc(x.title)}</a>` : esc(x.title)}</b><br>
      <span class="muted">${esc(x.source)} · ${esc(x.day)}${x.licence ? " · " + esc(x.licence) : ""}</span><br>
      <span class="muted">${esc((x.summary || "").slice(0, 240))}${(x.summary || "").length > 240 ? "…" : ""}</span><br>
      ${x.status === "new" ? `<button class="btn" data-research="read" data-id="${esc(x.id)}">Read into a rule</button> <button class="btn" data-research="dismiss" data-id="${esc(x.id)}">Dismiss</button>`
        : `<span class="muted">${x.rule_key ? "Read: the rule is in the Library" : "Read: " + esc(x.note || "no testable rule found")}</span>`}</span></div>`;
  const queue = !r ? `<p class="${(researchData || {}).failed ? "err" : "muted"}">${esc((researchData || {}).failed || "Loading…")}</p>`
    : ((r.new || []).map(item).join("") || `<p class="muted">Nothing waiting. Buddy searches for new papers every Sunday.</p>`) +
      ((r.read || []).length ? `<details><summary>Already read (${r.read.length})</summary>${r.read.map(item).join("")}</details>` : "");
  const starts = r && (r.starting_points || []).length ? `<p class="muted" style="margin:8px 0 4px">Starting points (public, with a stated licence):</p><p style="display:flex;flex-wrap:wrap;gap:6px;margin:0">`
    + r.starting_points.map((x) => `<button class="chip" data-start="${esc(x.url)}">${esc(x.title)}</button>`).join("") + `</p>` : "";
  return card(`Research queue (${esc(c.queued)})`, queue, r ? esc(r.note || "") + (r.last_search ? " Last search: " + esc(r.last_search) + "." : "") : "") +
    card("Find more", `<p><button class="btn solid" id="repo-discover">Read the public list of strategy papers</button> <button class="btn" id="research-search">Search for new papers now</button></p>
      ${repoData.last_discovery ? `<p class="muted">Last read of the paper list: ${esc(repoData.last_discovery)}.</p>` : ""}
      <p class="muted" style="margin-bottom:4px">A public GitHub file or folder (the repository must state a licence):</p>
      <div style="display:flex;gap:8px;flex-wrap:wrap"><input id="research-url" placeholder="https://github.com/owner/name/tree/main/folder" aria-label="GitHub link" style="flex:1;min-width:220px"><button class="btn" id="research-github">Add from GitHub</button></div>${starts}`,
      "Only sections that daily prices can test are read from the paper list. A paper becomes a rule only when you pick it.") +
    card("Add a strategy from a document", `<p class="muted">Upload a document that describes a strategy (PDF, Word, web page or text), paste its text, or give a page's address. Buddy writes it as a rule and puts it in the Library. Nothing trades until you approve it.</p>
      <p><input type="file" id="lab-file" accept=".pdf,.docx,.html,.htm,.txt,.md" aria-label="Strategy document"> <button class="btn solid" id="lab-upload">Upload and read</button></p>
      <textarea id="lab-text" rows="4" style="width:100%;box-sizing:border-box;background:var(--bg);color:inherit;border:1px solid var(--line);border-radius:10px;padding:8px" placeholder="Or paste the strategy's text here" aria-label="Strategy text"></textarea>
      <p><button class="btn" id="lab-paste">Read the pasted text</button></p>
      <div style="display:flex;gap:8px;flex-wrap:wrap"><input id="lab-url" placeholder="Or a page's address: https://…" aria-label="Page address" style="flex:1;min-width:220px"><button class="btn" id="lab-link">Read this page</button></div>`,
      "Buddy fetches a page once and keeps only the rule and the link. Pages that need a sign-in cannot be read this way: paste their text.") +
    card("Where strategies come from", repoData.references.map((x) => `<div class="guard"><span style="min-width:0"><a href="${safe(x.url)}" target="_blank" rel="noopener"><b>${esc(x.title)}</b></a> <span class="chipk">${esc(x.licence)}</span><br><span class="muted">${esc(x.use)}</span></span></div>`).join(""));
}

function librarySection() {
  const c = repoData.counts, added = Object.fromEntries(((labData || {}).added || []).map((r) => [r.key, r]));
  const one = (s) => { const [mark, color] = MARK[s.verdict] || ["·", "muted"], mine = added[s.key];
    return `<div class="guard"><span class="mark" style="color:var(--${color})">${mark}</span><span style="min-width:0;flex:1">
      <b>${esc(s.name)}</b> <span class="chipk">${esc(s.stage)}</span><br><span class="muted">${esc(s.rule)}</span>
      ${mine ? `<br>${mine.status !== "active" ? `<button class="btn solid" data-rule="approve" data-key="${esc(s.key)}">Approve and start testing</button> ` : `<button class="btn" data-rule="pause" data-key="${esc(s.key)}">Pause</button> `}<button class="btn" data-rule="remove" data-key="${esc(s.key)}">Remove</button>` : ""}
      <details><summary>Tests, source and what it needs</summary>
        ${score(s.history, "Past prices")}${score(s.live, "Live paper trades")}
        ${s.history && s.history.trades ? `<p class="muted" style="margin:6px 0 0">Against the market per trade: rising markets ${share(s.rising_market)}, falling markets ${share(s.falling_market)}, latest stretch ${share(s.latest)}.</p>` : ""}
        <ul class="plain">${s.reasons.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>
        ${mine && mine.needs.length ? `<p class="muted" style="margin:6px 0 0">Left out, because Buddy cannot measure it: ${mine.needs.map(esc).join("; ")}</p>` : ""}
        ${mine && mine.captures ? `<p class="muted" style="margin:6px 0 0">How much of the strategy this rule holds: ${esc(mine.captures)}. Read the rule against the document before you approve.</p>` : ""}
        <p class="muted" style="margin:6px 0 0">Needs: ${esc(s.needs)} · held at most ${esc(s.hold_days)} days · source: ${safe(s.source_url) ? `<a href="${safe(s.source_url)}" target="_blank" rel="noopener">${esc(s.source)}</a>` : esc(s.source)}${s.licence ? ` (${esc(s.licence)})` : ""}</p>
      </details></span></div>`; };
  const shown = repoData.strategies.filter((s) => !repoFamily || s.family === repoFamily);
  const families = repoData.families.map((f) => `<button class="chip${repoFamily === f.key ? " on" : ""}" data-family="${esc(f.key)}" title="${esc(f.learns)}${f.research ? " · " + esc(f.research) : ""}">${esc(f.name)} (${esc(f.rules)})</button>`).join(" ");
  const picked = repoData.families.find((f) => f.key === repoFamily);
  const none = ((labData || {}).not_rules || []);
  return card("By family", `<p style="display:flex;flex-wrap:wrap;gap:6px;margin:0 0 8px"><button class="chip${repoFamily ? "" : " on"}" data-family="">All (${esc(c.collected)})</button> ${families}</p>
      ${picked ? `<p class="muted">${esc(picked.learns)}${picked.research ? ` · research: ${esc(picked.research)}` : ""}</p>` : ""}
      ${shown.map(one).join("") || `<p class="muted">No strategy collected in this family yet.</p>`}`,
      "A rule you approve starts paper trading the next weekday. Run the replay in Test to fill in its record on past prices.") +
    (none.length ? card(`Papers that hold no rule of their own (${none.length})`, none.map((n) => `<p class="muted"><b>${safe(n.source_url) ? `<a href="${safe(n.source_url)}" target="_blank" rel="noopener">${esc(n.source)}</a>` : esc(n.source)}</b><br>${esc(n.why)}</p>`).join("")) : "");
}

function testSection() {
  const rp = (labData || {}).replay || {}, b = (researchData || {}).benchmarks || {}, f = b.factors || {};
  const line = rp.state === "running" ? "A replay over past prices is running; refresh in a minute or two."
    : rp.state === "failed" ? "The last replay failed: " + (rp.why || "")
    : rp.state === "done" ? `History covers ${rp.from} to ${rp.to}: ${Number(rp.trades).toLocaleString()} finished trades, replayed ${rp.at}.`
    : "History has not been replayed yet.";
  const frows = (f.rows || []).map((x) => `<div class="guard"><span style="min-width:0;flex:1"><b>${esc(x.name)}</b> <span class="muted">${esc(x.what)}</span></span>
      <span style="text-align:right;white-space:nowrap">${share(x.last_12_months)} <span class="muted">last 12 months</span><br>${share(x.average_year)} <span class="muted">a year</span></span></div>`).join("");
  const lrows = (b.lists || []).map((l) => `<div class="guard"><span style="min-width:0;flex:1"><b>${esc(l.title || l.name)}</b><br><span class="muted">${esc(l.symbols)} symbols · ${esc(l.day)}${l.in_use ? " · used by the lab" : ""}</span></span>
      <button class="btn" data-uselist="${l.in_use ? "" : esc(l.name)}">${l.in_use ? "Stop using" : "Use in the lab"}</button></div>`).join("");
  return card("Replay on past prices", `<p>${esc(line)}</p>
      <p><button class="btn solid" id="lab-replay">Replay over past prices</button> <button class="btn" id="lab-run">Run the lab now</button> <button class="btn" id="desk-refresh">Refresh</button></p>`,
      esc((labData || {}).history_note || "")) +
    card("What a rule must clear", `<ul class="plain">${repoData.policy.map((p) => `<li style="margin:4px 0">${esc(p)}</li>`).join("")}</ul>`,
      "Passed on past prices means: at least 30 past trades, beat the market after costs, also in the latest stretch, no fall deeper than 35%, and unlikely to be luck given how many rules were tried.") +
    card("Yardsticks: what plain styles returned", (frows || `<p class="muted">Not fetched yet.</p>`) + `<p><button class="btn" id="research-factors">Fetch the factor returns</button> <button class="btn" id="research-company">Fetch company figures now</button></p>`, esc(f.note || "")) +
    card("Stock lists the rules trade", (lrows || `<p class="muted">No lists yet. The lab uses its built-in list of about a hundred large US stocks and funds.</p>`) +
      `<p class="muted">Upload a holdings or index file (CSV or Excel with a Ticker column):</p>
       <div style="display:flex;gap:8px;flex-wrap:wrap"><input id="list-name" placeholder="List name, e.g. nasdaq100" maxlength="30" aria-label="List name"><input type="file" id="list-file" accept=".csv,.xlsx,.txt" aria-label="Holdings file"><button class="btn" id="list-upload">Upload</button></div>`,
      "Each list is kept with its date, so past membership builds up from today.");
}

function rankSection() {
  const rows = repoData.strategies.map((s) => { const h = s.history || {};
    return `<tr><td>${esc(s.rank)}</td><td><b>${esc(s.name)}</b><br><span class="muted">${esc(s.stage)}</span></td>
      <td>${h.trades ? share(h.average_vs_market) : "–"}</td><td>${h.trades ? share(h.yearly_return) : "–"}</td><td>${esc(h.sharpe ?? "–")}</td>
      <td>${h.trades ? share(-h.worst_fall) : "–"}</td><td>${h.not_luck != null ? Math.round(h.not_luck * 100) + "%" : "–"}</td><td>${esc(h.trades || 0)}</td></tr>`; }).join("");
  return card("Ranking", `<div style="overflow-x:auto"><table class="grid"><tr><th>#</th><th>Rule</th><th>A trade against the market</th><th>Return a year</th><th>Sharpe</th><th>Worst fall</th><th>Not luck</th><th>Past trades</th></tr>${rows}</table></div>`,
    "Proven live first, then rules that passed on past prices, then by how far a trade beat the market in the test. " + esc(repoData.note || ""));
}

function paperSection() {
  const rows = ((labData || {}).strategies || []);
  const sum = (k) => rows.reduce((a, x) => a + (x[k] || 0), 0);
  const list = rows.map((s) => `<div class="guard"><span style="min-width:0;flex:1"><b>${esc(s.name)}</b><br><span class="muted">${esc(s.says)}</span></span>
      <span style="text-align:right;white-space:nowrap">${esc(s.open)} open<br><span class="muted">${esc(s.closed)} closed</span></span></div>`).join("");
  return card("Paper trading", `<div class="kpis">${kpi(esc(sum("open")), "trades open now")}${kpi(esc(sum("closed")), "closed so far")}${kpi(sum("closed") ? `${esc(sum("won"))} of ${esc(sum("closed"))}` : "–", "closed trades that made money")}${kpi(esc(repoData.counts.trading), "rules trading")}</div>
      ${list || `<p class="muted">${esc((labData || {}).failed || "Loading…")}</p>`}
      <p><button class="btn solid" id="see-trades">See every trade</button> <button class="btn" id="lab-run">Run the lab now</button></p>`,
    esc((labData || {}).note || "Every approved rule puts a fixed amount of pretend money on each pick, every weekday. Nothing real is bought."));
}

function monitorSection() {
  const COLOR = { holding: "sev-good", failing: "sev-high", weaker: "sev-medium", "too early": "muted", "not trading": "muted" };
  const WORD = { holding: "Holding up", failing: "Failing live", weaker: "Weaker than its test", "too early": "Too early", "not trading": "Not trading" };
  const order = ["failing", "weaker", "holding", "too early", "not trading"];
  const rows = [...repoData.strategies].sort((a, b) => order.indexOf(a.watch.state) - order.indexOf(b.watch.state)).map((s) =>
    `<div class="guard"><span class="mark" style="color:var(--${COLOR[s.watch.state] || "muted"})">●</span><span style="min-width:0;flex:1"><b>${esc(s.name)}</b> <span class="chipk">${esc(WORD[s.watch.state] || s.watch.state)}</span><br>
      <span class="muted">${esc(s.watch.says)}</span></span></div>`).join("");
  const c = repoData.counts;
  return card("Is each rule doing live what it did in its test?", `<div class="kpis">${kpi(esc(c.failing_live), "failing live")}${kpi(esc(c.weaker_live), "weaker than their test")}${kpi(esc(c.proven), "proven live")}</div>${rows}`,
    "A rule is compared with its test only after 10 live closed trades. Buddy shows this; it does not pause a rule by itself. Pause one in the Library.");
}

function strategies() {
  if (!repoData) { loadRepo(); return `<p class="muted">Loading…</p>`; }
  if (repoData.failed) return `<p class="err">${esc(repoData.failed)}</p>`;
  if (!labData && !deskBusy) loadDesk();
  const c = repoData.counts;
  const chips = SECTIONS.map(([k, label]) => `<button class="chip${k === section ? " on" : ""}" data-section="${k}">${label}${k === "discover" && c.queued ? ` (${esc(c.queued)})` : k === "monitor" && (c.failing_live + c.weaker_live) ? ` (${esc(c.failing_live + c.weaker_live)})` : ""}</button>`).join(" ");
  return card("Investment Intelligence", `<div class="kpis">${kpi(esc(c.collected), "strategies collected")}${kpi(esc(c.passed), "passed on past prices")}${kpi(esc(c.failed), "failed")}
      ${kpi(esc(c.trading), "paper trading")}${kpi(esc(c.waiting), "waiting for your approval")}${kpi(esc(c.proven), "proven live")}</div>
      <p style="display:flex;flex-wrap:wrap;gap:6px;margin:12px 0 0">${chips}</p>`,
      "Finds investment strategies in published research, rewrites each as a plain rule, tests it itself on past prices, and practises with pretend money. Nothing real is bought.") +
    { discover: discoverSection, library: librarySection, test: testSection, rank: rankSection, paper: paperSection, monitor: monitorSection }[section]();
}

// --- Predictions: the three engines, what each learns from, and whether a tested model is in use
async function loadPredict() {
  if (predictBusy) return;
  predictBusy = true;
  try { predictData = await api("/api/intel/predict"); } catch (e) { predictData = { failed: e.message }; }
  predictBusy = false;
  if (tab === "predict") render();
}
async function loadHistory(button) {
  button.disabled = true; button.textContent = "Starting…";
  try { const out = await api("/api/intel/predict", { history: true }); predictData = out; render(); notice(out.text); }
  catch (e) { notice(e.message); render(); }
}
function modelRow(m) {
  const bar = Math.min(100, Math.round(100 * m.judged / m.needed));
  const state = m.in_use ? `<span style="color:var(--sev-good)">✓ model in use</span>` : m.last_status === "rejected" ? `<span style="color:var(--sev-high)">✗ last model did not pass its test</span>`
    : m.enough ? `<span class="muted">• enough to train; next weekly run</span>` : `<span class="muted">○ learning: not enough judged calls yet</span>`;
  const tested = m.tested_on ? `<br><span class="muted">Tested on ${esc(m.tested_on)} calls it had never seen: ${Math.round((m.right_overall || 0) * 100)}% right overall${m.right_when_liked != null ? `, ${Math.round(m.right_when_liked * 100)}% on the ones it liked` : ""}</span>` : "";
  const pc = (v) => v == null ? "–" : Math.round(v * 100) + "%";
  const checks = m.checks ? `<details style="margin-top:6px"><summary class="muted">The tests it had to pass</summary><ul class="plain">${Object.entries(m.checks).map(([what, ok]) => `<li>${ok ? "✓" : "✗"} ${esc(what)}</li>`).join("")}</ul>
      ${m.brier != null ? `<p class="muted">Error of its odds (Brier): ${esc(m.brier)} against ${esc(m.brier_of_the_average)} for always saying the average. Lower is better.</p>` : ""}
      ${m.simple_rule_accuracy != null ? `<p class="muted">The simple rule "already rising keeps rising" was right ${pc(m.simple_rule_accuracy)} of the time.</p>` : ""}
      ${m.calibration ? `<table class="grid"><tr><th>Odds it gave</th><th>Calls</th><th>Came true</th></tr>${m.calibration.table.map((t) => `<tr><td>${esc(t.odds)}</td><td>${esc(t.calls)}</td><td>${pc(t.came_true)}</td></tr>`).join("")}</table>` : ""}
      ${m.by_market ? `<p class="muted">Rising markets: ${esc(m.by_market.up.calls)} calls, ${pc(m.by_market.up.right_when_liked)} right on the ones it liked. Falling markets: ${esc(m.by_market.down.calls)} calls, ${pc(m.by_market.down.right_when_liked)}.</p>` : ""}</details>` : "";
  const live = m.live ? `<br><span class="muted">Since it went into use: ${esc(m.live.calls)} calls judged${m.live.liked ? `, ${pc(m.live.right_when_liked)} right on the ${esc(m.live.liked)} it liked (its test showed ${pc(m.live.promised)})` : ", none it liked yet"}. It is retired automatically if those stop beating calls overall.</span>` : "";
  return `<div class="guard"><span><b>${esc(m.name[0].toUpperCase() + m.name.slice(1))}</b> · ${state}<br>
    <span class="muted">${esc(m.judged)} of ${esc(m.needed)} judged calls (${esc(m.of_each_outcome[0])} went one way, ${esc(m.of_each_outcome[1])} the other)</span>
    <div style="height:6px;border-radius:3px;background:var(--line);margin:6px 0"><div style="height:6px;border-radius:3px;width:${bar}%;background:var(--accent, #3987e5)"></div></div>${tested}${live}${checks}</span></div>`;
}
function predict() {
  if (!predictData) { loadPredict(); return `<p class="muted">Loading…</p>`; }
  if (predictData.failed) return `<p class="err">${esc(predictData.failed)}</p>`;
  const h = predictData.history || {};
  const history = h.state === "done" ? `Loaded ${esc(h.records)} past records and ${esc(h.events)} past events with their results, covering ${esc(h.months)} months.`
    : h.state === "collecting" ? `Loading now: reached ${esc(h.reached || "the start")}, ${esc(h.new || 0)} records so far. Buddy will message when it is done.`
    : h.state === "failed" ? `The last load failed: ${esc(h.why || "")}` : "Not loaded yet. Until it is, the theme model learns only from events written since it was switched on.";
  const MARK = { ok: ["✓", "sev-good"], late: ["!", "sev-high"], never: ["○", "sev-low"] };
  const running = card("Is it really running?", (predictData.running || []).map((r) => `<div class="guard"><span class="mark" style="color:var(--${(MARK[r.state] || MARK.never)[1]})">${(MARK[r.state] || MARK.never)[0]}</span>
      <span><b>${esc(r.what)}</b> <span class="chipk">${r.state === "ok" ? "running" : r.state === "late" ? "needs a look" : "not yet"}</span><br><span class="muted">${esc(r.detail)}</span></span></div>`).join(""),
    "Read from what the jobs on this server wrote down, not from the code. Passing tests only show the code can run.");
  return running + predictData.engines.map((e) => card(esc(e.title), `<p class="muted" style="margin-top:0">${esc(e.asks)}</p>
      ${e.models.map(modelRow).join("")}
      <p class="note" style="margin-bottom:4px">Learns from</p><ul style="margin:0 0 0 18px;padding:0">${e.learns_from.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>`,
      `${esc(e.open_calls)} calls open and waiting for their outcome${e.watched != null ? ` · ${esc(e.watched)} saved searches checked daily` : ""}`)).join("") +
    card("History for the theme model", `<p>${history}</p><p><button class="btn" id="history"${h.state === "collecting" ? " disabled" : ""}>${h.state === "done" ? "Load again" : "Load the last 30 months"}</button></p>`,
      "Asks the official dated sources (company filings, fundraising notices, federal awards, scholarly works, NSF grants) about past dates, then replays the rules month by month with only what was known then and that day's prices. It takes a while. It is flattering in three ways: it only knows companies that exist today, news cannot be loaded for the past, and the list of technologies was chosen with hindsight. " + esc(predictData.hindsight || "")) +
    `<p class="note">${esc(predictData.note)}</p>`;
}

async function openTech(id) {
  $("panel").innerHTML = `<p class="muted">Loading…</p>`; $("sheet").classList.add("open");
  let t;
  try { t = await api(`/api/intel/technology?id=${encodeURIComponent(id)}`); }
  catch (e) { $("panel").innerHTML = `<p class="err">${esc(e.message)}</p>`; return; }
  const W = 640, H = 170, L = 26, B = 22, T = 14, plotH = H - B - T, totals = t.months.map((m) => Object.keys(KIND).reduce((n, k) => n + (m[k] || 0), 0));
  const top = Math.max(1, ...totals), step = (W - L) / t.months.length, bw = Math.min(26, step - 8);
  const y = (v) => T + plotH - (v / top) * plotH;
  const bars = t.months.map((m, i) => { const x = L + i * step + (step - bw) / 2, v = totals[i];
    return `<g>${v ? `<path d="M${x},${T + plotH} V${y(v) + 4} q0,-4 4,-4 h${bw - 8} q4,0 4,4 V${T + plotH} Z" fill="var(--accent)"/><text class="val" x="${x + bw / 2}" y="${y(v) - 4}" text-anchor="middle">${v}</text>` : ""}
      ${i % 2 === 0 ? `<text x="${x + bw / 2}" y="${H - 6}" text-anchor="middle">${esc(m.month.slice(5))}/${esc(m.month.slice(2, 4))}</text>` : ""}
      <rect class="hit" data-i="${i}" x="${L + i * step}" y="0" width="${step}" height="${H}" fill="transparent"/></g>`; }).join("");
  const rel = (t.relationships || []).map((r) => `<div class="guard"><span class="mark" style="color:var(--${r.standing === "verified" ? "sev-good" : "sev-low"})">${r.standing === "verified" ? "✓" : "•"}</span>
      <span><b>${esc(r.reads)}</b> <span class="chipk">${esc(r.standing)}</span><br><span class="muted">${esc(r.means)} · ${esc(day(r.day))}</span><br>
      ${safe(r.url) ? `<a href="${safe(r.url)}" target="_blank" rel="noopener">${esc(r.title)}</a>` : `<span class="muted">${esc(r.title)}</span>`}</span></div>`).join("");
  const byKind = Object.keys(KIND).filter((k) => (t.evidence[k] || []).length).map((k) => `<h3>${esc(KIND[k][0].toUpperCase() + KIND[k].slice(1))}</h3>${t.evidence[k].map(evidence).join("")}`).join("");
  $("panel").innerHTML = `<button class="close" id="closesheet" aria-label="Close">×</button>
    <div class="crumbs">Intelligence › ${esc(t.sector)}</div><h2>${esc(t.name)}</h2>
    <div class="kinds">${stage(t.stage)}<span class="num">${esc(t.signals_90_days)} records in 90 days · ${esc(growth(t.growth))}</span></div>
    <p class="muted">Why this stage: ${esc(t.why)}.</p><div class="kinds">${kinds(t.kinds)}</div>
    <h3>Records per month</h3><div class="chart" id="months"><svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Records per month, last 12 months">
      <line x1="${L}" x2="${W}" y1="${T + plotH}" y2="${T + plotH}" stroke="var(--line)"/>${bars}</svg><div class="tip" id="tip"></div></div>
    ${t.linked.length ? `<h3>Shows up together with</h3><div class="kinds">${t.linked.map((l) => `<button class="chip" data-tech="${esc(l.id)}">${esc(l.name)} · ${esc(l.shared)}</button>`).join("")}</div>` : ""}
    ${t.organisations.length ? `<h3>Organisations in the records</h3>${t.organisations.slice(0, 12).map((o) => `<div class="ev">${esc(o.name)}<small>${esc(o.records)} record${o.records === 1 ? "" : "s"}: ${esc(o.kinds.join(", "))}</small></div>`).join("")}` : ""}
    ${rel ? `<h3>Who is connected to whom</h3>${rel}<p class="note">✓ verified: from an official award or grant. • reported: read from a headline by an AI, with every name checked against the headline. A reported relationship is not a checked fact.</p>` : ""}
    ${byKind || `<p class="muted">No records collected for this technology yet.</p>`}`;
  $("closesheet").onclick = closeSheet; $("closesheet").focus();
  const box = $("months"), tip = $("tip");
  box.querySelectorAll(".hit").forEach((r) => {
    const show = (e) => { const m = t.months[+r.dataset.i]; tip.replaceChildren();
      const head = document.createElement("b"); head.textContent = m.month; tip.append(head);
      for (const k of Object.keys(KIND)) if (m[k]) { const row = document.createElement("div"); row.textContent = `${m[k]} ${KIND[k]}`; tip.append(row); }
      if (tip.childElementCount === 1) { const row = document.createElement("div"); row.textContent = "no records"; tip.append(row); }
      const b = box.getBoundingClientRect(); tip.style.display = "block";
      tip.style.left = Math.min(Math.max(0, e.clientX - b.left + 12), b.width - tip.offsetWidth) + "px";
      tip.style.top = Math.max(0, e.clientY - b.top - tip.offsetHeight - 8) + "px"; };
    r.addEventListener("pointermove", show); r.addEventListener("pointerdown", show);
    r.addEventListener("pointerleave", () => { tip.style.display = "none"; });
  });
  $("panel").querySelectorAll("[data-tech]").forEach((b) => b.onclick = () => openTech(b.dataset.tech));
}
function closeSheet() { $("sheet").classList.remove("open"); }

function render() {
  tabs();
  $("main").innerHTML = { overview, sectors, sources, predict, strategies,
    convergence: () => card("Technologies converging across sectors", data.convergences.length ? data.convergences.map((c, i) => convergence(c, i === 0)).join("")
      : `<p class="muted">None yet. It needs a few nights of records.</p>`, "The stage and rank come from the records by fixed rules. The interpretation, where there is one, is written by an AI from those records only and is marked as such.") }[tab]();
  document.querySelectorAll("#main [data-tech]").forEach((b) => b.onclick = () => openTech(b.dataset.tech));
  document.querySelectorAll("[data-sector]").forEach((b) => b.onclick = () => follow(b.dataset.sector, b.dataset.on === "1"));
  document.querySelectorAll("[data-family]").forEach((b) => b.onclick = () => { repoFamily = b.dataset.family; render(); });
  document.querySelectorAll("[data-section]").forEach((b) => b.onclick = () => { section = b.dataset.section; storageSet("intelSection", section); render(); });
  const on = (id, fn) => { if ($(id)) $(id).onclick = () => fn($(id)); };
  on("repo-discover", (b) => desk("/api/intel/strategies", { action: "discover" }, b, "Reading the list…"));
  on("research-search", (b) => desk("/api/invest/research", { action: "search" }, b, "Searching…"));
  on("research-factors", (b) => desk("/api/invest/research", { action: "factors" }, b, "Fetching…"));
  on("research-company", (b) => desk("/api/invest/research", { action: "company_figures" }, b, "Fetching…"));
  on("research-github", (b) => { const url = $("research-url").value.trim(); if (url) desk("/api/invest/research", { action: "github", url }, b, "Adding…"); });
  document.querySelectorAll("[data-start]").forEach((b) => b.onclick = () => desk("/api/invest/research", { action: "github", url: b.dataset.start }, b));
  document.querySelectorAll("[data-research]").forEach((b) => b.onclick = () => desk("/api/invest/research", { action: b.dataset.research, id: Number(b.dataset.id) }, b, b.dataset.research === "read" ? "Reading…" : "…"));
  document.querySelectorAll("[data-uselist]").forEach((b) => b.onclick = () => desk("/api/invest/research", { action: "use_list", name: b.dataset.uselist }, b));
  document.querySelectorAll("[data-rule]").forEach((b) => b.onclick = () => desk("/api/invest/lab/rule", { key: b.dataset.key, action: b.dataset.rule }, b));
  on("lab-replay", (b) => desk("/api/invest/lab/replay", {}, b, "Starting…"));
  on("lab-run", (b) => desk("/api/invest/lab", {}, b, "Running…"));
  on("desk-refresh", () => { labData = null; researchData = null; repoData = null; render(); });
  on("lab-paste", (b) => { const text = $("lab-text").value.trim(); if (text) desk("/api/invest/lab/upload", { name: "pasted text", text }, b, "Reading…"); });
  on("lab-link", (b) => { const url = $("lab-url").value.trim(); if (url) desk("/api/invest/lab/upload", { url }, b, "Reading the page…"); });
  const sendFile = (input, limit, make, b) => {
    const file = $(input).files[0];
    if (!file) { notice("Choose a file first."); return; }
    if (file.size > limit) { notice("That file is too large."); return; }
    const reader = new FileReader();
    reader.onload = () => make(file, String(reader.result).split(",")[1] || "");
    reader.onerror = () => notice("That file could not be read.");
    reader.readAsDataURL(file);
  };
  on("lab-upload", (b) => sendFile("lab-file", 8000000, (file, data) => desk("/api/invest/lab/upload", { name: file.name, data }, b, "Reading…"), b));
  on("list-upload", (b) => { const name = $("list-name").value.trim(); if (!name) { notice("Give the list a name."); return; }
    sendFile("list-file", 8000000, (file, data) => desk("/api/invest/research", { action: "upload_list", name, filename: file.name, data }, b, "Uploading…"), b); });
  on("see-trades", () => { storageSet("investTab", "practice"); location.href = "invest.html"; });
  if ($("scan")) $("scan").onclick = scan;
  if ($("history")) $("history").onclick = () => loadHistory($("history"));

}
async function follow(sector, on) {
  try { data = await api("/api/intel/switch", { sector, on }); render(); notice(on ? "Following. Its evidence fills in from tonight's collection." : "No longer followed. It still gets a light weekly check."); }
  catch (e) { notice(e.message); }
}
async function scan() {
  try { notice((await api("/api/intel/scan", {})).text); } catch (e) { notice(e.message); }
}
$("sheet").onclick = (e) => { if (e.target === $("sheet")) closeSheet(); };
document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeSheet(); });
(async () => {
  try { data = await api("/api/intel"); render(); }
  catch (e) { $("main").innerHTML = `<p class="err">${esc(e.message)}</p>`; }
})();
