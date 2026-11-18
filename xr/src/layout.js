// From an app's screen to what a display shows: the tree becomes rows, rows are wrapped to the width of the lens,
// and the lines are cut into pages. One frame is one page:
//   { id, title, lines: [{ text, kind, focused, level }], page, pages, image }
// A host only has to draw those lines.

import { allowed, WITHHELD } from "./privacy.js";

/** Break text into lines no wider than cols, at spaces where it can. */
export function wrap(text, cols) {
  const out = [];
  for (const para of String(text).split("\n")) {
    let line = "";
    for (let word of para.split(/\s+/).filter(Boolean)) {
      while (word.length > cols) {                     // a word longer than the lens is cut
        if (line) { out.push(line); line = ""; }
        out.push(word.slice(0, cols));
        word = word.slice(cols);
      }
      if (!line) line = word;
      else if (line.length + 1 + word.length <= cols) line += " " + word;
      else { out.push(line); line = word; }
    }
    out.push(line);
  }
  return out.length ? out : [""];
}

/** The screen as a flat list of rows: { kind, text, say, privacy, choose, level, src }.
 *  `choose` is set on rows the wearer can pick. `say` is what the row sounds like. */
export function rows(screen) {
  if (!screen || screen.type !== "screen") throw new Error("view() must return a Screen(...).");
  const base = screen.props.privacy || "public";
  const out = [];
  for (const n of screen.children) {
    const p = n.props, privacy = p.privacy || base;
    if (n.type === "text") out.push({ kind: "text", text: p.text, say: p.text, speak: p.say === true, privacy });
    else if (n.type === "stat") out.push({ kind: "stat", text: `${p.label}: ${p.value}`, say: `${p.label}: ${p.value}`, privacy });
    else if (n.type === "progress") { const pct = Math.round(p.value * 100) + "%"; out.push({ kind: "progress", text: `${p.label} ${pct}`, say: `${p.label}, ${pct}`, value: p.value, privacy }); }
    else if (n.type === "alert") out.push({ kind: "alert", text: p.text, say: p.text, level: p.level, speak: true, privacy });
    else if (n.type === "image") out.push({ kind: "image", text: p.alt || "", say: p.alt || "", src: p.src, noAlt: !p.alt, privacy });
    else if (n.type === "list") p.items.forEach((item, i) => {
      const text = [item.label, item.detail].filter(Boolean).join(" · ");
      out.push({ kind: "item", text, right: item.value ? String(item.value) : "", say: [item.label, item.detail, item.value].filter(Boolean).join(", "), privacy: item.privacy || privacy,
        position: [i + 1, p.items.length], choose: p.onSelect ? (ctx) => p.onSelect(item, i, ctx) : null });
    });
    else if (n.type === "ask") {
      out.push({ kind: "ask", text: p.question, say: p.question, speak: true, privacy });
      out.push({ kind: "option", text: p.yesLabel, say: p.yesLabel, answer: "yes", privacy: "public", choose: (ctx) => p.yes && p.yes(ctx) });
      out.push({ kind: "option", text: p.noLabel, say: p.noLabel, answer: "no", privacy: "public", choose: (ctx) => p.no && p.no(ctx) });
    } else throw new Error(`Unknown block "${n.type}" in a Screen.`);
  }
  return out;
}

/** One page for a display. focus: index among the rows that can be chosen. page: which page to show, or "focus"
 *  for the page the focused row is on. focusPage in the result says where that row is. */
export function frame(screen, list, profile, { focus = 0, page = 0, options = {} } = {}) {
  const d = profile.display;
  if (!d) return null;
  const choosable = list.filter((r) => r.choose);
  const focused = choosable[focus] || null;
  const lines = [];
  let focusLine = 0, image = null, withheld = 0;
  for (const r of list) {
    const ok = allowed(r.privacy, "display", profile, options);
    if (!ok) withheld++;
    if (r.kind === "image" && ok && d.images) { image = { src: r.src, alt: r.text }; continue; }
    if (r.kind === "image" && ok && !r.text) continue;        // no picture and no words for it: nothing to show
    const mark = r.choose ? (r === focused ? "› " : "  ") : "";
    if (r === focused) focusLine = lines.length;
    const width = d.cols - mark.length;
    // a value sits at the right edge of its line when there is room, like a price on a bill
    const whole = !ok ? WITHHELD.display : !r.right ? r.text
      : r.text.length + 1 + r.right.length <= width ? r.text + " ".repeat(width - r.text.length - r.right.length) + r.right : `${r.text} ${r.right}`;
    (ok && r.right && whole.length === width ? [whole] : wrap(whole, width)).forEach((text, i) =>
      lines.push({ text: (i ? " ".repeat(mark.length) : mark) + text, kind: r.kind, focused: r === focused, ...(r.level ? { level: r.level } : {}),
        ...(r.kind === "progress" && ok ? { value: r.value } : {}) }));
  }
  const body = Math.max(1, d.lines - 1);                       // the title takes the first line
  const pages = Math.max(1, Math.ceil(lines.length / body));
  const focusPage = choosable.length ? Math.floor(focusLine / body) : 0;
  const at = page === "focus" ? focusPage : Math.max(0, Math.min(page, pages - 1));
  const title = String(screen.props.title || "");
  const count = pages > 1 ? ` ${at + 1}/${pages}` : "";
  return { id: screen.props.id || title, title: title.slice(0, d.cols - count.length) + count, lines: lines.slice(at * body, at * body + body),
    page: at, pages, focusPage, image, withheld };
}
