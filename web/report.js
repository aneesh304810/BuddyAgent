// Buddy report viewer: any report (money review, weekly and monthly money, card benefits, SOC readiness,
// research...) in the same look as the dashboards. The report's Markdown is turned into cards, real
// tables and buttons here; every piece of text is escaped first, and only https and Buddy's own
// download links become links.

const params = new URLSearchParams(location.search);
function storageGet(k) { try { return localStorage.getItem(k); } catch { return null; } }
function storageSet(k, v) { try { localStorage.setItem(k, v); } catch {} }
if (params.get("theme")) storageSet("buddyTheme", params.get("theme") === "light" ? "light" : "default");
if ((params.get("theme") || storageGet("buddyTheme")) === "light") document.documentElement.dataset.theme = "light";
if (params.get("token")) storageSet("buddyToken", params.get("token"));
const token = params.get("token") || storageGet("buddyToken") || "";
const jobId = params.get("id") || "";
if (params.get("token")) history.replaceState(null, "", location.pathname + "?id=" + encodeURIComponent(jobId));

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const DASHBOARDS = { money_review: ["money.html", "Open the money dashboard"], money_monthly: ["money.html", "Open the money dashboard"],
  money_weekly: ["money.html", "Open the money dashboard"], card_benefits: ["cards.html", "Open the card benefits dashboard"],
  credit_report: ["money.html", "Open the money dashboard"], market_scan: ["money.html", "Open the money dashboard"] };
const NUMERIC = /^[\s(−\-+]*(?:[$₹€£]\s?)?[\d,]+(?:\.\d+)?\s?(?:%|x|days?|USD|INR)?\)?\s*$/;

function safeUrl(u) {
  return /^https:\/\/[^\s"'<>]+$/.test(u) || /^\/api\/files\/[A-Za-z0-9]+\?[A-Za-z0-9=&_-]+$/.test(u) ? u : "";
}

// Inline Markdown on already-escaped text: **bold**, `code`, [label](url), bare https links.
function inline(text) {
  let s = esc(text);
  s = s.replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, (m, label, url) => {
    const u = safeUrl(url.replace(/&amp;/g, "&"));
    return u ? `<a href="${esc(u)}" target="_blank" rel="noopener">${label}</a>` : label;
  });
  s = s.replace(/(^|[\s(])(https:\/\/[^\s<)]+)/g, (m, pre, url) =>
    `${pre}<a href="${url}" target="_blank" rel="noopener">${url.replace(/^https:\/\/(www\.)?/, "").slice(0, 40)}</a>`);
  s = s.replace(/\*\*(.+?)\*\*/g, "<b>$1</b>").replace(/`([^`]+)`/g, "<code>$1</code>");
  return s;
}

function table(rows) {
  const cells = rows.map((r) => r.replace(/^\s*\|/, "").replace(/\|\s*$/, "").split("|").map((c) => c.trim()));
  const head = cells[0], body = cells.slice(1).filter((r) => !r.every((c) => /^:?-{2,}:?$/.test(c) || c === ""));
  const numeric = head.map((_, i) => body.length && body.every((r) => !r[i] || NUMERIC.test(r[i].replace(/\*\*/g, ""))));
  const td = (c, i, tag) => `<${tag}${numeric[i] ? ' class="num"' : ""}>${inline(c)}</${tag}>`;
  return `<div class="tablewrap"><table><thead><tr>${head.map((c, i) => td(c, i, "th")).join("")}</tr></thead>` +
    `<tbody>${body.map((r) => `<tr>${head.map((_, i) => td(r[i] || "", i, "td")).join("")}</tr>`).join("")}</tbody></table></div>`;
}

// Markdown -> sections: each "## " heading starts a card; text before the first is the opening card.
function render(md) {
  const lines = String(md || "").replace(/\r/g, "").split("\n");
  const sections = [{ title: "", html: [] }];
  let i = 0;
  const cur = () => sections[sections.length - 1];
  while (i < lines.length) {
    const line = lines[i];
    if (/^#\s/.test(line)) { i++; continue; }  // the report's own title: shown in the header already
    if (/^##\s/.test(line)) { sections.push({ title: line.replace(/^##\s+/, ""), html: [] }); i++; continue; }
    if (/^###\s/.test(line)) { cur().html.push(`<h3>${inline(line.replace(/^###\s+/, ""))}</h3>`); i++; continue; }
    if (/^\s*\|/.test(line)) {
      const rows = [];
      while (i < lines.length && /^\s*\|/.test(lines[i])) rows.push(lines[i++]);
      cur().html.push(table(rows)); continue;
    }
    if (/^\s*(?:[-*•]|\d+[.)])\s+/.test(line)) {
      const ordered = /^\s*\d+[.)]/.test(line), items = [];
      while (i < lines.length && /^\s*(?:[-*•]|\d+[.)])\s+/.test(lines[i])) items.push(lines[i++].replace(/^\s*(?:[-*•]|\d+[.)])\s+/, ""));
      const tag = ordered ? "ol" : "ul";
      cur().html.push(`<${tag}>${items.map((x) => `<li>${inline(x)}</li>`).join("")}</${tag}>`); continue;
    }
    if (/^\s*>/.test(line)) {
      const quote = [];
      while (i < lines.length && /^\s*>/.test(lines[i])) quote.push(lines[i++].replace(/^\s*>\s?/, ""));
      cur().html.push(`<div class="note">${inline(quote.join(" "))}</div>`); continue;
    }
    const link = line.trim().match(/^\[([^\]]+)\]\(([^)\s]+)\)$/);
    if (link && safeUrl(link[2])) {  // a line that is only a link: a button (downloads, sources)
      cur().html.push(`<p><a class="button" href="${esc(safeUrl(link[2]))}" target="_blank" rel="noopener">${esc(link[1])}</a></p>`);
      i++; continue;
    }
    if (!line.trim()) { i++; continue; }
    const para = [];
    while (i < lines.length && lines[i].trim() && !/^(#{1,3}\s|\s*\||\s*(?:[-*•]|\d+[.)])\s|\s*>)/.test(lines[i])) para.push(lines[i++]);
    cur().html.push(`<p>${inline(para.join(" "))}</p>`);
  }
  return sections.filter((s) => s.html.length || s.title)
    .map((s) => `<section class="card">${s.title ? `<h2>${inline(s.title)}</h2>` : ""}${s.html.join("")}</section>`).join("");
}

async function load() {
  if (!token || !jobId) { $("main").innerHTML = '<p class="err">Open this report from the Buddy app (Reports).</p>'; return; }
  try {
    const res = await fetch(`/api/jobs/${encodeURIComponent(jobId)}`, { headers: { Authorization: `Bearer ${token}` } });
    if (!res.ok) throw new Error(res.status === 404 ? "This report is gone." : `Error ${res.status}`);
    const job = await res.json();
    $("title").textContent = job.title || "Report";
    const when = (job.finished_at || job.created_at || "").slice(0, 10);
    $("label").textContent = [job.type || "", when].filter(Boolean).join(" · ");
    const dash = DASHBOARDS[job.kind];
    const top = [];
    if (job.status && !["done", "failed"].includes(job.status)) top.push(`<section class="card"><p class="muted">Still working on it (${esc(job.status)}). Pull to refresh in a minute.</p></section>`);
    if (job.summary) top.push(`<section class="card summary"><p>${inline(job.summary)}</p>${dash ?
      `<p style="margin-top:12px"><a class="button" href="${dash[0]}">${esc(dash[1])}</a></p>` : ""}</section>`);
    $("main").innerHTML = top.join("") + (render(job.report) || (job.status === "failed" ?
      `<section class="card"><p class="err">${esc(job.error || "This report failed.")}</p></section>` : ""));
  } catch (e) { $("main").innerHTML = `<p class="err">${esc(e.message)}</p>`; }
}
load();
